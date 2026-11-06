# -*- coding: utf-8 -*-
"""每日简报共用版面（旗舰版 daily_brief 与运营版 feishu_push 共用数据结构和渲染模板，各自取数）。

数据结构（按 卖家账号 × 站点 分组）::

    account = {'account': 'A', 'title': str, 'built_at': iso, 'health': {'level', 'label', 'text'} | None,
               'sites': [site, ...]}
    site = {'region', 'name', 'currency', 'data_date',
            'cards': {'sales': card, 'orders': card, 'profit': card, 'traffic': card},
            'inventory': [{'product','sku','available','inbound','awd','daily','days','level','advice',
                           'restock_qty'?, 'order_by'?}],     # 后两个可选：旗舰版补货任务的建议量与最晚下单日
            'skus': [{'product','sku','sales','gross','margin','light','acos','breakeven'}],
            'buybox': [{'product','asin','share','sessions'}],
            'returns'?: [{'product','sku','units','rate','reasons'}],            # 可选（旗舰）：近 30 天退货件数、退货率、主要原因
            'pricing'?: [{'product','asin','my_price','lowest','buybox_price','buybox_mine','hint'}],   # 可选（旗舰）：价格与购买框
            'todo': [{'tier','product','title','why','do'}]}
    card = {'label', 'value' (数字或 None), 'unit': 'money'|'count'|'pct'|'text',
            'baseline' (前 7 日均值或 None), 'baseline_label', 'sub' (副行文字), 'note' (口径/截至日期), 'pending': bool}

规则：
- 一句话由 sentence() 按模板拼，句中每个数字都取自同一站点的 cards，不让 AI 生成数字。
- 读不到的数显示“待数据”，不显示 0；扣广告后利润注明广告费截至哪天，广告未到时标“预估/待数据”。
- 待办统一三级：紧急 / 注意 / 提示（运营版 P1→紧急、P2→注意、P3→提示），排序沿用各自规则表。
- 视觉沿用星空报告样式（深浅色都可读），只借样稿的版面结构。
"""
import html

TIERS = ('紧急', '注意', '提示')
TIER_OF = {'P1': '紧急', 'P2': '注意', 'P3': '提示', 'critical': '紧急', 'warn': '注意', 'info': '提示',
           '紧急': '紧急', '注意': '注意', '提示': '提示'}
TIER_CLASS = {'紧急': 'bad', '注意': 'warn', '提示': 'info'}
SYMBOL = {'USD': '$', 'GBP': '£', 'EUR': '€', 'JPY': '¥', 'CAD': 'C$', 'AUD': 'A$', 'MXN': 'MX$', 'CNY': '¥'}
PENDING = '待数据'


PROFIT_NOTE = '扣广告后利润 = 成本表口径毛贡献（售价 − VAT − 采购 − 头程 − FBA − 佣金等）− 广告花费；估算，不含仓储费、退款退货、优惠券'


def profit_after_ads(gross_before_ads, ad_spend, ad_complete=True, ad_through=None):
    """扣广告后利润的唯一计算入口（v1.3 9.3(2)，旗舰简报与运营版飞书日报都调用）。
    广告费没到齐时不硬算：返回 pending=True，卡片显示“预估/待数据”。"""
    if gross_before_ads is None or ad_spend is None or not ad_complete:
        return {'value': None, 'pending': True, 'note': '预估/待数据：广告费还没到齐', 'caliber': PROFIT_NOTE}
    return {'value': round(gross_before_ads - ad_spend, 2), 'pending': False,
            'note': f'广告费截至 {ad_through}' if ad_through else '', 'caliber': PROFIT_NOTE}


def tier(value):
    return TIER_OF.get(str(value), '提示')


def _e(v):
    return html.escape('' if v is None else str(v))


def fmt(value, unit, currency=None):
    if value is None:
        return PENDING
    if unit == 'money':
        sym = SYMBOL.get(currency or '', currency if currency in SYMBOL.values() else (currency or '') + ' ')
        return f"{'-' if value < 0 else ''}{sym}{abs(value):,.2f}"
    if unit == 'count':
        return f'{value:,.0f}'
    if unit == 'pct':
        return f'{value * 100:.1f}%'
    return str(value)


def change(value, baseline):
    """相对基准的变化（小数）；任一缺失返回 None。"""
    if value is None or not baseline:
        return None
    return value / baseline - 1


