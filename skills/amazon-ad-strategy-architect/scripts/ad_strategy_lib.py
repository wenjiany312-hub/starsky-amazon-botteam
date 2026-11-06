#!/usr/bin/env python3
"""Deterministic contracts for ad_strategy_brief 2.0."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from statistics import NormalDist
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


SOURCE_CLASSES = {
    "platform_current_fact",
    "account_current_fact",
    "business_derived",
    "market_evidence",
    "user_rule",
    "case_default",
    "AI_inference",
}
PRODUCTION_DECISION_SOURCE_CLASSES = SOURCE_CLASSES - {"case_default", "AI_inference"}


class StrategyContractError(ValueError):
    pass


def load_data(path: str | Path) -> Dict[str, Any]:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8-sig")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise StrategyContractError(
                f"{file_path} is YAML rather than JSON and PyYAML is unavailable"
            ) from exc
        value = yaml.safe_load(text)
    if not isinstance(value, dict):
        raise StrategyContractError(f"{file_path} must contain a mapping/object")
    return value


def dump_data(value: Mapping[str, Any], path: str | Path) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=False)
        handle.write("\n")


def canonical_strategy_payload(strategy: Mapping[str, Any]) -> Dict[str, Any]:
    payload = copy.deepcopy(dict(strategy))
    payload.pop("strategy_hash", None)
    meta = payload.get("_meta")
    if isinstance(meta, dict):
        meta.pop("generated_at", None)
    return payload


def compute_strategy_hash(strategy: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        canonical_strategy_payload(strategy), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def safe_div(numerator: Any, denominator: Any, metric: str) -> Dict[str, Any]:
    if numerator is None:
        return {"value": None, "reason": f"{metric}_numerator_missing"}
    if denominator is None:
        return {"value": None, "reason": f"{metric}_denominator_missing"}
    try:
        n_value, d_value = float(numerator), float(denominator)
    except (TypeError, ValueError):
        return {"value": None, "reason": f"{metric}_not_numeric"}
    if d_value == 0:
        return {"value": None, "reason": f"{metric}_denominator_zero"}
    return {"value": n_value / d_value, "reason": None}


def normalize_rate(value: Any, name: str) -> Tuple[Optional[float], Optional[str]]:
    if value is None or value == "":
        return None, f"{name}_missing"
    text = str(value).strip()
    try:
        numeric = float(text.replace("%", ""))
    except ValueError:
        return None, f"{name}_not_numeric"
    if "%" in text or 1 < numeric <= 100:
        return numeric / 100, "interpreted_as_percent"
    if 0 <= numeric <= 1:
        return numeric, "interpreted_as_decimal"
    return None, f"{name}_out_of_range"


def wilson_interval(successes: Any, trials: Any, confidence: float = 0.95) -> Dict[str, Any]:
    if successes is None or trials is None:
        return {"low": None, "high": None, "width": None, "reason": "missing_counts"}
    try:
        successes_value, trials_value = float(successes), float(trials)
    except (TypeError, ValueError):
        return {"low": None, "high": None, "width": None, "reason": "counts_not_numeric"}
    if trials_value <= 0:
        return {"low": None, "high": None, "width": None, "reason": "clicks_zero"}
    if successes_value < 0 or successes_value > trials_value:
        return {"low": None, "high": None, "width": None, "reason": "orders_outside_binomial_range"}
    z = NormalDist().inv_cdf(1 - (1 - confidence) / 2)
    p = successes_value / trials_value
    denominator = 1 + z * z / trials_value
    centre = (p + z * z / (2 * trials_value)) / denominator
    margin = z * math.sqrt(p * (1 - p) / trials_value + z * z / (4 * trials_value**2)) / denominator
    low, high = max(0.0, centre - margin), min(1.0, centre + margin)
    return {"low": low, "high": high, "width": high - low, "reason": None, "confidence": confidence}


def compose_effective_bid(base_bid: Any, rule: Any) -> Dict[str, Any]:
    """Compute a worst-case bid only from an explicitly sourced current rule.

    Supported mechanical compositions are generic; the caller must supply which
    one reflects the current account/platform and the evidence source.
    """
    if base_bid is None:
        return {"value": None, "reason": "base_bid_missing", "rule_verified": False}
    if not isinstance(rule, Mapping):
        return {"value": None, "reason": "current_adjustment_rule_missing", "rule_verified": False}
    source_class = rule.get("source_class")
    if source_class not in {"platform_current_fact", "account_current_fact"}:
        return {"value": None, "reason": "rule_not_current_platform_or_account_fact", "rule_verified": False}
    if not rule.get("source_id"):
        return {"value": None, "reason": "rule_source_id_missing", "rule_verified": False}
    try:
        value = float(base_bid)
    except (TypeError, ValueError):
        return {"value": None, "reason": "base_bid_not_numeric", "rule_verified": False}

    platform_multiplier = rule.get("platform_max_multiplier", 1.0)
    adjustments = rule.get("adjustments") or []
    try:
        platform_multiplier = float(platform_multiplier)
        percents = [float(item.get("percent", 0)) for item in adjustments if isinstance(item, Mapping)]
    except (TypeError, ValueError):
        return {"value": None, "reason": "adjustment_not_numeric", "rule_verified": False}

    composition = rule.get("composition")
    if composition == "multiplicative":
        effective = value * platform_multiplier
        for percent in percents:
            effective *= 1 + percent
    elif composition == "additive_on_base":
        effective = value * (platform_multiplier + sum(percents))
    else:
        return {"value": None, "reason": "unsupported_or_missing_composition", "rule_verified": False}
    return {
        "value": effective,
        "reason": None,
        "rule_verified": True,
        "source_class": source_class,
        "source_id": rule.get("source_id"),
        "composition": composition,
    }


def calculate_economics(payload: Mapping[str, Any]) -> Dict[str, Any]:
    warnings: List[str] = []
    price = payload.get("selling_price")
    contribution = payload.get("contribution_margin_before_ads")
    target_acos, target_note = normalize_rate(payload.get("target_acos"), "target_acos")
    if target_note and target_note not in {"interpreted_as_decimal", "interpreted_as_percent"}:
        warnings.append(target_note)
    break_even = safe_div(contribution, price, "break_even_acos")
    target_cpa = float(price) * target_acos if price is not None and target_acos is not None else None

    scenarios: List[Dict[str, Any]] = []
    for index, raw in enumerate(payload.get("cvr_scenarios") or []):
        if not isinstance(raw, Mapping):
            warnings.append(f"cvr_scenario_{index}_not_object")
            continue
        scenario = copy.deepcopy(dict(raw))
        cvr, cvr_note = normalize_rate(raw.get("cvr"), "cvr")
        if cvr is None and raw.get("clicks") is not None:
            cvr_result = safe_div(raw.get("orders"), raw.get("clicks"), "cvr")
            cvr = cvr_result["value"]
            cvr_note = cvr_result["reason"] or "derived_from_orders_clicks"
        interval = wilson_interval(raw.get("orders"), raw.get("clicks"))
        allowable_cpc = target_cpa * cvr if target_cpa is not None and cvr is not None else None
        break_even_cpc = float(contribution) * cvr if contribution is not None and cvr is not None else None

        market_signal = payload.get("market_cpc_signal") or {}
        market_value = market_signal.get("value") if isinstance(market_signal, Mapping) else None
        intent_factor = float(payload.get("intent_factor", 1.0))
        safety_factor = float(payload.get("safety_factor", 1.0))
        if market_value is not None and allowable_cpc is not None:
            initial_bid = min(float(market_value) * intent_factor, allowable_cpc * safety_factor)
        elif allowable_cpc is not None:
            initial_bid = allowable_cpc * safety_factor
            warnings.append(f"{scenario.get('scenario_id', index)}_market_cpc_missing_bid_capped_by_economics_only")
        else:
            initial_bid = None
        effective = compose_effective_bid(initial_bid, payload.get("current_adjustment_rule"))
        cap = allowable_cpc
        scenario.update({
            "scenario_id": scenario.get("scenario_id") or f"CVR-{index+1}",
            "cvr": cvr,
            "cvr_interpretation": cvr_note,
            "wilson_95": interval,
            "target_cpa": target_cpa,
            "allowable_cpc": allowable_cpc,
            "break_even_cpc": break_even_cpc,
            "initial_bid": initial_bid,
            "worst_case_effective_bid": effective,
            "economic_guardrail_passed": (
                effective.get("value") <= cap
                if effective.get("value") is not None and cap is not None
                else None
            ),
        })
        source_class = raw.get("source_class")
        scenario["production_decision_source_allowed"] = source_class in PRODUCTION_DECISION_SOURCE_CLASSES
        if source_class in {"case_default", "AI_inference", None}:
            warnings.append(f"{scenario['scenario_id']}_cannot_alone_authorize_production_bid_increase")
        scenarios.append(scenario)

    return {
        "schema_version": "1.0",
        "selling_price": price,
        "contribution_margin_before_ads": contribution,
        "break_even_acos": break_even,
        "target_acos": target_acos,
        "target_acos_interpretation": target_note,
        "target_cpa": target_cpa,
        "cvr_scenarios": scenarios,
        "warnings": sorted(set(warnings)),
        "parameter_lineage": {
            "intent_factor": {"value": payload.get("intent_factor", 1.0), "class": "provided_or_neutral_math_default"},
            "safety_factor": {"value": payload.get("safety_factor", 1.0), "class": "provided_or_neutral_math_default"},
            "market_cpc_signal": payload.get("market_cpc_signal"),
            "current_adjustment_rule": payload.get("current_adjustment_rule"),
        },
    }


def _rate_value(value: Any, name: str, errors: List[str]) -> Optional[float]:
    rate, note = normalize_rate(value, name)
    if note not in {None, "interpreted_as_decimal", "interpreted_as_percent", f"{name}_missing"}:
        errors.append(note)
    return rate


def validate_strategy(strategy: Mapping[str, Any]) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    required = [
        "schema_version", "run_id", "cycle_id", "run_mode", "entry_mode", "strategy_id", "strategy_version",
        "strategy_hash", "market", "product", "business_goal", "unit_economics", "budget",
        "complexity", "ad_product_decisions", "res_per_policy", "source_manifest",
        "data_explanation_window", "review_status",
    ]
    for field_name in required:
        if field_name not in strategy:
            errors.append(f"missing_required:{field_name}")
    if strategy.get("schema_version") != "2.0":
        errors.append("schema_version_must_be_2.0")
    if strategy.get("run_mode") not in {"production", "demo_direct_run"}:
        errors.append("invalid_run_mode")
    if strategy.get("entry_mode") not in {"architecture", "optimization", "full_cycle"}:
        errors.append("invalid_entry_mode")
    if not isinstance(strategy.get("strategy_version"), int) or int(strategy.get("strategy_version", 0)) < 1:
        errors.append("strategy_version_must_be_positive_integer")

    stored_hash = strategy.get("strategy_hash")
    computed_hash = compute_strategy_hash(strategy)
    if stored_hash != computed_hash:
        errors.append("strategy_hash_mismatch")

    economics = strategy.get("unit_economics") or {}
    if not isinstance(economics, Mapping):
        errors.append("unit_economics_must_be_object")
        economics = {}
    price = economics.get("selling_price")
    contribution = economics.get("contribution_margin_before_ads")
    supplied_break_even = _rate_value(economics.get("break_even_acos"), "break_even_acos", errors)
    target_acos = _rate_value(economics.get("target_acos"), "target_acos", errors)
    if price is not None and contribution is not None:
        derived = safe_div(contribution, price, "break_even_acos")
        if supplied_break_even is not None and derived["value"] is not None and not math.isclose(
            supplied_break_even, float(derived["value"]), rel_tol=1e-6, abs_tol=1e-6
        ):
            errors.append("break_even_acos_inconsistent_with_price_and_contribution")
    if price is not None and target_acos is not None and economics.get("target_cpa") is not None:
        expected_cpa = float(price) * target_acos
        if not math.isclose(float(economics["target_cpa"]), expected_cpa, rel_tol=1e-6, abs_tol=0.01):
            errors.append("target_cpa_inconsistent_with_price_and_target_acos")
    if supplied_break_even is not None and target_acos is not None and target_acos > supplied_break_even:
        warnings.append("target_acos_above_break_even_requires_explicit_loss_or_growth_rationale")

    decisions = strategy.get("ad_product_decisions") or {}
    if not isinstance(decisions, Mapping):
        errors.append("ad_product_decisions_must_be_object")
    else:
        for ad_type in ("SP", "SB", "SD"):
            block = decisions.get(ad_type)
            if not isinstance(block, Mapping):
                errors.append(f"missing_ad_product_decision:{ad_type}")
                continue
            capability = block.get("capability") or {}
            readiness = block.get("readiness") or {}
            if not isinstance(capability, Mapping) or capability.get("status") not in {
                "available", "missing_assets", "ineligible", "unknown"
            }:
                errors.append(f"invalid_ad_product_capability:{ad_type}")
            allowed_decisions={"open", "partial", "defer", "unknown"}
            if strategy.get('flow_version')=='r04.s4' and ad_type=='SP':allowed_decisions.add('plan_only')
            if not isinstance(readiness, Mapping) or readiness.get("decision") not in allowed_decisions:
                errors.append(f"invalid_ad_product_readiness:{ad_type}")
            if readiness.get("decision") in {"open", "partial"} and capability.get("status") != "available":
                errors.append(f"ad_product_open_without_available_capability:{ad_type}")
            if readiness.get("decision") in {"open", "partial"} and not readiness.get("decision_basis"):
                errors.append(f"ad_product_open_without_decision_basis:{ad_type}")

    product = strategy.get("product") or {}
    if not isinstance(product, Mapping):
        errors.append("product_must_be_object")
    else:
        for key in ("product_id", "child_asins", "seller_skus", "form", "lifecycle"):
            if key not in product:
                errors.append(f"product_missing_builder_field:{key}")
        if product.get("form") not in {"standard", "semi_standard", "nonstandard", "hybrid"}:
            errors.append("invalid_product_form")
        if product.get("lifecycle") not in {"new", "growth", "mature", "relaunch", "seasonal"}:
            errors.append("invalid_product_lifecycle")
        if strategy.get("run_mode") == "production" and not product.get("seller_skus"):
            warnings.append("production_strategy_missing_seller_skus_builder_will_block_upload_eligibility")
        if strategy.get("run_mode") == "production" and not product.get("child_asins"):
            warnings.append("production_strategy_missing_child_asins_builder_will_block_upload_eligibility")

    complexity = strategy.get("complexity") or {}
    if not isinstance(complexity, Mapping) or complexity.get("requested") not in {
        "auto", "simple", "standard", "advanced"
    }:
        errors.append("complexity_requested_invalid_or_missing")

    category_traffic = strategy.get("category_traffic") or {}
    if not isinstance(category_traffic, Mapping):
        errors.append("category_traffic_must_be_object")
    else:
        concentration = category_traffic.get("concentration", "unknown")
        if concentration not in {"concentrated", "distributed", "unknown"}:
            errors.append("category_traffic_concentration_invalid")
        entry_count = category_traffic.get("entry_count")
        if entry_count is not None and (not isinstance(entry_count, int) or entry_count < 0):
            errors.append("category_traffic_entry_count_invalid")
        if concentration != "unknown" and not category_traffic.get("evidence_refs"):
            errors.append("category_traffic_concentration_requires_evidence")

    architecture = strategy.get("architecture") or {}
    if not isinstance(architecture, Mapping) or not isinstance(architecture.get("campaign_blueprints"), list):
        errors.append("architecture_campaign_blueprints_missing")
    else:
        blueprints = architecture.get("campaign_blueprints") or []
        valid_objectives = {"profit", "investment", "defense", "exposure"}
        for index, blueprint in enumerate(blueprints):
            if not isinstance(blueprint, Mapping):
                errors.append(f"campaign_blueprint_{index}_not_object")
                continue
            for key in ("campaign_key", "ad_product", "role", "primary_goal", "ad_groups"):
                if key not in blueprint:
                    errors.append(f"campaign_blueprint_{index}_missing:{key}")
            objective = blueprint.get("objective")
            if objective is not None:
                if objective not in valid_objectives:
                    errors.append(f"campaign_blueprint_{index}_objective_invalid")
                evidence = blueprint.get("objective_evidence") or {}
                if not isinstance(evidence, Mapping) or not evidence.get("source_refs"):
                    errors.append(f"campaign_blueprint_{index}_objective_requires_evidence")
                if objective == "investment":
                    guardrails = blueprint.get("investment_guardrails") or {}
                    if not isinstance(guardrails, Mapping) or not (
                        guardrails.get("max_loss") is not None
                        and guardrails.get("expires_at")
                        and guardrails.get("rollback_if")
                    ):
                        errors.append(f"campaign_blueprint_{index}_investment_guardrails_incomplete")
                if objective in {"defense", "exposure"} and not blueprint.get("objective_kpi"):
                    errors.append(f"campaign_blueprint_{index}_objective_kpi_missing")
        if not blueprints:
            warnings.append("no_campaign_blueprints_builder_will_compile_zero_campaigns")

    res_per = strategy.get("res_per_policy") or {}
    if not isinstance(res_per, Mapping) or res_per.get("source_negative_after") != "destination_validated":
        errors.append("res_per_source_negative_must_wait_for_destination_validated")

    data_window = strategy.get("data_explanation_window") or {}
    for key in ("obtained_facts", "derived", "simulated", "missing_and_impact", "continue_and_revision"):
        if not isinstance(data_window, Mapping) or key not in data_window:
            errors.append(f"data_explanation_window_missing:{key}")

    for index, source in enumerate(strategy.get("source_manifest") or []):
        if not isinstance(source, Mapping):
            errors.append(f"source_manifest_{index}_not_object")
            continue
        digest = str(source.get("sha256") or "").replace("sha256:", "")
        if digest and not (len(digest) == 64 and all(char in "0123456789abcdefABCDEF" for char in digest)):
            errors.append(f"source_manifest_{index}_invalid_sha256")

    for index, item in enumerate(strategy.get("case_defaults_used") or []):
        if not isinstance(item, Mapping):
            errors.append(f"case_default_{index}_not_object")
            continue
        for key in ("source_id", "value", "applicability", "invalidation", "override"):
            if key not in item:
                errors.append(f"case_default_{index}_missing:{key}")

    if strategy.get("run_mode") == "demo_direct_run" and strategy.get("review_status") == "approved":
        errors.append("demo_strategy_cannot_be_approved_for_production")

    checks = {
        "hash_matches": stored_hash == computed_hash,
        "economics_consistent": not any("acos_inconsistent" in error or "cpa_inconsistent" in error for error in errors),
        "res_per_safe": "res_per_source_negative_must_wait_for_destination_validated" not in errors,
        "data_explanation_complete": not any(error.startswith("data_explanation_window_missing") for error in errors),
        "case_defaults_governed": not any(error.startswith("case_default_") for error in errors),
    }
    return {
        "valid": not errors,
        "strategy_id": strategy.get("strategy_id"),
        "strategy_version": strategy.get("strategy_version"),
        "stored_strategy_hash": stored_hash,
        "computed_strategy_hash": computed_hash,
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
    }
