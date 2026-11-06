# SellerSprite 来源字段契约

## 1. 主要字段

| 原字段 | canonical | 语义/要求 |
|---|---|---|
| 关键词 | `keyword/source_keyword` | 原词和归一键并存 |
| 关键词翻译 | `cn_translation` | 辅助语义 |
| AC推荐词 | `is_ac_choice` | 平台标记，不等于真实转化 |
| 流量占比 | `traffic_share` | 保留原单位/窗口 |
| 流量词类型 | `traffic_type` | 自然/广告等平台标签 |
| 预估周曝光量 | `weekly_impressions` | 曝光代理，不是搜索量/销量 |
| 相关产品 | `related_products` | 相关线索 |
| 相关ASIN | `related_asins` | 导出观察 ASIN，不等于确认竞品 |
| ABA周排名 | `aba_rank` | 周排名语义 |
| 月搜索量 | `monthly_search_volume` | 必须带 period.month |
| 月购买量 | `monthly_purchases` | 平台购买量字段，不称本品销量 |
| 购买率 | `purchase_rate` | `sellersprite_purchase_rate_not_amazon_ads_cvr` |
| 展示量/点击量 | `impressions/clicks` | 保留平台口径 |
| SPR | `spr` | 平台竞争信号，不与其他缩写混用 |
| 标题密度 | `title_density` | Listing 覆盖信号 |
| 商品数 | `product_count` | 供给信号 |
| 需供比 | `demand_supply_ratio` | 平台机会信号 |
| 广告竞品数 | `ad_competitors` | PPC 压力信号 |
| 点击总占比 | `total_click_share` | 集中度 |
| 转化总占比 | `total_conversion_share` | 集中度，不是 CVR |
| PPC竞价 | `ppc_bid` | USD 外部信号 |
| 建议竞价范围 | `cpc_low/cpc_high` | 两端保留，不强造中点 |
| 前三/前十 ASIN | `top_asins` | 占位/定位候选，需与确认池取交集 |

## 2. purchaseRate 红线

`purchase_rate` 可以原样保存，也可以在月购买量/月搜索量都存在时另存 `derived_purchase_rate` 并保留公式。两者都不得：

- 写入 `metrics.cvr`；
- 标 `amazon_account_cvr`；
- 直接作为账户 CPA、盈亏 Bid 或暂停动作的唯一分母；
- 在没有 month/source_id 时称为当前数据。

进入 Strategy 时明确为第三方情景信号。账户真实 CVR 由 Amazon 广告报表 `orders/clicks` 计算。

## 3. 工作簿附表

- `Unique Words`：根词频、位置/材质/功能/对象覆盖和污染候选；不直接批量否定。
- `Asin`：查询/导出 ASIN 上下文；与确认竞品池取交集。
- `Notes`：保留来源说明，不参与数值计算。

多个主表/批次必须独立 source_id；不同月份不覆盖。

## 4. Fitment/兼容性

车辆/设备 make/model/year、部件号、尺寸、代际、左右/前后位置只有当前商品证据支持时才能进入前台和正式精准池。未确认项进入 `fitment_review`；广告实验也须由 Strategy 显式决定并设置风险边界。

## 5. 机会阈值

SPR、title density、需供比、广告竞品数和集中度可排序、分位和异常检测。诸如 `SPR≤50`、`标题密度≤10`、`需供比>30` 只作为旧案例默认；必须写适用条件、企业覆盖和失效条件，不能硬编码为所有类目规则。

## 6. 出价

词包保留 `ppc_bid/cpc_low/cpc_high`、来源月与填充率。最终：

```text
allowable_cpc = target_cpa * expected_account_cvr
initial_bid <= economic_guardrail decided by Strategy
```

无利润/账户 CVR 时只提供市场信号与条件性区间，不给伪精确推荐。
