# 表头、来源与归因契约

## 1. 为什么来源单元是最小真相边界

一个文件的一个工作表定义一个 `source_unit_id`。汇总时始终保留该字段，除非人工证明以下五项同时成立：

1. 同一广告类型与账户边界；
2. 同一报表粒度；
3. 同一归因窗口；
4. 日期不重叠；
5. 对象不重复，或已有可审计去重键。

未证明前只能并排比较，不能合计。尤其禁止：

- SP 7 天订单/销售与 SB/SD 14 天订单/销售相加；
- Campaign 报表与 Search Term 报表重复相加；
- 月报与其内部周报相加；
- 不同 Parent/Child 变体边界静默合并。

## 2. 来源清单字段

每个接受单元必须登记：

```yaml
source_id: SRC-<file_sha_prefix>
source_unit_id: UNIT-<file_sha_and_sheet_prefix>
relative_path: ""
absolute_path: ""
sha256: ""
sheet: ""
header_row_zero_based: 0
rows: 0
original_columns: []
mapped_columns: {}
duplicate_mappings: {}
ad_type: SP | SB | SD | unknown
object_level: campaign | ad_group | targeting | search_term | placement | advertised_product | purchased_product
attribution_window_days: null
date_range: {start: null, end: null}
warnings: []
```

## 3. 标准列

| 标准字段 | 常见英文 | 常见中文 | 类型 |
|---|---|---|---|
| campaign | Campaign Name | 广告活动名称 | 文本 |
| ad_group | Ad Group Name | 广告组名称 | 文本 |
| targeting | Targeting / Keyword | 投放 / 关键词 | 文本 |
| match_type | Match Type | 匹配类型 | 文本 |
| search_term | Customer Search Term | 客户搜索词 / 搜索词 | 文本 |
| placement | Placement | 广告位 / 投放位置 | 文本 |
| advertised_asin/sku | Advertised ASIN/SKU | 广告商品 ASIN/SKU | 标识文本 |
| purchased_asin/sku | Purchased ASIN/SKU | 已购买商品 ASIN/SKU | 标识文本 |
| impressions | Impressions | 展示量 / 曝光量 | 数值 |
| clicks | Clicks | 点击量 | 数值 |
| spend | Spend / Cost | 花费 / 支出 | 货币数值 |
| sales | N Day Total Sales | N天总销售额 | 货币数值 |
| orders | N Day Total Orders | N天总订单数 | 数值 |
| units | N Day Total Units | N天总销售量 | 数值 |

列映射是逐列进行的；同一行可以同时有中文与英文表头。两个原始列映射到同一标准列时只做非空合并，并把原列名写入 `duplicate_mappings`。

## 4. 归因窗口

只从当前表头中明确出现的 `7 Day/14 Day/N天` 推断。文件名、旧课程或“SP 通常 7 天”不能替代当前字段事实。多个窗口同时出现或完全未出现时写 `null/unknown`。

同一窗口也不代表可以直接相加：仍需检查日期、对象、广告类型和报表粒度。

## 5. 数值与空值

- `$`, `£`, `€`, `¥`, `￥`、千分位和括号负数可解析。
- `-`、空白、`N/A` 保持缺失，不先填 0。
- ASIN、SKU、Campaign/Ad Group/Target ID 始终按文本保存。
- 任何百分比列仅作来源审计；CTR/CVR/ACoS 等从聚合计数重算。
- sales=0 与 sales 缺失含义不同，但二者都不能生成 ACoS=0%。

## 6. 报表识别降级

先看当前 `Ad Type/Ad Product` 列，再看文件/工作表中明确的 Sponsored Products/Brands/Display 名称；无法可靠识别则写 `unknown`。`unknown` 仍可在来源内分析，但不得参与跨类型汇总。

报表粒度按字段判断：Purchased Product → Search Term → Placement → Targeting → Advertised Product → Ad Group → Campaign。若只有指标而无对象维度，跳过并记录原因。

## 7. 数量守恒

验收至少对账：

- 已发现文件数；
- 接受/跳过工作表数；
- 每个来源单元原始有效行数；
- normalized 行数；
- 每层 aggregation 行数；
- YAML 展示行数与全量 CSV 路径；
- source SHA-256 与运行后再次计算一致。

Markdown/HTML 可只显示 Top N，但必须给出全量 CSV 入口和总行数。

