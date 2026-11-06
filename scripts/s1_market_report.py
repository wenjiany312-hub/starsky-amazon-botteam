#!/usr/bin/env python3
"""S1 市场分析报告（R09）：脚本出数 + AI 写判断，同一份内容同时出 Markdown 与 HTML。

- 数字与表格：s1_market_overview.build_overview() 与 01_market_facts.json，全部来自已保存回包。
- 判断与读法：AI 写在 S1_选品板块/amazon-market-research/market_narrative.yaml（模板见 `template` 子命令）。
- 重跑只刷新数字，AI 段落来自 yaml，不会被覆盖。缺 AI 段落时显示「待补写」，只有三处必填。

命令：
  python s1_market_report.py template <产品根>   打印 market_narrative.yaml 填写骨架（含候选商品清单）
  python s1_market_report.py check <产品根>      列出必填缺失、待补写段落、原话核对结果
  python s1_market_report.py render <产品根>     重新生成 01_市场分析报告.md 与 .html
"""
from __future__ import annotations

import html
import json
import re
from datetime import date
from pathlib import Path

import yaml

from s1_market_overview import MARKET, NARRATIVE, TIER_LABELS, build_overview, load_calls, load_narrative, review_summary

REPORT_MD = MARKET/'01_市场分析报告.md'
REPORT_HTML = MARKET/'01_市场分析报告.html'
REQUIRED = (('summary', 'verdict', '执行摘要 · 建议结论'), ('summary', 'breakthrough', '执行摘要 · 一句话破局'),
            ('counterargument', 'argument', '最强反方 · 反驳论点'))
VERDICTS = ('做', '有条件做', '暂缓', '不做')
PENDING = '待补写'
PENDING_DATA = '待补数'
READINGS = ('scale', 'trend', 'concentration', 'price', 'ratings', 'new_products', 'adjacent', 'pool', 'matrix', 'head',
            'attributes', 'keywords', 'strict', 'voc', 'difficulty')


# ---------- 数字格式（与 s1_readability 的显示精度一致） ----------

def _n(value):
    try: return None if value is None or isinstance(value, bool) else float(value)
    except (TypeError, ValueError): return None


def num(value, unit=''):
    value = _n(value)
    if value is None: return '—'
    text = f'{value:,.0f}' if abs(value - round(value)) < 1e-9 or abs(value) >= 1000 else f'{value:,.2f}'.rstrip('0').rstrip('.')
    return text + unit


def money(value):
    value = _n(value)
    return '—' if value is None else f'${value:,.2f}'


def pct(value, ratio=True):
    """ratio=True: 0.123 -> 12.3%；ratio=False: 12.3 -> 12.3%。"""
    value = _n(value)
    if value is None: return '—'
    value = value*100 if ratio else value
    text = f'{value:.1f}'.rstrip('0').rstrip('.')
    return text + '%'


def signed(value):
    value = _n(value)
    if value is None: return '—'
    return ('+' if value > 0 else '') + pct(value)


def text(value, fallback='—'):
    if value is None or value == '' or value == [] or value == {}: return fallback
    if isinstance(value, (list, tuple)): return '；'.join(text(v) for v in value if v not in (None, '')) or fallback
    if isinstance(value, dict): return '；'.join(f'{k}：{text(v)}' for k, v in value.items()) or fallback
    return str(value).replace('|', '／').replace('\n', ' ').strip() or fallback


def get(data, *keys, default=None):
    for key in keys:
        if not isinstance(data, dict): return default
        data = data.get(key)
    return default if data in (None, '', [], {}) else data


# ---------- 内容块：同一结构渲染两种格式 ----------

class Doc:
    def __init__(self):
        self.blocks = []

    def h(self, level, title, anchor=None): self.blocks.append(('h', level, title, anchor))
    def p(self, body): self.blocks.append(('p', body))
    def note(self, body): self.blocks.append(('note', body))
    def reading(self, body, pending_label=None):
        self.blocks.append(('reading', body if body else None, pending_label))
    def ul(self, items): self.blocks.append(('ul', [i for i in items if i]))
    def table(self, headers, rows, empty=None):
        self.blocks.append(('table', headers, [[text(c) for c in row] for row in rows], empty))
    def quote(self, body, verified=None): self.blocks.append(('quote', body, verified))
    def chart(self, kind, **data): self.blocks.append(('chart', kind, data))
    def kpis(self, items): self.blocks.append(('kpis', items))  # HTML only; the same numbers are in the table below
    def details(self, summary, body_lines): self.blocks.append(('details', summary, body_lines))


