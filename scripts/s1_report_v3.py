"""S1 决策报告（第 3 版）：一屏结论 + 七维度 + 12 章，左侧电梯导航，利润可编辑重算。

- 数字：脚本从已保存回包、市场事实、统一成本引擎（同毛利计算器）、合规规则库与敏感词库计算。
- 判断：AI 写在 S1_选品板块/_阶段总览/decision_narrative.yaml（`template` 出骨架、`check` 查缺项）；
  没写的地方显示“待补写”，不编造。
- 口径（用户 2026-11-04 定）：广告前毛利 ≥35% 过线、30–35% 勉强；退货率取细分类目值；
  广告按销售额 20% 测；关税不计；FBA 按回填尺寸重量套官方费率，后台实测可覆盖。

  python s1_report_v3.py template <产品根>   打印 decision_narrative.yaml 骨架（已带可用数据）
  python s1_report_v3.py check <产品根>      列出未填写的判断字段
"""
from __future__ import annotations

import copy
import html
import json
import math
import re
from pathlib import Path

import yaml

VERSION = 3
HOME = Path(__file__).resolve().parents[1]
NARRATIVE = Path('S1_选品板块/_阶段总览/decision_narrative.yaml')
RULER_INPUTS = Path('S1_选品板块/amazon-ip-risk-assessment/04A_ruler_inputs.yaml')
BLACKLIST = Path('S1_选品板块/amazon-ip-risk-assessment/04_compliance_blacklist.yaml')
QUICK = (Path('00A_quick_select.yaml'), Path('00_总控/starsky-fullchain-workflow/00A_quick_select.yaml'))
GATE_GOOD, GATE_MID = 0.35, 0.30
REPORT_TACOS = 20.0
PENDING = '待补写'
SOFT = {None, '', 'default_assumption', 'unlabeled', 'missing', 'ai_inferred', 'manual_override', 'scenario'}
HARD_FBA = {'user_attested', 'confirmed', 'user_profile', 'user_edit_pending', 'backend', 'seller_central'}

CHAPTERS = [
    ('overview', '00', '决策总览', '七个维度，一眼看能不能做'),
    ('why', '01', '选品缘由', '为什么会选到这个产品'),
    ('market', '02', '市场需求', '有多大、在不在涨'),
    ('users', '03', '用户痛点', '谁在买、为什么买、怕什么'),
    ('compete', '04', '竞争格局', '能不能挤进去'),
    ('entry', '05', '切入策略', '凭什么让买家选我们，首发怎么做'),
    ('profit', '06', '利润测算', '卖一单到底赚多少（可直接改数重算）'),
    ('promo', '07', '推广难度', '广告能不能打回本'),
    ('supply', '08', '货源供应', '货能不能稳定拿到'),
    ('ip', '09', '侵权合规', '会不会被投诉、被下架'),
    ('next', '10', '落地条件', '怎样才能做'),
    ('appendix', '11', '数据附录', '数据边界与来源'),
]
DIMS = [('market', '市场需求', 'market'), ('compete', '竞争格局', 'compete'), ('profit', '利润', 'profit'),
        ('promo', '推广', 'promo'), ('supply', '供应链', 'supply'), ('ip', '侵权合规', 'ip'),
        ('trend', '趋势与季节', 'market')]
LIGHT = {'good': ('🟢', '有利'), 'warn': ('🟡', '有风险'), 'crit': ('🔴', '卡脖子')}
LIGHT_ALIASES = {'green': 'good', 'ok': 'good', 'yellow': 'warn', 'orange': 'warn', 'mid': 'warn',
                 'red': 'crit', 'bad': 'crit'}
ADVICE = {'first': ('good', '首推'), 'recommend': ('good', '推荐'), 'must': ('good', '必做'),
          'later': ('warn', '第二批'), 'skip': ('warn', '先不做')}


# ---------------------------------------------------------------- 小工具
def esc(value):
    return html.escape('' if value is None else str(value), quote=True)


def num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def fmt_int(value):
    return f'{value:,.0f}' if num(value) else '—'


def money(value):
    return ('−$' if value < 0 else '$') + f'{abs(value):.2f}' if num(value) else '—'


def pct(value, digits=1):
    return f'{100*value:.{digits}f}%' if num(value) else '—'


def wan(value):
    return (f'{value/10000:.1f} 万' if value >= 10000 else f'{value:,.0f}') if num(value) else '—'


def light(name):
    name = LIGHT_ALIASES.get(str(name or '').lower(), str(name or '').lower())
    return name if name in LIGHT else 'warn'


def chip(name, label=None):
    name = light(name)
    icon, text = LIGHT[name]
    return f'<span class="chip {name}">{icon} {esc(label or text)}</span>'


def read(root, rel):
    path = Path(root) / rel
    if not path.is_file():
        return {}
    raw = path.read_text(encoding='utf-8-sig')
    data = json.loads(raw) if path.suffix == '.json' else yaml.safe_load(raw)
    return data if isinstance(data, dict) else {}


def listed(value):
    return [x for x in value if x not in (None, '')] if isinstance(value, list) else []


def text_or(value, fallback=PENDING):
    return str(value).strip() if isinstance(value, str) and value.strip() else fallback


# ---------------------------------------------------------------- 利润：统一成本引擎
def engine():
    import importlib.util
    import sys
    path = HOME / 'scripts/s1_cost_engine.py'
    key = 'starsky_decision_cost_engine'
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        before = list(sys.path)
        try:
            sys.path.insert(0, str(path.parent))
            spec.loader.exec_module(module)
        finally:
            sys.path[:] = before
    return sys.modules[key]


def _set(payload, key, value, source):
    payload['inputs'][key] = value
    node = dict(payload['input_state'].get(key) or {})
    node.update(value=value, status='scenario', source=source)
    payload['input_state'][key] = node


def report_payload(unified, facts):
    """Engine inputs with the report policy applied only where the case has no attested value."""
    if not unified or not unified.get('inputs'):
        return None, []
    payload = {'marketplace': unified.get('marketplace', 'US'), 'evaluation_date': unified.get('evaluation_date'),
               'inputs': copy.deepcopy(unified['inputs']), 'input_state': copy.deepcopy(unified.get('input_state') or {})}
    p, notes = payload['inputs'], []

    def soft(key):
        return (payload['input_state'].get(key) or {}).get('status') in SOFT

    if not p.get('dutyInc') and soft('duty') and p.get('duty') not in (0, 0.0):
        _set(payload, 'duty', 0.0, '用户口径：选品测算不计进口关税')
        notes.append('关税不计（用户口径）')
    rate = ((((facts.get('market_overview') or {}).get('category') or {}).get('size') or {}).get('return_rate'))
    if num(rate) and 0 < rate < 100 and soft('ret') and not p.get('ret'):
        _set(payload, 'ret', round(float(rate), 2), '细分类目退货率（Sorftime 类目报表）')
        notes.append(f'退货率取类目 {rate:.1f}%')
    if p.get('admode', 'tacos') == 'tacos' and soft('tacos') and p.get('tacos') != REPORT_TACOS:
        _set(payload, 'tacos', REPORT_TACOS, '报告口径：广告按销售额 20% 测，新品期 25–40% 可改')
        notes.append('广告按销售额 20%')
    fba, proof = auto_fba(payload)
    if fba is not None and (payload['input_state'].get('fba') or {}).get('status') not in HARD_FBA:
        _set(payload, 'fba', fba, 'FBA 按单包尺寸重量套费率表自动估算')
        notes.append(f'FBA 按尺寸重量自动估算 {money(fba)}')
    payload['_fba_proof'] = proof
    return payload, notes


def auto_fba(payload, price=None):
    p = payload['inputs']
    if str(payload.get('marketplace', 'US')).upper() != 'US':
        return None, {'status': 'missing', 'reason': '仅美国站自动估算，其他站点请填后台值'}
    dims = [p.get(k) for k in ('L', 'W', 'H')]
    if not all(num(v) and v > 0 for v in dims) or not num(p.get('g')):
        return None, {'status': 'missing', 'reason': '缺单包尺寸或重量'}
    table, _ = engine().load_fees('US')
    realized = (price or p.get('price') or 0) * (1 - (p.get('coupon') or 0) / 100)
    return engine().estimate_fba(realized, dims, p['g'] / 1000, table.get('fba'), payload.get('evaluation_date') or '')


def run(payload, **over):
    """Engine main basis (unattested pack/inbound take the engine's own upper-bound scenario)."""
    q = copy.deepcopy({k: v for k, v in payload.items() if not k.startswith('_')})
    for key, value in over.items():
        _set(q, key, value, '报告情景')
    result = engine().calculate(q)
    basis = result.get('main_basis') or {}
    shown = basis.get('result') or result.get('result') or {}
    scenarios = result.get('cost_assumption_scenarios') or []
    assumed = (scenarios[-1].get('assumptions') if scenarios and basis else {}) or {}
    return shown, assumed, result


def calculator_defaults(payload, assumed, normalized):
    keys = ('buy tax qty pack dom oneoff frate fmode div customs clear deliv dutyInc duty vatReg vat fx price coupon comm '
            'referralMin fba inbound srate months ltsR ltsM other awd awdS awdM awdIn awdOut awdT couponFixed couponRate '
            'admode tacos cpc cvr ret resell reserve g L W H pcs cL cW cH cKg').split()
    d = {k: normalized.get(k, payload['inputs'].get(k)) for k in keys}
    for key, value in assumed.items():
        if d.get(key) is None:
            d[key] = value
    for key, value in d.items():
        if value is None and key not in ('fba', 'cpc'):
            d[key] = 0
    return d


# ---------------------------------------------------------------- 数据整理
def traffic_terms(overview, narrative):
    traffic = overview.get('traffic') or {}
    asin = (narrative.get('market') or {}).get('traffic_asin')
    rows = traffic.get(asin) if asin in traffic else next(iter(traffic.values()), []) if traffic else []
    asin = asin if asin in traffic else next(iter(traffic), None)
    out = []
    for row in rows or []:
        if not isinstance(row, dict) or not row.get('keyword'):
            continue
        # market_overview stores searches/bid/organic; raw Sorftime rows use the long names.
        try:
            bid = float(row.get('bid', row.get('recommended_bid')))
        except (TypeError, ValueError):
            bid = None
        position = str(row.get('organic', row.get('latest_organic_position')) or '')
        match = re.match(r'Page (\d+), Pos (\d+)', position)
        out.append({'keyword': row['keyword'], 'volume': row.get('searches', row.get('monthly_search_volume')), 'cpc': bid,
                    'position': f'第{match[1]}页第{match[2]}位' if match else '无自然位',
                    'ad': bool(row.get('ad')) or 'Ad' in str(row.get('exposure_position') or '')})
    return asin, sorted(out, key=lambda r: -(r['volume'] or 0))


def split_terms(terms, narrative):
    exclude = [str(x).lower() for x in listed((narrative.get('market') or {}).get('exclude_keywords'))]
    relevant = [t for t in terms if not any(x in t['keyword'].lower() for x in exclude)]
    excluded = [t for t in terms if t not in relevant]
    return relevant, excluded


def pool_rows(overview, facts):
    rows = ((overview.get('pool') or {}).get('rows')) or facts.get('competitors') or []
    out = []
    for row in rows:
        if not isinstance(row, dict) or not row.get('asin'):
            continue
        out.append({'asin': row['asin'], 'brand': row.get('brand'), 'price': row.get('price', row.get('price_usd')),
                    'sales': row.get('monthly_sales'), 'reviews': row.get('reviews'), 'rating': row.get('rating'),
                    'date': row.get('listing_date') or row.get('online_date'), 'fba': row.get('fba_fee'),
                    'weight': row.get('weight_g'), 'title': row.get('title')})
    return out


def compliance_rows(root, ruler, narrative):
    """Rule library × product attributes; attribute null = 待厂家确认, never assumed absent."""
    library = read(HOME / 'skills/amazon-ip-risk-assessment/references', 'compliance_ruler.yaml')
    attrs = (read(root, RULER_INPUTS).get('attributes') or {})
    hits = {r.get('id'): r for r in (ruler.get('items') or []) if isinstance(r, dict)}
    involved, pending, clear = [], [], []
    for rule in library.get('rules') or []:
        if not isinstance(rule, dict) or rule.get('id', '').startswith('IP-'):
            continue
        names = (rule.get('when') or {}).get('attributes') or []
        values = [(attrs.get(n) or {}).get('value') for n in names]
        if rule['id'] in hits or True in values:
            hit = hits.get(rule['id']) or rule
            involved.append({'item': hit.get('title'), 'light': hit.get('light'), 'need': hit.get('requires'),
                             'who': hit.get('action'), 'status': 'involved'})
        elif names and all(v is False for v in values):
            clear.append(rule.get('title'))
        elif names:
            pending.append(rule.get('title'))
    custom = listed((narrative.get('ip') or {}).get('certs'))
    if custom:
        return custom, pending, clear
    return involved, pending, clear


