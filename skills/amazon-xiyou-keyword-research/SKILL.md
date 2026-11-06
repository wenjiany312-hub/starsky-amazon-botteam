---
name: amazon-xiyou-keyword-research
version: "4.1.0"
edition: starsky-v2
description: >
  西柚洞察单源关键词研究与 Keyword Package 2.0 生产 Skill。读取西柚“多 ASIN
  对比”导出 .xlsx，保留周均搜索量、第三方 CPC、点击转化率、Top3 集中度以及逐词
  × ASIN 的流量/自然位/SP 位证据，并输出带 source_id、SHA-256、as_of、period.month、
  null 状态、数量守恒、Hash 和 Builder 血缘的独立 keyword_package.yaml（写在本 Skill 夹，source_id=xiyou）。
  Top3 转化份额绝不当 CVR；不静默混入 SIF 或 SellerSprite。触发：西柚找词、
  西柚洞察、西柚关键词、多 ASIN 对比、清洗西柚 Excel、xiyou keyword、广告分池。
---

# 西柚关键词研究 4.0

## 目标与职责

把西柚多 ASIN 导出变成可审计、可局部修订、可交给 Listing 与广告闭环消费的单源关键词资产。保留平台原始语义和逐 ASIN 证据，不把代理指标包装成 Amazon 账户事实。

不决定最终 Campaign 数、预算、Bid、动态竞价或 Placement，不生成或授权 Bulk 上传。Strategy 负责经营决策，Builder 负责编译广告计划。

## S2 共同判断与固定报告

必读 `../../templates/keyword_report_layout.md`：三源共用八节报告和逐词判断方法；Listing 固定十节，详见 Copywriter 的报告契约。先读当前 S1/企业 IP 与声明限制，再做本品匹配、文案用途、广告用途，最后按同源同窗指标排序。

每词记录产品词根、主分类（产品词/属性词/其他）、多标签（功能/规格/材质/场景/人群/兼容/痛点）、direct/adjacent/mismatch/unknown 与 fact_refs。D1–D6 保留兼容；主分类统计唯一词，多标签不能重复计入唯一词数。场景、保留率和研究卡不设数量或比例下限。

继承用户或 manifest 选择；仅一个导表来源就沿用它，没有来源选择时默认优先 SIF。用户明确“仅用导表/不调用”时不调 MCP。运行前读本任务工具与 schema，分别记录配置/授权、工具暴露、协议与业务返回；只据本轮真实业务返回写 used/partial。

同源 MCP 增量用新 source_id 保留原始返回和脱敏调用摘要。跨源补数放独立 evidence/context，不能冒名填主包空值。优先补本品可用的高价值词、分歧词、季节敏感词；失败显示缺口、影响和可重试入口。

季节分析默认尝试两个完整年度可比历史；只有一年降低确定性，只有一周不判全年峰值。历史与当前榜分开；搜索量合计称“选定词集合的搜索量”，不称去重买家流量。

正式入口为插件根 `scripts/s2_report.py --package <path> --context <path> --output-dir <dir>`；有文案加 `--listing`，有基线加 `--baseline`。参数/JSON 见 `../../templates/s2_report_input.md` 和 `--help`。不写每个产品自己的 HTML 生成器。

同轮生成 keyword_report.html、keywords.csv、report_model.json、validation.json、manifest.json；文案输入另生成 listing_report.html、listing.md。旧 06/07 文件名可由路由映射并记录指纹；不按 KB、表格或进度条数验收。正式用途资格必须写入词包并更新 Hash，不能只改报告 context 后送广告。

## 执行前必读

1. `../_keyword_system_contract/references/keyword_package_2_contract.md`：统一 Package、Hash、来源隔离和下游交接。
2. `references/xiyou_source_contract.md`：西柚字段、双布局、null/排名、逐 ASIN 证据和 normalizer 契约。
3. 当前 run 的 product profile/facts、已确认竞品池、IP/合规黑名单和资料叠层。

## 运行模式

