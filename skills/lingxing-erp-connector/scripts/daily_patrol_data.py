# -*- coding: utf-8 -*-
"""每日巡检数据拉取（巡检日报的数据后端）

一条命令拉齐日报所需数据（patrol=true 的全部店铺）：
  1. 销售额脉搏：昨日 / 前日 / 上周同日 的 ASIN 日销售额（asinDailyLists）
     ⚠️ 该接口只返回销售额（map_value 字符串），不含销量件数——口径如实标注，不推算件数。
  2. 广告脉搏：昨日花费/销售/订单 + 近7天窗口合计
     ⚠️ 领星 campaigns 的 end_date 是开区间（不含尾日，start=end 即空区间全 0），
     API 传参一律 end+1；昨日用同起点双窗口差值精确推导，非估算（2026-07-07 与亚马逊后台核对）。
  3. 红线捕捉：近7天烧钱无单（花费≥$5 且 0 单）、serving_status 异常（OUT_OF_BUDGET 等）。

输出：
  - 原始+汇总 JSON 落盘 报告根目录\\lingxing\\{日期}\\daily_patrol_data.json（根目录规则见 lingxing_client.report_root）
  - stdout 打印紧凑汇总（供 Codex 直接读取撰写日报）

纪律：接口报错/字段缺失一律如实标注，禁止估算补齐。

用法（1688 MCP 的 venv 依赖已齐；或任意装有 requests/pycryptodome/pyyaml 的 python）：
  python scripts/daily_patrol_data.py [--days-back 1]
"""
import argparse
import os
import importlib.util
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("lingxing_client", _HERE / "lingxing_client.py")
client = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(client)
_fspec = importlib.util.spec_from_file_location("findings", _HERE / "findings.py")
F = importlib.util.module_from_spec(_fspec)
_fspec.loader.exec_module(F)
RULES = F.load_rules()   # 阈值一律来自 rules.yaml(40号 P1:规则外置)

SALES_PATH = "/erp/sc/data/sales_report/asinDailyLists"   # ✅ 实测；必填 sid + event_date(Y-m-d)
ADS_PATH = "/erp/sc/data/ads/campaigns"                   # ✅ 实测；sid + start_date + end_date（须为区间）
LISTING_PATH = "/erp/sc/data/mws/listing"                 # ✅ 实测；sid + offset/length（家族脉搏数据源）
ORDERS_PATH = "/erp/sc/data/mws/orders"                   # ✅ 实测；sid + start_date + end_date，end 开区间须 +1
FBA_PATH = "/erp/sc/routing/fba/fbaStock/fbaList"         # ✅ 实测；可售天数/可售量

RAW_ROOT = client.report_root() / "lingxing"
# 家族配置(父子体结构 + floor_price)：优先本 Skill 内，回退到兄弟 Skill 的历史位置。
# 四类家族级红线(跌破红线/价格异动/星级下跌/疑集中差评)全部依赖它；找不到就整块跳过。
_PATROL_CANDIDATES = (
    # 用户目录优先：插件升级换安装目录，放插件里的配置每次都丢（make_patrol_config.py 生成到这里）
    Path(os.environ.get("STARSKY_CONFIG_DIR") or (Path(os.environ.get("STARSKY_HOME") or (Path.home() / ".starsky-codex")) / "config")) / "巡检配置.yaml",
    _HERE.parent / "references" / "巡检配置.yaml",
    _HERE.parent.parent / "amazon-store-patrol-orchestrator" / "references" / "巡检配置.yaml",
    _HERE.parent.parent / "amazon-store-patrol" / "references" / "巡检配置.yaml",
)
PATROL_CFG = next((p for p in _PATROL_CANDIDATES if p.exists()), _PATROL_CANDIDATES[0])

BURN_MIN_SPEND = float(RULES["ads"]["burn_min_spend_7d"])
BAD_STATUS = tuple(RULES["ads"]["bad_status_keywords"])
BAD_STATUS_MIN_SPEND = float(RULES["ads"]["bad_status_min_spend_7d"])   # 僵尸活动降噪


