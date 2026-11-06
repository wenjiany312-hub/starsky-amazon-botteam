# -*- coding: utf-8 -*-
"""广告/市场验证过的需求词 ↔ 现文案埋词缺口。不改后台，品牌词永不入文案。"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

TOKEN = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?", re.I)


def normalize(text: str) -> str:
    return " ".join(TOKEN.findall((text or "").lower()))


def tokens(text: str) -> set[str]:
    return set(TOKEN.findall((text or "").lower()))


def listing_blob(listing: dict) -> dict[str, str]:
    bullets = listing.get("bullets") or []
    if isinstance(bullets, str):
        bullets = [bullets]
    return {
        "title": listing.get("title") or "",
        "bullets": " ".join(str(x) for x in bullets),
        "search_terms": listing.get("search_terms") or listing.get("generic_keywords") or "",
    }


def is_blocked(term: str, blocklist: list[str]) -> bool:
    hay = tokens(term)
    for brand in blocklist or []:
        if tokens(brand) & hay:
            return True
    return False


def is_buried(term: str, fields: dict[str, str]) -> str | None:
    phrase = normalize(term)
    if not phrase:
        return None
    for name, text in fields.items():
        blob = normalize(text)
        if phrase and phrase in blob:
            return name
        needed = tokens(term)
        if needed and needed <= tokens(text):
            return name
    return None


def analyze(payload: dict) -> dict:
    listing = payload.get("listing") or {}
    fields = listing_blob(listing)
    blocklist = [str(x) for x in (payload.get("brand_blocklist") or []) if str(x).strip()]
    buried, missing, blocked = [], [], []
    for raw in payload.get("demand_words") or []:
        if isinstance(raw, str):
            row = {"term": raw, "source": "unspecified", "role": "demand"}
        else:
            row = dict(raw)
        term = str(row.get("term") or "").strip()
        if not term:
            continue
        item = {
            "term": term,
            "source": row.get("source") or "unspecified",
            "role": row.get("role") or "demand",
        }
        if is_blocked(term, blocklist) or row.get("role") == "brand_block":
            item["verdict"] = "ads_only"
            item["note"] = "品牌词或变体只能投广告，不入标题/五点/ST。"
            blocked.append(item)
            continue
        place = is_buried(term, fields)
        if place:
            item["verdict"] = "buried"
            item["field"] = place
            buried.append(item)
        else:
            item["verdict"] = "missing"
            item["field"] = None
            missing.append(item)
    actions = []
    for item in missing:
        already = normalize(fields["title"] + " " + fields["bullets"])
        target = "search_terms" if normalize(item["term"]) in already else "bullets"
        if target == "search_terms" and normalize(item["term"]) in normalize(fields["search_terms"]):
            continue
        actions.append({
            "action_id": "L" + str(len(actions) + 1),
            "object": target,
            "term": item["term"],
            "current": "absent",
            "proposed": "bury_in_" + target,
            "title_change": False,
            "approval_status": "pending approval",
            "basis": "广告或市场已验证的需求词，文案全缺。",
        })
    return {
        "schema_version": "1.0",
        "artifact_type": "s5_listing_keyword_gap",
        "variant": listing.get("variant") or listing.get("sku") or "",
        "discipline": {
            "title_untouched": True,
            "one_variant_only": True,
            "wait_days_after_change": 7,
            "no_repeat_in_search_terms": True,
            "brand_never_in_copy": True,
        },
        "counts": {
            "demand": len(buried) + len(missing) + len(blocked),
            "buried": len(buried),
            "missing": len(missing),
            "blocked": len(blocked),
        },
        "buried": buried,
        "missing": missing,
        "blocked": blocked,
        "listing_actions": actions,
        "write_listing_api": False,
        "note": "只出改稿候选，不自动改 Listing：由用户到后台手动改。",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="文案埋词缺口")
    ap.add_argument("--input", required=True)
    ap.add_argument("--out")
    args = ap.parse_args()
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    result = analyze(payload)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
