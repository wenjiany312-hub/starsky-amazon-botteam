# 广告优化动作规则

## 1. 诊断顺序

```text
经营目标/异常事件
→ 广告与自然总体
→ Campaign 与预算利用率
→ Target/Match/ASIN/广告类型
→ Search Term
→ Placement
→ Listing/价格/促销/评分/库存/竞品
→ 上一轮动作结果
→ 最小可逆动作
```

不从“ACoS 高所以降 Bid”开始。

## 2. 花费四象限

| 象限 | 判断 | 常用动作 |
|---|---|---|
| 低 ACoS 出单 | 优质但仍检查增量/容量 | 保护；受预算约束时小步放量 |
| 高 ACoS 出单 | 有价值但成本高 | 查 CPC、位置、CVR、词性和目标；降控或迁移 |
| 高点击无单 | 浪费候选 | 查相关性、归因闭合、Listing；降价/否词/页面修复 |
| 低点击无单 | 样本不足/长尾 | 合并观察或低步长控制，不逐词过度操作 |

象限阈值必须由利润、CVR可靠性、购买周期和用户/企业规则生成；课程金额与点击数只能标 `case_default`。

## 3. 四类广告手术

1. `negative_or_exclusion`：最小粒度切除无关或已证实低效流量。
2. `bid_change`：改变获量能力，必须有经济上限、步长、观察与回滚。
3. `budget_or_placement`：先验证预算是真约束，并计算最大有效 Bid。
4. `add_effective_target`：有效投放过少时补新词/ASIN/广告类型，避免只靠砍。

非广告因素单列 `listing_price_inventory_fix`，不得伪装成广告动作。

## 4. 动作结构

```yaml
action_id: ACT-001
finding_id: F-001
priority: P0 | P1 | P2
action_type: ""
object: {}
current_value: null
recommended_value: null
value_source: business_derived | market_evidence | user_rule | case_default | null
evidence_refs: []
alternative_explanations: []
preconditions: []
protection_conditions: []
observe_until: ""
rollback_if: []
confidence: high | medium | low
requires_user_confirmation: true
human_decision: pending | approved | modified | rejected
```

无当前 Bid/预算/ID 时不得编造 `recommended_value`；可以给公式、范围或 `blocked_field`。

## 5. 自然流和 TACoS

- 广告销售、自然销售和总销售分栏。
- ACoS 降但 TACoS 升时，核查自然单、价格、销量、核心词和活动事件。
- “广告带动自然”只作相关性假设；用时间序列和可逆测试验证。
- 先处理与核心自然流关联弱的无效花费，避免误砍关键入口。

## 6. 变更上下文

每次分析把以下事件和广告数据放在同一窗口：价格、Coupon/促销、Listing/图片/A+、Rating/Review、库存/配送、竞品活动、节日/大促、平台异常。事件窗口未隔离时降低因果置信度。

## 7. 上一轮结果

每次先检查：批准了什么、实际执行了什么、执行时间、观察窗口是否结束、结果相对基线如何。未执行时不重复归因；窗口不足时标 `insufficient_data`；变差时触发回滚或规则修订。

## 8. 大促与 AMC/DSP 可选分支

大促按店铺→广告产品→位置→搜索词→ASIN→地区/受众→漏斗下钻。AMC/DSP 只有账户资格、受众状态/规模、基线、增量测量和停止条件齐全时才生成实验动作；案例效果不进入当前账户结论。
