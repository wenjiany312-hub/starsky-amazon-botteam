"""达到目标毛利的采购价与包装边界（R06.1 AS-01 第 3 批）。

3.0.22 的案例里助手为 05A/05B 现写了一段对成本引擎反复试算的脚本。这里把它做成原生功能：
  · 采购价上限：包装长宽不变，对几档包装厚度 × 几档售价，求“广告前毛利达到目标时，采购单价最高多少”
  · 只压包装：采购价不变，求各售价下包装厚度最多多少
全部通过统一成本引擎逐次试算（不另写成本公式），用主口径——未核费用按上界情景，与成本账一致。
毛利随采购价、包装厚度单调不增（FBA 分档是台阶），所以用二分法找边界，结果向下取整后再复核一次。
这些是询价目标和数学边界，不是厂家报价，也不说明厂家做得到。

成本输入里可选 `target_boundaries: {heights_cm: [...], prices: [...]}` 指定档位；不写时取当前包装厚度的
100% / 75% / 50% / 37.5% / 25% 和价格阶梯里的售价。
"""
from __future__ import annotations

import copy
import math

HEIGHT_SHARES = (1, .75, .5, .375, .25)
BUY_CEILING = 1000.0       # 采购单价搜索上界（元）
STEPS = 20                 # 1000 元区间二分 20 次已细于 0.01 元


def _margin(engine, payload, changes, price):
    trial = copy.deepcopy(payload)
    trial.pop('condition_scenarios', None)
    changes = {**changes, 'price': price}
    trial['inputs'].update(changes)
    states = trial.setdefault('input_state', {})
    for key, value in changes.items():  # 引擎会核对输入与来源记录一致
        if key in states:
            states[key] = {'value': value, 'status': 'scenario', 'source': '目标毛利反推试算，非报价'}
    result = engine.calculate(trial)['main_basis']['result']
    return result.get('pre_ad_margin')


def _search(engine, payload, field, low, high, price, target, extra=None, places=2):
    """在 [low, high] 里找使毛利 ≥ target 的最大 field 值；low 都达不到返回 None。"""
    def ok(value):
        margin = _margin(engine, payload, {**(extra or {}), field: value}, price)
        return margin is not None and margin >= target
    if not ok(low):
        return None
    if ok(high):
        return high
    for _ in range(STEPS):
        middle = (low + high) / 2
        low, high = (middle, high) if ok(middle) else (low, middle)
    unit = 10 ** places
    value = math.floor(low * unit) / unit
    while value > 0 and not ok(value):   # 取整后复核：台阶边上可能刚好差一点
        value = round(value - 1 / unit, places)
    while value + 1 / unit <= high and ok(round(value + 1 / unit, places)):  # 二分下界可能比真实边界低不到一格
        value = round(value + 1 / unit, places)
    return value if ok(value) else None


def boundaries(engine, payload, settings=None):
    settings = settings or {}
    base = engine.calculate({**copy.deepcopy(payload), 'condition_scenarios': []})
    inputs = base['inputs']  # 引擎补齐默认值后的输入（目标毛利默认 40%）
    target = inputs.get('target')
    L, W, H = inputs.get('L'), inputs.get('W'), inputs.get('H')
    if target is None or not all(isinstance(v, (int, float)) and v > 0 for v in (L, W, H)):
        return {'status': 'not_calculable', 'reason': '缺目标毛利或完整包装尺寸，不能反推'}
    target = target / 100
    if base['main_basis']['result'].get('pre_ad_margin') is None:
        return {'status': 'not_calculable', 'reason': '费用不全，算不出广告前毛利，不能反推'}
    ladder = [row['price'] for row in base['blocks']['price_ladder']['scenarios'] if row.get('price')]
    prices = sorted({round(float(p), 2) for p in (settings.get('prices') or ladder)})
    heights = sorted({round(float(h), 2) for h in (settings.get('heights_cm') or [H * s for s in HEIGHT_SHARES])}, reverse=True)
    grid = []
    for height in heights:
        row = {'package_cm': [L, W, height], 'volume_cm3': round(L * W * height, 2), 'reduction': 1 - height / H, 'limits': []}
        for price in prices:
            best = _search(engine, payload, 'buy', 0.0, BUY_CEILING, price, target, extra={'H': height})
            row['limits'].append({'price': price, 'max_buy_cny': best})
        grid.append(row)
    package_only = []
    buy = inputs.get('buy')
    if isinstance(buy, (int, float)):
        for price in prices:
            package_only.append({'price': price, 'buy_cny': buy,
                                 'max_height_cm': _search(engine, payload, 'H', 0.1, H, price, target, extra={'buy': buy})})
    return {'status': 'available', 'target_pre_ad_margin': target, 'basis': base['main_basis']['name'],
            'prices': prices, 'grid': grid, 'package_only': package_only, 'current': {'buy_cny': buy, 'package_cm': [L, W, H]},
            'method': '统一成本引擎逐次试算的单调边界（二分法），采购价取整到 0.01 元、厚度取整到 0.01cm 后复核；不另写成本公式',
            'note': '询价目标与数学边界，不是厂家报价，也不说明厂家做得到；体积相同但三边不同可能落入不同 FBA 档；'
                    '其他费用同当前输入，外箱装箱数不随包装厚度自动变化（头程按当前外箱规格）。'}


def render(result, currency='USD'):
    if not result or result.get('status') != 'available':
        return f"## 达到目标毛利：采购价与包装上限\n\n暂不能反推：{(result or {}).get('reason') or '缺输入'}。\n" if result else ''
    money = lambda v: '零采购价也达不到' if v is None else f'{v:.2f}'
    target = result['target_pre_ad_margin']
    lines = [f'## 达到广告前毛利 {target:.0%}：采购价与包装上限', '',
             f"口径：{result['basis']}。{result['note']}", '',
             '### 各包装厚度下，采购单价最高多少（元/件）', '',
             '| 包装 cm | 体积 cm³ | 比当前缩小 | ' + ' | '.join(f'售 {p:.2f}' for p in result['prices']) + ' |',
             '|---|---:|---:|' + '---:|' * len(result['prices'])]
    for row in result['grid']:
        size = '×'.join(f'{v:g}' for v in row['package_cm'])
        lines.append(f"| {size} | {row['volume_cm3']:g} | {row['reduction']:.0%} | " + ' | '.join(money(x['max_buy_cny']) for x in row['limits']) + ' |')
    if result['package_only']:
        lines += ['', f"### 采购价不变（{result['current']['buy_cny']:g} 元），包装厚度最多多少", '',
                  '| 售价 | 厚度上限 cm |', '|---:|---:|']
        for row in result['package_only']:
            height = row['max_height_cm']
            shown = '压到最薄也达不到' if height is None else '当前厚度即可' if height >= result['current']['package_cm'][2] else format(height, 'g')
            lines.append(f"| {row['price']:.2f} | {shown} |")
    return '\n'.join(lines) + '\n'
