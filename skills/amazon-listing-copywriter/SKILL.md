---
name: amazon-listing-copywriter
description: >
  当用户需要撰写、优化或审核 Amazon Title、Item Highlights、五点、长描述、
  Search Terms、A+ 或 Q&A 文案时使用。也用于 S2 词库到 Listing 的事实核对、
  中英审阅、埋词解释和局部改版。
version: "4.0.0"
edition: starsky-v2
edition_label: 实战版
author: 坚哥
author_id: jiangge
brand:
  wechat_mp: 跨境者说干货
  knowledge_planet: 星空的跨境圈子
  ecosystem: codex
input:
  required:
    - name: product_info
      type: user_text
      description: "当前商品事实（含未核实的扫描/目标规格，写入并标待核实）"
    - name: marketplace
      type: user_text
      default: us
  optional:
    - name: keyword_package
      type: yaml
      description: "已选择并校验的 Keyword Package 2.0；保留来源与 Hash"
    - name: product_facts
      type: yaml
      description: "03_product_facts.yaml 及 S1 IP/声明限制"
    - name: baseline
      type: json
      description: "已有文案基线；只改指定字段时保留其他字段及审核记录"
output:
  format: markdown
  schema_version: "2.0"
  bilingual: true
  structured:
    - name: listing_content
      type: markdown
      filename: "07_Listing文案.md"
    - name: listing_content_html
      type: html
      filename: "HTML/07_Listing文案.html"
    - name: listing_bullet_ledger
      type: yaml
      filename: "07_listing_bullet_ledger.yaml"
    - name: listing_conversion_blueprint
      type: yaml
      filename: "07A_Listing转化蓝图.yaml"
    - name: listing_validation_input
      type: json
      filename: "07B_Listing校验输入.json"
    - name: listing_validation_result
      type: json
      filename: "07C_Listing校验结果.json"
downstream:
  - skill: amazon-listing-image-designer
    note: "交接已审文案、事实引用与待核边界"
  - skill: amazon-ad-strategy-architect
    note: "引用已选择词包；广告策略和执行仍须独立审核"
---

# Amazon Listing 文案

目标是让买家看懂产品是否适合自己，并让审阅者能核对每句声明的依据。英文消费者文案、中文译文、事实/VOC 与内部审核信息分开保存。

## 先读什么

1. 当前 run 的 00C 与 runtime overlay（若有）、产品事实、已确认竞品池、S1 IP/声明限制、用户既有决定。资料接入遵循 `../../templates/data_intake_read_contract.md`。
2. 已选择的 `keyword_package.yaml` 与 `06B_场景库.yaml`（若有）。场景是素材，不能替代产品事实；无场景/VOC 时记录来源缺口，不补造卡片。
3. 写作前读 [字段规则](references/amazon-title-policy-2026.md) 和 [写作方法](references/competitive-copy-methodology.md)；交付时读 [固定报告](references/listing-report-contract.md) 与 [校验输入](references/copy-validation-schema.md)。

## 执行顺序

1. **确认本品可写范围。** 产品事实与 S1/企业限制先于选词和文案。S1 扫描到的规格、材质、配件、目标规格（如件数、尺寸、预贴垫）**照写进文案**，在审阅区逐条标“待核实”，由用户审核时决定；不要因为未经实物/厂家确认就不写。只有未证性能/功效（防水、耐磨、承重、抗菌等）、认证、品牌/IP、禁用词不写。禁用品牌与已确认自有品牌分开。
2. **按买家问题写。** 问题 → 本品事实 `fact_refs` → 合理收益或适配边界 → 最合适的字段。真实 VOC 说明买家关心什么，不证明本品已解决它。收益缺证时先写已有事实，把假设留在审核区。
3. **部署关键词。** 按产品词根、产品词/属性词与多标签理解意图；先判断产品资格，再比较同源同窗数据。候选与实际已用分开。L1/L2/L3 可作优先级，不要求每条五点重复核心词、固定首段位置或使用率。
4. **形成完整英文与中文对照。** Title、Highlights、B1–B5、Description、ST、5 组 Q&A 按固定字段填写并一并校验。英文不混中文、S1/S2、draft 或待工厂确认等内部备注；中文不得新增数字、效果或保证。
5. **校验后交付。** 字符/字节、重复词、声明限制、字段完整性、实际覆盖从最终文本计算；校验通过、没有禁用/品牌/侵权词就交付，S2 完成。人工审阅不是交付条件：不要生成逐条审阅清单（R1、R2…）让用户逐项确认，交付卡只用一句话提醒“上架前请自行过一遍文案”。用户主动提出修改时再改；后台必填属性有则核对，没有则列为上架前待办。机器通过不等于可上架或保证转化。

