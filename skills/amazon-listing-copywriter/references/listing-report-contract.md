# S2 Listing 固定报告

正式入口为插件根 `scripts/s2_report.py`。输入和参数见 `../../../templates/s2_report_input.md` 及 `--help`；字段验证见 `copy-validation-schema.md`。布局由随包模板版本决定，普通产品运行只填写数据。

## R02 逐变体固定顺序

概况之后，按输入顺序完整写完一个变体，再写下一个；陶艺回放为 BLACK → KHAKI。每款为 Title → Item Highlights → B1–B5 → 纯文字描述 → HTML描述 → ST → 后台属性 → 5组Q&A。每款埋词总表和版本对比各一份，末尾共用来源证据，不能再次完整重复问答或文案。

每字段显示：**英文原文 → 中文翻译 → 完整出现/仅分词覆盖/未覆盖 → 实算字符或字节数与当前规则**。标题与亮点只按完整连续词组计有效埋词；分词覆盖另列，不夸大为完整覆盖。每条问答的意图由 `intent` 标明；无真实VOC时注明编辑拟题，禁止冒充用户原话。

每字段和每条问答可复制英文；HTML按钮复制经过校验的英文源码。复制整套带英文字段名，顺序同上，但按后台粘贴顺序把属性置于QA之前；不会夹带中文、计数或审核备注。待核稿可复制本地编辑，不能标可直接上架。HTML表示不证明后台接受所有标签，API默认描述纯文字。

运营版手工粘贴。接了数据底座（SP-API）时按钮只生成审核请求，线上旧值未读取就明确 `not_read`。本地 `push_listing_copy.py` 读取同SKU/站点实际旧值，生成完整现在→改成提案；人工审核与确认后才写，写前保存可回滚旧值，写后实际回读。当前自动映射仅 `item_name`、`bullet_point`、`product_description`、`generic_keyword`；Highlights、QA、HTML和其他属性明确手工操作。不存在的SKU须手工创建，不能用空旧值假装更新或可回滚。

## 输入与审核分开

`listing.json` 的 `skus[]` 保存 title、item_highlights、五条 bullets、description、search_terms，独立 `description_html`、`attributes` 英文对象，以及 5 组 `qa`（`[{"intent","question","answer"}]`，S2 必交；少于或多于 5 组报告会列为待处理）。每个 SKU 的 `review_metadata.fields` 以 `title`、`bullets.1` 等字段键保存：

- `translation_zh`、`keyword_ids`、`buyer_questions`；
- `fact_refs`、`ct_refs`、`voc_refs`、`pending_items`；
- `review_status`（pending/reviewed）与有基线时的 `change_reason`。

完整结构以输入契约为准；审核字段不能拼进消费者文本。补证和资格决定写在 `context.json`，关联真实本品事实及限制来源。旧词包无资格证据时是待核，不靠“高相关”放行。

## 同源输出与验证

同一轮输出 `keyword_report.html`、`listing_report.html`、`listing.md`、`keywords.csv`、`report_model.json`、`validation.json`、`manifest.json`、`ct_mapping.json`。词库报告固定八节，见 `../../../templates/keyword_report_layout.md`。路由可将其映射到旧的 06/07 文件名，但不得另行改写正文或重排章节。

记录 template_version、schema_version、package_hash、listing_revision、policy_profile 与生成时间，指纹和技术细节放证据区。只改一个字段后，计数、覆盖、对比与 HTML 都重渲染；旧报告不能继续标为最新版。

验收检查字段完整性、事实资格、计数/去重、实际覆盖、风险可见性和输入对应。没有文件大小、表格数、进度条数或研究卡片数量下限。接口失败给出可解释错误；禁止临时写第二套 HTML 绕过固定入口。人工审核文案质量，模板校验不承诺平台审核或转化效果。

## 自动ST与生效输入

原JSON保留为种子来源，正常生成默认自动完成230–249字节ST；先合法新词与已有研究/CT许可，后已批准核心词复用，最后有界重复。回退只产生质量警告，不解锁品牌/禁用/错事实，也不宣称算法提升。报告摘要给实际字节/是否回退，详细来源另见search_terms_completion.json。report_model.fingerprints.source_listing是原输入，listing是effective_listing.json；复制和提案绑定后者。缺安全词源明确失败，不索取用户凑词。

普通集成的逻辑输出在 `S2_文案板块/github-tabler-html-report/HTML/s2-bundles/<source>/effective_listing.json` 与同目录 `search_terms_completion.json`；独立CLI以 `--output-dir` 为根。CompactCase运行仍按现有收集流程保存这些机器侧文件，生成器只在物化视图写输出；series_root和资产来源指纹通过现有logical_root映射绑定真实系列目录，不能把一次性视图目录写进系列资产身份。后续审核、剪贴板和提案须消费同轮effective文件，不得重新采用种子JSON里的旧ST。
