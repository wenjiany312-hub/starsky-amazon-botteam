#!/usr/bin/env python3
"""Deterministic compiler and validator for amazon-ad-campaign-builder v4."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
import re
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


MONEY = Decimal("0.01")
LEVEL_RANK = {"simple": 0, "standard": 1, "advanced": 2}
PLAN_HASH_SCHEMA = "ad-plan-c14n-v1"


class BuildError(ValueError):
    """Raised when an input cannot be compiled safely."""


def load_data(path: str | Path) -> Dict[str, Any]:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8-sig")
    stripped = text.lstrip()
    if file_path.suffix.lower() == ".json" or stripped.startswith(("{", "[")):
        data = json.loads(text)
    else:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise BuildError(
                f"{file_path} is YAML. Install PyYAML or provide JSON (JSON is valid YAML 1.2)."
            ) from exc
        data = yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
    if not isinstance(data, dict):
        raise BuildError(f"Expected an object at {file_path}, got {type(data).__name__}")
    return data


def dump_data(data: Dict[str, Any], path: str | Path) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    if file_path.suffix.lower() == ".json":
        text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    else:
        try:
            import yaml  # type: ignore

            text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120)
        except ImportError:
            # JSON is valid YAML 1.2 and keeps the tool dependency-free.
            text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    file_path.write_text(text, encoding="utf-8")


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _get_path(data: Dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise BuildError(f"Missing case default: {path}")
        current = current[part]
    return current


class DefaultResolver:
    def __init__(self, base: Dict[str, Any], overrides: Optional[Dict[str, Any]] = None):
        self.base = copy.deepcopy(base)
        self.overrides = copy.deepcopy(overrides or {})
        self.merged = deep_merge(self.base, self.overrides)
        self.used: List[Dict[str, Any]] = []

    def value(self, path: str) -> Any:
        value = _get_path(self.merged, path)
        try:
            _get_path(self.overrides, path)
            source = "ad_strategy_brief.case_defaults_overrides"
        except BuildError:
            source = self.base.get("source", "case_defaults")
        item = {"path": path, "value": copy.deepcopy(value), "source": source, "platform_rule": False}
        if item not in self.used:
            self.used.append(item)
        return copy.deepcopy(value)


def canonical_plan_payload(plan: Dict[str, Any]) -> Dict[str, Any]:
    payload = copy.deepcopy(plan)
    payload.pop("plan_hash", None)
    meta = payload.get("_meta")
    if isinstance(meta, dict):
        meta.pop("generated_at", None)
    return payload


def _canonical_json_ready(value: Any) -> Any:
    """Normalize all strings and mapping keys for ad-plan-c14n-v1."""
    if isinstance(value, dict):
        return {
            unicodedata.normalize("NFC", str(key)): _canonical_json_ready(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_json_ready(item) for item in value]
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    return value


def compute_plan_hash(plan: Dict[str, Any]) -> str:
    encoded = json.dumps(
        _canonical_json_ready(canonical_plan_payload(plan)),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def canonical_strategy_payload(strategy: Dict[str, Any]) -> Dict[str, Any]:
    """Match the canonical payload owned by amazon-ad-strategy-architect."""
    payload = copy.deepcopy(strategy)
    payload.pop("strategy_hash", None)
    meta = payload.get("_meta")
    if isinstance(meta, dict):
        meta.pop("generated_at", None)
    return payload


def compute_strategy_hash(strategy: Dict[str, Any]) -> str:
    encoded = json.dumps(
        canonical_strategy_payload(strategy), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def validate_keyword_package_input(
    package: Dict[str, Any],
    run_mode: str,
    diagnostics: Dict[str, List[Any]],
) -> str:
    """Run the shared Keyword Package 2.0 contract before consuming any pool."""
    schema = str(package.get("schema_version") or "")
    if schema != "2.0":
        diagnostics["warnings"].append("LEGACY_KEYWORD_PACKAGE_NOT_VERIFIED")
        if run_mode == "production":
            diagnostics["blockers"].append("LEGACY_KEYWORD_PACKAGE_NOT_ALLOWED_IN_PRODUCTION")
            return "legacy_blocked"
        return "legacy_not_verified"

    validator_path = (
        Path(__file__).resolve().parents[2]
        / "_keyword_system_contract"
        / "scripts"
        / "keyword_package_contract.py"
    )
    if not validator_path.is_file():
        diagnostics["blockers"].append("KEYWORD_PACKAGE_VALIDATOR_UNAVAILABLE")
        return "validator_unavailable"
    spec = importlib.util.spec_from_file_location("keyword_package_contract_shared", validator_path)
    if spec is None or spec.loader is None:
        diagnostics["blockers"].append("KEYWORD_PACKAGE_VALIDATOR_LOAD_FAILED")
        return "validator_unavailable"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.validate_package(package)
    diagnostics["warnings"].extend(
        f"KEYWORD_PACKAGE_WARNING:{item}" for item in (result.get("warnings") or [])
    )
    if result.get("status") != "pass":
        diagnostics["blockers"].extend(
            f"KEYWORD_PACKAGE_INVALID:{item}" for item in (result.get("errors") or ["unknown"])
        )
        return "failed"
    return "verified"


def _money(value: Any) -> float:
    return float(Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP))


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def _safe_ratio(numerator: Any, denominator: Any) -> Optional[float]:
    if numerator is None or denominator in (None, 0, 0.0):
        return None
    return float(_decimal(numerator) / _decimal(denominator))


def _slug(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value or "").strip())
    return text.strip("-") or "UNSET"


def normalize_keyword_package(package: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Normalize v2 packages and legacy advertising_pools without losing provenance."""
    if package.get("records") is not None and package.get("pools") is not None:
        normalized = copy.deepcopy(package)
        normalized.setdefault("negative_records", [])
        normalized.setdefault("negative_pools", {})
        normalized.setdefault("asin_records", [])
        normalized.setdefault("asin_pools", {})
        return normalized, []

    warnings = ["LEGACY_KEYWORD_PACKAGE_NORMALIZED"]
    source = str(package.get("source") or "legacy_unknown")
    package_id = str(package.get("package_id") or f"legacy-{source}")
    records: List[Dict[str, Any]] = []
    pools: Dict[str, List[str]] = {}
    seen: Dict[str, str] = {}
    existing_by_keyword: Dict[str, List[Dict[str, Any]]] = {}
    for raw_record in package.get("records") or []:
        if isinstance(raw_record, dict):
            key = str(raw_record.get("keyword") or raw_record.get("text") or "").strip().casefold()
            if key:
                existing_by_keyword.setdefault(key, []).append(raw_record)
    for pool_name, items in (package.get("advertising_pools") or {}).items():
        if pool_name == "asin_targets":
            continue
        pools[pool_name] = []
        for item in items or []:
            if isinstance(item, dict):
                keyword = str(item.get("keyword") or item.get("text") or "").strip()
                metrics = copy.deepcopy(item.get("metrics") or {})
            else:
                keyword = str(item).strip()
                metrics = {}
            if not keyword:
                continue
            key = keyword.casefold()
            source_records = copy.deepcopy(existing_by_keyword.get(key, []))
            seed = copy.deepcopy(source_records[0]) if source_records else {}
            if isinstance(item, dict):
                seed.update(copy.deepcopy(item))
                source_records.append(copy.deepcopy(item))
            keyword_id = seen.get(key)
            if keyword_id is None:
                digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
                keyword_id = f"LEGACY-KW-{digest}"
                seen[key] = keyword_id
                records.append(
                    {
                        **seed,
                        "keyword_id": keyword_id,
                        "keyword": keyword,
                        "labels": copy.deepcopy(seed.get("labels") or []),
                        "metrics": copy.deepcopy(seed.get("metrics") or metrics),
                        "legacy_source_records": source_records,
                        "lineage": copy.deepcopy(seed.get("lineage")) or [
                            {
                                "source_id": package_id,
                                "semantic": "legacy_untyped_keyword_package",
                                "source_mode": source,
                            }
                        ],
                    }
                )
            elif isinstance(item, dict):
                existing = next(record for record in records if record["keyword_id"] == keyword_id)
                existing["legacy_source_records"].append(copy.deepcopy(item))
            pools[pool_name].append(keyword_id)

    asin_records: List[Dict[str, Any]] = []
    asin_ids: List[str] = []
    for raw in (package.get("advertising_pools") or {}).get("asin_targets", []) or []:
        asin = str(raw.get("asin") if isinstance(raw, dict) else raw).strip()
        if not asin:
            continue
        asin_id = f"LEGACY-ASIN-{asin}"
        asin_ids.append(asin_id)
        asin_records.append(
            {
                "asin_id": asin_id,
                "asin": asin,
                "labels": [],
                "metrics": {},
                "lineage": [{"source_id": package_id, "semantic": "legacy_asin_target"}],
            }
        )

    negative_records: List[Dict[str, Any]] = []
    negative_pools: Dict[str, List[str]] = {}
    for match_type, items in (package.get("negative_pools") or {}).items():
        negative_pools[match_type] = []
        for index, raw in enumerate(items or [], start=1):
            expression = str(raw.get("expression") if isinstance(raw, dict) else raw).strip()
            if not expression:
                continue
            negative_id = f"LEGACY-NEG-{match_type}-{index}"
            negative_pools[match_type].append(negative_id)
            negative_records.append(
                {
                    "negative_id": negative_id,
                    "expression": expression,
                    "match_type": match_type,
                    "reason": "legacy_negative_pool",
                    "lineage": [{"source_id": package_id, "semantic": "legacy_negative"}],
                }
            )

    return (
        {
            "schema_version": "2.0-normalized-legacy",
            "package_id": package_id,
            "package_version": package.get("package_version", 1),
            "package_hash": package.get("package_hash", "unavailable"),
            "marketplace": package.get("marketplace"),
            "product_id": package.get("product_id"),
            "sku": package.get("sku") or package.get("seller_sku"),
            "seller_skus": copy.deepcopy(package.get("seller_skus") or []),
            "source_mode": source,
            "data_sources": copy.deepcopy(package.get("data_sources") or []),
            "records": records,
            "pools": pools,
            "negative_records": negative_records,
            "negative_pools": negative_pools,
            "asin_records": asin_records,
            "asin_pools": {"pat_candidates": asin_ids},
            "compliance": copy.deepcopy(package.get("compliance") or {}),
            "product_restrictions": copy.deepcopy(package.get("product_restrictions") or []),
            "restrictions": copy.deepcopy(package.get("restrictions") or []),
        },
        warnings,
    )


