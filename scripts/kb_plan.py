"""Deterministic step-to-MCP plan. Enum authority: bundled server inputSchema."""
import argparse
import ast
import hashlib
import json
import importlib.util
import re
from datetime import datetime, timezone
from pathlib import Path
import yaml

LIBRARIES = {
    'selection_knowledge_base': ('selection-kb', '选品库'),
    'ads_knowledge_base': ('ads-kb', '广告库'),
    'promotion_knowledge_base': ('promotion-kb', '产品推广库'),
}


def schemas(home, server):
    """Read declarations without importing three modules sharing the name kb."""
    folder = LIBRARIES[server][0]
    path = Path(home) / 'mcp' / folder / 'server.py'
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    namespace = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name): namespace[target.id] = value
        elif isinstance(node, ast.FunctionDef) and node.name == 'tool_defs':
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    return {tool['name']: tool['inputSchema'] for tool in namespace['tool_defs']()}


def load(path, warnings=None):
    try:
        data = yaml.safe_load(Path(path).read_text(encoding='utf-8-sig')) or {}
        if not isinstance(data, dict): raise ValueError('需要对象')
        return data
    except (OSError, ValueError, yaml.YAMLError) as error:
        if warnings is not None: warnings.append(f'{Path(path).name}：{error}')
        return {}


def roadmap_for(root,home,state=None):
    """Use the case's versioned route for both planning and final verification."""
    home=Path(home)
    script=home/'scripts/s1_roadmap.py'
    if not script.is_file() and Path(__file__).parent.name=='release-overrides':
        script=Path(__file__).resolve().parents[1]/'source/starsky-amazon-agent/scripts/s1_roadmap.py'
    if not script.is_file():raise ValueError('缺少已安装的S1路线兼容模块')
    spec=importlib.util.spec_from_file_location('starsky_kb_roadmap',script)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.resolve_roadmap(home,root,state)


def inside(root, path):
    resolved = (Path(root) / path).resolve()
    if not resolved.is_relative_to(Path(root).resolve()): raise ValueError('知识库产物路径越界')
    return resolved


def evidence_path(stage, number, root=None):
    folder = {'S1':'S1_选品板块','S2':'S2_文案板块','S3':'S3_视觉板块',
              'S4':'S4_广告板块','S5':'S5_运营板块'}[stage]
    cycle = ''
    if stage == 'S5' and root is not None:
        pointer = load(Path(root)/'S5_运营板块/amazon-ad-optimization/current.json')
        name = pointer.get('round')
        if name:
            if not isinstance(name,str) or not re.fullmatch(r'R\d+(?:_\d{8})?',name):
                raise ValueError('S5 知识库轮次路径无效')
            cycle = name+'/'
        else:
            rounds = load(Path(root)/'workflow_state.yaml').get('s5_execution',{}).get('cycles',[])
            if rounds:
                cycle = 'cycle-'+hashlib.sha256(str(rounds[-1]['cycle_id']).encode()).hexdigest()[:16]+'/'
    return f'{folder}/知识库依据/{stage}/{cycle}{number:02d}/kb_evidence.yaml'


