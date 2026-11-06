# -*- coding: utf-8 -*-
"""领星广告健康周检报告生成器（模块 D 数据后端，2026-07-06 首版）
拉 lingxing_config.yaml 里 patrol=true 店铺的广告活动（本周 vs 上周两个窗口）->
红线分层：烧钱无单 / ACOS超线 / 预算耗尽·投放异常 / 花费异动 / 关注 -> md + html 报告。

用法（1688 MCP 的 venv 依赖已齐）：
  "...\\1688-Scraper-MCP\\.venv\\Scripts\\python.exe" scripts/ads_health_check.py

复用同目录 lingxing_client 的 token/签名/调用。ACOS 一律用 cost/sales_amount 自算，不信接口字符串。
"""
import html
import importlib.util
import json
import time
from datetime import date, timedelta
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("lingxing_client", _HERE / "lingxing_client.py")
client = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(client)

ADS_PATH = "/erp/sc/data/ads/campaigns"   # ✅ 2026-07-06 实测
TODAY = date.today()
# 本周窗口 = 截止昨天的 7 天；上周 = 再往前 7 天
W_END = TODAY - timedelta(days=1)
W_START = W_END - timedelta(days=6)
P_END = W_START - timedelta(days=1)
P_START = P_END - timedelta(days=6)

REPORT_DIR = client.report_root() / "广告健康" / TODAY.isoformat()
RAW_DIR = client.report_root() / "lingxing" / TODAY.isoformat()

ACOS_RED = 0.35      # ACOS 红线
ACOS_YELLOW = 0.25   # ACOS 关注线
MIN_SPEND = 5.0      # 低于此花费不触发 ACOS/无单红线(噪音)
SPIKE = 0.5          # 花费环比 ±50% 视为异动
BAD_STATUS = ("OUT_OF_BUDGET", "SUSPENDED", "ENDED", "INCOMPLETE", "MISSING_")