def choose_complexity(
    strategy: Dict[str, Any], resolver: DefaultResolver, diagnostics: Dict[str, List[Any]]
) -> Dict[str, Any]:
    config = strategy.get("complexity") or {}
    requested = config.get("requested", "auto")
    if requested in LEVEL_RANK:
        return {
            "requested": requested,
            "selected": requested,
            "selection_basis": ["strategy_explicit"],
            "affordable_campaigns": None,
            "compression_log": [],
        }
    if requested != "auto":
        diagnostics["blockers"].append(f"INVALID_COMPLEXITY:{requested}")
        requested = "auto"

    level_order = resolver.value("complexity.level_order")
    evidence_order = resolver.value("complexity.evidence_order")
    daily_total = (strategy.get("budget") or {}).get("daily_total")
    min_budget = (strategy.get("budget") or {}).get("min_campaign_daily_budget")
    if min_budget in (None, 0, 0.0):
        min_budget = resolver.value("complexity.min_campaign_daily_budget")
    affordable = 0
    if daily_total is not None and min_budget not in (None, 0, 0.0):
        affordable = max(0, math.floor(float(daily_total) / float(min_budget)))
    else:
        diagnostics["warnings"].append("COMPLEXITY_CAPACITY_INCOMPLETE")

    maturity = config.get("evidence_maturity", "none")
    maturity_rank = evidence_order.index(maturity) if maturity in evidence_order else 0
    selected = level_order[0]
    for level in level_order:
        rule = resolver.value(f"complexity.levels.{level}")
        required_maturity = rule.get("min_evidence_maturity", evidence_order[0])
        required_rank = evidence_order.index(required_maturity) if required_maturity in evidence_order else 0
        if affordable >= int(rule.get("min_affordable_campaigns", 0)) and maturity_rank >= required_rank:
            selected = level
    return {
        "requested": "auto",
        "selected": selected,
        "selection_basis": [
            f"daily_budget={daily_total}",
            f"min_campaign_budget={min_budget}",
            f"affordable_campaigns={affordable}",
            f"evidence_maturity={maturity}",
        ],
        "affordable_campaigns": affordable,
        "compression_log": [],
    }


def select_blueprints(
    strategy: Dict[str, Any], complexity: Dict[str, Any], resolver: DefaultResolver, diagnostics: Dict[str, List[Any]]
) -> List[Dict[str, Any]]:
    blueprints = copy.deepcopy(((strategy.get("architecture") or {}).get("campaign_blueprints") or []))
    decisions = strategy.get("ad_product_decisions") or {}
    selected_rank = LEVEL_RANK.get(complexity["selected"], 0)
    candidates: List[Dict[str, Any]] = []

    for blueprint in blueprints:
        key = blueprint.get("campaign_key") or "UNNAMED"
        ad_product = blueprint.get("ad_product")
        decision_block = decisions.get(ad_product) or {}
        readiness = (decision_block.get("readiness") or {}).get("decision", "unknown")
        scope = (decision_block.get("readiness") or {}).get("recommended_scope") or []
        if readiness == "defer":
            complexity["compression_log"].append(
                {"campaign_key": key, "status": "deferred_ad_product", "loss_if_omitted": blueprint.get("loss_if_omitted")}
            )
            continue
        if readiness == "partial" and scope and blueprint.get("scope_id") not in scope:
            complexity["compression_log"].append(
                {"campaign_key": key, "status": "outside_partial_scope", "loss_if_omitted": blueprint.get("loss_if_omitted")}
            )
            continue
        minimum = blueprint.get("minimum_complexity", "simple")
        if LEVEL_RANK.get(minimum, 0) > selected_rank:
            if blueprint.get("required"):
                diagnostics["blockers"].append(f"REQUIRED_CAMPAIGN_EXCEEDS_COMPLEXITY:{key}")
                blueprint["execution_status"] = "blocked_complexity_overflow"
                candidates.append(blueprint)
                complexity["compression_log"].append(
                    {"campaign_key": key, "status": "retained_blocking", "loss_if_omitted": blueprint.get("loss_if_omitted")}
                )
            else:
                complexity["compression_log"].append(
                    {"campaign_key": key, "status": "omitted_by_complexity", "loss_if_omitted": blueprint.get("loss_if_omitted")}
                )
            continue
        candidates.append(blueprint)

    level_rule = resolver.value(f"complexity.levels.{complexity['selected']}")
    max_campaigns = (strategy.get("complexity") or {}).get("max_campaigns_override")
    if max_campaigns is None:
        max_campaigns = int(level_rule.get("max_campaigns", len(candidates)))
    required_items = [item for item in candidates if item.get("required")]
    optional_items = [item for item in candidates if not item.get("required")]
    if len(required_items) > int(max_campaigns):
        diagnostics["blockers"].append("REQUIRED_CAMPAIGN_COUNT_EXCEEDS_CAPACITY")
        return candidates
    kept = required_items + optional_items[: max(0, int(max_campaigns) - len(required_items))]
    kept_ids = {id(item) for item in kept}
    for item in candidates:
        if id(item) not in kept_ids:
            complexity["compression_log"].append(
                {
                    "campaign_key": item.get("campaign_key"),
                    "status": "omitted_by_campaign_cap",
                    "loss_if_omitted": item.get("loss_if_omitted"),
                }
            )
    return kept


