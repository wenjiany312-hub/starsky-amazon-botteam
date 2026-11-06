# -*- coding: utf-8 -*-
"""巡检结果推送到飞书群（日报的"最后一公里"）

读当天 findings（由 daily_patrol_data.py / fba_stock_alert.py 落盘），
组装成飞书卡片推到群机器人。异常自己来找人，不用人去翻报告。

数据源：报告根\\findings\\{日期}.json（结构见 findings.py）
配置：lingxing_config.yaml 顶层 feishu 段（webhook 必填，secret 开了签名校验才填）

纪律：
  - 数据只进不编：当天没有 findings 文件就如实推"今日无巡检数据"，绝不编造
  - 凭据不外传：webhook/secret 只从本地配置读，日志里一律脱敏
  - 只报不改：推送只是通知，不碰任何后台

用法：
  python scripts/feishu_push.py                  # 推今天
  python scripts/feishu_push.py --date 2026-08-01
  python scripts/feishu_push.py --dry-run        # 只打印卡片，不发送（先看长什么样）
  python scripts/feishu_push.py --test           # 发一条连通性测试，验证 webhook 通不通
"""
import argparse
import base64
import hashlib
import hmac
import importlib.util
import json
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

import requests
import yaml

_HERE = Path(__file__).resolve().parent
CONFIG = _HERE.parent / "lingxing_config.yaml"

_spec = importlib.util.spec_from_file_location("lingxing_client", _HERE / "lingxing_client.py")
client = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(client)
_fspec = importlib.util.spec_from_file_location("findings", _HERE / "findings.py")
F = importlib.util.module_from_spec(_fspec)
_fspec.loader.exec_module(F)

DEFAULT_FEISHU = {
    "webhook": "",
    "secret": "",
    "push_priority": ["P1", "P2"],   # 推哪些等级；P3 默认不打扰
    "max_items": 5,                  # 卡片里最多列几条，超出只报计数
    "push_when_clear": True,         # 没有异常时也推一条"今天没事"（想安静就设 false）
}

PRIORITY_ICON = {"P1": "🔴", "P2": "🟡", "P3": "⚪"}
# 与旗舰简报统一的三级待办（scripts/brief_layout.py TIER_OF）：P1→紧急、P2→注意、P3→提示
TIER = {"P1": "紧急", "P2": "注意", "P3": "提示"}


def load_feishu_cfg(require_webhook=True):
    """飞书配置在 lingxing_config.yaml 顶层 feishu 段，与 lingxing / stores 平级"""
    if not CONFIG.exists():
        sys.exit("未找到 lingxing_config.yaml —— 请按 references/领星接入清单 完成接入后再试")
    raw = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    cfg = dict(DEFAULT_FEISHU)
    cfg.update(raw.get("feishu") or {})
    if require_webhook and not cfg.get("webhook"):
        sys.exit(
            "lingxing_config.yaml 里 feishu.webhook 为空。\n"
            "拿 webhook：飞书群 → 右上角设置 → 群机器人 → 添加机器人 → 自定义机器人 → 复制 webhook 地址。\n"
            "（如果同时开了「签名校验」，把密钥填到 feishu.secret）"
        )
    return cfg


def mask(url):
    """webhook 脱敏——日志/报错里绝不打印完整地址"""
    return url[:42] + "…" + url[-4:] if len(url) > 50 else url[:12] + "…"


