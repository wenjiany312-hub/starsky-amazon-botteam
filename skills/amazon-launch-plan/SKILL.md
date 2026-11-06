---
name: amazon-launch-plan
version: "1.0.0"
description: >
  Amazon 新品 Launch Plan 深水 Skill。基于售价、成本、FBA、CPC、CVR、库存和交期，生成参数化上市节奏、
  销售/广告/现金流/库存/补货计划及计划-实际-偏差-修正回路。触发：上市计划、Launch Plan、推新节奏、补货现金流计划。
edition: starsky-v2
author: 坚哥
brand:
  ecosystem: codex
---

# Amazon Launch Plan

## 统一资料接入约束（00C，必读）

开始本 Skill 前，若当前产品 run 存在 00C 资料接入报告或 Overlay，必须先读它们，应用其中的可用来源、事实边界、合规约束和 MCP 补数计划；再读取本任务产物并执行。用户已提供未整理资料但没有 00C 时，先执行资料接入，不要求用户整理。读取 ../../templates/data_intake_read_contract.md 作为权威规则。

## 定位

本 Skill 是 S1 后、广告前后均可调用的深水计划层，不改变新品现场主链。它把业务假设变成可观察的周/日计划；不把固定 56 天当平台规律，也不伪造销量、CPC 或 CVR。

## 输入

- `01_product_profile.yaml`、S1 可行性结论、售价与成本结构；
- `keyword_package.yaml` 的 CPC/意图信号；
- `ad_plan.yaml`（若已生成）；
- MOQ、在途、可售库存、生产/头程/入仓周期；
- 退货率、促销、review 合规策略；
- 可选 PRD、企业/类目历史基线。

缺数时给低/中/高情景并标 `AI推断`。任何假设必须有 owner、校验日期和替换入口。

## 周期与增长曲线

周期由备货、入仓、学习期、转化积累和现金流决定，可为 21/30/45/56/90 天或自定义。至少比较：

- J 曲线：早期广告/折扣投入高，后段自然单释放；
- 线性：供应和需求稳定，按固定增量推进；
- S 曲线：前期学习、中期加速、后期趋稳。

选择曲线必须解释数据依据和失败信号，不能只画好看的增长线。

## 核心模型

```text
unit_contribution_before_ads = price - landed_cost - fba_fee - referral_fee - returns_reserve
break_even_acos = unit_contribution_before_ads / price
expected_orders = sessions × cvr
ad_sales = ad_clicks × cvr × price
ad_spend = ad_clicks × cpc
ending_inventory = beginning_inventory + receipts - units_sold - removals
reorder_point = daily_run_rate × total_lead_time + safety_stock
cash_balance = opening_cash + collections - procurement - freight - ad_spend - fees - refunds
```

报告必须实际代入至少一个情景，并对价格、CPC、CVR、退货率、交期做敏感性。

## 固定输出结构

1. 执行摘要与决策门；2. 输入事实/假设；3. 三条曲线对比；4. 选定周期与阶段目标；5. 日/周销售计划；6. 广告预算/ACOS/CPC/CVR；7. 价格与促销；8. 现金流；9. 库存和补货；10. Review/VOC 合规收集；11. Listing/视觉迭代；12. 风险和停止线；13. 计划-实际-偏差-修正表；14. 数据回填节奏。

每个阶段写目标、动作、责任人、预算、指标、领先信号、滞后信号、保护条件和复盘日期。

## 输出

- `S4_广告板块/amazon-launch-plan/Launch_Plan.md`
- `S4_广告板块/amazon-launch-plan/launch_plan.yaml`
- `S4_广告板块/amazon-launch-plan/Launch_计划实际偏差表.md`

HTML 如需生成，只能调用 `github-tabler-html-report` + `salon_report.py`。

## acceptance_checks

- [ ] 周期参数化，未把 56 天硬编码成唯一答案。
- [ ] J/线性/S 曲线有适用条件和失败信号。
- [ ] 价格、成本、FBA、CPC、CVR、库存、退货率均有来源/假设。
- [ ] 销售、广告、现金流、库存和补货彼此守恒。
- [ ] 有计划/实际/偏差/修正闭环和 3/7/14/自定义复盘窗。
- [ ] 未直接执行后台动作。

## Handoff

回 `starsky-fullchain-workflow` 或上线后的 `amazon-store-patrol-orchestrator`；实际数据优先覆盖计划假设，并按影响范围修订广告、库存或 Listing，不整份推倒重写。
