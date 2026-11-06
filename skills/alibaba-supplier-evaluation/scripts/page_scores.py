#!/usr/bin/env python3
"""1688 页面信号七维评分（R09）：页面上看得到的事实直接打分，标注口径，不等待厂家确认。

- 每一维给 1–5 分、依据和 JSON 路径来源；看不到的信号不扣成 0，按中性 3 分并写明“无此信号”。
- 这是“页面信号分”，不是验厂、样品或签约结论；厂家确认后由 AI 在证据里写入的分数优先。
- 价格维度：有其他候选就按页价排名；只有一家时用合作报价表的批发价对照，否则中性。
"""
from __future__ import annotations

import html
import re

DIMENSIONS = [('D1', '资质信誉', .20), ('D2', '价格竞争力', .20), ('D3', '产品质量信号', .15),
              ('D4', '供应链柔性', .15), ('D5', '跨境服务', .15), ('D6', '定制能力', .10), ('D7', '风险控制', .05)]
BASIS = '页面信号'


def _fields(data, name):
    node = data.get(name) if isinstance(data, dict) else None
    return (node or {}).get('fields') or {} if isinstance(node, dict) else {}


def _num(value):
    if value is None or isinstance(value, bool): return None
    try: return float(str(value).replace('%', '').replace('分', '').replace(',', '').strip())
    except ValueError: return None


def _clamp(value):
    return max(1.0, min(5.0, round(value*2)/2))


def signals(data, source):
    """Page JSON -> readable signals with JSON-path sources. Missing stays None."""
    title = _fields(data, 'productTitle'); shop = title.get('shopInfo') or {}; rate = title.get('rateInfo') or {}
    root = _fields(data, 'Root').get('dataJson') or {}
    sign = (root.get('sellerSign') or {}).get('signs') or root.get('sellerSign') or {}
    order = (root.get('orderParamModel') or {}).get('orderParam') or {}
    skus = (root.get('skuModel') or {}).get('skuInfoMap') or {}
    services = [s.get('serviceName') for s in (_fields(data, 'mainServices').get('guaranteeList') or []) if isinstance(s, dict)]
    shipping = _fields(data, 'shippingServices')
    operations = [o.get('operateType') for o in (_fields(data, 'submitOrder').get('operateItems') or []) if isinstance(o, dict)]
    custom = _fields(data, 'customMade')
    gallery = _fields(data, 'gallery')
    subject = str(gallery.get('subject') or (root.get('tempModel') or {}).get('offerTitle') or '')
    reviews = next((t.get('count') for t in rate.get('commonTagNodeList') or [] if t.get('name') == '全部'), None)
    prices = sorted(p for p in (_num(v.get('price') or v.get('discountPrice')) for v in skus.values() if isinstance(v, dict)) if p)
    return {
        'company': shop.get('authCompanyName') or shop.get('companyName') or (root.get('tempModel') or {}).get('companyName'),
        'factory': bool(sign.get('isFactoryDealer') or shop.get('cardType') == 'factory'),
        'trust_member': bool(sign.get('isTp')),
        'service_score': _num(shop.get('sellerSlrServiceScore')),
        'repeat_rate': _num(shop.get('byrRepeatRate3m')),
        'good_rate': _num(rate.get('goodRates')), 'goods_grade': _num(rate.get('goodsGrade')), 'reviews': _num(reviews),
        'moq': _num(order.get('beginNum')), 'sku_count': len(skus), 'min_price': prices[0] if prices else None,
        'max_price': prices[-1] if prices else None,
        'dropship': bool(root.get('supportPuhuo')), 'cross_border': 'CROSS_BORDER_PUBLISH' in operations or bool(re.search(r'跨境|亚马逊|amazon|外贸', subject, re.I)),
        'delivery_hours': _num(next((s.get('agreeDeliveryHours') for s in shipping.get('protectionInfos') or [] if isinstance(s, dict) and s.get('agreeDeliveryHours')), None)),
        'services': [html.unescape(str(s)) for s in services if s],
        'seven_days_refund': bool(root.get('sevenDaysRefund')),
        # An empty "定制项" widget (only uiType/label) appears on many pages; it is not customization evidence.
        'custom_module': any(v not in (None, '', [], {}, False) for k, v in custom.items() if k not in ('uiType', 'label')),
        'custom_words': bool(re.search(r'定制|OEM|ODM|来图|来样|贴牌', subject, re.I)),
        'title': subject, 'source': source}


