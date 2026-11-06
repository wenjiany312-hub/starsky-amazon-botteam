#!/usr/bin/env python3
"""S1 市场大盘与同款型池：只从已保存的原始回包计算，供市场分析报告出数。

三层口径并排，不混用：
- 类目大盘：目标节点 category_report / category_trend（Top100 统计）。
- 同款型池：搜索、详情等回包里的商品，按 AI 在 market_narrative.yaml 的 form_review 标签入池。
- 严格同款：需求门 02_demand_validation.yaml 已核对的同形态样本。

本模块不调用 MCP、不写文件；缺数据时如实返回 None 与缺口说明。
"""
from __future__ import annotations

import json
import math
import re
from datetime import date
from pathlib import Path

import yaml

MARKET = Path('S1_选品板块/amazon-market-research')
NARRATIVE = MARKET/'market_narrative.yaml'
PRODUCT_TOOLS = ('product_detail', 'product_search', 'product_search_from_name', 'product_search_from_history',
                 'keyword_search_results', 'category_products', 'potential_product', 'similar_product', 'category_low_price_search')
POOL_TIERS = ('strict', 'same_type')
TIER_LABELS = {'strict': '严格同款', 'same_type': '同款型', 'reference': '功能对照', 'excluded': '剔除'}
PRICE_EDGES = (10, 15, 20, 25, 30, 40, 60)


def short_tool(name):
    return str(name or '').split('__')[-1]


def number(value):
    if isinstance(value, dict): value = value.get('value')
    if value is None or isinstance(value, bool): return None
    if isinstance(value, str): value = value.strip().rstrip('%').replace(',', '')
    try: result = float(value)
    except (TypeError, ValueError): return None
    return result if math.isfinite(result) else None


def nonnegative(value):
    """Sorftime uses -1 for "no data"; never let it enter sums or shares."""
    result = number(value)
    return result if result is not None and result >= 0 else None


def positive(value):
    result = number(value)
    return result if result is not None and result > 0 else None


def unwrap(payload):
    """Decode MCP envelopes down to the data field; plain text stays text."""
    if isinstance(payload, dict) and 'response' in payload: return unwrap(payload['response'])
    if isinstance(payload, dict) and isinstance(payload.get('content'), list):
        texts = [item.get('text', '') for item in payload['content'] if isinstance(item, dict) and item.get('type') == 'text']
        for text in texts:
            try: return unwrap(json.loads(text))
            except (TypeError, ValueError): pass
        return '\n'.join(texts)
    if isinstance(payload, str):
        try: return unwrap(json.loads(payload))
        except (TypeError, ValueError): return payload
    if isinstance(payload, dict) and 'data' in payload and ('doc' in payload or len(payload) <= 3): return unwrap(payload['data'])
    return payload


def load_calls(root):
    """Every saved call in 运行数据, whatever the container format.

    Supported: {"calls": {key: {tool,args,response}}}, {"calls": [{tool,arguments,response,ok}]},
    single {tool,args,response} files, and legacy name__tool.json files holding bare data.
    """
    folder = Path(root)/MARKET/'运行数据'
    calls = []
    for path in sorted(folder.glob('*.json')) if folder.is_dir() else []:
        try: document = json.loads(path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError): continue
        rel = path.relative_to(root).as_posix()
        container = document.get('calls') if isinstance(document, dict) else None
        if isinstance(container, dict): entries = list(container.values())
        elif isinstance(container, list): entries = container
        elif isinstance(document, dict) and document.get('tool'): entries = [document]
        else:
            tool = path.stem.split('__')[-1] if '__' in path.stem else ''
            entries = [{'tool': tool, 'args': {}, 'response': document}] if tool else []
        for entry in entries:
            if not isinstance(entry, dict) or entry.get('ok') is False: continue
            if 'response' not in entry and 'decoded' not in entry: continue
            calls.append({'tool': short_tool(entry.get('tool')), 'args': entry.get('args') or entry.get('arguments') or {},
                          'data': entry['decoded'] if 'decoded' in entry else unwrap(entry.get('response')),
                          'source': rel, 'queried_at': entry.get('queried_at') or entry.get('saved_at')})
    return calls


def read_yaml(path):
    path = Path(path)
    if not path.is_file(): return {}
    try: return yaml.safe_load(path.read_text(encoding='utf-8-sig')) or {}
    except (OSError, yaml.YAMLError): return {}


