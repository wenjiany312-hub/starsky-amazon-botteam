---
name: amazon-seasonal-selection
version: "1.1.0"
edition: starsky-v2
description: >
  亚马逊季节性与节日选品。当用户问现在该备什么节日的货、万圣节/圣诞/情人节做什么、
  Q4 档期、前瞻选品、偏季品、去年同月回看、季节备货时间线时使用。输出节日×场景×
  产品方向表、提前 2~3 个月倒排、以及「旺季好卖且淡季不掉」判定。不要用近期上架筛选。
  装有 starsky-amazon-agent 时作为 S1 可选方法层，产物写 S1_选品板块/amazon-seasonal-selection/，
  偏季品判定回填 S1 报告 M11。不要在用户只要算竞争难度或只要写旺季广告计划时使用。
metadata:
  author: 基于 2025-07 选品实战班蒸馏（课60/63/67/70/71、评审30/47）
  created: 2026-09-03
  last_reviewed: 2026-09-03
  review_interval_days: 90
---

# 季节选品

你是档期规划员。节日日历是选品 + 营销 + 关键词三合一底表，不是灵感清单。

大表来源：装有星空插件时读 `<home>/templates/seasonal_calendar.yaml`；否则读 `references/seasonal-calendar.md`。阈值同理（`s1_thresholds.yaml` / `references/thresholds.md`）。大表里 1~5 月是课程实录，6~12 月是同构补全（每行有 `source` 标注），用前必须用去年同月数据验证。

## 数据来源（三级，逐级回退，写实）

| 级 | 来源 | 取什么 |
|---|---|---|
| 1 | 市场 MCP：Sorftime（类目月度趋势 / 去年同月榜单）；可加装西柚（季节性指数）、卖家精灵（月份选择器回看） | 先真实调用一次 |
| 2 | 用户导出：卖家精灵「选产品」按去年月份导出的表 | 贴数即验 |
| 3 | 只给档期方向 + 取数路径 | 标 `未验证`，不写成已验证机会 |

**空月份 ≠ 非季节**：接口返回空时回退核验月度趋势，不得输出「非季节品」。

## 工作流

1. 锁定「今天」和站点。
2. 打开大表，看往后 **2~3 个月** 的节日（常规备货）。强节日品（如 3/17 圣帕特里克）可提前约 **7 个月**。
3. 每个节日拆：场景 → 产品方向。场景不确定就现场用推理扩展，并标明「待亚马逊验证」。
4. 用去年同月数据验证，不看今年刚上架的季节噪点。
5. 做偏季品判定，再决定精铺做不做。
6. 倒排：选品 → 1688 交期 → 头程 → 入仓 → 开售。

## 去年同月回看（课60 / 课70）

卖家精灵 → 大数据选品 → 选产品 → **月份选择器**调到去年对应月。
例：7 月当下查去年 10~11 月，选万圣节/感恩节/圣诞方向，8~9 月发货。

不要用「近几月上架」当主筛选（课39）。

## 偏季品判定（课63）

最优季节品 = 高需求月卖得好 **且** 低需求月不掉太多。

| 类型 | 特征 | 决策 |
|---|---|---|
| 偏季品 | 旺季好、淡季还能卖 | 优先，不压库存 |
| 纯季节品 | 淡季归零 | 必须卡死时间线，精铺默认谨慎 |
| 已过窗口 | 节日导致的短期热卖 | 来不及就放弃 |

## 关键词三合一（课47）

每个方向至少给 3 类词：

- 节日词：`st patricks day necklace`
- 礼品词：`father's day gift`
- 场景词：`graduation party decorations`

侵权提醒：角色周边（马里奥等）、大牌平替不要进候选。

## 输出

```markdown
# 季节选品档期

## 当前时点与备货窗口
## 未来 2~3 个月档期表
| 月份 | 节日 | 场景 | 产品方向 | 去年验证 | 侵权风险 | 偏季？ | 来源 |
## 推荐 3~5 个方向（含倒排时间线）
## 明确不做
## 下一步
交给难度评估 / 市场穿透，不要在这里终裁立项。
```

## 接入星空亚马逊 Agent · S1 选品板块（编排契约）

装有 `starsky-amazon-agent` 时，本 Skill 是 S1 的**可选档期方法层**（级别同 `amazon-product-development-prd`），不是独立入口。

**什么时候被调用**
- 入口日常口令「现在该备什么节日的货 / Q4 档期 / 万圣节做什么 / 季节选品」→ 经 `starsky-singlepoint-intake` 单点路由到本 Skill；
- 快速选品卡用户在模板里写了节日 / 季节方向（D 自有方向含节日词）时，condition_select 前先调本 Skill出档期候选；
- `amazon-market-research` 写 M11「季节性与最晚下单日」需要偏季品判定与去年同月依据时调用。

**干什么、干到哪为止**
1. 出未来 2–3 个月档期表 + 3–5 个方向 + 倒排时间线；每行带 `source`（course67 / extrapolated）与「去年验证」状态。
2. **【等你】用户选定方向后停**，方向进 condition_select → `amazon-market-research`。本 Skill 不出「可以做」终裁。
3. 偏季品判定与去年同月依据回填 S1 综合报告 M11（加一行「偏季品判定：旺季好 / 淡季不掉 / 纯季节」）；**最晚下单日仍由插件按交期计算**，本 Skill 不重算。
4. 侵权线索只作提醒，终裁归 `amazon-ip-risk-assessment`。

**产物落盘**（`templates/skill_folder_map.yaml`）
- `{产品根}/S1_选品板块/amazon-seasonal-selection/SEASON_季节档期候选.md`
- `{产品根}/S1_选品板块/amazon-seasonal-selection/seasonal_candidates.yaml`（月份 / 节日 / 方向 / 偏季判定 / 去年验证 / 来源）

**Handoff 四标签**
```markdown
【本步完成】amazon-seasonal-selection
【下一步 Skill】amazon-market-research（或 amazon-trend-signal-scanner 先扫候选）
【推荐口令】继续。用我选的档期方向进 condition_select，并把偏季品判定带进 M11。
【MCP】used: <实际调用> / skipped: <原因> / not_required
```

未装插件时按本文独立运行。
