#!/usr/bin/env python3
"""S1 成本账 + 广告可推性（确定性：同一份 05_cost_inputs.yaml 永远得到同一份结果）。

  <python> <skill>/scripts/s1_cost_model.py --run "<产品根>" --init    生成待填表 05_cost_inputs.yaml
  <python> <skill>/scripts/s1_cost_model.py --run "<产品根>"           计算，出 05 MD / YAML / XLSX

只给指标和档位，不替用户判“做/不做”。
"""
from __future__ import annotations

import argparse
import copy
import datetime
import hashlib
import json
import math
import re
import statistics
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve()
DEFAULTS = HERE.parents[1] / 'references' / 'cost_defaults.yaml'
USER_PROFILE = Path.home() / '.starsky' / '物流配置.yaml'
OUT = Path('S1_选品板块') / 'amazon-market-research'
INPUTS, MD, MODEL, XLSX = '05_cost_inputs.yaml', '05_成本账与广告可推性.md', '05_cost_model.yaml', '05_利润测算表.xlsx'
GENERATOR = 's1_cost_model.py'
VERSION = '1.1'
REAL = {'confirmed', 'user_attested', 'user_profile'}
SOURCE_PRIORITY = ['sif', 'xiyou', 'sellersprite', 'sorftime']
CURRENCY = {'US': 'USD', 'UK': 'GBP', 'DE': 'EUR', 'FR': 'EUR', 'IT': 'EUR', 'ES': 'EUR', 'CA': 'CAD', 'JP': 'JPY'}
G_PER_OZ = 28.349523125
CM_PER_IN = 2.54
CUFT_CM3 = 28316.846592

FIELDS = {
    ('factory', 'unit_price_cny'): ('出厂单价', '¥/件', '按首批数量的出厂单价是多少？含税还是不含税？'),
    ('factory', 'tax_surcharge_rate'): ('开票加点', '比例', '不含税价如需开票，加几个点？'),
    ('factory', 'packaging_cny_per_unit'): ('包装费', '¥/件', '单价含不含彩盒/OPP 袋？不含的话包装每件多少钱？'),
    ('factory', 'labeling_cny_per_unit'): ('贴标费', '¥/件', '能否代贴 FNSKU 标签和箱唛？每件多少钱？'),
    ('factory', 'unit_package_cm'): ('单品包装尺寸', 'cm 长×宽×高', '单品含包装的长宽高各多少 cm？'),
    ('factory', 'unit_package_weight_kg'): ('单品含包装重量', 'kg', '单品含包装毛重多少 kg？'),
    ('factory', 'carton_cm'): ('外箱尺寸', 'cm 长×宽×高', '外箱长宽高各多少 cm？'),
    ('factory', 'carton_qty'): ('每箱件数', '件', '一箱装多少件？'),
    ('factory', 'carton_gross_kg'): ('外箱毛重', 'kg', '整箱毛重多少 kg？'),
    ('factory', 'domestic_freight_cny_per_shipment'): ('工厂→货代运费', '¥/票', '送到货代仓（深圳/义乌等）运费多少？谁承担？'),
    ('factory', 'one_off_cny'): ('打样+开模+检测', '¥ 一次性', '打样费、开模费、检测报告费各多少？下大货能否退？'),
    ('logistics', 'customs_mode'): ('报关方式', '', None),
    ('logistics', 'customs_fee_cny_per_shipment'): ('报关费', '¥/票', None),
    ('logistics', 'other_cny_per_shipment'): ('其他每票杂费', '¥/票', None),
    ('logistics', 'first_leg_basis'): ('头程计费方式', 'kg / cbm', None),
    ('logistics', 'first_leg_rate_cny'): ('头程单价', '¥/kg 或 ¥/方', None),
    ('logistics', 'volumetric_divisor'): ('体积重除数', '', None),
    ('logistics', 'duty_included'): ('头程已包税', '是/否', None),
    ('logistics', 'duty_rate'): ('关税率合计', '比例', None),
    ('amazon', 'referral_rate'): ('佣金率', '比例', None),
    ('amazon', 'referral_min'): ('最低佣金', '站点币', None),
    ('amazon', 'fba_fee'): ('FBA 配送费', '站点币/件', None),
    ('amazon', 'storage_per_cuft_month'): ('月仓储费', '站点币/立方英尺/月', None),
    ('amazon', 'storage_months'): ('平均在库月数', '月', None),
    ('amazon', 'inbound_placement_fee'): ('入库配置费', '站点币/件', None),
    ('amazon', 'return_loss_rate'): ('退货损耗率', '比例', None),
}
CRITICAL = [('factory', 'unit_price_cny'), ('factory', 'unit_package_cm'), ('factory', 'unit_package_weight_kg'),
            ('logistics', 'first_leg_rate_cny')]
STATUS_LABEL = {'confirmed': '厂家确认', 'user_attested': '用户认可', 'user_profile': '我的物流配置',
                'default_assumption': '默认假设', 'fx_snapshot': '汇率快照', 'page_extracted': '1688 页面价',
                'demo': '演示推演', 'ai_inferred': 'AI 推断', 'table_estimate': '近似表估算', 'mcp': 'MCP 实取',
                'missing': '缺失', 'unlabeled': '未标来源'}
OFFER_TYPE = {'single': '单品通用', 'bundle': '轻组合（加配件）', 'gift_set': '礼盒', 'multipack': '多件装',
              'custom_pack': '定制包装'}
LEVEL_LABEL = {'high': '高：核心词 CPC ≥ 保本线 2 倍', 'elevated': '偏高：核心词多数超保本线',
               'moderate': '中：多数在保本线与目标线之间', 'ok': '低：多数在目标 CPC 内',
               'loss': '广告前已亏损', 'no_cpc': '缺 CPC 数据', 'pending': '待采购价'}


