# Keyword Package 2.0 统一契约

## 目录

1. [职责和唯一真源](#1-职责和唯一真源)
2. [三线隔离](#2-三线隔离)
3. [来源与 MCP 真实性](#3-来源与-mcp-真实性)
4. [指标语义](#4-指标语义)
5. [数量守恒](#5-数量守恒)
6. [竞品共同覆盖](#6-竞品共同覆盖)
7. [Listing 分池](#7-listing-分池)
8. [广告分池与映射](#8-广告分池与映射)
9. [给 ad_strategy_brief 的字段](#9-给-ad_strategy_brief-的字段)
10. [Production / Demo 边界](#10-production--demo-边界)
11. [数据解释与后补修订](#11-数据解释与后补修订)
12. [Acceptance checks](#12-acceptance-checks)

## 1. 职责和唯一真源

关键词 Skill 负责把某一个明确平台的导出/MCP 证据变成可追溯的关键词资产。它不决定最终 Campaign 数、预算、动态竞价或 Placement；这些由 `amazon-ad-strategy-architect` 与 `amazon-ad-campaign-builder` 完成。

每条来源线的权威产物是：

```text
keyword_package_<source>.yaml
06_keyword_data_source_log_<source>.yaml
06_关键词库报告_<source>.md
```

Markdown/HTML 是审阅视图，不能成为 Builder 的输入真源。`package_id + package_version + package_hash` 唯一标识一次可执行词包版本。

## 2. 三线隔离

| 线 | `source_mode` | 允许的文件/MCP | 禁止 |
|---|---|---|---|
| SIF | `sif` | SIF 导出、SIF MCP | 偷用 SellerSprite/西柚指标补空 |
| 卖家精灵 | `sellersprite` | SellerSprite 导出、SellerSprite MCP | 把 Sorftime/SIF 称为本线补数 |
| 西柚 | `xiyou` | 西柚多 ASIN 导出、西柚/xydc MCP | 把其他平台共同覆盖写进本线 |

多个同平台文件可以进入同一包，但每个文件/调用必须有独立 `source_id`、SHA-256（文件）、`as_of`、period/month、站点、字段单位和缺失说明。

跨平台研究可以并行产出三个包；总控或用户按 `templates/keyword_package_selection.template.yaml` 生成 `keyword_package_selection.yaml`，只选择一个 `package_id + package_version + package_hash` 进入下游。若确需跨源对照，只做并列表，保留原值、时间窗和语义；不得生成加权平均或无血缘的“综合值”。选择记录只授权进入 Strategy 审核，不授权 Bulk 上传。

## 3. 来源与 MCP 真实性

`mcp.status=used` 只在本轮实际调用过对应工具、且 `calls[]` 记录工具名、调用时间、参数摘要、结果状态和返回量时成立。仅发现工具名、读取旧日志或知道曾配置过都不能写 `used`。

允许状态：

- `used`：本轮至少一条成功/部分成功调用。
- `partial`：本轮调用成功，但覆盖字段/关键词不完整。
- `skipped`：本轮未调用；必须给原因。连接器缺失不阻断已有导出表清洗。
- `failed`：本轮真实调用失败；记录脱敏错误，继续使用已有文件。

MCP 新值不得覆盖导出原值。两个证据以不同 `source_id` 并列，冲突进入 `source_conflicts`。

## 4. 指标语义

每个指标对象至少带 `value`、`status`、`source_id`、`semantic`、`period`。状态只用 `provided / derived / proxy / missing / invalid / not_observed_zero_rank / not_applicable`。空值保持 `null`；`value=null` 时必须写 `missing_reason`，不得填 0；排名源值为 0 时使用 `not_observed_zero_rank`，不能输出第 0 名。

| 指标 | 正确语义 | 禁止 |
|---|---|---|
| SIF `cvr_24h` | 第三方 24h 归因点击转化字段 | 冒充账户广告 CVR |
| SellerSprite `purchase_rate` | 平台购买率/购买信号 | 命名为 Amazon 广告 CVR，或直接当账户 CPA 分母 |
| 西柚 `click_conversion_rate_mean` | 西柚直接点击转化字段 | 冒充账户广告 CVR |
| Top3 conversion share | 头部转化集中结构 | 代替 CVR |
| CPC/bid | 第三方竞价/市场信号 | 越过经济 CPC 上限 |
| search volume | 对应周/月/周均需求口径 | 擅自换算成销量或另一时间单位 |

只有 Amazon 账户报表中 `orders/clicks` 才可标 `amazon_account_cvr`。第三方 CVR/购买率只能作为 Strategy 的情景输入，并保留指标名称。

## 5. 数量守恒

必须同时满足：

```text
raw_rows = blank_keyword_rows + invalid_rows + normalized_valid_rows
normalized_valid_rows = unique_keywords + merged_duplicate_rows
unique_keywords = retained + observe + brand_isolated
                + compliance_negative + low_relevance_negative
records_count = unique_keywords
```

`records` 保存全部唯一词，包括隔离和排除词，确保每个原始词可回溯。分池为空也必须显式写 `[]` 和计数 0。

`blocked_claim` 只限制消费者可见声明，不删除真实搜索词：该词保留并标 `claim_restricted=true`。品牌、商标、平台禁止词按合规结论进入隔离/否定；IP 待审不等于自动否词。

## 6. 竞品共同覆盖

```text
competitor_asins = export_observed_asins ∩ confirmed_competitor_asins
competitor_coverage_count = len(competitor_asins)
coverage_ratio = competitor_coverage_count / confirmed_competitor_count
```

分母为 0 时 `coverage_ratio=null` 并写原因，不能写 0。Top3 ASIN、导出 ASIN 或平台 `asin_count` 都不能替代已确认竞品池。动态阈值是解释参数，不是所有类目的固定硬门槛。

## 7. Listing 分池

保留：`title_candidates`、`highlights_candidates`、`bullet_feature`、`bullet_scenario`、`description_aplus`、`search_terms_candidates`、`attributes`、`qa`、`fitment_review`。

- 品牌/竞品品牌不得进入 Title、Highlights、Bullet、A+、ST。
- ST 候选仍需 Copywriter 做去重与 UTF-8 249 bytes 最终校验；关键词 Skill 不声称已生成最终 ST。
- 未证实兼容性、型号、年份、部件号进入 `fitment_review`。
- `claim_restricted` 词可保留为搜索需求证据，但文案输出必须避开未证实声明。

## 8. 广告分池与映射

`pools` 是 Builder 可直接解析的 ID 列表：

| Keyword Package 池 | Strategy 用途 | Builder / ad_plan 映射 |
|---|---|---|
| `exact` | 精准总候选 | `target_sources.pool=exact`, `match_type=exact` |
| `exact_p1` | 高证据核心候选 | 可由 PER/Rank 角色引用，不代表已验证出单 |
| `exact_p2` | 扩展精准候选 | 可由 RES/受控 Exact 引用 |
| `phrase_feature` | 属性/功能组合探索 | `match_type=phrase` |
| `phrase_scenario` | 场景/人群/对象探索 | `match_type=phrase` |
| `broad_discovery` | 干净词根探索 | `match_type=broad` |
| `observe` | 数据不足或待证实 | 默认不进生产 Target，除非 Strategy 显式实验 |

`negative_pools.exact|phrase` 映射为 Negative；`asin_pools.pat_candidates|defense_candidates` 映射为商品定位。`brand_review` 只是合规审阅候选，不等于可执行品牌投放。

关键词包不生成 Campaign，不激活 RES→PER 源端否定，也不授权 Bulk。Builder 必须逐 Target/Negative 保留 record `lineage`。

## 9. 给 `ad_strategy_brief` 的字段

`handoff.strategy_brief_inputs` 至少包含：

- 来源包引用与 Hash。
- 词量、各池数量和空池。
- 流量集中度、入口数、头腰部断层和季节性（有证据才填）。
- CPC/CVR/purchaseRate 的字段语义、窗口、填充率和缺失影响。
- 竞品共同覆盖、品牌/IP 风险、场景/人群/对象入口。
- 最强反方：为什么这批词不能直接扩大投放。

这些是策略证据，不是 Campaign 蓝图。

## 10. Production / Demo 边界

- `production`：可以进入 Strategy/Builder 审核，但词包本身永远不代表上传授权。
- `demo_direct_run`：必须写 `DEMO_ONLY_NOT_UPLOADABLE`，可用清楚标记的 demo 参数继续；不能进入 production Bulk。
- 缺产品事实、IP、真实 ASIN/SKU 只限制对应模块；除封闭 hard block 外，使用数据解释窗口继续。

## 11. 数据解释与后补修订

`data_explanation_window` 固定记录已获取事实、推导、模拟、缺失影响、继续方式。

后补数据执行 `late_data_revision`：保留 v1，递增 `package_version`，写 `supersedes`、变更 source/record/field、受影响产物和不受影响产物。Package Hash 变化后，引用旧 Hash 的 `ad_strategy_brief`、`ad_plan` 和审核自动失效；只重算受影响链，不重洗无关来源。

## 12. Acceptance checks

P0：Schema/Hash、来源隔离、SHA-256、MCP 真实性、null 语义、数量守恒、ID/lineage、品牌/IP、竞品交集、Listing/广告分池、Demo 边界均通过。

P1：流量结构、季节性、六维/交叉入口、缺失率、最强反方和局部修订影响完整。

每个通过项必须附脚本结果、算式、路径或计数；空勾选无效。
