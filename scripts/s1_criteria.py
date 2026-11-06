"""S1 结构化判据的原生派生（PL-02）。

十个判据里，凡是能由已落盘的市场事实、成本模型、合规尺子直接算出的，由本模块确定性地算出，不再要求助手手写：

    monthly_trend  listing_age  normalized_unit_price  cohort_comparison  coverage_downgrade
    allowable_landed_cost  unknown_attributes  latest_order_date

两个判断型判据 strongest_counterargument、pass_hold_stop 需要有人站在“不做”的角度下判断，永远不在这里编造，
仍由助手或用户写进 01_product_profile.yaml 的 market_criteria。

铁律（与 SKILL 规则 5 一致）：
- 每项都是 {status: available|not_calculable|pending, source, value, reason}；source 必须是相对证据路径；
- 数据不够就写具体 reason（缺什么、差多少、下一步补什么），不杜撰数值；
- 同一份输入永远得到同一份结果：不读系统时间，日期基准只用案例里已记录的观测日期；
- 作者在 market_criteria 里亲手写的条目永远优先（merge_criteria），要让本模块接管就删掉那个键。
"""
from __future__ import annotations

import math
import re
import statistics
from datetime import date, timedelta
from pathlib import Path

import yaml

KEYS = ('strongest_counterargument', 'pass_hold_stop', 'allowable_landed_cost', 'coverage_downgrade',
        'unknown_attributes', 'normalized_unit_price', 'cohort_comparison', 'listing_age', 'monthly_trend',
        'latest_order_date')
JUDGMENT_KEYS = ('strongest_counterargument', 'pass_hold_stop')
NATIVE_KEYS = tuple(k for k in KEYS if k not in JUDGMENT_KEYS)
MIN_SAMPLE = 3  # 代表样本少于 3 个不出分布、分位或分组对比
MARKET = Path('S1_选品板块/amazon-market-research')
FACTS_REL = (MARKET / '01_market_facts.json').as_posix()
COST_MODEL_REL = (MARKET / '05_cost_model.yaml').as_posix()
RULER_REL = 'S1_选品板块/amazon-ip-risk-assessment/04A_compliance_ruler.yaml'
DAYS_PER_MONTH = 30.4375
STATUS_LABEL = {'available': '已算出', 'pending': '待补证', 'not_calculable': '不可计算'}


# ───────────────────────── 基础工具 ─────────────────────────
def _num(value, positive=False):
    if isinstance(value, dict):
        value = value.get('value')
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 and (not positive or number > 0) else None


def _quantile(values, q):
    values = sorted(values)
    if not values:
        return None
    pos = (len(values) - 1) * q
    low = int(pos)
    high = min(low + 1, len(values) - 1)
    return round(values[low] + (values[high] - values[low]) * (pos - low), 2)


def _median(values):
    values = [v for v in values if v is not None]
    return round(statistics.median(values), 2) if values else None


def _day(text):
    match = re.search(r'(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})', str(text or ''))
    if not match:
        return None
    try:
        return date(int(match[1]), int(match[2]), int(match[3]))
    except ValueError:
        return None


def _field_label(key):
    """成本引擎输入键的中文名（复用引擎自己的 FIELD_LABELS，取不到就原样返回键名）。"""
    try:
        from s1_cost_engine import FIELD_LABELS
        return FIELD_LABELS.get(key, key)
    except Exception:  # 独立运行或引擎不可导入时降级，不影响判据本身
        return key


def _entry(status, source, *, value=None, reason=None, summary=None, **extra):
    item = {'status': status, 'source': source, 'origin': 'native'}
    if value is not None:
        item['value'] = value
    if reason:
        item['reason'] = reason
    item['summary'] = summary or (f'{STATUS_LABEL[status]}：{reason}' if reason else STATUS_LABEL[status])
    item.update(extra)
    return item


def criterion_text(item):
    """给报告表格用的一行可读文字；不把整个字典倒进读者眼前。"""
    if not isinstance(item, dict):
        return item
    if item.get('summary'):
        return item['summary']
    if item.get('status') == 'available':
        return str(item.get('value'))
    return f"{STATUS_LABEL.get(item.get('status'), item.get('status'))}：{item.get('reason') or '未说明原因'}"


