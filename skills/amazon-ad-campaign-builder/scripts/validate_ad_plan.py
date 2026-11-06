#!/usr/bin/env python3
"""Validate ad_plan 2.0 structure, economics, transfer safety, and artifact hashes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ad_plan_lib import load_data, validate_plan


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, help="ad_plan YAML/JSON")
    parser.add_argument(
        "--architecture-review", "--review-gate", dest="architecture_review",
        help="Optional flat architecture_review YAML/JSON; --review-gate is a legacy alias",
    )
    parser.add_argument("--handoff", help="Optional handoff YAML/JSON")
    parser.add_argument(
        "--schema",
        default=str(Path(__file__).resolve().parents[1] / "templates" / "ad_plan.schema.json"),
        help="JSON Schema 2020-12 file",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable result")
    return parser.parse_args()


def optional_jsonschema_check(plan: dict, schema_path: str) -> list[str]:
    try:
        import jsonschema  # type: ignore
    except ImportError:
        return []
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    return [f"SCHEMA:{error.json_path}:{error.message}" for error in sorted(validator.iter_errors(plan), key=lambda item: list(item.path))]


def main() -> int:
    args = parse_args()
    plan = load_data(args.plan)
    review_gate = load_data(args.architecture_review) if args.architecture_review else None
    handoff = load_data(args.handoff) if args.handoff else None
    result = validate_plan(plan, review_gate, handoff)
    result["errors"] = optional_jsonschema_check(plan, args.schema) + result["errors"]
    result["valid"] = not result["errors"]
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"valid={str(result['valid']).lower()}")
        for error in result["errors"]:
            print(f"ERROR {error}")
        for warning in result["warnings"]:
            print(f"WARN {warning}")
    return 0 if result["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
