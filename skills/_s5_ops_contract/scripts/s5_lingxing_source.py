#!/usr/bin/env python3
"""S5 第二部分数据源：从领星 OpenAPI 只读拉 SP 广告，整理成和「批量操作」下载表同样的行，交给 s5_optimize 诊断。

只调查询接口，不调任何修改接口。凭据读用户目录的 lingxing_config.yaml（与领星巡检同一份）。
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve()
CLIENT = HERE.parents[2] / 'lingxing-erp-connector' / 'scripts' / 'lingxing_client.py'
PAGE = 1000
PLACEMENT = {'TOP OF SEARCH ON-AMAZON': 'placementTop', 'DETAIL PAGE ON-AMAZON': 'placementProductPage',
             'OTHER ON-AMAZON': 'placementRestOfSearch'}
STRATEGY = {'legacyForSales': 'Dynamic bids - down only', 'autoForSales': 'Dynamic bids - up and down', 'manual': 'Fixed bid'}


def client():
    spec = importlib.util.spec_from_file_location('s5_lingxing_client', CLIENT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    home = Path(os.environ.get('STARSKY_HOME') or (Path.home() / '.starsky-codex')).expanduser()
    user = Path(os.environ.get('STARSKY_CONFIG_DIR') or (home / 'config')) / 'lingxing_config.yaml'
    if user.is_file():
        mod.CONFIG = user  # 与发行版一致：凭据在用户目录，不在插件目录
    return mod


def rows_of(resp):
    data = resp.get('data')
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ('list', 'records', 'rows'):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def fetch_all(lx, cfg, token, path, biz, keep=None, limit_pages=400):
    """三种翻页：返回 next_token 就按游标翻；新版广告接口（/newad/）不带版本头时 offset 是页码（从 1 起）；老接口 offset 是条数偏移。"""
    by_page = '/newad/' in path
    out, offset, cursor, seen = [], (1 if by_page else 0), None, set()
    for _ in range(limit_pages):
        params = {**biz, 'length': PAGE, **({'next_token': cursor} if cursor else {'offset': offset})}
        resp = lx.call(cfg, token, path, params)
        if str(resp.get('code')) not in ('0', '200'):
            raise RuntimeError(f"{path} 返回 code={resp.get('code')} {resp.get('msg') or resp.get('message')}")
        page = rows_of(resp)
        marker = repr(page[0]) if page else None
        if marker in seen:
            raise RuntimeError(f'{path} 翻页返回了重复的一页，停止以免重复计数')
        seen.add(marker)
        out += [r for r in page if keep is None or keep(r)]
        data = resp.get('data')
        cursor = resp.get('next_token') or (data.get('next_token') if isinstance(data, dict) else None)
        if not page or (not cursor and len(page) < PAGE):
            return out
        if not cursor:
            offset += 1 if by_page else PAGE
    raise RuntimeError(f'{path} 超过 {limit_pages} 页，没拉完')


def num(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def active(row):
    return row.get('state') == 'enabled' or num(row.get('impressions')) > 0


def metrics(row, cost='cost', sales='sales_amount', orders='order_num'):
    return {'impressions': num(row.get('impressions')), 'clicks': num(row.get('clicks')), 'spend': num(row.get(cost)),
            'sales': num(row.get(sales)), 'orders': num(row.get(orders)), 'units': num(row.get('units') or row.get(orders))}


def collect(sid, start, end):
    """返回 (entities, search_terms, portfolios, meta)，字段与批量下载表读进来后一致。"""
    lx = client()
    cfg = lx.load_config()
    token = lx.get_token(cfg)
    rng = {'sid': sid, 'start_date': start, 'end_date': end}
    t0 = time.time()
    counts = {}
    entities = defaultdict(list)
    portfolios = {str(p['portfolio_id']): p.get('name') or '' for p in
                  fetch_all(lx, cfg, token, '/pb/openapi/newad/portfolios', {'sid': sid})}
    camps = fetch_all(lx, cfg, token, '/erp/sc/data/ads/campaigns', rng, lambda r: r.get('ad_format') in (None, 'SP') and active(r))
    live = {str(c['campaign_id']) for c in camps}
    for c in camps:
        entities['Campaign'].append({'entity': 'Campaign', 'campaign_id': str(c['campaign_id']), 'campaign_name': c.get('campaign_name'),
                                     'state': c.get('state'), 'targeting_type': str(c.get('targeting_type') or '').upper(),
                                     'daily_budget': c.get('daily_budget'), 'bidding_strategy': STRATEGY.get(c.get('strategy'), c.get('strategy')),
                                     'portfolio_id': str(c.get('portfolio_id') or ''), 'portfolio_name': c.get('portfolio_name'),
                                     'start_date': c.get('start_date'),
                                     **metrics(c)})
    in_live = lambda r: str(r.get('campaign_id')) in live
    # 广告位：当前加价（基础数据 bidding JSON）+ 按天广告位报告
    placements, strategy = {}, {}
    for c in fetch_all(lx, cfg, token, '/pb/openapi/newad/spCampaigns', {'sid': sid}, in_live):
        try:
            bidding = json.loads(c.get('bidding') or '{}')
        except (TypeError, ValueError):
            bidding = {}
        strategy[str(c['campaign_id'])] = bidding.get('strategy')
        placements[str(c['campaign_id'])] = {a.get('predicate'): num(a.get('percentage')) for a in bidding.get('adjustments') or []}
    perf = defaultdict(lambda: defaultdict(lambda: {m: 0.0 for m in ('impressions', 'clicks', 'spend', 'sales', 'orders', 'units')}))
    day = date.fromisoformat(start)
    while day <= date.fromisoformat(end):
        for p in fetch_all(lx, cfg, token, '/pb/openapi/newad/campaignPlacementReports', {'sid': sid, 'report_date': day.isoformat()}, in_live):
            key = PLACEMENT.get(str(p.get('placement_type') or '').upper())
            if key:
                for m, v in metrics(p, sales='sales', orders='orders').items():
                    perf[str(p['campaign_id'])][key][m] += v
        day += timedelta(days=1)
    for row in entities['Campaign']:
        cid = row['campaign_id']
        row['placements'] = placements.get(cid, {})
        row['placement_perf'] = {k: dict(v) for k, v in perf.get(cid, {}).items()}
        row['strategy_raw'] = strategy.get(cid)
    for g in fetch_all(lx, cfg, token, '/erp/sc/data/ads/adGroups', rng, in_live):
        entities['Ad Group'].append({'entity': 'Ad Group', 'campaign_id': str(g['campaign_id']), 'ad_group_id': str(g['ad_group_id']),
                                     'ad_group_name': g.get('ad_group_name'), 'state': g.get('state'),
                                     'ad_group_default_bid': g.get('default_bid'), **metrics(g)})
    for k in fetch_all(lx, cfg, token, '/erp/sc/data/ads/keywords', rng, lambda r: in_live(r) and active(r)):
        entities['Keyword'].append({'entity': 'Keyword', 'campaign_id': str(k['campaign_id']), 'ad_group_id': str(k['ad_group_id']),
                                    'keyword_id': str(k['keyword_id']), 'keyword_text': k.get('keyword_text'),
                                    'match_type': k.get('match_type'), 'bid': k.get('bid'), 'state': k.get('state'), **metrics(k)})
    for t in fetch_all(lx, cfg, token, '/erp/sc/data/ads/targets', rng, lambda r: in_live(r) and active(r)):
        entities['Product Targeting'].append({'entity': 'Product Targeting', 'campaign_id': str(t['campaign_id']), 'ad_group_id': str(t['ad_group_id']),
                                              'product_targeting_id': str(t['target_id']), 'product_targeting_expression': t.get('target_expression'),
                                              'bid': t.get('bid'), 'state': t.get('state'), **metrics(t)})
    for a in fetch_all(lx, cfg, token, '/pb/openapi/newad/spProductAds', {'sid': sid}, in_live):
        entities['Product Ad'].append({'entity': 'Product Ad', 'campaign_id': str(a['campaign_id']), 'ad_group_id': str(a['ad_group_id']),
                                       'ad_id': str(a['ad_id']), 'sku': a.get('sku'), 'asin': a.get('asin'), 'state': a.get('state')})
    for kind in ('keyword', 'target'):
        for n in fetch_all(lx, cfg, token, '/pb/openapi/newad/spNegativeTargetsOrKeywords', {'sid': sid, 'target_type': kind}, in_live):
            if n.get('state') == 'archived':
                continue
            entity = ('Negative Product Targeting' if kind == 'target' else
                      'Negative Keyword' if n.get('ad_group_id') else 'Campaign Negative Keyword')
            entities[entity].append({'entity': entity, 'campaign_id': str(n['campaign_id']), 'ad_group_id': str(n.get('ad_group_id') or ''),
                                     'keyword_id': str(n.get('target_id') or ''), 'keyword_text': n.get('negative_text'),
                                     'product_targeting_expression': n.get('negative_text'), 'match_type': n.get('negative_match_type'),
                                     'state': n.get('state')})
    # 搜索词按天报表逐日拉再合并（汇总接口条数不全）
    merged = {}
    day = date.fromisoformat(start)
    while day <= date.fromisoformat(end):
        for q in fetch_all(lx, cfg, token, '/pb/openapi/newad/queryWordReports', {'sid': sid, 'report_date': day.isoformat()}, in_live):
            key = (str(q['campaign_id']), str(q['ad_group_id']), str(q.get('target_id') or ''), q.get('query'))
            row = merged.setdefault(key, {'campaign_id': key[0], 'ad_group_id': key[1], 'keyword_text': q.get('target_text'),
                                          'match_type': q.get('match_type'), 'search_term': q.get('query'),
                                          **{m: 0.0 for m in ('impressions', 'clicks', 'spend', 'sales', 'orders', 'units')}})
            for m, v in metrics(q, sales='sales', orders='orders').items():
                row[m] += v
        day += timedelta(days=1)
    for name, rows in entities.items():
        counts[name] = len(rows)
    counts['search_terms'] = len(merged)
    meta = {'source': 'lingxing_openapi', 'sid': sid, 'start': start, 'end': end, 'seconds': round(time.time() - t0, 1),
            'counts': counts, 'portfolios': len(portfolios), 'note': '只读：仅调用领星查询接口；只保留在投或期间有曝光的对象'}
    return entities, list(merged.values()), portfolios, meta


def _daterange(start, end):
    day = date.fromisoformat(start)
    while day <= date.fromisoformat(end):
        yield day
        day += timedelta(days=1)


def children_of(sid, parents):
    """父体 ASIN → 在售的所有变体 [{asin, sku, parent}]。用领星 Listing（parent_asin 分组），没找到的父体单独返回。"""
    lx = client()
    cfg = lx.load_config()
    token = lx.get_token(cfg)
    wanted = {str(p).strip().upper() for p in parents if str(p).strip()}
    rows, offset = [], 0
    while True:
        resp = lx.call(cfg, token, '/erp/sc/data/mws/listing', {'sid': int(sid), 'offset': offset, 'length': 1000})
        data = resp.get('data')
        page = (data if isinstance(data, list) else (data or {}).get('list')) or []
        rows += [r for r in page if isinstance(r, dict)]
        if len(page) < 1000:
            break
        offset += 1000
    kids, found = [], set()
    for r in rows:
        if str(r.get('is_delete')) != '0':
            continue
        parent = str(r.get('parent_asin') or '').upper()
        asin = str(r.get('asin') or '').upper()
        if parent in wanted or asin in wanted:  # 给的是变体 ASIN 也认：取它所在的整个父体
            found.add(parent if parent in wanted else asin)
            kids.append({'asin': asin, 'sku': str(r.get('seller_sku') or ''), 'parent': parent or asin, 'active': str(r.get('status')) == '1'})
    # 给的是变体 ASIN 时，把同父体的兄弟也带上
    parents_of_given = {k['parent'] for k in kids}
    for r in rows:
        parent = str(r.get('parent_asin') or '').upper()
        if parent in parents_of_given and str(r.get('is_delete')) == '0':
            entry = {'asin': str(r.get('asin') or '').upper(), 'sku': str(r.get('seller_sku') or ''), 'parent': parent, 'active': str(r.get('status')) == '1'}
            if entry not in kids:
                kids.append(entry)
    return kids, sorted(wanted - found)


def collect_products(sid, start, end):
    """产品级（按 ASIN）两段对比：本期 start–end 与之前同样天数。全部订单 / 件数 / 销售额 + 广告花费 / 广告单（同 SKU 归因）。"""
    lx = client()
    cfg = lx.load_config()
    token = lx.get_token(cfg)
    span = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    windows = {'current': (start, end),
               'previous': ((date.fromisoformat(start) - timedelta(days=span)).isoformat(),
                            (date.fromisoformat(start) - timedelta(days=1)).isoformat())}
    out = {}
    for label, (a, b) in windows.items():
        prod = defaultdict(lambda: {'orders': 0, 'units': 0, 'sales': 0.0, 'sku_units': defaultdict(int), 'ad_spend': 0.0,
                                    'ad_sales_all': 0.0, 'ad_orders': 0, 'ad_units': 0, 'ad_sales': 0.0, 'ad_impressions': 0.0,
                                    'ad_clicks': 0.0, 'name': ''})
        stop = (date.fromisoformat(b) + timedelta(days=1)).isoformat()
        for o in fetch_all(lx, cfg, token, '/erp/sc/data/mws/orders', {'sid': sid, 'start_date': a, 'end_date': stop}):
            if str(o.get('order_status') or '').lower().startswith('cancel'):
                continue
            day = str(o.get('purchase_date_local') or '')[:10]
            if not (a <= day <= b):
                continue
            seen = set()
            for item in o.get('item_list') or []:
                asin = str(item.get('asin') or '').upper()
                qty = int(num(item.get('quantity_ordered')))
                if not asin:
                    continue
                prod[asin]['units'] += qty
                prod[asin]['sku_units'][str(item.get('seller_sku') or '')] += qty
                if asin not in seen:
                    prod[asin]['orders'] += 1
                    seen.add(asin)
        for day in _daterange(a, b):
            for s in fetch_all(lx, cfg, token, '/erp/sc/data/sales_report/asinDailyLists', {'sid': sid, 'event_date': day.isoformat()}):
                asin = str(s.get('asin') or '').upper()
                prod[asin]['sales'] += num(s.get('map_value'))
                prod[asin]['name'] = prod[asin]['name'] or s.get('product_name') or ''
            for r in fetch_all(lx, cfg, token, '/pb/openapi/newad/spProductAdReports', {'sid': sid, 'report_date': day.isoformat()}):
                asin = str(r.get('asin') or '').upper()
                p = prod[asin]
                p['ad_spend'] += num(r.get('cost'))
                p['ad_impressions'] += num(r.get('impressions'))
                p['ad_clicks'] += num(r.get('clicks'))
                p['ad_sales_all'] += num(r.get('sales'))
                p['ad_orders'] += int(num(r.get('same_orders')))
                p['ad_units'] += int(num(r.get('same_units')))
                p['ad_sales'] += num(r.get('same_sales'))
        out[label] = {'start': a, 'end': b, 'products': {k: {**v, 'sku_units': dict(v['sku_units'])} for k, v in prod.items() if k}}
    return out
