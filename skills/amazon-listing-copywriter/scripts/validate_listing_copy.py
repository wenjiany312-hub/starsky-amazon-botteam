#!/usr/bin/env python3
"""Deterministic copy counts, lexical coverage and review structure, not claim truth.

See references/copy-validation-schema.md. This module does not establish Amazon
upload eligibility, translation accuracy, indexing, ranking or product performance.
"""
from __future__ import annotations

import argparse
from collections import Counter
from html.parser import HTMLParser
import json
import re
import sys
import copy
from pathlib import Path
from typing import Any

TITLE_SPECIAL_CHARACTERS = set("!$?_{}^¬¦")
STOPWORDS = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with"}
DESCRIPTION_KEYS = ("description", "product_description", "long_description")
INTERNAL_NOTE_PATTERNS = [
    r"\b(?:this|the)\s+listing\s+is\s+(?:a\s+)?draft\b",
    r"\b(?:draft\s+(?:listing|copy)|(?:listing|copy)\s+draft)\b",
    r"\b(?:pending|awaiting)\s+(?:sample|factory|supplier|fact|product\s+fact)\b",
    r"\b(?:sample|factory|supplier)\s+(?:confirmation|verification)\s+pending\b",
    r"\b(?:subject\s+to|requires?)\s+(?:sample|factory|supplier)\s+(?:confirmation|verification)\b",
    r"(?<!\w)S[12](?!\w)",
    r"\b(?:internal\s+(?:note|review)|fact\s+boundary|not\s+for\s+upload)\b",
    r"待(?:工厂|样品|供应商|产品事实|事实)(?:确认|核实|验证)?",
    r"(?:内部备注|内部审核|文案草稿|上架草稿|样品待确认)",
    # S2-R15: planning words leaked into English copy ("The target kit contains…")
    r"\b(?:the|our|this)\s+target\s+(?:kit|set|pack|product|spec(?:ification)?s?|size|model)\b",
    r"\b(?:to\s+be\s+confirmed|tbc|tbd|placeholder)\b",
]
# S2-R15 写作标准（SKILL「写作标准」+ references/competitive-copy-methodology.md）：机器能查的部分
GENERIC_ADVICE_PATTERNS = [            # 说明书腔 / 空泛建议：把判断推给买家，不回答“适合谁、解决什么”
    r"\bmeasure\s+(?:your|the)\b[^.]{0,60}\bbefore\b",
    r"\b(?:please\s+)?(?:measure|check|confirm)\s+(?:your|the)\s+\w+(?:\s+\w+)?\s+(?:size|dimensions?|measurements?)\b",
    r"\bcompare\b[^.]{0,60}\bbefore\s+(?:buying|purchasing|ordering|cutting|choosing)\b",
    r"\bbefore\s+(?:buying|purchasing|placing\s+an\s+order)\b",
    r"\bplease\s+(?:read|note|refer)\b",
]
SCENE_AUDIENCE_CUES = (
    r"\b(?:ideal|perfect|great|suitable|designed|made)\s+for\b", r"\bfor\s+(?:your\s+)?(?:home|kids|children|pets?|dogs?|cats?|family|families|"
    r"owners?|renters?|travel(?:ers)?|campers?|beginners?|professionals?|groomers?|office|kitchen|bathroom|bedroom|garden|patio|porch|rv|car|outdoor)",
    r"\b(?:home|kitchen|bathroom|bedroom|office|garden|patio|porch|balcony|garage|rv|camper|car|travel|outdoor|indoor|gift|holiday|christmas)\b",
)
QA_SCENE_CONCERN = r"\b(?:who|where|when|suitable|work\s+for|fit|compatible|can\s+i\s+use|use\s+(?:it|this)\s+(?:for|on|in)|be\s+used\s+(?:for|on|in|with)|help\s+with|keep\s+out|safe|last|durable|wash|clean|return|worth|scene|audience|concern)\b"


def listing_rules(overrides=None):
    """Resolve the single platform registry at call time."""
    import importlib.util
    path = Path(__file__).resolve().parents[3] / 'scripts/platform_rules.py'
    spec = importlib.util.spec_from_file_location('_copy_platform_rules', path)
    registry = importlib.util.module_from_spec(spec); spec.loader.exec_module(registry)
    return registry.listing_rules(overrides)


def legacy_rules():
    import importlib.util
    path=Path(__file__).resolve().parents[3]/'scripts/platform_rules.py'
    spec=importlib.util.spec_from_file_location('_legacy_platform_rules',path)
    registry=importlib.util.module_from_spec(spec);spec.loader.exec_module(registry)
    return registry.load_rules()['legacy_listing']


