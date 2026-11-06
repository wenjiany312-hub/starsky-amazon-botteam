# S2 固定报告输入与运行

适用：`s2-unified-3.0`。输入先定版，再生成页面；普通任务只填数据，不写新 HTML。源词包与原始导出只读。旧输入可以生成缺口页，但不能据此宣称业务通过。

## 四份输入

1. `keyword_package.yaml`：现有 Keyword Package 2.0，保留来源、周期、原始词条、数量守恒和 package hash。SIF 线由 `sif_clean.py` 生成，含 `analysis`（集中度、季节、MCP 补数、声明解锁等）供报告第 1–6 节使用。
2. `07B_Listing校验输入.json`：`product_id`、`marketplace`、`listing_revision`、`blacklist_terms`、非空 `skus`（含 5 组 `qa`）。逐 SKU 正文与审阅注解见下。
3. `s2_report_context.json`：本品事实、SKU 映射、分类决定、S1 限制与可选季节证据。SIF 线由 `sif_clean.py` 自动生成，不手写；SKU 映射写在 `review_decisions.json#sku_product_map` 后重跑。不得包含 API key、授权 URL 或凭证。
4. `scenes.yaml`（`--scenes`，可选）：场景库，`scenes[].search_terms` 用当前词包的 `keyword_id`。

```json
{
  "product_id": "DEMO-PRODUCT",
  "product_name": "合成演示产品",
  "marketplace": "US",
  "sku_product_map": {"DEMO-SKU": "DEMO-PRODUCT"},
  "policy_profile": "US_non_media_2026_07",
  "as_of": "2026-09-12",
  "facts": [
    {"id": "F-01", "text": "本品事实及其适用范围", "status": "pending", "source": "产品规格书/来源文件及字段"}
  ],
  "decisions": {
    "KW-001": {
      "product_match": "direct",
      "fact_refs": ["F-01"],
      "reason": "这个词指向本品产品类型",
      "product_root": "产品词根",
      "primary_type": "product",
      "tags": ["场景", "属性"]
    }
  },
  "restrictions": [
    {"terms": ["unsupported material"], "status": "pending", "reason": "缺本品材质证明", "evidence_refs": ["S1-R1"]}
  ],
  "seasonality": {"series": []},
  "next_actions": ["先确认 F-01，再决定文案位置"],
  "evidence": []
}
```

`product_id`/`marketplace` 必须与词包一致。`sku_product_map` 明确每个 SKU 属于哪个产品；词包已声明 SKU 范围时以词包范围为准。不同 SKU 可以有独立文案，但不能换身份绕过产品限制。

事实保留原始来源状态和source/hash；verified/user_confirmed须有真实依据。page_extracted/image_extracted规格可作来源明确的草稿，样品核对放审阅区；性能声明另需真实测试。推测、待核及合成样例不能提升为已确认。本例 F-01 是 pending，所以只会生成待核报告。

主分类 `primary_type`：`product` 产品词、`attribute` 属性词或 `other`；多标签不作为去重词数。`product_match`：direct、adjacent、mismatch、unknown。没有本品依据的旧 retained 词在报告显示待核，不能只凭流量进入文案/广告。

`restrictions` 合并 S1 与企业清单；完整词/短语匹配，支持 `scope` 对 product_id、sku、marketplace 的限定。品牌排除不自动生成广告否定。来源记录中的限制不能被 context 清除。

## 文案与审阅注解

每个 `skus[]` 包含：`sku`、可选 `asin`、可选 `brand`（已确定的自有品牌；也可写在 `context.brand`）、`title`、`item_highlights`、五条字符串 `bullets`、`description`、独立 `description_html`、`search_terms`、`attributes:{name:English string}`；R02必交 `qa:[{intent,question,answer}]` 五条不同意图。兼容描述别名 `product_description`/`long_description`，多个别名不一致会失败。

