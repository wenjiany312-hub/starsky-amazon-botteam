---
name: amazon-store-patrol-orchestrator
version: "1.1.0"
description: >
  Amazon 上线后店铺巡检编排（S5: 运营板块的主 Skill）。连接 Amazon Ads API、领星 OpenAPI 或用户导出报表，执行日/周/月三层巡检， <!--flagship-->
  Amazon 上线后店铺巡检编排（S5: 运营板块的主 Skill）。连接领星 OpenAPI 或用户导出报表，执行日/周/月三层巡检， <!--basic-->
  形成 snapshot/finding/reason/action/result/rule_feedback 闭环。只诊断、排序和生成待审核动作，不直接修改后台。
  用户说「店铺巡检 / 今日巡检 / 日检 / 周检 / 月检 / 每日运营体检 / FBA 预警 / 广告健康周检 / 经营月复盘」时，
  先由入口 starsky-amazon-agent 按《日常口令直达》进 S5 并静默建夹，再调用本 Skill 的对应巡检层；本 Skill 不自己建夹、不出选择卡。
  「复盘某某产品广告 / 文案缺口」不是本 Skill，走 `_s5_ops_contract` 复盘轨。
edition: starsky-v2
author: 坚哥
brand:
  ecosystem: codex
---

# Amazon Store Patrol Orchestrator

## 统一资料接入约束（00C，必读）

开始本 Skill 前，若当前产品 run 存在 00C 资料接入报告或 Overlay，必须先读它们，应用其中的可用来源、事实边界、合规约束和 MCP 补数计划；再读取本任务产物并执行。用户已提供未整理资料但没有 00C 时，先执行资料接入，不要求用户整理。读取 ../../templates/data_intake_read_contract.md 作为权威规则。

## 场景边界

这是上线后的独立 Ops 深水链，不插入新品沙龙现场主链。它可消费 Launch Plan、广告计划和真实店铺数据，但不得因为巡检能力存在而拉长选品→上架流程。

本 Skill 只做只读取数、诊断、建议和复盘；删除广告、改 Bid/预算、创建 Campaign、补货下单等动作必须由用户审核后交相应 Skill 或人工执行。

已配置 S5 本地终端时，调用 `amazon-ops-console` 统一执行 Ads + SP-API 分域采集、巡检快照、审核账本与候选导出。每日任务按北京时间 09:00 运行，但日报使用各站已结束的当地自然日，并把今日实时快照与 7 天广告归因分开。无类目规则或经济卡时只列缺口；首版不执行后台动作。 <!--flagship-->

## 数据连接器（第 1 步先印状态，再决定能不能巡检）

先跑 `skills/_s5_ops_contract/scripts/s5_ingress.py`。契约：`../_s5_ops_contract/S5_CONTRACT.md`。控制器：`scripts/workflow_flow.py --stage S5`。

`s5_ingress.py` 目前是静态接入检查，只能给 `needs_verification`，不能证明本轮业务接口可调用。广告账户事实三选一并沿用用户选择：Amazon Ads API、领星、文件投喂；不得静默切源或混源。 <!--flagship-->
`s5_ingress.py` 目前是静态接入检查，只能给 `needs_verification`，不能证明本轮业务接口可调用。广告账户事实二选一并沿用用户选择：领星、文件投喂；不得静默切源或混源。 <!--basic-->

1. **Amazon Ads API**（官方广告路线）：调用 `amazon-ads-api-connector`，按 `授权 → profiles → SP 报表 → 分析 → 建议` 推进。首版只支持 SP；profile、region、currency、timezone、日期、归因窗口和 campaign scope 必须明确。SB/SD 写 `unsupported`。同一 `collect` 命令与同一输出目录可续拉异步任务。 <!--flagship-->
2. **领星**：本机 `~/.starsky-codex/config/lingxing_config.yaml` 的 `app_id` 非空。全店采集默认写入 `STARSKY_OPS_ROOT`（未设则为 `~/.starsky-codex/ops_workspace`）：
   ```powershell
   $env:STARSKY_OPS_ROOT = "$env:USERPROFILE\.starsky-codex\ops_workspace"
   python "<home>/skills/lingxing-erp-connector/scripts/daily_patrol_data.py" --marketplace <US|UK>
   ```
   只读取配置存在性与连接状态，不展示、复制或写入密钥。