def operation_runtime():
    import importlib.util
    path=Path(__file__).resolve().parents[3]/'scripts/platform_rules.py'
    spec=importlib.util.spec_from_file_location('_validator_operation_rules',path)
    registry=importlib.util.module_from_spec(spec);spec.loader.exec_module(registry)
    return registry.operation_runtime()


class _SafeDescription(HTMLParser):
    def __init__(self, allowed):
        super().__init__(convert_charrefs=True); self.allowed=set(allowed); self.stack=[]; self.errors=[]
    def handle_starttag(self, tag, attrs):
        if tag not in self.allowed or attrs: self.errors.append('forbidden_tag_or_attributes')
        if tag != 'br': self.stack.append(tag)
    def handle_startendtag(self, tag, attrs):
        if tag != 'br' or attrs: self.errors.append('forbidden_self_closing_tag')
    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop()!=tag: self.errors.append('unbalanced_tags')
    def handle_comment(self, data): self.errors.append('comments_not_allowed')
    def handle_decl(self, decl): self.errors.append('declarations_not_allowed')
    def handle_pi(self, data): self.errors.append('processing_instruction_not_allowed')
    def handle_data(self, data):
        if '<' in data or '>' in data: self.errors.append('encoded_markup_not_allowed')


def validate_description_html(value, plain, rules=None):
    spec = (rules or listing_rules())['description_html']
    errors=[]
    if not isinstance(value,str) or not value.strip(): return ['HTML description missing']
    parser=_SafeDescription(spec['tags'])
    try: parser.feed(value);parser.close()
    except Exception: errors.append('invalid_html')
    errors += parser.errors
    if parser.stack: errors.append('unbalanced_tags')
    if len(value)>spec['max']: errors.append('html_source_length')
    if _tokens(description_plaintext(value)) != _tokens(description_plaintext(plain or '')):
        errors.append('html_plain_semantic_mismatch')
    return list(dict.fromkeys(errors))


def add_check(results: list[dict[str, Any]], level: str, check: str, message: str, **details: Any) -> None:
    entry: dict[str, Any] = {"level": level, "check": check, "message": message}
    if details:
        entry["details"] = details
    results.append(entry)


def _status(checks: list[dict[str, Any]]) -> str:
    levels = {entry["level"] for entry in checks}
    return next((level for level in ("fail", "pending", "warn") if level in levels), "pass")


def text_tokens(value: str) -> list[str]:
    """Retain legacy meaningful title-token audit behavior."""
    return [token.lower() for token in re.findall(r"[A-Za-z0-9+]+", value) if token.lower() not in STOPWORDS]


def _tokens(value: str) -> list[str]:
    """Unicode word tokens; retain stopwords, never infer stems or synonyms."""
    return re.findall(r"[^\W_]+", value.casefold())