def load_yaml(path):
    try:
        data = yaml.safe_load(Path(path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return {}
    return data if isinstance(data, dict) else {}


def cell(value, status, source=''):
    if value is None:
        return {'value': None, 'status': 'missing', 'source': ''}
    return {'value': value, 'status': status, 'source': source}


def layers(root):
    """Profile layers, lowest priority first."""
    out = [(load_yaml(DEFAULTS), 'default_assumption', 'cost_defaults.yaml')]
    for path, label in ((USER_PROFILE, '~/.starsky/物流配置.yaml'),
                        (root / '00_产品资料' / '物流配置.yaml', '00_产品资料/物流配置.yaml')):
        if path.is_file():
            out.append((load_yaml(path), 'user_profile', label))
    return out


def pick(stack, group, key):
    chosen = cell(None, 'missing')
    for data, status, source in stack:
        section = data.get(group) if group else data
        if isinstance(section, dict) and section.get(key) is not None:
            chosen = cell(section[key], status, source)
    return chosen


def money_values(text):
    return [float(v) for v in re.findall(r'\$?\s*(\d+(?:\.\d+)?)', str(text))]


def profile_prices(root):
    profile = load_yaml(root / OUT / '01_product_profile.yaml')
    found = {}

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ('floor_price', 'ceiling_price', 'target_price', 'recommended_price') and money_values(value):
                    found.setdefault(key, money_values(value)[0])
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    walk(profile)
    floor, ceiling = found.get('floor_price'), found.get('ceiling_price')
    target = found.get('recommended_price') or found.get('target_price')
    if floor and ceiling and ceiling > floor:
        mid = target or math.floor((floor + ceiling) / 2) + 0.99
        return [{'id': 'A', 'label': '低价走量', 'price': floor},
                {'id': 'B', 'label': '主推', 'price': round(mid, 2), 'recommended': True},
                {'id': 'C', 'label': '高价升级', 'price': ceiling}]
    if target:
        return [{'id': 'B', 'label': '主推', 'price': target, 'recommended': True}]
    return []


def snapshot_rate(snap):
    """成本测算该用快照里的哪个汇率：实际结汇价 > 含缓冲有效汇率（schema 的嵌套字段）> 旧版顶层字段。

    与 scripts/refresh_fx_snapshot.py 的 effective_rate 是同一约定；本脚本独立运行，不能 import 它。
    """
    for value in ((snap.get('settlement_override') or {}).get('actual_settlement_rate'),
                  (snap.get('fx_buffer') or {}).get('effective_purchase_rate'),
                  snap.get('effective_purchase_rate')):
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            return number
    return None


def skeleton(root):
    stack = layers(root)
    fx = pick(stack, None, 'fx')
    # 先刷新快照（scripts/refresh_fx_snapshot.py）再 --init：此处只在生成输入表时读一次快照。
    rate = snapshot_rate(load_yaml(root / 'fx_rate_snapshot.yaml'))
    if rate:
        fx = cell(rate, 'fx_snapshot', 'fx_rate_snapshot.yaml')
    factory = {key: cell(None, 'missing') for (group, key) in FIELDS if group == 'factory'}
    factory['tax_surcharge_rate'] = cell(0, 'default_assumption', '默认按含税价')
    for key in PROFILE_FACTORY:  # the user's own packing / labelling rate, until a supplier quote replaces it
        mine = pick(stack[1:], 'factory', key)
        if mine['status'] == 'user_profile':
            factory[key] = mine
    return {
        'schema_version': VERSION, 'generator': GENERATOR, 'marketplace': 'US', 'currency': 'USD',
        'product_configuration_sha256': None,
        'product': '', 'fx': fx,
        'target_net_margin': pick(stack, None, 'target_net_margin'),
        'target_pre_ad_margin': pick(stack[1:], None, 'target_pre_ad_margin'),
        'first_batch_qty': pick(stack, None, 'first_batch_qty'),
        'price_scenarios': profile_prices(root),
        'factory': factory,
        'logistics': {key: pick(stack, 'logistics', key) for (group, key) in FIELDS if group == 'logistics'},
        'amazon': {key: pick(stack, 'amazon', key) for (group, key) in FIELDS if group == 'amazon'},
        'ads': {'cvr_scenarios': pick(stack, 'ads', 'cvr_scenarios'), 'keywords': []},
        'market_price_bands': {'p25': None, 'p50': None, 'p75': None, 'p90': None,
                               'source': '01_市场穿透报告 M09 价格分位'},
        'base_pain_points': [],
        'offers': [],
        'offer_format': '[{id, name, type: bundle|gift_set|multipack|custom_pack, contents, pain_points: [], '
                        'main_qty, extra_cny_per_unit, extra_one_off_cny, package_cm, package_weight_kg, '
                        'prices: [], recommended_price}]',
        'status_legend': 'confirmed=厂家确认 / user_attested=用户认可 / user_profile=我的物流配置 / page_extracted=1688页面价 / '
                         'demo=演示推演 / ai_inferred=AI推断 / default_assumption=默认假设 / missing=缺失',
        'keyword_format': '[{keyword, search_volume, cpc, source: sif|xiyou|sellersprite|sorftime, core: true|false}]',
    }


class Inputs:
    def __init__(self, doc):
        self.doc = doc
        self.used = {}

    def get(self, group, key, default=None):
        node = (self.doc.get(group) or {}).get(key) if group else self.doc.get(key)
        if isinstance(node, dict) and 'value' in node:
            value, status, source = node.get('value'), node.get('status') or 'unlabeled', node.get('source') or ''
        else:
            value, status, source = node, 'unlabeled', ''
        if value is None:
            status, source = 'missing', ''
        self.used[(group, key)] = (value, status, source)
        return default if value is None else value

    def status(self, group, key):
        return self.used.get((group, key), (None, 'missing', ''))[1]


def num(value, name, required=False):
    if value is None or value == '':
        if required:
            raise ValueError(f'缺必填项：{name}')
        return None
    if isinstance(value, bool):
        raise ValueError(f'{name} 应为数字')
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{name} 应为数字，收到 {value!r}') from None
    if not math.isfinite(number) or number < 0:
        raise ValueError(f'{name} 不能为负数或非数字')
    return number


def dims(value, name):
    if value in (None, '', []):
        return None
    if isinstance(value, str):
        if not re.fullmatch(r'\s*\d+(?:\.\d+)?\s*[xX×,， ]\s*\d+(?:\.\d+)?\s*[xX×,， ]\s*\d+(?:\.\d+)?\s*', value):
            raise ValueError(f'{name} 需要三个正数尺寸')
        value = re.findall(r'\d+(?:\.\d+)?', value)
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f'{name} 需要 [长, 宽, 高]')
    out = [num(v, name, True) for v in value]
    if min(out) <= 0:
        raise ValueError(f'{name} 各边必须大于 0')
    return out


def flag(value):
    if isinstance(value, str):
        return value.strip().lower() in {'true', 'yes', 'y', '是', '1'}
    return bool(value)


def band(margin, bands):
    for floor, label in sorted(bands, key=lambda b: -b[0]):
        if margin >= floor:
            return label
    return bands[-1][1]


def delivery_module(name):
    """Load a shared scripts/<name>.py by path (the cost engine's neighbours)."""
    scripts = HERE.parents[3] / 'scripts'
    import importlib.util
    key = 'starsky_s1_'+name+'_'+hashlib.sha256(str((scripts/(name+'.py')).resolve()).encode()).hexdigest()[:16]
    if key not in sys.modules:
        previous = list(sys.path)
        try:
            sys.path.insert(0, str(scripts))
            spec = importlib.util.spec_from_file_location(key, scripts / (name+'.py'))
            module = importlib.util.module_from_spec(spec); sys.modules[key] = module; spec.loader.exec_module(module)
        finally:
            sys.path[:] = previous
    return sys.modules[key]


def cost_engine():
    # The canonical engine also serves the loopback calculator; no JS arithmetic.
    scripts = HERE.parents[3] / 'scripts'
    import importlib.util
    name = 'starsky_s1_cost_engine_'+hashlib.sha256(str((scripts/'s1_cost_engine.py').resolve()).encode()).hexdigest()[:16]
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, scripts / 's1_cost_engine.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


def first_leg(basis, rate, divisor, carton, carton_qty, carton_kg, unit, unit_kg):
    return cost_engine().freight_cost(basis, rate, divisor, carton, carton_qty, carton_kg, unit, unit_kg)


def fba_estimate(price, unit, unit_kg, table, market, evaluation_date=None):
    if market != 'US':
        return None, '非美国站：请填后台收入计算器的 FBA 费'
    fee, proof = cost_engine().estimate_fba(price, unit, unit_kg, table,
                                          str(evaluation_date or datetime.date.today()))
    # M1's displayed fee was rounded before its legacy ledger; preserve that contract.
    note = proof.get('reason') or (f"{'大号标准尺寸' if proof['size_tier']=='large_standard' else '小号标准尺寸'}；2026官方非服装表；{proof['season']}；附加费 {proof['surcharge_rate']:.1%}")
    return (round(fee, 2) if fee is not None else None), note


def landed(p):
    return cost_engine().legacy_landed(p)


def keyword_rows(raw, warnings):
    grouped = {}
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        keyword = str(item.get('keyword') or '').strip().lower()
        cpc = num(item.get('cpc'), f'关键词 {keyword} 的 CPC')
        if not keyword or cpc is None:
            continue
        grouped.setdefault(keyword, []).append({
            'cpc': cpc, 'source': str(item.get('source') or 'other').strip().lower(),
            'search_volume': num(item.get('search_volume'), f'关键词 {keyword} 的搜索量'),
            'core': flag(item.get('core'))})
    rows = []
    for keyword, entries in grouped.items():
        entries.sort(key=lambda e: SOURCE_PRIORITY.index(e['source']) if e['source'] in SOURCE_PRIORITY else 99)
        best = entries[0]
        volumes = [e['search_volume'] for e in entries if e['search_volume'] is not None]
        rows.append({'keyword': keyword, 'cpc': best['cpc'], 'source': best['source'],
                     'other_sources': {e['source']: e['cpc'] for e in entries[1:]},
                     'search_volume': max(volumes) if volumes else None, 'core': any(e['core'] for e in entries)})
    rows.sort(key=lambda r: (-(r['search_volume'] or 0), r['keyword']))
    if rows and all(r['source'] == 'sorftime' for r in rows):
        warnings.append('CPC 只有 Sorftime 参考值：请用 SIF 建议竞价或西柚 CPC 复核核心词')
    if rows and not any(r['core'] for r in rows):
        for row in rows[:5]:
            row['core'] = True
    return rows


def ad_pressure(scenario, rows, cvr):
    if scenario['contribution'] is None:
        return {'level': 'pending', 'label': LEVEL_LABEL['pending'], 'keywords': []}
    if scenario['contribution'] <= 0:
        return {'level': 'loss', 'label': LEVEL_LABEL['loss'], 'keywords': []}
    be = scenario['contribution'] * cvr
    allow = scenario['price'] * scenario['target_acos'] * cvr
    judged = []
    for row in rows:
        light = '绿' if row['cpc'] <= allow + 1e-9 else '黄' if row['cpc'] <= be + 1e-9 else '红'
        judged.append({**row, 'light': light, 'ratio_to_break_even': round(row['cpc'] / be, 2)})
    core = [r for r in judged if r['core']]
    if not core:
        return {'level': 'no_cpc', 'label': LEVEL_LABEL['no_cpc'], 'keywords': judged}
    weight = lambda r: r['search_volume'] or 1  # noqa: E731
    total = sum(weight(r) for r in core)
    red = sum(weight(r) for r in core if r['light'] == '红') / total
    green = sum(weight(r) for r in core if r['light'] == '绿') / total
    ratio = statistics.median(r['ratio_to_break_even'] for r in core)
    level = ('high' if red >= 0.5 and ratio >= 2 else 'elevated' if red >= 0.5
             else 'ok' if green >= 0.5 else 'moderate')
    return {'level': level, 'label': LEVEL_LABEL[level], 'red_share': round(red, 3), 'green_share': round(green, 3),
            'core_median_cpc': round(statistics.median(r['cpc'] for r in core), 2),
            'core_median_ratio': ratio, 'keywords': judged}


def market_bands(node):
    node = node or {}
    out = {}
    for key in ('p25', 'p50', 'p75', 'p90'):
        value = node.get(key)
        value = value.get('value') if isinstance(value, dict) else value
        if value is not None:
            out[key] = num(value, f'市场价格分位 {key}')
    return out


def position(price, bands):
    if not bands:
        return '—'
    steps = [('p25', '低于 P25（走量价）'), ('p50', 'P25–P50（性价比区）'), ('p75', 'P50–P75（主流区）'),
             ('p90', 'P75–P90（偏高）')]
    for key, label in steps:
        if key in bands and price <= bands[key] + 1e-9:
            return label
    return '高于 P90（溢价）' if 'p90' in bands else '高于已知分位'


def build_offers(doc, scenarios_in, unit, unit_kg, leg, basis, rate, divisor, warnings):
    """The single product plus each bundle/multipack/gift offer; a bundle sells as one unit."""
    offers = [{'id': 'base', 'name': '单品', 'type': 'single', 'contents': doc.get('base_contents') or '主品 ×1',
               'pain_points': doc.get('base_pain_points') or [], 'mult': 1.0, 'extra': 0.0, 'extra_one_off': 0.0,
               'dims': unit, 'kg': unit_kg, 'leg': leg, 'fba': None, 'own_package': False, 'prices': scenarios_in}]
    seen = {'base'}
    for raw in doc.get('offers') or []:
        if not isinstance(raw, dict):
            continue
        oid = str(raw.get('id') or '').strip()
        if not oid or oid in seen:
            raise ValueError(f'组合方案 id 缺失或重复：{oid!r}')
        seen.add(oid)
        name = str(raw.get('name') or oid)
        mult = num(raw.get('main_qty', 1), f'{name} 主品件数', True)
        if mult <= 0 or not mult.is_integer():
            raise ValueError(f'{name} 主品件数必须是正整数')
        extra = num(raw.get('extra_cny_per_unit', 0), f'{name} 配件/定制包装费') or 0.0
        once = num(raw.get('extra_one_off_cny', 0), f'{name} 一次性费用') or 0.0
        pdims = dims(raw.get('package_cm'), f'{name} 组合包装尺寸')
        pkg = num(raw.get('package_weight_kg'), f'{name} 组合包装重量')
        if pdims and pkg is not None:
            o_leg = first_leg(basis, rate, divisor, None, None, None, pdims, pkg)[0]
        else:
            o_leg = leg * mult if leg is not None else None
            pdims, pkg = unit, (unit_kg * mult if unit_kg is not None else None)
            if mult != 1 or extra:
                warnings.append(f'{name}：未填组合包装尺寸重量，头程按主品×件数、FBA/仓储按单品尺寸估，会偏低')
        prices = [p for p in (raw.get('prices') or []) if p is not None]
        if not prices:
            raise ValueError(f'{name} 没有售价（prices）')
        chosen = raw.get('recommended_price')
        offers.append({
            'id': oid, 'name': name, 'type': str(raw.get('type') or 'bundle'), 'contents': str(raw.get('contents') or ''),
            'pain_points': [str(p) for p in raw.get('pain_points') or []], 'mult': mult, 'extra': extra,
            'extra_one_off': once, 'dims': pdims, 'kg': pkg, 'leg': o_leg, 'own_package': bool(raw.get('package_cm')) and raw.get('package_weight_kg') is not None,
            'fba': num(raw.get('fba_fee'), f'{name} FBA 费'),
            'prices': [{'id': f'{oid}·{float(p):g}', 'label': name, 'price': p,
                        'recommended': chosen is not None and float(p) == float(chosen)} for p in prices]})
    return offers


def _compute_scenarios(doc, defaults):
    x = Inputs(doc)
    warnings = []
    market = str(doc.get('marketplace') or 'US').upper()
    currency = doc.get('currency') or CURRENCY.get(market, 'USD')
    fx = num(x.get(None, 'fx'), '汇率 fx', True)
    if fx <= 0:
        raise ValueError('汇率 fx 必须大于 0')
    target = num(x.get(None, 'target_net_margin', 0.15), '目标净利率')
    qty = num(x.get(None, 'first_batch_qty'), '首批数量 first_batch_qty', True)
    if qty <= 0:
        raise ValueError('首批数量必须大于 0')
    scenarios_in = [s for s in (doc.get('price_scenarios') or []) if isinstance(s, dict)]
    if not scenarios_in:
        raise ValueError('price_scenarios 为空：至少填一个售价档')

    def f(key, default=None):
        return x.get('factory', key, default)

    def lg(key, default=None):
        return x.get('logistics', key, default)

    def az(key, default=None):
        return x.get('amazon', key, default)

    unit_price = num(f('unit_price_cny'), '出厂单价')
    unit = dims(f('unit_package_cm'), '单品包装尺寸')
    unit_kg = num(f('unit_package_weight_kg'), '单品含包装重量')
    carton = dims(f('carton_cm'), '外箱尺寸')
    carton_qty = num(f('carton_qty'), '每箱件数')
    carton_kg = num(f('carton_gross_kg'), '外箱毛重')
    lg('customs_mode')
    basis = str(lg('first_leg_basis', 'kg')).lower()
    if basis not in {'kg', 'cbm'}:
        raise ValueError('first_leg_basis 只能是 kg 或 cbm')
    divisor = num(lg('volumetric_divisor', 6000), '体积重除数')
    if divisor <= 0:
        raise ValueError('体积重除数必须大于 0')
    leg, leg_note, by_carton = first_leg(basis, num(lg('first_leg_rate_cny'), '头程单价'), divisor,
                                         carton, carton_qty, carton_kg, unit, unit_kg)
    if leg is not None and not by_carton:
        warnings.append('外箱规格缺失：头程按单品包装估算，未含外箱余量，实际通常偏高 5–15%')
    duty_included = flag(lg('duty_included', True))
    duty_rate = num(lg('duty_rate'), '关税率')
    if not duty_included and duty_rate is None:
        warnings.append('头程不包税但关税率未填：实际到岸成本及实际利润指标为空')
    params = {
        'unit_price': unit_price, 'tax': num(f('tax_surcharge_rate', 0), '开票加点') or 0,
        'packaging': num(f('packaging_cny_per_unit'), '包装费'), 'labeling': num(f('labeling_cny_per_unit'), '贴标费'),
        'domestic': num(f('domestic_freight_cny_per_shipment'), '工厂→货代运费'),
        'customs': num(lg('customs_fee_cny_per_shipment'), '报关费'), 'other': num(lg('other_cny_per_shipment'), '其他杂费'),
        'one_off': num(f('one_off_cny'), '打样开模检测'), 'first_leg': leg, 'qty': qty, 'fx': fx,
        'duty_included': duty_included, 'duty_rate': duty_rate,
    }
    for key, label in (('packaging', '包装费'), ('labeling', '贴标费'), ('domestic', '工厂→货代运费'),
                       ('customs', '报关费'), ('other', '其他杂费'), ('one_off', '打样开模检测费')):
        if params[key] is None:
            params[key] = 0.0
            warnings.append(f'{label}未填：实际到岸成本及实际利润指标为空')
    cost_ready = unit_price is not None and leg is not None
    if unit_price is None:
        warnings.append('出厂单价缺失：只能倒算允许到岸成本（采购目标），不出毛利和广告可推性')
    elif leg is None:
        warnings.append(f'头程无法计算（{leg_note}）：不出毛利和广告可推性')
    referral_rate = num(az('referral_rate', 0.15), '佣金率')
    referral_min = num(az('referral_min', 0), '最低佣金') or 0
    fba_given = num(az('fba_fee'), 'FBA 配送费')
    fba_status = x.status('amazon', 'fba_fee') if fba_given is not None else 'table_estimate'
    storage_rate = num(az('storage_per_cuft_month', 0), '月仓储费') or 0
    storage_months = num(az('storage_months', 0), '在库月数') or 0
    if not unit:
        warnings.append('缺单品包装尺寸：仓储费无法计算，实际利润指标为空')
    placement = num(az('inbound_placement_fee'), '入库配置费')
    if placement is None:
        placement = 0.0
        warnings.append('入库配置费未填：费用合计与实际利润指标为空；分仓方案确认后填入费用或明确的零费用')
    return_rate = num(az('return_loss_rate', 0), '退货损耗率') or 0
    cvr_node = x.get('ads', 'cvr_scenarios') if isinstance((doc.get('ads') or {}).get('cvr_scenarios'), dict) \
        else (doc.get('ads') or {}).get('cvr_scenarios')
    cvrs = sorted(num(v, 'CVR 情景', True) for v in (cvr_node or [0.08, 0.10, 0.12]))
    if any(v <= 0 or v >= 1 for v in cvrs):
        raise ValueError('CVR 情景需是 0–1 之间的小数，如 0.10')
    ref_cvr = statistics.median(cvrs)
    if ref_cvr not in cvrs:
        cvrs = sorted(cvrs + [ref_cvr])
    rows = keyword_rows((doc.get('ads') or {}).get('keywords'), warnings)
    bands = defaults.get('margin_bands') or [[0.4, '很好'], [0.3, '优秀'], [0.2, '一般'], [0, '偏低'], [-99, '亏损']]
    table = defaults.get('fba_fee_table_us')
    price_bands = market_bands(doc.get('market_price_bands'))
    offers = build_offers(doc, scenarios_in, unit, unit_kg, leg, basis, num(lg('first_leg_rate_cny'), '头程单价'),
                          divisor, warnings)

    scenarios, fba_notes, offer_costs = [], {}, {}
    for offer in offers:
        cost_ok = cost_ready and offer['leg'] is not None
        o_params = {**params, 'mult': offer['mult'], 'extra': offer['extra'], 'extra_one_off': offer['extra_one_off'],
                    'first_leg': offer['leg']}
        o_landed, o_parts, o_duty = landed(o_params) if cost_ok else (None, [], None)
        offer_costs[offer['id']] = {'landed': o_landed, 'parts': o_parts, 'duty': o_duty, 'params': o_params}
        dims_o = offer['dims']
        storage = dims_o[0] * dims_o[1] * dims_o[2] / CUFT_CM3 * storage_rate * storage_months if dims_o else 0.0
        for raw in offer['prices']:
            price = num(raw.get('price'), f'{offer["name"]} 售价', True)
            if price <= 0:
                raise ValueError('售价必须大于 0')
            sid = str(raw.get('id'))
            if fba_given is not None and offer['id'] == 'base':
                fba, fba_note, fba_src = fba_given, '用户填写', fba_status
            elif offer.get('fba') is not None:
                fba, fba_note, fba_src = offer['fba'], '组合方案填写', 'offer_input'
            else:
                fba, fba_note = fba_estimate(price, dims_o, offer['kg'], table, market, doc.get('evaluation_date'))
                fba_src = 'table_estimate' if fba is not None else 'missing'
            fba_notes[sid] = fba_note
            if fba is None:
                warnings.append(f'{sid} FBA 费无法得出（{fba_note}）：费用合计与实际利润指标为空')
            engine_fees = cost_engine().legacy_fees(price, referral_rate, referral_min, fba, storage, placement, return_rate)
            referral = engine_fees['referral']
            fees = engine_fees['total']
            allowed = price - fees - target * price
            contribution = price - o_landed - fees if cost_ok else None
            margin = contribution / price if cost_ok else None
            scenario = {
                'id': sid, 'label': raw.get('label') or '', 'price': price, 'recommended': bool(raw.get('recommended')),
                'offer': {k: offer[k] for k in ('id', 'name', 'type', 'contents', 'pain_points', 'mult', 'extra',
                                                'extra_one_off', 'own_package')},
                'first_leg_cny': offer['leg'],
                'price_position': position(price, price_bands),
                'price_per_piece': price / offer['mult'] if offer['mult'] > 1 else None,
                'fees': {'referral': referral, 'fba': fba, 'fba_status': fba_src, 'storage': storage,
                         'inbound_placement': placement, 'return_loss': price * return_rate, 'total': fees},
                'allowed_landed_cost': allowed, 'landed_cost': o_landed,
                'landed_headroom': allowed - o_landed if cost_ok else None,
                'contribution': contribution, 'gross_margin': margin,
                'margin_band': band(margin, bands) if margin is not None else '待采购价',
                'break_even_acos': margin, 'target_acos': max(margin - target, 0) if margin is not None else None,
                'cpc_by_cvr': [{'cvr': c, 'break_even_cpc': contribution * c if cost_ok else None,
                                'allowable_cpc': price * max(margin - target, 0) * c if cost_ok else None} for c in cvrs],
            }
            scenario['ad_pressure'] = ad_pressure(scenario, rows, ref_cvr) if rows or not cost_ok else \
                {'level': 'no_cpc', 'label': LEVEL_LABEL['no_cpc'], 'keywords': []}
            scenarios.append(scenario)
    base_scenarios = [s for s in scenarios if s['offer']['id'] == 'base']
    # A bundle chosen as the lead offer outranks the single-item default the skeleton pre-marks.
    marked = sorted((s for s in scenarios if s['recommended']), key=lambda s: s['offer']['id'] == 'base')
    ref = marked[0] if marked else base_scenarios[len(base_scenarios) // 2]
    base_ref = ref if ref['offer']['id'] == 'base' else next(
        (s for s in base_scenarios if s['recommended']), base_scenarios[len(base_scenarios) // 2])
    for s in scenarios:
        s['recommended'] = s is ref
        added = (s['landed_cost'] - base_ref['landed_cost']
                 if s['landed_cost'] is not None and base_ref['landed_cost'] is not None else None)
        s['value_multiple'] = ((s['price'] - base_ref['price']) / added
                               if s['offer']['id'] != 'base' and added and added > 0 else None)
    landed_usd, parts, duty = (offer_costs['base'][k] for k in ('landed', 'parts', 'duty'))

    sensitivity = []
    if ref['landed_cost'] is not None:
        fees = ref['fees']['total']
        ref_params = offer_costs[ref['offer']['id']]['params']
        for label, change, key, factor in (('出厂单价', '−20%', 'unit_price', 0.8), ('出厂单价', '+20%', 'unit_price', 1.2),
                                           ('头程', '−30%', 'first_leg', 0.7), ('头程', '+30%', 'first_leg', 1.3),
                                           ('汇率', '−0.3', 'fx', None), ('汇率', '+0.3', 'fx', None)):
            varied = dict(ref_params)
            varied[key] = ref_params[key] * factor if factor else ref_params['fx'] + (0.3 if change.startswith('+') else -0.3)
            cost = landed(varied)[0]
            sensitivity.append({'variable': label, 'change': change,
                                'gross_margin': (ref['price'] - cost - fees) / ref['price']})

    critical = {k: x.status(*k) for k in CRITICAL}
    if not duty_included:
        critical[('logistics', 'duty_rate')] = x.status('logistics', 'duty_rate')
    basis_real = all(s in REAL for s in critical.values())
    inquiry = [FIELDS[k][2] for k in FIELDS if k[0] == 'factory' and FIELDS[k][2] and x.status(*k) not in REAL
               and not (k == ('factory', 'tax_surcharge_rate'))]
    own_rates = [FIELDS[k][0] for k in FIELDS if k[0] in ('logistics', 'amazon')
                 and x.status(*k) == 'default_assumption']
    field_rows = [{'group': g, 'key': k, 'label': FIELDS[(g, k)][0], 'unit': FIELDS[(g, k)][1],
                   'value': x.used[(g, k)][0], 'status': x.used[(g, k)][1], 'source': x.used[(g, k)][2]}
                  for (g, k) in FIELDS if (g, k) in x.used]
    return {
        'schema_version': VERSION, 'generator': GENERATOR, 'product': doc.get('product') or '',
        'marketplace': market, 'currency': currency,
        'cost_basis': 'real' if basis_real and cost_ready else 'conditional',
        'effective_purchase_rate': fx, 'fx_status': x.status(None, 'fx'),
        'target_net_margin': target, 'first_batch_qty': qty, 'reference_cvr': ref_cvr, 'cvr_scenarios': cvrs,
        'cvr_status': (doc.get('ads') or {}).get('cvr_scenarios', {}).get('status', 'unlabeled')
        if isinstance((doc.get('ads') or {}).get('cvr_scenarios'), dict) else 'default_assumption',
        'landed': {'total': landed_usd, 'duty': duty, 'first_leg_note': leg_note, 'unit_price_cny': unit_price,
                   'parts_cny': [{'item': n, 'cny': v} for n, v in parts]},
        'critical_status': {f'{g}.{k}': s for (g, k), s in critical.items()},
        'market_price_bands': price_bands, 'value_base_id': base_ref['id'],
        'offers': [{k: o[k] for k in ('id', 'name', 'type', 'contents', 'pain_points', 'mult', 'extra', 'extra_one_off')}
                   for o in offers],
        'fba_notes': fba_notes, 'scenarios': scenarios, 'keywords': rows, 'sensitivity': sensitivity,
        'inquiry': inquiry, 'replace_with_own_rates': own_rates,
        'simulated_rates': [label for group, key, label in ASK_FIELDS if key in MUST_ASK
                            and x.status(group, key) not in REAL], 'fields': field_rows,
        'warnings': list(dict.fromkeys(warnings)),
    }


def offer_evidence_gaps(raw, today):
    """Bind a bundle quote to every numeric/package value used in arithmetic.

    offers[].evidence uses dated fact cells keyed by the existing raw offer
    fields. Confirmed zero extras are required; omitted extras are not free.
    The base factory and logistics evidence gates still apply separately.
    """
    fields = ('main_qty', 'extra_cny_per_unit', 'extra_one_off_cny',
              'package_cm', 'package_weight_kg', 'fba_fee')
    evidence = raw.get('evidence') or {}
    gaps = []
    for key in fields:
        node = evidence.get(key) if isinstance(evidence, dict) else None
        valid = (isinstance(node, dict) and raw.get(key) is not None
                 and node.get('value') == raw[key] and not isinstance(node.get('value'), bool)
                 and node.get('status') in REAL and bool(str(node.get('source') or '').strip()))
        if valid:
            try:
                observed = datetime.date.fromisoformat(str(node.get('observed_at'))[:10])
                until = datetime.date.fromisoformat(str(node.get('valid_until'))[:10])
                valid = observed <= today <= until
            except (ValueError, TypeError):
                valid = False
        if not valid:
            gaps.append('offer.' + key + ': current quote must match the used value')
    return gaps


def normalize_price_scenarios(doc):
    """Keep user floor inputs, but bind percentile scenarios to their actual source."""
    doc = copy.deepcopy(doc)
    bands = market_bands(doc.get('market_price_bands'))
    output = []
    for raw in doc.get('price_scenarios', []):
        row = copy.deepcopy(raw)
        key = str(row.get('id', '')).lower()
        if key in bands:
            if row.get('price') != bands[key] and any(t in str(row.get('label')) for t in ('下界', '下限')):
                output.append({**row, 'id': 'price_floor', 'label': '价带下限', 'price_source': 'price_scenarios 原价带下限'})
            row.update(price=bands[key], label=key.upper(), price_source=f'market_price_bands.{key}')
        output.append(row)
    doc['price_scenarios'] = output
    return doc


PIECES = re.compile(r'(\d+)\s*(?:件套|件|只装|个装|(?:pcs|pieces|piece|pc)\b)', re.I)


def _pieces(text):
    match = PIECES.search(str(text or ''))
    return int(match.group(1)) if match else None


def reference_piece_mismatch(doc, ref):
    """Why a reference SKU's page price cannot stand in for the target product's purchase price, or ''."""
    target = _pieces(doc.get('product') or doc.get('product_name'))
    if not target or target <= 1:
        return ''
    name = str(ref.get('sku_name') or '')
    quoted = _pieces(name) if re.search(r'套|set|pcs|pieces', name, re.I) else 1
    if quoted == target:
        return ''
    what = '单只' if quoted in (None, 1) else f'{quoted} 件套'
    return (f'参考SKU「{name}」是{what}的页价，目标是 {target} 件套，不能直接当整套采购价；'
            '请填整套报价或按件数自行折算后写入 factory.unit_price_cny')


def conditional_cases(doc, defaults):
    """Separate, fully labelled arithmetic; never fills the actual evidence ledger."""
    prepared = copy.deepcopy(doc)
    assumptions = []
    def get(dotted):
        group, key = dotted.split('.')
        node = prepared.get(group, {}).get(key)
        return node.get('value') if isinstance(node, dict) else node
    ref = prepared.get('supplier_reference') or {}
    mismatch = reference_piece_mismatch(prepared, ref)
    if get('factory.unit_price_cny') is None and ref.get('source') and ref.get('sku_name') and mismatch:
        # 3.0.23: a ¥3 single tray was taken as the price of an 8-piece set and inflated the margin.
        assumptions.append({'field': 'factory.unit_price_cny', 'value': None, 'source': ref['source'],
                            'sku_name': ref['sku_name'], 'label': mismatch})
    elif get('factory.unit_price_cny') is None and ref.get('source') and ref.get('sku_name'):
        price = num(ref.get('unit_price_cny'), '参考SKU页价')
        if price is not None and price > 0:
            for key, val in [('unit_price_cny', price), ('unit_package_cm', ref.get('package_cm')),
                             ('unit_package_weight_kg', ref.get('package_weight_kg'))]:
                if val is not None:
                    prepared['factory'][key] = cell(val, 'page_extracted', ref['source'])
            # A reference package cannot inherit an unrelated confirmed carton.
            for key in ('carton_cm', 'carton_qty', 'carton_gross_kg'):
                prepared['factory'][key] = cell(None, 'missing')
            assumptions.append({'field': 'factory.unit_price_cny', 'value': price,
                                'source': ref['source'], 'label': '页价代采购价，非厂家报价', 'sku_name': ref['sku_name']})
    bounds = defaults.get('conditional_fee_bounds') or {}
    uppers = {**bounds.get('upper', {}), **(doc.get('conditional_fee_bounds') or {}).get('upper', {})}
    unknown = [key for key in uppers if get(key) is None]
    result = {}
    for mode in (['zero', 'upper'] if unknown else ['evidence']):
        trial = copy.deepcopy(prepared)
        used = copy.deepcopy(assumptions)
        for dotted in unknown:
            group, key = dotted.split('.')
            amount = 0 if mode == 'zero' else num(uppers[dotted], dotted, True)
            source = (doc.get('conditional_fee_bounds') or {}).get('source') or bounds.get('source', '')
            trial[group][key] = cell(amount, 'default_assumption', source)
            used.append({'field': dotted, 'value': amount, 'source': source,
                         'label': '未知费用零值情景' if mode == 'zero' else '未知费用假设上界（实际可超出）'})
        # Unpriced custom alternatives cannot silently inherit a free configuration.
        trial['offers'] = [o for o in trial.get('offers', []) if all(o.get(k) is not None for k in
            ('extra_cny_per_unit', 'extra_one_off_cny', 'package_cm', 'package_weight_kg'))]
        raw_model = _compute_scenarios(trial, defaults)
        def trial_value(g,k):
            n = trial.get(g, {}).get(k)
            return n.get('value') if isinstance(n, dict) else n
        fee_complete = trial_value('factory', 'unit_package_cm') is not None and all(trial_value('amazon', k) is not None for k in
            ('referral_rate', 'referral_min', 'storage_per_cuft_month', 'storage_months', 'inbound_placement_fee', 'return_loss_rate'))
        cost_complete = all(trial_value(g,k) is not None for g,k in [
            ('factory','unit_price_cny'),('factory','tax_surcharge_rate'),('factory','packaging_cny_per_unit'),
            ('factory','labeling_cny_per_unit'),('factory','domestic_freight_cny_per_shipment'),('factory','one_off_cny'),
            ('logistics','customs_fee_cny_per_shipment'),('logistics','other_cny_per_shipment')])
        duty_ok = flag(trial_value('logistics','duty_included')) or trial_value('logistics','duty_rate') is not None
        for row in raw_model['scenarios']:
            if not fee_complete or row['fees']['fba'] is None:
                continue
            if not (cost_complete and duty_ok and row['first_leg_cny'] is not None):
                for key in ('contribution','gross_margin','landed_cost','landed_headroom','break_even_acos','target_acos'):
                    row[key] = None
                row['margin_band'] = '采购成本待补'
                row['cpc_by_cvr'] = []
                row['ad_pressure'] = {'level': 'pending', 'label': '采购成本待补', 'keywords': []}
            row.update(case_id=mode, case_label={'zero':'未知费用按零','upper':'未知费用按假设上界','evidence':'已有证据条件测算'}[mode],
                       assumptions=used, evidence_gaps=[], supplier_reference=ref, status='conditional')
            result.setdefault(row['id'], []).append(row)
    return result


def compute(doc, defaults):
    """Legacy scenario arithmetic, with an evidence gate before exposing actuals.

    Undated confirmed legacy cells remain usable, explicitly marked unspecified.
    Pass evaluation_date (ISO date) to reproduce the freshness assessment later.
    Actual totals stay gated; explicitly labelled assumptions live only in separate scenarios.
    """
    if str(doc.get('schema_version')) == '2.0' or doc.get('calculation_version') == 'r01.cost.2':
        return compute_current(doc)
    original_doc = doc
    doc = normalize_price_scenarios(doc)
    evidence = {}
    today = datetime.date.fromisoformat(str(doc.get('evaluation_date') or datetime.date.today()))
    def value(group, key):
        node = (doc.get(group) or {}).get(key) if group else doc.get(key)
        return node.get('value') if isinstance(node, dict) else node
    for group in ('factory', 'logistics', 'amazon', None):
        keys = [k for g, k in FIELDS if g == group] if group else ['fx', 'first_batch_qty', 'target_pre_ad_margin', 'target_net_margin']
        for key in keys:
            raw = (doc.get(group) or {}).get(key) if group else doc.get(key)
            node = copy.deepcopy(raw) if isinstance(raw, dict) else {'value': raw, 'status': 'unlabeled', 'source': ''}
            node['freshness'] = 'unspecified'
            for date_key in ('observed_at', 'valid_until'):
                if node.get(date_key):
                    try:
                        date = datetime.date.fromisoformat(str(node[date_key])[:10])
                    except ValueError:
                        raise ValueError(f'{key}.{date_key} 需要 ISO 日期') from None
                    if date_key == 'valid_until':
                        node['freshness'] = 'expired' if date < today else 'current'
                    elif date > today:
                        raise ValueError(f'{key}.observed_at 不能在未来')
            evidence[f'{group}.{key}' if group else key] = node
    for group, key in [(None, 'target_pre_ad_margin'), (None, 'target_net_margin'), ('amazon', 'referral_rate'),
                       ('amazon', 'return_loss_rate'), ('factory', 'tax_surcharge_rate'), ('logistics', 'duty_rate')]:
        n = num(value(group, key), key)
        if n is not None and n >= 1:
            raise ValueError(f'{key} 必须是 0–1 之间的小数，且小于 1')
    for group, key in [(None, 'first_batch_qty'), ('factory', 'carton_qty')]:
        n = num(value(group, key), key)
        if n is not None and (n <= 0 or not n.is_integer()):
            raise ValueError(f'{key} 必须是正整数')
    for key in ('unit_package_weight_kg', 'carton_gross_kg'):
        n = num(value('factory', key), key)
        if n is not None and n <= 0: raise ValueError(f'{key} 必须大于 0')
    d = value('logistics', 'duty_included')
    if d is not None and not isinstance(d, bool) and str(d).strip().lower() not in {'true', 'false', 'yes', 'no', 'y', 'n', '是', '否', '1', '0'}:
        raise ValueError('duty_included 必须是 是/否')
    ids = [s.get('id') for s in doc.get('price_scenarios', []) if isinstance(s, dict)]
    if len(ids) != len(set(ids)) or any(not i for i in ids):
        raise ValueError('售价方案 id 缺失或重复')
    model = _compute_scenarios(doc, defaults)
    target = num(value(None, 'target_pre_ad_margin'), 'target_pre_ad_margin')
    required = ['fx', 'first_batch_qty'] + [f'{g}.{k}' for g, k in FIELDS
        if k not in {'carton_cm', 'carton_qty', 'carton_gross_kg', 'customs_mode', 'duty_rate'}]
    if value('factory', 'carton_cm') is not None and value('factory', 'carton_qty') is not None:
        required += ['factory.carton_cm', 'factory.carton_qty']
        if value('logistics', 'first_leg_basis') != 'cbm': required.append('factory.carton_gross_kg')
    if not flag(d): required.append('logistics.duty_rate')
    gaps = [k for k in required if evidence[k].get('value') is None or evidence[k].get('status') not in REAL
            or not evidence[k].get('source') or evidence[k]['freshness'] == 'expired']
    missing = [k for k in required if evidence[k].get('value') is None]
    profit_keys = ('contribution', 'gross_margin', 'break_even_acos', 'target_acos', 'cpc_by_cvr', 'ad_pressure')
    for s in model['scenarios']:
        sgaps = list(gaps)
        smissing = list(missing)
        if s['offer']['id'] != 'base':
            raw_offer = next((o for o in doc.get('offers', []) if isinstance(o, dict)
                              and o.get('id') == s['offer']['id']), {})
            sgaps.extend(offer_evidence_gaps(raw_offer, today))
        if s['fees']['fba_status'] == 'table_estimate':
            sgaps.append('amazon.fba_fee: table_estimate')
            smissing = [k for k in smissing if k != 'amazon.fba_fee']
        complete_numbers = not smissing and s['landed_cost'] is not None and s['fees']['fba'] is not None
        s['conditional_estimate'] = ({k: copy.deepcopy(s[k]) for k in profit_keys} if complete_numbers and sgaps else None)
        s['evidence_gaps'] = sgaps
        s['status'] = 'incomplete' if not complete_numbers else ('conditional' if sgaps else 'complete')
        if sgaps:
            for key in profit_keys[:4]: s[key] = None
            s['cpc_by_cvr'] = [{'cvr': c, 'break_even_cpc': None, 'allowable_cpc': None} for c in model['cvr_scenarios']]
            s['ad_pressure'] = {'level': 'pending', 'label': '成本证据未齐：不作实际广告利润判断', 'keywords': []}
            s['margin_band'] = '条件测算' if complete_numbers else '费用不完整'
        fee_missing = any(k.startswith('amazon.') for k in smissing) or 'factory.unit_package_cm' in smissing
        if fee_missing:
            s['fees']['total'] = None
            s['allowed_landed_cost'] = s['landed_headroom'] = None
        for key, field in [('inbound_placement', 'inbound_placement_fee'), ('storage', 'storage_per_cuft_month')]:
            if f'amazon.{field}' in smissing: s['fees'][key] = None
        if 'amazon.storage_months' in smissing or 'factory.unit_package_cm' in smissing: s['fees']['storage'] = None
        if 'amazon.referral_rate' in smissing or 'amazon.referral_min' in smissing: s['fees']['referral'] = None
        if 'amazon.return_loss_rate' in smissing: s['fees']['return_loss'] = None
        boundaries = {'target_pre_ad_margin': target, 'required_price': None,
                      'max_total_cost_at_price': None, 'unknown_cost_room': None, 'market_price_limits': {},
                      'status': s['status'], 'room_semantics': 'remaining_budget_for_all_unknown_costs'}
        if target is not None:
            boundaries['max_total_cost_at_price'] = s['price'] * (1-target)
            if s['landed_cost'] is not None and not any(k.startswith('factory.') or k.startswith('logistics.') for k in smissing):
                known_fees = sum(s['fees'][k] or 0 for k in ('referral', 'fba', 'storage', 'inbound_placement', 'return_loss'))
                boundaries['unknown_cost_room'] = s['price']*(1-target)-known_fees-s['landed_cost']
            for label, price in model['market_price_bands'].items():
                limit = {'price': price, 'max_total_cost': price*(1-target), 'max_landed_cost': None,
                         'unknown_cost_room': None}
                if not fee_missing and s['fees']['fba'] is not None:
                    market_fee = max(price*float(value('amazon', 'referral_rate')), float(value('amazon', 'referral_min')))
                    market_fee += s['fees']['fba'] + s['fees']['storage'] + s['fees']['inbound_placement'] + price*float(value('amazon', 'return_loss_rate'))
                    limit['max_landed_cost'] = price*(1-target)-market_fee
                    if s['landed_cost'] is not None: limit['unknown_cost_room'] = limit['max_landed_cost']-s['landed_cost']
                boundaries['market_price_limits'][label] = limit
            if complete_numbers:
                fixed = s['landed_cost'] + s['fees']['fba'] + s['fees']['storage'] + s['fees']['inbound_placement']
                referral = float(value('amazon', 'referral_rate'))
                minimum = float(value('amazon', 'referral_min'))
                returns = float(value('amazon', 'return_loss_rate'))
                denominator = 1-target-returns-referral
                if denominator > 0:
                    boundaries['required_price'] = max(fixed/denominator, (fixed+minimum)/(1-target-returns))
                boundaries['unknown_cost_room'] = s['price']*(1-target)-s['fees']['total']-s['landed_cost']
        s['pricing_boundaries'] = boundaries
        target_evidence = evidence['target_pre_ad_margin']
        target_confirmed = target_evidence.get('status') in REAL and target_evidence.get('source') and target_evidence['freshness'] != 'expired'
        s['meets_pre_ad_target'] = (s['gross_margin'] >= target if s['status'] == 'complete' and target is not None and target_confirmed else None)
        if any(k.startswith('factory.') or k.startswith('logistics.') for k in smissing):
            s['landed_cost'] = s['landed_headroom'] = None
    model.update(target_pre_ad_margin=target, evidence=evidence, evaluation_date=today.isoformat(),
                 input_sha256=hashlib.sha256(json.dumps(doc, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest())
    model['cost_basis'] = 'real' if all(s['status'] == 'complete' for s in model['scenarios']) else 'conditional'
    if model['cost_basis'] != 'real': model['sensitivity'] = []
    model['legacy_undated_evidence'] = [k for k in required if evidence[k].get('status') in REAL
                                      and evidence[k]['freshness'] == 'unspecified']
    if any(k.startswith('factory.') or k.startswith('logistics.') for k in missing):
        model['landed']['total'] = None
    part_inputs = {'包装': 'factory.packaging_cny_per_unit', '贴标': 'factory.labeling_cny_per_unit',
                   '工厂→货代': 'factory.domestic_freight_cny_per_shipment', '报关': 'logistics.customs_fee_cny_per_shipment',
                   '其他每票杂费': 'logistics.other_cny_per_shipment', '打样开模检测摊销': 'factory.one_off_cny'}
    for part in model['landed']['parts_cny']:
        if part_inputs.get(part['item']) in missing: part['cny'] = None
    if 'logistics.duty_rate' in missing: model['landed']['duty'] = None
    model['warnings'] = [w.replace('按 0 计', '未确认，不计入实际利润结论') for w in model['warnings']]
    cases = conditional_cases(doc, defaults)
    for s in model['scenarios']:
        s['price_source'] = next((p.get('price_source') for p in doc['price_scenarios'] if p['id'] == s['id']), None)
        s['conditional_cases'] = cases.get(s['id'], []) if s['status'] != 'complete' else []
        s['conditional_estimate'] = None
        if s['conditional_cases']:
            for case in s['conditional_cases']:
                case['evidence_gaps'] = s['evidence_gaps']
            s['conditional_estimate'] = copy.deepcopy(s['conditional_cases'][0])
    model['supplier_reference'] = doc.get('supplier_reference')
    model['competitor_fba_reference'] = doc.get('competitor_fba_reference')
    model['fba_table_evidence'] = {k: (defaults.get('fba_fee_table_us') or {}).get(k) for k in
                                 ('as_of', 'source_url', 'verified_on', 'valid_from', 'valid_until')}
    model['input_sha256'] = hashlib.sha256(json.dumps(original_doc, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    return model


def compute_current(doc, *, root=None, price_scenarios=None):
    engine=cost_engine()
    payload=engine.from_cost_inputs(doc)
    if root is not None:payload=engine.enrich_from_case(root,payload)
    unified=engine.calculate(payload,price_scenarios=price_scenarios)
    r=unified['result']; p=unified['inputs']
    # R06.1 AS-01 第 3 批：达到目标毛利的采购价与包装边界（原 05A/05B 由助手现写）
    boundaries=delivery_module('s1_target_boundaries').boundaries(engine,payload,doc.get('target_boundaries'))
    scenarios=[]
    for index,row in enumerate(unified['blocks']['price_ladder']['scenarios']):
        scenarios.append({'id':f'm2_{index}', 'label':row['label'],'price':row['price'],
            'recommended':row['label']=='当前售价','gross_margin':row['pre_ad_margin'],
            'contribution':row['pre_ad_profit'],'landed_cost':r['landed_cost'],
            'status':'incomplete' if unified['missing'] else 'conditional',
            'meets_pre_ad_target':None,'margin_band':'当前输入测算，业务验收待复核',
            'break_even_acos':row['pre_ad_margin'],
            'ad_pressure':{'label':unified['promotion']['conclusion']},
            'pricing_boundaries':{'required_price':r['target_price_pre_ad'],
                                  'target_pre_ad_margin':p['target']/100}})
    return {'schema_version':'2.0','calculation_version':engine.VERSION,'generator':GENERATOR,
            'product':doc.get('product',''),'marketplace':unified['marketplace'],
            'currency':unified['currency'],'evaluation_date':unified['evaluation_date'],
            'cost_basis':'conditional','target_pre_ad_margin':p['target']/100,
            'first_batch_qty':p['qty'],'effective_purchase_rate':p['fx'],
            'landed':{'total':r['landed_cost'],'duty':r.get('landed_parts',{}).get('duty'),
                      'unit_price_cny':p.get('buy'),'first_leg_note':r['first_leg_note'],
                      'parts_cny':[]}, 'scenarios':scenarios,'unified_cost':unified,
            'input_sha256':engine.canonical_hash(doc),'engine_provenance':unified['provenance'],
            'warnings':unified['warnings'],'inquiry':unified['missing'],
            'simulated_rates':[key for key,node in unified['input_state'].items()
                               if node['status']=='default_assumption' and node['value'] not in (0,None,False)],
            'market_price_bands':{k:p.get(k) for k in ('p25','p50','p75')},
            'product_configuration_sha256':doc.get('product_configuration_sha256'),
            'target_boundaries':boundaries}


def render_current(model):
    u=model['unified_cost'];r=u['result'];currency=u['currency']
    lines=['# 综合成本与定价','',f"生成器：`{GENERATOR}` · {u['schema_version']} · {u['evaluation_date']}",
           '',f"当前输入测算：落地 {money(r['landed_cost'],currency)}；广告前含预留毛利 {r['pre_ad_margin']*100:.2f}%" if r['pre_ad_margin'] is not None else '实际利润待补数据；未知费用不按零验收。',
           '',f"目标广告前毛利 {u['inputs']['target']:g}%；目标价 {money(r['target_price_pre_ad'],currency)}；竞争力 {r['competitiveness']['level']}（{r['competitiveness']['reason']}）。",'']
    if (u.get('main_basis') or {}).get('kind')=='user_condition':
        # R06.1 PL-07：用户选定的条件情景是主口径
        lines.extend([delivery_module('s1_cost_present').condition_table(u,heading='## 主口径：你选定的条件情景'),''])
    for key,block in u['blocks'].items():
        lines.extend(['## '+block['title'],''])
        if key=='price_ladder':
            lines.extend(['| 售价情景 | 售价 | 广告前毛利 | 广告后毛利 |','|---|---:|---:|---:|'])
            for row in block['scenarios']:
                lines.append(f"| {row['label']} | {money(row['price'],currency)} | {pct(row['pre_ad_margin'])} | {pct(row['post_ad_margin'])} |")
        elif key=='test_plans':
            for row in block['plans']:
                lines.append(f"- {row['label']}：标价 {money(row['list_price'],currency)}，券 {row['coupon_percent']:g}%，成交价 {money(row['realized_price'],currency)}。")
        else:
            lines.extend(['| 项目 | 数值 | 来源 / 状态 |','|---|---:|---|'])
            for row in block['rows']:
                lines.append(f"| {row['label']} | {money(row['value'],currency)} | {row['source']} / {row['status']} |")
        lines.append('')
    if model.get('target_boundaries'):
        lines.extend([delivery_module('s1_target_boundaries').render(model['target_boundaries']),''])
    default_rates=model['simulated_rates']
    if default_rates:
        lines.extend(['## ⚠️ 模拟费率','', '仍采用默认情景：'+'、'.join(cost_engine().FIELD_LABELS.get(k,k) for k in default_rates),''])
    if u.get('conditional_tax_scenarios'):
        lines.extend(['## 税点未知的两档条件测算','','以下仅为条件情景，3%不是厂家报价上界；实际税点仍待确认。','',
                      '| 税点情景 | 落地成本 | 广告前含预留毛利 |','|---|---:|---:|'])
        for row in u['conditional_tax_scenarios']:
            lines.append(f"| {row['label']} | {money(row['result']['landed_cost'],currency)} | {pct(row['result']['pre_ad_margin'])} |")
        lines.append('')
    lines.extend(['## 输入来源','', '| 字段 | 数值 | 状态 | 来源 |','|---|---|---|---|'])
    for key,node in u['input_state'].items():
        if key not in u['inputs']: continue
        source=str(node.get('source','')).replace('|','\\|').replace('\n',' ')
        source=source.replace('caller input without attestation','调用输入，未经用户确认').replace('R01 prototype scenario default','默认测算情景，尚未确认').replace('cost_defaults.yaml','默认费率表')
        lines.append(f"| {cost_engine().FIELD_LABELS.get(key,key)} | {node['value']} | {cost_engine().STATUS_LABELS.get(node.get('status'),'待确认')} | {source} |")
    lines.extend(['','## 风险与来源边界',''])
    lines.extend('- '+w for w in u['warnings'])
    lines.extend(['','US自动FBA为已收录官方非服装标准尺寸表估算，保留未取整燃油附加费；可按卖家后台实测覆盖。',
                  '引擎 SHA-256：`'+u['provenance']['engine_sha256']+'`；费率表 SHA-256：`'+u['fee_provenance']['sha256']+'`。',
                  '测算不等于采购或业务放行；改输入后原审批需重新验收。',''])
    return '\n'.join(lines)+'\n'+delivery_module('s1_cost_present').render_assumptions(u)


def money(value, currency):
    if value is None:
        return '—'
    return (f'${value:,.2f}' if currency == 'USD' else f'{currency} {value:,.2f}').replace('$-', '-$')


def pct(value):
    return '—' if value is None else f'{value * 100:.1f}%'


def render_md(m):
    if m.get('unified_cost'):
        return render_current(m)
    m = copy.deepcopy(m)
    for s in m['scenarios']:
        if s.get('conditional_estimate'):
            estimate = s['conditional_estimate']
            for key in ('fees', 'allowed_landed_cost', 'landed_cost', 'landed_headroom', 'contribution',
                        'gross_margin', 'margin_band', 'cpc_by_cvr', 'ad_pressure'):
                if key in estimate:
                    s[key] = copy.deepcopy(estimate[key])
            s['label'] += '（条件）'
    cur = m['currency']
    real = m['cost_basis'] == 'real'
    lines = [f'# 成本账与广告可推性{" · " + m["product"] if m["product"] else ""}', '',
             f'> 生成：`{GENERATOR}` v{VERSION}（确定性脚本）｜站点 {m["marketplace"]}｜'
             f'汇率快照 effective_purchase_rate = {m["effective_purchase_rate"]:g}（{STATUS_LABEL.get(m["fx_status"], m["fx_status"])}）',
             '> 口径：' + ('**成本账**——关键成本（出厂价、尺寸重量、头程）均为厂家确认/用户认可，下表毛利可作经营参考。' if real else
                        '**条件性测算**——关键成本含页面价/演示/默认假设/缺失，毛利只作方向参考、不作利润结论；'
                        '「允许到岸成本」是给厂家砍价用的采购目标。'),
             '> 本页只给指标和档位，**做不做由你拍板**；市场前景见市场穿透报告（容量、增长、季节、竞争）。']
    if m['simulated_rates']:
        lines.append(f'> ⚠️ **模拟费率**：{"、".join(m["simulated_rates"])}还是默认值，不是你的真实费率。'
                     '在对话里回「物流费率：头程 X 元/kg，包税（或不包税、关税 X%），报关 X 元/票」即可存成你的配置并重算。')
    lines.append('')
    if m.get('legacy_undated_evidence'):
        lines += ['> 兼容历史确认记录：部分证据未标有效期，当前时效未重新核验；成本确认不代表报价仍有效。', '']
    lines += ['> 实际利润指标仅在成本证据齐全时显示；条件情景与实际结论分开。缺失费用不按零成本认定。',
              f'> 广告前目标：{pct(m.get("target_pre_ad_margin"))}；广告后目标：{pct(m["target_net_margin"])}（'
              + ('用户设定' if m['evidence']['target_net_margin'].get('status') in REAL else '默认情景（非你设定）') + '）。',
              f'> 分摊数量：{m["first_batch_qty"]:g} 件（' + ('用户设定' if m['evidence']['first_batch_qty'].get('status') in REAL
                  else '默认情景（非你设定）') + '）。', '']
    if any(s.get('conditional_estimate') for s in m['scenarios']):
        lines += ['## 条件情景（仅供讨论，不是实际利润）', '', '| 档位 | 小费用假设 | 广告前毛利率 | 尺子灯 | 允许到岸成本 |', '|---|---|---:|---|---:|']
        for s in m['scenarios']:
            for c in s.get('conditional_cases', []):
                lines.append(f'| {s["id"]} | {c["case_label"]} | {pct(c["gross_margin"])} | {c["margin_band"]} | {money(c["allowed_landed_cost"], cur)} |')
        used = {json.dumps(a, ensure_ascii=False, sort_keys=True): a for s in m['scenarios'] for c in s.get('conditional_cases', []) for a in c['assumptions']}
        lines += ['', '假设清单（金额按字段单位；假设上界不是实际收费上限）：']
        for a in used.values():
            group, key = a['field'].split('.')
            label, unit, _ = FIELDS[(group,key)]
            lines.append(f'- {label}：{a["value"]} {unit}；{a["label"]}；来源：{a["source"]}')
        lines.append('下表条件指标取“未知费用按零”基准；上方假设上界用于观察预算压力，不构成实际利润结论。')
        lines.append('')
    if m.get('target_pre_ad_margin') is not None:
        lines += ['## 个人目标的价格与成本边界', '', '| 档位 | 所需售价（条件值） | 总成本上限 | 未知费用剩余额度 |', '|---|---:|---:|---:|']
        for s in m['scenarios']:
            b = s['pricing_boundaries']
            lines.append(f'| {s["id"]} | {money(b["required_price"], cur)} | {money(b["max_total_cost_at_price"], cur)} | {money(b["unknown_cost_room"], cur)} |')
        lines += ['', '> 剩余额度是全部未知费用可共用的预算，负数表示已知成本已超目标；不能当作实际利润。', '']
    lines += ['## 一、尺子：各方案 × 售价一眼看', '',
              f'| 方案 · 售价档 | 售价 | 价格带位置 | 广告前毛利率 | 毛利率档位 | 允许到岸成本 | 到岸成本（按证据口径） | 到岸余量 | 保本 CPC（CVR {pct(m["reference_cvr"])}） | 核心词 CPC 中位 | 广告压力 |',
              '|---|---:|---|---:|---|---:|---:|---:|---:|---:|---|']
    for s in m['scenarios']:
        be = next((c['break_even_cpc'] for c in s['cpc_by_cvr'] if c['cvr'] == m['reference_cvr']), None)
        ap = s['ad_pressure']
        name = s['id'] + ' ' + s['label'] if s['offer']['id'] == 'base' else s['offer']['name'] + f' · {money(s["price"], cur)}'
        per = f'（单件 {money(s["price_per_piece"], cur)}）' if s['price_per_piece'] else ''
        shares = f'（按搜索量：红 {ap["red_share"]:.0%}·绿 {ap["green_share"]:.0%}）' if 'red_share' in ap else ''
        lines.append(f'| {name}{" ★" if s["recommended"] else ""} | {money(s["price"], cur)}{per} | {s["price_position"]} | '
                     f'{pct(s["gross_margin"])} | {s["margin_band"]} | {money(s["allowed_landed_cost"], cur)} | '
                     f'{money(s["landed_cost"], cur)} | {money(s["landed_headroom"], cur)} | {money(be, cur)} | '
                     f'{money(ap.get("core_median_cpc"), cur)} | {ap["label"]}{shares} |')
    lines += ['', '- ★ = 主推档（关键词逐个对照和敏感性按此档）。',
              '- 毛利率 = (售价 − 到岸成本 − 亚马逊费用) ÷ 售价，未扣广告。参考档位：≥40% 很好，30–40% 优秀，20–30% 一般，<20% 偏低。档位和目标毛利都是参考：定高一点前期压力小、成功把握大，实际能接受多少由用户决定。',
              '- 允许到岸成本 = 售价 − 亚马逊费用 − 目标净利（' + pct(m['target_net_margin']) + '）；到岸余量为正说明采购价还有空间。',
              '- 毛利率高而广告压力偏高的品，前期仍可小预算测试；毛利率低且广告压力高的品，要有自然流量或提价的办法才好推。']
    if m['market_price_bands']:
        b = m['market_price_bands']
        lines.append('- 价格带：' + '，'.join(f'{k.upper()} {money(v, cur)}' for k, v in b.items()) + '（来自市场穿透报告价格分位）。')
    else:
        lines.append('- 价格带位置未算：把市场穿透报告 M09 的 P25/P50/P75/P90 填进 `market_price_bands`。')
    lines.append('')
    combos = [s for s in m['scenarios'] if s['offer']['id'] != 'base']
    if combos:
        lines += ['## 一B、组合与定价方案', '',
                  '| 方案 | 类型 | 内容 | 解决的痛点 / 需求 | 比单品多花（到岸/套） | 售价 | 价格带 | 毛利率 | 组合增值倍数 |',
                  '|---|---|---|---|---:|---:|---|---:|---:|']
        base = [s for s in m['scenarios'] if s['id'] == m['value_base_id']]
        for s in base + combos:
            o = s['offer']
            base_landed = m['landed']['total']
            extra = s['landed_cost'] - base_landed if s['landed_cost'] is not None and base_landed is not None else None
            vm = f'{s["value_multiple"]:.1f} 倍' if s['value_multiple'] is not None else ('基准' if o['id'] == 'base' else '—')
            lines.append(f'| {o["name"]}{" ★" if s["recommended"] else ""} | {OFFER_TYPE.get(o["type"], o["type"])} | {o["contents"] or "—"} | '
                         f'{"、".join(o["pain_points"]) or "—"} | {money(extra, cur)} | {money(s["price"], cur)} | {s["price_position"]} | '
                         f'{pct(s["gross_margin"])} | {vm} |')
        lines += ['', '- 组合增值倍数 = (组合售价 − 单品主推售价) ÷ (组合到岸成本 − 单品到岸成本)：每多花 1 块成本多卖回几块；'
                  '≥3 倍说明组合加价有空间，<1.5 倍说明加的东西用户不一定买账或成本太高。',
                  '- 组合是否解决真实痛点以市场穿透报告 VOC 为准；定价同时看价格带位置——性价比区好走量，主流以上要靠组合/包装撑住价值感。', '']
    lines += ['## 二、单件到岸成本拆解', '']
    if m['landed']['total'] is None:
        lines += ['到岸成本所需字段不完整，合计为空；已知费用不能当作完整到岸成本。「允许到岸成本」仅在平台费用可计算时提供。', '']
    else:
        fx = m['effective_purchase_rate']
        lines += ['| 项目 | ¥/件 | 折合/件 |', '|---|---:|---:|']
        for part in m['landed']['parts_cny']:
            lines.append(f'| {part["item"]} | ¥{part["cny"]:.2f} | {money(part["cny"] / fx, cur)} |')
        lines += [f'| 关税 | — | {money(m["landed"]["duty"], cur)} |',
                  f'| **到岸成本合计** | — | **{money(m["landed"]["total"], cur)}** |', '',
                  f'- 头程：{m["landed"]["first_leg_note"]}；报关、国内运费、打样开模检测按首批 {m["first_batch_qty"]:g} 件分摊。', '']
    ref_fba = m.get('competitor_fba_reference') or {}
    lines += ['## 三、亚马逊费用', '', '| 售价档 | 佣金 | FBA 配送 | 同尺寸级别竞品 FBA 中位数 | 仓储 | 入库配置 | 退货损耗 | 合计 |', '|---|---:|---:|---:|---:|---:|---:|---:|']
    for s in m['scenarios']:
        fe = s['fees']
        lines.append(f'| {s["id"]} | {money(fe["referral"], cur)} | {money(fe["fba"], cur)} | {money(ref_fba.get("median"), cur)} | {money(fe["storage"], cur)} | '
                     f'{money(fe["inbound_placement"], cur)} | {money(fe["return_loss"], cur)} | {money(fe["total"], cur)} |')
    lines += [''] + [f'- {sid} 档 FBA：{note}' for sid, note in m['fba_notes'].items()]
    ref_fba = m.get('competitor_fba_reference') or {}
    if ref_fba:
        lines.append(f'- 同尺寸级别竞品 FBA 中位数：{money(ref_fba.get("median"), cur)}（{ref_fba.get("count", 0)} 个独立父体；仅作对照）。')
    if any(s['fees']['fba_status'] == 'table_estimate' for s in m['scenarios']):
        table = m.get('fba_table_evidence') or {}
        lines.append(f'- FBA 按[2026年亚马逊官方费用表]({table.get("source_url", "")})估算（{table.get("verified_on", "")}核验）；仅非服装、非危险品标准件，不含SIPP优惠及低库存等额外费用，最终用卖家后台收入计算器复核。')
    lines += ['', '## 四、广告可推性卡', '',
              '- 保本 CPC = 广告前单件毛利 × CVR（超过就是每单广告亏钱）；目标 CPC = 售价 × (毛利率 − 目标净利率) × CVR。',
              f'- CVR 情景 {", ".join(pct(c) for c in m["cvr_scenarios"])}（{STATUS_LABEL.get(m["cvr_status"], m["cvr_status"])}），'
              f'有 SIF/西柚本品类真实转化率时替换。', '',
              '| 售价档 | CVR | 保本 CPC（最大可承受 CPC） | 目标 CPC |', '|---|---:|---:|---:|']
    empty_cpc = 0
    for s in m['scenarios']:
        for c in s['cpc_by_cvr']:
            if c['break_even_cpc'] is None:
                empty_cpc += 1
                continue
            lines.append(f'| {s["id"]} | {pct(c["cvr"])} | {money(c["break_even_cpc"], cur)} | {money(c["allowable_cpc"], cur)} |')
    if empty_cpc:
        lines.append(f'以下 {empty_cpc} 个广告测算情景待成本回填，暂不展开空值。')
    ref = next(s for s in m['scenarios'] if s['recommended'])
    judged = ref['ad_pressure'].get('keywords') or []
    lines += ['', f'关键词逐个对照（{ref["id"]} 档，CVR {pct(m["reference_cvr"])}；绿=目标内，黄=保本内，红=超保本）：', '']
    if judged:
        lines += ['| 关键词 | 月搜索量 | CPC | 来源 | 核心词 | 灯 | CPC ÷ 保本 CPC |', '|---|---:|---:|---|---|---|---:|']
        for r in judged:
            vol = f'{r["search_volume"]:,.0f}' if r['search_volume'] is not None else '—'
            lines.append(f'| {r["keyword"]} | {vol} | {money(r["cpc"], cur)} | {r["source"]} | {"是" if r["core"] else ""} | '
                         f'{r["light"]} | {r["ratio_to_break_even"]:.2f} |')
    else:
        lines.append('（' + ref['ad_pressure']['label'] + '：把 SIF/西柚/Sorftime 的核心词 CPC 填进 `ads.keywords` 后重跑）')
    if m['sensitivity']:
        lines += ['', f'## 五、敏感性（{ref["id"]} 档 {money(ref["price"], cur)}，基准毛利率 {pct(ref["gross_margin"])}）', '',
                  '| 变量 | 变化 | 毛利率 |', '|---|---|---:|']
        lines += [f'| {r["variable"]} | {r["change"]} | {pct(r["gross_margin"])} |' for r in m['sensitivity']]
    else:
        lines += ['', '## 五、敏感性', '', '实际成本证据未齐，先看上方未知小费用的两档条件情景。']
    lines += ['', '## 六、还缺什么', '']
    if m['inquiry']:
        lines += ['**问厂家（可直接复制）：**', ''] + [f'{i}. {q}' for i, q in enumerate(m['inquiry'], 1)] + ['']
    if m['replace_with_own_rates']:
        lines += ['**仍按默认值算的费率**：' + '、'.join(m['replace_with_own_rates'])
                  + '。在对话里说「改物流费率」并报数字，会存进你的物流配置，以后所有产品通用。', '']
    if not m['inquiry'] and not m['replace_with_own_rates'] and m['cost_basis'] == 'real':
        lines += ['关键字段已齐。', '']
    if m['warnings']:
        lines += ['## 七、提示', ''] + [f'- {w}' for w in m['warnings']] + ['']
    lines += ['## 八、M12 摘要（可直接放进 S1 报告）', '']
    for s in m['scenarios']:
        be = next((c['break_even_cpc'] for c in s['cpc_by_cvr'] if c['cvr'] == m['reference_cvr']), None)
        lines.append(f'> {s["id"] if s["offer"]["id"] == "base" else s["offer"]["name"]} {money(s["price"], cur)}：允许到岸成本 {money(s["allowed_landed_cost"], cur)}，'
                     f'到岸成本（{"已确认" if s.get("status") == "complete" else "条件测算"}）{money(s["landed_cost"], cur)}；广告前毛利率 {pct(s["gross_margin"])}（{s["margin_band"]}）；'
                     f'保本 CPC {money(be, cur)}；广告压力 {s["ad_pressure"]["label"]}。')
    lines += [f'> 口径：{"成本账" if real else "条件性测算，非利润结论"}；汇率快照 effective_purchase_rate={m["effective_purchase_rate"]:g}。', '']
    return '\n'.join(lines)


def write_xlsx(m, doc, path):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    if m.get('unified_cost'):
        # An export of the engine's computed values, not a second workbook formula engine.
        ws=wb.active;ws.title='统一成本引擎输出'
        ws.append(['模块','项目','数值','单位','来源','状态'])
        for block in m['unified_cost']['blocks'].values():
            for row in block['rows']:
                ws.append([block['title'],row['label'],row['value'],row['unit'],row['source'],row['status']])
        ws=wb.create_sheet('价格阶梯');ws.append(['情景','售价','广告前毛利','广告后毛利'])
        for row in m['unified_cost']['blocks']['price_ladder']['scenarios']:
            ws.append([row['label'],row['price'],row['pre_ad_margin'],row['post_ad_margin']])
        ws=wb.create_sheet('输入来源');ws.append(['字段','值','来源','状态'])
        for key,row in m['unified_cost']['input_state'].items():
            ws.append([key,row['value'],str(row.get('source','')),row.get('status','')])
        wb.save(path);return
    ws = wb.active
    ws.title = '输入'
    ws.append(['分组', '项目', '值', '单位', '状态', '来源'])
    ref = {}

    def put(key, group, label, value, unit, status, source):
        ws.append([group, label, value, unit, STATUS_LABEL.get(status, status), source])
        ref[key] = f"'输入'!$C${ws.max_row}"

    put('fx', '全局', '汇率（1 站点币 = ¥）', m['effective_purchase_rate'], '', m['fx_status'], '')
    put('target', '全局', '目标净利率', m['target_net_margin'], '比例', 'default_assumption', '')
    put('qty', '全局', '首批数量', m['first_batch_qty'], '件', '', '')
    put('cvr', '全局', '参考 CVR', m['reference_cvr'], '比例', m['cvr_status'], '')
    for row in m['fields']:
        value = row['value']
        key = f'{row["group"]}.{row["key"]}'
        if row['key'].endswith('_cm'):
            parts = dims(value, row['label']) if value else [None] * 3
            for axis, v in zip(('长', '宽', '高'), parts):
                put(f'{key}.{axis}', row['group'], f'{row["label"]}·{axis}', v, 'cm', row['status'], row['source'])
            continue
        if row['key'] == 'duty_included':
            value = flag(value)
        if row['key'] == 'fba_fee':
            continue
        put(key, row['group'], row['label'], value, row['unit'], row['status'], row['source'])
    for key in ('factory.unit_package_cm', 'factory.carton_cm'):
        if f'{key}.长' not in ref:
            for axis in ('长', '宽', '高'):
                put(f'{key}.{axis}', 'factory', f'{FIELDS[tuple(key.split("."))][0]}·{axis}', None, 'cm', 'missing', '')
    for key in [f'{g}.{k}' for (g, k) in FIELDS if k not in ('fba_fee', 'unit_package_cm', 'carton_cm')]:
        if key not in ref:
            put(key, key.split('.')[0], FIELDS[tuple(key.split('.'))][0], None, FIELDS[tuple(key.split('.'))][1], 'missing', '')
    for col, width in zip('ABCDEF', (10, 24, 12, 18, 12, 28)):
        ws.column_dimensions[col].width = width
    yellow = PatternFill('solid', fgColor='FFF2CC')
    for row in ws.iter_rows(min_row=2):
        if row[4].value not in ('厂家确认', '用户认可', '我的物流配置'):
            row[2].fill = yellow
    ws['H1'] = '黄色格 = 非厂家确认/用户认可的数值，改完「测算」页自动更新。'

    calc = wb.create_sheet('测算')
    r = ref
    uL, uW, uH = r['factory.unit_package_cm.长'], r['factory.unit_package_cm.宽'], r['factory.unit_package_cm.高']
    cL, cW, cH = r['factory.carton_cm.长'], r['factory.carton_cm.宽'], r['factory.carton_cm.高']
    leg_carton = (f'IF({r["logistics.first_leg_basis"]}="cbm",{cL}*{cW}*{cH}/1000000*{r["logistics.first_leg_rate_cny"]}/{r["factory.carton_qty"]},'
                  f'MAX({r["factory.carton_gross_kg"]},{cL}*{cW}*{cH}/{r["logistics.volumetric_divisor"]})*{r["logistics.first_leg_rate_cny"]}/{r["factory.carton_qty"]})')
    leg_unit = (f'IF({r["logistics.first_leg_basis"]}="cbm",{uL}*{uW}*{uH}/1000000*{r["logistics.first_leg_rate_cny"]},'
                f'MAX({r["factory.unit_package_weight_kg"]},{uL}*{uW}*{uH}/{r["logistics.volumetric_divisor"]})*{r["logistics.first_leg_rate_cny"]})')
    per_column = ('price', 'fba', 'mult', 'extra', 'once')
    rows = [
        ('price', '售价', None), ('fba', 'FBA 配送费（可改）', None), ('mult', '主品件数', None),
        ('extra', '组合配件/定制包装 ¥/套', None), ('once', '组合一次性费用 ¥', None),
        ('factory', '出厂价 ¥', f'{r["factory.unit_price_cny"]}*(1+{r["factory.tax_surcharge_rate"]})*[mult]'),
        ('pack', '包装 ¥', r['factory.packaging_cny_per_unit']), ('label', '贴标 ¥', r['factory.labeling_cny_per_unit']),
        ('dom', '工厂→货代 ¥/件', f'{r["factory.domestic_freight_cny_per_shipment"]}/{r["qty"]}'),
        ('customs', '报关 ¥/件', f'{r["logistics.customs_fee_cny_per_shipment"]}/{r["qty"]}'),
        ('other', '其他杂费 ¥/件', f'{r["logistics.other_cny_per_shipment"]}/{r["qty"]}'),
        ('leg', '头程 ¥/件', f'IF(OR({r["factory.carton_qty"]}="",{r["factory.carton_qty"]}=0),{leg_unit},{leg_carton})*[mult]'),
        ('amort', '打样开模检测摊销 ¥/件', f'({r["factory.one_off_cny"]}+[once])/{r["qty"]}'),
        ('duty', '关税', f'IF({r["logistics.duty_included"]},0,([factory]+[extra])/{r["fx"]}*{r["logistics.duty_rate"]})'),
        ('landed', '到岸成本', f'([factory]+[extra]+[pack]+[label]+[dom]+[customs]+[other]+[leg]+[amort])/{r["fx"]}+[duty]'),
        ('referral', '佣金', f'MAX([price]*{r["amazon.referral_rate"]},{r["amazon.referral_min"]})'),
        ('storage', '仓储', f'{uL}*{uW}*{uH}/{CUFT_CM3}*{r["amazon.storage_per_cuft_month"]}*{r["amazon.storage_months"]}'),
        ('place', '入库配置费', r['amazon.inbound_placement_fee']),
        ('ret', '退货损耗', f'[price]*{r["amazon.return_loss_rate"]}'),
        ('fees', '亚马逊费用合计', '[fba]+[referral]+[storage]+[place]+[ret]'),
        ('contrib', '广告前毛利', '[price]-[landed]-[fees]'),
        ('margin', '广告前毛利率', '[contrib]/[price]'),
        ('band', '毛利率档位', 'IF([margin]>=0.4,"很好",IF([margin]>=0.3,"优秀",IF([margin]>=0.2,"一般",IF([margin]>=0,"偏低","亏损"))))'),
        ('be_acos', '盈亏平衡 ACOS', '[margin]'),
        ('t_acos', '目标 ACOS', f'MAX([margin]-{r["target"]},0)'),
        ('allowed', '允许到岸成本', f'[price]-[fees]-{r["target"]}*[price]'),
        ('headroom', '到岸余量', '[allowed]-[landed]'),
        ('be_cpc', '保本 CPC（参考 CVR）', f'[contrib]*{r["cvr"]}'),
        ('t_cpc', '目标 CPC（参考 CVR）', f'[price]*[t_acos]*{r["cvr"]}'),
    ]
    row_of = {key: index for index, (key, _, _) in enumerate(rows, start=2)}
    calc.append(['项目'] + [(s['id'] + ' ' + s['label']) if s['offer']['id'] == 'base' else s['id'] for s in m['scenarios']])
    for key, label, formula in rows:
        index = row_of[key]
        calc.cell(row=index, column=1, value=label)
        for col, s in enumerate(m['scenarios'], start=2):
            letter = calc.cell(row=1, column=col).column_letter
            static = {'price': s['price'], 'fba': s['fees']['fba'], 'mult': s['offer']['mult'],
                      'extra': s['offer']['extra'], 'once': s['offer']['extra_one_off']}
            if s['offer']['own_package']:
                # A bundle with its own box has its own chargeable weight and cubic feet; the script computed them.
                static['leg'] = s['first_leg_cny'] or 0
                static['storage'] = s['fees']['storage']
            if key in static:
                value = static[key]
            else:
                value = '=' + re.sub(r'\[(\w+)\]', lambda g: f'{letter}{row_of[g.group(1)]}', formula)
            if s.get('status') != 'complete' and key in {'contrib', 'margin', 'be_acos', 't_acos', 'headroom', 'be_cpc', 't_cpc'}:
                value = None
            if s.get('status') != 'complete' and key == 'band':
                value = s['margin_band']
            if s['fees']['total'] is None and key in {'fees', 'allowed'}:
                value = None
            if s['landed_cost'] is None and key == 'landed': value = None
            cell_ = calc.cell(row=index, column=col, value=value)
            if key in per_column or (key in ('leg', 'storage') and s['offer']['own_package']):
                cell_.fill = yellow
            if key in ('margin', 'be_acos', 't_acos'):
                cell_.number_format = '0.0%'
            elif key not in per_column and key != 'band':
                cell_.number_format = '0.00'
    calc.column_dimensions['A'].width = 24
    for cell_ in calc[1]:
        cell_.font = Font(bold=True)

    kw = wb.create_sheet('关键词CPC')
    ref_col = next(i for i, s in enumerate(m['scenarios'], start=2) if s['recommended'])
    letter = calc.cell(row=1, column=ref_col).column_letter
    kw.append(['关键词', '月搜索量', 'CPC', '来源', '核心词', f'保本 CPC（{m["scenarios"][ref_col - 2]["id"]}）', '目标 CPC', '灯'])
    for i, row in enumerate(m['keywords'], start=2):
        kw.append([row['keyword'], row['search_volume'], row['cpc'], row['source'], '是' if row['core'] else '',
                   f"='测算'!{letter}{row_of['be_cpc']}", f"='测算'!{letter}{row_of['t_cpc']}",
                   f'=IF(C{i}<=G{i},"绿",IF(C{i}<=F{i},"黄","红"))'])
        if m['scenarios'][ref_col - 2].get('status') != 'complete':
            for col in (6, 7, 8): kw.cell(i, col).value = None
    kw.column_dimensions['A'].width = 34
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def clean(value):
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


ASK_FIELDS = [
    ('logistics', 'first_leg_rate_cny', '头程单价'), ('logistics', 'first_leg_basis', '头程计费方式'),
    ('logistics', 'duty_included', '头程是否包税'), ('logistics', 'duty_rate', '关税率合计（不包税才用）'),
    ('logistics', 'customs_fee_cny_per_shipment', '报关费'), ('logistics', 'other_cny_per_shipment', '其他每票杂费（如英国清关）'),
    (None, 'target_net_margin', '目标净利率'),
    (None, 'target_pre_ad_margin', '个人广告前目标毛利率（选填）'),
    # S1-R13: packing, labelling and inbound placement used to be asked in two more rounds at step 6
    ('factory', 'packaging_cny_per_unit', '包装费（每件，选填）'), ('factory', 'labeling_cny_per_unit', '贴标费（每件，选填）'),
    ('amazon', 'inbound_placement_fee', '入库配置费（每件，选填）'),
]
PROFILE_FACTORY = ('packaging_cny_per_unit', 'labeling_cny_per_unit')  # the only factory costs a rate profile may carry
MUST_ASK = {'first_leg_rate_cny', 'duty_included', 'customs_fee_cny_per_shipment'}


def profile_path(root, scope):
    return USER_PROFILE if scope == 'user' else Path(root) / '00_产品资料' / '物流配置.yaml'


def show_rate(key, value, basis):
    if value is None:
        return '—'
    if key == 'first_leg_rate_cny':
        return f'¥{value:g}/{"方" if basis == "cbm" else "kg"}'
    if key == 'first_leg_basis':
        return '按方' if value == 'cbm' else '按 kg 计费重'
    if key == 'duty_included':
        return '包税' if flag(value) else '不包税'
    if key in ('customs_fee_cny_per_shipment', 'other_cny_per_shipment'):
        return f'¥{value:g}/票'
    if key in PROFILE_FACTORY:
        return f'¥{value:g}/件'
    if key == 'inbound_placement_fee':
        return f'{value:g}/件（站点币）'
    return f'{value * 100:g}%' if isinstance(value, (int, float)) else str(value)


def profile_status(root):
    """Which of the user's own rates are still simulated defaults, plus the card to show in chat."""
    stack = layers(Path(root))
    rows = []
    basis = pick(stack, 'logistics', 'first_leg_basis')['value']
    for group, key, label in ASK_FIELDS:
        node = pick(stack[1:] if key == 'target_pre_ad_margin' else stack, group, key)
        rows.append({'key': key, 'label': label, 'value': node['value'], 'status': node['status'], 'source': node['source'],
                     'shown': show_rate(key, node['value'], basis)})
    needs = [r['label'] for r in rows if r['key'] in MUST_ASK and r['status'] != 'user_profile']
    source = {'user_profile': '你的物流配置', 'missing': '未填'}
    table = '\n'.join(f'| {r["label"]} | {r["shown"]} | {source.get(r["status"], "默认模拟")} |' for r in rows)
    card = ('【等你】费率确认（算成本前只问这一次，下面所有项一起回）\n'
            '下面是你自己的货代、报关、包装贴标和入库费率，决定到岸成本：**填了就按你的真实费率算；不填就按模拟值算，报告会标「模拟」，不会再单独问**。\n\n'
            '| 项目 | 现在用的 | 来源 |\n|---|---|---|\n' + table + '\n\n'
            '直接回一句话就行，例如：\n'
            '物流费率：头程 12 元/kg，包税，报关 180 一票，贴标 0.5 元/件\n\n'
            '或回「用默认」：先按模拟值算，以后随时说「改物流费率」再改。\n'
            '填过的会存进你的物流配置，以后所有产品自动使用，不再问。')
    return {'ok': True, 'needs_ask': bool(needs), 'simulated': needs, 'rows': rows,
            'user_profile': str(USER_PROFILE), 'card': card if needs else ''}


def parse_rate(key, raw):
    text = str(raw).strip()
    if key == 'duty_included':
        if text.lower() in {'true', 'yes', 'y', '1', '是', '包税'}:
            return True
        if text.lower() in {'false', 'no', 'n', '0', '否', '不包税'}:
            return False
        raise ValueError('头程是否包税只能填 是/否')
    if key == 'first_leg_basis':
        value = {'kg': 'kg', '公斤': 'kg', 'cbm': 'cbm', '方': 'cbm'}.get(text.lower())
        if not value:
            raise ValueError('头程计费方式只能是 kg 或 cbm')
        return value
    value = num(text.rstrip('%'), key, True)
    if text.endswith('%'):
        value /= 100
    if key in ('duty_rate', 'target_net_margin', 'target_pre_ad_margin') and value >= 1:
        raise ValueError(f'{key} 请填小数，如 0.25 或 25%')
    return value


def set_profile(root, pairs, scope='user'):
    allowed = {key: (group, key) for group, key, _ in ASK_FIELDS}
    allowed.update({f'{group}.{key}': (group, key) for group, key, _ in ASK_FIELDS if group})
    path = profile_path(root, scope)
    data = load_yaml(path)
    changed = {}
    for pair in pairs:
        if '=' not in pair:
            raise ValueError(f'格式应为 键=值：{pair}')
        name, raw = pair.split('=', 1)
        if name.strip() not in allowed:
            raise ValueError(f'不认识的费率项：{name}（可用：{"、".join(k for _, k, _ in ASK_FIELDS)}）')
        group, key = allowed[name.strip()]
        value = parse_rate(key, raw)
        (data.setdefault(group, {}) if group else data)[key] = value
        changed[key] = value
    data['updated_at'] = datetime.date.today().isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('# 星空物流配置：头程、报关等你自己的费率。由 s1_cost_model.py --set-profile 写入，也可手改。\n'
                    + yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding='utf-8')
    return {'ok': True, 'path': str(path), 'set': changed, 'status': profile_status(root)}


def refresh_defaults(doc, root):
    """Rates filled into the profile after --init replace the simulated defaults already copied into the inputs."""
    stack = layers(Path(root))
    applied = []
    for group in ('logistics', 'amazon', 'factory', None):
        keys = ([k for (g, k) in FIELDS if g == group] if group != 'factory' else list(PROFILE_FACTORY)) if group else \
            ['target_net_margin', 'target_pre_ad_margin', 'first_batch_qty']
        for key in keys:
            node = (doc.get(group) or {}).get(key) if group else doc.get(key)
            status = node.get('status') if isinstance(node, dict) else None
            eligible = status in ('default_assumption', 'missing') or (key == 'target_pre_ad_margin' and (node is None or status == 'user_profile'))
            if not eligible:
                continue
            fresh = pick(stack[1:] if key == 'target_pre_ad_margin' else stack, group, key)
            if fresh['status'] == 'user_profile' and (not isinstance(node, dict) or fresh['value'] != node.get('value')
                                                      or fresh.get('source') != node.get('source') or status != 'user_profile'):
                (doc.setdefault(group, {}) if group else doc)[key] = fresh
                applied.append(key)
    return applied


def load_cost_references(root, doc):
    """Join page SKU prices to the same package evidence, without claiming a selected product."""
    doc = copy.deepcopy(doc)
    factory = doc.get('factory') or {}
    def val(key):
        n = factory.get(key)
        return n.get('value') if isinstance(n, dict) else n
    package, weight = val('unit_package_cm'), val('unit_package_weight_kg')
    source = ' '.join(str((factory.get(k) or {}).get('source') or '') for k in ('unit_package_cm','unit_package_weight_kg')
                      if isinstance(factory.get(k), dict))
    if not doc.get('supplier_reference') and package and weight:
        matches = []
        evidence = load_yaml(root/'S1_选品板块/alibaba-supplier-evaluation/02_supplier_evidence.yaml')
        for candidate in evidence.get('candidates', []):
            offer_id = str(candidate.get('offer_id') or '')
            if not offer_id or not re.search(r'(?<!\d)' + re.escape(offer_id) + r'(?!\d)', source):
                continue
            prices = {s.get('name'): s.get('price') for s in candidate.get('skus', []) if isinstance(s, dict)}
            for row in candidate.get('piece_weight_scale', []):
                name = row.get('sku1')
                try:
                    d = [float(row[k]) for k in ('length','width','height')]
                    kg, price = float(row['weight'])/1000, float(prices.get(name))
                except (ValueError, TypeError, KeyError):
                    continue
                if price <= 0 or not math.isfinite(price) or sorted(d) != sorted(map(float,package)) or abs(kg-float(weight)) > .001:
                    continue
                matches.append(dict(sku_name=name, offer_id=offer_id, unit_price_cny=price, package_cm=d,
                    package_weight_kg=kg, url=candidate.get('url'), status='page_extracted',
                    source=f'02_supplier_evidence.yaml / offer {offer_id} / SKU {name}',
                    match_rule='与已有参考包裹的供应商编号、尺寸和重量匹配；同价按SKU名称排序；非选定采购SKU'))
        if matches:
            doc['supplier_reference'] = sorted(matches, key=lambda r:(r['unit_price_cny'],r['sku_name']))[0]
    def size_tier(ds, kg):
        if not ds or kg is None or kg <= 0:
            return None
        length, width, height = sorted((float(d)/CM_PER_IN for d in ds), reverse=True)
        if length <= 15 and width <= 12 and height <= .75 and kg*1000/G_PER_OZ <= 16:
            return 'small_standard'
        if length <= 18 and width <= 14 and height <= 8 and kg*1000/G_PER_OZ <= 320:
            return 'large_standard'
        return None
    target_tier = size_tier(package, weight)
    samples = []
    canonical = root/OUT/'01_market_facts.json'
    if canonical.is_file():
        samples = json.loads(canonical.read_text(encoding='utf-8')).get('representatives', [])
    else:
        for path in sorted((root/OUT/'运行数据').glob('detail_*.json')):
            try:
                payload = json.loads(path.read_text(encoding='utf-8'))
                for content in payload.get('content', []):
                    row = json.loads(content.get('text', '{}')).get('data')
                    if isinstance(row, dict) and row.get('asin'):
                        samples.append({**row, 'source': path.relative_to(root).as_posix()})
            except (ValueError, TypeError):
                continue
    matched = {}
    for row in samples:
        try:
            ds = row.get('package_size_cm')
            if isinstance(ds, list) and len(ds) == 1: ds = ds[0]
            ds = dims(ds, '竞品包裹')
            kg, fee = float(row.get('weight_g'))/1000, float(row.get('fba_fee'))
        except (ValueError, TypeError):
            continue
        if not target_tier or size_tier(ds, kg) != target_tier or not math.isfinite(fee) or fee <= 0:
            continue
        key = row.get('parent_asin') or row.get('asin')
        matched.setdefault(key, dict(asin=row.get('asin'), parent_asin=key, fee=fee, source=row.get('source'),
                                     package_cm=ds, weight_kg=kg))
    if matched:
        fees = [r['fee'] for r in matched.values()]
        doc['competitor_fba_reference'] = dict(median=statistics.median(fees), count=len(fees),
            minimum=min(fees), maximum=max(fees), size_tier=target_tier, samples=list(matched.values()),
            basis='同标准尺寸级别，独立父体；非完全相同计费重；仅工具费率对照，不替代本品费用')
    return doc


def cost_step(root):
    home=HERE.parents[3]
    if not (home/'templates/stage_roadmap.yaml').is_file():
        return 7  # Source-only legacy fixtures have no installed roadmap.
    previous=list(sys.path)
    try:
        sys.path.insert(0,str(home/'scripts'))
        from s1_roadmap import resolve_roadmap
        version=resolve_roadmap(home,root)['stages']['S1'].get('roadmap_version','legacy.m1')
        return 6 if version=='r01.m2' else 7
    finally:sys.path[:]=previous


def run(root, init=False, force=False, export_xlsx=False, price_scenarios=None):
    # The shared adapter preserves this generator's exact native bytes/algorithm.
    delivery_scripts = Path(__file__).resolve().parents[3] / 'scripts'
    previous_paths = list(sys.path)
    try:
        sys.path.insert(0, str(delivery_scripts))
        from r01_native import execute_native
        from s1_readability import reader_projection
        compact = execute_native(root, lambda view: run(view, init, force, export_xlsx, price_scenarios), step=cost_step(root),
                                 report_paths=[(OUT / MD).as_posix()])
    finally:
        sys.path[:] = previous_paths
    if compact is not NotImplemented:
        return compact
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f'产品根不存在：{root}')
    out = root / OUT
    inputs = out / INPUTS
    if init:
        if inputs.exists() and not force:
            return {'ok': True, 'action': 'kept_existing', 'inputs': str(inputs)}
        out.mkdir(parents=True, exist_ok=True)
        initial=skeleton(root)
        if cost_step(root)==6:
            initial.update(schema_version='2.0',calculation_version='r01.cost.2')
            initial['target_pre_ad_margin']=cell(.4,'default_assumption','S1新案默认广告前目标40%，待用户条件确认')
            initial['logistics']['duty_included']=cell(False,'default_assumption','默认不包税；需实际确认')
        inputs.write_text(yaml.safe_dump(initial, allow_unicode=True, sort_keys=False), encoding='utf-8')
        return {'ok': True, 'action': 'initialized', 'inputs': str(inputs)}
    if not inputs.is_file():
        raise ValueError(f'缺 {INPUTS}：先用 --init 生成待填表')
    doc = yaml.safe_load(inputs.read_bytes().decode('utf-8-sig')) or {}
    applied = refresh_defaults(doc, root)
    if applied:
        inputs.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding='utf-8')
    raw = inputs.read_bytes()
    doc = load_cost_references(root, doc)
    if cost_step(root)==6:
        doc.update(schema_version='2.0',calculation_version='r01.cost.2')
    model = (compute_current(doc,root=root,price_scenarios=price_scenarios) if str(doc.get('schema_version'))=='2.0'
             or doc.get('calculation_version')=='r01.cost.2' else compute(doc, load_yaml(DEFAULTS)))
    md = reader_projection(render_md(model))
    # Attach actual knowledge use before computing the native Markdown hash.
    delivery_scripts = Path(__file__).resolve().parents[3] / 'scripts'
    if (delivery_scripts / 'kb_ui.py').is_file():
        previous_paths = list(sys.path)
        sys.path.insert(0, str(delivery_scripts))
        try:
            from kb_ui import enrich_report
            md = enrich_report(md, root, delivery_scripts.parent, 'S1', cost_step(root))
        finally:
            sys.path[:] = previous_paths
    md = reader_projection(md)
    (out / MD).write_text(md, encoding='utf-8', newline='\n')
    for supplement in out.glob('*条件测算*.md'):
        original = supplement.read_text(encoding='utf-8-sig')
        from s1_readability import keep_history_once
        supplement.write_text(keep_history_once(md, original, '此前条件测算原文（仅作历史核对）'), encoding='utf8', newline='\n')
    if export_xlsx:
        write_xlsx(model, doc, out / XLSX)
    model['inputs_sha256'] = hashlib.sha256(raw).hexdigest()
    # Explicitly reviewed cost-input binding; never stamp the current BOM automatically.
    model['product_configuration_sha256'] = doc.get('product_configuration_sha256')
    model['md_sha256'] = hashlib.sha256(md.encode('utf-8')).hexdigest()
    (out / MODEL).write_text(yaml.safe_dump(clean(model), allow_unicode=True, sort_keys=False), encoding='utf-8')
    return {'ok': True, 'cost_basis': model['cost_basis'], 'md': str(out / MD),
            'xlsx':str(out / XLSX),
            'scenarios': [{'id': s['id'], 'price': s['price'], 'gross_margin': clean(s['gross_margin']),
                           'margin_band': s['margin_band'], 'ad_pressure': s['ad_pressure']['label']}
                          for s in model['scenarios']],
            'missing_for_factory': len(model['inquiry']), 'applied_profile': applied,
            'simulated_rates': model['simulated_rates'], 'warnings': model['warnings']}


def main(argv=None):
    parser = argparse.ArgumentParser(description='S1 成本账 + 广告可推性')
    parser.add_argument('--run', required=True, help='产品根')
    parser.add_argument('--init', action='store_true', help='生成 05_cost_inputs.yaml 待填表')
    parser.add_argument('--force', action='store_true', help='与 --init 连用：覆盖已有待填表')
    parser.add_argument('--export-xlsx', action='store_true', help='按需导出统一引擎测算值')
    parser.add_argument('--price-scenarios', help='逗号分隔原生多售价情景；不改写原售价或input_state')
    parser.add_argument('--profile-status', action='store_true', help='查哪些物流费率仍是模拟默认值，输出确认卡')
    parser.add_argument('--set-profile', nargs='+', metavar='键=值', help='把用户回复的费率写进物流配置')
    parser.add_argument('--scope', choices=['user', 'product'], default='user', help='user=所有产品通用；product=只管本品')
    args = parser.parse_args(argv)
    try:
        if args.profile_status:
            result = profile_status(Path(args.run).expanduser().resolve())
        elif args.set_profile:
            result = set_profile(Path(args.run).expanduser().resolve(), args.set_profile, args.scope)
        else:
            result = run(args.run, args.init, args.force, args.export_xlsx, args.price_scenarios)
    except ValueError as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