def fnum(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def fetch_window(cfg, token, sid, start, end):
    """拉一个日期窗口内全部活动(带指标)。接口返回全部历史活动,窗口只影响指标聚合。
    ⚠️ 领星 end_date 为开区间(不含尾日)，API 传参 end+1 才能覆盖到 end 当天(2026-07-07 实测)。"""
    rows, offset, length = [], 0, 1000
    while True:
        data = client.call(cfg, token, ADS_PATH,
                           {"sid": sid, "start_date": start.isoformat(),
                            "end_date": (end + timedelta(days=1)).isoformat(),
                            "offset": offset, "length": length}, method="POST")
        if str(data.get("code")) not in ("0", "200"):
            raise SystemExit(f"sid={sid} 广告拉取失败: {data.get('message')}")
        d = data.get("data")
        lst = d.get("list") if isinstance(d, dict) else d
        lst = lst or []
        rows += lst
        total = (d.get("total") if isinstance(d, dict) else data.get("total")) or 0
        offset += len(lst)
        if not lst or offset >= int(total):
            break
        time.sleep(0.6)   # 限流保护(令牌桶)
    return rows


def metrics(r):
    cost = fnum(r.get("cost"))
    sales = fnum(r.get("sales_amount"))
    orders = int(fnum(r.get("order_num")))
    clicks = int(fnum(r.get("clicks")))
    impr = int(fnum(r.get("impressions")))
    acos = (cost / sales) if sales > 0 else None
    return cost, sales, orders, clicks, impr, acos


def classify(r, prev_cost):
    cost, sales, orders, clicks, impr, acos = metrics(r)
    state = (r.get("state") or "").lower()
    serving = (r.get("serving_status") or "").upper()
    flags = []
    if state == "enabled" and any(k in serving for k in BAD_STATUS):
        flags.append("status")
    if cost >= MIN_SPEND and orders == 0:
        return ("watch" if clicks < 10 else "burn"), flags
    if cost >= MIN_SPEND and acos is not None and acos > ACOS_RED:
        return "acos", flags
    if flags:
        return "status", flags
    if prev_cost is not None and max(cost, prev_cost) >= 10:
        chg = (cost - prev_cost) / prev_cost if prev_cost > 0 else 1.0
        if abs(chg) > SPIKE:
            return "spike", flags
    if cost >= MIN_SPEND and acos is not None and acos > ACOS_YELLOW:
        return "yellow", flags
    if cost > 0 or clicks > 0:
        return "green", flags
    return "idle", flags


def pct(v):
    return f"{v*100:.1f}%" if v is not None else "—"


def chg_txt(cur, prev):
    if prev is None:
        return "基线"
    if prev == 0:
        return "新增" if cur > 0 else "—"
    c = (cur - prev) / prev
    arrow = "↑" if c > 0 else ("↓" if c < 0 else "")
    return f"{arrow}{abs(c)*100:.0f}%"


MD_HDR = ("| 店铺 | 广告活动 | 类型 | 状态 | 花费 | 销售额 | ACOS | 点击 | 订单 | 花费环比 |\n"
          "|:--|:--|:--|:--|--:|--:|--:|--:|--:|:--|")


def md_row(r):
    cost, sales, orders, clicks, impr, acos = metrics(r)
    cur = r.get("currency_code") or ""
    return (f"| {r['_nick']} | {(r.get('campaign_name') or '—')[:34]} | {r.get('ad_format') or '—'} | "
            f"{r.get('state')}/{(r.get('serving_status') or '—')[:22]} | {cost:.2f} {cur} | {sales:.2f} | "
            f"{pct(acos)} | {clicks} | {orders} | {chg_txt(cost, r.get('_prev_cost'))} |")


def md_section(title, rows, note=""):
    L = [f"## {title}", ""]
    if note:
        L += [note, ""]
    if rows:
        L += [MD_HDR] + [md_row(r) for r in rows]
    else:
        L.append("_无。_")
    L.append("")
    return L


CSS = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>广告健康周检 {today}</title>
<style>
:root{{--red:#e5484d;--amber:#f0a020;--gray:#8a94a6;--green:#30a46c;--ink:#1c2333;--line:#e7e9ee;--blue:#4a7bd6;}}
*{{box-sizing:border-box;}}body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;
background:#f4f5f7;color:var(--ink);margin:0;padding:28px 16px;line-height:1.55;}}
.wrap{{max-width:1180px;margin:0 auto;}}h1{{font-size:24px;margin:0 0 4px;}}
.meta{{color:#6b7280;font-size:13px;margin-bottom:18px;}}.meta b{{color:var(--ink);}}
.cards{{display:flex;gap:12px;flex-wrap:wrap;margin:18px 0 8px;}}
.card{{flex:1 1 128px;background:#fff;border-radius:12px;padding:15px 12px;text-align:center;
box-shadow:0 1px 3px rgba(20,30,60,.08);border-top:3px solid var(--line);}}
.card .n{{font-size:26px;font-weight:750;line-height:1.15;}}.card .t{{font-size:12px;color:#6b7280;margin-top:4px;}}
.card.red{{border-top-color:var(--red);}}.card.red .n{{color:var(--red);}}
.card.amber{{border-top-color:var(--amber);}}.card.amber .n{{color:var(--amber);}}
.card.blue{{border-top-color:var(--blue);}}.card.blue .n{{color:var(--blue);}}
.card.green{{border-top-color:var(--green);}}.card.green .n{{color:var(--green);}}
.summary{{background:#fff8e6;border:1px solid #f5e2a8;border-left:4px solid var(--amber);
padding:14px 18px;border-radius:8px;margin:16px 0;font-size:14.5px;}}
section{{background:#fff;border-radius:12px;padding:18px 20px;margin:16px 0;box-shadow:0 1px 3px rgba(20,30,60,.06);}}
section h2{{font-size:17px;margin:0 0 12px;}}
table{{width:100%;border-collapse:collapse;font-size:13px;}}
th{{background:#2c3550;color:#fff;padding:9px 10px;text-align:left;font-weight:600;white-space:nowrap;}}
th.r,td.r{{text-align:right;}}td{{padding:8px 10px;border-top:1px solid var(--line);}}
tbody tr:nth-child(even){{background:#fafbfc;}}tbody tr:hover{{background:#f0f4ff;}}
.pill{{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;font-weight:600;}}
.pill.bad{{background:#fdecec;color:var(--red);}}.pill.dim{{background:#eef0f3;color:#6b7280;}}
.hi{{color:var(--red);font-weight:700;}}details summary{{cursor:pointer;font-size:17px;font-weight:600;margin-bottom:8px;}}
.empty{{color:#9aa1ac;font-style:italic;}}footer{{color:#8a94a6;font-size:12px;margin-top:20px;line-height:1.7;}}
</style></head><body><div class="wrap">"""


def _e(v):
    return html.escape(str(v)) if v not in (None, "") else "—"


def html_row(r):
    cost, sales, orders, clicks, impr, acos = metrics(r)
    acos_cls = ' class="r hi"' if (acos is not None and acos > ACOS_RED) else ' class="r"'
    serving = r.get("serving_status") or "—"
    pill = "bad" if any(k in serving.upper() for k in BAD_STATUS) else "dim"
    return ("<tr>"
            f"<td>{_e(r['_nick'])}</td><td>{_e((r.get('campaign_name') or '')[:40])}</td>"
            f"<td>{_e(r.get('ad_format'))}</td>"
            f"<td>{_e(r.get('state'))} <span class='pill {pill}'>{_e(serving[:24])}</span></td>"
            f"<td class='r'>{cost:.2f}</td><td class='r'>{sales:.2f}</td>"
            f"<td{acos_cls}>{pct(acos)}</td><td class='r'>{clicks}</td><td class='r'>{orders}</td>"
            f"<td>{_e(chg_txt(cost, r.get('_prev_cost')))}</td></tr>")


def html_table(rows):
    if not rows:
        return '<p class="empty">无。</p>'
    head = ("<table><thead><tr><th>店铺</th><th>广告活动</th><th>类型</th><th>状态</th>"
            "<th class='r'>花费</th><th class='r'>销售额</th><th class='r'>ACOS</th>"
            "<th class='r'>点击</th><th class='r'>订单</th><th>花费环比</th></tr></thead><tbody>")
    return head + "".join(html_row(r) for r in rows) + "</tbody></table>"


def main():
    cfg = client.load_config()
    token = client.get_token(cfg)
    stores = [s for s in client.load_stores() if s.get("patrol", True)]
    if not stores:
        raise SystemExit("lingxing_config.yaml 的 stores 为空")

    all_rows, store_sum = [], {}
    for s in stores:
        nick = s.get("nickname") or s["sid"]
        cur_rows = fetch_window(cfg, token, s["sid"], W_START, W_END)
        time.sleep(0.6)
        prev_rows = fetch_window(cfg, token, s["sid"], P_START, P_END)
        prev_cost = {r.get("campaign_id"): fnum(r.get("cost")) for r in prev_rows}
        tc = ts = 0.0
        pc = sum(prev_cost.values())
        n_active = 0
        for r in cur_rows:
            r["_nick"] = nick
            r["_prev_cost"] = prev_cost.get(r.get("campaign_id"))
            cost, sales, orders, clicks, impr, acos = metrics(r)
            tc += cost
            ts += sales
            if (r.get("state") or "").lower() == "enabled":
                n_active += 1
        store_sum[nick] = {"cost": tc, "sales": ts, "prev_cost": pc, "active": n_active,
                           "acos": (tc / ts) if ts > 0 else None,
                           "currency": next((r.get("currency_code") for r in cur_rows if r.get("currency_code")), "")}
        all_rows += cur_rows

    b = {k: [] for k in ("burn", "acos", "status", "spike", "yellow", "green", "idle")}
    for r in all_rows:
        cat, _ = classify(r, r.get("_prev_cost"))
        b[cat].append(r)
    for k in ("burn", "acos", "yellow", "green"):
        b[k].sort(key=lambda r: -fnum(r.get("cost")))
    b["spike"].sort(key=lambda r: -abs(fnum(r.get("cost")) - (r.get("_prev_cost") or 0)))

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_DIR / "ads_campaigns_raw.json").write_text(
        json.dumps(all_rows, ensure_ascii=False, indent=1), encoding="utf-8")

    nb, na, ns, nk, ny = len(b["burn"]), len(b["acos"]), len(b["status"]), len(b["spike"]), len(b["yellow"])
    win_txt = f"{W_START} ~ {W_END}(上周 {P_START} ~ {P_END} 对比)"
    sum_txt = "、".join(
        f"{k} 花费 {v['cost']:.2f}{v['currency']}(环比{chg_txt(v['cost'], v['prev_cost'])}) "
        f"销售 {v['sales']:.2f} 综合ACOS {pct(v['acos'])} 在投{v['active']}个"
        for k, v in store_sum.items())

    L = [
        "# 广告健康周检报告(领星数据)", "",
        f"- **生成时间**:{TODAY} · **统计窗口**:{win_txt}",
        "- **数据来源**:领星 ERP OpenAPI 广告接口(官方后台数据,防关联安全);ACOS=花费/销售额自算",
        f"- **店铺汇总**:{sum_txt}", "",
        "## 一句话结论", "",
        f"🔴 烧钱无单 {nb} 个;🔴 ACOS>{int(ACOS_RED*100)}% {na} 个;🟠 预算耗尽/投放异常 {ns} 个;"
        f"🔵 花费异动±{int(SPIKE*100)}% {nk} 个;🟡 ACOS {int(ACOS_YELLOW*100)}-{int(ACOS_RED*100)}% 关注 {ny} 个。", "",
    ]
    L += md_section(f"🔴 烧钱无单({nb})—— 本周花费≥{MIN_SPEND:.0f} 且 0 订单,优先处理", b["burn"])
    L += md_section(f"🔴 ACOS 超线({na})—— ACOS>{int(ACOS_RED*100)}% 且花费≥{MIN_SPEND:.0f}", b["acos"])
    L += md_section(f"🟠 预算耗尽/投放异常({ns})—— enabled 但 serving_status 异常", b["status"])
    L += md_section(f"🔵 花费异动({nk})—— 环比变化超±{int(SPIKE*100)}%", b["spike"])
    L += md_section(f"🟡 ACOS 关注({ny})—— {int(ACOS_YELLOW*100)}-{int(ACOS_RED*100)}%", b["yellow"])
    L += [f"## 🟢 正常在投:{len(b['green'])} 个 · 零花费闲置:{len(b['idle'])} 个(略)", "",
          "---",
          f"> 口径:窗口内花费≥{MIN_SPEND:.0f} 才触发红线(防噪);ACOS 用 cost/sales_amount 自算;"
          "深挖某活动:用 lingxing_client 拉 ads/adGroups → ads/keywords → ads/queryWords 四层定位。",
          f"> 原始数据:`{RAW_DIR}\\ads_campaigns_raw.json`"]

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "广告健康周检.md").write_text("\n".join(L), encoding="utf-8")

    H = [CSS.format(today=TODAY)]
    H.append("<h1>广告健康周检报告</h1>")
    H.append(f'<div class="meta"><b>统计窗口</b> {win_txt} · <b>数据来源</b> 领星 ERP 广告接口<br>'
             f'<b>店铺汇总</b> {html.escape(sum_txt)}</div>')
    H.append('<div class="cards">'
             f'<div class="card red"><div class="n">{nb}</div><div class="t">🔴 烧钱无单</div></div>'
             f'<div class="card red"><div class="n">{na}</div><div class="t">🔴 ACOS超{int(ACOS_RED*100)}%</div></div>'
             f'<div class="card amber"><div class="n">{ns}</div><div class="t">🟠 投放异常</div></div>'
             f'<div class="card blue"><div class="n">{nk}</div><div class="t">🔵 花费异动</div></div>'
             f'<div class="card amber"><div class="n">{ny}</div><div class="t">🟡 ACOS关注</div></div>'
             f'<div class="card green"><div class="n">{len(b["green"])}</div><div class="t">🟢 正常在投</div></div></div>')
    H.append(f'<div class="summary">优先级:🔴 烧钱无单(直接止血)→ 🔴 ACOS 超线(降价/否词/关词)→ '
             f'🟠 投放异常(预算耗尽=错失流量)→ 🔵 异动核对。深挖用 adGroups→keywords→queryWords 四层定位。</div>')
    for title, key in ((f"🔴 烧钱无单({nb})", "burn"), (f"🔴 ACOS 超线({na})", "acos"),
                       (f"🟠 预算耗尽/投放异常({ns})", "status"), (f"🔵 花费异动({nk})", "spike"),
                       (f"🟡 ACOS 关注({ny})", "yellow")):
        H.append(f"<section><h2>{title}</h2>{html_table(b[key])}</section>")
    H.append(f'<section><details><summary>🟢 正常在投({len(b["green"])})—— 点击展开</summary>'
             f'{html_table(b["green"])}</details></section>')
    H.append(f"<footer>零花费闲置活动 {len(b['idle'])} 个未列出。窗口内花费≥{MIN_SPEND:.0f} 才触发红线;"
             f"ACOS=花费/销售额自算。原始数据:{html.escape(str(RAW_DIR))}\\ads_campaigns_raw.json</footer>")
    H.append("</div></body></html>")
    (REPORT_DIR / "广告健康周检.html").write_text("\n".join(H), encoding="utf-8")

    print(f"[OK] HTML 报告: {REPORT_DIR / '广告健康周检.html'}")
    print(f"[OK] MD   报告: {REPORT_DIR / '广告健康周检.md'}")
    print(f"     活动总数 {len(all_rows)} | 烧钱无单 {nb} | ACOS超线 {na} | 投放异常 {ns} | 异动 {nk} | 关注 {ny} | 正常 {len(b['green'])} | 闲置 {len(b['idle'])}")
    for k, v in store_sum.items():
        print(f"     {k}: 花费 {v['cost']:.2f}{v['currency']} (上周 {v['prev_cost']:.2f}) 销售 {v['sales']:.2f} 综合ACOS {pct(v['acos'])} 在投 {v['active']}")


if __name__ == "__main__":
    main()
