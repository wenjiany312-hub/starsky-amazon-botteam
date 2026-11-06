# -*- coding: utf-8 -*-
"""产品复盘半自动：经济账 + 广告建议 + 文案缺口。全部待审批，不写 Ads API。"""
from __future__ import annotations

import argparse
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path

import listing_keyword_gap as gap

THIN_CLICKS = 10
UP_DOWN = {"up_down", "auto_for_sales", "autoforsales", "提高和降低", "dynamic_up_down"}


def _num(value, default=None):
    try:
        if value is None or value == "":
            return default
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _value(row, *keys):
    """A supplied zero/null is authoritative; never borrow another window."""
    return next((row[k] for k in keys if k in row), None)


def economics(card: dict) -> dict:
    aov = _num(_value(card, "aov", "price"))
    target = _num(card.get("target_acos"))
    cvr_low = _num(_value(card, "cvr_low", "assumed_cvr"))
    cvr_high = _num(card.get("cvr_high", cvr_low))
    if (aov is None or aov <= 0 or target is None or not 0 < target <= 1
            or cvr_low is None or cvr_high is None or not 0 < cvr_low <= cvr_high <= 1):
        return {
            "state": "needs_card",
            "bid_ceiling_low": None,
            "bid_ceiling_high": None,
            "formula": "客单价 × 目标ACOS × CVR",
            "note": "需要正数客单价、0–1 的目标 ACOS 和用户明确的 CVR 范围；不填默认 CVR。",
        }
    low = round(aov * target * cvr_low, 2)
    high = round(aov * target * cvr_high, 2)
    return {
        "state": "ready",
        "aov": aov,
        "target_acos": target,
        "cvr_low": cvr_low,
        "cvr_high": cvr_high,
        "bid_ceiling_low": low,
        "bid_ceiling_high": high,
        "mid": round((low + high) / 2, 2),
        "formula": "客单价 × 目标ACOS × CVR",
        "cvr_basis": card.get("cvr_basis") or "user_supplied_assumption",
    }


def _acos(row: dict):
    spend = _num(_value(row, "spend", "w7_spend"))
    sales = _num(_value(row, "sales", "w7_sales"))
    if spend is None or spend < 0 or sales is None or sales <= 0:
        return None
    return spend / sales


def _ads_scope_complete(context):
    try:
        return (context.get("source") == "amazon_ads_api"
                and str(context.get("profile_id") or "").isdigit()
                and context.get("region") in {"NA", "EU", "FE"}
                and isinstance(context.get("currency"), str) and len(context["currency"]) == 3
                and bool(context.get("timezone"))
                and _num(context.get("attribution_days")) in {7, 14}
                and date.fromisoformat(context["start_date"]) <= date.fromisoformat(context["end_date"]))
    except (ValueError, TypeError, KeyError):
        return False


def classify_ad(row: dict, eco: dict, card: dict | None = None) -> dict:
    card = card or {}
    clicks = _num(_value(row, "clicks", "w7_clicks"))
    orders = _num(_value(row, "orders", "w7_orders"))
    spend = _num(_value(row, "spend", "w7_spend"))
    sales = _num(_value(row, "sales", "w7_sales"))
    bid = _num(_value(row, "bid", "default_bid"))
    role = str(row.get("role") or "unknown").lower()
    strategy = str(row.get("strategy") or "").lower()
    name = row.get("name") or row.get("campaign") or row.get("object") or ""
    layer = row.get("layer")
    id_field = {"campaigns": "campaign_id", "adgroups": "adgroup_id",
                "keywords": "keyword_id", "targets": "target_id"}.get(layer)
    native_id = row.get("native_id") or row.get(id_field) or row.get("id")
    acos = _acos(row)
    ceiling = eco.get("bid_ceiling_high")
    mid = eco.get("mid")
    base = {
        "object": name,
        "native_id": native_id,
        "layer": layer,
        "campaign_id": row.get("campaign_id"),
        "adgroup_id": row.get("adgroup_id"),
        "source_context": row.get("source_context") or {},
        "evidence": row.get("evidence") or [],
        "role": role,
        "current_bid": bid,
        "current_strategy": row.get("strategy"),
        "clicks": clicks,
        "orders": orders,
        "spend": spend,
        "sales": sales,
        "acos": acos,
        "spend_without_sales": spend is not None and spend > 0 and sales == 0,
        "approval_status": "pending approval",
    }
    def result(verdict, basis, proposed=None, reason_code=None):
        return {**base, "verdict": verdict, "proposed": proposed, "basis": basis,
                "reason_code": reason_code or verdict}

    if row.get("data_blocked"):
        return result("needs_data", "数据未完成、范围不一致或币种不同；补齐后再给动作。")
    if row.get("pending_action"):
        return result("watch", "同一对象已有待执行或观察中的动作，先核回执和观察窗口。",
                      reason_code="pending_action")
    if layer == "query_words":
        return result("watch", "搜索词是效果证据；转精准或否定需验证目的端，不直接修改搜索词出价。")
    if not native_id:
        return result("needs_data", "缺该广告层级的后台原生 ID，先补采集。")
    if any(v is None or v < 0 for v in (clicks, orders, spend, sales)):
        return result("needs_data", "点击、订单、花费或销售额缺失/无效，不能当作 0。")
    min_clicks = max(THIN_CLICKS, _num(card.get("min_clicks"), THIN_CLICKS))
    if clicks < min_clicks:
        return result("watch", "薄样本只观察，不写成纯浪费，不准暂停。")
    stop = card.get("stop_loss") or {}
    stop_acos = _num(_value(stop, "acos", "acos_over"))
    stop_days = _num(_value(stop, "consecutive_days", "days"))
    actual_days = _num(row.get("over_stop_loss_days"))
    # Compatibility with the earlier explicit >100% streak field, only for the same threshold.
    if actual_days is None and stop_acos == 1:
        actual_days = _num(row.get("over_100_days"))
    if (stop_acos is not None and stop_acos > 0 and stop_days is not None and stop_days >= 1
            and actual_days is not None and actual_days >= stop_days
            and acos is not None and acos > stop_acos):
        return result("stop_loss", "超过执行卡止损线且持续天数已达阈值；请审核防守范围和回滚条件。",
                      {"review_stop_loss": True, "acos_threshold": stop_acos, "consecutive_days": stop_days})
    if eco.get("state") != "ready":
        return result("needs_data", "缺完整经济账执行卡，只记录效果，补齐参数后评估出价。")
    under_target = acos is not None and acos <= eco["target_acos"]
    if role == "auto" and not under_target and ceiling and mid and bid and bid > ceiling and layer in {"adgroups", "targets", "keywords"}:
        return result("change", "自动投放效果未达到目标且出价超过所填 CVR 场景的天花板；供人工审核。", {"bid": mid})
    if (role == "auto" and not under_target and acos is not None and layer == "campaigns"
            and strategy.replace(" ", "") in {s.replace(" ", "") for s in UP_DOWN}):
        return result("change", "活动 ACOS 超过目标且使用提高和降低，可审核改为只降低；ACOS 不是净利润。",
                      {"strategy": "down_only"})
    return result("watch", "保留当前设置并继续观察；低出价不证明盈利，零销售不计算 ACOS。")