def to_markdown(doc):
    out = []
    for block in doc.blocks:
        kind = block[0]
        if kind == 'h': out += ['#'*block[1] + ' ' + block[2], '']
        elif kind == 'p': out += [block[1], '']
        elif kind == 'note': out += ['> ' + block[1], '']
        elif kind == 'reading':
            out += [('**读法**：' + block[1]) if block[1] else f'**读法**：{PENDING}' + (f'（{block[2]}）' if block[2] else ''), '']
        elif kind == 'ul':
            out += ['- ' + item for item in block[1]] + [''] if block[1] else []
        elif kind == 'table':
            headers, rows, empty = block[1], block[2], block[3]
            if not rows:
                out += [empty or '暂无可核实记录。', '']
                continue
            out += ['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join('---' for _ in headers) + ' |']
            out += ['| ' + ' | '.join(row) + ' |' for row in rows] + ['']
        elif kind == 'quote':
            mark = '' if block[2] is None else ('（原话已在留存评论中核对）' if block[2] else '（未在留存评论中找到这句原话，待核）')
            out += ['> 原话：' + text(block[1]) + mark, '']
        elif kind == 'details':
            out += ['<details><summary>' + block[1] + '</summary>', ''] + block[2] + ['', '</details>', '']
    return '\n'.join(out).rstrip() + '\n'


# ---------- 读入 ----------

def _read_yaml(path):
    path = Path(path)
    if not path.is_file(): return {}
    try: return yaml.safe_load(path.read_text(encoding='utf-8-sig')) or {}
    except (OSError, yaml.YAMLError): return {}


def _normal(value):
    return re.sub(r'[\W_]+', '', str(value or '').lower())


def missing_required(narrative):
    result = []
    for section, key, label in REQUIRED:
        value = get(narrative, section, key)
        if not value or str(value).strip() in ('', PENDING): result.append(label)
    verdict = get(narrative, 'summary', 'verdict')
    if verdict and str(verdict).strip() not in VERDICTS:
        result.append('执行摘要 · 建议结论须为：' + ' / '.join(VERDICTS))
    return result


def quote_checker(root):
    texts = [_normal(row['content']) for row in review_summary(load_calls(root))['texts']] if root else []
    def check(quote):
        needle = _normal(quote)
        if not needle or not texts: return None
        return any(needle in item for item in texts)
    return check


# ---------- 章节 ----------

def _structural_difficulty(pool, category):
    """沿用 9/24 报告口径：头部集中度×0.4 + 评论壁垒×0.35 + 价格战×0.25。"""
    stats = pool['stats']
    rows = pool['rows']
    if not rows: return None
    top3 = stats.get('top3_share')
    review_median = stats.get('review_median')
    p50 = (stats.get('price_quantiles') or {}).get('p50')
    cheap = [r for r in rows if r.get('price') is not None and p50 and r['price'] < p50*0.8]
    priced = [r for r in rows if r.get('price') is not None]
    parts = [('头部集中度（同款型池 Top3 销量占比）', top3, top3, 0.40),
             ('评论壁垒（同款型池评论中位数 ÷ 1000，封顶 1）', review_median, min((review_median or 0)/1000, 1) if review_median is not None else None, 0.35),
             ('价格战（低于中位价 80% 的 SKU 占比）', f'{len(cheap)}/{len(priced)}' if priced else None, len(cheap)/len(priced) if priced else None, 0.25)]
    if any(p[2] is None for p in parts): return {'parts': parts, 'score': None}
    score = round(sum(p[2]*p[3] for p in parts), 3)
    category_top3 = get(category or {}, 'concentration', 'top3_product')
    alt = round(score - parts[0][2]*0.4 + (category_top3/100)*0.4, 3) if category_top3 is not None else None
    def level(value): return '易' if value < 0.3 else '中' if value < 0.5 else '难'
    return {'parts': parts, 'score': score, 'level': level(score), 'category_score': alt, 'category_level': level(alt) if alt is not None else None}


def _gate(facts):
    coverage = (facts or {}).get('coverage') or {}
    rows = []
    for key, label in (('competitors', '同款竞品价/销/评'), ('monthly_trend', '同款逐月趋势（月份数）'), ('reviews', '同款真实评论')):
        node = coverage.get(key) or {}
        if node: rows.append((label, f"{num(node.get('observed'))} / {num(node.get('required'))}", '够了' if node.get('status') == 'met' else '不够'))
    return rows


def build_document(facts, overview, narrative, inputs=None, *, check_quote=None, product_name=None):
    inputs = inputs or {}
    narrative = narrative or {}
    category = overview.get('category')
    trend = overview.get('category_trend')
    pool = overview.get('pool') or {'rows': [], 'stats': {}, 'others': [], 'unlabeled_count': 0, 'label_origin': 'none', 'strict_count': 0}
    stats = pool.get('stats') or {}
    readings = narrative.get('readings') or {}
    summary = narrative.get('summary') or {}
    keywords = overview.get('keywords') or []
    core = keywords[0] if keywords else {}
    difficulty = _structural_difficulty(pool, category)
    doc = Doc()

    name = summary.get('selected_form') or product_name or get(inputs, 'demand', 'positioning') or '目标产品'
    doc.h(1, f'{name} · 市场分析报告')
    node = overview.get('target_node_id')
    doc.p(f"站点：{text(inputs.get('marketplace') or get(inputs, 'conditions', 'marketplace') or 'US')}　｜　目标类目节点：{text(node)}　｜　数据日期：{overview.get('as_of')}"
          + '　｜　口径：类目大盘、同款型池、严格同款三层并列，互不替代。')
    missing = missing_required(narrative)
    if missing:
        doc.note('⚠️ 必填段落未完成：' + '、'.join(missing) + '。在 market_narrative.yaml 补写后重新生成本报告。')

    # 1 执行摘要
    doc.h(2, '1. 执行摘要', 'summary')
    scale = '—'
    if category: scale = f"类目 Top100 月销 {num(get(category, 'size', 'top100_units'))} 件、月销售额 {money(get(category, 'size', 'top100_revenue'))}"
    if stats.get('units'): scale += f"；同款型池 {num(stats.get('skus'))} 个 SKU 月销 {num(stats.get('units'))} 件"
    growth = []
    if trend and trend.get('ytd_yoy') is not None: growth.append(f"类目 {trend['ytd_range'][0]}～{trend['ytd_range'][1]} 同比 {signed(trend['ytd_yoy'])}")
    if core.get('ytd_yoy') is not None: growth.append(f"核心词 {core['keyword']} 同期同比 {signed(core['ytd_yoy'])}")
    rows = [('选定款型', summary.get('selected_form') or PENDING), ('建议规格', summary.get('spec') or PENDING),
            ('建议售价', summary.get('price') or PENDING), ('为什么是这个站点', summary.get('site_reason') or PENDING),
            ('市场体量', scale), ('增长', '；'.join(growth) or '—'),
            ('结构性难度', f"{difficulty['score']}（{difficulty['level']}，同款型池 {num(stats.get('skus'))} 个样本）" if difficulty and difficulty.get('score') is not None else '—'),
            ('允许到岸成本', _criterion(facts, 'allowable_landed_cost') or '第 6 步成本测算后回填'),
            ('建议结论', summary.get('verdict') or PENDING + '（必填）'),
            ('一句话破局', summary.get('breakthrough') or PENDING + '（必填）')]
    doc.kpis([('类目 Top100 月销', num(get(category or {}, 'size', 'top100_units')), '件 / 月', None),
              ('类目今年同比', signed(get(trend or {}, 'ytd_yoy')), text(get(trend or {}, 'ytd_range')), 'warn' if (_n(get(trend or {}, 'ytd_yoy')) or 0) < 0 else 'ok'),
              ('同款型池', num(stats.get('skus')), f"月销 {num(stats.get('units'))} 件", None),
              ('建议结论', text(summary.get('verdict'), PENDING), '必填', None if summary.get('verdict') else 'warn')])
    doc.table(['项', '结论'], rows)

    # 2 数据来源
    doc.h(2, '2. 分析范围与数据来源', 'sources')
    source_rows = []
    if category: source_rows.append(('类目大盘', 'Sorftime 类目报表', f'节点 {node}', category.get('window')[1] or str(category.get('queried_at') or '最近 30 天')[:10], '规模、集中度、品牌、评分与评论分布'))
    if trend: source_rows.append(('类目趋势', 'Sorftime 类目趋势', f'节点 {node}', f"{trend['months'][0]['month']}～{trend['months'][-1]['month']}", '增长与季节性'))
    if overview.get('adjacent'): source_rows.append(('相邻类目', 'Sorftime 类目报表', f"{len(overview['adjacent'])} 个同级节点", '同月', '替代市场比较'))
    if keywords: source_rows.append(('关键词', 'Sorftime 关键词详情与趋势', '、'.join(k['keyword'] for k in keywords[:5]), text(core.get('updated')), '需求规模与走向'))
    source_rows.append(('商品候选', 'Sorftime 搜索、详情等', f"{num(len(pool.get('rows', [])) + len(pool.get('others', [])) + pool.get('unlabeled_count', 0))} 个 ASIN", '各次调用', '同款型池与竞品表'))
    reviews = overview.get('reviews') or {}
    if reviews.get('total'): source_rows.append(('评论', 'Sorftime 商品评论', f"{len(reviews.get('by_asin') or {})} 个 ASIN", '近一年', 'VOC 痛点与价值点'))
    if overview.get('traffic'): source_rows.append(('流量词', 'Sorftime 商品流量词', f"{len(overview['traffic'])} 个 ASIN", '最近一次曝光', '反向工程'))
    doc.table(['数据', '工具', '对象', '时间', '用途'], source_rows)

    # 3 类目大盘
    doc.h(2, '3. 类目大盘：市场容量、趋势与集中度', 'category')
    if not category and not trend:
        doc.p(f'{PENDING_DATA}：目标类目的大盘报表和 24 个月趋势还没取到，本章暂空；补齐方法见附录 A。')
    if category:
        size = category['size']
        doc.h(3, '3.1 市场容量与规模')
        doc.table(['指标', '数值', '口径'], [
            ('Top100 月销量', num(size['top100_units'], ' 件'), '类目前 100 名商品估算合计'),
            ('Top100 月销售额', money(size['top100_revenue']), '同上'),
            ('类目平均售价', money(size['avg_price']), f"最低 {money(size['min_price'])}，最高 {money(size['max_price'])}"),
            ('类目商品总数', num(size['product_count']), f"在售 {num(size['available_count'])}"),
            ('类目转化率', pct(size['conversion_rate'], False), '工具估算'),
            ('退货率', pct(size['return_rate'], False) if size['return_rate'] else '工具未提供', ''),
            ('Top100 平均上架天数', num(size['avg_listing_days'], ' 天'), '')])
        doc.reading(readings.get('scale'), '规模属于大/中/小，够不够养一个精铺 SKU')
    if trend:
        doc.h(3, '3.2 趋势与季节性')
        months = trend['months']
        by_month = {m['month']: m['value'] for m in months}
        trend_rows = []
        for item in months[-24:]:
            prior = by_month.get(f"{int(item['month'][:4]) - 1}{item['month'][4:]}")
            partial = item['month'] == trend.get('partial_month')
            yoy = (item['value']/prior - 1) if prior and not partial else None
            mark = '（当月未结束，不算同比）' if partial else ''
            trend_rows.append((item['month'] + mark, num(item['value']), signed(yoy)))
        doc.chart('line', title='类目 Top100 月销量', series=[{'name': '类目月销', 'points': [(m['month'], m['value']) for m in months[-24:]]}])
        doc.table(['月份', '类目月销量', '同比'], trend_rows)
        facts_line = []
        if trend.get('ytd_yoy') is not None: facts_line.append(f"今年 {trend['ytd_range'][0]}～{trend['ytd_range'][1]} 合计同比 {signed(trend['ytd_yoy'])}")
        if trend.get('yoy_12m') is not None: facts_line.append(f"最近 12 个完整月同比 {signed(trend['yoy_12m'])}")
        if trend.get('peak_months'): facts_line.append('旺季月 ' + '、'.join(trend['peak_months']))
        if trend.get('low_months'): facts_line.append('淡季月 ' + '、'.join(trend['low_months']))
        if facts_line: doc.p('；'.join(facts_line) + '。')
        doc.reading(readings.get('trend'), '在涨、见顶还是季节性，最晚备货时间')
    if category:
        conc = category['concentration']
        doc.h(3, '3.3 集中度')
        doc.table(['指标', '数值'], [('Top3 商品销量占比', pct(conc['top3_product'], False)), ('Top3 品牌销量占比', pct(conc['top3_brand'], False)),
                                    ('Top3 卖家销量占比', pct(conc['top3_seller'], False)), ('亚马逊自营销量占比', pct(conc['amazon_owned'], False))])
        doc.chart('bars', title='品牌销量占比 Top10', items=[(b['name'], _n(b['share'])) for b in category['brands'][:10]], unit='%')
        doc.table(['品牌', '商品数', '月销量', '销量占比', '近 6 个月新品数'],
                  [(b['name'], num(b['products']), num(b['units']), pct(b['share'], False), num(b['new_6m'])) for b in category['brands'][:10]])
        doc.table(['卖家', '商品数', '月销量', '销量占比'], [(s['name'], num(s['products']), num(s['units']), pct(s['share'], False)) for s in category['sellers'][:10]])
        doc.reading(readings.get('concentration'), '头部是否垄断，有没有自营压制')
        doc.h(3, '3.4 价格分布')
        size = category['size']
        doc.p(f"类目均价 {money(size['avg_price'])}（最低 {money(size['min_price'])}，最高 {money(size['max_price'])}），单品平均利润 {money(size['avg_profit'])}。"
              + ('各价格段转化率：' + '；'.join(f"{money(r['low'])}–{money(r['high'])} {pct(r['rate'], False)}" for r in category['order_rate']) + '。' if category['order_rate'] else '各价格段转化率工具未提供。')
              + '同款型池的价格带与销量矩阵见 4.3。')
        doc.reading(readings.get('price'), '主战场价带、空档价带')
        doc.h(3, '3.5 评分与评论分布')
        doc.chart('bars', title='按评论数分层的销量占比', items=[(r['range'], _n(r['share'])) for r in category['review_bands']], unit='%')
        doc.table(['评论数区间', '商品数', '销量占比'], [(r['range'], num(r['products']), pct(r['share'], False)) for r in category['review_bands']])
        doc.table(['星级区间', '商品数', '销量占比'], [(r['range'], num(r['products']), pct(r['share'], False)) for r in category['stars'] if _n(r['products'])])
        doc.reading(readings.get('ratings'), '新品要多少评论才有量，评分门槛')
        doc.h(3, '3.6 新品表现')
        new_units = sum((_n(b['units']) or 0)*(_n(b['new_6m_share']) or 0)/100 for b in category['brands'])
        total = _n(get(category, 'size', 'top100_units'))
        movers = [b for b in category['brands'] if (_n(b['new_6m']) or 0) > 0]
        doc.p(f"Top100 中近 6 个月上架的新品约月销 {num(round(new_units))} 件，占 Top100 销量 {pct(new_units/total) if total else '—'}；"
              f"有新品进入 Top100 的品牌 {len(movers)} 个。Top100 平均上架 {num(get(category, 'size', 'avg_listing_days'), ' 天')}。")
        doc.table(['品牌', '近 6 个月新品数', '品牌月销量', '新品销量占品牌比例'],
                  [(b['name'], num(b['new_6m']), num(b['units']), pct(b['new_6m_share'], False)) for b in sorted(movers, key=lambda b: -(_n(b['units']) or 0))[:10]],
                  empty='Top100 里没有近 6 个月上架的新品。')
        doc.reading(readings.get('new_products'), '新手窗口有多大')
    if overview.get('adjacent'):
        doc.h(3, '3.7 相邻类目对比')
        rows = []
        if category: rows.append((f'目标 {node}', num(get(category, 'size', 'top100_units')), money(get(category, 'size', 'top100_revenue')), money(get(category, 'size', 'avg_price')), pct(get(category, 'concentration', 'top3_product'), False), '目标类目'))
        rows += [(a['node_id'], num(a['units']), money(a['revenue']), money(a['avg_price']), pct(a['top3_product'], False), a.get('reason')) for a in overview['adjacent']]
        doc.table(['类目节点', 'Top100 月销量', 'Top100 月销售额', '均价', 'Top3 商品占比', '说明'], rows)
        doc.reading(readings.get('adjacent'), '有没有更好的替代市场')
    countries = {}
    for row in pool.get('rows') or []:
        if row.get('seller_country'): countries[row['seller_country']] = countries.get(row['seller_country'], 0) + 1
    doc.h(3, '3.8 卖家精灵补充维度')
    doc.p(('同款型池卖家所属地：' + '、'.join(f'{k} {v} 个' for k, v in sorted(countries.items(), key=lambda x: -x[1])) + '。') if countries else '')
    doc.p(f'{PENDING_DATA}（卖家精灵）：类目卖家所属地分布、FBA/FBM/亚马逊配送占比、A+ 与视频覆盖率。接入卖家精灵后补充。')

    # 4 同款型竞争格局
    doc.h(2, '4. 同款型竞争格局', 'competition')
    rows = pool.get('rows') or []
    doc.h(3, '4.1 同款型池定义与形态核对')
    counts = {}
    for row in rows + (pool.get('others') or []): counts[row['tier_label']] = counts.get(row['tier_label'], 0) + 1
    doc.p('入池：' + '、'.join(f'{k} {v} 个' for k, v in counts.items()) + f"；未判定候选 {num(pool.get('unlabeled_count'))} 个。" if counts else f"尚无形态判定；未判定候选 {num(pool.get('unlabeled_count'))} 个。")
    if pool.get('label_origin') != 'narrative':
        doc.note(f"同款型池还没做形态判定，目前只有需求门的 {num(pool.get('strict_count'))} 个严格同款。请在 market_narrative.yaml 的 form_review 给候选商品打标签（模板命令会列出候选清单）。")
    if pool.get('others'):
        doc.table(['ASIN', '判定', '理由', '标题'], [(r['asin'], r['tier_label'], r.get('reason'), (r.get('title') or '')[:60]) for r in pool['others'][:15]])
    doc.reading(readings.get('pool'), '同款型的边界划在哪里、为什么')
    doc.h(3, '4.2 竞品数据表')
    doc.table(['#', 'ASIN', '品牌', '售价', '月销', '评论', '星级', '上架日', 'FBA 费', '层级', '规格要点'],
              [(i, r['asin'], r.get('brand'), money(r.get('price')), num(r.get('monthly_sales')), num(r.get('reviews')), num(r.get('rating')),
                r.get('listing_date'), money(r.get('fba_fee')), r['tier_label'], r.get('note') or (r.get('title') or '')[:50]) for i, r in enumerate(rows[:20], 1)],
              empty='同款型池为空。')
    doc.h(3, '4.3 价格带 × 销量矩阵')
    quant = stats.get('price_quantiles') or {}
    doc.chart('bars', title='各价格带销量占比（同款型池）', items=[(b['band'], (_n(b['unit_share']) or 0)*100) for b in stats.get('price_bands') or []], unit='%')
    doc.table(['价格带', 'SKU 数', '月销', '销量占比', '金额占比', '评论中位数'],
              [(b['band'], num(b['skus']), num(b['units']), pct(b['unit_share']), pct(b['revenue_share']), num(b['review_median'])) for b in stats.get('price_bands') or []])
    if any(quant.values()):
        doc.p(f"价格分位（样本 {num(stats.get('skus'))}）：P25 {money(quant.get('p25'))} ｜ P50 {money(quant.get('p50'))} ｜ P75 {money(quant.get('p75'))} ｜ P90 {money(quant.get('p90'))}。")
    doc.p('**定价建议**：' + (text(narrative.get('pricing')) if narrative.get('pricing') else PENDING))
    doc.reading(readings.get('matrix'), '主战场与空档价带')
    doc.h(3, '4.4 头部结构与新链接')
    category_top3 = get(category or {}, 'concentration', 'top3_product')
    doc.table(['指标', '类目 Top100', '同款型池'], [('Top3 商品销量占比', pct(category_top3, False), pct(stats.get('top3_share'))),
                                                   ('评论中位数', '—', num(stats.get('review_median')))])
    if stats.get('age_layers'):
        doc.table(['上架时间', 'SKU 数', '月销', '销量占比'], [(l['layer'], num(l['skus']), num(l['units']), pct(l['unit_share'])) for l in stats['age_layers']])
    doc.table(['低评论但有量的新链接', '品牌', '上架日', '评论', '月销', '售价'],
              [(m['asin'], m.get('brand'), m.get('listing_date'), num(m.get('reviews')), num(m.get('monthly_sales')), money(m.get('price'))) for m in stats.get('new_movers') or []],
              empty='同款型池里没有一年内上架、评论少于 300 且月销达到中位数的新链接。')
    doc.reading(readings.get('head'), '新手窗口读法')
    doc.h(3, '4.5 属性注册表')
    attribute_rows = []
    sales = {r['asin']: _n(r.get('monthly_sales')) or 0 for r in rows}
    total_units = sum(sales.values())
    for attribute in narrative.get('attributes') or []:
        values = attribute.get('values') or {}
        grouped = {}
        for asin in sales:
            grouped.setdefault(values.get(asin) or '未知', []).append(asin)
        for value, members in sorted(grouped.items(), key=lambda x: -sum(sales[a] for a in x[1])):
            units = sum(sales[a] for a in members)
            attribute_rows.append((attribute.get('name'), value, num(len(members)), num(units), pct(units/total_units) if total_units else '—'))
    doc.table(['属性', '取值', 'SKU 数', '月销', '销量占比'], attribute_rows, empty=f'{PENDING}：在 market_narrative.yaml 的 attributes 按标题与主图给同款型池抽取属性（未知的保持未知，不重新分配）。')
    doc.reading(readings.get('attributes'), '哪个属性组合是空白')
    unit = narrative.get('normalized_unit') or {}
    if unit.get('quantities'):
        doc.h(3, '4.6 归一化单价')
        by_asin = {r['asin']: r for r in rows}
        doc.table(['ASIN', '售价', f"标称数量（{unit.get('unit') or '单位'}）", f"每{unit.get('unit') or '单位'}价格"],
                  [(a, money((by_asin.get(a) or {}).get('price')), num(q), money(((by_asin.get(a) or {}).get('price') or 0)/q) if _n(q) else '—') for a, q in unit['quantities'].items()])

    # 5 反向工程
    doc.h(2, '5. Top3–5 反向工程', 'reverse')
    reverse = narrative.get('reverse') or {}
    traffic = overview.get('traffic') or {}
    if not rows: doc.p('同款型池为空，暂不能做反向工程。')
    for row in rows[:5]:
        doc.h(3, f"{row['asin']} · {text(row.get('brand'))}")
        doc.table(['项目', '事实'], [('售价 / 星级 / 评论', f"{money(row.get('price'))} / {num(row.get('rating'))} / {num(row.get('reviews'))}"),
                                    ('月销', num(row.get('monthly_sales'))), ('上架日', text(row.get('listing_date'))), ('变体数', num(row.get('variations'))),
                                    ('包装 / 重量', f"{text(row.get('package'))} / {num(row.get('weight_g'), ' g')}"), ('标题', (row.get('title') or '—')[:160])])
        terms = traffic.get(row['asin']) or []
        if terms:
            organic = sum('organic' in str(t.get('position') or '').lower() for t in terms)
            doc.p(f"流量词（已取 {len(terms)} 个，自然 {organic} 个、广告 {len(terms) - organic} 个），按搜索量前 5：")
            doc.table(['关键词', '月搜索量', '曝光位置'], [(t['keyword'], num(t['searches']), t.get('organic') or t.get('ad') or t.get('position')) for t in terms[:5]])
        item = reverse.get(row['asin']) or {}
        doc.table(['可跟进', '应避开'], [(item.get('follow') or PENDING, item.get('avoid') or PENDING)])

    # 6 关键词与需求
    doc.h(2, '6. 关键词与需求', 'keywords')
    doc.table(['关键词', '月搜索量', '今年同比', '近 12 个月同比', 'CPC', '旺季月'],
              [(k['keyword'], num(k['monthly_searches']), signed(k.get('ytd_yoy')), signed(k.get('yoy_12m')), money(k.get('cpc')), text(k.get('peak_months'))) for k in keywords],
              empty=f'{PENDING_DATA}：还没有关键词详情回包。')
    if core.get('trend'):
        doc.chart('line', title=f"{core['keyword']} 月搜索量", series=[{'name': core['keyword'], 'points': [(m['month'], m['value']) for m in core['trend']]}])
    opportunities = narrative.get('keyword_opportunities') or []
    doc.table(['关键词', '机会判断'], [(o.get('keyword'), o.get('judgement')) for o in opportunities], empty='**机会判断**：' + PENDING)
    doc.reading(readings.get('keywords'), '需求空白、意图偏移的词')
    doc.h(3, '6.2 需求验证（严格同款证据）')
    gate = _gate(facts)
    doc.table(['证据', '已取得 / 要求', '判断'], gate, empty='需求门结果尚未生成。')
    if gate and any(row[2] == '不够' for row in gate):
        doc.p('严格同款证据没达到需求门要求：说明“和目标款完全一样的商品”在市场上的验证还不充分，不代表品类没有需求；正文其余章节按同款型池和类目大盘分析，结论保留这条缺口。')
    doc.reading(readings.get('strict'), '证据缺口影响哪些判断、用户怎么选的')

    # 7 VOC
    doc.h(2, '7. 用户画像与 VOC：需求与痛点', 'voc')
    doc.p(f"评论样本：已留存 {num(reviews.get('total'))} 条（{len(reviews.get('by_asin') or {})} 个 ASIN），其中 1–3 星 {num(reviews.get('low_star'))} 条。计数是方向性样本，不等于全类目投诉率。")
    doc.h(3, '7.1 画像')
    doc.table(['人群', '动机', '典型搜索', '价格敏感', '购买阻断'],
              [(x.get('who'), x.get('motive'), x.get('search'), x.get('price_sensitivity'), x.get('blocker')) for x in narrative.get('personas') or []], empty=PENDING)
    doc.h(3, '7.2 痛点')
    pains = narrative.get('pains') or []
    doc.table(['编号', '主题', '条数', '涉及 ASIN', '严重度', '结构原因', '产品应对'],
              [(x.get('id'), x.get('theme'), num(x.get('count')), text(x.get('asins')), x.get('severity'), x.get('cause'), x.get('response')) for x in pains], empty=PENDING)
    quoted = {}
    for item in pains:
        if item.get('quote'):
            key = _normal(item['quote'])
            if key in quoted:
                doc.p(f"{text(item.get('id'))} {text(item.get('theme'))}：原话同 {quoted[key]}。")
                continue
            quoted[key] = text(item.get('id'))
            doc.p(f"{text(item.get('id'))} {text(item.get('theme'))}：")
            doc.quote(item['quote'], check_quote(item['quote']) if check_quote else None)
    doc.h(3, '7.3 价值点')
    doc.table(['好评方向', '条数', '说明'], [(x.get('theme'), num(x.get('count')), x.get('note')) for x in narrative.get('values') or []], empty=PENDING)
    doc.h(3, '7.4 事实、推断与待验证')
    fi = narrative.get('facts_inference') or {}
    doc.table(['类型', '内容'], [('事实', text(fi.get('facts'))), ('推断', text(fi.get('inferences'))), ('待验证', text(fi.get('to_verify')))] if fi else [], empty=PENDING)
    offsite = narrative.get('offsite') or {}
    threads = [x for x in offsite.get('threads') or [] if isinstance(x, dict) and any(x.values())]
    if threads or offsite.get('none'):  # 可选：Sorftime 没接或没调时整节不出
        doc.h(3, '7.5 站外讨论线索（Reddit / Quora）')
        doc.p('每条是单帖线索，不代表问题有多普遍；要算进痛点条数，须先与亚马逊评论交叉核对。')
        if threads:
            doc.table(['平台', '帖子 ID', '日期', '场景', '遇到的麻烦', '试过的办法', '为什么不满意', '对本品的启示', '相关性'],
                      [(x.get('platform'), x.get('id'), x.get('date'), x.get('scene'), x.get('trouble'), x.get('tried'),
                        x.get('unsatisfied'), x.get('takeaway'), x.get('relevance')) for x in threads])
        if offsite.get('none'):
            doc.p(text(offsite['none']))
    doc.reading(readings.get('voc'), '最该改的痛点')

    # 8 难度
    doc.h(2, '8. 进入难度', 'difficulty')
    doc.h(3, '8.1 结构性难度')
    if difficulty:
        doc.p('难度 = 头部集中度 × 0.4 + 评论壁垒 × 0.35 + 价格战 × 0.25（0.3 以下易，0.3–0.5 中，0.5 以上难）。')
        doc.table(['分项', '原始值', '归一化', '权重', '加权'],
                  [(label, pct(raw) if 'Top3' in label else text(raw) if isinstance(raw, str) else num(raw), num(round(norm, 3)) if norm is not None else '—', num(weight), num(round(norm*weight, 3)) if norm is not None else '—')
                   for label, raw, norm, weight in difficulty['parts']])
        if difficulty.get('score') is not None:
            line = f"合计 {difficulty['score']}（{difficulty['level']}）"
            if difficulty.get('category_score') is not None:
                line += f"；头部集中度改用类目 Top100 口径时为 {difficulty['category_score']}（{difficulty['category_level']}）"
            doc.p(line + '。')
    else:
        doc.p(f'{PENDING_DATA}：同款型池为空，算不出结构性难度。')
    diff = narrative.get('difficulty') or {}
    doc.h(3, '8.2 新品进场难度双法')
    doc.p(diff.get('double_method') or PENDING + '（调用 amazon-competition-difficulty-assessor 后写结论）')
    doc.h(3, '8.3 竞品信号判读')
    doc.table(['ASIN', '判读', '理由'], [(x.get('asin'), x.get('label'), x.get('reason')) for x in diff.get('signals') or []], empty=PENDING + '（调用 amazon-gray-tactic-detector）')
    doc.reading(readings.get('difficulty'), '难在哪、能不能进')

    # 9 SWOT 与机会
    doc.h(2, '9. SWOT、切入点与机会', 'swot')
    swot = narrative.get('swot') or {}
    doc.table(['优势 S', '劣势 W'], [(text(swot.get('S'), PENDING), text(swot.get('W'), PENDING))])
    doc.table(['机会 O', '威胁 T'], [(text(swot.get('O'), PENDING), text(swot.get('T'), PENDING))])
    doc.table(['#', '证据', '产品改法', '关键词', 'Listing / 视觉', '价格', '供应链动作', '广告验证', '失败阈值'],
              [(i, b.get('evidence'), b.get('change'), b.get('keywords'), b.get('listing'), b.get('price'), b.get('supply'), b.get('ad_test'), b.get('fail_threshold'))
               for i, b in enumerate(narrative.get('breakthroughs') or [], 1)], empty='**破局切入点**：' + PENDING)

    # 10 成本
    doc.h(2, '10. 成本边界', 'cost')
    cost_rows = [(label, _criterion(facts, key)) for key, label in (('allowable_landed_cost', '允许到岸成本'), ('pass_hold_stop', '30/60/90 天 Gate'))]
    cost_rows = [row for row in cost_rows if row[1]]
    doc.table(['项目', '当前结果'], cost_rows, empty='第 6 步成本测算后回填；详细数字见成本报告。')
    if narrative.get('cost_summary'): doc.p(text(narrative['cost_summary']))

    # 11 结论
    doc.h(2, '11. 结论、最强反方与翻转条件', 'conclusion')
    conclusion = narrative.get('conclusion') or {}
    doc.p('**建议结论**：' + (text(summary.get('verdict')) if summary.get('verdict') else PENDING + '（必填）') + ('。' + text(conclusion.get('verdict_text')) if conclusion.get('verdict_text') else ''))
    doc.ul(['前提：' + text(x) for x in conclusion.get('premises') or []] + (['首批建议：' + text(conclusion['first_batch'])] if conclusion.get('first_batch') else []))
    recorded = [x.get('value') if isinstance(x, dict) else x for x in (get(inputs, 'demand', 'counterevidence') or [])]
    if recorded: doc.p('需求研究已记录的反证：' + '；'.join(text(x) for x in recorded if x) + '。')
    counter = narrative.get('counterargument') or {}
    doc.table(['最强反方', '内容'], [('反驳论点', counter.get('argument') or PENDING + '（必填）'), ('可证伪触发', counter.get('trigger') or PENDING),
                                     ('触发后动作', counter.get('action') or PENDING)])

    # 附录
    gaps = list(overview.get('gaps') or [])
    labels = {'category_comparison': '相邻类目比较', 'competitors': '同款竞品详情', 'variations': '变体明细', 'reviews': '同款评论',
              'monthly_trend': '同款逐月趋势', 'keyword_signals': '关键词需求', 'peak_top100': '旺季 Top100', 'organic_top5': '自然前 5', 'ad_keyword_count': '广告词证据'}
    for key, node in ((facts or {}).get('coverage') or {}).items():
        if isinstance(node, dict) and node.get('status') not in ('met', 'not_applicable'):
            gaps.append({'item': labels.get(key, key), 'impact': f"已取得 {num(node.get('observed'))}，要求 {num(node.get('required'))}", 'how': f"补拉 {node.get('tool') or '对应工具'}"})
    doc.h(2, '附录', 'appendix')
    doc.h(3, 'A. 数据缺口（缺什么 → 影响什么 → 怎么补）')
    doc.table(['缺什么', '影响', '怎么补'], [(g['item'], g['impact'], g['how']) for g in gaps], empty='当前没有影响本报告的数据缺口。')
    sources = sorted({s for s in [get(category or {}, 'source'), get(trend or {}, 'source')] + [a['source'] for a in overview.get('adjacent') or []] if s})
    manifest = [(row.get('source'), row.get('sha256')) for row in (facts or {}).get('source_manifest') or [] if isinstance(row, dict) and row.get('source')]
    lines = ['- ' + s for s in sources] + [f'- {path}（{digest}）' if digest else f'- {path}' for path, digest in manifest]
    doc.details('B. 来源文件与哈希', lines or ['- 暂无'])
    return doc


def _criterion(facts, key):
    node = ((facts or {}).get('criteria') or {}).get(key)
    if isinstance(node, dict) and node.get('status') == 'available' and node.get('value') not in (None, ''):
        return text(node.get('value'))
    return None


# ---------- HTML（统一报告样式：github-tabler-html-report 的 Report，左侧电梯导航） ----------

GROUPS = [('overview', '先看结论'), ('market', '类目大盘'), ('competition', '竞争格局'),
          ('demand', '需求与用户'), ('decision', '判断与结论'), ('evidence', '附录')]
SECTION_GROUPS = {'summary': 'overview', 'sources': 'overview', 'category': 'market', 'competition': 'competition',
                  'reverse': 'competition', 'keywords': 'demand', 'voc': 'demand', 'difficulty': 'decision',
                  'swot': 'decision', 'cost': 'decision', 'conclusion': 'decision', 'appendix': 'evidence'}


def _renderer():
    import importlib.util
    import sys
    path = Path(__file__).resolve().parents[1]/'skills/github-tabler-html-report/assets/salon_report.py'
    key = '_starsky_market_salon_report'
    if key not in sys.modules:
        previous = list(sys.path)
        try:
            sys.path.insert(0, str(path.parent))  # s1_product_front ships next to salon_report
            # Source checkout: s1_product_front still lives in release-overrides until the build copies it.
            overrides = next((parent / 'release-overrides' for parent in path.parents if (parent / 'release-overrides/s1_product_front.py').is_file()), None)
            if overrides: sys.path.insert(1, str(overrides))
            spec = importlib.util.spec_from_file_location(key, path)
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        finally:
            sys.path[:] = previous
        sys.modules[key] = module
    module = sys.modules[key]
    module.STAGE_GROUPS.setdefault('MARKET', GROUPS)
    return module


def _inline(value):
    value = html.escape(str(value), quote=False)
    value = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', value)
    return re.sub(r'`([^`]+)`', r'<code>\1</code>', value)


def _svg_line(series, title):
    points = series[0]['points'] if series else []
    values = [v for _, v in points if v is not None]
    if len(values) < 2: return ''
    width, height, pad = 720, 220, 36
    top = max(values) or 1
    step = (width - 2*pad)/(len(points) - 1)
    coords = [(pad + i*step, height - pad - (v/top)*(height - 2*pad)) for i, (_, v) in enumerate(points)]
    path = ' '.join(('M' if i == 0 else 'L') + f'{x:.1f},{y:.1f}' for i, (x, y) in enumerate(coords))
    muted = 'font-size:11px;fill:var(--muted)'
    labels = ''.join(f'<text x="{x:.1f}" y="{height - 10}" style="{muted}">{html.escape(points[i][0][2:])}</text>'
                     for i, (x, _) in enumerate(coords) if i % max(1, len(points)//8) == 0 or i == len(points) - 1)
    dots = ''.join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.5" style="fill:var(--accent)"><title>{html.escape(points[i][0])}：{num(points[i][1])}</title></circle>'
                   for i, (x, y) in enumerate(coords))
    return (f'<figure style="margin:10px 0 14px"><figcaption style="font-size:13px;color:var(--muted);margin-bottom:6px">{html.escape(title)}</figcaption>'
            f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title)}" style="width:100%;height:auto">'
            f'<line x1="{pad}" y1="{height - pad}" x2="{width - pad}" y2="{height - pad}" style="stroke:var(--line)"/>'
            f'<text x="{pad}" y="{pad - 12}" style="{muted}">峰值 {num(top)}</text>'
            f'<path d="{path}" style="fill:none;stroke:var(--accent);stroke-width:2.2"/>{dots}{labels}</svg></figure>')


