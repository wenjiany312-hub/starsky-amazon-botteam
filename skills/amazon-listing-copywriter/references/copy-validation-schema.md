# Listing consumer-copy validation and review annotations

Validator output schema: **1.1**. Review annotation schema: **1.0**.
The CLI retains `--input`, `--output` and `--strict-warnings`; no external Python dependency is required.

## Input and compatibility

```json
{
  "schema_version": "1.0",
  "blacklist_terms": ["medical grade"],
  "keyword_rows": [{"keyword_id": "kw-ring", "keyword": "key ring"}],
  "skus": [{
    "sku": "SYNTHETIC-KEYRING",
    "title": "NOVA Split Key Ring",
    "item_highlights": "Round profile; keeps small essentials together",
    "bullets": ["First complete bullet.", "Second complete bullet.", "Third complete bullet.", "Fourth complete bullet.", "Fifth complete bullet."],
    "description": "The complete consumer-facing product description belongs here.",
    "search_terms": "keychain holder pocket accessory",
    "qa": [{"question": "What is included?", "answer": "One split ring."}]
  }]
}
```

The example demonstrates structure using synthetic copy; it supplies no evidence for a real product.
The standalone complete fixture is `scripts/fixtures/listing_validation_s2_complete.json`.

| Field | Required checks |
|---|---|
| `title` | Non-empty string, at most 75 Unicode code points, including spaces. Existing special-character/repetition audits remain exception-aware warnings. |
| `item_highlights` | Non-empty string, at most 125 Unicode code points. Title overlap is a review warning, not a blanket ban. |
| `bullets` | Exactly five non-empty strings, each at most 500 Unicode code points. Numbers, objects, arrays and null are never coerced into copy. |
| `description` | Required non-empty string with non-empty readable text. `product_description` and `long_description` are compatible aliases, in that precedence order after `description`. Different simultaneous alias values fail; an empty canonical value does not silently fall back to another alias. |
| `search_terms` | Required non-empty string, at most 249 UTF-8 bytes. Case-insensitive Unicode token repetitions fail, including repeated stopwords. Natural distinct forms such as `keychain keychains` are not automatically collapsed. |
| `qa` | Optional. If the key exists, it must contain a non-empty array of objects, each with non-empty string `question` and `answer`. An empty declared section fails; omit an unused section. |

The 200–450 character bullet range and 1200–2000 character description range are writing targets, not minimum-pass gates or permission to invent facts. A non-empty description passing structural validation does not establish editorial completeness.

Missing or invalid text counts remain `null`. An explicitly empty string can have a measured count of zero but still fails presence and applicable length checks. Metrics never stringify invalid bullets. The description produces both `description_raw_unicode_code_points` and `description_plaintext_unicode_code_points`; the latter counts the returned `description_plaintext` exactly, including retained paragraph newlines.

Readable HTML text is computed with Python `HTMLParser`: character references decode, paragraph/list/block boundaries become newlines, horizontal whitespace collapses, blank lines disappear, and script/style/template content is excluded. This is a deterministic text extraction rule, not a browser rendering engine, CSS visibility analysis, HTML sanitizer, or Amazon tag-support decision. `upload_format_status` stays `not_verified`. The fixed report must escape source HTML and copy/export readable plaintext until upload format support is confirmed independently.

## Risk scan scope

The configured blacklist scans each Title, Highlights, bullet, readable description, ST, Q&A question/answer and declared Chinese review translation separately. It matches complete tokens or contiguous phrases; punctuation/hyphens may separate phrase tokens. `car` does not match `cart` or `scarlet`, while `medical grade` matches `medical-grade`. It never combines the end of one field with the beginning of another to manufacture a phrase. Blacklist entries are literal token phrases, not regular expressions; whitespace-delimited language boundaries are the supported rule, not a Chinese word-segmentation model.

The same fields are checked for recognizable internal process notes, including `This listing is a draft`, `pending sample confirmation`, `S1`/`S2` identifiers, `待工厂确认`, and internal-review labels. Product contexts such as `paint sample` and `draft stopper` are not rejected merely for containing `sample` or `draft`. This pattern audit cannot detect every paraphrase of an internal note.

Human notes belong in `pending_items`, which is not consumer copy and is excluded from these scans. `fact_refs`, `voc_refs` and review annotations likewise must not be inserted into consumer fields. The checks include `scanned_fields` and match locations so an unscanned field cannot be represented as reviewed.

## Optional structured Chinese review

Add `review_metadata` to each SKU. Its stable `fields` IDs are `title`, `item_highlights`, `bullets.1` through `bullets.5`, `description`, `search_terms`, and, if present, `qa.1.question` / `qa.1.answer` and subsequent Q&A indices.

```json
{
  "review_metadata": {
    "schema_version": "1.0",
    "fields": {
      "title": {
        "translation_zh": "NOVA 开口钥匙环",
        "keyword_ids": ["kw-ring"],
        "buyer_questions": ["What is this product?"],
        "fact_refs": ["fact-product-type"],
        "voc_refs": [],
        "pending_items": ["Verify the product type against the supplied specification."],
        "review_status": "pending"
      }
    }
  }
}
```

