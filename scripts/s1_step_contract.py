"""Read-only early C1/C3/C12 checks, shared by record and standalone audit."""
import hashlib
import json
from pathlib import Path

from s1_readability import readability_issues
from s1_report_v2 import required_field_issues

MARKET = Path('S1_选品板块/amazon-market-research')


def _narrative_contract(root):
    """R09 cases (marked at S1 init) require the three AI verdict slots before step 4 closes; older cases keep their flow."""
    import yaml
    path = Path(root) / 'workflow_state.yaml'
    state = yaml.safe_load(path.read_text(encoding='utf-8-sig')) if path.is_file() else {}
    return ((state or {}).get('s1_execution') or {}).get('market_report_contract') == 'r09.narrative.v1'


def check_step_contract(root, number):
    root = Path(root).resolve()
    errors = []
    market = root / MARKET
    paths = []
    if number in (3, 4):
        reports = list(market.glob('01_市场*报告.md'))
        if not reports:
            errors.append('[C1] 缺市场分析报告：先运行第3/4步原生生成 01_市场分析报告.md')
        for path in reports:
            bad = required_field_issues(path.read_text(encoding='utf-8-sig'), 'market')
            if bad:
                errors.append('[C1] ' + path.name + ' 缺必备字段：' + '、'.join(bad))
        facts = market / '01_market_facts.json'
        try:
            doc = json.loads(facts.read_bytes())
            if doc.get('generator') != 's1_market_facts.py' or not isinstance(doc.get('coverage'), dict):
                raise ValueError('缺原生生成标记或结构化覆盖')
            sources = doc.get('generation_sources') or {}
            if not sources:
                raise ValueError('缺原生输入来源')
            for relative, digest in sources.items():
                source = (root / relative).resolve()
                if not source.is_relative_to(root) or hashlib.sha256(source.read_bytes()).hexdigest() != digest:
                    raise ValueError('事实来源已变化：' + relative)
        except (OSError, ValueError, TypeError) as error:
            errors.append('[C3] 先运行 <python> scripts/s1_step_refresh.py --root "<产品根>" --step ' + str(number) +
                          '（用 s1_market_facts.py 重算 01_market_facts.json 并重新生成市场报告），再用同一条 record 重交：' + str(error))
        if number == 4 and _narrative_contract(root):
            from s1_market_overview import load_narrative
            from s1_market_report import missing_required
            missing = missing_required(load_narrative(root))
            if missing:
                errors.append('[C1] market_narrative.yaml 必填未完成：' + '、'.join(missing) +
                              '。用 scripts/s1_market_report.py template 看骨架、check 查缺项，写入后报告自动重新生成')
        paths = reports
    elif number == 5:
        paths = list((root / 'S1_选品板块/1688-product-detail-scraper').glob('03_1688产品资料_*.md'))
    elif number == 6:
        paths = list(market.glob('*条件测算*.md')) + list(market.glob('*成本*.md'))
    elif number == 7:
        paths = list(market.glob('*推广难度.md'))
    for path in paths:
        issues = readability_issues(path.read_text(encoding='utf-8-sig'))
        if issues:
            errors.append('[C12] ' + path.name + '：' + '、'.join(issues))
    return {'status': 'fail' if errors else 'pass', 'errors': errors}