## 写作标准（R08：每个字段写满、写出卖点）

文案要让买家一眼看懂“这是什么、有什么不同、适合我吗”。**不写空泛建议**（如 “Measure before choosing”“Plan a place for each category”）——那是教程，不是卖点。每句都要落到本品的具体规格、结构、配件、场景或人群。

| 字段 | 长度（含空格） | 写法 |
|---|---|---|
| Title | **70–75**，贴近 75 | 品牌位（无品牌就空）+ 核心词 + 件数/尺寸/关键配件 + 主要场景；核心词放前 40 字符 |
| Item Highlights | **115–125** | 一句话说清最强的 2–3 个卖点，与标题不重复 |
| Bullet 1–5 | **每条 220–250**，五条长短接近 | 开头用大写卖点标签（如 `MIXED-SIZE SET:`），后接具体规格 → 带来的好处 → 适用场景，自然带 1–2 个可用词。五条分主题：①套装构成/尺寸 ②材质与外观 ③结构与细节设计 ④具体使用场景与人群 ⑤搭配方式/送礼/使用效果 |
| Product Description | **1200–1500**（纯文字） | 分 4–5 段：产品是什么 → 解决哪些整理痛点 → 套装构成、尺寸、材质与细节 → 多个具体使用场景 → 使用与清洁建议、包装内容。不复制五点原句 |
| Search Terms | **230–249 UTF-8 字节**，尽量填满 | 由正式入口自动补齐：先放前台没出现的可用词，再放同义词、场景词、核心词；不够 230 时允许重复核心词补满。只排除品牌/竞品/ASIN/禁用词 |
| Q&A | **正好 5 组** | 每组“问题 + 1–3 句回答”作为一个整体；覆盖 5 种不同顾虑（尺寸/是否放得下、包装里有什么、使用中的担心、适合哪些场景、怎么维护或搭配） |

规格来源：S1 的目标规格（件数、尺寸、配件）、CT 扫描事实、1688 页面规格都直接写进英文；在审阅区“待核实清单”逐条列出，供用户上架前和工厂核对。**不因为“未经实物确认”就删掉或改短**。仍然不写：未测试的性能/功效（waterproof、heavy-duty、antibacterial 等）、认证、绝对化用语、竞品品牌。

长度不够时，补**具体信息**：尺寸数字、件数配比、配件、结构细节、更多场景、使用方法；不要用形容词堆砌，也不要复制别的字段凑字数。

## Q&A：5 组，S2 必交（S3 高级 A+ 问答模块直接取用）

按 Alexa for Shopping（前身 Rufus） 这类购物助手会被问到的问题来写，而不是凑常见问题：

1. **选题**：优先取 S1 差评/VOC 的头部痛点和场景库的购买阻断点（装得下吗、能装多少、配件有什么、带出门结实吗、适不适合送礼），再补有真实来源的购物助手问题；没有VOC时明确编辑拟题，不伪称用户原话。5 组覆盖不同顾虑，不重复五点已经说透的点。
2. **回答**：1–3 句，先给直接答案，再给依据；可用已登记的 `fact_refs`（含待核实规格，审阅区标注）。兼容、容量这类数字按 S1 规格直接答，并提示以尺寸图为准；也**不能反着答**（如"不兼容 top loader"），否定答案会和 S1 卖点冲突、后面补证后还要改。待证点换一个已确认的角度出题（如"双层套膜卡放得下吗"），补证通过后再加该声明的问答。
3. **埋词**：每组自然带 1–2 个本品可用词（问题里用买家原话，回答里用规范词），不堆词、不放竞品品牌/IP。
4. **边界**：这是编辑撰写的问答，供 A+ 问答模块和详情页问答种子使用；不冒充买家已提问、不伪造评论。

写入 listing JSON 的 `qa: [{"question","answer"}]`，`review_metadata.fields` 用 `qa.1.question`、`qa.1.answer`… 记译文、`keyword_ids`、`buyer_questions`、`fact_refs`。报告第 6 节逐组展示；数量不是 5 会列为待处理。