def _reps(facts):
    return [r for r in (facts.get('representatives') or facts.get('competitors') or []) if isinstance(r, dict)]


def reference_date(facts):
    """日期基准：竞品快照的观测日期（已记录在案例里），绝不用系统当前时间。"""
    days = [_day(r.get('observed_at')) for r in (facts.get('competitors') or []) + (facts.get('representatives') or [])
            if isinstance(r, dict)]
    days = [d for d in days if d]
    return max(days) if days else None


# ───────────────────────── 月度趋势 ─────────────────────────
def _trend_rows(trend):
    rows = {}
    for item in trend or []:
        if isinstance(item, dict):
            month, value = str(item.get('month') or '')[:7], _num(item.get('sales', item.get('value')))
        elif isinstance(item, str):
            month, _, raw = item.partition('=')
            month, value = month.strip()[:7], _num(raw)
        else:
            continue
        if re.fullmatch(r'\d{4}-\d{2}', month) and value is not None:
            rows[month] = value
    return sorted(rows.items())


def _month_gaps(months):
    if not months:
        return []
    year, month = int(months[0][:4]), int(months[0][5:])
    last = (int(months[-1][:4]), int(months[-1][5:]))
    have, gaps = set(months), []
    while (year, month) <= last:
        key = f'{year:04d}-{month:02d}'
        if key not in have:
            gaps.append(key)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return gaps


def monthly_trend(facts):
    coverage = (facts.get('coverage') or {}).get('monthly_trend') or {}
    source = coverage.get('sources') or [FACTS_REL]
    rows = _trend_rows(facts.get('monthly_trend'))
    if not rows:
        return _entry('pending', source, reason='没有取得月度趋势数据；需要类目或同类趋势回包补足连续 12 个月')
    gaps = _month_gaps([m for m, _ in rows])
    if len(rows) < 12 or gaps:
        detail = f'月度趋势仅 {len(rows)}/12 个月' + (f'，缺 {"、".join(gaps[:6])}' if gaps else '')
        return _entry('pending', source, reason=detail + '；补足连续 12 个月后重算')
    peak, trough = max(rows, key=lambda r: r[1]), min(rows, key=lambda r: r[1])
    total = sum(v for _, v in rows)
    top3 = round(sum(sorted((v for _, v in rows), reverse=True)[:3]) / total, 3) if total > 0 else None
    ratio = round(peak[1] / trough[1], 2) if trough[1] > 0 else None
    value = {'months': len(rows), 'first_month': rows[0][0], 'last_month': rows[-1][0],
             'peak_month': peak[0], 'peak_value': peak[1], 'trough_month': trough[0], 'trough_value': trough[1],
             'peak_to_trough_ratio': ratio, 'top3_months_share': top3,
             'series': [{'month': m, 'value': v} for m, v in rows]}
    text = f'{len(rows)} 个月（{rows[0][0]} 至 {rows[-1][0]}）；高点 {peak[0]}，低点 {trough[0]}'
    text += f'，峰谷比 {ratio}' if ratio else '，低点为 0 无法算峰谷比'
    text += f'；最高三个月占全期 {top3:.0%}' if top3 is not None else ''
    return _entry('available', source, value=value, summary=text)


# ───────────────────────── 上架年限与新老分层 ─────────────────────────
def _aged_rows(facts):
    ref = reference_date(facts)
    rows = []
    for rep in _reps(facts):
        listed = _day(rep.get('online_date') or rep.get('listing_date'))
        if listed and ref and listed <= ref:
            rows.append({'asin': rep.get('asin'), 'online_date': listed.isoformat(),
                         'age_months': round((ref - listed).days / DAYS_PER_MONTH, 1), 'rep': rep})
    return ref, rows


def _row_sources(reps):
    sources = sorted({str(r.get('source')) for r in reps if r.get('source')})
    return sources or [FACTS_REL]