def fnum(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def rows_of(resp):
    data = resp.get("data")
    if isinstance(data, list):
        return data
    return (data or {}).get("list") or []


def fetch_paged(cfg, token, path, biz, max_pages=30):
    rows, offset, pages, truncated = [], 0, 0, False
    err = None
    while True:
        p = dict(biz)
        p.update({"offset": offset, "length": 200})
        resp = client.call(cfg, token, path, p, method="POST")
        if str(resp.get("code")) not in ("0", "200"):
            err = "code=%s msg=%s" % (resp.get("code"), resp.get("msg") or resp.get("message"))
            break
        lst = rows_of(resp)
        rows += lst
        pages += 1
        if len(lst) < 200:
            break
        if pages >= max_pages:
            truncated = True
            break
        offset += 200
    meta = {
        "pagination_complete": not truncated and err is None,
        "truncated": truncated,
        "row_count": len(rows),
        "pages": pages,
    }
    return rows, err, meta


def sales_of_day(cfg, token, sid, day):
    """asinDailyLists: 每行 = 一个 ASIN 当日销售额（map_value 字符串，币种 currency_code）"""
    rows, err, meta = fetch_paged(cfg, token, SALES_PATH,
                            {"sid": sid, "event_date": day.isoformat()})
    if err:
        return {"date": day.isoformat(), "error": err, "pagination": meta}
    total = 0.0
    by_asin = []
    for r in rows:
        amt = fnum(r.get("map_value"), None)
        if amt is not None:
            total += amt
        by_asin.append({"asin": r.get("asin"), "amount": amt,
                        "name": (r.get("product_name") or "")[:60]})
    by_asin.sort(key=lambda x: -(x["amount"] or 0))
    return {"date": day.isoformat(), "asin_count": len(rows),
            "total_amount": round(total, 2),
            "currency": rows[0].get("currency_code") if rows else None,
            "top_asin": by_asin[:30],
            "pagination": meta,
            "note": "领星该接口只提供销售额，无销量件数"}


def orders_by_asin(cfg, token, sid, day):
    """按 ASIN 汇总昨日订单。笔数=去重 amazon_order_id；件数=quantity_ordered。
    不落盘原始订单（含地址字段）。end_date 与广告接口一样是开区间，须 +1。"""
    rows, err, meta = fetch_paged(cfg, token, ORDERS_PATH, {
        "sid": sid,
        "start_date": day.isoformat(),
        "end_date": (day + timedelta(days=1)).isoformat(),
    })
    if err:
        return {"error": err, "by_asin": {}, "pagination": meta}
    by, skipped, canceled = {}, 0, 0
    for o in rows:
        st = str(o.get("order_status") or "").lower()
        if st in ("canceled", "cancelled"):
            canceled += 1
            continue
        local = str(o.get("purchase_date_local") or "")[:10]
        if local and local != day.isoformat():
            skipped += 1
            continue
        oid = o.get("amazon_order_id")
        for it in o.get("item_list") or []:
            if str(it.get("order_status") or "").lower() in ("canceled", "cancelled"):
                continue
            a = it.get("asin")
            if not a:
                continue
            rec = by.setdefault(a, {"order_ids": set(), "units": 0})
            if oid:
                rec["order_ids"].add(oid)
            rec["units"] += int(fnum(it.get("quantity_ordered"), 0))
    return {
        "by_asin": {a: {"orders": len(v["order_ids"]), "units": v["units"]}
                    for a, v in by.items()},
        "order_rows": len(rows),
        "skipped_other_day": skipped,
        "canceled": canceled,
        "note": "订单笔数=去重 amazon_order_id；不含取消单；end_date 开区间已+1",
        "pagination": meta,
    }


def fba_by_asin(cfg, token, sid):
    """FBA 按 ASIN 汇总可售量和短周期可售天数。多 SKU 时：可售量相加，有货 SKU 取最小天数。"""
    rows, err, meta = fetch_paged(cfg, token, FBA_PATH, {"sid": sid})
    if err:
        return {"error": err, "by_asin": {}, "pagination": meta}
    by = {}
    for r in rows:
        a = r.get("asin")
        if not a:
            continue
        rec = by.setdefault(a, {"fulfillable": 0, "dos_in_stock": [], "dos_all": [], "health": None})
        avail = int(fnum(r.get("afn_fulfillable_quantity"), 0))
        rec["fulfillable"] += avail
        dos = fnum(r.get("short_term_historical_days_of_supply"))
        if dos is None:
            dos = fnum(r.get("historical_days_of_supply"))
        if dos is not None:
            rec["dos_all"].append(dos)
            if avail > 0:
                rec["dos_in_stock"].append(dos)
        h = r.get("fba_inventory_level_health_status")
        if h and (not rec["health"] or any(k in h for k in ("缺货", "低库存", "短缺", "断货"))):
            rec["health"] = h
    out = {}
    for a, v in by.items():
        pool = v["dos_in_stock"] or v["dos_all"]
        dos = min(pool) if pool else None
        out[a] = {
            "fulfillable": v["fulfillable"],
            "days_of_supply": round(dos, 1) if dos is not None else None,
            "health": v["health"],
        }
    return {
        "by_asin": out,
        "sku_rows": len(rows),
        "pagination": meta,
        "note": "可售天数=short_term_historical_days_of_supply，多 SKU 有货取最小；缺天数保持 null，不是 0",
    }


def ads_pulse(cfg, token, sid, d1):
    """窗口A 与 窗口B 同起点差值 = 昨日当天（精确，非估算）。
    ⚠️ 领星 campaigns 的 end_date 是开区间（不含尾日；start=end 即空区间，全 0 的怪癖由此而来），
    故 API 传参一律 end+1：A=[d1-6, d1] 传 end=d1+1；B=[d1-6, d1-1] 传 end=d1。
    2026-07-07 实测定位：未 +1 时"昨日"实际取到前日（与亚马逊后台逐日核对确认）。"""
    start = (d1 - timedelta(days=6)).isoformat()
    wa, err_a, meta_a = fetch_paged(cfg, token, ADS_PATH,
                            {"sid": sid, "start_date": start,
                             "end_date": (d1 + timedelta(days=1)).isoformat()})
    wb, err_b, meta_b = fetch_paged(cfg, token, ADS_PATH,
                            {"sid": sid, "start_date": start,
                             "end_date": d1.isoformat()})
    wc, err_c, meta_c = fetch_paged(cfg, token, ADS_PATH,
                            {"sid": sid, "start_date": start,
                             "end_date": (d1 - timedelta(days=1)).isoformat()})
    if err_a or err_b or err_c:
        return {"error": err_a or err_b or err_c, "pagination": {"A": meta_a, "B": meta_b, "C": meta_c}}

    def metrics(r):
        return (fnum(r.get("cost")), fnum(r.get("sales_amount")),
                fnum(r.get("order_num")), fnum(r.get("clicks")))

    b_map = {r.get("campaign_id"): metrics(r) for r in wb}
    c_map = {r.get("campaign_id"): metrics(r) for r in wc}
    dbef = {"spend": 0.0, "sales": 0.0, "orders": 0.0}
    for r in wb:   # 前日 = B窗口 − C窗口(同起点差值,与"昨日"同法,完整口径)
        c, s, o, _k = metrics(r)
        cc, cs, co, _ = c_map.get(r.get("campaign_id"), (0.0, 0.0, 0.0, 0.0))
        dbef["spend"] += c - cc
        dbef["sales"] += s - cs
        dbef["orders"] += o - co
    w7 = {"spend": 0.0, "sales": 0.0, "orders": 0.0}
    yday = {"spend": 0.0, "sales": 0.0, "orders": 0.0}
    burn_no_order, bad_status, y_movers = [], [], []
    for r in wa:
        c, s, o, k = metrics(r)
        name = r.get("campaign_name") or r.get("name")
        st = str(r.get("serving_status") or "")
        w7["spend"] += c
        w7["sales"] += s
        w7["orders"] += o
        bc, bs, bo, _ = b_map.get(r.get("campaign_id"), (0.0, 0.0, 0.0, 0.0))
        dc, ds, do = c - bc, s - bs, o - bo
        yday["spend"] += dc
        yday["sales"] += ds
        yday["orders"] += do
        cid = r.get("campaign_id")
        if dc > 0.5:
            y_movers.append({"campaign": name, "campaign_id": cid, "y_spend": round(dc, 2),
                             "y_orders": do})
        if (r.get("state") == "enabled" and any(b in st for b in BAD_STATUS)
                and c >= BAD_STATUS_MIN_SPEND):   # 7天没花钱的僵尸活动不报(降噪)
            bad_status.append({"campaign": name, "campaign_id": cid, "serving_status": st,
                               "w7_spend": round(c, 2)})
        if c >= BURN_MIN_SPEND and o == 0:
            burn_no_order.append({"campaign": name, "campaign_id": cid, "w7_spend": round(c, 2),
                                  "w7_clicks": k, "w7_orders": 0})
    burn_no_order.sort(key=lambda x: -x["w7_spend"])
    y_movers.sort(key=lambda x: -x["y_spend"])
    for d in (w7, yday, dbef):
        d["spend"] = round(d["spend"], 2)
        d["sales"] = round(d["sales"], 2)
        d["acos"] = round(d["spend"] / d["sales"], 4) if d["sales"] else None
    return {"window7": w7, "yesterday": yday, "day_before": dbef,
            "yesterday_note": "昨日=同起点双窗口差值(end_date按领星开区间口径已+1)，标记 derived；"
                              "day_before=前日完整口径(供美国站早卡使用，北京早8点美国昨日尚未收盘)",
            "derived": True,
            "pagination": {"A": meta_a, "B": meta_b, "C": meta_c},
            "y_top_spenders": y_movers[:8],
            "burn_no_order_7d": burn_no_order[:10],
            "bad_status": bad_status[:10]}


_FAM_WARNED = False


def load_patrol_families(sid):
    """从巡检配置 v2 读该店铺家族结构：{parent: {nickname, children:{asin:{nickname,floor_price}}}}

    配置缺失时返回 {} 并提示一次——家族级红线整块跳过是「功能未启用」，不是「今天没问题」，
    静默降级会让人误以为已经检查过了(纪律：缺什么如实说)。"""
    global _FAM_WARNED
    try:
        import yaml
        cfg = yaml.safe_load(PATROL_CFG.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if not _FAM_WARNED:
            print("[提示] 未找到家族配置 %s\n"
                  "       本次跳过家族级红线：跌破红线 / 价格异动 / 星级下跌 / 疑集中差评。\n"
                  "       一句话生成：python scripts/make_patrol_config.py（领星在售 Listing 按父体分组 + 成本表保本售价，写到用户目录，可手改）。"
                  % PATROL_CFG, file=sys.stderr)
            _FAM_WARNED = True
        return {}
    except Exception as e:
        if not _FAM_WARNED:
            print("[提示] 家族配置读取失败(%s: %s)，本次跳过家族级红线。"
                  % (e.__class__.__name__, e), file=sys.stderr)
            _FAM_WARNED = True
        return {}
    for st in (cfg or {}).get("stores") or []:
        if st.get("sid") == sid:
            return {f["parent"]: {"nickname": f.get("nickname") or f["parent"],
                                  "children": {c["asin"]: c for c in f.get("children") or []}}
                    for f in st.get("families") or []}
    return {}


def aggregate_listing(rows):
    """按 ASIN 聚合多 SKU 行（价格取最高在售价，库存/销量累加，评论取最大）"""
    agg = {}
    for r in rows:
        if int(fnum(r.get("is_delete"))) == 1 or not r.get("asin"):
            continue
        a = agg.setdefault(r["asin"], {"asin": r["asin"], "parent": r.get("parent_asin") or r["asin"],
                                       "price": 0.0, "star": None, "reviews": 0,
                                       "y_units": 0, "d30_units": 0, "fba": 0, "name": ""})
        a["price"] = max(a["price"], fnum(r.get("price")) or fnum(r.get("listing_price")))
        st = fnum(r.get("last_star"), 0)
        if st:
            a["star"] = st
        a["reviews"] = max(a["reviews"], int(fnum(r.get("review_num"))))
        a["y_units"] += int(fnum(r.get("yesterday_volume")))
        a["d30_units"] += int(fnum(r.get("thirty_volume")))
        a["fba"] += int(fnum(r.get("afn_fulfillable_quantity")))
        if not a["name"]:
            a["name"] = (r.get("local_name") or r.get("item_name") or "")[:40]
    return agg


def find_prev_listing(sid):
    """向前找最近一份 listing 原始快照作 diff 基线（最多回看 7 天）"""
    for back in range(1, 8):
        d = (date.today() - timedelta(days=back)).isoformat()
        f = RAW_ROOT / d / f"listing_raw_sid{sid}.json"
        if f.exists():
            return f, d
    return None, None


def family_pulse(cfg, token, sid, raw_dir):
    """家族脉搏：listing 按 站点→父体→子体 组织 + 与上一基线 diff + 子体级红线。
    件数口径：listing 的 yesterday_volume（领星同步时点略滞后于日销金额，如实标注）。"""
    rows, err, meta = fetch_paged(cfg, token, LISTING_PATH, {"sid": sid})
    if err:
        return {"error": err, "pagination": meta}
    (raw_dir / f"listing_raw_sid{sid}.json").write_text(
        json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    cur = aggregate_listing(rows)
    prev_f, prev_day = find_prev_listing(sid)
    prev = {}
    if prev_f:
        try:
            prev = aggregate_listing(json.loads(prev_f.read_text(encoding="utf-8")))
        except Exception:
            prev, prev_day = {}, None

    fam_cfg = load_patrol_families(sid)
    groups = {}
    for asin, a in cur.items():
        parent = next((p for p, f in fam_cfg.items() if asin in f["children"]), a["parent"])
        groups.setdefault(parent, []).append(a)

    families, flags_all = [], []
    for parent, members in groups.items():
        fcfg = fam_cfg.get(parent) or {}
        fnick = fcfg.get("nickname") or parent
        kids = []
        for a in sorted(members, key=lambda x: -x["d30_units"]):
            ccfg = (fcfg.get("children") or {}).get(a["asin"]) or {}
            nick = ccfg.get("nickname") or a["name"] or a["asin"]
            p = prev.get(a["asin"]) or {}
            child = {"asin": a["asin"], "nickname": nick, "price": a["price"],
                     "star": a["star"], "reviews": a["reviews"],
                     "y_units": a["y_units"], "d30_units": a["d30_units"], "fba": a["fba"]}
            if p:
                child["delta"] = {
                    "price": round(a["price"] - fnum(p.get("price")), 2),
                    "star": round(a["star"] - p["star"], 1) if a["star"] and p.get("star") else None,
                    "reviews": a["reviews"] - int(fnum(p.get("reviews"))),
                    "d30_units": a["d30_units"] - int(fnum(p.get("d30_units"))),
                    "fba": a["fba"] - int(fnum(p.get("fba")))}
            fl = []
            floor = fnum(ccfg.get("floor_price"), 0)
            if floor and 0 < a["price"] < floor:
                fl.append(f"价格 {a['price']:.2f} 跌破红线 {floor:.2f}")
            pp = fnum(p.get("price"), 0)
            if pp and a["price"] and abs(a["price"] - pp) / pp > float(RULES["family"]["price_change_pct"]):
                fl.append(f"价格异动 {pp:.2f}→{a['price']:.2f}")
            if a["star"] and p.get("star") and p["star"] - a["star"] >= float(RULES["family"]["star_drop"]):
                fl.append(f"星级下跌 {p['star']}→{a['star']}")
            rdiff = a["reviews"] - int(fnum(p.get("reviews"))) if p else 0
            if (p and rdiff >= int(RULES["family"]["review_jump"])
                    and a["star"] and p.get("star") and a["star"] <= p["star"]):
                fl.append(f"评论 +{rdiff} 且星级未升（疑集中差评）")
            if fl:
                child["red_flags"] = fl
                flags_all.append({"family": fnick, "asin": a["asin"],
                                  "nickname": nick, "flags": fl})
            kids.append(child)
        families.append({"parent": parent, "nickname": fnick,
                         "y_units": sum(k["y_units"] for k in kids),
                         "d30_units": sum(k["d30_units"] for k in kids),
                         "children": kids})
    families.sort(key=lambda f: -f["d30_units"])
    return {"baseline_date": prev_day,
            "baseline_note": ("Δ/红线基于 %s 基线对比" % prev_day) if prev_day else "无历史基线，今日建基线",
            "y_units_total": sum(f["y_units"] for f in families),
            "units_note": "件数=listing yesterday_volume（领星同步口径，时点略滞后于日销金额，不得与日销金额强行合成同日）",
            "pagination": meta,
            "families": families, "red_flags": flags_all}


def build_findings(out):
    """把三段脉搏的红线转成统一 finding 协议对象(40号 P1)"""
    R = RULES
    fl = []
    for e in out["stores"]:
        nick = e["nickname"]
        sales, ads, fam = e.get("sales") or {}, e.get("ads") or {}, e.get("family") or {}
        cur = ((sales.get("yesterday") or {}).get("currency")
               or (sales.get("day_before") or {}).get("currency") or "")
        yd, db = sales.get("yesterday") or {}, sales.get("day_before") or {}
        ya, da = yd.get("total_amount"), db.get("total_amount")
        if (ya is not None and da and da >= R["sales"]["min_amount_base"]
                and yd.get("asin_count")):
            drop = (da - ya) / da
            if drop >= R["sales"]["drop_pct_warn"]:
                lw = (sales.get("same_day_last_week") or {}).get("total_amount")
                fl.append(F.make_finding(
                    "sales", nick, f"{nick} 全店销售",
                    fact=f"昨日销售额 {ya} {cur}，环比前日 {da} 下跌 {drop*100:.0f}%",
                    status="warning",
                    reason_chain=[f"上周同日 {lw} {cur}",
                                  "贡献 ASIN 拆解待 Phase2；先看家族脉搏定位下跌款"],
                    impact=f"单日销售额减少 {da-ya:.2f} {cur}",
                    impact_amount=da - ya, currency=cur,
                    action="核对是否断货/改价/预算耗尽；对照家族脉搏找下跌 ASIN",
                    confidence="中", confidence_basis="单日环比，未排除周内节奏波动",
                    rule="sales_drop_dod", rules=R,
                    marketplace=e.get("marketplace") or "", store_key=nick))
        if "error" not in ads:
            y, w7 = ads.get("yesterday") or {}, ads.get("window7") or {}
            if (y.get("spend", 0) >= R["ads"]["min_spend_for_red"]
                    and (y.get("acos") or 0) > R["ads"]["acos_red"]):
                fl.append(F.make_finding(
                    "ads", nick, f"{nick} 广告账户(昨日)",
                    fact=f"昨日 ACOS {y['acos']*100:.0f}%（花费 {y['spend']} / 销售 {y['sales']} {cur}），"
                         f"超 {R['ads']['acos_red']*100:.0f}% 红线",
                    status="warning",
                    reason_chain=[f"昨日订单 {y.get('orders')}",
                                  f"近7天 ACOS {(w7.get('acos') or 0)*100:.0f}%",
                                  "Campaign 级贡献拆解待 Phase2（可先跑 ads_health_check.py）"],
                    impact=f"昨日广告花费 {y['spend']} {cur} 效率超线",
                    impact_amount=y.get("spend"), currency=cur,
                    action="跑 ads_health_check.py 定位超线活动 → queryWords 出否词/降bid",
                    confidence="高" if y.get("spend", 0) >= 20 else "中",
                    confidence_basis=f"昨日花费样本 {y.get('spend')} {cur}",
                    rule="ads_acos_over", rules=R,
                    marketplace=e.get("marketplace") or "", store_key=nick))
            for c in (ads.get("burn_no_order_7d") or []):
                clicks = c.get("w7_clicks", 0) or 0
                thin = clicks < 10
                fl.append(F.make_finding(
                    "ads", nick, c["campaign"],
                    fact=f"近7天花费 {c['w7_spend']} {cur}、{clicks:.0f} 点击、0 订单",
                    status="watch" if thin else "warning",
                    reason_chain=["样本较少，先当信号" if thin else "点击有量但零转化",
                                  "未定位搜索词前不得暂停",
                                  "词级定位待补采集"],
                    impact=f"近7天待观察花费 {c['w7_spend']} {cur}",
                    impact_amount=c["w7_spend"], currency=cur,
                    action=("继续观察并补 Campaign/搜索词原生 ID，禁止因薄样本暂停"
                            if thin else f"先定位【{c['campaign']}】搜索词后再给收紧/否词候选"),
                    confidence="低" if thin else "中",
                    confidence_basis=f"近7天 {clicks:.0f} 次点击",
                    rule="ads_burn_no_order", rules=R,
                    marketplace=e.get("marketplace") or "",
                    native_id=c.get("campaign_id") or "",
                    store_key=nick))
            for b in (ads.get("bad_status") or []):
                oob = "OUT_OF_BUDGET" in b["serving_status"]
                fl.append(F.make_finding(
                    "ads", nick, b["campaign"],
                    fact=f"enabled 但 serving_status={b['serving_status']}（近7天花费 {b['w7_spend']} {cur}）",
                    status="watch",
                    reason_chain=["预算耗尽=每天中途停跑、错失流量" if oob else "投放状态异常"],
                    impact="在跑活动被中途停投",
                    impact_amount=b["w7_spend"], currency=cur,
                    action=("提高预算或关停低效活动让位；ACCOUNT 级查后台账户预算上限"
                            if oob else "到广告后台核查该活动状态"),
                    confidence="高", confidence_basis="领星 serving_status 字段直读",
                    rule="ads_bad_status", rules=R,
                    marketplace=e.get("marketplace") or "",
                    native_id=b.get("campaign_id") or "", store_key=nick))
        for flg in (fam.get("red_flags") or []):
            amt = None
            for f2 in fam.get("families", []):
                for k in f2["children"]:
                    if k["asin"] == flg["asin"]:
                        amt = (round((k.get("y_units") or 0) * (k.get("price") or 0), 2)
                               or round((k.get("d30_units") or 0) * (k.get("price") or 0) / 30, 2))
            fl.append(F.make_finding(
                "product", nick, f"{flg['family']} / {flg['nickname']}（{flg['asin']}）",
                fact="；".join(flg["flags"]), status="warning",
                reason_chain=[fam.get("baseline_note", "")],
                impact=f"该子体日销约 {amt} {cur}" if amt else "低销子体",
                impact_amount=amt, currency=cur,
                action="前台核查评论/价格原文；必要时派 listing 做差评复盘",
                confidence="中", confidence_basis="listing 字段对比基线，未读差评原文",
                rule="family_flag", rules=R,
                marketplace=e.get("marketplace") or "",
                native_id=flg.get("asin") or "", store_key=nick))
    return fl


def _merge_patrol_json(raw_dir, out, marketplace):
    """分站点写入 daily_patrol_data_{UK|US}.json，再按 sid 合并进当日总文件。"""
    combined_path = raw_dir / "daily_patrol_data.json"
    if marketplace:
        mp_path = raw_dir / ("daily_patrol_data_%s.json" % marketplace)
        mp_path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        combined = {"generated_at": out.get("generated_at"), "pulse_days": {}, "stores": []}
        if combined_path.exists():
            try:
                combined = json.loads(combined_path.read_text(encoding="utf-8")) or combined
            except Exception:
                pass
        pulse_days = dict(combined.get("pulse_days") or {})
        pulse_days[marketplace] = out.get("pulse_day")
        by_sid = {e.get("sid"): e for e in (combined.get("stores") or []) if e.get("sid") is not None}
        for e in out.get("stores") or []:
            by_sid[e["sid"]] = e
        combined.update({
            "generated_at": date.today().isoformat(),
            "pulse_days": pulse_days,
            "stores": list(by_sid.values()),
        })
        combined_path.write_text(json.dumps(combined, ensure_ascii=False, indent=1), encoding="utf-8")
        return mp_path, combined_path
    combined_path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return combined_path, combined_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days-back", type=int, default=1,
                    help="以几天前为'昨日'（默认1；领星数据有延迟时可取2）")
    ap.add_argument("--marketplace", help="只拉 UK 或 US，供分站点定时任务使用")
    a = ap.parse_args()

    cfg = client.load_config()
    mp = client.normalize_marketplace(a.marketplace) if a.marketplace else None
    stores = client.patrol_stores(mp)
    if not stores:
        if mp:
            sys.exit("lingxing_config.yaml 里没有 marketplace=%s 且 patrol=true 的店铺" % mp)
        sys.exit("lingxing_config.yaml 的 stores 里没有 patrol=true 的店铺")
    token = client.get_token(cfg)

    if mp:
        d1 = client.closed_pulse_day(mp, days_back=a.days_back)  # 站点当地昨天
    else:
        d1 = date.today() - timedelta(days=a.days_back)
    d2 = d1 - timedelta(days=1)
    d7 = d1 - timedelta(days=7)

    raw_dir = RAW_ROOT / date.today().isoformat()
    raw_dir.mkdir(parents=True, exist_ok=True)

    collected = datetime.now(timezone.utc).isoformat()
    out = {"generated_at": date.today().isoformat(),
           "business_date": d1.isoformat(),
           "pulse_day": d1.isoformat(),
           "collected_at": collected,
           "report_generated_at": collected,
           "source_as_of": d1.isoformat(),
           "marketplace": mp, "timezone": client.marketplace_tz_name(mp) if mp else None,
           "stores": []}
    for s in stores:
        sid, nick = s["sid"], s.get("nickname") or str(s["sid"])
        print(f"[..] {nick} (sid={sid}) 拉取中")
        out["stores"].append({
            "sid": sid, "nickname": nick,
            "marketplace": client.normalize_marketplace(s.get("marketplace")) or mp,
            "sales": {"yesterday": sales_of_day(cfg, token, sid, d1),
                      "day_before": sales_of_day(cfg, token, sid, d2),
                      "same_day_last_week": sales_of_day(cfg, token, sid, d7)},
            "ads": ads_pulse(cfg, token, sid, d1),
            "family": family_pulse(cfg, token, sid, raw_dir),
            "orders": orders_by_asin(cfg, token, sid, d1),
            "fba": fba_by_asin(cfg, token, sid)})

    out_path, combined_path = _merge_patrol_json(raw_dir, out, mp)

    fnd = build_findings(out)
    nicks = [e["nickname"] for e in out["stores"]]
    fpath = F.save_findings(client.report_root(), ("sales", "ads", "product"), fnd,
                            replace_stores=nicks if mp else None)
    n_p1 = sum(1 for x in fnd if x["priority"] == "P1")
    n_p2 = sum(1 for x in fnd if x["priority"] == "P2")
    print(f"[FINDINGS] 本脚本产出 {len(fnd)} 条（P1 {n_p1} / P2 {n_p2}）-> {fpath}")

    # 紧凑汇总（Codex 直接读这段写日报）
    print("\n==== 巡检脉搏 %s%s ====" % (
        d1.isoformat(),
        (" · " + client.marketplace_label(mp)) if mp else ""))
    for e in out["stores"]:
        sy, sb, sw = (e["sales"][k] for k in
                      ("yesterday", "day_before", "same_day_last_week"))
        ad = e["ads"]
        print(f"[店铺] {e['nickname']}")
        if "error" in sy:
            print("  销售额: 数据不可用(%s) —— 如实标注，不编造" % sy["error"])
        else:
            print("  销售额: 昨日 %s %s（前日 %s，上周同日 %s）· 动销ASIN %d 个"
                  % (sy["total_amount"], sy.get("currency") or "",
                     sb.get("total_amount", "?"), sw.get("total_amount", "?"),
                     sy["asin_count"]))
        if "error" in ad:
            print("  广告: 数据不可用(%s)" % ad["error"])
        else:
            y, w = ad["yesterday"], ad["window7"]
            fa = lambda v: ("%.0f%%" % (v * 100)) if v else "-"
            sym = {"USD": "$", "GBP": "£", "EUR": "€", "JPY": "¥", "CNY": "¥"}.get(sy.get("currency") or "", "")
            print("  广告昨日: 花费 %s%s 销售 %s%s 订单 %s ACOS %s"
                  % (sym, y["spend"], sym, y["sales"], y["orders"], fa(y["acos"])))
            print("  广告近7天: 花费 %s%s 销售 %s%s 订单 %s ACOS %s"
                  % (sym, w["spend"], sym, w["sales"], w["orders"], fa(w["acos"])))
            for b in ad["burn_no_order_7d"][:3]:
                print("    红线-近7天烧钱无单: %s %s%s" % (b["campaign"], sym, b["w7_spend"]))
            for b in ad["bad_status"][:3]:
                print("    红线-投放异常: %s %s" % (b["campaign"], b["serving_status"]))
        fp = e.get("family") or {}
        if "error" in fp:
            print("  家族脉搏: 数据不可用(%s)" % fp["error"])
        elif fp:
            print("  家族脉搏: 昨日售出 %d 件 · %d 个家族 · %s"
                  % (fp["y_units_total"], len(fp["families"]), fp["baseline_note"]))
            for f in fp["families"][:5]:
                print("    %s: 昨日 %d 件 / 30天 %d 件" % (f["nickname"], f["y_units"], f["d30_units"]))
            for fl in fp["red_flags"][:5]:
                print("    红线-子体: [%s] %s(%s) %s"
                      % (fl["family"], fl["nickname"], fl["asin"], "; ".join(fl["flags"])))
        od = e.get("orders") or {}
        if "error" in od:
            print("  订单: 数据不可用(%s)" % od["error"])
        else:
            print("  订单: %d 个 ASIN · 原始 %d 行 · 取消 %d"
                  % (len(od.get("by_asin") or {}), od.get("order_rows") or 0, od.get("canceled") or 0))
        fb = e.get("fba") or {}
        if "error" in fb:
            print("  FBA: 数据不可用(%s)" % fb["error"])
        else:
            print("  FBA: %d 个 ASIN · SKU %d 行"
                  % (len(fb.get("by_asin") or {}), fb.get("sku_rows") or 0))
    print("\n[OUT]", out_path)
    if combined_path != out_path:
        print("[OUT]", combined_path)


if __name__ == "__main__":
    main()
