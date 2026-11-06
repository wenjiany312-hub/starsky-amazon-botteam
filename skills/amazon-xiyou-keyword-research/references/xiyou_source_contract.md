# 西柚来源字段与标准化契约

## 目录

1. [适用范围与来源身份](#1-适用范围与来源身份)
2. [基础 18 字段](#2-基础-18-字段)
3. [每个导出 ASIN 的 7 类字段](#3-每个导出-asin-的-7-类字段)
4. [null、0 与 status](#4-null0-与-status)
5. [指标不可替代](#5-指标不可替代)
6. [normalizer CLI 与产物](#6-normalizer-cli-与产物)
7. [数量守恒](#7-数量守恒)
8. [未知列和错误处理](#8-未知列和错误处理)

## 1. 适用范围与来源身份

只接收西柚洞察“多 ASIN 对比”导出的 `.xlsx`，以及本轮真实调用的西柚/Xiyou/xydc MCP/API。SIF、SellerSprite、Sorftime 数据不得写入本包补空。

每个文件独立分配 `source_id`，保存相对路径、`sha256:<64hex>`、Sheet、marketplace、`as_of`、`period.month`、timezone、单位、字段映射、未知列和缺失统计。多个批次不能互相覆盖。

支持两种列顺序，解析均基于“ASIN + 字段后缀”而不是固定列号：

- `by_asin_grouped`：同一 ASIN 的 7 类字段连续。
- `by_metric_grouped`：同一指标跨多个 ASIN 连续。

## 2. 基础 18 字段

| 西柚原字段 | canonical | 单位/语义 |
|---|---|---|
| 关键词 (数据来源于西柚洞察) | `keyword/source_keyword` | 原词与归一键并存 |
| 翻译 | `cn_translation` | 辅助语义，不覆盖英文 |
| 词标签 | `source_tag` | 平台标签，不替代六维 |
| 相关性得分 | `relevance_score` | 原始分数；0 可为有效值 |
| 相关性档位 | `relevance_tier` | 平台档位，仍需产品事实复核 |
| 流量总和 | `traffic_sum` | 流量规模，不是销量 |
| 周平均关键词排名 / 关键词排名 | `avg_keyword_rank` | 正数越小越靠前；空/0 为 null |
| 周平均搜索量 / 周搜索量 | `avg_search_volume` | 周平均；记录快照月不改变单位 |
| CPC建议竞价($) | `cpc_suggested` | USD 第三方市场信号 |
| 建议竞价范围($) | `bid_low/bid_high` | 两端保留，不造中点 |
| 点击转化率(均值) | `click_conversion_rate_mean` | 西柚直接字段；非 Amazon 账户 CVR |
| 周平均竞争难度 / 竞争难度 | `avg_competition_difficulty` | 平台竞争信号，不是排名 |
| 竞争难度档位 | `competition_tier` | 平台档位 |
| 自然位滚动率 | `organic_rank_volatility` | 位置波动，不是 CTR/CVR |
| Top3周平均点击份额 / Top3 点击份额 | `top3_click_share` | 头部点击集中度 |
| Top3周平均转化份额 / Top3 转化份额 | `top3_conversion_share` | 头部转化集中度，绝不当 CVR |
| Top3 ASIN | `top3_asins` | 头部占位线索，不等于确认竞品 |
| asin数量 | `asin_count` | 导出样本字段，不替代确认覆盖 |

百分比标准化为 0–1。原字段为 null 时值保持 null 并写 status；不能填 0。

## 3. 每个导出 ASIN 的 7 类字段

| 字段后缀 | canonical | 规则 |
|---|---|---|
| 流量份额 | `traffic_share` | 与流量占比分开 |
| 流量占比 | `traffic_ratio` | 与流量份额分开 |
| 自然排名 | `organic_rank` | 正数有效；空/0 为 null |
| 自然排名(页码-页内排名) | `organic_rank_display` | 审计文本；0/0-0 不视为排名 |
| SP广告排名 | `sp_rank` | 正数有效；不是广告花费 |
| SP广告排名(页码-页内排名) | `sp_rank_display` | 审计文本；0/0-0 不视为排名 |
| 展示位置 | `display_positions` | 自然/SP/SB/SBV 等原样保留 |

normalizer 对每个有效源行和每个已发现导出 ASIN 都输出一条 evidence，即使该 ASIN 的 7 个字段全空。这样才能验证：

```text
asin_evidence_rows = normalized_valid_rows × discovered_export_asin_count
```

`export_observed_asins` 只包括存在正流量份额/占比、正自然/SP 排名或有效展示文本的 ASIN。

## 4. null、0 与 status

数值字段至少使用：

- `provided`：源字段有可解析值；非排名字段的真实 0 可保留。
- `missing`：空、`-`、`N/A` 等源缺失。
- `invalid`：非空但无法按字段类型解析，原值仍可由 lineage 定位。
- `not_observed_zero_rank`：排名字段为 0 或非正数，标准值为 null。

空值与 0 不得互换。排名字段不得输出 0；显示值 `0`、`0-0`、`0/0` 也不能形成 observed signal。

## 5. 指标不可替代

| 禁止混用 | 正确边界 |
|---|---|
| `top3_conversion_share → click_conversion_rate_mean` | 只有“点击转化率(均值)”进入直接字段；Top3 只是集中度 |
| 西柚点击转化率 → `amazon_account_cvr` | semantic 固定为第三方非账户 CVR；账户 CVR 来自 Amazon orders/clicks |
| `traffic_sum ↔ avg_search_volume` | 一个是流量规模，一个是周均搜索量 |
| `traffic_share ↔ traffic_ratio` | 两列分别保留 |
| 自然位滚动率 → CTR/CVR/竞争难度 | 只解释位置波动 |
| Top3/导出 ASIN → 确认竞品 | 必须与 `confirmed_competitor_asins` 取交集 |

第三方 CPC/CVR 只可作 Strategy 情景输入，不能越过利润/账户数据决定最终 Bid。

## 6. normalizer CLI 与产物

```powershell
python .\scripts\normalize_xiyou_export.py `
  --input <xiyou.xlsx> `
  --output-dir <normalized-dir> `
  --source-id SRC-XIYOU-FILE-001 `
  --as-of <ISO-8601> `
  --period-month YYYY-MM `
  --marketplace US `
  --source-log <S2_文案板块/amazon-xiyou-keyword-research/06_keyword_data_source_log.yaml>
```

固定输出：

- `xiyou_keywords_normalized.csv`：有效源行级基础指标、状态和来源元数据。
- `xiyou_keyword_asin_evidence.csv`：有效源行 × 导出 ASIN 全笛卡尔证据。
- `xiyou_export_validation.json`：字段、未知列、SHA、null、原始/有效/重复和 evidence 守恒。
- `--source-log`（可选）：写 JSON 语法的 YAML 1.2 来源日志，不依赖 PyYAML。

脚本不合并 unique records、不做产品相关性、IP、Listing/广告池、Campaign、Bid 或上传决策。unique 合并及五类 disposition 在 Skill 层完成，所有源行写入 record lineage。

## 7. 数量守恒

normalizer 必须先通过：

```text
raw_rows = blank_keyword_rows + invalid_rows + normalized_valid_rows
normalized_valid_rows = unique_normalized_keywords + merged_duplicate_rows
asin_evidence_rows = normalized_valid_rows × discovered_export_asin_count
```

Package 层再通过共享五类 disposition 守恒。任一失败都不得 handoff。

## 8. 未知列和错误处理

未知列保存表头、列位置、非空数量和最多 3 个样例；不猜用途、不静默丢失。缺基础必需字段或未发现任何 ASIN 字段时停止并要求重新导出“多 ASIN 对比”。

多 Sheet 默认选择必需字段最多、ASIN 最多、有效行最多者；可以用 `--sheet` 显式指定。文件只支持 `.xlsx`。