R02字段规则与来源见 `references/amazon-title-policy-2026.md`，运行时只用 `validate_listing_copy.listing_rules()` 的同一快照。标题目标68–75/上限75（写到70以上）；自有品牌已确定时写在标题最前面并计入长度，未确定时不虚构、不写占位符，标题正文按上限减去 brand_reserve（品牌名＋空格，当前 12 字符）写，即目标56–63/上限63，亮点目标110–125/上限125且标题有效；五点目标200–250（写到220以上），按结构、尺寸用法、材质、场景人群、礼品分主题；纯文字描述1200–1500，同时提供≤2000字符的安全HTML表示。ST目标230–249 UTF-8字节、硬线249；由正式生成入口自动补齐。优先已有资产中未被前台使用的可写词，再取既有市场研究与CT人群/用途/场景的有来源许可词；不足时自动复用已批准产品核心词，最后允许有界核心词重复以达到目标。避免重复是质量建议，用户已授权合法核心词回退；不得改弱标题重复限制，品牌/禁用/错材质形态/缺证用途仍禁用。无安全核心词时出明确缺口，不反复让用户提供职业、场景。补长度只增加规格、具体用途和自然词汇，不堆形容词或虚构性能。

每款完整输出全部字段、双语属性与五条不同意图QA，再写下一款；标题/亮点只认完整连续词组。英文→中文→完整/分词埋词→实算计数。复制仅英文；整套含英文字段名、HTML和QA。运营版手工粘贴；旗舰按钮只生成审核请求，经本地实际旧值提案、人工确认、可持久化回滚备份后才有写入，写后实际回读。当前SP接口只允许标题/五点/纯描述/ST四个既有属性，其他字段列为手工操作。不存在SKU不自动创建。

页面/图片/目标规格直接写进文案，样品核对放审阅区的“待核实清单”，不作为写不写的门槛。图示工具用途只作有限展示描述，不说全部适配；缺样品性能测试不写防水、耐磨或耐用保证。无VOC、人群、清洁方法等资料时如实保留缺口；通用买家拟题不可标成真实VOC。

## 统一生成与交接

从插件根运行正式入口，参数以 `--help` 和 `../../templates/s2_report_input.md` 为准：

```powershell
python scripts/s2_report.py --package "<keyword_package.yaml>" --listing "<listing.json>" --context "<s2_report_context.json>" --scenes "<scenes.yaml>" --output-dir "<report-dir>"
```

`context` 用词库清洗脚本生成的 `s2_report_context.json`（本品事实登记与状态都在里面），不要手写。`scenes` 是 S2 第 5 步的 scenes.yaml，关键词按当前词包的 `keyword_id` 引用；换了词包要重建场景库。有基线才加 `--baseline "<baseline.json>"`。`07_Listing文案.md` 直接取本次输出的 `listing.md`，不另写。仅词库时省略 `--listing`。禁止为单个产品临时编写 HTML 生成器；缺输入/依赖按入口错误补齐，不另造页面绕过。

同一次输入生成 `keyword_report.html`、`listing_report.html`、`listing.md`、`keywords.csv`、`report_model.json`、`validation.json`、`manifest.json`（Listing 视图取决于是否传入文案）。路由可映射为 06/07 旧文件名，必须保留映射和指纹。四联账本与转化蓝图使用同一组字段审核信息，不能另写一份相矛盾的文案。

报告渲染只读输入。正式用途资格若改变，先写回所选词包、更新版本/Hash 并使旧审核失效，再交 S4；单改报告不能改变投放资格。S3 只引用已审文案与待核边界，关键词候选不授权建广告或上传。

## 文案进店推送门（2026-09-14：A店铺实测定的铁律）

⛔ 文案进店铺后台之前**必须人工复核**，禁止默认直推。批量推送一时爽，侵权敏感词上去轻则链接下架、重则被投诉。每次推送给用户出风险告知：商标/品牌词、绝对化功效词、conditional 词禁入前台、07C 校验 PASS，四项缺一不推；回“确认推送+版本号”才进后台，只回“继续”不算数。复核通过写 `reviewed_by + reviewed_at` 进账本。

## 按需参考

- [转化蓝图与视觉交接](references/listing-conversion-system.md)：需要图片/运营交接或改版验证时。
- [COSMO 意图关系](references/cosmo-intent-relations.md)：需要检查人群、场景、用途与边界时。
- [购物助手问题取样](references/rufus-mining.md)：有真实详情页问题或用户提供截图/文本时。
- [购物助手内容边界](references/alexa-shopping-algorithm.md)：解释算法适配时；不承诺权重、收录或排名。

