"""第 7 步推广分析（R06.1 AS-01 第 2 批）：只用统一成本引擎的结果推导，不另算成本。

3.0.22 的案例里助手现写了 23KB 脚本做这几件事；这里按引擎口径给出精确结果：
  · 转化率网格：每个点击转化率下的保本 CPC = 广告前利润 × 转化率
  · 关键词广告单：每单广告成本 = CPC ÷ 转化率，广告后利润 = 广告前利润 − 每单广告成本（与引擎 admode=cpc 完全一致）
  · CPC 上限：要达到广告后目标毛利 t，CPC ≤（广告前利润 − t × 成交价）× 转化率；广告前就够不到时写明“不投广告也达不到”
用户选了条件情景（如 A/B 两种采购方案）时逐套计算，否则用主口径。全部是条件测算，不是投放授权。
"""
from __future__ import annotations

CVR_GRID = (3, 5, 8, 10, 12)       # 点击转化率，%
ORDER_CVRS = (5, 8, 10)
CEILING_CVR = 10


def _bases(unified):
    main = unified.get('main_basis') or {}
    if main.get('kind') == 'user_condition':
        return [(row['label'], row['result']) for row in main['scenarios']]
    if main.get('result'):
        return [(main.get('name') or '主口径', main['result'])]
    return []


def analyse(unified, *, cvr_grid=CVR_GRID, order_cvrs=ORDER_CVRS, ceiling_cvr=CEILING_CVR):
    inputs = unified.get('inputs') or {}
    post_target = inputs.get('targetPost')
    target_kind = '广告后目标毛利'
    if post_target is None:
        post_target, target_kind = inputs.get('target'), '广告前目标毛利（未单独设广告后目标，按同一比例参考）'
    keywords = [k for k in (unified.get('promotion') or {}).get('keywords') or [] if k.get('cpc') is not None]
    bases = []
    for label, result in _bases(unified):
        pre, realized = result.get('pre_ad_profit'), result.get('realized_price')
        row = {'label': label, 'pre_ad_profit': pre, 'realized_price': realized, 'cvr_grid': [], 'keyword_orders': [], 'cpc_ceiling': None}
        if pre is None or not realized:
            row['note'] = '费用不全，算不出单件广告前利润，本套不做推广测算'
            bases.append(row)
            continue
        row['cvr_grid'] = [{'cvr_percent': c, 'break_even_cpc': pre * c / 100 if pre > 0 else 0.0} for c in cvr_grid]
        for keyword in keywords:
            for c in order_cvrs:
                cost = keyword['cpc'] / (c / 100)
                post = pre - cost
                row['keyword_orders'].append({'keyword': keyword.get('keyword'), 'source': keyword.get('source'), 'cpc': keyword['cpc'],
                                              'cvr_percent': c, 'ad_cost_per_order': cost, 'post_ad_profit': post,
                                              'post_ad_margin': post / realized})
        if post_target is not None:
            target = post_target / 100
            room = pre - target * realized
            row['cpc_ceiling'] = {'target_margin': target, 'target_kind': target_kind, 'cvr_percent': ceiling_cvr,
                                  'max_cpc': room * ceiling_cvr / 100 if room > 0 else None,
                                  'reason': None if room > 0 else f'广告前毛利已低于 {target:.0%}，不投广告也达不到'}
        bases.append(row)
    return {'schema_version': 1, 'bases': bases, 'keyword_count': len(keywords),
            'note': '按统一成本引擎结果推导的条件测算；转化率是假设值，不是实绩，不构成投放或采购授权。'}


def render(analysis):
    """给 06_推广难度.md 的章节。"""
    bases = analysis.get('bases') or []
    if not bases:
        return ''
    money = lambda v: '—' if v is None else f'{v:.2f}'
    pct = lambda v: '—' if v is None else f'{v * 100:.1f}%'
    lines = ['## 推广测算（按方案）', '', analysis['note'], '']
    grid = bases[0]['cvr_grid']
    if grid:
        lines += ['### 各点击转化率下的保本 CPC', '',
                  '| 方案 | 广告前利润 | ' + ' | '.join(f"CVR {g['cvr_percent']}%" for g in grid) + ' |',
                  '|---|---:|' + '---:|' * len(grid)]
        for base in bases:
            cells = [money(g['break_even_cpc']) for g in base['cvr_grid']] or ['—'] * len(grid)
            lines.append(f"| {base['label']} | {money(base['pre_ad_profit'])} | " + ' | '.join(cells) + ' |')
        lines.append('')
    orders = [(base['label'], o) for base in bases for o in base['keyword_orders']]
    if orders:
        lines += ['### 关键词广告单：每单广告成本与广告后利润', '',
                  '| 方案 | 关键词 | 来源 | CPC | 点击转化率 | 每单广告成本 | 广告后利润 | 广告后毛利率 |',
                  '|---|---|---|---:|---:|---:|---:|---:|']
        for label, o in orders:
            lines.append(f"| {label} | {o['keyword'] or '核心词'} | {o.get('source') or '未标'} | {money(o['cpc'])} | {o['cvr_percent']}% | "
                         f"{money(o['ad_cost_per_order'])} | {money(o['post_ad_profit'])} | {pct(o['post_ad_margin'])} |")
        lines.append('')
    ceilings = [(base['label'], base['cpc_ceiling']) for base in bases if base.get('cpc_ceiling')]
    if ceilings:
        first = ceilings[0][1]
        lines += [f"### 要守住{first['target_kind']} {first['target_margin']:.0%}，CPC 最高能出多少（点击转化率 {first['cvr_percent']}%）", '',
                  '| 方案 | CPC 上限 | 说明 |', '|---|---:|---|']
        for label, c in ceilings:
            lines.append(f"| {label} | {money(c['max_cpc'])} | {c['reason'] or '超过这个 CPC，广告后毛利就低于目标'} |")
        lines.append('')
    for base in bases:
        if base.get('note'):
            lines.append(f"- {base['label']}：{base['note']}")
    return '\n'.join(lines).rstrip() + '\n'