def to_html(doc, title):
    salon = _renderer()
    head_title = next((b[2] for b in doc.blocks if b[0] == 'h' and b[1] == 1), title)
    intro = []
    for block in doc.blocks:
        if block[0] == 'h' and block[1] == 2: break
        if block[0] == 'p': intro.append(block[1])
    report = salon.Report(head_title, intro[0] if intro else '', meta=[], stage='MARKET')
    section = None
    for block in doc.blocks:
        kind = block[0]
        if kind == 'h':
            level, heading, anchor = block[1], block[2], block[3]
            if level == 2:
                sid = anchor or 'section-' + str(len(report.sections) + 1)
                section = report.section(sid, heading, group=SECTION_GROUPS.get(sid, 'evidence'))
            elif level >= 3 and section is not None:
                section.h3(heading)
            continue
        if section is None:
            continue  # title/intro are already in the header; warnings are repeated below
        if kind == 'p' and block[1]: section.p(_inline(block[1]))
        elif kind == 'note': section.raw(f'<div class="callout warn">{_inline(block[1])}</div>')
        elif kind == 'reading':
            body = _inline(block[1]) if block[1] else f'{PENDING}' + (f'（{html.escape(block[2])}）' if block[2] else '')
            section.raw(f'<div class="callout {"info" if block[1] else "warn"}"><b>读法</b>　{body}</div>')
        elif kind == 'ul' and block[1]: section.raw('<ul>' + ''.join(f'<li>{_inline(i)}</li>' for i in block[1]) + '</ul>')
        elif kind == 'table':
            headers, rows, empty = block[1], block[2], block[3]
            if not rows:
                section.p(f'<span style="color:var(--muted)">{_inline(empty or "暂无可核实记录。")}</span>'); continue
            section.table(headers, [[_inline(c) for c in row] for row in rows], searchable=len(rows) > 20)
        elif kind == 'quote':
            mark = '' if block[2] is None else ('<span class="pill ok">已核对原话</span>' if block[2] else '<span class="pill warn">原话待核</span>')
            section.raw(f'<blockquote>{html.escape(text(block[1]))} {mark}</blockquote>')
        elif kind == 'kpis':
            section.kpis([(label, html.escape(value), sub, tone) for label, value, sub, tone in block[1]])
        elif kind == 'chart':
            data = block[2]
            if block[1] == 'line':
                section.raw(_svg_line(data['series'], data['title']))
            else:
                items = [(name, value) for name, value in data['items'] if value is not None]
                if items:
                    section.raw(f'<div style="font-size:13px;color:var(--muted);margin:10px 0 4px">{html.escape(data["title"])}</div>')
                    top = max(v for _, v in items) or 1
                    for name, value in items:
                        section.bar(str(name), value, top, right=f"{num(round(value, 1))}{data.get('unit', '')}")
        elif kind == 'details':
            items = ''.join(f'<li>{html.escape(line.lstrip("- "))}</li>' for line in block[2])
            section.details(block[1], f'<ul style="font-size:12px;word-break:break-all">{items}</ul>')
    return report.render()