- `production`：可进入 Strategy/Builder 审核；词包本身永远不授权外部上传。
- `demo_direct_run`：写 `DEMO_ONLY_NOT_UPLOADABLE`；演示参数、占位 ASIN/SKU 不得进入 Production Bulk。

缺少产品事实、竞品池或 IP 结论时先完成结构/字段诊断，把受影响池标为 conditional。只有继续会伪造事实、越过生产 IP 决策、执行不可逆外部动作，或用户要求可上传文件但缺真实标识/官方模板时 hard block。

`blocked_claim` 需求词保留在 records 并写 `claim_restricted=true`，但退出当前可用文案池及默认广告候选；补证和具体用途审核后再更新资格。

## 输入与单源边界

| 输入 | 最低要求 | 缺失处理 |
|---|---|---|
| 西柚多 ASIN `.xlsx` | 至少一个可识别关键词 Sheet 和一组 ASIN 字段 | 无文件时仅在本轮真实可调用西柚 MCP/API 时拉取；否则不编词 |
| product scope | marketplace、product_id | Demo 可用显著占位；Production 下游 conditional |
| product profile/facts | 核心/排除词根、事实、适配边界 | 先标准化；语义、Listing 和核心广告池待审 |
| competitor pool | 已确认 ASIN 和决定来源 | 只报 export observed，不称 competitor coverage |
| blacklist | 品牌、商标、平台禁用、blocked claim 分型 | 品牌/敏感词先隔离，不假装已合规 |

本线 `source_mode=xiyou`、`source_line_policy=isolated`。发现 SIF 或 SellerSprite 文件时只记录另一路线存在，不读取其指标补空，不输出跨源平均值。多来源研究应分别产三个词包，由总控或用户选择一个 `package_id/version/hash`。

## 标准作业流程

### Step 0｜建立运行身份和来源账本

生成 `run_id/package_id/package_version`。每个西柚文件独立登记 `source_id`、相对路径、原文件 SHA-256、Sheet、原始行数、marketplace、`as_of`、`period.month`、timezone、字段单位、字段映射、未知列、缺失率和失败信息。

`period.month` 是导出快照月份；`avg_search_volume` 的单位仍是周平均，不得因记录月份而改写为月搜索量。

### Step 1｜MCP 真实性

只有本轮实际调用西柚/Xiyou/xydc 工具，且 `mcp.calls[]` 记录工具名、调用时间、参数摘要、结果状态和返回量时，才写 `used/partial`。

- 工具未暴露：`skipped + no_callable_xiyou_tool_this_run`，继续清洗 Excel。
- 本轮调用失败：`failed`，记录脱敏错误，继续使用已有文件。
- 调用成功：分配新 `source_id` 并列保存，不能覆盖 Excel 原值。

SIF、SellerSprite、Sorftime 不得作为本线 MCP 补空。

### Step 2｜确定性标准化

运行附带脚本：

```powershell
python .\scripts\normalize_xiyou_export.py `
  --input "S2_文案板块\_投喂_关键词导出\西柚找词\多ASIN对比.xlsx" `
  --output-dir "S2_文案板块\amazon-xiyou-keyword-research\06A_西柚关键词底表" `
  --source-id "SRC-XIYOU-FILE-001" `
  --as-of "2026-08-09T12:00:00+08:00" `
  --period-month "2026-08" `
  --marketplace "US" `
  --source-log "S2_文案板块\amazon-xiyou-keyword-research\06_keyword_data_source_log.yaml"