def load_narrative(root):
    data = read_yaml(Path(root)/NARRATIVE)
    return data if isinstance(data, dict) else {}


def target_node(root, narrative=None):
    narrative = narrative if narrative is not None else load_narrative(root)
    for value in (narrative.get('target_node_id'), read_yaml(Path(root)/MARKET/'comparison_scope.yaml').get('target_node_id'),
                  ((read_yaml(Path(root)/MARKET/'02_demand_validation.yaml').get('category_comparison_plan') or {}).get('target_node_id'))):
        if value: return str(value)
    return None


def _rows(data):
    if isinstance(data, dict) and isinstance(data.get('data'), list): data = data['data']
    if isinstance(data, dict): return [data]
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def _list_rows(items, names):
    result = []
    for item in items or []:
        if isinstance(item, (list, tuple)) and len(item) >= len(names):
            result.append({name: item[index] for index, name in enumerate(names)})
    return result


def category_report(calls, node):
    """Latest target-node report; prefers the current window over historical windows."""
    found = [call for call in calls if call['tool'] == 'category_report' and isinstance(call['data'], dict)
             and str(call['data'].get('node_id') or call['args'].get('node_id')) == str(node)]
    if not node or not found: return None
    found.sort(key=lambda call: (not call['args'].get('query_start'), str(call.get('queried_at') or '')), reverse=True)
    call = found[0]; data = call['data']
    def share(key): return number(data.get(key))
    price = data.get('category_products_price') or []
    company = ('name', 'products', 'new_1m', 'new_2m', 'new_3m', 'new_6m', 'units', 'share',
               'new_1m_share', 'new_2m_share', 'new_3m_share', 'new_6m_share')
    units = positive(data.get('top100_monthly_sales_volume')); revenue = positive(data.get('top100_monthly_sales_amount'))
    return {'node_id': str(node), 'source': call['source'], 'queried_at': call.get('queried_at'),
            'window': [call['args'].get('query_start') or None, call['args'].get('query_date') or None],
            'size': {'top100_units': units, 'top100_revenue': revenue,
                     'avg_price': number(price[0]) if len(price) > 0 else None, 'min_price': number(price[1]) if len(price) > 1 else None,
                     'max_price': number(price[2]) if len(price) > 2 else None, 'avg_profit': number(price[3]) if len(price) > 3 else None,
                     'product_count': positive(data.get('category_product_number')),
                     'available_count': positive(data.get('available_product_count')),
                     'conversion_rate': positive(data.get('conversion_rate')), 'return_rate': positive(data.get('return_rate')),
                     'avg_listing_days': positive(data.get('avg_listing_days') or data.get('product_age_avg_days'))},
            'concentration': {'top3_product': share('top3_product_sales_volume_share'), 'top3_brand': share('top3_brand_sales_volume_share'),
                              'top3_seller': share('top3_seller_sales_volume_share'), 'amazon_owned': share('amazon_owned_sales_volume_share')},
            'brands': sorted(_list_rows(data.get('brands_list'), company), key=lambda r: -(number(r['units']) or 0)),
            'sellers': sorted(_list_rows(data.get('sellers_list'), company), key=lambda r: -(number(r['units']) or 0)),
            'stars': _list_rows(data.get('stars_list'), ('range', 'products', 'share')),
            'review_bands': _list_rows(data.get('ratings_list'), ('range', 'products', 'share')),
            'order_rate': _list_rows(data.get('order_rate'), ('low', 'high', 'rate'))}


def parse_months(values):
    """['2026-01=5', ...] or flat ['202601', 5, ...] -> [(YYYY-MM, value)]."""
    result = []
    if not isinstance(values, list): return result
    if all(isinstance(item, str) and '=' in item for item in values):
        for item in values:
            month, value = item.split('=', 1)
            result.append((month.strip()[:7], number(value)))
    else:
        for index in range(0, len(values) - 1, 2):
            month = str(values[index])
            if re.fullmatch(r'\d{6}', month): month = month[:4]+'-'+month[4:]
            if re.fullmatch(r'\d{4}-\d{2}', month): result.append((month, number(values[index+1])))
    return [(month, value) for month, value in result if value is not None and value >= 0]


