#!/usr/bin/env python3
"""Read-only regression checks for the 1688 shop-to-offer handoff contract."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


OFFER_URL = re.compile(r"^https://detail\.1688\.com/offer/\d+\.html(?:[?#].*)?$")


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def require_contains(text: str, needle: str, label: str) -> None:
    if needle not in text:
        fail(f"{label} is missing required contract text: {needle}")


def yaml_scalar(text: str, key: str) -> str:
    match = re.search(rf"(?m)^\s*{re.escape(key)}:\s*[\"']?([^\"'\n#]+)", text)
    return match.group(1).strip() if match else ""


def verify_skill(skill_dir: Path) -> None:
    skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    state_text = (skill_dir / "references" / "workflow_state.example.yaml").read_text(
        encoding="utf-8"
    )
    for needle in (
        "https://detail.1688.com/offer/{数字}.html",
        "waiting_1688_verification",
        "waiting_1688_offer_url",
        "ready_for_product_scrape",
        "不得从店铺 URL、标题或搜索卡片拼接商品 ID",
    ):
        require_contains(skill_text, needle, "SKILL.md")
    for needle in (
        "supplier_shortlist_status:",
        "supplier_handoff:",
        "selected_offer_url:",
        "selected_offer_id:",
        "source_shop_url:",
        "discovery_keywords:",
        "browser_context:",
        "verification_status:",
    ):
        require_contains(state_text, needle, "workflow_state example")


def verify_runtime_state(state_path: Path) -> None:
    text = state_path.read_text(encoding="utf-8")
    handoff_match = re.search(
        r"(?ms)^supplier_handoff:\n(?P<body>.*?)(?=^[A-Za-z_][A-Za-z0-9_]*:\s|\Z)", text
    )
    if not handoff_match:
        fail(f"{state_path} has no supplier_handoff section")
    handoff = handoff_match.group("body")
    status = yaml_scalar(handoff, "status")
    selected_url = yaml_scalar(handoff, "selected_offer_url")
    selected_id = yaml_scalar(handoff, "selected_offer_id")
    current_step = yaml_scalar(text, "current_step")
    blocked_reason = yaml_scalar(text, "blocked_reason")

    if status == "ready_for_product_scrape":
        if not OFFER_URL.fullmatch(selected_url):
            fail("ready_for_product_scrape requires a valid selected_offer_url")
        offer_id = re.search(r"/offer/(\d+)\.html", selected_url)
        if not offer_id or selected_id != offer_id.group(1):
            fail("selected_offer_id must match selected_offer_url")
    elif status == "waiting_1688_verification":
        if blocked_reason != "waiting_1688_verification":
            fail("verification wait must preserve matching blocked_reason")
        if not yaml_scalar(handoff, "source_shop_url"):
            fail("verification wait must preserve source_shop_url")
    elif status == "waiting_1688_offer_url":
        if blocked_reason != "waiting_1688_offer_url":
            fail("offer wait must preserve matching blocked_reason")

    if current_step == "product_scrape" and status != "ready_for_product_scrape":
        fail("product_scrape cannot start without ready_for_product_scrape handoff")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, help="Optional workflow_state.yaml to validate")
    args = parser.parse_args()
    skill_dir = Path(__file__).resolve().parents[1]
    verify_skill(skill_dir)
    if args.state:
        verify_runtime_state(args.state)
    print("PASS: supplier handoff contract is consistent")


if __name__ == "__main__":
    main()
