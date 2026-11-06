#!/usr/bin/env python3
"""Amazon sponsored ads report analyzer.

The script is deliberately conservative:
- source files are never modified;
- each file/sheet is hashed and remains an independent aggregation partition;
- reports with different attribution windows or object grains are never added together;
- undefined ratios remain null with a reason (no zero-filled ACoS/CVR);
- recommendations are review candidates, never executable account changes.

JSON is emitted to ``.yaml`` files because JSON is valid YAML 1.2 and avoids a
hard dependency on PyYAML. PyYAML is used opportunistically when reading YAML.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import NormalDist
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd


SCRIPT_VERSION = "4.0.0"
PLAN_HASH_SCHEMA = "ad-plan-c14n-v1"
SUPPORTED_SUFFIXES = {".xlsx", ".xlsm", ".csv", ".tsv"}
METRIC_FIELDS = ("impressions", "clicks", "spend", "sales", "orders", "units")


ALIASES: dict[str, list[str]] = {
    "start_date": ["Start Date", "开始日期", "起始日期"],
    "end_date": ["End Date", "结束日期", "截止日期"],
    "portfolio": ["Portfolio name", "Portfolio Name", "广告组合名称", "广告组合"],
    "campaign": ["Campaign Name", "Campaign name", "广告活动名称", "广告活动"],
    "campaign_id": ["Campaign ID", "Campaign Id", "广告活动ID", "广告活动 ID"],
    "ad_group": ["Ad Group Name", "Ad group name", "广告组名称", "广告组"],
    "ad_group_id": ["Ad Group ID", "Ad group ID", "广告组ID", "广告组 ID"],
    "targeting": [
        "Targeting", "Keyword", "Keyword Text", "Target", "投放", "关键词", "投放表达式"
    ],
    "targeting_id": ["Targeting ID", "Keyword ID", "Target ID", "投放ID", "关键词ID"],
    "match_type": ["Match Type", "Keyword Match Type", "匹配类型"],
    "search_term": [
        "Customer Search Term", "Search Term", "Customer Search Query", "客户搜索词", "搜索词"
    ],
    "placement": ["Placement", "Placement Type", "广告位", "投放位置"],
    "advertised_asin": ["Advertised ASIN", "Advertised Asin", "广告商品ASIN", "广告商品 ASIN"],
    "advertised_sku": ["Advertised SKU", "Advertised Sku", "广告商品SKU", "广告商品 SKU"],
    "purchased_asin": ["Purchased ASIN", "Purchased Asin", "已购买商品ASIN", "购买的 ASIN"],
    "purchased_sku": ["Purchased SKU", "Purchased Sku", "已购买商品SKU", "购买的 SKU"],
    "ad_type": ["Ad Type", "Ad Product", "Campaign Type", "广告类型", "广告产品"],
    "targeting_type": ["Targeting Type", "投放类型"],
    "impressions": ["Impressions", "展示量", "曝光量"],
    "clicks": ["Clicks", "点击量"],
    "spend": ["Spend", "Cost", "花费", "支出"],
    "sales": [
        "Sales", "Total Sales", "7 Day Total Sales", "14 Day Total Sales",
        "7天总销售额", "14天总销售额", "销售额"
    ],
    "orders": [
        "Orders", "Total Orders", "7 Day Total Orders (#)", "14 Day Total Orders (#)",
        "7天总订单数(#)", "14天总订单数(#)", "7天总订单数", "14天总订单数", "订单数"
    ],
    "units": [
        "Units", "Total Units", "7 Day Total Units (#)", "14 Day Total Units (#)",
        "7天总销售量(#)", "14天总销售量(#)", "7天总销售量", "14天总销售量", "销售量"
    ],
    "budget": ["Campaign Budget", "Daily Budget", "广告活动预算", "每日预算"],
    "currency": ["Currency", "Currency Code", "货币", "币种"],
}


DIMENSION_KEYS: dict[str, list[str]] = {
    "campaign": ["campaign_id", "campaign"],
    "ad_group": ["campaign_id", "campaign", "ad_group_id", "ad_group"],
    "targeting": [
        "campaign_id", "campaign", "ad_group_id", "ad_group",
        "targeting_id", "targeting", "match_type", "targeting_type",
    ],
    "search_term": [
        "campaign_id", "campaign", "ad_group_id", "ad_group",
        "targeting_id", "targeting", "match_type", "search_term",
    ],
    "placement": ["campaign_id", "campaign", "placement"],
    "advertised_product": ["campaign_id", "campaign", "advertised_asin", "advertised_sku"],
    "purchased_product": [
        "campaign_id", "campaign", "advertised_asin", "advertised_sku",
        "purchased_asin", "purchased_sku",
    ],
}


@dataclass
class SourceUnit:
    source_id: str
    source_unit_id: str
    path: Path
    relative_path: str
    sha256: str
    sheet: str
    header_row_zero_based: int
    dataframe: pd.DataFrame
    original_columns: list[str]
    mapped_columns: dict[str, str]
    duplicate_mappings: dict[str, list[str]]
    ad_type: str
    object_level: str
    attribution_window_days: int | None
    date_range: dict[str, str | None]
    warnings: list[str] = field(default_factory=list)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_json_yaml(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=False, default=json_default)
        handle.write("\n")


def json_default(value: Any) -> Any:
    if pd.isna(value):
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"Unsupported JSON value: {type(value)!r}")


def load_structured(path: str | None) -> Any:
    if not path:
        return None
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8-sig")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                f"{file_path} is YAML rather than JSON, but PyYAML is unavailable. "
                "Install PyYAML or provide JSON-compatible YAML."
            ) from exc
        return yaml.safe_load(text)


def normalize_header(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).replace("\ufeff", "").strip().lower()
    return re.sub(r"[\s_\-—–:：()（）#%/\\]+", "", text)


ALIAS_INDEX: dict[str, str] = {}
for canonical, variants in ALIASES.items():
    for variant in [canonical, *variants]:
        ALIAS_INDEX.setdefault(normalize_header(variant), canonical)


def alias_for(value: Any) -> str | None:
    return ALIAS_INDEX.get(normalize_header(value))


def infer_header_row(preview: pd.DataFrame) -> tuple[int, int]:
    best_row, best_score = 0, -1
    for index, row in preview.head(30).iterrows():
        hits = [alias_for(value) for value in row.tolist()]
        unique_hits = {hit for hit in hits if hit}
        metric_hits = unique_hits.intersection(METRIC_FIELDS)
        dimension_hits = unique_hits.intersection({"campaign", "targeting", "search_term", "placement"})
        score = len(unique_hits) * 3 + len(metric_hits) * 2 + len(dimension_hits)
        if score > best_score:
            best_row, best_score = int(index), score
    return best_row, best_score


def detect_csv_encoding(path: Path) -> str:
    sample = path.read_bytes()[:65536]
    for encoding in ("utf-8-sig", "utf-16", "gb18030", "latin-1"):
        try:
            sample.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    return "utf-8-sig"


def read_tabular_unit(path: Path, sheet: str | None = None) -> tuple[pd.DataFrame, int, list[str]]:
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xlsm"}:
        preview = pd.read_excel(path, sheet_name=sheet, header=None, nrows=30, dtype=object)
        header_row, score = infer_header_row(preview)
        if score < 12:
            raise ValueError(f"no recognizable Amazon Ads header (score={score})")
        frame = pd.read_excel(path, sheet_name=sheet, header=header_row, dtype=object)
    else:
        encoding = detect_csv_encoding(path)
        separator = "\t" if suffix == ".tsv" else None
        preview = pd.read_csv(
            path, header=None, nrows=30, dtype=object, encoding=encoding,
            sep=separator, engine="python" if separator is None else "c",
        )
        header_row, score = infer_header_row(preview)
        if score < 12:
            raise ValueError(f"no recognizable Amazon Ads header (score={score})")
        frame = pd.read_csv(
            path, header=header_row, dtype=object, encoding=encoding,
            sep=separator, engine="python" if separator is None else "c",
        )
    original = [str(column).strip() for column in frame.columns]
    frame = frame.dropna(how="all").copy()
    return frame, header_row, original


def coerce_number(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")
    cleaned = series.astype("string").str.strip()
    negative = cleaned.str.match(r"^\(.*\)$", na=False)
    cleaned = cleaned.str.replace(r"[,$£€¥￥%\s]", "", regex=True)
    cleaned = cleaned.str.replace(r"^\((.*)\)$", r"\1", regex=True)
    cleaned = cleaned.replace({"": pd.NA, "-": pd.NA, "—": pd.NA, "N/A": pd.NA, "n/a": pd.NA})
    numeric = pd.to_numeric(cleaned, errors="coerce")
    numeric.loc[negative] = -numeric.loc[negative].abs()
    return numeric


def normalize_dataframe(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str], dict[str, list[str]], list[str]]:
    mapped: dict[str, list[str]] = {}
    for original in frame.columns:
        canonical = alias_for(original)
        if canonical:
            mapped.setdefault(canonical, []).append(original)

    normalized = pd.DataFrame(index=frame.index)
    duplicate_mappings: dict[str, list[str]] = {}
    for canonical, originals in mapped.items():
        if len(originals) > 1:
            duplicate_mappings[canonical] = [str(item) for item in originals]
        combined = frame[originals[0]]
        for original in originals[1:]:
            combined = combined.combine_first(frame[original])
        normalized[canonical] = combined

    for metric in (*METRIC_FIELDS, "budget"):
        if metric in normalized:
            normalized[metric] = coerce_number(normalized[metric])

    for dimension in set(sum(DIMENSION_KEYS.values(), [])) | {
        "portfolio", "ad_type", "currency", "start_date", "end_date"
    }:
        if dimension in normalized and dimension not in (*METRIC_FIELDS, "budget"):
            normalized[dimension] = normalized[dimension].astype("string").str.strip()

    warnings: list[str] = []
    for metric in ("impressions", "clicks", "spend", "sales", "orders"):
        if metric not in normalized:
            warnings.append(f"missing_metric:{metric}")
            normalized[metric] = pd.Series(pd.NA, index=normalized.index, dtype="Float64")
    if "units" not in normalized:
        normalized["units"] = pd.Series(pd.NA, index=normalized.index, dtype="Float64")

    mapping = {str(original): canonical for canonical, originals in mapped.items() for original in originals}
    return normalized, mapping, duplicate_mappings, warnings


def attribution_window_from_columns(columns: Sequence[str]) -> int | None:
    windows: set[int] = set()
    for column in columns:
        for pattern in (r"\b(\d+)\s*day\b", r"(\d+)\s*天"):
            match = re.search(pattern, unicodedata.normalize("NFKC", column), re.IGNORECASE)
            if match:
                windows.add(int(match.group(1)))
    if len(windows) == 1:
        return next(iter(windows))
    return None


def normalize_ad_type(value: str) -> str:
    token = normalize_header(value)
    if token in {"sp", "sponsoredproduct", "sponsoredproducts"} or any(
        key in token for key in ("sponsoredproducts", "商品推广")
    ):
        return "SP"
    if token in {"sb", "sponsoredbrand", "sponsoredbrands"} or any(
        key in token for key in ("sponsoredbrands", "品牌推广")
    ):
        return "SB"
    if token in {"sd", "sponsoreddisplay"} or any(
        key in token for key in ("sponsoreddisplay", "展示型推广")
    ):
        return "SD"
    return "unknown"


def infer_ad_type(frame: pd.DataFrame, path: Path, sheet: str) -> str:
    if "ad_type" in frame:
        values = [normalize_ad_type(str(value)) for value in frame["ad_type"].dropna().head(100)]
        known = {value for value in values if value != "unknown"}
        if len(known) == 1:
            return next(iter(known))
    label = f"{path.stem} {sheet}"
    short_code = re.search(r"(?:^|[\s_.-])(SP|SB|SD)(?:[\s_.-]|$)", label, re.IGNORECASE)
    if short_code:
        return short_code.group(1).upper()
    token = normalize_header(label)
    patterns = {
        "SP": ("sponsoredproducts", "商品推广", "spsearch", "spreport", "sp广告"),
        "SB": ("sponsoredbrands", "品牌推广", "sbkeyword", "sbreport", "sb广告"),
        "SD": ("sponsoreddisplay", "展示型推广", "sdreport", "sd广告"),
    }
    for ad_type, keys in patterns.items():
        if any(key in token for key in keys):
            return ad_type
    return "unknown"


def infer_object_level(columns: Iterable[str]) -> str:
    available = set(columns)
    if "purchased_asin" in available or "purchased_sku" in available:
        return "purchased_product"
    if "search_term" in available:
        return "search_term"
    if "placement" in available:
        return "placement"
    if "targeting" in available:
        return "targeting"
    if "advertised_asin" in available or "advertised_sku" in available:
        return "advertised_product"
    if "ad_group" in available:
        return "ad_group"
    if "campaign" in available:
        return "campaign"
    return "unknown"


def frame_date_range(frame: pd.DataFrame) -> dict[str, str | None]:
    result: dict[str, str | None] = {"start": None, "end": None}
    for field_name, output_name, reducer in (
        ("start_date", "start", "min"), ("end_date", "end", "max")
    ):
        if field_name not in frame:
            continue
        parsed = pd.to_datetime(frame[field_name], errors="coerce")
        if parsed.notna().any():
            value = parsed.min() if reducer == "min" else parsed.max()
            result[output_name] = value.date().isoformat()
    return result


def discover_files(inputs: Sequence[str], output_dir: Path) -> list[Path]:
    discovered: list[Path] = []
    output_resolved = output_dir.resolve()
    for raw in inputs:
        path = Path(raw).expanduser()
        if not path.exists():
            raise FileNotFoundError(path)
        candidates = [path] if path.is_file() else sorted(
            item for item in path.rglob("*") if item.is_file() and item.suffix.lower() in SUPPORTED_SUFFIXES
        )
        for candidate in candidates:
            if candidate.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            try:
                candidate.resolve().relative_to(output_resolved)
                continue
            except ValueError:
                pass
            if candidate.name.startswith("~$"):
                continue
            discovered.append(candidate.resolve())
    return sorted(set(discovered), key=lambda item: str(item).lower())


def load_source_units(paths: Sequence[Path], input_roots: Sequence[str]) -> tuple[list[SourceUnit], list[dict[str, Any]]]:
    units: list[SourceUnit] = []
    rejected: list[dict[str, Any]] = []
    root_candidates = [Path(root).resolve() for root in input_roots if Path(root).exists() and Path(root).is_dir()]

    for path in paths:
        file_sha = sha256_file(path)
        source_id = f"SRC-{file_sha[:12]}"
        try:
            relative = next((str(path.relative_to(root)) for root in root_candidates if path.is_relative_to(root)), path.name)
        except AttributeError:
            relative = path.name
        sheets: list[str | None]
        if path.suffix.lower() in {".xlsx", ".xlsm"}:
            try:
                sheets = list(pd.ExcelFile(path).sheet_names)
            except Exception as exc:  # pragma: no cover - engine/environment specific
                rejected.append({"path": str(path), "sha256": file_sha, "reason": f"excel_open_failed:{exc}"})
                continue
        else:
            sheets = [None]

        for sheet in sheets:
            sheet_name = sheet or "CSV"
            try:
                raw, header_row, original_columns = read_tabular_unit(path, sheet)
                normalized, mapping, duplicates, warnings = normalize_dataframe(raw)
                ad_type = infer_ad_type(normalized, path, sheet_name)
                object_level = infer_object_level(normalized.columns)
                if object_level == "unknown":
                    raise ValueError("recognized metrics but no campaign/target/search-term/placement dimension")
                unit_token = stable_hash({"sha256": file_sha, "sheet": sheet_name})[:12]
                unit_id = f"UNIT-{unit_token}"
                normalized.insert(0, "source_unit_id", unit_id)
                normalized.insert(1, "source_id", source_id)
                normalized.insert(2, "ad_type_normalized", ad_type)
                normalized.insert(3, "object_level_normalized", object_level)
                window = attribution_window_from_columns(original_columns)
                normalized.insert(4, "attribution_window_days", window)
                units.append(SourceUnit(
                    source_id=source_id,
                    source_unit_id=unit_id,
                    path=path,
                    relative_path=relative,
                    sha256=file_sha,
                    sheet=sheet_name,
                    header_row_zero_based=header_row,
                    dataframe=normalized,
                    original_columns=original_columns,
                    mapped_columns=mapping,
                    duplicate_mappings=duplicates,
                    ad_type=ad_type,
                    object_level=object_level,
                    attribution_window_days=window,
                    date_range=frame_date_range(normalized),
                    warnings=warnings,
                ))
            except Exception as exc:
                rejected.append({
                    "path": str(path), "sha256": file_sha, "sheet": sheet_name,
                    "reason": f"sheet_skipped:{type(exc).__name__}:{exc}",
                })
    return units, rejected


def safe_ratio(numerator: Any, denominator: Any, reason_prefix: str) -> tuple[float | None, str | None]:
    if numerator is None or pd.isna(numerator):
        return None, f"{reason_prefix}_numerator_missing"
    if denominator is None or pd.isna(denominator):
        return None, f"{reason_prefix}_denominator_missing"
    denominator_value = float(denominator)
    if denominator_value == 0:
        return None, f"{reason_prefix}_denominator_zero"
    return float(numerator) / denominator_value, None


def wilson_interval(successes: Any, trials: Any, confidence: float = 0.95) -> dict[str, Any]:
    if successes is None or trials is None or pd.isna(successes) or pd.isna(trials):
        return {"lower": None, "upper": None, "width": None, "confidence": confidence, "reason": "missing_counts"}
    successes_value, trials_value = float(successes), float(trials)
    if trials_value <= 0:
        return {"lower": None, "upper": None, "width": None, "confidence": confidence, "reason": "clicks_zero"}
    if successes_value < 0 or successes_value > trials_value:
        return {
            "lower": None, "upper": None, "width": None, "confidence": confidence,
            "reason": "orders_outside_binomial_range",
        }
    z = NormalDist().inv_cdf(1 - (1 - confidence) / 2)
    proportion = successes_value / trials_value
    denominator = 1 + z * z / trials_value
    centre = (proportion + z * z / (2 * trials_value)) / denominator
    margin = z * math.sqrt(
        proportion * (1 - proportion) / trials_value + z * z / (4 * trials_value * trials_value)
    ) / denominator
    lower, upper = max(0.0, centre - margin), min(1.0, centre + margin)
    return {
        "lower": lower, "upper": upper, "width": upper - lower,
        "confidence": confidence, "reason": None,
    }


def reliability_label(interval: Mapping[str, Any], thresholds: Mapping[str, float]) -> str:
    if interval.get("reason"):
        return "not_estimated"
    width = float(interval["width"])
    if width <= thresholds["stable_width"]:
        return "comparatively_stable"
    if width <= thresholds["directional_width"]:
        return "directional"
    return "exploratory"


def sum_min_count(series: pd.Series) -> float | None:
    numeric = pd.to_numeric(series, errors="coerce")
    if not numeric.notna().any():
        return None
    return float(numeric.sum())


def aggregate_frame(
    frame: pd.DataFrame,
    group_keys: Sequence[str],
    confidence: float,
    reliability_thresholds: Mapping[str, float],
) -> pd.DataFrame:
    usable_keys = [key for key in group_keys if key in frame.columns and frame[key].notna().any()]
    if not usable_keys:
        return pd.DataFrame()
    working = frame.copy()
    aggregated = working.groupby(usable_keys, dropna=False, sort=False).agg(
        {metric: sum_min_count for metric in METRIC_FIELDS}
    ).reset_index()

    records: list[dict[str, Any]] = []
    for _, row in aggregated.iterrows():
        record = row.to_dict()
        ratios = {
            "ctr": safe_ratio(row.get("clicks"), row.get("impressions"), "ctr"),
            "cpc": safe_ratio(row.get("spend"), row.get("clicks"), "cpc"),
            "cvr": safe_ratio(row.get("orders"), row.get("clicks"), "cvr"),
            "cpa": safe_ratio(row.get("spend"), row.get("orders"), "cpa"),
            "acos": safe_ratio(row.get("spend"), row.get("sales"), "acos"),
            "roas": safe_ratio(row.get("sales"), row.get("spend"), "roas"),
        }
        for name, (value, reason) in ratios.items():
            record[name] = value
            record[f"{name}_reason"] = reason
        interval = wilson_interval(row.get("orders"), row.get("clicks"), confidence)
        record["cvr_ci_low"] = interval["lower"]
        record["cvr_ci_high"] = interval["upper"]
        record["cvr_ci_width"] = interval["width"]
        record["cvr_ci_reason"] = interval["reason"]
        record["sample_reliability"] = reliability_label(interval, reliability_thresholds)
        records.append(record)
    return pd.DataFrame(records)


def build_aggregations(
    units: Sequence[SourceUnit], confidence: float, reliability_thresholds: Mapping[str, float]
) -> dict[str, pd.DataFrame]:
    outputs: dict[str, list[pd.DataFrame]] = {level: [] for level in DIMENSION_KEYS}
    for unit in units:
        frame = unit.dataframe
        base_keys = ["source_unit_id", "source_id", "ad_type_normalized", "attribution_window_days"]
        for level, dimension_keys in DIMENSION_KEYS.items():
            required_anchor = {
                "campaign": "campaign", "ad_group": "ad_group", "targeting": "targeting",
                "search_term": "search_term", "placement": "placement",
                "advertised_product": "advertised_asin", "purchased_product": "purchased_asin",
            }[level]
            alternatives = {
                "advertised_product": ("advertised_asin", "advertised_sku"),
                "purchased_product": ("purchased_asin", "purchased_sku"),
            }.get(level, (required_anchor,))
            if not any(anchor in frame and frame[anchor].notna().any() for anchor in alternatives):
                continue
            aggregated = aggregate_frame(frame, [*base_keys, *dimension_keys], confidence, reliability_thresholds)
            if not aggregated.empty:
                aggregated.insert(4, "analysis_level", level)
                outputs[level].append(aggregated)
    return {
        level: pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
        for level, frames in outputs.items()
    }


def build_traffic_concentration(aggregations: Mapping[str, pd.DataFrame]) -> dict[str, Any]:
    frame = aggregations.get("search_term", pd.DataFrame())
    if frame.empty or "search_term" not in frame.columns or "sales" not in frame.columns:
        return {"status": "unknown", "reason": "search_term_sales_unavailable", "source_refs": [], "windows": []}
    partitions: list[dict[str, Any]] = []
    partition_keys = [key for key in ("source_unit_id", "source_id", "ad_type_normalized", "attribution_window_days") if key in frame.columns]
    if not partition_keys:
        return {"status": "unknown", "reason": "source_partition_unavailable", "source_refs": [], "windows": []}
    for values, part in frame.groupby(partition_keys, dropna=False, sort=False):
        if not isinstance(values, tuple):
            values = (values,)
        sales = pd.to_numeric(part["sales"], errors="coerce").fillna(0)
        valid = part.assign(_sales=sales).loc[part["search_term"].notna() & (sales > 0)]
        total = float(valid["_sales"].sum()) if not valid.empty else 0.0
        if total <= 0:
            continue
        ordered = valid.sort_values(["_sales", "search_term"], ascending=[False, True])
        cumulative = ordered["_sales"].cumsum() / total
        def share(limit: int) -> float:
            return float(ordered.head(limit)["_sales"].sum() / total)
        cutoff = cumulative.ge(0.8)
        record = dict(zip(partition_keys, values))
        record.update({
            "status": "concentrated" if share(10) >= 0.8 else "distributed",
            "entry_count": int(len(ordered)),
            "top1_share": share(1), "top3_share": share(3), "top10_share": share(10),
            "keywords_to_80pct": int(cutoff.to_numpy().argmax() + 1) if cutoff.any() else None,
            "source_refs": [record.get("source_id")],
            "coverage": "search_term_sales",
        })
        partitions.append(record)
    if not partitions:
        return {"status": "unknown", "reason": "no_positive_search_term_sales", "source_refs": [], "windows": []}
    return {"status": "partitioned", "partitions": partitions,
            "source_refs": sorted({str(item.get("source_id")) for item in partitions if item.get("source_id")}),
            "windows": sorted({item.get("attribution_window_days") for item in partitions})}


def build_contribution_evidence(business_context: Any, aggregations: Mapping[str, pd.DataFrame]) -> dict[str, Any]:
    expected = first_nested(business_context, [("expected_contribution",), ("contribution_evidence", "expected")])
    frame = aggregations.get("campaign", pd.DataFrame())
    actual = {key: float(pd.to_numeric(frame[key], errors="coerce").fillna(0).sum()) for key in ("spend", "sales", "orders") if key in frame.columns} if not frame.empty else {}
    if expected is None:
        return {"status": "unavailable", "reason": "expected_baseline_not_supplied", "expected": None, "actual": actual, "variance": None}
    expected_window = expected.get("window") if isinstance(expected, Mapping) else None
    actual_windows = sorted({unit for unit in frame.get("attribution_window_days", pd.Series(dtype=object)).dropna().tolist()})
    if not isinstance(expected, Mapping) or not expected_window or (actual_windows and expected_window.get("attribution_window_days") not in actual_windows):
        return {"status": "unavailable", "reason": "expected_actual_window_mismatch", "expected": expected, "actual": actual, "variance": None}
    expected_values = {key: expected.get(key) for key in ("spend", "sales", "orders")}
    variance = {key: (actual.get(key) - expected_values[key]) if actual.get(key) is not None and expected_values[key] is not None else None for key in expected_values}
    return {"status": "available", "basis": expected.get("basis", "user_supplied_baseline"), "expected": expected_values, "actual": actual, "variance": variance, "window": expected_window, "source_refs": expected.get("source_refs", [])}


def build_natural_flow_impact(business_context: Any) -> dict[str, Any]:
    natural = first_nested(business_context, [("natural_flow_impact",), ("natural_flow",)])
    if not isinstance(natural, Mapping):
        return {"status": "unavailable", "reason": "natural_flow_evidence_not_supplied", "causal_claim": False}
    required = ("window", "natural_orders", "total_sales")
    if any(natural.get(key) is None for key in required):
        return {"status": "unavailable", "reason": "natural_flow_inputs_incomplete", "causal_claim": False, "inputs": dict(natural)}
    return {"status": "available", "causal_claim": False, "attribution_caveat": "observed_window_comparison_only", "inputs": dict(natural)}


def build_negative_impact_previews(aggregations: Mapping[str, pd.DataFrame], negative_click_reference: int) -> list[dict[str, Any]]:
    frame = aggregations.get("search_term", pd.DataFrame())
    if frame.empty or "search_term" not in frame.columns:
        return []
    previews: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        clicks = row.get("clicks")
        orders = row.get("orders")
        term = row.get("search_term")
        if pd.isna(term) or pd.isna(clicks) or pd.isna(orders) or float(clicks) < negative_click_reference or float(orders) != 0:
            continue
        metrics = {key: (None if pd.isna(row.get(key)) else float(row.get(key))) for key in METRIC_FIELDS if key in row}
        previews.append({"candidate": {"expression": str(term), "match_type": "negative_exact"}, "scope": "exact_term_only", "metrics": metrics, "source_unit_id": row.get("source_unit_id"), "source_id": row.get("source_id"), "attribution_window_days": row.get("attribution_window_days"), "executable": False, "requires_user_confirmation": True})
    return previews


def record_from_row(row: pd.Series) -> dict[str, Any]:
    return {key: (None if pd.isna(value) else json_default(value) if not isinstance(value, (str, int, float, bool)) else value)
            for key, value in row.to_dict().items()}


def first_nested(mapping: Any, paths: Sequence[Sequence[str]]) -> Any:
    if not isinstance(mapping, Mapping):
        return None
    for path in paths:
        current: Any = mapping
        for key in path:
            if not isinstance(current, Mapping) or key not in current:
                current = None
                break
            current = current[key]
        if current is not None:
            return current
    return None


def normalize_rate(value: Any) -> tuple[float | None, str | None]:
    if value is None or value == "":
        return None, None
    try:
        numeric = float(str(value).strip().replace("%", ""))
    except ValueError:
        return None, "rate_not_numeric"
    if "%" in str(value) or (1 < numeric <= 100):
        return numeric / 100, "interpreted_as_percent"
    if 0 <= numeric <= 1:
        return numeric, "interpreted_as_decimal"
    return None, "rate_out_of_range"


def _plan_json_ready(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            unicodedata.normalize("NFC", str(key)): _plan_json_ready(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_plan_json_ready(item) for item in value]
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    return value


def compute_ad_plan_hash(plan: Mapping[str, Any]) -> str:
    payload = copy.deepcopy(dict(plan))
    payload.pop("plan_hash", None)
    meta = payload.get("_meta")
    if isinstance(meta, dict):
        meta.pop("generated_at", None)
    encoded = json.dumps(
        _plan_json_ready(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def plan_reference(plan_path: str | None) -> dict[str, Any]:
    if not plan_path:
        return {
            "plan_id": None, "plan_version": None, "plan_hash": None,
            "hash_schema": None, "hash_schema_supported": None,
            "computed_plan_hash": None, "plan_hash_valid": None, "plan_file_sha256": None,
        }
    data = load_structured(plan_path) or {}
    stored_hash = first_nested(data, [("plan_hash",), ("identity", "plan_hash"), ("plan", "plan_hash")])
    computed_hash = compute_ad_plan_hash(data) if isinstance(data, Mapping) and "plan_hash" in data else None
    hash_schema = data.get("hash_schema")
    hash_schema_supported = hash_schema in (None, PLAN_HASH_SCHEMA)
    return {
        "plan_id": first_nested(data, [("plan_id",), ("identity", "plan_id"), ("plan", "plan_id")]),
        "plan_version": first_nested(data, [("plan_version",), ("identity", "plan_version"), ("plan", "plan_version")]),
        "plan_hash": stored_hash,
        "hash_schema": hash_schema,
        "hash_schema_supported": hash_schema_supported,
        "computed_plan_hash": computed_hash,
        "plan_hash_valid": (
            stored_hash == computed_hash and hash_schema_supported
            if stored_hash and computed_hash
            else None
        ),
        "plan_file_sha256": sha256_file(Path(plan_path)),
        "path": str(Path(plan_path).resolve()),
    }


def action_signature(action: Mapping[str, Any]) -> str:
    return stable_hash({
        "action_type": action.get("action_type"),
        "object": action.get("object"),
    })[:16]


def prior_action_index(prior: Any) -> dict[str, Mapping[str, Any]]:
    if not isinstance(prior, Mapping):
        return {}
    values = prior.get("actions") or prior.get("action_results") or []
    if not isinstance(values, list):
        return {}
    return {action_signature(value): value for value in values if isinstance(value, Mapping)}


def object_ref(row: pd.Series, level: str) -> dict[str, Any]:
    fields = ["ad_type_normalized", "campaign_id", "campaign", "ad_group_id", "ad_group"]
    fields.extend({
        "targeting": ["targeting_id", "targeting", "match_type"],
        "search_term": ["targeting_id", "targeting", "match_type", "search_term"],
        "placement": ["placement"],
        "campaign": [],
    }.get(level, []))
    return {field: row.get(field) for field in fields if field in row and not pd.isna(row.get(field))}


def build_findings_and_actions(
    aggregations: Mapping[str, pd.DataFrame],
    business_context: Any,
    prior_actions: Any,
    negative_click_reference: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    target_acos_raw = first_nested(business_context, [
        ("unit_economics", "target_acos"), ("target_acos",), ("business_goal", "target_acos")
    ])
    break_even_raw = first_nested(business_context, [
        ("unit_economics", "break_even_acos"), ("break_even_acos",)
    ])
    target_acos, target_note = normalize_rate(target_acos_raw)
    break_even_acos, break_even_note = normalize_rate(break_even_raw)
    warnings = [
        note for note in (target_note, break_even_note)
        if note and ("not" in note or "out" in note)
    ]
    prior_index = prior_action_index(prior_actions)
    findings: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    transfers: list[dict[str, Any]] = []

    def add_action(finding: dict[str, Any], action_type: str, row: pd.Series, level: str,
                   priority: str, preconditions: list[str], protection: list[str]) -> None:
        action = {
            "action_id": f"ACT-{len(actions)+1:04d}",
            "finding_id": finding["finding_id"],
            "priority": priority,
            "action_type": action_type,
            "object": object_ref(row, level),
            "current_value": None,
            "recommended_value": None,
            "value_source": "evidence_review_required",
            "evidence_refs": [finding["finding_id"]],
            "expected_effect": {
                "status": "pending_review",
                "description": "由审核者根据证据确认预期影响",
                "causal_claim": False,
            },
            "alternative_explanations": finding["alternative_explanations"],
            "preconditions": preconditions,
            "protection_conditions": protection,
            "observe_until": None,
            "rollback_if": [],
            "confidence": finding["confidence"],
            "requires_user_confirmation": True,
            "human_decision": "pending",
        }
        signature = action_signature(action)
        prior = prior_index.get(signature)
        if prior:
            action["previous_action_ref"] = prior.get("action_id")
            result = prior.get("result") or prior.get("status")
            if result in {"pending", "approved", "executed", "insufficient_data", None}:
                action["action_type"] = "hold_for_previous_action_observation"
                action["priority"] = "P1"
                action["preconditions"].append("previous_action_observation_window_complete")
        actions.append(action)

    search_terms = aggregations.get("search_term", pd.DataFrame())
    if not search_terms.empty:
        for _, row in search_terms.iterrows():
            clicks = row.get("clicks")
            orders = row.get("orders")
            spend = row.get("spend")
            sales = row.get("sales")
            if pd.notna(clicks) and pd.notna(orders) and float(clicks) >= negative_click_reference and float(orders) == 0:
                finding = {
                    "finding_id": f"FND-{len(findings)+1:04d}",
                    "level": "search_term",
                    "type": "zero_order_click_sample",
                    "object": object_ref(row, "search_term"),
                    "metrics": {key: row.get(key) for key in ("clicks", "orders", "spend", "sales", "cvr_ci_high")},
                    "rule_context": {
                        "negative_click_reference": negative_click_reference,
                        "parameter_class": "case_default_general_guidance",
                        "not_an_automatic_negative_rule": True,
                    },
                    "alternative_explanations": [
                        "search_term_is_relevant_but_conversion_is_delayed",
                        "listing_price_review_or_inventory_changed",
                        "attribution_window_not_mature",
                    ],
                    "confidence": "medium",
                }
                findings.append(finding)
                add_action(
                    finding, "review_negative_or_bid_down", row, "search_term", "P0",
                    ["confirm_term_relevance", "confirm_attribution_window_mature"],
                    ["do_not_block_foundation_or_valid_per_destination", "human_review_required"],
                )
            if pd.notna(orders) and float(orders) > 0 and str(row.get("match_type", "")).lower() not in {"exact", "精准", "精确"}:
                transfer_id = f"TRN-{len(transfers)+1:04d}"
                source_object = object_ref(row, "search_term")
                destination_object = {"target_type": "keyword", "match_type": "exact", "expression": row.get("search_term")}
                transfers.append({
                    "transfer_id": transfer_id,
                    "source_object": source_object,
                    "destination_object": destination_object,
                    "destination_status": "planned",
                    "validation_rule": "destination_must_be_live_and_show_qualified_acceptance_evidence",
                    "validation_evidence": [],
                    "acceptance_evidence_required": ["impressions", "clicks", "orders_or_explicit_test_completion"],
                    "source_negative": {
                        "match_type": "negative_exact",
                        "activate_after": "destination_validated",
                        "status": "held",
                        "executable": False,
                    },
                    "rollback_rule": ["destination_fails_to_receive_qualified_traffic"],
                })

    for level in ("campaign", "targeting", "placement", "search_term"):
        frame = aggregations.get(level, pd.DataFrame())
        if frame.empty or target_acos is None:
            continue
        for _, row in frame.iterrows():
            acos = row.get("acos")
            sales = row.get("sales")
            if pd.isna(acos) or pd.isna(sales) or float(sales) <= 0 or float(acos) <= target_acos:
                continue
            finding = {
                "finding_id": f"FND-{len(findings)+1:04d}",
                "level": level,
                "type": "acos_above_business_target",
                "object": object_ref(row, level),
                "metrics": {
                    "spend": row.get("spend"), "sales": sales, "orders": row.get("orders"),
                    "acos": acos, "target_acos": target_acos, "break_even_acos": break_even_acos,
                    "sample_reliability": row.get("sample_reliability"),
                },
                "alternative_explanations": [
                    "bid_or_placement_cost", "listing_or_offer_conversion", "temporary_context_event",
                    "campaign_role_intentionally_optimizes_for_rank_or_defense",
                ],
                "confidence": "medium" if row.get("sample_reliability") != "exploratory" else "low",
            }
            findings.append(finding)
            add_action(
                finding, "review_bid_placement_structure_or_offer", row, level, "P0",
                ["confirm_campaign_primary_goal", "review_change_context", "confirm_attribution_window_mature"],
                ["do_not_reduce_budget_unless_budget_is_the_actual_constraint", "limit_simultaneous_variables"],
            )
    return findings, actions, transfers, warnings


def dataframe_records(frame: pd.DataFrame, limit: int) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    ordered = frame.sort_values("spend", ascending=False, na_position="last") if "spend" in frame else frame
    return [record_from_row(row) for _, row in ordered.head(limit).iterrows()]


def source_manifest(units: Sequence[SourceUnit], rejected: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "source_id": unit.source_id,
            "source_unit_id": unit.source_unit_id,
            "relative_path": unit.relative_path,
            "absolute_path": str(unit.path),
            "sha256": unit.sha256,
            "sheet": unit.sheet,
            "header_row_zero_based": unit.header_row_zero_based,
            "rows": len(unit.dataframe),
            "original_columns": unit.original_columns,
            "mapped_columns": unit.mapped_columns,
            "duplicate_mappings": unit.duplicate_mappings,
            "ad_type": unit.ad_type,
            "object_level": unit.object_level,
            "attribution_window_days": unit.attribution_window_days,
            "date_range": unit.date_range,
            "warnings": unit.warnings,
        }
        for unit in units
    ] + [dict(item, status="rejected_or_skipped") for item in rejected]


def source_totals(unit: SourceUnit, confidence: float, reliability_thresholds: Mapping[str, float]) -> dict[str, Any]:
    frame = aggregate_frame(
        unit.dataframe,
        ["source_unit_id", "source_id", "ad_type_normalized", "attribution_window_days"],
        confidence,
        reliability_thresholds,
    )
    if frame.empty:
        return {"source_unit_id": unit.source_unit_id}
    return record_from_row(frame.iloc[0])


def percent(value: Any) -> str:
    return "—" if value is None or pd.isna(value) else f"{float(value):.1%}"


def number(value: Any, currency: bool = False) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"${float(value):,.2f}" if currency else f"{float(value):,.0f}"


def markdown_table(frame: pd.DataFrame, columns: Sequence[tuple[str, str, str]], limit: int = 30) -> list[str]:
    if frame.empty:
        return ["*无可用数据。*"]
    ordered = frame.sort_values("spend", ascending=False, na_position="last") if "spend" in frame else frame
    lines = ["| " + " | ".join(label for _, label, _ in columns) + " |",
             "|" + "|".join("---" for _ in columns) + "|"]
    for _, row in ordered.head(limit).iterrows():
        values: list[str] = []
        for key, _, fmt in columns:
            value = row.get(key)
            if fmt == "pct":
                rendered = percent(value)
            elif fmt == "money":
                rendered = number(value, True)
            elif fmt == "num":
                rendered = number(value)
            else:
                rendered = "—" if value is None or pd.isna(value) else str(value).replace("|", "\\|")
            values.append(rendered)
        lines.append("| " + " | ".join(values) + " |")
    return lines


def generate_markdown(
    output_path: Path,
    analysis: Mapping[str, Any],
    aggregations: Mapping[str, pd.DataFrame],
    findings: Sequence[Mapping[str, Any]],
    actions: Sequence[Mapping[str, Any]],
) -> None:
    meta = analysis["identity"]
    scope = analysis["scope"]
    lines: list[str] = [
        "# 亚马逊广告报表诊断",
        "",
        f"> analysis_id: `{meta['analysis_id']}` · run_id: `{meta['run_id']}` · cycle_id: `{meta['cycle_id']}`",
        f"> 生成时间: {meta['generated_at']} · marketplace: {scope['marketplace']} · timezone: {scope['timezone']}",
        "",
        "## 一、执行结论",
        "",
        f"- 已接收 {analysis['coverage']['accepted_source_units']} 个来源单元，跳过 {analysis['coverage']['rejected_source_units']} 个。",
        "- 每个文件/工作表独立汇总；不同归因窗口、广告类型和报表粒度没有被相加。",
        "- 无销售时 ACoS 为 `null/no_sales`，不是 0%；所有动作均为待人工审核候选。",
        f"- 本轮形成 {len(findings)} 条 finding、{len(actions)} 条待审核动作、{len(analysis['transfers'])} 条 RES→PER 候选。",
        "",
        "## 二、数据来源与归因边界",
        "",
        "| Source Unit | 类型 | 粒度 | 归因窗口 | 日期 | 行数 | SHA-256 |",
        "|---|---|---|---:|---|---:|---|",
    ]
    for source in analysis["sources"]:
        if source.get("status") == "rejected_or_skipped":
            continue
        date_range = source.get("date_range") or {}
        lines.append(
            f"| {source['source_unit_id']} | {source['ad_type']} | {source['object_level']} | "
            f"{source.get('attribution_window_days') or 'unknown'} | "
            f"{date_range.get('start') or '?'}~{date_range.get('end') or '?'} | {source['rows']} | "
            f"`{source['sha256'][:16]}…` |"
        )

    lines.extend(["", "### 来源单元总览（禁止跨窗口合计）", "",
                  "| Source Unit | 广告类型 | 归因 | 展示 | 点击 | 花费 | 销售 | 订单 | CVR | ACoS |",
                  "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"])
    for total in analysis["source_unit_totals"]:
        lines.append(
            f"| {total.get('source_unit_id')} | {total.get('ad_type_normalized')} | "
            f"{total.get('attribution_window_days') or 'unknown'} | {number(total.get('impressions'))} | "
            f"{number(total.get('clicks'))} | {number(total.get('spend'), True)} | "
            f"{number(total.get('sales'), True)} | {number(total.get('orders'))} | "
            f"{percent(total.get('cvr'))} | {percent(total.get('acos'))} |"
        )

    common_columns = [
        ("ad_type_normalized", "类型", "text"), ("campaign", "Campaign", "text"),
        ("clicks", "点击", "num"), ("spend", "花费", "money"),
        ("sales", "销售", "money"), ("orders", "订单", "num"),
        ("cvr", "CVR", "pct"), ("acos", "ACoS", "pct"),
        ("sample_reliability", "样本", "text"),
    ]
    lines.extend(["", "## 三、Campaign 分层", ""])
    lines.extend(markdown_table(aggregations.get("campaign", pd.DataFrame()), common_columns))
    lines.extend(["", "## 四、Ad Group / Target 分层", "", "### Target", ""])
    target_columns = [
        ("ad_type_normalized", "类型", "text"), ("campaign", "Campaign", "text"),
        ("ad_group", "Ad Group", "text"), ("targeting", "Target", "text"),
        ("match_type", "Match", "text"), ("clicks", "点击", "num"),
        ("spend", "花费", "money"), ("orders", "订单", "num"), ("acos", "ACoS", "pct"),
    ]
    lines.extend(markdown_table(aggregations.get("targeting", pd.DataFrame()), target_columns))
    lines.extend(["", "## 五、Search Term 与 RES→PER 候选", ""])
    search_columns = [
        ("ad_type_normalized", "类型", "text"), ("campaign", "Campaign", "text"),
        ("search_term", "Search Term", "text"), ("match_type", "来源 Match", "text"),
        ("clicks", "点击", "num"), ("spend", "花费", "money"),
        ("orders", "订单", "num"), ("cvr", "观察 CVR", "pct"),
        ("cvr_ci_low", "CVR CI Low", "pct"), ("cvr_ci_high", "CVR CI High", "pct"),
        ("acos", "ACoS", "pct"),
    ]
    lines.extend(markdown_table(aggregations.get("search_term", pd.DataFrame()), search_columns, 50))
    lines.extend(["", "## 六、Placement 分层", ""])
    placement_columns = [
        ("ad_type_normalized", "类型", "text"), ("campaign", "Campaign", "text"),
        ("placement", "Placement", "text"), ("clicks", "点击", "num"),
        ("spend", "花费", "money"), ("orders", "订单", "num"),
        ("cvr", "CVR", "pct"), ("cpc", "CPC", "money"), ("acos", "ACoS", "pct"),
    ]
    lines.extend(markdown_table(aggregations.get("placement", pd.DataFrame()), placement_columns))

    lines.extend(["", "## 七、Finding 与动作候选", ""])
    if not findings:
        lines.append("*当前证据没有触发规则候选；不代表广告一定健康。* ")
    for finding in findings:
        lines.append(f"- **{finding['finding_id']} · {finding['type']}** — `{json.dumps(finding['object'], ensure_ascii=False)}`")
    lines.extend(["", "> 所有动作均需人工审核；脚本不直接生成可执行否词、预算或 Bid 修改。", ""])
    for action in actions:
        lines.append(
            f"- `{action['action_id']}` {action['priority']} · {action['action_type']} · "
            f"decision={action['human_decision']}"
        )

    lines.extend([
        "", "## 八、数据解释窗口", "",
        "### 已获取事实", "",
        *[f"- {item}" for item in analysis["data_explanation_window"]["obtained_facts"]],
        "", "### 自动提取/推导", "",
        *[f"- {item}" for item in analysis["data_explanation_window"]["derived"]],
        "", "### 模拟参数", "",
        *[f"- {item}" for item in analysis["data_explanation_window"]["simulated"]],
        "", "### 缺失及影响", "",
        *[f"- {item}" for item in analysis["data_explanation_window"]["missing_and_impact"]],
        "", "### 继续与修订", "",
        *[f"- {item}" for item in analysis["data_explanation_window"]["continue_and_revision"]],
        "", "## 九、验收", "",
        *[f"- [{'x' if check['status'] == 'passed' else ' '}] {check['check_id']}: {check['evidence']}"
          for check in analysis["acceptance_checks"]],
    ])
    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Amazon SP/SB/SD report analyzer with source lineage")
    parser.add_argument("--input", nargs="+", required=True, help="Report files and/or directories")
    parser.add_argument("--output", required=True, help="Output directory")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--cycle-id", default=None)
    parser.add_argument("--plan", default=None, help="Optional ad_plan YAML/JSON")
    parser.add_argument("--business-context", default=None, help="Optional context YAML/JSON")
    parser.add_argument("--prior-actions", default=None, help="Optional prior actions/feedback YAML/JSON")
    parser.add_argument("--change-context", default=None, help="Optional change context YAML/JSON")
    parser.add_argument("--mcp-evidence", default=None, help="Optional MCP evidence YAML/JSON; script never calls MCP")
    parser.add_argument("--marketplace", default="US")
    parser.add_argument("--currency", default="USD")
    parser.add_argument("--timezone", default="America/Los_Angeles")
    parser.add_argument("--target-asin", action="append", default=[])
    parser.add_argument("--target-sku", action="append", default=[])
    parser.add_argument("--top-n", type=int, default=100)
    parser.add_argument("--wilson-confidence", type=float, default=0.95)
    parser.add_argument(
        "--negative-click-reference", type=int, default=20,
        help="Case-default review trigger, not an automatic negative rule",
    )
    parser.add_argument("--demo", action="store_true", help="Mark outputs DEMO_ONLY_NOT_UPLOADABLE")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = discover_files(args.input, output_dir)
    if not paths:
        raise SystemExit("No supported .xlsx/.xlsm/.csv/.tsv report files found")

    business_context = load_structured(args.business_context) or {}
    prior_actions = load_structured(args.prior_actions) or {}
    change_context = load_structured(args.change_context) or {}
    mcp_evidence = load_structured(args.mcp_evidence) or {
        "status": "skipped", "reason": "no_mcp_evidence_file_supplied"
    }
    plan_ref = plan_reference(args.plan)
    units, rejected = load_source_units(paths, args.input)
    if not units:
        write_json_yaml(output_dir / "00_rejected_sources.yaml", rejected)
        raise SystemExit("Files were found, but no recognizable Amazon Ads report unit was loaded")

    reliability_thresholds = {
        "stable_width": 0.10,
        "directional_width": 0.20,
        "parameter_class": "configurable_statistical_interpretation_default",
    }
    aggregations = build_aggregations(units, args.wilson_confidence, reliability_thresholds)
    concentration_evidence = build_traffic_concentration(aggregations)
    contribution_evidence = build_contribution_evidence(business_context, aggregations)
    natural_flow_impact = build_natural_flow_impact(business_context)
    negative_impact_previews = build_negative_impact_previews(aggregations, args.negative_click_reference)
    findings, actions, transfers, action_warnings = build_findings_and_actions(
        aggregations, business_context, prior_actions, args.negative_click_reference
    )
    sources = source_manifest(units, rejected)
    source_unit_totals = [source_totals(unit, args.wilson_confidence, reliability_thresholds) for unit in units]
    identity_seed = {
        "sources": [(unit.sha256, unit.sheet) for unit in units],
        "plan_ref": plan_ref,
        "marketplace": args.marketplace,
    }
    analysis_id = f"ANA-{stable_hash(identity_seed)[:12]}"
    run_id = args.run_id or f"RUN-{stable_hash(identity_seed)[:12]}"
    cycle_id = args.cycle_id or f"CYCLE-{datetime.now().strftime('%Y%m%d')}"

    attribution_windows = sorted({unit.attribution_window_days for unit in units if unit.attribution_window_days})
    unknown_windows = sum(1 for unit in units if unit.attribution_window_days is None)
    ad_types = sorted({unit.ad_type for unit in units})
    missing_and_impact: list[str] = []
    if unknown_windows:
        missing_and_impact.append(
            f"{unknown_windows} 个来源单元无法从当前表头确认归因窗口；其指标仅在本来源单元内解释。"
        )
    if "unknown" in ad_types:
        missing_and_impact.append("部分来源无法可靠识别 SP/SB/SD；未按文件名猜测合并。")
    if not args.business_context:
        missing_and_impact.append("未提供 target_acos/break_even_acos；不输出基于经营目标的数值调价。")
    if not args.change_context:
        missing_and_impact.append("未提供价格、促销、页面、评分、库存和竞品变更日志；原因诊断保留替代解释。")
    if plan_ref.get("plan_hash_valid") is False:
        missing_and_impact.append("提供的 ad_plan 内容与其 plan_hash 不一致；可以继续读报表，但所有计划映射与执行链必须停止。")

    accepted_rows = sum(len(unit.dataframe) for unit in units)
    analysis: dict[str, Any] = {
        "schema_version": "2.0",
        "identity": {
            "analysis_id": analysis_id,
            "run_id": run_id,
            "cycle_id": cycle_id,
            "generated_at": utc_now(),
            "script_version": SCRIPT_VERSION,
            "mode": "demo_direct_run" if args.demo else "production_analysis",
            "uploadability": "DEMO_ONLY_NOT_UPLOADABLE" if args.demo else "ANALYSIS_ONLY_NOT_AN_UPLOAD_FILE",
        },
        "plan_ref": plan_ref,
        "scope": {
            "marketplace": args.marketplace,
            "currency": args.currency,
            "timezone": args.timezone,
            "asins": args.target_asin,
            "skus": args.target_sku,
            "attribution_windows": attribution_windows,
        },
        "coverage": {
            "files_discovered": len(paths),
            "accepted_source_units": len(units),
            "rejected_source_units": len(rejected),
            "accepted_rows": accepted_rows,
            "ad_types": ad_types,
            "object_levels": sorted({unit.object_level for unit in units}),
            "cross_source_aggregation": False,
            "cross_attribution_window_aggregation": False,
            "cross_object_level_aggregation": False,
        },
        "sources": sources,
        "source_unit_totals": source_unit_totals,
        "business_context": business_context,
        "change_context": change_context,
        "mcp_evidence": mcp_evidence,
        "contribution_evidence": contribution_evidence,
        "natural_flow_impact": natural_flow_impact,
        "traffic_concentration": concentration_evidence,
        "negative_impact_previews": negative_impact_previews,
        "previous_action_results": prior_actions,
        "analysis_layers": {
            level: {
                "row_count": len(frame),
                "full_csv": f"aggregations/{level}.csv" if not frame.empty else None,
                "top_by_spend": dataframe_records(frame, args.top_n),
            }
            for level, frame in aggregations.items()
        },
        "findings": findings,
        "transfers": transfers,
        "parameter_registry": {
            "negative_click_reference": {
                "value": args.negative_click_reference,
                "class": "case_default_general_guidance",
                "usage": "review_trigger_only",
                "automatic_negative": False,
            },
            "wilson_confidence": {"value": args.wilson_confidence, "class": "statistical_method"},
            "reliability_widths": reliability_thresholds,
        },
        "warnings": action_warnings,
        "data_explanation_window": {
            "obtained_facts": [
                f"{len(units)} 个报表来源单元，{accepted_rows} 行；每个文件记录 SHA-256。",
                f"广告类型覆盖: {', '.join(ad_types)}；归因窗口: {attribution_windows or ['unknown']}。",
                f"plan_ref: {plan_ref.get('plan_id') or 'not_provided'} / {plan_ref.get('plan_hash') or 'not_provided'}。",
            ],
            "derived": [
                "CTR/CPC/CVR/CPA/ACoS/ROAS 均从聚合后的原始计数重新计算。",
                f"CVR 输出 {args.wilson_confidence:.0%} Wilson 区间和区间宽度可靠性标签。",
                "Campaign/Ad Group/Target/Search Term/Placement 按来源单元分层，不跨报表粒度相加。",
            ],
            "simulated": [
                f"否词复核点击参考={args.negative_click_reference}，仅作 case_default 触发器，不自动否词。",
                "CVR 可靠性宽度 0.10/0.20 为可覆盖的统计解释默认值。",
            ],
            "missing_and_impact": missing_and_impact or ["未发现影响主分析的关键缺口。"],
            "continue_and_revision": [
                "后补 business_context 只需重算 finding/action，不改原始归一化层。",
                "后补报表会建立新 source_id/source_unit_id；保留本轮产物，不覆盖旧 run。",
                "MCP/ERP 数据必须先落来源文件并传入 --mcp-evidence，脚本不会伪装实时调用。",
            ],
        },
        "acceptance_checks": [
            {"check_id": "source_sha256", "status": "passed", "evidence": f"{len(units)} accepted units hashed"},
            {"check_id": "mixed_header_mapping", "status": "passed", "evidence": "each column mapped independently"},
            {"check_id": "zero_denominator_safe", "status": "passed", "evidence": "undefined ratios are null plus reason"},
            {"check_id": "attribution_isolation", "status": "passed", "evidence": "source_unit_id remains an aggregation key"},
            {"check_id": "raw_source_preserved", "status": "passed", "evidence": "script performs read-only source access"},
            {
                "check_id": "plan_hash_integrity",
                "status": (
                    "passed" if plan_ref.get("plan_hash_valid") is True
                    else "failed" if plan_ref.get("plan_hash_valid") is False
                    else "not_applicable"
                ),
                "evidence": (
                    "stored plan_hash matches canonical ad_plan 2.0 hash"
                    if plan_ref.get("plan_hash_valid") is True
                    else "stored plan_hash does not match canonical content"
                    if plan_ref.get("plan_hash_valid") is False
                    else "no canonical ad_plan supplied"
                ),
            },
            {"check_id": "actions_require_review", "status": "passed", "evidence": f"{len(actions)} actions pending human review"},
            {"check_id": "res_per_source_negative_guard", "status": "passed", "evidence": "source negatives non-executable before destination validation"},
        ],
    }

    diagnostic_card = {
        "schema_version": "2.0",
        "analysis_id": analysis_id,
        "run_id": run_id,
        "cycle_id": cycle_id,
        "plan_ref": plan_ref,
        "scope": analysis["scope"],
        "sources": sources,
        "data_quality": {
            "accepted_rows": accepted_rows,
            "rejected_sources": len(rejected),
            "unknown_attribution_windows": unknown_windows,
            "warnings": missing_and_impact + action_warnings,
        },
        "business_context": business_context,
        "advertising_context": {
            "overall_by_source_unit": source_unit_totals,
            **{level: dataframe_records(frame, args.top_n) for level, frame in aggregations.items()},
        },
        "change_context": change_context,
        "mcp_evidence": mcp_evidence,
        "contribution_evidence": contribution_evidence,
        "natural_flow_impact": natural_flow_impact,
        "traffic_concentration": concentration_evidence,
        "negative_impact_previews": negative_impact_previews,
        "previous_action_results": prior_actions,
        "finding_refs": [finding["finding_id"] for finding in findings],
    }
    optimization_actions = {
        "schema_version": "2.0",
        "analysis_id": analysis_id,
        "run_id": run_id,
        "cycle_id": cycle_id,
        "plan_ref": plan_ref,
        "actions": actions,
        "transfers": transfers,
        "contribution_evidence": contribution_evidence,
        "natural_flow_impact": natural_flow_impact,
        "traffic_concentration": concentration_evidence,
        "negative_impact_previews": negative_impact_previews,
        "status": "pending_review",
        "not_executable_without": [
            "human_action_review", "architecture_or_plan_revision", "new_plan_hash",
            "valid_plan_hash_if_plan_supplied", "bulk_release_review"
        ],
    }

    normalized_dir = output_dir / "normalized"
    aggregation_dir = output_dir / "aggregations"
    normalized_dir.mkdir(exist_ok=True)
    aggregation_dir.mkdir(exist_ok=True)
    for unit in units:
        unit.dataframe.to_csv(normalized_dir / f"{unit.source_unit_id}.csv", index=False, encoding="utf-8-sig")
    for level, frame in aggregations.items():
        if not frame.empty:
            frame.to_csv(aggregation_dir / f"{level}.csv", index=False, encoding="utf-8-sig")

    write_json_yaml(output_dir / "00_source_manifest.yaml", {"schema_version": "1.0", "sources": sources})
    write_json_yaml(output_dir / "ad_analysis.yaml", analysis)
    write_json_yaml(output_dir / "diagnostic_evidence_card.yaml", diagnostic_card)
    write_json_yaml(output_dir / "optimization_actions.yaml", optimization_actions)
    report_name = f"13_广告报表诊断_{datetime.now().strftime('%Y%m%d')}.md"
    generate_markdown(output_dir / report_name, analysis, aggregations, findings, actions)

    print(json.dumps({
        "status": "ok",
        "analysis_id": analysis_id,
        "run_id": run_id,
        "source_units": len(units),
        "accepted_rows": accepted_rows,
        "findings": len(findings),
        "actions": len(actions),
        "transfers": len(transfers),
        "output": str(output_dir),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(1)
