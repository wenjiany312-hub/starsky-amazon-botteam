#!/usr/bin/env python3
"""Generate a versioned Amazon Sponsored Products Bulk candidate.

This tool never uploads anything.  Production mode only accepts a hash-locked
template reference declaring that the workbook came from the current Amazon
advertising account.  Demo mode may use a fixture, but the output is forcibly
marked DEMO_ONLY_NOT_UPLOADABLE without touching the official header row.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import openpyxl

from bulk_core import (
    ContractError,
    DEMO_MARKER,
    apply_demo_markers,
    compare_counts,
    dump_yaml,
    expected_counts_for,
    extract_plan_ref,
    load_yaml,
    normalize_operations,
    operation_counts,
    resolve_column_map,
    review_scope_hash,
    row_for_template,
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


def _demo_output_path(path: Path) -> Path:
    if DEMO_MARKER in path.name:
        return path
    return path.with_name(f"{path.stem}_{DEMO_MARKER}{path.suffix}")


def _header_map(ws: Any) -> tuple[list[str], dict[str, int]]:
    values = [cell.value for cell in ws[1]]
    headers = [str(value) for value in values if value not in (None, "")]
    if len(headers) != len(set(headers)):
        raise ContractError("TEMPLATE_DUPLICATE_HEADERS", "模板首行存在重复列名")
    return headers, {str(cell.value): cell.column for cell in ws[1] if cell.value not in (None, "")}


def _assert_clean_template(ws: Any, operation_header: str, columns: dict[str, int]) -> None:
    operation_col = columns[operation_header]
    populated = [
        row
        for row in range(2, ws.max_row + 1)
        if ws.cell(row=row, column=operation_col).value not in (None, "")
    ]
    if populated:
        raise ContractError(
            "TEMPLATE_NOT_CLEAN",
            f"模板含已有 Operation 数据行 {populated[:10]}；请使用当前账号下载的干净模板，后台快照另存",
        )


def generate(args: argparse.Namespace) -> dict[str, Any]:
    plan_path = Path(args.plan).resolve()
    review_path = Path(args.review_gate).resolve()
    template_path = Path(args.template).resolve()
    template_ref_path = Path(args.template_ref).resolve()
    output_path = Path(args.output).resolve()
    manifest_path = Path(args.manifest).resolve()
    compile_context_path = (
        Path(args.compile_context).resolve()
        if getattr(args, "compile_context", None)
        else None
    )
    mode = args.mode
    ad_product = args.ad_product.upper()

    if output_path.suffix.lower() != ".xlsx":
        raise ContractError("OUTPUT_EXTENSION_INVALID", str(output_path))
    if mode == "demo_direct_run":
        output_path = _demo_output_path(output_path)

    input_paths = {plan_path, review_path, template_path, template_ref_path}
    if compile_context_path:
        input_paths.add(compile_context_path)
    if output_path in input_paths or manifest_path in input_paths or output_path == manifest_path:
        raise ContractError(
            "OUTPUT_PATH_CONFLICT",
            f"output={output_path} manifest={manifest_path} must not replace an input or each other",
        )
    existing = [str(path) for path in (output_path, manifest_path) if path.exists()]
    if existing:
        raise ContractError(
            "OUTPUT_ALREADY_EXISTS",
            f"refusing to overwrite versioned artifact(s): {existing}",
        )

    raw_plan = plan_path.read_text(encoding="utf-8")
    plan = load_yaml(plan_path)
    review_gate = load_yaml(review_path)
    template_ref = load_yaml(template_ref_path)
    compile_context = load_yaml(compile_context_path) if compile_context_path else None

    hash_schema, warnings = verify_plan_hash(plan, raw_plan)
    plan_ref = extract_plan_ref(plan, hash_schema)
    warnings.extend(verify_plan_run_mode(plan, mode))
    warnings.extend(verify_plan_review(review_gate, plan_ref, mode))
    warnings.extend(
        verify_compile_context_review(
            review_gate, plan, compile_context_path, mode
        )
    )

    marketplace = str(
        plan.get("marketplace") or (plan.get("market") or {}).get("marketplace") or ""
    ).upper()
    if not marketplace:
        raise ContractError("MARKETPLACE_MISSING", "ad_plan.marketplace 必填")

    warnings.extend(
        verify_template_ref(template_path, template_ref, mode, marketplace, ad_product)
    )
    column_map = resolve_column_map(template_ref, ad_product)
    rows, operation_warnings = normalize_operations(plan, ad_product, compile_context)
    warnings.extend(operation_warnings)
    validate_required_fields(rows)
    validate_parent_graph(rows)
    validate_archive_review(review_gate, rows, mode)
    effective_plan = dict(plan)
    if isinstance(compile_context, dict) and compile_context.get("sku_asin_map"):
        effective_plan["sku_asin_map"] = compile_context["sku_asin_map"]
    validate_production_skus(effective_plan, rows, mode)
    if mode == "demo_direct_run":
        apply_demo_markers(rows)

    actual_counts = operation_counts(rows)
    expected_counts = expected_counts_for(plan, ad_product, rows)
    compare_counts(expected_counts, actual_counts)

    sheet_name = str(template_ref.get("sheet_name") or "Sponsored Products Campaigns")
    source_hash_before = sha256_file(template_path)
    workbook = openpyxl.load_workbook(template_path, read_only=False, data_only=False, keep_links=True)
    try:
        if sheet_name not in workbook.sheetnames:
            raise ContractError("TEMPLATE_SHEET_MISSING", sheet_name)
        ws = workbook[sheet_name]
        _, columns = _header_map(ws)
        required_headers = sorted(set(column_map.values()))
        missing_headers = [header for header in required_headers if header not in columns]
        if missing_headers:
            raise ContractError("TEMPLATE_HEADERS_MISSING", str(missing_headers))
        _assert_clean_template(ws, column_map["operation"], columns)
    finally:
        workbook.close()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(template_path, output_path)
    workbook = openpyxl.load_workbook(output_path, read_only=False, data_only=False, keep_links=True)
    row_map: list[dict[str, Any]] = []
    try:
        ws = workbook[sheet_name]
        _, columns = _header_map(ws)
        for source_row in rows:
            excel_row = ws.max_row + 1
            values = row_for_template(source_row, column_map)
            for header, value in values.items():
                ws.cell(row=excel_row, column=columns[header], value=value)
            row_map.append(
                {
                    "excel_row": excel_row,
                    "action_id": source_row["action_id"],
                    "entity": source_row["entity"],
                    "operation": source_row["operation"],
                }
            )
        workbook.save(output_path)
    finally:
        workbook.close()

    if sha256_file(template_path) != source_hash_before:
        raise ContractError("SOURCE_TEMPLATE_MUTATED", str(template_path))

    output_hash = sha256_file(output_path)
    generated_at = datetime.now(timezone.utc).isoformat()
    uploadability = (
        "demo_only_not_uploadable"
        if mode == "demo_direct_run"
        else "candidate_needs_technical_validation_and_bulk_release_review"
    )
    manifest = {
        "schema_version": "1.0",
        "artifact_type": "amazon_ad_bulk_manifest",
        "generated_at": generated_at,
        "mode": mode,
        "ad_product": ad_product,
        "marketplace": marketplace,
        "plan_ref": plan_ref,
        "review_gate_ref": {
            "path": str(review_path),
            "sha256": sha256_file(review_path),
            "scope": "architecture_review+archive_review",
            "scope_sha256": review_scope_hash(review_gate),
        },
        "template_ref": {
            "path": str(template_path),
            "ref_path": str(template_ref_path),
            "source": template_ref.get("source"),
            "downloaded_at": template_ref.get("downloaded_at"),
            "locale": template_ref.get("locale"),
            "advertising_account_id": template_ref.get("advertising_account_id"),
            "sheet_name": sheet_name,
            "adapter": template_ref.get("adapter"),
            "file_sha256": source_hash_before,
        },
        "compile_context_ref": (
            {
                "path": str(compile_context_path),
                "sha256": sha256_file(compile_context_path),
            }
            if compile_context_path
            else None
        ),
        "bulk_file": {
            "path": str(output_path),
            "sha256": output_hash,
        },
        "expected_counts": expected_counts,
        "actual_counts": actual_counts,
        "row_map": row_map,
        "uploadability": {
            "status": uploadability,
            "technical_validation": "pending",
            "bulk_release_review": "pending",
            "amazon_upload_status": "not_submitted",
        },
        "warnings": warnings,
        "hard_blocks": (["demo_mode_never_upload"] if mode == "demo_direct_run" else []),
    }
    dump_yaml(manifest_path, manifest)
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, help="ad_plan.yaml")
    parser.add_argument(
        "--review-gate",
        required=True,
        help="canonical flat architecture_review.yaml (legacy CLI flag retained)",
    )
    parser.add_argument("--template", required=True, help="当前账户下载的干净 Amazon Bulk 模板")
    parser.add_argument("--template-ref", required=True, help="template_ref.yaml（含模板 SHA-256）")
    parser.add_argument("--output", required=True, help="输出 .xlsx 候选文件")
    parser.add_argument("--manifest", required=True, help="输出 bulk_manifest.yaml")
    parser.add_argument(
        "--mode", required=True, choices=("production", "demo_direct_run")
    )
    parser.add_argument("--ad-product", default="SP", choices=("SP", "SB", "SD"))
    parser.add_argument(
        "--compile-context",
        help="bulk_compile_context.yaml required when ad_plan uses canonical campaigns instead of bulk_operations",
    )
    return parser


def main() -> int:
    try:
        manifest = generate(build_parser().parse_args())
    except ContractError as exc:
        print(f"[BLOCKED] {exc.code}: {exc.message}", file=sys.stderr)
        return 3 if exc.code.startswith("CAPABILITY_BLOCK") else 2
    print(f"[OK] bulk={manifest['bulk_file']['path']}")
    print(f"[OK] sha256={manifest['bulk_file']['sha256']}")
    print(f"[STATE] {manifest['uploadability']['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