3. **投喂夹**（降级）：优先 `ops_workspace/_投喂_巡检报表/`，兼容产品夹 `S5_运营板块/_投喂_巡检报表/`。只有投喂、没有接口时，卡片必须写「无实时接口，结论仅覆盖你丢进来的文件」。
4. **Amazon SP-API**（独立业务域，可选）：本机 `sp_api_config.yaml` 只需 LWA，用于订单、库存和 Listing。通道成功不代表 Ads API 可用。 <!--flagship-->
5. **广告知识库**（建议）：`ads_knowledge_base`。诊断抽短症状再 `kb_diagnose`。它不改广告。
6. **产品推广知识库**（建议，断货/掉位/被超必调）：`promotion_knowledge_base`。先 `kb_status`，掉量掉位抽短症状再 `kb_diagnose`，整品节奏用 `kb_scenario_rules(产品类型, 生命周期, 推广场景)`，手册用 `kb_playbook`。只给原因树与D0/D3/D7节奏，不改后台。

Ads API、领星和广告文件都没有 → 广告复盘停在第 1 步。连接失败不代表店铺异常；记录 `data_gap`。不得编造实时数据。不得把 7 次点击 0 单写成纯浪费或暂停令。 <!--flagship-->
领星和广告文件都没有 → 广告复盘停在第 1 步。连接失败不代表店铺异常；记录 `data_gap`。不得编造实时数据。不得把 7 次点击 0 单写成纯浪费或暂停令。 <!--basic-->

## 记住范围：ops_state.yaml

巡检范围写在 `S5_运营板块/amazon-ops-workflow/ops_state.yaml` 的 `patrol_scope`（站点、店铺、ASIN/SKU 范围、数据源、上次巡检日期、上次报告路径；模板 `templates/ops_state.example.yaml`）。

- 文件存在 → 直接沿用，首屏第一行只写一句「沿用上次范围：US · 店铺 X · 12 个 ASIN（要改直接说）」，⛔ 不再问站点、店铺、ASIN。
- 用户说「换成 UK」「只看这 3 个 ASIN」→ 改 `patrol_scope` 后继续，下次自动沿用。
- 每次巡检结束更新 `last_run_date`、`last_report`，并把本轮 `action` 挂到 `pending_actions`，下一轮先核验它们有没有执行（见「原因与动作规范」）。

## 飞书推送（可选，默认不推）

`lingxing_config.yaml` 顶层 `feishu.webhook` 已配置，且 `patrol_scope.push_feishu: true` 或用户本轮说「推飞书」时，巡检报告落盘后执行：
```powershell
python "<home>/skills/lingxing-erp-connector/scripts/feishu_push.py" --marketplace <US|UK>
```
推的是当天 findings 卡片，不推原始数据。没配 webhook 就跳过，不提示、不阻断。

## 资料包与首次快照

若当前 run 存在 `00_总控/starsky-fullchain-workflow/runtime_data_overlay.yaml`，先读取 Ops 的 `store_baseline`、`advertising`、`listing` 与 `shared_rule` 条目。它们只能作为历史比较基线、命名/SOP 参考和异常阈值线索；真实店铺/广告/库存数据优先，不能用历史数替代今日数据。

没有资料包或没有历史基线时，直接创建 `S5_运营板块/_阶段总览/Ops_首次店铺快照.md` 并标注 `baseline_status: first_snapshot`，不把缺基线当作阻断。资料包源目录只读，巡检产物写入当前产品根的 `S5_运营板块/amazon-store-patrol-orchestrator/`。