def trend_summary(months, today=None):
    """Complete months only; YoY compares the latest 12 complete months with the 12 before."""
    today = today or date.today()
    current = today.strftime('%Y-%m')
    complete = [(month, value) for month, value in months if month < current]
    result = {'months': [{'month': month, 'value': value} for month, value in months],
              'partial_month': next((month for month, _ in months if month >= current), None),
              'latest_complete': complete[-1][0] if complete else None}
    if len(complete) >= 24:
        recent = sum(value for _, value in complete[-12:]); prior = sum(value for _, value in complete[-24:-12])
        result['yoy_12m'] = round(recent/prior - 1, 4) if prior else None
    if complete:
        latest_month, latest = complete[-1]
        year_ago = f'{int(latest_month[:4]) - 1}{latest_month[4:]}'
        before = dict(complete).get(year_ago)
        result['latest_yoy'] = round(latest/before - 1, 4) if before else None
        ytd = [(m, v) for m, v in complete if m[:4] == latest_month[:4]]
        prior_ytd = [dict(complete).get(f'{int(m[:4]) - 1}{m[4:]}') for m, _ in ytd]
        if ytd and all(v is not None for v in prior_ytd) and sum(prior_ytd):
            result['ytd_yoy'] = round(sum(v for _, v in ytd)/sum(prior_ytd) - 1, 4)
            result['ytd_range'] = [ytd[0][0], ytd[-1][0]]
    last12 = complete[-12:]
    if len(last12) >= 6:
        average = sum(v for _, v in last12)/len(last12)
        result['peak_months'] = [m for m, v in sorted(last12, key=lambda x: -x[1])[:3] if average and v >= average*1.15]
        result['low_months'] = [m for m, v in sorted(last12, key=lambda x: x[1])[:3] if average and v <= average*0.85]
    return result


def category_trend(calls, node, today=None):
    for call in reversed(calls):
        if call['tool'] != 'category_trend' or str(call['args'].get('node_id')) != str(node): continue
        if (call['args'].get('trend_index') or 'SalesCount') != 'SalesCount': continue
        data = call['data']
        values = data.get('category_monthly_sales_volume_trend') if isinstance(data, dict) else data
        months = parse_months(values)
        if months: return {'node_id': str(node), 'source': call['source'], **trend_summary(months, today)}
    return None


def adjacent_categories(root, calls):
    scope = read_yaml(Path(root)/MARKET/'comparison_scope.yaml')
    reasons = {str(row.get('node_id')): row.get('reason') for row in scope.get('selected') or [] if isinstance(row, dict)}
    result = []
    for node in [str(x) for x in scope.get('node_ids') or []]:
        report = category_report(calls, node)
        if report:
            result.append({'node_id': node, 'reason': reasons.get(node), 'units': report['size']['top100_units'],
                           'revenue': report['size']['top100_revenue'], 'avg_price': report['size']['avg_price'],
                           'top3_product': report['concentration']['top3_product'], 'amazon_owned': report['concentration']['amazon_owned'],
                           'product_count': report['size']['product_count'], 'source': report['source']})
    return result


def keywords(calls, today=None):
    detail = {}; trends = {}
    for call in calls:
        data = call['data']
        if call['tool'] == 'keyword_detail' and isinstance(data, dict) and data.get('keyword'):
            detail[str(data['keyword']).lower()] = {'row': data, 'source': call['source']}
        if call['tool'] == 'keyword_trend' and isinstance(data, dict) and data.get('keyword'):
            months = parse_months(data.get('search_volume_trend'))
            if months: trends[str(data['keyword']).lower()] = {'summary': trend_summary(months, today), 'source': call['source']}
    result = []
    for key in sorted(set(detail) | set(trends)):
        row = (detail.get(key) or {}).get('row') or {}
        item = {'keyword': row.get('keyword') or key, 'monthly_searches': positive(row.get('monthly_search_volume')),
                'cpc': positive(row.get('cpc') or row.get('recommended_bid') or row.get('avg_cpc')),
                'conversion_rate': number(row.get('search_conversion_rate')), 'weekly_rank': positive(row.get('weekly_search_rank')),
                'click_share_top3': number(row.get('top3_click_share') or row.get('click_concentration')),
                'updated': row.get('update'), 'source': (detail.get(key) or trends.get(key))['source']}
        if key in trends:
            summary = trends[key]['summary']
            item.update(trend=summary['months'][-24:], yoy_12m=summary.get('yoy_12m'), ytd_yoy=summary.get('ytd_yoy'),
                        ytd_range=summary.get('ytd_range'), peak_months=summary.get('peak_months'), trend_source=trends[key]['source'])
        result.append(item)
    return sorted(result, key=lambda r: -(r['monthly_searches'] or 0))


