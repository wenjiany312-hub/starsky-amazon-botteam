#!/usr/bin/env python3
"""Normalize one Xiyou Insight multi-ASIN keyword export.

The parser performs deterministic source preservation only. It maps Xiyou's
base metrics, expands every valid source row across every discovered export
ASIN, records null/rank status and emits source metadata plus conservation
checks. Product semantics, compliance, Listing pools, ad pools, Campaigns,
bids and uploads remain outside this script.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

try:
    import pandas as pd
except ImportError as exc:  # pragma: no cover - environment-specific
    raise SystemExit(
        "缺少 pandas/openpyxl，无法读取西柚 .xlsx。请使用 Codex 主运行时执行。"
    ) from exc


PARSER_VERSION = "4.0.0"
SOURCE_PROVIDER = "xiyou"
DEFAULT_SOURCE_ID = "SRC-XIYOU-FILE-001"
CVR_SEMANTIC = "xiyou_click_conversion_rate_mean_not_amazon_account_cvr"
TOP3_CONVERSION_SEMANTIC = "xiyou_top3_conversion_concentration_not_cvr"

# Canonical field -> known Xiyou header variants. Matching normalizes only
# visual differences; it never guesses a semantically different field.
BASE_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "keyword": ("关键词 (数据来源于西柚洞察)", "关键词"),
    "cn_translation": ("翻译", "中文翻译"),
    "source_tag": ("词标签",),
    "relevance_score": ("相关性得分",),
    "relevance_tier": ("相关性档位", "相关性"),
    "traffic_sum": ("流量总和",),
    "avg_keyword_rank": ("周平均关键词排名", "关键词排名"),
    "avg_search_volume": ("周平均搜索量", "周搜索量", "搜索量"),
    "cpc_suggested": ("CPC建议竞价($)", "CPC建议竞价", "建议竞价($)"),
    "bid_range": ("建议竞价范围($)", "建议竞价范围"),
    "click_conversion_rate_mean": ("点击转化率(均值)", "点击转化率"),
    "avg_competition_difficulty": ("周平均竞争难度", "竞争难度"),
    "competition_tier": ("竞争难度档位",),
    "organic_rank_volatility": ("自然位滚动率",),
    "top3_click_share": ("Top3周平均点击份额", "Top3点击份额"),
    "top3_conversion_share": ("Top3周平均转化份额", "Top3转化份额"),
    "top3_asins": ("Top3 ASIN", "Top3ASIN"),
    "asin_count": ("asin数量", "ASIN数量"),
}

REQUIRED_BASE_FIELDS = (
    "keyword",
    "relevance_score",
    "relevance_tier",
    "traffic_sum",
    "avg_search_volume",
)

PER_ASIN_SUFFIX_ALIASES: dict[str, tuple[str, ...]] = {
    "traffic_share": ("流量份额",),
    "traffic_ratio": ("流量占比",),
    "organic_rank": ("自然排名",),
    "organic_rank_display": ("自然排名(页码-页内排名)",),
    "sp_rank": ("SP广告排名",),
    "sp_rank_display": ("SP广告排名(页码-页内排名)",),
    "display_positions": ("展示位置",),
}

MISSING_TEXT = {
    "",
    "-",
    "--",
    "—",
    "–",
    "n/a",
    "na",
    "null",
    "none",
    "nan",
}

ZERO_RANK_TEXT = {"0", "0.0", "0-0", "0/0", "0－0", "0—0"}

BASE_OUTPUT_COLUMNS = [
    "source_id",
    "source_provider",
    "source_file",
    "source_sha256",
    "source_sheet",
    "source_row",
    "source_record_id",
    "marketplace",
    "as_of",
    "period_month",
    "source_keyword",
    "keyword",
    "cn_translation",
    "source_tag",
    "relevance_score",
    "relevance_score_status",
    "relevance_tier",
    "traffic_sum",
    "traffic_sum_status",
    "avg_keyword_rank",
    "avg_keyword_rank_status",
    "avg_search_volume",
    "avg_search_volume_status",
    "cpc_suggested",
    "cpc_suggested_status",
    "bid_range_source",
    "bid_low",
    "bid_high",
    "bid_range_status",
    "click_conversion_rate_mean",
    "click_conversion_rate_mean_status",
    "click_conversion_rate_mean_semantic",
    "avg_competition_difficulty",
    "avg_competition_difficulty_status",
    "competition_tier",
    "organic_rank_volatility",
    "organic_rank_volatility_status",
    "top3_click_share",
    "top3_click_share_status",
    "top3_conversion_share",
    "top3_conversion_share_status",
    "top3_conversion_share_semantic",
    "top3_asins",
    "asin_count",
    "asin_count_status",
    "export_observed_asin_count",
    "export_observed_asins",
]

ASIN_OUTPUT_COLUMNS = [
    "source_id",
    "source_provider",
    "source_file",
    "source_sha256",
    "source_sheet",
    "source_row",
    "source_record_id",
    "marketplace",
    "as_of",
    "period_month",
    "source_keyword",
    "keyword",
    "asin",
    "traffic_share",
    "traffic_share_status",
    "traffic_ratio",
    "traffic_ratio_status",
    "organic_rank",
    "organic_rank_status",
    "organic_rank_display",
    "organic_rank_display_status",
    "sp_rank",
    "sp_rank_status",
    "sp_rank_display",
    "sp_rank_display_status",
    "display_positions",
    "display_positions_status",
    "has_observed_signal",
]


def normalize_header(value: Any) -> str:
    """Normalize only visual header differences; retain semantic text."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", "", text).strip()


