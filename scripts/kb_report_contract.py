"""Required report writers; no fabricated citations or hash-locked MD edits.

助手自己写的步骤报告（selection_md / demand_md / market_md / promotion_md / ip_md 等），record 前用本工具把本步
已登记的「知识库依据」写进去，并按验收规则统一数字显示精度（R06.1）：

    python scripts/kb_report_contract.py --root "<产品根>" --step 2 --role selection_md --file <报告相对路径>

3.0.23 自动实跑：这些报告没有写入器，验收报了十几条"缺知识库依据小节 / 缺引用 KC-xxxx"。
"""
import argparse
import json
import sys
from pathlib import Path
import re
import yaml

AUTHORED_ROLES=frozenset({'intake_md','selection_md','demand_md','market_md','promotion_md','ip_md',
    'competitor_md','download_card_md','keyword_md','listing_md','manual_md','selling_points_md',
    'listing_prompt_md','listing_feed_md','video_prompt_md','classification_md','parameters_md',
    'architecture_md','ad_signal_md','closing_md','diagnosis_md','report_md'})
LOCKED_WRITERS={'ct_md':'skills/1688-product-detail-scraper/scripts/product_ct.py',
                'cost_md':'skills/amazon-market-research/scripts/s1_cost_model.py',
                'report_md':'scripts/s1_report_v2.py'}


def _role_error(role,home,stage,number):
    """Say what to do instead (3.0.25: product_md / supplier_md were invented and the bare refusal led to source digging)."""
    required=[]
    try:
        roadmap=yaml.safe_load((Path(home)/'templates/stage_roadmap.yaml').read_text(encoding='utf-8-sig')) or {}
        step=next((s for s in roadmap['stages'][stage]['steps'] if int(s['n'])==int(number)),{})
        required=[r for r in (step.get('control') or {}).get('required_artifacts',[]) if str(r).endswith('_md')]
    except (OSError,KeyError,TypeError,ValueError,StopIteration,yaml.YAMLError):pass
    if role in LOCKED_WRITERS:
        head=f'{role} 由原生生成器 {LOCKED_WRITERS[role]} 写出并自动带知识库依据，不要用本工具补写；重新运行该生成器即可。'
    else:
        head=f'「{role}」不是登记的报告角色，本工具不能给它写知识库依据。'
    # R09: the S1 market report is native too; its judgement lives in market_narrative.yaml.
    native={'market_md':'scripts/s1_market_report.py，判断写 market_narrative.yaml'} if stage=='S1' else {}
    need=[r for r in required if r in AUTHORED_ROLES and r not in native and not (r=='report_md' and stage=='S1')]
    locked=[f'{r}（{LOCKED_WRITERS.get(r) or native[r]}）' for r in required if r in LOCKED_WRITERS or r in native]
    tail=f'{stage} 第 {number} 步登记的报告：'+('需本工具补依据的 '+'、'.join(need) if need else '没有需要本工具补依据的报告')
    if locked:tail+='；原生生成的 '+'、'.join(locked)
    return head+tail+'。其他文件（如 1688 产品资料、供应商短名单）不需要补依据，直接 record。'


def render_native_narrative(md,root,home,number,role='market_md'):
    """Before collection/hash only; standalone output may lack step evidence.

    Absence remains a controller gate failure, never an invented citation.
    """
    import kb_plan
    path=kb_plan.inside(root,kb_plan.evidence_path('S1',number,root))
    if not path.is_file():return md
    return render_authored_report(md,root,home,'S1',number,role,evidence=kb_plan.load(path))