出品：星空亚马逊 Agent 实战版｜坚哥｜跨境者说干货 · 星空的跨境圈子

## ST自动补齐与同源输出（R02）

正常`scripts/s2_report.py`和两端固定报告入口默认调用共享`scripts/s2_search_terms.py::complete_listing`，不是只为示范手工填词。实际系列根由宿主传入/CLI从当前词包附近已有资产有界发现；同一资产select_many只加载一次，按SKU重投影许可。其他产品/站点/子款、有品牌/禁止许可、仅图示弱化或流量高但没有本品事实许可的词不得成为裸ST。

输出`effective_listing.json`及`search_terms_completion.json`；报告、复制、校验、CT映射与旗舰提案均读生效字段。推送的`--listing-json`必须指向已审核生效JSON，不能仍用补齐前的26字节种子。原输入单独保留指纹，来源变化/生效文案变化必须重审，不能复用旧证明。

人只看实际字节与是否使用核心回退；扩展回执保留真实来源、许可事实引用、新增/复用/重复、原文和生效hash。自动补齐不新增VOC、人群、性能或适配承诺，不代表Amazon排序增益、整套编辑验收或业务授权。传入的block或超长原文保留供H拦截，不静默删除伪造合规。重复处理已生效文案不得继续追加，也不得丢失回退警告/来源。


## R02 合规硬门禁

第 7 步短摘要预检调用 `scripts/compliance_scan.py scan --listing <effective_listing.json> --series <系列根> --context <当前上下文.json> --output <compliance_precheck.json> --summary`。聊天只接收状态、问题数和报告路径，完整证明保留在输出文件；随后由正式 `s2_report.py` 生成当前合规与编辑自检，交付门独立重算当前输入，预检不能替换正式报告证明。命中问题回第 6 步改稿，显示“需修改 N 处 / 按修改清单改 N 处”，不得发 S3 口令。

正式报告和带真实系列/证据上下文的校验 API 调用共享 `scripts/compliance_scan.py`，扫描三层词库及全部消费者字段（含 HTML、Q&A、后台属性）。独立 validator CLI 没有系列上下文时只查基础库与字段规则，不能作为正式交付许可。block、未证 conditional、过期/无有效期 seasonal 只能生成 `needs_changes` 草稿及整改卡，不能正式交付、验收、交接 S3 或推送。条件词必须有同产品/SKU/站点/声明的证书或测试内容、SHA-256、有效期和复核记录；页面性能文案、fact ID 或标记不是证明。warn 显示语境和候选替换，替换也须符合事实。

“加入禁用词：xxx”执行 `scripts/compliance_scan.py custom-add --series <产品根> --term xxx --source "user command"`。手动全系列已上架快照复扫：`rescan-series --series <产品根> --listings-manifest <清单.json> --context <上下文.json>`；清单列每个SKU和本地Listing路径，缺失快照保持覆盖缺口。本轮没有自动周期任务或线上修改。

规则唯一数值源是 `templates/amazon_platform_rules.yaml`，按调用读取；原始UTF-8含空格/标点249上限是项目保守计数，不等同Amazon索引侧计数。ST先使用有许可的新词，再自动复用核心词、必要时有界重复以达230–249；重复仅质量提示，不据此宣称排名收益。核心完整词在标题和亮点分别核查，不能以字符达标代替。

“算法匹配度”是编辑自检：五类意图提供实际字段原句与已存在事实引用；必填属性须有适用product_type schema；5条不同QA意图；核心词完整出现；CT红项有明确说明。不是Amazon评分。缺VOC、适配或性能证据如实保留。

写文案时从已批准资产/CT自动选定本次主核心并记录在SKU或context.core_keywords及既有来源注解中；H只核查这一明确选择，不把整库核心标签全部塞入标题/亮点。缺作者登记时修复作者输入，不让用户自己想词或反复补定位。

旗舰确认写入前，先用 `scripts/compliance_scan.py write-proof --proposal <审核提案.json> --listing <effective_listing.json> --context <当前上下文.json> --series <产品根> --output <proof.json>` 生成当前绑定证明，再由原推送CLI加载 `--compliance-proof`、`--compliance-context`、`--series`。它们不能替代本人reviewer/confirm；回滚另加 `write-proof --rollback` 并再次人工审核，旧文案也重新扫描。仅四个既有API属性可写，其他字段仍手工处理。无实测回读不得称已写入成功。
