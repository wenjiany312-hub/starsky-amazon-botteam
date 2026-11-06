"""Collect and verify R05 adjacent categories from complete, hash-bound MCP receipts.

Only genuine siblings in an actual parent category_tree are selected by default.
The target and parent are research inputs, never hardcoded category mappings.
"""
from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import uuid

import yaml
from s1_acceptance_contract import decode, floors, tool_name

CONTRACT = 'r05.adjacent.v1'
MARKET = Path('S1_选品板块/amazon-market-research')
SCOPE = MARKET/'comparison_scope.yaml'
SITES = {'US','GB','DE','FR','IN','CA','JP','ES','IT','MX','AE','AU','BR','SA'}


def required(root, execution=None):
    if execution is None:
        path=Path(root)/'workflow_state.yaml'
        state=yaml.safe_load(path.read_bytes()) or {} if path.is_file() else {}
        execution=state.get('s1_execution') or {}
    return execution.get('category_comparison_contract') == CONTRACT


def _hash(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe(root, relative):
    path=(Path(root)/relative).resolve()
    if not path.is_relative_to(Path(root).resolve()):raise ValueError('category evidence source escapes product root')
    return path


def _window(plan):
    start=date.fromisoformat(plan['query_start']);end=date.fromisoformat(plan['query_date'])
    if start.strftime('%Y-%m')!=end.strftime('%Y-%m') or not 3<=(end-start).days<=40:
        raise ValueError('category reports require one identical month/window (3–40 days)')
    return plan['query_start'],plan['query_date']


def _metadata(value):
    """Visit envelopes before decode discards status/pagination siblings of data."""
    if isinstance(value,str):
        try:parsed=json.loads(value)
        except (ValueError,TypeError):return
        yield from _metadata(parsed)
    elif isinstance(value,dict):
        yield value
        for key,child in value.items():
            if key!='doc':yield from _metadata(child)
    elif isinstance(value,list):
        for child in value:yield from _metadata(child)


def _check_response(response, site, discovery=False):
    metadata=list(_metadata(response))
    for value in metadata:
        if value.get('isError') or value.get('error') or value.get('success') is False or value.get('ok') is False:
            raise ValueError('category response reports a failed call')
        for key in ('amz_site','site'):
            if value.get(key) is not None and value[key]!=site:raise ValueError('category response site mismatch')
        if discovery and (value.get('has_more') or value.get('hasMore') or value.get('next_page') or
                          value.get('nextCursor') or value.get('complete') is False):
            raise ValueError('category discovery is incomplete')
    return metadata


def _children(response, parent, site):
    """The tree API returns children of the requested node, not search hits.

    Retain the entire response; reject pagination/error/empty envelopes rather
    than interpreting them as proof that the target has no adjacent categories.
    """
    metadata=_check_response(response,site,discovery=True)
    data=decode(response)
    if isinstance(data,dict):
        if data.get('node_id') is not None and str(data['node_id'])!=parent:
            raise ValueError('category discovery parent mismatch')
        data=next((data[key] for key in ('children','categories','nodes') if isinstance(data.get(key),list)),None)
    if not isinstance(data,list) or not data:raise ValueError('category discovery empty or unsupported; no NA proof')
    for envelope in metadata:
        for key in ('total','total_count','totalCount'):
            if key in envelope and (type(envelope[key]) is not int or envelope[key]!=len(data)):
                raise ValueError('category discovery total proves an incomplete/invalid response')
    result={}
    for row in data:
        if not isinstance(row,dict) or not row.get('node_id'):raise ValueError('category discovery lacks node identity')
        node=str(row['node_id'])
        if node==parent or node in result:raise ValueError('category discovery repeats node or parent')
        if row.get('parent_id',row.get('parent_node_id',parent)) not in (parent, int(parent) if parent.isdigit() else parent):
            raise ValueError('category discovery contains a different parent')
        result[node]=row
    return result


def _stats(call, scope, node):
    args=call['arguments'];data=decode(call['response'])
    _check_response(call['response'],scope['amz_site'])
    expected={'amz_site':scope['amz_site'],'node_id':node,
              'query_start':scope['query_start'],'query_date':scope['query_date']}
    if tool_name(call['tool'])!='category_report' or args!=expected or call.get('ok') is not True:
        raise ValueError('category report request scope mismatch or failed call')
    if not isinstance(data,dict) or str(data.get('node_id'))!=node:
        raise ValueError('category report response node mismatch')
    for key in ('amz_site','query_start','query_date'):
        if key in data and data[key]!=expected[key]:raise ValueError('category report response '+key+' mismatch')
    stats={}
    for key in ('top100_monthly_sales_volume','top100_monthly_sales_amount'):
        value=data.get(key)
        if not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value) or value<0:
            raise ValueError('category report missing numeric '+key)
        stats[key]=value
    return stats


