#!/usr/bin/env python3
"""Deterministic contracts shared by the Amazon SP Bulk generator.

The module deliberately does not call Amazon or mutate the source template.
It validates a row-operation plan, verifies its integrity/review gate, and
provides exact field mappings for the current-account template supplied by the
user.  Built-in mappings are adapters, not bundled production templates.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

import yaml

from campaign_compiler import compile_campaigns_to_operations


HASH_SCHEMA = "ad-plan-c14n-v1"
LEGACY_HASH_SCHEMA = "legacy-yaml-line-v1"
DEMO_MARKER = "DEMO_ONLY_NOT_UPLOADABLE"
ARCHITECTURE_REVIEW_CHECKS = (
    "identity_scope",
    "campaign_role_and_goal",
    "daily_budget_reconciliation",
    "profit_and_inventory_guardrails",
    "maximum_effective_bid",
    "placement_and_bidding",
    "targeting_source_traceability",
    "negative_relevance_and_res_per_safety",
    "sp_sb_sd_eligibility",
)
RELEASE_REVIEW_CHECKS = (
    "real_child_sku_asin",
    "marketplace_account_scope",
    "current_official_template",
    "budget_and_profit_authorized",
    "plan_bulk_counts_match",
    "unresolved_high_risk_actions",
)

SP_HEADERS = [
    "Product",
    "Entity",
    "Operation",
    "Campaign ID",
    "Ad Group ID",
    "Portfolio ID",
    "Ad ID",
    "Keyword ID",
    "Product Targeting ID",
    "Campaign Name",
    "Ad Group Name",
    "Start Date",
    "End Date",
    "Targeting Type",
    "State",
    "Daily Budget",
    "SKU",
    "Ad Group Default Bid",
    "Bid",
    "Keyword Text",
    "Native Language Keyword",
    "Native Language Locale",
    "Match Type",
    "Bidding Strategy",
    "Placement",
    "Percentage",
    "Product Targeting Expression",
    "Audience ID",
    "Shopper Cohort Percentage",
    "Shopper Cohort Type",
]

SP_COLUMN_MAP = {
    "product": "Product",
    "entity": "Entity",
    "operation": "Operation",
    "campaign_id": "Campaign ID",
    "ad_group_id": "Ad Group ID",
    "portfolio_id": "Portfolio ID",
    "ad_id": "Ad ID",
    "keyword_id": "Keyword ID",
    "product_targeting_id": "Product Targeting ID",
    "campaign_name": "Campaign Name",
    "ad_group_name": "Ad Group Name",
    "start_date": "Start Date",
    "end_date": "End Date",
    "targeting_type": "Targeting Type",
    "state": "State",
    "daily_budget": "Daily Budget",
    "sku": "SKU",
    "ad_group_default_bid": "Ad Group Default Bid",
    "bid": "Bid",
    "keyword_text": "Keyword Text",
    "native_language_keyword": "Native Language Keyword",
    "native_language_locale": "Native Language Locale",
    "match_type": "Match Type",
    "bidding_strategy": "Bidding Strategy",
    "placement": "Placement",
    "percentage": "Percentage",
    "product_targeting_expression": "Product Targeting Expression",
    "audience_id": "Audience ID",
    "shopper_cohort_percentage": "Shopper Cohort Percentage",
    "shopper_cohort_type": "Shopper Cohort Type",
}

SP_ENTITIES = {
    "Campaign",
    "Ad Group",
    "Bidding Adjustment",
    "Campaign Negative Keyword",
    "Keyword",
    "Negative Keyword",
    "Product Targeting",
    "Negative Product Targeting",
    "Product Ad",
}
OPERATIONS = {"Create", "Update", "Archive"}

CREATE_REQUIRED = {
    "Campaign": {
        "campaign_id",
        "campaign_name",
        "start_date",
        "targeting_type",
        "state",
        "daily_budget",
        "bidding_strategy",
    },
    "Ad Group": {
        "campaign_id",
        "ad_group_id",
        "campaign_name",
        "ad_group_name",
        "ad_group_default_bid",
        "state",
    },
    "Product Ad": {
        "campaign_id",
        "ad_group_id",
        "campaign_name",
        "ad_group_name",
        "sku",
        "state",
    },
    "Keyword": {
        "campaign_id",
        "ad_group_id",
        "campaign_name",
        "ad_group_name",
        "keyword_text",
        "match_type",
        "state",
    },
    "Negative Keyword": {
        "campaign_id",
        "ad_group_id",
        "campaign_name",
        "ad_group_name",
        "keyword_text",
        "match_type",
        "state",
    },
    "Campaign Negative Keyword": {
        "campaign_id",
        "campaign_name",
        "keyword_text",
        "match_type",
        "state",
    },
    "Product Targeting": {
        "campaign_id",
        "ad_group_id",
        "campaign_name",
        "ad_group_name",
        "product_targeting_expression",
        "state",
    },
    "Negative Product Targeting": {
        "campaign_id",
        "ad_group_id",
        "campaign_name",
        "ad_group_name",
        "product_targeting_expression",
        "state",
    },
    "Bidding Adjustment": {
        "campaign_id",
        "campaign_name",
        "placement",
    },
}

PRIMARY_ID = {
    "Campaign": "campaign_id",
    "Ad Group": "ad_group_id",
    "Product Ad": "ad_id",
    "Keyword": "keyword_id",
    "Negative Keyword": "keyword_id",
    "Campaign Negative Keyword": "keyword_id",
    "Product Targeting": "product_targeting_id",
    "Negative Product Targeting": "product_targeting_id",
    "Bidding Adjustment": "campaign_id",
}


class ContractError(ValueError):
    """A stable, user-facing contract failure."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ContractError("INVALID_YAML_ROOT", f"{path} 顶层必须是 mapping")
    return data


