#!/usr/bin/env python3
"""Non-mutating product qualification shared by S2 decisions and S4 targets.

Pool membership is never qualification. Legacy explicit relevance is compatible
only when no stronger constraint applies, and always reports its evidence gap.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import time
from functools import lru_cache
from pathlib import Path
import re
import unicodedata
from typing import Any


ELIGIBILITY_VERSION = "1.0"
_NEGATIVE = {"compliance_negative", "low_relevance_negative", "off_topic", "excluded", "rejected"}
_MATCHED = {"direct", "direct_match", "matched", "match", "relevant", "verified"}
_MISMATCH = {"mismatch", "mismatched", "not_matched", "not_matching", "no_match", "irrelevant", "excluded"}


@lru_cache(maxsize=1)
def _compliance_module():
    path=Path(__file__).resolve().parents[3]/'scripts/compliance_scan.py'
    spec=importlib.util.spec_from_file_location('_eligibility_h_scan',path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod


def _tokens(value: Any) -> list[str]:
    return re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", str(value or "")).casefold())


def term_matches(keyword: Any, term: Any) -> bool:
    """Match consecutive complete tokens; `ring` must never match `spring`."""
    words, wanted = _tokens(keyword), _tokens(term)
    return bool(wanted) and any(words[pos:pos + len(wanted)] == wanted
                                for pos in range(len(words) - len(wanted) + 1))


def _list(value: Any) -> list:
    return value if isinstance(value, list) else ([] if value is None else [value])


def _unique(values: list) -> list:
    result = []
    for value in values:
        if value not in result:
            result.append(copy.deepcopy(value))
    return result


def _identity_values(value: Any) -> set[str]:
    return {str(item).strip().casefold() for item in _list(value) if item is not None and str(item).strip()}


def _bind_identity(record: dict[str, Any], context: dict[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
    if context is None:
        return record, []
    bound = dict(record)
    conflicts = []
    for field in ("product_id", "sku", "marketplace"):
        if field not in context:
            continue
        actual = record.get(field)
        if field == "sku" and actual is None:
            actual = record.get("seller_sku") or record.get("seller_skus")
        expected = context.get(field)
        if _identity_values(actual) and _identity_values(expected) and not _identity_values(actual).issubset(_identity_values(expected)):
            conflicts.append(f"identity_mismatch:{field}")
        # Unknown authoritative identity stays unknown: a record cannot invent an exemption.
        bound[field] = expected
    return bound, conflicts


def package_restrictions(package: dict[str, Any]) -> list[dict[str, Any]]:
    """Read explicit product restrictions and existing compliance term lists."""
    result = []
    for container in (package, package.get("compliance") or {}):
        if not isinstance(container, dict):
            continue
        for field in ("restrictions", "product_restrictions"):
            for item in _list(container.get(field)):
                if isinstance(item, dict):
                    result.append(copy.deepcopy(item))
                else:
                    result.append({"terms": [item], "status": "pending",
                                   "reason": "unstructured_product_restriction", "evidence_refs": []})
        for field, status, kind in (("removed_terms", "excluded", "compliance"),
                                    ("brand_isolated_terms", "excluded", "brand"),
                                    ("claim_restricted_terms", "pending", "claim")):
            for item in _list(container.get(field)):
                rule = copy.deepcopy(item) if isinstance(item, dict) else {"terms": [item]}
                rule.setdefault("terms", _list(rule.get("keyword") or rule.get("term") or rule.get("expression")))
                rule.setdefault("status", status)
                rule.setdefault("reason", field)
                rule.setdefault("evidence_refs", _list(container.get("blacklist_source")))
                rule.setdefault("kind", kind)
                result.append(rule)
    return result


def _scope_matches(record: dict[str, Any], rule: dict[str, Any]) -> tuple[bool, bool]:
    """Only a proven identity mismatch exempts a record from a scoped rule."""
    scope = rule.get("scope")
    if not scope or scope in ("all", "product"):
        return True, False
    if not isinstance(scope, dict):
        return True, True
    uncertain = False
    for field, expected in scope.items():
        record_field = {"keyword_ids": "keyword_id", "product_ids": "product_id",
                        "skus": "sku", "seller_sku": "sku", "seller_skus": "sku",
                        "marketplaces": "marketplace"}.get(field, field)
        if record_field not in {"keyword_id", "product_id", "sku", "marketplace"}:
            uncertain = True
            continue
        actual = record.get(record_field)
        if not _identity_values(actual) or not _identity_values(expected):
            uncertain = True
        elif not _identity_values(actual).intersection(_identity_values(expected)):
            return False, uncertain
    return True, uncertain



def qualified_use_terms(keyword, terms):
    """One shared, narrow tool + container grammar for every source consumer."""
    aliases = set()
    for term in terms:
        term = str(term).casefold()
        aliases.add(term)
        if term in {'pliers', 'scissors'}: aliases.add(term[:-1])
        elif term.endswith(('ch', 'sh', 'x')): aliases.add(term+'es')
        elif not term.endswith('s'): aliases.add(term+'s')
    tool_tokens = {token for term in aliases for token in _tokens(term)}
    forms = {'roll','rolls','pouch','pouches','organizer','organizers','wrap','wraps','carrier','carriers',
             'storage','bag','bags','holder','holders','case','cases'}
    words = set(_tokens(keyword))
    allowed = tool_tokens | forms | {'tool','tools','for','with','and','of','the','a','an','in','on','to'}
    return sorted(aliases) if words <= allowed and words & forms and words & tool_tokens else []


def _qualified_proof(record):
    proof = record.get('qualified_copy') or {}
    return bool(proof.get('allowed') is True and proof.get('scope') == 'illustrated_use_only'
        and proof.get('ct_ref') == 'CT:uses' and 'CT:uses' in _list(record.get('fact_refs'))
        and proof.get('source_status') in {'image_extracted','page_extracted'} and proof.get('source')
        and re.fullmatch(r'[0-9a-f]{64}', str(proof.get('sha256',''))) and proof.get('reason')
        and qualified_use_terms(record.get('keyword') or record.get('text'), proof.get('illustrated_terms') or []))


def ct_constraints(record, context=None):
    """Only referenced CT fields constrain this term; evidence stays SKU-bound."""
    refs = set(_list(record.get('ct_refs')) + _list(record.get('fact_refs')))
    for field in ('product_match','product_qualification'):
        if isinstance(record.get(field), dict): refs.update(_list(record[field].get('fact_refs')))
    refs = {ref for ref in refs if str(ref).startswith('CT:')}
    mapping = (context or {}).get('ct_mapping')
    rows = mapping.get('rows', []) if mapping is not None else record.get('ct_evidence') or []
    indexed = {row['ct_ref']:row for row in rows}
    blocked, weak = [], []
    for ref in sorted(refs):
        row = indexed.get(ref)
        # A validated illustrated-use proof is itself a source-bound uses node.
        if row is None and ref == 'CT:uses' and _qualified_proof(record):
            weak.append(ref); continue
        if row is None:
            blocked.append(ref+':missing_ct_evidence'); continue
        scopes = _identity_values(row.get('skus'))
        actual = _identity_values(record.get('sku') or record.get('skus'))
        word_scope = _identity_values(record.get('skus'))
        source_status = row.get('source_status')
        sourced = (row.get('state') in {'known','illustrated'} and row.get('source') and source_status in {
            'page_extracted','image_extracted','supplier_confirmed','sample_measured','user_confirmed','user_attested'})
        # Unverified (scanned, inferred, target) facts stay writable; the report lists them for the seller to confirm.
        # A CT row the seller left without a source but with English terms (audiences, scenes, pain points)
        # is written as a to-confirm fact rather than blocked: facts are annotated, not gated.
        unverified = row.get('state') == 'unverified' or (row.get('state') == 'missing' and bool(row.get('en_terms')))
        permitted = ((sourced or unverified) and (row.get('copy_permission') in {'可写','弱化表述'} or unverified)
            and (not scopes or bool(actual) and actual <= scopes)
            and (not word_scope or not actual or actual <= word_scope))
        if ref == 'CT:pain_points' and not row.get('voc_refs') and not unverified: permitted = False
        if not permitted:
            blocked.append(ref+':pending_source_or_scope'); continue
        # The product's own use (e.g. replacing or repairing a screen) is its identity; a 弱化表述 use
        # is written softened and needs no separate illustrated proof.
        if row.get('copy_permission') == '弱化表述':
            weak.append(ref)
    return blocked, weak

def evaluate_record(record: dict[str, Any], restrictions: list[dict[str, Any]] | None = None,
                    *, strict_product_evidence: bool = False,
                    authoritative_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return conservative eligibility without modifying record or source values."""
    record, identity_conflicts = _bind_identity(record, authoritative_context)
    reasons: list[str] = []
    evidence = _list(record.get("evidence_refs")) + _list(record.get("fact_refs"))
    gaps: list[str] = []
    status = "usable"
    brand_review = False

    def block(level: str, reason: Any) -> None:
        nonlocal status
        if level == "excluded" or status != "excluded":
            status = level
        reasons.append(str(reason))

    for reason in identity_conflicts:
        block("pending", reason)
    keyword = record.get("keyword") or record.get("text") or record.get("normalized_keyword")
    if not _tokens(keyword):
        block("pending", "keyword_missing")
    disposition = str(record.get("disposition") or "").casefold()
    if disposition in _NEGATIVE or record.get("off_topic") or record.get("relevant") is False:
        block("excluded", "off_topic" if record.get("off_topic") else f"disposition:{disposition or 'irrelevant'}")
    elif disposition == "observe":
        block("pending", "observe_requires_review")
    if record.get("brand_hits") or disposition == "brand_isolated":
        block("excluded", "brand_requires_separate_review")
        brand_review = True
    if record.get("claim_restricted"):
        block("pending", "claim_restricted")

    qualification_fields = [(key, record[key]) for key in ("product_match", "product_qualification") if key in record]
    if qualification_fields:
        basis = "product_qualification"
        product_evidence = _list(record.get("fact_refs")) + _list(record.get("product_evidence_refs"))
        for field, qualification in qualification_fields:
            if isinstance(qualification, dict):
                value = qualification.get("status") or qualification.get("match") or qualification.get("value")
                product_evidence += _list(qualification.get("evidence_refs")) + _list(qualification.get("fact_refs"))
                if qualification.get("reason"):
                    reasons.append(str(qualification["reason"]))
            else:
                value = qualification
            value = str(value or "unknown").casefold()
            if value in _MISMATCH:
                block("excluded", f"{field}:{value}")
            elif value not in _MATCHED:
                block("pending", f"{field}:{value}")
        evidence += product_evidence
        if not product_evidence and not record.get("evidence_refs"):
            gaps.append("product_evidence_missing")
            if strict_product_evidence:
                block("pending", "product_evidence_missing")
    elif (disposition == "retained" or record.get("relevant") is True
          or str(record.get("relevance") or "").casefold() in {"relevant", "direct", "high"}
          or str(record.get("retention_status") or "").casefold() in {"retained", "retained_core", "retained_relevant"}):
        basis = "legacy_explicit_relevance"
        gaps.append("legacy_product_evidence_missing")
        if strict_product_evidence:
            block("pending", "product_qualification_missing")
    else:
        basis = "unqualified"
        gaps.append("product_qualification_missing")
        block("pending", "product_qualification_missing")

    compliance = record.get("compliance") or record.get("compliance_status")
    if isinstance(compliance, dict):
        evidence += _list(compliance.get("evidence_refs"))
        compliance = compliance.get("status")
    if str(compliance or "").casefold() in {"excluded", "blocked", "prohibited", "forbidden"}:
        block("excluded", "compliance_excluded")
    elif str(compliance or "").casefold() in {"pending", "unknown", "restricted", "review", "claim_restricted"}:
        block("pending", "compliance_pending")

    rules = list(restrictions or []) + package_restrictions(record)
    matched_restrictions = []
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            block("pending", "restriction_not_object")
            continue
        terms = rule.get("terms")
        # Empty/malformed rules may hide a constraint; do not silently approve.
        if not isinstance(terms, list) or not any(_tokens(term) for term in terms):
            block("pending", "restriction_terms_missing")
            continue
        scope_match, uncertain = _scope_matches(record, rule)
        if not scope_match or not any(term_matches(keyword, term) for term in terms):
            continue
        if uncertain:
            gaps.append("restriction_scope_unverified")
        block("excluded" if rule.get("status") == "excluded" else "pending",
              rule.get("reason") or "product_restriction")
        evidence += _list(rule.get("evidence_refs"))
        brand_review = brand_review or rule.get("kind") == "brand"
        matched_restrictions.append(index)

    variant_listing_allowed = True
    variant_ad_allowed = True
    for variant in record.get("legacy_source_records") or []:
        if not isinstance(variant, dict):
            continue
        inherited = {key: value for key, value in record.items() if key != "legacy_source_records"}
        inherited.update(variant)
        inherited.pop("legacy_source_records", None)
        variant_decision = evaluate_record(inherited, restrictions, strict_product_evidence=strict_product_evidence,
                                           authoritative_context=authoritative_context)
        if variant_decision["status"] != "usable":
            block(variant_decision["status"], "legacy_source_constraint")
        reasons += variant_decision["reasons"]
        variant_listing_allowed = variant_listing_allowed and variant_decision["listing_eligible"]
        variant_ad_allowed = variant_ad_allowed and variant_decision["ad_eligible"]
        evidence += variant_decision["evidence_refs"]
        brand_review = brand_review or variant_decision["ad_review_status"] == "pending"

    ct_blocked, ct_weak = ct_constraints(record, authoritative_context)
    for reason in ct_blocked: block('pending', reason)
    qualified = record.get('qualified_copy') or {}
    qualified_valid = _qualified_proof(record)
    if qualified.get('allowed') and not qualified_valid: block('pending', 'qualified_usage_scope_unproven')
    listing_allowed = status == "usable" and variant_listing_allowed
    ad_allowed = status == "usable" and variant_ad_allowed and not ct_weak and not qualified_valid
    illustrated = {tuple(_tokens(t)) for t in qualified_use_terms(keyword, qualified.get('illustrated_terms') or [])}
    claims = _list(record.get('claim_hits'))
    claims_scoped = bool(claims) and all(tuple(_tokens(c)) in illustrated for c in claims)
    restrictions_scoped = all(rules[i].get('kind') == 'claim' and rules[i].get('status') == 'pending'
        and all(tuple(_tokens(t)) in illustrated for t in rules[i]['terms']) for i in matched_restrictions)
    if (qualified_valid and not ct_blocked and status != 'excluded' and disposition == 'observe' and not identity_conflicts
        and (not record.get('claim_restricted') or claims_scoped) and restrictions_scoped and not brand_review
        and not record.get('legacy_source_records') and str(compliance or '').casefold() not in {'pending', 'unknown', 'restricted', 'review', 'claim_restricted'}):
        listing_allowed = True
        ad_allowed = False
        reasons.append('qualified_copy:illustrated_use_only_no_universal_fit')
    for field, channel in (("listing_eligibility", "listing"), ("ad_eligibility", "ad")):
        restriction = record.get(field)
        if isinstance(restriction, dict) and (restriction.get("allowed") is False or restriction.get("blocked_reasons")):
            reasons += [f"{channel}:{reason}" for reason in _list(restriction.get("blocked_reasons"))]
            reasons.append(f"{channel}_explicitly_ineligible")
            if channel == "listing":
                listing_allowed = False
            else:
                ad_allowed = False
    h_result=_compliance_module().keyword_permission({'keyword':keyword,'listing_eligible':listing_allowed,'ad_eligible':ad_allowed},
        evidence=authoritative_context or {},series=(authoritative_context or {}).get('series_root'))
    if h_result['compliance_scan']['blocked']:
        listing_allowed=False
        reasons.append('copy_compliance_blocked')
        # Name the rule that blocked it, so a wrong lexicon entry can be found without reading source.
        reasons += ['copy_compliance_hit:' + s['entry_id'] for h in h_result['compliance_scan'].get('hits', [])
                    if h.get('level') == 'block' for s in h.get('sources', []) if s.get('entry_id')]
    if status == "usable" and not listing_allowed and not ad_allowed:
        status = "pending"
    return {"status": status, "listing_eligible": listing_allowed, "ad_eligible": ad_allowed,
            "reasons": _unique(reasons), "evidence_refs": _unique(evidence),
            "qualification_basis": basis, "evidence_gaps": _unique(gaps),
            "ct_blocked": ct_blocked, "copy_permission": "弱化表述" if listing_allowed and (ct_weak or qualified_valid) else "可写" if listing_allowed else "禁止",
            "ad_review_status": "pending" if brand_review else "not_required",
            "matched_restrictions": matched_restrictions, "eligibility_version": ELIGIBILITY_VERSION}