def review(payload: dict) -> dict:
    card = payload.get("card") or {}
    eco = economics(card)
    layers = payload.get("layers") or {}
    context = payload.get("source_context") or {}
    quality = payload.get("data_quality") or {}
    context_keys = ("source", "profile_id", "region", "currency", "timezone", "country", "attribution_days", "start_date", "end_date")
    row_contexts = [row.get("source_context") or {} for key in layers for row in layers.get(key) or []]
    known_contexts = ([context] if context else []) + row_contexts
    ads_declared = any(c.get("source") == "amazon_ads_api" for c in known_contexts)
    missing_ads_scope = (ads_declared and not _ads_scope_complete(context)
                         and not (row_contexts and all(_ads_scope_complete(c) for c in row_contexts)))
    mixed_batch = any(len({str(c[k]) for c in known_contexts if c.get(k) is not None}) > 1 for k in context_keys)
    if mixed_batch:
        quality = {**quality, "complete": False, "issues": [*(quality.get("issues") or []), "mixed_source_context"]}
    if missing_ads_scope:
        quality = {**quality, "complete": False, "issues": [*(quality.get("issues") or []), "ads_scope_incomplete"]}
    if row_contexts and not mixed_batch:
        context = {**context, **{k: next(c[k] for c in row_contexts if c.get(k) is not None)
                   for k in context_keys if context.get(k) is None and any(c.get(k) is not None for c in row_contexts)}}
    pending = payload.get("pending_actions") or []
    rows = []
    for key in ("campaigns", "adgroups", "keywords", "targets", "query_words"):
        for row in layers.get(key) or []:
            item = dict(row)
            item.setdefault("layer", key)
            row_context = item.get("source_context") or {}
            mixed = any(k in row_context and k in context and row_context[k] != context[k] for k in context_keys)
            item["source_context"] = {**context, **row_context}
            currency_mismatch = (card.get("currency") and item["source_context"].get("currency")
                                 and card["currency"] != item["source_context"]["currency"])
            item["data_blocked"] = (item.get("data_blocked", False) or quality.get("complete") is False
                                    or quality.get("truncated", False) or mixed or mixed_batch or bool(currency_mismatch))
            item["pending_action"] = any(
                str(p.get("native_id")) == str(item.get("native_id")) and p.get("layer") == key
                and (p.get("status") or p.get("approval_status")) in {"pending", "pending approval", "approved", "submitted", "observing"}
                and all((p.get("source_context") or {}).get(k) == item["source_context"].get(k)
                        for k in ("source", "profile_id", "region")) for p in pending)
            rows.append(item)
    ad_actions = [classify_ad(row, eco, card) for row in rows]
    listing = gap.analyze(payload) if (payload.get("listing") or payload.get("demand_words")) else None
    change = [a for a in ad_actions if a["verdict"] == "change"]
    watch = [a for a in ad_actions if a["verdict"] == "watch"]
    stop = [a for a in ad_actions if a["verdict"] == "stop_loss"]
    return {
        "schema_version": "1.1",
        "artifact_type": "s5_product_review",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "card": {
            "store": card.get("store"),
            "asin": card.get("asin"),
            "family": card.get("family"),
            "observe": card.get("observe") or {"fast": "72h", "attrib": "7d", "routine": "biweekly"},
        },
        "economics": eco,
        "source_context": context,
        "data_quality": quality,
        "layers_present": {k: len(layers.get(k) or []) for k in
                           ("campaigns", "adgroups", "keywords", "targets", "query_words")},
        "ad_actions": ad_actions,
        "branches": {
            "change": len(change),
            "watch": len(watch),
            "stop_loss": len(stop),
            "needs_data": sum(a["verdict"] == "needs_data" for a in ad_actions),
        },
        "listing_gap": listing,
        "approval_status": "pending approval",
        "forbidden": ["write_ads_api", "auto_patch_listing", "call_waste_on_thin_sample", "sum_across_attribution"],
        "next": "人批通过后才生成 Bulk 候选；文案只出改稿，不自动 PATCH。",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="S5 产品复盘半自动")
    ap.add_argument("--input", required=True)
    ap.add_argument("--out")
    args = ap.parse_args()
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    result = review(payload)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