def dump_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120),
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = unicodedata.normalize("NFC", str(raw_key))
            if key in normalized:
                raise ContractError(
                    "CANONICAL_KEY_COLLISION",
                    f"multiple mapping keys normalize to {key!r}",
                )
            normalized[key] = _json_ready(raw_value)
        return normalized
    if isinstance(value, list):
        return [_json_ready(v) for v in value]
    if isinstance(value, tuple):
        return [_json_ready(v) for v in value]
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    return value


def review_scope_hash(review_gate: dict[str, Any]) -> str:
    top_level_review = (
        {
            key: value
            for key, value in review_gate.items()
            if key not in {"archive_review", "bulk_release_review"}
        }
        if review_gate.get("status") is not None
        and review_gate.get("plan_id") is not None
        and review_gate.get("plan_hash") is not None
        else None
    )
    payload = {
        "architecture_review": (
            top_level_review
            or review_gate.get("architecture_review")
            or review_gate.get("plan_review")
            or review_gate.get("plan")
        ),
        "archive_review": review_gate.get("archive_review"),
    }
    encoded = json.dumps(
        _json_ready(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def canonical_plan_payload(plan: dict[str, Any]) -> bytes:
    payload = copy.deepcopy(plan)
    payload.pop("plan_hash", None)
    meta = payload.get("_meta")
    if isinstance(meta, dict):
        meta.pop("generated_at", None)
    text = json.dumps(
        _json_ready(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return text.encode("utf-8")


def canonical_plan_hash(plan: dict[str, Any]) -> str:
    return f"sha256:{hashlib.sha256(canonical_plan_payload(plan)).hexdigest()}"


def legacy_plan_hash_text(raw_text: str) -> str:
    kept = [
        line
        for line in raw_text.splitlines(keepends=True)
        if not re.match(r"^\s*plan_hash\s*:", line)
    ]
    return f"sha256:{hashlib.sha256(''.join(kept).encode('utf-8')).hexdigest()}"


def get_stored_plan_hash(plan: dict[str, Any]) -> tuple[str, str | None]:
    integrity = plan.get("integrity") if isinstance(plan.get("integrity"), dict) else {}
    value = plan.get("plan_hash") or integrity.get("plan_hash")
    schema = plan.get("hash_schema") or integrity.get("hash_schema") or integrity.get("canonicalization")
    if not value:
        raise ContractError("PLAN_HASH_MISSING", "ad_plan 缺 plan_hash")
    value = str(value)
    if not value.startswith("sha256:"):
        value = f"sha256:{value}"
    return value.lower(), str(schema) if schema else None


def verify_plan_hash(plan: dict[str, Any], raw_text: str) -> tuple[str, list[str]]:
    stored, schema = get_stored_plan_hash(plan)
    warnings: list[str] = []
    if schema == HASH_SCHEMA:
        actual = canonical_plan_hash(plan)
    elif schema == LEGACY_HASH_SCHEMA:
        actual = legacy_plan_hash_text(raw_text)
        warnings.append("legacy_hash_accepted_migrate_on_next_semantic_revision")
    elif schema:
        raise ContractError("PLAN_HASH_SCHEMA_UNSUPPORTED", schema)
    else:
        canonical = canonical_plan_hash(plan)
        legacy = legacy_plan_hash_text(raw_text)
        if stored == canonical.lower():
            actual = canonical
            schema = HASH_SCHEMA
            warnings.append("hash_schema_inferred_canonical")
        elif stored == legacy.lower():
            actual = legacy
            schema = LEGACY_HASH_SCHEMA
            warnings.append("legacy_hash_inferred_migrate_on_next_semantic_revision")
        else:
            actual = canonical
    if stored != actual.lower():
        raise ContractError(
            "PLAN_HASH_MISMATCH", f"stored={stored} computed={actual.lower()} schema={schema or 'unknown'}"
        )
    return schema or HASH_SCHEMA, warnings


def extract_plan_ref(plan: dict[str, Any], hash_schema: str) -> dict[str, Any]:
    plan_id = str(plan.get("plan_id") or "").strip()
    version = plan.get("plan_version")
    stored, _ = get_stored_plan_hash(plan)
    if not plan_id or version in (None, ""):
        raise ContractError("PLAN_REF_INCOMPLETE", "plan_id 与 plan_version 必填")
    return {
        "plan_id": plan_id,
        "plan_version": version,
        "plan_hash": stored,
        "hash_schema": hash_schema,
    }


def _architecture_review_section(
    review_gate: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    if "bulk_release_review" in review_gate:
        raise ContractError(
            "BULK_RELEASE_REVIEW_MUST_BE_SEPARATE",
            "bulk_release_review.yaml is a post-validation artifact and cannot be embedded in architecture_review.yaml",
        )
    flat_present = all(
        review_gate.get(key) is not None for key in ("status", "plan_id", "plan_hash")
    )
    legacy_section = (
        review_gate.get("architecture_review")
        or review_gate.get("plan_review")
        or review_gate.get("plan")
    )
    if flat_present and isinstance(legacy_section, dict):
        raise ContractError(
            "ARCHITECTURE_REVIEW_AMBIGUOUS",
            "flat canonical fields and a nested legacy review cannot coexist",
        )
    if flat_present:
        section = review_gate
    else:
        section = legacy_section
    if isinstance(section, dict) and not flat_present:
        warnings.append("legacy_nested_architecture_review_accepted;migrate_to_flat")
    if not isinstance(section, dict):
        raise ContractError("PLAN_REVIEW_MISSING", "review_gate 缺 architecture_review")
    return section, warnings


def review_metadata_issues(
    section: dict[str, Any], required_checks: Iterable[str]
) -> list[str]:
    issues: list[str] = []
    reviewer = str(section.get("reviewed_by") or "").strip()
    if not reviewer:
        issues.append("reviewed_by is required")
    reviewed_at = str(section.get("reviewed_at") or "").strip()
    try:
        parsed = datetime.fromisoformat(
            reviewed_at[:-1] + "+00:00" if reviewed_at.endswith("Z") else reviewed_at
        )
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("timezone required")
    except (TypeError, ValueError):
        issues.append("reviewed_at must be an ISO-8601 timestamp with timezone")

    checks = section.get("checks")
    if not isinstance(checks, dict):
        checks = {}
    incomplete = []
    for name in required_checks:
        value = checks.get(name)
        passed = value is True or str(value or "").strip().lower() in {
            "pass",
            "passed",
            "approved",
            "ok",
            "true",
        }
        if not passed:
            incomplete.append(name)
    if incomplete:
        issues.append(f"review checks incomplete: {incomplete}")
    return issues


def verify_plan_review(
    review_gate: dict[str, Any], plan_ref: dict[str, Any], mode: str
) -> list[str]:
    section, warnings = _architecture_review_section(review_gate)
    canonical = "approved_for_demo_bulk" if mode == "demo_direct_run" else "approved_for_bulk"
    legacy = (
        {"demo_reviewed", "demo_default", "accepted_for_demo"}
        if mode == "demo_direct_run"
        else {"approved", "accepted_with_real_data"}
    )
    status = str(section.get("status") or "")
    if status != canonical and status not in legacy:
        raise ContractError("PLAN_REVIEW_NOT_APPROVED", f"status={status or 'missing'}")
    gate_plan_id = section.get("approved_plan_id") or section.get("plan_id")
    gate_hash = section.get("approved_plan_hash") or section.get("plan_hash")
    if str(gate_plan_id or "") != plan_ref["plan_id"]:
        raise ContractError("PLAN_REVIEW_ID_MISMATCH", f"review={gate_plan_id} plan={plan_ref['plan_id']}")
    if str(gate_hash or "").lower() != str(plan_ref["plan_hash"]).lower():
        raise ContractError("PLAN_REVIEW_HASH_MISMATCH", "review_gate 批准的不是当前 plan_hash")
    gate_version = section.get("approved_plan_version") or section.get("plan_version")
    if gate_version not in (None, "") and str(gate_version) != str(plan_ref["plan_version"]):
        raise ContractError(
            "PLAN_REVIEW_VERSION_MISMATCH",
            f"review={gate_version} plan={plan_ref['plan_version']}",
        )
    metadata_issues = review_metadata_issues(section, ARCHITECTURE_REVIEW_CHECKS)
    if metadata_issues:
        raise ContractError(
            "ARCHITECTURE_REVIEW_INCOMPLETE", " | ".join(metadata_issues)
        )
    if status in legacy:
        warnings.append(f"legacy_review_status_accepted:{status};migrate_to:{canonical}")
    return warnings


def verify_plan_run_mode(plan: dict[str, Any], mode: str) -> list[str]:
    plan_mode = str(plan.get("run_mode") or "").strip()
    canonical_campaigns = isinstance(plan.get("campaigns"), list)
    if not plan_mode:
        if canonical_campaigns:
            raise ContractError(
                "PLAN_RUN_MODE_MISSING", "canonical campaigns require ad_plan.run_mode"
            )
        return ["legacy_plan_run_mode_missing"]
    if plan_mode not in {"production", "demo_direct_run"}:
        raise ContractError("PLAN_RUN_MODE_INVALID", plan_mode)
    if mode == "production" and plan_mode != "production":
        raise ContractError(
            "PLAN_RUN_MODE_MISMATCH",
            f"demo plan cannot be rendered or validated as production: plan={plan_mode}",
        )
    eligibility = plan.get("upload_eligibility")
    if mode == "production" and isinstance(eligibility, dict):
        reasons = [str(item) for item in eligibility.get("reasons") or []]
        if eligibility.get("status") == "blocked" or DEMO_MARKER in reasons:
            raise ContractError(
                "PLAN_UPLOAD_ELIGIBILITY_BLOCKED",
                f"status={eligibility.get('status')} reasons={reasons}",
            )
    if mode == "demo_direct_run" and plan_mode == "production":
        return ["production_plan_rendered_as_demo_only"]
    return []


def verify_compile_context_review(
    review_gate: dict[str, Any],
    plan: dict[str, Any],
    compile_context_path: Path | None,
    mode: str,
) -> list[str]:
    bulk_operations = plan.get("bulk_operations")
    uses_campaign_compiler = not (
        isinstance(bulk_operations, list) and bool(bulk_operations)
    ) and isinstance(plan.get("campaigns"), list)
    if not uses_campaign_compiler or compile_context_path is None:
        return []

    section, _ = _architecture_review_section(review_gate)
    approved_hash = str(
        section.get("approved_compile_context_sha256") or ""
    ).lower()
    actual_hash = sha256_file(compile_context_path).lower()
    if approved_hash == actual_hash:
        return []

    code = (
        "COMPILE_CONTEXT_REVIEW_MISSING"
        if not approved_hash
        else "COMPILE_CONTEXT_REVIEW_MISMATCH"
    )
    detail = f"approved={approved_hash or 'missing'} actual={actual_hash}"
    if mode == "production":
        raise ContractError(code, detail)
    return [f"demo_{code.lower()}:{detail}"]


def decimal_string(value: Any) -> str:
    try:
        result = Decimal(str(value or 0))
    except InvalidOperation as exc:
        raise ContractError("INVALID_DECIMAL", str(value)) from exc
    return format(result.quantize(Decimal("0.01")), "f")


def is_real_id(value: Any) -> bool:
    return bool(re.fullmatch(r"[0-9]+", str(value or "").strip()))


def is_temp_id(value: Any) -> bool:
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]*", str(value or "").strip()))


def normalize_operations(
    plan: dict[str, Any],
    ad_product: str,
    compile_context: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    raw_ops = plan.get("bulk_operations")
    if not isinstance(raw_ops, list) or not raw_ops:
        compiled_rows, compiled_warnings = compile_campaigns_to_operations(
            plan, ad_product, compile_context, ContractError
        )
        for row in compiled_rows:
            row["product"] = "Sponsored Products"
            row["ad_product"] = ad_product
        return compiled_rows, compiled_warnings
    if not isinstance(raw_ops, list) or not raw_ops:
        raise ContractError("BULK_OPERATIONS_MISSING", "ad_plan.bulk_operations 必须是非空列表")
    selected: list[dict[str, Any]] = []
    warnings: list[str] = []
    action_ids: set[str] = set()
    other_products: Counter[str] = Counter()
    allowed_keys = set(SP_COLUMN_MAP) | {"action_id", "ad_product"}

    for index, source in enumerate(raw_ops, start=1):
        if not isinstance(source, dict):
            raise ContractError("INVALID_OPERATION_ROW", f"bulk_operations[{index}] 不是 mapping")
        row = copy.deepcopy(source)
        row_product = str(row.get("ad_product") or ad_product).upper()
        if row_product != ad_product:
            other_products[row_product] += 1
            continue
        unknown = sorted(set(row) - allowed_keys)
        if unknown:
            raise ContractError("UNKNOWN_OPERATION_FIELDS", f"row {index}: {unknown}")
        action_id = str(row.get("action_id") or "").strip()
        if not action_id or action_id in action_ids:
            raise ContractError("ACTION_ID_INVALID", f"row {index}: action_id 缺失或重复")
        action_ids.add(action_id)
        entity = str(row.get("entity") or "")
        operation = str(row.get("operation") or "")
        if entity not in SP_ENTITIES:
            raise ContractError("ENTITY_UNSUPPORTED", f"{action_id}: {entity}")
        if operation not in OPERATIONS:
            raise ContractError("OPERATION_UNSUPPORTED", f"{action_id}: {operation}")
        row["product"] = row.get("product") or "Sponsored Products"
        row["ad_product"] = row_product
        selected.append(row)

    if other_products:
        warnings.append(f"other_ad_products_skipped:{dict(other_products)}")
    if not selected:
        raise ContractError("NO_OPERATIONS_FOR_AD_PRODUCT", ad_product)
    return selected, warnings


def validate_required_fields(rows: Iterable[dict[str, Any]]) -> None:
    rows = list(rows)
    for row in rows:
        action_id = row["action_id"]
        entity = row["entity"]
        operation = row["operation"]
        if operation == "Create":
            required = CREATE_REQUIRED[entity]
        else:
            required = {PRIMARY_ID[entity]}
            if entity != "Campaign":
                required.add("campaign_id")
            if entity in {
                "Ad Group",
                "Product Ad",
                "Keyword",
                "Negative Keyword",
                "Product Targeting",
                "Negative Product Targeting",
            }:
                required.add("ad_group_id")
            if entity == "Bidding Adjustment":
                required.add("placement")
        missing = sorted(key for key in required if row.get(key) in (None, ""))
        if missing:
            raise ContractError("REQUIRED_FIELDS_MISSING", f"{action_id}: {missing}")
        if operation in {"Update", "Archive"}:
            primary = PRIMARY_ID[entity]
            if not is_real_id(row.get(primary)):
                raise ContractError("REAL_ID_REQUIRED", f"{action_id}: {primary} 必须是后台真实数字 ID")
            for parent_key in ("campaign_id", "ad_group_id"):
                if parent_key in required and not is_real_id(row.get(parent_key)):
                    raise ContractError(
                        "REAL_PARENT_ID_REQUIRED",
                        f"{action_id}: {parent_key} must be a real numeric ID for {operation}",
                    )
        if operation == "Create" and entity == "Campaign" and not is_temp_id(row.get("campaign_id")):
            raise ContractError("TEMP_CAMPAIGN_ID_INVALID", action_id)
        if operation == "Create" and entity == "Ad Group" and not is_temp_id(row.get("ad_group_id")):
            raise ContractError("TEMP_AD_GROUP_ID_INVALID", action_id)

    seen_existing_actions: dict[tuple[str, str, str], str] = {}
    for row in rows:
        if row["operation"] not in {"Update", "Archive"}:
            continue
        entity = str(row["entity"])
        primary_id = str(row.get(PRIMARY_ID[entity]) or "")
        qualifier = str(row.get("placement") or "") if entity == "Bidding Adjustment" else ""
        identity = (entity, primary_id, qualifier)
        prior = seen_existing_actions.get(identity)
        if prior:
            raise ContractError(
                "CONFLICTING_ENTITY_ACTIONS",
                f"{prior} and {row['action_id']} target {identity}",
            )
        seen_existing_actions[identity] = str(row["action_id"])


def validate_parent_graph(rows: Iterable[dict[str, Any]]) -> None:
    rows = list(rows)
    campaign_ids = [
        str(row["campaign_id"])
        for row in rows
        if row["operation"] == "Create" and row["entity"] == "Campaign"
    ]
    group_ids = [
        (str(row["campaign_id"]), str(row["ad_group_id"]))
        for row in rows
        if row["operation"] == "Create" and row["entity"] == "Ad Group"
    ]
    if len(campaign_ids) != len(set(campaign_ids)):
        raise ContractError("DUPLICATE_TEMP_CAMPAIGN_ID", str(campaign_ids))
    if len(group_ids) != len(set(group_ids)):
        raise ContractError("DUPLICATE_TEMP_AD_GROUP_ID", str(group_ids))
    campaigns = set(campaign_ids)
    groups = set(group_ids)
    for row in rows:
        if row["operation"] != "Create" or row["entity"] == "Campaign":
            continue
        action_id = row["action_id"]
        campaign_id = str(row.get("campaign_id") or "")
        if not is_real_id(campaign_id) and not is_temp_id(campaign_id):
            raise ContractError(
                "PARENT_CAMPAIGN_ID_INVALID", f"{action_id}: {campaign_id}"
            )
        if is_temp_id(campaign_id) and campaign_id not in campaigns:
            raise ContractError("PARENT_CAMPAIGN_BROKEN", f"{action_id}: {campaign_id}")
        if row["entity"] in {
            "Product Ad",
            "Keyword",
            "Negative Keyword",
            "Product Targeting",
            "Negative Product Targeting",
        }:
            group_id = str(row.get("ad_group_id") or "")
            if not is_real_id(group_id) and not is_temp_id(group_id):
                raise ContractError(
                    "PARENT_AD_GROUP_ID_INVALID", f"{action_id}: {group_id}"
                )
            if is_temp_id(group_id) and (campaign_id, group_id) not in groups:
                raise ContractError("PARENT_AD_GROUP_BROKEN", f"{action_id}: {campaign_id}/{group_id}")


def validate_archive_review(review_gate: dict[str, Any], rows: Iterable[dict[str, Any]], mode: str) -> None:
    archive_ids = sorted(row["action_id"] for row in rows if row["operation"] == "Archive")
    if not archive_ids or mode == "demo_direct_run":
        return
    section = review_gate.get("archive_review")
    if not isinstance(section, dict) or section.get("status") != "approved":
        raise ContractError("ARCHIVE_REVIEW_REQUIRED", f"Archive actions={archive_ids}")
    approved = sorted(str(value) for value in section.get("approved_action_ids") or [])
    if approved != archive_ids:
        raise ContractError("ARCHIVE_REVIEW_SCOPE_MISMATCH", f"approved={approved} actual={archive_ids}")
    metadata_issues = review_metadata_issues(section, ())
    if metadata_issues:
        raise ContractError(
            "ARCHIVE_REVIEW_INCOMPLETE", " | ".join(metadata_issues)
        )


def validate_production_skus(plan: dict[str, Any], rows: Iterable[dict[str, Any]], mode: str) -> None:
    if mode != "production":
        return
    if plan.get('flow_version')=='r04.s4':
        import importlib.util
        helper=Path(__file__).resolve().parents[3]/'scripts/s4_launch.py'
        spec=importlib.util.spec_from_file_location('_bulk_r04_sku',helper);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        proof=plan.get('sku_mapping_confirmation') or {}
        try:selected=m.validate_mapping(plan,proof.get('rows') or [],proof.get('decision') or {})
        except (ValueError,KeyError,TypeError) as error:raise ContractError('PRODUCTION_SELLER_SKU_UNCONFIRMED',str(error))
        allowed={r['seller_sku'] for r in selected}
        if plan.get('launch_status')!='ready_for_bulk' or set(plan.get('product',{}).get('seller_skus') or [])!=allowed:raise ContractError('PRODUCTION_SKU_SCOPE_MISMATCH','本次投放商品与已确认SKU不一致')
        for row in rows:
            if row['operation']=='Create' and row['entity']=='Product Ad' and row.get('sku') not in allowed:raise ContractError('PRODUCTION_SELLER_SKU_UNCONFIRMED','每行商品广告必须带已确认的Seller SKU')
        return
    mappings = plan.get("sku_asin_map")
    if isinstance(mappings, list):
        sku_map = {
            str(item.get("sku")): str(item.get("asin"))
            for item in mappings
            if isinstance(item, dict) and item.get("sku") and item.get("asin")
        }
    elif isinstance(mappings, dict):
        sku_map = {str(k): str(v) for k, v in mappings.items() if k and v}
    else:
        sku_map = {}
    for row in rows:
        if row["operation"] == "Create" and row["entity"] == "Product Ad":
            sku = str(row.get("sku") or "")
            asin = sku_map.get(sku, "")
            if not sku or DEMO_MARKER in sku or not re.fullmatch(r"B0[A-Z0-9]{8}", asin):
                raise ContractError("PRODUCTION_SKU_ASIN_UNVERIFIED", f"{row['action_id']}: sku={sku} asin={asin}")


def apply_demo_markers(rows: list[dict[str, Any]]) -> None:
    prefix = f"{DEMO_MARKER}__"
    for row in rows:
        if row.get("campaign_name") and not str(row["campaign_name"]).startswith(prefix):
            row["campaign_name"] = prefix + str(row["campaign_name"])
        if row.get("ad_group_name") and not str(row["ad_group_name"]).startswith(prefix):
            row["ad_group_name"] = prefix + str(row["ad_group_name"])
        if row["entity"] == "Product Ad" and row["operation"] == "Create":
            row["sku"] = DEMO_MARKER


def operation_counts(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(rows)
    by_entity = Counter(str(row["entity"]) for row in rows)
    by_operation = Counter(str(row["operation"]) for row in rows)
    daily_budget = sum(
        (Decimal(str(row.get("daily_budget") or 0)) for row in rows if row["entity"] == "Campaign"),
        Decimal("0"),
    )
    return {
        "total_rows": len(rows),
        "by_entity": dict(sorted(by_entity.items())),
        "by_operation": dict(sorted(by_operation.items())),
        "daily_budget_sum": format(daily_budget.quantize(Decimal("0.01")), "f"),
    }


def expected_counts_for(
    plan: dict[str, Any],
    ad_product: str,
    compiled_rows: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    by_product = plan.get("expected_counts_by_ad_product")
    if isinstance(by_product, dict) and isinstance(by_product.get(ad_product), dict):
        return by_product[ad_product]
    expected = plan.get("expected_counts")
    if not isinstance(expected, dict) and compiled_rows is not None and isinstance(
        plan.get("campaigns"), list
    ):
        return operation_counts(compiled_rows)
    if not isinstance(expected, dict):
        raise ContractError("EXPECTED_COUNTS_MISSING", "ad_plan 缺 expected_counts")
    return expected


def compare_counts(expected: dict[str, Any], actual: dict[str, Any]) -> None:
    for key in ("total_rows", "by_entity", "by_operation", "daily_budget_sum"):
        if key not in expected:
            raise ContractError("EXPECTED_COUNTS_INCOMPLETE", key)
    if int(expected["total_rows"]) != actual["total_rows"]:
        raise ContractError("COUNT_MISMATCH", f"total expected={expected['total_rows']} actual={actual['total_rows']}")
    for dimension in ("by_entity", "by_operation"):
        normalized = {str(k): int(v) for k, v in (expected.get(dimension) or {}).items() if int(v) != 0}
        if normalized != actual[dimension]:
            raise ContractError("COUNT_MISMATCH", f"{dimension} expected={normalized} actual={actual[dimension]}")
    if decimal_string(expected["daily_budget_sum"]) != actual["daily_budget_sum"]:
        raise ContractError(
            "BUDGET_SUM_MISMATCH",
            f"expected={decimal_string(expected['daily_budget_sum'])} actual={actual['daily_budget_sum']}",
        )


def resolve_column_map(template_ref: dict[str, Any], ad_product: str) -> dict[str, str]:
    adapter = str(template_ref.get("adapter") or "")
    if ad_product != "SP" or adapter != "SP_V1":
        raise ContractError(
            "CAPABILITY_BLOCK_ADAPTER_MISSING",
            f"ad_product={ad_product} adapter={adapter or 'missing'}；SB/SD 必须提供已实现的类型适配器",
        )
    overrides = template_ref.get("column_map")
    result = dict(SP_COLUMN_MAP)
    if isinstance(overrides, dict):
        result.update({str(k): str(v) for k, v in overrides.items()})
    return result


def verify_template_ref(
    template_path: Path,
    template_ref: dict[str, Any],
    mode: str,
    marketplace: str,
    ad_product: str,
) -> list[str]:
    warnings: list[str] = []
    source = str(template_ref.get("source") or "")
    if mode == "production" and source not in {
        "amazon_account_download",
        "current_account_download",
        "skill_bundled_official",
    }:
        raise ContractError("PRODUCTION_TEMPLATE_SOURCE_INVALID", source or "missing")
    if mode == "production":
        for key in ("downloaded_at", "marketplace", "locale", "ad_product", "sheet_name", "adapter"):
            if not template_ref.get(key):
                raise ContractError("TEMPLATE_REF_INCOMPLETE", key)
    if source == "fixture" and mode == "production":
        raise ContractError("FIXTURE_NOT_ALLOWED_IN_PRODUCTION", str(template_path))
    if str(template_ref.get("marketplace") or marketplace).upper() != marketplace.upper():
        raise ContractError("TEMPLATE_MARKETPLACE_MISMATCH", str(template_ref.get("marketplace")))
    if str(template_ref.get("ad_product") or ad_product).upper() != ad_product:
        raise ContractError("TEMPLATE_AD_PRODUCT_MISMATCH", str(template_ref.get("ad_product")))
    expected_hash = str(template_ref.get("file_sha256") or template_ref.get("sha256") or "").lower()
    if not expected_hash:
        raise ContractError("TEMPLATE_HASH_MISSING", "template_ref.file_sha256 必填")
    if not expected_hash.startswith("sha256:"):
        expected_hash = f"sha256:{expected_hash}"
    actual_hash = sha256_file(template_path).lower()
    if expected_hash != actual_hash:
        raise ContractError("TEMPLATE_HASH_MISMATCH", f"expected={expected_hash} actual={actual_hash}")
    if mode == "demo_direct_run" and source == "fixture":
        warnings.append("fixture_template_demo_only")
    return warnings


def row_for_template(row: dict[str, Any], column_map: dict[str, str]) -> dict[str, Any]:
    return {
        column_map[key]: value
        for key, value in row.items()
        if key in column_map and value not in (None, "")
    }