def allocate_budgets(
    blueprints: List[Dict[str, Any]], daily_total: Any, diagnostics: Dict[str, List[Any]]
) -> Dict[str, float]:
    if daily_total is None:
        diagnostics["blockers"].append("MISSING_DAILY_TOTAL_BUDGET")
        return {str(item.get("campaign_key")): 0.0 for item in blueprints}
    total = _decimal(daily_total)
    weights = [_decimal(item.get("daily_budget_weight", 0)) for item in blueprints]
    if not blueprints:
        return {}
    if any(weight <= 0 for weight in weights) or sum(weights) <= 0:
        diagnostics["blockers"].append("INVALID_CAMPAIGN_BUDGET_WEIGHT")
        return {str(item.get("campaign_key")): 0.0 for item in blueprints}
    total_weight = sum(weights)
    amounts = [(total * weight / total_weight).quantize(MONEY, rounding=ROUND_HALF_UP) for weight in weights]
    remainder = total.quantize(MONEY, rounding=ROUND_HALF_UP) - sum(amounts)
    amounts[0] += remainder
    return {str(item.get("campaign_key")): float(amount) for item, amount in zip(blueprints, amounts)}


def assess_cvr_reliability(expected: Dict[str, Any], resolver: DefaultResolver) -> str:
    source_type = expected.get("source_type", "unknown")
    if source_type != "amazon_account":
        if expected.get("value") is not None:
            return "external_estimate"
        return "unknown"
    clicks = expected.get("clicks")
    orders = expected.get("orders")
    if clicks is None or orders is None:
        return "unknown"
    high = resolver.value("sample_reliability.high")
    medium = resolver.value("sample_reliability.medium")
    if clicks >= high["min_clicks"] and orders >= high["min_orders"]:
        return "high"
    if clicks >= medium["min_clicks"] and orders >= medium["min_orders"]:
        return "medium"
    return "low"


def derive_plan_economics(strategy: Dict[str, Any], diagnostics: Dict[str, List[Any]]) -> Dict[str, Any]:
    source = copy.deepcopy(strategy.get("unit_economics") or {})
    price = source.get("selling_price")
    contribution = source.get("contribution_margin_before_ads")
    break_even_acos = source.get("break_even_acos")
    target_acos = source.get("target_acos")
    if contribution is None and price not in (None, 0, 0.0) and break_even_acos is not None:
        contribution = float(_decimal(price) * _decimal(break_even_acos))
    if break_even_acos is None:
        break_even_acos = _safe_ratio(contribution, price)
    target_cpa = None
    if price is not None and target_acos is not None:
        target_cpa = _money(_decimal(price) * _decimal(target_acos))
    else:
        diagnostics["blockers"].append("MISSING_TARGET_CPA_INPUT")
    return {
        "launch_parameters": copy.deepcopy(strategy.get("launch_parameters") or {}) if strategy.get("flow_version")=="r04.s4" else {},
        "selling_price": price,
        "contribution_margin_before_ads": contribution,
        "break_even_acos": break_even_acos,
        "target_acos": target_acos,
        "target_cpa": target_cpa,
        "currency": (strategy.get("market") or {}).get("currency"),
        "source_id": source.get("source_id"),
        "exploration_override": copy.deepcopy(source.get("exploration_override") or {}),
    }


def compile_bid_guardrail(
    blueprint: Dict[str, Any], economics: Dict[str, Any], resolver: DefaultResolver, diagnostics: Dict[str, List[Any]]
) -> Dict[str, Any]:
    if blueprint.get("launch_policy")=="r04.s4":return r04_bid_guardrail(blueprint,economics,resolver,diagnostics)
    expected = copy.deepcopy(blueprint.get("expected_cvr") or {})
    reliability = assess_cvr_reliability(expected, resolver)
    expected["sample_reliability"] = reliability
    cvr = expected.get("value")
    target_cpc = break_even_cpc = allowable_cpc = None
    if cvr is not None and economics.get("target_cpa") is not None:
        target_cpc = _money(_decimal(economics["target_cpa"]) * _decimal(cvr))
    if cvr is not None and economics.get("contribution_margin_before_ads") is not None:
        break_even_cpc = _money(_decimal(economics["contribution_margin_before_ads"]) * _decimal(cvr))

    override = economics.get("exploration_override") or {}
    override_complete = bool(
        override.get("confirmed")
        and override.get("max_loss") is not None
        and override.get("expires_at")
        and override.get("rollback_if")
    )
    if target_cpc is not None and break_even_cpc is not None:
        if economics.get("target_acos") is not None and economics.get("break_even_acos") is not None:
            above_break_even = float(economics["target_acos"]) > float(economics["break_even_acos"])
        else:
            above_break_even = False
        allowable_cpc = target_cpc if above_break_even and override_complete else min(target_cpc, break_even_cpc)
        if above_break_even and not override_complete:
            diagnostics["warnings"].append(
                f"EXPLORATION_OVERRIDE_INCOMPLETE:{blueprint.get('campaign_key')}"
            )
    elif target_cpc is not None:
        allowable_cpc = target_cpc
        diagnostics["warnings"].append(f"BREAK_EVEN_CPC_UNAVAILABLE:{blueprint.get('campaign_key')}")
    elif break_even_cpc is not None:
        allowable_cpc = break_even_cpc

    bid_signal = blueprint.get("bid_signal") or {}
    intent_factor = bid_signal.get("intent_factor")
    if intent_factor is None:
        intent_factor = resolver.value("bid.intent_factor")
    safety_factor = bid_signal.get("safety_factor")
    if safety_factor is None:
        safety_factor = resolver.value("bid.safety_factor")
    signal_value = bid_signal.get("value")
    if signal_value is not None:
        candidate_bid = float(_decimal(signal_value) * _decimal(intent_factor))
    else:
        candidate_bid = allowable_cpc
        diagnostics["warnings"].append(f"BID_SIGNAL_MISSING_CAP_USED:{blueprint.get('campaign_key')}")

    placement = blueprint.get("placement_adjustments") or {}
    max_placement_pct = max([float(value) for value in placement.values()] or [0.0])
    factors = blueprint.get("effective_bid_factors") or {}
    formula_mode = factors.get("formula_mode", "unverified")
    nonzero_adjustment = max_placement_pct != 0
    factor_paths = {
        "dynamic_multiplier": "bid.neutral_dynamic_multiplier",
        "business_multiplier": "bid.neutral_business_multiplier",
        "audience_multiplier": "bid.neutral_audience_multiplier",
    }
    resolved_factors: Dict[str, float] = {}
    for key, default_path in factor_paths.items():
        value = factors.get(key)
        if value is None:
            value = resolver.value(default_path)
        resolved_factors[key] = float(value)
        nonzero_adjustment = nonzero_adjustment or float(value) != 1.0

    calculable = formula_mode == "multiplicative" or not nonzero_adjustment
    total_multiplier = None
    initial_bid = worst_bid = None
    capped = False
    if allowable_cpc is None or candidate_bid is None:
        diagnostics["blockers"].append(f"BID_GUARDRAIL_INCOMPLETE:{blueprint.get('campaign_key')}")
    elif not calculable:
        diagnostics["blockers"].append(f"EFFECTIVE_BID_FORMULA_UNVERIFIED:{blueprint.get('campaign_key')}")
    else:
        placement_multiplier = 1.0 + max_placement_pct / 100.0
        total_multiplier = (
            resolved_factors["dynamic_multiplier"]
            * resolved_factors["business_multiplier"]
            * resolved_factors["audience_multiplier"]
            * placement_multiplier
        )
        economic_start_cap = float(allowable_cpc) * float(safety_factor)
        effective_start_cap = float(allowable_cpc) / total_multiplier if total_multiplier > 0 else 0.0
        raw = min(float(candidate_bid), economic_start_cap)
        safe = min(raw, effective_start_cap)
        capped = safe + 1e-9 < float(candidate_bid)
        initial_bid = _money(safe)
        worst_bid = _money(float(initial_bid) * total_multiplier)
        if capped:
            diagnostics["normalizations"].append(
                {"code": "WORST_CASE_BID_CAPPED", "campaign_key": blueprint.get("campaign_key")}
            )

    requested_action = bid_signal.get("requested_action", "hold")
    increase_allowed = reliability in ("medium", "high")
    if requested_action == "increase" and not increase_allowed:
        diagnostics["warnings"].append(f"SMALL_SAMPLE_BID_INCREASE_BLOCKED:{blueprint.get('campaign_key')}")

    return {
        "expected_cvr": expected,
        "target_cpc": target_cpc,
        "break_even_cpc": break_even_cpc,
        "allowable_cpc": allowable_cpc,
        "demand_candidate_bid": _money(candidate_bid) if candidate_bid is not None else None,
        "intent_factor": intent_factor,
        "safety_factor": safety_factor,
        "formula_mode": formula_mode,
        "effective_bid_factors": {**resolved_factors, "max_placement_pct": max_placement_pct},
        "total_effective_multiplier": total_multiplier,
        "initial_bid": initial_bid,
        "worst_case_effective_bid": worst_bid,
        "capped": capped,
        "requested_action": requested_action,
        "increase_allowed": increase_allowed,
    }