def listing_age(facts):
    ref, rows = _aged_rows(facts)
    reps = _reps(facts)
    source = _row_sources(reps)
    if ref is None:
        return _entry('pending', source, reason='竞品快照没有记录观测日期，无法确定上架年限的计算基准；补取带日期的 product_detail 回包')
    if len(rows) < MIN_SAMPLE:
        return _entry('pending', source, reason=f'仅 {len(rows)}/{len(reps)} 个代表样本有有效上架日期，至少需要 {MIN_SAMPLE} 个；补取 product_detail 回包')
    ages = [r['age_months'] for r in rows]
    buckets = {'6个月内': sum(a <= 6 for a in ages), '6到12个月': sum(6 < a <= 12 for a in ages),
               '12到24个月': sum(12 < a <= 24 for a in ages), '24个月以上': sum(a > 24 for a in ages)}
    newest, oldest = min(rows, key=lambda r: r['age_months']), max(rows, key=lambda r: r['age_months'])
    share = round(sum(a <= 12 for a in ages) / len(ages), 3)
    value = {'reference_date': ref.isoformat(), 'sample': len(rows), 'of': len(reps), 'median_age_months': _median(ages),
             'newest': {k: newest[k] for k in ('asin', 'online_date', 'age_months')},
             'oldest': {k: oldest[k] for k in ('asin', 'online_date', 'age_months')},
             'share_within_12_months': share, 'buckets': buckets,
             'rows': [{k: r[k] for k in ('asin', 'online_date', 'age_months')} for r in rows]}
    text = (f'{len(rows)}/{len(reps)} 个样本有上架日期（以 {ref.isoformat()} 为基准）；中位 {value["median_age_months"]} 个月，'
            f'12 个月内新品占 {share:.0%}；最新 {newest["online_date"]}，最早 {oldest["online_date"]}')
    return _entry('available', source, value=value, summary=text)


def cohort_comparison(facts, cutoff_months=12):
    ref, rows = _aged_rows(facts)
    aged = listing_age(facts)
    if aged['status'] != 'available':
        return _entry('pending', aged['source'], reason='新老分层依赖上架年限：' + str(aged.get('reason')))
    cohorts = {'new': [r for r in rows if r['age_months'] <= cutoff_months],
               'established': [r for r in rows if r['age_months'] > cutoff_months]}
    label = {'new': f'{cutoff_months} 个月内新品', 'established': f'{cutoff_months} 个月以上老品'}
    for name, group in cohorts.items():
        if not group:
            other = 'established' if name == 'new' else 'new'
            return _entry('not_calculable', aged['source'],
                          reason=f'全部 {len(rows)} 个有日期的样本都属于「{label[other]}」，没有「{label[name]}」可对比；需要补取另一组的同类竞品')

    def stats(group):
        reps = [r['rep'] for r in group]
        return {'count': len(group), 'median_monthly_sales': _median([_num(p.get('monthly_sales')) for p in reps]),
                'median_reviews': _median([_num(p.get('reviews')) for p in reps]),
                'median_price_usd': _median([_num(p.get('price_usd'), True) for p in reps])}
    new, old = stats(cohorts['new']), stats(cohorts['established'])
    ratio = round(new['median_monthly_sales'] / old['median_monthly_sales'], 2) \
        if new['median_monthly_sales'] is not None and old['median_monthly_sales'] else None
    small = min(new['count'], old['count']) < MIN_SAMPLE
    value = {'cutoff_months': cutoff_months, 'new': new, 'established': old,
             'new_vs_established_sales_ratio': ratio, 'small_sample': small}
    text = (f'{label["new"]} {new["count"]} 个（月销中位 {new["median_monthly_sales"]}，评论中位 {new["median_reviews"]}）对比 '
            f'{label["established"]} {old["count"]} 个（月销中位 {old["median_monthly_sales"]}，评论中位 {old["median_reviews"]}）')
    text += f'；新品月销约为老品的 {ratio} 倍' if ratio is not None else ''
    text += '；任一组不足 3 个，仅作方向参考' if small else ''
    return _entry('available', aged['source'], value=value, summary=text)