def context(root, warnings):
    root = Path(root)
    state = load(root / 'workflow_state.yaml')
    data = {}
    paths = [root / '00A_quick_select.yaml', root / '00_总控/starsky-fullchain-workflow/00A_quick_select.yaml',
             root / 'S1_选品板块/amazon-market-research/01_product_profile.yaml']
    for record in state.get('s1_execution', {}).get('records', []):
        for role, item in record.get('artifacts', {}).items():
            if role in ('intake_yaml','profile_yaml'):
                paths.append(inside(root,item.get('path') if isinstance(item,dict) else item))
    for path in dict.fromkeys(paths):
        if path.is_file(): data.update(load(path, warnings))
    for key in ('selection','selection_settings','requirements','quick_select'):
        if isinstance(data.get(key),dict): data.update(data[key])
    brief_path=root/'S4_广告板块/amazon-ad-strategy-architect/ad_strategy_brief.yaml'
    brief = load(brief_path,warnings) if brief_path.is_file() else {}
    product = brief.get('product') or {}
    # Translate existing business contract values, then validate against each actual MCP schema.
    form = {'standard':'标品','nonstandard':'非标品','hybrid':'半标品'}
    lifecycle = {'new':'新品期','growth':'成长期','mature':'成熟期','decline':'衰退清仓'}
    if product.get('form') in form: data['product_type']=form[product['form']]
    if product.get('lifecycle') in lifecycle: data['lifecycle_stage']=lifecycle[product['lifecycle']]
    pointer=load(root/'S5_运营板块/amazon-ad-optimization/current.json')
    name=pointer.get('round')
    if name:
        if not isinstance(name,str) or not re.fullmatch(r'R\d+(?:_\d{8})?',name): raise ValueError('S5 知识库轮次路径无效')
        diagnosis=load(root/'S5_运营板块/amazon-ad-optimization'/name/'02_诊断方案.json',warnings)
        findings=[str(row.get('why') or '') for key in ('actions','watch') for row in diagnosis.get(key,[]) if isinstance(row,dict)]
        health=diagnosis.get('product_health') or []
        findings.extend(str(row.get('verdict') or '') for row in health if isinstance(row,dict))
        if any(findings): data['s5_findings']='；'.join(dict.fromkeys(x for x in findings if x))
        explicit=list(dict.fromkeys(row['lifecycle_stage'] for row in health if isinstance(row,dict) and row.get('lifecycle_stage')))
        if len(explicit)==1: data['lifecycle_stage']=explicit[0]
        elif health: warnings.append('健康灯只说明健康状态，不能单独确定生命周期；沿用有依据的预判，缺失用通用')
    data.update(state.get('kb_context') or {})
    return data


def library_context(data, server):
    local=dict(data)
    if server=='promotion_knowledge_base':
        lifecycle={'新品期':'新品期D0-30','成长期':'成长期D30-90','成熟期':'成熟期D90+','断货重推':'断货重启',
                   '旺季大促':'通用','淡季':'通用'}
        local['lifecycle_stage']=data.get('promotion_lifecycle_stage') or lifecycle.get(data.get('lifecycle_stage'),data.get('lifecycle_stage','通用'))
    return local


def validate_args(schema, args):
    for key in schema.get('required',[]):
        if key not in args or args[key] is None: raise ValueError('缺 MCP 参数：'+key)
    types = {'string':str,'object':dict,'array':list,'boolean':bool,'integer':int,'number':(int,float)}
    for key,value in args.items():
        spec=schema.get('properties',{}).get(key)
        if spec is None: raise ValueError('未知 MCP 参数：'+key)
        if spec.get('type') in types and not isinstance(value,types[spec['type']]):
            raise ValueError('MCP 参数类型不符：'+key)
        if 'enum' in spec and value not in spec['enum']: raise ValueError('MCP 枚举值无效：'+key)


def status_seen(root, stage, number, server):
    for prior in range(1,number):
        data=load(Path(root)/evidence_path(stage,prior,root))
        if data.get('step')!=f'{stage}.{prior}': continue
        if any(c.get('server')==server and c.get('tool')=='kb_status' and c.get('status')=='ok'
               for c in data.get('calls',[]) if isinstance(c,dict)): return True
    return False