def sign_payload(payload, secret):
    """飞书自定义机器人签名校验：timestamp + secret 做 HMAC-SHA256 再 base64"""
    ts = str(int(time.time()))
    to_sign = f"{ts}\n{secret}"
    digest = hmac.new(to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    payload["timestamp"] = ts
    payload["sign"] = base64.b64encode(digest).decode("utf-8")
    return payload


def verdict(counts):
    """结论三选一（给值班的人看，不说深挖）"""
    if counts.get("P1", 0) > 0:
        return "需要处理", "red"
    if counts.get("P2", 0) > 0:
        return "留意", "orange"
    return "正常", "green"


def fmt_money(amount, currency):
    if amount is None:
        return ""
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return ""
    if amount == 0:
        return ""
    cur = {"USD": "$", "CNY": "¥", "EUR": "€", "GBP": "£", "JPY": "¥"}.get(currency, "")
    if cur:
        return f"{cur}{amount:,.2f}"
    return f"{amount:,.2f} {currency}".strip()


def fmt_money_or_dash(amount, currency):
    s = fmt_money(amount, currency)
    return s if s else "—"


def fmt_pct(value, signed=False):
    if value is None:
        return "—"
    pct = value * 100
    if signed:
        return f"{pct:+.0f}%"
    return f"{pct:.0f}%"


def fmt_delta(cur, prev):
    if cur is None or prev in (None, 0):
        return "—"
    return fmt_pct((cur - prev) / prev, signed=True)


def md_date(iso):
    try:
        d = datetime.strptime(iso, "%Y-%m-%d")
        return f"{d.month}月{d.day}日"
    except Exception:
        return iso or ""


def friendly_store(nick, marketplace=None):
    """A店_老店_US → A店 · 美国站；分站点推送时标题已有站点，只留店名。"""
    raw = nick or "未标注店铺"
    shop = raw.replace("_老店", "").replace("_新店", "")
    shop = shop.replace("_US", "").replace("_UK", "").replace("_GB", "")
    shop = shop.strip("_") or raw
    mp = client.normalize_marketplace(marketplace) if marketplace else None
    if not mp:
        if raw.endswith("_US") or "美国" in raw:
            mp = "US"
        elif raw.endswith("_UK") or raw.endswith("_GB") or "英国" in raw:
            mp = "UK"
    if mp and marketplace:
        return shop
    if mp:
        return f"{shop} · {client.marketplace_label(mp)}"
    return shop


def human_object(finding):
    obj = finding.get("object") or ""
    if "广告账户" in obj:
        return "昨日广告账户"
    if finding.get("rule") == "ads_bad_status" and "ACCOUNT_OUT_OF_BUDGET" in (finding.get("fact") or ""):
        return "账户日预算耗尽"
    if len(obj) > 34:
        return obj[:32] + "…"
    return obj


CURRENCY_SYMBOL = {"USD": "$", "GBP": "£", "EUR": "€", "CNY": "¥", "JPY": "¥"}


def symbolize(text):
    """「5.94 GBP」→「£5.94」：卡片上金额统一用货币符号。"""
    return re.sub(r"([0-9][0-9,]*(?:\.[0-9]+)?) ?(USD|GBP|EUR|CNY|JPY)\b",
                  lambda m: CURRENCY_SYMBOL[m.group(2)] + m.group(1), text or "")


def human_fact(finding):
    return symbolize(_human_fact(finding))


def _human_fact(finding):
    fact = finding.get("fact") or ""
    rule = finding.get("rule")
    if finding.get("object") == "账户日预算耗尽":
        return fact
    if rule == "ads_bad_status":
        if "ACCOUNT_OUT_OF_BUDGET" in fact:
            return "账户日预算耗尽，在跑广告已停投"
        if "CAMPAIGN_OUT_OF_BUDGET" in fact:
            money = fmt_money(finding.get("impact_amount"), finding.get("currency"))
            return "活动预算耗尽，中途停投" + (f"（近7天 {money}）" if money else "")
        return "投放状态异常，活动未在正常投放"
    if rule == "ads_acos_over":
        cur = finding.get("currency") or ""
        sym = {"USD": "$", "GBP": "£", "EUR": "€", "CNY": "¥", "JPY": "¥"}.get(cur, "")
        cleaned = fact.replace(" USD", "").replace(" GBP", "").replace(" EUR", "")
        if sym:
            cleaned = re.sub(r"花费 ([0-9.]+)", rf"花费 {sym}\1", cleaned)
            cleaned = re.sub(r"销售 ([0-9.]+)", rf"销售 {sym}\1", cleaned)
        return cleaned.replace("（花费 ", " · 花费 ").replace("），超", "，超")
    if rule == "ads_burn_no_order":
        return fact
    return fact


def human_action(finding):
    fact = finding.get("fact") or ""
    rule = finding.get("rule")
    if finding.get("object") == "账户日预算耗尽" or "ACCOUNT_OUT_OF_BUDGET" in fact:
        return "到广告后台提高账户日预算"
    if "CAMPAIGN_OUT_OF_BUDGET" in fact:
        return "提高该活动预算，或关掉低效活动让位"
    if rule == "ads_acos_over":
        return "先定位超线活动，再否词或降竞价"
    action = finding.get("action") or ""
    action = action.replace("跑 ads_health_check.py 定位超线活动 → queryWords 出否词/降bid",
                            "先定位超线活动，再否词或降竞价")
    action = action.replace("ACCOUNT 级查后台账户预算上限", "到后台查账户日预算")
    return action


def collapse_findings(items):
    """同一店铺多条账户预算耗尽合并成一条，避免英国站刷屏。"""
    out, bucket = [], {}
    for x in items:
        fact = x.get("fact") or ""
        if x.get("rule") == "ads_bad_status" and "ACCOUNT_OUT_OF_BUDGET" in fact:
            bucket.setdefault(x.get("store"), []).append(x)
        else:
            out.append(x)
    merged = []
    for store, rows in bucket.items():
        if len(rows) == 1:
            merged.append(rows[0])
            continue
        first = dict(rows[0])
        names = [r.get("object") or "" for r in rows]
        first["object"] = "账户日预算耗尽"
        first["fact"] = f"{len(rows)} 条在跑活动已停投（含 {names[0]} 等）"
        first["action"] = "到广告后台提高账户日预算"
        first["impact_amount"] = round(sum(r.get("impact_amount") or 0 for r in rows), 2)
        merged.append(first)
    return merged + out


def load_patrol_bundle(run_day, marketplace=None):
    """读当日脉搏 JSON。分站点文件优先，否则从总文件按店铺过滤。"""
    root = client.report_root() / "lingxing" / run_day
    candidates = []
    if marketplace:
        candidates.append(root / ("daily_patrol_data_%s.json" % marketplace))
    candidates.append(root / "daily_patrol_data.json")
    stores, pulse_day, tz_name = [], None, None
    for p in candidates:
        if not p.exists():
            continue
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        stores = list(raw.get("stores") or [])
        pulse_day = raw.get("pulse_day") or pulse_day
        tz_name = raw.get("timezone") or tz_name
        if marketplace:
            mp = client.normalize_marketplace(marketplace)
            nicks = set(client.store_nicknames(mp))
            stores = [s for s in stores
                      if s.get("nickname") in nicks
                      or client.normalize_marketplace(s.get("marketplace")) == mp]
        if stores:
            break
    if marketplace and not tz_name:
        tz_name = client.marketplace_tz_name(marketplace)
    if marketplace and not pulse_day:
        pulse_day = client.closed_pulse_day(marketplace).isoformat()
    return stores, pulse_day, tz_name


def pulse_fields(store):
    """一张店的昨日脉搏：全店销售 / 广告花费 / 昨日ACOS / 7天ACOS。"""
    sales = (store or {}).get("sales") or {}
    ads = (store or {}).get("ads") or {}
    fam = (store or {}).get("family") or {}
    y = sales.get("yesterday") or {}
    db = sales.get("day_before") or {}
    lw = sales.get("same_day_last_week") or {}
    if y.get("error") or ads.get("error"):
        return None
    cur = y.get("currency") or ""
    ya, da, la = y.get("total_amount"), db.get("total_amount"), lw.get("total_amount")
    ay = ads.get("yesterday") or {}
    w7 = ads.get("window7") or {}
    dod = fmt_delta(ya, da)
    wow = fmt_delta(ya, la)
    families = sorted(fam.get("families") or [], key=lambda f: -(f.get("y_units") or 0))
    top_fam = families[0] if families else None
    return {
        "currency": cur,
        "sales": fmt_money_or_dash(ya, cur),
        "dod": dod,
        "wow": wow,
        "asin_count": y.get("asin_count") or 0,
        "ad_spend": fmt_money_or_dash(ay.get("spend"), cur),
        "ad_sales": fmt_money_or_dash(ay.get("sales"), cur),
        "ad_orders": ay.get("orders"),
        "acos_y": fmt_pct(ay.get("acos")),
        "acos_7": fmt_pct(w7.get("acos")),
        "units": fam.get("y_units_total"),
        "top_family": (top_fam or {}).get("nickname"),
        "top_units": (top_fam or {}).get("y_units"),
    }


ASIN_LIMIT = 8
ADS_TOP_LIMIT = 3


def _family_children(store):
    kids = []
    for fam in (((store or {}).get("family") or {}).get("families") or []):
        kids.extend(fam.get("children") or [])
    return kids


def fmt_dos(days, fulfillable):
    if fulfillable is not None and fulfillable <= 0:
        return "缺货"
    if days is None:
        return None
    if days >= 100:
        return "可售100天+"
    if days < 1:
        return "可售<1天"
    return "可售%.0f天" % days


def asin_detail_block(store, limit=ASIN_LIMIT):
    """金额=日销，件数=Listing，笔数=订单去重，可售天数=FBA 短周期。四套口径并列。"""
    sales = ((store or {}).get("sales") or {}).get("yesterday") or {}
    if sales.get("error"):
        return None
    cur = sales.get("currency") or ""
    amount_map, name_map = {}, {}
    for r in sales.get("top_asin") or []:
        a = r.get("asin")
        if not a:
            continue
        amount_map[a] = r.get("amount")
        name_map[a] = (r.get("name") or "").strip()
    unit_map, nick_map, listing_fba = {}, {}, {}
    for c in _family_children(store):
        a = c.get("asin")
        if not a:
            continue
        unit_map[a] = int(c.get("y_units") or 0)
        if c.get("nickname"):
            nick_map[a] = c["nickname"]
        if c.get("fba") is not None:
            listing_fba[a] = int(c.get("fba") or 0)
    orders_info = (store or {}).get("orders") or {}
    orders_map = orders_info.get("by_asin") or {}
    fba_info = (store or {}).get("fba") or {}
    fba_map = fba_info.get("by_asin") or {}
    asins = set(amount_map) | {a for a, u in unit_map.items() if u > 0} | set(orders_map)
    rows = []
    for a in asins:
        nick = nick_map.get(a) or name_map.get(a) or a
        if len(nick) > 18:
            nick = nick[:16] + "…"
        fba = fba_map.get(a) or {}
        fulfillable = fba.get("fulfillable")
        if fulfillable is None:
            fulfillable = listing_fba.get(a)
        od = orders_map.get(a) or {}
        rows.append({
            "asin": a, "nick": nick,
            "units": unit_map.get(a),
            "orders": od.get("orders"),
            "amount": amount_map.get(a),
            "fulfillable": fulfillable,
            "dos": fba.get("days_of_supply"),
        })
    rows.sort(key=lambda r: (-(r["amount"] or 0), -(r["units"] or 0), -(r["orders"] or 0)))
    total = len(rows)
    rows = rows[:limit]
    if not rows:
        return None
    lines = ["**昨日 ASIN**（件数=Listing，笔数=订单去重，可售天数=FBA）"]
    if orders_info.get("error"):
        lines.append("订单接口不可用，本段不显示笔数")
    if fba_info.get("error"):
        lines.append("FBA 接口不可用，本段不显示可售天数")
    for r in rows:
        u = "—" if r["units"] is None else f"{r['units']}件"
        bits = [f"`{r['asin']}` {r['nick']}", u]
        if r["orders"] is not None:
            bits.append(f"{r['orders']}笔")
        bits.append(fmt_money_or_dash(r["amount"], cur))
        dos = fmt_dos(r["dos"], r["fulfillable"])
        if dos:
            bits.append(dos)
        elif r["fulfillable"] is not None:
            bits.append(f"FBA {r['fulfillable']}")
        lines.append("　".join(bits))
    if total > len(rows):
        lines.append(f"还有 {total - len(rows)} 个动销 ASIN 未展开")
    return "\n".join(lines)


def ads_top_block(store, limit=ADS_TOP_LIMIT):
    movers = ((store or {}).get("ads") or {}).get("y_top_spenders") or []
    if not movers:
        return None
    cur = (((store or {}).get("sales") or {}).get("yesterday") or {}).get("currency") or ""
    lines = ["**昨日广告花费 Top**（广告订单，不是全店订单）"]
    for m in movers[:limit]:
        name = (m.get("campaign") or "").strip() or "未命名活动"
        if len(name) > 28:
            name = name[:26] + "…"
        spend = fmt_money(m.get("y_spend"), cur) or "—"
        orders = m.get("y_orders")
        od = f"{int(orders)}单" if orders is not None else ""
        line = f"{name}　{spend}"
        if od:
            line += f"　{od}"
        lines.append(line)
    return "\n".join(lines)


def append_detail_blocks(elements, stores, marketplace=None):
    if not stores:
        return
    chunks = []
    multi = len(stores) > 1
    for s in stores:
        parts = []
        asin_b = asin_detail_block(s)
        ads_b = ads_top_block(s)
        if not asin_b and not ads_b:
            continue
        if multi:
            parts.append(f"**{friendly_store(s.get('nickname'), marketplace)}**")
        if asin_b:
            parts.append(asin_b)
        if ads_b:
            parts.append(ads_b)
        chunks.append("\n\n".join(parts))
    if not chunks:
        return
    elements.append({"tag": "hr"})
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n\n".join(chunks)}})


def filter_findings_by_marketplace(data, marketplace):
    """只保留该站点店铺的 findings，并重算计数。"""
    if not data or not marketplace:
        return data
    nicks = set(client.store_nicknames(marketplace))
    items = [x for x in (data.get("findings") or []) if x.get("store") in nicks]
    counts = {p: sum(1 for x in items if x.get("priority") == p) for p in ("P1", "P2", "P3")}
    filtered = dict(data)
    filtered["findings"] = items
    filtered["counts"] = counts
    return filtered


HEALTH_LINES = 5


def health_blocks(day, marketplace, currency):
    """S5 s5_daily_health.py 写的「昨日利润 + 产品健康灯」；当天没有这份数据就不显示，日报照常。"""
    if not marketplace:
        return []
    path = client.report_root() / "lingxing" / day / f"product_health_{client.normalize_marketplace(marketplace)}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    sym = CURRENCY_SYMBOL.get(currency or "", "")
    money = lambda v: "—" if v is None else (f"-{sym}{abs(v):,.2f}" if v < 0 else f"{sym}{v:,.2f}")
    y = data.get("yesterday") or {}
    note = [f"广告费 {money(y.get('ad_spend'))}", f"TACOS {fmt_pct(y.get('tacos'))}"]
    if y.get("units_without_cost"):
        note.append(f"{y['units_without_cost']} 件没填成本未计")
    blocks = [{"tag": "hr"}, {"tag": "div", "fields": [
        {"is_short": True, "text": {"tag": "lark_md", "content": f"**💰 昨日利润（估）**\n{money(y.get('profit'))}\n扣广告后" + ("（按估算毛利）" if y.get("estimate") else "")}},
        {"is_short": True, "text": {"tag": "lark_md", "content": "**产品健康（近7天）**\n" + " · ".join(
            f"{k} {v}" for k, v in (data.get("health_counts") or {}).items() if v) + "\n广告 + 自然合看"}},
    ]}, {"tag": "note", "elements": [{"tag": "plain_text", "content": " · ".join(note)}]}]
    flagged = [h for h in data.get("health") or [] if h.get("light") in ("🔴", "🟡")][:HEALTH_LINES]
    if flagged:
        lines = []
        for h in flagged:
            name = (h.get("name") or "")[:18]
            bits = [h["verdict"].split("：")[0]]
            if h.get("profit_after_ads") is not None:
                bits.append(f"近7天 {money(h['profit_after_ads'])}")
            if h.get("ad_acos") is not None and h.get("break_even_acos") is not None:
                bits.append(f"广告 ACOS {fmt_pct(h['ad_acos'])}（保本 {fmt_pct(h['break_even_acos'])}）")
            if h.get("organic_share") is not None:
                arrow = "" if h.get("organic_delta") is None else ("↑" if h["organic_delta"] >= 0.03 else "↓" if h["organic_delta"] <= -0.03 else "→")
                bits.append(f"自然单 {fmt_pct(h['organic_share'])}{arrow}")
            lines.append(f"{h['light']} `{h['asin']}` {name}　" + " · ".join(bits))
        blocks.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}})
    return blocks