# ───────────────────────── 单价归一 ─────────────────────────
_PACK = (re.compile(r'\bset\s+of\s+(\d{1,2})\b', re.I), re.compile(r'\bpack\s+of\s+(\d{1,2})\b', re.I),
         re.compile(r'(?<![\d.x×*])(\d{1,2})\s*[- ]?\s*pack\b', re.I))


def pack_count(title):
    """从标题读套装数量。只认 set of N / pack of N / N-pack；pcs、pieces 常指配件个数，不据此归一，按 1 件计。"""
    for pattern in _PACK:
        match = pattern.search(str(title or ''))
        if match and 2 <= int(match[1]) <= 24:
            return int(match[1]), 'title'
    return 1, 'default_single'


def normalized_unit_price(facts):
    reps = _reps(facts)
    source = _row_sources(reps)
    rows = []
    for rep in reps:
        price = _num(rep.get('price_usd'), True)
        if price is None:
            continue
        count, how = pack_count(rep.get('title'))
        rows.append({'asin': rep.get('asin'), 'price_usd': price, 'pack_count': count, 'pack_source': how,
                     'unit_price_usd': round(price / count, 2)})
    if len(rows) < MIN_SAMPLE:
        return _entry('pending', source, reason=f'仅 {len(rows)}/{len(reps)} 个代表样本有可用价格，至少需要 {MIN_SAMPLE} 个；补取 product_detail 回包')
    units = [r['unit_price_usd'] for r in rows]
    split = sum(r['pack_count'] > 1 for r in rows)
    value = {'quantity_basis': 1, 'unit': 'USD/件',
             'basis_note': '数量基数为 1 件（空包按 1 件计）；标题明确写 set of / pack of / N-pack 的才按套装数量折算，其余一律按 1 件',
             'sample': len(rows), 'p25': _quantile(units, .25), 'p50': _quantile(units, .5), 'p75': _quantile(units, .75),
             'multipack_listings': split, 'rows': rows}
    text = (f'按 1 件为数量基数（USD/件）：{len(rows)} 个样本，P25 {value["p25"]}、中位 {value["p50"]}、P75 {value["p75"]}'
            + (f'；其中 {split} 个为套装已折算' if split else '；均为单件'))
    return _entry('available', source, value=value, summary=text)


# ───────────────────────── 覆盖降级 ─────────────────────────
def coverage_downgrade(facts):
    coverage = facts.get('coverage')
    if not isinstance(coverage, dict) or not coverage:
        return _entry('not_calculable', [FACTS_REL], reason='事实快照里没有 coverage，无法逐项对照下限；先运行 s1_market_facts.py')
    gaps, met, na = [], [], []
    for key, item in coverage.items():
        status = (item or {}).get('status')
        if status == 'data_gap':
            gaps.append({'key': key, 'observed': item.get('observed'), 'required': item.get('required'),
                         'tool': item.get('tool'), 'reason': item.get('reason')})
        elif status == 'not_applicable':
            na.append(key)
        else:
            met.append(key)
    value = {'downgraded': bool(gaps), 'gaps': gaps, 'met': met, 'not_applicable': na}
    if gaps:
        detail = '、'.join(f'{g["key"]} {g["observed"]}/{g["required"]}' for g in gaps)
        text = f'有 {len(gaps)} 项覆盖未达下限，结论需按缺口降级：{detail}'
    else:
        text = f'{len(met)} 项覆盖均达下限，无需降级'
    return _entry('available', [FACTS_REL], value=value, summary=text)


# ───────────────────────── 允许到岸成本 ─────────────────────────
def _yaml(path):
    try:
        data = yaml.safe_load(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, yaml.YAMLError):
        return None
    return data if isinstance(data, dict) else None