# ---------- 入口 ----------

def render(root, facts=None, inputs=None, narrative=None, today=None):
    """Return (markdown, html) for a product root. facts defaults to 01_market_facts.json."""
    root = Path(root)
    if facts is None:
        path = root/MARKET/'01_market_facts.json'
        facts = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    narrative = narrative if narrative is not None else load_narrative(root)
    inputs = inputs if inputs is not None else _read_yaml(root/'s1_decision_inputs.yaml')
    # Always recompute: the pool depends on the latest AI form labels, the numbers on saved replies.
    overview = build_overview(root, facts, narrative, today)
    profile = _read_yaml(root/MARKET/'01_product_profile.yaml')
    state = _read_yaml(root/'workflow_state.yaml')
    product = profile.get('product_name') or profile.get('product_type') or get(state, 'product', 'name') or state.get('product_name')
    doc = build_document(facts, overview, narrative, inputs, check_quote=quote_checker(root), product_name=product)
    return to_markdown(doc), to_html(doc, '市场分析报告')


def render_from_facts(facts, inputs=None, narrative=None):
    """Root-less rendering used by legacy callers and selftests."""
    overview = (facts or {}).get('market_overview') or {'pool': None, 'gaps': [], 'as_of': date.today().isoformat()}
    return to_markdown(build_document(facts or {}, overview, narrative or {}, inputs or {}))