def _write_progress(path, done, total):
    """Long full-package checks report 'N / total' to a small file the assistant and user can read."""
    try:
        Path(path).write_text(json.dumps({"phase": os.environ.get("STARSKY_PROGRESS_PHASE", "资格校验"), "done": done, "total": total,
                                          "percent": round(done * 100 / total) if total else 100,
                                          "updated_at": time.strftime("%H:%M:%S")}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


@_compliance_module().operation_runtime().boundary
def evaluate_package(package: dict[str, Any], restrictions: list[dict[str, Any]] | None = None,
                     *, strict_product_evidence: bool = False,
                     authoritative_context: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    rules = package_restrictions(package) + list(restrictions or [])
    context = {field: package.get(field) for field in ("product_id", "sku", "marketplace")}
    context["sku"] = context["sku"] or package.get("seller_sku") or package.get("seller_skus")
    context["ct_mapping"] = (package.get("analysis") or {}).get("ct_mapping")
    if authoritative_context is not None:
        context.update(authoritative_context)
    if '_compliance_session' not in context:
        sku=context.get('sku') if isinstance(context.get('sku'),str) else None
        context['_compliance_session']=_compliance_module().ScanSession(context.get('series_root'),sku,context.get('marketplace'))
    _, package_conflicts = _bind_identity(package, context)
    decisions = {}
    records = package.get("records") or []
    progress = os.environ.get("STARSKY_PROGRESS_FILE")
    for done, record in enumerate(records, 1):
        if progress and (done % 1000 == 0 or done == len(records)):
            _write_progress(progress, done, len(records))
        if not isinstance(record, dict) or not record.get("keyword_id"):
            continue
        decision = evaluate_record(record, rules, strict_product_evidence=strict_product_evidence,
                                   authoritative_context=context)
        if package_conflicts:
            decision["status"] = "excluded" if decision["status"] == "excluded" else "pending"
            decision["listing_eligible"] = decision["ad_eligible"] = False
            decision["reasons"] = _unique(decision["reasons"] + package_conflicts)
        decisions[str(record["keyword_id"])] = decision
    return decisions


def apply_eligibility(package: dict[str, Any], restrictions: list[dict[str, Any]] | None = None,
                      *, strict_product_evidence: bool = False) -> dict[str, Any]:
    """Produce a derived consumer view; never rehash or overwrite the source package."""
    result = copy.deepcopy(package)
    decisions = evaluate_package(package, restrictions, strict_product_evidence=strict_product_evidence)
    result["eligibility_decisions"] = decisions
    for field, eligible in (("listing_pools", "listing_eligible"), ("pools", "ad_eligible")):
        for name, ids in (result.get(field) or {}).items():
            if field == "pools" and name == "observe":
                continue
            result[field][name] = [item for item in ids if decisions.get(str(item), {}).get(eligible, False)]
    result["eligibility_projection"] = {"source_package_hash": package.get("package_hash"),
                                        "requires_new_hash_before_package_handoff": True}
    return result
