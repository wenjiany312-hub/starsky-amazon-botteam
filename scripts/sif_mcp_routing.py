"""Plan-bound SIF receipts. Native tools belong to the host; Python never invokes them.

The explicit bridge is a separate MCP client. It must receive the correct host
config, probes once per task, and stops on a dead transport or denied permission.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone
import uuid


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def short_tool(tool):
    return str(tool).split('__')[-1]


def request_key(call, identity):
    return digest({'tool':short_tool(call.get('tool') or call.get('tool_name')),
                   'parameters':call.get('parameters') or {},'identity':identity})


def make_plan(calls, identity):
    calls=[dict(call,logical_request_key=request_key(call,identity),
                parameters_sha256=digest(call['parameters'])) for call in calls]
    return {'schema_version':1,'plan_id':digest({'identity':identity,'calls':calls}),
            'identity':identity,'marketplace':identity['marketplace'],
            'package_id':'SIF-'+identity['product_id'],'native_available':None,
            'instructions':'由宿主核对当前可调用工具及schema，按实际名称原生调用；Python不调用注入工具。'
                '原始结果经record-native写入独立attempt；失败同样保留。无原生工具时明确选择bridge并传实际宿主--mcp-config；'
                '无可用通道则保留缺口，不编造成功。', 'calls':calls}


def payload(result):
    if not isinstance(result,dict):return None
    if result.get('isError'):return None
    if isinstance(result.get('structuredContent'),dict):
        body=result['structuredContent']
        return None if body.get('error') or body.get('isError') else body
    text=''.join(c.get('text','') for c in result.get('content') or [] if c.get('type')=='text')
    try:
        value=json.loads(text)
        return value if isinstance(value,dict) and not value.get('error') and not value.get('isError') else None
    except (ValueError,TypeError):return None


SAFE_CATEGORIES={'transport','timeout','authorization_denied','rate_limited','tool_error','invalid_response',
    'not_configured','disabled','tool_disabled','missing_credential','config_unreadable','missing_transport',
    'tool_not_found','invalid_arguments_or_file','connection_or_tool_failed'}
STOP_CATEGORIES={'transport','timeout','authorization_denied','rate_limited','missing_credential','disabled',
                 'not_configured','config_unreadable','missing_transport','connection_or_tool_failed','invalid_response'}


def error_category(envelope):
    category=envelope.get('error_category') or (envelope.get('diagnostics') or {}).get('category')
    if category in SAFE_CATEGORIES:return category
    if (envelope.get('result') or {}).get('isError'):return 'tool_error'
    text=str(envelope.get('error') or '')
    code=text.split(':',1)[0]
    if code in SAFE_CATEGORIES:return code
    return 'invalid_response'


def safe_detail(value):
    value=value if isinstance(value,dict) else {}
    types={'ConnectError','ConnectionError','ReadError','WriteError','ReadTimeout','ConnectTimeout',
           'TimeoutError','TimeoutExpired','HTTPStatusError','FileNotFoundError','BrokenPipeError','OSError',
           'PermissionError','EndOfStream','ClosedResourceError','RuntimeError'}
    return {'phase':value.get('phase') if value.get('phase') in {'config','start','initialize','list','call','decode','probe'} else 'unknown',
        'leaf_types':sorted({name if name in types else 'OtherError' for name in value.get('leaf_types',[]) if isinstance(name,str)}),
        'http_status':sorted({code for code in value.get('http_status',[]) if code in {400,401,403,404,408,409,429,500,502,503,504}})}


def record_attempt(out, plan, call, *, tool_name, channel, response=None, status='success', error=None, called_at=None, diagnostics=None):
    if channel not in {'native_mcp','direct_bridge'}:raise ValueError('actual_channel_required')
    if short_tool(tool_name)!=call['tool']:raise ValueError('actual_tool_mismatch')
    attempt=uuid.uuid4().hex
    success=status=='success' and isinstance(response,dict) and not response.get('isError') and not response.get('error')
    doc={'call_id':call['call_id'],'logical_request_key':request_key(call,plan['identity']),
         'attempt_id':attempt,'plan_id':plan['plan_id'],'identity':plan['identity'],
         'tool_name':tool_name,'channel':channel,'called_at':called_at or datetime.now(timezone.utc).isoformat(),
         'parameters':call['parameters'],'parameters_sha256':digest(call['parameters']),
         'status':'success' if success else 'failed','response':response if success else None,
         'error_category':None if success else error if error in SAFE_CATEGORIES else 'tool_error'}
    if diagnostics is not None:doc['diagnostics']=safe_detail(diagnostics)
    folder=Path(out)/'mcp/calls';folder.mkdir(parents=True,exist_ok=True)
    path=folder/(call['call_id']+'-'+attempt+'.json')
    # Exclusive creation never replaces an earlier raw attempt.
    with path.open('x',encoding='utf8') as handle:json.dump(doc,handle,ensure_ascii=False,indent=1)
    return doc


def fetch_bridge(out, plan, server, *, route='native', config=None, retry=False, runner=None):
    if route!='bridge':
        return {'status':'native_calls_required','native_available':None,'remaining':len(plan['calls']),
                'manual_plan':'宿主核对实际注入工具/schema后调用并落盘；无可用工具时明确选择bridge和宿主配置。'}
    if config is None:raise ValueError('explicit_host_mcp_config_required')
    config=Path(config).resolve()
    if not config.is_file():raise ValueError('mcp_config_missing')
    out=Path(out);folder=out/'mcp';folder.mkdir(parents=True,exist_ok=True)
    route_id=digest({'plan_id':plan['plan_id'],'config_path':str(config),
                     'config_sha256':hashlib.sha256(config.read_bytes()).hexdigest(),'server':server})
    state_file=folder/('route-'+route_id+'.json')
    state=json.loads(state_file.read_text('utf8')) if state_file.is_file() else {}
    if state.get('circuit_open') and not retry:
        return {'status':'failed','circuit_open':True,'probe':state['probe'],
                'remaining':len(plan['calls']),'manual_plan':'修复配置/授权或运输原因后显式--mcp-retry；不自动换通道。'}
    runner=runner or subprocess.run
    prefix=[sys.executable,str(Path(__file__).with_name('starsky_mcp.py')),'--config',str(config),
            '--server',server,'--timeout','180']
    def invoke(tail):
        try:
            proc=runner(prefix+tail,capture_output=True,text=True,timeout=240)
            value=json.loads((proc.stdout.strip().splitlines() or ['{}'])[-1])
            return value if isinstance(value,dict) else {'ok':False,'error':'invalid_response'}
        except subprocess.TimeoutExpired:return {'ok':False,'diagnostics':{'category':'timeout'}}
        except (OSError,ValueError):return {'ok':False,'error':'invalid_response'}
    if state.get('probe',{}).get('status')=='success' and not retry:
        probe_receipt=state['probe'];available=set(state['tools'])
    else:
        probe=invoke(['list'])
        probe_receipt={'attempt_id':uuid.uuid4().hex,'plan_id':plan['plan_id'],'channel':'direct_bridge',
            'called_at':datetime.now(timezone.utc).isoformat(),'status':'success' if probe.get('ok') else 'failed',
            'error_category':None if probe.get('ok') else error_category(probe),'phase':'probe',
            'diagnostics':safe_detail(probe.get('diagnostics'))}
        (folder/('probe-'+probe_receipt['attempt_id']+'.json')).write_text(json.dumps(probe_receipt),encoding='utf8')
        available={t['name'] for t in (probe.get('result') or {}).get('tools',[]) if isinstance(t,dict) and 'name' in t}
    state={'probe':probe_receipt,'tools':sorted(available),'circuit_open':probe_receipt['status']!='success'}
    done=[]
    if probe_receipt['status']=='success':
        for call in plan['calls']:
            if call['tool'] not in available:
                done.append(record_attempt(out,plan,call,tool_name=call['tool'],channel='direct_bridge',
                                           status='failed',error='tool_not_found'))
                continue
            args_file=folder/('.args-'+uuid.uuid4().hex+'.json')
            try:
                args_file.write_text(json.dumps(call['parameters'],ensure_ascii=False),encoding='utf8')
                envelope=invoke(['call',call['tool'],'@'+str(args_file)])
            finally:args_file.unlink(missing_ok=True)
            response=payload(envelope.get('result')) if envelope.get('ok') else None
            category=error_category(envelope) if not envelope.get('ok') else 'invalid_response'
            doc=record_attempt(out,plan,call,tool_name=call['tool'],channel='direct_bridge',response=response,
                               status='success' if response is not None else 'failed',error=category,diagnostics=envelope.get('diagnostics'))
            done.append(doc)
            if doc['status']=='failed' and category in STOP_CATEGORIES:
                state['circuit_open']=True
                state['last_error_category']=category
                break
    temp=state_file.with_suffix('.tmp')
    temp.write_text(json.dumps(state),encoding='utf8');temp.replace(state_file)
    success=sum(d['status']=='success' for d in done)
    return {'status':'fetched' if success==len(plan['calls']) else 'partial' if success else 'failed',
            'probe':probe_receipt,'calls':[{'attempt_id':d['attempt_id'],'status':d['status'],
                'call_id':d['call_id'],'error_category':d['error_category']} for d in done],
            'remaining':len(plan['calls'])-len(done)}


def returned_count(tool, resp):
    for key in ('profiles','keywords','top_keywords','top_competitors','dates'):
        if isinstance(resp.get(key),list):return len(resp[key])
    return 0


def data_status(tool, resp):
    if returned_count(tool,resp):return 'data'
    return 'empty' if any(isinstance(resp.get(k),list) for k in ('profiles','keywords','top_keywords','top_competitors','dates')) else 'missing_fields'


def merge_receipts(root, out, marketplace, plan):
    root,out=Path(root),Path(out)
    parsed={k:[] for k in ('demand','history','root_trend','competition')}
    identity=plan.get('identity') or {}
    expected={request_key(c,identity):c for c in plan.get('calls',[])}
    calls=[];selected={}
    for path in sorted((out/'mcp/calls').glob('*.json')):
        raw=path.read_bytes();raw_hash=hashlib.sha256(raw).hexdigest()
        try:
            doc=json.loads(raw.decode('utf-8-sig'))
            if not isinstance(doc,dict):raise ValueError('receipt_not_object')
        except (ValueError,UnicodeError):
            calls.append({'attempt_id':raw_hash,'raw_path':path.relative_to(root).as_posix(),
                          'sha256':'sha256:'+raw_hash,'eligible':False,'result_status':'failed',
                          'channel':'unknown','error_category':'invalid_response'});continue
        tool=str(doc.get('tool_name') or doc.get('tool') or '')
        short=short_tool(tool);params=doc.get('parameters') or {}
        # The plan names tools without the host prefix; a receipt registered under that short name is
        # still a SIF call, so it is reported under the SIF namespace the package contract expects.
        if tool and 'sif' not in tool.casefold():tool='mcp__sif__'+short
        key=request_key({'tool':short,'parameters':params},identity)
        channel=doc.get('channel') if doc.get('channel') in {'native_mcp','direct_bridge'} else 'unknown'
        # Historical migration is explicit and hash-bound, never guessed from a filename.
        historical=plan.get('evidence_mode')=='historical_replay' and raw_hash in plan.get('legacy_receipt_hashes',[])
        called=str(doc.get('called_at') or '')
        # A receipt proves one logical request (tool + parameters + product/marketplace/date/source identity), so a
        # rebuilt plan that still asks the same request reuses it; plan_id only names the plan instance and changes
        # whenever any other call or a review decision changes. Changed requests get a new key and are refetched.
        eligible=(key in expected and channel!='unknown' and marketplace==identity.get('marketplace') and
            (historical or doc.get('identity')==identity) and
            (historical or not identity.get('collection_date') or called[:10]==identity['collection_date']) and
            (not doc.get('parameters_sha256') or doc['parameters_sha256']==digest(params)) and
            (not doc.get('logical_request_key') or doc['logical_request_key']==key))
        response=doc.get('response')
        success=doc.get('status')=='success' and isinstance(response,dict) and not response.get('isError') and not response.get('error')
        row={'attempt_id':doc.get('attempt_id') or raw_hash,'logical_request_key':key,'tool_name':tool,
            'called_at':called,'channel':channel,'parameters_sha256':digest(params),
            'parameters_summary':json.dumps(params,ensure_ascii=False)[:300],
            'result_status':'success' if success else 'failed','returned_count':returned_count(short,response if isinstance(response,dict) else {}),
            'data_status':data_status(short,response) if success else 'not_returned',
            'raw_path':path.relative_to(root).as_posix(),'sha256':'sha256:'+raw_hash,'eligible':eligible,
            'error_category':None if success else error_category(doc)}
        calls.append(row)
        if eligible and success:
            # Latest effective success once; a later failure never erases it.
            rank=(called,raw_hash)
            if key not in selected or rank>selected[key][0]:selected[key]=(rank,row,doc)
    sources=[]
    for key,(_,row,doc) in sorted(selected.items()):
        resp=doc['response'];params=doc.get('parameters') or {};short=short_tool(row['tool_name'])
        dates=[str(d) for d in resp.get('dates') or []];called=row['called_at']
        sid='SRC-SIF-MCP-'+key[:20]
        source={'source_id':sid,'provider':'sif','acquisition_method':'mcp','tool_name':short,
            'sha256':row['sha256'],'subject':doc.get('subject') or params.get('subject') or ('unknown' if params.get('asin') or params.get('asins') else 'market'),
            **({k:params[k] for k in ('asin','asins') if k in params}),
            'raw_path':row['raw_path'],'marketplace':marketplace,'as_of':called[:10],
            'period':doc.get('period') or {'start':dates[0] if dates else None,'end':dates[-1] if dates else called[:10],'month':called[:7]},
            'units':{'volumes':'aba_search_volume_per_period'},'field_mapping':{},'missing_fields':[],
            'logical_request_key':key,'attempt_id':row['attempt_id']}
        row['source_id']=sid;sources.append(source)
        if short in {'market_screen_keyword_opportunities','market_get_asin_keyword_signals','market_get_asin_aba_footprint'}:
            parsed.setdefault(short,[]).append((sid,params,resp))
        group=next((k for k in ('demand','history','root_trend','competition') if k in short),None)
        if group:parsed[group].append((sid,params,resp))
    total=len(expected);success=len(selected)
    mcp={'status':'used' if success and success==total else 'partial' if success else 'failed' if calls else 'skipped',
        'calls':calls,'logical_total':total,'logical_success':success,'logical_missing':total-success,
        'semantic_missing':sum(row['data_status']!='data' for _,row,_ in selected.values()),
        'effective_channels':sorted({row['channel'] for _,row,_ in selected.values()}),
        'missing_requests':[{'call_id':c['call_id'],'tool':c['tool']} for k,c in expected.items() if k not in selected],
        'evidence_mode':plan.get('evidence_mode','current_plan'),'plan_id':plan.get('plan_id')}
    if not success:mcp['skipped_reason']='尚无本计划的有效成功记录；宿主按计划调用真实可用工具，或明确选择bridge及宿主配置。'
    return sources,mcp,parsed


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['record-native'])
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--call-id',required=True)
    parser.add_argument('--tool',required=True)
    outcome=parser.add_mutually_exclusive_group(required=True)
    outcome.add_argument('--result',type=Path,help='Host-written unmodified MCP result envelope')
    outcome.add_argument('--failure-category',choices=sorted(SAFE_CATEGORIES),help='Actual failed host call; no fabricated response')
    parser.add_argument('--called-at',help='Actual host call time, ISO 8601')
    args=parser.parse_args()
    plan=json.loads(args.plan.read_text('utf-8-sig'))
    call=next((c for c in plan['calls'] if c['call_id']==args.call_id),None)
    if call is None:parser.error('call_id_not_in_plan')
    result=json.loads(args.result.read_text('utf-8-sig')) if args.result else {}
    response=payload(result) if args.result else None
    doc=record_attempt(args.plan.parent,plan,call,tool_name=args.tool,channel='native_mcp',response=response,
        status='success' if response is not None else 'failed',called_at=args.called_at,
        error=args.failure_category or ('tool_error' if result.get('isError') else 'invalid_response'))
    print(json.dumps({'attempt_id':doc['attempt_id'],'status':doc['status'],'native_available':'host_call_receipt_only'}))
    return 0 if doc['status']=='success' else 2


if __name__=='__main__':raise SystemExit(main())