| Annotation | Meaning and status |
|---|---|
| `translation_zh` | Non-empty Chinese review translation. Missing/empty is pending; invalid type fails. It must not introduce additional quantities, performance or guarantees. Translation accuracy remains an explicit human review responsibility. |
| `keyword_ids` | Array of keyword references; empty is valid. A supplied catalog is resolved and actual text coverage recomputed. With no catalog, non-empty references remain pending; unknown IDs in a supplied catalog fail. |
| `buyer_questions` | Array of concrete buyer questions addressed by the field. Missing/empty remains pending. |
| `fact_refs` | Array of own-product fact references supporting the field. Missing/empty remains pending. References are not evidence of truth by themselves. |
| `voc_refs` | Array of buyer-problem/VOC references. Empty is permitted. VOC does not prove product performance. |
| `pending_items` | Array of remaining human review items. Empty is permitted; non-empty retains pending status. |
| `review_status` | Only `pending` or `reviewed`. Missing is pending; unsupported values such as `verified` fail. `reviewed` is an input declaration, never a machine-generated claim-verification result. |

All five list properties require arrays of non-empty strings when supplied. Missing annotations/list fields are explicitly pending. Malformed declared objects and unknown field IDs fail. Missing English consumer fields fail even when review annotations exist. Consumer length/type/risk validation is the responsibility of `validate_sku`; `validate_review_metadata` does not replace it.

`validate_review_metadata` returns `status`, `checks` and per-field `fields` results with recomputed `coverage`. A `pass` only means the required review structure exists, review is declared, and there are no open items detected by this structural check. `truth_verification` and `translation_accuracy_verification` always remain `not_performed`; `fact_reference_resolution` is `caller_required`.

The fixed report/business gate must separately resolve every fact ID against the authoritative fact catalog, require the applicable `verified` or `user_confirmed` evidence state, preserve sources, and show remaining evidence/translation uncertainties. Do not turn reference presence, a human status declaration, or a lexical match into a verified product claim or an upload approval.

## Public Python APIs

```python
consumer_result = validate_sku(sku, blacklist_terms)  # legacy signature retained
review_result = validate_review_metadata(sku, keyword_rows=None)
field_texts = consumer_fields(sku)
readable_description = description_plaintext(description_html)
match = keyword_coverage(field_texts["description"], "split ring")
```

`consumer_fields` returns canonical field IDs and strings or null, always including the five required bullet slots; extra bullets are retained for scanning even though their count fails. Description aliases normalize to `description` and its readable text.

`keyword_rows` accepts objects with unique non-empty `keyword_id` and `keyword`; `term` / `original_keyword` are compatible text aliases. Catalog source metrics are not modified.

Coverage statuses are calculated from actual text:

| Status | Rule | Example for `keychain clip` |
|---|---|---|
| `exact` | Whole phrase, case/whitespace insensitive, same word order and punctuation. | `A KEYCHAIN clip for keys` |
| `tokens` | Every Unicode word token exists, allowing different order/separators; repeated tokens require sufficient occurrences. | `A clip for a keychain` or `keychain-clip` |
| `absent` | The phrase is absent and one or more required tokens are absent. | `A keychain` or `A clasp for keys` |

The helper returns `matched_tokens`, `missing_tokens`, `method`, and `semantic_verification: not_performed`. It does not stem, equate singular/plural, infer synonyms, promise indexing or assign a semantic score. A word-form or semantic association proposed by an analyst must be reported separately and must not be labeled `exact`. Keyword assignments can describe intended placement: an `absent` coverage value remains visible rather than being converted into a fabricated match.

## CLI status and verification

The CLI `status` / exit code retain the legacy **consumer-copy** meaning: failure returns 1; warnings return 1 only under `--strict-warnings`. The added `review_results` provide independent `pass`/`pending`/`fail` review states. Each `sku_results[].review_metadata_status` uses the same catalog as its corresponding CLI `review_results` entry. A successful legacy exit code alone does not establish completion of the unified S2 report or its evidence review.

```powershell
python skills/amazon-listing-copywriter/scripts/validate_listing_copy.py --input INPUT.json --output OUTPUT.json
python -m unittest discover -s skills/amazon-listing-copywriter/tests -p 'test_*.py' -v
```

Tests cover missing description, nonstring/empty/fewer/more bullets, repeated ST, UTF-8 and field limits, description/QA/translation risk scope, whole-token substring safety, optional versus incomplete Q&A, readable HTML/source counts, actual exact/token/absent coverage, unresolved review metadata, and CLI status consistency. The original title audit fixture intentionally remains incomplete and fails completeness while retaining its specific title audit behavior.

## R02 G S2 profile

`validate_sku(sku, blacklist_terms, profile='r02_s2', rules=None)`采用运行时`listing_rules()`有效快照；无profile保留原独立消费者兼容行为。新增独立`description_html`及`attributes.<name>`消费者字段；均须对应中文审阅注解。HTML不作为description别名，禁止活动内容、属性、未知标签及编码markup，源长度单算，并与纯描述的可读词序一致。

R02要求五个QA intent互异；标题格式/重复词失败阻止亮点就绪。所有目标未满足是项目编辑缺口，与硬上限不同。ST自动补齐优先新增词，排除含描述、属性和QA的前台已有词（含窄单复数）；不足时依用户授权复用合法核心词，最后有界重复。R02 profile重复/前台重叠为质量warn，非本轮合规硬拦截；独立legacy profile仍保留原行为。每字段coverage_counts分complete/tokens_only，标题亮点只认complete。新字段扫描/计数和复制均不拼接审阅说明；文案声明真伪仍由源证据及人工评审负责。

页面规格可保持page_extracted/image_extracted来源用于草稿，不能被升级为样品测试；性能需要对应测试，图示用途弱化许可仍受F来源/hash/SKU约束。后续H对所有新增层统一扫描并强制交付门禁，G不宣称完整合规扫描已通过。

正常报告模型先调用共享complete_listing；source_listing指纹绑定原输入，listing指纹绑定实际effective_listing.json。completion回执持久化输入/来源/输出hash与fallback警告；旧输入原文中block不静默删除。当前提案--listing-json读取同一已审生效文件。
