#!/usr/bin/env python3
"""在售产品成本维护表：每个 SKU 的售价、落地成本、毛利、盈亏 ACOS、目标 ACOS。

表是用户自己维护的 Excel（成本表 / 参数 / 说明 三页），只填黄色输入列，绿色列由公式算。
这里按同样的公式用 Python 重算，不依赖 Excel 缓存值（别的软件改过、没重算也照样对）。

  python s5_cost_table.py --set US "/…/成本表_美国站.xlsx"     # 登记表的位置（写用户目录 config/cost_tables.yaml）
  python s5_cost_table.py --show US                            # 看每个 SKU 算出来的保本 / 目标 ACOS
  python s5_cost_table.py --template "/…/成本表模板.xlsx"         # A 档：空白模板（黄色列填，绿色列公式算）
  python s5_cost_table.py --generate US                        # B 档：从领星预填售价 / FBA 费 / 采购成本 / 头程，你只补空格
  python s5_cost_table.py --estimate US 0.30                   # C 档：不填表，按毛利率 30% 估算（报告和日报标「估算」）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from pathlib import Path

import openpyxl
import yaml

warnings.filterwarnings('ignore', message='Workbook contains no default style')

COLUMNS = {'店铺': 'store', 'SKU': 'sku', 'ASIN': 'asin', '名称': 'name', '状态': 'status', '近30天销量': 'units_30d',
           'FBA可售': 'fba_available', '售价(站点币)': 'price', '采购成本(元)': 'purchase_cny', '头程(元/件)': 'freight_cny',
           'FBA配送费(站点币)': 'fba_fee', '其他费用(站点币)': 'other_fee', '佣金率': 'commission', '目标利润率': 'target_margin',
           '姿态': 'posture', '备注': 'note'}
PARAMS = {'汇率': 'fx', '默认佣金率': 'commission', '默认目标利润率': 'target_margin', '目标ACOS下限': 'acos_floor',
          '目标ACOS上限': 'acos_cap', 'VAT率': 'vat', '数字服务费率': 'dst'}


def config_path():
    home = Path(os.environ.get('STARSKY_HOME') or (Path.home() / '.starsky-codex')).expanduser()
    return Path(os.environ.get('STARSKY_CONFIG_DIR') or (home / 'config')) / 'cost_tables.yaml'


def registered():
    path = config_path()
    return (yaml.safe_load(path.read_text(encoding='utf-8')) or {}) if path.is_file() else {}


def register(marketplace, table):
    doc = registered()
    doc[marketplace.upper()] = str(Path(table).expanduser().resolve())
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding='utf-8')
    return path


def num(value):
    try:
        return None if value in (None, '') else float(value)
    except (TypeError, ValueError):
        return None


def read_params(ws):
    params, posture = {}, {}
    for row in ws.iter_rows(values_only=True):
        label = str(row[0] or '')
        for key, name in PARAMS.items():
            if label.startswith(key) and num(row[1]) is not None:
                params[name] = num(row[1])
        if len(row) > 4 and row[3] and num(row[4]) is not None:
            posture[str(row[3]).strip()] = num(row[4])
    return params, posture


def compute(row, params, posture):
    """与表里公式一致：落地成本 = (采购+头程)/汇率；毛利 = 售价 − 落地 − FBA − 其他 − 佣金 [− 数字服务费 − VAT]；
    盈亏 ACOS = 毛利率；目标 ACOS = 毛利率 − 目标利润率 + 姿态调整，夹在上下限之间。"""
    price, purchase, freight = num(row.get('price')), num(row.get('purchase_cny')), num(row.get('freight_cny'))
    fba = num(row.get('fba_fee'))
    if not price or purchase is None or freight is None or fba is None or not params.get('fx'):
        return None
    commission = num(row.get('commission'))
    commission = params.get('commission', 0.15) if commission is None else commission
    target_margin = num(row.get('target_margin'))
    target_margin = params.get('target_margin', 0.1) if target_margin is None else target_margin
    landed = (purchase + freight) / params['fx']
    gross = price - landed - fba - (num(row.get('other_fee')) or 0) - price * commission
    gross -= (price * commission + fba) * params.get('dst', 0)
    vat = params.get('vat', 0)
    gross -= price * vat / (1 + vat) if vat else 0
    margin = gross / price
    adjust = posture.get(str(row.get('posture') or '均衡').strip(), 0)
    target = max(params.get('acos_floor', 0.05), min(params.get('acos_cap', 0.6), margin - target_margin + adjust))
    fixed = landed + fba + (num(row.get('other_fee')) or 0) + fba * params.get('dst', 0)
    rate = 1 - commission * (1 + params.get('dst', 0)) - (vat / (1 + vat) if vat else 0)
    breakeven_price = fixed / rate if rate > 0 else None  # 毛利 = 0 的售价：低于它广告前就亏
    return {'landed_cost': round(landed, 4), 'gross_profit': round(gross, 4), 'break_even_acos': round(margin, 4),
            'breakeven_price': round(breakeven_price, 2) if breakeven_price else None,
            'target_acos': round(target, 4), 'commission': commission, 'target_margin': target_margin}


def load(table):
    wb = openpyxl.load_workbook(table, data_only=True, read_only=True)
    params, posture = read_params(wb['参数'])
    ws = wb['成本表']
    rows = list(ws.iter_rows(values_only=True))
    header = [str(h or '').replace('\n', '').strip() for h in rows[0]]
    out = []
    for values in rows[1:]:
        raw = {COLUMNS[h]: v for h, v in zip(header, values) if h in COLUMNS}
        if not raw.get('sku'):
            continue
        calc = compute(raw, params, posture)
        out.append({'sku': str(raw['sku']).strip(), 'asin': str(raw.get('asin') or '').strip().upper(), 'name': raw.get('name') or '',
                    'status': raw.get('status') or '', 'price': num(raw.get('price')), 'posture': raw.get('posture') or '均衡',
                    'units_30d': num(raw.get('units_30d')), 'complete': calc is not None, **(calc or {})})
    wb.close()
    return {'table': str(table), 'params': params, 'posture': posture, 'rows': out}


def load_marketplace(marketplace):
    table = registered().get(str(marketplace or '').upper())
    if isinstance(table, dict) and table.get('estimate_margin'):
        m = float(table['estimate_margin'])
        return {'table': f'估算：毛利率 {m:.0%}', 'estimate_margin': m, 'params': {}, 'posture': {}, 'rows': []}
    if not table or not Path(str(table)).is_file():
        return None
    return load(table)


def estimate_row(margin, floor=0.05, target_margin=0.10):
    """C 档：没有 SKU 成本时的统一估算口径。"""
    return {'break_even_acos': round(margin, 4), 'target_acos': round(max(floor, margin - target_margin), 4), 'source': '估算'}


# ---------- A 档模板 / B 档生成 ----------

HEADERS = ['店铺', 'SKU', 'ASIN', '名称', '状态', '近30天销量', 'FBA可售', '售价(站点币)', '采购成本(元)', '头程(元/件)',
           'FBA配送费(站点币)', '其他费用(站点币)', '佣金率', '目标利润率', '姿态', '备注', '落地成本(站点币)', '毛利(站点币)',
           '毛利率=盈亏ACOS', '目标ACOS']
YELLOW = ('H', 'I', 'J', 'K', 'L', 'M', 'N', 'O')
SITE_DEFAULTS = {'US': (7.1, 0, 0), 'UK': (9.1, 0.2, 0.02), 'DE': (7.7, 0.19, 0.03), 'FR': (7.7, 0.2, 0.03), 'IT': (7.7, 0.22, 0.03),
                 'ES': (7.7, 0.21, 0.03), 'JP': (0.048, 0.1, 0), 'CA': (5.2, 0, 0.03)}


def formulas(r):
    return {'Q': f'=IF(I{r}="","",(I{r}+J{r})/参数!$B$2)',
            'R': f'=IF(Q{r}="","",H{r}-Q{r}-K{r}-N(L{r})-H{r}*M{r}-(H{r}*M{r}+K{r})*参数!$B$8-H{r}*参数!$B$7/(1+参数!$B$7))',
            'S': f'=IF(R{r}="","",R{r}/H{r})',
            'T': f'=IF(S{r}="","",MAX(参数!$B$5,MIN(参数!$B$6,S{r}-N{r}+IFERROR(VLOOKUP(O{r},参数!$D$2:$E$6,2,FALSE),0))))'}


def new_workbook(marketplace='US', rows=300):
    from openpyxl.styles import PatternFill
    fx, vat, dst = SITE_DEFAULTS.get(marketplace.upper(), (7.1, 0, 0))
    cur = {'US': 'USD', 'UK': 'GBP', 'JP': 'JPY', 'CA': 'CAD'}.get(marketplace.upper(), 'EUR')
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '成本表'
    ws.append(HEADERS)
    yellow, green = PatternFill('solid', fgColor='FFF2CC'), PatternFill('solid', fgColor='E2EFDA')
    for r in range(2, rows + 2):
        for col, f in formulas(r).items():
            ws[f'{col}{r}'] = f
            ws[f'{col}{r}'].fill = green
        for col in YELLOW:
            ws[f'{col}{r}'].fill = yellow
    ps = wb.create_sheet('参数')
    for row in (['参数', '值', '', '姿态', '目标ACOS调整'], [f'汇率（1 {cur} = ? 元）', fx, None, '保守', -0.03],
                ['默认佣金率', 0.15, None, '均衡', 0], ['默认目标利润率', 0.1, None, '进取', 0.05],
                ['目标ACOS下限', 0.05, None, '新品', 0.1], ['目标ACOS上限', 0.6, None, '清仓', 0.15],
                ['VAT率（含税售价里要交出去的税，美国填 0）', vat], ['数字服务费率（按 佣金+FBA 计，没有填 0）', dst]):
        ps.append(row)
    ns = wb.create_sheet('说明')
    for line in ('怎么填（只填黄色列，绿色列自动算）：',
                 '1. 采购成本(元)、头程(元/件)：人民币，按单件算。头程 = 这批货的头程总运费 ÷ 件数。',
                 '2. FBA配送费(站点币)：后台费用预览里这个 SKU 的 FBA 配送费。',
                 '3. 其他费用(站点币)：可选。包装、月仓储摊销、退货损耗等，没有就空着。',
                 '4. 佣金率：默认 15%，类目不同的自己改。目标利润率：广告后想留下的净利，默认 10%。',
                 '5. 姿态：均衡（默认）/ 保守（目标 ACOS 再收 3 个点）/ 进取（放 5 个点）/ 新品（放 10 个点，允许亏钱推量）/ 清仓（放 15 个点）。',
                 '6. 参数页：汇率每月核一次；英国 / 欧洲站的 VAT、数字服务费已按常见税率预填，以你的实际为准。',
                 '7. 毛利率 = 盈亏 ACOS（广告花到这里刚好不赚不亏）；目标 ACOS = 毛利率 − 目标利润率 + 姿态调整。'):
        ns.append([line])
    return wb


def profit_prefill(lx, cfg, token, sid, days=30):
    """领星利润报表（按 MSKU 汇总）近 30 天：每件 FBA 配送费、实际佣金比例；领星里维护过采购价 / 头程的还有人民币单价。
    结算口径，截止到前天；没开财务权限或没数据就返回空，不影响其他预填。"""
    from datetime import date, timedelta
    end = date.today() - timedelta(days=2)
    base = {'sids': [int(sid)], 'startDate': (end - timedelta(days=days - 1)).isoformat(), 'endDate': end.isoformat(),
            'summaryEnabled': True, 'length': 1000}

    def records(extra):
        out, offset = [], 0
        while True:
            try:
                resp = lx.call(cfg, token, '/bd/profit/report/open/report/msku/list', dict(base, offset=offset, **extra))
            except Exception:  # noqa: BLE001
                return out
            data = resp.get('data')
            page = (data.get('records') if isinstance(data, dict) else data) or []
            if not isinstance(page, list):
                return out
            out += [x for x in page if isinstance(x, dict)]
            if len(page) < 1000:
                return out
            offset += 1000

    result = {}
    for x in records({}):
        qty, fba_qty = _num(x.get('totalSalesQuantity')) or 0, _num(x.get('fbaSalesQuantity')) or _num(x.get('totalSalesQuantity')) or 0
        amount, entry = _num(x.get('totalSalesAmount')) or 0, {}
        if fba_qty > 0 and _num(x.get('fbaDeliveryFee')):
            entry['fba_fee'] = round(abs(_num(x['fbaDeliveryFee'])) / fba_qty, 2)
        if amount > 0 and _num(x.get('platformFee')):
            rate = abs(_num(x['platformFee'])) / amount
            if 0.03 <= rate <= 0.45:  # 含促销 / 退款时会失真，超出常见佣金区间就不填
                entry['commission'] = round(rate, 3)
        if qty > 0 and entry:
            result[x.get('msku')] = entry
    for x in records({'currencyCode': 'CNY'}):  # 采购价 / 头程在领星里是人民币维护的，用人民币口径取
        entry = result.setdefault(x.get('msku'), {})
        if _num(x.get('cgUnitPrice')):
            entry['cg_cny'] = round(_num(x['cgUnitPrice']), 2)
        if _num(x.get('cgTransportUnitCosts')):
            entry['freight_cny'] = round(_num(x['cgTransportUnitCosts']), 2)
        if not entry:
            result.pop(x.get('msku'), None)
    return result


def generate(marketplace, dest=None, into_existing=False):
    """B 档：从领星预填。已有表只补空的黄色格、追加新 SKU，不覆盖你填过的值。"""
    import importlib.util
    here = Path(__file__).resolve()
    spec = importlib.util.spec_from_file_location('ct_lingxing', here.parents[2] / 'lingxing-erp-connector' / 'scripts' / 'lingxing_client.py')
    lx = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lx)
    user_cfg = config_path().parent / 'lingxing_config.yaml'
    if user_cfg.is_file():
        lx.CONFIG = user_cfg
    mp = marketplace.upper()
    existing = Path(registered().get(mp)) if isinstance(registered().get(mp), str) and Path(registered()[mp]).is_file() else None
    if existing and not into_existing:
        return {'status': 'exists', 'table': str(existing),
                'note': '你已经登记了自己的成本表，没有动它。要用领星把新上架的 SKU 和空格补进这张表，说「用领星补成本表」（--into-existing）'}
    store = next((s for s in lx.patrol_stores(mp)), None) or next((s for s in (lx.load_stores() or []) if str(s.get('marketplace')).upper() == mp), None)
    if not store:
        return {'status': 'blocked', 'error': f'领星配置里没有 {mp} 店铺：先「接入领星」'}
    cfg = lx.load_config()
    token = lx.get_token(cfg)
    listing, offset = [], 0
    while True:
        resp = lx.call(cfg, token, '/erp/sc/data/mws/listing', {'sid': store['sid'], 'offset': offset, 'length': 1000})
        page = (resp.get('data') if isinstance(resp.get('data'), list) else (resp.get('data') or {}).get('list')) or []
        listing += page
        if len(page) < 1000:
            break
        offset += 1000
    live = [r for r in listing if str(r.get('status')) == '1' and str(r.get('is_delete')) == '0']
    fba = {}
    for i in range(0, len(live), 500):
        try:
            resp = lx.call(cfg, token, '/listing/listing/open/api/listing/getPrices',
                           {'data': [{'sid': int(store['sid']), 'msku': r.get('seller_sku')} for r in live[i:i + 500]]})
        except Exception:  # noqa: BLE001 — 这个接口不少账号没开（404），改用利润报表
            break
        if not isinstance(resp.get('data'), list):
            break
        for x in resp['data']:
            if isinstance(x, dict):
                fba[x.get('msku')] = x.get('fba_fee') or x.get('fba_fee_report')
    profit = profit_prefill(lx, cfg, token, store['sid'])
    local = {}
    skus = sorted({r.get('local_sku') for r in live if r.get('local_sku')})
    for i in range(0, len(skus), 100):
        try:
            resp = lx.call(cfg, token, '/erp/sc/routing/data/local_inventory/batchGetProductInfo', {'skus': skus[i:i + 100]})
        except Exception:  # noqa: BLE001 — 没开本地产品权限就只预填售价和 FBA 费
            break
        for p in resp.get('data') or []:
            freight = None
            for rel in p.get('product_logistics_relation') or []:
                if str(rel.get(f'{mp}_currency') or 'CNY').upper() in ('CNY', 'RMB', ''):
                    freight = rel.get(f'{mp}_cg_transport_costs')
            local[p.get('sku')] = {'cg_price': p.get('cg_price'), 'freight': freight}
    dest = Path(dest) if dest else Path(os.environ.get('STARSKY_OPS_ROOT') or config_path().parent.parent / 'ops_workspace') / '成本表' / f'成本表_{mp}.xlsx'
    target = existing or dest
    if target.is_file():
        wb = openpyxl.load_workbook(target)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        wb = new_workbook(mp)
    ws = wb['成本表']
    header = [str(c.value or '').replace('\n', '').strip() for c in ws[1]]
    col = {h: i + 1 for i, h in enumerate(header)}
    rows_by_sku = {str(ws.cell(r, col['SKU']).value or '').strip(): r for r in range(2, ws.max_row + 1) if ws.cell(r, col['SKU']).value}
    next_row = max([1] + list(rows_by_sku.values())) + 1
    filled, added, blanks = 0, 0, []
    for r in live:
        sku = str(r.get('seller_sku') or '').strip()
        row = rows_by_sku.get(sku)
        if row is None:
            row = next_row
            next_row += 1
            added += 1
            for c, f in formulas(row).items():
                if ws[f'{c}{row}'].value in (None, ''):
                    ws[f'{c}{row}'] = f
        loc = local.get(r.get('local_sku')) or {}
        pf = profit.get(sku) or {}
        values = {'店铺': store.get('nickname'), 'SKU': sku, 'ASIN': r.get('asin'), '名称': (r.get('local_name') or r.get('item_name') or '')[:60],
                  '状态': '在售', 'FBA可售': r.get('afn_fulfillable_quantity'), '售价(站点币)': _num(r.get('listing_price') or r.get('price')),
                  '采购成本(元)': _num(loc.get('cg_price')) or pf.get('cg_cny'), '头程(元/件)': _num(loc.get('freight')) or pf.get('freight_cny'),
                  'FBA配送费(站点币)': _num(fba.get(sku)) or pf.get('fba_fee'), '近30天销量': r.get('thirty_volume'),
                  '佣金率': pf.get('commission') or 0.15, '目标利润率': 0.10, '姿态': '均衡'}
        for name, value in values.items():
            if name in col and value not in (None, '') and ws.cell(row, col[name]).value in (None, ''):
                ws.cell(row, col[name]).value = value
                filled += 1
        need = [n for n in ('售价(站点币)', '采购成本(元)', '头程(元/件)', 'FBA配送费(站点币)') if n in col and ws.cell(row, col[n]).value in (None, '')]
        if need:
            blanks.append({'sku': sku, 'asin': r.get('asin'), '待补': need})
    wb.save(target)
    register(mp, target)
    return {'status': 'ok', 'table': str(target), 'skus': len(live), 'added_rows': added, 'cells_prefilled': filled,
            'profit_report_skus': len(profit),
            'need_you': blanks, 'next': '打开表只补「待补」里的黄色格，保存后下一轮诊断自动用' if blanks else '全部预填好了，核对一眼数字即可'}


def _num(v):
    try:
        return None if v in (None, '') else round(float(v), 4)
    except (TypeError, ValueError):
        return None


def index(cost):
    """SKU / ASIN → 成本行（只收算得出来的；同一 ASIN 多个 SKU 时取在售、近 30 天卖得多的）。"""
    by_sku, by_asin = {}, {}
    rows = sorted((r for r in (cost or {}).get('rows', []) if r['complete']),
                  key=lambda r: (r['status'] != '在售', -(r.get('units_30d') or 0)))
    for r in rows:
        by_sku.setdefault(r['sku'], r)
        if r['asin']:
            by_asin.setdefault(r['asin'], r)
    return by_sku, by_asin


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--set', nargs=2, metavar=('站点', '表路径'))
    ap.add_argument('--show', metavar='站点')
    ap.add_argument('--template', metavar='保存路径')
    ap.add_argument('--generate', metavar='站点')
    ap.add_argument('--into-existing', action='store_true', help='补进已登记的那张表（只填空格、追加新 SKU）')
    ap.add_argument('--estimate', nargs=2, metavar=('站点', '毛利率'))
    ap.add_argument('--site', default='US', help='--template 用哪个站点的默认参数')
    args = ap.parse_args(argv)
    if args.template:
        path = Path(args.template).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        new_workbook(args.site).save(path)
        print(json.dumps({'status': 'ok', 'template': str(path), 'next': f'填好黄色列后：--set {args.site} "{path}"'}, ensure_ascii=False))
        return 0
    if args.generate:
        out = generate(args.generate, into_existing=args.into_existing)
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0 if out.get('status') in ('ok', 'exists') else 2
    if args.estimate:
        mp, m = args.estimate[0].upper(), float(args.estimate[1].rstrip('%')) / (100 if args.estimate[1].endswith('%') or float(args.estimate[1].rstrip('%')) > 1 else 1)
        doc = registered()
        doc[mp] = {'estimate_margin': round(m, 4)}
        config_path().parent.mkdir(parents=True, exist_ok=True)
        config_path().write_text(yaml.safe_dump(doc, allow_unicode=True), encoding='utf-8')
        print(json.dumps({'status': 'ok', 'marketplace': mp, 'estimate_margin': m, **estimate_row(m),
                          'note': '报告和日报会标「估算」；以后「生成成本表」或「登记成本表」就自动换成真实成本'}, ensure_ascii=False))
        return 0
    if args.set:
        path = register(*args.set)
        print(json.dumps({'status': 'ok', 'config': str(path), 'registered': registered()}, ensure_ascii=False))
        return 0
    if args.show:
        cost = load_marketplace(args.show)
        if not cost:
            print(json.dumps({'status': 'missing', 'error': f'{args.show} 还没登记成本表：--set {args.show} <表路径>'}, ensure_ascii=False))
            return 2
        done = [r for r in cost['rows'] if r['complete']]
        print(json.dumps({'status': 'ok', 'table': cost['table'], 'params': cost['params'], 'complete': len(done),
                          'incomplete': [r['sku'] for r in cost['rows'] if not r['complete'] and r['status'] == '在售'],
                          'rows': [{k: r.get(k) for k in ('sku', 'asin', 'status', 'price', 'gross_profit', 'break_even_acos', 'target_acos')}
                                   for r in done]}, ensure_ascii=False, indent=1))
        return 0
    ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