def write_reports(root, facts=None, inputs=None, narrative=None):
    root = Path(root)
    markdown, page = render(root, facts, inputs, narrative)
    (root/REPORT_MD).parent.mkdir(parents=True, exist_ok=True)
    (root/REPORT_HTML).write_text(page, encoding='utf8')
    return markdown


def template(root):
    root = Path(root)
    path = root/MARKET/'01_market_facts.json'
    facts = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    narrative = load_narrative(root)
    overview = build_overview(root, facts, narrative)
    candidates = overview['pool']['unlabeled_sample']
    skeleton = {
        'schema': 'starsky.market_narrative.v1',
        'target_node_id': overview.get('target_node_id') or '目标类目 node_id',
        'summary': {'verdict': '必填：' + ' / '.join(VERDICTS), 'selected_form': '', 'spec': '', 'price': '',
                    'breakthrough': '必填：一句话破局', 'site_reason': ''},
        'readings': {key: '' for key in READINGS},
        'form_review': [{'asin': row['asin'], 'tier': 'same_type / reference / excluded 三选一', 'reason': '',
                         'spec_note': '', 'title_hint': row.get('title')} for row in candidates],
        'pricing': '', 'attributes': [{'name': '属性名', 'values': {'ASIN': '取值'}}],
        'normalized_unit': {'unit': '', 'quantities': {}},
        'reverse': {}, 'keyword_opportunities': [{'keyword': '', 'judgement': ''}],
        'personas': [{'who': '', 'motive': '', 'search': '', 'price_sensitivity': '', 'blocker': ''}],
        'pains': [{'id': 'P-001', 'theme': '', 'count': 0, 'asins': [], 'severity': '', 'quote': '评论原话（必须与留存评论一致）', 'cause': '', 'response': ''}],
        'values': [{'theme': '', 'count': 0, 'note': ''}],
        'facts_inference': {'facts': [], 'inferences': [], 'to_verify': []},
        'offsite': {'threads': [], 'none': ''},
        'difficulty': {'double_method': '', 'signals': [{'asin': '', 'label': '可参照 / 仅证明市场存在 / 不可参照', 'reason': ''}]},
        'swot': {'S': [], 'W': [], 'O': [], 'T': []},
        'breakthroughs': [{'evidence': '', 'change': '', 'keywords': '', 'listing': '', 'price': '', 'supply': '', 'ad_test': '', 'fail_threshold': ''}],
        'conclusion': {'verdict_text': '', 'premises': [], 'first_batch': ''},
        'counterargument': {'argument': '必填：最强反驳论点', 'trigger': '', 'action': ''},
        'cost_summary': ''}
    header = ('# market_narrative.yaml 填写骨架。只写判断与读法，数字由脚本从回包计算。\n'
              '# 已有内容会保留：本骨架只用于新建；修改已有文件请只改对应字段。\n'
              '# offsite 可选（Sorftime Reddit/Quora）：threads 每条 platform/id/date/scene/trouble/tried/unsatisfied/takeaway/relevance；\n'
              '#   调了但没相关结果写 none（含用过的搜索词）；Sorftime 没接或没调就留空，报告不出这一节。\n'
              '# form_review 已列出与查询词最相关、再按月销排序的前 40 个未判定候选；title_hint 仅供参考，可删除。\n'
              '# 写入方式（紧凑案例）：r01_native.py execute --step <3或4>，inputs 里放本文件的 yaml。\n')
    return header + yaml.safe_dump(skeleton, allow_unicode=True, sort_keys=False, width=1000)


def check(root):
    root = Path(root)
    narrative = load_narrative(root)
    checker = quote_checker(root)
    readings = narrative.get('readings') or {}
    result = {'narrative_exists': (root/NARRATIVE).is_file(), 'missing_required': missing_required(narrative),
              'pending_readings': [key for key in READINGS if not readings.get(key)],
              'unverified_quotes': [item.get('id') for item in narrative.get('pains') or [] if item.get('quote') and checker(item['quote']) is False]}
    path = root/MARKET/'01_market_facts.json'
    facts = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    overview = build_overview(root, facts, narrative)
    result['data_gaps'] = [g['item'] + '：' + g['how'] for g in overview['gaps']]
    result['ok'] = not result['missing_required']
    return result


if __name__ == '__main__':
    import argparse
    import sys
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('action', choices=('template', 'check', 'render'))
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    if args.action == 'template':
        print(template(args.root))
    elif args.action == 'check':
        outcome = check(args.root)
        print(json.dumps(outcome, ensure_ascii=False, indent=2))
        sys.exit(0 if outcome['ok'] else 2)
    else:
        from s1_step_refresh import refresh_market
        print(json.dumps(refresh_market(args.root), ensure_ascii=False))
