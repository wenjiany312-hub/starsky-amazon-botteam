"""Rebuild S1 derived evidence and reports locally; never fetch or approve business actions."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

HOME = Path(__file__).resolve().parents[1]


def module(relative, name):
    path = HOME / relative
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    sys.path.insert(0, str(path.parent))
    spec.loader.exec_module(result)
    return result


def run(root):
    from s1_report_links import rewrite_reports
    from s1_market_facts import write_market_facts, build_enrichment_facts, apply_enrichment_coverage
    from sync_s1_decision_reports import sync, write_transaction
    root = Path(root).resolve()
    if not (root / 's1_decision_inputs.yaml').is_file():
        raise ValueError('缺少 s1_decision_inputs.yaml；先从真实用户选择和来源建立输入，不能凭空生成产品决策')
    # Resolve moved-project sources before generating any dependent hash receipts.
    links = rewrite_reports(root)
    facts = write_market_facts(root)
    facts_path = root / 'S1_选品板块/amazon-market-research/01_market_facts.json'
    facts = json.loads(facts_path.read_text(encoding='utf8'))
    supplementary = root / '运行数据/S1_后补采集'
    if (supplementary / 'enrichment_facts.json').is_file():
        enriched = build_enrichment_facts(supplementary)
        write_transaction(root, {supplementary / 'enrichment_facts.json': json.dumps(enriched,ensure_ascii=False,indent=2,default=str).encode()})
        facts = apply_enrichment_coverage(facts, enriched, supplementary.relative_to(root).as_posix())
        write_transaction(root, {facts_path: json.dumps(facts,ensure_ascii=False,indent=2,default=str).encode()})
    cost_dir = root / 'S1_选品板块/amazon-market-research'
    if (cost_dir / '05_cost_inputs.yaml').is_file():
        cost = module('skills/amazon-market-research/scripts/s1_cost_model.py', 's1_repair_cost')
        cost.run(root)
    ip_dir = root / 'S1_选品板块/amazon-ip-risk-assessment'
    if (ip_dir / '04A_ruler_inputs.yaml').is_file():
        ip = module('skills/amazon-ip-risk-assessment/scripts/sync_ip_report.py', 's1_repair_ip')
        ip.run(root)
    result = sync(root)
    result['portable_links_updated'] = links
    result['business_approval'] = 'unchanged; requires evidence gates and explicit user authorization'
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.root), ensure_ascii=False, indent=2, default=str))