def fmt_change(value, baseline):
    c = change(value, baseline)
    return '—' if c is None else f"{'+' if c >= 0 else ''}{c * 100:.0f}%"


def counts(site):
    out = {t: 0 for t in TIERS}
    for row in site.get('todo') or []:
        out[tier(row.get('tier'))] += 1
    return out


def sentence(site):
    """今日一句话：只拼 cards 里已有的数字。"""
    cards, cur = site.get('cards') or {}, site.get('currency')
    sales, orders, profit = cards.get('sales') or {}, cards.get('orders') or {}, cards.get('profit') or {}
    parts = [f"{site.get('name') or site.get('region')} {site.get('data_date') or ''} 销售 {fmt(sales.get('value'), 'money', cur)}"]
    if sales.get('value') is not None and sales.get('baseline'):
        parts[-1] += f"（较{sales.get('baseline_label') or '前 7 日均值'} {fmt_change(sales['value'], sales['baseline'])}）"
    parts.append(f"订单 {fmt(orders.get('value'), 'count')}")
    if profit.get('pending') or profit.get('value') is None:
        parts.append('扣广告后利润' + PENDING)
    else:
        parts.append(f"扣广告后利润约 {fmt(profit['value'], 'money', cur)}" + (f"（{profit['note']}）" if profit.get('note') else ''))
    c = counts(site)
    todo = (f"紧急 {c['紧急']} 件、注意 {c['注意']} 件" if c['紧急'] or c['注意'] else '没有需要马上处理的事')
    return '，'.join(parts) + '；' + todo + '。'


CSS = """
.bl-tabs{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0}
.bl-tabs button{border:1px solid var(--line,#d0d7de);background:var(--card,#fff);color:inherit;border-radius:999px;padding:4px 12px;cursor:pointer}
.bl-tabs button.on{background:var(--accent,#0f766e);border-color:var(--accent,#0f766e);color:#fff}
.bl-site{display:none}.bl-site.on{display:block}
.bl-one{font-size:16px;line-height:1.6;margin:8px 0 12px}
.bl-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}
.bl-card{border:1px solid var(--line,#d0d7de);border-radius:10px;padding:10px 12px}
.bl-card b{display:block;font-size:20px;margin:4px 0}
.bl-card small{display:block;opacity:.75}
.bl-pending b{opacity:.55}
.bl-tag{display:inline-block;border-radius:4px;padding:0 6px;margin-right:6px;font-size:12px;color:#fff}
.bl-tag.bad{background:#c0392b}.bl-tag.warn{background:#d4880f}.bl-tag.info{background:#5b6b7b}
.bl-bar{display:inline-block;height:8px;border-radius:4px;background:#0f766e;vertical-align:middle;margin-right:6px}
.bl-bar.warn{background:#d4880f}.bl-bar.bad{background:#c0392b}
/* 页面基础样式让数字列不换行、只有首列可换行：首列给足宽度，说明列允许换行，避免产品名一词一行、建议被截断 */
.bl-table td:first-child,.bl-table th:first-child{min-width:220px;max-width:340px}
.bl-table td.bl-wrap,.bl-table th.bl-wrap{white-space:normal;text-align:left;min-width:240px}
.bl-health{font-size:13px;padding:6px 10px;border-radius:8px;border:1px solid var(--line,#d0d7de);margin:6px 0}
.bl-health.stale{border-color:#c0392b}.bl-health.attention{border-color:#d4880f}
"""


def _card_html(c, cur):
    pending = c.get('pending') or c.get('value') is None
    value = PENDING if pending else fmt(c.get('value'), c.get('unit', 'text'), cur)
    sub = c.get('sub') or (f"较{c.get('baseline_label') or '前 7 日均值'} {fmt_change(c.get('value'), c.get('baseline'))}"
                           if c.get('baseline') else '')
    return (f"<div class='bl-card{' bl-pending' if pending else ''}'><span>{_e(c.get('label'))}</span><b>{_e(value)}</b>"
            + (f"<small>{_e(sub)}</small>" if sub else '') + (f"<small>{_e(c.get('note'))}</small>" if c.get('note') else '') + '</div>')


WRAP_HEADS = ('建议', '提示', '主要原因')


