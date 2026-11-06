"""Read-only, bounded keyword evidence via the user's existing MCP connections.

SIF is preferred. Provider estimates and captured ranks never become own-account
traffic or proof that inserting a phrase caused a sale. Secrets stay in config/env.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tomllib
from urllib.parse import urlsplit

from ops_collect import write_json, _read
from ops_rules import keyword_coverage

PROVIDERS = {
    'SIF': ('sif_mcp', 'mcp.sif.com', 'market_get_asin_keyword_signals'),
    'Sorftime': ('sorftime', 'mcp.sorftime.com', 'product_traffic_terms'),
    'Xiyou': ('xydc-mcp', 'mcp.xydc.com', 'get_asin_keywords'),
}


def _now(): return datetime.now(timezone.utc)


def _target(market,asin):
    return market in ('US','UK') and isinstance(asin,str) and re.fullmatch(r'[A-Z0-9]{10}',asin)


def _stamp(value):
    try:
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        return parsed if parsed.tzinfo is not None else None
    except (AttributeError,ValueError,TypeError): return None


def _context_asin(value,market):
    if not isinstance(value,str): return None
    if re.fullmatch(r'[A-Z0-9]{10}',value): return value
    match=re.fullmatch(r'\[([A-Z0-9]{10})\]\((https://[^\s()]+)\)',value)
    if not match: return None
    asin,url=match.groups();parsed=urlsplit(url)
    domain={'US':'amazon.com','UK':'amazon.co.uk'}[market]
    if (parsed.netloc not in (domain,'www.'+domain) or parsed.path!='/dp/'+asin
            or parsed.query or parsed.fragment): return None
    return asin


def _env(name):
    value=os.environ.get(name)
    if not value and os.name=='nt':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,'Environment') as key:
                value=winreg.QueryValueEx(key,name)[0]
        except OSError: pass
    return value


def _credentials(provider, config_path=None):
    server,host,_=PROVIDERS[provider]
    path=Path(config_path) if config_path else Path.home()/'.codex/config.toml'
    text=path.read_text(encoding='utf-8-sig')
    if path.suffix.lower()=='.json':
        # Claude Code registry: {"mcpServers": {name: {type,url,headers}}}; SIF is registered as "sif".
        servers=json.loads(text).get('mcpServers',{})
        names=(server,'sif','sif-mcp') if provider=='SIF' else (server,)
        entry=next((servers[n] for n in names if n in servers),{})
        block={'url':entry.get('url'),'http_headers':entry.get('headers') or {}}
    else:
        block=tomllib.loads(text).get('mcp_servers',{}).get(server,{})
    if block.get('enabled') is False: raise ValueError('provider_disabled')
    url=block.get('url')
    if not url and provider=='SIF': url='https://mcp.sif.com/mcp'
    parsed=urlsplit(url or '')
    if parsed.scheme!='https' or parsed.hostname!=host or parsed.username or parsed.password or parsed.port not in (None,443):
        raise ValueError('provider_endpoint_unavailable')
    headers=dict(block.get('http_headers') or {})
    for key,name in (block.get('env_http_headers') or {}).items():
        if _env(name): headers[key]=_env(name)
    token=_env(block.get('bearer_token_env_var',''))
    if token: headers['Authorization']='Bearer '+token
    if provider=='SIF' and not any(k.lower()=='secret-key' for k in headers):
        token=_env('SIF_MCP_TOKEN') or _env('SIF_MCP_SECRET_KEY')
        if not token: raise ValueError('provider_credentials_missing')
        headers['secret-key']=token
    return url,headers


def call_provider(provider,asin,market,config_path=None):
    """Only a fixed read-only tool may be dispatched; no arbitrary MCP name."""
    if provider not in PROVIDERS or market not in ('US','UK') or not re.fullmatch(r'[A-Z0-9]{10}',asin):
        raise ValueError('keyword_request_invalid')
    import requests
    url,headers=_credentials(provider,config_path)
    headers.update({'Accept':'application/json, text/event-stream','Content-Type':'application/json'})
    session=requests.Session();session.trust_env=False
    def rpc(method,params,request_id=None):
        body={'jsonrpc':'2.0','method':method,'params':params}
        if request_id is not None: body['id']=request_id
        with session.post(url,headers=headers,json=body,timeout=(10,45),allow_redirects=False,stream=True) as response:
            if not 200<=response.status_code<300: raise ValueError('provider_http_'+str(response.status_code))
            if response.headers.get('Mcp-Session-Id'): headers['Mcp-Session-Id']=response.headers['Mcp-Session-Id']
            chunks=[];size=0
            for chunk in response.iter_content(65536):
                size+=len(chunk)
                if size>8*1024*1024: raise ValueError('provider_response_too_large')
                chunks.append(chunk)
            text=b''.join(chunks).decode('utf8')
        if not text.strip(): return {}
        if text.lstrip().startswith('{'): value=json.loads(text)
        else:
            values=[]
            for event in re.split(r'\r?\n\r?\n',text):
                data='\n'.join(line[5:].strip() for line in event.splitlines() if line.startswith('data:'))
                if data:
                    try: values.append(json.loads(data))
                    except ValueError: pass
            value=next((v for v in values if v.get('id')==request_id),{})
        if value.get('error'): raise ValueError('provider_rpc_error')
        if request_id is not None and value.get('id')!=request_id: raise ValueError('provider_rpc_id_mismatch')
        return value.get('result',{})
    try:
        init=rpc('initialize',{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'starsky-ops-readonly','version':'1.0'}},1)
        headers['MCP-Protocol-Version']=init.get('protocolVersion','2025-03-26')
        rpc('notifications/initialized',{})
        if provider=='SIF':
            parameters={'asin':asin,'country':market,'listingSearch':False,'time_type':'lately','time_value':'7','topN':50}
        elif provider=='Sorftime': parameters={'asin':asin,'amz_site':'GB' if market=='UK' else market,'page':1}
        else: parameters={'asin':asin,'country':market,'page':1,'page_size':50,'sort_field':'traffic','sort_order':'desc',
            'intent_summary':'读取已授权店铺ASIN的词排名与第三方流量，供本地人工复核。'}
        result=rpc('tools/call',{'name':PROVIDERS[provider][2],'arguments':parameters},2)
        if result.get('isError'): raise ValueError('provider_tool_error')
        return {'provider':provider,'tool':PROVIDERS[provider][2],'asin':asin,'market':market,
                'collected_at':_now().isoformat(),'parameters':parameters,'result':result}
    finally: session.close()


def unpack(result):
    if not isinstance(result,dict) or result.get('isError'): raise ValueError('keyword_tool_failed')
    if isinstance(result.get('structuredContent'),dict): return result['structuredContent']
    for entry in result.get('content',[]):
        if entry.get('type')=='text':
            try: return json.loads(entry['text'])
            except (ValueError,TypeError): pass
    raise ValueError('keyword_payload_unrecognized')


def _number(value):
    return value if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) else None


def normalize(envelope):
    if not _target(envelope.get('market'),envelope.get('asin')) or envelope.get('provider') not in PROVIDERS:
        raise ValueError('keyword_scope_invalid')
    provider=envelope['provider'];payload=unpack(envelope['result'])
    if payload.get('error') or (isinstance(payload.get('status'),int) and payload['status']>=400): raise ValueError('keyword_business_error')
    context=payload.get('query_context') or {}
    if context.get('asin') and _context_asin(context['asin'],envelope['market'])!=envelope['asin']: raise ValueError('keyword_asin_mismatch')
    country=context.get('country',context.get('market',context.get('amz_site')))
    if country and ('UK' if country=='GB' else country)!=envelope['market']: raise ValueError('keyword_market_mismatch')
    common={k:envelope[k] for k in ('store','market','asin','provider','collected_at')}
    common.update({'period':'近7天' if provider!='Sorftime' else '近期采样','traffic_basis':'第三方观测/估算，非账户实绩',
        'data_notice':payload.get('data_notice'),'tool':envelope['tool']})
    rows=[]
    if provider=='SIF':
        for row in payload.get('top_keywords',[]):
            if not isinstance(row.get('keyword'),str): continue
            rows.append({**common,'keyword':row['keyword'],'traffic_share':_number(row.get('traffic_share')),
                'estimated_traffic':None,'search_volume':_number(row.get('search_volume')),
                'organic_rank':row.get('organic_rank'),'ad_rank':row.get('sp_rank'),
                'rank_at':row.get('organic_rank_time_cn'),'crawl_at':row.get('keyword_crawl_time_cn'),
                'natural_ratio':_number(row.get('natural_ratio')),'rank_change':row.get('rank_evolution'),
                'traffic_change':_number(row.get('contri_change')),'market_cpc':_number(row.get('cpc_median')),
                'coverage_note':f"返回{len(payload.get('top_keywords',[]))}词；期间共{(payload.get('summary') or {}).get('total_keywords_in_period','未知')}词"})
    elif provider=='Xiyou':
        data=payload.get('data') or {}
        if data.get('error'): raise ValueError('keyword_business_error')
        for row in data.get('list',[]):
            if not isinstance(row.get('searchTerm'),str): continue
            ranks=row.get('ranks') or [];organic=[r for r in ranks if str(r.get('position','')).lower() in ('or','organic','nf')]
            paid=[r for r in ranks if str(r.get('position','')).lower()=='sp'];traffic=(row.get('trafficSummary') or {}).get('traffic') or {}
            rows.append({**common,'keyword':row['searchTerm'],'traffic_share':None,'estimated_traffic':_number(traffic.get('total')),
                'search_volume':None,'organic_rank':organic[0].get('totalRank') if organic else None,'ad_rank':paid[0].get('totalRank') if paid else None,
                'rank_at':organic[0].get('rankTime') if organic else None,'coverage_note':'最多50词，第三方近7天采样'})
    else:
        if not isinstance(payload.get('data'),list): raise ValueError('sorftime_keyword_schema_pending')
        for row in payload['data']:
            if not isinstance(row.get('keyword'),str): continue
            rows.append({**common,'keyword':row['keyword'],'traffic_share':None,'estimated_traffic':None,
                'search_volume':_number(row.get('monthly_search_volume')),
                'organic_rank':row.get('latest_organic_position') or None,'ad_rank':row.get('latest_ad_position') or None,
                'rank_at':row.get('latest_organic_exposure_time') or None,'ad_rank_at':row.get('latest_ad_exposure_time') or None,
                'coverage_note':'第1页最多20词；近期开前三页的关键词，不是全量或当前实时排名'})
    return rows


def collect_keywords(config,snapshot,caller=call_provider):
    policy=config.get('keywords') or {}
    if not policy.get('enabled'): return {'state':'not_enabled','collected':0}
    root=Path(config['workspace'])/'keyword_evidence';root.mkdir(parents=True,exist_ok=True)
    products=snapshot['queries'].get('products',{}).get('rows',[])
    targets=sorted({(p['market'],p['asin']) for p in products if _target(p.get('market'),p.get('asin')) and not p.get('stale')})
    preferred={(p['market'],p['asin']) for p in policy.get('priority_asins',[]) if p.get('market') and p.get('asin')}
    # Preserve explicit priorities, then alternate markets within each priority tier.
    def interleave(values):
        queues={market:[v for v in values if v[0]==market] for market in ('US','UK')}
        return [queues[market][i] for i in range(max((len(v) for v in queues.values()),default=0))
                for market in ('US','UK') if i<len(queues[market])]
    targets=interleave([v for v in targets if v in preferred])+interleave([v for v in targets if v not in preferred])
    maximum=max(1,min(int(policy.get('max_asins_per_run',5)),100));calls=0;items=[]
    ttl=timedelta(hours=max(1,int(policy.get('cache_hours',24))))
    for market,asin in targets:
        path=root/market/asin/'manifest.json';prior=_read(path,{}) or {}
        if prior.get('store')==config['store'] and prior.get('state')=='complete':
            try:
                stamp=_stamp(prior.get('collected_at'))
                cached=(path.parent/prior.get('file','')).resolve()
                if (stamp and timedelta(0)<=_now()-stamp<ttl and prior.get('market')==market and prior.get('asin')==asin
                        and cached.is_relative_to(path.parent.resolve()) and cached.is_file()
                        and hashlib.sha256(cached.read_bytes()).hexdigest()==prior.get('sha256')): continue
            except (ValueError,KeyError,TypeError): pass
        if calls>=maximum: break
        calls+=1;attempts=[];success=None
        for provider in policy.get('providers',['SIF','Sorftime','Xiyou']):
            if provider not in PROVIDERS: continue
            try:
                envelope=caller(provider,asin,market,policy.get('mcp_config'));envelope['store']=config['store']
                if any(envelope.get(k)!=v for k,v in {'provider':provider,'market':market,'asin':asin}.items()):
                    raise ValueError('keyword_scope_mismatch')
                stamp=_stamp(envelope.get('collected_at'))
                if stamp is None or not timedelta(0)<=_now()-stamp<ttl: raise ValueError('keyword_timestamp_invalid')
                rows=normalize(envelope)
                raw=json.dumps(envelope,ensure_ascii=False,sort_keys=True,allow_nan=False).encode('utf8')
                digest=hashlib.sha256(raw).hexdigest();file=path.parent/(digest+'.json');file.parent.mkdir(parents=True,exist_ok=True)
                if not file.exists(): file.write_bytes(raw)
                success={'store':config['store'],'market':market,'asin':asin,'state':'complete','provider':provider,
                    'collected_at':envelope['collected_at'],'file':file.name,'sha256':digest,'row_count':len(rows),'attempts':attempts}
                break
            except Exception as exc:
                # Exception bodies may contain authenticated URLs. Only known codes.
                code=str(exc) if isinstance(exc,ValueError) and re.fullmatch(r'[a-z_0-9]+',str(exc)) else 'provider_request_failed'
                attempts.append({'provider':provider,'state':'error','code':code})
        record=success or {'store':config['store'],'market':market,'asin':asin,'state':'error','attempts':attempts,'checked_at':_now().isoformat()}
        if not success and prior.get('state')=='complete' and prior.get('store')==config['store']: record['last_healthy']=prior
        write_json(path,record);items.append(record)
    write_json(root/'status.json',{'collected_at':_now().isoformat(),'store':config['store'],'items':items,'target_count':len(targets),'attempted':calls})
    return {'state':'complete','attempted':calls,'success':sum(i['state']=='complete' for i in items),'target_count':len(targets)}


def enrich_keywords(config,snapshot):
    metrics=[];coverage=[];connections=[];root=Path(config['workspace'])/'keyword_evidence'
    products=snapshot['queries'].get('products',{}).get('rows',[])
    fields=snapshot['queries'].get('listing_fields',{}).get('rows',[])
    rules=_read(Path(config.get('listing_rules_file') or ''),{}) or {}
    for market,asin in sorted({(p['market'],p['asin']) for p in products if _target(p.get('market'),p.get('asin'))}):
        folder=root/market/asin;manifest=_read(folder/'manifest.json',{}) or {};info=manifest
        stale=manifest.get('state')!='complete'
        if stale: info=manifest.get('last_healthy') or {}
        if info.get('store')!=config['store'] or info.get('market')!=market or info.get('asin')!=asin: continue
        file=(folder/info.get('file','')).resolve()
        if not file.is_relative_to(folder.resolve()) or not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest()!=info.get('sha256'): continue
        try:
            envelope=_read(file,{});new=normalize(envelope)
        except (ValueError,KeyError,TypeError): continue
        if any(envelope.get(k)!=info.get(k) for k in ('store','market','asin','provider','collected_at')): continue
        ttl=timedelta(hours=max(1,int((config.get('keywords') or {}).get('cache_hours',24))))
        stamp,reference=_stamp(info.get('collected_at')),_stamp(snapshot.get('generatedAt')) or _now()
        if stamp is None or stamp>reference: continue
        stale=stale or reference-stamp>=ttl
        for r in new:
            r.update({'stale':stale,'evidence_sha256':info['sha256'],'evidence_file':str(file.relative_to(Path(config['workspace'])))})
            metrics.append(r)
        for listing in fields:
            if listing['market']!=market or listing['asin']!=asin: continue
            texts={k:'\n'.join(v['value']) if isinstance(v.get('value'),list) and all(isinstance(x,str) for x in v['value']) else v['value']
                   for k,v in listing['fields'].items() if v.get('state')=='returned' and isinstance(v.get('value'),(str,list))}
            texts={k:v for k,v in texts.items() if isinstance(v,str)}
            result=keyword_coverage(texts,[r['keyword'] for r in new],rules)
            covered={r['keyword']:r['fields'] for r in result['covered']}
            for row in new:
                word=row['keyword'];found=covered.get(word,[])
                coverage.append({**row,'sku':listing['sku'],'version':listing['version'],'contentHash':listing['contentHash'],
                    'covered_fields':'、'.join(found),'state':'规则拦截' if word in result['blocked'] else '已覆盖' if found else '待核对相关性',
                    'suggested_field':None if found or word in result['blocked'] else '标题 / 亮点 / 五点 / ST，按词意人工选择',
                    'reason':'文本匹配不证明Amazon已索引，也不证明埋词产生流量。未返回的字段不判断缺词。',
                    'unknown_fields':'、'.join(k for k,v in listing['fields'].items() if v.get('state')=='not_returned')})
        connections.append({'market':market,'asin':asin,'provider':info['provider'],'state':'stale' if stale else 'observed','row_count':len(new),'collected_at':info['collected_at']})
    for key,label,rows in [('keyword_metrics','关键词排名与流量信号',metrics),('keyword_coverage','现有文案词覆盖',coverage),('keyword_connections','关键词来源状态',connections)]:
        snapshot['queries'][key]={'rows':rows,'source':{'label':label,'provider':'SIF first; source-tagged MCP evidence',
            'evidenceFlow':[{'title':'数据口径','detail':'第三方观测与估算，和自有Ads点击/订单分开。排名带采样时间，空值不是0。'}]}}
    return snapshot