def _index_by(items: Iterable[Dict[str, Any]], key: str) -> Dict[str, Dict[str, Any]]:
    return {str(item.get(key)): item for item in items if item.get(key) is not None}


def _lineage(item: Dict[str, Any], fallback_source: str, package_schema: str) -> List[Dict[str, Any]]:
    lineage = copy.deepcopy(item.get("lineage") or [])
    if not lineage and package_schema != "2.0":
        lineage = [{"source_id": fallback_source, "semantic": "legacy_package_lineage_unverified"}]
    return lineage


def compile_ad_groups(
    blueprint: Dict[str, Any], package: Dict[str, Any], strategy: Dict[str, Any], diagnostics: Dict[str, List[Any]]
) -> List[Dict[str, Any]]:
    keyword_index = _index_by(package.get("records") or [], "keyword_id")
    asin_index = _index_by(package.get("asin_records") or [], "asin_id")
    negative_index = _index_by(package.get("negative_records") or [], "negative_id")
    package_id = str(package.get("package_id") or "keyword-package-unknown")
    package_schema = str(package.get("schema_version") or "")
    guard_path = Path(__file__).resolve().parents[2] / "_keyword_system_contract" / "scripts" / "keyword_eligibility.py"
    guard_spec = importlib.util.spec_from_file_location("keyword_eligibility_builder", guard_path)
    eligibility = {}
    guard = None
    strategy_product = strategy.get("product") or {}
    strategy_market = strategy.get("market") or {}
    strategy_skus = strategy_product.get("seller_skus") or []
    advertised_skus = blueprint.get("advertised_skus") or []
    sku_scope_invalid = bool(strategy_skus and advertised_skus and not set(advertised_skus).issubset(set(strategy_skus)))
    if sku_scope_invalid:
        diagnostics["blockers"].append("ADVERTISED_SKU_OUTSIDE_STRATEGY")
    scope_context = {
        "product_id": strategy_product.get("product_id") or package.get("product_id"),
        "marketplace": strategy_market.get("marketplace") or strategy.get("marketplace") or package.get("marketplace"),
        "sku": (strategy_skus if sku_scope_invalid else advertised_skus) or strategy_skus
               or package.get("sku") or package.get("seller_sku") or package.get("seller_skus"),
    }
    if guard_path.is_file() and guard_spec and guard_spec.loader:
        guard = importlib.util.module_from_spec(guard_spec)
        guard_spec.loader.exec_module(guard)
        eligibility = guard.evaluate_package(package, authoritative_context=scope_context)
    else:
        diagnostics["blockers"].append("KEYWORD_ELIGIBILITY_GUARD_UNAVAILABLE")
    groups: List[Dict[str, Any]] = []

    for group in blueprint.get("ad_groups") or []:
        targets: List[Dict[str, Any]] = []
        negatives: List[Dict[str, Any]] = []
        for source in group.get("target_sources") or []:
            pool_name = source.get("pool")
            max_items = source.get("max_items")
            if pool_name in (package.get("pools") or {}):
                ids = list((package.get("pools") or {}).get(pool_name) or [])
                if max_items is not None:
                    ids = ids[: int(max_items)]
                for keyword_id in ids:
                    record = keyword_index.get(str(keyword_id))
                    if record is None:
                        diagnostics["blockers"].append(f"UNKNOWN_KEYWORD_ID:{keyword_id}")
                        continue
                    decision = eligibility.get(str(keyword_id), {})
                    if sku_scope_invalid or not decision.get("ad_eligible", False):
                        diagnostics["blockers"].append(
                            f"KEYWORD_NOT_AD_ELIGIBLE:{keyword_id}:{decision.get('status', 'pending')}"
                        )
                        continue
                    targets.append(
                        {
                            "target_id": str(keyword_id),
                            "target_type": "keyword",
                            "expression": record.get("keyword"),
                            "match_type": source.get("match_type"),
                            "source_pool": pool_name,
                            "lineage": _lineage(record, package_id, package_schema),
                            "metrics": copy.deepcopy(record.get("metrics") or {}),
                            "labels": copy.deepcopy(record.get("labels") or []),
                        }
                    )
            elif pool_name in (package.get("asin_pools") or {}):
                ids = list((package.get("asin_pools") or {}).get(pool_name) or [])
                if max_items is not None:
                    ids = ids[: int(max_items)]
                for asin_id in ids:
                    record = asin_index.get(str(asin_id))
                    if record is None:
                        diagnostics["blockers"].append(f"UNKNOWN_ASIN_ID:{asin_id}")
                        continue
                    targets.append(
                        {
                            "target_id": str(asin_id),
                            "target_type": "asin",
                            "expression": record.get("asin"),
                            "match_type": "product",
                            "source_pool": pool_name,
                            "lineage": _lineage(record, package_id, package_schema),
                            "metrics": copy.deepcopy(record.get("metrics") or {}),
                            "labels": copy.deepcopy(record.get("labels") or []),
                        }
                    )
            else:
                diagnostics["blockers"].append(f"UNKNOWN_TARGET_POOL:{pool_name}")

        for manual in group.get("manual_targets") or []:
            item = copy.deepcopy(manual)
            if item.get("target_type") == "keyword" or item.get("match_type") in {"exact", "phrase", "broad"}:
                expression = item.get("expression") or item.get("keyword")
                known = [record for record in keyword_index.values() if guard and
                         guard.term_matches(expression, record.get("keyword")) and
                         guard.term_matches(record.get("keyword"), expression)]
                manual_record = copy.deepcopy(known[0]) if known else {}
                manual_record.update(item)
                manual_record["keyword"] = expression
                manual_package = dict(package, records=[dict(manual_record, keyword_id="__manual__")])
                decision = guard.evaluate_package(manual_package, authoritative_context=scope_context).get("__manual__", {}) if guard else {}
                if (sku_scope_invalid or not decision.get("ad_eligible", False) or any(
                        not eligibility.get(str(record["keyword_id"]), {}).get("ad_eligible", False) for record in known)):
                    diagnostics["blockers"].append(f"MANUAL_KEYWORD_NOT_AD_ELIGIBLE:{expression}")
                    continue
            item.setdefault("target_id", f"MANUAL-{len(targets) + 1}")
            item.setdefault("lineage", [{"source_id": strategy.get("strategy_id"), "semantic": "strategy_manual_target"}])
            item.setdefault("metrics", {})
            targets.append(item)

        for source in group.get("negative_sources") or []:
            pool_name = source.get("pool")
            ids = list((package.get("negative_pools") or {}).get(pool_name) or [])
            max_items = source.get("max_items")
            if max_items is not None:
                ids = ids[: int(max_items)]
            if pool_name not in (package.get("negative_pools") or {}):
                diagnostics["blockers"].append(f"UNKNOWN_NEGATIVE_POOL:{pool_name}")
            for negative_id in ids:
                record = negative_index.get(str(negative_id))
                if record is None:
                    diagnostics["blockers"].append(f"UNKNOWN_NEGATIVE_ID:{negative_id}")
                    continue
                negatives.append(
                    {
                        "negative_id": str(negative_id),
                        "expression": record.get("expression"),
                        "match_type": record.get("match_type") or source.get("match_type"),
                        "status": "active",
                        "reason": record.get("reason"),
                        "lineage": _lineage(record, package_id, package_schema),
                    }
                )
        for manual in group.get("manual_negatives") or []:
            item = copy.deepcopy(manual)
            item.setdefault("negative_id", f"MANUAL-NEG-{len(negatives) + 1}")
            item.setdefault("status", "active")
            item.setdefault("lineage", [{"source_id": strategy.get("strategy_id"), "semantic": "strategy_manual_negative"}])
            negatives.append(item)

        groups.append(
            {
                "ad_group_key": group.get("ad_group_key") or f"{blueprint.get('campaign_key')}-AG-{len(groups)+1}",
                "name": group.get("name") or f"{blueprint.get('campaign_key')}-AG-{len(groups)+1}",
                "purpose": group.get("purpose") or blueprint.get("primary_goal") or "unspecified",
                "targets": targets,
                "negatives": negatives,
            }
        )
    if not groups:
        diagnostics["blockers"].append(f"NO_AD_GROUPS:{blueprint.get('campaign_key')}")
    return groups