def brand_terms(root, overview, pool, narrative):
    blacklist = read(root, BLACKLIST)
    own = {str(x).lower() for x in listed((narrative.get('ip') or {}).get('own_brands'))}
    verified = {}
    for item in blacklist.get('brand_terms') or []:
        if isinstance(item, dict) and item.get('term'):
            verified[item['term']] = item.get('reason') or '已登记'
    for item in listed(blacklist.get('forbidden_terms')) + listed(blacklist.get('blocked_terms')):
        # 黑名单条目既可能是字符串，也可能是 {term, reason, ...}（blocked_terms 为后者）
        if isinstance(item, dict):
            term, reason = item.get('term'), item.get('reason')
        else:
            term, reason = item, None
        if term:
            verified.setdefault(str(term), reason or '已登记禁用')
            if reason and verified[str(term)] == '已登记禁用':
                verified[str(term)] = reason
    pool_brands = [r['brand'] for r in pool if r.get('brand')]
    category = [b.get('brand') or b.get('name') for b in ((overview.get('category') or {}).get('brands') or [])[:10]
                if isinstance(b, dict)]
    groups = [('已核实 / 已登记', list(verified)), ('同款竞品品牌', pool_brands), ('类目 Top10 销量品牌', category)]
    seen, out = set(own), []
    for label, names in groups:
        kept = []
        for name in names:
            if name and str(name).lower() not in seen:
                seen.add(str(name).lower())
                kept.append(str(name))
        if kept:
            out.append((label, kept, verified if label.startswith('已核实') else {}))
    return out


def platform_terms():
    lexicon = read(HOME / 'templates', 'compliance_base_lexicon.yaml')
    rows = []
    for chapter in lexicon.get('chapters') or []:
        if chapter.get('id') not in ('ch7_listing_quality', 'ch2_epa_pesticide', 'ch5_compliance_triggers', 'ch3_fda_medical'):
            continue
        terms, replace = [], []
        for entry in chapter.get('entries') or []:
            if entry.get('level') not in ('block', 'conditional', 'seasonal'):
                continue
            terms += [entry['term']] if entry.get('term') else listed(entry.get('terms'))
            replace += listed(entry.get('replace'))
        if terms:
            rows.append({'group': chapter.get('name'), 'terms': terms[:12], 'why': chapter.get('consequence') or '',
                         'replace': '、'.join(dict.fromkeys(replace[:4])) or '不写'})
    return rows


# ---------------------------------------------------------------- 组装
def build(model, root, *, cost, ct, product, ruler, inputs):
    root = Path(root)
    facts = model.get('facts') or {}
    overview = facts.get('market_overview') or {}
    narrative = read(root, NARRATIVE)
    v2 = model.get('report_v2') or {}
    decision = v2.get('decision_summary') or {}
    front = v2.get('front') or {}
    quick = next((read(root, q) for q in QUICK if (root / q).is_file()), {})
    category = overview.get('category') or {}
    size = category.get('size') or {}
    conc = category.get('concentration') or {}
    trend = overview.get('category_trend') or {}
    stats = (overview.get('pool') or {}).get('stats') or {}
    quant = stats.get('price_quantiles') or facts.get('price_bands') or {}
    pool = pool_rows(overview, facts)
    asin, terms = traffic_terms(overview, narrative)
    relevant, excluded = split_terms(terms, narrative)
    core = (overview.get('keywords') or [{}])[0]

    unified = cost.get('unified_cost') or {}
    payload, policy = report_payload(unified, facts)
    profit = None
    if payload:
        try:
            shown, assumed, result = run(payload)
            if shown.get('pre_ad_profit') is not None:
                defaults = calculator_defaults(payload, assumed, result.get('inputs') or {})
                defaults['monthly'] = int((narrative.get('profit') or {}).get('monthly_units') or 0) or \
                    max(30, int(round((stats.get('units') or 0) / max(stats.get('skus') or 1, 1) * .2 / 10) * 10))
                defaults['adshare'] = 100.0
                if defaults.get('cpc') in (None, 0):
                    defaults['cpc'] = next((t['cpc'] for t in relevant if t['cpc']), core.get('cpc') or 0)
                fba_auto = (payload['input_state'].get('fba') or {}).get('source', '').startswith('FBA 按单包')
                profit = {'shown': shown, 'payload': payload, 'defaults': defaults, 'policy': policy,
                          'fba_auto': fba_auto, 'fba_proof': payload.get('_fba_proof') or {},
                          'table': engine().load_fees(payload.get('marketplace', 'US'))[0].get('fba')}
        except (ValueError, KeyError, TypeError) as error:
            policy = policy + ['统一成本引擎未能计算：' + str(error)]

    def at(price=None, **over):
        if not profit:
            return None
        if price is not None:
            over['price'] = price
        return run(profit['payload'], **over)[0]

    return {'schema_version': VERSION, 'root': str(root), 'model': model, 'narrative': narrative, 'decision': decision,
            'front': front, 'quick': quick, 'facts': facts, 'overview': overview, 'size': size, 'conc': conc,
            'trend': trend, 'stats': stats, 'quant': quant, 'pool': pool, 'traffic_asin': asin, 'terms': terms,
            'relevant': relevant, 'excluded': excluded, 'core': core, 'profit': profit, 'policy': policy, 'at': at,
            'ct': ct, 'product': product, 'ruler': ruler, 'inputs': inputs, 'cost': cost,
            'compliance': compliance_rows(root, ruler, narrative), 'brands': brand_terms(root, overview, pool, narrative),
            'platform_terms': platform_terms(), 'v2': v2}


# ---------------------------------------------------------------- 判定与七维度
def price_of(r):
    return ((r['profit'] or {}).get('defaults') or {}).get('price') or r['front'].get('price') or None


def dims(r):
    narrative = r['narrative'].get('dimensions') or {}
    shown = (r['profit'] or {}).get('shown') or {}
    out = []
    for key, name, href in DIMS:
        auto_light, auto_head, auto_text = 'warn', PENDING, ''
        if key == 'profit' and shown.get('pre_ad_margin') is not None:
            m = shown['pre_ad_margin']
            auto_light = 'good' if m >= GATE_GOOD else 'warn' if m >= GATE_MID else 'crit'
            auto_head = {'good': '毛利过线', 'warn': '毛利勉强', 'crit': '按现在的成本赚不到钱'}[auto_light]
            auto_text = (f'售价 {money(price_of(r))} 广告前毛利 {pct(m)}、广告后 {pct(shown.get("post_ad_margin"))}；'
                         f'标准是广告前 35% 以上。')
        elif key == 'promo' and shown.get('pre_ad_profit') is not None and r['relevant']:
            top = next((t for t in r['relevant'] if t['cpc']), None)
            if top:
                be = top['cpc'] / shown['pre_ad_profit'] if shown['pre_ad_profit'] > 0 else None
                auto_light = 'crit' if be is None or be > .15 else 'warn' if be > .10 else 'good'
                auto_head = '广告打不回本' if auto_light == 'crit' else '广告有压力' if auto_light == 'warn' else '广告能打平'
                auto_text = f'{top["keyword"]} CPC {money(top["cpc"])}，保本需要点击转化 {pct(be) if be else "（广告前已亏）"}。'
        elif key == 'market' and num(r['stats'].get('units')):
            units = r['stats']['units']
            auto_light = 'good' if units >= 3000 else 'warn' if units >= 1000 else 'crit'
            auto_head = '需求够大' if auto_light == 'good' else '需求一般' if auto_light == 'warn' else '需求偏小'
            top = r['relevant'][0] if r['relevant'] else None
            auto_text = f'同款 {r["stats"].get("skus")} 款合计月销 {fmt_int(units)} 件' + (
                f'；对标款头部流量词 {top["keyword"]} {wan(top["volume"])}/月。' if top else '。')
        elif key == 'compete' and num(r['stats'].get('top3_share')):
            share, median = r['stats']['top3_share'], r['stats'].get('review_median') or 0
            auto_light = 'crit' if share >= .6 or median >= 2000 else 'warn' if share >= .4 or median >= 800 else 'good'
            auto_head = {'good': '竞争分散', 'warn': '同款头部集中', 'crit': '头部垄断'}[auto_light]
            auto_text = (f'类目 Top3 占 {r["conc"].get("top3_product", 0):.1f}%；同款 Top3 占 {pct(share)}，'
                         f'评论中位 {fmt_int(median)}。')
        elif key == 'trend' and num(r['trend'].get('yoy_12m')):
            yoy = r['trend']['yoy_12m']
            auto_light = 'good' if yoy >= -.05 else 'warn' if yoy >= -.15 else 'crit'
            auto_head = '盘子平稳' if auto_light == 'good' else '盘子在缩'
            auto_text = f'类目近 12 个月同比 {pct(yoy)}。'
        elif key == 'ip':
            lights = [str(x[1]) for x in r['v2'].get('front', {}).get('ip_rows') or []]
            auto_light = 'crit' if any('🔴' in x for x in lights) else 'warn' if any('🟡' in x or '⚪' in x for x in lights) else 'good'
            auto_head = {'crit': '有红灯', 'warn': '无红灯，有项要查清', 'good': '无风险项'}[auto_light]
        elif key == 'supply':
            offer = r['front'].get('offer_url')
            auto_light = 'warn' if offer else 'crit'
            auto_head = '有货源，规格待厂家确认' if offer else '还没有选定货源'
        given = narrative.get(key) or {}
        out.append({'key': key, 'name': name, 'href': href, 'light': light(given.get('light') or auto_light),
                    'head': text_or(given.get('headline'), auto_head), 'text': text_or(given.get('text'), auto_text or PENDING)})
    return out


# ---------------------------------------------------------------- HTML 片段
def section(key, body, lead=''):
    _, no, name, question = next(c for c in CHAPTERS if c[0] == key)
    return (f'<section class="chapter" id="{key}" data-chapter="{no}"><h2><span class="no">{no}</span>{esc(name)}'
            f'<span class="q">{esc(question)}</span></h2>' + (f'<p class="lead">{esc(lead)}</p>' if lead else '') +
            body + '</section>')


def table_html(headers, rows, numeric=(), highlight=None):
    head = ''.join(f'<th{" class=num" if i in numeric else ""}>{esc(h)}</th>' for i, h in enumerate(headers))
    body = ''
    for index, row in enumerate(rows):
        cls = ' class="me"' if highlight is not None and highlight(index, row) else ''
        body += f'<tr{cls}>' + ''.join(f'<td{" class=num" if i in numeric else ""}>{cell}</td>' for i, cell in enumerate(row)) + '</tr>'
    return f'<table><tr>{head}</tr>{body}</table>' if rows else '<p class="lead">本轮没有对应数据。</p>'


def hbars(rows, unit='', muted=()):
    vals = [v for _, v, _ in rows if num(v)]
    top = max(vals) if vals else 1
    out = []
    for label, value, note in rows:
        width = 0 if not num(value) or not value else max(1.5, 100 * value / top)
        color = '#c3c2b7' if label in muted else '#2a78d6'
        out.append(f'<div class="hb" data-tip="{esc(label)}：{fmt_int(value)}{esc(unit)}"><div class="hb-l">{esc(label)}</div>'
                   f'<div class="hb-t"><div class="hb-f" style="width:{width:.1f}%;background:{color}"></div></div>'
                   f'<div class="hb-v">{fmt_int(value)}{esc(unit)}</div><div class="hb-n">{esc(note)}</div></div>')
    return '<div class="hbars">' + ''.join(out) + '</div>'


def callout(kind, text_html):
    return f'<div class="callout {kind}">{text_html}</div>'


def pending_box(what):
    return callout('note', f'<b>{PENDING}：</b>{esc(what)}（写在 decision_narrative.yaml，重新生成报告即可）')