def source_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if bool(pd.isna(value)):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = unicodedata.normalize("NFKC", str(value)).strip()
    return "" if text.lower() in MISSING_TEXT else text


def normalize_keyword(value: Any) -> str:
    text = source_text(value).lower()
    text = text.replace("_", " ").replace("/", " ").replace("／", " ")
    text = re.sub(r"[\-—–]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_number(value: Any, *, percent: bool = False) -> float | None:
    if value is None:
        return None
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return None if math.isnan(number) else number

    text = source_text(value)
    if not text:
        return None
    contains_percent = "%" in text
    cleaned = (
        text.replace(",", "")
        .replace("$", "")
        .replace("￥", "")
        .replace("¥", "")
        .replace("%", "")
        .strip()
    )
    if not re.fullmatch(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", cleaned):
        return None
    number = float(cleaned)
    if contains_percent:
        return number / 100.0
    if percent and abs(number) > 1:
        return number / 100.0
    return number


def parse_bid_range(value: Any) -> tuple[float | None, float | None, str]:
    text = source_text(value)
    if not text:
        return None, None, "missing"
    # Bid ranges cannot be negative. Treat hyphens/en-dashes as separators so
    # text such as ``0.90-1.60`` never parses the upper bound as ``-1.60``.
    values = re.findall(r"(?:\d+(?:\.\d*)?|\.\d+)", text.replace(",", ""))
    if not values:
        return None, None, "invalid"
    if len(values) == 1:
        return float(values[0]), None, "provided_partial"
    return float(values[0]), float(values[1]), "provided"


def value_with_status(
    value: Any,
    *,
    percent: bool = False,
    rank: bool = False,
) -> tuple[float | None, str]:
    text = source_text(value)
    if not text:
        return None, "missing"
    parsed = parse_number(value, percent=percent)
    if parsed is None:
        return None, "invalid"
    if rank and parsed <= 0:
        return None, "not_observed_zero_rank"
    return parsed, "provided"


def rank_display_with_status(value: Any) -> tuple[str, str]:
    text = source_text(value)
    if not text:
        return "", "missing"
    compact = normalize_header(text).lower()
    if compact in ZERO_RANK_TEXT:
        return "", "not_observed_zero_rank"
    return text, "provided"


def text_with_status(value: Any) -> tuple[str, str]:
    text = source_text(value)
    return (text, "provided") if text else ("", "missing")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def parse_asin_header(header: str) -> tuple[str, str] | None:
    normalized = normalize_header(header)
    match = re.match(r"^([A-Z0-9]{10})[-_—–](.+)$", normalized, flags=re.I)
    if not match:
        return None
    return match.group(1).upper(), normalize_header(match.group(2))


def suffix_lookup() -> dict[str, str]:
    return {
        normalize_header(alias): canonical
        for canonical, aliases in PER_ASIN_SUFFIX_ALIASES.items()
        for alias in aliases
    }


def build_column_lookup(columns: list[str]) -> dict[str, str]:
    by_normalized = {normalize_header(column): column for column in columns}
    mapping: dict[str, str] = {}
    for canonical, aliases in BASE_FIELD_ALIASES.items():
        for alias in aliases:
            found = by_normalized.get(normalize_header(alias))
            if found is not None:
                mapping[canonical] = found
                break
    return mapping


def discover_per_asin_columns(headers: list[str]) -> dict[str, dict[str, str]]:
    aliases = suffix_lookup()
    result: dict[str, dict[str, str]] = {}
    for header in headers:
        parsed = parse_asin_header(header)
        if parsed is None:
            continue
        asin, raw_suffix = parsed
        canonical_suffix = aliases.get(raw_suffix)
        if canonical_suffix is None:
            continue
        result.setdefault(asin, {}).setdefault(canonical_suffix, header)
    return result


def classify_layout(headers: list[str]) -> str:
    aliases = suffix_lookup()
    pairs: list[tuple[str, str]] = []
    for header in headers:
        parsed = parse_asin_header(header)
        if parsed is None:
            continue
        asin, raw_suffix = parsed
        canonical = aliases.get(raw_suffix)
        if canonical:
            pairs.append((asin, canonical))
    if len(pairs) < 2:
        return "unknown"
    same_asin = sum(
        left[0] == right[0] and left[1] != right[1]
        for left, right in zip(pairs, pairs[1:])
    )
    same_metric = sum(
        left[1] == right[1] and left[0] != right[0]
        for left, right in zip(pairs, pairs[1:])
    )
    if same_asin > same_metric:
        return "by_asin_grouped"
    if same_metric > same_asin:
        return "by_metric_grouped"
    return "unknown"


def choose_sheet(
    path: Path,
    requested_sheet: str,
) -> tuple[str, pd.DataFrame, dict[str, str], dict[str, dict[str, str]]]:
    candidates: list[
        tuple[int, int, int, str, pd.DataFrame, dict[str, str], dict[str, dict[str, str]]]
    ] = []
    # Close the workbook explicitly. On Windows an open ExcelFile handle prevents
    # callers and regression tests from cleaning up or replacing the source file.
    with pd.ExcelFile(path) as workbook:
        sheet_names = workbook.sheet_names if requested_sheet == "auto" else [requested_sheet]
        for sheet_name in sheet_names:
            if sheet_name not in workbook.sheet_names:
                raise ValueError(f"找不到指定 Sheet：{sheet_name}")
            frame = pd.read_excel(workbook, sheet_name=sheet_name, dtype=object)
            headers = [str(column) for column in frame.columns]
            base_mapping = build_column_lookup(headers)
            asin_mapping = discover_per_asin_columns(headers)
            required_count = sum(field in base_mapping for field in REQUIRED_BASE_FIELDS)
            candidates.append(
                (
                    required_count,
                    len(asin_mapping),
                    len(frame),
                    sheet_name,
                    frame,
                    base_mapping,
                    asin_mapping,
                )
            )

    if not candidates:
        raise ValueError("工作簿没有可读取的 Sheet。")
    candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    required_count, asin_count, _, sheet_name, frame, base_mapping, asin_mapping = candidates[0]
    missing = [field for field in REQUIRED_BASE_FIELDS if field not in base_mapping]
    if missing or required_count != len(REQUIRED_BASE_FIELDS):
        raise ValueError(
            "不是可识别的西柚多 ASIN 关键词导出；缺少基础字段："
            + ", ".join(missing)
            + "。请确认导出的是‘多 ASIN 对比’关键词表。"
        )
    if asin_count == 0:
        raise ValueError("未发现 ‘ASIN-流量份额/自然排名/SP广告排名’ 等西柚重复列。")
    return sheet_name, frame, base_mapping, asin_mapping


def unknown_column_log(
    frame: pd.DataFrame,
    base_mapping: dict[str, str],
    asin_mapping: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    recognized = set(base_mapping.values())
    recognized.update(header for fields in asin_mapping.values() for header in fields.values())
    result: list[dict[str, Any]] = []
    for index, raw_header in enumerate(frame.columns, start=1):
        header = str(raw_header)
        if header in recognized:
            continue
        values = [source_text(value) for value in frame[raw_header].tolist()]
        non_empty = [value for value in values if value]
        result.append(
            {
                "column_index": index,
                "header": header,
                "normalized_header": normalize_header(header),
                "non_null_count": len(non_empty),
                "sample_values": non_empty[:3],
            }
        )
    return result


def metric_has_signal(metrics: dict[str, Any]) -> bool:
    for field in ("traffic_share", "traffic_ratio", "organic_rank", "sp_rank"):
        value = metrics.get(field)
        if isinstance(value, (int, float)) and value > 0:
            return True
    return bool(
        metrics.get("organic_rank_display")
        or metrics.get("sp_rank_display")
        or metrics.get("display_positions")
    )


def status_counts(frame: pd.DataFrame, status_columns: list[str]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for column in status_columns:
        if column not in frame.columns:
            continue
        counts = Counter(str(value) for value in frame[column].fillna("missing").tolist())
        result[column] = dict(sorted(counts.items()))
    return result


def validate_metadata(source_id: str, period_month: str | None, marketplace: str) -> None:
    if not source_id.strip():
        raise ValueError("source_id 不能为空。")
    if period_month and not re.fullmatch(r"20\d{2}-(?:0[1-9]|1[0-2])", period_month):
        raise ValueError("period-month 必须为 YYYY-MM。")
    if not marketplace.strip():
        raise ValueError("marketplace 不能为空。")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_export(
    input_path: Path,
    output_dir: Path,
    requested_sheet: str = "auto",
    *,
    source_id: str = DEFAULT_SOURCE_ID,
    as_of: str | None = None,
    period_month: str | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
    timezone: str = "America/Los_Angeles",
    marketplace: str = "US",
    run_id: str = "",
    source_relative_path: str | None = None,
    source_log_path: Path | None = None,
) -> dict[str, Any]:
    validate_metadata(source_id, period_month, marketplace)
    sheet_name, frame, base_mapping, asin_mapping = choose_sheet(input_path, requested_sheet)
    output_dir.mkdir(parents=True, exist_ok=True)

    source_id = source_id.strip()
    as_of = as_of or None
    source_relative_path = source_relative_path or input_path.name
    source_sha256 = sha256_file(input_path)
    period = {
        "start": period_start or None,
        "end": period_end or None,
        "month": period_month or None,
        "timezone": timezone,
    }
    headers = [str(column) for column in frame.columns]
    unknown_columns = unknown_column_log(frame, base_mapping, asin_mapping)

    base_rows: list[dict[str, Any]] = []
    asin_rows: list[dict[str, Any]] = []
    blank_keyword_count = 0
    invalid_row_count = 0

    for source_row_number, (_, row) in enumerate(frame.iterrows(), start=2):
        source_keyword = source_text(row[base_mapping["keyword"]])
        if not source_keyword:
            blank_keyword_count += 1
            continue
        keyword = normalize_keyword(source_keyword)
        if not keyword:
            invalid_row_count += 1
            continue

        source_record_id = f"{source_id}:ROW:{source_row_number}"
        observed_asins: list[str] = []

        for asin, columns in sorted(asin_mapping.items()):
            traffic_share, traffic_share_status = value_with_status(
                row[columns["traffic_share"]] if columns.get("traffic_share") else None,
                percent=True,
            )
            traffic_ratio, traffic_ratio_status = value_with_status(
                row[columns["traffic_ratio"]] if columns.get("traffic_ratio") else None,
                percent=True,
            )
            organic_rank, organic_rank_status = value_with_status(
                row[columns["organic_rank"]] if columns.get("organic_rank") else None,
                rank=True,
            )
            organic_display, organic_display_status = rank_display_with_status(
                row[columns["organic_rank_display"]]
                if columns.get("organic_rank_display")
                else None
            )
            sp_rank, sp_rank_status = value_with_status(
                row[columns["sp_rank"]] if columns.get("sp_rank") else None,
                rank=True,
            )
            sp_display, sp_display_status = rank_display_with_status(
                row[columns["sp_rank_display"]] if columns.get("sp_rank_display") else None
            )
            display_positions, display_positions_status = text_with_status(
                row[columns["display_positions"]] if columns.get("display_positions") else None
            )
            metrics = {
                "traffic_share": traffic_share,
                "traffic_ratio": traffic_ratio,
                "organic_rank": organic_rank,
                "organic_rank_display": organic_display,
                "sp_rank": sp_rank,
                "sp_rank_display": sp_display,
                "display_positions": display_positions,
            }
            has_signal = metric_has_signal(metrics)
            if has_signal:
                observed_asins.append(asin)

            # Deliberately emit the complete valid-row × ASIN cartesian product.
            asin_rows.append(
                {
                    "source_id": source_id,
                    "source_provider": SOURCE_PROVIDER,
                    "source_file": input_path.name,
                    "source_sha256": source_sha256,
                    "source_sheet": sheet_name,
                    "source_row": source_row_number,
                    "source_record_id": source_record_id,
                    "marketplace": marketplace,
                    "as_of": as_of,
                    "period_month": period_month,
                    "source_keyword": source_keyword,
                    "keyword": keyword,
                    "asin": asin,
                    "traffic_share": traffic_share,
                    "traffic_share_status": traffic_share_status,
                    "traffic_ratio": traffic_ratio,
                    "traffic_ratio_status": traffic_ratio_status,
                    "organic_rank": organic_rank,
                    "organic_rank_status": organic_rank_status,
                    "organic_rank_display": organic_display,
                    "organic_rank_display_status": organic_display_status,
                    "sp_rank": sp_rank,
                    "sp_rank_status": sp_rank_status,
                    "sp_rank_display": sp_display,
                    "sp_rank_display_status": sp_display_status,
                    "display_positions": display_positions,
                    "display_positions_status": display_positions_status,
                    "has_observed_signal": has_signal,
                }
            )

        relevance_score, relevance_score_status = value_with_status(
            row[base_mapping["relevance_score"]]
            if base_mapping.get("relevance_score")
            else None
        )
        traffic_sum, traffic_sum_status = value_with_status(
            row[base_mapping["traffic_sum"]] if base_mapping.get("traffic_sum") else None
        )
        avg_keyword_rank, avg_keyword_rank_status = value_with_status(
            row[base_mapping["avg_keyword_rank"]]
            if base_mapping.get("avg_keyword_rank")
            else None,
            rank=True,
        )
        avg_search_volume, avg_search_volume_status = value_with_status(
            row[base_mapping["avg_search_volume"]]
            if base_mapping.get("avg_search_volume")
            else None
        )
        cpc_suggested, cpc_suggested_status = value_with_status(
            row[base_mapping["cpc_suggested"]]
            if base_mapping.get("cpc_suggested")
            else None
        )
        bid_range_source = (
            source_text(row[base_mapping["bid_range"]]) if base_mapping.get("bid_range") else ""
        )
        bid_low, bid_high, bid_range_status = parse_bid_range(bid_range_source)
        cvr, cvr_status = value_with_status(
            row[base_mapping["click_conversion_rate_mean"]]
            if base_mapping.get("click_conversion_rate_mean")
            else None,
            percent=True,
        )
        competition_difficulty, competition_difficulty_status = value_with_status(
            row[base_mapping["avg_competition_difficulty"]]
            if base_mapping.get("avg_competition_difficulty")
            else None
        )
        organic_volatility, organic_volatility_status = value_with_status(
            row[base_mapping["organic_rank_volatility"]]
            if base_mapping.get("organic_rank_volatility")
            else None,
            percent=True,
        )
        top3_click, top3_click_status = value_with_status(
            row[base_mapping["top3_click_share"]]
            if base_mapping.get("top3_click_share")
            else None,
            percent=True,
        )
        top3_conversion, top3_conversion_status = value_with_status(
            row[base_mapping["top3_conversion_share"]]
            if base_mapping.get("top3_conversion_share")
            else None,
            percent=True,
        )
        asin_count, asin_count_status = value_with_status(
            row[base_mapping["asin_count"]] if base_mapping.get("asin_count") else None
        )

        base_rows.append(
            {
                "source_id": source_id,
                "source_provider": SOURCE_PROVIDER,
                "source_file": input_path.name,
                "source_sha256": source_sha256,
                "source_sheet": sheet_name,
                "source_row": source_row_number,
                "source_record_id": source_record_id,
                "marketplace": marketplace,
                "as_of": as_of,
                "period_month": period_month,
                "source_keyword": source_keyword,
                "keyword": keyword,
                "cn_translation": source_text(row[base_mapping["cn_translation"]])
                if base_mapping.get("cn_translation")
                else "",
                "source_tag": source_text(row[base_mapping["source_tag"]])
                if base_mapping.get("source_tag")
                else "",
                "relevance_score": relevance_score,
                "relevance_score_status": relevance_score_status,
                "relevance_tier": source_text(row[base_mapping["relevance_tier"]])
                if base_mapping.get("relevance_tier")
                else "",
                "traffic_sum": traffic_sum,
                "traffic_sum_status": traffic_sum_status,
                "avg_keyword_rank": avg_keyword_rank,
                "avg_keyword_rank_status": avg_keyword_rank_status,
                "avg_search_volume": avg_search_volume,
                "avg_search_volume_status": avg_search_volume_status,
                "cpc_suggested": cpc_suggested,
                "cpc_suggested_status": cpc_suggested_status,
                "bid_range_source": bid_range_source,
                "bid_low": bid_low,
                "bid_high": bid_high,
                "bid_range_status": bid_range_status,
                "click_conversion_rate_mean": cvr,
                "click_conversion_rate_mean_status": cvr_status,
                "click_conversion_rate_mean_semantic": CVR_SEMANTIC,
                "avg_competition_difficulty": competition_difficulty,
                "avg_competition_difficulty_status": competition_difficulty_status,
                "competition_tier": source_text(row[base_mapping["competition_tier"]])
                if base_mapping.get("competition_tier")
                else "",
                "organic_rank_volatility": organic_volatility,
                "organic_rank_volatility_status": organic_volatility_status,
                "top3_click_share": top3_click,
                "top3_click_share_status": top3_click_status,
                "top3_conversion_share": top3_conversion,
                "top3_conversion_share_status": top3_conversion_status,
                "top3_conversion_share_semantic": TOP3_CONVERSION_SEMANTIC,
                "top3_asins": source_text(row[base_mapping["top3_asins"]])
                if base_mapping.get("top3_asins")
                else "",
                "asin_count": asin_count,
                "asin_count_status": asin_count_status,
                "export_observed_asin_count": len(observed_asins),
                "export_observed_asins": "|".join(sorted(observed_asins)),
            }
        )

    base_frame = pd.DataFrame(base_rows, columns=BASE_OUTPUT_COLUMNS)
    asin_frame = pd.DataFrame(asin_rows, columns=ASIN_OUTPUT_COLUMNS)
    normalized_path = output_dir / "xiyou_keywords_normalized.csv"
    asin_path = output_dir / "xiyou_keyword_asin_evidence.csv"
    base_frame.to_csv(normalized_path, index=False, encoding="utf-8-sig")
    asin_frame.to_csv(asin_path, index=False, encoding="utf-8-sig")

    normalized_valid_rows = len(base_frame)
    unique_keywords = int(base_frame["keyword"].nunique()) if not base_frame.empty else 0
    merged_duplicate_rows = normalized_valid_rows - unique_keywords
    evidence_expected = normalized_valid_rows * len(asin_mapping)
    evidence_actual = len(asin_frame)
    raw_conservation_ok = len(frame) == (
        blank_keyword_count + invalid_row_count + normalized_valid_rows
    )
    evidence_conservation_ok = evidence_expected == evidence_actual

    metric_value_columns = [
        "relevance_score",
        "traffic_sum",
        "avg_keyword_rank",
        "avg_search_volume",
        "cpc_suggested",
        "click_conversion_rate_mean",
        "avg_competition_difficulty",
        "organic_rank_volatility",
        "top3_click_share",
        "top3_conversion_share",
        "asin_count",
    ]
    metric_missing_counts = {
        field: int(base_frame[field].isna().sum())
        if field in base_frame.columns
        else normalized_valid_rows
        for field in metric_value_columns
    }
    base_status_columns = [
        column for column in BASE_OUTPUT_COLUMNS if column.endswith("_status")
    ]
    asin_status_columns = [
        column for column in ASIN_OUTPUT_COLUMNS if column.endswith("_status")
    ]
    missing_optional_base_fields = sorted(set(BASE_FIELD_ALIASES) - set(base_mapping))
    missing_per_asin_fields = {
        asin: sorted(set(PER_ASIN_SUFFIX_ALIASES) - set(fields))
        for asin, fields in sorted(asin_mapping.items())
        if set(PER_ASIN_SUFFIX_ALIASES) - set(fields)
    }
    duplicate_examples = []
    if not base_frame.empty:
        duplicate_counts = base_frame["keyword"].value_counts()
        duplicate_examples = [
            {"keyword": keyword, "source_row_count": int(count)}
            for keyword, count in duplicate_counts[duplicate_counts > 1].head(20).items()
        ]

    outputs: dict[str, Any] = {
        "normalized_keyword_csv": normalized_path.name,
        "asin_evidence_csv": asin_path.name,
        "validation_json": "xiyou_export_validation.json",
    }
    if source_log_path is not None:
        outputs["source_log"] = str(source_log_path)

    manifest: dict[str, Any] = {
        "schema_version": "2.0",
        "parser_version": PARSER_VERSION,
        "source": SOURCE_PROVIDER,
        "source_mode": SOURCE_PROVIDER,
        "source_id": source_id,
        "source_file": input_path.name,
        "source_relative_path": source_relative_path,
        "source_sha256": source_sha256,
        "source_sheet": sheet_name,
        "marketplace": marketplace,
        "as_of": as_of,
        "period": period,
        "layout": classify_layout(headers),
        "raw_row_count": int(len(frame)),
        "blank_keyword_count": blank_keyword_count,
        "invalid_row_count": invalid_row_count,
        "normalized_valid_row_count": normalized_valid_rows,
        "unique_normalized_keyword_count": unique_keywords,
        "merged_duplicate_row_count": merged_duplicate_rows,
        "duplicate_examples": duplicate_examples,
        "discovered_export_asins": sorted(asin_mapping),
        "discovered_export_asin_count": len(asin_mapping),
        "asin_evidence_conservation": {
            "formula": "normalized_valid_rows * discovered_export_asin_count",
            "expected_rows": evidence_expected,
            "actual_rows": evidence_actual,
            "passed": evidence_conservation_ok,
        },
        "raw_row_conservation": {
            "formula": "blank_keyword_rows + invalid_rows + normalized_valid_rows",
            "expected_rows": int(len(frame)),
            "actual_rows": blank_keyword_count + invalid_row_count + normalized_valid_rows,
            "passed": raw_conservation_ok,
        },
        "base_field_mapping": base_mapping,
        "missing_optional_base_fields": missing_optional_base_fields,
        "per_asin_field_mapping": asin_mapping,
        "per_asin_field_counts": {
            canonical: sum(canonical in fields for fields in asin_mapping.values())
            for canonical in PER_ASIN_SUFFIX_ALIASES
        },
        "missing_per_asin_fields": missing_per_asin_fields,
        "unknown_columns": unknown_columns,
        "metric_missing_counts": metric_missing_counts,
        "base_metric_status_counts": status_counts(base_frame, base_status_columns),
        "asin_metric_status_counts": status_counts(asin_frame, asin_status_columns),
        "null_semantics": {
            "provided": "source field contained a parseable value",
            "missing": "source field was blank or an explicit missing marker",
            "invalid": "source field was non-empty but not parseable under its field contract",
            "not_observed_zero_rank": "rank was zero/non-positive and normalized to null, never rank 0",
        },
        "not_provided_by_export": [
            "negative_or_irrelevant_sheet",
            "product_profile_roots",
            "IP_and_compliance_blacklist",
            "confirmed_competitor_decisions",
            "amazon_account_cvr",
        ],
        "outputs": outputs,
        "data_boundary": [
            "流量份额与流量占比保持为两个字段。",
            "Top3周平均转化份额不是关键词 CVR。",
            "只有点击转化率(均值)写入 click_conversion_rate_mean，且它不是 Amazon 账户 CVR。",
            "空/0 排名不会被解释为第 0 名。",
            "Top3 ASIN 与导出 ASIN 不等于已确认竞品池。",
            "normalizer 不决定 Campaign、最终 Bid 或上传。",
        ],
        "acceptance_checks": {
            "raw_row_conservation": raw_conservation_ok,
            "asin_evidence_conservation": evidence_conservation_ok,
            "source_sha256_present": bool(re.fullmatch(r"sha256:[0-9a-f]{64}", source_sha256)),
            "source_id_present": bool(source_id),
            "unknown_columns_logged": True,
            "top3_conversion_not_cvr": True,
            "zero_rank_normalized_to_null": True,
        },
    }

    if not raw_conservation_ok or not evidence_conservation_ok:
        raise RuntimeError("西柚标准化数量守恒失败；已阻止生成可交接校验结果。")

    validation_path = output_dir / "xiyou_export_validation.json"
    write_json(validation_path, manifest)

    if source_log_path is not None:
        missing_fields = list(missing_optional_base_fields)
        if not as_of:
            missing_fields.append("as_of")
        if not period_month:
            missing_fields.append("period.month")
        source_log = {
            "schema_version": "2.0",
            "run_id": run_id,
            "package_ref": {
                "package_id": None,
                "package_version": None,
                "package_hash": None,
            },
            "source_mode": SOURCE_PROVIDER,
            "sources": [
                {
                    "source_id": source_id,
                    "provider": SOURCE_PROVIDER,
                    "acquisition_method": "file",
                    "file": {
                        "relative_path": source_relative_path,
                        "sha256": source_sha256,
                        "sheet": sheet_name,
                        "raw_rows": int(len(frame)),
                        "rows_returned": normalized_valid_rows,
                    },
                    "marketplace": marketplace,
                    "as_of": as_of,
                    "period": period,
                    "units": {
                        "avg_search_volume": "weekly_average",
                        "cpc_suggested": "USD",
                        "shares_and_rates": "ratio_0_to_1",
                        "ranks": "positive_integer_or_null",
                    },
                    "field_mapping": {
                        "base": base_mapping,
                        "per_asin": asin_mapping,
                    },
                    "unknown_columns": unknown_columns,
                    "missing_fields": sorted(set(missing_fields)),
                    "missing_counts": metric_missing_counts,
                    "row_counts": {
                        "raw": int(len(frame)),
                        "blank_keyword": blank_keyword_count,
                        "invalid": invalid_row_count,
                        "normalized_valid": normalized_valid_rows,
                        "unique_normalized_keywords": unique_keywords,
                        "merged_duplicate_rows": merged_duplicate_rows,
                        "asin_evidence_rows": evidence_actual,
                    },
                    "truncated": False,
                    "failures": [],
                    "secret_redacted": True,
                }
            ],
            "mcp": {
                "status": "skipped",
                "skipped_reason": "normalizer_processes_file_only_no_mcp_call_this_run",
                "calls": [],
            },
            "source_conflicts": [],
            "acceptance_checks": manifest["acceptance_checks"],
        }
        write_json(source_log_path, source_log)

    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="标准化西柚洞察多 ASIN 关键词导出")
    parser.add_argument("--input", required=True, type=Path, help="西柚 .xlsx 导出文件")
    parser.add_argument("--output-dir", required=True, type=Path, help="标准化输出目录")
    parser.add_argument(
        "--sheet",
        default="auto",
        help="默认 auto 自动选择；也可填写具体 Sheet 名。",
    )
    parser.add_argument("--source-id", default=DEFAULT_SOURCE_ID, help="本文件唯一 source_id")
    parser.add_argument("--as-of", default=None, help="导出/获取时间，建议 ISO-8601")
    parser.add_argument("--period-month", default=None, help="来源快照月 YYYY-MM")
    parser.add_argument("--period-start", default=None, help="来源窗口开始日期/时间")
    parser.add_argument("--period-end", default=None, help="来源窗口结束日期/时间")
    parser.add_argument(
        "--timezone",
        default="America/Los_Angeles",
        help="来源窗口时区",
    )
    parser.add_argument("--marketplace", default="US", help="Amazon marketplace")
    parser.add_argument("--run-id", default="", help="当前 run_id")
    parser.add_argument(
        "--source-relative-path",
        default=None,
        help="写入来源日志的相对路径；默认只保存文件名",
    )
    parser.add_argument(
        "--source-log",
        type=Path,
        default=None,
        help="可选：输出 JSON 语法的 06_keyword_data_source_log_xiyou.yaml",
    )
    args = parser.parse_args()

    if not args.input.is_file():
        raise SystemExit(f"找不到输入文件：{args.input}")
    if args.input.suffix.lower() != ".xlsx":
        raise SystemExit("当前仅支持西柚导出的 .xlsx。")

    try:
        manifest = normalize_export(
            args.input,
            args.output_dir,
            args.sheet,
            source_id=args.source_id,
            as_of=args.as_of,
            period_month=args.period_month,
            period_start=args.period_start,
            period_end=args.period_end,
            timezone=args.timezone,
            marketplace=args.marketplace,
            run_id=args.run_id,
            source_relative_path=args.source_relative_path,
            source_log_path=args.source_log,
        )
    except Exception as exc:
        raise SystemExit(f"西柚导出校验失败：{exc}") from exc

    print(
        json.dumps(
            {
                "status": "ok",
                "source": manifest["source"],
                "source_id": manifest["source_id"],
                "sheet": manifest["source_sheet"],
                "layout": manifest["layout"],
                "raw_row_count": manifest["raw_row_count"],
                "normalized_valid_row_count": manifest["normalized_valid_row_count"],
                "unique_normalized_keyword_count": manifest[
                    "unique_normalized_keyword_count"
                ],
                "export_asin_count": manifest["discovered_export_asin_count"],
                "asin_evidence_rows": manifest["asin_evidence_conservation"]["actual_rows"],
                "outputs": manifest["outputs"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
