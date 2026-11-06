"""把用户在第 4 步（选款）、第 5 步（选货源）的选择落到案例文件里（R06.1 AS-01 第 5 批）。

3.0.22 的案例里助手每次都现写脚本：改产品档案、改报告里的“需要你决定”、从 1688 页面 JSON 抠价格和件重尺、
改产品事实 / 供应商证据 / 定位、往短名单末尾追加“本轮用户决定”（这一步就是 PL-05 翻倍的起点）。现在：

    python scripts/s1_choices.py --root "<产品根>" style --code 1 --reply "1，选中号宽口手提单包…"
    python scripts/s1_choices.py --root "<产品根>" supplier --offer 1052310354739 --reply "1，选万圣…" [--sku 棕色]

选货源会读该 offer 已保存的 1688_source.json：页面价格档、起订量、件重尺（长宽高、重量），每个值都带 JSON 路径来源，
状态是 page_extracted（页面提取，不是厂家确认）；已经是厂家确认或用户确认的值不会被页面值覆盖。写进 03_product_facts.yaml
后，第 6 步成本模型会自动读到采购价、包装尺寸和重量。

这里只落文件，不替用户做决定：本步回执仍要绑定用户原话（decision.source: user）后正常 record。
"""
from __future__ import annotations

import argparse
import copy
import html
import json
import re
import sys
from datetime import date
from pathlib import Path

import yaml

MARKET = Path('S1_选品板块/amazon-market-research')
SCRAPER = Path('S1_选品板块/1688-product-detail-scraper')
SUPPLIERS = Path('S1_选品板块/alibaba-supplier-evaluation')
PROFILE = MARKET / '01_product_profile.yaml'
FACTS = SCRAPER / '03_product_facts.yaml'
EVIDENCE = SUPPLIERS / '02_supplier_evidence.yaml'
SHORTLIST = SUPPLIERS / '02_1688供应商短名单.md'
CONFIRMED = {'confirmed', 'user_attested', 'supplier_confirmed'}


def _load(path):
    return yaml.safe_load(Path(path).read_text(encoding='utf-8-sig')) or {} if Path(path).is_file() else {}


def _dump(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding='utf-8')


def _reply(reply):
    text = str(reply or '').strip()
    if text.lower() in ('', '继续', '下一步', '好', 'ok', '确认'):
        raise ValueError('需要用户本轮的原话（--reply），泛化的“继续”不算选择')
    return text


def _note(view, relative, title, body):
    path = Path(view) / relative
    if path.is_file():
        from s1_readability import upsert_section
        path.write_text(upsert_section(path.read_text(encoding='utf-8-sig'), title, body), encoding='utf-8')
        return relative.as_posix()
    return None


# ───────────── 第 4 步：选款 ─────────────
def style_options(profile):
    """第 4 步候选款，统一成 [{code, name, …原字段}]。

    3.0.23 自动实跑：本工具原先只认 product_options，而插件里没有任何一步写这个字段，第 4 步永远记不上。
    真实案例里出现过 product_options（code/name）、candidate_options（id/form）、selection.options（id/name）三种写法，都认。"""
    selection = profile.get('selection') if isinstance(profile.get('selection'), dict) else {}
    for rows in (profile.get('product_options'), profile.get('candidate_options'), selection.get('options'), selection.get('candidates')):
        if isinstance(rows, list) and rows:
            return [{**row, 'code': str(next((row[k] for k in ('code', 'id', 'n') if row.get(k) is not None), '')),
                     'name': next((row[k] for k in ('name', 'form', 'product_name', 'product_direction') if row.get(k)), '')}
                    for row in rows if isinstance(row, dict)]
    return []


