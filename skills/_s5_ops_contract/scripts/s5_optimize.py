#!/usr/bin/env python3
"""S5 第一部分：手动报表驱动的广告 + 文案双链路优化（不连 API、不改后台）。

用户从广告后台「批量操作」按日期下载表格（勾选 SP 搜索词数据），放进
S5_运营板块/_投喂_广告报表/ 或直接给路径。本脚本：

  ingest    读下载表：活动/广告组/关键词/投放/商品广告/否定词/广告组合 + 搜索词，全部带后台 ID；按本品或全店筛范围
  diagnose  按本品利润账（保本 / 目标 ACOS、客单价、CVR）跑规则 → 动作候选；读 S2 现文案做埋词缺口；
            生成 evidence.yaml（助手填广告知识库 KC 号、SIF / 西柚证据）和 listing_proposal.yaml（助手写改后文案）
  report    合并证据 → 03_优化前后对比.html / .md（改什么、改前改后、为什么、依据）；退出码 2 = 必须改
  approve   记录用户审批（全部 / 部分 / 拒绝）
  bulk      只把批准的动作写进官方批量模板（真实 ID 更新、新词带广告组合）→ 05_上传包/ + 改文案页
  receipt   用户上传后回执 → 72 小时看方向、7 天看转化，下一轮从 ingest 开始

  python s5_optimize.py ingest --run "<产品根>" [--input <文件或文件夹>] [--scope product|store] [--sku X] [--asin B0…]
"""

from __future__ import annotations

import argparse
import hashlib
import os
import html
import importlib.util
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import warnings

import openpyxl
import yaml

warnings.filterwarnings('ignore', message='Workbook contains no default style')

HERE = Path(__file__).resolve()
SKILLS = HERE.parents[2]
BULK_SKILL = SKILLS / 'amazon-bulk-upload'

DROP = 'S5_运营板块/_投喂_广告报表'
WORK = 'S5_运营板块/amazon-ad-optimization'
S4_PLAN = 'S4_广告板块/amazon-ad-campaign-builder/ad_plan.yaml'
S4_BRIEF = 'S4_广告板块/amazon-ad-strategy-architect/ad_strategy_brief.yaml'
S2_LISTING = 'S2_文案板块/amazon-listing-copywriter/07B_Listing校验输入.json'
BLACKLIST = 'S1_选品板块/amazon-ip-risk-assessment/04_compliance_blacklist.yaml'
PRODUCT_CT = SKILLS / '1688-product-detail-scraper' / 'scripts' / 'product_ct.py'
GAP = HERE.parent / 'listing_keyword_gap.py'

THIN_CLICKS = 10
DEFAULT_CVR = 0.10
METRICS = ('impressions', 'clicks', 'spend', 'sales', 'orders', 'units')
SHARED_NOTICE = '含共享活动，汇总绩效不能当作单品表现；共享活动下的候选保持暂缓。'
HEADER = {
    'product': 'product', 'entity': 'entity', 'operation': 'operation', 'campaign id': 'campaign_id',
    'ad group id': 'ad_group_id', 'portfolio id': 'portfolio_id', 'ad id': 'ad_id', 'keyword id': 'keyword_id',
    'product targeting id': 'product_targeting_id', 'targeting id': 'product_targeting_id',
    'campaign name': 'campaign_name', 'ad group name': 'ad_group_name', 'portfolio name': 'portfolio_name',
    'state': 'state', 'campaign state': 'campaign_state', 'ad group state': 'ad_group_state',
    'daily budget': 'daily_budget', 'sku': 'sku', 'asin': 'asin', 'ad group default bid': 'ad_group_default_bid',
    'bid': 'bid', 'keyword text': 'keyword_text', 'match type': 'match_type', 'bidding strategy': 'bidding_strategy',
    'targeting type': 'targeting_type', 'product targeting expression': 'product_targeting_expression',
    'resolved product targeting expression': 'resolved_expression', 'customer search term': 'search_term',
    'search term': 'search_term', 'impressions': 'impressions', 'clicks': 'clicks', 'spend': 'spend', 'cost': 'spend',
    'sales': 'sales', 'orders': 'orders', 'units': 'units', 'sales7d': 'sales', 'purchases7d': 'orders',
    'unitssoldclicks7d': 'units', 'start date': 'start_date', 'end date': 'end_date',
    'placement': 'placement', 'percentage': 'percentage', '广告位': 'placement', '百分比': 'percentage',
    '7 day total sales': 'sales', '14 day total sales': 'sales', '7 day total orders (#)': 'orders',
    '14 day total orders (#)': 'orders', '7 day total units (#)': 'units', 'targeting': 'keyword_text',
    'portfolio name': 'portfolio_name', 'asin': 'asin',
    # 中文后台（批量下载 + 商品推广搜索词报告）
    '产品': 'product', '实体层级': 'entity', '操作': 'operation', '广告活动编号': 'campaign_id', '广告组编号': 'ad_group_id',
    '广告组合编号': 'portfolio_id', '广告编号': 'ad_id', '关键词编号': 'keyword_id', '商品投放 id': 'product_targeting_id',
    '商品投放 ID': 'product_targeting_id', '广告活动名称': 'campaign_name', '广告组名称': 'ad_group_name',
    '广告组合名称': 'portfolio_name', '状态': 'state', '每日预算': 'daily_budget', '广告组默认竞价': 'ad_group_default_bid',
    '竞价': 'bid', '关键词文本': 'keyword_text', '匹配类型': 'match_type', '竞价方案': 'bidding_strategy',
    '投放类型': 'targeting_type', '拓展商品投放编号': 'product_targeting_expression',
    '已解析的投放表达式': 'resolved_expression', '顾客搜索词': 'search_term', '客户搜索词': 'search_term', '投放': 'keyword_text',
    '展示量': 'impressions', '点击量': 'clicks', '花费': 'spend', '销量': 'sales', '订单数量': 'orders', '商品数量': 'units',
    '7天总销售额': 'sales', '7天总订单数(#)': 'orders', '7天总销售量(#)': 'units', '开始日期': 'start_date', '结束日期': 'end_date',
}
# 批量表里的中文取值 → 内部英文（写回时再按下载表的语言翻回去）
ENTITY_ZH = {'广告活动': 'Campaign', '广告组': 'Ad Group', '商品广告': 'Product Ad', '关键词': 'Keyword',
             '否定关键词': 'Negative Keyword', '广告活动否定关键词': 'Campaign Negative Keyword', '商品定向': 'Product Targeting',
             '否定商品定向': 'Negative Product Targeting', '竞价调整': 'Bidding Adjustment', '广告组合': 'Portfolio'}
STATE_ZH = {'已启用': 'enabled', '已暂停': 'paused', '已归档': 'archived'}
MATCH_ZH = {'精准': 'exact', '词组': 'phrase', '广泛': 'broad', '否定精准匹配': 'negativeExact', '否定词组': 'negativePhrase'}
TARGETING_ZH = {'自动': 'AUTO', '手动': 'MANUAL'}
STRATEGY_ZH = {'动态竞价 - 仅降低': 'Dynamic bids - down only', '动态竞价 - 提高和降低': 'Dynamic bids - up and down', '固定竞价': 'Fixed bid'}
OPERATION_ZH = {'创建': 'Create', '更新': 'Update', '归档': 'Archive'}
PRODUCT_ZH = {'商品推广': 'Sponsored Products', '品牌推广': 'Sponsored Brands', '展示型推广': 'Sponsored Display'}
# 广告位统一成批量表 Config 里的英文值；报表 / 中文表 / 领星各有叫法
PLACEMENT_KEYS = {
    'placementTop': ('placementTop', 'placement top', 'top of search on-amazon', 'placement_top', '广告位：搜索结果首页首位', '搜索结果顶部'),
    'placementProductPage': ('placementProductPage', 'placement product page', 'detail page on-amazon', 'placement_product_page', '广告位：商品页面', '商品页面'),
    'placementRestOfSearch': ('placementRestOfSearch', 'placement rest of search', 'other on-amazon', 'placement_rest_of_search', '广告位：搜索结果的其余位置', '搜索结果的其余位置'),
    'placementAmazonBusiness': ('placementAmazonBusiness', 'placement amazon business', 'site_amazon_business', '广告位亚马逊企业购', '亚马逊企业购'),
}
PLACEMENT_CN = {'placementTop': '搜索顶部', 'placementProductPage': '商品页面', 'placementRestOfSearch': '搜索其余位置', 'placementAmazonBusiness': '企业购'}


def place_key(value):
    text = str(value or '').strip().lower()
    for key, names in PLACEMENT_KEYS.items():
        if text in {n.lower() for n in names}:
            return key
    return None


ACTION_LABEL = {'bid_down': '降价', 'bid_up': '加价', 'pause': '暂停', 'harvest_exact': '出单词转精准',
                'negative_exact': '否定精准', 'negative_phrase_ct': '否定词组（产品 CT）', 'negative_asin': '否定 ASIN',
                'budget_up': '加预算', 'budget_down': '降预算', 'placement_up': '广告位加价', 'placement_down': '广告位降价'}
RULES = {
    'K2': '花了钱没出单，点击已够判断一半：先降 30% 看看',
    'K3': '花费已超过两单的目标成本还没出单：暂停',
    'K4': '出单了但 ACOS 高于目标：按目标 ACOS 折算降价',
    'K5': '出单稳定且 ACOS 明显低于目标：加价抢位，不超过出价天花板',
    'S1': '搜索词出了 2 单以上且还不是精准词：加进精准组放大（源头否定等下一轮确认精准组跑起来再做）',
    'S2': '搜索词点击够多、花费超过一单目标成本还没出单：在该广告组否定精准',
    'S3': '搜索的是别人家 ASIN、点击够多没出单：否定这个 ASIN',
    'C1': '搜索词带产品 CT 里「本品不是」的属性词（别的材质/形态）：否定词组',
    'K6': '活动整体 ACOS 超目标、花费已够，但下面单个词样本都不够：整组按比例降价',
    'M1': '人工指定（用户或助手按知识库判定明显不相关 / 需要处理，写明理由后走同样的审批）',
    'B1': '预算基本花满、有转化且 ACOS 达标：加预算 30%（预算跟着已验证的转化走）',
    'B2': '预算基本花满但 ACOS 超目标 2 倍以上或花了两单成本还没单：降预算 30%，钱挪给达标活动',
    'P1': '这个广告位出单稳、ACOS 明显低于目标：加价 10 个百分点抢更多展示',
    'P2': '这个广告位有加价但 ACOS 超目标 1.5 倍（或花了两单成本没单）：加价减半',
}
AUTO_NAMES = {'queryhighrelmatches': '紧密匹配', 'close-match': '紧密匹配', 'querybroadrelmatches': '宽泛匹配', 'loose-match': '宽泛匹配',
              'asinsubstituterelated': '同类商品', 'substitutes': '同类商品', 'asinaccessoryrelated': '关联商品', 'complements': '关联商品'}


def display_name(text):
    key = str(text or '').strip().rstrip('=').lower()
    return AUTO_NAMES.get(key, text)


# ---------- 小工具 ----------

def now():
    return datetime.now(timezone.utc).astimezone()


def load(path):
    path = Path(path)
    if not path.is_file():
        return {}
    text = path.read_text(encoding='utf-8-sig')
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return yaml.safe_load(text) or {}


def dump_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def num(value):
    if value in (None, '', '-', '—'):
        return None
    try:
        return float(str(value).replace(',', '').replace('$', '').replace('%', '').strip())
    except ValueError:
        return None


def ident(value):
    """后台 ID：Excel 可能读成浮点或科学计数，一律转回纯数字字符串。"""
    if value in (None, ''):
        return ''
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    return text[:-2] if re.fullmatch(r'\d+\.0', text) else text


def norm_header(value):
    text = re.sub(r'\s*[（(]\s*(informational only|仅供参考)\s*[)）]\s*', '', str(value or ''), flags=re.I).strip()
    return HEADER.get(text) or HEADER.get(text.lower())


def english(row):
    """中文下载表的取值统一成英文内部值。"""
    for key, table in (('entity', ENTITY_ZH), ('state', STATE_ZH), ('match_type', MATCH_ZH), ('targeting_type', TARGETING_ZH),
                       ('bidding_strategy', STRATEGY_ZH), ('operation', OPERATION_ZH), ('product', PRODUCT_ZH)):
        value = row.get(key)
        if isinstance(value, str) and value.strip() in table:
            row[key] = table[value.strip()]
    return row


def ratio(a, b):
    return round(a / b, 4) if a is not None and b else None


def money(value):
    return None if value is None else round(float(value) + 1e-9, 2)


def is_asin(text):
    return bool(re.fullmatch(r'b0[a-z0-9]{8}', str(text or '').strip().lower()))


def toks(text):
    return re.findall(r'[a-z0-9]+', str(text or '').lower())


def has_phrase(tokens, phrase):
    p = toks(phrase)
    return bool(p) and any(tokens[i:i + len(p)] == p for i in range(len(tokens) - len(p) + 1))


def module(path, name):
    if str(Path(path).parent) not in sys.path:
        sys.path.insert(0, str(Path(path).parent))  # bulk_core 要从同目录导入 campaign_compiler
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------- 轮次 ----------

def work(root):
    return Path(root) / WORK


def current_round(root):
    pointer = load(work(root) / 'current.json')
    if not pointer.get('round'):
        raise SystemExit(json.dumps({'status': 'fail', 'error': '还没有本轮数据：先 ingest'}, ensure_ascii=False))
    return work(root) / pointer['round']


def new_round(root):
    base = work(root)
    base.mkdir(parents=True, exist_ok=True)
    n = 1 + max([int(m.group(1)) for p in base.iterdir() if (m := re.match(r'R(\d+)_', p.name))] or [0])
    folder = base / f'R{n:02d}_{now():%Y%m%d}'
    folder.mkdir()
    dump_json(base / 'current.json', {'round': folder.name, 'created_at': now().isoformat()})
    return folder


# ---------- ingest ----------

def read_rows(path):
    """Yield each sheet and a one-pass row iterator; never trust worksheet dimensions."""
    import csv
    import time

    def sheet(title, source):
        source = iter(source)
        header = next(source, None)
        if header is None:
            return None
        keys = [norm_header(h) for h in header]
        if not any(keys):
            return None

        def records():
            started = last = time.monotonic()
            for n, values in enumerate(source, 1):
                if n % 10000 == 0 and time.monotonic() - last >= 10:
                    print(f'[读取报表] {path.name} / {title}：已读 {n:,} 行，{time.monotonic()-started:.1f} 秒', file=sys.stderr, flush=True)
                    last = time.monotonic()
                if not any(v not in (None, '') for v in values):
                    continue
                row = {}
                for k, v in zip(keys, values):
                    if k and (k not in row or row[k] in (None, '')):
                        row[k] = v
                yield english(row)
        return title, keys, records(), [str(h or '') for h in header]

    if path.suffix.lower() in {'.csv', '.tsv'}:
        with path.open(encoding='utf-8-sig', newline='') as fh:
            item = sheet(path.stem, csv.reader(fh, delimiter='\t' if path.suffix.lower() == '.tsv' else ','))
            if item:
                yield item
    else:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            for ws in wb.worksheets:
                ws.reset_dimensions()
                item = sheet(ws.title, ws.iter_rows(values_only=True))
                if item:
                    yield item
        finally:
            wb.close()


def attribution_window(headers):
    """Return only windows actually named by source metric columns."""
    found = set()
    for value in headers:
        name = str(value).lower()
        if not any(x in name for x in ('sales', 'orders', 'purchases', 'units', '销售', '订单', '销量', '购买')):
            continue
        match = re.search(r'(7|14|30)\s*(?:day|天|d\b)', name)
        if match:
            found.add(int(match.group(1)))
    return sorted(found)


def window_notice(data):
    windows = data.get('data_windows') or {}
    period = windows.get('export_period') or data.get('period') or {}
    days = windows.get('attribution_days')
    attribution = f'{days} 天（源字段明确）' if days else '待核（原字段未注明或口径混合）'
    return f"导出区间：{period.get('start') or '待核'} 至 {period.get('end') or '待核'}；归因窗：{attribution}；本快照为区间汇总，未保留逐日序列，不能据此做 T+3/T+7 观察。"


def aggregate_terms(rows):
    """单独下载的商品推广搜索词报告按天分行：按活动/组/投放/匹配/搜索词合并。"""
    merged = {}
    for r in rows:
        key = tuple(str(r.get(k) or '') for k in ('campaign_name', 'ad_group_name', 'keyword_text', 'match_type', 'search_term'))
        if key not in merged:
            merged[key] = dict(r, **{m: 0.0 for m in METRICS})
        for m in METRICS:
            merged[key][m] += num(r.get(m)) or 0.0
    return list(merged.values())


def metrics_of(row):
    return {m: (num(row.get(m)) or 0.0) for m in METRICS}


