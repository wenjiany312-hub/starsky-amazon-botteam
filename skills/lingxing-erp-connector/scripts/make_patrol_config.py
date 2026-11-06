#!/usr/bin/env python3
"""自动生成家族巡检配置（巡检配置.yaml）：领星在售 Listing 按父体分组 + 成本表保本售价当「跌破红线」。

  python make_patrol_config.py                    # 所有 patrol=true 的店铺
  python make_patrol_config.py --marketplace US   # 只更新美国站

写到用户目录 <星空主目录>/config/巡检配置.yaml（插件升级不丢）。已存在时保留用户手改的 floor_price / 昵称，
只补新上架的子体、标出已下架的子体；成本表里没有的 SKU 不写 floor_price（只做价格异动 / 星级 / 差评检查）。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import yaml

HERE = Path(__file__).resolve()


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


client = module(HERE.parent / 'lingxing_client.py', 'lingxing_client_pc')
cost = module(HERE.parents[2] / '_s5_ops_contract' / 'scripts' / 's5_cost_table.py', 's5_cost_table_pc')
_user_cfg = Path(os.environ.get('STARSKY_CONFIG_DIR') or (Path(os.environ.get('STARSKY_HOME') or (Path.home() / '.starsky-codex')).expanduser() / 'config')) / 'lingxing_config.yaml'
if _user_cfg.is_file():
    client.CONFIG = _user_cfg  # 凭据在用户目录（与发行版一致）


def config_path():
    home = Path(os.environ.get('STARSKY_HOME') or (Path.home() / '.starsky-codex')).expanduser()
    return Path(os.environ.get('STARSKY_CONFIG_DIR') or (home / 'config')) / '巡检配置.yaml'


def fetch_listing(cfg, token, sid):
    rows, offset = [], 0
    while True:
        resp = client.call(cfg, token, '/erp/sc/data/mws/listing', {'sid': sid, 'offset': offset, 'length': 1000})
        data = resp.get('data')
        page = data if isinstance(data, list) else (data or {}).get('list') or []
        rows += page
        if len(page) < 1000:
            return rows
        offset += 1000


def short(text, n=24):
    text = ' '.join(str(text or '').split())
    return text if len(text) <= n else text[:n - 1] + '…'


def build_store(store, listing, cost_rows, existing):
    """一个店铺的 families；existing 是已有配置里这个店铺的 {parent: family}，用户手改的值保留。"""
    by_sku = {r['sku']: r for r in cost_rows if r.get('complete')}
    by_asin = {r['asin']: r for r in cost_rows if r.get('complete') and r.get('asin')}
    live = [r for r in listing if str(r.get('status')) == '1' and str(r.get('is_delete')) == '0']
    groups = defaultdict(dict)
    for r in live:
        parent = str(r.get('parent_asin') or r.get('asin') or '').upper()
        asin = str(r.get('asin') or '').upper()
        if parent and asin and asin not in groups[parent]:
            groups[parent][asin] = r
    families, report = [], {'families': 0, 'children': 0, 'with_floor': 0, 'kept_user_values': 0, 'no_cost': []}
    for parent, kids in sorted(groups.items()):
        old = existing.get(parent) or {}
        old_kids = {c.get('asin'): c for c in old.get('children') or []}
        children = []
        for asin, r in sorted(kids.items()):
            prev = old_kids.get(asin) or {}
            row = by_sku.get(str(r.get('seller_sku') or '')) or by_asin.get(asin)
            child = {'asin': asin, 'nickname': prev.get('nickname') or short(r.get('local_name') or r.get('item_name'))}
            if prev.get('floor_price') is not None:
                child['floor_price'] = prev['floor_price']
                report['kept_user_values'] += 1
            elif row and row.get('breakeven_price'):
                child['floor_price'] = row['breakeven_price']
                child['floor_source'] = '成本表保本售价（毛利=0）'
            else:
                report['no_cost'].append(f"{asin}（{r.get('seller_sku')}）")
            if 'floor_price' in child:
                report['with_floor'] += 1
            children.append(child)
        name = old.get('nickname') or short(next(iter(kids.values())).get('local_name') or next(iter(kids.values())).get('item_name'), 16)
        families.append({'parent': parent, 'nickname': name, 'children': children})
        report['families'] += 1
        report['children'] += len(children)
    retired = [p for p in existing if p not in groups]
    if retired:
        report['retired_families'] = retired
    return {'sid': store['sid'], 'nickname': store.get('nickname'), 'marketplace': store.get('marketplace'), 'families': families}, report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--marketplace')
    args = ap.parse_args(argv)
    cfg = client.load_config()
    token = client.get_token(cfg)
    stores = client.patrol_stores(client.normalize_marketplace(args.marketplace) if args.marketplace else None)
    if not stores:
        print(json.dumps({'status': 'blocked', 'error': 'lingxing_config.yaml 里没有要巡检的店铺'}, ensure_ascii=False))
        return 2
    path = config_path()
    doc = yaml.safe_load(path.read_text(encoding='utf-8')) if path.is_file() else {}
    doc = doc or {}
    by_sid = {s.get('sid'): s for s in doc.get('stores') or []}
    summary = {}
    for store in stores:
        mp = client.normalize_marketplace(store.get('marketplace'))
        table = cost.load_marketplace(mp) if mp else None
        existing = {f.get('parent'): f for f in (by_sid.get(store['sid']) or {}).get('families') or []}
        entry, report = build_store(store, fetch_listing(cfg, token, store['sid']), (table or {}).get('rows') or [], existing)
        report['cost_table'] = (table or {}).get('table') or '没登记成本表：s5_cost_table.py --set <站点> <表路径>'
        by_sid[store['sid']] = entry
        summary[mp or str(store['sid'])] = report
    doc['stores'] = list(by_sid.values())
    path.parent.mkdir(parents=True, exist_ok=True)
    head = ('# 家族巡检配置（make_patrol_config.py 生成，可以手改）\n'
            '# floor_price = 跌破就报警的最低售价；默认取成本表保本售价（毛利=0），手改后重新生成会保留你的值。\n')
    path.write_text(head + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding='utf-8')
    print(json.dumps({'status': 'ok', 'config': str(path), 'stores': summary}, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