def plan_blocks(day, marketplace):
    """S5 s5_daily_plan.py 当天生成的优化方案摘要；没有就不显示。"""
    if not marketplace:
        return []
    path = client.report_root() / "lingxing" / day / f"daily_plan_{client.normalize_marketplace(marketplace)}.json"
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    kinds = "、".join(f"{k} {v}" for k, v in (plan.get("by_kind") or {}).items())
    head = f"**📋 今日方案**　{plan.get('actions', 0)} 条建议" + (f"（{kinds}）" if kinds else "（本轮没有达到动手条件的对象）")
    lines = [head]
    if plan.get('seasonal'):
        scripts = str(Path(__file__).resolve().parents[3] / 'scripts')
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        from seasonal_display import display_text
        lines.append('🎄 ' + display_text(plan['seasonal']))
    if plan.get("breakdown"):  # 单量拆解：先定位是哪一端（广告知识库 KC-0163）
        lines.append("🔍 单量拆解：" + "；".join(f"`{r['asin']}` {r['orders'][0]}→{r['orders'][1]} 单 · {r['node']}" for r in plan["breakdown"][:4]))
    if plan.get("red_products"):
        lines.append("🔴 产品：" + "、".join(f"`{a}`" for a in plan["red_products"][:6]))
    lines.append("回到电脑说「看今天的广告方案」：补知识库 / SIF 依据 → 你确认 → 能写回的一键同步，新建活动出上传表，文案出改前改后")
    return [{"tag": "hr"}, {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}}]