def compile_transfer(transfer: Dict[str, Any], diagnostics: Dict[str, List[Any]]) -> Dict[str, Any]:
    item = copy.deepcopy(transfer)
    requested = ((item.get("source_negative") or {}).get("activate_after") or item.get("source_negative_after"))
    destination_status = item.get("destination_status", "planned")
    evidence = item.get("validation_evidence") or []
    source_negative = copy.deepcopy(item.get("source_negative") or {})
    source_negative["requested_activate_after"] = requested
    source_negative["activate_after"] = "destination_validated"
    if requested not in (None, "destination_validated"):
        diagnostics["normalizations"].append(
            {"code": "TRANSFER_GATE_NORMALIZED", "transfer_id": item.get("transfer_id"), "requested": requested}
        )
    if destination_status == "validated" and evidence:
        source_negative["status"] = "ready_for_review"
    else:
        source_negative["status"] = "held"
    item["source_negative"] = source_negative
    item.setdefault("validation_rule", {})
    item.setdefault("validation_evidence", [])
    item.setdefault("rollback_rule", {})
    return item


def _production_identifier_blockers(strategy: Dict[str, Any]) -> List[str]:
    product = strategy.get("product") or {}
    blockers: List[str] = []
    if not product.get("seller_skus"):
        blockers.append("PRODUCTION_MISSING_SELLER_SKU")
    if strategy.get("flow_version") != "r04.s4" and not product.get("child_asins"):
        blockers.append("PRODUCTION_MISSING_CHILD_ASIN")
    return blockers


def verify_strategy_review(
    strategy: Dict[str, Any],
    review: Optional[Dict[str, Any]],
    diagnostics: Dict[str, List[Any]],
) -> str:
    """Verify that the exact Strategy Brief version was approved for Builder."""
    schema = str(strategy.get("schema_version") or "")
    if schema == "1.0" and review is None:
        diagnostics["warnings"].append("LEGACY_STRATEGY_REVIEW_NOT_VERIFIED")
        return "legacy_not_verified"
    if not isinstance(review, dict):
        diagnostics["blockers"].append("STRATEGY_REVIEW_MISSING")
        return "missing"
    section = review.get("strategy_review") if isinstance(review.get("strategy_review"), dict) else review
    expected_status = (
        "approved_for_demo_builder"
        if strategy.get("run_mode") == "demo_direct_run"
        else "approved_for_builder"
    )
    status = str(section.get("status") or "")
    if status != expected_status:
        diagnostics["blockers"].append(
            f"STRATEGY_REVIEW_NOT_APPROVED:{status or 'missing'}:{expected_status}"
        )
    expected = {
        "strategy_id": strategy.get("strategy_id"),
        "strategy_version": strategy.get("strategy_version"),
        "strategy_hash": strategy.get("strategy_hash"),
    }
    actual = {
        "strategy_id": section.get("approved_strategy_id") or section.get("strategy_id"),
        "strategy_version": section.get("approved_strategy_version") or section.get("strategy_version"),
        "strategy_hash": section.get("approved_strategy_hash") or section.get("strategy_hash"),
    }
    for key in expected:
        if str(actual.get(key) or "") != str(expected.get(key) or ""):
            diagnostics["blockers"].append(f"STRATEGY_REVIEW_{key.upper()}_MISMATCH")
    failed = any(str(item).startswith("STRATEGY_REVIEW_") for item in diagnostics["blockers"])
    return "failed" if failed else "verified"