def render_authored_report(md,root,home,stage,number,role,*,evidence=None):
    if role not in AUTHORED_ROLES or (role=='report_md' and stage=='S1'):raise ValueError(_role_error(role,home,stage,number))
    from kb_ui import enrich_report
    import kb_plan
    plan=kb_plan.plan_step(root,home,stage,number)
    if not plan['calls'] and not plan.get('reuse_step'):return md
    if evidence is None:
        path=kb_plan.inside(root,kb_plan.evidence_path(stage,plan.get('reuse_step',number),root))
        if path.is_file():evidence=kb_plan.load(path)
    # S1.4 and S1.8 deliberately share the market report. Its latest basis
    # must retain both active steps' actual registered uses, even on a full
    # regeneration. Never copy references from an unexecuted or historical step.
    if stage == 'S1' and number in (4, 8) and role == 'market_md' and evidence is not None:
        other = 8 if number == 4 else 4
        active = kb_plan.load(Path(root) / 'workflow_state.yaml').get('s1_execution', {}).get('records', [])
        if any(row.get('n') == other for row in active):
            source = kb_plan.inside(root, kb_plan.evidence_path(stage, other, root))
            if source.is_file():
                previous = kb_plan.load(source)
                if any(not str(row.get('usage') or '').strip() for row in previous.get('calls', [])
                       if row.get('status') == 'ok' and row.get('card_ids')):
                    raise ValueError('共享市场报告缺少实际知识卡用途；先补实际登记，再用写入器重新生成')
                calls = [dict(row, usage=f'第{other}步已登记用途：' + str(row.get('usage') or ''))
                         for row in previous.get('calls', []) if row.get('status') == 'ok' and row.get('card_ids')]
                evidence = {**evidence, 'calls': [*evidence.get('calls', []), *calls]}
    result=enrich_report(md,root,home,stage,number,evidence=evidence)
    if evidence and evidence.get('status')=='unavailable' and not any(c.get('card_ids') for c in evidence.get('calls',[])):
        if not evidence.get('reason') or not any(c.get('status')=='error' and c.get('error') for c in evidence.get('calls',[])):
            raise ValueError('断库降级须保留实际失败调用及原因')
        return result  # The unchanged verifier still checks the complete plan.
    if not re.search(r'^##[ \t]+知识库依据[ \t]*$',result,re.M):
        raise ValueError('先保存实际知识库回包和用途；不得编造知识库依据')
    return result


def stage_basis(root):
    """Use this case's registered card uses, never case-specific hardcoded IDs."""
    names={'selection_knowledge_base':'选品库','promotion_knowledge_base':'产品推广库','ads_knowledge_base':'广告库'}
    lines=['以下为本轮留存的引用用途；原始回包、计划及哈希仍由阶段验收逐项核验，不把登记等同已通过。']
    for path in sorted((Path(root)/'S1_选品板块/知识库依据/S1').glob('*/kb_evidence.yaml')) if root is not None else []:
        doc=yaml.safe_load(path.read_bytes()) or {}
        for call in doc.get('calls',[]):
            library=names.get(call.get('server'));usage=str(call.get('usage') or '').strip()
            if library and usage:
                for card in call.get('card_ids',[]):lines.append('- '+library+' '+str(card)+'：用于'+usage)
    if len(lines)==1:lines.append('本轮尚无留存的实际引用用途；需先补知识库回包与具体判断记录。')
    return '\n'.join(dict.fromkeys(lines))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--stage', default='S1', choices=['S1', 'S2', 'S3', 'S4', 'S5'])
    parser.add_argument('--step', required=True, type=int)
    parser.add_argument('--role', required=True, help='报告角色，如 selection_md / demand_md / market_md / promotion_md / ip_md')
    parser.add_argument('--file', required=True, help='报告相对产品根的路径')
    options = parser.parse_args(argv)
    root = options.root.resolve()
    path = (root / options.file).resolve()
    try:
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('报告不在产品根内或不存在：' + options.file)
        home = Path(__file__).resolve().parents[1]
        def rewrite(base):
            target = Path(base) / options.file
            before = target.read_text(encoding='utf-8-sig')
            # A hand-written basis section would sit next to the registered one (two sections, a C8 duplicate).
            body = re.sub(r'(?ms)^##[ \t]+知识库依据[ \t]*\n.*?(?=^##[ \t]|\Z)', '', before).rstrip() + '\n'
            after = render_authored_report(body, Path(base), home, options.stage, options.step, options.role)
            from s1_readability import numeric_lines
            after = numeric_lines(after, render=True)[0].rstrip() + '\n'  # C12 display precision, same rule as acceptance
            target.write_text(after, encoding='utf-8')
            return {'ok': True, 'file': options.file, 'role': options.role, 'changed': after != before, 'md': options.file}
        # 3.0.25 step 8: rewriting a sealed report in place broke the case store ("physical source hash
        # mismatch"). In a compact case the edit goes through the native transaction like every generator.
        try:
            from r01_native import execute_native
            compact = execute_native(root, rewrite, step=options.step)
        except ImportError:
            compact = NotImplemented
        result = rewrite(root) if compact is NotImplemented else compact
        print(json.dumps({k: result.get(k) for k in ('ok', 'file', 'role', 'changed')} | ({'compact': True} if compact is not NotImplemented else {}),
                         ensure_ascii=False, default=str))
        return 0
    except (ValueError, OSError, KeyError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
