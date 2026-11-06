"""Bounded, read-only Amazon Ads transport using Python 3.11 standard library.

Inject transport(method, url, headers, body_bytes_or_None, timeout_seconds,
max_bytes) -> (status, response_headers, response_bytes). Transports MUST NOT
follow redirects. The default transport disables proxies and redirects. Injected
transports are trusted test/application code, never loaded from configuration.
"""
import base64
import ctypes
import email.utils
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

REGIONS = {
    'NA': ('https://advertising-api.amazon.com', 'https://api.amazon.com/auth/o2/token', 'https://www.amazon.com/ap/oa'),
    'EU': ('https://advertising-api-eu.amazon.com', 'https://api.amazon.co.uk/auth/o2/token', 'https://eu.account.amazon.com/ap/oa'),
    'FE': ('https://advertising-api-fe.amazon.com', 'https://api.amazon.co.jp/auth/o2/token', 'https://apac.account.amazon.com/ap/oa'),
}
MEDIA = {
    '/sp/campaigns/list': 'application/vnd.spCampaign.v3+json',
    '/sp/adGroups/list': 'application/vnd.spAdGroup.v3+json',
    '/sp/keywords/list': 'application/vnd.spKeyword.v3+json',
    '/sp/productAds/list': 'application/vnd.spProductAd.v3+json',
    '/sp/targets/list': 'application/vnd.spTargetingClause.v3+json',
    '/reporting/reports': 'application/vnd.createasyncreportrequest.v3+json',
}

class AdsApiError(Exception):
    """Safe category/status only; never captures response bodies or URLs."""
    def __init__(self, code, status=None):
        self.code = code
        self.status = status
        super().__init__(code + (f' (HTTP {status})' if status else ''))

def default_config_path():
    if os.environ.get('STARSKY_ADS_CONFIG'):
        return Path(os.environ['STARSKY_ADS_CONFIG']).expanduser()
    return Path(os.environ.get('STARSKY_HOME', str(Path.home()/'.starsky-codex'))).expanduser()/'config'/'amazon_ads_api.yaml'

