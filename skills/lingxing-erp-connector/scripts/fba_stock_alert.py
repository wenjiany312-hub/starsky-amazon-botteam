# -*- coding: utf-8 -*-
"""领星 FBA 库存断货预警报告生成器
拉 lingxing_config.yaml 里 patrol=true 的所有店铺的 FBA 库存 -> 分层：
低库存预警 / 零库存待确认 / 补货在途 / 关注 / 滞销过剩 -> 生成 md 报告 + 原始数据落盘。

用法（1688 MCP 的 venv 依赖已齐）：
  "...\\1688-Scraper-MCP\\.venv\\Scripts\\python.exe" scripts/fba_stock_alert.py

复用同目录 lingxing_client 的 token/签名/调用，不重复实现。
"""
import html
import importlib.util
import json
from datetime import date
from pathlib import Path

# 加载同目录 lingxing_client（凭据/签名/调用封装）
_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("lingxing_client", _HERE / "lingxing_client.py")
client = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(client)
_fspec = importlib.util.spec_from_file_location("findings", _HERE / "findings.py")
F = importlib.util.module_from_spec(_fspec)
_fspec.loader.exec_module(F)

FBA_PATH = "/erp/sc/routing/fba/fbaStock/fbaList"   # ✅ 2026-07-06 实测
TODAY = date.today().isoformat()
REPORT_DIR = client.report_root() / "断货预警" / TODAY
RAW_DIR = client.report_root() / "lingxing" / TODAY


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def inbound_of(r):
    return (r.get("afn_inbound_shipped_quantity") or 0) + (r.get("afn_inbound_receiving_quantity") or 0) \
        + (r.get("afn_inbound_working_quantity") or 0)


def fetch_all(cfg, token, sid):
    rows, offset = [], 0
    while True:
        data = client.call(cfg, token, FBA_PATH,
                           {"sid": sid, "offset": offset, "length": 200}, method="POST")
        if str(data.get("code")) not in ("0", "200"):
            raise SystemExit(f"sid={sid} 拉取失败: {data.get('message')}")
        d = data.get("data") or {}
        lst = d.get("list") or []
        rows += lst
        total = d.get("total") or 0
        offset += len(lst)
        if not lst or offset >= total:
            break
    return rows


def classify(r):
    dos = fnum(r.get("historical_days_of_supply"))
    dos_s = fnum(r.get("short_term_historical_days_of_supply"))
    use_dos = dos_s if dos_s is not None else dos
    health = r.get("fba_inventory_level_health_status") or ""
    avail = r.get("afn_fulfillable_quantity") or 0
    inbound = inbound_of(r)
    excess = fnum(r.get("estimated_excess_quantity")) or 0
    age365 = r.get("inv_age_365_plus_days") or 0

    if avail > 0 and (any(k in health for k in ("滞销", "冗余", "过量", "过剩")) or excess > 0
                      or (age365 > 0 and (use_dos is None or use_dos > 180))):
        return "dead"
    if any(k in health for k in ("低库存", "短缺", "断货")):
        return "red"
    if avail <= 0:
        return "inbound" if inbound > 0 else "zero"
    if use_dos is not None and use_dos < 14:
        return "red"
    if use_dos is not None and use_dos < 30:
        return "yellow"
    return "green"


def dos_key(r):
    return fnum(r.get("short_term_historical_days_of_supply")) or fnum(r.get("historical_days_of_supply")) or 0


HDR = ("| 店铺 | ASIN | SKU | 品名 | 可售 | 可售天数 | 在途 | 领星健康 | 领星建议 |\n"
       "|:--|:--|:--|:--|--:|--:|--:|:--|:--|")


def row_line(r):
    dos = r.get("short_term_historical_days_of_supply") or r.get("historical_days_of_supply") or "—"
    return (f"| {r.get('_nick')} | {r.get('asin')} | {r.get('sku') or r.get('msku') or '—'} | "
            f"{(r.get('product_name') or '—')[:20]} | {r.get('afn_fulfillable_quantity') or 0} | "
            f"{dos} | {inbound_of(r)} | {r.get('fba_inventory_level_health_status') or '—'} | "
            f"{r.get('recommended_action') or '—'} |")


