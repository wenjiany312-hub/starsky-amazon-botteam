#!/usr/bin/env python3
"""飞书日报的「昨日利润 + 产品健康灯」：领星只读取数 + 成本表，结果写进当天日报数据夹，feishu_push.py 自动带上。

  python s5_daily_health.py --marketplace US      # 站点当地昨天的利润 + 近 7 天（对比前 7 天）产品健康灯

口径与 S5 诊断一致：保本 ACOS 是广告亏钱线，目标 ACOS 是方向；灯看 TACOS、扣广告后利润、自然单占比趋势。
利润 = 各 SKU 件数 × 成本表每件毛利 − 广告花费（估算：按成本表售价，未扣优惠券 / 退货）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve()


def module(path, name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


opt = module(HERE.parent / 's5_optimize.py', 's5_optimize_daily')
layout = module(HERE.parents[3] / 'scripts' / 'brief_layout.py', 'brief_layout_daily')   # 扣广告后利润与旗舰简报同一入口
src = module(HERE.parent / 's5_lingxing_source.py', 's5_lingxing_source_daily')
ct = module(HERE.parent / 's5_cost_table.py', 's5_cost_table_daily')


def closed_day(lx, marketplace):
    return lx.closed_pulse_day(marketplace)  # 站点当地已收盘的昨天


def profit_of(products, marketplace):
    table = ct.load_marketplace(marketplace) or {}
    by_sku, by_asin = ct.index(table)
    est = table.get('estimate_margin')
    total, spend, sales, units_missing, rows = 0.0, 0.0, 0.0, 0, []
    for asin, p in products.items():
        gp, known = 0.0, True
        if est and not table.get('rows'):
            gp = p['sales'] * est  # C 档估算
            spend += p['ad_spend']
            sales += p['sales']
            total += layout.profit_after_ads(gp, p['ad_spend'])['value']
            if p['units'] or p['ad_spend']:
                rows.append({'asin': asin, 'profit': layout.profit_after_ads(gp, p['ad_spend'])['value'], 'units': p['units'], 'ad_spend': round(p['ad_spend'], 2),
                             'cost_complete': False, 'estimate': True})
            continue
        for sku, n in (p['sku_units'] or {}).items():
            row = by_sku.get(sku) or by_asin.get(asin)
            if row:
                gp += row['gross_profit'] * n
            else:
                units_missing += n
                known = False
        spend += p['ad_spend']
        sales += p['sales']
        total += layout.profit_after_ads(gp, p['ad_spend'])['value']
        if p['units'] or p['ad_spend']:
            rows.append({'asin': asin, 'profit': layout.profit_after_ads(gp, p['ad_spend'])['value'], 'units': p['units'], 'ad_spend': round(p['ad_spend'], 2),
                         'cost_complete': known})
    return {'profit': round(total, 2), 'ad_spend': round(spend, 2), 'sales': round(sales, 2), 'caliber': layout.PROFIT_NOTE,
            'tacos': round(spend / sales, 4) if sales else None, 'units_without_cost': units_missing,
            'estimate': bool(est and not table.get('rows')), 'products': sorted(rows, key=lambda r: r['profit'])}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--marketplace', required=True)
    args = ap.parse_args(argv)
    mp = args.marketplace.upper()
    lx = src.client()
    store = next((s for s in lx.patrol_stores(mp)), None)
    if not store:
        print(json.dumps({'status': 'blocked', 'error': f'lingxing_config.yaml 里没有 {mp} 的巡检店铺'}, ensure_ascii=False))
        return 2
    sid = int(store['sid'])
    day = closed_day(lx, mp)
    week = src.collect_products(sid, (day - timedelta(days=6)).isoformat(), day.isoformat())
    yesterday = src.collect_products(sid, day.isoformat(), day.isoformat())
    home = Path(os.environ.get('STARSKY_HOME') or (Path.home() / '.starsky-codex')).expanduser()
    root = Path(os.environ.get('STARSKY_OPS_ROOT') or (home / 'ops_workspace')) / mp  # 与 S5 诊断同一份投入期历史
    health, note = opt.product_health(root, SimpleNamespace(marketplace=mp), {'products': week, 'marketplace': mp})
    counts = {k: sum(1 for h in health if h['light'] == k) for k in ('🟢', '🟡', '🔴', '⚪')}
    out = {'artifact_type': 's5_daily_health', 'generator': 's5_daily_health.py', 'marketplace': mp, 'sid': sid,
           'data_day': day.isoformat(), 'generated_at': datetime.now().astimezone().isoformat(),
           'yesterday': profit_of(yesterday['current']['products'], mp), 'health_window': note, 'health_counts': counts,
           'health': health}
    folder = lx.report_root() / 'lingxing' / datetime.now().date().isoformat()  # 与 daily_patrol_data / feishu_push 同一天的数据夹
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'product_health_{mp}.json'
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    y = out['yesterday']
    print(json.dumps({'status': 'ok', 'file': str(path), 'data_day': out['data_day'], 'profit': y['profit'], 'ad_spend': y['ad_spend'],
                      'tacos': y['tacos'], 'lights': counts}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