def build_plan(
    strategy: Dict[str, Any],
    keyword_package: Dict[str, Any],
    case_defaults: Dict[str, Any],
    prior_plan: Optional[Dict[str, Any]] = None,
    strategy_review: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    diagnostics: Dict[str, List[Any]] = {"blockers": [], "warnings": [], "normalizations": []}
    keyword_package_verification = validate_keyword_package_input(
        keyword_package, str(strategy.get("run_mode") or "production"), diagnostics
    )
    normalized_package, package_warnings = normalize_keyword_package(keyword_package)
    diagnostics["warnings"].extend(package_warnings)
    resolver = DefaultResolver(case_defaults, strategy.get("case_defaults_overrides") or {})

    for field in ("strategy_id", "strategy_version", "strategy_hash", "run_mode"):
        if strategy.get(field) in (None, ""):
            diagnostics["blockers"].append(f"MISSING_STRATEGY_FIELD:{field}")
    strategy_schema = str(strategy.get("schema_version") or "")
    strategy_hash_verification = "not_verified"
    if strategy_schema == "2.0":
        for identity_field in ("run_id", "cycle_id"):
            if strategy.get(identity_field) in (None, ""):
                diagnostics["blockers"].append(
                    f"MISSING_STRATEGY_FIELD:{identity_field}"
                )
        if strategy.get("strategy_hash") != compute_strategy_hash(strategy):
            diagnostics["blockers"].append("STRATEGY_HASH_MISMATCH")
            strategy_hash_verification = "failed"
        else:
            strategy_hash_verification = "verified"
    elif strategy_schema == "1.0":
        diagnostics["warnings"].append("LEGACY_STRATEGY_HASH_NOT_VERIFIED")
        strategy_hash_verification = "legacy_not_verified"
    else:
        diagnostics["blockers"].append(
            f"UNSUPPORTED_STRATEGY_SCHEMA_VERSION:{strategy_schema or 'missing'}"
        )
    strategy_review_verification = verify_strategy_review(strategy, strategy_review, diagnostics)
    complexity = choose_complexity(strategy, resolver, diagnostics)
    blueprints = select_blueprints(strategy, complexity, resolver, diagnostics)
    budgets = allocate_budgets(blueprints, (strategy.get("budget") or {}).get("daily_total"), diagnostics)
    economics = derive_plan_economics(strategy, diagnostics)

    decisions = copy.deepcopy(strategy.get("ad_product_decisions") or {})
    strategy_run_id = str(strategy.get("run_id") or "").strip()
    package_run_id = str(normalized_package.get("run_id") or "").strip()
    if strategy.get('flow_version')=='r04.s4':
        reference=strategy.get('keyword_package_ref') or {}
        if not all(reference.get(k) not in (None,'') for k in ('package_id','package_version','package_hash')):diagnostics['blockers'].append('KEYWORD_PACKAGE_REF_REQUIRED')
        for field in ('package_id','package_version','package_hash'):
            if field in reference and str(reference[field])!=str(normalized_package.get(field)):diagnostics['blockers'].append('KEYWORD_PACKAGE_REF_MISMATCH:'+field)
    elif strategy_run_id and package_run_id and strategy_run_id != package_run_id:
        diagnostics["blockers"].append("RUN_ID_MISMATCH_STRATEGY_KEYWORD_PACKAGE")
    strategy_marketplace = str((strategy.get("market") or {}).get("marketplace") or "").strip()
    package_marketplace = str(normalized_package.get("marketplace") or "").strip()
    if strategy_marketplace and package_marketplace and strategy_marketplace != package_marketplace:
        diagnostics["blockers"].append("MARKETPLACE_MISMATCH_STRATEGY_KEYWORD_PACKAGE")
    strategy_product_id = str((strategy.get("product") or {}).get("product_id") or "").strip()
    package_product_id = str(normalized_package.get("product_id") or "").strip()
    if strategy_product_id and package_product_id and strategy_product_id != package_product_id:
        diagnostics["blockers"].append("PRODUCT_ID_MISMATCH_STRATEGY_KEYWORD_PACKAGE")
    for ad_product in ("SP", "SB", "SD"):
        block = decisions.get(ad_product) or {}
        capability = (block.get("capability") or {}).get("status", "unknown")
        readiness = (block.get("readiness") or {}).get("decision", "unknown")
        basis = (block.get("readiness") or {}).get("decision_basis") or []
        if readiness in ("open", "partial") and not basis:
            diagnostics["blockers"].append(f"AD_PRODUCT_DECISION_WITHOUT_EVIDENCE:{ad_product}")
        if readiness in ("open", "partial") and capability != "available":
            diagnostics["blockers"].append(f"AD_PRODUCT_CAPABILITY_NOT_AVAILABLE:{ad_product}:{capability}")

    campaigns: List[Dict[str, Any]] = []
    marketplace = (strategy.get("market") or {}).get("marketplace", "UNSET")
    for blueprint in blueprints:
        if strategy.get("flow_version")=="r04.s4":blueprint["launch_policy"]="r04.s4"
        campaign_key = str(blueprint.get("campaign_key") or f"CMP-{len(campaigns)+1}")
        campaign_name = blueprint.get("campaign_name") or (
            f"{_slug(marketplace)}-{_slug(blueprint.get('ad_product'))}-{_slug(blueprint.get('role'))}-{_slug(campaign_key)}"
        )
        campaigns.append(
            {
                "campaign_key": campaign_key,
                "campaign_name": campaign_name,
                "execution_status": blueprint.get("execution_status", "draft"),
                "ad_product": blueprint.get("ad_product"),
                "scope_id": blueprint.get("scope_id"),
                "role": blueprint.get("role"),
                "primary_goal": blueprint.get("primary_goal"),
                "objective": blueprint.get("objective"),
                "objective_evidence": copy.deepcopy(blueprint.get("objective_evidence") or {}),
                "objective_kpi": copy.deepcopy(blueprint.get("objective_kpi") or {}),
                "investment_guardrails": copy.deepcopy(blueprint.get("investment_guardrails") or {}),
                "kpi": copy.deepcopy(blueprint.get("kpi") or {}),
                "advertised_skus": copy.deepcopy(blueprint.get("advertised_skus") or (strategy.get("product") or {}).get("seller_skus") or []),
                "daily_budget": budgets.get(campaign_key, 0.0),
                "daily_budget_weight": blueprint.get("daily_budget_weight"),
                "bidding_strategy": "fixed" if strategy.get("flow_version")=="r04.s4" else blueprint.get("bidding_strategy") or "unspecified",
                "bid_guardrail": compile_bid_guardrail(blueprint, economics, resolver, diagnostics),
                "placement_adjustments": copy.deepcopy(blueprint.get("placement_adjustments") or {}),
                "ad_groups": compile_ad_groups(blueprint, normalized_package, strategy, diagnostics),
                "success_rule": copy.deepcopy(blueprint.get("success_rule") or {}),
                "stop_or_reduce_rule": copy.deepcopy(blueprint.get("stop_or_reduce_rule") or {}),
                "graduation_rule": copy.deepcopy(blueprint.get("graduation_rule") or {}),
                "rollback_rule": copy.deepcopy(blueprint.get("rollback_rule") or {}),
                "review_cadence": copy.deepcopy(blueprint.get("review_cadence") or {}),
                "source_blueprint": {"strategy_id": strategy.get("strategy_id"), "campaign_key": campaign_key},
            }
        )

    transfers = [compile_transfer(item, diagnostics) for item in ((strategy.get("architecture") or {}).get("transfers") or [])]
    product = copy.deepcopy(strategy.get("product") or {})
    plan_id = None
    version = 1
    supersedes = None
    if prior_plan:
        plan_id = prior_plan.get("plan_id")
        version = int(prior_plan.get("plan_version", 0)) + 1
        supersedes = {
            "plan_id": prior_plan.get("plan_id"),
            "plan_version": prior_plan.get("plan_version"),
            "plan_hash": prior_plan.get("plan_hash"),
        }
    if not plan_id:
        plan_id = (strategy.get("plan") or {}).get("plan_id") or f"AD-{_slug(marketplace)}-{_slug(product.get('product_id'))}"
    run_id = str(strategy.get("run_id") or normalized_package.get("run_id") or "").strip()
    if not run_id:
        diagnostics["blockers"].append("MISSING_RUN_ID")
    cycle_id = str(strategy.get("cycle_id") or "").strip()
    if not cycle_id and run_id:
        cycle_id = f"{run_id}-CYCLE-{version}"
        diagnostics["normalizations"].append(
            {"code": "CYCLE_ID_DERIVED", "source": "run_id+plan_version", "value": cycle_id}
        )
    change_reason = ((strategy.get("revision") or {}).get("change_reason") or ("initial_compile" if version == 1 else None))
    if version > 1 and not change_reason:
        diagnostics["blockers"].append("REVISION_MISSING_CHANGE_REASON")
        change_reason = "missing_change_reason"

    deferred = []
    for ad_product, block in decisions.items():
        readiness = block.get("readiness") or {}
        if readiness.get("decision") in ("defer", "unknown"):
            deferred.append(
                {
                    "ad_product": ad_product,
                    "decision": readiness.get("decision"),
                    "decision_basis": copy.deepcopy(readiness.get("decision_basis") or []),
                    "reevaluate_when": copy.deepcopy(readiness.get("reevaluate_when") or []),
                }
            )

    ad_group_count = sum(len(campaign["ad_groups"]) for campaign in campaigns)
    target_count = sum(len(group["targets"]) for campaign in campaigns for group in campaign["ad_groups"])
    negative_count = sum(len(group["negatives"]) for campaign in campaigns for group in campaign["ad_groups"])
    budget_by_type = {ad_product: _money(sum(c["daily_budget"] for c in campaigns if c["ad_product"] == ad_product)) for ad_product in ("SP", "SB", "SD")}
    total_budget = _money(sum(campaign["daily_budget"] for campaign in campaigns))
    expected_total = (strategy.get("budget") or {}).get("daily_total")
    budget_matches = expected_total is not None and abs(total_budget - float(expected_total)) < 0.011

    run_mode = strategy.get("run_mode", "production")
    if run_mode == "production":
        diagnostics["blockers"].extend(_production_identifier_blockers(strategy))
    else:
        diagnostics["blockers"].append("DEMO_ONLY_NOT_UPLOADABLE")

    plan: Dict[str, Any] = {
        "schema_version": "2.0",
        "run_id": run_id,
        "cycle_id": cycle_id,
        "plan_id": plan_id,
        "plan_version": version,
        "plan_hash": "",
        "hash_schema": PLAN_HASH_SCHEMA,
        "supersedes": supersedes,
        "change_reason": change_reason,
        "strategy_ref": {
            "schema_version": strategy_schema,
            "strategy_id": strategy.get("strategy_id"),
            "strategy_version": strategy.get("strategy_version"),
            "strategy_hash": strategy.get("strategy_hash"),
            "hash_verification": strategy_hash_verification,
            "review_verification": strategy_review_verification,
        },
        "keyword_package_ref": {
            "package_id": normalized_package.get("package_id"),
            "package_version": normalized_package.get("package_version"),
            "package_hash": normalized_package.get("package_hash"),
            "source_mode": normalized_package.get("source_mode"),
            "contract_verification": keyword_package_verification,
        },
        "run_mode": run_mode,
        "market": copy.deepcopy(strategy.get("market") or {}),
        "product": product,
        "classification": {
            "form": product.get("form"),
            "lifecycle": product.get("lifecycle"),
            "form_evidence": copy.deepcopy(product.get("form_evidence") or []),
            "lifecycle_evidence": copy.deepcopy(product.get("lifecycle_evidence") or []),
        },
        "business_goal": copy.deepcopy(strategy.get("business_goal") or {}),
        "source_manifest": copy.deepcopy(strategy.get("source_manifest") or []) + copy.deepcopy(normalized_package.get("data_sources") or []),
        "source_conflicts": copy.deepcopy(strategy.get("source_conflicts") or []),
        "assumptions": copy.deepcopy(strategy.get("assumptions") or []),
        "case_defaults_used": resolver.used,
        "data_explanation_window": {
            "facts": ["strategy_ref", "product", "budget", "unit_economics", "keyword_package_ref"],
            "derived": ["complexity", "campaign_budget", "target_cpa", "cpc_guardrails", "plan_hash"],
            "defaults": [item["path"] for item in resolver.used],
            "missing_and_impact": copy.deepcopy(diagnostics["blockers"]),
            "late_data_revision": "Create a new plan_version; preserve the prior plan and only recompute affected fields.",
        },
        "complexity": complexity,
        "economics": economics,
        "ad_product_decisions": decisions,
        "deferred_ad_products": deferred,
        "campaigns": campaigns,
        "transfers": transfers,
        "totals": {
            "campaign_count": len(campaigns),
            "campaign_count_by_ad_product": {ad_product: sum(1 for c in campaigns if c["ad_product"] == ad_product) for ad_product in ("SP", "SB", "SD")},
            "ad_group_count": ad_group_count,
            "target_count": target_count,
            "negative_count": negative_count,
            "daily_budget": total_budget,
            "daily_budget_by_ad_product": budget_by_type,
        },
        "upload_eligibility": {
            "status": "blocked" if diagnostics["blockers"] else "review_required",
            "reasons": copy.deepcopy(diagnostics["blockers"]),
        },
        "consistency_checks": {
            "budget_sum_matches": budget_matches,
            "ad_product_declared": all(campaign.get("ad_product") in ("SP", "SB", "SD") for campaign in campaigns),
            "campaign_roles_declared": all(bool(campaign.get("role")) for campaign in campaigns),
            "targets_have_lineage": all(bool(target.get("lineage")) for campaign in campaigns for group in campaign["ad_groups"] for target in group["targets"]),
            "transfers_are_staged": all((item.get("source_negative") or {}).get("activate_after") == "destination_validated" for item in transfers),
        },
        "normalization_log": copy.deepcopy(diagnostics["normalizations"]),
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "generator": "amazon-ad-campaign-builder/4.0.0",
        },
    }
    if not budget_matches and campaigns:
        diagnostics["blockers"].append("BUDGET_SUM_MISMATCH")
        plan["upload_eligibility"]["status"] = "blocked"
        plan["upload_eligibility"]["reasons"] = copy.deepcopy(diagnostics["blockers"])
        plan["data_explanation_window"]["missing_and_impact"] = copy.deepcopy(diagnostics["blockers"])
    if strategy.get("flow_version")=="r04.s4":
        plan.update(flow_version="r04.s4",launch_status="prelaunch" if not product.get("seller_skus") else "sku_unconfirmed")
    plan["plan_hash"] = compute_plan_hash(plan)

    fatal_review_blockers = [
        item for item in diagnostics["blockers"] if item != "DEMO_ONLY_NOT_UPLOADABLE"
    ]
    gate_status = "blocked" if fatal_review_blockers else "pending"
    review_gate = {
        "schema_version": "2.0",
        "artifact_type": "architecture_review",
        "run_id": run_id,
        "cycle_id": cycle_id,
        "strategy_id": strategy.get("strategy_id"),
        "strategy_version": strategy.get("strategy_version"),
        "strategy_hash": strategy.get("strategy_hash"),
        "plan_id": plan_id,
        "plan_version": version,
        "plan_hash": plan["plan_hash"],
        "hash_schema": PLAN_HASH_SCHEMA,
        "status": gate_status,
        "blocking_reasons": copy.deepcopy(diagnostics["blockers"]),
        "warnings": copy.deepcopy(diagnostics["warnings"]),
        "required_reviews": {
            "strategy_confirmed": False,
            "production_identifiers_confirmed": bool(product.get("seller_skus") and product.get("child_asins")),
            "economics_reviewed": False,
            "transfers_reviewed": not bool(transfers),
            "official_bulk_template_available": False,
        },
        "checks": {
            "identity_scope": "pending",
            "campaign_role_and_goal": "pending",
            "daily_budget_reconciliation": "pending",
            "profit_and_inventory_guardrails": "pending",
            "maximum_effective_bid": "pending",
            "placement_and_bidding": "pending",
            "targeting_source_traceability": "pending",
            "negative_relevance_and_res_per_safety": "pending",
            "sp_sb_sd_eligibility": "pending",
        },
        "requested_changes": [],
        "decision_record": [],
        "reviewed_by": None,
        "reviewed_at": None,
    }
    handoff = {
        "schema_version": "1.0",
        "from_skill": "amazon-ad-campaign-builder",
        "to_skill": "amazon-bulk-upload",
        "plan_ref": {
            "plan_id": plan_id,
            "plan_version": version,
            "plan_hash": plan["plan_hash"],
            "hash_schema": PLAN_HASH_SCHEMA,
        },
        "strategy_ref": copy.deepcopy(plan["strategy_ref"]),
        "status": "blocked" if diagnostics["blockers"] else "awaiting_review",
        "prerequisites": {
            "human_review_approved": False,
            "production_identifiers_confirmed": review_gate["required_reviews"]["production_identifiers_confirmed"],
            "official_template_supplied": False,
            "plan_hash_reverified": False,
        },
        "instructions": [
            "Approve review_gate before Bulk generation.",
            "Supply the current marketplace official Bulk template.",
            "Recompute and compare plan_hash before and after Bulk generation.",
            "Generate SP/SB/SD Bulk files separately for active ad products.",
        ],
    }
    diagnostics_output = {
        "schema_version": "1.0",
        "plan_ref": copy.deepcopy(handoff["plan_ref"]),
        **copy.deepcopy(diagnostics),
    }
    return plan, review_gate, handoff, diagnostics_output