def _product(row, source, tool):
    asin = row.get('asin') or row.get('Asin')
    if not asin: return None
    package = row.get('package_size_cm') or row.get('package_size')
    if isinstance(package, list): package = package[0] if package else None
    return {'asin': str(asin), 'parent_asin': row.get('parent_asin'), 'title': row.get('title'), 'brand': row.get('brand'),
            'price': positive(row.get('price')), 'monthly_sales': nonnegative(row.get('monthly_sales_volume', row.get('monthly_sale_volume'))),
            'reviews': nonnegative(row.get('review_count', row.get('rating_count'))),
            'rating': positive(row.get('star_rating', row.get('ratings', row.get('rating')))),
            'listing_date': row.get('online_date') or row.get('listing_date'), 'fba_fee': positive(row.get('fba_fee')),
            'package': package, 'weight_g': positive(row.get('weight') or row.get('weight_g')),
            'variations': nonnegative(row.get('variation_count')), 'seller_country': row.get('seller_country'),
            'image': row.get('main_image'), 'source': source, 'tool': tool}


TOOL_PRIORITY = {'product_detail': 3, 'product_search': 2, 'product_search_from_name': 2, 'category_products': 2}


def product_candidates(calls):
    """One snapshot per ASIN: detail beats search; historical/peak searches never override current values."""
    best = {}
    for call in calls:
        if call['tool'] not in PRODUCT_TOOLS: continue
        if call['tool'] == 'product_search_from_history': continue
        for row in _rows(call['data']):
            item = _product(row, call['source'], call['tool'])
            if not item: continue
            rank = TOOL_PRIORITY.get(call['tool'], 1)
            old = best.get(item['asin'])
            if old is None or rank > old[0]:
                if old: item = {**{k: v for k, v in old[1].items() if v is not None}, **{k: v for k, v in item.items() if v is not None}}
                best[item['asin']] = (rank, item)
            else:
                for key, value in item.items():
                    if old[1].get(key) is None and value is not None: old[1][key] = value
    return {asin: value[1] for asin, value in best.items()}


def form_labels(root, narrative, facts):
    """AI labels decide the pool. Without them only the demand-gate strict sample is known."""
    labels = {}
    for row in narrative.get('form_review') or []:
        if isinstance(row, dict) and row.get('asin') and row.get('tier') in TIER_LABELS:
            labels[str(row['asin'])] = {'tier': row['tier'], 'reason': row.get('reason'), 'note': row.get('spec_note')}
    origin = 'narrative' if labels else 'none'
    for row in (facts or {}).get('competitors') or []:
        asin = str(row.get('asin') or '')
        if asin and asin not in labels:
            labels[asin] = {'tier': 'strict', 'reason': '需求门已核对的同形态样本', 'note': None}
            origin = origin if origin == 'narrative' else 'demand_gate'
    return labels, origin


def _band(price):
    if price is None: return None
    low = 0
    for edge in PRICE_EDGES:
        if price < edge: return f'${low}–{edge}' if low else f'<${edge}'
        low = edge
    return f'≥${PRICE_EDGES[-1]}'


def quantile(values, q):
    values = sorted(values)
    if not values: return None
    position = (len(values) - 1)*q; low = int(position); high = min(low + 1, len(values) - 1)
    return round(values[low] + (values[high] - values[low])*(position - low), 2)


