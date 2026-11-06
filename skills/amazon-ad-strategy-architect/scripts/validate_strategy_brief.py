#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from ad_strategy_lib import load_data, validate_strategy


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate ad_strategy_brief 2.0")
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    result = validate_strategy(load_data(args.strategy))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        from pathlib import Path
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