def plan_step(root, home, stage, number):
    root=Path(root).resolve();home=Path(home).resolve()
    roadmap=roadmap_for(root,home)
    spec=roadmap['stages'][stage]['steps'][number-1]
    if spec['n']!=number: raise ValueError('路线图编号不连续')
    policy=spec.get('kb') or {}
    warnings=[];data=context(root,warnings);calls=[];by_server={}
    update = (load(root/'workflow_state.yaml').get('kb_step_context') or {}).get(f'{stage}.{number}')
    if update:
        data.update(update['values'])
    for row in policy.get('calls',[]):
        if row.get('when')=='seasonal' and not (data.get('seasonal') is True or data.get('entry_path')=='季节档期'): continue
        server=row['server'];tool=row['tool']
        if tool=='kb_status' and row.get('once_per_stage') and status_seen(root,stage,number,server): continue
        if server not in by_server: by_server[server]=schemas(home,server)
        schema=by_server[server][tool];args=dict(row.get('args',{}));local=library_context(data,server)
        for key,target in row.get('bind',{}).items():
            value=local.get(target)
            if isinstance(value,list): value='；'.join(str(x) for x in value)
            if not value:
                value='本步异常待核实'
                warnings.append('缺少实际异常发现；诊断只检查风险，不表示已发现异常')
            args[key]=value
        for key,prop in schema.get('properties',{}).items():
            if key in args: continue
            if key in local:
                value=local[key]
                if 'enum' in prop and value not in prop['enum']:
                    warnings.append(f'{key} 输入无效，回退到通用');value='通用'
                args[key]=value
            elif key in schema.get('required',[]) and ('通用' in prop.get('enum',[]) or key in ('product_type','lifecycle_stage','ad_type')): args[key]='通用'
            elif 'default' in prop: args[key]=prop['default']
        if stage=='S4' and server=='ads_knowledge_base' and tool=='kb_query' and 'tags' not in args:
            authority=by_server[server]['kb_scenario_rules']['properties']
            args['tags']={key:[local[key] if local.get(key) in authority[key]['enum'] else '通用'] for key in ('product_type','lifecycle_stage')}
            args['tags']['ad_type']=['SP']
        if tool=='kb_playbook' and server=='ads_knowledge_base':
            for key in ('product_type','lifecycle_stage','ad_type'):
                if args[key] not in by_server[server]['kb_scenario_rules']['properties'][key]['enum']:
                    warnings.append(f'{key} 输入无效，回退到通用');args[key]='通用'
        validate_args(schema,args)
        calls.append({'server':server,'library':LIBRARIES[server][1],'tool':tool,'args':args})
    # Each library is probed on its first appearance in this stage, including promotion at S1.8.
    for server in reversed(list(dict.fromkeys(c['server'] for c in calls))):
        if not status_seen(root,stage,number,server) and not any(c['server']==server and c['tool']=='kb_status' for c in calls):
            calls.insert(0,{'server':server,'library':LIBRARIES[server][1],'tool':'kb_status','args':{}})
    plan={'schema_version':1,'step':f'{stage}.{number}','step_id':spec['control']['id'],
          'required':bool(policy.get('required')),'calls':calls,'tools':spec.get('tools',[]),
          'evidence_path':evidence_path(stage,number,root),'warnings':warnings,
          'label':'本步无库' if not calls else '；'.join(dict.fromkeys(c['library'] for c in calls))}
    if policy.get('reuse_step'):
        plan['reuse_step']=policy['reuse_step'];plan['label']=f"引用本轮第{policy['reuse_step']}步知识库依据"
    if policy.get('observations'): plan['observations']=policy['observations']
    if update: plan['context_update'] = update
    plan['plan_sha256']=hashlib.sha256(json.dumps(plan,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return plan


def update_anomaly(root, home, stage, number, anomaly, reason):
    """Bind a concrete observation before the actual diagnose call, per step."""
    if not isinstance(anomaly, str) or not anomaly.strip() or not isinstance(reason, str) or not reason.strip():
        raise ValueError('异常写回需要实际异常和本次原话，不能用空白或占位符')
    root = Path(root).resolve()
    state_path = root / 'workflow_state.yaml'
    state = load(state_path)
    execution = state.get(stage.lower() + '_execution') or {}
    if (any(row.get('n') == number for row in execution.get('records', [])) or
            execution.get('delivery_status') == 'complete' or
            (execution.get('cursor') is not None and execution['cursor'] != number)):
        raise ValueError('仅当前未完成步骤可更新诊断计划；已完成步骤请先正式 reopen 重开并撤销旧验收')
    roadmap = roadmap_for(root, home)
    spec = roadmap['stages'][stage]['steps'][number-1]
    bindings = {target for row in (spec.get('kb') or {}).get('calls', [])
                if row['tool'] == 'kb_diagnose' for target in row.get('bind', {}).values()}
    if not bindings:
        raise ValueError('本步没有可写回的诊断绑定')
    before = plan_step(root, home, stage, number)
    update = {'values': {key: str(anomaly).strip() for key in bindings},
              'reason': str(reason), 'updated_at': datetime.now(timezone.utc).isoformat()}
    previous = (state.get('kb_step_context') or {}).get(f'{stage}.{number}')
    state.setdefault('kb_step_context', {})[f'{stage}.{number}'] = update
    state.setdefault('kb_plan_audit', []).append({'step': f'{stage}.{number}', **update,
                                                'previous': previous, 'previous_plan_sha256': before['plan_sha256']})
    from sync_s1_decision_reports import write_transaction, rollback_transaction
    with rollback_transaction(root):
        write_transaction(root, {state_path: yaml.safe_dump(state, allow_unicode=True, sort_keys=False).encode('utf8')}, preserve_history=False)
        plan = plan_step(root, home, stage, number)
        path = inside(root, plan['evidence_path']).with_name('kb_plan.json')
        raw = json.dumps(plan, ensure_ascii=False, indent=2).encode('utf8')
        from case_mutation import mutate
        def write_plan(view):
            write_transaction(view, {view / path.relative_to(root): raw}, preserve_history=False)
            return {'step': plan['step'], 'plan_sha256': plan['plan_sha256']}
        mutate(root, stage, number, write_plan, operation='kb_plan_context')
    return plan


def fallback_gaps(root, evidence, *, cache=None, verify_integrity=True, allow_pending=False):
    """Read technical coverage gaps from hashed MCP results, never business observations."""
    def payloads(value):
        if not isinstance(value, dict): return
        yield value
        for key in ('result', 'structuredContent'):
            yield from payloads(value.get(key))
        for item in value.get('content', []):
            if isinstance(item, dict) and item.get('type') == 'text':
                try: yield from payloads(json.loads(item.get('text', '')))
                except (ValueError, TypeError): pass
    gaps = []
    for row in evidence.get('calls', []):
        if row.get('status') != 'ok' or row.get('server') != 'ads_knowledge_base' or row.get('tool') != 'kb_playbook': continue
        raw = inside(root, row['response_path'])
        selector = row.get('response_selector')
        if cache is not None:
            response = cache.response(raw, selector)
            observed = cache.sha256(raw, selector) if verify_integrity else None
        else:
            data = raw.read_bytes()
            response = json.loads(data.decode('utf-8-sig'))
            if selector:
                if not isinstance(selector, str) or not selector.startswith('/'):
                    raise ValueError('response_selector 必须是 JSON Pointer')
                for token in selector[1:].split('/'):
                    token = token.replace('~1', '/').replace('~0', '~')
                    response = response[int(token)] if isinstance(response, list) else response[token]
                data = json.dumps(response,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf8')
            observed = hashlib.sha256(data).hexdigest() if verify_integrity else None
        if verify_integrity and observed != row.get('response_sha256') and not (allow_pending and not row.get('response_sha256')):
            raise ValueError('知识库原始响应已变化：kb_playbook')
        for value in payloads(response):
            fallback = value.get('fallback')
            if not isinstance(fallback, dict) or fallback.get('tool') != 'kb_scenario_rules': continue
            if not fallback.get('reason') or not fallback.get('notice'): raise ValueError('规则卡回退缺原提示或说明')
            gaps.append({'kind':'missing_handbook', 'status':'open', 'library':'广告库',
                         'scenario':fallback.get('args', {}), 'reason':fallback['reason'],
                         'notice':'广告库：'+fallback['notice']+'；原提示：'+fallback['reason'],
                         'response_path':row['response_path'], 'response_sha256':row.get('response_sha256')})
    return gaps


def fallback_notices(root, evidence):
    return list(dict.fromkeys(g['notice'] for g in fallback_gaps(root, evidence)))


def save_plan(root, plan):
    root = Path(root).resolve()
    stage, number = plan['step'].split('.')
    path = inside(root, plan['evidence_path']).with_name('kb_plan.json')
    raw = json.dumps(plan, ensure_ascii=False, indent=2).encode('utf8')
    from case_mutation import mutate
    from sync_s1_decision_reports import write_transaction
    def write(view):
        write_transaction(view, {view / path.relative_to(root): raw}, preserve_history=False)
        return {'step': plan['step'], 'plan_sha256': plan.get('plan_sha256')}
    return mutate(root, stage, int(number), write, operation='kb_plan_save')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',required=True);parser.add_argument('--home',default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--stage',choices=['S1','S2','S3','S4','S5'],required=True);parser.add_argument('--step',type=int,required=True)
    parser.add_argument('--save',action='store_true',help='保存本步调用计划，供真实 MCP 调用与证据核对')
    parser.add_argument('--anomaly', help='调用诊断前写回本步实际异常')
    parser.add_argument('--reason', help='本次实际用户原话或原始发现')
    args=parser.parse_args()
    plan = update_anomaly(args.root,args.home,args.stage,args.step,args.anomaly,args.reason) if args.anomaly is not None else plan_step(args.root,args.home,args.stage,args.step)
    if args.save and args.anomaly is None:
        save_plan(args.root, plan)
    print(json.dumps(plan,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
