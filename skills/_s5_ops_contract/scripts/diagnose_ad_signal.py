# -*- coding: utf-8 -*-
"""把领星广告脉搏转成一条可审核诊断包。不改后台，不把薄样本写成暂停令。"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

THIN_CLICKS = 10


def _num(value, default=None):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def pick_signal(payload: dict) -> dict | None:
    for store in payload.get("stores") or []:
        ads = store.get("ads") or {}
        marketplace = store.get("marketplace") or payload.get("marketplace") or ""
        for row in ads.get("burn_no_order_7d") or []:
            return {
                "store_key": store.get("nickname") or str(store.get("sid") or ""),
                "sid_present": bool(store.get("sid")),
                "marketplace": marketplace,
                "campaign_name": row.get("campaign") or row.get("campaign_name"),
                "campaign_id": row.get("campaign_id"),
                "w7_spend": _num(row.get("w7_spend")),
                "w7_clicks": _num(row.get("w7_clicks"), 0) or 0,
                "w7_orders": _num(row.get("w7_orders"), 0) or 0,
                "pagination": ads.get("pagination") or {},
            }
    return None


def diagnose(payload: dict) -> dict:
    signal = pick_signal(payload)
    generated = datetime.now(timezone.utc).isoformat()
    if signal is None:
        return {
            "schema_version": "1.0",
            "artifact_type": "s5_ad_work_item",
            "state": "no_signal",
            "generated_at": generated,
            "business_date": payload.get("pulse_day") or payload.get("business_date"),
            "action": None,
            "note": "本批数据没有花费无单信号。不要编造异常。",
        }
    clicks = signal["w7_clicks"]
    native_id = signal["campaign_id"]
    if not native_id:
        state = "needs_data"
        action = None
        allowed = ["补采集 Campaign 原生 ID", "人工到广告后台定位", "继续观察"]
        kb_query = None
    elif clicks < THIN_CLICKS:
        state = "signal_observe"
        action = None
        allowed = ["保持", "观察", "补搜索词层再判断"]
        kb_query = "花费无单样本不足"
    else:
        state = "proposal_ready"
        action = None
        allowed = ["保持", "观察", "收紧出价候选", "否词候选"]
        kb_query = "高点击低转化"
    return {
        "schema_version": "1.0",
        "artifact_type": "s5_ad_work_item",
        "state": state,
        "generated_at": generated,
        "business_date": payload.get("pulse_day") or payload.get("business_date"),
        "entity": {
            "store_key": signal["store_key"],
            "marketplace": signal["marketplace"],
            "entity_type": "campaign",
            "native_id": native_id,
            "name": signal["campaign_name"],
        },
        "facts": {
            "w7_spend": signal["w7_spend"],
            "w7_clicks": clicks,
            "w7_orders": signal["w7_orders"],
            "sample": "thin" if clicks < THIN_CLICKS else "enough_for_tree",
            "pagination": signal["pagination"],
        },
        "diagnosis": {
            "primary": "有花费但尚无归因订单" if native_id else "对象未定位",
            "alternatives": ["归因未成熟", "搜索词不相关", "详情页或库存问题", "样本本身不够"],
            "kb_query": kb_query,
            "kb_note": "先抽短症状再 kb_diagnose。知识卡不是本店已经被证实。",
        },
        "proposed_actions": allowed,
        "action": action,
        "forbidden": ["pause_without_id", "call_waste_on_thin_sample", "write_ads_api"],
        "approval_status": "pending approval" if state == "proposal_ready" else "not_applicable",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="广告异常诊断包")
    ap.add_argument("--patrol-json", required=True)
    ap.add_argument("--out")
    args = ap.parse_args()
    payload = json.loads(Path(args.patrol_json).read_text(encoding="utf-8"))
    item = diagnose(payload)
    text = json.dumps(item, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