def bundled_header():
    """没有用户下载表时（领星取数），上传表照插件内置官方模板的英文表头写。"""
    path = HERE.parents[2] / 'amazon-bulk-upload' / 'AmazonAdvertisingBulksheetSellerTemplate.xlsx'
    wb = openpyxl.load_workbook(path, read_only=True)
    ws = wb['Sponsored Products Campaigns']
    ws.reset_dimensions()
    header = [str(h or '') for h in next(ws.iter_rows(values_only=True))]
    wb.close()
    return {'path': str(path), 'sheet': 'Sponsored Products Campaigns', 'header': header, 'language': 'en', 'kind': 'bundled_template'}


def ingest(args):
    if getattr(args, 'source', 'file') == 'lingxing':
        if not args.sid or not args.start or not args.end:
            return {'status': 'blocked', 'error': '领星取数要 --sid（店铺）和 --start / --end（日期）'}, 2
        src = module(HERE.parent / 's5_lingxing_source.py', 's5_lingxing_source')
        if getattr(args, 'parent', None):  # 父体：一次优化它下面所有在售变体
            try:
                kids, unknown = src.children_of(int(args.sid), args.parent)
            except (RuntimeError, SystemExit, OSError) as exc:
                return {'status': 'blocked', 'error': f'领星取父体下的变体失败：{exc}'}, 2
            if not kids:
                return {'status': 'blocked', 'error': '领星里没找到这个父体 / ASIN 的在售变体：核对 ASIN 和店铺（sid）', 'looked_for': unknown}, 2
            args.scope = 'product'
            args.asin = sorted({k['asin'] for k in kids if k['asin']} | {a.upper() for a in (args.asin or [])})
            args.sku = sorted({k['sku'] for k in kids if k['sku']} | set(args.sku or []))
            args.family = {'parents': sorted({k['parent'] for k in kids}), 'children': kids, 'unknown': unknown}
        try:
            entities_lx, terms_lx, portfolios_lx, meta = src.collect(int(args.sid), args.start, args.end)
        except (RuntimeError, SystemExit, OSError) as exc:
            return {'status': 'blocked', 'error': f'领星取数失败：{exc}'}, 2
        try:
            products = src.collect_products(int(args.sid), args.start, args.end)
        except (RuntimeError, SystemExit, OSError) as exc:
            products = {'error': f'产品级数据没拉到：{exc}'}
        return build_snapshot(args, defaultdict(list, entities_lx), terms_lx, [], portfolios_lx,
                              [{'source': 'lingxing', **meta}], bundled_header(), {'start': args.start, 'end': args.end},
                              extra={'products': products})
    root = Path(args.run)
    sources = [Path(p) for p in (args.input or [])] or [root / DROP]
    files = []
    for src in sources:
        if src.is_dir():
            files += sorted(p for p in src.rglob('*') if p.suffix.lower() in {'.xlsx', '.xlsm', '.csv', '.tsv'} and not p.name.startswith('~$'))
        elif src.is_file():
            files.append(src)
    if not files:
        return {'status': 'blocked', 'error': f'没找到报表：把广告后台「批量操作」下载的表格放进 {DROP}/，或 --input 给路径'}, 2
    entities = defaultdict(list)
    bulk_terms, report_terms, portfolios, file_refs = [], [], {}, []
    download = None  # 批量下载表：写上传表时照它的语言和表头
    period = {}
    for path in files:
        kinds = []
        windows = set()
        unknown_metric_window = False
        source_has_dates = False
        m = re.search(r'bulk-[^-]+-(\d{8})-(\d{8})', path.name)
        if m:
            period = {'start': f'{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}', 'end': f'{m.group(2)[:4]}-{m.group(2)[4:6]}-{m.group(2)[6:]}'}
        for title, keys, rows, raw_header in read_rows(path):
            windows.update(attribution_window(raw_header))
            for key, header in zip(keys, raw_header):
                if key in ('sales', 'orders', 'units') and not attribution_window([header]):
                    unknown_metric_window = True
            source_has_dates = source_has_dates or any(str(h).strip().lower() in ('date', '日期') for h in raw_header)
            if 'entity' in keys:
                counts = Counter()
                for row in rows:
                    entity = str(row.get('entity') or '').strip()
                    if entity == 'Portfolio':
                        portfolios[ident(row.get('portfolio_id'))] = str(row.get('portfolio_name') or '')
                        continue
                    if row.get('product') not in (None, '', 'Sponsored Products'):
                        counts[str(row.get('product'))] += 1
                        continue
                    entities[entity].append(row)
                    counts['Sponsored Products'] += 1
                if counts.get('Sponsored Products'):
                    download = {'path': str(path), 'sheet': title, 'header': raw_header,
                                'language': 'zh' if '实体层级' in raw_header else 'en'}
                for product, n in counts.items():
                    if product != 'Sponsored Products':
                        entities['__other__'].append({'sheet': title, 'product': product, 'rows': n})
                kinds.append(f'{title}:{dict(counts) or "portfolios"}')
            elif 'search_term' in keys:
                sp = [r for r in rows if r.get('product') in (None, '', 'Sponsored Products')]
                (bulk_terms if 'campaign_id' in keys else report_terms).extend(dict(r, _sheet=title) for r in sp)
                kinds.append(f'{title}:search_terms:{len(sp)}' + ('' if 'campaign_id' in keys else '（无 ID 的搜索词报告）'))
        file_refs.append({'path': str(path), 'sha256': sha(path), 'sheets': kinds,
                          'attribution_days': sorted(windows), 'source_has_dates': source_has_dates,
                          'has_unknown_metric_window': unknown_metric_window})
    return build_snapshot(args, entities, bulk_terms, report_terms, portfolios, file_refs, download, period)


def build_snapshot(args, entities, bulk_terms, report_terms, portfolios, file_refs, download, period, extra=None):
    root = Path(args.run)
    # 批量表自带带 ID 的搜索词页；单独下载的搜索词报告（按天、无 ID）只在没有批量表时用，避免重复计算
    search_terms = bulk_terms or aggregate_terms(report_terms)
    ignored_reports = len(report_terms) if bulk_terms and report_terms else 0
    campaigns = {ident(r.get('campaign_id')): r for r in entities.get('Campaign', [])}
    groups = {ident(r.get('ad_group_id')): r for r in entities.get('Ad Group', [])}
    ads = entities.get('Product Ad', [])
    if not campaigns and not search_terms:
        return {'status': 'blocked', 'error': '文件里没有「实体 Entity」表也没有搜索词表：请用广告后台「批量操作 → 创建并下载电子表格」，勾选 SP 搜索词数据'}, 2
    # 范围：本品 = 含本品 SKU/ASIN 商品广告的活动；全店 = 全部
    skus, asins = set(args.sku or []), {a.upper() for a in (args.asin or [])}
    if args.scope == 'product' and not (skus or asins):
        plan = load(root / S4_PLAN)
        for m in plan.get('sku_asin_map') or []:
            if isinstance(m, dict):
                skus.add(str(m.get('sku') or ''))
                asins.add(str(m.get('asin') or '').upper())
        for item in load(root / S2_LISTING).get('skus') or []:
            skus.add(str(item.get('sku') or ''))
        skus.discard('')
        asins.discard('')
    found = sorted({str(a.get('sku') or '') for a in ads if a.get('sku')})
    if args.scope == 'product':
        scope_campaigns = {ident(a.get('campaign_id')) for a in ads
                           if str(a.get('sku') or '') in skus or str(a.get('asin') or '').upper() in asins}
        if not scope_campaigns:
            return {'status': 'blocked', 'error': '下载表里找不到本品的商品广告', 'looked_for': {'sku': sorted(skus), 'asin': sorted(asins)},
                    'skus_in_file': found[:30], 'hint': '用 --sku 指定，或 --scope store 分析全店'}, 2
    else:
        scope_campaigns = set(campaigns) | {ident(a.get('campaign_id')) for a in ads}
    name_index = {(str(campaigns.get(ident(g.get('campaign_id')), {}).get('campaign_name') or ''), str(g.get('ad_group_name') or '')):
                  (ident(g.get('campaign_id')), gid) for gid, g in groups.items()}

    def in_scope(row):
        return ident(row.get('campaign_id')) in scope_campaigns

    def clean(row, entity):
        cid, gid = ident(row.get('campaign_id')), ident(row.get('ad_group_id'))
        camp, group = campaigns.get(cid, {}), groups.get(gid, {})
        bid = num(row.get('bid'))
        return {'entity': entity, 'campaign_id': cid, 'ad_group_id': gid,
                'keyword_id': ident(row.get('keyword_id')), 'product_targeting_id': ident(row.get('product_targeting_id')),
                'campaign_name': str(row.get('campaign_name') or camp.get('campaign_name') or ''),
                'ad_group_name': str(row.get('ad_group_name') or group.get('ad_group_name') or ''),
                'text': str(row.get('keyword_text') or row.get('product_targeting_expression') or row.get('resolved_expression') or ''),
                'match_type': str(row.get('match_type') or ('targeting' if entity == 'Product Targeting' else '')),
                'state': str(row.get('state') or '').lower(), 'bid': bid,
                'default_bid': num(group.get('ad_group_default_bid')),
                'campaign_targeting': str(camp.get('targeting_type') or '').upper(),
                'metrics': metrics_of(row)}

    targets = [clean(r, e) for e in ('Keyword', 'Product Targeting') for r in entities.get(e, []) if in_scope(r)]
    negatives = [clean(r, e) for e in ('Negative Keyword', 'Campaign Negative Keyword', 'Negative Product Targeting')
                 for r in entities.get(e, []) if in_scope(r)]
    terms = []
    for r in search_terms:
        cid, gid = ident(r.get('campaign_id')), ident(r.get('ad_group_id'))
        if not cid:
            cid, gid = name_index.get((str(r.get('campaign_name') or ''), str(r.get('ad_group_name') or '')), ('', ''))
        if args.scope == 'product' and cid not in scope_campaigns:
            continue
        terms.append({'search_term': str(r.get('search_term') or '').strip(), 'campaign_id': cid, 'ad_group_id': gid,
                      'campaign_name': str(r.get('campaign_name') or campaigns.get(cid, {}).get('campaign_name') or ''),
                      'ad_group_name': str(r.get('ad_group_name') or groups.get(gid, {}).get('ad_group_name') or ''),
                      'targeting': str(r.get('keyword_text') or r.get('product_targeting_expression') or ''),
                      'match_type': str(r.get('match_type') or ''), 'has_ids': bool(cid and gid), 'metrics': metrics_of(r)})
    adjust = defaultdict(dict)
    adjust_perf = defaultdict(dict)
    for b in entities.get('Bidding Adjustment', []):
        key = place_key(b.get('placement'))
        if key:
            adjust[ident(b.get('campaign_id'))][key] = num(b.get('percentage')) or 0.0
            adjust_perf[ident(b.get('campaign_id'))][key] = metrics_of(b)
    camp_rows = []
    for cid in sorted(scope_campaigns):
        r = campaigns.get(cid)
        if not r:
            continue
        pid = ident(r.get('portfolio_id'))
        placements = r.get('placements') or adjust.get(cid) or {}
        perf = r.get('placement_perf') or adjust_perf.get(cid) or {}
        camp_rows.append({'campaign_id': cid, 'campaign_name': str(r.get('campaign_name') or ''),
                          'state': str(r.get('state') or '').lower(), 'targeting_type': str(r.get('targeting_type') or '').upper(),
                          'daily_budget': num(r.get('daily_budget')), 'bidding_strategy': str(r.get('bidding_strategy') or ''),
                          'portfolio_id': pid, 'portfolio_name': portfolios.get(pid) or str(r.get('portfolio_name') or ''),
                          'start_date': str(r.get('start_date') or ''), 'strategy_raw': r.get('strategy_raw'),
                          'placements': placements, 'placement_perf': perf, 'metrics': metrics_of(r)})
    group_rows = [{'ad_group_id': gid, 'campaign_id': ident(g.get('campaign_id')), 'ad_group_name': str(g.get('ad_group_name') or ''),
                   'state': str(g.get('state') or '').lower(), 'default_bid': num(g.get('ad_group_default_bid'))}
                  for gid, g in groups.items() if ident(g.get('campaign_id')) in scope_campaigns]
    ad_rows = [{'campaign_id': ident(a.get('campaign_id')), 'ad_group_id': ident(a.get('ad_group_id')), 'ad_id': ident(a.get('ad_id')),
                'sku': str(a.get('sku') or ''), 'asin': str(a.get('asin') or '').upper(), 'state': str(a.get('state') or '').lower()}
               for a in ads if in_scope(a)]
    shared = sorted({a['campaign_id'] for a in ad_rows if args.scope == 'product' and
                     (a['sku'] or a['asin']) and not (a['sku'] in skus or a['asin'] in asins)})
    totals = {m: round(sum(c['metrics'][m] for c in camp_rows), 2) for m in METRICS} if camp_rows else \
        {m: round(sum(t['metrics'][m] for t in terms), 2) for m in METRICS}
    extra = dict(extra or {})
    scope_asins = None
    if args.scope == 'product':  # 只看这个范围：产品健康灯、单量拆解也只列范围内的 ASIN，不混进全店其他产品
        scope_asins = {a['asin'] for a in ad_rows if a['asin']} | asins
        prods = extra.get('products')
        if isinstance(prods, dict) and prods.get('current') and prods.get('previous'):
            extra['products'] = {k: (dict(v, products={a: p for a, p in v['products'].items() if a in scope_asins}) if k in ('current', 'previous') else v)
                                 for k, v in prods.items()}
    folder = new_round(root)
    snapshot = {'artifact_type': 's5_ads_snapshot', 'generator': 's5_optimize.py', 'created_at': now().isoformat(),
                'scope': {'mode': args.scope, 'skus': sorted(skus), 'asins': sorted(scope_asins if scope_asins is not None else asins),
                          'requested_asins': sorted(asins), 'shared_campaign_ids': shared,
                          'notice': SHARED_NOTICE if shared else '',
                          'campaigns': len(camp_rows), **({'family': args.family} if getattr(args, 'family', None) else {})},
                'period': {'start': args.start or period.get('start'), 'end': args.end or period.get('end'),
                           'note': '' if (args.start or period) else '文件里没有日期区间：以下载时选择的区间为准'},
                'download': download, 'ignored_search_term_report_rows': ignored_reports,
                'files': file_refs, 'has_ids': bool(campaigns), 'skus_in_file': found[:50],
                'portfolios': portfolios, 'totals': totals, 'campaigns': camp_rows, 'ad_groups': group_rows,
                'product_ads': ad_rows, 'targets': targets, 'negatives': negatives, 'search_terms': terms,
                'other_ad_products': dict(Counter(e.get('sheet') for e in entities.get('__other__', []))), **extra}
    windows = sorted({d for f in file_refs for d in f.get('attribution_days', [])})
    unknown = any(f.get('has_unknown_metric_window', True) for f in file_refs)
    snapshot['data_windows'] = {'export_period': snapshot['period'], 'attribution_days': windows[0] if len(windows)==1 and not unknown else None,
                                'has_unknown_metric_window': unknown,
                                'source_windows': windows, 'daily_series_preserved': False,
                                'source_has_dates': any(f.get('source_has_dates') for f in file_refs)}
    dump_json(folder / '01_广告快照.json', snapshot)
    summary = {'status': 'ok', 'round': folder.name, 'scope': args.scope, 'campaigns': len(camp_rows), 'targets': len(targets),
               'search_terms': len(terms), 'has_ids': bool(campaigns), 'totals': totals}
    if not campaigns:
        summary['warning'] = '只有搜索词报表、没有后台 ID：能出诊断和建议，但不能生成可直接上传的批量表；补一份「批量操作」下载表再跑'
    return summary, 0


# ---------- diagnose ----------

def economics(root, args):
    brief = load(Path(root) / S4_BRIEF)
    ue = brief.get('unit_economics') or {}
    price = num(args.price) or num(ue.get('selling_price')) or num(((brief.get('product_competitiveness') or {}).get('price') or {}).get('value'))
    target = num(args.target_acos) or num(ue.get('target_acos')) or num(((brief.get('business_goal') or {}).get('guardrails') or {}).get('target_acos_ceiling'))
    breakeven = num(args.break_even_acos) or num(ue.get('break_even_acos'))
    cvr, cvr_source = num(args.cvr), 'user'
    if cvr is None:
        found = re.search(r'"expected_cvr"\s*:\s*\{[^}]*"value"\s*:\s*([0-9.]+)', json.dumps(brief))
        cvr, cvr_source = (float(found.group(1)), 'S4 策略假设') if found else (DEFAULT_CVR, '默认 10%')
    return {'price': price, 'target_acos': target, 'break_even_acos': breakeven, 'assumed_cvr': cvr,
            'cvr_source': cvr_source, 'target_cpa': round(price * target, 2) if price and target else None,
            'source': S4_BRIEF if ue else 'args'}


def marketplace_of(args, snap):
    mp = getattr(args, 'marketplace', None) or snap.get('marketplace')
    if mp:
        return mp.upper()
    sid = next((f.get('sid') for f in snap.get('files') or [] if f.get('sid')), None)
    home = Path(os.environ.get('STARSKY_HOME') or (Path.home() / '.starsky-codex')).expanduser()
    cfg = load(Path(os.environ.get('STARSKY_CONFIG_DIR') or (home / 'config')) / 'lingxing_config.yaml')
    return next((str(s.get('marketplace')).upper() for s in cfg.get('stores') or [] if s.get('sid') == sid), None)