```

脚本只做字段识别、数值/null/排名标准化、来源留痕、逐词 × ASIN 长表和守恒校验；不做产品语义、Listing/广告分池或最终词包决策。

必须保留：

- 原词、归一词、source row、source_id、as_of、period.month 和原文件 Hash。
- 18 类基础字段、7 类逐 ASIN 字段及每个数值的 status。
- 所有未知列的表头、位置、非空量和样例；不能静默丢失或猜语义。
- `normalized_valid_rows × discovered_export_asin_count = asin_evidence_rows`。
- `raw_rows = blank + invalid + normalized_valid`，以及 unique/merged duplicate 数量。

### Step 3｜指标防误映射

- `click_conversion_rate_mean` 仅来自“点击转化率(均值)”，语义是 `xiyou_click_conversion_rate_mean_not_amazon_account_cvr`。
- `top3_conversion_share` 是头部转化集中度，绝不填入 CVR 或作为账户 CPA 分母。
- 周平均搜索量与流量总和分开；流量份额与流量占比分开；自然位滚动率不改名为 CTR/CVR/竞争难度。
- 周平均关键词排名、自然排名、SP 排名中的空值或 0 标为 null，并分别写 `missing` 或 `not_observed_zero_rank`；不得出现“第 0 名”。
- 第三方 CPC/CVR 只进入 Strategy 的情景输入；账户 CVR 只能由 Amazon 报表 `orders/clicks` 计算。

### Step 4｜去重、污染、品牌、IP 与数量守恒

归一化同词合并成一个 record，所有原始行进入 lineage。使用完整词/短语边界，核心词根优先于排除词根。

每个唯一词且只进入一个 disposition：`retained / observe / brand_isolated / compliance_negative / low_relevance_negative`。全部唯一词保留在 records；守恒失败不得 handoff。

竞品/未许可品牌不进入消费者可见池；已确认自有品牌可用于 Title，兼容词按 S1 指定范围复核。IP 待审不是自动否词；否定建议另记作用范围、匹配方式、理由与误伤检查。

### Step 5｜六维和竞品共同覆盖

按当前产品事实生成 D1 材质/结构、D2 功能/机制、D3 场景、D4 人群、D5 对象、D6 意图；未分类词计数、举例并说明影响。场景数量和 inferred 比例可以作为案例诊断，不硬编码为所有类目的通用门槛。

共同覆盖只计算：

```text
competitor_asins = export_observed_asins ∩ confirmed_competitor_asins
coverage_ratio = len(competitor_asins) / confirmed_competitor_count
```

确认竞品数为 0 时 ratio 为 null 并写原因。Top3 ASIN、导出 ASIN 数或平台 asin 数量都不能替代确认竞品池。

### Step 6｜Listing 和广告候选池

先通过当前 SKU 的事实、匹配与声明许可，再输出可用 Listing ID 池：Title、Highlights、Bullet feature/scenario、Description/A+、ST candidates、Attributes、Q&A；待核留 Fitment review/observe。最终 ST 词去重、去禁用品牌和 UTF-8 249 bytes 由 Copywriter 完成。

待证/禁用词退出默认投放候选；按事实与用途资格输出 Builder 可解析的候选：

```text
exact = exact_p1 ∪ exact_p2
phrase_feature
phrase_scenario
broad_discovery
observe
negative_pools.exact / phrase
asin_pools.pat_candidates / defense_candidates / brand_review
```

P1/P2 是当前词包证据层级，不代表 Amazon 账户已验证出单。只保留西柚 CPC/range、填充率和经济模型所需输入；Strategy 结合利润、阶段和账户 CVR 决定最终可承受 CPC/Bid。

### Step 7｜只生成西柚专属词包

权威结构化产物固定为：

```text
S2_文案板块/amazon-xiyou-keyword-research/keyword_package.yaml
S2_文案板块/amazon-xiyou-keyword-research/06_keyword_data_source_log.yaml
S2_文案板块/amazon-xiyou-keyword-research/06_关键词库报告.md
S2_文案板块/amazon-xiyou-keyword-research/keyword_package.validation.json
```

V2.1 靠 Skill 夹区分来源：本线只写 `S2_文案板块/amazon-xiyou-keyword-research/keyword_package.yaml`（`source_id` 标 `xiyou`），⛔ 不得写进 SIF / 卖家精灵的 Skill 夹，也不得在产品根或 `_阶段总览/` 另存副本。Markdown/HTML 只是审阅视图，不是 Builder 真源。

### Step 8｜Hash 和共享 validator

使用共享模板生成 JSON 语法的 `.yaml`，确保无 PyYAML 环境也可校验：

```powershell
python ..\_keyword_system_contract\scripts\keyword_package_contract.py finalize `
  --input "S2_文案板块\amazon-xiyou-keyword-research\keyword_package.yaml" `
  --expected-source xiyou `
  --report "S2_文案板块\amazon-xiyou-keyword-research\keyword_package.validation.json"
```

