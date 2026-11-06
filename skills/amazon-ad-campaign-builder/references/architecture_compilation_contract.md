# 广告策略编译契约

## 1. 职责

Builder 是执行计划编译器，不是策略生成器。它可以做确定性的筛选、换算、守恒、冲突检测和
版本化；不能自行改变经营目标、发明类目结论、重新解释报表或执行在线广告动作。

| 输入结论 | 所有者 | Builder 行为 |
|---|---|---|
| 品类/产品/生命周期 | Strategy Architect | 原样引用并保留 evidence |
| Campaign 角色与主目标 | Strategy Architect | 编译为 Campaign / Ad Group |
| 关键词/ASIN 候选 | Keyword Skill | 按 ID 解析并保留 lineage |
| 预算与目标 ACoS | 用户 + Strategy | 做守恒和经济预检 |
| 报表诊断/优化优先级 | Analyzer + Strategy | 仅消费已审核 revision brief |
| Bulk 字段 | Bulk Skill | Builder 只提供 plan，不写模板 |

## 2. 输入优先级

1. 当前产品直接事实及用户本轮确认。
2. 平台/账户资格、合规和生产标识。
3. 当前 Strategy Brief 及其引用的 Amazon 报表。
4. 当前词包中来源明确的第三方市场信号。
5. 企业历史资料。
6. 标注的 `case_default`。

同字段冲突必须进入 `source_conflicts`，不得静默选值。当前账户 CPC/CVR 与第三方市场指标是
不同语义，可同时保留，不能覆盖成一个字段。

## 3. 动态复杂度编译

复杂度来自 brief 的显式选择，或用预算容量和证据成熟度机械选择。所有数值阈值在
`case_defaults.yaml`；主逻辑只认识 `simple < standard < advanced`。

每个 Campaign blueprint 必须包含：

```yaml
campaign_key: SP-RES-AUTO
minimum_complexity: simple
required: true
loss_if_omitted: "失去自动探索入口"
ad_product: SP
role: res
primary_goal: discovery
daily_budget_weight: 1
```

若预算只能承受 simple：

- `minimum_complexity=standard|advanced` 的可选层进入 `compression_log`。
- `required=true` 的层不能被删除，计划转 `blocked`。
- 不能只写“压缩为 4 个 Campaign”；必须列出删了什么、损失什么。

## 4. Campaign 编译要求

每条 Campaign 至少包含：

- `campaign_key/name/ad_product/role/primary_goal/kpi`。
- `advertised_skus/daily_budget/bidding_strategy/placement_adjustments`。
- 至少一个 Ad Group；每个 Ad Group 有 purpose、targets、negatives。
- success、stop/reduce、graduation、rollback、review cadence。
- 预算、Bid、CVR 和 Target 的来源。

一个 Campaign 只有一个主目标。需要排名与利润两个目标时拆 Campaign，或由 Strategy 明确主次；
Builder 不把两个 KPI 拼成一个模糊目标。

## 5. Plan 修订

修订不是覆盖：

```yaml
plan_id: stable-per-product-line
plan_version: 3
supersedes:
  plan_id: stable-per-product-line
  plan_version: 2
  plan_hash: sha256:...
change_reason: "PER 承接验证通过，进入源端否定审核"
```

Plan ID 在同一产品计划链保持稳定；版本递增。若改变站点、商品集合或经营目标到已经不是同一
执行计划，应创建新 Plan ID，并在 decision log 说明关系。

## 6. 输出一致性

- `ad_plan.yaml` 是唯一执行真源。
- MD/HTML、独立 architecture review、handoff、Bulk 都引用其 ID、version、hash、hash_schema。
- Campaign/Ad Group/Target/Negative 数量由 plan 计算，不能在人读报告中另写一套。
- Builder 输出 `pending`，不把待审核等同 `approved_for_bulk`；批准只能来自独立审核件。