INVEST_MAX_DAYS = 14
RISE_PTS = 0.03


# ---------- 单量拆解（广告知识库 KC-0163 / KC-0164）：先定位是哪一端出了问题，再决定动作 ----------

NODE_PLAYBOOK = {
    '样本太小': ('两期订单都不到 5 单，先拉长到 7–14 天再下结论', '按单日波动动手'),
    '平稳': ('按正常节奏优化', '—'),
    '市场端': ('曝光、点击和自然单一起掉：先查搜索量 / 季节（SIF 词搜索量趋势），控预算保利润', '盲目加价抢量'),
    '广告曝光端': ('查预算是否提前花完、竞价是否被抢、广告位', '改图改文案'),
    '点击端': ('看搜索结果页：主图、价格、星级、竞品有没有降价或上新', '加预算'),
    '转化端': ('广告单和自然单一起掉：查差评、价格、Listing、变体断货', '加价加词（会放大亏损）'),
    '自然端': ('广告稳、自然单掉：查核心词自然排名，用广告撬回核心词位置', '全面收缩广告'),
    '广告端': ('广告单掉，曝光 / 点击 / 转化都没单独大变：逐个活动看', '三板斧一起抡'),
    '上升': ('看是广告还是自然带起来的，做对的放大', '—'),
}
DROP_REL = 0.20  # 曝光 / 点击率 / 转化率相对上期下降 ≥20% 才算「这一端出了问题」


def _rel(cur, prev):
    return round(cur / prev - 1, 4) if cur is not None and prev else None


def _side(x):
    orders, ad_orders = x.get('orders') or 0, x.get('ad_orders') or 0
    imp, clicks = x.get('ad_impressions') or x.get('impressions'), x.get('ad_clicks') or x.get('clicks')
    return {'orders': orders, 'ad_orders': ad_orders, 'organic': max(0, orders - ad_orders) if 'ad_orders' in x else None,
            'impressions': imp, 'clicks': clicks, 'ctr': ratio(clicks, imp), 'cvr': ratio(ad_orders, clicks),
            'tacos': ratio(x.get('ad_spend'), x.get('sales')) if 'sales' in x else None}


def locate_node(cur, prev):
    """一个对象两期对比 → (端, 依据)。只有广告数据时 organic 为 None，只判广告这一侧。"""
    c, p = _side(cur), _side(prev)
    if max(c['orders'], p['orders']) < 5:
        return '样本太小', c, p, []
    d_orders = c['orders'] - p['orders']
    why = []
    imp_rel, ctr_rel, cvr_rel = _rel(c['impressions'], p['impressions']), _rel(c['ctr'], p['ctr']), _rel(c['cvr'], p['cvr'])
    for name, v in (('广告曝光', imp_rel), ('点击率', ctr_rel), ('广告转化率', cvr_rel)):
        if v is not None and abs(v) >= DROP_REL:
            why.append(f'{name} {v:+.0%}')
    if abs(d_orders) < max(3, 0.15 * p['orders']):
        return '平稳', c, p, why
    if d_orders > 0:
        return '上升', c, p, why
    d_ad = c['ad_orders'] - p['ad_orders']
    d_org = None if c['organic'] is None or p['organic'] is None else c['organic'] - p['organic']
    if d_org is not None:
        why.insert(0, f'广告单 {d_ad:+d}、自然单 {d_org:+d}')
    both_down = d_org is not None and d_org < 0 and d_ad < 0
    if both_down and imp_rel is not None and imp_rel <= -DROP_REL and (ctr_rel is None or ctr_rel > -DROP_REL):
        return '市场端', c, p, why
    if both_down and cvr_rel is not None and cvr_rel <= -DROP_REL:
        return '转化端', c, p, why
    if d_org is not None and d_org < 0 and abs(d_org) >= abs(d_ad):
        return '自然端', c, p, why
    if imp_rel is not None and imp_rel <= -DROP_REL:
        return '广告曝光端', c, p, why
    if ctr_rel is not None and ctr_rel <= -DROP_REL:
        return '点击端', c, p, why
    if cvr_rel is not None and cvr_rel <= -DROP_REL:
        return '转化端', c, p, why
    return '广告端', c, p, why


def previous_snapshot(folder):
    """第一部分没有产品级两期数据：拿上一轮（同一范围）的广告快照做广告侧对比。"""
    rounds = sorted(x for x in Path(folder).parent.glob('R*') if x.is_dir() and x.name < Path(folder).name)
    for r in reversed(rounds):
        snap = load(r / '01_广告快照.json')
        if snap.get('totals'):
            return r.name, snap
    return None, None


def order_breakdown(snap, folder):
    """单量拆解：领星来源按产品拆（广告单 / 自然单 / 曝光 / 点击率 / 转化率 / TACOS）；下载表格来源拿上一轮做广告侧对比。"""
    prods = snap.get('products') or {}
    rows = []
    if prods.get('current') and prods.get('previous'):
        cur, prev = prods['current']['products'], prods['previous']['products']
        for asin in sorted(set(cur) | set(prev)):
            c, p = cur.get(asin) or {'ad_orders': 0, 'sales': 0}, prev.get(asin) or {'ad_orders': 0, 'sales': 0}
            if not (c.get('orders') or p.get('orders')):
                continue
            node, cs, ps, why = locate_node(c, p)
            t_delta = None if cs['tacos'] is None or ps['tacos'] is None else round(cs['tacos'] - ps['tacos'], 4)
            if t_delta is not None and t_delta >= RISE_PTS:
                why.append(f'TACOS 升 {t_delta * 100:.1f} 个点（利润端）')
            do, dont = NODE_PLAYBOOK[node]
            rows.append({'asin': asin, 'name': (c.get('name') or p.get('name') or '')[:60], 'node': node, 'why': why,
                         'do': do, 'dont': dont, 'current': cs, 'previous': ps, 'tacos_delta': t_delta})
        order = list(NODE_PLAYBOOK)
        rows.sort(key=lambda r: (r['node'] in ('平稳', '上升', '样本太小'), r['current']['orders'] - r['previous']['orders'], order.index(r['node'])))
        return {'mode': 'products', 'window': [prods['current'].get('start'), prods['current'].get('end')],
                'previous': [prods['previous'].get('start'), prods['previous'].get('end')], 'rows': rows,
                'evidence_refs': ['KC-0163', 'KC-0164'],
                'note': '先定位是哪一端，再动手；定位在转化端 / 市场端的产品，本轮不加价、不加预算'}
    name, old = previous_snapshot(folder)
    if not old:
        return {'mode': 'none', 'rows': [], 'note': '第一轮没有上期可比；下一轮起自动对比上一轮。要拆自然单请接领星（ERP 路线）'}
    if (old.get('scope') or {}).get('mode') != (snap.get('scope') or {}).get('mode'):
        return {'mode': 'none', 'rows': [], 'note': f'上一轮 {name} 的范围和本轮不同，不做对比'}
    node, cs, ps, why = locate_node(snap['totals'], old['totals'])
    do, dont = NODE_PLAYBOOK[node]
    return {'mode': 'ads_only', 'previous_round': name, 'window': [(snap.get('period') or {}).get('start'), (snap.get('period') or {}).get('end')],
            'previous': [(old.get('period') or {}).get('start'), (old.get('period') or {}).get('end')],
            'rows': [{'asin': '本轮范围合计', 'name': '', 'node': node, 'why': why, 'do': do, 'dont': dont, 'current': cs, 'previous': ps}],
            'evidence_refs': ['KC-0163', 'KC-0164'],
            'note': '手动报表路线只有广告数据，只能看广告这一侧；单量掉了还要看后台「业务报告」里的总订单和会话数，或接领星拆自然单'}


GROW = {'bid_up', 'budget_up', 'placement_up'}
HOLD_GROW = {'转化端', '市场端'}


def product_health(root, args, snap):
    """产品级健康：广告 + 自然合看。保本 ACOS 是广告亏钱线，目标 ACOS 是方向；最终看 TACOS、扣广告后利润、自然单占比趋势。"""
    prods = snap.get('products') or {}
    if not prods or prods.get('error'):
        return [], {'status': 'missing', 'note': (prods or {}).get('error') or '没有产品级数据（下载表格来源没有全店订单；领星来源才有）'}
    ct = module(HERE.parent / 's5_cost_table.py', 's5_cost_table')
    mp = marketplace_of(args, snap)
    cost = ct.load_marketplace(mp) if mp else None
    by_sku, by_asin = ct.index(cost)
    est = (cost or {}).get('estimate_margin')
    history_path = work(root) / 'product_health_history.json'
    history = load(history_path)
    today = now().date().isoformat()
    cur, prev = prods['current']['products'], prods['previous']['products']
    out = []
    for asin, c in cur.items():
        if not (c['orders'] or c['ad_spend']):
            continue
        p = prev.get(asin) or {}
        rows = [(by_sku.get(sku) or by_asin.get(asin), n) for sku, n in c['sku_units'].items()] or [(by_asin.get(asin), c['units'])]
        known = [(r, n) for r, n in rows if r]
        gp = sum(r['gross_profit'] * n for r, n in known)
        unknown_units = sum(n for r, n in rows if not r)
        ref = known[0][0] if known else by_asin.get(asin)
        organic = max(0, c['orders'] - c['ad_orders'])
        share = organic / c['orders'] if c['orders'] else None
        p_share = max(0, p.get('orders', 0) - p.get('ad_orders', 0)) / p['orders'] if p.get('orders') else None
        delta = round(share - p_share, 4) if share is not None and p_share is not None else None
        ad_acos = ratio(c['ad_spend'], c['ad_sales_all'])
        tacos = ratio(c['ad_spend'], c['sales'])
        profit = round(gp - c['ad_spend'], 2) if ref else None
        be = (ref or {}).get('break_even_acos')
        target = (ref or {}).get('target_acos')
        if not ref and est:  # C 档估算：毛利 = 销售额 × 估算毛利率
            profit = round(c['sales'] * est - c['ad_spend'], 2)
            be, target = est, ct.estimate_row(est)['target_acos']
        rising = delta is not None and delta >= RISE_PTS
        ads_losing = ad_acos is not None and be is not None and ad_acos > be
        if profit is None:
            light, verdict = '⚪', '缺成本'
        elif profit > 0 and not ads_losing:
            light, verdict = '🟢', '健康' + ('（广告 ACOS 超目标，还有优化空间）' if ad_acos and target and ad_acos > target else '')
        elif profit == 0:
            light, verdict = '🟡', '观察：扣广告后刚好打平'
        elif profit > 0 and ads_losing:
            light, verdict = ('🟡', '投入期：广告单亏，但整体赚、自然单在涨') if rising else ('🟡', '观察：广告单亏，整体还赚，但自然单没起来')
        elif rising:
            light, verdict = '🟡', '投入期：整体暂亏，自然单在涨'
        else:
            light, verdict = '🔴', '需处理：广告亏、整体亏、自然单没涨'
        rec = history.get(asin) or {}
        if verdict.startswith('投入期'):
            since = rec.get('invest_since') or today
            days = (datetime.fromisoformat(today) - datetime.fromisoformat(since)).days
            history[asin] = {'invest_since': since}
            if days >= INVEST_MAX_DAYS:
                light, verdict = '🔴', f'需处理：投入期已 {days} 天，超过 {INVEST_MAX_DAYS} 天'
        elif asin in history:
            history.pop(asin)
        if not ref and est and profit is not None:
            verdict += '（估算）'
        out.append({'asin': asin, 'name': (ref or {}).get('name') or c.get('name') or '', 'light': light, 'verdict': verdict,
                    'orders': c['orders'], 'orders_prev': p.get('orders', 0), 'ad_orders': c['ad_orders'], 'organic_orders': organic,
                    'organic_share': round(share, 4) if share is not None else None, 'organic_share_prev': round(p_share, 4) if p_share is not None else None,
                    'organic_delta': delta, 'sales': round(c['sales'], 2), 'ad_spend': round(c['ad_spend'], 2), 'ad_acos': ad_acos,
                    'tacos': tacos, 'break_even_acos': be, 'target_acos': target, 'profit_after_ads': profit,
                    'units': c['units'], 'units_no_cost': unknown_units})
    dump_json(history_path, history)
    out.sort(key=lambda x: ({'🔴': 0, '🟡': 1, '⚪': 2, '🟢': 3}[x['light']], -(x['ad_spend'] or 0)))
    return out, {'status': 'ok', 'window': [prods['current']['start'], prods['current']['end']],
                 'previous': [prods['previous']['start'], prods['previous']['end']]}


def campaign_economics(root, args, snap):
    """每个活动按它投的 SKU 查成本表：客单价、目标 ACOS、保本 ACOS（多 SKU 取最严的目标）。"""
    ct = module(HERE.parent / 's5_cost_table.py', 's5_cost_table')
    mp = marketplace_of(args, snap)
    cost = ct.load_marketplace(mp) if mp else None
    if not cost:
        return {}, {'status': 'missing', 'marketplace': mp, 'note': '没登记成本表：s5_cost_table.py --set <站点> <表路径>；本轮用全店统一目标'}
    if cost.get('estimate_margin') and not cost.get('rows'):
        est = ct.estimate_row(cost['estimate_margin'])
        out = {c['campaign_id']: {'price': None, 'target_acos': est['target_acos'], 'break_even_acos': est['break_even_acos'],
                                  'skus': [], 'source': '估算'} for c in snap['campaigns']}
        return out, {'status': 'ok', 'marketplace': mp, 'table': cost['table'], 'campaigns_covered': len(out),
                     'campaigns_total': len(snap['campaigns']), 'estimate': True}
    by_sku, by_asin = ct.index(cost)
    skus = defaultdict(list)
    for a in snap['product_ads']:
        row = by_sku.get(a['sku']) or by_asin.get(a['asin'])
        if row and row not in skus[a['campaign_id']]:
            skus[a['campaign_id']].append(row)
    out = {}
    for cid, rows in skus.items():
        out[cid] = {'price': round(sum(r['price'] for r in rows) / len(rows), 2),
                    'target_acos': min(r['target_acos'] for r in rows), 'break_even_acos': min(r['break_even_acos'] for r in rows),
                    'skus': [r['sku'] for r in rows], 'source': '成本表'}
    covered = sum(1 for c in snap['campaigns'] if c['campaign_id'] in out)
    return out, {'status': 'ok', 'marketplace': mp, 'table': cost['table'], 'campaigns_covered': covered,
                 'campaigns_total': len(snap['campaigns'])}


