"""S2-only final delivery authority; K consumes this independent of step number."""
import importlib.util
import json
import sys
from pathlib import Path

HOME=Path(__file__).resolve().parents[1]

LABELS = {'intent_shape':'形态','intent_function':'功能','intent_scene':'场景','intent_audience':'人群',
    'intent_gift':'送礼','required_attributes':'必填属性','qa_intents':'五类问答意图','core_phrases':'核心完整词组','ct_coverage':'卖点覆盖说明'}
ACTIONS = {'required_attributes':'补入适用商品类型的属性要求与缺失值；保持真实来源。',
    'core_phrases':'由文案 Skill 从已批准资产/CT 自动选定并登记主核心，再核对标题与亮点。',
    'qa_intents':'按五种实际购买问题重审问答原句与来源；不能只改意图编号。',
    'ct_coverage':'解释未覆盖卖点及实际证据缺口。'}


def remediation_items(proof):
    """Project this exact native proof; never scan, read assets, or count aggregates.

    One repair per category/SKU/field/rule/term (or source). Duplicate hits and
    editorial_failures' aggregate copy do not add repairs. Order is stable.
    """
    rows={}
    def add(category, sku, key, message, action):
        identity=json.dumps([category,sku,key],ensure_ascii=False,sort_keys=True)
        rows.setdefault(identity,{'id':identity,'category':category,'sku':sku,'message':message,'action':action})
    for source in proof.get('source_errors') or []:
        add('source','来源',str(source),'来源文件缺失或哈希变化：'+str(source),'核对当前来源文件后重新生成报告。')
    for scan in proof.get('scans') or []:
        sku=str(scan.get('sku') or '')
        for hit in scan.get('hits') or []:
            if hit.get('level')=='block':
                term=hit.get('found') or hit.get('term')
                key=[hit.get('field'),hit.get('entry_id') or hit.get('rule') or hit.get('id'),str(term).casefold(),hit.get('reason')]
                add('lexical',sku,key,'违规词/条件证据：'+str(hit.get('field'))+' · '+str(term or hit.get('reason') or '需核验'),
                    '按真实事实重写命中字段或补适用证据，再扫描。')
        for error in scan.get('errors') or []:
            add('field',sku,str(error),'字段格式：'+str(error),'修复字段数据格式后重新生成报告。')
    for item in proof.get('field_failures') or []:
        add('field',str(item.get('sku') or ''),[item.get('check'),item.get('field')],
            '字段硬规则：'+str(item.get('check'))+' · '+str(item.get('message') or ''),'修改对应字段；实际限制与计数见文案。')
    for sku in proof.get('editorial') or []:
        for check in sku.get('checks') or []:
            if not check.get('ok') and not check.get('advisory'):  # advisory gaps are listing-day to-dos, not repairs
                key=check['id']
                add('editorial',str(sku.get('sku') or ''),key,LABELS.get(key,key),
                    ACTIONS.get(key,'补齐实际字段原句与已有事实来源；缺证据保持待核。'))
    return list(rows.values())


def presentation(result):
    """Persist only the canonical decision, not the full native proof."""
    status=result.get('status','data_gap');count=result.get('count')
    if status=='needs_changes':
        label=f'需修改 {count} 处' if count is not None else '需修改（待核验）'
        action=f'按修改清单改 {count} 处' if count is not None else '查看修改清单并重新核验'
        next_text='回到第 6 步修改文案，再执行当前合规与审阅'
    elif status=='review_pending':
        label='待人工审阅';action='审阅当前文案并明确确认';next_text='留在第 7 步，完成当前文案人工审阅'
    elif status=='pass':
        label='可进入人工审阅';action='审阅当前文案并明确确认';next_text='完成当前文案人工审阅'
    else:
        label='验收数据待核验';action='核对来源并重新生成当前报告';next_text='补齐当前证明与来源后重新核验'
    return {key:value for key,value in {**result,'label':label,'action':action,'next':next_text}.items() if key!='current'}


