#!/usr/bin/env python3
"""Compute or verify the canonical SHA-256 hash of an ad_plan 2.0 file."""

from __future__ import annotations

import argparse

from ad_plan_lib import compute_plan_hash, dump_data, load_data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, help="ad_plan YAML/JSON")
    parser.add_argument("--write", action="store_true", help="Write the computed hash back to the plan")
    parser.add_argument("--verify", action="store_true", help="Exit non-zero when stored and computed hashes differ")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    plan = load_data(args.plan)
    stored = plan.get("plan_hash")
    computed = compute_plan_hash(plan)
    print(f"stored={stored}")
    print(f"computed={computed}")
    if args.write:
        plan["plan_hash"] = computed
        dump_data(plan, args.plan)
    if args.verify and stored != computed:
        print("status=PLAN_HASH_MISMATCH")
        return 2
    print("status=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