def diagnose(args):
    root = Path(args.run)
    folder = current_round(root)
    locked = [n for n in ('04_审批.json', '06_写回回执.json') if (folder / n).exists()]
    if locked:
        return {'status': 'blocked', 'error': f"{folder.name} 已经{'写回' if '06_写回回执.json' in locked else '审批'}过，不能重跑诊断（会改动作编号）：重新 ingest 开新一轮"}, 2
    snap = load(folder / '01_广告快照.json')
    eco = economics(root, args)
    totals = snap['totals']
    store = snap['scope']['mode'] == 'store'
    if store and not eco.get('price') and totals['orders']:
        eco['price'] = round(totals['sales'] / totals['orders'], 2)
        eco['price_source'] = '全店平均客单价（每个活动优先用自己的客单价）'
    camp_econ, cost_note = campaign_economics(root, args, snap)
    if camp_econ and not eco.get('target_acos'):
        vals = list(camp_econ.values())
        eco['target_acos'] = round(sum(v['target_acos'] for v in vals) / len(vals), 4)
        eco['break_even_acos'] = eco.get('break_even_acos') or round(sum(v['break_even_acos'] for v in vals) / len(vals), 4)
        eco['source'] = '成本表（活动平均，仅作全店兜底）'
    if camp_econ and not eco.get('price'):
        eco['price'] = round(sum(v['price'] for v in camp_econ.values()) / len(camp_econ), 2)
    eco['cost_table'] = cost_note
    missing = [k for k in ('price', 'target_acos') if not eco.get(k)]
    if missing:
        return {'status': 'blocked', 'error': '缺利润账：' + '、'.join(missing) + '（S4 策略里没有就用 --price / --target-acos 给；全店分析至少给 --target-acos）'}, 2
    eco['target_cpa'] = round(eco['price'] * eco['target_acos'], 2)
    aov = {c['campaign_id']: round(c['metrics']['sales'] / c['metrics']['orders'], 2)
           for c in snap['campaigns'] if c['metrics']['orders'] >= 2} if store else {}

    def price_of(cid):
        return (camp_econ.get(cid) or {}).get('price') or aov.get(cid) or eco['price']

    def tacos(cid):
        return (camp_econ.get(cid) or {}).get('target_acos') or eco['target_acos']
    observed_cvr = ratio(totals['orders'], totals['clicks']) if totals['clicks'] >= 100 else None
    cvr = observed_cvr or eco['assumed_cvr']
    eco.update(observed_cvr=observed_cvr, cvr_used=cvr, cvr_used_source='本品本期实测（点击≥100）' if observed_cvr else eco['cvr_source'])
    cpa = eco['target_cpa']
    enough = max(THIN_CLICKS, round(1.5 / cvr))
    heavy = max(THIN_CLICKS, round(2 / cvr))
    thresholds = {'thin_clicks': THIN_CLICKS, 'no_order_clicks': enough, 'pause_clicks': heavy,
                  'pause_spend': round(2 * cpa, 2), 'negative_spend': round(cpa, 2)}
    actions, watch = [], []
    cooling = recent_writes(root, days=COOLDOWN_DAYS)

    def add(kind, rule, row, current, proposed, data, **extra):
        key = str(extra.get('keyword_id') or extra.get('product_targeting_id') or '') or \
            (str(row.get('campaign_id')) if kind.startswith(('budget', 'placement')) else '')
        if key and key in cooling:
            watch.append({'object': display_name(row.get('text') or row.get('search_term')), 'match_type': row.get('match_type'),
                          'why': f"{cooling[key]} 刚通过写回改过，观察期 {COOLDOWN_DAYS} 天内不再动（KC-0024）", **data})
            return
        actions.append({'id': f'A{len(actions) + 1:02d}', 'kind': kind, 'label': ACTION_LABEL[kind], 'rule': rule,
                        'why': RULES[rule], 'object': row.get('text') or row.get('search_term'),
                        'match_type': row.get('match_type'), 'campaign_name': row.get('campaign_name'),
                        'ad_group_name': row.get('ad_group_name'), 'campaign_id': row.get('campaign_id'),
                        'ad_group_id': row.get('ad_group_id'), 'current': current, 'proposed': proposed, 'data': data, **extra})

    def data_of(m):
        return {'clicks': int(m['clicks']), 'orders': int(m['orders']), 'spend': money(m['spend']), 'sales': money(m['sales']),
                'acos': ratio(m['spend'], m['sales']), 'cpc': ratio(m['spend'], m['clicks']), 'cvr': ratio(m['orders'], m['clicks'])}

    for t in snap['targets']:
        if t['state'] not in ('enabled', ''):
            continue
        target = tacos(t['campaign_id'])
        m, d = t['metrics'], data_of(t['metrics'])
        bid = t['bid'] or t['default_bid']
        entity_id = {'keyword_id': t['keyword_id']} if t['entity'] == 'Keyword' else {'product_targeting_id': t['product_targeting_id']}
        common = {'entity': t['entity'], **entity_id}
        if m['orders'] == 0 and m['clicks'] < THIN_CLICKS:
            if m['clicks'] > 0:
                watch.append({'object': t['text'], 'match_type': t['match_type'], 'why': f'点击 {int(m["clicks"])} 次，不到 {THIN_CLICKS} 次判断不了', **d})
            continue
        if bid is None:
            continue
        cpa_here = price_of(t['campaign_id']) * tacos(t['campaign_id'])
        if m['orders'] == 0 and m['spend'] >= 2 * cpa_here and m['clicks'] >= heavy:
            add('pause', 'K3', t, {'state': 'enabled', 'bid': bid}, {'state': 'paused', 'bid': bid}, d, **common)
        elif m['orders'] == 0 and m['clicks'] >= enough:
            add('bid_down', 'K2', t, {'bid': bid}, {'bid': max(0.02, round(bid * 0.7, 2))}, d, **common)
        elif m['orders'] >= 1 and d['acos'] and d['acos'] > target:
            factor = min(0.95, max(0.6, target / d['acos']))
            note = ('实际 CPC 高于竞价：多半是广告位加价或「提高和降低」动态竞价在抬价，只降竞价不够，一并检查广告位百分比'
                    if d['cpc'] and d['cpc'] > bid * 1.15 else '')
            add('bid_down', 'K4', t, {'bid': bid}, {'bid': max(0.02, round(bid * factor, 2))}, d, note=note, **common)
        elif m['orders'] >= 2 and d['acos'] is not None and d['acos'] <= 0.7 * target:
            ceiling = round(price_of(t['campaign_id']) * tacos(t['campaign_id']) * (d['cvr'] or cvr), 2)
            new = min(round(bid * 1.15, 2), ceiling)
            if new > bid:
                add('bid_up', 'K5', t, {'bid': bid}, {'bid': new}, d, ceiling=ceiling, **common)
        elif m['orders'] == 0 or (d['acos'] and d['acos'] > 0.7 * target):
            watch.append({'object': t['text'], 'match_type': t['match_type'], 'why': '在目标附近，先观望', **d})

    # 活动层：词都薄样本时，按活动整体 ACOS 整组降价
    acted = {a.get('keyword_id') or a.get('product_targeting_id') for a in actions}
    campaign_view = []
    for c in snap['campaigns']:
        m = c['metrics']
        d = data_of(m)
        ctarget = tacos(c['campaign_id'])
        need = round(d['cpc'] / (price_of(c['campaign_id']) * tacos(c['campaign_id'])), 4) if d['cpc'] else None
        ce = camp_econ.get(c['campaign_id']) or {}
        view = {'campaign_id': c['campaign_id'], 'campaign_name': c['campaign_name'], 'portfolio_name': c['portfolio_name'],
                'target_acos': ctarget, 'break_even_acos': ce.get('break_even_acos'), 'econ_source': ce.get('source', '全店值'),
                'state': c['state'], 'daily_budget': c['daily_budget'], 'bidding_strategy': c['bidding_strategy'], **d, 'need_cvr': need,
                'verdict': ('无单' if m['orders'] == 0 and m['spend'] > 0
                            else '亏钱（超保本）' if d['acos'] and ce.get('break_even_acos') and d['acos'] > ce['break_even_acos']
                            else '超目标' if d['acos'] and d['acos'] > ctarget
                            else '达标' if d['acos'] else '无花费')}
        campaign_view.append(view)
        if not (d['acos'] and d['acos'] > ctarget and m['spend'] >= 3 * price_of(c['campaign_id']) * tacos(c['campaign_id'])):
            continue
        factor = min(0.95, max(0.7, ctarget / d['acos']))
        for t in snap['targets']:
            if t['campaign_id'] != c['campaign_id'] or t['state'] not in ('enabled', '') or t['metrics']['clicks'] < 3:
                continue
            key = t['keyword_id'] or t['product_targeting_id']
            bid = t['bid'] or t['default_bid']
            own = data_of(t['metrics'])
            if key in acted or not bid or (own['acos'] is not None and own['acos'] <= ctarget):
                continue  # 自己已达标的赢词不跟着活动一起降
            acted.add(key)
            entity_id = {'keyword_id': t['keyword_id']} if t['entity'] == 'Keyword' else {'product_targeting_id': t['product_targeting_id']}
            add('bid_down', 'K6', t, {'bid': bid}, {'bid': max(0.02, round(bid * factor, 2))}, data_of(t['metrics']),
                note=f"活动整体 ACOS {d['acos']:.0%}，按目标折算 ×{factor:.2f}", entity=t['entity'], **entity_id)
    period = snap.get('period') or {}
    try:
        days = (datetime.fromisoformat(period['end']) - datetime.fromisoformat(period['start'])).days + 1
    except (KeyError, TypeError, ValueError):
        days = None
    for c in snap['campaigns']:
        if c['state'] != 'enabled':
            continue
        ctarget = tacos(c['campaign_id'])
        m, d = c['metrics'], data_of(c['metrics'])
        cpa_here = price_of(c['campaign_id']) * tacos(c['campaign_id'])
        budget = c.get('daily_budget')
        base = {'text': c['campaign_name'], 'campaign_name': c['campaign_name'], 'campaign_id': c['campaign_id'],
                'ad_group_name': '', 'ad_group_id': '', 'match_type': '活动'}
        if budget and days:
            used = m['spend'] / days / budget
            d_used = {**d, 'budget_used': round(used, 2)}
            if used >= 0.9 and m['orders'] >= 2 and d['acos'] is not None and d['acos'] <= ctarget:
                add('budget_up', 'B1', base, {'daily_budget': budget}, {'daily_budget': round(budget * 1.3, 2)}, d_used,
                    entity='Campaign', start_date=c.get('start_date'), state='enabled', bidding_strategy=c.get('bidding_strategy'))
            elif used >= 0.9 and ((d['acos'] and d['acos'] > 2 * ctarget) or (m['orders'] == 0 and m['spend'] >= 2 * cpa_here)):
                add('budget_down', 'B2', base, {'daily_budget': budget}, {'daily_budget': max(1.0, round(budget * 0.7, 2))}, d_used,
                    entity='Campaign', start_date=c.get('start_date'), state='enabled', bidding_strategy=c.get('bidding_strategy'))
        for key, pm in (c.get('placement_perf') or {}).items():
            if key == 'placementAmazonBusiness':
                continue
            pct = (c.get('placements') or {}).get(key, 0.0) or 0.0
            pd = data_of(pm)
            where = {**base, 'text': f"{c['campaign_name']} · {PLACEMENT_CN[key]}", 'match_type': '广告位'}
            extra = {'entity': 'Bidding Adjustment', 'placement': key, 'placements_now': c.get('placements') or {},
                     'strategy_raw': c.get('strategy_raw')}
            if pm['orders'] >= 2 and pm['clicks'] >= 10 and pd['acos'] is not None and pd['acos'] <= 0.7 * ctarget:
                add('placement_up', 'P1', where, {'percentage': pct}, {'percentage': min(900.0, pct + 10)}, pd, **extra)
            elif pct > 0 and ((pd['acos'] and pd['acos'] > 1.5 * ctarget and pm['clicks'] >= 20)
                              or (pm['orders'] == 0 and pm['spend'] >= 2 * cpa_here)):
                add('placement_down', 'P2', where, {'percentage': pct}, {'percentage': round(pct / 2)}, pd, **extra)
    exact_now = {t['text'].lower() for t in snap['targets'] if str(t['match_type']).lower() == 'exact'}
    negated = {(n['ad_group_id'], n['text'].lower()) for n in snap['negatives']} | {(n['campaign_id'], n['text'].lower()) for n in snap['negatives']}
    ct = None
    if PRODUCT_CT.is_file():
        result = module(PRODUCT_CT, 's5_product_ct').evaluate(root)
        ct = result if result.get('status') != 'missing' else None
    not_this = [(w, kind) for kind, words in ((ct or {}).get('not_this') or {}).items() for w in words]
    harvest, ct_done = [], set()
    for s in sorted(snap['search_terms'], key=lambda s: -s['metrics']['orders']):
        term, m, d = s['search_term'], s['metrics'], data_of(s['metrics'])
        if not term:
            continue
        low = term.lower()
        if m['orders'] >= 2 and not is_asin(term) and low not in exact_now and str(s['match_type']).lower() != 'exact':
            harvest.append((s, d))
            exact_now.add(low)
            continue
        own_exact = str(s['match_type']).lower() == 'exact' and low == str(s['targeting']).lower()
        if m['orders'] == 0 and m['clicks'] >= enough and m['spend'] >= price_of(s['campaign_id']) * tacos(s['campaign_id']) \
                and (s['ad_group_id'], low) not in negated and not own_exact:
            add('negative_asin' if is_asin(term) else 'negative_exact', 'S3' if is_asin(term) else 'S2', s,
                {'state': '在投'}, {'negative': 'asin="' + term.upper() + '"' if is_asin(term) else 'negativeExact'}, d,
                entity='Negative Product Targeting' if is_asin(term) else 'Negative Keyword')
            negated.add((s['ad_group_id'], low))
            continue
        hits = [w for w, _ in not_this if has_phrase(toks(term), w)]
        if hits and m['orders'] == 0 and m['spend'] > 0:
            for w in hits:
                if (s['ad_group_id'], w) in ct_done or (s['ad_group_id'], w) in negated:
                    continue
                ct_done.add((s['ad_group_id'], w))
                add('negative_phrase_ct', 'C1', dict(s, text=w), {'state': '在投'}, {'negative': 'negativePhrase'},
                    d, entity='Negative Keyword', example=term)
    for s, d in harvest:
        destination = harvest_destination(snap, s)
        bid = round(min((d['cpc'] or 0.5) * 1.1, price_of(s['campaign_id']) * tacos(s['campaign_id']) * (d['cvr'] or cvr)), 2)
        add('harvest_exact', 'S1', s, {'match_type': s['match_type'] or '自动', 'from': s['campaign_name']},
            {'match_type': 'exact', 'bid': max(0.02, bid), 'into': destination['label']}, d, entity='Keyword',
            destination=destination, source_negative='下一轮：精准组跑出数据后，再在来源组否定精准，避免断流')
    health, health_note = product_health(root, args, snap)
    light_of = {h['asin']: h for h in health}
    camp_asins = defaultdict(set)
    for a in snap['product_ads']:
        if a.get('asin'):
            camp_asins[a['campaign_id']].add(a['asin'].upper())
    shrink = {'bid_down', 'pause', 'budget_down', 'placement_down'}
    kept = []
    for a in actions:
        hs = [light_of[x] for x in camp_asins.get(a['campaign_id'], ()) if x in light_of]
        if a['kind'] in shrink and hs and all(h['light'] in ('🟢', '🟡') for h in hs):
            watch.append({'object': display_name(a['object']), 'match_type': a.get('match_type'),
                          'why': '产品整体' + '、'.join(sorted({h['light'] + h['verdict'].split('（')[0].split('：')[0] for h in hs})) + '，本轮不收缩（广告 + 自然合看）',
                          **a['data']})
            continue
        kept.append(a)
    breakdown = order_breakdown(snap, folder)
    node_of = {r['asin']: r for r in breakdown['rows'] if r['node'] in HOLD_GROW}
    guarded = []
    for a in kept:  # 问题在转化端 / 市场端：加价加预算只会放大亏损，先拆解处理（KC-0163）
        hit = [node_of[x] for x in camp_asins.get(a['campaign_id'], ()) if x in node_of]
        if a['kind'] in GROW and hit:
            watch.append({'object': display_name(a['object']), 'match_type': a.get('match_type'),
                          'why': '单量拆解：' + '、'.join(sorted({r['node'] for r in hit})) + '出了问题，本轮不加价 / 加预算（先' + hit[0]['do'].split('：')[-1] + '）',
                          **a['data']})
            continue
        guarded.append(a)
    kept = guarded
    for i, a in enumerate(kept, 1):
        a['id'] = f'A{i:02d}'
        if shared_action(a, snap['scope']):
            a['scope_hold'] = SHARED_NOTICE
    actions[:] = kept
    listing = listing_gap(root, snap, ct, folder)
    plan = {'artifact_type': 's5_optimization_plan', 'generator': 's5_optimize.py', 'created_at': now().isoformat(),
            'round': folder.name, 'scope': snap['scope'], 'economics': eco, 'thresholds': thresholds,
            'data_windows': snap.get('data_windows', {}),
            'totals': {**snap['totals'], 'acos': ratio(snap['totals']['spend'], snap['totals']['sales']),
                       'cvr': ratio(snap['totals']['orders'], snap['totals']['clicks'])},
            'campaign_economics': camp_econ, 'order_breakdown': breakdown, 'product_health': health, 'product_health_note': health_note, 'actions': actions, 'campaign_view': sorted(campaign_view, key=lambda v: -(v['spend'] or 0)), 'watch': watch[:200], 'watch_count': len(watch),
            'listing': listing, 'product_ct': {'status': (ct or {}).get('status', 'missing'), 'material': (ct or {}).get('material_cn')},
            'has_ids': snap['has_ids']}
    dump_json(folder / '02_诊断方案.json', plan)
    write_evidence_template(folder, plan)
    write_listing_template(folder, listing)
    return {'status': 'ok', 'round': folder.name, 'actions': dict(Counter(a['label'] for a in actions)),
            'watch': len(watch), 'listing_missing': len(listing['gap'].get('missing') or []),
            'listing_current_issues': len(listing['current_issues']),
            'next': '助手填 evidence.yaml（广告知识库 KC 号、SIF/西柚证据）与 listing_proposal.yaml（改后文案），再跑 report'}, 0


COOLDOWN_DAYS = 7


def recent_writes(root, days=COOLDOWN_DAYS):
    """最近通过领星写回改过的对象（投放 ID / 活动 ID → 改动日期），冷却期内不再给动作。"""
    out = {}
    cutoff = now() - timedelta(days=days)
    for receipt in work(root).glob('R*/06_写回回执.json'):
        doc = load(receipt)
        try:
            at = datetime.fromisoformat(doc.get('at'))
        except (TypeError, ValueError):
            continue
        if at < cutoff:
            continue
        for r in doc.get('results') or []:
            if r.get('ok') or r.get('api_code') == 'SUCCESS':
                out[str(r.get('target_id'))] = f'{at:%m-%d}'
    return out