def allowable_landed_cost(root):
    path = Path(root) / COST_MODEL_REL
    if not path.is_file():
        return _entry('pending', [COST_MODEL_REL], reason='成本模型尚未生成（第 6 步综合成本与定价）；生成后重算')
    model = _yaml(path)
    if model is None:
        return _entry('not_calculable', [COST_MODEL_REL], reason='成本模型文件不可读，无法倒推允许到岸成本')
    target = _num(model.get('target_pre_ad_margin'))
    rows = []
    for scenario in model.get('scenarios') or []:  # 旧引擎已经算出的值，直接引用，不重算
        estimates = [scenario] if scenario.get('status') == 'complete' else scenario.get('conditional_cases') or \
            ([scenario['conditional_estimate']] if scenario.get('conditional_estimate') else [])
        for estimate in estimates:
            allowed = _num(estimate.get('allowed_landed_cost', scenario.get('allowed_landed_cost')))
            if allowed is not None:
                rows.append({'price': scenario.get('price'), 'allowed_landed_cost_usd': round(allowed, 2),
                             'basis': estimate.get('case_label') or scenario.get('label') or scenario.get('id'),
                             'origin': 'cost_model.scenarios'})
    unified = model.get('unified_cost') or {}
    skipped = 0
    for scenario in unified.get('price_scenarios') or []:  # 统一引擎：允许到岸 = 售价 − 亚马逊费用 − 退货准备 − 目标毛利×售价
        result = scenario.get('result') or {}
        price, fees, landed = _num(result.get('price'), True), _num(result.get('amazon_fees')), _num(result.get('landed_cost'))
        reserve, profit = _num(result.get('returns_reserve')) or 0.0, result.get('pre_ad_profit')
        if price is None or fees is None or target is None:
            skipped += 1
            continue
        if landed is not None and isinstance(profit, (int, float)) and abs(price - landed - fees - reserve - profit) > 0.02:
            skipped += 1  # 费用口径与广告前利润对不上就不倒推，宁缺勿错
            continue
        allowed = price - fees - reserve - target * price
        rows.append({'price': price, 'allowed_landed_cost_usd': round(allowed, 2), 'current_landed_cost_usd': landed,
                     'headroom_usd': round(allowed - landed, 2) if landed is not None else None,
                     'basis': scenario.get('basis_name'), 'origin': 'unified_cost.price_scenarios'})
    if not rows:
        why = '，且有 %d 个情景因费用字段不全或口径对不上被跳过' % skipped if skipped else ''
        return _entry('not_calculable', [COST_MODEL_REL], reason='成本模型里没有可用的费用字段，无法倒推允许到岸成本' + why)
    verified = (unified.get('main_basis') or {}).get('actual_profit') is not None
    value = {'target_pre_ad_margin': target, 'currency': model.get('currency') or 'USD', 'fees_verified': verified,
             'scenarios': rows, 'skipped_scenarios': skipped,
             'note': None if verified else '费用为条件情景（含未核假设），不是厂家或后台已核实的实际费用'}
    shown = '；'.join(f'售价 {r["price"]} → 允许到岸 {r["allowed_landed_cost_usd"]}' for r in rows[:4])
    text = f'按参考目标广告前毛利 {target:.0%}（可按你的口径改）：{shown}' + ('' if verified else '（条件费用情景，未核实）')
    return _entry('available', [COST_MODEL_REL], value=value, summary=text)


# ───────────────────────── 未知属性 ─────────────────────────
def unknown_attributes(root):
    root = Path(root)
    items, sources, missing_sources = [], [], []
    ruler = _yaml(root / RULER_REL)
    if ruler is not None:
        sources.append(RULER_REL)
        labels = ruler.get('attributes') or {}
        for key in ruler.get('unknown_attributes') or []:
            items.append({'attribute': key, 'label': (labels.get(key) or {}).get('label', key), 'where': '合规尺子（第 8 步）'})
    else:
        missing_sources.append('合规尺子（第 8 步）')
    model = _yaml(root / COST_MODEL_REL)
    if model is not None:
        sources.append(COST_MODEL_REL)
        unified = model.get('unified_cost') or {}
        missing = unified.get('missing')
        state = unified.get('input_state') if isinstance(unified.get('input_state'), dict) else {}
        for entry in missing if isinstance(missing, list) else []:
            key = entry.get('label') if isinstance(entry, dict) else str(entry)
            why = (state.get(key) or {}).get('source') if isinstance(state.get(key), dict) else None
            items.append({'attribute': key, 'label': _field_label(key), 'where': '成本模型（第 6 步）',
                          **({'reason': why} if why else {})})
    else:
        missing_sources.append('成本模型（第 6 步）')
    if not sources:
        return _entry('pending', [RULER_REL, COST_MODEL_REL],
                      reason='合规尺子与成本模型都尚未生成，无法汇总未知属性；第 6、8 步完成后重算')
    value = {'count': len(items), 'items': items, 'not_yet_available': missing_sources}
    text = (f'{len(items)} 项属性待厂家或样品确认：' + '、'.join(i['label'] for i in items[:8]) if items else '已生成的来源里没有未知属性')
    if missing_sources:
        text += '；尚缺来源：' + '、'.join(missing_sources)
    return _entry('available', sources, value=value, summary=text)