def _table(head, rows):
    wrap = [h in WRAP_HEADS for h in head]
    cls = lambda i: " class='bl-wrap'" if wrap[i] else ''
    return ("<div style='overflow-x:auto'><table class='bl-table'><thead><tr>" + ''.join(f'<th{cls(i)}>{_e(h)}</th>' for i, h in enumerate(head)) + '</tr></thead><tbody>'
            + ''.join('<tr>' + ''.join(f'<td{cls(i)}>{cell}</td>' for i, cell in enumerate(row)) + '</tr>' for row in rows) + '</tbody></table></div>')


def _days_bar(days, level):
    if days is None:
        return PENDING
    width = max(4, min(72, int(days * 1.2)))
    return f"<span class='bl-bar {_e(level or '')}' style='width:{width}px'></span>{days:.0f} 天"


def site_html(site):
    cur = site.get('currency')
    cards = site.get('cards') or {}
    out = [f"<p class='bl-one'>{_e(sentence(site))}</p><div class='bl-cards'>"
           + ''.join(_card_html(cards[k], cur) for k in ('sales', 'orders', 'profit', 'traffic') if k in cards) + '</div>']
    inv = site.get('inventory') or []
    restock = any('restock_qty' in r for r in inv)
    restock_cell = lambda r: [_e('—' if r.get('restock_qty') is None else f"{r['restock_qty']:g} 件"
                                 + (f"（{r['order_by']} 前下单）" if r.get('order_by') and r.get('restock_qty') else ''))] if restock else []
    out.append('<h3>库存</h3>' + (_table(['产品', '可售（FBA+AWD）', '在途', '日均', '可售天数'] + (['建议补货'] if restock else []) + ['建议'], [
        [_e(r.get('product')), _e(fmt(r.get('available'), 'count')), _e(fmt(r.get('inbound'), 'count')),
         _e('—' if r.get('daily') is None else f"{r['daily']:.1f}"), _days_bar(r.get('days'), r.get('level'))] + restock_cell(r) + [_e(r.get('advice') or '')]
        for r in inv]) if inv else "<p class='mute'>暂无库存数据或库存都正常。</p>"))
    skus = site.get('skus') or []
    out.append('<h3>SKU 利润（近 7 天）</h3>' + (_table(['产品', '销售额', '毛贡献（未扣广告）', '利润率', '健康', 'ACoS / 保本线'], [
        [_e(r.get('product')), _e(fmt(r.get('sales'), 'money', cur)), _e(fmt(r.get('gross'), 'money', cur)),
         _e(fmt(r.get('margin'), 'pct')), _e(r.get('light') or '—'),
         _e(f"{fmt(r.get('acos'), 'pct')} / {fmt(r.get('breakeven'), 'pct')}")] for r in skus]) if skus else f"<p class='mute'>{PENDING}</p>"))
    if 'pricing' in site:
        price = site.get('pricing') or []
        out.append('<h3>价格与购买框</h3>' + (_table(['产品', 'ASIN', '我的价', '最低价', '购买框价', '购买框', '提示'], [
            [_e(r.get('product')), _e(r.get('asin')), _e(fmt(r.get('my_price'), 'money', cur)), _e(fmt(r.get('lowest'), 'money', cur)),
             _e(fmt(r.get('buybox_price'), 'money', cur)), _e({True: '在我这', False: '不在我这'}.get(r.get('buybox_mine'), PENDING)), _e(r.get('hint') or '')]
            for r in price]) if price else f"<p class='mute'>{_e(site.get('pricing_note') or PENDING)}</p>"))
    if 'returns' in site:
        ret = site.get('returns') or []
        out.append('<h3>退货（近 30 天）</h3>' + (_table(['产品', '退货件数', '退货率', '主要原因'], [
            [_e(r.get('product')), _e(fmt(r.get('units'), 'count')), _e(fmt(r.get('rate'), 'pct')), _e(r.get('reasons') or '')]
            for r in ret]) if ret else f"<p class='mute'>{_e(site.get('returns_note') or '近 30 天没有退货。')}</p>"))
    box = site.get('buybox') or []
    if box:
        out.append('<h3>购买框异常</h3>' + _table(['产品', 'ASIN', '购买框占比', '访问'], [
            [_e(r.get('product')), _e(r.get('asin')), _e(fmt(r.get('share'), 'pct')), _e(fmt(r.get('sessions'), 'count'))] for r in box]))
    todo = site.get('todo') or []
    out.append('<h3>今日待办</h3>' + ('<ol class="todo">' + ''.join(
        f"<li><span class='bl-tag {TIER_CLASS[tier(t.get('tier'))]}'>{tier(t.get('tier'))}</span><b>{_e(t.get('product'))} · {_e(t.get('title'))}</b>"
        f"<small>{_e(t.get('why'))}</small><small>建议：{_e(t.get('do'))}</small></li>" for t in todo) + '</ol>'
        if todo else "<p class='mute'>今天没有需要处理的事。</p>"))
    return ''.join(out)