def harvest_destination(snap, source):
    """出单词进哪：同一产品（同 SKU）已有的精准组，其次同广告组合；都没有就给这个 SKU 新建精准活动，放进来源活动的广告组合。"""
    sku_of = defaultdict(set)
    for a in snap['product_ads']:
        if a['sku']:
            sku_of[a['ad_group_id']].add(a['sku'])
    portfolio_of = {c['campaign_id']: c['portfolio_id'] for c in snap['campaigns']}
    skus = sku_of.get(source['ad_group_id']) or set()
    pid = portfolio_of.get(source['campaign_id'], '')
    counts = Counter((t['campaign_id'], t['ad_group_id']) for t in snap['targets']
                     if str(t['match_type']).lower() == 'exact' and t['state'] in ('enabled', ''))
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    for test in (lambda cid, gid: skus and sku_of.get(gid, set()) & skus,
                 lambda cid, gid: not skus and pid and portfolio_of.get(cid) == pid):
        for (cid, gid), _ in ranked:
            if test(cid, gid):
                t = next(t for t in snap['targets'] if t['campaign_id'] == cid and t['ad_group_id'] == gid)
                return {'mode': 'existing', 'campaign_id': cid, 'ad_group_id': gid, 'campaign_name': t['campaign_name'],
                        'ad_group_name': t['ad_group_name'], 'label': f"同产品已有精准组「{t['campaign_name']} / {t['ad_group_name']}」"}
    sku = sorted(skus)[0] if skus else ''
    asin = next((a['asin'] for a in snap['product_ads'] if a['sku'] == sku and a['asin']), '')
    slug = re.sub(r'[^A-Za-z0-9]+', '', sku)[:24] or 'Product'
    name = f"SP_{re.sub(r'[^A-Za-z0-9]+', '-', sku or 'PRODUCT')[:40]}_EXACT_HARVEST"
    pname = snap['portfolios'].get(pid) or pid
    return {'mode': 'new', 'campaign_id': f'S5Harvest{slug}', 'ad_group_id': f'S5HarvestGroup{slug}', 'campaign_name': name,
            'ad_group_name': 'EXACT_HARVEST', 'portfolio_id': pid, 'portfolio_name': pname, 'sku': sku, 'asin': asin,
            'label': f"新建精准活动「{name}」" + (f"（自动放进广告组合「{pname}」）" if pid else '（来源活动没有广告组合）')
                     + ('' if sku else '；来源广告组找不到 SKU，需人工补')}


def current_listing(root):
    data = load(Path(root) / S2_LISTING)
    skus = data.get('skus') or []
    item = skus[0] if skus else {}
    bullets = item.get('bullets') or []
    if isinstance(bullets, str):
        try:
            bullets = json.loads(bullets)
        except ValueError:
            bullets = [bullets]
    return {'sku': item.get('sku') or '', 'title': item.get('title') or '', 'bullets': [str(b) for b in bullets],
            'search_terms': item.get('search_terms') or '', 'source': S2_LISTING if item else None}, [str(x) for x in data.get('blacklist_terms') or []]


def brand_terms(root, extra):
    doc = load(Path(root) / BLACKLIST)
    words = list(extra)
    # brand_terms is a rule list in current S1 output and {ads_only_pool, other} in older runs;
    # every brand word stays out of listing copy whichever pool it sits in.
    brands = doc.get('brand_terms') or []
    pools = [brands.get('ads_only_pool'), brands.get('other')] if isinstance(brands, dict) else [brands]
    pools.append(doc.get('brand_terms_ads_only'))
    for pool in pools:
        for item in pool or []:
            words.append((item.get('term') or item.get('phrase')) if isinstance(item, dict) else item)
    words += [b.get('term') for b in doc.get('blocked_terms') or [] if isinstance(b, dict)]
    return sorted({str(w) for w in words if w and re.search(r'[A-Za-z]', str(w))})


# ---------- 文案合规把关：改文案一定过 S1 的 IP / 敏感词排查，撞了就不给用户 ----------

# 亚马逊 Listing 常见受限宣称（不依赖 S1 是否做过）：绝对化 / 促销 / 医疗与疗效 / EPA 抗菌杀菌 / 认证背书
BUILTIN_CLAIMS = [
    (r'#\s?1\b|\bno\.?\s?1\b|\bnumber\s+(?:one|1)\b|\bbest[\s-]?sell(?:er|ing)\b|\btop[\s-]rated\b|\bamazon[\x27’]?s\s+choice\b', '绝对化 / 榜单用语'),
    (r'\b100\s?%\s+(?:guarantee|guaranteed|safe|natural|effective)\b|\bguaranteed\b|\bbest\s+(?:in\s+the\s+world|ever|quality)\b|\bworld[\x27’]?s\s+best\b', '绝对化承诺'),
    (r'\bfree\s+shipping\b|\bon\s+sale\b|\bdiscount(?:ed)?\b|\bcoupon\b|\b\d+\s?%\s+off\b|\blimited\s+time\b', '促销用语（标题 / 五点不允许）'),
    (r'\b(?:cures?|cured|heals?|healing|treats?|treatment|prevents?\s+(?:disease|infection)|therapeutic|clinically\s+proven|medical[\s-]grade)\b', '医疗 / 疗效宣称'),
    (r'\bfda[\s-]?(?:approved|cleared|registered|certified)\b', 'FDA 背书宣称'),
    (r'\banti[\s-]?(?:bacterial|microbial|viral|fungal)\b|\bkills?\s+(?:germs?|bacteria|viruses|99)|\bdisinfect(?:s|ant|ing)?\b|\bsanitiz(?:e|es|er|ing)\b|\bsterili[sz](?:e|es|ed|ing)\b', 'EPA 抗菌 / 杀菌宣称（无注册不能写）'),
]
CLAIM_KEYS = ('term', 'phrase', 'claim', 'word', 'text', 'name')


def _collect_terms(node, out):
    if isinstance(node, str):
        if node.strip():
            out.append(node.strip())
    elif isinstance(node, dict):
        hit = [node[k] for k in CLAIM_KEYS if isinstance(node.get(k), str)]
        if hit:
            out.append(hit[0].strip())
        else:
            for v in node.values():
                if isinstance(v, (list, dict)):
                    _collect_terms(v, out)
    elif isinstance(node, list):
        for v in node:
            _collect_terms(v, out)


RULER = HERE.parents[2] / 'amazon-ip-risk-assessment' / 'references' / 'compliance_ruler.yaml'


def ruler_traps():
    """合规尺子自带的通用词陷阱（Q-tips 这类别人的商标）：没做过 S1 排查也能挡住明显的侵权词。"""
    doc = load(RULER)
    return [{'term': str(t.get('term') or ''), 'generic': str(t.get('generic') or ''), 'owner': str(t.get('owner') or '')}
            for t in doc.get('generic_trademark_traps') or [] if isinstance(t, dict) and t.get('term')]


def compliance_state(root):
    """Keep receipt-backed S1 blacklist evidence available to independent S5."""
    store=sys.modules.get('r01_case_store')
    if store is None:
        path=HERE.parents[3]/'scripts/r01_case_store.py'
        spec=importlib.util.spec_from_file_location('r01_case_store',path)
        store=importlib.util.module_from_spec(spec);sys.modules['r01_case_store']=store
        spec.loader.exec_module(store)
    with store.CompactCase(root).materialize() as view:
        return _compliance_state_native(view.root)


def _compliance_state_native(root):
    """S1 IP / 合规排查产出的黑名单（品牌词、禁用词、受限宣称）。没有这个文件 = 这个品没做过排查。"""
    path = Path(root) / BLACKLIST
    doc = load(path)
    has = bool(doc)
    terms, claims = [], []
    for key in ('blocked_terms', 'brand_terms'):
        _collect_terms(doc.get(key), terms)
    _collect_terms(doc.get('restricted_claims'), claims)
    allowed = []
    _collect_terms(doc.get('allowed_context'), allowed)
    _collect_terms(load(Path(root) / S2_LISTING).get('blacklist_terms'), terms)  # S2 词库 / 文案里带下来的禁用词一并查
    return {'has_s1': has, 'source': str(BLACKLIST) if has else None,
            'terms': sorted({t for t in terms if len(t) > 1}), 'claims': sorted({c for c in claims if len(c) > 1}), 'allowed': allowed,
            'traps': ruler_traps()}


def claim_hits(text, compliance):
    """改后文案里撞到的：(类别, 命中词) 列表。S1 黑名单里的词 + 内置受限宣称。"""
    text = str(text or '')
    tks = toks(text)
    found = []
    for word in (compliance or {}).get('terms', []):
        if has_phrase(tks, word) or (not re.search(r'[A-Za-z0-9]', word) and word in text):
            found.append(('S1 / S2 排查结果里的品牌 / 禁用词', word))
    for word in (compliance or {}).get('claims', []):
        if has_phrase(tks, word) or (not re.search(r'[A-Za-z0-9]', word) and word in text):
            found.append(('S1 / S2 排查结果里的受限宣称', word))
    for pattern, label in BUILTIN_CLAIMS:
        for m in re.finditer(pattern, text, re.I):
            found.append((label, m.group(0)))
    for trap in (compliance or {}).get('traps', []):
        if has_phrase(tks, trap['term']):
            found.append((f"常见商标陷阱词（{trap['owner'] or '他人商标'}" + (f"，通用说法：{trap['generic']}" if trap['generic'] else '') + '）', trap['term']))
    seen, out = set(), []
    for kind, word in found:
        key = (kind, word.lower())
        if key not in seen:
            seen.add(key)
            out.append((kind, word))
    return out


def listing_gap(root, snap, ct, folder):
    listing, blacklist = current_listing(root)
    manual = folder.parent.parent / '_投喂_广告报表' / '当前文案.json'
    if manual.is_file():
        listing = {**listing, **load(manual), 'source': str(manual)}
    brands = brand_terms(root, blacklist)
    proven = sorted((s for s in snap['search_terms'] if s['metrics']['orders'] >= 1 and not is_asin(s['search_term'])),
                    key=lambda s: -s['metrics']['orders'])
    words, seen = [], set()
    for s in proven:
        low = s['search_term'].lower()
        if low in seen:
            continue
        seen.add(low)
        words.append({'term': s['search_term'], 'source': f"广告出单 {int(s['metrics']['orders'])} 单", 'role': 'demand'})
    gap = module(GAP, 's5_gap').analyze({'listing': listing, 'demand_words': words[:40], 'brand_blocklist': brands}) if listing.get('title') else {}
    issues = []
    fields = {'标题': listing.get('title', ''), '五点': ' '.join(listing.get('bullets') or []), 'Search Terms': listing.get('search_terms', '')}
    for field, text in fields.items():
        t = toks(text)
        for kind, words_ in ((ct or {}).get('not_this') or {}).items():
            hits = sorted({w for w in words_ if has_phrase(t, w)})
            if hits:
                issues.append({'field': field, 'problem': '写了本品不是的属性（产品 CT）：' + '、'.join(hits), 'terms': hits})
        hits = sorted({b for b in brands if has_phrase(t, b)})
        if hits:
            issues.append({'field': field, 'problem': '含品牌词/禁用词：' + '、'.join(hits), 'terms': hits})
    compliance = compliance_state(root)
    for field, text in fields.items():
        for kind, word in claim_hits(text, compliance):
            issues.append({'field': field, 'problem': f'现有文案里有受限用语（{kind}）：{word}', 'terms': [word]})
    return {'current': listing, 'gap': gap, 'current_issues': issues, 'brand_terms': brands,
            'compliance': {'has_s1': compliance['has_s1'], 'source': compliance['source'], 'terms': len(compliance['terms']),
                           'claims': len(compliance['claims'])}}


def write_evidence_template(folder, plan):
    path = folder / 'evidence.yaml'
    if path.exists():
        return
    doc = {'mcp_used': [], 'kb_summary': '',
           'breakdown_check': {r['asin']: {'market': '', 'competition': '', 'traffic': '', 'conclusion': ''}
                               for r in (plan.get('order_breakdown') or {}).get('rows') or [] if r['node'] not in ('平稳', '上升', '样本太小')},
           'actions': {a['id']: {'kb': [], 'market': '', 'note': a.get('scope_hold', ''),
                               **({'advice':'暂缓'} if a.get('scope_hold') else {})} for a in plan['actions']},
           'market_words': []}
    head = ('# 助手填：\n# - breakdown_check.<ASIN>：单量出问题的产品先看总盘、再谈动作（不要因为某天卖得差就砍价格、砍预算）：\n'
            '#     market = 核心词搜索量趋势（SIF / Sorftime / 卖家精灵 / 西柚，同期对比）；competition = 竞品价格 / 排名 / 新品 / 断货变化；\n'
            '#     traffic = 曝光、点击、访问是否达标（报告里已有广告侧数字，自然侧看 SIF 流量结构）；conclusion = 一句话：是市场、竞争、流量还是转化的问题\n'
            '# - 定位在「市场端 / 转化端」的产品本轮不会出加价加预算；这一栏要写清楚依据，报告会原样显示\n# - actions.<编号>.kb：ads_knowledge_base kb_diagnose / kb_scenario_rules 返回的 KC 编号（断货/掉位加 promotion 的 PK 号）\n'
            '# - actions.<编号>.market：SIF / 西柚 对这个词的现窗数据（周搜索、CPC、排名）一句话\n'
            '# - market_words：SIF / 西柚发现的机会词（term、source、weekly_volume、cpc、note），会进文案缺口\n'
            '# - mcp_used：实际调用过的 MCP；没调就留空，报告如实写「未调用」\n')
    path.write_text(head + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding='utf-8')


def write_listing_template(folder, listing):
    path = folder / 'listing_proposal.yaml'
    if path.exists():
        return
    cur = listing['current']
    doc = {'change': False, 'title': cur.get('title', ''), 'bullets': list(cur.get('bullets') or []),
           'search_terms': cur.get('search_terms', ''), 'notes': []}
    head = ('# 助手写改后文案（从现文案改，不整篇重写）。change: true 才进报告的「文案前后对比」。\n'
            '# 纪律：默认不动标题；一次只改一个变体；ST ≤ 249 字节、不重复标题五点已有词；品牌词和产品 CT「不是本品」的词不进文案。\n'
            '# notes：每处改动一句理由，如 {field: bullet_3, add: "quick dry", why: "广告出单 5 单、五点没有"}\n')
    path.write_text(head + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=200), encoding='utf-8')


# ---------- report ----------

def knowledge_text(root,number,md):
    """Enrich before the native report digest; standalone legacy use remains available."""
    home=Path(__file__).resolve().parents[3]
    if not (home/'scripts/kb_ui.py').is_file(): return md
    before=sys.path[:]
    try:
        sys.path.insert(0,str(home/'scripts'))
        from kb_ui import enrich_report
        return enrich_report(md,root,home,'S5',number)
    finally: sys.path[:]=before


def kb_report(args):
    root=Path(args.run);folder=current_round(root)
    data=load(folder/'02_诊断方案.json')
    md=['# 本轮诊断说明','',data.get('scope',{}).get('notice',''),window_notice(data),f"轮次：{folder.name}",'']
    md.extend(f"- {a.get('id','观察')}：{a.get('why','')}" for a in data.get('actions',[]))
    md.extend(f"- 观望：{a.get('why','')}" for a in data.get('watch',[]))
    md.extend(f"- 健康状态：{a.get('light','')} {a.get('verdict','')}" for a in data.get('product_health',[]))
    path=folder/'02_诊断说明.md'
    path.write_text(knowledge_text(root,2,'\n'.join(md)+'\n'),encoding='utf8')
    return {'status':'ok','markdown':str(path)},0

def diff_html(old, new):
    old_t = set(toks(old))
    out = []
    for part in re.split(r'(\s+)', str(new or '')):
        core = toks(part)
        out.append(f'<mark>{html.escape(part)}</mark>' if core and not set(core) <= old_t else html.escape(part))
    return ''.join(out)


IP_UNCHECKED = ('这个产品没有做过 IP / 敏感词排查（S1 / S2 没有留下合规黑名单）：下面的改稿只对照了通用禁用词和已知商标陷阱词，'
                '改之前你必须自己确认改后的每个词都没有侵权、没有敏感；想让助手先帮你排一遍，说「IP排查」')


def check_listing(proposal, listing, brands, ct, compliance=None):
    errors, warnings = [], []
    if not proposal.get('change'):
        return errors, warnings
    cur = listing['current']
    if compliance is not None and not compliance.get('has_s1'):
        # 没排查过不拦，但要醒目提醒：改之前用户必须自己确认没有侵权词 / 敏感词（批准文案改稿时还要用户明确回一句）
        warnings.append(IP_UNCHECKED)
    fields = {'title': proposal.get('title') or '', 'bullets': ' '.join(proposal.get('bullets') or []),
              'search_terms': proposal.get('search_terms') or ''}
    for field, text in fields.items():
        t = toks(text)
        bad = sorted({b for b in brands if has_phrase(t, b)})
        if bad:
            errors.append(f'改后 {field} 含品牌词/禁用词：' + '、'.join(bad))
        for kind, word in claim_hits(text, compliance):
            if word not in bad:
                errors.append(f'改后 {field} 含受限用语（{kind}）：{word}')
        for kind, words in ((ct or {}).get('not_this') or {}).items():
            bad = sorted({w for w in words if has_phrase(t, w)})
            if bad:
                errors.append(f'改后 {field} 写了本品不是的属性（产品 CT）：' + '、'.join(bad))
    st = fields['search_terms']
    if len(st.encode('utf-8')) > 249:
        errors.append(f'Search Terms {len(st.encode("utf-8"))} 字节，超过 249')
    if len(fields['title']) > 200:
        errors.append(f'标题 {len(fields["title"])} 字符，超过 200')
    if (proposal.get('title') or '') != cur.get('title', ''):
        warnings.append('动了标题：默认不动标题，确认是有意为之')
    for i, b in enumerate(proposal.get('bullets') or [], 1):
        if not 150 <= len(b) <= 260:
            warnings.append(f'五点第 {i} 条 {len(b)} 字符（建议 200–260）')
    repeat = sorted(set(toks(st)) & set(toks(fields['title'] + ' ' + fields['bullets'])) - {'for', 'and', 'with', 'the'})
    if repeat:
        warnings.append('ST 重复了标题/五点已有词：' + '、'.join(repeat[:12]))
    return errors, warnings