def build_card(day, data, cfg, marketplace=None, pulse_day=None, tz_name=None, patrol_stores=None):
    """飞书日报卡：先脉搏（卖了多少/花了多少），再今日要办。"""
    wanted = set(cfg["push_priority"])
    raw_items = data.get("findings") or []
    items = collapse_findings([x for x in raw_items if x.get("priority") in wanted])
    counts = {
        p: sum(1 for x in items if x.get("priority") == p)
        for p in ("P1", "P2", "P3")
    }
    counts["P3"] = sum(1 for x in raw_items if x.get("priority") == "P3")
    concl, color = verdict({**counts, "P1": counts.get("P1", 0), "P2": counts.get("P2", 0)})
    label = client.marketplace_label(marketplace) if marketplace else ""
    data_day = pulse_day or day
    data_md = md_date(data_day)

    shown = items[: int(cfg["max_items"])]
    total_shown = len(items)

    stores = list(patrol_stores or [])
    pulse = pulse_fields(stores[0]) if len(stores) == 1 else None

    if pulse:
        elements = [{
            "tag": "div",
            "fields": [
                {"is_short": True, "text": {"tag": "lark_md",
                 "content": f"**全店销售**\n{pulse['sales']}\n较前日 {pulse['dod']}"}},
                {"is_short": True, "text": {"tag": "lark_md",
                 "content": f"**广告花费**\n{pulse['ad_spend']}\n广告成交 {pulse['ad_sales']}"}},
                {"is_short": True, "text": {"tag": "lark_md",
                 "content": f"**昨日 ACOS**\n{pulse['acos_y']}\n近7天 {pulse['acos_7']}"}},
                {"is_short": True, "text": {"tag": "lark_md",
                 "content": f"**结论**\n{concl}\n紧急 {counts.get('P1', 0)} · 注意 {counts.get('P2', 0)}"}},
            ],
        }]
        extra = []
        extra.append(f"较上周 {pulse['wow']}")
        extra.append(f"动销 {pulse['asin_count']} 个 ASIN")
        if pulse.get("units") is not None:
            extra.append(f"家族约 {pulse['units']} 件")
        if pulse.get("top_family"):
            extra.append(f"{pulse['top_family']} {pulse['top_units']} 件")
        if extra:
            elements.append({"tag": "note", "elements": [{"tag": "plain_text",
                "content": " · ".join(extra)}]})
        elements.extend(health_blocks(day, marketplace, pulse.get("currency")))
        elements.extend(plan_blocks(day, marketplace))
        append_detail_blocks(elements, stores, marketplace)
    elif stores:
        chunks = []
        for s in stores:
            p = pulse_fields(s)
            name = friendly_store(s.get("nickname"), marketplace)
            if not p:
                chunks.append(f"**{name}**\n脉搏数据不可用")
                continue
            chunks.append(
                f"**{name}**\n销售 {p['sales']}（前日 {p['dod']} / 上周 {p['wow']}）"
                f" · 花费 {p['ad_spend']} · ACOS {p['acos_y']} / 7天 {p['acos_7']}"
            )
        elements = [{"tag": "div", "text": {"tag": "lark_md", "content": "\n\n".join(chunks)}}]
        elements.append({
            "tag": "div",
            "fields": [
                {"is_short": True, "text": {"tag": "lark_md",
                 "content": f"**结论**\n{concl}"}},
                {"is_short": True, "text": {"tag": "lark_md",
                 "content": f"**紧急**\n{counts.get('P1', 0)}"}},
                {"is_short": True, "text": {"tag": "lark_md",
                 "content": f"**注意**\n{counts.get('P2', 0)}"}},
                {"is_short": True, "text": {"tag": "lark_md",
                 "content": f"**提示（留档）**\n{counts.get('P3', 0)}"}},
            ],
        })
        append_detail_blocks(elements, stores, marketplace)
    else:
        elements = [{
            "tag": "div",
            "fields": [
                {"is_short": True, "text": {"tag": "lark_md",
                 "content": f"**结论**\n{concl}"}},
                {"is_short": True, "text": {"tag": "lark_md",
                 "content": f"**紧急**\n{counts.get('P1', 0)}"}},
                {"is_short": True, "text": {"tag": "lark_md",
                 "content": f"**注意**\n{counts.get('P2', 0)}"}},
                {"is_short": True, "text": {"tag": "lark_md",
                 "content": f"**提示（留档）**\n{counts.get('P3', 0)}"}},
            ],
        }]

    elements.append({"tag": "hr"})
    if shown:
        lines = ["**今日要办**"]
        for x in shown:
            icon = PRIORITY_ICON.get(x.get("priority"), "")
            money = fmt_money(x.get("impact_amount"), x.get("currency"))
            head = f"{icon} [{TIER.get(x.get('priority'), '提示')}] **{human_object(x)}**"
            if money:
                head += f"　{money}"
            lines.append(head)
            fact = human_fact(x)
            if fact:
                lines.append(f"　{fact}")
            action = human_action(x)
            if action:
                lines.append(f"　→ {action}")
        elements.append({"tag": "div",
                         "text": {"tag": "lark_md", "content": "\n".join(lines)}})
        if total_shown > len(shown):
            elements.append({"tag": "note", "elements": [{"tag": "plain_text",
                "content": f"还有 {total_shown - len(shown)} 条未展开，已按优先级截断"}]})
    else:
        elements.append({"tag": "div", "text": {"tag": "lark_md",
            "content": "**今日要办**\n推送等级内没有要处理的条目。"}})

    foot = f"数据日 {data_md}"
    if label:
        foot += f" · {label}当地昨天"
    foot += " · 只诊断，不改后台"
    elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": foot}]})

    title = f"{label}日报 · {data_md}" if label else f"店铺巡检 · {data_md}"
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": color,
            },
            "elements": elements,
        },
    }