def score(sig, peers=None, quote_price=None):
    """Seven dimensions from page signals. peers: page min prices of the other candidates."""
    src = sig['source']
    rows = {}
    # D1 资质信誉
    s = 3.0; why = []
    if sig['factory']: s += 1; why.append('页面工厂标识')
    if sig['trust_member']: s += .5; why.append('诚信通')
    if sig['service_score'] is not None:
        s += .5 if sig['service_score'] >= 4.8 else (-.5 if sig['service_score'] < 4.5 else 0); why.append(f"服务分 {sig['service_score']:g}")
    if sig['repeat_rate'] is not None:
        s += .5 if sig['repeat_rate'] >= 50 else (.25 if sig['repeat_rate'] >= 30 else -.5); why.append(f"近3月回头率 {sig['repeat_rate']:g}%")
    rows['D1'] = (_clamp(s), '；'.join(why) or '无资质信号，按中性', '#/productTitle/fields/shopInfo')
    # D2 价格竞争力
    price = sig['min_price']
    others = sorted(p for p in (peers or []) if p)
    if price is not None and others:
        cheaper = sum(p < price for p in others)
        s = 5 - 3*cheaper/len(others); why = f'页面最低价 ¥{price:g}，{len(others)} 家对照中有 {cheaper} 家更便宜'
    elif price is not None and quote_price:
        gap = price/quote_price - 1
        s = 3.5 if gap <= .1 else 3.0; why = f'页面最低价 ¥{price:g}，合作报价表批发价 ¥{quote_price:g}（页价{"贴近" if gap <= .1 else "高于"}合作报价）；无其他厂家对照'
    else:
        s = 3.0; why = '只有一家且无合作报价对照，按中性 3 分'
    rows['D2'] = (_clamp(s), why, '#/Root/fields/dataJson/skuModel/skuInfoMap')
    # D3 产品质量信号
    if sig['good_rate'] is not None:
        s = 4.5 if sig['good_rate'] >= 98 else 4.0 if sig['good_rate'] >= 95 else 3.0 if sig['good_rate'] >= 90 else 2.0
        if (sig['reviews'] or 0) < 20: s -= .5
        why = f"好评率 {sig['good_rate']:g}%" + (f"，评价 {sig['reviews']:g} 条" if sig['reviews'] is not None else '') + '；页面评价，非样品检测'
    else:
        s, why = 3.0, '页面无评价数据，按中性'
    rows['D3'] = (_clamp(s), why, '#/productTitle/fields/rateInfo')
    # D4 供应链柔性
    s = 3.0; why = []
    if sig['moq'] is not None:
        s += .5 if sig['moq'] <= 2 else (0 if sig['moq'] <= 50 else -.5); why.append(f"起订 {sig['moq']:g} 件")
    if sig['dropship']: s += .5; why.append('支持一件代发')
    if sig['delivery_hours'] is not None and sig['delivery_hours'] <= 48: s += .5; why.append(f"承诺 {sig['delivery_hours']:g} 小时发货")
    if sig['sku_count'] >= 6: s += .5; why.append(f"{sig['sku_count']} 个 SKU")
    rows['D4'] = (_clamp(s), '；'.join(why) or '无柔性信号，按中性', '#/Root/fields/dataJson/orderParamModel')
    # D5 跨境服务
    rows['D5'] = ((3.5, '页面支持跨境铺货 / 标题含跨境；FBA 贴标、箱唛、外箱规格待厂家确认', '#/submitOrder/fields/operateItems')
                  if sig['cross_border'] else (2.5, '页面没有跨境信号；出口包装与 FBA 服务待厂家确认', '#/submitOrder/fields/operateItems'))
    # D6 定制能力
    if sig['custom_module']: s, why = 4.0, '页面有定制服务模块'
    elif sig['custom_words']: s, why = 3.5, '标题提到定制 / OEM / 来图'
    elif sig['factory']: s, why = 3.0, '工厂标识但页面未写定制'
    else: s, why = 2.5, '页面无定制信号'
    rows['D6'] = (s, why + '；打样与改款能力待厂家确认', '#/customMade')
    # D7 风险控制
    s = 3.0; got = [x for x in ('7天无理由退货', '退货包运费', '晚发必赔') if x in sig['services'] or (x == '7天无理由退货' and sig['seven_days_refund'])]
    s += .5*len(got)
    rows['D7'] = (_clamp(s), ('页面保障：' + '、'.join(got)) if got else '页面无退换或赔付保障', '#/mainServices/fields/guaranteeList')
    result = {}
    for key, label, weight in DIMENSIONS:
        value, reason, pointer = rows[key]
        result[key] = {'score': value, 'reason': reason, 'source': src + pointer, 'basis': BASIS}
    total = round(sum(result[k]['score']*w for k, _, w in DIMENSIONS), 2)
    return result, total