CSS = """
:root{--bg:#f7f8fa;--card:#fff;--ink:#1d2330;--mute:#667085;--line:#e4e7ec;--acc:#0f766e;--up:#15803d;--down:#b42318;--warn:#b54708;--mark:#fff3b0}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0f1115;--card:#171a21;--ink:#e6e8ee;--mute:#98a2b3;--line:#2a2f3a;--acc:#2dd4bf;--up:#4ade80;--down:#f87171;--warn:#fbbf24;--mark:#5c4d00}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{display:flex;gap:20px;max-width:1280px;margin:0 auto;padding:16px}
nav{position:sticky;top:16px;align-self:flex-start;width:190px;flex:none;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px}
nav a{display:block;color:var(--ink);text-decoration:none;padding:4px 6px;border-radius:6px}nav a:hover{background:var(--bg)}
main{flex:1;min-width:0}section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin-bottom:14px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:0 0 10px}.mute{color:var(--mute)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px}.kpi{border:1px solid var(--line);border-radius:8px;padding:8px 10px}.kpi b{display:block;font-size:18px}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{border-bottom:1px solid var(--line);padding:6px;text-align:left;vertical-align:top}th{color:var(--mute);font-weight:600}
.box{max-height:460px;overflow:auto}.up{color:var(--up);font-weight:600}.down{color:var(--down);font-weight:600}.warn{color:var(--warn)}
mark{background:var(--mark);color:inherit;padding:0 2px;border-radius:3px}.tag{display:inline-block;border:1px solid var(--line);border-radius:10px;padding:0 6px;font-size:12px;color:var(--mute)}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:10px}.pair>div{border:1px solid var(--line);border-radius:8px;padding:8px}
details summary{cursor:pointer;color:var(--acc)}code{background:var(--bg);padding:1px 4px;border-radius:4px}
@media (max-width:760px){.wrap{display:block;padding:12px}nav{display:none}.pair{grid-template-columns:1fr}}
"""


def delta_pts(v):
    return '—' if v is None else '%+.0f 点' % (v * 100)


def cost_line(note):
    note = note or {}
    if note.get('status') != 'ok':
        return note.get('note') or '未接成本表，全店统一目标'
    if note.get('estimate'):
        return f"{note['table']}（没填成本表，所有活动按同一估算毛利判断；填了成本表会更准）"
    return (f"{Path(note['table']).name}，{note['campaigns_covered']}/{note['campaigns_total']} 个活动按自己 SKU 的保本 / 目标 ACOS 判断，"
            "其余用全店值")


def pct(v):
    return '—' if v is None else f'{v * 100:.1f}%'


def fmt(v):
    return '—' if v is None else (f'{v:,.2f}' if isinstance(v, float) else str(v))


def report(args):
    root = Path(args.run)
    folder = current_round(root)
    plan = load(folder / '02_诊断方案.json')
    evidence = load(folder / 'evidence.yaml')
    proposal = load(folder / 'listing_proposal.yaml')
    ct = None
    if PRODUCT_CT.is_file():
        result = module(PRODUCT_CT, 's5_product_ct').evaluate(root)
        ct = result if result.get('status') != 'missing' else None
    listing = plan['listing']
    errors, warnings = check_listing(proposal, listing, listing['brand_terms'], ct, compliance_state(root))
    ev_actions = evidence.get('actions') or {}
    held = sorted({i for i, e in ev_actions.items() if isinstance(e, dict) and e.get('advice') == '暂缓'} |
                  {a['id'] for a in plan['actions'] if a.get('scope_hold')})
    no_kb = [a['id'] for a in plan['actions'] if not (ev_actions.get(a['id']) or {}).get('kb')]
    if plan['actions'] and len(no_kb) == len(plan['actions']):
        warnings.append('所有动作都没写广告知识库依据（evidence.yaml 的 kb）：装了 ads_knowledge_base 就先 kb_diagnose 再出报告')
    if not plan['has_ids']:
        warnings.append('没有后台 ID：本轮只能看建议，生成上传表前补一份「批量操作」下载表')
    unchecked = [r['asin'] for r in (plan.get('order_breakdown') or {}).get('rows') or []
                 if r['node'] not in ('平稳', '上升', '样本太小') and not ((evidence.get('breakdown_check') or {}).get(r['asin']) or {}).get('conclusion')]
    if unchecked:  # 单量有问题先查总盘：市场、竞争、流量，不是一看卖得差就砍价格砍预算
        warnings.append(f"单量有问题的 {len(unchecked)} 个产品还没做总盘核对（市场 / 竞争 / 流量，写进 evidence.yaml 的 breakdown_check）："
                        + '、'.join(unchecked[:6]))
    eco, tot = plan['economics'], plan['totals']
    by_kind = defaultdict(list)
    for a in plan['actions']:
        by_kind[a['kind']].append(a)
    gap = listing.get('gap') or {}
    toc, body = [], []

    def sec(anchor, title, inner):
        toc.append(f'<a href="#{anchor}">{html.escape(title)}</a>')
        body.append(f'<section id="{anchor}"><h2>{html.escape(title)}</h2>{inner}</section>')

    verdict = '达标' if tot['acos'] is not None and tot['acos'] <= eco['target_acos'] else ('无销售' if tot['acos'] is None else '高于目标')
    sec('scope', '数据范围与口径', '<p class="warn">'+html.escape(plan.get('scope',{}).get('notice',''))+'</p><p>'+html.escape(window_notice(plan))+'</p>')
    sec('sum', '一眼结论', f"""<div class="kpis">
<div class="kpi"><span class="mute">花费</span><b>${fmt(tot['spend'])}</b></div>
<div class="kpi"><span class="mute">广告销售额</span><b>${fmt(tot['sales'])}</b></div>
<div class="kpi"><span class="mute">订单</span><b>{int(tot['orders'])}</b></div>
<div class="kpi"><span class="mute">ACOS（目标 {pct(eco['target_acos'])} · 保本 {pct(eco['break_even_acos'])}）</span><b>{pct(tot['acos'])} · {verdict}</b></div>
<div class="kpi"><span class="mute">转化率</span><b>{pct(tot['cvr'])}</b></div>
<div class="kpi"><span class="mute">建议动作 / 观望</span><b>{len(plan['actions'])} / {plan['watch_count']}</b></div></div>
<p>{'；'.join(f"{ACTION_LABEL[k]} {len(v)} 条" for k, v in by_kind.items()) or '本期没有需要动的对象'}。
范围：{'本品（' + '、'.join(plan['scope']['skus'] or plan['scope']['asins']) + '）' if plan['scope']['mode'] == 'product' else '全店'}，{plan['scope']['campaigns']} 个活动。</p>""")

    rows = []
    for a in plan['actions']:
        cur, new = a['current'], a['proposed']
        if a['kind'] in ('bid_down', 'bid_up'):
            change = f"竞价 ${fmt(cur['bid'])} → <span class=\"{'up' if a['kind'] == 'bid_up' else 'down'}\">${fmt(new['bid'])}</span>"
        elif a['kind'] == 'pause':
            change = '在投 → <span class="down">暂停</span>'
        elif a['kind'] in ('budget_up', 'budget_down'):
            change = (f"日预算 ${fmt(cur['daily_budget'])} → <span class=\"{'up' if a['kind'] == 'budget_up' else 'down'}\">${fmt(new['daily_budget'])}</span>"
                      f"<br><span class=\"mute\">近 {'—' if a['data'].get('budget_used') is None else int(a['data']['budget_used'] * 100)}% 预算被花掉</span>")
        elif a['kind'] in ('placement_up', 'placement_down'):
            change = (f"{PLACEMENT_CN.get(a['placement'], a['placement'])}加价 {cur['percentage']:.0f}% → "
                      f"<span class=\"{'up' if a['kind'] == 'placement_up' else 'down'}\">{new['percentage']:.0f}%</span>")
        elif a['kind'] == 'harvest_exact':
            change = f"{html.escape(str(cur['match_type']))}（{html.escape(str(cur['from']))}）→ <span class=\"up\">精准 ${fmt(new['bid'])}</span><br><span class=\"mute\">进{html.escape(new['into'])}；{html.escape(a['source_negative'])}</span>"
        else:
            change = f"在投 → <span class=\"down\">{html.escape(str(new['negative']))}</span>" + (f"<br><span class=\"mute\">例：{html.escape(a['example'])}</span>" if a.get('example') else '')
        ev = ev_actions.get(a['id']) or {}
        advice = str(ev.get('advice') or '')
        basis = (f'<b class="{"warn" if advice == "暂缓" else "up"}">{html.escape(advice)}</b><br>' if advice else '') + \
            ' '.join(f'<span class="tag">{html.escape(str(k))}</span>' for k in ev.get('kb') or []) + \
            (f"<br>{html.escape(str(ev.get('market')))}" if ev.get('market') else '') + \
            (f"<br><span class=\"mute\">{html.escape(str(ev.get('note')))}</span>" if ev.get('note') else '') + \
            (f"<br><span class=\"warn\">{html.escape(a['note'])}</span>" if a.get('note') else '')
        d = a['data']
        rows.append(f"<tr><td>{a['id']}</td><td><b>{html.escape(str(display_name(a['object'])))}</b> <span class=\"tag\">{html.escape(str(a['match_type'] or ''))}</span><br>"
                    f"<span class=\"mute\">{html.escape(a['campaign_name'] or '')} / {html.escape(a['ad_group_name'] or '')}</span></td>"
                    f"<td>{a['label']}</td><td>{change}</td><td>{html.escape(a['why'])}</td><td>{basis or '<span class=warn>未写依据</span>'}</td>"
                    f"<td>点击 {d['clicks']} · 单 {d['orders']}<br>花费 ${fmt(d['spend'])} · ACOS {pct(d['acos'])}<br>CPC ${fmt(d['cpc'])}</td></tr>")
    sec('ads', '广告：优化前 → 优化后', ('<div class="box"><table><tr><th>#</th><th>对象</th><th>动作</th><th>改前 → 改后</th><th>为什么</th><th>依据（知识库 / 市场）</th><th>本期数据</th></tr>'
                                   + ''.join(rows) + '</table></div>') if rows else '<p>本期没有达到动手条件的对象，全部观望。</p>')
    health = plan.get('product_health') or []
    if health:
        hrows = ''.join(
            f"<tr><td>{h['light']}</td><td><b>{html.escape(h['asin'])}</b><br><span class=\"mute\">{html.escape(str(h['name'])[:40])}</span></td>"
            f"<td>{html.escape(h['verdict'])}</td><td>{h['orders']}（上期 {h['orders_prev']}）</td>"
            f"<td>{pct(h['organic_share'])}（上期 {pct(h['organic_share_prev'])}，{delta_pts(h['organic_delta'])}）</td>"
            f"<td>{pct(h['ad_acos'])}<br><span class=\"mute\">目标 {pct(h['target_acos'])} / 保本 {pct(h['break_even_acos'])}</span></td>"
            f"<td>{pct(h['tacos'])}</td><td class=\"{'down' if (h['profit_after_ads'] or 0) < 0 else 'up'}\">{'—' if h['profit_after_ads'] is None else '$' + fmt(h['profit_after_ads'])}</td></tr>"
            for h in health)
        note = plan.get('product_health_note') or {}
        body.insert(0, f'<section id="health"><h2>产品健康（广告 + 自然合看）</h2><p class="mute">本期 {"～".join(note.get("window") or [])}，对比上期 {"～".join(note.get("previous") or [])}。'
                       f'保本 ACOS 是广告亏钱线，目标 ACOS 是方向；最终看 TACOS、扣广告后利润和自然单占比。🟢🟡 的产品本轮不收缩广告，只有 🔴 才出降价 / 降预算。</p>'
                       f'<div class="box"><table><tr><th></th><th>产品</th><th>判定</th><th>订单</th><th>自然单占比</th><th>广告 ACOS</th><th>TACOS</th><th>扣广告后利润</th></tr>{hrows}</table></div></section>')
        toc.insert(0, '<a href="#health">产品健康</a>')
    bd = plan.get('order_breakdown') or {}
    if bd.get('rows'):
        def cell(side, key, fmt_fn):
            v = side.get(key)
            return '—' if v is None else fmt_fn(v)
        checks_ev = evidence.get('breakdown_check') or {}

        def overall(r):
            if r['node'] in ('平稳', '上升', '样本太小'):
                return '—'
            c = checks_ev.get(r['asin']) or {}
            got = [f'{label}：{c[key]}' for label, key in (('市场', 'market'), ('竞争', 'competition'), ('流量', 'traffic')) if c.get(key)]
            if c.get('conclusion'):
                got.append('<b>结论：' + html.escape(str(c['conclusion'])) + '</b>')
            return '<br>'.join(html.escape(g) if not g.startswith('<b>') else g for g in got) or '<span class="warn">未核对总盘</span>'
        brows = ''.join(
            f"<tr><td><b>{html.escape(r['node'])}</b></td><td><b>{html.escape(r['asin'])}</b><br><span class=\"mute\">{html.escape(r['name'])}</span></td>"
            f"<td>{r['current']['orders']}（上期 {r['previous']['orders']}）<br><span class=\"mute\">广告 {r['current']['ad_orders']} / 自然 {cell(r['current'], 'organic', str)}</span></td>"
            f"<td>曝光 {cell(r['current'], 'impressions', lambda v: f'{v:,.0f}')}（上期 {cell(r['previous'], 'impressions', lambda v: f'{v:,.0f}')}）<br>"
            f"点击率 {pct(r['current']['ctr'])}（{pct(r['previous']['ctr'])}）· 转化 {pct(r['current']['cvr'])}（{pct(r['previous']['cvr'])}）</td>"
            f"<td>{html.escape('；'.join(r['why']) or '—')}</td><td>{overall(r)}</td><td>{html.escape(r['do'])}<br><span class=\"warn\">先别：{html.escape(r['dont'])}</span></td></tr>"
            for r in bd['rows'][:30])
        span = f"本期 {'～'.join(str(x) for x in bd.get('window') or [])}，对比{'上一轮 ' + bd['previous_round'] + ' ' if bd.get('previous_round') else '上期 '}{'～'.join(str(x) for x in bd.get('previous') or [])}。"
        body.insert(0, f'<section id="breakdown"><h2>单量拆解：先定位是哪一端</h2><p class="mute">{span}单量变了先拆：广告单还是自然单？曝光、点击率还是转化率？'
                       f'定位到端再动手，拆不出原因的加广告 / 降价 / 改图都是赌（广告知识库 KC-0163 / KC-0164）。{html.escape(bd.get("note") or "")}</p>'
                       f'<div class="box"><table><tr><th>哪一端</th><th>产品</th><th>订单</th><th>广告侧</th><th>依据</th><th>总盘核对（市场 / 竞争 / 流量）</th><th>该做 / 先别做</th></tr>{brows}</table></div></section>')
        toc.insert(0, '<a href="#breakdown">单量拆解</a>')
    elif bd.get('note'):
        body.insert(0, f'<section id="breakdown"><h2>单量拆解</h2><p class="mute">{html.escape(bd["note"])}</p></section>')
    views = plan.get('campaign_view') or []
    if views:
        cpc_all = ratio(tot['spend'], tot['clicks'])
        need_all = round(cpc_all / (eco['price'] * eco['target_acos']), 4) if cpc_all else None
        struct = (f"<p><b>结构性判断：</b>平均点击 ${fmt(cpc_all)}、客单价 ${fmt(eco['price'])}，要把 ACOS 压到 {pct(eco['target_acos'])}，转化率要到 <b>{pct(need_all)}</b>；"
               f"本期实际 {pct(tot['cvr'])}。" + ('差距在转化，只调竞价压不下来，还要看价格、图片、评论和 Listing 承接。' if need_all and tot['cvr'] and need_all > tot['cvr'] * 1.2 else '转化够，重点在控点击成本。') + '</p>') if need_all else ''
        vrows = ''.join(f"<tr><td>{html.escape(v['campaign_name'] or '')}</td><td>{html.escape(v['portfolio_name'] or '')}</td><td>${fmt(v['spend'])}</td><td>${fmt(v['sales'])}</td>"
                        f"<td>{v['orders']}</td><td class=\"{'down' if v['verdict'] in ('超目标', '无单', '亏钱（超保本）') else 'up' if v['verdict'] == '达标' else ''}\">{pct(v['acos'])} {v['verdict']}</td>"
                        f"<td>{pct(v.get('target_acos'))} / {pct(v.get('break_even_acos'))}<br><span class=\"mute\">{html.escape(str(v.get('econ_source') or ''))}</span></td>"
                        f"<td>${fmt(v['cpc'])}</td><td>{pct(v['cvr'])}</td><td>{pct(v['need_cvr'])}</td><td>${fmt(v['daily_budget'])}</td><td>{html.escape(str(v['bidding_strategy'] or ''))}</td></tr>"
                        for v in views if v['spend'] or v['state'] == 'enabled')
        body.insert(1, f'<section id="camp"><h2>活动一览（按花费）</h2>{struct}<div class="box"><table><tr><th>活动</th><th>广告组合</th><th>花费</th><th>销售</th><th>单</th><th>ACOS</th><th>目标 / 保本</th><th>CPC</th><th>转化率</th><th>达标需转化率</th><th>日预算</th><th>竞价策略</th></tr>{vrows}</table></div></section>')
        toc.insert(1, '<a href="#camp">活动一览</a>')
    watch_rows = ''.join(f"<tr><td>{html.escape(str(display_name(w['object'])))}</td><td>{html.escape(str(w['match_type'] or ''))}</td><td>{w['clicks']}</td><td>{w['orders']}</td><td>${fmt(w['spend'])}</td><td>{html.escape(w['why'])}</td></tr>" for w in plan['watch'])
    sec('watch', f"观望（{plan['watch_count']}）", f"<details><summary>展开：样本不够或在目标附近，本轮不动</summary><div class=\"box\"><table><tr><th>对象</th><th>匹配</th><th>点击</th><th>单</th><th>花费</th><th>原因</th></tr>{watch_rows}</table></div></details>")

    cur = listing['current']
    parts = []
    if listing['current_issues']:
        parts.append('<p class="warn"><b>现文案问题：</b>' + '；'.join(f"{html.escape(i['field'])} {html.escape(i['problem'])}" for i in listing['current_issues']) + '</p>')
    miss = gap.get('missing') or []
    buried = gap.get('buried') or []
    market = evidence.get('market_words') or []
    if miss or buried or market:
        parts.append('<table><tr><th>需求词</th><th>来源</th><th>现文案</th></tr>' +
                     ''.join(f"<tr><td>{html.escape(m['term'])}</td><td>{html.escape(str(m['source']))}</td><td class=\"down\">没埋</td></tr>" for m in miss[:30]) +
                     ''.join(f"<tr><td>{html.escape(str(m.get('term')))}</td><td>{html.escape(str(m.get('source') or ''))} {html.escape(str(m.get('note') or ''))}</td><td class=\"warn\">机会词</td></tr>" for m in market[:20]) +
                     ''.join(f"<tr><td>{html.escape(b['term'])}</td><td>{html.escape(str(b['source']))}</td><td class=\"up\">已在 {html.escape(str(b.get('field')))}</td></tr>" for b in buried[:30]) + '</table>')
    if proposal.get('change') and not compliance_state(root)['has_s1']:
        parts.insert(0, '<div style="border:2px solid var(--warn);border-radius:10px;padding:10px 14px;margin-bottom:12px">'
                        '<b class="warn">⚠️ 改文案前请先自己确认：这个产品没有做过 IP / 敏感词排查</b>'
                        '<p>S1 / S2 没有留下合规黑名单，所以下面的改稿只对照了通用禁用词和已知商标陷阱词。改稿是建议，改不改由你判断，但<b>改之前你要自己确认</b>：</p>'
                        '<ul><li>改后的每个词、每个短语，都不是别人的商标或品牌（USPTO <code>tmsearch.uspto.gov</code> 查一下核心词和新增的卖点短语）；</li>'
                        '<li>没有绝对化用语（#1、best、100% guaranteed）、促销词、疗效 / 医疗宣称、抗菌杀菌、FDA / EPA 背书；</li>'
                        '<li>没有「兼容 / 同款 / 类似」某品牌的写法，除非那是允许的说明性用法；</li>'
                        '<li>不确定的词，先不用，或者回对话说「IP排查」让助手先帮你排一遍。</li></ul>'
                        '<p class="mute">批准文案改动时，助手会请你明确回一句「我会自己核对，确认没有侵权词和敏感词」。</p></div>')
    if proposal.get('change'):
        pairs = [('标题', cur.get('title', ''), proposal.get('title', ''))]
        for i, (old, new) in enumerate(zip(list(cur.get('bullets') or []) + [''] * 5, list(proposal.get('bullets') or []) + [''] * 5), 1):
            if i > 5 or (not old and not new):
                continue
            pairs.append((f'五点 {i}', old, new))
        pairs.append(('Search Terms', cur.get('search_terms', ''), proposal.get('search_terms', '')))
        parts.append(''.join(f"<h3>{html.escape(n)}{'' if o == nw else ' <span class=tag>改</span>'}</h3><div class=\"pair\"><div><span class=\"mute\">改前</span><br>{html.escape(o)}</div><div><span class=\"mute\">改后（黄底=新增）</span><br>{diff_html(o, nw)}</div></div>"
                             for n, o, nw in pairs))
        notes = proposal.get('notes') or []
        if notes:
            parts.append('<ul>' + ''.join(f"<li>{html.escape(json.dumps(n, ensure_ascii=False) if isinstance(n, dict) else str(n))}</li>" for n in notes) + '</ul>')
    else:
        parts.append('<p class="mute">本轮不改文案（listing_proposal.yaml 的 change 为 false）。</p>')
    sec('copy', '文案：优化前 → 优化后', ''.join(parts) + f"<p class=\"mute\">现文案来源：{html.escape(str(cur.get('source') or '未找到（放 当前文案.json 到投喂夹）'))}。文案改动需你在后台编辑商品页面手动粘贴保存；改后等 7 天再看。</p>")

    mcp = evidence.get('mcp_used') or []
    sec('how', '数据与方法', f"""<ul>
<li>数据：{'、'.join(Path(f['path']).name if f.get('path') else f"领星 OpenAPI 只读（店铺 sid {f.get('sid')}，{f.get('start')}～{f.get('end')}，{f.get('seconds')} 秒）" for f in load(folder / '01_广告快照.json').get('files', []))}（{html.escape(load(folder / '01_广告快照.json').get('period', {}).get('note') or '')}）</li>
<li>成本表：{html.escape(cost_line(eco.get('cost_table')))}</li>
<li>利润账：客单价 ${fmt(eco['price'])}，目标 ACOS {pct(eco['target_acos'])}，保本 ACOS {pct(eco['break_even_acos'])}，一单目标成本 ${fmt(eco['target_cpa'])}；转化率按 {pct(eco['cvr_used'])}（{html.escape(eco['cvr_used_source'])}）</li>
<li>阈值：点击 &lt; {plan['thresholds']['thin_clicks']} 只观望；无单降价需点击 ≥ {plan['thresholds']['no_order_clicks']}；暂停需点击 ≥ {plan['thresholds']['pause_clicks']} 且花费 ≥ ${fmt(plan['thresholds']['pause_spend'])}</li>
<li>知识库 / 市场 MCP：{'、'.join(map(str, mcp)) or '本轮未调用'}{('；' + html.escape(str(evidence.get('kb_summary')))) if evidence.get('kb_summary') else ''}</li>
<li>产品 CT：{html.escape(str(plan['product_ct'].get('material') or plan['product_ct'].get('status')))}（不是本品的属性词自动进否定建议、禁止进文案）</li></ul>""")
    checks = ''.join(f'<li class="down">必须改：{html.escape(e)}</li>' for e in errors) + ''.join(f'<li class="warn">{html.escape(w)}</li>' for w in warnings)
    sec('next', '下一步', f"""{('<ul>' + checks + '</ul>') if checks else ''}
<ol><li>看完回复「全部同意」，或「同意 A01、A03，不要 A02」「文案不改」。</li>
<li>助手生成上传表：05_上传包/ 里的 xlsx 到广告后台「批量操作 → 上传」；新活动已带广告组合，不用手动归组。</li>
<li>文案：打开卖家后台「编辑商品信息」，按上传包里的改文案页逐栏复制保存。</li>
<li>上传后回复处理报告结果（成功 N/N），72 小时看方向、7 天看转化，下一轮重新下载表格。</li></ol>""")
    kb_basis=knowledge_text(root,3,'')
    if kb_basis:
        sec('knowledge','知识库依据','<div>'+''.join('<p>'+html.escape(line)+'</p>' for line in kb_basis.splitlines() if line.strip())+'</div>')
        if kb_basis.startswith('本步未能调用知识库'):
            body.insert(0,'<section class="warn"><p>'+html.escape(kb_basis.splitlines()[0])+'</p></section>')
    title = f"广告与文案优化 · {plan['round']}"
    page = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>S5 优化前后对比</title><style>{CSS}</style></head><body><div class="wrap"><nav>{''.join(toc)}</nav><main>
