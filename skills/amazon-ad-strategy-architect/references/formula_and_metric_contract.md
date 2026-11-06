# 广告公式、样本与经济护栏合同

## 1. 基础指标

所有指标必须带 marketplace、currency、timezone、date_range、attribution_window 和 source_id。零分母返回 `null` 与原因，不得把无销售 ACoS 写成 `0`。

```text
CTR   = clicks / impressions
CPC   = spend / clicks
CVR   = orders / clicks
CPA   = spend / orders
ACoS  = spend / attributed_sales
ROAS  = attributed_sales / spend
TACoS = ad_spend / total_sales
```

`SP/SB/SD` 归因窗口或点击/浏览归因口径不同时分别展示；未对齐前不直接相加、平均或排名。

## 2. 利润边界

```text
break_even_acos = contribution_margin_before_ads / selling_price
target_cpa      = selling_price * target_acos
break_even_cpa  = contribution_margin_before_ads
target_cpc      = target_cpa * expected_cvr
break_even_cpc  = contribution_margin_before_ads * expected_cvr
```

`contribution_margin_before_ads` 应从售价中扣除真实采购、FBA、佣金、优惠、退款/退货、仓储及其他可变成本。缺项时输出区间与受影响字段，不编造唯一盈亏 ACoS。

`apollo_reference_acos = reference_bid / (price * converting_target_cvr)` 只可标为课程参考，不等于盈亏 ACoS、官方类目基准或未来保证值。

## 3. CVR 样本可靠性

每个 CVR 证据必须包含：

```yaml
value: null
clicks: 0
orders: 0
window: {}
source_id: ""
wilson_95: {low: null, high: null}
interval_width: null
reliability: unknown | low | medium | high
decision_use: observe_only | scenario_input | primary_evidence
```

使用 Wilson 95% 区间表达不确定性。可靠性不能只看“出过几单”；至少同时看 clicks、orders、区间宽度、归因是否闭合和业务决策的风险。分类阈值由企业规则或当前任务声明；如无规则，只输出区间与相对可靠性，不把小样本点估计用作大幅加 Bid/预算的唯一证据。

外部类目 CVR、第三方 purchaseRate 或 24h 指标可以做情景输入，但不能冒充账户真实 CVR。

## 4. 初始 Bid 与曝光信号

```text
demand_signal_adjusted_bid = external_cpc_signal * intent_factor
economic_cap               = allowable_cpc * safety_factor
initial_bid                = min(demand_signal_adjusted_bid, economic_cap)
```

如果市场 CPC 信号明显高于经济上限，报告必须明确“当前利润/转化无法同时满足曝光竞争”，提供价格、页面 CVR、目标 ACoS 或探索亏损预算的条件性方案，不能用夹逼公式强行把 Bid 抬到建议区间。

缺可靠 CVR 时输出低/中/高情景：每档写 CVR 来源、target CPC、可能曝光不足风险和损失上限。

## 5. 最大有效 Bid

不得只检查基础 Bid。按当前账户实际规则计算叠加后上限：

```text
worst_case_effective_bid = platform_current_rule(
  base_bid,
  bidding_strategy,
  placement_adjustment,
  business_adjustment,
  audience_adjustment
)
```

平台对不同调整的叠加/上调方式会变化，必须从当前官方资料或账户界面提取公式。未核验时 `worst_case_effective_bid=null`，并阻止“已通过经济护栏”的结论。若最坏上限超过 target/break-even CPC，需降低基础 Bid/调整幅度或把该动作标为受控实验。

## 6. 预算与容量

```text
budget_utilization = actual_spend / configured_budget
required_clicks    = target_orders / expected_cvr
required_spend     = required_clicks * expected_cpc
```

只有预算真实触顶/接近触顶且证据窗口足够时，设置预算才被视为流量约束。份额进入容量分析前必须有：`metric_name`、`scope`、`source`、`window`。线性变化只输出敏感性情景，不做销量承诺。

## 7. 来源标签

任何数值或阈值必须属于：

- `platform_current_fact`
- `account_current_fact`
- `business_derived`
- `market_evidence`
- `user_rule`
- `case_default`
- `AI_inference`

生产自动决策不得仅由最后两类触发。每个 `case_default` 要附来源、适用条件、失效条件和覆盖入口。

## 8. 机检要求

- 所有除法零分母安全。
- 预算合计与总预算按币种精度一致。
- ACoS、TACoS、reference ACoS 分栏。
- CVR 主证据含样本与区间。
- source hash、plan/strategy hash 可复算。
- 提高 Bid/预算的动作必须通过经济上限或显式标为有损探索实验。
