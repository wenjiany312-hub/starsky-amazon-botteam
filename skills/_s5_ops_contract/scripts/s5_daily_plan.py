#!/usr/bin/env python3
"""每天早上顺带生成当天的广告优化方案（规则诊断，不用 AI），摘要写进日报数据夹，飞书卡片显示「今日方案 N 条」。

  python s5_daily_plan.py --marketplace US

流程：领星只读拉「站点当地昨天为止 7 天」→ 在运营账本 <ops_workspace>/<站点> 开新一轮 → diagnose → report。
知识库 / SIF 依据留给用户回到电脑说「看今天的广告方案」时由助手补，再审批、写回。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve()


def module(path, name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


opt = module(HERE.parent / 's5_optimize.py', 's5_optimize_plan')
src = module(HERE.parent / 's5_lingxing_source.py', 's5_lingxing_source_plan')


def ops_root(marketplace):
    home = Path(os.environ.get('STARSKY_HOME') or (Path.home() / '.starsky-codex')).expanduser()
    return Path(os.environ.get('STARSKY_OPS_ROOT') or (home / 'ops_workspace')) / marketplace


def run(argv):
    args = opt.build_parser().parse_args(argv)
    return opt.COMMANDS[args.cmd](args)


def seasonal_summary(root, store_id, marketplace, today=None):
    scripts = str(HERE.parents[3] / 'scripts')
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from seasonal_display import collect
    return collect(Path(root) / 'seasonal_bindings.json', consumer='s5', store_id=store_id,
                   marketplace=marketplace, today=today)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--marketplace', required=True)
    ap.add_argument('--days', type=int, default=7)
    args = ap.parse_args(argv)
    mp = args.marketplace.upper()
    lx = src.client()
    store = next((s for s in lx.patrol_stores(mp)), None)
    if not store:
        print(json.dumps({'status': 'blocked', 'error': f'lingxing_config.yaml 里没有 {mp} 的巡检店铺'}, ensure_ascii=False))
        return 2
    end = lx.closed_pulse_day(mp)
    start = end - timedelta(days=args.days - 1)
    root = ops_root(mp)
    root.mkdir(parents=True, exist_ok=True)
    steps = {}
    for name, argv_ in (('ingest', ['ingest', '--run', str(root), '--source', 'lingxing', '--sid', str(store['sid']),
                                    '--start', start.isoformat(), '--end', end.isoformat(), '--scope', 'store']),
                        ('diagnose', ['diagnose', '--run', str(root), '--marketplace', mp]),
                        ('report', ['report', '--run', str(root)])):
        out, code = run(argv_)
        steps[name] = out
        if code not in (0, 2) or out.get('status') in ('blocked', 'fail') and name != 'report':
            print(json.dumps({'status': 'fail', 'step': name, 'detail': out}, ensure_ascii=False))
            return 2
    folder = opt.current_round(root)
    plan = opt.load(folder / '02_诊断方案.json')
    health = plan.get('product_health') or []
    summary = {'artifact_type': 's5_daily_plan', 'marketplace': mp, 'round': folder.name, 'window': [start.isoformat(), end.isoformat()],
               'report': str(folder / '03_优化前后对比.html'), 'actions': len(plan['actions']),
               'by_kind': dict(Counter(a['label'] for a in plan['actions'])), 'watch': plan.get('watch_count'),
               'red_products': [h['asin'] for h in health if h['light'] == '🔴'],
               'breakdown': [{'asin': r['asin'], 'node': r['node'], 'orders': [r['previous']['orders'], r['current']['orders']]}
                             for r in (plan.get('order_breakdown') or {}).get('rows') or []
                             if r['node'] not in ('平稳', '上升', '样本太小')][:6],
               'evidence': 'pending', 'generated_at': datetime.now().astimezone().isoformat()}
    summary['seasonal'] = seasonal_summary(root, str(store['sid']), mp)
    day_folder = lx.report_root() / 'lingxing' / datetime.now().date().isoformat()  # 与 daily_patrol_data / feishu_push 同一天
    day_folder.mkdir(parents=True, exist_ok=True)
    (day_folder / f'daily_plan_{mp}.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps({'status': 'ok', **{k: summary[k] for k in ('round', 'actions', 'by_kind', 'red_products', 'report', 'seasonal')}}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