def _validate_config(config):
    if not isinstance(config, dict) or config.get('region') not in REGIONS:
        raise AdsApiError('config_invalid')
    for field in ('client_id', 'client_secret', 'refresh_token'):
        if field in config and (not isinstance(config[field], str) or not config[field].strip()):
            raise AdsApiError('config_invalid')
        env = config.get(field+'_env')
        if env is not None and (not isinstance(env,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',env)):
            raise AdsApiError('config_invalid')
    profile = config.get('profile_id')
    if profile is not None and not re.fullmatch(r'[0-9]+',str(profile)):
        raise AdsApiError('config_invalid')
    return dict(config)

def load_config(path):
    """Read personal YAML/JSON with the installer's safe YAML dependency."""
    try:
        path=Path(path).expanduser().resolve()
        raw=path.read_text(encoding='utf-8-sig')
        if raw.lstrip().startswith('{'):
            config=json.loads(raw)
        else:
            import yaml
            config=yaml.safe_load(raw)
        if not isinstance(config,dict): raise ValueError()
        if config.get('credential_file'):
            env_path=Path(config['credential_file']).expanduser()
            if not env_path.is_absolute(): env_path=path.parent/env_path
            config['_file_credentials']=read_credential_file(env_path)
            # A source file is authoritative for this connection; never mix regional files via process env.
            if config.get('profile_id_env'):
                config['profile_id']=config['_file_credentials'].get(config['profile_id_env'])
            expected_base=REGIONS.get(config.get('region'),('',))[0]
            declared=config['_file_credentials'].get('ADS_ENDPOINT')
            if declared and declared.rstrip('/')!=expected_base: raise AdsApiError('credential_region_mismatch')
        elif config.get('profile_id_env'):
            config['profile_id']=os.environ.get(config['profile_id_env'])
        config=_validate_config(config)
        token_file=config.get('token_file')
        if token_file is not None and not isinstance(token_file,str): raise ValueError()
        token_path=Path(token_file).expanduser() if token_file else path.parent/'amazon_ads_api.tokens.json'
        if not token_path.is_absolute(): token_path=path.parent/token_path
        config['token_file']=str(token_path.resolve())
        return config
    except AdsApiError: raise
    except Exception: raise AdsApiError('config_invalid') from None


def read_credential_file(path):
    """Read a local .env as data, without execution, expansion or changing os.environ."""
    try:
        path=Path(path)
        if path.stat().st_size>1024*1024: raise ValueError()
        result={}
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            line=line.strip()
            if not line or line.startswith('#'): continue
            match=re.fullmatch(r'(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)',line)
            if not match or match[1] in result: raise ValueError()
            key,value=match.groups()
            if value.startswith('"'):
                value,end=json.JSONDecoder().raw_decode(value)
                rest=match[2][end:].strip()
                if not isinstance(value,str) or (rest and not rest.startswith('#')): raise ValueError()
            elif value.startswith("'"):
                end=value.find("'",1)
                if end<0 or (value[end+1:].strip() and not value[end+1:].strip().startswith('#')): raise ValueError()
                value=value[1:end]
            else:
                value=re.split(r'\s+#',value,1)[0].strip()
            if '\r' in value or '\n' in value: raise ValueError()
            result[key]=value
        return result
    except Exception:
        raise AdsApiError('credential_file_invalid') from None

def _token_path(config):
    return Path(config.get('token_store_path') or config.get('token_file') or default_config_path().parent/'amazon_ads_api.tokens.json').expanduser()

def _dpapi(data, decrypt=False):
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_=[('cbData',wintypes.DWORD),('pbData',ctypes.POINTER(ctypes.c_ubyte))]
    buf=ctypes.create_string_buffer(data)
    source=Blob(len(data),ctypes.cast(buf,ctypes.POINTER(ctypes.c_ubyte)))
    target=Blob()
    crypt=ctypes.windll.crypt32
    fn=crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    if not fn(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target)):
        raise AdsApiError('token_storage_failed')
    try: return ctypes.string_at(target.pbData,target.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes=[ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(target.pbData)

def save_refresh_token(config, token):
    """Windows DPAPI user encryption; other OS: permission-only private file."""
    try:
        if not isinstance(token,str) or not token: raise ValueError()
        path=_token_path(config).resolve()
        plugin=Path(__file__).resolve().parents[3]
        if path.is_relative_to(plugin): raise AdsApiError('token_storage_inside_plugin')
        path.parent.mkdir(parents=True,exist_ok=True)
        mode='windows_dpapi' if os.name=='nt' else 'private_file_unencrypted'
        data=_dpapi(token.encode()) if os.name=='nt' else token.encode()
        envelope=json.dumps({'storage':mode,'data':base64.b64encode(data).decode()})
        temp=path.with_name(path.name+'.'+secrets.token_hex(6)+'.tmp')
        try:
            fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w',encoding='utf-8') as f: f.write(envelope)
            if os.name!='nt': os.chmod(temp,0o600)
            os.replace(temp,path)
        finally:
            if temp.exists(): temp.unlink()
        return {'state':'authorized','storage':mode,'token_file':str(path)}
    except AdsApiError: raise
    except Exception: raise AdsApiError('token_storage_failed') from None

def load_refresh_token(config):
    try:
        path=_token_path(config)
        if os.name!='nt' and path.stat().st_mode & 0o077: raise AdsApiError('token_file_permissions')
        env=json.loads(path.read_text(encoding='utf-8'))
        data=base64.b64decode(env['data'],validate=True)
        if env['storage']=='windows_dpapi' and os.name=='nt': data=_dpapi(data,True)
        elif env['storage']!='private_file_unencrypted': raise ValueError()
        return data.decode()
    except AdsApiError: raise
    except Exception: raise AdsApiError('authorization_required') from None

def _credential(config, field):
    env=config.get(field+'_env')
    source=config.get('_file_credentials') if '_file_credentials' in config else os.environ
    value=source.get(env) if env else config.get(field)
    if not value and field=='refresh_token': return load_refresh_token(config)
    if not isinstance(value,str) or not value.strip() or '\r' in value or '\n' in value:
        raise AdsApiError('credentials_missing')
    return value

class _LoopbackServer(HTTPServer):
    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(1)
        return connection, address
    def handle_error(self, request, client_address):
        pass  # Never print request details or exceptions from callbacks.

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None

def default_transport(method,url,headers,body,timeout,max_bytes):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),_NoRedirect())
    req=urllib.request.Request(url,data=body,headers=headers,method=method)
    try:
        try: response=opener.open(req,timeout=timeout)
        except urllib.error.HTTPError as error: response=error
        with response:
            status=response.code
            # Never consume or expose error response bodies.
            data=b''
            if 200<=status<300:
                chunks=[]
                size=0
                deadline=time.monotonic()+timeout
                while True:
                    if time.monotonic()>deadline: raise AdsApiError('network_timeout')
                    chunk=response.read1(min(65536,max_bytes+1-size))
                    if not chunk: break
                    size+=len(chunk)
                    if size>max_bytes: raise AdsApiError('response_too_large')
                    chunks.append(chunk)
                data=b''.join(chunks)
            return status,dict(response.headers.items()),data
    except AdsApiError: raise
    except Exception: raise AdsApiError('network_error') from None

class AdsClient:
    def __init__(self,config,transport=None,sleeper=None):
        self.config=_validate_config(config)
        self.transport=transport or default_transport
        self.sleeper=sleeper or time.sleep
        self._access_token=None
        self.base_url,self.token_url,self.auth_url=REGIONS[self.config['region']]
    def _send(self,method,url,headers,body=None,max_bytes=16*1024*1024):
        for attempt in range(3):
            try:
                status,response_headers,data=self.transport(method,url,dict(headers),body,30,max_bytes)
                if not isinstance(data,bytes) or len(data)>max_bytes: raise AdsApiError('response_too_large')
            except AdsApiError: raise
            except Exception: raise AdsApiError('network_error') from None
            if 300<=status<400: raise AdsApiError('redirect_rejected',status)
            if status==429 or 500<=status<600:
                if attempt==2: raise AdsApiError('rate_limited' if status==429 else 'service_unavailable',status)
                value=next((v for k,v in response_headers.items() if k.lower()=='retry-after'),None)
                delay=2**attempt
                if value is not None:
                    try: delay=float(value)
                    except (ValueError,TypeError):
                        try: delay=max(0,email.utils.parsedate_to_datetime(value).timestamp()-time.time())
                        except Exception: delay=2**attempt
                if not math.isfinite(delay) or delay>30: raise AdsApiError('retry_deferred',status)
                self.sleeper(max(0,delay)); continue
            if not 200<=status<300:
                code='authentication_failed' if status in (401,403) else 'contract_mismatch' if status==400 else 'http_error'
                raise AdsApiError(code,status)
            return data
    def _json(self,*args,**kwargs):
        raw=self._send(*args,**kwargs)
        try: return json.loads(raw)
        except Exception: raise AdsApiError('invalid_json') from None
    def _exchange(self,form):
        form=dict(form,client_id=_credential(self.config,'client_id'),client_secret=_credential(self.config,'client_secret'))
        payload=self._json('POST',self.token_url,{'Content-Type':'application/x-www-form-urlencoded'},urllib.parse.urlencode(form).encode())
        if not isinstance(payload,dict) or not isinstance(payload.get('access_token'),str) or not payload['access_token']:
            raise AdsApiError('authentication_failed')
        self._access_token=payload['access_token']
        return payload
    def request(self,method,path,body=None,profile_id=None):
        allowed=(method=='GET' and path=='/v2/profiles') or (method=='POST' and path in MEDIA) or (method=='GET' and re.fullmatch(r'/reporting/reports/[A-Za-z0-9_-]{1,128}',path or ''))
        if not allowed: raise AdsApiError('endpoint_not_allowed')
        profile=profile_id if profile_id is not None else self.config.get('profile_id')
        if path!='/v2/profiles' and not re.fullmatch(r'[0-9]+',str(profile or '')): raise AdsApiError('profile_required')
        if method=='GET' and body is not None: raise AdsApiError('invalid_request')
        try: encoded=json.dumps(body).encode() if body is not None else None
        except Exception: raise AdsApiError('invalid_request') from None
        for auth_attempt in range(2):
            if not self._access_token:
                self._exchange({'grant_type':'refresh_token','refresh_token':_credential(self.config,'refresh_token')})
            headers={'Authorization':'Bearer '+self._access_token,'Amazon-Advertising-API-ClientId':_credential(self.config,'client_id')}
            if path!='/v2/profiles': headers['Amazon-Advertising-API-Scope']=str(profile)
            if path in MEDIA: headers.update({'Content-Type':MEDIA[path],'Accept':MEDIA[path]})
            try: return self._json(method,self.base_url+path,headers,encoded)
            except AdsApiError as error:
                if error.status==401 and auth_attempt==0: self._access_token=None; continue
                raise
    def profiles(self):
        data=self.request('GET','/v2/profiles')
        if not isinstance(data,list): raise AdsApiError('invalid_profiles')
        return data
    def download_report(self,url):
        try:
            parsed=urllib.parse.urlsplit(url); host=parsed.hostname or ''
            allowed=re.fullmatch(r'(?:[a-z0-9][a-z0-9.-]*\.)?s3(?:[.-](?:us|eu|ap|ca|sa|me|af|il|mx)-(?:[a-z]+-)?[a-z]+-[0-9]+)?\.amazonaws\.com',host)
            if parsed.scheme!='https' or not allowed or parsed.username or parsed.password or parsed.port not in (None,443) or parsed.fragment:
                raise ValueError()
        except Exception: raise AdsApiError('download_url_rejected') from None
        return self._send('GET',url,{},max_bytes=64*1024*1024)

def _redirect(config):
    uri=config.get('redirect_uri','http://127.0.0.1:8765/callback')
    try:
        p=urllib.parse.urlsplit(uri)
        if p.scheme!='http' or p.hostname!='127.0.0.1' or not p.port or p.username or p.password or p.query or p.fragment or not p.path.startswith('/'):
            raise ValueError()
    except Exception: raise AdsApiError('redirect_uri_invalid') from None
    return uri,p

def validate_callback(callback_url,expected_state,redirect_uri):
    try:
        p=urllib.parse.urlsplit(callback_url); expected=urllib.parse.urlsplit(redirect_uri)
        if (p.scheme,p.netloc,p.path)!=(expected.scheme,expected.netloc,expected.path) or p.fragment: raise ValueError()
        params=urllib.parse.parse_qs(p.query,keep_blank_values=True)
        if set(params)-{'state','code','scope'} or len(params.get('state',[]))!=1 or len(params.get('code',[]))!=1: raise ValueError()
        if not hmac.compare_digest(params['state'][0],expected_state) or not params['code'][0]: raise ValueError()
        return params['code'][0]
    except Exception: raise AdsApiError('oauth_callback_rejected') from None

def authorize(config,timeout_seconds=180,open_browser=True,transport=None):
    """Explicit interactive OAuth only. Returns safe storage metadata, no tokens.

Does not print authorization URLs. open_browser=False supports a trusted local
harness that inspects webbrowser.open separately; ordinary use opens the browser.
"""
    config=_validate_config(config); uri,parsed=_redirect(config)
    if not isinstance(timeout_seconds,(int,float)) or not 1<=timeout_seconds<=600: raise AdsApiError('oauth_timeout_invalid')
    client=AdsClient(config,transport=transport)
    _credential(config,'client_secret')  # Fail before opening a browser when the application is incomplete.
    state=secrets.token_urlsafe(32)
    result={}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_GET(self):
            try:
                if len(self.path)>8192: raise AdsApiError('oauth_callback_rejected')
                code=validate_callback('http://'+parsed.netloc+self.path,state,uri)
                result['code']=code; status=200
            except AdsApiError: status=400
            payload=b'Authorization received. Return to the application.' if status==200 else b'Authorization callback rejected.'
            self.send_response(status); self.send_header('Content-Type','text/plain'); self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(payload)
    try:
        with _LoopbackServer(('127.0.0.1',parsed.port),Handler) as server:
            server.timeout=0.5
            url=client.auth_url+'?'+urllib.parse.urlencode({'client_id':_credential(config,'client_id'),'scope':'advertising::campaign_management','response_type':'code','redirect_uri':uri,'state':state})
            if open_browser and not webbrowser.open(url): raise AdsApiError('browser_open_failed')
            deadline=time.monotonic()+timeout_seconds
            while 'code' not in result and time.monotonic()<deadline: server.handle_request()
        if 'code' not in result: raise AdsApiError('oauth_timeout')
        tokens=client._exchange({'grant_type':'authorization_code','code':result.pop('code'),'redirect_uri':uri})
        if not tokens.get('refresh_token'): raise AdsApiError('authorization_failed')
        return save_refresh_token(config,tokens['refresh_token'])
    except AdsApiError: raise
    except Exception: raise AdsApiError('authorization_failed') from None