def account_html(account, extra_by_region=None):
    """账号区：页头新鲜度行 + 站点页签。extra_by_region 是各站点追加在本站点页签末尾的 HTML（如广告区）。"""
    sites = account.get('sites') or []
    health = account.get('health')
    out = [f'<style>{CSS}</style>']
    if health:
        out.append(f"<div class='bl-health {_e(health.get('level'))}'><b>数据健康：{_e(health.get('label'))}</b> · {_e(health.get('text'))}</div>")
    out.append("<div class='bl-tabs'>" + ''.join(
        f"<button class='{'on' if i == 0 else ''}' onclick='blTab({i})'>{_e(s.get('name'))}</button>" for i, s in enumerate(sites)) + '</div>')
    for i, s in enumerate(sites):
        out.append(f"<section class='card bl-site{' on' if i == 0 else ''}'>" + site_html(s)
                   + ((extra_by_region or {}).get(s.get('region')) or '') + '</section>')
    out.append("<script>function blTab(i){document.querySelectorAll('.bl-tabs button').forEach((b,j)=>b.classList.toggle('on',i===j));"
               "document.querySelectorAll('.bl-site').forEach((s,j)=>s.classList.toggle('on',i===j));}</script>")
    return ''.join(out)


def pulse_line(site):
    cards, cur = site.get('cards') or {}, site.get('currency')
    profit = cards.get('profit') or {}
    p = PENDING if profit.get('pending') or profit.get('value') is None else fmt(profit['value'], 'money', cur)
    return (f"**{site.get('name')}** 销售 {fmt((cards.get('sales') or {}).get('value'), 'money', cur)} · "
            f"订单 {fmt((cards.get('orders') or {}).get('value'), 'count')} · 扣广告后 {p}")


def feishu_card(account, extra_md=None, open_hint='回电脑打开简报看详情'):
    """每个卖家账号一张卡：每站一行脉搏；有紧急/注意的站点才展开明细；卡末合并前 3 件待办。飞书里不放推送按钮。"""
    sites = account.get('sites') or []
    lines, detail, todo = [], [], []
    worst = '提示'
    for s in sites:
        lines.append(pulse_line(s))
        c = counts(s)
        if c['紧急'] or c['注意']:
            detail.append(f"**{s.get('name')}**：{sentence(s)}")
            worst = '紧急' if c['紧急'] or worst == '紧急' else '注意'
        for t in s.get('todo') or []:
            todo.append((TIERS.index(tier(t.get('tier'))), s.get('name'), t))
    todo.sort(key=lambda x: x[0])
    els = [{'tag': 'div', 'text': {'tag': 'lark_md', 'content': '\n'.join(lines) or PENDING}}]
    health = account.get('health')
    if health and health.get('level') != 'normal':
        els.append({'tag': 'note', 'elements': [{'tag': 'plain_text', 'content': '数据健康：' + health.get('label', '') + ' · ' + health.get('text', '')}]})
    if detail:
        els.append({'tag': 'div', 'text': {'tag': 'lark_md', 'content': '\n'.join(detail)}})
    els.append({'tag': 'div', 'text': {'tag': 'lark_md', 'content': '**今天先做**\n' + (
        '\n'.join(f"{i}. [{tier(t.get('tier'))}][{name}] {t.get('product')}：{t.get('title')}——{t.get('do')}"
                  for i, (_, name, t) in enumerate(todo[:3], 1)) if todo else '没有需要马上处理的事')}})
    for md in extra_md or []:
        els.append({'tag': 'div', 'text': {'tag': 'lark_md', 'content': md}})
    els.append({'tag': 'note', 'elements': [{'tag': 'plain_text', 'content': open_hint}]})
    template = {'紧急': 'red', '注意': 'orange', '提示': 'green'}[worst]
    return {'config': {'wide_screen_mode': True},
            'header': {'title': {'tag': 'plain_text', 'content': account.get('title') or '每日经营简报'}, 'template': template},
            'elements': els}