def unavailable(errors, *, current=None):
    rows=remediation_items(current or {})
    return {**presentation({'ok':False,'status':'data_gap','count':len(rows) if rows else None,'remediations':rows,
        'errors':list(errors),'blocked_count':(current or {}).get('blocked_count')}),'current':current}


def review_result(errors, *, checked=None):
    errors=list(errors)
    human={'Listing 缺本次用户审阅','Listing 审阅未绑定当前文案',"'listing_review'",
           '缺本步产物：listing_review','缺校验依赖产物：listing_review'}
    only_human=bool(errors) and all(str(e).removeprefix('当前人工审阅未通过：') in human for e in errors)
    if not only_human:return unavailable(errors,current=(checked or {}).get('current'))
    return presentation({'ok':False,'status':'review_pending','count':0,'remediations':[],
        'errors':errors,'blocked_count':(checked or {}).get('blocked_count',0)})


def from_verified(verified):
    """Consume verify_bundle's already recomputed proof, never cached approval."""
    current=verified.get('current_compliance') or {}
    if not verified.get('ok'):return unavailable(verified.get('errors') or ['当前证明不可用'],current=current)
    rows=remediation_items(current)
    if rows:
        status='data_gap' if any(r['category']=='source' for r in rows) else 'needs_changes'
        result={'ok':False,'status':status,'count':len(rows),'remediations':rows,
            'errors':[str(r['sku'])+' · '+r['message'] for r in rows],'blocked_count':current.get('blocked_count')}
    elif current.get('status')!='pass':
        return unavailable(verified.get('validation',{}).get('issues') or ['当前检查未通过，缺少可核对的逐项清单'],current=current)
    elif verified.get('validation',{}).get('status')!='ready_for_review':
        # Compliance and hard field rules pass; what is left (to-confirm facts, human review) travels as
        # review to-dos. Copy done means S2 done; the review is a pre-listing step.
        result={'ok':True,'status':'pass','count':0,'remediations':[],'errors':[],'blocked_count':current.get('blocked_count',0),
            'review_todos':verified.get('validation',{}).get('issues') or []}
    else:
        result={'ok':True,'status':'pass','count':0,'remediations':[],'errors':[],'blocked_count':current.get('blocked_count',0)}
    return {**presentation(result),'current':current}


def load(home,name):
    spec=importlib.util.spec_from_file_location('_gate_'+name,Path(home)/'scripts'/(name+'.py'))
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod


def check_bundle(report_path, *, home=None):
    home=Path(home or HOME)
    try:
        report=load(home,'s2_report'); root,_=report.report_bundle_for(report_path)
        verified=report.verify_bundle(root)
        return from_verified(verified)
    except (OSError,ValueError,KeyError,TypeError) as exc:
        return unavailable(['S2当前合规证明不可用：'+str(exc)])


def check_stage(root,state=None,*,home=None,receipt=None,_materialized=False):
    try:
        if not _materialized:
            # Acceptance display also reads compact receipt-backed bundles.
            # Reuse the same module identity and authenticated view as the flow.
            name='r01_case_store'; store=sys.modules.get(name)
            if store is None:
                spec=importlib.util.spec_from_file_location(name,Path(home or HOME)/'scripts/r01_case_store.py')
                store=importlib.util.module_from_spec(spec);sys.modules[name]=store;spec.loader.exec_module(store)
            with store.CompactCase(root).materialize() as view:
                return check_stage(view.root,state,home=home,receipt=receipt,_materialized=True)
        if receipt is None:
            records=(state or {}).get('s2_execution',{}).get('records') or []
            receipt=records[-1] if records else {}
        artifacts=receipt.get('artifacts') or {}
        report=artifacts.get('report_html') or artifacts.get('listing_html')
        relative=report.get('path') if isinstance(report,dict) else report
        if not relative: raise ValueError('missing_current_listing_report')
        base=Path(root).resolve(); path=(base/relative).resolve()
        if not path.is_relative_to(base): raise ValueError('report_path_escapes_product')
        return check_bundle(path,home=home)
    except (OSError,ValueError,TypeError,KeyError) as exc:
        return unavailable([str(exc)])