每个字段在 `review_metadata.fields` 有一份注解。固定键：`title`、`item_highlights`、`bullets.1` 至 `bullets.5`、`description`、`description_html`、`search_terms`、`attributes.<name>`；Q&A 为 `qa.1.question`/`qa.1.answer` 等。

```json
{
  "translation_zh": "英文原句的中文审阅译文，不加新功效",
  "keyword_ids": ["KW-001"],
  "buyer_questions": ["这是什么产品？"],
  "fact_refs": ["F-01"],
  "voc_refs": [],
  "pending_items": ["等待 F-01 事实确认"],
  "review_status": "pending",
  "change_reason": "与旧版相比，减少重复并明确产品类型"
}
```

字段中只写目标站点英文。内部备注放 `pending_items`；中文单列审阅。字段计数和 `exact`/`tokens`/`absent` 覆盖由脚本计算，不能手填通过。`tokens` 是词序/分隔符变化后的分词覆盖，不证明词形、同义词或算法语义收录。分析者如提出语义关联，应作为另行待核说明。

ST 必须做词去重，使用 ≤249 UTF-8 字节保守口径。Title 75 / Highlights 125 用于当前 US 非媒体配置，具体站点/类目需核实。报告的 HTML 不代表亚马逊允许上传这些标签，描述复制默认只复制可读纯文本。

完整结构与校验边界：[copy-validation-schema.md](../skills/amazon-listing-copywriter/references/copy-validation-schema.md)。合成输入：[listing_validation_s2_complete.json](../skills/amazon-listing-copywriter/scripts/fixtures/listing_validation_s2_complete.json)。合成样例不是本品事实证据。

## MCP 与季节数据

沿用词包 `mcp` 的调用状态与来源血缘，尊重用户只用导表的限制。季节序列格式：`{keyword_id,source,metric,unit,points:[{month:"YYYY-MM",value:数字或null}]}`。每条序列只含同一来源/指标/单位。缺失不补零；重复月份不重复计周期；当前未结束年度不算完整年度。两个完整年度是分析目标，不是凑数据的门槛。同源同指标的两个连续完整年度才计算逐月同比；基期为零保留未知，历史峰值月份不当作预测。

## 统一命令

```text
python <plugin>/scripts/s2_report.py --package <keyword_package.yaml> --listing <07B_Listing校验输入.json> --context <s2_report_context.json> --output-dir <审阅输出目录>
python <plugin>/scripts/s2_report.py --output-dir <审阅输出目录> --verify
```

`--baseline <旧版Listing.json>` 生成同 SKU 逐字段改前/改后及理由。`--require-ready` 使尚有业务缺口的运行返回非零；输出仍保留供修复。默认返回 0 只代表文件已渲染。

正式阶段目录兼容入口：

```text
python <plugin>/skills/_keyword_system_contract/scripts/render_s2_visible_reports.py <产品根> --plugin-home <plugin> --context <s2_report_context.json>
```

多源需 `context.downstream_source` 或 `--source sif|sellersprite|xiyou` 指定文案主线。其余来源单独出词库，不混算。入口同时刷新标准文件名与 S2 总览；不会填造场景、修改原始词包/文案输入。

## 固定产物与状态

输出 `keyword_report.html`、`listing_report.html`、`keywords.md`、`listing.md`、`keywords.csv`、`report_model.json`、`validation.json`、`manifest.json`。来源路径写在本地清单用于检查输入是否改变；分享之前按业务数据边界选择文件。

HTML 八/十节顺序固定；CSV 保存全部词条及来源指标，避免公式注入。筛选默认本品可用；原始/待核/排除词均能查看。复制按钮只复制英文正文。

验收分层：文件生成 → 固定模板/当前指纹一致 → 文案校验与事实引用齐全 → 用户业务审阅。`ready_for_review` 也只表示可进入人工审阅，不是上架/建广告授权。只改一个字段也必须重渲染，旧的中文/埋词注解需核对。