def pool_statistics(rows, as_of):
    priced = [r for r in rows if r.get('price') is not None]
    units = sum(r['monthly_sales'] or 0 for r in rows)
    revenue = sum((r['monthly_sales'] or 0)*(r['price'] or 0) for r in rows)
    bands = {}
    for row in priced:
        bands.setdefault(_band(row['price']), []).append(row)
    order = ['<$10'] + [f'${a}–{b}' for a, b in zip(PRICE_EDGES, PRICE_EDGES[1:])] + [f'≥${PRICE_EDGES[-1]}']
    band_rows = []
    for name in order:
        members = bands.get(name)
        if not members: continue
        band_units = sum(r['monthly_sales'] or 0 for r in members)
        band_revenue = sum((r['monthly_sales'] or 0)*r['price'] for r in members)
        reviews = sorted(r['reviews'] for r in members if r.get('reviews') is not None)
        band_rows.append({'band': name, 'skus': len(members), 'units': band_units,
                          'unit_share': round(band_units/units, 4) if units else None,
                          'revenue_share': round(band_revenue/revenue, 4) if revenue else None,
                          'review_median': quantile(reviews, .5)})
    ranked = sorted((r['monthly_sales'] or 0 for r in rows), reverse=True)
    ages = []
    for row in rows:
        try: ages.append(((as_of - date.fromisoformat(str(row.get('listing_date'))[:10])).days, row))
        except (TypeError, ValueError): pass
    layers = []
    for label, low, high in (('近6个月', 0, 183), ('6–12个月', 184, 365), ('1–2年', 366, 730), ('2年以上', 731, 10**6)):
        members = [row for age, row in ages if low <= age <= high]
        layer_units = sum(r['monthly_sales'] or 0 for r in members)
        layers.append({'layer': label, 'skus': len(members), 'units': layer_units,
                       'unit_share': round(layer_units/units, 4) if units else None})
    sales = [r['monthly_sales'] for r in rows if r.get('monthly_sales')]
    floor = quantile(sales, .5) if sales else None
    movers = [r for age, r in ages if age <= 365 and (r.get('reviews') or 0) < 300 and floor and (r.get('monthly_sales') or 0) >= floor]
    return {'skus': len(rows), 'units': units or None, 'revenue': round(revenue, 2) if revenue else None,
            'price_quantiles': {k: quantile([r['price'] for r in priced], q) for k, q in (('p25', .25), ('p50', .5), ('p75', .75), ('p90', .9))},
            'price_bands': band_rows, 'top3_share': round(sum(ranked[:3])/units, 4) if units else None,
            'review_median': quantile([r['reviews'] for r in rows if r.get('reviews') is not None], .5),
            'age_layers': layers if ages else [], 'dated_skus': len(ages),
            'new_movers': sorted(({'asin': r['asin'], 'brand': r.get('brand'), 'listing_date': r.get('listing_date'),
                                   'reviews': r.get('reviews'), 'monthly_sales': r.get('monthly_sales'), 'price': r.get('price')} for r in movers),
                                 key=lambda r: -(r['monthly_sales'] or 0))}


def _relevance(row, terms):
    title = str(row.get('title') or '').lower()
    return sum(term in title for term in terms)


def search_terms(calls, facts):
    """Words of the queried keywords and strict-sample titles; used only to order candidates for labelling."""
    words = set()
    for call in calls:
        for key in ('keyword', 'attribute_name', 'name'):
            if call['tool'] in PRODUCT_TOOLS + ('keyword_detail',) and call['args'].get(key):
                words.update(w for w in re.findall(r'[a-z]{4,}', str(call['args'][key]).lower()))
    return words - {'with', 'for', 'dogs', 'cats', 'pets', 'small', 'large', 'medium'}


def same_type_pool(root, calls, narrative, facts, as_of):
    candidates = product_candidates(calls)
    terms = search_terms(calls, facts)
    labels, origin = form_labels(root, narrative, facts)
    rows = []
    for asin, label in labels.items():
        if label['tier'] not in POOL_TIERS: continue
        row = {'monthly_sales': None, 'price': None, 'reviews': None, **(candidates.get(asin) or {'asin': asin, 'source': None})}
        for fact in (facts or {}).get('competitors') or []:
            if str(fact.get('asin')) == asin:
                for key, value in (('price', fact.get('price_usd')), ('monthly_sales', fact.get('monthly_sales')), ('reviews', fact.get('reviews')),
                                   ('brand', fact.get('brand')), ('listing_date', fact.get('online_date'))):
                    if row.get(key) is None and value is not None: row[key] = value
        row.update(tier=label['tier'], tier_label=TIER_LABELS[label['tier']], reason=label['reason'], note=label['note'])
        rows.append(row)
    rows.sort(key=lambda r: -(r.get('monthly_sales') or 0))
    excluded = [{'asin': asin, 'tier': label['tier'], 'tier_label': TIER_LABELS[label['tier']], 'reason': label['reason'],
                 'title': (candidates.get(asin) or {}).get('title')}
                for asin, label in labels.items() if label['tier'] not in POOL_TIERS]
    unlabeled = sorted(set(candidates) - set(labels))
    return {'label_origin': origin, 'rows': rows, 'others': excluded, 'unlabeled_count': len(unlabeled),
            'unlabeled_sample': [{'asin': a, 'title': candidates[a].get('title'), 'monthly_sales': candidates[a].get('monthly_sales')}
                                 for a in sorted(unlabeled, key=lambda a: (-_relevance(candidates[a], terms), -(candidates[a].get('monthly_sales') or 0)))[:40]],
            'strict_count': sum(r['tier'] == 'strict' for r in rows), 'stats': pool_statistics(rows, as_of)}


