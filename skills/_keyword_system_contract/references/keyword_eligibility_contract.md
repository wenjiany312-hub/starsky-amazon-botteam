# Shared product qualification, version 1.0

`scripts/keyword_eligibility.py` is the common decision guard for S2 and S4. It
does not modify raw records, source metrics, historical classifications or
source hashes. Being in a positive pool never grants product qualification.

```python
evaluate_record(record, restrictions=None, *, strict_product_evidence=False, authoritative_context=None)
evaluate_package(package, restrictions=None, *, strict_product_evidence=False, authoritative_context=None)
apply_eligibility(package, restrictions=None, *, strict_product_evidence=False)
```

The record decision contains `status` (`usable`, `pending`, `excluded`),
`listing_eligible`, `ad_eligible`, `reasons`, `evidence_refs`, `qualification_basis`,
`evidence_gaps`, `ad_review_status`, `matched_restrictions` and `eligibility_version`.
Package evaluation returns `{keyword_id: decision}`. Applying produces a deep
copy with `eligibility_decisions` and filtered Listing/positive advertising
pools. Records, observe pools and negative records remain available for review.
The projection retains its source hash for traceability; it is **not a newly
valid hash-bound package**. Before formal handoff, persist reviewed decisions and
restrictions to a new package revision, update pools and references, then hash
and validate that complete revision. Report-only restrictions do not update S4.

Product qualification uses `product_match` or `product_qualification`, either a
string or `{status, reason, evidence_refs, fact_refs}`. Direct/direct_match,
matched/match, relevant and verified indicate a match; mismatch/irrelevant
exclude; unknown, adjacent and any unrecognized values remain pending. When
both fields exist, the more restrictive result wins. Product evidence can be in
qualification evidence refs or record `fact_refs`, `product_evidence_refs`, or
`evidence_refs`; producers must reference **this SKU's facts**, not competitor
performance or keyword metrics. References prove traceability, not factual truth.

New decision reports must call with `strict_product_evidence=True`: absent
qualification or product evidence remains pending. Compatibility callers may
accept legacy explicit retained/relevant classifications when no restriction
applies, but must expose `legacy_product_evidence_missing`. Pool membership,
`allowed: true`, or an unclassified legacy string never qualifies a keyword.

`claim_restricted`, `brand_hits`, off-topic flags, negative dispositions, product
mismatch/unknown, and explicit channel rejection override positive pools.
Claims remain pending until evidence is reviewed. Brand hits prevent consumer
copy and default ad targets; `ad_review_status=pending` keeps competitor/brand
advertising as a separate decision. No exclusion automatically creates an ad
negative, phrase negative, or generic-root negative.

Restrictions have this form:

```json
{"terms":["magnetic"],"status":"excluded","reason":"SKU has no magnet",
 "evidence_refs":["S1/product-facts#magnet"],"scope":{"product_id":"SKU-A"}}
```

`status` is `pending` or `excluded`. A term is a complete token or contiguous
phrase after Unicode/case/punctuation normalization: `ring` does not match
`spring`, while `key smart` matches `KEY-SMART`. No stemming or broad substring
guess is applied. Omit `scope` for the current package. Known product/SKU,
marketplace or keyword ID mismatch exempts other records; absent/unknown scope
evidence cannot exempt a matching term and adds a visible gap.

`evaluate_package` treats package product/SKU/marketplace as authoritative. A raw
record cannot replace that identity to escape a scoped rule: conflicting identity
is pending, and any applicable exclusion is still applied using the authority.
Missing authority stays unknown instead of trusting the record for an exemption.
An external consumer such as a report must pass its package context explicitly:

```python
evaluate_record(effective_record, restrictions, strict_product_evidence=True,
    authoritative_context={"product_id": package.get("product_id"),
                           "marketplace": package.get("marketplace"),
                           "sku": package.get("sku") or package.get("seller_skus")})
```

Keep the original identity in `effective_record` so conflicts remain visible; do
not overwrite it before calling the guard. S4 gives the current strategy's
product/marketplace precedence over the package, and checks the advertised SKU
scope against strategy SKUs. A rule matching any SKU in a multi-SKU campaign
prevents that shared target from being emitted. Record, manual and duplicate
legacy inputs all use this same context. No source identity is rewritten.

Package and record `restrictions`/`product_restrictions` are honored, including
those under `compliance`. Existing `removed_terms`, `brand_isolated_terms` and
`claim_restricted_terms` accept strings or objects with `terms` (or `keyword`,
`term`, `expression`), preserving reason/evidence. Missing terms in a rule fail
closed. Existing raw keyword records are never rewritten by the guard.

The package validator rejects ineligible positive pool references and reports
evidence gaps. S4 evaluates again when emitting targets, even for blocked or
legacy plans. Legacy normalization preserves all duplicate source records so a
later restriction cannot disappear during deduplication. Manual keyword targets
must pass package restrictions and cannot bypass a rejected matching record.