## 统一巡检契约

运行前读取 `../../templates/store_patrol_contract.schema.yaml`。每个 finding 固定包含：

`snapshot → finding → reason → action → result → rule_feedback`

并附 scope、source_id、severity、confidence、owner、due_date、approval_status、observation_window、rollback_condition。

## 三层巡检

### 每日巡检

⛔ 零叙述：读契约、读 00C、探测领星或报表这些准备动作不打印；第一条回复要么是下面的首屏，要么是缺数据时的那一句追问（「把领星导出/后台报表发我，或回『先建首次快照』」）。

首屏给三个刺眼数字：昨日销售/订单变化、可售库存风险、广告花费或 ACOS 异常。随后检查销量脉搏、断货/库龄、Buy Box/在售状态、重大报错、预算跑空和红线告警。

### 每周巡检

检查 ASIN/SKU 自然与广告流量、Campaign→Ad Group→Target→Search Term 四层、预算利用率、CPC/CVR/CPA/ACOS、无效花费、出单词迁移、Listing 转化和退货/VOC。广告分析调用 `amazon-ad-report-analyzer`，变更回 `amazon-ad-campaign-builder`/Bulk。

### 每月巡检

检查产品线利润、现金流、库存周转、补货准确性、价格/促销、退货原因、内容迭代、Launch Plan 偏差和规则有效性。

### 成本表维护（2026-09-14：精细化运营地基）

分 SKU 成本表（`成本表_cost_table.yaml`，版本化，复核人+日期必填）：售价/采购到仓/头程分摊/FBA/佣金率/目标ACOS；巡检只计算毛利/毛利率/盈亏平衡ACOS/广告日上限，不手填推导值。回填两种：SKU 少聊天逐项问，多发 Excel 回填单（表头同上）放回 run 根。缺项 null 不编数，无成本 SKU 上限判 `unknown` 只报绝对花费；目标 ACOS 必须用户给。每次巡检引用成本表版本号，ACOS > 目标×1.5 直接预警。

## 原因与动作规范

一个异常至少列主因和一个替代解释，区分“数据已证实/运营推断/待验证”。动作必须写对象、幅度、保护条件、预期指标、观察窗、失败/回滚条件；禁止“优化广告”“提高转化”这类不可执行句子。

每轮先核验上一轮动作是否执行，再比较 3/7/14 天或业务自定义窗口。未执行不能评价动作效果；观察不足不能写成长期结论。

## 输出

- `S5_运营板块/amazon-store-patrol-orchestrator/巡检/{date}_店铺巡检报告.md`
- `S5_运营板块/amazon-store-patrol-orchestrator/巡检/{date}_patrol_snapshot.yaml`
- `S5_运营板块/amazon-store-patrol-orchestrator/巡检/{date}_待审核动作清单.yaml`
- `S5_运营板块/github-tabler-html-report/HTML/{date}_店铺巡检报告.html`（仅 `github-tabler-html-report` + `salon_report.py`）

## acceptance_checks

- [ ] 数据源、profile/店铺、region/站点、币种、时区、日期、归因窗口和 campaign/ASIN/SKU scope 明确。
- [ ] 自然与广告流量分开，事实/推断/建议分开。
- [ ] finding 可复算，reason 有替代解释，action 有保护/回滚条件。
- [ ] 核验上一轮 action 与 3/7/14 天 result。
- [ ] 凭据未写入报告/Skill/日志。
- [ ] 所有后台动作均为 pending approval，未自动执行。
- [ ] 若使用资料包，报告标明 source_id、时间窗、与本轮真实数据的差异及使用边界。

## Handoff

广告问题 → `amazon-ad-report-analyzer`；架构/出价 → `amazon-ad-campaign-builder`；改表 → `amazon-bulk-upload-troubleshooter`；Listing → `amazon-listing-copywriter`；库存/补货保留待审核动作，由用户选择企业系统或人工执行。