def _style(view, code, reply, report, name=None):
    view = Path(view)
    profile = _load(view / PROFILE)
    options = style_options(profile)
    chosen = next((o for o in options if o['code'] == str(code)), None)
    if chosen is None and name and not options:
        chosen = {'code': str(code), 'name': str(name).strip()}  # 候选只在聊天里列过：按用户选中的款名直接记
    if chosen is None:
        listed = '、'.join(f"{o['code']} {o['name']}" for o in options)
        raise ValueError(f"产品档案里没有方案 {code}；" + (f'可选：{listed}' if listed else
                         '档案里还没有候选款：把候选写进 01_product_profile.yaml 的 product_options: [{code, name, …}]，或加 --name "<用户选中的款名>" 直接记录'))
    profile['selected_style'] = {**chosen, 'selected_by': 'user', 'user_reply': reply}
    profile['style_selection_status'] = 'user_confirmed'
    _dump(view / PROFILE, profile)
    noted = _note(view, Path(report), '本轮用户决定', f"用户选择方案 {code}：{chosen.get('name')}。\n\n用户原话：{reply}") if report else None
    return {'step': 4, 'selected_style': chosen.get('name'), 'code': str(code), 'report_updated': noted}


# ───────────── 第 5 步：选货源 ─────────────
def find_source(view, offer):
    view = Path(view)
    # 1688_scraper 存在 offer_<编号>/ 下（3.0.23 自动实跑：原先只认名为 <编号> 的目录，选货源永远找不到）
    hits = sorted(p for p in (view / SCRAPER).rglob('1688_source.json') if offer in p.parts or 'offer_' + offer in p.parts)
    if not hits:
        raise ValueError(f'找不到 offer {offer} 的 1688_source.json：先用 1688_scraper 的 get_offer 抓取并保存该货源页')
    return hits[0]


def _pointer_token(text):
    return str(text).replace('~', '~0').replace('/', '~1')


def _sku_tiers(data, relative, sku):
    """按 SKU 定价的页面（skuPriceType=skuPrice）没有 offerPriceRanges，价格在 skuModel.skuInfoMap 里。
    3.0.23 自动实跑：这类页面价格档恒为空，助手只能手工镜像页面价。"""
    root = ((data.get('Root') or {}).get('fields') or {}).get('dataJson') or {}
    skus = (root.get('skuModel') or {}).get('skuInfoMap') or {}
    priced = {name: row for name, row in skus.items() if isinstance(row, dict) and row.get('price') not in (None, '')}
    if not priced:
        return []
    # 1688 stores SKU names HTML-escaped (绿色&gt;L); users and the page show 绿色>L. Match and display
    # the readable name; the JSON pointer keeps the raw key.
    readable = lambda name: html.unescape(str(name))
    wanted = html.unescape(str(sku or ''))
    chosen = {name: row for name, row in priced.items() if all(part in readable(name) for part in wanted.split())} if sku else priced
    if not chosen:
        raise ValueError(f"页面没有包含「{wanted}」的 SKU；可选：{'、'.join(readable(n) for n in priced)}。"
                         "用其中一个原样作 --sku，例如 --sku \"" + readable(next(iter(priced))) + "\"")
    prices = {float(row['price']) for row in chosen.values()}
    if len(prices) > 1:
        listed = '、'.join(f"{readable(name)}（¥{row['price']}）" for name, row in chosen.items())
        raise ValueError(f'各 SKU 价格不同，请加 --sku 指定用户选的那一款：{listed}')
    name, row = next(iter(chosen.items()))
    order = (root.get('orderParamModel') or {}).get('orderParam') or {}
    base = '/Root/fields/dataJson'
    return [{'min_qty': order.get('beginNum') or 1, 'max_qty': None, 'price_cny': float(row['price']), 'sku_name': readable(name),
             'source': f"{relative}#{base}/skuModel/skuInfoMap/{_pointer_token(name)}/price",
             'moq_source': f'{relative}#{base}/orderParamModel/orderParam/beginNum'}]