报告中的资格决定是只读视图。正式 S4 交接必须把确认后的决定及限制写入新版本 Keyword Package，更新 hash 与审核引用；不能把报表文件当作授权或绕过词包的独立来源。

## R02 G补充契约

逐变体完整文案；每字段英文→中文→完整词组/仅分词覆盖→实算计数。标题/亮点只认完整词组。`description_html`与纯描述实质内容一致，只允许p/br/b/ul/li无属性；HTML默认只复制，不证明后台支持。`qa[].intent`必须五个不同值，无VOC时标编辑拟题。

`context.listing_rules`可注入同一数值快照；H统一登记表接入这个解析点。`context.product_ct`和实际listing进入map_ct，`previous_ct_mapping`必须是上次真实映射，变化影响字段需重写/重审。`ct_mapping.json`随包持久化，不能把规划位置当实际覆盖。

`context.edition=operating|flagship`、`sp_api_connected=true`控制审核请求按钮；按钮只下载请求且旧值状态not_read。真实推送先用本地CLI读取旧值生成提案，再人工确认；支持四个既有写属性，其他字段手工操作。`context.replay_mode=historical_offline_analysis`和`business_execution=false`仅作历史软件回放，不能改变S1 Hold或原竞品确认。

## 自动ST完成（默认启用）

共享`scripts/s2_search_terms.py::complete_listing(listing, package, context, rules=None, series_root=None)`消费当前来源绑定词包、系列资产、已有研究与CT。宿主传series_root；CLI默认仅在词包六级祖先内发现已有workflow_state与资产，不扫描全盘。context可显式提供`st_asset_binding:{series_root,product_id,marketplace}`；必须与当前产品/站点一致。资产同快照一次读取，逐SKU保留许可。

额外既有研究`context.st_context_terms[]`至少带term/product_id/marketplace/copy_permission=可写/fact_refs/source/sha256，须匹配真实本品事实；流量本身不授予相关性。CT uses/audiences/scenarios保持原source/status/hash：ai_inferred/missing/pending不补成事实，弱化图示用途不拆成裸ST。无新增来源时自动使用已批准产品核心词，不问用户凑职业/场景。

输出effective_listing.json为真正生效文案（报告/复制/校验/CT图/推送--listing-json共用），search_terms_completion.json保留新增、核心复用与重复的来源回执。先新词，后合法核心回退/有界重复，达到230即停且≤249 UTF8，不切断词或Unicode。重复和前台重叠是质量建议；原文block/超长不静默删除，H最终仍须拦截。已生效文件再次输入稳定且保留回退警告。原source_listing与生效listing指纹分别记录；不把字节目标通过称为编辑/算法/业务验收。

## H 当前合规输入

宿主传实际 series_root，context.evidence_root 指向真实来源根。指定本次 core_keywords（可在 SKU 单独指定），不要把整库核心标签当成必须塞入标题的词单。context.required_attributes 提供与 context.product_type 对应的 fields/source/可用 sha256；有 schema 时逐项核对；没有时不捏造后台字段要求，记为“上架前待办”（旗舰版可用 SP-API 读取商品类型定义，或用类目模板、后台截图补），不阻塞 S2 交付。生成 required_attributes：`scripts/s2_required_attributes.py --sp-config <sp_api.yaml> --search "<商品英文词>"` 先列候选商品类型，用户确认后 `--product-type <类型> --out <json>`；已有下载的 schema JSON 用 `--schema-file`。五类编辑意图复用 G 已有字段原句和来源引用；普通送礼意图不冒充真实 VOC。

`compliance` 将真实违规、来源失效、字段硬规则及 H5 编辑缺项分开记录。条件词证据、季节有效窗、custom 命令和最终证明 API 详见 `docs/compliance_contract.md`。规则来自单一动态登记表；报告完整性通过不等于当前合规通过，合规通过也不等于业务验收。
