#!/usr/bin/env python3
"""Hash and validate Keyword Package 2.0 without business-specific thresholds.

The validator is deliberately independent from pandas/jsonschema. JSON is a
valid YAML 1.2 subset, so a `.yaml` file can always be emitted in JSON syntax.
If PyYAML is installed, ordinary YAML is accepted as input as well.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import math
import re
import sys
from pathlib import Path
from typing import Any, Iterable


_ELIGIBILITY_SPEC = importlib.util.spec_from_file_location(
    "keyword_eligibility_contract", Path(__file__).with_name("keyword_eligibility.py")
)
if _ELIGIBILITY_SPEC is None or _ELIGIBILITY_SPEC.loader is None:
    raise ImportError("Shared keyword eligibility guard is unavailable")
_ELIGIBILITY = importlib.util.module_from_spec(_ELIGIBILITY_SPEC)
_ELIGIBILITY_SPEC.loader.exec_module(_ELIGIBILITY)


CONTRACT_VERSION = "2.0.0"
CANONICALIZATION_VERSION = "kwp-c14n-1"
VALID_SOURCES = {"sif", "sellersprite", "xiyou"}
VALID_DISPOSITIONS = {
    "retained",
    "observe",
    "brand_isolated",
    "compliance_negative",
    "low_relevance_negative",
}
POSITIVE_AD_POOLS = {
    "exact",
    "exact_p1",
    "exact_p2",
    "phrase_feature",
    "phrase_scenario",
    "broad_discovery",
}
REQUIRED_LISTING_POOLS = {
    "title_candidates",
    "highlights_candidates",
    "bullet_feature",
    "bullet_scenario",
    "description_aplus",
    "search_terms_candidates",
    "attributes",
    "qa",
    "fitment_review",
}
REQUIRED_AD_POOLS = POSITIVE_AD_POOLS | {"observe"}
REQUIRED_NEGATIVE_POOLS = {"exact", "phrase"}
REQUIRED_ASIN_POOLS = {"pat_candidates", "defense_candidates", "brand_review"}
VALID_METRIC_STATUSES = {
    "provided",
    "derived",
    "proxy",
    "missing",
    "invalid",
    "not_observed_zero_rank",
    "not_applicable",
}
EXPECTED_TOOL_TOKENS = {
    "sif": ("sif",),
    "sellersprite": ("sellersprite", "seller_sprite"),
    "xiyou": ("xiyou", "xydc", "西柚"),
}
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
MONTH_RE = re.compile(r"^20[0-9]{2}-(0[1-9]|1[0-2])$")


def load_document(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as json_error:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise ValueError(
                "输入不是 JSON 语法，且当前 Python 没有 PyYAML。请把 keyword_package.yaml "
                "输出为 JSON 语法（JSON 是 YAML 1.2 子集），或在带 PyYAML 的运行时执行。"
            ) from exc
        try:
            value = yaml.safe_load(text)
        except Exception as exc:  # pragma: no cover - PyYAML environment path
            raise ValueError(f"无法解析 JSON/YAML：{json_error}; {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("Keyword Package 顶层必须是 object/map。")
    return value


def canonical_payload(package: dict[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(package)
    payload.pop("package_hash", None)
    # The external store revision is not a change in this source-bound slice.
    payload.pop('series_asset', None)
    for record in payload.get('records', []):
        record.pop('asset_ref', None)
    # The handoff repeats the package's own hash for downstream convenience.
    # Exclude that self-reference from the digest to avoid a circular hash.
    handoff = payload.get("handoff")
    if isinstance(handoff, dict):
        strategy_inputs = handoff.get("strategy_brief_inputs")
        if isinstance(strategy_inputs, dict):
            package_ref = strategy_inputs.get("package_ref")
            if isinstance(package_ref, dict):
                package_ref.pop("package_hash", None)
    meta = payload.get("_meta")
    if isinstance(meta, dict):
        meta.pop("generated_at", None)
    return payload


def compute_package_hash(package: dict[str, Any]) -> str:
    encoded = json.dumps(
        canonical_payload(package),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _required(package: dict[str, Any], names: Iterable[str], errors: list[str]) -> None:
    for name in names:
        if name not in package:
            errors.append(f"MISSING_TOP_LEVEL:{name}")


def _ids(items: Any, key: str, errors: list[str], prefix: str) -> tuple[dict[str, dict[str, Any]], set[str]]:
    index: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    if not isinstance(items, list):
        errors.append(f"{prefix}_NOT_ARRAY")
        return index, duplicates
    for position, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"{prefix}_ITEM_NOT_OBJECT:{position}")
            continue
        value = str(item.get(key) or "").strip()
        if not value:
            errors.append(f"{prefix}_MISSING_ID:{position}")
            continue
        if value in index:
            duplicates.add(value)
        index[value] = item
    for value in sorted(duplicates):
        errors.append(f"{prefix}_DUPLICATE_ID:{value}")
    return index, duplicates


def _validate_pools(
    pools: Any,
    allowed_ids: set[str],
    errors: list[str],
    prefix: str,
) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    if not isinstance(pools, dict):
        errors.append(f"{prefix}_NOT_OBJECT")
        return result
    for pool_name, raw_ids in pools.items():
        if not isinstance(raw_ids, list):
            errors.append(f"{prefix}_POOL_NOT_ARRAY:{pool_name}")
            continue
        values = [str(value) for value in raw_ids]
        if len(values) != len(set(values)):
            errors.append(f"{prefix}_DUPLICATE_ID_IN_POOL:{pool_name}")
        ids = set(values)
        result[str(pool_name)] = ids
        for unknown in sorted(ids - allowed_ids):
            errors.append(f"{prefix}_UNKNOWN_ID:{pool_name}:{unknown}")
    return result


def _source_period_has_month(source: dict[str, Any]) -> bool:
    period = source.get("period") or {}
    return isinstance(period, dict) and bool(period.get("month"))


def _validate_period(period: Any, errors: list[str], prefix: str) -> None:
    if not isinstance(period, dict):
        errors.append(f"{prefix}_PERIOD_NOT_OBJECT")
        return
    for field in ("start", "end", "month"):
        if field not in period:
            errors.append(f"{prefix}_PERIOD_FIELD_MISSING:{field}")
    month = period.get("month")
    if month is not None and not MONTH_RE.fullmatch(str(month)):
        errors.append(f"{prefix}_PERIOD_MONTH_INVALID:{month}")


def _require_pool_keys(pools: dict[str, set[str]], required: set[str], errors: list[str], prefix: str) -> None:
    for name in sorted(required - set(pools)):
        errors.append(f"{prefix}_POOL_MISSING:{name}")


def validate_package(
    package: dict[str, Any],
    expected_source: str | None = None,
    *, series=None,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    _required(
        package,
        (
            "schema_version", "package_id", "package_version", "package_hash", "run_id",
            "run_mode", "marketplace", "product_id", "source_mode", "source_line_policy",
            "data_sources", "mcp", "records", "listing_pools", "pools",
            "negative_records", "negative_pools", "asin_records", "asin_pools",
            "compliance", "conservation", "analysis", "handoff", "downstream_eligibility",
            "data_explanation_window", "late_data_revision", "acceptance_checks",
        ),
        errors,
    )

    source_mode = str(package.get("source_mode") or "")
    if source_mode not in VALID_SOURCES:
        errors.append(f"INVALID_SOURCE_MODE:{source_mode}")
    if expected_source and source_mode != expected_source:
        errors.append(f"EXPECTED_SOURCE_MISMATCH:{expected_source}:{source_mode}")
    if package.get("source_line_policy") != "isolated":
        errors.append("SOURCE_LINE_NOT_ISOLATED")
    if package.get("schema_version") != "2.0":
        errors.append(f"SCHEMA_VERSION_NOT_2_0:{package.get('schema_version')}")
    for field in ("package_id", "run_id", "marketplace", "product_id"):
        if not str(package.get(field) or "").strip():
            errors.append(f"TOP_LEVEL_VALUE_MISSING:{field}")
    if not isinstance(package.get("package_version"), int) or package.get("package_version", 0) < 1:
        errors.append("PACKAGE_VERSION_INVALID")
    if not SHA256_RE.fullmatch(str(package.get("package_hash") or "")):
        errors.append("PACKAGE_HASH_FORMAT_INVALID")
    supersedes = package.get("supersedes")
    if isinstance(package.get("package_version"), int) and package.get("package_version", 0) > 1:
        if not isinstance(supersedes, dict):
            errors.append("SUPERSEDES_REQUIRED_FOR_REVISION")
        else:
            for field in ("package_id", "package_version", "package_hash"):
                if field not in supersedes:
                    errors.append(f"SUPERSEDES_FIELD_MISSING:{field}")
            if not SHA256_RE.fullmatch(str(supersedes.get("package_hash") or "")):
                errors.append("SUPERSEDES_HASH_INVALID")
        if not str(package.get("change_reason") or "").strip():
            errors.append("CHANGE_REASON_REQUIRED_FOR_REVISION")

    expected_hash = compute_package_hash(package)
    if package.get("package_hash") != expected_hash:
        errors.append("PACKAGE_HASH_MISMATCH")

    sources_raw = package.get("data_sources")
    if not isinstance(sources_raw, list) or not sources_raw:
        errors.append("DATA_SOURCES_EMPTY")
        sources_raw = []
    source_index: dict[str, dict[str, Any]] = {}
    for position, source in enumerate(sources_raw):
        if not isinstance(source, dict):
            errors.append(f"DATA_SOURCE_NOT_OBJECT:{position}")
            continue
        source_id = str(source.get("source_id") or "").strip()
        if not source_id:
            errors.append(f"DATA_SOURCE_ID_MISSING:{position}")
            continue
        if source_id in source_index:
            errors.append(f"DATA_SOURCE_ID_DUPLICATE:{source_id}")
        source_index[source_id] = source
        provider = str(source.get("provider") or "")
        if provider != source_mode:
            errors.append(f"CROSS_SOURCE_PROVIDER:{source_id}:{provider}:{source_mode}")
        method = source.get("acquisition_method")
        if method not in {"file", "mcp"}:
            errors.append(f"INVALID_ACQUISITION_METHOD:{source_id}:{method}")
        if method == "file" and not SHA256_RE.fullmatch(str(source.get("sha256") or "")):
            errors.append(f"FILE_SHA256_MISSING_OR_INVALID:{source_id}")
        if not str(source.get("as_of") or "").strip():
            errors.append(f"SOURCE_AS_OF_MISSING:{source_id}")
        if str(source.get("marketplace") or "") != str(package.get("marketplace") or ""):
            errors.append(f"SOURCE_MARKETPLACE_MISMATCH:{source_id}")
        _validate_period(source.get("period"), errors, f"SOURCE:{source_id}")
        if method == "file" and not str(source.get("relative_path") or "").strip():
            errors.append(f"FILE_RELATIVE_PATH_MISSING:{source_id}")
        if not isinstance(source.get("units"), dict):
            errors.append(f"SOURCE_UNITS_NOT_OBJECT:{source_id}")
        if not isinstance(source.get("field_mapping"), dict):
            errors.append(f"SOURCE_FIELD_MAPPING_NOT_OBJECT:{source_id}")
        if not isinstance(source.get("missing_fields"), list):
            errors.append(f"SOURCE_MISSING_FIELDS_NOT_ARRAY:{source_id}")

    mcp = package.get("mcp") or {}
    if not isinstance(mcp, dict):
        errors.append("MCP_LOG_NOT_OBJECT")
        mcp = {}
    mcp_status = mcp.get("status")
    calls = mcp.get("calls")
    if not isinstance(calls, list):
        errors.append("MCP_CALLS_NOT_ARRAY")
        calls = []
    successful_calls = 0
    for position, call in enumerate(calls):
        if not isinstance(call, dict):
            errors.append(f"MCP_CALL_NOT_OBJECT:{position}")
            continue
        tool_name = str(call.get("tool_name") or "")
        if source_mode in EXPECTED_TOOL_TOKENS and not any(
            token.casefold() in tool_name.casefold() for token in EXPECTED_TOOL_TOKENS[source_mode]
        ):
            errors.append(f"MCP_TOOL_CROSS_SOURCE:{position}:{tool_name}")
        for field in ("tool_name", "called_at", "parameters_summary", "result_status", "returned_count"):
            if field not in call:
                errors.append(f"MCP_CALL_FIELD_MISSING:{position}:{field}")
        if call.get("result_status") in {"success", "partial"}:
            successful_calls += 1
    if mcp_status in {"used", "partial"} and successful_calls == 0:
        errors.append("MCP_USED_WITHOUT_SUCCESSFUL_CALL_THIS_RUN")
    if mcp_status in {"skipped", "failed"} and not str(mcp.get("skipped_reason") or "").strip():
        errors.append(f"MCP_{str(mcp_status).upper()}_WITHOUT_REASON")
    if mcp_status not in {"used", "partial", "skipped", "failed"}:
        errors.append(f"INVALID_MCP_STATUS:{mcp_status}")

    record_index, _ = _ids(package.get("records"), "keyword_id", errors, "RECORD")
    normalized_seen: dict[str, str] = {}
    actual_dispositions = {name: 0 for name in VALID_DISPOSITIONS}
    for keyword_id, record in record_index.items():
        keyword = str(record.get("keyword") or "").strip()
        normalized = str(record.get("normalized_keyword") or "").strip().casefold()
        if not keyword or not normalized:
            errors.append(f"RECORD_KEYWORD_MISSING:{keyword_id}")
        if normalized in normalized_seen:
            errors.append(f"RECORD_NORMALIZED_DUPLICATE:{keyword_id}:{normalized_seen[normalized]}")
        normalized_seen[normalized] = keyword_id
        disposition = str(record.get("disposition") or "")
        if disposition not in VALID_DISPOSITIONS:
            errors.append(f"INVALID_DISPOSITION:{keyword_id}:{disposition}")
        else:
            actual_dispositions[disposition] += 1
        six_dimensions = record.get("six_dimensions")
        if not isinstance(six_dimensions, dict) or any(key not in six_dimensions for key in ("D1", "D2", "D3", "D4", "D5", "D6")):
            errors.append(f"SIX_DIMENSIONS_INCOMPLETE:{keyword_id}")
        lineage = record.get("lineage")
        if not isinstance(lineage, list) or not lineage:
            errors.append(f"RECORD_LINEAGE_EMPTY:{keyword_id}")
        else:
            for position, link in enumerate(lineage):
                source_id = str(link.get("source_id") if isinstance(link, dict) else "")
                if source_id not in source_index:
                    errors.append(f"RECORD_UNKNOWN_SOURCE:{keyword_id}:{position}:{source_id}")

        metrics = record.get("metrics")
        if not isinstance(metrics, dict):
            errors.append(f"METRICS_NOT_OBJECT:{keyword_id}")
            metrics = {}
        for metric_name, metric in metrics.items():
            if not isinstance(metric, dict):
                errors.append(f"METRIC_NOT_OBJECT:{keyword_id}:{metric_name}")
                continue
            for field in ("value", "status", "source_id", "semantic", "period"):
                if field not in metric:
                    errors.append(f"METRIC_FIELD_MISSING:{keyword_id}:{metric_name}:{field}")
            status = metric.get("status")
            if status not in VALID_METRIC_STATUSES:
                errors.append(f"METRIC_STATUS_INVALID:{keyword_id}:{metric_name}:{status}")
            if metric.get("value") is None and not str(metric.get("missing_reason") or "").strip():
                errors.append(f"NULL_METRIC_WITHOUT_REASON:{keyword_id}:{metric_name}")
            if metric.get("value") is None and status not in {
                "missing", "invalid", "not_observed_zero_rank", "not_applicable"
            }:
                errors.append(f"NULL_METRIC_STATUS_INVALID:{keyword_id}:{metric_name}:{status}")
            if metric.get("value") is not None and status in {
                "missing", "invalid", "not_observed_zero_rank", "not_applicable"
            }:
                errors.append(f"NON_NULL_METRIC_STATUS_INVALID:{keyword_id}:{metric_name}:{status}")
            source_id = metric.get("source_id")
            if source_id is not None and str(source_id) not in source_index:
                errors.append(f"METRIC_UNKNOWN_SOURCE:{keyword_id}:{metric_name}:{source_id}")
            semantic = str(metric.get("semantic") or "").casefold()
            if not semantic:
                errors.append(f"METRIC_SEMANTIC_MISSING:{keyword_id}:{metric_name}")
            _validate_period(metric.get("period"), errors, f"METRIC:{keyword_id}:{metric_name}")
            if (
                metric_name == "cvr"
                and "amazon_account_cvr" in semantic
                and "not_amazon_account_cvr" not in semantic
            ):
                errors.append(f"THIRD_PARTY_CVR_MASQUERADES_AS_ACCOUNT_CVR:{keyword_id}")
            if metric_name == "cvr" and "purchase" in semantic:
                errors.append(f"PURCHASE_RATE_MAPPED_TO_CVR:{keyword_id}")
            if metric_name == "cvr" and "top3" in semantic and "conversion" in semantic:
                errors.append(f"TOP3_CONVERSION_SHARE_MAPPED_TO_CVR:{keyword_id}")
            if metric_name == "purchase_rate":
                if source_mode != "sellersprite":
                    errors.append(f"PURCHASE_RATE_OUTSIDE_SELLERSPRITE:{keyword_id}")
                if "not_amazon_ads_cvr" not in semantic:
                    errors.append(f"PURCHASE_RATE_SEMANTIC_UNSAFE:{keyword_id}")
            if source_mode == "sellersprite" and metric_name in {"monthly_search_volume", "monthly_purchases", "purchase_rate"}:
                period = metric.get("period") or {}
                source = source_index.get(str(source_id), {})
                if not (isinstance(period, dict) and period.get("month")) and not _source_period_has_month(source):
                    errors.append(f"SELLERSPRITE_MONTH_MISSING:{keyword_id}:{metric_name}")

        coverage = record.get("coverage")
        if not isinstance(coverage, dict):
            errors.append(f"COVERAGE_NOT_OBJECT:{keyword_id}")
        else:
            confirmed = coverage.get("confirmed_competitor_count")
            count = coverage.get("competitor_coverage_count")
            asins = coverage.get("competitor_asins")
            ratio = coverage.get("coverage_ratio")
            if not isinstance(confirmed, int) or confirmed < 0 or not isinstance(count, int) or count < 0:
                errors.append(f"COVERAGE_COUNT_INVALID:{keyword_id}")
            elif not isinstance(asins, list) or count != len(set(map(str, asins))):
                errors.append(f"COVERAGE_ASIN_COUNT_MISMATCH:{keyword_id}")
            elif count > confirmed:
                errors.append(f"COVERAGE_EXCEEDS_CONFIRMED_POOL:{keyword_id}")
            if isinstance(confirmed, int) and confirmed == 0:
                if ratio is not None:
                    errors.append(f"ZERO_DENOMINATOR_RATIO_NOT_NULL:{keyword_id}")
                if not str(coverage.get("coverage_missing_reason") or "").strip():
                    errors.append(f"ZERO_DENOMINATOR_REASON_MISSING:{keyword_id}")
            elif isinstance(confirmed, int) and confirmed > 0 and isinstance(count, int):
                expected_ratio = count / confirmed
                if not isinstance(ratio, (int, float)) or not math.isclose(float(ratio), expected_ratio, rel_tol=0, abs_tol=1e-9):
                    errors.append(f"COVERAGE_RATIO_MISMATCH:{keyword_id}")

    record_ids = set(record_index)
    listing_pools = _validate_pools(package.get("listing_pools"), record_ids, errors, "LISTING")
    ad_pools = _validate_pools(package.get("pools"), record_ids, errors, "ADS")
    _require_pool_keys(listing_pools, REQUIRED_LISTING_POOLS, errors, "LISTING")
    _require_pool_keys(ad_pools, REQUIRED_AD_POOLS, errors, "ADS")
    if not ad_pools.get("exact_p1", set()).issubset(ad_pools.get("exact", set())):
        errors.append("EXACT_P1_NOT_SUBSET_OF_EXACT")
    if not ad_pools.get("exact_p2", set()).issubset(ad_pools.get("exact", set())):
        errors.append("EXACT_P2_NOT_SUBSET_OF_EXACT")

    listing_ids = set().union(*listing_pools.values()) if listing_pools else set()
    positive_ad_ids = set().union(*(ad_pools.get(name, set()) for name in POSITIVE_AD_POOLS))
    assigned_ids = listing_ids | positive_ad_ids | ad_pools.get("observe", set())
    eligibility_decisions = _ELIGIBILITY.evaluate_package(package,authoritative_context={'series_root':str(series) if series else None})
    for keyword_id, record in record_index.items():
        decision = eligibility_decisions[keyword_id]
        if keyword_id in listing_ids and not decision["listing_eligible"]:
            errors.append(f"PRODUCT_INELIGIBLE_IN_LISTING_POOL:{keyword_id}:{decision['status']}")
        if keyword_id in positive_ad_ids and not decision["ad_eligible"]:
            errors.append(f"PRODUCT_INELIGIBLE_IN_AD_POOL:{keyword_id}:{decision['status']}")
        for gap in decision["evidence_gaps"]:
            warnings.append(f"PRODUCT_QUALIFICATION_GAP:{keyword_id}:{gap}")
        disposition = record.get("disposition")
        if disposition == "retained" and keyword_id not in assigned_ids and not str(record.get("unassigned_reason") or "").strip():
            errors.append(f"RETAINED_RECORD_UNASSIGNED:{keyword_id}")
        if disposition in {"brand_isolated", "compliance_negative", "low_relevance_negative"} and keyword_id in listing_ids:
            errors.append(f"INELIGIBLE_RECORD_IN_LISTING_POOL:{keyword_id}:{disposition}")
        if disposition in {"brand_isolated", "compliance_negative", "low_relevance_negative"} and keyword_id in positive_ad_ids:
            errors.append(f"INELIGIBLE_RECORD_IN_POSITIVE_AD_POOL:{keyword_id}:{disposition}")
        if disposition == "observe" and keyword_id in positive_ad_ids:
            errors.append(f"OBSERVE_RECORD_IN_POSITIVE_AD_POOL:{keyword_id}")
        if record.get("claim_restricted") and disposition in {"compliance_negative", "low_relevance_negative"}:
            warnings.append(f"CLAIM_RESTRICTED_TERM_ALSO_NEGATIVE_REVIEW:{keyword_id}")

    negative_index, _ = _ids(package.get("negative_records"), "negative_id", errors, "NEGATIVE")
    negative_pools = _validate_pools(package.get("negative_pools"), set(negative_index), errors, "NEGATIVE_POOL")
    _require_pool_keys(negative_pools, REQUIRED_NEGATIVE_POOLS, errors, "NEGATIVE")
    for negative_id, record in negative_index.items():
        lineage = record.get("lineage")
        if not isinstance(lineage, list) or not lineage:
            errors.append(f"NEGATIVE_LINEAGE_EMPTY:{negative_id}")
        else:
            for position, link in enumerate(lineage):
                source_id = str(link.get("source_id") if isinstance(link, dict) else "")
                if source_id not in source_index:
                    errors.append(f"NEGATIVE_UNKNOWN_SOURCE:{negative_id}:{position}:{source_id}")
        if record.get("match_type") not in {"exact", "phrase"}:
            errors.append(f"NEGATIVE_MATCH_INVALID:{negative_id}")

    asin_index, _ = _ids(package.get("asin_records"), "asin_id", errors, "ASIN")
    asin_pools = _validate_pools(package.get("asin_pools"), set(asin_index), errors, "ASIN_POOL")
    _require_pool_keys(asin_pools, REQUIRED_ASIN_POOLS, errors, "ASIN")
    for asin_id, record in asin_index.items():
        if not re.fullmatch(r"[A-Z0-9]{10}", str(record.get("asin") or "")):
            errors.append(f"ASIN_INVALID:{asin_id}")
        lineage = record.get("lineage")
        if not isinstance(lineage, list) or not lineage:
            errors.append(f"ASIN_LINEAGE_EMPTY:{asin_id}")
        else:
            for position, link in enumerate(lineage):
                source_id = str(link.get("source_id") if isinstance(link, dict) else "")
                if source_id not in source_index:
                    errors.append(f"ASIN_UNKNOWN_SOURCE:{asin_id}:{position}:{source_id}")

    conservation = package.get("conservation")
    if not isinstance(conservation, dict):
        errors.append("CONSERVATION_NOT_OBJECT")
        conservation = {}
    try:
        raw_rows = int(conservation.get("raw_rows"))
        blank = int(conservation.get("blank_keyword_rows"))
        invalid = int(conservation.get("invalid_rows"))
        valid_rows = int(conservation.get("normalized_valid_rows"))
        unique = int(conservation.get("unique_keywords"))
        merged = int(conservation.get("merged_duplicate_rows"))
        records_count = int(conservation.get("records_count"))
        numeric_counts = (raw_rows, blank, invalid, valid_rows, unique, merged, records_count)
        if any(value < 0 for value in numeric_counts):
            errors.append("CONSERVATION_NEGATIVE_COUNT")
        if raw_rows != blank + invalid + valid_rows:
            errors.append("CONSERVATION_RAW_ROWS_MISMATCH")
        if valid_rows != unique + merged:
            errors.append("CONSERVATION_NORMALIZED_ROWS_MISMATCH")
        disposition_counts = conservation.get("disposition_counts") or {}
        disposition_total = sum(int(disposition_counts.get(name, -1)) for name in VALID_DISPOSITIONS)
        if unique != disposition_total:
            errors.append("CONSERVATION_DISPOSITION_SUM_MISMATCH")
        if unique != records_count or records_count != len(record_index):
            errors.append("CONSERVATION_RECORD_COUNT_MISMATCH")
        for name, actual in actual_dispositions.items():
            if int(disposition_counts.get(name, -1)) != actual:
                errors.append(f"CONSERVATION_DISPOSITION_ACTUAL_MISMATCH:{name}")
    except (TypeError, ValueError):
        errors.append("CONSERVATION_NON_INTEGER")

    handoff = package.get("handoff") or {}
    package_ref = ((handoff.get("strategy_brief_inputs") or {}).get("package_ref") or {}) if isinstance(handoff, dict) else {}
    if not package_ref:
        errors.append("HANDOFF_PACKAGE_REF_MISSING")
    else:
        for field in ("package_id", "package_version", "package_hash"):
            if package_ref.get(field) != package.get(field):
                errors.append(f"HANDOFF_PACKAGE_REF_MISMATCH:{field}")
    if not isinstance(handoff, dict) or handoff.get("never_authorizes_external_upload") is not True:
        errors.append("HANDOFF_UPLOAD_BOUNDARY_MISSING")

    eligibility = package.get("downstream_eligibility") or {}
    if not isinstance(eligibility, dict) or eligibility.get("external_upload") != "never_authorized_by_keyword_package":
        errors.append("EXTERNAL_UPLOAD_BOUNDARY_INVALID")
    if package.get("run_mode") == "demo_direct_run" and package.get("demo_label") != "DEMO_ONLY_NOT_UPLOADABLE":
        errors.append("DEMO_LABEL_MISSING")
    if package.get("run_mode") not in {"production", "demo_direct_run"}:
        errors.append(f"INVALID_RUN_MODE:{package.get('run_mode')}")

    window = package.get("data_explanation_window")
    if not isinstance(window, dict):
        errors.append("DATA_EXPLANATION_WINDOW_MISSING")
    else:
        for field in ("obtained_facts", "derived", "simulated", "missing_and_impact", "continue_and_revision"):
            if not isinstance(window.get(field), list):
                errors.append(f"DATA_EXPLANATION_FIELD_INVALID:{field}")
    revision = package.get("late_data_revision")
    if not isinstance(revision, dict) or revision.get("policy") != "impact_scoped":
        errors.append("LATE_DATA_REVISION_POLICY_INVALID")
    elif revision.get("invalidates_downstream_hashes") is not True:
        errors.append("LATE_DATA_HASH_INVALIDATION_MISSING")

    acceptance = package.get("acceptance_checks")
    if not isinstance(acceptance, dict):
        errors.append("ACCEPTANCE_CHECKS_NOT_OBJECT")
    elif not acceptance:
        warnings.append("ACCEPTANCE_CHECKS_EMPTY:validation_report_must_be_referenced_before_handoff")

    return {
        "status": "pass" if not errors else "fail",
        "contract_version": CONTRACT_VERSION,
        "canonicalization_version": CANONICALIZATION_VERSION,
        "package_id": package.get("package_id"),
        "package_version": package.get("package_version"),
        "package_hash": package.get("package_hash"),
        "computed_hash": expected_hash,
        "source_mode": source_mode,
        "eligibility_decisions": eligibility_decisions,
        "counts": {
            "sources": len(source_index),
            "records": len(record_index),
            "negative_records": len(negative_index),
            "asin_records": len(asin_index),
            "mcp_calls": len(calls),
        },
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
    }


def write_json_yaml(path: Path, package: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def stamp_self_reference(package: dict[str, Any], package_hash: str) -> None:
    handoff = package.setdefault("handoff", {})
    strategy_inputs = handoff.setdefault("strategy_brief_inputs", {})
    package_ref = strategy_inputs.setdefault("package_ref", {})
    package_ref["package_id"] = package.get("package_id")
    package_ref["package_version"] = package.get("package_version")
    package_ref["package_hash"] = package_hash


def main() -> int:
    parser = argparse.ArgumentParser(description="Keyword Package 2.0 Hash 与业务校验")
    subparsers = parser.add_subparsers(dest="command", required=True)

    hash_parser = subparsers.add_parser("hash", help="计算规范化 package_hash")
    hash_parser.add_argument("--input", required=True, type=Path)
    hash_parser.add_argument("--write", action="store_true", help="写回 package_hash；输出为 JSON 语法 YAML")

    validate_parser = subparsers.add_parser("validate", help="校验词包契约")
    validate_parser.add_argument("--input", required=True, type=Path)
    validate_parser.add_argument("--expected-source", choices=sorted(VALID_SOURCES))
    validate_parser.add_argument("--report", type=Path)

    finalize_parser = subparsers.add_parser("finalize", help="写入 Hash 后校验并保存报告")
    finalize_parser.add_argument("--input", required=True, type=Path)
    finalize_parser.add_argument("--output", type=Path)
    finalize_parser.add_argument("--expected-source", required=True, choices=sorted(VALID_SOURCES))
    finalize_parser.add_argument("--report", required=True, type=Path)
    finalize_parser.add_argument("--series", type=Path, help="真实系列根；门禁通过后合并词库资产")

    args = parser.parse_args()
    try:
        package = load_document(args.input)
    except Exception as exc:
        print(json.dumps({"status": "fail", "errors": [f"LOAD_ERROR:{exc}"]}, ensure_ascii=False))
        return 2

    if args.command == "hash":
        stamp_self_reference(package, str(package.get("package_hash") or ""))
        value = compute_package_hash(package)
        if args.write:
            package["package_hash"] = value
            stamp_self_reference(package, value)
            write_json_yaml(args.input, package)
        print(json.dumps({"status": "ok", "package_hash": value}, ensure_ascii=False))
        return 0

    if args.command == "finalize":
        if args.report and "STARSKY_PROGRESS_FILE" not in os.environ:
            os.environ["STARSKY_PROGRESS_FILE"] = str(args.report.with_name("keyword_package.progress.json"))
        stamp_self_reference(package, str(package.get("package_hash") or ""))
        package["package_hash"] = compute_package_hash(package)
        stamp_self_reference(package, package["package_hash"])
        output = args.output or args.input
        series = args.series
        if series is None and 'gate_result' in locals():
            series = next((p for p in [output.resolve().parent, *output.resolve().parents]
                           if (p/'workflow_state.yaml').is_file()), None)
        if series is not None:
            try:
                os.environ["STARSKY_PROGRESS_PHASE"] = "写入前资格校验"
                checked = validate_package(package, args.expected_source, series=series)
                if checked['status'] != 'pass':
                    raise ValueError('incomplete expected-source package: '+'; '.join(checked['errors'][:8]))
                adapter_path = Path(__file__).resolve().parents[3]/'scripts/s2_keyword_asset_adapter.py'
                adapter_spec = importlib.util.spec_from_file_location('s2_keyword_asset_adapter', adapter_path)
                adapter = importlib.util.module_from_spec(adapter_spec); adapter_spec.loader.exec_module(adapter)
                package = adapter.merge_package(series, package, input_path=output, validated=checked)
                package['package_hash'] = compute_package_hash(package)
                stamp_self_reference(package, package['package_hash'])
            except ValueError as exc:
                result = {'status': 'fail', 'errors': [str(exc)], 'asset_written': False}
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
                print(json.dumps(result, ensure_ascii=False)); return 1
        write_json_yaml(output, package)
        os.environ["STARSKY_PROGRESS_PHASE"] = "写入后资格校验"
        result = validate_package(package, args.expected_source, series=series)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "pass" else 1

    result = validate_package(package, args.expected_source)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
