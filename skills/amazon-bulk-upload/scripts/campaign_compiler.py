#!/usr/bin/env python3
"""Compile canonical amazon-ad-campaign-builder campaigns into SP Bulk rows."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable


def _slug(value: Any) -> str:
    result = re.sub(r"[^A-Za-z0-9_.:-]+", "-", str(value or "").strip()).strip("-")
    return result or "UNSET"


def compile_campaigns_to_operations(
    plan: dict[str, Any],
    ad_product: str,
    compile_context: dict[str, Any] | None,
    contract_error: Callable[[str, str], Exception],
) -> tuple[list[dict[str, Any]], list[str]]:
    campaigns = plan.get("campaigns")
    if not isinstance(campaigns, list) or not campaigns:
        raise contract_error(
            "BULK_OPERATIONS_MISSING",
            "ad_plan must contain bulk_operations or canonical campaigns",
        )
    if not isinstance(compile_context, dict):
        raise contract_error(
            "BULK_COMPILE_CONTEXT_REQUIRED",
            "canonical campaigns require a hash-locked bulk_compile_context.yaml",
        )
    start_date = str(compile_context.get("start_date") or "")
    if not re.fullmatch(r"[0-9]{8}", start_date):
        raise contract_error("COMPILE_START_DATE_INVALID", start_date or "missing")
    try:
        datetime.strptime(start_date, "%Y%m%d")
    except ValueError:
        raise contract_error("COMPILE_START_DATE_INVALID", start_date)
    default_state = str(compile_context.get("default_state") or "")
    if default_state not in {"enabled", "paused"}:
        raise contract_error("COMPILE_DEFAULT_STATE_INVALID", default_state or "missing")

    bidding_strategy_map = {
        "dynamic_down_only": "Dynamic bids - down only",
        "dynamic_up_and_down": "Dynamic bids - up and down",
        "fixed": "Fixed bid",
        "fixed_bids": "Fixed bid",
    }
    placement_map = {
        "TOS": "placementTop",
        "PP": "placementProductPage",
        "ROS": "placementRestOfSearch",
    }
    auto_expression_map = {
        "close_match": "close-match",
        "close-match": "close-match",
        "loose_match": "loose-match",
        "loose-match": "loose-match",
        "substitutes": "substitutes",
        "complements": "complements",
    }
    warnings = ["canonical_campaigns_compiled_to_bulk_operations"]
    rows: list[dict[str, Any]] = []
    selected = [
        item
        for item in campaigns
        if isinstance(item, dict)
        and str(item.get("ad_product") or "").upper() == ad_product
    ]
    if not selected:
        raise contract_error("NO_CAMPAIGNS_FOR_AD_PRODUCT", ad_product)

    for campaign_index, campaign in enumerate(selected, start=1):
        if str(campaign.get("execution_status") or "draft").startswith("blocked"):
            raise contract_error(
                "CAMPAIGN_EXECUTION_BLOCKED",
                str(campaign.get("campaign_key") or campaign_index),
            )
        campaign_key = _slug(campaign.get("campaign_key") or campaign_index)
        campaign_id = f"TmpCampaign:{campaign_key}"
        campaign_name = str(campaign.get("campaign_name") or "").strip()
        ad_groups = campaign.get("ad_groups")
        if not campaign_name or not isinstance(ad_groups, list) or not ad_groups:
            raise contract_error(
                "CANONICAL_CAMPAIGN_INCOMPLETE",
                f"{campaign_key}: campaign_name/ad_groups required",
            )
        all_targets = [
            target
            for group in ad_groups
            if isinstance(group, dict)
            for target in (group.get("targets") or [])
            if isinstance(target, dict)
        ]
        target_families = {
            "auto" if str(target.get("target_type") or "").lower() == "auto" else "manual"
            for target in all_targets
        }
        if not all_targets or len(target_families) != 1:
            raise contract_error(
                "CAMPAIGN_TARGETING_MODE_AMBIGUOUS",
                f"{campaign_key}: target_families={sorted(target_families)}",
            )
        targeting_type = "AUTO" if target_families == {"auto"} else "MANUAL"
        strategy_key = str(campaign.get("bidding_strategy") or "")
        bidding_strategy = bidding_strategy_map.get(strategy_key)
        if not bidding_strategy:
            raise contract_error(
                "BIDDING_STRATEGY_UNSUPPORTED", f"{campaign_key}: {strategy_key}"
            )
        rows.append(
            {
                "action_id": f"C{campaign_index:03d}-CAMPAIGN",
                "ad_product": ad_product,
                "entity": "Campaign",
                "operation": "Create",
                "campaign_id": campaign_id,
                "campaign_name": campaign_name,
                "start_date": start_date,
                "targeting_type": targeting_type,
                "state": default_state,
                "daily_budget": campaign.get("daily_budget"),
                "bidding_strategy": bidding_strategy,
            }
        )
        advertised_skus = [str(value) for value in campaign.get("advertised_skus") or [] if value]
        if not advertised_skus:
            raise contract_error("ADVERTISED_SKUS_MISSING", campaign_key)
        campaign_bid = (campaign.get("bid_guardrail") or {}).get("initial_bid")

        for group_index, group in enumerate(ad_groups, start=1):
            if not isinstance(group, dict):
                raise contract_error("CANONICAL_AD_GROUP_INVALID", campaign_key)
            group_key = _slug(group.get("ad_group_key") or group_index)
            ad_group_id = f"TmpAdGroup:{campaign_key}:{group_key}"
            ad_group_name = str(group.get("name") or "").strip()
            default_bid = group.get("default_bid", campaign_bid)
            try:
                if Decimal(str(default_bid)) <= 0:
                    raise ValueError
            except (InvalidOperation, TypeError, ValueError):
                raise contract_error(
                    "AD_GROUP_DEFAULT_BID_INVALID",
                    f"{campaign_key}/{group_key}: {default_bid}",
                )
            prefix = f"C{campaign_index:03d}-G{group_index:03d}"
            common = {
                "ad_product": ad_product,
                "campaign_id": campaign_id,
                "ad_group_id": ad_group_id,
                "campaign_name": campaign_name,
                "ad_group_name": ad_group_name,
            }
            rows.append(
                {
                    **common,
                    "action_id": f"{prefix}-ADGROUP",
                    "entity": "Ad Group",
                    "operation": "Create",
                    "ad_group_default_bid": default_bid,
                    "state": default_state,
                }
            )
            for sku_index, sku in enumerate(advertised_skus, start=1):
                rows.append(
                    {
                        **common,
                        "action_id": f"{prefix}-AD-{sku_index:03d}",
                        "entity": "Product Ad",
                        "operation": "Create",
                        "sku": sku,
                        "state": default_state,
                    }
                )
            group_targets = [item for item in group.get("targets") or [] if isinstance(item, dict)]
            if not group_targets:
                raise contract_error("AD_GROUP_TARGETS_MISSING", f"{campaign_key}/{group_key}")
            for target_index, target in enumerate(group_targets, start=1):
                target_type = str(target.get("target_type") or "").lower()
                expression = str(target.get("expression") or "").strip()
                bid = target.get("bid", default_bid)
                target_row = {
                    **common,
                    "action_id": f"{prefix}-TARGET-{target_index:03d}",
                    "operation": "Create",
                    # A target the plan holds back (e.g. auto groups not in use) stays paused even
                    # when the rest of the sheet is uploaded enabled.
                    "state": "paused" if str(target.get("state") or "").lower() == "paused" else default_state,
                    "bid": bid,
                }
                if target_type == "keyword":
                    match_type = str(target.get("match_type") or "").lower()
                    if match_type not in {"exact", "phrase", "broad"} or not expression:
                        raise contract_error(
                            "KEYWORD_TARGET_INVALID", f"{campaign_key}/{group_key}: {target}"
                        )
                    target_row.update(
                        {"entity": "Keyword", "keyword_text": expression, "match_type": match_type}
                    )
                elif target_type == "auto":
                    official_expression = auto_expression_map.get(expression.lower())
                    if not official_expression:
                        raise contract_error("AUTO_TARGET_INVALID", expression)
                    target_row.update(
                        {
                            "entity": "Product Targeting",
                            "product_targeting_expression": official_expression,
                        }
                    )
                elif target_type in {"asin", "product"}:
                    if re.fullmatch(r"B0[A-Z0-9]{8}", expression.upper()):
                        official_expression = f'asin="{expression.upper()}"'
                    elif "=" in expression:
                        official_expression = expression
                    else:
                        raise contract_error("PRODUCT_TARGET_INVALID", expression)
                    target_row.update(
                        {
                            "entity": "Product Targeting",
                            "product_targeting_expression": official_expression,
                        }
                    )
                else:
                    raise contract_error("TARGET_TYPE_UNSUPPORTED", target_type or "missing")
                rows.append(target_row)

            for negative_index, negative in enumerate(group.get("negatives") or [], start=1):
                if not isinstance(negative, dict):
                    continue
                status = str(negative.get("status") or "active").lower()
                if status not in {"active", "enabled"}:
                    warnings.append(
                        f"negative_not_compiled:{campaign_key}/{group_key}/{negative.get('negative_id')}:{status}"
                    )
                    continue
                expression = str(negative.get("expression") or "").strip()
                match_type = str(negative.get("match_type") or "").lower()
                if match_type not in {"exact", "phrase"} or not expression:
                    raise contract_error("NEGATIVE_KEYWORD_INVALID", str(negative))
                rows.append(
                    {
                        **common,
                        "action_id": f"{prefix}-NEG-{negative_index:03d}",
                        "entity": "Negative Keyword",
                        "operation": "Create",
                        "keyword_text": expression,
                        "match_type": match_type,
                        "state": default_state,
                    }
                )

        for placement_key, percentage in (campaign.get("placement_adjustments") or {}).items():
            try:
                numeric_percentage = Decimal(str(percentage))
            except InvalidOperation as exc:
                raise contract_error("PLACEMENT_PERCENTAGE_INVALID", str(percentage)) from exc
            if numeric_percentage == 0:
                continue
            placement = placement_map.get(str(placement_key).upper())
            if not placement:
                raise contract_error("PLACEMENT_UNSUPPORTED", str(placement_key))
            rows.append(
                {
                    "action_id": f"C{campaign_index:03d}-PLACEMENT-{str(placement_key).upper()}",
                    "ad_product": ad_product,
                    "entity": "Bidding Adjustment",
                    "operation": "Create",
                    "campaign_id": campaign_id,
                    "campaign_name": campaign_name,
                    "placement": placement,
                    "percentage": percentage,
                }
            )
    return rows, warnings