def build_empty_card(day):
    """没有 findings 文件时——如实说没数据，不编"""
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": f"店铺巡检 · {day}"},
                       "template": "grey"},
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md",
                 "content": "**今天没有巡检数据。**\n还没跑取数，或领星接口当天不可用。"}},
                {"tag": "note", "elements": [{"tag": "plain_text",
                 "content": "缺数就标明缺数，不估算补齐。"}]},
            ],
        },
    }


def push(payload, cfg):
    if cfg.get("secret"):
        payload = sign_payload(payload, cfg["secret"])
    try:
        r = requests.post(cfg["webhook"], json=payload, timeout=20)
    except requests.RequestException as e:
        sys.exit(f"推送失败（网络）: {e.__class__.__name__} —— webhook {mask(cfg['webhook'])}")
    try:
        res = r.json()
    except ValueError:
        sys.exit(f"推送失败：飞书返回非 JSON（HTTP {r.status_code}）")
    if res.get("code") not in (0, None) or res.get("StatusCode") not in (0, None):
        hint = ""
        if res.get("code") == 19021:
            hint = "\n提示：开了签名校验但 feishu.secret 没填或填错"
        elif res.get("code") == 19001:
            hint = "\n提示：webhook 地址不对，去群机器人设置里重新复制"
        sys.exit(f"推送失败：{res}{hint}")
    return res


