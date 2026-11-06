#!/usr/bin/env python3
"""Validate an Amazon SP Bulk candidate and optionally reconcile a processing report.

The validator is intentionally separate from generation.  It never uploads,
archives, or changes Amazon state.  A technically valid file remains only a
candidate until the exact plan hash and bulk file hash receive release review.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import openpyxl


def _load_generator_core() -> None:
    configured = os.environ.get("AMAZON_BULK_GENERATOR_SKILL_DIR")
    if configured:
        scripts_dir = Path(configured).resolve() / "scripts"
    else:
        scripts_dir = Path(__file__).resolve().parents[2] / "amazon-bulk-upload" / "scripts"
    if not (scripts_dir / "bulk_core.py").exists():
        raise RuntimeError(
            "amazon-bulk-upload/scripts/bulk_core.py not found; install the paired generator Skill "
            "or set AMAZON_BULK_GENERATOR_SKILL_DIR"
        )
    sys.path.insert(0, str(scripts_dir))


_load_generator_core()

from bulk_core import (  # noqa: E402
    ContractError,
    DEMO_MARKER,
    RELEASE_REVIEW_CHECKS,
    apply_demo_markers,
    compare_counts,
    decimal_string,
    dump_yaml,
    expected_counts_for,
    extract_plan_ref,
    load_yaml,
    normalize_operations,
    operation_counts,
    resolve_column_map,
    review_scope_hash,
    review_metadata_issues,
    sha256_file,
    validate_archive_review,
    validate_parent_graph,
    validate_production_skus,
    validate_required_fields,
    verify_plan_hash,
    verify_plan_run_mode,
    verify_plan_review,
    verify_compile_context_review,
    verify_template_ref,
)


def _record(
    checks: list[dict[str, str]], code: str, status: str, detail: str = ""
) -> None:
    checks.append({"code": code, "status": status, "detail": detail})


def _header_values(ws: Any) -> list[str]:
    return ["" if cell.value in (None, "") else str(cell.value) for cell in ws[1]]


def _normalize_cell(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, float):
        return format(value, ".15g")
    return str(value)


def _normalize_excel_row_key(value: Any) -> str:
    text = str(value or "").strip()
    try:
        number = float(text)
    except ValueError:
        return text
    return str(int(number)) if number.is_integer() else text


def _normalize_count_snapshot(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError("MANIFEST_COUNTS_INVALID", "count snapshot must be a mapping")
    try:
        total_rows = int(value["total_rows"])
        by_entity = {
            str(key): int(count)
            for key, count in (value["by_entity"] or {}).items()
            if int(count) != 0
        }
        by_operation = {
            str(key): int(count)
            for key, count in (value["by_operation"] or {}).items()
            if int(count) != 0
        }
        daily_budget_sum = decimal_string(value["daily_budget_sum"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError("MANIFEST_COUNTS_INVALID", str(exc)) from exc
    return {
        "total_rows": total_rows,
        "by_entity": dict(sorted(by_entity.items())),
        "by_operation": dict(sorted(by_operation.items())),
        "daily_budget_sum": daily_budget_sum,
    }


def _row_map_issues(
    row_map: Any,
    planned: list[dict[str, Any]],
    actual: list[dict[str, Any]],
) -> list[str]:
    if not isinstance(row_map, list):
        return ["manifest.row_map must be a list"]
    entries = [item for item in row_map if isinstance(item, dict)]
    if len(entries) != len(row_map):
        return ["manifest.row_map contains a non-mapping entry"]
    excel_rows = [_normalize_excel_row_key(item.get("excel_row")) for item in entries]
    action_ids = [str(item.get("action_id") or "") for item in entries]
    issues: list[str] = []
    if any(not value for value in excel_rows) or len(excel_rows) != len(set(excel_rows)):
        issues.append("manifest.row_map excel_row is missing or duplicated")
    if any(not value for value in action_ids) or len(action_ids) != len(set(action_ids)):
        issues.append("manifest.row_map action_id is missing or duplicated")
    planned_ids = {str(row["action_id"]) for row in planned}
    actual_excel_rows = {_normalize_excel_row_key(row.get("_excel_row")) for row in actual}
    if set(action_ids) != planned_ids:
        issues.append("manifest.row_map action_id set differs from plan")
    if set(excel_rows) != actual_excel_rows:
        issues.append("manifest.row_map excel_row set differs from workbook operation rows")
    return issues


def _read_bulk_rows(
    bulk_path: Path,
    sheet_name: str,
    column_map: dict[str, str],
    row_map: Any,
    data_start_row: int,
) -> tuple[list[str], list[dict[str, Any]]]:
    workbook = openpyxl.load_workbook(bulk_path, read_only=True, data_only=False, keep_links=True)
    try:
        if sheet_name not in workbook.sheetnames:
            raise ContractError("BULK_SHEET_MISSING", sheet_name)
        ws = workbook[sheet_name]
        header_values = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ())
        headers = ["" if value in (None, "") else str(value) for value in header_values]
        header_positions = {
            str(value): index
            for index, value in enumerate(header_values)
            if value not in (None, "")
        }
        missing = sorted(header for header in set(column_map.values()) if header not in header_positions)
        if missing:
            raise ContractError("BULK_HEADERS_MISSING", str(missing))
        reverse = {header: canonical for canonical, header in column_map.items()}
        if not isinstance(row_map, list):
            raise ContractError("MANIFEST_ROW_MAP_INVALID", "manifest.row_map must be a list")
        action_by_excel_row: dict[int, str] = {}
        seen_action_ids: set[str] = set()
        for item in row_map:
            if not isinstance(item, dict):
                raise ContractError(
                    "MANIFEST_ROW_MAP_INVALID", "manifest.row_map contains a non-mapping entry"
                )
            row_key = _normalize_excel_row_key(item.get("excel_row"))
            action_id = str(item.get("action_id") or "")
            if not row_key.isdigit() or int(row_key) < data_start_row or not action_id:
                raise ContractError(
                    "MANIFEST_ROW_MAP_INVALID",
                    f"excel_row={item.get('excel_row')} action_id={action_id or 'missing'}",
                )
            excel_row = int(row_key)
            if excel_row in action_by_excel_row or action_id in seen_action_ids:
                raise ContractError(
                    "MANIFEST_ROW_MAP_INVALID",
                    f"duplicate excel_row/action_id: {excel_row}/{action_id}",
                )
            action_by_excel_row[excel_row] = action_id
            seen_action_ids.add(action_id)
        operation_index = header_positions[column_map["operation"]]
        rows: list[dict[str, Any]] = []
        for excel_row, values in enumerate(
            ws.iter_rows(min_row=data_start_row, values_only=True), start=data_start_row
        ):
            if values[operation_index] in (None, ""):
                if any(value not in (None, "") for value in values):
                    raise ContractError(
                        "UNTRACKED_BULK_ROW_DATA",
                        f"excel_row={excel_row} contains data but Operation is blank",
                    )
                continue
            row: dict[str, Any] = {
                "action_id": action_by_excel_row.get(excel_row, ""),
                "ad_product": "SP",
                "_excel_row": excel_row,
            }
            for header, canonical in reverse.items():
                value = values[header_positions[header]]
                if value not in (None, ""):
                    row[canonical] = value
            rows.append(row)
        return headers, rows
    finally:
        workbook.close()


def _sheet_value_matrix(ws: Any, max_row: int, max_column: int) -> list[tuple[Any, ...]]:
    if max_row < 1 or max_column < 1:
        return []
    return list(
        ws.iter_rows(
            min_row=1,
            max_row=max_row,
            min_col=1,
            max_col=max_column,
            values_only=True,
        )
    )


def _verify_workbook_preservation(
    template_path: Path,
    bulk_path: Path,
    target_sheet: str,
) -> int:
    source = openpyxl.load_workbook(template_path, read_only=True, data_only=False, keep_links=True)
    candidate = openpyxl.load_workbook(bulk_path, read_only=True, data_only=False, keep_links=True)
    try:
        if source.sheetnames != candidate.sheetnames:
            raise ContractError(
                "WORKBOOK_SHEET_STRUCTURE_MUTATED",
                f"template={source.sheetnames} bulk={candidate.sheetnames}",
            )
        if target_sheet not in source.sheetnames:
            raise ContractError("TEMPLATE_SHEET_MISSING", target_sheet)
        for sheet_name in source.sheetnames:
            source_ws = source[sheet_name]
            candidate_ws = candidate[sheet_name]
            if source_ws.sheet_state != candidate_ws.sheet_state:
                raise ContractError(
                    "WORKBOOK_SHEET_STATE_MUTATED",
                    f"{sheet_name}: template={source_ws.sheet_state} bulk={candidate_ws.sheet_state}",
                )
            if sheet_name == target_sheet:
                baseline_rows = source_ws.max_row
                width = max(source_ws.max_column, candidate_ws.max_column)
                source_values = _sheet_value_matrix(source_ws, baseline_rows, width)
                candidate_values = _sheet_value_matrix(candidate_ws, baseline_rows, width)
                if source_values != candidate_values:
                    raise ContractError(
                        "WORKBOOK_TEMPLATE_BASELINE_MUTATED",
                        f"target sheet baseline differs: {target_sheet}",
                    )
                continue
            width = max(source_ws.max_column, candidate_ws.max_column)
            height = max(source_ws.max_row, candidate_ws.max_row)
            if _sheet_value_matrix(source_ws, height, width) != _sheet_value_matrix(
                candidate_ws, height, width
            ):
                raise ContractError(
                    "WORKBOOK_AUXILIARY_SHEET_MUTATED", sheet_name
                )
        return source[target_sheet].max_row
    finally:
        source.close()
        candidate.close()


def _compare_rows(
    planned: list[dict[str, Any]], actual: list[dict[str, Any]]
) -> list[str]:
    planned_by_id = {row["action_id"]: row for row in planned}
    actual_by_id = {row.get("action_id"): row for row in actual if row.get("action_id")}
    issues: list[str] = []
    if len(actual_by_id) != len(actual):
        issues.append("manifest row_map 缺 action_id 或 action_id 重复")
    if set(planned_by_id) != set(actual_by_id):
        issues.append(
            f"action_id set mismatch planned_only={sorted(set(planned_by_id)-set(actual_by_id))[:10]} "
            f"bulk_only={sorted(set(actual_by_id)-set(planned_by_id))[:10]}"
        )
        return issues
    ignored = {"action_id", "ad_product", "_excel_row"}
    for action_id, expected in planned_by_id.items():
        observed = actual_by_id[action_id]
        for key in sorted(set(expected) | set(observed)):
            if key in ignored:
                continue
            expected_value = expected.get(key)
            if _normalize_cell(observed.get(key)) != _normalize_cell(expected_value):
                issues.append(
                    f"{action_id}.{key}: plan={_normalize_cell(expected_value)} bulk={_normalize_cell(observed.get(key))}"
                )
                if len(issues) >= 50:
                    return issues
    return issues


def _bulk_release_state(
    release_review: dict[str, Any] | None,
    approved_validation_path: Path | None,
    plan_ref: dict[str, Any],
    bulk_hash: str,
    technical_fingerprint: str,
    manifest_hash: str,
    mode: str,
) -> tuple[str, list[str]]:
    if release_review is None:
        if approved_validation_path is not None:
            return "stale", ["approved validation supplied without bulk release review"]
        return "pending", []

    canonical_status = str(release_review.get("status") or "pending")
    if canonical_status in {"pending", "changes_requested", "rejected", "blocked"}:
        return canonical_status, []
    if canonical_status not in {"approved_for_external_upload", "demo_not_releasable"}:
        return "stale", [f"bulk release status unsupported: {canonical_status}"]

    errors: list[str] = []
    expected_status = (
        "demo_not_releasable" if mode == "demo_direct_run" else "approved_for_external_upload"
    )
    if canonical_status != expected_status:
        errors.append(
            f"bulk release status incompatible with mode: status={canonical_status} mode={mode}"
        )
    if str(release_review.get("run_mode") or "") != mode:
        errors.append("bulk release run_mode mismatch")

    plan_section = release_review.get("plan")
    bulk_section = release_review.get("bulk")
    validation_section = release_review.get("validation")
    if not isinstance(plan_section, dict):
        plan_section = {}
        errors.append("bulk release plan section is required")
    if not isinstance(bulk_section, dict):
        bulk_section = {}
        errors.append("bulk release bulk section is required")
    if not isinstance(validation_section, dict):
        validation_section = {}
        errors.append("bulk release validation section is required")

    if str(plan_section.get("plan_id") or "") != str(plan_ref.get("plan_id") or ""):
        errors.append("bulk release plan id mismatch")
    if str(plan_section.get("plan_version") or "") != str(plan_ref.get("plan_version") or ""):
        errors.append("bulk release plan version mismatch")
    if str(plan_section.get("plan_hash") or "").lower() != str(
        plan_ref.get("plan_hash") or ""
    ).lower():
        errors.append("bulk release plan hash mismatch")
    approved_bulk_hash = bulk_section.get("bulk_hash") or bulk_section.get("sha256")
    if str(approved_bulk_hash or "").lower() != bulk_hash.lower():
        errors.append("bulk release file hash mismatch")

    if str(validation_section.get("status") or "") not in {"pass", "passed"}:
        errors.append("bulk release validation status is not pass")
    expected_validation_hash = str(
        validation_section.get("validation_hash") or validation_section.get("sha256") or ""
    ).lower()
    if not expected_validation_hash:
        errors.append("bulk release validation hash is required")
    if approved_validation_path is None:
        errors.append("approved validation artifact path is required")
    elif not approved_validation_path.exists():
        errors.append("approved validation artifact is missing")
    elif sha256_file(approved_validation_path).lower() != expected_validation_hash:
        errors.append("approved validation artifact hash mismatch")
    else:
        approved_validation = load_yaml(approved_validation_path)
        if approved_validation.get("technical_status") != "passed":
            errors.append("approved validation technical_status is not passed")
        if approved_validation.get("hard_blocks") not in (None, []):
            errors.append("approved validation contains hard blocks")
        approved_plan_ref = approved_validation.get("plan_ref") or {}
        for key in ("plan_id", "plan_version", "plan_hash"):
            if str(approved_plan_ref.get(key) or "").lower() != str(
                plan_ref.get(key) or ""
            ).lower():
                errors.append(f"approved validation {key} mismatch")
        if str((approved_validation.get("bulk_file") or {}).get("sha256") or "").lower() != bulk_hash.lower():
            errors.append("approved validation bulk hash mismatch")
        if str(approved_validation.get("technical_fingerprint") or "").lower() != technical_fingerprint.lower():
            errors.append("approved validation technical fingerprint mismatch")
        if str((approved_validation.get("manifest_ref") or {}).get("sha256") or "").lower() != manifest_hash.lower():
            errors.append("approved validation manifest hash mismatch")
        if str(approved_validation.get("mode") or "") != mode:
            errors.append("approved validation mode mismatch")

    errors.extend(
        f"bulk release {issue}"
        for issue in review_metadata_issues(
            release_review, RELEASE_REVIEW_CHECKS
        )
    )
    if release_review.get("external_action_authorized") is not False:
        errors.append("bulk release must not authorize the external upload action")

    if errors:
        return "stale", errors
    return (
        "demo_not_releasable" if canonical_status == "demo_not_releasable" else "approved",
        [],
    )


def _technical_validation_fingerprint(
    plan_ref: dict[str, Any],
    bulk_hash: str,
    template_hash: str,
    manifest_hash: str,
) -> str:
    payload = {
        "plan_id": plan_ref.get("plan_id"),
        "plan_version": plan_ref.get("plan_version"),
        "plan_hash": plan_ref.get("plan_hash"),
        "bulk_sha256": bulk_hash,
        "template_sha256": template_hash,
        "manifest_sha256": manifest_hash,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _rows_from_processing_report_unchecked(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    if path.suffix.lower() == ".xlsx":
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            ws = workbook[workbook.sheetnames[0]]
            headers = [str(cell.value or "") for cell in ws[1]]
            return [
                dict(zip(headers, values))
                for values in ws.iter_rows(min_row=2, values_only=True)
                if any(value not in (None, "") for value in values)
            ]
        finally:
            workbook.close()
    raise ContractError("PROCESSING_REPORT_FORMAT_UNSUPPORTED", path.suffix)


def _rows_from_processing_report(path: Path) -> list[dict[str, Any]]:
    try:
        return _rows_from_processing_report_unchecked(path)
    except ContractError:
        raise
    except Exception as exc:
        raise ContractError(
            "PROCESSING_REPORT_READ_ERROR", f"{path}: {type(exc).__name__}: {exc}"
        ) from exc


def _processing_outcome(status: str, error: str) -> str:
    normalized = status.strip().lower()
    failure = bool(error.strip()) or bool(
        re.search(
            r"\b(?:fail(?:ed|ure)?|error(?:s)?|reject(?:ed)?|invalid|skipped)\b",
            normalized,
        )
        or re.search(
            r"\bnot\s+(?:processed|submitted|completed|complete|successful|succeeded)\b",
            normalized,
        )
    )
    if failure:
        return "failure"
    if re.search(
        r"\b(?:success(?:ful(?:ly)?)?|succeed(?:ed)?|processed|complete(?:d)?)\b",
        normalized,
    ):
        return "success"
    return "unknown"


def _reconcile_processing_report(
    path: Path | None,
    row_map: list[dict[str, Any]],
    bulk_hash: str,
    marketplace: str,
    advertising_account_id: str,
    processing_ref: dict[str, Any] | None,
    processing_ref_path: Path | None,
    mode: str,
) -> dict[str, Any]:
    if path is None:
        return {
            "provided": False,
            "amazon_upload_status": "not_submitted",
            "success_count": 0,
            "failure_count": 0,
            "unknown_count": 0,
            "retry_action_ids": [],
            "unmapped_failure_rows": [],
            "coverage_complete": False,
            "expected_action_count": len(row_map),
            "covered_action_count": 0,
            "missing_action_ids": [],
            "unexpected_action_ids": [],
            "duplicate_action_ids": [],
            "unmapped_report_rows": [],
            "binding_verified": False,
            "binding_issues": [],
            "binding_ref": None,
        }
    rows = _rows_from_processing_report(path)
    report_hash = sha256_file(path)
    binding_issues: list[str] = []
    if mode == "demo_direct_run":
        binding_issues.append("demo mode cannot claim an Amazon submission result")
    if not isinstance(processing_ref, dict):
        binding_issues.append("processing_report_ref is required")
    else:
        required_bindings = (
            "source_bulk_sha256",
            "processing_report_sha256",
            "submission_reference",
            "marketplace",
            "advertising_account_id",
        )
        for key in required_bindings:
            if not processing_ref.get(key):
                binding_issues.append(f"{key} is required")
        if str(processing_ref.get("source_bulk_sha256") or "").lower() != bulk_hash.lower():
            binding_issues.append("source_bulk_sha256 mismatch")
        if str(processing_ref.get("processing_report_sha256") or "").lower() != report_hash.lower():
            binding_issues.append("processing_report_sha256 mismatch")
        if str(processing_ref.get("marketplace") or "").upper() != marketplace.upper():
            binding_issues.append("marketplace mismatch")
        if not advertising_account_id:
            binding_issues.append("template_ref.advertising_account_id is required")
        elif str(processing_ref.get("advertising_account_id") or "") != advertising_account_id:
            binding_issues.append("advertising_account_id mismatch")
    binding_verified = not binding_issues
    action_by_excel_row = {
        _normalize_excel_row_key(item.get("excel_row")): str(item.get("action_id"))
        for item in row_map
    }
    successes = 0
    failures = 0
    unknown = 0
    retry_ids: list[str] = []
    details: list[dict[str, Any]] = []
    unmapped_failure_rows: list[dict[str, Any]] = []
    unmapped_report_rows: list[dict[str, Any]] = []
    observed_action_ids: list[str] = []
    expected_action_ids = {value for value in action_by_excel_row.values() if value}
    for report_row, source in enumerate(rows, start=2):
        normalized = {str(k).strip().lower(): v for k, v in source.items()}
        status = str(
            normalized.get("status")
            or normalized.get("record status")
            or normalized.get("processing status")
            or ""
        ).strip()
        error = str(
            normalized.get("error message")
            or normalized.get("error")
            or normalized.get("errors")
            or ""
        ).strip()
        excel_row = _normalize_excel_row_key(
            normalized.get("excel row")
            or normalized.get("row")
            or normalized.get("row number")
            or ""
        )
        action_id = str(normalized.get("action_id") or normalized.get("action id") or "").strip()
        if not action_id and excel_row:
            action_id = action_by_excel_row.get(excel_row, "")
        if action_id:
            observed_action_ids.append(action_id)
        else:
            unmapped_report_rows.append(
                {
                    "report_row": report_row,
                    "excel_row": excel_row,
                    "status": status,
                    "error": error,
                }
            )
        outcome = _processing_outcome(status, error)
        if outcome == "failure":
            failures += 1
            if action_id and action_id in expected_action_ids:
                retry_ids.append(action_id)
            else:
                unmapped_failure_rows.append(
                    {
                        "report_row": report_row,
                        "excel_row": excel_row,
                        "status": status,
                        "error": error,
                    }
                )
        elif outcome == "success":
            successes += 1
        else:
            unknown += 1
        details.append(
            {
                "action_id": action_id,
                "excel_row": excel_row,
                "status": status,
                "error": error,
                "returned_ids": {
                    key: normalized[key]
                    for key in (
                        "campaign id",
                        "ad group id",
                        "ad id",
                        "keyword id",
                        "product targeting id",
                        "record id",
                    )
                    if normalized.get(key) not in (None, "")
                },
                "source_fields": {str(key): value for key, value in source.items()},
            }
        )
    observed_set = set(observed_action_ids)
    missing_action_ids = sorted(expected_action_ids - observed_set)
    unexpected_action_ids = sorted(observed_set - expected_action_ids)
    duplicate_action_ids = sorted(
        action_id
        for action_id in observed_set
        if observed_action_ids.count(action_id) > 1
    )
    coverage_complete = bool(rows) and not any(
        (
            missing_action_ids,
            unexpected_action_ids,
            duplicate_action_ids,
            unmapped_report_rows,
        )
    )
    if not binding_verified or not coverage_complete or unknown:
        upload_status = "processing_report_unresolved"
    elif failures and successes:
        upload_status = "processing_partial_failure"
    elif failures:
        upload_status = "processing_failed"
    elif rows and not unknown:
        upload_status = "processing_succeeded"
    else:
        upload_status = "processing_report_unresolved"
    return {
        "provided": True,
        "path": str(path),
        "sha256": report_hash,
        "amazon_upload_status": upload_status,
        "success_count": successes,
        "failure_count": failures,
        "unknown_count": unknown,
        "retry_action_ids": sorted(set(retry_ids)),
        "unmapped_failure_rows": unmapped_failure_rows,
        "coverage_complete": coverage_complete,
        "expected_action_count": len(expected_action_ids),
        "covered_action_count": len(observed_set & expected_action_ids),
        "missing_action_ids": missing_action_ids,
        "unexpected_action_ids": unexpected_action_ids,
        "duplicate_action_ids": duplicate_action_ids,
        "unmapped_report_rows": unmapped_report_rows,
        "binding_verified": binding_verified,
        "binding_issues": binding_issues,
        "binding_ref": (
            {
                "path": str(processing_ref_path),
                "sha256": sha256_file(processing_ref_path),
                "submission_reference": processing_ref.get("submission_reference"),
                "advertising_account_id": processing_ref.get("advertising_account_id"),
                "marketplace": processing_ref.get("marketplace"),
                "source_bulk_sha256": processing_ref.get("source_bulk_sha256"),
                "processing_report_sha256": processing_ref.get("processing_report_sha256"),
            }
            if binding_verified and processing_ref_path and processing_ref
            else None
        ),
        "details": details,
    }


def validate(args: argparse.Namespace) -> dict[str, Any]:
    plan_path = Path(args.plan).resolve()
    review_path = Path(args.review_gate).resolve()
    template_path = Path(args.template).resolve()
    template_ref_path = Path(args.template_ref).resolve()
    bulk_path = Path(args.bulk).resolve()
    manifest_path = Path(args.manifest).resolve()
    output_path = Path(args.output).resolve()
    processing_path = Path(args.processing_report).resolve() if args.processing_report else None
    processing_ref_path = (
        Path(args.processing_report_ref).resolve()
        if getattr(args, "processing_report_ref", None)
        else None
    )
    compile_context_path = (
        Path(args.compile_context).resolve()
        if getattr(args, "compile_context", None)
        else None
    )
    bulk_release_review_path = (
        Path(args.bulk_release_review).resolve()
        if getattr(args, "bulk_release_review", None)
        else None
    )
    approved_validation_path = (
        Path(args.approved_validation).resolve()
        if getattr(args, "approved_validation", None)
        else None
    )

    input_paths = {
        plan_path,
        review_path,
        template_path,
        template_ref_path,
        bulk_path,
        manifest_path,
    }
    input_paths.update(
        path
        for path in (
            processing_path,
            processing_ref_path,
            compile_context_path,
            bulk_release_review_path,
            approved_validation_path,
        )
        if path is not None
    )
    if output_path in input_paths:
        raise ContractError(
            "VALIDATION_OUTPUT_PATH_CONFLICT",
            f"output must not replace an input artifact: {output_path}",
        )
    if output_path.exists():
        raise ContractError(
            "VALIDATION_OUTPUT_ALREADY_EXISTS",
            f"refusing to overwrite versioned validation artifact: {output_path}",
        )

    plan_raw = plan_path.read_text(encoding="utf-8")
    plan = load_yaml(plan_path)
    review_gate = load_yaml(review_path)
    template_ref = load_yaml(template_ref_path)
    manifest = load_yaml(manifest_path)
    processing_ref = load_yaml(processing_ref_path) if processing_ref_path else None
    compile_context = load_yaml(compile_context_path) if compile_context_path else None
    bulk_release_review = (
        load_yaml(bulk_release_review_path) if bulk_release_review_path else None
    )
    manifest_hash = sha256_file(manifest_path)
    checks: list[dict[str, str]] = []
    hard_blocks: list[str] = []
    release_blocks: list[str] = []
    warnings: list[str] = []

    try:
        hash_schema, hash_warnings = verify_plan_hash(plan, plan_raw)
        warnings.extend(hash_warnings)
        plan_ref = extract_plan_ref(plan, hash_schema)
        _record(checks, "PLAN_HASH", "passed", hash_schema)
    except ContractError as exc:
        plan_ref = {
            "plan_id": str(plan.get("plan_id") or ""),
            "plan_version": plan.get("plan_version"),
            "plan_hash": str(plan.get("plan_hash") or ""),
            "hash_schema": str(plan.get("hash_schema") or ""),
        }
        _record(checks, exc.code, "failed", exc.message)
        hard_blocks.append(exc.code)

    mode = str(manifest.get("mode") or "")
    ad_product = str(manifest.get("ad_product") or "SP").upper()
    marketplace = str(
        manifest.get("marketplace")
        or plan.get("marketplace")
        or (plan.get("market") or {}).get("marketplace")
        or ""
    ).upper()
    if mode not in {"production", "demo_direct_run"}:
        _record(checks, "MANIFEST_MODE_INVALID", "failed", mode)
        hard_blocks.append("MANIFEST_MODE_INVALID")

    context_issues: list[str] = []
    plan_marketplace = str(
        plan.get("marketplace") or (plan.get("market") or {}).get("marketplace") or ""
    ).upper()
    plan_ad_product = str(plan.get("ad_product") or "").upper()
    if plan_marketplace and marketplace != plan_marketplace:
        context_issues.append(f"marketplace manifest={marketplace} plan={plan_marketplace}")
    if plan_ad_product and ad_product != plan_ad_product:
        context_issues.append(f"ad_product manifest={ad_product} plan={plan_ad_product}")
    if context_issues:
        _record(checks, "MANIFEST_PLAN_CONTEXT_MISMATCH", "failed", " | ".join(context_issues))
        hard_blocks.append("MANIFEST_PLAN_CONTEXT_MISMATCH")
    else:
        _record(checks, "MANIFEST_PLAN_CONTEXT", "passed")

    try:
        warnings.extend(verify_plan_run_mode(plan, mode))
        warnings.extend(verify_plan_review(review_gate, plan_ref, mode))
        warnings.extend(
            verify_compile_context_review(
                review_gate, plan, compile_context_path, mode
            )
        )
        _record(checks, "PLAN_REVIEW", "passed")
    except ContractError as exc:
        _record(checks, exc.code, "failed", exc.message)
        hard_blocks.append(exc.code)

    try:
        warnings.extend(
            verify_template_ref(template_path, template_ref, mode, marketplace, ad_product)
        )
        column_map = resolve_column_map(template_ref, ad_product)
        _record(checks, "TEMPLATE_REF", "passed", sha256_file(template_path))
    except ContractError as exc:
        column_map = {}
        _record(checks, exc.code, "failed", exc.message)
        hard_blocks.append(exc.code)

    manifest_template_ref = manifest.get("template_ref") or {}
    manifest_template_issues: list[str] = []
    actual_template_hash = sha256_file(template_path).lower()
    if str(manifest_template_ref.get("file_sha256") or "").lower() != actual_template_hash:
        manifest_template_issues.append("template file hash differs")
    for key in (
        "source",
        "downloaded_at",
        "locale",
        "advertising_account_id",
        "sheet_name",
        "adapter",
    ):
        if str(manifest_template_ref.get(key) or "") != str(template_ref.get(key) or ""):
            manifest_template_issues.append(f"{key} differs")
    if manifest_template_issues:
        _record(
            checks,
            "MANIFEST_TEMPLATE_REF_MISMATCH",
            "failed",
            " | ".join(manifest_template_issues),
        )
        hard_blocks.append("MANIFEST_TEMPLATE_REF_MISMATCH")
    else:
        _record(checks, "MANIFEST_TEMPLATE_REF", "passed", actual_template_hash)

    manifest_compile_ref = manifest.get("compile_context_ref")
    if manifest_compile_ref:
        if not compile_context_path or not isinstance(compile_context, dict):
            _record(
                checks,
                "COMPILE_CONTEXT_REQUIRED",
                "failed",
                "manifest declares a compile context but validator did not receive it",
            )
            hard_blocks.append("COMPILE_CONTEXT_REQUIRED")
        elif (
            str(manifest_compile_ref.get("sha256") or "").lower()
            != sha256_file(compile_context_path).lower()
        ):
            _record(checks, "COMPILE_CONTEXT_HASH_MISMATCH", "failed")
            hard_blocks.append("COMPILE_CONTEXT_HASH_MISMATCH")
        else:
            _record(
                checks,
                "COMPILE_CONTEXT_REF",
                "passed",
                sha256_file(compile_context_path),
            )
    elif compile_context_path:
        _record(
            checks,
            "UNEXPECTED_COMPILE_CONTEXT",
            "failed",
            str(compile_context_path),
        )
        hard_blocks.append("UNEXPECTED_COMPILE_CONTEXT")

    bulk_hash = sha256_file(bulk_path)
    manifest_bulk_hash = str((manifest.get("bulk_file") or {}).get("sha256") or "").lower()
    if bulk_hash.lower() == manifest_bulk_hash:
        _record(checks, "BULK_FILE_HASH", "passed", bulk_hash)
    else:
        _record(checks, "BULK_FILE_HASH_MISMATCH", "failed", f"manifest={manifest_bulk_hash} actual={bulk_hash}")
        hard_blocks.append("BULK_FILE_HASH_MISMATCH")

    manifest_plan_ref = manifest.get("plan_ref") or {}
    if all(
        str(manifest_plan_ref.get(key) or "").lower() == str(plan_ref.get(key) or "").lower()
        for key in ("plan_id", "plan_version", "plan_hash")
    ):
        _record(checks, "MANIFEST_PLAN_REF", "passed")
    else:
        _record(checks, "MANIFEST_PLAN_REF_MISMATCH", "failed", str(manifest_plan_ref))
        hard_blocks.append("MANIFEST_PLAN_REF_MISMATCH")

    manifest_review_ref = manifest.get("review_gate_ref") or {}
    current_review_scope_hash = review_scope_hash(review_gate)
    if (
        manifest_review_ref.get("scope") == "architecture_review+archive_review"
        and str(manifest_review_ref.get("scope_sha256") or "").lower()
        == current_review_scope_hash.lower()
    ):
        _record(checks, "MANIFEST_REVIEW_SCOPE", "passed", current_review_scope_hash)
    else:
        _record(
            checks,
            "MANIFEST_REVIEW_SCOPE_MISMATCH",
            "failed",
            f"manifest={manifest_review_ref} current={current_review_scope_hash}",
        )
        hard_blocks.append("MANIFEST_REVIEW_SCOPE_MISMATCH")

    planned_rows: list[dict[str, Any]] = []
    actual_rows: list[dict[str, Any]] = []
    effective_plan = dict(plan)
    if isinstance(compile_context, dict) and compile_context.get("sku_asin_map"):
        effective_plan["sku_asin_map"] = compile_context["sku_asin_map"]
    if column_map:
        try:
            planned_rows, op_warnings = normalize_operations(
                plan, ad_product, compile_context
            )
            warnings.extend(op_warnings)
            validate_required_fields(planned_rows)
            validate_parent_graph(planned_rows)
            validate_archive_review(review_gate, planned_rows, mode)
            validate_production_skus(effective_plan, planned_rows, mode)
            if mode == "demo_direct_run":
                apply_demo_markers(planned_rows)
            _record(checks, "PLAN_OPERATION_GRAPH", "passed")
        except ContractError as exc:
            _record(checks, exc.code, "failed", exc.message)
            hard_blocks.append(exc.code)

        try:
            sheet_name = str(template_ref.get("sheet_name") or "Sponsored Products Campaigns")
            source_max_row = _verify_workbook_preservation(
                template_path, bulk_path, sheet_name
            )
            source_wb = openpyxl.load_workbook(template_path, read_only=True, data_only=False)
            try:
                source_headers = _header_values(source_wb[sheet_name])
            finally:
                source_wb.close()
            bulk_headers, actual_rows = _read_bulk_rows(
                bulk_path,
                sheet_name,
                column_map,
                manifest.get("row_map"),
                source_max_row + 1,
            )
            if bulk_headers != source_headers:
                raise ContractError(
                    "HEADER_ROW_MUTATED", f"template={source_headers} bulk={bulk_headers}"
                )
            _record(checks, "HEADER_ROW_EXACT", "passed")
        except (ContractError, KeyError) as exc:
            code = exc.code if isinstance(exc, ContractError) else "TEMPLATE_SHEET_MISSING"
            detail = exc.message if isinstance(exc, ContractError) else str(exc)
            _record(checks, code, "failed", detail)
            hard_blocks.append(code)

    if planned_rows or actual_rows:
        row_map_issues = _row_map_issues(
            manifest.get("row_map"), planned_rows, actual_rows
        )
        if row_map_issues:
            _record(checks, "MANIFEST_ROW_MAP_INVALID", "failed", " | ".join(row_map_issues))
            hard_blocks.append("MANIFEST_ROW_MAP_INVALID")
        else:
            _record(checks, "MANIFEST_ROW_MAP", "passed", f"rows={len(actual_rows)}")
        row_issues = _compare_rows(planned_rows, actual_rows)
        if row_issues:
            _record(checks, "PLAN_TO_BULK_ROW_MISMATCH", "failed", " | ".join(row_issues))
            hard_blocks.append("PLAN_TO_BULK_ROW_MISMATCH")
        else:
            _record(checks, "PLAN_TO_BULK_ROWS", "passed", f"rows={len(actual_rows)}")
        try:
            validate_required_fields(actual_rows)
            validate_parent_graph(actual_rows)
            validate_production_skus(effective_plan, actual_rows, mode)
            actual_counts = operation_counts(actual_rows)
            expected_counts = expected_counts_for(plan, ad_product, planned_rows)
            compare_counts(expected_counts, actual_counts)
            _record(checks, "COUNTS_AND_PARENT_GRAPH", "passed", str(actual_counts))
        except ContractError as exc:
            actual_counts = operation_counts(actual_rows)
            expected_counts = plan.get("expected_counts") or {}
            _record(checks, exc.code, "failed", exc.message)
            hard_blocks.append(exc.code)
    else:
        actual_counts = operation_counts(actual_rows)
        expected_counts = plan.get("expected_counts") or {}

    try:
        plan_count_snapshot = _normalize_count_snapshot(expected_counts)
        manifest_expected_snapshot = _normalize_count_snapshot(manifest.get("expected_counts"))
        manifest_actual_snapshot = _normalize_count_snapshot(manifest.get("actual_counts"))
        actual_count_snapshot = _normalize_count_snapshot(actual_counts)
        if plan_count_snapshot != manifest_expected_snapshot:
            raise ContractError(
                "MANIFEST_COUNTS_MISMATCH", "manifest.expected_counts differs from plan"
            )
        if manifest_actual_snapshot != actual_count_snapshot:
            raise ContractError(
                "MANIFEST_COUNTS_MISMATCH", "manifest.actual_counts differs from workbook"
            )
        _record(checks, "MANIFEST_COUNTS", "passed", str(actual_count_snapshot))
    except ContractError as exc:
        _record(checks, exc.code, "failed", exc.message)
        hard_blocks.append(exc.code)

    if mode == "demo_direct_run":
        demo_ok = DEMO_MARKER in bulk_path.name and all(
            DEMO_MARKER in str(row.get("campaign_name") or DEMO_MARKER)
            for row in actual_rows
            if row.get("campaign_name")
        )
        if demo_ok:
            _record(checks, "DEMO_MARKERS", "passed")
        else:
            _record(checks, "DEMO_MARKER_MISSING", "failed")
            hard_blocks.append("DEMO_MARKER_MISSING")
    elif any(DEMO_MARKER in str(value) for row in actual_rows for value in row.values()):
        _record(checks, "DEMO_VALUE_IN_PRODUCTION", "failed")
        hard_blocks.append("DEMO_VALUE_IN_PRODUCTION")

    technical_fingerprint = _technical_validation_fingerprint(
        plan_ref, bulk_hash, actual_template_hash, manifest_hash
    )
    release_state, release_errors = _bulk_release_state(
        bulk_release_review,
        approved_validation_path,
        plan_ref,
        bulk_hash,
        technical_fingerprint,
        manifest_hash,
        mode,
    )
    if release_errors:
        _record(checks, "BULK_RELEASE_REVIEW_STALE", "failed", " | ".join(release_errors))
        release_blocks.append("BULK_RELEASE_REVIEW_STALE")
    elif release_state in {"approved", "demo_not_releasable"}:
        _record(checks, "BULK_RELEASE_REVIEW", "passed", release_state)
    else:
        _record(
            checks,
            "BULK_RELEASE_REVIEW",
            "pending",
            f"state={release_state}; 技术校验后仍需独立审核精确 plan/bulk/validation SHA",
        )

    processing = _reconcile_processing_report(
        processing_path,
        manifest.get("row_map") or [],
        bulk_hash,
        marketplace,
        str(template_ref.get("advertising_account_id") or ""),
        processing_ref,
        processing_ref_path,
        mode,
    )
    technical_status = "passed" if not hard_blocks else "failed"
    if mode == "demo_direct_run":
        readiness = "demo_only_not_uploadable"
    elif technical_status == "failed":
        readiness = "blocked"
    elif release_state == "approved":
        readiness = "reviewed_candidate_upload_prepared"
    elif release_state == "stale":
        readiness = "bulk_release_stale"
    elif release_state == "changes_requested":
        readiness = "bulk_release_changes_requested"
    elif release_state in {"rejected", "blocked"}:
        readiness = "bulk_release_rejected"
    else:
        readiness = "candidate_needs_bulk_release_review"

    result = {
        "schema_version": "1.0",
        "artifact_type": "amazon_ad_bulk_validation",
        "mode": mode,
        "ad_product": ad_product,
        "marketplace": marketplace,
        "plan_ref": plan_ref,
        "technical_fingerprint": technical_fingerprint,
        "bulk_file": {"path": str(bulk_path), "sha256": bulk_hash},
        "manifest_ref": {"path": str(manifest_path), "sha256": manifest_hash},
        "compile_context_ref": manifest.get("compile_context_ref"),
        "bulk_release_review_ref": (
            {
                "path": str(bulk_release_review_path),
                "sha256": sha256_file(bulk_release_review_path),
                "status": bulk_release_review.get("status"),
            }
            if bulk_release_review_path and bulk_release_review_path.exists()
            else None
        ),
        "approved_validation_ref": (
            {
                "path": str(approved_validation_path),
                "sha256": sha256_file(approved_validation_path),
            }
            if approved_validation_path and approved_validation_path.exists()
            else None
        ),
        "technical_status": technical_status,
        "uploadability": {
            "status": readiness,
            "bulk_release_review": release_state,
            "amazon_upload_status": processing["amazon_upload_status"],
            "important_boundary": "文件存在或技术通过不等于已上传；只有 Amazon 处理报告/API 回执可证明后台结果。",
        },
        "expected_counts": expected_counts,
        "actual_counts": actual_counts,
        "checks": checks,
        "warnings": sorted(set(warnings)),
        "hard_blocks": sorted(set(hard_blocks)),
        "release_blocks": sorted(set(release_blocks)),
        "processing_report": processing,
    }
    dump_yaml(output_path, result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument(
        "--review-gate",
        required=True,
        help="canonical flat architecture_review.yaml (legacy CLI flag retained)",
    )
    parser.add_argument("--template", required=True)
    parser.add_argument("--template-ref", required=True)
    parser.add_argument("--bulk", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True, help="bulk_validation.yaml")
    parser.add_argument("--processing-report", help="可选 Amazon 后台处理报告 .xlsx/.csv")
    parser.add_argument(
        "--processing-report-ref",
        help="YAML sidecar binding the report to exact Bulk SHA, submission, marketplace, and account",
    )
    parser.add_argument(
        "--compile-context",
        help="same hash-locked bulk_compile_context.yaml used by generator",
    )
    parser.add_argument(
        "--bulk-release-review",
        help="independent canonical bulk_release_review.yaml; omit for first-pass technical validation",
    )
    parser.add_argument(
        "--approved-validation",
        help="prior passed bulk_validation.yaml whose exact SHA is approved by the release review",
    )
    return parser


def main() -> int:
    try:
        result = validate(build_parser().parse_args())
    except (ContractError, RuntimeError, FileNotFoundError) as exc:
        print(f"[BLOCKED] {exc}", file=sys.stderr)
        return 2
    print(f"[TECHNICAL] {result['technical_status']}")
    print(f"[READINESS] {result['uploadability']['status']}")
    print(f"[AMAZON] {result['uploadability']['amazon_upload_status']}")
    return 0 if result["technical_status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