def collect(document, root, call_tool):
    """Invoke real transport; save full raw calls even when discovery/report fails."""
    root=Path(root).resolve();plan=deepcopy(document.get('category_comparison_plan') or {})
    scope={'schema':CONTRACT,'amz_site':plan.get('amz_site'),'target_node_id':str(plan.get('target_node_id') or ''),
        'parent_node_id':str(plan.get('parent_node_id') or ''),'query_start':plan.get('query_start'),
        'query_date':plan.get('query_date'),'node_ids':[],'selected':[],'reports':[],'sources':[],'status':'data_gap'}
    calls=[]
    def invoke(tool,args):
        call={'tool':tool,'arguments':deepcopy(args),'request_id':str(uuid.uuid4()),
              'response_id':str(uuid.uuid4()),'queried_at':datetime.now(timezone.utc).isoformat()}
        try:
            call['response']=call_tool(tool,args)
            if isinstance(call['response'],dict) and call['response'].get('isError'):
                raise ValueError('MCP returned isError: '+json.dumps(call['response'],ensure_ascii=False))
            call['ok']=True
        except Exception as error:call.update(ok=False,error=str(error))
        calls.append(call)
        return {'index':len(calls)-1,'request_id':call['request_id'],'response_id':call['response_id']},call
    try:
        if scope['amz_site'] not in SITES or not scope['target_node_id'] or not scope['parent_node_id']:
            raise ValueError('category_comparison_plan needs actual amz_site, target_node_id and parent_node_id')
        _window(scope)
        scope['discovery'],tree=invoke('category_tree',{'amz_site':scope['amz_site'],'node_id':scope['parent_node_id']})
        if not tree['ok']:raise ValueError(tree['error'])
        children=_children(tree['response'],scope['parent_node_id'],scope['amz_site'])
        if scope['target_node_id'] not in children:raise ValueError('category target not in discovered parent children')
        neighbors=sorted(set(children)-{scope['target_node_id']})
        if not neighbors:
            scope['status']='not_applicable'
        else:
            selected=plan.get('selected')
            if selected is None:
                selected=[{'node_id':node,'reason':'实际 category_tree 同一直接父节点下的相邻子类；与目标并列比较。'} for node in neighbors[:3]]
            scope['selected']=deepcopy(selected)
            scope['node_ids']=[str(row['node_id']) for row in selected]
            if len(set(scope['node_ids']))!=len(scope['node_ids']) or not set(scope['node_ids'])<=set(neighbors):
                raise ValueError('category selection repeats target/node or is outside discovered siblings')
            if any(not row.get('reason') for row in selected):raise ValueError('category selection needs evidence-linked reasons')
            for node in scope['node_ids']:
                ref,call=invoke('category_report',{'amz_site':scope['amz_site'],'node_id':node,
                    'query_start':scope['query_start'],'query_date':scope['query_date']})
                ref['node_id']=node
                scope['reports'].append(ref)
                try:ref['statistics']=_stats(call,scope,node)
                except (ValueError,KeyError,TypeError) as error:ref['error']=str(error)
            scope['status']='collected'
    except (ValueError,KeyError,TypeError) as error:scope['collection_error']=str(error)
    raw_path=root/MARKET/'运行数据'/('adjacent_'+uuid.uuid4().hex+'.json')
    raw_path.parent.mkdir(parents=True,exist_ok=True)
    raw_path.write_text(json.dumps({'schema':CONTRACT,'calls':calls},ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    scope['sources']=[{'source':raw_path.relative_to(root).as_posix(),'sha256':_hash(raw_path)}]
    scope_path=_safe(root,SCOPE);scope_path.parent.mkdir(parents=True,exist_ok=True)
    scope_path.write_text(yaml.safe_dump(scope,allow_unicode=True,sort_keys=False),encoding='utf8')
    collect_target(root,scope,call_tool)
    return category_coverage(root)


TARGET_CONTRACT='r09.target_category.v1'


def collect_target(root, scope, call_tool):
    """R09: the market report needs the target node's own Top100 report and 24-month trend.

    Saved as a separate bundle so the adjacent-comparison bundle keeps its exact call count.
    Failures are recorded, never invented; the report then lists the gap."""
    site=scope.get('amz_site');node=str(scope.get('target_node_id') or '')
    if site not in SITES or not node:return None
    requests=[('category_report',{'amz_site':site,'node_id':node,
                                  **({'query_start':scope['query_start'],'query_date':scope['query_date']} if scope.get('query_start') and scope.get('query_date') else {})}),
              ('category_trend',{'amz_site':site,'node_id':node,'trend_index':'SalesCount'})]
    calls=[]
    for tool,args in requests:
        call={'tool':tool,'arguments':deepcopy(args),'request_id':str(uuid.uuid4()),'response_id':str(uuid.uuid4()),
              'queried_at':datetime.now(timezone.utc).isoformat()}
        try:
            call['response']=call_tool(tool,args)
            if isinstance(call['response'],dict) and call['response'].get('isError'):
                raise ValueError('MCP returned isError: '+json.dumps(call['response'],ensure_ascii=False))
            call['ok']=True
        except Exception as error:call.update(ok=False,error=str(error))
        calls.append(call)
    path=root/MARKET/'运行数据'/('target_category_'+uuid.uuid4().hex+'.json')
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps({'schema':TARGET_CONTRACT,'calls':calls},ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return path


def category_coverage(root):
    """Derive coverage by rereading full source receipts; never trust declared counts."""
    required_count=floors()['category_comparison']
    result={'contract':CONTRACT,'observed':0,'required':required_count,'tool':'category_report','status':'data_gap',
        'sources':[],'source_manifest':[],'validation_errors':[],
        'reason':'category comparison requires three distinct source-bound adjacent reports',
        'next_action':'按真实类目树补采同站同月三个相邻类目 category_report，再运行需求续拉与市场事实。'}
    try:
        scope_path=_safe(root,SCOPE);scope=yaml.safe_load(scope_path.read_bytes())
        if not isinstance(scope,dict) or scope.get('schema')!=CONTRACT:raise ValueError('category comparison scope schema missing')
        if scope.get('amz_site') not in SITES or not scope.get('target_node_id') or not scope.get('parent_node_id'):
            raise ValueError('category scope identity missing')
        _window(scope)
        # One dedicated immutable bundle binds all discovery and report calls.
        sources=scope.get('sources') or []
        if len(sources)!=1:raise ValueError('category comparison needs one bound full-call bundle')
        source=sources[0];path=_safe(root,source['source'])
        if _hash(path)!=source['sha256']:raise ValueError('category evidence source hash mismatch')
        bundle=json.loads(path.read_bytes())
        if bundle.get('schema')!=CONTRACT:raise ValueError('category bundle schema mismatch')
        calls=bundle['calls']
        ids=[(c['request_id'],c['response_id']) for c in calls]
        if len({a for a,b in ids})!=len(ids) or len({b for a,b in ids})!=len(ids):raise ValueError('category duplicate receipt IDs')
        def resolve(ref):
            if type(ref['index']) is not int or ref['index']<0:raise ValueError('category invalid call index')
            call=calls[ref['index']]
            if any(not call.get(key) or call[key]!=ref.get(key) for key in ('request_id','response_id')):
                raise ValueError('category request/response identity mismatch')
            return call
        tree=resolve(scope['discovery'])
        if tool_name(tree['tool'])!='category_tree' or tree.get('ok') is not True or tree['arguments']!={'amz_site':scope['amz_site'],'node_id':scope['parent_node_id']}:
            raise ValueError('category discovery request mismatch')
        children=_children(tree['response'],str(scope['parent_node_id']),scope['amz_site'])
        target=str(scope['target_node_id'])
        if target not in children:raise ValueError('category target outside discovered siblings')
        neighbors=set(children)-{target}
        result['sources']=[SCOPE.as_posix(),source['source']]
        result['source_manifest']=[{'source':SCOPE.as_posix(),'sha256':_hash(scope_path)},deepcopy(source)]
        if scope.get('status')=='not_applicable':
            if neighbors or scope.get('node_ids') or scope.get('selected') or scope.get('reports') or len(calls)!=1:
                raise ValueError('category no-adjacent claim contradicted by actual discovery')
            result.update(observed=None,status='not_applicable',error='no_adjacent_categories',
                reason='完整真实类目树证明目标在直接父节点下没有其他相邻子类。',
                not_applicable_evidence={'kind':'category_tree_no_adjacent','tool':'category_tree',
                    'source':SCOPE.as_posix(),'sha256':_hash(scope_path)})
            return result
        selected=scope.get('selected') or [];nodes=[str(row['node_id']) for row in selected]
        if nodes!=scope.get('node_ids') or len(set(nodes))!=len(nodes) or not set(nodes)<=neighbors:
            raise ValueError('category selected nodes repeated, target or outside discovered adjacent scope')
        if any(not isinstance(row.get('reason'),str) or not row['reason'].strip() for row in selected):raise ValueError('category adjacency reasons missing')
        reports=scope.get('reports') or []
        if len(reports)!=len(nodes) or len(calls)!=1+len(reports):raise ValueError('category report receipt count mismatch')
        observed=set();indices=set()
        for ref in reports:
            node=str(ref['node_id'])
            if node not in nodes or node in observed or ref['index'] in indices or ref['index']==scope['discovery']['index']:
                raise ValueError('category report repeated or outside selected scope')
            stats=_stats(resolve(ref),scope,node)
            if stats!=ref.get('statistics'):raise ValueError('category statistics do not match raw response')
            observed.add(node);indices.add(ref['index'])
        result['observed']=len(observed)
        if len(observed)<required_count:raise ValueError('category reports fewer than required three adjacent categories')
        result.update(status='met',reason=None,error=None)
    except (OSError,ValueError,KeyError,TypeError,IndexError,yaml.YAMLError) as error:
        result['validation_errors'].append('category_comparison: '+str(error))
        result['reason']=result['validation_errors'][0]
    return result


def coverage_error(root,item):
    expected=category_coverage(root)
    if expected['validation_errors']:return '; '.join(expected['validation_errors'])
    for key in ('contract','observed','status','sources','source_manifest','not_applicable_evidence'):
        if item.get(key)!=expected.get(key):return 'category coverage differs from verified source evidence: '+key
    return None
