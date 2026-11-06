#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from ad_strategy_lib import calculate_economics, dump_data, load_data


def main() -> int:
    parser = argparse.ArgumentParser(description="Calculate ACoS/CPA/CPC and effective-bid scenarios")
    parser.add_argument("--input", required=True, help="Economics input YAML/JSON")
    parser.add_argument("--output", required=True, help="Economics scenarios YAML/JSON")
    args = parser.parse_args()
    result = calculate_economics(load_data(args.input))
    dump_data(result, args.output)
    print(json.dumps({"status": "ok", "scenarios": len(result["cvr_scenarios"]), "output": args.output}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