def review_summary(calls):
    seen = {}
    for call in calls:
        if call['tool'] != 'product_reviews': continue
        asin = str(call['args'].get('asin') or '')
        for row in _rows(call['data']):
            if not row.get('content'): continue
            key = (asin, row.get('review_date'), re.sub(r'\s+', ' ', str(row['content'])).strip())
            seen.setdefault(key, {'asin': asin, 'star': number(row.get('star_rating')), 'date': row.get('review_date'),
                                  'content': row['content'], 'source': call['source']})
    rows = list(seen.values())
    by_asin = {}
    for row in rows:
        node = by_asin.setdefault(row['asin'], {'count': 0, 'low_star': 0})
        node['count'] += 1; node['low_star'] += bool(row['star'] is not None and row['star'] <= 3)
    return {'total': len(rows), 'low_star': sum(v['low_star'] for v in by_asin.values()), 'by_asin': by_asin, 'texts': rows}


def traffic_terms(calls):
    result = {}
    for call in calls:
        if call['tool'] != 'product_traffic_terms': continue
        asin = str(call['args'].get('asin') or '')
        for row in _rows(call['data']):
            if row.get('keyword'):
                result.setdefault(asin, {})[row['keyword']] = {'keyword': row['keyword'], 'searches': positive(row.get('monthly_search_volume')),
                    'position': row.get('exposure_position'), 'organic': row.get('latest_organic_position'), 'ad': row.get('latest_ad_position'),
                    'bid': row.get('recommended_bid')}
    return {asin: sorted(rows.values(), key=lambda r: -(r['searches'] or 0)) for asin, rows in result.items()}


def build_overview(root, facts=None, narrative=None, today=None):
    root = Path(root)
    narrative = narrative if narrative is not None else load_narrative(root)
    today = today or date.today()
    calls = load_calls(root)
    node = target_node(root, narrative)
    category = category_report(calls, node)
    trend = category_trend(calls, node, today)
    pool = same_type_pool(root, calls, narrative, facts, today)
    gaps = []
    if not node: gaps.append({'item': '目标类目节点', 'impact': '无法给出类目大盘', 'how': '在 market_narrative.yaml 写 target_node_id，或在需求门写 category_comparison_plan'})
    if node and not category: gaps.append({'item': '目标类目大盘报表', 'impact': '没有市场规模、集中度、价格与评论分布', 'how': f'调用 Sorftime category_report（node_id={node}）'})
    if node and not trend: gaps.append({'item': '目标类目 24 个月趋势', 'impact': '无法判断增长与季节性', 'how': f'调用 Sorftime category_trend（node_id={node}，SalesCount）'})
    if pool['label_origin'] != 'narrative': gaps.append({'item': '同款型池形态判定', 'impact': f"只有 {pool['strict_count']} 个严格同款，价格带与头部结构样本太少",
                                                         'how': f"在 market_narrative.yaml 的 form_review 给 {pool['unlabeled_count']} 个候选商品打 same_type / reference / excluded 标签"})
    reviews = review_summary(calls)
    return {'schema': 'starsky.market_overview.v1', 'as_of': today.isoformat(), 'target_node_id': node,
            'category': category, 'category_trend': trend, 'adjacent': adjacent_categories(root, calls),
            'keywords': keywords(calls, today), 'pool': pool,
            'reviews': {k: v for k, v in reviews.items() if k != 'texts'}, 'traffic': traffic_terms(calls),
            'calls_count': len(calls), 'gaps': gaps}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    overview = build_overview(args.root)
    print(json.dumps({'target_node_id': overview['target_node_id'], 'category': bool(overview['category']),
                      'category_trend': bool(overview['category_trend']), 'pool_skus': overview['pool']['stats']['skus'],
                      'unlabeled': overview['pool']['unlabeled_count'], 'gaps': overview['gaps']}, ensure_ascii=False, indent=2))