def section(title, rows, note=""):
    L = [f"## {title}", ""]
    if note:
        L += [note, ""]
    if rows:
        L.append(HDR)
        L += [row_line(r) for r in rows]
    else:
        L.append("_无。_")
    L.append("")
    return L


HTML_HEAD = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FBA 断货预警报告 {today}</title>
<style>
:root{{--red:#e5484d;--amber:#f0a020;--gray:#8a94a6;--green:#30a46c;--ink:#1c2333;--line:#e7e9ee;}}
*{{box-sizing:border-box;}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;
background:#f4f5f7;color:var(--ink);margin:0;padding:28px 16px;line-height:1.55;}}
.wrap{{max-width:1120px;margin:0 auto;}}
h1{{font-size:24px;margin:0 0 4px;}}
.meta{{color:#6b7280;font-size:13px;margin-bottom:20px;}}
.meta b{{color:var(--ink);}}
.cards{{display:flex;gap:12px;flex-wrap:wrap;margin:18px 0 8px;}}
.card{{flex:1 1 130px;background:#fff;border-radius:12px;padding:16px 14px;text-align:center;
box-shadow:0 1px 3px rgba(20,30,60,.08);border-top:3px solid var(--line);}}
.card .n{{font-size:30px;font-weight:750;line-height:1.1;}}
.card .t{{font-size:12px;color:#6b7280;margin-top:4px;}}
.card.red{{border-top-color:var(--red);}} .card.red .n{{color:var(--red);}}
.card.truck{{border-top-color:#4a7bd6;}} .card.truck .n{{color:#4a7bd6;}}
.card.zero{{border-top-color:var(--gray);}} .card.zero .n{{color:var(--gray);}}
.card.amber{{border-top-color:var(--amber);}} .card.amber .n{{color:var(--amber);}}
.card.dead{{border-top-color:#5b6472;}} .card.dead .n{{color:#5b6472;}}
.card.green{{border-top-color:var(--green);}} .card.green .n{{color:var(--green);}}
.summary{{background:#fff8e6;border:1px solid #f5e2a8;border-left:4px solid var(--amber);
padding:14px 18px;border-radius:8px;margin:18px 0;font-size:14.5px;}}
section{{background:#fff;border-radius:12px;padding:18px 20px;margin:16px 0;
box-shadow:0 1px 3px rgba(20,30,60,.06);}}
section h2{{font-size:17px;margin:0 0 12px;display:flex;align-items:center;gap:8px;}}
.note{{color:#6b7280;font-size:13px;margin:-4px 0 12px;}}
table{{width:100%;border-collapse:collapse;font-size:13px;}}
th{{background:#2c3550;color:#fff;padding:9px 10px;text-align:left;font-weight:600;white-space:nowrap;}}
th.r,td.r{{text-align:right;}}
td{{padding:8px 10px;border-top:1px solid var(--line);}}
tbody tr:nth-child(even){{background:#fafbfc;}}
tbody tr:hover{{background:#f0f4ff;}}
.pill{{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;font-weight:600;}}
.pill.low{{background:#fdecec;color:var(--red);}}
.pill.dim{{background:#eef0f3;color:#6b7280;}}
.dmin{{color:var(--red);font-weight:700;}}
details summary{{cursor:pointer;font-size:17px;font-weight:600;margin-bottom:8px;outline:none;}}
.empty{{color:#9aa1ac;font-style:italic;}}
footer{{color:#8a94a6;font-size:12px;margin-top:20px;padding:0 4px;line-height:1.7;}}
a.raw{{color:#4a7bd6;text-decoration:none;}}
</style></head><body><div class="wrap">"""


def _tf(v):
    return html.escape(str(v)) if v not in (None, "") else "—"


def dos_cell(r, red=False):
    v = r.get("short_term_historical_days_of_supply") or r.get("historical_days_of_supply")
    if v in (None, ""):
        return '<td class="r">—</td>'
    fv = fnum(v)
    cls = ' class="r dmin"' if (red and fv is not None and fv < 30) else ' class="r"'
    return f'<td{cls}>{html.escape(str(v))}</td>'


def health_cell(r):
    h = r.get("fba_inventory_level_health_status")
    if not h:
        return "<td>—</td>"
    cls = "low" if any(k in h for k in ("低库存", "短缺", "断货")) else "dim"
    return f'<td><span class="pill {cls}">{html.escape(h)}</span></td>'


def row_html(r, red=False):
    return ("<tr>"
            f"<td>{_tf(r.get('_nick'))}</td>"
            f"<td>{_tf(r.get('asin'))}</td>"
            f"<td>{_tf(r.get('sku') or r.get('msku'))}</td>"
            f"<td>{_tf((r.get('product_name') or '')[:26])}</td>"
            f'<td class="r">{r.get("afn_fulfillable_quantity") or 0}</td>'
            f"{dos_cell(r, red)}"
            f'<td class="r">{inbound_of(r)}</td>'
            f"{health_cell(r)}"
            f"<td>{_tf(r.get('recommended_action'))}</td>"
            "</tr>")


def table_html(rows, red=False):
    if not rows:
        return '<p class="empty">无。</p>'
    head = ("<table><thead><tr><th>店铺</th><th>ASIN</th><th>SKU</th><th>品名</th>"
            '<th class="r">可售</th><th class="r">可售天数</th><th class="r">在途</th>'
            "<th>领星健康</th><th>领星建议</th></tr></thead><tbody>")
    return head + "".join(row_html(r, red) for r in rows) + "</tbody></table>"


def render_html(per_store, all_rows, b, nr, nz, ni, ny, nd):
    parts = [HTML_HEAD.format(today=TODAY)]
    parts.append(f"<h1>店铺 FBA 断货预警报告</h1>")
    stores_txt = "、".join(f"{html.escape(str(k))}（{v} SKU）" for k, v in per_store.items())
    parts.append(f'<div class="meta"><b>生成时间</b> {TODAY} &nbsp;·&nbsp; '
                 f'<b>数据来源</b> 领星 ERP OpenAPI（官方后台数据，防关联安全）<br>'
                 f'<b>覆盖店铺</b> {stores_txt} &nbsp;·&nbsp; <b>SKU 总数</b> {len(all_rows)}</div>')
    parts.append('<div class="cards">'
                 f'<div class="card red"><div class="n">{nr}</div><div class="t">🔴 低库存待补</div></div>'
                 f'<div class="card truck"><div class="n">{ni}</div><div class="t">🚚 零库存·在途</div></div>'
                 f'<div class="card zero"><div class="n">{nz}</div><div class="t">⬜ 零库存·无在途</div></div>'
                 f'<div class="card amber"><div class="n">{ny}</div><div class="t">🟡 14–30天关注</div></div>'
                 f'<div class="card dead"><div class="n">{nd}</div><div class="t">⚫ 滞销/过剩</div></div>'
                 f'<div class="card green"><div class="n">{len(b["green"])}</div><div class="t">🟢 库存健康</div></div>'
                 '</div>')
    parts.append(f'<div class="summary">🔴 <b>{nr} 个 SKU 正在低库存</b>（领星已标低库存或可售天数&lt;14，需尽快补货）；'
                 f'🚚 {ni} 个已零库存但补货在途（盯到货）；⬜ {nz} 个零库存且无在途（多为停售/僵尸链接，人工过一遍）；'
                 f'🟡 {ny} 个 14–30 天需排产；⚫ {nd} 个滞销/过剩需清仓。<br>'
                 f'<b>优先级</b>：先处理 🔴（真金白银在断）→ 扫 ⬜ 决定复活/清理 → ⚫ 安排清仓。</div>')

    parts.append(f'<section class="sec-red"><h2>🔴 低库存预警（{nr}）—— 最紧急在前，建议立即建 FBA 补货计划</h2>'
                 + table_html(b["red"], red=True) + "</section>")
    parts.append(f'<section><h2>🚚 已零库存 · 补货在途（{ni}）—— 无需下单，盯到货时间</h2>'
                 + table_html(b["inbound"]) + "</section>")
    parts.append('<section><details><summary>⬜ 零库存 · 无在途（%d）—— 点击展开逐个确认</summary>'
                 '<p class="note">这些可售 0 且无补货在途，多数是已下架或从未铺货的链接，不必都补，'
                 '重点挑还在推广/有自然流量的款。</p>%s</details></section>'
                 % (nz, table_html(b["zero"])))
    parts.append(f'<section><h2>🟡 需关注（{ny}）—— 可售天数 14–30 天，排下一批备货</h2>'
                 + table_html(b["yellow"], red=True) + "</section>")
    parts.append(f'<section><h2>⚫ 滞销 / 过剩（{nd}）—— 长库龄或过量，评估清仓/降价/停补</h2>'
                 + table_html(b["dead"]) + "</section>")
    parts.append(f'<section><details><summary>🟢 库存健康（{len(b["green"])}）—— 点击展开</summary>'
                 + table_html(b["green"]) + "</details></section>")
    parts.append('<footer>分类口径：低库存预警=领星判「低库存/短缺」或有货且短期可售天数&lt;14；'
                 '零库存按有无在途拆分；滞销=有货但领星判滞销或有 365 天+库龄且长可售天数。'
                 '可售天数取领星「短期历史可售天数」。<br>'
                 f'原始数据落盘：{html.escape(str(RAW_DIR))}\\fbastock_raw.json 。补货前请结合在途与实际销售节奏复核。</footer>')
    parts.append("</div></body></html>")
    return "\n".join(parts)


def main():
    cfg = client.load_config()
    token = client.get_token(cfg)
    stores = [s for s in client.load_stores() if s.get("patrol", True)]
    if not stores:
        raise SystemExit("lingxing_config.yaml 的 stores 为空——先跑 lingxing_client.py --smoke 拿 sid 填进去")

    all_rows, per_store = [], {}
    for s in stores:
        rows = fetch_all(cfg, token, s["sid"])
        for r in rows:
            r["_nick"] = s.get("nickname") or s["sid"]
        per_store[s.get("nickname") or s["sid"]] = len(rows)
        all_rows += rows

    b = {k: [] for k in ("red", "zero", "inbound", "yellow", "dead", "green")}
    for r in all_rows:
        b[classify(r)].append(r)
    b["red"].sort(key=dos_key)
    b["zero"].sort(key=lambda r: (str(r.get("_nick")), str(r.get("asin"))))
    b["dead"].sort(key=lambda r: -(fnum(r.get("estimated_excess_quantity")) or 0))

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_DIR / "fbastock_raw.json").write_text(
        json.dumps(all_rows, ensure_ascii=False, indent=2), encoding="utf-8")

    nr, nz, ni, ny, nd = (len(b["red"]), len(b["zero"]), len(b["inbound"]), len(b["yellow"]), len(b["dead"]))
    L = [
        "# 店铺 FBA 断货预警报告", "",
        f"- **生成时间**：{TODAY}",
        "- **数据来源**：领星 ERP OpenAPI（FBA 库存实时接口，官方后台数据，防关联安全）",
        "- **覆盖店铺**：" + "、".join(f"{k}（{v} SKU）" for k, v in per_store.items()),
        f"- **SKU 总数**：{len(all_rows)}", "",
        "## 一句话结论", "",
        f"🔴 **{nr} 个 SKU 正在低库存**（领星已标低库存或可售天数<14，需尽快补货）；"
        f"🚚 {ni} 个已零库存但补货在途（监控到货）；"
        f"⬜ {nz} 个零库存且无在途（多为停售/僵尸链接，需人工过一遍确认）；"
        f"🟡 {ny} 个 14–30 天需排产；⚫ {nd} 个滞销/过剩需清仓。", "",
        "> 优先级建议：先处理 🔴（真金白银在断），再花几分钟扫 ⬜ 决定哪些款要复活/清理，⚫ 安排清仓。", "",
    ]
    L += section(f"🔴 低库存预警（{nr}）—— 最紧急在前，建议立即建 FBA 补货计划", b["red"])
    L += section(f"🚚 已零库存 · 补货在途（{ni}）—— 无需下单，盯到货时间", b["inbound"])
    L += section(f"⬜ 零库存 · 无在途（{nz}）—— 逐个人工确认：停售款忽略 / 在卖款尽快补",
                 b["zero"], "> 这些可售 0 且无补货在途，多数是已下架或从未铺货的链接，不必都补，重点挑还在推广/有自然流量的款。")
    L += section(f"🟡 需关注（{ny}）—— 可售天数 14–30 天，排下一批备货", b["yellow"])
    L += section(f"⚫ 滞销 / 过剩（{nd}）—— 长库龄或过量，评估清仓/降价/停补", b["dead"])
    L += [
        f"## 🟢 库存健康：{len(b['green'])} 个 SKU（略）", "",
        "---",
        "> 分类口径：低库存预警=领星判「低库存/短缺」或有货且短期可售天数<14；零库存按有无在途拆分；"
        "滞销=有货但领星判滞销或有 365 天+库龄且长可售天数。可售天数取领星「短期历史可售天数」。",
        f"> 原始数据落盘：`{RAW_DIR}\\fbastock_raw.json`。补货前请结合在途与实际销售节奏复核。",
    ]

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_md = REPORT_DIR / "FBA断货预警报告.md"
    out_md.write_text("\n".join(L), encoding="utf-8")
    out_html = REPORT_DIR / "FBA断货预警报告.html"
    out_html.write_text(render_html(per_store, all_rows, b, nr, nz, ni, ny, nd), encoding="utf-8")
    print(f"[OK] HTML 报告: {out_html}")
    print(f"[OK] MD   报告: {out_md}")
    print(f"     总{len(all_rows)}  低库存{nr}  零库存在途{ni}  零库存无在途{nz}  关注{ny}  滞销{nd}  健康{len(b['green'])}")

    # 统一 finding 协议(40号 P1)：🔴逐 SKU=时间敏感 P1；其余按店铺聚合降噪
    R = F.load_rules()
    fnd = []
    for r in b["red"]:
        dos = r.get("short_term_historical_days_of_supply") or r.get("historical_days_of_supply")
        fnd.append(F.make_finding(
            "inventory", str(r.get("_nick")),
            f"{r.get('asin')} / {r.get('sku') or r.get('msku') or ''}",
            fact=f"低库存：可售 {r.get('afn_fulfillable_quantity') or 0} 件、可售天数 {dos or '—'}、"
                 f"在途 {inbound_of(r)}（领星判：{r.get('fba_inventory_level_health_status') or '—'}）",
            status="critical",
            reason_chain=[f"领星建议：{r.get('recommended_action') or '—'}"],
            impact="断货=销量与排名双损失，时间敏感", impact_amount=None,
            action=f"立即建 FBA 补货计划：{(r.get('product_name') or '')[:30]}",
            confidence="高", confidence_basis="领星 FBA 实时库存直读",
            rule="stock_red", time_sensitive=True, rules=R))
    agg = (("inbound", "🚚 零库存·在途", "盯到货时间，无需下单", "watch"),
           ("zero", "⬜ 零库存·无在途", "人工过一遍：停售款忽略/在卖款尽快补", "watch"),
           ("yellow", "🟡 可售 14-30 天", "排下一批备货", "watch"),
           ("dead", "⚫ 滞销/过剩", "评估清仓/降价/停补", "watch"))
    for key, label, act, st in agg:
        per = {}
        for r in b[key]:
            per.setdefault(str(r.get("_nick")), []).append(r)
        for nick, rows in per.items():
            fnd.append(F.make_finding(
                "inventory", nick, f"{nick} {label}",
                fact=f"{len(rows)} 个 SKU（明细见断货预警报告）", status=st,
                reason_chain=[], impact=f"{len(rows)} 个 SKU", impact_amount=None,
                action=act, confidence="高", confidence_basis="领星 FBA 库存直读",
                rule=f"stock_{key}", rules=R))
    fpath = F.save_findings(client.report_root(), ("inventory",), fnd)
    n_p1 = sum(1 for x in fnd if x["priority"] == "P1")
    print(f"[FINDINGS] 库存域 {len(fnd)} 条（P1 {n_p1}）-> {fpath}")


if __name__ == "__main__":
    main()
