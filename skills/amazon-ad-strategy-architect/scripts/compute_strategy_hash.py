#!/usr/bin/env python3
from __future__ import annotations

import argparse

from ad_strategy_lib import compute_strategy_hash, dump_data, load_data


def main() -> int:
    parser = argparse.ArgumentParser(description="Compute or verify ad_strategy_brief SHA-256")
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--write", action="store_true", help="Write the computed hash back to the file")
    args = parser.parse_args()
    strategy = load_data(args.strategy)
    stored = strategy.get("strategy_hash")
    computed = compute_strategy_hash(strategy)
    print(f"stored={stored}")
    print(f"computed={computed}")
    print(f"matches={stored == computed}")
    if args.write:
        strategy["strategy_hash"] = computed
        dump_data(strategy, args.strategy)
        print("written=true")
        return 0
    return 0 if stored == computed else 2


if __name__ == "__main__":
    raise SystemExit(main())

