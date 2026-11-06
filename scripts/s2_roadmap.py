"""Current S2 projection; old receipts are retained, never new final proof."""
from copy import deepcopy
import hashlib
from pathlib import Path
import yaml

CURRENT = 'r02.s2'
IDS = ('inherit_asset','competitor_select','export_intake','asset_clean','ct_placement','variant_copy','compliance_delivery')
OLD = ('s2_intake','competitor_asin_research','keyword_export_prepare','keyword_research','scene_library','s2_report_delivery')


def current_review(root, state):
    """Re-read the actual human decision and current copy, including compact IO."""
    from r01_case_store import CompactCase
    try:
        root=Path(root).resolve();store=CompactCase(root)
        flow=(state or {}).get('s2_execution') or {}
        records=flow.get('records') or []
        final=next((row for row in reversed(records) if row.get('n')==7),{}) if flow.get('roadmap_version')==CURRENT else {}
        artifacts=final.get('artifacts') or {}
        def content(role):
            ref=artifacts[role];path=(root/(ref['path'] if isinstance(ref,dict) else ref)).resolve()
            if not path.is_relative_to(root):raise ValueError('审阅文件越出当前产品')
            return store.read_bytes(path)
        review=yaml.safe_load(content('listing_review')) or {} if 'listing_review' in artifacts else {}
        reviewed=(review.get('source')=='user' and review.get('status')=='approved'
                  and review.get('sha256')==hashlib.sha256(content('listing_md')).hexdigest())
    except (yaml.YAMLError,OSError,ValueError,KeyError,TypeError):
        reviewed=False
    # R08.2: human review never blocks S2; it is a listing-day to-do until the user approves the current copy.
    return {'ok':True,'errors':[],'reviewed':reviewed,
            **({} if reviewed else {'todo':'文案尚未人工审阅：上架前请逐字段过一遍（尤其尺寸、材质和用途表述）'})}


def check_inputs(root, number, files, state):
    """Read named existing inputs; missing facts remain gaps, never approval."""
    from s2_ct_mapping import fingerprint
    from keyword_asset import read_snapshot
    root=Path(root);errors=[]
    handoff=root/'_NEXT_S1到S2.md'
    result={'s1_acceptance':((state or {}).get('stage_acceptance',{}).get('S1') or {}).get('status','unknown'),
            'handoff_sha256':hashlib.sha256(handoff.read_bytes()).hexdigest() if handoff.is_file() else None}
    ctpath=root/'S1_选品板块/1688-product-detail-scraper/03_product_ct.yaml'
    ct=yaml.safe_load(ctpath.read_bytes()) if ctpath.is_file() else {}
    if ct is not None and not isinstance(ct,dict):raise ValueError('产品 CT 格式错误')
    from s2_ct_supplement import effective_ct          # S2-R2：S2 补充层只填 S1 空着的节点，不改 S1 文件
    ct=effective_ct(root) if ctpath.is_file() else (ct or {})
    ct=(ct or {}).get('ct',ct or {})
    for key,label in [('uses','用途'),('audiences','人群'),('scenarios','场景'),('pain_points','痛点')]:
        node=ct.get(key) or {}
        if not isinstance(node,dict) or not node.get('value') or node.get('source_status',node.get('status','missing'))=='missing':
            result.setdefault('ct_gaps',[]).append('产品 CT 缺'+label+'已确认来源；按假设使用并标注待核，不写成已确认')
    result['ct_sha256']=hashlib.sha256(ctpath.read_bytes()).hexdigest() if ctpath.is_file() else None
    asset_path=root/'_词库资产/keyword_master.yaml'
    snapshot=read_snapshot(root) if asset_path.is_file() else None
    result['asset']=snapshot.summary() if snapshot else {'status':'not_created'}
    if number>=4 and snapshot is None:errors.append('尚未写入本系列词库资产')
    if number>=5:
        mapping=yaml.safe_load(Path(files['ct_mapping']).read_bytes())
        if not isinstance(mapping,dict) or not mapping.get('rows'):errors.append('缺当前 CT 卖点映射')
        elif mapping.get('source_fingerprint')!=fingerprint(ct):errors.append('CT 已变化；重新生成卖点映射与埋词规划')
        # The binding is always checked on the series map; a separately drafted plan file is kept as a plan,
        # not rejected for living elsewhere (3.0.24 rerun lost a step to this).
        series_map=(root/'_词库资产/placement_map.yaml').resolve()
        placement_path=Path(files.get('placement_map') or series_map).resolve()
        if placement_path!=series_map:
            result['placement_plan']=str(placement_path); placement_path=series_map
        if not placement_path.is_file():errors.append('系列词库尚未生成埋词地图：先完成第 4 步入库')
        placement=yaml.safe_load(placement_path.read_bytes()) if placement_path.is_file() else {}
        if not isinstance(placement,dict) or placement.get('authority')!='keyword_master.yaml' or not snapshot or placement.get('revision')!=snapshot.summary()['revision']:
            errors.append('埋词地图未绑定当前词库版本')
    return {'status':'fail' if errors else 'pass','errors':errors,**result}


def project_state(state):
    old = (state or {}).get('s2_execution')
    if not old or old.get('roadmap_version') == CURRENT:
        return state
    version = old.get('roadmap_version', 'legacy.s2.6')
    if version != 'legacy.s2.6':
        raise ValueError('unknown S2 roadmap version: ' + str(version))
    result = deepcopy(state)
    flow = result['s2_execution']
    history = flow.setdefault('legacy_history', [])
    history.append({'roadmap_version': version, 'execution': deepcopy(old),
                    'acceptance': deepcopy((state.get('stage_acceptance') or {}).get('S2'))})
    records = []
    for record in old.get('records') or []:
        n = record.get('n')
        if n in (1,2,3,4) and record.get('step', record.get('id')) == OLD[n-1]:
            records.append({**record, 'step': IDS[n-1], 'legacy_step': OLD[n-1]})
    completed = {r['n'] for r in records if r.get('outcome','complete') != 'blocked'}
    cursor = next((n for n in range(1,5) if n not in completed), 5)
    flow.update(roadmap_version=CURRENT, records=records, events=[], cursor=cursor,
                delivery_status='pending', pending_card=None, validation={}, migration_pending=True)
    result.setdefault('stage_acceptance', {})['S2'] = {'status':'pending',
        'issues':['旧六步记录已保留；核对 CT 埋词规划并重新执行当前合规与文案审阅。']}
    if result.get('current_stage') == 'S2':
        result['current_step'] = IDS[cursor-1]
        result['next_step'] = {'step_id':IDS[cursor-1], 'step_number':cursor,
                               'instruction':'核对现有 CT 埋词规划；最后重新扫描当前文案。'}
    return result