# ───────────────────────── 最晚下单日 ─────────────────────────
def latest_order_date(facts, profile=None):
    """旺季窗口起点 − (生产天数 + 头程天数 + 入仓接收缓冲)。三个提前期必须由用户或厂家/货代亲自给出，不推断。"""
    lead = (profile or {}).get('lead_times') if isinstance(profile, dict) else None
    lead = lead if isinstance(lead, dict) else {}
    parts = {'production_days': '厂家生产与发货天数', 'transit_days': '头程运输天数', 'receiving_buffer_days': '入仓接收缓冲天数'}
    given = {key: _num(lead.get(key)) for key in parts}
    trend = monthly_trend(facts)
    ref = reference_date(facts)
    missing = [label for key, label in parts.items() if given[key] is None]
    if trend['status'] != 'available':
        missing.append('旺季窗口（需要完整 12 个月趋势）')
    if ref is None:
        missing.append('日期基准（竞品观测日期）')
    source = [FACTS_REL, (MARKET / '01_product_profile.yaml').as_posix()]
    if missing:
        recompute = ['生产/发货天数（厂家书面确认）', '头程时效（货代确认）', '入仓接收缓冲', '旺季窗口起点']
        known = {}
        if trend['status'] == 'available':
            known['peak_month'] = trend['value']['peak_month']
        return _entry('not_calculable', source, reason='缺少：' + '、'.join(missing) + '；这些由用户、厂家或货代确认后，写入 01_product_profile.yaml 的 lead_times 即可重算',
                      recompute_items=recompute, known=known or None)
    peak_month = int(trend['value']['peak_month'][5:])
    start = date(ref.year, peak_month, 1)
    if start < ref:
        start = date(ref.year + 1, peak_month, 1)
    total = int(sum(given.values()))
    latest = start - timedelta(days=total)
    value = {'peak_window_start': start.isoformat(), 'latest_order_date': latest.isoformat(), 'lead_time_days': total,
             'lead_times': {k: int(v) for k, v in given.items()}, 'reference_date': ref.isoformat(),
             'already_late': latest < ref}
    text = (f'旺季窗口按 {start.isoformat()} 起算，提前期合计 {total} 天，最晚下单日 {latest.isoformat()}'
            + ('（按基准日已经错过，需要评估空运或错峰）' if latest < ref else ''))
    return _entry('available', source, value=value, summary=text)


# ───────────────────────── 汇总与合并 ─────────────────────────
def derive_criteria(facts, root, profile=None):
    """返回 {判据名: 条目}，只含能由数据算（或明确说明缺什么）的八项；判断型两项不在其中。"""
    facts = facts if isinstance(facts, dict) else {}
    return {'monthly_trend': monthly_trend(facts), 'listing_age': listing_age(facts),
            'normalized_unit_price': normalized_unit_price(facts), 'cohort_comparison': cohort_comparison(facts),
            'coverage_downgrade': coverage_downgrade(facts), 'allowable_landed_cost': allowable_landed_cost(root),
            'unknown_attributes': unknown_attributes(root), 'latest_order_date': latest_order_date(facts, profile)}


def merge_criteria(author, native):
    """作者亲手写的条目永远优先；其余用原生派生结果。作者写了非字典的垃圾值不覆盖原生结果。"""
    merged = {key: value for key, value in (native or {}).items()}
    for key, value in (author or {}).items():
        if isinstance(value, dict) or key not in merged:
            merged[key] = value
    return merged
