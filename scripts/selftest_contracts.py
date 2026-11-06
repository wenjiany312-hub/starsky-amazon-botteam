"""Check every required Markdown role has a writer or the sole ruler exception."""
import argparse
import json
from pathlib import Path
import yaml
import sys
import shutil
import tempfile
from kb_report_contract import AUTHORED_ROLES,LOCKED_WRITERS


def writer_probes(home,roadmap):
    """Synthetic format probes; never claim live KB evidence or acceptance."""
    from kb_report_contract import render_authored_report,render_native_narrative
    from s1_report_v2 import market_report
    from verify_kb_evidence import report_errors
    import kb_plan
    with tempfile.TemporaryDirectory(prefix='s1-writer-contract-') as folder:
        base=Path(folder);probe_home=base/'home';root=base/'case';root.mkdir()
        for relative,source in [('templates/stage_roadmap.yaml',roadmap),('scripts/s1_roadmap.py',home/'scripts/s1_roadmap.py')]+[(f'mcp/{name}/server.py',home/f'mcp/{name}/server.py') for name in ('selection-kb','promotion-kb','ads-kb')]:
            target=probe_home/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
        used={'status':'used','calls':[{'server':'selection_knowledge_base','tool':'kb_query','card_ids':['KC-9999'],'usage':'用于核对合成格式，不代表业务证据'}]}
        evidence=root/kb_plan.evidence_path('S1',4,root);evidence.parent.mkdir(parents=True);evidence.write_text(yaml.safe_dump(used,allow_unicode=True),encoding='utf8')
        rendered=render_native_narrative(market_report({},{}),root,probe_home,4)
        report=root/'market.md';report.write_text(rendered,encoding='utf8')
        if report_errors(report,[('selection_knowledge_base','KC-9999')]):raise ValueError('原生市场报告依据写入探针失败')
        if render_authored_report('# 无库',root,probe_home,'S2',3,'download_card_md')!='# 无库':raise ValueError('本步无库分支改变正文')
        failed={'status':'unavailable','reason':'合成连接失败','calls':[{'status':'error','error':'synthetic offline','card_ids':[]}]}
        text=render_authored_report('# 断库',root,probe_home,'S1',4,'market_md',evidence=failed)
        if not all(x in text.split('\n\n')[0] for x in ('本步未能调用知识库','合成连接失败','检测星空配置')):raise ValueError('断库顶部说明缺失')
    return ['native-market-used','authored-no-library','authored-unavailable']


def check(home,roadmap=None):
    home=Path(home);roadmap=Path(roadmap or home/'templates/stage_roadmap.yaml')
    try:from verify_kb_evidence import SCRIPT_GENERATED_ROLES
    except ModuleNotFoundError:
        sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'release-overrides'))
        from verify_kb_evidence import SCRIPT_GENERATED_ROLES
    stages=yaml.safe_load(roadmap.read_bytes())['stages'];errors=[];rows=[]
    if SCRIPT_GENERATED_ROLES!=frozenset({'ruler_md'}):errors.append('确定性例外仅允许ruler_md')
    for stage,route in stages.items():
        for step in route['steps']:
            for role in step['control'].get('required_artifacts',[]):
                if not role.endswith('_md'):continue
                locked=LOCKED_WRITERS.get(role) if role!='report_md' or stage=='S1' else None
                writer='ruler deterministic hash seal' if role in SCRIPT_GENERATED_ROLES else locked or ('scripts/kb_report_contract.py:render_authored_report' if role in AUTHORED_ROLES else None)
                if not writer:errors.append(f'{stage}.{step["n"]} {role}没有知识库依据生成契约')
                elif locked:
                    text=(home/locked).read_text(encoding='utf-8-sig')
                    if ('enrich_report' if role!='report_md' else 'stage_basis') not in text:errors.append(role+'生成器未接入依据写入')
                rows.append({'stage':stage,'step':step['n'],'role':role,'writer':writer})
    try:probes=writer_probes(home,roadmap)
    except (OSError,ValueError,KeyError,TypeError) as error:errors.append(str(error));probes=[]
    # R09: r01_native delegates the market report to s1_step_refresh.write_market_reports.
    native=(home/'scripts/r01_native.py').read_text(encoding='utf8');refresh=(home/'scripts/s1_step_refresh.py').read_text(encoding='utf8')
    if not ('render_native_narrative' in native or ('write_market_reports' in native and 'render_native_narrative' in refresh)):errors.append('原生市场报告未在封存前接入依据写入')
    return {'ok':not errors,'errors':errors,'roles':rows,'writer_probes':probes,'boundary':'synthetic format probes do not replace actual body/reply evidence validation'}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--home',type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument('--roadmap',type=Path)
    a=p.parse_args();result=check(a.home,a.roadmap);print(json.dumps(result,ensure_ascii=False,indent=2));raise SystemExit(0 if result['ok'] else 1)