def page_facts(view, offer, sku=None):
    """从已保存的 1688 页面 JSON 提取价格档、起订量、件重尺；每个值带 JSON 路径来源。"""
    path = find_source(view, offer)
    relative = path.relative_to(view).as_posix()
    data = json.loads(path.read_text(encoding='utf-8'))
    fields = lambda key: (data.get(key) or {}).get('fields') or {}
    observed = date.today().isoformat()
    node = lambda value, pointer, **extra: {'value': value, 'status': 'page_extracted', 'source': f'{relative}#{pointer}',
                                            'observed_at': observed, **extra}
    ranges_ptr = '/mainPrice/fields/finalPriceModel/tradeWithoutPromotion/offerPriceRanges'
    ranges = ((fields('mainPrice').get('finalPriceModel') or {}).get('tradeWithoutPromotion') or {}).get('offerPriceRanges') or []
    tiers = [{'min_qty': r.get('beginAmount'), 'max_qty': r.get('endAmount') or None, 'price_cny': float(r['price']),
              'source': f'{relative}#{ranges_ptr}/{i}/price'} for i, r in enumerate(ranges) if r.get('price') not in (None, '')]
    if not tiers:
        tiers = _sku_tiers(data, relative, sku)
    scale_ptr = '/productPackInfo/fields/pieceWeightScale/pieceWeightScaleInfo'
    scales = ((fields('productPackInfo').get('pieceWeightScale') or {}).get('pieceWeightScaleInfo')) or []
    title = fields('productTitle')
    shop = title.get('shopInfo') or {}
    temp = (fields('Root').get('dataJson') or {}).get('tempModel') or {}
    return {'source': relative, 'company': shop.get('companyName') or temp.get('companyName'),
            'title': (fields('gallery').get('subject') or temp.get('offerTitle')), 'tiers': tiers,
            'scales': [(i, s) for i, s in enumerate(scales)], 'node': node, 'scale_ptr': scale_ptr}


def _keep(old, new):
    """厂家/用户确认过的值不被页面值覆盖。"""
    return old if isinstance(old, dict) and old.get('status') in CONFIRMED else new