<section><h1>{html.escape(title)}</h1><p class="mute">生成于 {now():%Y-%m-%d %H:%M}。全部是待你审批的建议；系统不上传、不改后台。</p></section>
{''.join(body)}</main></div></body></html>"""
    page_path = folder / '03_优化前后对比.html'
    page_path.write_text(page, encoding='utf-8')
    md = [f'# {title}', '', plan.get('scope',{}).get('notice',''), window_notice(plan),
          f"ACOS {pct(tot['acos'])}（目标 {pct(eco['target_acos'])}），花费 ${fmt(tot['spend'])}，订单 {int(tot['orders'])}。", '',
          '| # | 对象 | 动作 | 改前 → 改后 | 为什么 |', '|---|---|---|---|---|']
    for a in plan['actions']:
        md.append(f"| {a['id']} | {a['object']} | {a['label']} | {json.dumps(a['current'], ensure_ascii=False)} → {json.dumps(a['proposed'], ensure_ascii=False)} | {a['why']} |")
    md += ['', f"文案：{'有改稿，见 HTML' if proposal.get('change') else '本轮不改'}；缺口 {len(miss)} 词。"] + [f'- 必须改：{e}' for e in errors] + [f'- 提醒：{w}' for w in warnings]
    (folder / '03_优化前后对比.md').write_text(knowledge_text(root,3,'\n'.join(md)+'\n'), encoding='utf-8')
    status = {'generator': 's5_optimize.py', 'page_sha256': sha(page_path), 'plan_sha256': sha(folder / '02_诊断方案.json'),
              'evidence_sha256': sha(folder / 'evidence.yaml'), 'proposal_sha256': sha(folder / 'listing_proposal.yaml'),
              'errors': errors, 'warnings': warnings, 'actions': len(plan['actions']), 'listing_change': bool(proposal.get('change')),
              'ip_unchecked': bool(proposal.get('change')) and not compliance_state(root)['has_s1']}
    dump_json(folder / '03_report_status.json', status)
    return {'status': 'fail' if errors else 'ok', 'html': str(page_path), 'errors': errors, 'warnings': warnings}, (2 if errors else 0)


# ---------- approve / bulk / receipt ----------

def approve(args):
    folder = current_round(Path(args.run))
    plan = load(folder / '02_诊断方案.json')
    status = load(folder / '03_report_status.json')
    if not status or status.get('errors'):
        return {'status': 'blocked', 'error': '报告还有「必须改」或没生成：先 report'}, 2
    if args.listing and status.get('ip_unchecked') and not getattr(args, 'ack_ip', False):
        return {'status': 'need_ack', 'error': IP_UNCHECKED, 'ask': '文案要改的话，请你明确回一句：「我会自己核对，确认改后没有侵权词和敏感词」。'
                '你回了，助手再用 --ack-ip 批准；也可以先不批文案，只批广告动作。'}, 2
    ids = [a['id'] for a in plan['actions']]
    approved = ids if args.all else [i for i in (args.approve or '').replace('，', ',').split(',') if i.strip()]
    rejected = [i for i in (args.reject or '').replace('，', ',').split(',') if i.strip()]
    unknown = sorted(set(approved + rejected) - set(ids))
    if unknown:
        return {'status': 'fail', 'error': '没有这些动作编号：' + '、'.join(unknown)}, 2
    approved = [i for i in approved if i not in rejected]
    scope_held = {a['id'] for a in plan['actions'] if a.get('scope_hold')}
    if not args.all and scope_held.intersection(approved):
        return {'status':'blocked', 'error':SHARED_NOTICE, 'held':sorted(scope_held.intersection(approved))}, 2
    approved = [i for i in approved if i not in scope_held]
    evidence = load(folder / 'evidence.yaml')
    held = sorted({i for i, e in (evidence.get('actions') or {}).items() if isinstance(e, dict) and e.get('advice') == '暂缓'} | scope_held)
    if args.all:
        approved = [i for i in approved if i not in held]  # 「全部同意」不含依据判为暂缓的；要做就点名
    doc = {'plan_sha256': sha(folder / '02_诊断方案.json'), 'approved_actions': approved, 'held_by_evidence': [i for i in held if i not in approved], 'rejected_actions': rejected, 'listing': 'approved' if args.listing else 'not_approved',
           'by': args.by, 'user_words': args.note, 'at': now().isoformat(), 'report_page_sha256': status['page_sha256'],
           'ip_check': ('用户确认自己核对（该品没有 S1 / S2 排查记录）' if args.listing and status.get('ip_unchecked') else 'S1 / S2 排查记录已对照' if args.listing else '')}
    dump_json(folder / '04_审批.json', doc)
    return {'status': 'ok', 'approved': len(approved), 'rejected': len(rejected), 'listing': doc['listing']}, 0


def shared_action(action, scope):
    shared = set(scope.get('shared_campaign_ids', []))
    return bool(action.get('scope_hold') or action.get('campaign_id') in shared or
                (action.get('destination') or {}).get('campaign_id') in shared)


def operations(plan, approved):
    ops = []
    chosen = [a for a in plan['actions'] if a['id'] in approved]
    if any(shared_action(a, plan.get('scope', {})) for a in chosen):
        raise ValueError(SHARED_NOTICE)
    created = set()
    for a in chosen:
        dest = a.get('destination') or {}
        if a['kind'] != 'harvest_exact' or dest.get('mode') != 'new' or dest['campaign_id'] in created:
            continue
        if not dest.get('sku'):
            raise ValueError(f"{a['id']}：来源广告组找不到 SKU，不能新建精准活动；拒绝这条或手动指定")
        created.add(dest['campaign_id'])
        n = len(created)
        base = {'campaign_id': dest['campaign_id'], 'campaign_name': dest['campaign_name']}
        ops.append({'action_id': f'NEW-CAMPAIGN-{n}', 'entity': 'Campaign', 'operation': 'Create', **base,
                    'portfolio_id': dest.get('portfolio_id') or None, 'start_date': f'{now():%Y%m%d}', 'targeting_type': 'MANUAL',
                    'state': 'enabled', 'daily_budget': 10.0, 'bidding_strategy': 'Dynamic bids - down only'})
        group = {**base, 'ad_group_id': dest['ad_group_id'], 'ad_group_name': dest['ad_group_name']}
        ops.append({'action_id': f'NEW-AD-GROUP-{n}', 'entity': 'Ad Group', 'operation': 'Create', **group,
                    'ad_group_default_bid': 0.5, 'state': 'enabled'})
        ops.append({'action_id': f'NEW-PRODUCT-AD-{n}', 'entity': 'Product Ad', 'operation': 'Create', **group, 'sku': dest['sku'], 'state': 'enabled'})
    for a in chosen:
        ids = {'campaign_id': a['campaign_id'], 'ad_group_id': a['ad_group_id'], 'campaign_name': a['campaign_name'], 'ad_group_name': a['ad_group_name']}
        if a['kind'] in ('budget_up', 'budget_down'):
            row = {'action_id': a['id'], 'entity': 'Campaign', 'operation': 'Update', 'campaign_id': a['campaign_id'],
                   'campaign_name': a['campaign_name'], 'daily_budget': a['proposed']['daily_budget'], 'state': 'enabled',
                   'start_date': a.get('start_date') or None, 'bidding_strategy': a.get('bidding_strategy') or None}
            ops.append({k: v for k, v in row.items() if v not in (None, '')})
            continue
        if a['kind'] in ('placement_up', 'placement_down'):
            ops.append({'action_id': a['id'], 'entity': 'Bidding Adjustment', 'operation': 'Update', 'campaign_id': a['campaign_id'],
                        'placement': a['placement'], 'percentage': a['proposed']['percentage']})
            continue
        if a['kind'] in ('bid_down', 'bid_up', 'pause'):
            key = 'keyword_id' if a['entity'] == 'Keyword' else 'product_targeting_id'
            row = {'action_id': a['id'], 'entity': a['entity'], 'operation': 'Update', **ids, key: a[key],
                   'state': a['proposed'].get('state', 'enabled'), 'bid': a['proposed']['bid']}
        elif a['kind'] == 'harvest_exact':
            target = {k: a['destination'][k] for k in ('campaign_id', 'ad_group_id', 'campaign_name', 'ad_group_name')}
            row = {'action_id': a['id'], 'entity': 'Keyword', 'operation': 'Create', **target, 'keyword_text': a['object'],
                   'match_type': 'exact', 'bid': a['proposed']['bid'], 'state': 'enabled'}
        elif a['kind'] == 'negative_asin':
            row = {'action_id': a['id'], 'entity': 'Negative Product Targeting', 'operation': 'Create', **ids,
                   'product_targeting_expression': a['proposed']['negative'], 'state': 'enabled'}
        else:
            row = {'action_id': a['id'], 'entity': 'Negative Keyword', 'operation': 'Create', **ids, 'keyword_text': a['object'],
                   'match_type': 'negativePhrase' if a['kind'] == 'negative_phrase_ct' else 'negativeExact', 'state': 'enabled'}
        ops.append({k: v for k, v in row.items() if v not in (None, '')})
    for op in ops:
        op['ad_product'] = 'SP'
    return ops


ZH_OUT = {'entity': {v: k for k, v in ENTITY_ZH.items()}, 'state': {v: k for k, v in STATE_ZH.items()},
          'match_type': {v: k for k, v in MATCH_ZH.items()}, 'targeting_type': {v: k for k, v in TARGETING_ZH.items()},
          'bidding_strategy': {v: k for k, v in STRATEGY_ZH.items()}, 'operation': {v: k for k, v in OPERATION_ZH.items()},
          'product': {v: k for k, v in PRODUCT_ZH.items()},
          'placement': {'placementTop': '广告位：搜索结果首页首位', 'placementProductPage': '广告位：商品页面',
                        'placementRestOfSearch': '广告位：搜索结果的其余位置', 'placementAmazonBusiness': '广告位亚马逊企业购'}}


def write_like_download(download, ops, path, operation_lang):
    """按用户自己下载的批量表：同一张表名、同一行表头、同一种语言写改动行（只写要改的行）。"""
    header = download['header']
    columns = {}
    for i, raw in enumerate(header):
        key = norm_header(raw)
        if key and key not in columns and not re.search(r'仅供参考|informational only', raw, re.I):
            columns[key] = i
    zh = download['language'] == 'zh'
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = download['sheet']
    ws.append(header)
    row_map = []
    for op in ops:
        values = [None] * len(header)
        for key, value in op.items():
            if key in ('action_id', 'ad_product') or key not in columns:
                continue
            # 名称列只属于活动行 / 广告组行；其他实体在下载表里也是空的（名字在「仅供参考」列）
            if (key == 'campaign_name' and op['entity'] != 'Campaign') or (key == 'ad_group_name' and op['entity'] != 'Ad Group'):
                continue
            if zh and key in ZH_OUT and not (key == 'operation' and operation_lang == 'en'):
                value = ZH_OUT[key].get(value, value)
            values[columns[key]] = value
        ws.append(values)
        row_map.append({'excel_row': ws.max_row, 'action_id': op['action_id'], 'entity': op['entity'], 'operation': op['operation']})
    missing = sorted({k for op in ops for k in op if k not in ('action_id', 'ad_product', 'campaign_name', 'ad_group_name') and k not in columns})
    if missing:
        raise ValueError('下载表里没有这些可写列：' + '、'.join(missing))
    wb.save(path)
    return row_map


def bulk(args):
    root = Path(args.run)
    folder = current_round(root)
    plan = load(folder / '02_诊断方案.json')
    snap = load(folder / '01_广告快照.json')
    decision = load(folder / '04_审批.json')
    status = load(folder / '03_report_status.json')
    if not decision:
        return {'status': 'blocked', 'error': '还没审批：用户同意后先 approve'}, 2
    if decision.get('report_page_sha256') != status.get('page_sha256') or status.get('page_sha256') != sha(folder / '03_优化前后对比.html'):
        return {'status': 'blocked', 'error': '审批后报告又变了：重新给用户看报告并重新 approve'}, 2
    if decision.get('plan_sha256') and decision['plan_sha256'] != sha(folder / '02_诊断方案.json'):
        return {'status': 'blocked', 'error': '审批后方案又变了（动作编号可能已不同）：重新出报告、重新 approve'}, 2
    out = folder / '05_上传包'
    out.mkdir(exist_ok=True)
    result = {'status': 'ok', 'folder': str(out)}
    if decision['approved_actions']:
        download = snap.get('download')
        if not plan['has_ids'] or not download:
            return {'status': 'blocked', 'error': '没有「批量操作」下载表（带后台 ID），不能生成上传表：补下载表后重跑 ingest → diagnose → report'}, 2
        core = module(BULK_SKILL / 'scripts' / 'bulk_core.py', 's5_bulk_core')
        try:
            ops = operations(plan, decision['approved_actions'])
            for op in ops:
                op['product'] = 'Sponsored Products'
            core.validate_required_fields(ops)
            core.validate_parent_graph(ops)
        except (ValueError, core.ContractError) as exc:
            return {'status': 'fail', 'error': str(exc)}, 2
        xlsx = out / f"S5_优化上传_{plan['round']}.xlsx"
        try:
            row_map = write_like_download(download, ops, xlsx, args.operation_lang)
        except ValueError as exc:
            return {'status': 'fail', 'error': str(exc)}, 2
        news = {a['destination']['campaign_id']: a['destination'] for a in plan['actions']
                if a['id'] in decision['approved_actions'] and (a.get('destination') or {}).get('mode') == 'new'}
        manifest = {'artifact_type': 's5_bulk_upload', 'generator': 's5_optimize.py', 'created_at': now().isoformat(),
                    'round': plan['round'], 'file': {'path': str(xlsx), 'sha256': sha(xlsx)},
                    'format_from': {'path': download['path'], 'sheet': download['sheet'], 'language': download['language'],
                                    'operation_values': 'en' if args.operation_lang == 'en' else download['language']},
                    'counts': core.operation_counts(ops), 'row_map': row_map, 'approval': str(folder / '04_审批.json'),
                    'new_campaigns': [{'name': d['campaign_name'], 'portfolio_id': d.get('portfolio_id'), 'portfolio': d.get('portfolio_name')} for d in news.values()],
                    'uploadability': {'amazon_upload_status': 'not_submitted', 'note': '系统不代传；用户在广告后台「批量操作」上传后回执'}}
        dump_json(out / 'bulk_manifest.json', manifest)
        result.update(bulk=str(xlsx), rows=len(ops), counts=manifest['counts'], new_campaigns=manifest['new_campaigns'])
    page = copy_page(root, folder, plan, decision)
    result['copy_page'] = str(page)
    return result, 0


def copy_page(root, folder, plan, decision):
    proposal = load(folder / 'listing_proposal.yaml')
    cur = plan['listing']['current']
    items = []
    if decision.get('listing') == 'approved' and proposal.get('change'):
        fields = [('商品名称（标题）', cur.get('title', ''), proposal.get('title', ''))]
        fields += [(f'五点描述 第 {i} 条', o, n) for i, (o, n) in enumerate(zip(list(cur.get('bullets') or []) + [''] * 5, proposal.get('bullets') or []), 1) if o != n]
        fields.append(('搜索关键词（Search Terms）', cur.get('search_terms', ''), proposal.get('search_terms', '')))
        items = [(n, o, nw) for n, o, nw in fields if o != nw]
    blocks = ''.join(f"""<section><h2>{html.escape(n)}</h2><div class="pair"><div><span class="mute">后台现在</span><br>{html.escape(o)}</div>