class _DescriptionParser(HTMLParser):
    BREAK_TAGS = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr"}
    HIDDEN_TAGS = {"script", "style", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.HIDDEN_TAGS:
            self.hidden.append(tag)
        if not self.hidden and tag in self.BREAK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
            return
        if tag in self.BREAK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def description_plaintext(value: str) -> str:
    """Readable HTML text with deterministic whitespace, not an upload sanitizer."""
    if not isinstance(value, str):
        raise TypeError("Description must be a string.")
    parser = _DescriptionParser()
    parser.feed(value)
    parser.close()
    lines = [re.sub(r"[^\S\n]+", " ", line).strip() for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line)


def description_value(sku: dict[str, Any], *, strict_aliases=False) -> Any:
    """One canonical raw reader for validation, report and reviewed-source binding.

    Presence wins over truthiness: an empty canonical value never falls through.
    Reports retain conflict gaps; operations requiring one approved value refuse.
    """
    value=next((sku[key] for key in DESCRIPTION_KEYS if key in sku),None)
    if strict_aliases and any(sku[key]!=value for key in DESCRIPTION_KEYS if key in sku):
        raise ValueError('description_alias_conflict')
    return value


def _description_value(sku: dict[str, Any]) -> Any:
    return description_value(sku)


def consumer_fields(sku: dict[str, Any]) -> dict[str, str | None]:
    """Canonical fields, rendered description, null for missing/nonstring values.

    Includes five bullet slots and any extra bullets for risk scanning. qa is
    optional; declared list entries add both question and answer slots.
    """
    if not isinstance(sku, dict):
        sku = {}
    fields = {key: sku.get(key) for key in ("title", "item_highlights")}
    bullets = sku.get("bullets")
    bullets = bullets if isinstance(bullets, list) else []
    for index in range(max(5, len(bullets))):
        fields[f"bullets.{index + 1}"] = bullets[index] if index < len(bullets) else None
    raw = _description_value(sku)
    fields["description"] = description_plaintext(raw) if isinstance(raw, str) else None
    fields["search_terms"] = sku.get("search_terms")
    if 'description_html' in sku: fields['description_html'] = sku['description_html']
    if isinstance(sku.get('attributes'), dict):
        fields.update({'attributes.'+str(key): value for key,value in sku['attributes'].items()})
    if isinstance(sku.get("qa"), list):
        for index, entry in enumerate(sku["qa"], 1):
            for part in ("question", "answer"):
                fields[f"qa.{index}.{part}"] = entry.get(part) if isinstance(entry, dict) else None
    return {key: value if isinstance(value, str) else None for key, value in fields.items()}


def keyword_coverage(text: str, keyword: str) -> dict[str, Any]:
    """Return exact / tokens / absent based on actual text, never semantics.

    exact ignores case and whitespace but preserves phrase order/punctuation.
    tokens requires every token (including repeated occurrences); no stemming.
    """
    text = text if isinstance(text, str) else ""
    keyword = keyword if isinstance(keyword, str) else ""
    wanted, observed = Counter(_tokens(keyword)), Counter(_tokens(text))
    matched = sorted(token for token in wanted if observed[token] >= wanted[token])
    missing = sorted(token for token in wanted if observed[token] < wanted[token])
    phrase, body = " ".join(keyword.casefold().split()), " ".join(text.casefold().split())
    exact = bool(wanted) and bool(re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", body))
    return {"status": "exact" if exact else "tokens" if wanted and not missing else "absent",
            "matched_tokens": matched, "missing_tokens": missing,
            "method": "casefold_whole_phrase_or_unicode_tokens_no_stemming",
            "semantic_verification": "not_performed"}


def _blacklist_match(text: str, term: str) -> bool:
    words = _tokens(term)
    if not words:
        return False
    pattern = r"(?<!\w)" + r"[\W_]+".join(re.escape(word) for word in words) + r"(?!\w)"
    return bool(re.search(pattern, text.casefold()))


def _translations(sku: dict[str, Any]) -> dict[str, str]:
    metadata = sku.get("review_metadata")
    fields = metadata.get("fields") if isinstance(metadata, dict) else None
    if not isinstance(fields, dict):
        return {}
    return {f"review_metadata.fields.{key}.translation_zh": annotation["translation_zh"]
            for key, annotation in fields.items()
            if isinstance(annotation, dict) and isinstance(annotation.get("translation_zh"), str)}


def writing_standard_checks(checks, fields, bullets, qa):
    """S2-R15：写作标准里机器能查的三条，都是修改提示（warn），不拦交付。"""
    texts = {k: v for k, v in fields.items() if isinstance(v, str) and k != 'search_terms'}
    generic = {k: sorted({m.group(0) for p in GENERIC_ADVICE_PATTERNS for m in re.finditer(p, v, re.I)}) for k, v in texts.items()}
    generic = {k: v for k, v in generic.items() if v}
    add_check(checks, 'warn' if generic else 'pass', 'generic_advice',
              '五点 / 描述 / 问答不写“购买前请测量 / 对比后再买”这类空泛建议：直接写清尺寸、适合谁、在哪用、解决什么问题。',
              fields=generic)
    scene = [i for i, b in enumerate(bullets, 1) if isinstance(b, str) and any(re.search(p, b, re.I) for p in SCENE_AUDIENCE_CUES)]
    add_check(checks, 'pass' if scene else 'warn', 'bullet_scene_audience',
              '五点分工：构成尺寸 / 材质 / 结构细节 / 场景与人群 / 搭配·送礼·效果；至少一条五点写清适合谁、在哪用（Rufus / COSMO 按这类意图匹配）。',
              bullets=scene)
    qs = [str(q.get('question') or '') + ' ' + str(q.get('intent') or '') for q in qa if isinstance(q, dict)]
    hit = [q for q in qs if re.search(QA_SCENE_CONCERN, q, re.I)]
    add_check(checks, 'pass' if hit or not qs else 'warn', 'qa_scene_concern',
              '问答除了尺寸配件，至少一条回答“适合谁 / 能不能用在某场景 / 耐不耐用、好不好清洗”这类买家顾虑。')


def _risk_checks(fields: dict[str, str | None], blacklist_terms: list[str]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    texts = {key: value for key, value in fields.items() if isinstance(value, str)}
    hits = {key: sorted({term for term in blacklist_terms if term.strip() and _blacklist_match(value, term)})
            for key, value in texts.items()}
    hits = {key: matches for key, matches in hits.items() if matches}
    add_check(checks, "fail" if hits else "pass", "blacklist_terms",
              "Configured whole-token/phrase scan of consumer copy and review translations.",
              matches=sorted({term for terms in hits.values() for term in terms}), fields=hits, scanned_fields=list(texts))
    note_hits = {key: sorted({match.group(0) for pattern in INTERNAL_NOTE_PATTERNS
                             for match in re.finditer(pattern, value, re.IGNORECASE)}) for key, value in texts.items()}
    note_hits = {key: values for key, values in note_hits.items() if values}
    add_check(checks, "fail" if note_hits else "pass", "internal_process_notes",
              "Internal draft, sample-confirmation and workflow notes belong in review pending_items.",
              fields=note_hits, scanned_fields=list(texts))
    return checks


@operation_runtime().boundary
def validate_sku(sku: dict[str, Any], blacklist_terms: list[str], *, profile=None, rules=None, compliance_evidence=None, series=None) -> dict[str, Any]:
    """Compatible consumer-copy API; review completeness has a separate API."""
    config = listing_rules(rules)
    legacy = legacy_rules()
    BULLET_MAX_SPREAD = legacy['bullets']['max_spread']
    TITLE_LIMIT = config['title']['max'] if profile == 'r02_s2' else legacy['title']['max']
    HIGHLIGHTS_LIMIT = config['item_highlights']['max'] if profile == 'r02_s2' else legacy['item_highlights']['max']
    BULLET_LIMIT = config['bullets']['max'] if profile == 'r02_s2' else legacy['bullets']['max']
    BULLET_TARGET = tuple(config['bullets']['target']) if profile == 'r02_s2' else tuple(legacy['bullets']['target'])
    BULLET_WARN_RANGE = BULLET_TARGET if profile == 'r02_s2' else tuple(legacy['bullets']['warn_range'])
    DESCRIPTION_TARGET = tuple(config['description']['target']) if profile == 'r02_s2' else tuple(legacy['description']['target'])
    ST_FILL_TARGET = tuple(config['search_terms']['target']) if profile == 'r02_s2' else tuple(legacy['search_terms']['target'])
    ST_FILL_WARN = ST_FILL_TARGET[0] if profile == 'r02_s2' else legacy['search_terms']['fill_warn']
    ST_BYTES_LIMIT = config['search_terms']['max'] if profile == 'r02_s2' else legacy['search_terms']['max']
    checks: list[dict[str, Any]] = []
    if not isinstance(sku, dict):
        add_check(checks, "fail", "sku_type", "Each SKU must be a JSON object.")
        sku = {}
    if not isinstance(blacklist_terms, list) or not all(isinstance(term, str) for term in blacklist_terms):
        add_check(checks, "fail", "blacklist_type", "Blacklist terms must be an array of strings.")
        blacklist_terms = []
    sku_id = str(sku.get("sku") or sku.get("asin") or "UNNAMED")
    fields = consumer_fields(sku)
    title, highlights, search_terms = fields["title"], fields["item_highlights"], fields["search_terms"]
    bullets = sku.get("bullets")
    if not isinstance(bullets, list):
        add_check(checks, "fail", "bullets_type", "Bullets must be a JSON array.")
        bullets = []
    add_check(checks, "pass" if len(bullets) == 5 else "fail", "bullet_count",
              f"Found {len(bullets)} Bullets; exactly five non-empty strings are required.")
    raw_description = _description_value(sku)
    supplied = [key for key in DESCRIPTION_KEYS if key in sku]
    if len(supplied) > 1 and any(sku[key] != raw_description for key in supplied):
        add_check(checks, "fail", "description_alias_conflict", "Description aliases disagree; retain one canonical description.", fields=supplied)
    for key, value in fields.items():
        valid = isinstance(value, str) and bool(value.strip())
        check_name = {"title": "title_present", "item_highlights": "highlights_present"}.get(key, f"{key}_present")
        add_check(checks, "pass" if valid else "fail", check_name, f"{key} requires a non-empty consumer string.", field=key)
    if "qa" in sku:
        qa = sku["qa"]
        add_check(checks, "pass" if isinstance(qa, list) and bool(qa) else "fail", "qa_structure",
                  "Declared Q&A requires a non-empty array; question/answer completeness is checked per field.")
        if isinstance(qa, list):
            for index, entry in enumerate(qa, 1):
                if not isinstance(entry, dict):
                    add_check(checks, "fail", "qa_item_type", "Q&A entries must be objects.", qa_index=index)

    def count(value: Any) -> int | None:
        return len(value) if isinstance(value, str) else None

    st_bytes = len(search_terms.encode("utf-8")) if isinstance(search_terms, str) else None
    st_counter = Counter(_tokens(search_terms)) if isinstance(search_terms, str) else Counter()
    repeated_st = sorted(token for token, occurrences in st_counter.items() if occurrences > 1)
    metrics = {
        "title_unicode_code_points": count(title), "item_highlights_unicode_code_points": count(highlights),
        "bullet_unicode_code_points": [count(bullet) for bullet in bullets],
        "description_raw_unicode_code_points": count(raw_description),
        "description_plaintext_unicode_code_points": count(fields["description"]),
        "search_terms_utf8_bytes": st_bytes,
        "search_terms_token_count": sum(st_counter.values()) if search_terms is not None else None,
        "search_terms_unique_token_count": len(st_counter) if search_terms is not None else None,
        "search_terms_repeated_tokens": repeated_st,
        "qa_unicode_code_points": [{part: count(fields.get(f"qa.{index}.{part}")) for part in ("question", "answer")}
                                    for index in range(1, len(sku["qa"]) + 1)] if isinstance(sku.get("qa"), list) else None,
    }
    for value, measured, limit, check_name, label in [
        (title, count(title), TITLE_LIMIT, "title_length", "Title Unicode code points"),
        (highlights, count(highlights), HIGHLIGHTS_LIMIT, "highlights_length", "Item Highlights Unicode code points"),
        (search_terms, st_bytes, ST_BYTES_LIMIT, "search_terms_utf8_bytes", "Search Terms UTF-8 bytes"),
    ]:
        valid = isinstance(value, str) and bool(value.strip()) and measured <= limit
        add_check(checks, "pass" if valid else "fail", check_name, f"{label}: {measured if measured is not None else 'missing'}/{limit}.")
    for index, bullet in enumerate(bullets, 1):
        measured = count(bullet)
        valid = isinstance(bullet, str) and bool(bullet.strip()) and measured <= BULLET_LIMIT
        add_check(checks, "pass" if valid else "fail", "bullet_length",
                  f"Bullet {index}: {measured if measured is not None else 'invalid type'}/{BULLET_LIMIT} Unicode code points.", bullet_index=index)
        if valid:
            in_range = BULLET_WARN_RANGE[0] <= measured <= BULLET_WARN_RANGE[1]
            add_check(checks, "pass" if in_range else "warn", "bullet_length_target",
                      f"Bullet {index}: {measured} characters; target {BULLET_TARGET[0]}–{BULLET_TARGET[1]}, "
                      f"review below {BULLET_WARN_RANGE[0]} or above {BULLET_WARN_RANGE[1]}. Add product facts, scenes or natural keywords, not filler.",
                      bullet_index=index)
    description_chars = count(fields["description"])
    if description_chars:
        in_range = DESCRIPTION_TARGET[0] <= description_chars <= DESCRIPTION_TARGET[1]
        add_check(checks, "pass" if in_range else "warn", "description_length_target",
                  f"Description plaintext: {description_chars} characters; target {DESCRIPTION_TARGET[0]}–{DESCRIPTION_TARGET[1]}.",
                  field="description", chars=description_chars)
    lengths = [count(b) for b in bullets if isinstance(b, str) and b.strip()]
    if len(lengths) > 1:
        spread = max(lengths) - min(lengths)
        add_check(checks, "warn" if spread > BULLET_MAX_SPREAD else "pass", "bullet_length_balance",
                  f"Bullet lengths differ by {spread} characters; keep the five within about {BULLET_MAX_SPREAD}.", spread=spread)
    add_check(checks, "fail" if search_terms is None or not search_terms.strip() else ("warn" if profile=='r02_s2' else "fail") if repeated_st else "pass",
              "search_terms_token_repeat", "Avoid repeated generic tokens; R02 approved-core fallback may reuse them with a quality warning." if profile=='r02_s2' else "Search Terms must not repeat tokens.", tokens=repeated_st)
    if isinstance(search_terms, str) and search_terms.strip() and st_bytes is not None and st_bytes <= ST_BYTES_LIMIT:
        # Buyers never see ST, so unused bytes are lost indexing; fill with new eligible words, never pad.
        add_check(checks, "pass" if st_bytes >= ST_FILL_WARN else "warn", "search_terms_fill",
                  f"Search Terms use {st_bytes}/{ST_BYTES_LIMIT} bytes; target {ST_FILL_TARGET[0]}–{ST_FILL_TARGET[1]}, "
                  f"below {ST_FILL_WARN} leaves indexing room unused.", bytes=st_bytes)
        visible = set()
        for text in ([description_plaintext(v) if k=='description_html' and isinstance(v,str) else v for k,v in fields.items() if k != 'search_terms'] if profile == 'r02_s2' else [title, highlights, *bullets]):
            if isinstance(text, str):
                visible |= set(_tokens(text))
        overlap = sorted(set(st_counter) & visible)
        if profile == 'r02_s2':
            singular=lambda w: w[:-3]+'y' if w.endswith('ies') else w[:-2] if w.endswith(('ches','shes')) else w[:-1] if len(w)>3 and w.endswith('s') and not w.endswith('ss') else w
            overlap=sorted(t for t in st_counter if singular(t) in {singular(v) for v in visible})
        add_check(checks, "warn" if overlap else "pass", "search_terms_visible_overlap",
                  "Words already in frontend fields must be replaced with new eligible terms.",
                  tokens=overlap)
    if isinstance(title, str) and title.strip():
        special = sorted({char for char in title if char in TITLE_SPECIAL_CHARACTERS})
        add_check(checks, "warn" if special else "pass", "title_special_characters",
                  "Check project-restricted characters against a documented brand/model exception.", characters=special)
        title_counter = Counter(text_tokens(title))
        repeated = sorted(token for token, occurrences in title_counter.items() if occurrences > 2)
        add_check(checks, "warn" if repeated else "pass", "title_token_repeat",
                  "Meaningful title tokens occurring more than twice require review.", tokens=repeated)
        if isinstance(highlights, str) and highlights.strip():
            overlap = sorted(set(title_counter) & set(text_tokens(highlights)))
            add_check(checks, "warn" if overlap else "pass", "title_highlights_overlap",
                      "Review whether shared Title/Highlights tokens are needed for clarity.", tokens=overlap)
    checks.extend(_risk_checks({**fields, **_translations(sku)}, blacklist_terms))
    import importlib.util
    scan_path=Path(__file__).resolve().parents[3]/'scripts/compliance_scan.py'
    spec=importlib.util.spec_from_file_location('_validator_compliance',scan_path)
    scanner=importlib.util.module_from_spec(spec);spec.loader.exec_module(scanner)
    scan=scanner.scan_fields(scanner.listing_fields(sku),series=series,evidence=compliance_evidence)
    add_check(checks, 'fail' if scan['blocked'] else 'pass', 'compliance_scan',
              '全部消费者字段三层合规扫描；条件词须当前适用证据。', **scan)

    if profile == 'r02_s2':
        chinese=[key for key,value in fields.items() if isinstance(value,str) and re.search(r'[\u3400-\u9fff]',value)]
        add_check(checks,'fail' if chinese else 'pass','consumer_english_only','Keep Chinese translations in review metadata only.',fields={key:['Chinese consumer text'] for key in chinese})
        for key in ('title','item_highlights'):
            value=fields[key]; target=config[key]['target']; size=len(value) if isinstance(value,str) else None
            add_check(checks, 'pass' if size is not None and target[0]<=size<=target[1] else 'warn', key+'_length_target',
                      f'Project target {target[0]}–{target[1]} characters; actual {size}.', field=key)
        repeated=[t for t,n in Counter(text_tokens(title or '')).items() if n>config['title']['repeat']]
        add_check(checks,'fail' if repeated else 'pass','title_token_repeat','Title meaningful-word repeat limit.',field='title',tokens=repeated)
        brand=str(sku.get('brand') or '').strip()
        reserve=int(config['title'].get('brand_reserve') or 0)
        if isinstance(title,str) and title.strip():
            if brand:
                add_check(checks,'pass' if title.casefold().startswith(brand.casefold()) else 'warn','title_brand_first',
                          'Own brand leads the title.',field='title',brand=brand)
            elif 0 < reserve < TITLE_LIMIT:
                room=TITLE_LIMIT-reserve
                add_check(checks,'pass' if len(title)<=room else 'warn','title_brand_reserve',
                          f'Brand undecided: title {len(title)}/{room} leaves {reserve} characters for the brand name and a space.',
                          field='title',chars=len(title),room=room,reserve=reserve)
        bad_title = bool(title and (title.isupper() or any(c in TITLE_SPECIAL_CHARACTERS for c in title) or re.search(r'\b(best|guarantee|sale|free shipping)\b', title,re.I)))
        add_check(checks,'fail' if bad_title else 'pass','title_format','No all-caps, project-restricted special characters or promotional title.',field='title')
        valid_title=not any(c['level']=='fail' and c['check'].startswith('title') for c in checks)
        add_check(checks,'pass' if valid_title else 'fail','highlights_title_prerequisite','Highlights require a valid title.',field='item_highlights')
        html_errors=validate_description_html(sku.get('description_html'),fields.get('description'),config)
        add_check(checks,'fail' if html_errors else 'pass','description_html_safe','HTML is a local representation; backend support is not verified.',field='description_html',errors=html_errors)
        qa=sku.get('qa') or []; qa=qa if isinstance(qa,list) else []
        intents=[q.get('intent') for q in qa if isinstance(q,dict)]
        add_check(checks,'pass' if len(qa)==config['qa']['count'] and len(intents)==len(qa) and all(isinstance(x,str) and x.strip() for x in intents) and len(set(intents))==len(qa) else 'fail',
                  'qa_intents','Five distinct editorial buyer-question intents are required; no VOC attribution is implied.')
        for key in ('attributes',):
            if key in sku and not isinstance(sku[key],dict): add_check(checks,'fail','attributes_type','Attributes must be an English field dictionary.')
        writing_standard_checks(checks, fields, bullets, qa)
        metrics['description_html_unicode_code_points']=len(sku['description_html']) if isinstance(sku.get('description_html'),str) else None
        metrics['effective_rules']=config
    return {"sku": sku_id, "status": _status(checks), "metrics": metrics, "checks": checks,
            "description_plaintext": fields["description"], "upload_format_status": "not_verified",
            "review_metadata_status": validate_review_metadata(sku)["status"]}


def validate_review_metadata(sku: dict[str, Any], keyword_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Check annotations and recompute coverage; missing review data stays pending.

    References/status declarations are not proof. The caller resolves fact_refs
    against authoritative facts with verified/user_confirmed status separately.
    """
    checks: list[dict[str, Any]] = []
    results: dict[str, Any] = {}
    if not isinstance(sku, dict):
        add_check(checks, "fail", "sku_type", "SKU must be an object.")
        sku = {}
    metadata = sku.get("review_metadata")
    if metadata is None:
        add_check(checks, "pending", "review_metadata_present", "Chinese review annotations have not been provided.")
        metadata = {}
    elif not isinstance(metadata, dict):
        add_check(checks, "fail", "review_metadata_type", "review_metadata must be an object.")
        metadata = {}
    version = metadata.get("schema_version")
    if version != "1.0":
        add_check(checks, "pending" if version is None else "fail", "review_schema_version", "Review annotation schema_version must be 1.0.")
    annotations = metadata.get("fields")
    if annotations is None:
        annotations = {}
    elif not isinstance(annotations, dict):
        add_check(checks, "fail", "review_fields_type", "review_metadata.fields must be an object.")
        annotations = {}
    catalog: dict[str, str] = {}
    if keyword_rows is not None:
        if not isinstance(keyword_rows, list):
            add_check(checks, "fail", "keyword_catalog_type", "keyword_rows must be an array when supplied.")
        else:
            for row in keyword_rows:
                if not isinstance(row, dict):
                    add_check(checks, "fail", "keyword_catalog_row", "Keyword rows must be objects.")
                    continue
                key = row.get("keyword_id")
                term = next((row[name] for name in ("keyword", "term", "original_keyword") if name in row), None)
                if not isinstance(key, str) or not key.strip() or not isinstance(term, str) or not term.strip() or key in catalog:
                    add_check(checks, "fail", "keyword_catalog_row", "Keyword IDs must be unique with non-empty keyword text.")
                else:
                    catalog[key] = term
    fields = consumer_fields(sku)
    for field, text in fields.items():
        field_checks: list[dict[str, Any]] = []
        annotation = annotations.get(field)
        coverage: list[dict[str, Any]] = []
        if not isinstance(text, str) or not text.strip():
            add_check(field_checks, "fail", "review_consumer_field_missing", "English consumer field is missing.", field=field)
        if annotation is None:
            add_check(field_checks, "pending", "review_field_missing", "Review annotation is missing.", field=field)
            annotation = {}
        elif not isinstance(annotation, dict):
            add_check(field_checks, "fail", "review_field_type", "Review annotation must be an object.", field=field)
            annotation = {}
        translation = annotation.get("translation_zh")
        if not isinstance(translation, str) or not translation.strip():
            level = "pending" if translation is None or isinstance(translation, str) else "fail"
            add_check(field_checks, level, "review_translation", "A non-empty Chinese review translation is required.", field=field)
        for name in ("keyword_ids", "buyer_questions", "fact_refs", "voc_refs", "pending_items"):
            value = annotation.get(name)
            if value is None:
                add_check(field_checks, "pending", "review_list_missing", f"{name} has not been provided.", field=field)
            elif not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
                add_check(field_checks, "fail", "review_list_type", f"{name} must be an array of non-empty strings.", field=field)
            elif name in ("buyer_questions", "fact_refs") and not value:
                add_check(field_checks, "pending", "review_evidence_missing", f"{name} needs a concrete review reference.", field=field)
            elif name == "pending_items" and value:
                add_check(field_checks, "pending", "review_pending_items", "Open review items remain.", field=field)
        review_status = annotation.get("review_status")
        if review_status != "reviewed":
            add_check(field_checks, "pending" if review_status in (None, "pending") else "fail", "review_status",
                      "Human review must be recorded as pending or reviewed.", field=field)
        keyword_ids = annotation.get("keyword_ids")
        if isinstance(keyword_ids, list):
            for key in keyword_ids:
                if not isinstance(key, str):
                    continue
                if keyword_rows is None:
                    add_check(field_checks, "pending", "keyword_reference_unresolved", "No keyword catalog was supplied.", field=field, keyword_id=key)
                elif key not in catalog:
                    add_check(field_checks, "fail", "keyword_reference_unknown", "Keyword ID is absent from the supplied catalog.", field=field, keyword_id=key)
                else:
                    coverage.append({"keyword_id": key, "keyword": catalog[key], **keyword_coverage(text, catalog[key])})
        field_checks.extend(check for check in _risk_checks({field: translation}, []) if check["level"] == "fail")
        if not field_checks:
            add_check(field_checks, "pass", "review_annotation_structure", "Required annotations are present; claim truth was not checked.", field=field)
        results[field] = {"status": _status(field_checks), "coverage": coverage, "checks": field_checks}
        checks.extend(field_checks)
    for field in annotations:
        if field not in fields:
            add_check(checks, "fail", "review_field_unknown", "Annotation points to an unknown consumer field.", field=field)
    return {"schema_version": "1.0", "status": _status(checks), "checks": checks, "fields": results,
            "truth_verification": "not_performed", "translation_accuracy_verification": "not_performed",
            "fact_reference_resolution": "caller_required"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Listing copy from a JSON payload.")
    parser.add_argument("--input", required=True, help="Path to 07B_Listing校验输入.json")
    parser.add_argument("--output", required=True, help="Path to 07C_Listing校验结果.json")
    parser.add_argument("--strict-warnings", action="store_true", help="Return non-zero when warnings exist.")
    args = parser.parse_args()
    payload = json.loads(Path(args.input).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("Input JSON must be an object.")
    skus = payload.get("skus")
    if not isinstance(skus, list) or not skus:
        raise ValueError("Input JSON must contain a non-empty 'skus' array.")
    blacklist_terms = payload.get("blacklist_terms", [])
    if not isinstance(blacklist_terms, list) or not all(isinstance(term, str) for term in blacklist_terms):
        raise ValueError("'blacklist_terms' must be an array of strings when present.")
    sku_results = [validate_sku(sku, blacklist_terms) for sku in skus]
    review_results = [validate_review_metadata(sku, payload.get("keyword_rows")) for sku in skus]
    for sku_result, review_result in zip(sku_results, review_results):
        sku_result["review_metadata_status"] = review_result["status"]
    overall_status = _status([{"level": result["status"]} for result in sku_results])
    effective = legacy_rules()
    result = {
        "schema_version": "1.1",
        "count_method": {
            "title_and_highlights": "Python Unicode code point count (len)",
            "bullets": "Python Unicode code point count; invalid types remain null",
            "description": "Raw source len and deterministic HTML-to-plaintext len; not upload approval",
            "search_terms": "Project raw UTF-8 including spaces/punctuation; Amazon indexing-side length differs; repetition is editorial warning",
        },
        "limits": {"title": effective["title"]["max"], "item_highlights": effective["item_highlights"]["max"], "bullet": effective["bullets"]["max"],
                   "search_terms_utf8_bytes": effective["search_terms"]["max"]},
        "writing_targets": {"bullet_unicode_code_points": effective["bullets"]["target"], "description_plaintext_unicode_code_points": effective["description"]["target"]},
        "status": overall_status, "sku_results": sku_results,
        "review_results": review_results,
        "notes": [
            "Consumer-copy status and review-metadata status are separate; missing review evidence is pending.",
            "This validator does not prove product facts, translations, eligibility, indexing, ranking, or conversion.",
            "Writing targets are guidance, never permission to add filler or unverified claims.",
            "HTML readable-text counts do not establish supported Amazon upload tags; use plain text until confirmed.",
            "Warnings require human explanation; consumer-copy failures prevent listing_copy completion.",
        ],
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{overall_status.upper()}: wrote {output}")
    return 1 if overall_status == "fail" or (args.strict_warnings and overall_status == "warn") else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