def main():
    ap = argparse.ArgumentParser(description="把当天巡检 findings 推到飞书群")
    ap.add_argument("--date", help="数据日期 YYYY-MM-DD，默认今天（北京推送日）")
    ap.add_argument("--marketplace", help="只推 UK 或 US 站点")
    ap.add_argument("--dry-run", action="store_true", help="只打印卡片 JSON，不发送")
    ap.add_argument("--test", action="store_true", help="发一条连通性测试消息")
    args = ap.parse_args()

    cfg = load_feishu_cfg(require_webhook=not args.dry_run)
    mp = client.normalize_marketplace(args.marketplace) if args.marketplace else None

    if args.test:
        payload = {"msg_type": "text", "content": {
            "text": f"[连通性测试] 领星巡检推送已接通　{datetime.now():%Y-%m-%d %H:%M:%S}"}}
        push(payload, cfg)
        print(f"✅ 测试消息已发出 —— webhook {mask(cfg['webhook'])}")
        return

    day = args.date or date.today().isoformat()
    try:
        day_obj = datetime.strptime(day, "%Y-%m-%d").date()
    except ValueError:
        sys.exit(f"--date 格式不对: {day}（应为 YYYY-MM-DD）")

    data = F.load_findings(client.report_root(), day_obj)
    patrol_stores, pulse_day, tz_name = load_patrol_bundle(day, mp)
    if data is not None and mp:
        data = filter_findings_by_marketplace(data, mp)

    if data is None:
        payload = build_empty_card(day)
    else:
        counts = data.get("counts") or {}
        _, _ = verdict(counts)
        if not any(counts.get(p, 0) for p in cfg["push_priority"]) and not cfg["push_when_clear"]:
            print(f"无 {'/'.join(cfg['push_priority'])} 异常且 push_when_clear=false，跳过推送")
            return
        payload = build_card(day, data, cfg, marketplace=mp, pulse_day=pulse_day,
                             tz_name=tz_name, patrol_stores=patrol_stores)

    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    push(payload, cfg)
    if data is None:
        print(f"✅ 已推送（{day} 无巡检数据）—— webhook {mask(cfg['webhook'])}")
    else:
        c = data.get("counts") or {}
        print(f"✅ 已推送 {day}　P1={c.get('P1', 0)} P2={c.get('P2', 0)} P3={c.get('P3', 0)}"
              f" —— webhook {mask(cfg['webhook'])}")


if __name__ == "__main__":
    main()