def _supplier(view, offer, reply, sku):
    view = Path(view)
    page = page_facts(view, offer, sku)
    node = page['node']
    if not page['tiers']:
        raise ValueError('页面数据里没有价格档，不能作为采购价来源；请先补抓价格或向厂家询价')
    scales = page['scales']
    if sku:
        # A row matches when every one of its spec names (sku1, sku2, …) appears in what the user chose, e.g.
        # "灰色金属挂钩六层" → 灰色金属挂钩 + 六层. 3.0.23 run 6: an exact-field match failed and the first row (三层)
        # was recorded for the 六层 SKU.
        text = re.sub(r'\s+', '', str(sku))
        def names(row):
            return [re.sub(r'\s+', '', str(v)) for k, v in row.items() if k.startswith('sku') and k != 'skuId' and v not in (None, '')]
        matched = [(i, s) for i, s in scales if names(s) and all(n in text for n in names(s))]
        if matched:
            scales = matched
        elif len({(s.get('length'), s.get('width'), s.get('height'), s.get('weight')) for _, s in scales}) > 1:
            scales = []  # several sizes and none matches the chosen SKU: record none rather than the first row
    url = f'https://detail.1688.com/offer/{offer}.html'
    facts = _load(view / FACTS)
    first = page['tiers'][0]
    facts.update(status='selected_page_data_pending_supplier_confirmation',
                 selected_supplier={'name': page['company'], 'offer_id': str(offer), 'offer_url': url,
                                    'selection_source': 'user', 'user_reply': reply, **({'sku': sku} if sku else {})},
                 selected_offer=str(offer))
    page_data = facts.setdefault('page_data', {})
    page_data['price'] = _keep(page_data.get('price'), node(first['price_cny'], first['source'].split('#', 1)[1],
                                                             note=f"起订 {first['min_qty']} 件的页面价；不是厂家确认价"))
    page_data['price_tiers'] = page['tiers']
    page_data['moq'] = node(first['min_qty'], (first.get('moq_source') or first['source'].rsplit('/', 1)[0] + '/beginAmount').split('#', 1)[1])
    factory = facts.setdefault('factory', {})
    factory['unit_price_cny'] = _keep(factory.get('unit_price_cny'), page_data['price'])
    packaging = facts.setdefault('packaging', {})
    sizes = {(s.get('length'), s.get('width'), s.get('height'), s.get('weight')) for _, s in scales}
    if scales:
        index, scale = scales[0]
        pointer = f"{page['scale_ptr']}/{index}"
        note = '件重尺为页面数据，是否含最终包装待厂家确认' + ('' if len(sizes) == 1 else '；各 SKU 件重尺不同，取第一个，选定 SKU 后用 --sku 重新登记')
        if all(isinstance(scale.get(k), (int, float)) and scale.get(k) > 0 for k in ('length', 'width', 'height')):
            packaging['unit_package_cm'] = _keep(packaging.get('unit_package_cm'),
                                                 node([scale['length'], scale['width'], scale['height']], pointer, note=note))
        if isinstance(scale.get('weight'), (int, float)) and scale['weight'] > 0:
            packaging['unit_package_weight_kg'] = _keep(packaging.get('unit_package_weight_kg'),
                                                        node(scale['weight'] / 1000, pointer + '/weight', note=note))
            page_data['weight_kg'] = packaging['unit_package_weight_kg']
    demand = _load(view / MARKET / '02_demand_validation.yaml')
    profile = _load(view / PROFILE)
    positioning = copy.deepcopy(demand.get('validated_positioning') or facts.get('positioning') or {})
    style = (profile.get('selected_style') or {}).get('name')
    positioning.update(selected_offer_id=str(offer), **({'specific_form': style} if style else {}))
    facts['positioning'] = positioning
    _dump(view / FACTS, facts)
    evidence = _load(view / EVIDENCE)
    if evidence:
        evidence['selected_supplier'] = {'name': page['company'], 'offer_id': str(offer), 'offer_url': url, 'selection_source': 'user'}
        _dump(view / EVIDENCE, evidence)
    if profile:
        profile['selected_supplier'] = {'company': page['company'], 'offer_id': str(offer), 'offer_url': url, 'selected_by': 'user'}
        _dump(view / PROFILE, profile)
    noted = _note(view, SHORTLIST, '本轮用户决定',
                  f"用户选择货源：{page['company']}（offer {offer}）{'，SKU ' + sku if sku else ''}。页面价与件重尺为页面提取值，厂家确认后补。\n\n用户原话：{reply}")
    return {'step': 5, 'offer_id': str(offer), 'company': page['company'], 'price_tiers': len(page['tiers']),
            'unit_price_cny': factory['unit_price_cny']['value'], 'package_cm': (packaging.get('unit_package_cm') or {}).get('value'),
            'weight_kg': (packaging.get('unit_package_weight_kg') or {}).get('value'), 'facts': FACTS.as_posix(), 'report_updated': noted}


def _apply(root, step, callback):
    from case_mutation import mutate
    return mutate(Path(root).resolve(), 'S1', step, callback, operation='user_choice')


def record_style(root, code, reply, report=None, name=None):
    reply = _reply(reply)
    return _apply(root, 4, lambda view: _style(view, code, reply, report, name))


def record_supplier(root, offer, reply, sku=None):
    reply = _reply(reply)
    offer = str(offer).strip()
    if not offer.isdigit():
        raise ValueError('offer 应是 1688 商品编号（纯数字）')
    return _apply(root, 5, lambda view: _supplier(view, offer, reply, sku))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', required=True, type=Path)
    sub = parser.add_subparsers(dest='kind', required=True)
    style = sub.add_parser('style', help='第 4 步选款')
    style.add_argument('--code', required=True)
    style.add_argument('--reply', required=True)
    style.add_argument('--report', help='要写入“本轮用户决定”的报告相对路径（可选）')
    style.add_argument('--name', help='候选只在聊天里列过、档案里没有候选表时，写用户选中的款名')
    supplier = sub.add_parser('supplier', help='第 5 步选货源')
    supplier.add_argument('--offer', required=True)
    supplier.add_argument('--reply', required=True)
    supplier.add_argument('--sku')
    options = parser.parse_args(argv)
    try:
        result = (record_style(options.root, options.code, options.reply, options.report, options.name) if options.kind == 'style'
                  else record_supplier(options.root, options.offer, options.reply, options.sku))
    except (OSError, ValueError, KeyError, yaml.YAMLError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps({'ok': True, **result}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
