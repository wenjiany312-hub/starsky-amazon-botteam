"""Registered source-based regeneration for reopened S1 market/supplier steps."""
import argparse
import json
from pathlib import Path
import yaml


def read(path):
    return yaml.safe_load(path.read_text(encoding='utf-8-sig')) if path.is_file() else {}


def write_market_reports(root, facts, step):
    """R09: one renderer for every caller. Numbers come from saved replies, judgement from
    market_narrative.yaml (read back, never overwritten). Writes the Markdown and HTML report."""
    from s1_market_report import render, REPORT_MD, REPORT_HTML
    from kb_report_contract import render_native_narrative
    root=Path(root)
    markdown,page=render(root,facts,read(root/'s1_decision_inputs.yaml') or {})
    text=render_native_narrative(markdown,root,Path(__file__).resolve().parents[1],step)
    market=root/REPORT_MD.parent;market.mkdir(parents=True,exist_ok=True)
    # Older cases may still carry 01_市场穿透报告.md; keep every market report name in step.
    paths=sorted(set(market.glob('01_市场*报告.md'))|{root/REPORT_MD})
    for path in paths:path.write_text(text,encoding='utf8',newline='\n')
    (root/REPORT_HTML).write_text(page,encoding='utf8',newline='\n')
    return {'md':root/REPORT_MD,'html':root/REPORT_HTML}


def refresh_market(root):
    """CLI helper: regenerate the market report after editing market_narrative.yaml."""
    return refresh(root,4)


def refresh(root, step):
    from r01_native import execute_native
    handled=execute_native(root,lambda view:refresh(view,step),step=step)
    if handled is not NotImplemented:return handled
    root=Path(root)
    market=root/'S1_选品板块/amazon-market-research'
    if step in (3,4):
        from s1_market_facts import write_market_facts
        paths=write_market_reports(root,write_market_facts(root),step)
        return {'ok':True,'md':str(paths['md']),'html':str(paths['html']),'facts':str(market/'01_market_facts.json')}
    if step != 5:raise ValueError('本入口仅支持第3/4/5步；成本与推广使用各自原生写入器')
    from offer_report import product_markdown
    from kb_report_contract import render_native_narrative
    folder=root/'S1_选品板块/1688-product-detail-scraper'
    product=read(folder/'03_product_facts.yaml') or {}
    selected=product.get('selected_offer') or {}
    offer=str(selected.get('offer_id') or '') if isinstance(selected,dict) else str(selected)
    if not offer.isdigit():raise ValueError('缺真实所选offer，不推定货源')
    candidates=(read(root/'S1_选品板块/alibaba-supplier-evaluation/02_supplier_evidence.yaml') or {}).get('candidates',[])
    candidate=next((x for x in candidates if str(x.get('offer_id'))==offer),None)
    if candidate is None:raise ValueError('缺所选offer已保存的供应商证据')
    source=folder/'运行数据'/offer/'1688_source.json'
    if not source.is_file():raise ValueError('缺所选offer原始页面回包')
    raw=json.loads(source.read_bytes())
    observations=read(folder/'运行数据/05_image_observations.json') or {}
    path=folder/f'03_1688产品资料_{offer}.md'
    original=path.read_text(encoding='utf-8-sig') if path.is_file() else ''
    text=product_markdown(raw,candidate,observations)
    text=render_native_narrative(text,root,Path(__file__).resolve().parents[1],5)
    if original:text+='\n<details><summary>此前产品资料原文（保留关键词与备注，仅供历史核对）</summary>\n\n'+original+'\n</details>\n'
    path.write_text(text,encoding='utf8',newline='\n')
    return {'ok':True,'md':str(path),'source':str(source),'offer_id':offer}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--step',type=int,choices=[3,4,5],required=True)
    args=parser.parse_args()
    try:
        print(json.dumps(refresh(args.root,args.step),ensure_ascii=False));raise SystemExit(0)
    except (OSError,ValueError,KeyError) as error:
        print(json.dumps({'ok':False,'error':str(error)},ensure_ascii=False));raise SystemExit(1)
