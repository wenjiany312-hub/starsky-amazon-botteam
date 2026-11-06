#!/usr/bin/env python3
"""Compile an approved strategy brief and keyword package into ad_plan 2.0."""

from __future__ import annotations

import argparse
from pathlib import Path

from ad_plan_lib import build_plan, dump_data, load_data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", required=True, help="ad_strategy_brief YAML/JSON")
    parser.add_argument("--keywords", required=True, help="keyword_package YAML/JSON")
    parser.add_argument("--output-dir", required=True, help="Directory for plan/gate/handoff")
    parser.add_argument("--prior-plan", help="Optional prior ad_plan for a versioned revision")
    parser.add_argument(
        "--strategy-review",
        help="Strategy review gate approving the exact strategy_id/version/hash; required by 2.0 inputs",
    )
    parser.add_argument(
        "--case-defaults",
        default=str(Path(__file__).resolve().parents[1] / "references" / "case_defaults.yaml"),
        help="Governed case defaults YAML/JSON",
    )
    parser.add_argument("--format", choices=("yaml", "json"), default="yaml")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    strategy = load_data(args.strategy)
    keywords = load_data(args.keywords)
    defaults = load_data(args.case_defaults)
    prior = load_data(args.prior_plan) if args.prior_plan else None
    strategy_review = load_data(args.strategy_review) if args.strategy_review else None
    plan, review_gate, handoff, diagnostics = build_plan(
        strategy, keywords, defaults, prior, strategy_review
    )
    suffix = ".json" if args.format == "json" else ".yaml"
    output = Path(args.output_dir)
    dump_data(plan, output / f"ad_plan{suffix}")
    dump_data(review_gate, output / f"architecture_review{suffix}")
    dump_data(handoff, output / f"handoff{suffix}")
    dump_data(diagnostics, output / f"build_diagnostics{suffix}")
    print(f"plan_id={plan['plan_id']}")
    print(f"plan_version={plan['plan_version']}")
    print(f"plan_hash={plan['plan_hash']}")
    print(f"review_status={review_gate['status']}")
    print(f"blockers={len(diagnostics['blockers'])}")
    print(f"warnings={len(diagnostics['warnings'])}")
    # Demo must finish the compile path; its blocker applies to upload, not artifact creation.
    fatal_build_blockers = [
        item for item in diagnostics["blockers"] if item != "DEMO_ONLY_NOT_UPLOADABLE"
    ]
    return 0 if not fatal_build_blockers else 2


if __name__ == "__main__":
    raise SystemExit(main())