校验失败不得进入下游。任何 source、record、pool 或 metric 变化都必须改变 Hash；旧 Strategy/Plan 审核随旧 Hash 失效。

### Step 9｜数据解释和局部修订

按统一八节报告展示来源/窗口、逐 ASIN 证据、资格、用途、缺口与引用。Top3/CVR 边界和未知列保持可核对，全量研究词可定位；反方只据实际证据记录。

后补文件或字段时保留 v1，递增 `package_version`，写 `supersedes`、changed source/record/field、受影响和不受影响产物。只重算影响链，不重洗 SIF/SellerSprite 包。

## Acceptance checks

### P0

- [ ] Skill frontmatter、Package Schema/Hash 和验证报告均通过。
- [ ] 每个文件有 source_id、SHA-256、as_of、period.month、marketplace、单位和字段映射。
- [ ] provider 全为 xiyou；无 SIF/SellerSprite/Sorftime 指标混入。
- [ ] MCP `used/partial` 有本轮调用证据；否则为 skipped/failed。
- [ ] 未知列已记录；null/0 和周/月未误映射。
- [ ] Top3 转化份额未进入 CVR；0/空排名未变成第 0 名。
- [ ] 原始/有效/重复/五类 disposition/records 及逐词 × ASIN 证据均守恒。
- [ ] Listing、广告、Negative、ASIN 池 ID 可解析到 records 和 lineage。
- [ ] Demo、外部上传边界、data explanation 和 late revision 字段完整。

### P1

- [ ] 竞品覆盖只使用确认池交集；六维/交叉入口可复核。
- [ ] CPC/CVR 填充率、集中度和固定阈值均标来源/适用条件。
- [ ] 最强反方说明为什么当前词包不能直接扩大投放。

所有勾选附路径、算式或计数；空勾无效。

## Handoff

```markdown
【本步完成】amazon-xiyou-keyword-research 4.0
【词包】S2_文案板块/amazon-xiyou-keyword-research/keyword_package.yaml（package_id/version/hash）
【来源】Xiyou files=<N> / MCP=used|partial|skipped|failed
【证据】raw=<N> / normalized=<N> / unique=<N> / evidence=<N> (= normalized×ASIN)
【下一步】显式选择本包后交 amazon-ad-strategy-architect；Builder 只消费该 Hash。
【边界】Top3 转化份额不是 CVR；本词包不决定 Campaign/Bid，也不授权上传。
```

## 版本记录

| 版本 | 说明 |
|---|---|
| 4.0.0 | 西柚单源 Package 2.0；强化 source/as_of/month/Hash、未知列、null/0 排名、逐词×ASIN 守恒、Strategy/Builder 血缘、Demo 与局部修订；取消跨源补空及词库内 Campaign/最终 Bid/上传职责。 |


### R02 分层与持久词库接口

S2 竞品采用同类目 BSR 1–30 / 31–60 / 61–100 的 6/6/5 个与官方新品榜3个；只以相关同类/同款补位并标来源段、补位理由、价格带状态。新品榜不可得有证据才用日期窗口回退；不足报告实际数。旧候选池、原备注保留，新分层需本表用户确认，不能继承旧20池审批或演示默认。

完整单源词包用 `keyword_package_contract.py finalize --series <真实系列根>`，通过分层/来源/完整性门禁后接入系列资产；CSV normalize 不等于入库成功。只取 `keyword_asset.py select --limit 20` 摘要与正式关键词报告链接，禁止将整个 master 读入/粘贴模型上下文。保留原指标与周/月口径，来源不能互补填空。具体接口及 CT 有限用途边界见 `../../docs/keyword_asset_contract.md`。
