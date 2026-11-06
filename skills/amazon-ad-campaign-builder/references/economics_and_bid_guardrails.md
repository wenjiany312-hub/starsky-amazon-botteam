# 经济、CVR 与最坏有效 Bid 护栏

## 1. 指标语义

```text
target_cpa = selling_price × target_acos
target_cpc = target_cpa × expected_cvr
break_even_acos = contribution_margin_before_ads / selling_price
break_even_cpc = contribution_margin_before_ads × expected_cvr
```

若 contribution margin 缺失但有经确认的 break-even ACoS，可反推；若两者都缺失，只能做
曝光/市场信号草案，production 不得声称守住利润线。

所有除法零分母返回 `null + reason`。币种、站点、时间窗口和归因口径必须跟随字段。

## 2. CVR 证据

`expected_cvr` 最少包含：

```yaml
value: 0.12
clicks: 80
orders: 10
source_type: amazon_account | third_party | user_assumption | case_default
source_id: REPORT-20260801
window: {start: 2026-07-18, end: 2026-07-31, timezone: America/Los_Angeles}
metric_semantic: advertised_product_orders_over_clicks
sample_reliability: high | medium | low | external_estimate | unknown
```

- 只有 Amazon 账户观测值才按 clicks/orders 阈值评估样本可靠性。
- SIF、卖家精灵、西柚等转化或购买指标保持原语义，标 `external_estimate`。
- 小样本可以用于区间估算，但不能作为提高 Bid 或预算的唯一证据。
- 阈值来自 `case_defaults.yaml` 或企业覆盖，不是 Amazon 官方规则。

## 3. 初始 Bid

市场 CPC 是需求信号，不是必须追到的价格：

```text
demand_candidate = external_cpc_signal × intent_factor
economic_cap = min(target_cpc, break_even_cpc)
initial_bid = min(demand_candidate, economic_cap × safety_factor)
```

若新品目标 ACoS 高于盈亏线，仅当 brief 同时有 `confirmed=true`、最大探索亏损、截止时间和回滚
条件时，economic cap 才可暂用 target CPC；否则仍以 break-even CPC 为上限。

不得把目标 CPC 强制夹到第三方“建议 CPC 低值”以上。

## 4. 最坏有效 Bid

平台叠加公式可能变化，Builder 只在 brief 明确当前账户公式与因子时计算：

```text
worst_case_effective_bid =
  base_bid
  × dynamic_bidding_multiplier
  × placement_multiplier
  × business_multiplier
  × audience_multiplier
```

`placement_multiplier` 由当前最高适用位置加价转换。若当前平台为不同叠加规则，Strategy 必须
给 `formula_mode` 和核验来源，脚本不得擅自假设。

预检逻辑：

1. 计算总 multiplier。
2. `guardrail_base_bid = allowable_cpc / total_multiplier`。
3. 初始 Bid 超过该值时收紧并记录 `WORST_CASE_BID_CAPPED`。
4. 存在非零调整但公式/因子未知时，production `blocked`。

## 5. 预算守恒

预算按 Strategy 权重归一化：

```text
campaign_budget = total_daily_budget × weight / sum(active_weights)
```

货币精度产生的余差回填给第一个活动并记录，不得让 Campaign 合计与总预算不一致。固定预算占比、
每日美元额和阶段系数只能作为 `case_default`。