def line_chart(series_by_asin, names):
    order = list(series_by_asin)[:3]
    if not order:
        return '<p class="lead">没有逐月销量数据。</p>'
    months = sorted({m for a in order for m, _ in series_by_asin[a]})
    colors = ['#2a78d6', '#eb6834', '#1baf7a']
    width, height, left, right, top, bottom = 760, 300, 48, 150, 16, 34
    peak = max(v for a in order for _, v in series_by_asin[a]) or 1
    ymax = math.ceil(peak / 500) * 500
    x = lambda i: left + i * (width - left - right) / max(len(months) - 1, 1)
    y = lambda v: top + (height - top - bottom) * (1 - v / ymax)
    out = [f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" aria-label="对标商品月销量走势">']
    step = max(ymax // 4, 1)
    for g in range(0, int(ymax) + 1, int(step)):
        out.append(f'<line x1="{left}" x2="{width-right}" y1="{y(g):.1f}" y2="{y(g):.1f}" stroke="#e1e0d9"/>'
                   f'<text x="{left-6}" y="{y(g)+4:.1f}" text-anchor="end" class="tick">{g:,}</text>')
    for i, m in enumerate(months):
        if i % 3 == 0 or i == len(months) - 1:
            out.append(f'<text x="{x(i):.1f}" y="{height-12}" text-anchor="middle" class="tick">{esc(m[2:])}</text>')
    data = {}
    for k, a in enumerate(order):
        lookup = dict(series_by_asin[a])
        pts = [(i, lookup[m]) for i, m in enumerate(months) if m in lookup]
        data[a] = [lookup.get(m) for m in months]
        out.append(f'<polyline points="{" ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in pts)}" fill="none" '
                   f'stroke="{colors[k]}" stroke-width="2" stroke-linejoin="round"/>')
        i, v = pts[-1]
        out.append(f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="4" fill="{colors[k]}" stroke="#fcfcfb" stroke-width="2"/>'
                   f'<text x="{x(i)+8:.1f}" y="{y(v)+4:.1f}" class="dlabel">{v:,}</text>')
    labels = [names.get(a, a) for a in order]
    out.append(f'<rect class="hit" x="{left}" y="{top}" width="{width-left-right}" height="{height-top-bottom}" fill="transparent" '
               f"data-months='{esc(json.dumps(months))}' data-series='{esc(json.dumps(data))}' "
               f"data-names='{esc(json.dumps(labels, ensure_ascii=False))}' data-colors='{esc(json.dumps(colors))}' "
               f"data-geo='{json.dumps([left, width-right])}'/>")
    out.append(f'<line class="xhair" x1="0" x2="0" y1="{top}" y2="{height-bottom}" stroke="#c3c2b7" visibility="hidden"/></svg>')
    legend = ''.join(f'<span class="lg"><i style="background:{colors[k]}"></i>{esc(n)}</span>' for k, n in enumerate(labels))
    return f'<div class="legend">{legend}</div><div class="chartwrap">{"".join(out)}</div>'


# ---------------------------------------------------------------- 各章
def ch_hero(r):
    n = r['narrative']
    decision = r['decision']
    from s1_report_v2 import decision_headline
    headline = decision_headline(decision) if decision and decision.get('formed', True) else '尚未形成决策'
    product = r['model'].get('product_name') or '待确认产品'
    image = r['front'].get('image')
    picture = (f'<img src="{esc(image)}" alt="{esc(product)}">' if image and str(image).startswith(('data:image/', 'https://'))
               else '<div class="noimg">产品主图缺失：尚未绑定当前款式图片</div>')
    offer = r['front'].get('offer_url')
    supplier = (r['product'].get('selected_supplier') or {}) if isinstance(r['product'].get('selected_supplier'), dict) else {}
    shown = (r['profit'] or {}).get('shown') or {}
    d = (r['profit'] or {}).get('defaults') or {}
    band = r['quick'].get('price_band')
    facts = [('目标价带', f'${band}' if band else '—'), ('测款价', money(d.get('price'))), ('同款中位价', money(r['quant'].get('p50'))),
             ('到岸成本', money(shown.get('landed_cost'))), ('采购', f'¥{d["buy"]:g}/件' if num(d.get('buy')) else '—'),
             ('采购授权', '已授权' if decision.get('purchase_authorized') else '未授权')]
    warning = '' if decision.get('accepted') else (f'验收未通过（{decision.get("pending_count")} 项）' if decision.get('pending_count') else '验收待完成')
    link = ''
    if offer and re.match(r'https://detail\.1688\.com/offer/\d+\.html$', str(offer)):
        label = '、'.join(x for x in (supplier.get('name'), '1688 商品页', supplier.get('sku')) if x)
        link = f'<p class="srclink">🔗 所选货源：<a href="{esc(offer)}" target="_blank" rel="noopener">{esc(label)}</a></p>'
    return (f'<section class="hero" id="top" data-report-version="{VERSION}">{picture}<div>'
            f'<div class="kicker">S1 选品可行性研究 · {esc(r["inputs"].get("marketplace") or r["quick"].get("marketplace") or "US")} 站 · 数据截至 {esc(r["model"].get("evidence_date") or "—")}</div>'
            f'<h1>{esc(product)}</h1>'
            f'<div class="verdict"><span data-decision="{esc(decision.get("code", ""))}">唯一结论：{esc(headline)}</span>'
            f'<small>{esc(text_or(n.get("verdict_reason"), warning))}</small></div>'
            f'<p class="oneline">{esc(text_or(n.get("verdict_line"), "一句话结论" + PENDING))}</p>'
            f'<div class="facts">{"".join(f"<span>{esc(k)} <b>{esc(v)}</b></span>" for k, v in facts)}</div>{link}</div></section>')


def ch_overview(r):
    cards = ''.join(f'<a class="dim {d["light"]}" href="#{d["href"]}"><div class="t"><b>{esc(d["name"])}</b>'
                    f'<span class="st">{LIGHT[d["light"]][0]} {LIGHT[d["light"]][1]}</span></div>'
                    f'<div class="h">{esc(d["head"])}</div><p>{esc(d["text"])}</p></a>' for d in dims(r))
    return section('overview', f'<div class="dims">{cards}</div>',
                   '点任一卡片跳到对应章节。红色是“不解决就不能做”，黄色是“要查清”，绿色是“不用担心”。')


def ch_why(r):
    why = r['narrative'].get('why') or {}
    q = r['quick']
    supplier = r['product'].get('selected_supplier') if isinstance(r['product'].get('selected_supplier'), dict) else {}
    boxes = [('① 条件选品', text_or(why.get('condition'), '、'.join(x for x in (
                  f'{q.get("marketplace")} 站' if q.get('marketplace') else None, f'价带 ${q["price_band"]}' if q.get('price_band') else None,
                  f'广告前目标毛利 {q["target_margin_pre_ad"]*100:.0f}%' if num(q.get('target_margin_pre_ad')) else None) if x) or PENDING)),
             ('② 确认的形态', text_or(why.get('form'), r['model'].get('product_name') or PENDING)),
             ('③ 选的货源', text_or(why.get('supplier'), '、'.join(x for x in ((supplier or {}).get('name'), (supplier or {}).get('sku')) if x) or PENDING))]
    body = '<div class="why">' + ''.join(f'<div><b>{esc(t)}</b><span>{esc(s)}</span></div>' for t, s in boxes) + '</div>'
    body += callout('key', '<b>为什么是这个站点：</b>' + esc(text_or(why.get('site_reason'), text_or(((read(r['root'], 'S1_选品板块/amazon-market-research/market_narrative.yaml').get('summary') or {}).get('site_reason')), PENDING))))
    body += callout('key', '<b>它解决的事：</b>' + esc(text_or(why.get('solves'))))
    return section('why', body, '前因：这是按条件挑出、由你确认的方向，不是系统随机推的。')


def ch_market(r):
    n = r['narrative'].get('market') or {}
    stats, size, trend = r['stats'], r['size'], r['trend']
    top = r['relevant'][0] if r['relevant'] else {}
    tiles = [(wan(top.get('volume')), f'{top.get("keyword", "核心流量词")} 月搜索量', '对标款真实流量词里的头部词'),
             (fmt_int(stats.get('units')), f'{stats.get("skus", "—")} 款同款合计月销（件）', '严格同款'),
             (wan(size.get('top100_units')), '类目 Top100 月销（件）', f'节点 {r["overview"].get("target_node_id") or "—"}'),
             (pct(trend.get('yoy_12m')), '类目近 12 个月同比', '整个类目口径')]
    body = '<div class="stats">' + ''.join(f'<div class="stat"><div class="v">{esc(v)}</div><div class="k">{esc(k)}</div><div class="n">{esc(m)}</div></div>' for v, k, m in tiles) + '</div>'
    rows = [(t['keyword'], t['volume'], f'CPC {money(t["cpc"])} · 对标款自然{t["position"]}') for t in r['relevant'][:10]]
    core = r['core']
    if core.get('keyword') and core['keyword'] not in [t['keyword'] for t in r['relevant'][:10]]:
        rows.append((core['keyword'] + '（需求门核心词）', core.get('monthly_searches'), '需求门登记的核心词'))
    total = sum(t['volume'] or 0 for t in r['relevant'])
    kw = (f'<b>买家实际在搜什么</b>（对标款 {esc(r["traffic_asin"] or "—")} 的流量词，只列与本品相关的，月搜索量）'
          f'<div style="margin-top:12px">{hbars(rows, muted=tuple(x[0] for x in rows if "核心词" in x[0]))}</div>'
          f'<p class="lead" style="margin:8px 0 0">相关词合计约 <b>{wan(total)} 次/月</b>。</p>') if rows else '<b>买家实际在搜什么</b><p class="lead">本轮没有取到对标款流量词。</p>'
    if r['excluded']:
        kw += callout('risk', '已剔除的非本品需求词：' + '、'.join(f'{esc(t["keyword"])}（{fmt_int(t["volume"])}）' for t in r['excluded'][:5]) +
                      '。这些词搜索量大但不是本品，打广告要否掉。')
    body += f'<div class="card">{kw}</div>'
    series = {}
    for asin, points in (r['facts'].get('trend_by_asin') or {}).items():
        pts = [(str(p.get('month', '')).replace('-', ''), p.get('sales')) for p in points or [] if isinstance(p, dict) and num(p.get('sales')) and p['sales'] >= 0]
        if len(pts) >= 3:
            series[asin] = sorted(pts)
    wanted = listed(n.get('trend_asins'))
    order = [a for a in wanted if a in series] or sorted(series, key=lambda a: -series[a][-1][1])
    brand = {p['asin']: p for p in r['pool']}
    names = {a: f'{a} · {brand.get(a, {}).get("brand") or ""} · {money(brand.get(a, {}).get("price"))}' for a in order}
    body += (f'<div class="card"><b>对标商品月销量走势</b>（件/月；鼠标移上去看每月数值）<div style="margin-top:10px">'
             f'{line_chart({a: series[a] for a in order[:3]}, names)}</div>'
             f'<p class="lead" style="margin:6px 0 0">{esc(text_or(n.get("reading")))}</p></div>')
    body += '<h3 class="sub">市场容量</h3><p class="lead">' + esc(
        f'类目 Top100 月销 {fmt_int(size.get("top100_units"))} 件、月销售额 {money(size.get("top100_revenue"))}；同款 {stats.get("skus", "—")} 款月销 {fmt_int(stats.get("units"))} 件；'
        f'类目退货率 {size.get("return_rate", "—")}%、工具估算转化率 {size.get("conversion_rate", "—")}%。') + '</p>'
    return section('market', body, text_or(n.get('lead'), '结论：' + next((d['head'] for d in dims(r) if d['key'] == 'market'), PENDING)))


def ch_users(r):
    n = r['narrative'].get('users') or {}
    reviews = r['overview'].get('reviews') or {}
    scenes, pains = listed(n.get('scenes')), listed(n.get('pains'))
    q = lambda x: f'<div class="quote"><b>{esc(x.get("title"))}</b>' + (f' · {esc(x["count"])}' if x.get('count') else '') + \
        (f'<br>“{esc(x["quote"])}”' if x.get('quote') else '') + (f' → <b>{esc(x["opportunity"])}</b>' if x.get('opportunity') else '') + '</div>'
    left = ''.join(q(x) for x in scenes if isinstance(x, dict)) or pending_box('为什么买：使用场景 + 评论原话 + 条数')
    right = ''.join(q(x) for x in pains if isinstance(x, dict)) or pending_box('怕什么：痛点 + 评论原话 + 对我们的机会')
    body = f'<div class="two"><div class="card" style="margin:0"><b>为什么买（场景）</b>{left}</div><div class="card" style="margin:0"><b>怕什么（痛点 → 我们的机会）</b>{right}</div></div>'
    body += '<h3 class="sub">需求与痛点</h3><p class="lead">' + esc(text_or(n.get('summary'), text_or(((read(r['root'], 'S1_选品板块/amazon-market-research/market_narrative.yaml').get('readings') or {}).get('voc'))))) + '</p>'
    if not reviews.get('low_star'):
        body += callout('note', '差评样本几乎没有：痛点只能看“有人提到”，不能算投诉率。可用 Sorftime 按 1–3 星补拉，或在卖家后台“商机探测器”选该细分类目，把好评点 / 差评点截图或导出文件交给我补充。')
    return section('users', body, f'来自 {reviews.get("total", "—")} 条竞品评论（其中 1–3 星 {reviews.get("low_star", "—")} 条）。')


def ch_compete(r):
    n = r['narrative'].get('compete') or {}
    stats, conc = r['stats'], r['conc']
    new = len(stats.get('new_movers') or [])
    tiles = [(f'{conc["top3_product"]:.1f}%' if num(conc.get('top3_product')) else '—', '类目 Top3 销量占比', f'亚马逊自营 {conc.get("amazon_owned", "—")}%'),
             (pct(stats.get('top3_share')), '同款 Top3 销量占比', '同款内头部集中度'),
             (fmt_int(stats.get('review_median')), '同款评论中位数', '新品要追的评论门槛'),
             (str(new), '一年内上架且起量的新链接', '同款池')]
    body = '<div class="stats">' + ''.join(f'<div class="stat"><div class="v">{esc(v)}</div><div class="k">{esc(k)}</div><div class="n">{esc(m)}</div></div>' for v, k, m in tiles) + '</div>'
    bands = [(b['band'], b.get('units'), f'{b.get("skus")} 款') for b in stats.get('price_bands') or [] if isinstance(b, dict)]
    body += f'<div class="card"><b>同款价格带</b>（月销按价格带汇总）<div style="margin-top:12px">{hbars(bands, " 件")}</div></div>'
    bench = n.get('benchmark_asin')
    rows = [(esc(p['asin']) + (' · 对标' if p['asin'] == bench else ''), esc(p['brand'] or '—'), money(p['price']), fmt_int(p['sales']),
             fmt_int(p['reviews']), esc(p['rating'] or '—'), esc(p['date'] or '—'), money(p['fba']),
             f'{p["weight"]:.0f} g' if num(p['weight']) else '—') for p in r['pool']]
    dated = sum(1 for p in r['pool'] if p['date'])
    body += (f'<details><summary>{len(r["pool"])} 款同款竞品明细（价格、月销、评论、上架日、FBA 费、重量）</summary><div>'
             + table_html(['ASIN', '品牌', '售价', '月销', '评论', '星级', '上架', 'FBA 费', '重量'], rows, (2, 3, 4, 5, 7, 8),
                          lambda i, row: r['pool'][i]['asin'] == bench)
             + (f'<p class="lead" style="margin-top:8px">上架日、FBA 费、重量只取到 {dated}/{len(r["pool"])} 款；补齐后“新链接能不能起来”的判断才完整。</p>' if dated < len(r['pool']) else '')
             + '</div></details>')
    body += '<h3 class="sub">竞争格局</h3><p class="lead">' + esc(text_or(n.get('reading'))) + '</p>'
    body += callout('note', '想细看竞品页面（主图、A+、价格与优惠券历史、BSR 走势）：用卖家精灵或 SIF 的浏览器插件打开 3–5 个重点竞品，页面顶部就有这些数据。')
    return section('compete', body, text_or(n.get('lead'), '结论：' + next((d['head'] for d in dims(r) if d['key'] == 'compete'), PENDING)))


def ch_entry(r):
    n = r['narrative'].get('entry') or {}
    body = ''
    thresholds = [x for x in listed(n.get('thresholds')) if isinstance(x, dict)]
    if thresholds:
        rows = [(esc(x.get('item')), esc(x.get('evidence')), chip(x.get('light'), x.get('ours'))) for x in thresholds]
        body += f'<div class="card"><b>先认清门槛：头部已经做到的，我们必须有</b><div style="margin-top:8px">{table_html(["门槛项", "竞品现状（证据）", "我们的货源现状"], rows)}</div></div>'
    points = [x for x in listed(n.get('points')) if isinstance(x, dict)]
    if points:
        rows = []
        for i, x in enumerate(points, 1):
            tone, label = ADVICE.get(str(x.get('advice')), ('warn', x.get('advice') or '待定'))
            rows.append((str(i), f'<b>{esc(x.get("title"))}</b>', esc(x.get('need')), esc(x.get('evidence')), esc(x.get('cost')), chip(tone, label)))
        body += (f'<div class="card"><b>能拉开差距的切入点</b>（按“解决真实不满 × 竞品没做好 × 成本可控”排序）<div style="margin-top:8px">'
                 + table_html(['#', '切入点 / 改款', '解决的未满足需求', '评论证据', '成本与难度', '建议'], rows,
                              highlight=lambda i, row: ADVICE.get(str(points[i].get('advice')), ('', ''))[1] == '首推') + '</div></div>')
    if not thresholds and not points:
        body += pending_box('门槛项（thresholds）与切入点（points）')
    body += '<h3 class="sub">切入点与机会</h3><p class="lead">' + esc(text_or(n.get('opportunity'), text_or(((read(r['root'], 'S1_选品板块/amazon-market-research/market_narrative.yaml').get('summary') or {}).get('breakthrough'))))) + '</p>'
    launch = n.get('launch') or {}
    variants = [x for x in listed(launch.get('variants')) if isinstance(x, dict)]
    card = '<div class="card"><b>上架方案（首发）</b>：做哪几个变体、卖多少钱、第一批进多少货。'
    if launch.get('variant_logic'):
        card += f'<h3 class="sub">① 变体怎么定</h3><p class="lead">{esc(launch["variant_logic"])}</p>'
    if variants:
        rows = []
        for x in variants:
            price, buy = x.get('price'), x.get('buy')
            res = r['at'](price, **({'buy': buy} if num(buy) else {})) if num(price) and x.get('launch', True) is not False else None
            rows.append((f'<b>{esc(x.get("name"))}</b>', esc(x.get('reason')), money(price) if num(price) else esc(x.get('plan') or '暂不上'),
                         f'¥{buy:g}' if num(buy) else '现价', pct(res.get('pre_ad_margin')) if res else '—', pct(res.get('post_ad_margin')) if res else '—'))
        card += table_html(['首发变体', '为什么', '建议售价', '采购价', '广告前毛利', f'广告后（广告 {REPORT_TACOS:g}%）'], rows, (2, 3, 4, 5),
                           lambda i, row: variants[i].get('main'))
    options = [x for x in listed(n.get('price_options') or launch.get('price_options')) if isinstance(x, dict) and num(x.get('price'))]
    if r['profit'] and not options:
        d = r['profit']['defaults']
        options = [{'label': '同款中位价', 'price': r['quant'].get('p50')}, {'label': 'P75', 'price': r['quant'].get('p75')},
                   {'label': '测款价', 'price': d['price']}, {'label': '测款价 +$5', 'price': round(d['price'] + 5, 2)},
                   {'label': '测款价 +$10', 'price': round(d['price'] + 10, 2)}]
        options = [o for o in options if num(o['price'])]
    if options:
        rows = []
        for o in options:
            res = r['at'](o['price'], **({'buy': o['buy']} if num(o.get('buy')) else {}))
            if not res:
                continue
            m = res.get('pre_ad_margin')
            tone = 'good' if num(m) and m >= GATE_GOOD else 'warn' if num(m) and m >= GATE_MID else 'crit'
            rows.append((esc(o.get('label')), money(o['price']), f'¥{o["buy"]:g}' if num(o.get('buy')) else '现价', pct(m),
                         pct(res.get('post_ad_margin')), chip(tone, {'good': '过线', 'warn': '勉强', 'crit': '不过线'}[tone])))
        card += '<h3 class="sub">② 价格怎么定：按广告前 35% 倒推</h3>' + table_html(['方案', '售价', '采购价', '广告前毛利', f'广告 {REPORT_TACOS:g}% 后', '是否过线'], rows, (1, 2, 3, 4))
    if launch.get('pricing_note'):
        card += callout('risk', esc(launch['pricing_note']))
    stages = [x for x in listed(launch.get('pricing')) if isinstance(x, dict)]
    if stages:
        card += table_html(['阶段', '做法'], [(esc(x.get('stage')), esc(x.get('action'))) for x in stages])
    stats = r['stats']
    sales = sorted(p['sales'] for p in r['pool'] if num(p['sales']))
    median = sales[len(sales)//2] if sales else None
    batch = launch.get('first_batch') or {}
    rows = [('同款月销中位数', f'约 {fmt_int(median)} 件/月', f'最低 {fmt_int(sales[0])}、最高 {fmt_int(sales[-1])}' if sales else '—'),
            ('新链接前 2 个月预计', f'{fmt_int(median*.15 if median else None)}–{fmt_int(median*.25 if median else None)} 件/月', '按中位款的 15–25% 估（新品没评论、主要靠广告）')]
    if num(batch.get('qty')):
        res = r['at'](None, qty=batch['qty']) if r['profit'] else None
        rows.append(('<b>建议首批</b>', f'<b>{fmt_int(batch["qty"])} 件</b>', esc(batch.get('reason') or '')))
        if res and num(res.get('landed_cost')):
            rows.append(('首批资金（到岸）', money(res['landed_cost'] * batch['qty']), '不含广告预算和样品'))
    card += '<h3 class="sub">③ 第一批进多少货</h3>' + table_html(['依据', '数字', '怎么来的'], [(a if a.startswith('<') else esc(a), b if b.startswith('<') else esc(b), c) for a, b, c in rows], (1,))
    if not variants and not stages and not num(batch.get('qty')):
        card += pending_box('上架方案：变体（launch.variants）、价格阶段（launch.pricing）、首批数量（launch.first_batch）')
    body += card + '</div>'
    if n.get('summary'):
        body += callout('key', '<b>一句话首发方案：</b>' + esc(n['summary']))
    if n.get('boundary'):
        body += callout('note', esc(n['boundary']))
    body += callout('note', '数量和价格是按现有市场数据推的起点，不是采购授权；正式下单前在 S4 / 上市计划里结合广告预算和现金流再定。')
    return section('entry', body, '把痛点和竞品现状放在一起看：哪些是“不做就输”的门槛，哪些才是能拉开差距的点；最后给出首发方案。')


FIELDS = [
    ('① 出厂综合成本（采购 + 包装贴标）', [('buy', '采购单价', '¥/件'), ('pack', '包装 / 贴标 / 箱唛', '¥/件'), ('tax', '开票税点（单价不含税时）', '%'), ('oneoff', '打样 / 开模 / 检测', '¥/票')]),
    ('② 头程（国内段 + 国际段）', [('dom', '国内段：厂家 → 货代', '¥/票'), ('frate', '国际段：货代 → 目的港', '¥/kg'), ('g', '单件含包装重', 'g'),
                               ('L', '单包 长', 'cm'), ('W', '单包 宽', 'cm'), ('H', '单包 高', 'cm'), ('div', '体积重除数', ''),
                               ('pcs', '每箱件数（有外箱按外箱算）', '件'), ('cL', '外箱 长', 'cm'), ('cW', '外箱 宽', 'cm'), ('cH', '外箱 高', 'cm'), ('cKg', '整箱毛重', 'kg'),
                               ('customs', '报关费', '¥/票'), ('clear', '清关费（英国约 ¥300）', '¥/票'), ('deliv', '派送到 FBA（单独收时）', '¥/票'),
                               ('qty', '首批件数（每票分摊）', '件'), ('fx', '汇率', '¥/站点币')]),
    ('③ 亚马逊费用', [('price', '售价', '站点币'), ('coupon', '优惠券 / 折扣', '%'), ('fba', 'FBA 配送费（尾程，留空自动算）', '站点币'), ('comm', '佣金率', '%'),
                   ('inbound', '入库配置费', '/件'), ('srate', '月仓储费率', '/ft³·月'), ('months', '平均在库', '月'), ('other', '其他费用（月租分摊等）', '/件')]),
    ('④ 退货', [('ret', '退货率', '%'), ('resell', '退货可再售比例', '%'), ('reserve', '额外销售预留', '%')]),
    ('⑤ 广告', [('admode', '广告算法', ''), ('tacos', 'TACoS（广告费占销售额）', '%'), ('cpc', 'CPC 竞价', '站点币'), ('cvr', '点击转化率', '%'), ('adshare', '广告单占比', '%')]),
    ('⑥ 规模', [('monthly', '预计月销量', '件')]),
]


def source_note(r, key):
    state = (r['profit']['payload']['input_state'].get(key) or {}) if r['profit'] else {}
    labels = engine().STATUS_LABELS if r['profit'] else {}
    status, source = state.get('status'), state.get('source')
    if key == 'fba' and r['profit']['fba_auto']:
        proof = r['profit']['fba_proof']
        return (f'自动：{proof.get("shipping_oz", 0)/16:.2f} 磅 · {"大号" if proof.get("size_tier") == "large_standard" else "小号"}标准件 · '
                f'{"旺季" if proof.get("season") == "peak" else "平季"}，含 {100*(proof.get("surcharge_rate") or 0):.1f}% 附加费；上架后用后台实际值覆盖')
    if key in ('pack', 'inbound') and status == 'missing':
        return '未报价，按未核上界'
    text = labels.get(status, status or '')
    return '；'.join(x for x in (text, str(source or '')[:40]) if x)


def ch_profit(r):
    if not r['profit']:
        return section('profit', pending_box('成本测算：统一成本引擎还没有算出结果，先在第 6 步补齐成本输入') +
                       callout('note', '；'.join(r['policy'])), '统一成本引擎未出结果。')
    form = ''
    for group, fields in FIELDS:
        cells = ''
        for key, label, unit in fields:
            if key == 'admode':
                cells += ('<div class="f"><label for="c_admode">广告算法</label><select id="c_admode"><option value="tacos">按 TACoS</option>'
                          '<option value="cpc">按 CPC ÷ 转化率 × 广告单占比</option></select></div>')
                continue
            note = source_note(r, key) if key not in ('monthly', 'adshare') else ('估算：同款中位月销 × 20%' if key == 'monthly' else '广告带来的订单占全部订单')
            cells += (f'<div class="f" data-k="{key}"><label for="c_{key}">{esc(label)}</label><div class="in"><input id="c_{key}" type="number" step="any" min="0">'
                      f'<span class="u">{esc(unit)}</span></div><div class="src">{esc(note)}</div></div>')
        form += f'<fieldset><legend>{esc(group)}</legend><div class="grid">{cells}</div></fieldset>'
    count = sum(len(f) for _, f in FIELDS)
    kpi = lambda i, k: f'<div class="kpi"><div class="k">{k}</div><div class="v" id="o_{i}"></div><div class="n" id="o_{i}_n"></div></div>'
    body = ('<div class="calc" id="calc"><div class="kpis">' + kpi('landed', '单件到岸成本') + kpi('pre', '广告前利润 / 毛利率') +
            kpi('post', '广告后利润 / 净利率') + kpi('ad', '每单广告费 · 保本转化率') + kpi('month', '预计月利润') + kpi('cap', '首批资金（到岸）') +
            '</div><div class="verdictline" id="o_verdict"></div>'
            '<div class="card" style="margin:12px 0"><b>每卖一单，钱去了哪</b>（按当前售价）<div id="o_bars" style="margin-top:10px"></div></div>'
            '<div class="card" style="margin:12px 0"><b>不同售价下的利润</b>（其他参数同下方）<table style="margin-top:8px"><thead><tr><th>售价</th><th>位置</th>'
            '<th class="num">广告前利润</th><th class="num">广告前毛利率</th><th class="num">广告后利润</th><th class="num">广告后</th><th>判断</th></tr></thead>'
            '<tbody id="o_ladder"></tbody></table></div>'
            f'<details class="calc-in" id="calc_in"><summary><span>✏️ 调整测算参数（共 {count} 项，点开可改，上面的结果实时更新）</span><span id="c_changed"></span></summary>'
            '<div class="calc-bar"><button type="button" id="c_reset">恢复报告原值</button><button type="button" id="c_copy">复制参数</button><span id="c_msg"></span></div>'
            + form + '<p class="lead" style="font-size:12.5px">改动保存在本机浏览器里，刷新不丢，不会写回案例。要正式存进产品，请在导航“💰 毛利计算”填同样的数并保存，下次生成报告就用新数。</p></details></div>')
    proof = r['profit']['fba_proof']
    if r['profit']['fba_auto'] and proof.get('base_fee'):
        body += callout('risk', f'<b>FBA {money(r["profit"]["defaults"]["fba"])} 是怎么来的：</b>按单包 {r["profit"]["defaults"]["L"]:g}×{r["profit"]["defaults"]["W"]:g}×{r["profit"]["defaults"]["H"]:g} cm、'
                        f'{r["profit"]["defaults"]["g"]:g} g，计费重 {proof["shipping_oz"]/16:.2f} 磅，{"大号" if proof.get("size_tier") == "large_standard" else "小号"}标准件，'
                        f'{"旺季" if proof.get("season") == "peak" else "平季"}基础费 {money(proof["base_fee"])}，再加 {100*proof["surcharge_rate"]:.1f}% 燃油附加费。'
                        + ('3 磅以上的费率取自第三方整理的 2026 费率表，待用后台费用预览核对。' if proof.get('status') == 'third_party_index_estimate' else '')
                        + '改包装尺寸或重量后，把参数里的 FBA 一格清空，就会按新尺寸自动重算。')
    body += callout('note', '口径：出厂综合成本 = 采购价 + 包装贴标箱唛；头程 = 国内段（厂家→货代，按票分摊）+ 国际段（按实重与体积重取大）；'
                    '报关按票分摊，美国没有清关费，英国另加约 ¥300/票；关税不计；退货额 = 退货率 ×（不可再售部分到岸成本 + FBA + 退款手续费）+ 额外预留。'
                    '本报告口径：' + '；'.join(r['policy']) + '。')
    return section('profit', body, '结果按本案例数据算好，口径与导航“💰 毛利计算”同一套引擎；需要更准，就点开“调整测算参数”改数。')


def ch_promo(r):
    n = r['narrative'].get('promo') or {}
    shown = (r['profit'] or {}).get('shown') or {}
    pre = shown.get('pre_ad_profit')
    rows = []
    for t in [t for t in r['relevant'] if t['cpc']][:5]:
        be = t['cpc'] / pre if num(pre) and pre > 0 else None
        rows.append((esc(t['keyword']), fmt_int(t['volume']), money(t['cpc']), pct(be), money(t['cpc'] / .08), money(pre - t['cpc'] / .08) if num(pre) else '—'))
    body = (f'<div class="card"><b>核心词保本转化率</b>（按售价 {money(price_of(r))}、广告前利润 {money(pre)}；后两列按 8% 转化估算）<div style="margin-top:8px">'
            + table_html(['关键词', '月搜索', '建议竞价', '保本转化率', '8% 转化时每单广告费', '每单广告后'], rows, (1, 2, 3, 4, 5)) + '</div></div>')
    body += callout('risk' if any(x for x in rows) else 'note', esc(text_or(n.get('reading'), '新品点击转化一般 8–12%；保本转化率高于这个区间，前期每单广告都在倒贴。')))
    if n.get('tactics'):
        body += callout('note', esc(n['tactics']))
    return section('promo', body, text_or(n.get('lead'), '结论：' + next((d['head'] for d in dims(r) if d['key'] == 'promo'), PENDING)))


def ch_supply(r):
    n = r['narrative'].get('supply') or {}
    supplier = r['product'].get('selected_supplier') if isinstance(r['product'].get('selected_supplier'), dict) else {}
    offer = r['front'].get('offer_url')
    head = f'<b>已选货源</b>：{esc((supplier or {}).get("name") or "—")}' + (f' · <a href="{esc(offer)}" target="_blank" rel="noopener">1688 商品页</a>' if offer else '')
    skus = [x for x in listed(n.get('skus')) if isinstance(x, dict)]
    if not skus:
        page = r['product'].get('page_data') or {}
        skus = [{'name': t.get('sku_name'), 'price': t.get('price_cny'), 'moq': t.get('min_qty')} for t in page.get('price_tiers') or [] if isinstance(t, dict)]
    d = (r['profit'] or {}).get('defaults') or {}
    rows = [(esc(x.get('name')), f'¥{x["price"]:g}' if num(x.get('price')) else '—', esc(x.get('moq') or '—'),
             f'{d["g"]:g} g' if num(d.get('g')) else '—', f'{d.get("L"):g}×{d.get("W"):g}×{d.get("H"):g} cm' if num(d.get('L')) else '—') for x in skus]
    body = f'<div class="card">{head}<div style="margin-top:8px">{table_html(["SKU", "页面价", "起订", "单包重", "单包尺寸"], rows, (1,))}</div></div>'
    questions = listed(n.get('questions')) or listed(r['ct'].get('supplier_questions'))
    if questions:
        items = ''.join(f'<li><b>{esc(q.get("q") if isinstance(q, dict) else q)}</b>' + (f'<small>{esc(q.get("why"))}</small>' if isinstance(q, dict) and q.get('why') else '') + '</li>' for q in questions[:6])
        body += f'<div class="card"><b>拿样前必须让厂家书面回答的事</b><ol class="steps">{items}</ol></div>'
    alternates = [x for x in listed(n.get('alternates')) if isinstance(x, dict)] or [
        {'name': x.get('supplier_name') or x.get('company'), 'price': x.get('page_price'), 'signal': x.get('reason')} for x in r['facts'].get('supplier_shortlist') or [] if isinstance(x, dict)]
    if alternates:
        body += ('<details><summary>备选货源（页面起价未绑定同规格，不能直接比价）</summary><div>' +
                 table_html(['商家 / 商品', '页面起价', '可见信号'], [(esc(x.get('name')), esc(x.get('price') or '—'), esc(x.get('signal') or '')) for x in alternates]) + '</div></details>')
    return section('supply', body, text_or(n.get('lead'), '已有可拿样的货源；关键规格没有书面确认前，不能直接下单。'))


def ch_ip(r):
    n = r['narrative'].get('ip') or {}
    involved, pending, clear = r['compliance']
    rows = [(esc(x.get('item')), chip('warn' if str(x.get('light') or 'yellow') in ('yellow', 'orange', 'warn') else x.get('light'), '涉及' if x.get('status', 'involved') == 'involved' else x.get('status')),
             esc(x.get('need') or ''), esc(x.get('who') or ''), esc(x.get('difficulty') or ('高' if x.get('light') in ('red', 'orange') else '低'))) for x in involved]
    if pending:
        rows.append(('待厂家确认的属性', chip('warn', '待确认'), esc('、'.join(pending)), '拿样时让厂家书面确认；确认不涉及就不用做对应检测', '—'))
    if clear:
        rows.append(('已排查、不涉及', chip('good', '不涉及'), esc('、'.join(clear)), '—', '—'))
    body = (f'<div class="card"><b>① 上架资质与检测清单</b>（按插件合规规则库逐项排查）<div style="margin-top:8px">'
            + table_html(['项目', '是否涉及', '需要什么', '谁来做 / 怎么办', '对进入难度'], rows) + '</div></div>')
    words = '<div class="card"><b>② 上架禁用词清单</b>（S2 词库与文案、S4 广告否词都要避开）'
    words += '<h3 class="sub">A. 竞品与类目头部品牌词 · 标题、五点、ST、广告、图片、A+ 一律不用</h3>'
    words += table_html(['来源', '品牌词'], [(esc(label), esc('、'.join(names)) + ('' if not ver else ('<br><small>' + esc('；'.join(f'{k}：{v}' for k, v in ver.items())) + '</small>'))) for label, names, ver in r['brands']])
    fit = [x for x in listed(n.get('fit_brands')) if isinstance(x, dict)]
    words += '<h3 class="sub">B. 家具 / 设备品牌适配词 · 默认不写</h3>'
    words += table_html(['词', '风险', '可以怎么写'], [(esc(x.get('terms')), esc(x.get('why')), esc(x.get('instead'))) for x in fit]) if fit else pending_box('适配品牌词（fit_brands）：本品类常被搜的他人品牌，以及替代写法')
    words += '<h3 class="sub">C. 平台敏感 / 夸大词 · 来自你的《亚马逊敏感词与违规词避坑汇总》</h3>'
    words += table_html(['类别', '禁用词', '后果', '替换写法'], [(esc(x['group']), esc('、'.join(x['terms'])), esc(x['why']), esc(x['replace'])) for x in r['platform_terms']])
    material = [x for x in listed(n.get('material_terms')) if isinstance(x, dict)]
    words += '<h3 class="sub">D. 材质与属性误标词 · 最容易被忽略</h3>'
    words += table_html(['不能写', '原因', '正确写法'], [(esc(x.get('bad')), esc(x.get('why')), esc(x.get('good'))) for x in material]) if material else pending_box('材质与属性误标词（material_terms）：颜色≠材质、木种、未实测的尺寸承重等')
    body += words + '</div>'
    rights = [(esc(x[0]), esc(x[1]), esc(x[2]), esc(x[3])) for x in (r['v2'].get('front', {}).get('ip_rows') or [])]
    body += f'<div class="card"><b>③ 权利检索</b><div style="margin-top:8px">{table_html(["项目", "灯", "结论", "需要你做"], rights)}</div></div>'
    return section('ip', body, text_or(n.get('lead'), '结论：' + next((d['head'] for d in dims(r) if d['key'] == 'ip'), PENDING)))


def ch_next(r):
    n = r['narrative'].get('next') or {}
    conditions = [x for x in listed(n.get('conditions')) if isinstance(x, dict)]
    items = ''.join(f'<li><b>{esc(x.get("title"))}</b><small>{esc(x.get("detail"))}</small></li>' for x in conditions) or \
        ''.join(f'<li><b>{esc(x)}</b></li>' for x in (r['decision'].get('conditions') or [])[:5])
    body = f'<div class="card"><ol class="steps">{items or "<li>" + PENDING + "</li>"}</ol></div>'
    if n.get('this_week'):
        body += callout('key', '<b>这周能做的：</b>' + esc(n['this_week']))
    body += callout('note', '本报告不构成采购、拿样付款或广告授权；当前授权以你的记录为准。')
    return section('next', body, text_or(n.get('lead'), '满足下面条件才建议下首批。'))


def ch_appendix(r):
    from s1_report_v2 import render_purchase
    coverage = r['facts'].get('coverage') or {}
    rows = [(esc(k), f'{v.get("observed")} / {v.get("required")}', esc(v.get('reason') or v.get('status') or '')) for k, v in coverage.items() if isinstance(v, dict)]
    body = f'<details><summary>这次没拉到或没拉全的数据</summary><div>{table_html(["项目", "已取得 / 目标", "状态与原因"], rows)}</div></details>'
    manifest = r['model'].get('source_manifest') or {}
    body += ('<details><summary>数据来源与 SHA-256（' + str(len(manifest)) + ' 个文件）</summary><div>' +
             table_html(['来源文件', 'SHA-256'], [(esc(p), esc(s)) if s else (esc(Path(p).stem) + '（本轮未提供）', '—') for p, s in manifest.items()]) + '</div></details>')
    try:
        from kb_report_contract import stage_basis
        basis = stage_basis(r['model'].get('root'))
    except Exception:  # noqa: BLE001 - knowledge basis is supplementary evidence
        basis = ''
    if basis:
        items = [esc(re.sub(r'[*`]', '', line[2:])) for line in basis.splitlines() if line.startswith('- ')]
        body += '<details><summary>知识库依据</summary><div><ul>' + ''.join(f'<li>{x}</li>' for x in items) + '</ul></div></details>'
    decision = r['decision']
    issues = decision.get('issues') or []
    body += ('<details><summary>验收自检' + (f'（{decision.get("pending_count")} 项待处理）' if decision.get('pending_count') else '') + '</summary><div>' +
             (''.join(f'<p>· {esc(x)}</p>' for x in issues) or '<p>本轮验收结果见阶段回执。</p>') + '</div></details>')
    checklist = (r['v2'].get('purchase_checklist') or {})
    if checklist.get('groups'):
        body += f'<details id="purchase"><summary>采购落地清单（样品、厂家配合、合同、发票报关）</summary><div class="purchase">{render_purchase(checklist)}</div></details>'
    identity = r['model'].get('plugin_identity') or {}
    body += f'<p class="foot">报告第 {VERSION} 版 · 插件 {esc(identity.get("version") or "—")} · 数字由脚本从已保存回包与统一成本引擎计算，判断来自 decision_narrative.yaml。</p>'
    return section('appendix', body)


def render_html(r):
    product = r['model'].get('product_name') or '待确认产品'
    nav = ''.join(f'<a href="#{key}"><span class="nn">{no}</span>{esc(name)}</a>' for key, no, name, _ in CHAPTERS)
    chapters = [ch_overview, ch_why, ch_market, ch_users, ch_compete, ch_entry, ch_profit, ch_promo, ch_supply, ch_ip, ch_next, ch_appendix]
    body = ch_hero(r) + ''.join(fn(r) for fn in chapters)
    from s1_report_v2 import PURCHASE_JS
    calc = ''
    if r['profit']:
        d = dict(r['profit']['defaults'])
        if r['profit']['fba_auto']:
            d['fba'] = None
        calc = (f'<script>window.CALC_DEFAULTS={json.dumps(d)};window.CALC_SHOWN_FBA={json.dumps(r["profit"]["defaults"]["fba"])};'
                f'window.CALC_CASE={json.dumps(Path(r["root"]).name, ensure_ascii=False)};window.CALC_MARKET={json.dumps(r["profit"]["payload"].get("marketplace", "US"))};'
                f'window.CALC_DATE={json.dumps(r["profit"]["payload"].get("evaluation_date") or "")};window.CALC_FBA={json.dumps(r["profit"]["table"])};'
                f'window.CALC_LADDER={json.dumps(ladder(r), ensure_ascii=False)};window.CALC_GATES=[{GATE_MID},{GATE_GOOD}];</script>')
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="starsky-report" content="s1-decision-v{VERSION}"><title>S1 选品可行性研究报告 · {esc(product)}</title><style>{CSS}</style></head>'
            f'<body data-report-version="{VERSION}"><nav class="toc"><div class="brand">S1 选品报告<b>{esc(product)}</b></div><div class="in">{nav}</div></nav>'
            f'<div class="wrap" id="report-content">{body}</div><div id="tip"></div>{calc}<script>{JS}</script>{PURCHASE_JS}</body></html>')


def ladder(r):
    q, d = r['quant'], r['profit']['defaults']
    band = str(r['quick'].get('price_band') or '')
    top = re.findall(r'\d+(?:\.\d+)?', band)
    rows = [(q.get('p25'), 'P25 · 同款低价'), (q.get('p50'), 'P50 · 同款中位价'), (q.get('p75'), 'P75'), (d.get('price'), '本次测款价'),
            (round(d['price'] + 5, 2) if num(d.get('price')) else None, ''), (float(top[-1]) - .01 if top else None, '价带上限')]
    seen, out = set(), []
    for price, tag in rows:
        if num(price) and round(price, 2) not in seen:
            seen.add(round(price, 2))
            out.append([round(price, 2), tag])
    return sorted(out)


# ---------------------------------------------------------------- Markdown（同源）
def render_md(r):
    from s1_report_v2 import decision_headline
    n, product = r['narrative'], r['model'].get('product_name') or '待确认产品'
    shown = (r['profit'] or {}).get('shown') or {}
    lines = [f'<!-- s1-report-version: {VERSION} -->', f'# S1 选品可行性研究报告｜{product}', '',
             f'**唯一结论：{decision_headline(r["decision"]) if r["decision"] and r["decision"].get("formed", True) else "尚未形成决策"}**', '', text_or(n.get('verdict_line'), '一句话结论' + PENDING), '']
    for key, no, name, question in CHAPTERS:
        lines += [f'## {no} {name} · {question}', '']
        if key == 'overview':
            lines += ['| 维度 | 灯 | 判断 | 依据 |', '| --- | --- | --- | --- |'] + [
                f'| {d["name"]} | {LIGHT[d["light"]][0]} {LIGHT[d["light"]][1]} | {d["head"]} | {d["text"]} |' for d in dims(r)]
        elif key == 'why':
            why = n.get('why') or {}
            lines += ['### 为什么是这个站点', '', text_or(why.get('site_reason'), text_or(((read(r['root'], 'S1_选品板块/amazon-market-research/market_narrative.yaml').get('summary') or {}).get('site_reason')))), '',
                      f'条件：{text_or(why.get("condition"))}；形态：{text_or(why.get("form"), product)}；货源：{text_or(why.get("supplier"))}。']
        elif key == 'market':
            lines += ['### 市场容量', '', f'类目 Top100 月销 {fmt_int(r["size"].get("top100_units"))} 件；同款 {r["stats"].get("skus", "—")} 款月销 {fmt_int(r["stats"].get("units"))} 件；类目近 12 个月同比 {pct(r["trend"].get("yoy_12m"))}。', '']
            lines += ['| 流量词 | 月搜索 | CPC | 对标款自然位 |', '| --- | --- | --- | --- |'] + [f'| {t["keyword"]} | {fmt_int(t["volume"])} | {money(t["cpc"])} | {t["position"]} |' for t in r['relevant'][:10]]
            lines += ['', text_or((n.get('market') or {}).get('reading'))]
        elif key == 'users':
            u = n.get('users') or {}
            lines += ['### 需求与痛点', '', text_or(u.get('summary'))] + [f'- 场景：{x.get("title")}（{x.get("count", "")}）' for x in listed(u.get('scenes')) if isinstance(x, dict)] + \
                     [f'- 痛点：{x.get("title")} → {x.get("opportunity", "")}' for x in listed(u.get('pains')) if isinstance(x, dict)]
        elif key == 'compete':
            lines += ['### 竞争格局', '', text_or((n.get('compete') or {}).get('reading')), '',
                      f'同款 Top3 占 {pct(r["stats"].get("top3_share"))}，评论中位 {fmt_int(r["stats"].get("review_median"))}。']
        elif key == 'entry':
            e = n.get('entry') or {}
            lines += ['### 切入点与机会', '', text_or(e.get('opportunity'))] + [f'- {x.get("title")}：{x.get("need", "")}' for x in listed(e.get('points')) if isinstance(x, dict)]
            if e.get('summary'):
                lines += ['', '首发方案：' + e['summary']]
        elif key == 'profit':
            lines += [f'售价 {money(price_of(r))}：到岸 {money(shown.get("landed_cost"))}，广告前利润 {money(shown.get("pre_ad_profit"))}（{pct(shown.get("pre_ad_margin"))}），'
                      f'广告后 {money(shown.get("post_ad_profit"))}（{pct(shown.get("post_ad_margin"))}）。口径：' + '；'.join(r['policy']) + '。HTML 版可直接改数重算。'] if r['profit'] else [PENDING]
        elif key == 'promo':
            lines += [text_or((n.get('promo') or {}).get('reading'), '见 HTML 版保本转化率表。')]
        elif key == 'supply':
            lines += [f'已选货源：{r["front"].get("offer_url") or "—"}'] + [f'- {q.get("q") if isinstance(q, dict) else q}' for q in (listed((n.get('supply') or {}).get('questions')) or listed(r['ct'].get('supplier_questions')))[:6]]
        elif key == 'ip':
            lines += ['禁用品牌词：' + '、'.join(name for _, names, _ in r['brands'] for name in names)] + \
                     [f'- {x.get("bad")}：{x.get("why")} → {x.get("good")}' for x in listed((n.get('ip') or {}).get('material_terms')) if isinstance(x, dict)]
        elif key == 'next':
            lines += [f'{i}. {x.get("title")}：{x.get("detail", "")}' for i, x in enumerate([x for x in listed((n.get('next') or {}).get('conditions')) if isinstance(x, dict)], 1)] or [PENDING]
        elif key == 'appendix':
            lines += ['<details><summary>数据来源</summary>', ''] + [f'- {p}' for p, s in (r['model'].get('source_manifest') or {}).items() if s] + ['', '</details>']
        lines.append('')
    return '\n'.join(lines) + '\n'


# ---------------------------------------------------------------- 验收（C1/C8/C9 第 3 版）
def inspect_html(text):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(text, 'html.parser')
    issues = []
    if not text.lstrip().lower().startswith('<!doctype html><html lang="zh-cn"><head><meta charset="utf-8">'):
        issues.append('首行缺UTF-8声明')
    hero = soup.select('section.hero[data-report-version]')
    if len(hero) != 1:
        return issues + ['结论首屏缺失或重复']
    decisions = soup.select('[data-decision]')
    if len(decisions) != 1 or decisions[0].get('data-decision') not in ('invest', 'conditional', 'hold', 'reject'):
        issues.append('缺唯一明确判定')
    if len(re.findall(r'唯一结论\s*[：:]', soup.get_text(' ', strip=True))) != 1:
        issues.append('全页唯一结论缺失或重复')
    if hero[0].select_one('img') is None and hero[0].select_one('.noimg') is None:
        issues.append('缺当前产品主图或缺图说明')
    if len(soup.select('#overview .dim')) != 7:
        issues.append('缺七维度判定卡')
    for key, no, name, _ in CHAPTERS:
        node = soup.find(id=key)
        if node is None or node.get('data-chapter') != no:
            issues.append(f'缺正文章节：{no} {name}')
    links = [a.get('href') for a in soup.select('nav.toc a')]
    if len(links) != len(CHAPTERS):
        issues.append('左侧导航章节数不对')
    for details in soup.select('#appendix details'):
        if details.has_attr('open'):
            issues.append('附录必须默认折叠')
            break
    return issues


def md_issues(text):
    missing = [f'{no} {name}' for _, no, name, _ in CHAPTERS if not re.search(rf'^## {no} {re.escape(name)}\b', text, re.M)]
    return ['缺必备章节：' + x for x in missing] + ([] if re.search(r'唯一结论[：:]\s*\S+', text) else ['缺必备字段：唯一结论'])


# ---------------------------------------------------------------- AI 填写骨架
TEMPLATE_GUIDE = '''# S1 决策报告的判断（第 9 步写，数字不用写，脚本会算）。没写的地方报告显示“待补写”。
# 只写有证据的事；评论原话必须来自已留存评论。light 取 good / warn / crit。
# 紧凑案例：r01_native.py execute --root <产品根> --step 9，inputs 放 {"S1_选品板块/_阶段总览/decision_narrative.yaml": {"yaml": {...}}}。
'''


def template(root):
    root = Path(root)
    facts = read(root, 'S1_选品板块/amazon-market-research/01_market_facts.json')
    overview = facts.get('market_overview') or {}
    asin, terms = traffic_terms(overview, {})
    pool = pool_rows(overview, facts)
    skeleton = {
        'schema': 'starsky.s1_decision_narrative.v1',
        'verdict_line': '一句话：有没有人买、卡在哪、怎样才能做',
        'verdict_reason': '结论旁的短理由，如：不是没市场，是现在这套货源和包装赚不到钱',
        'dimensions': {k: {'light': 'warn', 'headline': '', 'text': ''} for k, _, _ in DIMS},
        'why': {'condition': '', 'form': '', 'supplier': '', 'site_reason': '', 'solves': ''},
        'market': {'traffic_asin': asin, 'exclude_keywords': ['不是本品需求的词片段，如 riser'], 'trend_asins': [p['asin'] for p in pool[:3]],
                   'lead': '', 'reading': ''},
        'users': {'summary': '', 'scenes': [{'title': '', 'count': '', 'quote': ''}], 'pains': [{'title': '', 'quote': '', 'opportunity': ''}]},
        'compete': {'benchmark_asin': '', 'lead': '', 'reading': ''},
        'entry': {'opportunity': '', 'thresholds': [{'item': '', 'evidence': '', 'ours': '', 'light': 'crit'}],
                  'points': [{'title': '', 'need': '', 'evidence': '', 'cost': '', 'advice': 'first|recommend|must|later|skip'}],
                  'launch': {'variant_logic': '尺寸驱动还是颜色驱动、为什么',
                             'variants': [{'name': '', 'reason': '', 'price': 0, 'buy': None, 'main': True}],
                             'price_options': [{'label': '', 'price': 0, 'buy': None}], 'pricing_note': '',
                             'pricing': [{'stage': '', 'action': ''}], 'first_batch': {'qty': 0, 'reason': ''}},
                  'summary': '', 'boundary': ''},
        'profit': {'monthly_units': None},
        'promo': {'lead': '', 'reading': '', 'tactics': ''},
        'supply': {'lead': '', 'questions': [{'q': '', 'why': ''}], 'alternates': []},
        'ip': {'lead': '', 'own_brands': [], 'certs': [], 'fit_brands': [{'terms': '', 'why': '', 'instead': ''}],
               'material_terms': [{'bad': '', 'why': '', 'good': ''}]},
        'next': {'lead': '', 'conditions': [{'title': '', 'detail': ''}], 'this_week': ''},
    }
    hints = ['# 可用数据（只读参考）：',
             '#  对标款流量词：' + '、'.join(f'{t["keyword"]}({t["volume"]})' for t in terms[:20]),
             '#  同款竞品：' + '、'.join(f'{p["asin"]} {p["brand"]} ${p["price"]} 月销{p["sales"]}' for p in pool[:10])]
    return TEMPLATE_GUIDE + '\n'.join(hints) + '\n' + yaml.safe_dump(skeleton, allow_unicode=True, sort_keys=False)


def check(root):
    n = read(root, NARRATIVE)
    gaps = []
    for path in ('verdict_line', 'why.solves', 'market.reading', 'users.scenes', 'users.pains', 'compete.reading', 'entry.points',
                 'entry.launch.variants', 'entry.launch.first_batch', 'ip.material_terms', 'ip.fit_brands', 'next.conditions'):
        node = n
        for part in path.split('.'):
            node = node.get(part) if isinstance(node, dict) else None
        if node in (None, '', [], {}) or (isinstance(node, list) and not any(isinstance(x, dict) and any(x.values()) or isinstance(x, str) and x for x in node)):
            gaps.append(path)
    return gaps


CSS = r'''
:root{--bg:#f6f5f1;--card:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--line:#e1e0d9;--blue:#2a78d6;--good:#0ca30c;--warn:#fab219;--crit:#d03b3b;--deep:#17324d}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 system-ui,-apple-system,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;padding-left:200px}
.wrap{max-width:1080px;margin:0 auto;padding:28px 24px 80px}
nav.toc{position:fixed;left:0;top:0;bottom:0;width:200px;z-index:5;background:#fcfcfb;border-right:1px solid var(--line);overflow-y:auto}
nav.toc .brand{padding:22px 20px 10px;font-size:12px;letter-spacing:.08em;color:var(--muted)}
nav.toc .brand b{display:block;font-size:14px;letter-spacing:0;color:var(--ink);margin-top:4px;line-height:1.4}
nav.toc .in{display:flex;flex-direction:column;padding:6px 10px 20px;font-size:14px}
nav.toc a{color:var(--ink2);text-decoration:none;padding:8px 12px;border-radius:8px;border-left:3px solid transparent}
nav.toc a:hover{background:#f0efec;color:var(--ink)}
nav.toc a.on{background:#eef4fc;color:#1c5cab;font-weight:600;border-left-color:#2a78d6}
nav.toc .nn{display:inline-block;width:26px;color:var(--muted);font-variant-numeric:tabular-nums}nav.toc a.on .nn{color:#2a78d6}
.srclink{margin:12px 0 0;font-size:14px}.srclink a{color:#1c5cab;font-weight:600}
.hero{display:grid;grid-template-columns:240px 1fr;gap:28px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:26px;scroll-margin-top:20px}
.hero img{width:100%;border-radius:10px;border:1px solid var(--line)}.noimg{display:grid;place-items:center;min-height:200px;background:#f0efec;border-radius:10px;color:#7a5200;padding:12px}
.kicker{font-size:12.5px;letter-spacing:.08em;color:var(--muted)}
h1{font-size:26px;line-height:1.3;margin:6px 0 10px}
.verdict{display:inline-flex;align-items:center;flex-wrap:wrap;gap:10px;padding:8px 14px;border-radius:999px;background:#fff4dc;border:1px solid #f3d38b;font-weight:700}
.verdict small{font-weight:500;color:var(--ink2)}
.oneline{font-size:18px;margin:14px 0 6px;line-height:1.55}
.facts{display:flex;flex-wrap:wrap;gap:8px 18px;margin-top:12px;font-size:13px;color:var(--ink2)}.facts b{color:var(--ink)}
h2{font-size:21px;margin:44px 0 6px;scroll-margin-top:20px}
h2 .no{color:var(--muted);font-weight:500;margin-right:10px;font-variant-numeric:tabular-nums}
h2 .q{font-size:15px;font-weight:500;color:var(--ink2);margin-left:12px}h2 .q:before{content:'· ';color:var(--muted)}
.chapter{scroll-margin-top:20px}
.lead{color:var(--ink2);margin:0 0 16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px 22px;margin:14px 0}
.dims{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:12px;margin-top:16px}
.dim{display:block;text-decoration:none;color:inherit;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;border-left:5px solid var(--line)}
.dim:hover{border-color:#c3c2b7}.dim.good{border-left-color:var(--good)}.dim.warn{border-left-color:var(--warn)}.dim.crit{border-left-color:var(--crit)}
.dim .t{display:flex;justify-content:space-between;align-items:baseline;gap:8px}.dim .t b{font-size:16px}.dim .st{font-size:12.5px;color:var(--ink2);white-space:nowrap}
.dim .h{font-weight:600;margin:4px 0 2px}.dim p{margin:0;font-size:13.5px;color:var(--ink2)}
.why{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.why div{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.why b{display:block;margin-bottom:2px}.why span{font-size:13.5px;color:var(--ink2)}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:14px 0}
.stat{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.stat .v{font-size:26px;font-weight:700;line-height:1.2}.stat .k{font-size:13px;color:var(--ink2)}.stat .n{font-size:12px;color:var(--muted);margin-top:4px}
.callout{border-radius:10px;padding:12px 16px;margin:14px 0;font-size:14.5px}
.callout.key{background:#eef4fc;border:1px solid #cde2fb}.callout.risk{background:#fdf0ef;border:1px solid #f3c9c8}
.callout.note{background:#f4f3ee;border:1px solid var(--line);color:var(--ink2);font-size:13.5px}
.chart{width:100%;height:auto;display:block}.chartwrap{position:relative}
.tick{font-size:11px;fill:#898781;font-variant-numeric:tabular-nums}.dlabel{font-size:12px;fill:#0b0b0b;font-weight:600}
.legend{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:13px;color:var(--ink2);margin-bottom:6px}
.lg i{display:inline-block;width:14px;height:3px;border-radius:2px;vertical-align:middle;margin-right:6px}
.hbars{display:grid;gap:7px}
.hb{display:grid;grid-template-columns:230px 1fr 80px 210px;gap:10px;align-items:center;font-size:13.5px}
.hb-l{color:var(--ink)}.hb-t{height:12px;background:#f0efec;border-radius:4px;overflow:hidden}
.hb-f{height:100%;border-radius:0 4px 4px 0}.hb-v{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}.hb-n{color:var(--muted);font-size:12.5px}
.money .hb{grid-template-columns:240px 1fr 70px 50px}.hb.total{border-top:1px solid var(--line);padding-top:8px;margin-top:4px}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th{text-align:left;font-weight:600;color:var(--ink2);border-bottom:1px solid #c3c2b7;padding:8px}
td{border-bottom:1px solid var(--line);padding:8px;vertical-align:top;overflow-wrap:anywhere}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}tr.me td{background:#eef4fc}
.chip{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;white-space:nowrap}
.chip.good{background:#e3f5e3;color:#006300}.chip.warn{background:#fff3d6;color:#7a5200}.chip.crit{background:#fbe3e3;color:#a12626}
.two{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.quote{border-left:3px solid #cde2fb;padding:4px 12px;margin:8px 0;color:var(--ink2);font-size:13.5px}.quote b{color:var(--ink)}
details{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:10px 0}
details summary{cursor:pointer;padding:11px 16px;font-weight:600;color:var(--ink2)}details>div{padding:0 16px 14px}
.steps{counter-reset:s;list-style:none;padding:0;margin:0}
.steps li{counter-increment:s;position:relative;padding:10px 12px 10px 46px;border-bottom:1px solid var(--line)}
.steps li:before{content:counter(s);position:absolute;left:10px;top:10px;width:24px;height:24px;border-radius:50%;background:var(--deep);color:#fff;font-size:13px;text-align:center;line-height:24px}
.steps li small{display:block;color:var(--muted)}
h3.sub{font-size:15px;margin:18px 0 8px}
#tip{position:fixed;pointer-events:none;background:#0b0b0b;color:#fff;font-size:12.5px;padding:7px 10px;border-radius:6px;z-index:20;display:none;max-width:320px;line-height:1.5}
.calc-in{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:0 16px;margin-top:12px}
.calc-in>summary{cursor:pointer;padding:13px 0;font-weight:600;color:#1c5cab;display:flex;justify-content:space-between;gap:10px;list-style:none}
.calc-in>summary::-webkit-details-marker{display:none}.calc-in[open]>summary{border-bottom:1px solid var(--line);margin-bottom:10px}.calc-in[open]{padding-bottom:12px}
#c_changed{font-weight:500;font-size:12.5px;color:#7a5200}
.calc-in fieldset{border:0;border-top:1px solid var(--line);margin:10px 0 0;padding:10px 0 0}.calc-in legend{font-weight:700;font-size:13.5px;padding:0 6px 0 0}
.calc-in .grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px 12px}
.calc-in .f label{display:block;font-size:12px;color:var(--ink2)}
.calc-in .in{display:flex;align-items:center;border:1px solid #d6d5cc;border-radius:7px;background:#fff;overflow:hidden}
.calc-in .in:focus-within{border-color:var(--blue);box-shadow:0 0 0 2px #cde2fb}
.calc-in input{flex:1;min-width:0;border:0;padding:6px 8px;font:inherit;font-size:14px;font-variant-numeric:tabular-nums;background:transparent}
.calc-in .u{font-size:11.5px;color:var(--muted);padding:0 8px;white-space:nowrap}
.calc-in select{width:100%;padding:6px 8px;border:1px solid #d6d5cc;border-radius:7px;font:inherit;font-size:13.5px;background:#fff}
.calc-in .src{font-size:11px;color:var(--muted);margin-top:2px;line-height:1.35}.calc-in .f.changed .in{border-color:#eda100;background:#fffaf0}
.calc-bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.calc-bar button,.purchase button{font:inherit;font-size:13px;padding:6px 12px;border-radius:7px;border:1px solid #c3c2b7;background:#fff;cursor:pointer}
#c_msg{font-size:12px;color:var(--muted)}
.kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.kpi .k{font-size:12.5px;color:var(--ink2)}.kpi .v{font-size:22px;font-weight:700;line-height:1.25;font-variant-numeric:tabular-nums}.kpi .n{font-size:12px;color:var(--muted)}
.verdictline{margin:12px 0 0;padding:10px 14px;border-radius:10px;font-size:14px;border:1px solid var(--line);background:#f4f3ee}
.verdictline.good{background:#e9f6e9;border-color:#bfe3bf}.verdictline.warn{background:#fff6e0;border-color:#f3d38b}.verdictline.crit{background:#fdf0ef;border-color:#f3c9c8}
.purchase fieldset{border:0;padding:0;margin:8px 0}.purchase legend{font-weight:700}.purchase-item{display:block;font-size:13.5px}
.foot{color:var(--muted);font-size:12.5px;margin-top:20px}
@media (max-width:1100px){.calc-in .grid{grid-template-columns:1fr 1fr}.kpis{grid-template-columns:1fr 1fr}}
@media (max-width:900px){body{padding-left:0}nav.toc{position:sticky;bottom:auto;width:auto;height:auto;border-right:0;border-bottom:1px solid var(--line)}nav.toc .brand{display:none}nav.toc .in{flex-direction:row;overflow-x:auto;white-space:nowrap;padding:6px 10px}}
@media (max-width:760px){.hero{grid-template-columns:1fr}.why,.stats,.two{grid-template-columns:1fr 1fr}.hb{grid-template-columns:1fr 60px}.hb-t,.hb-n{display:none}}
@media print{body{padding-left:0}nav.toc{display:none}details{break-inside:avoid}details:not([open])>div{display:block}}
'''

JS = r'''
const links=[...document.querySelectorAll('nav.toc a')];const targets=links.map(a=>document.querySelector(a.getAttribute('href')));
function spy(){let k=0;targets.forEach((el,i)=>{if(el&&el.getBoundingClientRect().top<140)k=i});links.forEach((a,i)=>a.classList.toggle('on',i===k))}
addEventListener('scroll',spy,{passive:true});spy();
const tip=document.getElementById('tip');
function show(e,t){tip.innerHTML=t;tip.style.display='block';tip.style.left=Math.min(e.clientX+14,innerWidth-330)+'px';tip.style.top=(e.clientY+14)+'px'}
function hide(){tip.style.display='none'}
document.querySelectorAll('[data-tip]').forEach(el=>{el.addEventListener('mousemove',e=>show(e,el.dataset.tip));el.addEventListener('mouseleave',hide)});
document.querySelectorAll('rect.hit').forEach(r=>{
 const svg=r.ownerSVGElement,xh=svg.querySelector('.xhair'),m=JSON.parse(r.dataset.months),s=JSON.parse(r.dataset.series),n=JSON.parse(r.dataset.names),c=JSON.parse(r.dataset.colors),[L,R]=JSON.parse(r.dataset.geo);
 const keys=Object.keys(s);
 r.addEventListener('mousemove',e=>{const pt=svg.createSVGPoint();pt.x=e.clientX;pt.y=e.clientY;const p=pt.matrixTransform(svg.getScreenCTM().inverse());
  const i=Math.max(0,Math.min(m.length-1,Math.round((p.x-L)/(R-L)*(m.length-1))));const X=L+i*(R-L)/Math.max(m.length-1,1);
  xh.setAttribute('x1',X);xh.setAttribute('x2',X);xh.setAttribute('visibility','visible');
  show(e,'<b>'+m[i].slice(0,4)+'-'+m[i].slice(4)+'</b><br>'+keys.map((k,j)=>'<span style="color:'+c[j]+'">■</span> '+n[j]+'：'+(s[k][i]??'—').toLocaleString()+' 件').join('<br>'))});
 r.addEventListener('mouseleave',()=>{xh.setAttribute('visibility','hidden');hide()})});
(function(){
const D=window.CALC_DEFAULTS;if(!D)return;
const KEY='s1calc:'+window.CALC_CASE,CUFT=28316.846592,OZ=0.028349523125,MIN={US:.30,UK:.25,DE:.30,JP:30,AU:.30}[window.CALC_MARKET]??.30;
const [GM,GG]=window.CALC_GATES;
const $=id=>document.getElementById('c_'+id);
const editable=[...document.querySelectorAll('.calc-in .f[data-k]')].map(f=>f.dataset.k);
function fba(d,P){const T=window.CALC_FBA;if(window.CALC_MARKET!=='US'||!T)return null;const date=window.CALC_DATE;
 if(!(d.L>0&&d.W>0&&d.H>0&&d.g>0)||!(T.valid_from<=date&&date<=T.valid_until))return null;
 const [l,w,h]=[d.L,d.W,d.H].map(v=>v/2.54).sort((a,b)=>b-a),oz=d.g/1000/OZ;let key,ship,tier;
 if(l<=15&&w<=12&&h<=.75&&oz<=16){key='small_standard_oz';ship=oz;tier='small'}else if(l<=18&&w<=14&&h<=8&&oz<=320){key='large_standard_oz';ship=Math.max(oz,l*w*h/139*16);tier='large'}else return null;
 const peak=T.peak_from<=date&&date<=T.valid_until,col=P<10?1:P<=50?2:3;let base=null;
 for(const row of T[peak?'peak':'non_peak'][key]){if(ship<=row[0]+1e-9){base=row[col];break}}
 const o=T.large_standard_over_3lb;if(base===null&&tier==='large'&&o&&o.from_oz<ship&&ship<=o.max_oz+1e-9)base=o[peak?'peak':'non_peak'][col-1]+Math.ceil((ship-o.from_oz)/4-1e-9)*o.per_4oz;
 if(base===null)return null;return base*(1+(date>=T.fuel_surcharge_from?T.fuel_surcharge_rate:0))}
function calc(d){
 const fx=d.fx,qty=d.qty,purchase=d.buy*(1+d.tax/100),kg=d.g/1000,unit=[d.L,d.W,d.H],vol=unit[0]*unit[1]*unit[2];
 const carton=d.pcs>0&&d.cL>0&&d.cW>0&&d.cH>0&&d.cKg>0,cv=d.cL*d.cW*d.cH;
 const leg=carton?(d.fmode==='cbm'?cv/1e6*d.frate/d.pcs:Math.max(d.cKg,cv/d.div)*d.frate/d.pcs):(d.fmode==='cbm'?vol/1e6*d.frate:Math.max(kg,vol/d.div)*d.frate);
 const P=d.price*(1-d.coupon/100),US=window.CALC_MARKET==='US';
 const parts={factory:purchase/fx+d.pack/fx,domestic:d.dom/qty/fx,oneoff:d.oneoff/qty/fx,international:leg/fx,customs:d.customs/qty/fx,clearance:(US?0:d.clear)/qty/fx,delivery:d.deliv/qty/fx};
 const goods=(purchase+d.pack)/fx,duty=d.dutyInc?0:goods*d.duty/100;parts.duty=duty;
 parts.import_vat=(!US&&!d.dutyInc&&!d.vatReg)?(goods+leg/fx+duty)*d.vat/100:0;
 const landed=Object.values(parts).reduce((a,b)=>a+b,0);
 const F=(d.fba===null||d.fba===undefined||d.fba==='')?fba(d,P):d.fba;
 const comm=Math.max(d.referralMin??MIN,P*d.comm/100),salesVat=(!US&&d.vatReg)?P/(1+d.vat/100)*(d.vat/100):0;
 const ft3=vol/CUFT,storage=ft3*d.srate*d.months,lts=ft3*d.ltsR*d.ltsM;
 let awd=0;if(US&&d.awd){const n=d.pcs||1,box=carton?cv/CUFT:ft3*n;awd=(box*(d.awdS*d.awdM+d.awdT)+d.awdIn+d.awdOut)/n}
 const couponFee=d.coupon&&qty?d.couponFixed/qty+P*d.couponRate/100:0;
 const amazon={fba:F??0,comm,inbound:d.inbound,storage,lts,awd,other:d.other,coupon:couponFee};
 const returns=d.ret/100*((1-d.resell/100)*landed+(F??0)+Math.min(5,comm*.2))+P*d.reserve/100;
 const pre=P-salesVat-landed-Object.values(amazon).reduce((a,b)=>a+b,0)-returns;
 const ad=d.admode==='tacos'?P*d.tacos/100:(d.cvr>0?d.cpc/(d.cvr/100)*(d.adshare??100)/100:0);
 return {P,parts,landed,amazon,returns,pre,ad,post:pre-ad,fba:F,salesVat};
}
function read(){const d=Object.assign({},D);d.admode=$('admode').value;editable.forEach(k=>{const raw=$(k).value;if(k==='fba'&&raw.trim()===''){d.fba=null;return}const v=parseFloat(raw);d[k]=isFinite(v)?v:0});
 if(!(d.qty>0))d.qty=1;if(!(d.fx>0))d.fx=D.fx||1;if(!(d.div>0))d.div=6000;return d}
function write(d){editable.forEach(k=>{$(k).value=(d[k]===null||d[k]===undefined)?'':d[k]});$('admode').value=d.admode||'tacos'}
const money=v=>(v<0?'−$':'$')+Math.abs(v).toFixed(2),pct=v=>(100*v).toFixed(1)+'%';
function lvl(r){const m=r.pre/r.P;return m<GM?'crit':m<GG?'warn':'good'}
const CH={good:'<span class="chip good">🟢 过线</span>',warn:'<span class="chip warn">🟡 勉强</span>',crit:'<span class="chip crit">🔴 不过线</span>'};
function render(){
 const d=read(),r=calc(d);
 let n=0;editable.forEach(k=>{const f=document.querySelector('.calc-in .f[data-k="'+k+'"]'),c=k==='fba'?String(d.fba)!==String(D.fba):Math.abs(d[k]-D[k])>1e-9;f.classList.toggle('changed',c);if(c)n++});if(d.admode!==D.admode)n++;
 document.getElementById('c_changed').textContent=n?('已改 '+n+' 项，结果按你的数计算'):'当前为报告原值';
 const set=(id,h)=>document.getElementById('o_'+id).innerHTML=h;
 set('landed',money(r.landed));set('landed_n','约 ¥'+(r.landed*d.fx).toFixed(2)+' / 件');
 set('pre',money(r.pre));set('pre_n','毛利率 '+pct(r.pre/r.P));set('post',money(r.post));set('post_n','净利率 '+pct(r.post/r.P));
 const be=r.pre>0&&d.cpc?d.cpc/r.pre:null;set('ad',money(r.ad));set('ad_n',be?('按 CPC $'+(+d.cpc).toFixed(2)+' 保本需转化 '+pct(be)):'广告前已亏或缺 CPC');
 set('month',money(r.post*d.monthly));set('month_n',(+d.monthly).toLocaleString()+' 件/月 × 广告后利润');
 set('cap',money(r.landed*d.qty));set('cap_n',(+d.qty).toLocaleString()+' 件 · 约 ¥'+Math.round(r.landed*d.qty*d.fx).toLocaleString());
 if(r.fba===null)set('landed_n','FBA 算不出：请填后台值');
 const fbaNote=document.querySelector('.calc-in .f[data-k="fba"] .src');if(fbaNote&&(d.fba===null))fbaNote.textContent='自动：'+(r.fba===null?'尺寸重量不全或超出费率表，请手填':money(r.fba))+'；上架后用后台实际值覆盖';
 const L=lvl(r),vl=document.getElementById('o_verdict');vl.className='verdictline '+L;
 vl.innerHTML={good:'🟢 广告前毛利 ≥ '+GG*100+'%：利润这关可以过，广告占到 20% 左右仍有钱赚。',warn:'🟡 广告前毛利 '+GM*100+'–'+GG*100+'%：勉强，推新期广告一高就亏，要靠提价或降本补。',crit:'🔴 广告前毛利低于 '+GM*100+'%：按选品标准（35–40%）这组数不过线。'}[L];
 const rows=[['出厂综合成本（采购+包装贴标）',r.parts.factory],['头程·国内段（厂家→货代）',r.parts.domestic],['头程·国际段',r.parts.international],
  ['报关 + 清关（按票分摊）',r.parts.customs+r.parts.clearance],['打样检测 / 派送分摊',r.parts.oneoff+r.parts.delivery],['进口税（VAT）',r.parts.import_vat+r.salesVat],
  ['FBA 配送费（尾程）',r.amazon.fba],['平台佣金',r.amazon.comm],['入库 + 仓储 + 其他',r.amazon.inbound+r.amazon.storage+r.amazon.lts+r.amazon.awd+r.amazon.other+r.amazon.coupon],
  ['退货与额外预留',r.returns],['广告',r.ad]];
 if(r.parts.duty>0.005)rows.splice(5,0,['进口关税',r.parts.duty]);
 const scale=Math.max(...rows.map(x=>x[1]),Math.abs(r.post),1);let h='<div class="hbars money">';
 rows.forEach(([l,v])=>{if(Math.abs(v)<0.005&&!/国内段/.test(l))return;h+='<div class="hb" data-tip="'+l+'：'+money(v)+'（占售价 '+pct(v/r.P)+'）"><div class="hb-l">'+l+'</div><div class="hb-t"><div class="hb-f" style="width:'+(100*Math.max(v,0)/scale).toFixed(1)+'%;background:'+(/FBA/.test(l)?'#eb6834':'#2a78d6')+'"></div></div><div class="hb-v">'+money(v)+'</div><div class="hb-n">'+(100*v/r.P).toFixed(0)+'%</div></div>'});
 h+='<div class="hb total"><div class="hb-l"><b>到手利润（广告后）</b></div><div class="hb-t"><div class="hb-f" style="width:'+(100*Math.max(r.post,0)/scale).toFixed(1)+'%;background:'+(r.post>0?'#0ca30c':'#d03b3b')+'"></div></div><div class="hb-v"><b>'+money(r.post)+'</b></div><div class="hb-n"><b>'+pct(r.post/r.P)+'</b></div></div></div>';
 set('bars',h);
 const lad=window.CALC_LADDER.slice();if(!lad.some(x=>Math.abs(x[0]-d.price)<1e-9))lad.push([d.price,'你填的售价']);lad.sort((a,b)=>a[0]-b[0]);
 set('ladder',lad.map(([p,tag])=>{const x=calc(Object.assign({},d,{price:p}));return '<tr'+(Math.abs(p-d.price)<1e-9?' class="me"':'')+'><td>$'+p.toFixed(2)+'</td><td>'+tag+'</td><td class="num">'+money(x.pre)+'</td><td class="num">'+pct(x.pre/x.P)+'</td><td class="num">'+money(x.post)+'</td><td class="num">'+pct(x.post/x.P)+'</td><td>'+CH[lvl(x)]+'</td></tr>'}).join(''));
 document.querySelectorAll('#o_bars [data-tip]').forEach(el=>{el.addEventListener('mousemove',e=>show(e,el.dataset.tip));el.addEventListener('mouseleave',hide)});
 try{localStorage.setItem(KEY,JSON.stringify(d))}catch(e){}
}
window.S1_CALC=calc;
let saved=null;try{saved=JSON.parse(localStorage.getItem(KEY)||'null')}catch(e){}
write(Object.assign({},D,saved||{}));if(saved)document.getElementById('c_msg').textContent='已载入你上次改过的数';
document.querySelectorAll('.calc-in input,.calc-in select').forEach(el=>el.addEventListener('input',render));
$('reset').addEventListener('click',()=>{try{localStorage.removeItem(KEY)}catch(e){}write(D);render();document.getElementById('c_msg').textContent='已恢复报告原值'});
$('copy').addEventListener('click',()=>{const txt=JSON.stringify(read(),null,1);(navigator.clipboard?navigator.clipboard.writeText(txt):Promise.reject()).then(()=>document.getElementById('c_msg').textContent='参数已复制，可发给 AI 或填进毛利计算器',()=>prompt('复制下面的参数',txt))});
render();
})();
'''


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('action', choices=('template', 'check'))
    parser.add_argument('root', type=Path)
    args = parser.parse_args(argv)
    if args.action == 'template':
        print(template(args.root))
        return 0
    gaps = check(args.root)
    print(json.dumps({'ok': not gaps, 'missing': gaps, 'file': NARRATIVE.as_posix()}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