<div><span class="mute">改成（黄底=新增）</span><br>{diff_html(o, nw)}</div></div>
<p><button onclick="navigator.clipboard.writeText(this.dataset.t);this.textContent='已复制'" data-t="{html.escape(nw, quote=True)}">复制改后内容</button></p></section>"""
                     for n, o, nw in items) or '<section><p>本轮没有批准的文案改动。</p></section>'
    xlsx = next((p.name for p in (folder / '05_上传包').glob('*.xlsx')), None)
    steps = (f"<li>广告后台 → 批量操作 → 上传 <code>{html.escape(xlsx)}</code>，等处理报告显示成功 N/N。</li>"
             "<li>新建的精准活动已写好广告组合编号，上传后会自动归到本品的广告组合，不用手动拖。</li>") if xlsx else '<li>本轮没有广告改动。</li>'
    page = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>S5 上传与改文案</title><style>{CSS} button{{border:1px solid var(--line);background:var(--bg);color:var(--ink);border-radius:6px;padding:4px 10px;cursor:pointer}}</style></head>
<body><div class="wrap"><main><section><h1>从这里开始 · {html.escape(plan['round'])}</h1><ol>{steps}
<li>文案：卖家后台 → 库存 → 编辑商品信息（SKU {html.escape(cur.get('sku') or '')}），按下面逐栏复制粘贴，保存。</li>
<li>都做完回复「已上传，N/N 成功」，助手记回执：72 小时看方向，7 天看转化。</li></ol>
<p class="mute">系统没有代你上传或修改任何东西。</p></section>{blocks}</main></div></body></html>"""
    path = folder / '05_上传包' / '从这里开始.html'
    path.write_text(page, encoding='utf-8')
    return path


def receipt(args):
    folder = current_round(Path(args.run))
    at = now()
    doc = {'uploaded': args.status, 'user_words': args.note, 'at': at.isoformat(),
           'check_direction_at': (at + timedelta(hours=72)).isoformat(), 'check_conversion_at': (at + timedelta(days=7)).isoformat(),
           'next_round': '到期后重新下载「批量操作」表格放进投喂夹，从 ingest 开始下一轮；本轮暂缓的来源否定在下一轮核实精准组后执行'}
    dump_json(folder / '06_回执与观察.json', doc)
    return {'status': 'ok', **doc}, 0


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('ingest')
    p.add_argument('--run', required=True)
    p.add_argument('--input', action='append')
    p.add_argument('--source', choices=['file', 'lingxing'], default='file', help='file = 后台下载表（第一部分）；lingxing = 领星只读取数（第二部分）')
    p.add_argument('--sid', help='领星店铺 sid（--source lingxing 时必填）')
    p.add_argument('--scope', choices=['product', 'store'], default='product')
    p.add_argument('--sku', action='append')
    p.add_argument('--asin', action='append')
    p.add_argument('--parent', action='append', help='父体 ASIN（领星路线）：一次优化它下面所有在售变体；给变体 ASIN 也行，会带上同父体的兄弟')
    p.add_argument('--start')
    p.add_argument('--end')
    p = sub.add_parser('diagnose')
    p.add_argument('--run', required=True)
    for k in ('--price', '--target-acos', '--break-even-acos', '--cvr', '--marketplace'):
        p.add_argument(k)
    p = sub.add_parser('report')
    p.add_argument('--run', required=True)
    p = sub.add_parser('kb-report',help='实际知识库调用完成后生成本轮诊断说明，不改变方案或审批')
    p.add_argument('--run', required=True)
    p = sub.add_parser('approve')
    p.add_argument('--run', required=True)
    p.add_argument('--all', action='store_true')
    p.add_argument('--approve')
    p.add_argument('--reject')
    p.add_argument('--listing', action='store_true', help='用户同意文案改稿')
    p.add_argument('--ack-ip', dest='ack_ip', action='store_true', help='该品没有 S1 / S2 排查记录时：用户已明确回复「我会自己核对，确认没有侵权词和敏感词」')
    p.add_argument('--by', default='user')
    p.add_argument('--note', default='')
    p = sub.add_parser('bulk')
    p.add_argument('--run', required=True)
    p.add_argument('--operation-lang', choices=['auto', 'en'], default='auto',
                   help='操作列取值：auto 跟下载表语言（中文表写 创建/更新）；后台报错不认时用 en（Create/Update）')
    p = sub.add_parser('status')
    p.add_argument('--run', required=True)
    p = sub.add_parser('manual')
    p.add_argument('--run', required=True)
    p.add_argument('--kind', choices=['negative_exact', 'negative_phrase'], required=True)
    p.add_argument('--term', required=True, help='搜索词原文')
    p.add_argument('--campaign', help='活动 ID 或名称（搜索词出现在多个活动时必填）')
    p.add_argument('--reason', required=True, help='为什么要否（写进报告）')
    p = sub.add_parser('receipt')
    p.add_argument('--run', required=True)
    p.add_argument('--status', choices=['uploaded', 'partial', 'failed', 'listing_only'], required=True)
    p.add_argument('--note', default='')
    return ap


def manual(args):
    """人工补一条动作（否定精准 / 否定词组 / 暂停），进本轮方案，之后照样出报告、审批、写回。"""
    folder = current_round(Path(args.run))
    plan_path = folder / '02_诊断方案.json'
    plan = load(plan_path)
    snap = load(folder / '01_广告快照.json')
    term = args.term.strip()
    hit = [s for s in snap['search_terms'] if s['search_term'].lower() == term.lower()
           and (not args.campaign or args.campaign in (s['campaign_id'], s['campaign_name']))]
    if args.kind in ('negative_exact', 'negative_phrase') and not hit:
        return {'status': 'fail', 'error': f'本轮搜索词里没有「{term}」' + (f'（活动 {args.campaign}）' if args.campaign else '')}, 2
    if len(hit) > 1 and not args.campaign:
        return {'status': 'fail', 'error': '这个搜索词出现在多个活动，用 --campaign 指定', 'campaigns': sorted({s['campaign_name'] for s in hit})}, 2
    s = hit[0]
    m = s['metrics']
    data = {'clicks': int(m['clicks']), 'orders': int(m['orders']), 'spend': money(m['spend']), 'sales': money(m['sales']),
            'acos': ratio(m['spend'], m['sales']), 'cpc': ratio(m['spend'], m['clicks']), 'cvr': ratio(m['orders'], m['clicks'])}
    kind = 'negative_exact' if args.kind == 'negative_exact' else 'negative_phrase_ct'
    action = {'id': f"A{len(plan['actions']) + 1:02d}", 'kind': kind, 'label': '否定精准' if kind == 'negative_exact' else '否定词组',
              'rule': 'M1', 'why': f"{RULES['M1']}：{args.reason}", 'object': term, 'match_type': s['match_type'],
              'campaign_name': s['campaign_name'], 'ad_group_name': s['ad_group_name'], 'campaign_id': s['campaign_id'],
              'ad_group_id': s['ad_group_id'], 'current': {'state': '在投'},
              'proposed': {'negative': 'negativeExact' if kind == 'negative_exact' else 'negativePhrase'}, 'data': data,
              'entity': 'Negative Keyword', 'manual_reason': args.reason}
    plan['actions'].append(action)
    dump_json(plan_path, plan)
    return {'status': 'ok', 'added': action['id'], 'next': '重跑 report 给用户看，批准后 approve --approve ' + action['id']}, 0


def status(args):
    """当前这一轮到哪一步了：方案、依据、审批、写回；「看今天的广告方案」先跑这个。"""
    folder = current_round(Path(args.run))
    plan, snap = load(folder / '02_诊断方案.json'), load(folder / '01_广告快照.json')
    evidence = load(folder / 'evidence.yaml')
    decision, receipt = load(folder / '04_审批.json'), load(folder / '06_写回回执.json')
    filled = [i for i, e in (evidence.get('actions') or {}).items() if isinstance(e, dict) and (e.get('kb') or e.get('advice'))]
    ids = [a['id'] for a in plan.get('actions') or []]
    if not plan:
        step = '先 diagnose'
    elif len(filled) < len(ids):
        step = '补依据：调广告知识库（kb_diagnose / kb_scenario_rules）和 SIF / 西柚，填 evidence.yaml 的 kb / market / advice，再 report 给用户看'
    elif not decision:
        step = '等用户审批：发报告链接，用户回「确认按建议改」或点名编号 → approve'
    elif not receipt:
        step = '审批已记：能写回的走 s5_lingxing_writeback.py（先预演再 --execute）；新建活动走 bulk 出上传表；文案出改前改后给用户手改'
    else:
        step = '本轮已写回：72 小时看方向、7 天看效果；下一轮重新 ingest'
    health = plan.get('product_health') or []
    return {'status': 'ok', 'round': folder.name, 'window': [snap.get('period', {}).get('start'), snap.get('period', {}).get('end')],
            'source': (snap.get('files') or [{}])[0].get('source') or 'file', 'report': str(folder / '03_优化前后对比.html'),
            'lights': dict(Counter(h['light'] for h in health)),
            'actions': [{'id': a['id'], 'label': a['label'], 'object': display_name(a['object']), 'campaign': a['campaign_name'],
                         'current': a['current'], 'proposed': a['proposed'],
                         'advice': ((evidence.get('actions') or {}).get(a['id']) or {}).get('advice')} for a in plan.get('actions') or []],
            'evidence_filled': f'{len(filled)}/{len(ids)}', 'approved': decision.get('approved_actions'),
            'written': (receipt.get('summary') or {}), 'next': step}, 0


COMMANDS = {'ingest': ingest, 'diagnose': diagnose, 'report': report, 'kb-report':kb_report, 'approve': approve, 'bulk': bulk, 'receipt': receipt,
            'manual': manual, 'status': status}


def main(argv=None):
    args = build_parser().parse_args(argv)
    result, code = COMMANDS[args.cmd](args)
    print(json.dumps(result, ensure_ascii=False))
    return code


if __name__ == '__main__':
    sys.exit(main())
