#!/usr/bin/env python3
"""Extract failed action IDs from bulk_validation into a reviewable retry package.

The script does not edit the prior Bulk or invent a new plan hash.  The retry
package must be reviewed and compiled into a new ad_plan version before the
paired generator creates v2/v3.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _load_core() -> None:
    configured = os.environ.get("AMAZON_BULK_GENERATOR_SKILL_DIR")
    scripts_dir = (
        Path(configured).resolve() / "scripts"
        if configured
        else Path(__file__).resolve().parents[2] / "amazon-bulk-upload" / "scripts"
    )
    sys.path.insert(0, str(scripts_dir))


_load_core()

from bulk_core import (  # noqa: E402
    ContractError,
    dump_yaml,
    extract_plan_ref,
    is_temp_id,
    load_yaml,
    normalize_operations,
    sha256_file,
    verify_plan_hash,
)


def build(
    plan_path: Path,
    validation_path: Path,
    output_path: Path,
    compile_context_path: Path | None = None,
) -> dict:
    plan_raw = plan_path.read_text(encoding="utf-8")
    plan = load_yaml(plan_path)
    validation = load_yaml(validation_path)
    hash_schema, _ = verify_plan_hash(plan, plan_raw)
    plan_ref = extract_plan_ref(plan, hash_schema)
    validation_plan_ref = validation.get("plan_ref") or {}
    if validation.get("artifact_type") != "amazon_ad_bulk_validation":
        raise ContractError(
            "VALIDATION_ARTIFACT_INVALID", str(validation.get("artifact_type") or "missing")
        )
    if any(
        str(validation_plan_ref.get(key) or "").lower()
        != str(plan_ref.get(key) or "").lower()
        for key in ("plan_id", "plan_version", "plan_hash")
    ):
        raise ContractError(
            "RETRY_PLAN_REF_MISMATCH",
            f"validation={validation_plan_ref} plan={plan_ref}",
        )
    processing = validation.get("processing_report") or {}
    retry_ids = sorted(str(value) for value in processing.get("retry_action_ids") or [])
    if not processing.get("provided"):
        raise ContractError("PROCESSING_REPORT_REQUIRED", "没有 Amazon 处理报告，不能推断失败 action")
    if not processing.get("binding_verified"):
        raise ContractError(
            "PROCESSING_REPORT_BINDING_REQUIRED",
            str(processing.get("binding_issues") or "processing report is not bound to this Bulk"),
        )
    if processing.get("amazon_upload_status") not in {
        "processing_failed",
        "processing_partial_failure",
    }:
        raise ContractError(
            "PROCESSING_REPORT_NOT_ACTIONABLE",
            str(processing.get("amazon_upload_status") or "missing"),
        )
    if not retry_ids:
        raise ContractError("NO_FAILED_ACTIONS", "处理报告没有可映射的失败 action_id")

    compile_context_ref = validation.get("compile_context_ref")
    compile_context = load_yaml(compile_context_path) if compile_context_path else None
    if isinstance(compile_context_ref, dict):
        if compile_context_path is None:
            raise ContractError(
                "RETRY_COMPILE_CONTEXT_REQUIRED",
                "validation was produced from canonical campaigns and a compile context",
            )
        if str(compile_context_ref.get("sha256") or "").lower() != sha256_file(
            compile_context_path
        ).lower():
            raise ContractError(
                "RETRY_COMPILE_CONTEXT_HASH_MISMATCH",
                str(compile_context_ref),
            )
    elif compile_context_path is not None:
        raise ContractError(
            "RETRY_COMPILE_CONTEXT_UNEXPECTED",
            str(compile_context_path),
        )

    all_operations, _ = normalize_operations(
        plan,
        str(validation.get("ad_product") or "SP").upper(),
        compile_context,
    )
    operations = [
        row
        for row in all_operations
        if isinstance(row, dict) and str(row.get("action_id")) in retry_ids
    ]
    found = {str(row.get("action_id")) for row in operations}
    missing = sorted(set(retry_ids) - found)
    if missing:
        raise ContractError("RETRY_ACTION_UNMAPPED", str(missing))
    campaign_parent_actions = {
        str(row.get("campaign_id")): str(row.get("action_id"))
        for row in all_operations
        if row.get("operation") == "Create"
        and row.get("entity") == "Campaign"
        and is_temp_id(row.get("campaign_id"))
    }
    ad_group_parent_actions = {
        (str(row.get("campaign_id")), str(row.get("ad_group_id"))): str(
            row.get("action_id")
        )
        for row in all_operations
        if row.get("operation") == "Create"
        and row.get("entity") == "Ad Group"
        and is_temp_id(row.get("ad_group_id"))
    }
    retry_campaign_ids = {
        str(row.get("campaign_id"))
        for row in operations
        if row.get("operation") == "Create" and row.get("entity") == "Campaign"
    }
    retry_ad_group_ids = {
        (str(row.get("campaign_id")), str(row.get("ad_group_id")))
        for row in operations
        if row.get("operation") == "Create" and row.get("entity") == "Ad Group"
    }
    evidence_by_action = {
        str(item.get("action_id")): item
        for item in processing.get("details") or []
        if isinstance(item, dict) and item.get("action_id")
    }
    parent_resolution_required: list[dict] = []
    child_entities = {
        "Product Ad",
        "Keyword",
        "Negative Keyword",
        "Product Targeting",
        "Negative Product Targeting",
    }
    for row in operations:
        action_id = str(row.get("action_id"))
        campaign_id = str(row.get("campaign_id") or "")
        if row.get("entity") != "Campaign" and is_temp_id(campaign_id):
            if campaign_id not in retry_campaign_ids:
                parent_action_id = campaign_parent_actions.get(campaign_id, "")
                parent_resolution_required.append(
                    {
                        "action_id": action_id,
                        "parent_type": "Campaign",
                        "temporary_id": campaign_id,
                        "parent_action_id": parent_action_id,
                        "parent_processing_evidence": evidence_by_action.get(parent_action_id),
                        "required_resolution": "replace with Amazon-returned real Campaign ID in a new plan version",
                    }
                )
        if row.get("entity") in child_entities:
            ad_group_id = str(row.get("ad_group_id") or "")
            pair = (campaign_id, ad_group_id)
            if is_temp_id(ad_group_id) and pair not in retry_ad_group_ids:
                parent_action_id = ad_group_parent_actions.get(pair, "")
                parent_resolution_required.append(
                    {
                        "action_id": action_id,
                        "parent_type": "Ad Group",
                        "temporary_id": ad_group_id,
                        "parent_action_id": parent_action_id,
                        "parent_processing_evidence": evidence_by_action.get(parent_action_id),
                        "required_resolution": "replace with Amazon-returned real Ad Group ID in a new plan version",
                    }
                )
    failure_evidence = [
        evidence_by_action[action_id]
        for action_id in retry_ids
        if action_id in evidence_by_action
    ]
    status = (
        "blocked_parent_resolution_required"
        if parent_resolution_required
        else "draft_requires_plan_revision_and_review"
    )
    result = {
        "schema_version": "1.0",
        "artifact_type": "amazon_ad_bulk_retry_actions",
        "status": status,
        "source_plan_ref": plan_ref,
        "source_bulk": validation.get("bulk_file") or {},
        "processing_report_ref": {
            "path": processing.get("path"),
            "sha256": processing.get("sha256"),
            "binding_ref": processing.get("binding_ref"),
        },
        "retry_action_ids": retry_ids,
        "bulk_operations": operations,
        "unmapped_action_ids": [],
        "failure_evidence": failure_evidence,
        "parent_resolution_required": parent_resolution_required,
        "required_next_steps": [
            "核对每个失败原因与 Amazon 返回行",
            "生成新的 ad_plan 版本/hash，并记录 supersedes/change_reason",
            "重新人工审核当前 plan hash",
            "调用 amazon-bulk-upload 生成新的 v2/v3；不得覆盖旧 Bulk",
        ],
    }
    dump_yaml(output_path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--validation", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--compile-context",
        help="same hash-locked compile context used when the source plan has canonical campaigns",
    )
    args = parser.parse_args()
    try:
        result = build(
            Path(args.plan),
            Path(args.validation),
            Path(args.output),
            Path(args.compile_context) if args.compile_context else None,
        )
    except ContractError as exc:
        print(f"[BLOCKED] {exc.code}: {exc.message}", file=sys.stderr)
        return 2
    print(f"[OK] retry_actions={len(result['retry_action_ids'])}")
    print(f"[STATE] {result['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