def validate_plan(
    plan: Dict[str, Any], review_gate: Optional[Dict[str, Any]] = None, handoff: Optional[Dict[str, Any]] = None
) -> Dict[str, List[str]]:
    errors: List[str] = []
    warnings: List[str] = []
    required = [
        "schema_version", "run_id", "cycle_id", "plan_id", "plan_version", "plan_hash",
        "hash_schema", "strategy_ref", "keyword_package_ref", "run_mode", "market",
        "product", "complexity", "economics", "ad_product_decisions", "campaigns", "transfers", "totals",
        "upload_eligibility", "consistency_checks",
    ]
    for key in required:
        if key not in plan:
            errors.append(f"MISSING_PLAN_FIELD:{key}")
    if plan.get("schema_version") != "2.0":
        errors.append("UNSUPPORTED_SCHEMA_VERSION")
    if plan.get("hash_schema") != PLAN_HASH_SCHEMA:
        errors.append("UNSUPPORTED_PLAN_HASH_SCHEMA")
    if plan.get("plan_hash") != compute_plan_hash(plan):
        errors.append("PLAN_HASH_MISMATCH")

    campaigns = plan.get("campaigns") or []
    keys = [campaign.get("campaign_key") for campaign in campaigns]
    names = [campaign.get("campaign_name") for campaign in campaigns]
    if len(keys) != len(set(keys)):
        errors.append("DUPLICATE_CAMPAIGN_KEY")
    if len(names) != len(set(names)):
        errors.append("DUPLICATE_CAMPAIGN_NAME")
    calculated_budget = _money(sum(float(campaign.get("daily_budget") or 0) for campaign in campaigns))
    totals = plan.get("totals") or {}
    if abs(calculated_budget - float(totals.get("daily_budget") or 0)) >= 0.011:
        errors.append("TOTAL_DAILY_BUDGET_MISMATCH")
    if len(campaigns) != totals.get("campaign_count"):
        errors.append("CAMPAIGN_COUNT_MISMATCH")

    decisions = plan.get("ad_product_decisions") or {}
    for campaign in campaigns:
        key = campaign.get("campaign_key")
        for field in ("campaign_key", "campaign_name", "ad_product", "role", "primary_goal", "bidding_strategy"):
            if campaign.get(field) in (None, ""):
                errors.append(f"CAMPAIGN_MISSING_FIELD:{key}:{field}")
        objective = campaign.get("objective")
        if objective is not None and objective not in {"profit", "investment", "defense", "exposure"}:
            errors.append(f"CAMPAIGN_OBJECTIVE_INVALID:{key}")
        if objective is not None and not (campaign.get("objective_evidence") or {}).get("source_refs"):
            errors.append(f"CAMPAIGN_OBJECTIVE_WITHOUT_EVIDENCE:{key}")
        if objective == "investment":
            guardrails = campaign.get("investment_guardrails") or {}
            if guardrails.get("max_loss") is None or not guardrails.get("expires_at") or not guardrails.get("rollback_if"):
                errors.append(f"INVESTMENT_GUARDRAILS_INCOMPLETE:{key}")
        decision = (((decisions.get(campaign.get("ad_product")) or {}).get("readiness") or {}).get("decision"))
        if decision == "defer":
            errors.append(f"DEFERRED_AD_PRODUCT_HAS_CAMPAIGN:{key}")
        groups = campaign.get("ad_groups") or []
        if not groups:
            errors.append(f"CAMPAIGN_WITHOUT_AD_GROUP:{key}")
        guardrail = campaign.get("bid_guardrail") or {}
        expected = guardrail.get("expected_cvr") or {}
        if guardrail.get("requested_action") == "increase" and expected.get("sample_reliability") not in ("medium", "high"):
            if guardrail.get("increase_allowed"):
                errors.append(f"SMALL_SAMPLE_INCREASE_ALLOWED:{key}")
        allowable = guardrail.get("allowable_cpc")
        worst = guardrail.get("worst_case_effective_bid")
        if allowable is not None and worst is not None and float(worst) - float(allowable) > 0.011:
            errors.append(f"WORST_CASE_BID_EXCEEDS_ALLOWABLE:{key}")
        if any(float(value) != 0 for value in (campaign.get("placement_adjustments") or {}).values()):
            if guardrail.get("worst_case_effective_bid") is None:
                errors.append(f"PLACEMENT_WITHOUT_EFFECTIVE_BID:{key}")

        for group in groups:
            targets = group.get("targets") or []
            negatives = group.get("negatives") or []
            for target in targets:
                if not target.get("lineage"):
                    errors.append(f"TARGET_WITHOUT_LINEAGE:{key}:{target.get('target_id')}")
            for negative in negatives:
                if not negative.get("lineage"):
                    errors.append(f"NEGATIVE_WITHOUT_LINEAGE:{key}:{negative.get('negative_id')}")
            active_negative_expressions = {
                (str(item.get("expression")).casefold(), item.get("match_type"))
                for item in negatives
                if item.get("status") == "active"
            }
            for target in targets:
                pair = (str(target.get("expression")).casefold(), target.get("match_type"))
                if pair in active_negative_expressions:
                    errors.append(f"TARGET_NEGATIVE_CONFLICT:{key}:{target.get('target_id')}")

    for transfer in plan.get("transfers") or []:
        negative = transfer.get("source_negative") or {}
        if negative.get("activate_after") != "destination_validated":
            errors.append(f"UNSAFE_TRANSFER_GATE:{transfer.get('transfer_id')}")
        status = transfer.get("destination_status")
        evidence = transfer.get("validation_evidence") or []
        if status != "validated" and negative.get("status") != "held":
            errors.append(f"UNVALIDATED_TRANSFER_NOT_HELD:{transfer.get('transfer_id')}")
        if status == "validated" and not evidence and negative.get("status") != "held":
            errors.append(f"TRANSFER_VALIDATED_WITHOUT_EVIDENCE:{transfer.get('transfer_id')}")

    if plan.get("run_mode") == "demo_direct_run":
        eligibility = plan.get("upload_eligibility") or {}
        if eligibility.get("status") != "blocked" or "DEMO_ONLY_NOT_UPLOADABLE" not in (eligibility.get("reasons") or []):
            errors.append("DEMO_UPLOAD_GATE_MISSING")
    if plan.get("run_mode") == "production":
        product = plan.get("product") or {}
        if not product.get("seller_skus") or (plan.get("flow_version")!="r04.s4" and not product.get("child_asins")):
            if (plan.get("upload_eligibility") or {}).get("status") != "blocked":
                errors.append("PRODUCTION_IDENTIFIERS_MISSING_NOT_BLOCKED")

    expected_ref = {
        "plan_id": plan.get("plan_id"),
        "plan_version": plan.get("plan_version"),
        "plan_hash": plan.get("plan_hash"),
        "hash_schema": plan.get("hash_schema"),
    }
    if review_gate is not None:
        review_ref = {
            "plan_id": review_gate.get("plan_id"),
            "plan_version": review_gate.get("plan_version"),
            "plan_hash": review_gate.get("plan_hash"),
            "hash_schema": review_gate.get("hash_schema"),
        }
        if review_ref != expected_ref:
            errors.append("REVIEW_GATE_PLAN_REF_MISMATCH")
        if plan.get('flow_version')=='r04.s4':
            helper=Path(__file__).resolve().parents[3]/'scripts/s4_launch.py'
            spec=importlib.util.spec_from_file_location('_r04_approved_architecture',helper)
            launch=importlib.util.module_from_spec(spec);spec.loader.exec_module(launch)
            try:launch.validate_approved_architecture(plan,review_gate)
            except ValueError:errors.append('APPROVED_ARCHITECTURE_CHANGED')
    if handoff is not None and handoff.get("plan_ref") != expected_ref:
        errors.append("HANDOFF_PLAN_REF_MISMATCH")
    if not campaigns:
        warnings.append("PLAN_HAS_NO_ACTIVE_CAMPAIGNS")
    return {"errors": errors, "warnings": warnings}


