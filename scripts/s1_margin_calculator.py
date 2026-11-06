#!/usr/bin/env python3
"""Loopback-only approved calculator UI; every result comes from s1_cost_engine.

open --root CASE [--no-browser] --json; quick --text TEXT --offline --json;
save --root CASE --input-json FILE (or '-' for stdin). No business authorization.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

HOME=Path(__file__).resolve().parents[1]
CALCULATOR_SOURCE_SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
OUT=Path('S1_选品板块/amazon-market-research')
INPUTS='05_cost_inputs.yaml'
FX_URL='https://open.er-api.com/v6/latest/CNY'
CODES={'US':'USD','UK':'GBP','DE':'EUR','JP':'JPY','AU':'AUD'}


def _service_fingerprint():
    """Bind an already-loaded service to its actual packaged dependencies."""
    paths=set((HOME/'scripts').rglob('*.py'))
    paths.update((HOME/'templates/fee_tables').rglob('*'))
    paths.update(HOME/name for name in (
        'assets/margin_calculator.html',
        'skills/amazon-market-research/scripts/s1_cost_model.py',
        'skills/amazon-market-research/references/cost_defaults.yaml'))
    files={p.relative_to(HOME).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
           for p in sorted(paths) if p.is_file() and '__pycache__' not in p.parts}
    return hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()


# Capture loaded resources once. Re-reading them in /api/status would let an old
# process claim an in-place upgrade whose Python modules it has never loaded.
SERVICE_RESOURCE_SHA256=_service_fingerprint()


def load_module(path,name):
    path=Path(path).resolve()
    # A process can build several real host payloads. A friendly module name
    # alone would retain the previous payload's HOME (possibly already removed).
    name=name+'_'+hashlib.sha256(str(path).encode()).hexdigest()[:16]
    module=sys.modules.get(name)
    if module is None:
        spec=importlib.util.spec_from_file_location(name,path)
        module=importlib.util.module_from_spec(spec);sys.modules[name]=module
        previous=list(sys.path)
        try:
            sys.path.insert(0,str(Path(path).parent))
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name,None);raise
        finally:sys.path[:]=previous
    return module


def engine():
    return load_module(HOME/'scripts/s1_cost_engine.py','starsky_s1_cost_engine')


def _read_json_url(url):
    with urllib.request.urlopen(url,timeout=4) as response:
        return json.load(response)


def fx_rate(market, *, cache_dir=None, fetcher=None, offline=False):
    if market not in CODES:
        raise ValueError('unknown marketplace')
    cache=Path(cache_dir or Path.home()/'.starsky/cache/margin-calculator')/'fx.json'
    cached={}
    try:
        cached=json.loads(cache.read_text(encoding='utf-8'))
    except (FileNotFoundError,ValueError):
        pass
    error=None
    if not offline:
        try:
            raw=(fetcher or _read_json_url)(FX_URL)
            rates=raw['rates']
            entries={}
            for site,code in CODES.items():
                rate=engine().number(rates.get(code),'exchange rate')
                if rate and rate>0:
                    entries[site]={'value':1/rate,'observed_at':raw.get('time_last_update_utc') or dt.datetime.now(dt.timezone.utc).isoformat(),
                                   'fetched_at':dt.datetime.now(dt.timezone.utc).isoformat(),'url':FX_URL}
            if market not in entries:
                raise ValueError('FX response missing requested currency')
            cached.update(entries)
            cache.parent.mkdir(parents=True,exist_ok=True)
            _write(cache,json.dumps(cached,ensure_ascii=False,indent=2).encode())
            return {**entries[market],'source':'live','status':'fx_snapshot','unit':'CNY/'+CODES[market]}
        except (OSError,ValueError,KeyError,TypeError) as exc:
            error=str(exc)
    if market in cached:
        node=cached[market]
        value=engine().number(node.get('value'),'cached exchange rate')
        if value and value>0:
            return {**node,'value':value,'source':'cache','status':'fx_snapshot','unit':'CNY/'+CODES[market],
                    'warning':'联网失败，使用缓存汇率' if error else '离线使用缓存汇率'}
    fees,_=engine().load_fees(market)
    return {'value':fees.get('default_fx_cny_per_currency',6.69),'source':'default',
            'status':'default_assumption','observed_at':None,'unit':'CNY/'+CODES[market],
            'warning':'未取得实时值/缓存，使用示例默认汇率；非实时','url':FX_URL}


def default_payload(market='US', *, example=False):
    data=json.loads((HOME/'templates/margin_calculator_examples.json').read_text(encoding='utf-8'))
    p=copy.deepcopy(data['markets'][market])
    p['div']=float(p['div']);p['fba']=None
    if market=='US':p['vat']=0
    if not example:
        # A blank/new case must not borrow a demonstration supplier's quote/carton.
        for key in ('buy','L','W','H','g','p25','p50','p75'):
            p[key]=None
        for key in ('pcs','cL','cW','cH','cKg'):
            p[key]=0
        p['price']=24.99 if market=='US' else p['price']
        p['dutyInc']=False;p['duty']=None;p['tax']=None
    return {'marketplace':market,'evaluation_date':str(dt.date.today()),'inputs':p,
            'input_state':{k:{'value':v,'status':'default_assumption',
                              'source':'批准原型演示值' if example else '未确认的初始情景，需用户填写'} for k,v in p.items()}}


def load_product(root=None,market=None,*,example=False):
    if root is None:
        return default_payload(market or 'US',example=example)
    root=Path(root).resolve()
    store=load_module(HOME/'scripts/r01_case_store.py','starsky_margin_case_store').CompactCase(root)
    with store.materialize() as view:
        path=view.root/OUT/INPUTS
        doc=yaml.safe_load(path.read_text(encoding='utf-8-sig')) if path.is_file() else {}
        doc=doc or {}
        producer=load_module(HOME/'skills/amazon-market-research/scripts/s1_cost_model.py','starsky_margin_cost_producer')
        doc=producer.load_cost_references(view.root,doc)
        payload=engine().from_cost_inputs(doc)
        wanted=market or payload.get('marketplace','US')
        base=default_payload(wanted,example=example)
        # Never carry another site's account fee/VAT/FX as an automatic default.
        if wanted==payload.get('marketplace','US'):
            base['inputs'].update(payload['inputs']);base['input_state'].update(payload['input_state'])
            for key,value in payload['inputs'].items():
                if key not in payload['input_state']:
                    base['input_state'][key]={'value':value,'status':'missing' if value is None else 'unlabeled',
                                              'source':'当前成本输入：'+key}
        else:
            physical={'buy','tax','qty','pack','dom','oneoff','L','W','H','g','pcs','cL','cW','cH','cKg'}
            for key in physical:
                if key in payload['inputs']:
                    base['inputs'][key]=payload['inputs'][key]
                    if key in payload['input_state']:base['input_state'][key]=payload['input_state'][key]
        base.update({k:v for k,v in payload.items() if k not in ('marketplace','inputs','input_state')})
        return engine().enrich_from_case(view.root,base)


def parse_quick(text,root=None):
    payload=load_product(root)
    p=payload['inputs'];states=payload['input_state']
    supplied={}
    for key,pattern in [('buy',r'(?:采购|进价|成本单价)\s*[¥￥]?\s*(\d+(?:\.\d+)?)'),
                        ('price',r'(?:卖|售价|售价为)\s*[$＄]?\s*(\d+(?:\.\d+)?)')]:
        match=re.search(pattern,text)
        if match:supplied[key]=float(match.group(1))
    weight=re.search(r'(\d+(?:\.\d+)?)\s*(kg|千克|公斤|g|克|lb)',text,re.I)
    if weight:
        value=float(weight.group(1));unit=weight.group(2).lower()
        supplied['g']=value*(1000 if unit in ('kg','千克','公斤') else 453.59237 if unit=='lb' else 1)
    dims=re.search(r'(\d+(?:\.\d+)?)\s*[xX×*]\s*(\d+(?:\.\d+)?)\s*[xX×*]\s*(\d+(?:\.\d+)?)\s*(cm|厘米|in|英寸)',text,re.I)
    if dims:
        for key,value in zip(('L','W','H'),dims.groups()[:3]):
            supplied[key]=float(value)*(2.54 if dims.group(4).lower() in ('in','英寸') else 1)
    if not all(k in supplied or p.get(k) is not None for k in ('buy','price','g','L','W','H')):
        raise ValueError('快算需要采购单价、单包重量、三边尺寸和售价')
    for key,value in supplied.items():
        p[key]=value;states[key]={'value':value,'status':'user_attested','source':'用户快算原文',
                                'observed_at':dt.datetime.now(dt.timezone.utc).isoformat()}
    payload['source_text']=text
    return payload


def quick(text,root=None,*,offline=False):
    payload=parse_quick(text,root)
    fx=fx_rate(payload['marketplace'],offline=offline)
    if root is None or payload['input_state'].get('fx',{}).get('status')=='default_assumption':
        payload['inputs']['fx']=fx['value'];payload['input_state']['fx']=fx
    result=engine().calculate(payload)
    # Unknown tax/duty get a separate, explicitly hypothetical quick card, not actuals.
    if result['missing']:
        scenario=copy.deepcopy(payload);assumptions=[]
        for key in ('tax','duty'):
            if scenario['inputs'].get(key) is None:
                scenario['inputs'][key]=0;scenario['input_state'][key]={'value':0,'status':'default_assumption','source':'快算零税率条件情景，并非已确认费用'}
                assumptions.append(key+'=0（待确认）')
        if assumptions:
            projected=engine().calculate(scenario)
            result['conditional_scenarios']=[{'label':'条件快算；非实际利润；'+projected['main_basis']['name'],'assumptions':assumptions,
                'result':projected['main_basis']['result']}]
    result['fx_state']=fx
    return result


def format_quick_card(model):
    """Production chat formatter; arithmetic remains exclusively in the engine."""
    actual=model['result'];p=model['inputs'];conditional=bool(model.get('missing'))
    scenarios=model.get('conditional_scenarios') or []
    result=scenarios[0]['result'] if conditional and scenarios else actual
    currency=model['currency'];symbol={'USD':'$','GBP':'£','EUR':'€','JPY':'¥','AUD':'A$'}[currency]
    def money(value):return '待补齐' if value is None else f'{symbol}{value:.2f}'
    def percent(value):return '待补齐' if value is None else f'{value*100:.1f}%'
    lines=['💰 毛利快算'+('（条件测算）' if conditional else '（当前输入测算）'),'',
           f"采购 ¥{p['buy']:g} / 单包 {p['g']:g}g / {p['L']:g}×{p['W']:g}×{p['H']:g}cm",'',
           '| 售价 | 单件落地 | 广告前含预留毛利 | 广告后净利率 |',
           '|---:|---:|---:|---:|',
           f"| {money(result['price'])} | {money(result['landed_cost'])} | {percent(result['pre_ad_margin'])} | {percent(result['post_ad_margin'])} |",'']
    if conditional:
        lines.append('实际利润待确认：'+'、'.join(engine().FIELD_LABELS.get(k,k) for k in model['missing'])+'。')
        if scenarios:lines.append('上表仅为条件情景：'+'；'.join(scenarios[0]['assumptions'])+'。')
    else:
        lines.append('广告前目标价：'+money(actual.get('target_price_pre_ad'))+'；竞争力：'+actual['competitiveness']['reason']+'。')
    assumed=[engine().FIELD_LABELS.get(k,k) for k in ('frate','customs','qty','fx','inbound','srate','ret')
             if model['input_state'].get(k,{}).get('status')=='default_assumption']
    if assumed:lines.append('仍用默认情景：'+'、'.join(assumed)+'；打开计算器补齐后存回重算。')
    lines.append('本卡不构成采购授权；输入变化需重新验收。')
    return '\n'.join(lines)


def _write(path,raw):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if path.is_file() and path.read_bytes()==raw:return
    temp=path.with_name(path.name+'.r01.tmp')
    try:
        temp.write_bytes(raw);os.replace(temp,path)
    finally:
        temp.unlink(missing_ok=True)


def _call_in_scripts(function,*args,**kwargs):
    """Native generators also perform lazy imports when called as a Python API."""
    previous=list(sys.path)
    try:
        sys.path.insert(0,str(HOME/'scripts'))
        return function(*args,**kwargs)
    finally:sys.path[:]=previous


def recompute(root):
    cost=load_module(HOME/'skills/amazon-market-research/scripts/s1_cost_model.py','starsky_margin_cost_producer')
    result={'cost':cost.run(root)}
    promotion=load_module(HOME/'scripts/s1_promotion_model.py','starsky_margin_promotion')
    result['promotion']=promotion.run(root)
    try:
        receipt=Path(root)/'00_总控/starsky-fullchain-workflow/运行数据/流程控制/S1/receipts/09.json'
        if receipt.is_file():
            stage=load_module(HOME/'scripts/s1_stage_report.py','starsky_margin_stage_report')
            result['stage']=_call_in_scripts(stage.run,root,compact=True,receipt_path=receipt)
        else:
            result['stage']={'ok':False,'reason':'尚无末步正式回执；成本已重算，综合报告待正式门禁'}
            # Original native cases have no compact09 receipt. Refresh only an
            # already-delivered legacy report, never fabricate a new final step.
            structured=Path(root)/'s1_decision_inputs.yaml'
            if cost.cost_step(root)==7 and structured.is_file():
                data=yaml.safe_load(structured.read_text(encoding='utf-8-sig'))
                stage=load_module(HOME/'scripts/s1_stage_report.py','starsky_margin_stage_report')
                prior_report=any((Path(root)/path).is_file() for path in (stage.MD,stage.HTML))
                if prior_report and isinstance(data,dict) and data.get('schema_version') and data.get('product_name'):
                    decision=load_module(HOME/'scripts/s1_decision_report.py','starsky_margin_decision_report')
                    _call_in_scripts(decision.generate,root)
                    result['stage']=_call_in_scripts(stage.run,root)
                    result['stage']['legacy_refresh']=True
    except (ValueError,FileNotFoundError,KeyError,ImportError) as exc:
        result['stage']={'ok':False,'reason':str(exc)}
    return result


def save_product(root,payload):
    if root is None:raise ValueError('先绑定当前产品，才能存回')
    root=Path(root).resolve()
    if not root.is_dir():raise ValueError('产品根不存在')
    values=payload.get('inputs')
    if not isinstance(values,dict):raise ValueError('inputs 必须是object')
    edited=payload.get('edited_fields')
    if not isinstance(edited,list) or any(not isinstance(k,str) or k not in values for k in edited):
        raise ValueError('edited_fields 必须明确列出用户本次修改字段')
    allowed=set(engine().DEFAULTS)|engine().NUMERIC
    if any(key not in allowed for key in values):raise ValueError('输入包含未知字段')
    # Validate without trusting caller-supplied status/source.
    trial={'marketplace':payload.get('marketplace','US'),'inputs':values}
    engine().calculate(trial)
    store_module=load_module(HOME/'scripts/r01_case_store.py','starsky_margin_case_store')
    store=store_module.CompactCase(root)
    with store.materialize() as view:
        path=view.root/OUT/INPUTS
        old=yaml.safe_load(path.read_text(encoding='utf-8-sig')) if path.is_file() else {}
    doc=copy.deepcopy(old or {})
    before=engine().canonical_hash(doc)
    existing=load_product(root)
    states=copy.deepcopy(existing.get('input_state') or {})
    for key,value in values.items():
        if key not in edited:
            if key in existing['inputs'] and existing['inputs'][key]!=value:
                raise ValueError('未声明编辑字段发生变化：'+key)
            states.setdefault(key,{'value':value,'status':'default_assumption','source':'初始情景，用户未认可'})
    now=dt.datetime.now(dt.timezone.utc).isoformat()
    for key in edited:
        states[key]={'value':values[key],'status':'user_attested','source':'毛利计算器用户存回', 'observed_at':now}
    doc.update(schema_version='2.0',calculation_version=engine().VERSION,
               marketplace=payload.get('marketplace',doc.get('marketplace','US')),
               calculator_inputs=copy.deepcopy(values),calculator_input_state=states)
    # Exact historic source/notes remain; only the calculator-owned branch changes.
    raw=yaml.safe_dump(doc,allow_unicode=True,sort_keys=False).encode()
    native=load_module(HOME/'scripts/r01_native.py','starsky_margin_native')
    def write_inputs(native_root):
        _write(Path(native_root)/OUT/INPUTS,raw)
        return {'ok':True,'inputs':str(Path(native_root)/OUT/INPUTS),'source':'user_attested'}
    producer=load_module(HOME/'skills/amazon-market-research/scripts/s1_cost_model.py','starsky_margin_cost_producer')
    step=producer.cost_step(root)
    response=native.execute_native(root,write_inputs,step=step,report_paths=[])
    if response is NotImplemented:write_inputs(root)
    state_path=root/'workflow_state.yaml'
    if state_path.is_file():
        state=yaml.safe_load(state_path.read_text(encoding='utf-8-sig')) or {}
        acceptance=state.setdefault('stage_acceptance',{}).setdefault('S1',{})
        acceptance.update(status='pending',reason='成本输入已由用户修改，旧成本/推广/综合报告审批失效，需重新验收')
        change={'before_sha256':before,'after_sha256':engine().canonical_hash(doc),'observed_at':now,
                'affected_steps':sorted({step,7,9}),'status':'pending'}
        execution=state.get('s1_execution')
        cursor=execution.get('cursor') if isinstance(execution,dict) else None
        if isinstance(cursor,int) and not isinstance(cursor,bool) and 1<=cursor<=9:
            execution.setdefault('validation',{})['cost_input_change']=change
        else:
            state['cost_input_change']=change
        _write(state_path,yaml.safe_dump(state,allow_unicode=True,sort_keys=False).encode())
    result=recompute(root)
    return {'ok':True,'inputs':str(root/OUT/INPUTS),'source':'user_attested',
            'changed_fields':edited,'approval_status':'pending','recompute':result}


def make_server(root=None,*,port=0,offline=False):
    root=Path(root).resolve() if root else None
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def respond(self,obj,status=200,content_type='application/json; charset=utf-8'):
            raw=obj if isinstance(obj,bytes) else json.dumps(obj,ensure_ascii=False,allow_nan=False).encode()
            self.send_response(status);self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(raw)
        def do_GET(self):
            url=urllib.parse.urlsplit(self.path);query=urllib.parse.parse_qs(url.query)
            try:
                if url.path=='/':return self.respond((HOME/'assets/margin_calculator.html').read_bytes(),content_type='text/html; charset=utf-8')
                if url.path=='/api/status':return self.respond({'ok':True,'pid':os.getpid(),'root':str(root) if root else None,'plugin_home':str(HOME.resolve()),'service_resource_sha256':SERVICE_RESOURCE_SHA256,'engine_sha256':engine().ENGINE_SOURCE_SHA256,'calculator_sha256':CALCULATOR_SOURCE_SHA256})
                if url.path=='/api/bootstrap':return self.respond({'payload':load_product(root,query.get('market',[None])[0],example=query.get('example')==['1']),'root':str(root) if root else None,'display_names':engine().FIELD_LABELS,'status_labels':engine().STATUS_LABELS})
                if url.path=='/api/fx':return self.respond(fx_rate(query.get('market',['US'])[0],offline=offline))
                if url.path in ('/report','/api/purchase-checklist') and root:
                    stage=load_module(HOME/'scripts/s1_stage_report.py','starsky_margin_stage_report')
                    if url.path=='/api/purchase-checklist':return self.respond({'ok':True,'checklist':stage.purchase_checklist(root)})
                    # Current formal report only. No arbitrary local file serving.
                    report=(root/stage.HTML).resolve()
                    if not report.is_relative_to(root) or not report.is_file():raise ValueError('缺正式S1报告，请先完成末步门禁')
                    self.send_response(302);self.send_header('Location','/'+urllib.parse.quote(report.relative_to(root).as_posix(),safe='/'))
                    self.end_headers();return
                if root:
                    relative=urllib.parse.unquote(url.path).lstrip('/')
                    target=(root/relative).resolve()
                    permitted=target.is_relative_to(root) and target.relative_to(root).parts[0] in ('S1_选品板块','00_总控')
                    types={'.html':'text/html; charset=utf-8','.md':'text/plain; charset=utf-8',
                           '.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp',
                           '.gif':'image/gif','.mp4':'video/mp4','.svg':'image/svg+xml'}
                    if permitted and target.is_file() and target.suffix.lower() in types:
                        return self.respond(target.read_bytes(),content_type=types[target.suffix.lower()])
                return self.respond({'ok':False,'error':'not found'},404)
            except (ValueError,FileNotFoundError,KeyError,AttributeError) as exc:
                return self.respond({'ok':False,'error':str(exc)},400)
        def do_POST(self):
            origin=self.headers.get('Origin')
            expected=f'http://127.0.0.1:{self.server.server_port}'
            if origin is not None and origin!=expected:
                return self.respond({'ok':False,'error':'cross-origin request rejected'},403)
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':
                return self.respond({'ok':False,'error':'JSON required'},415)
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=2_000_000:raise ValueError('invalid body length')
                payload=json.loads(self.rfile.read(length))
                if self.path=='/api/calculate':return self.respond(engine().calculate(payload))
                if self.path=='/api/save':return self.respond(save_product(root,payload))
                if self.path=='/api/purchase-checklist' and root:
                    stage=load_module(HOME/'scripts/s1_stage_report.py','starsky_margin_stage_report')
                    return self.respond(stage.save_purchase_checklist(root,payload.get('updates')))
                return self.respond({'ok':False,'error':'not found'},404)
            except (ValueError,KeyError,TypeError,FileNotFoundError) as exc:
                return self.respond({'ok':False,'error':str(exc)},400)
    return ThreadingHTTPServer(('127.0.0.1',port),Handler)


def _runtime_info(root):
    runtime=Path(tempfile.gettempdir())/'starsky-margin-calculator'
    identity=hashlib.sha256((str(Path(root).resolve()) if root else 'standalone').encode()).hexdigest()[:20]
    return runtime/(identity+'.json')


def existing_url(root=None):
    """Read an existing service only; never start a process or contact the Internet."""
    try:
        info=_runtime_info(root)
        candidate=json.loads(info.read_text(encoding='utf-8'))
        parsed=urllib.parse.urlsplit(candidate['url'])
        if parsed.scheme=='http' and parsed.hostname=='127.0.0.1' and parsed.path=='/' and not parsed.username and not parsed.password:
            with urllib.request.urlopen(candidate['url']+'api/status',timeout=.35) as response:
                status=json.load(response)
            expected_root=str(Path(root).resolve()) if root else None
            current=hashlib.sha256((HOME/'scripts/s1_cost_engine.py').read_bytes()).hexdigest()
            calculator_current=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
            if (status.get('root')==expected_root and status.get('plugin_home')==str(HOME.resolve())
                    and status.get('service_resource_sha256')==_service_fingerprint()
                    and status.get('engine_sha256')==current and status.get('calculator_sha256')==calculator_current):
                return candidate['url']
    except (OSError,ValueError,KeyError):return None
    return None


def open_calculator(root=None,*,no_browser=False,offline=False):
    info=_runtime_info(root);info.parent.mkdir(exist_ok=True)
    url=existing_url(root)
    result=json.loads(info.read_text(encoding='utf-8')) if url else None
    if result is None:
        command=[sys.executable,str(Path(__file__).resolve()),'serve','--info',str(info)]
        if root:command+=['--root',str(Path(root).resolve())]
        if offline:command+=['--offline']
        flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
        process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                 creationflags=flags,close_fds=True)
        for _ in range(80):
            if process.poll() is not None:raise ValueError('计算器服务启动失败')
            try:
                result=json.loads(info.read_text(encoding='utf-8'))
                if result.get('pid')==process.pid:break
            except (OSError,ValueError):pass
            time.sleep(.05)
        else:
            process.terminate();raise ValueError('计算器服务启动超时')
    if not no_browser:webbrowser.open(result['url'])
    return {'ok':True,**result,'browser_opened':not no_browser}


def main(argv=None):
    parser=argparse.ArgumentParser(description='星空毛利计算器，共用Python成本引擎')
    subs=parser.add_subparsers(dest='command',required=True)
    for command in ('open','quick','save','serve'):
        p=subs.add_parser(command);p.add_argument('--root');p.add_argument('--json',action='store_true');p.add_argument('--offline',action='store_true')
        if command=='open':p.add_argument('--no-browser',action='store_true')
        if command=='quick':p.add_argument('--text',required=True)
        if command=='save':p.add_argument('--input-json',required=True)
        if command=='serve':p.add_argument('--info',required=True)
    args=parser.parse_args(argv)
    try:
        if args.command=='open':result=open_calculator(args.root,no_browser=args.no_browser,offline=args.offline)
        elif args.command=='quick':result=quick(args.text,args.root,offline=args.offline)
        elif args.command=='save':
            payload=json.load(sys.stdin) if args.input_json=='-' else json.loads(Path(args.input_json).read_text(encoding='utf-8-sig'))
            result=save_product(args.root,payload)
        else:
            server=make_server(args.root,offline=args.offline)
            _write(Path(args.info),json.dumps({'url':f'http://127.0.0.1:{server.server_port}/','pid':os.getpid(),'root':args.root}).encode())
            try:server.serve_forever(poll_interval=.25)
            finally:server.server_close();Path(args.info).unlink(missing_ok=True)
            return 0
        if args.command=='quick' and not args.json:
            print(format_quick_card(result))
        else:
            print(json.dumps(result,ensure_ascii=False,allow_nan=False,default=str,indent=2))
        return 0
    except (ValueError,FileNotFoundError) as exc:
        print(json.dumps({'ok':False,'error':str(exc)},ensure_ascii=False));return 1


if __name__=='__main__':raise SystemExit(main())