def r04_bid_guardrail(blueprint,economics,resolver,diagnostics):
    """Use demand signal to plan launch; a conflict needs the explicit report decision."""
    import math
    old=copy.deepcopy(blueprint);old.pop('launch_policy',None);temporary={'blockers':[],'warnings':[],'normalizations':[]}
    result=compile_bid_guardrail(old,economics,resolver,temporary)
    parameters=economics.get('launch_parameters') or {};cap=parameters.get('max_bid')
    if cap is not None and (parameters.get('source')!='user' or not parameters.get('message')):
        diagnostics['blockers'].append('LAUNCH_BID_CAP_REQUIRES_USER');cap=None
    if cap is None:cap=result.get('target_cpc')
    signal=(blueprint.get('bid_signal') or {}).get('value')
    if signal is None:
        diagnostics['blockers'].append('LAUNCH_MARKET_BID_MISSING')
        return {**result,'initial_bid':None,'knowledge_refs':['KC-0053','KC-0105','KC-0146']}
    if not math.isfinite(float(signal)) or float(signal)<=0 or cap is None or not math.isfinite(float(cap)) or float(cap)<=0:
        diagnostics['blockers'].append('LAUNCH_BID_INPUT_INVALID');return result
    bid=_money(signal);multiplier=result.get('total_effective_multiplier') or 1.;worst=_money(bid*multiplier)
    conflict=worst>float(cap)+.001
    if conflict:diagnostics['blockers'].append('LAUNCH_BID_CONFLICT_REQUIRES_CHOICE:'+str(blueprint.get('campaign_key')))
    return {**result,'initial_bid':bid,'demand_candidate_bid':bid,'worst_case_effective_bid':worst,'allowable_cpc':max(float(cap),worst),
        'user_bid_ceiling':float(cap),'capped':False,'launch_decision_required':conflict,'knowledge_refs':['KC-0053','KC-0105','KC-0146'],
        'signal_source':copy.deepcopy(blueprint.get('bid_signal') or {}),'no_impressions_rule':{'day':3,'raise_pct':15,'ceiling':float(cap)}}
