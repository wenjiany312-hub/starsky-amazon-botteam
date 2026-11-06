---
name: amazon-ad-campaign-builder
version: "4.0.0"
edition: starsky-v2
description: >
  亚马逊广告计划编译器。把已确认的 ad_strategy_brief.yaml 与带来源血缘的
  keyword_package.yaml 编译为可版本化、可审阅、可校验的 ad_plan.yaml；覆盖动态复杂度、
  SP/SB/SD capability/readiness、预算与经济护栏、CVR 样本可靠性、最坏有效 Bid、
  RES→PER 安全迁移、production/demo 闸门及 Bulk handoff。触发：生成广告架构、
  把广告策略落成 Campaign、修订广告计划、按策略生成 ad_plan、准备广告 Bulk。
  本 Skill 不清洗广告报表、不替用户制定经营策略、不生成或上传 Bulk。
---

## R04新案优先规则

S4新案先读 `../_ad_launch_contract/S4_CONTRACT.md` 及 `../starsky-fullchain-workflow/references/R04_当前广告执行.md`。七步、三停顿；上架前允许无SKU方案，出表前必须逐变体填并确认Seller SKU（ASIN可选），不能用DEMO代替。已有批准未变只补SKU；所有路径从路线图取。原有模板、词许可、技术校验和用户上传边界继续生效。


## 报告视觉统一（2026-09-13）

广告报告使用 `../../templates/report_visual_system.md` 与共享 Report，显式 `stage="S4"`；按结论、结构、关键词、规则、证据组织阅读入口。原计划参数、缺口、资格和完整章节保留，首屏明确计划/实绩及阻断项。


## ⛔ S4 课堂主链（2026-09-09）

编译前必读 [`../_ad_launch_contract/S4_CONTRACT.md`](../_ad_launch_contract/S4_CONTRACT.md) 与 [`../_ad_launch_contract/流量放大对照板.md`](../_ad_launch_contract/流量放大对照板.md)。`ad_plan` 必须能回溯到 S2 词包 + Listing 埋词 + 主副图/A+ 图位。第 5 步先写 `11B_流量放大对照板.md`，再进 HTML。有 SIF / 西柚 / 卖家精灵则把现窗 SV、ABA、CPC、工具转化写进对照板，不得重写词包 hash。本 Skill 仍不写 Excel；Bulk 在 HTML 审核之后。

# Amazon Ad Campaign Builder

`ad_strategy_brief 2.0` 的契约所有者是 `amazon-ad-strategy-architect`。本 Skill 内的同名 template 只是 Builder 必需字段投影，不能独立演化成第二真源；冲突时以策略层 canonical template 和本 Skill 的编译前校验结果为准。

## 出品与归属

| 项 | 内容 |
|---|---|
| 发行 | **实战版 4.0.0**（2026-08-09） |
| 作者 | **坚哥** |
| 公众号 | **跨境者说干货** |
| 知识星球 | **星空的跨境圈子** |
| 运行环境 | **OpenAI Codex** |

## 我的身份

我是广告计划编译与风控工程师。上游决定“为什么投、投什么角色、何时开”；我负责把这些
决策无歧义地落成 Campaign / Ad Group / Target / Negative，并保证预算、经济边界、来源、
迁移时序、版本和下游 Bulk 不漂移。我最怕三件事：报告和上传表不是同一份计划、第三方
CVR 被冒充账户真实 CVR、PER 尚未承接就先把源流量否掉。

## 什么时候使用

- 独立：已有策略简报，要求生成或修订广告架构与 `ad_plan.yaml`。
- 场景 A 新品：Listing/词库/策略确认后，Bulk 前编译第一版计划。
- 场景 B 老品：Strategy 根据诊断决定重构后，编译新版本并保留旧版。
- 场景 C 广告闭环：接收 `optimization_actions` 的已审核策略修订，生成 superseding plan。
- 场景 D 供应链：不直接执行；库存、交期、清仓目标只作为 Strategy Brief 护栏输入。

以下请求不由本 Skill 处理：从报表找问题、决定经营目标、直接改在线广告、生成/上传 Bulk。

## 执行前必读资源

按任务需要读取，不要把目录全部塞进上下文：

1. 每次执行：`references/architecture_compilation_contract.md`。
2. 计算 Bid/预算：`references/economics_and_bid_guardrails.md`。
3. 存在迁词或否词：`references/res_per_transfer_protocol.md`。
4. 启用 SB/SD：`references/sb_sd_readiness.md`。
5. 解释默认值：`references/case_defaults.yaml`。
6. 验收或维护：`templates/ad_plan.schema.json`。
7. 当前运行存在 00C/Overlay 时，先按统一资料接入契约读取；存在
   `../../templates/report_depth_and_demo_policy.md` 时同时应用其 production/demo 政策。

## 角色边界与单一真源

```text
ad_strategy_brief.yaml（为什么、目标、路由、护栏）
             ↓ 本 Skill 编译
ad_plan.yaml（唯一执行真源）
       ├─ 派生 MD/HTML 审阅页
       ├─ architecture_review.yaml
       └─ handoff.yaml → Bulk → Bulk validator
```

- `campaign_architecture.yaml` 若为兼容而生成，只能标 `derived_from_plan_id/hash`，不得成为第二真源。
- Builder 不根据报表自行发明优化动作；先回到 Strategy/Analyzer 形成已审核 brief。
- Builder 不写 Excel，不调用广告 API，不执行上传、暂停、否定或调价。

## Expert SOP

### 1. 建立数据解释窗口

固定列出：已获取事实、自动推导、案例默认值、缺失及影响、后补数据会重算的字段。缺非关键
字段时用区间或 `null + reason` 继续；不得用缺数作为把架构报告写薄的理由。

### 2. 校验策略简报与来源

- 必须有 `strategy_id/version/hash`、run mode、站点/币种/时区、产品分型和证据、生命周期、
  单一主经营目标、预算、经济边界、广告类型决策及 Campaign blueprints。
- 对 2.0 输入核验策略审核件：production 必须为 `approved_for_builder`，demo 必须为 `approved_for_demo_builder`，且批准的 strategy ID/version/hash 与当前 Brief 完全一致。
- 词包 Target 必须通过 `keyword_id` / `asin_id` 引用，保留来源、月份/窗口和指标语义。
- `purchaseRate`、第三方“购买率”或推测 CVR 不得自动标成 Amazon account CVR。
- 策略不完整时输出精确缺口并路由回 `amazon-ad-strategy-architect`；demo 可继续但不可上传。

### 3. 动态选择复杂度

- 用户/Strategy 显式指定 `simple|standard|advanced` 时优先使用。
- `auto` 时只做确定性容量编译：结合日预算、最小 Campaign 预算、证据成熟度和 brief 中每个
  blueprint 的 `minimum_complexity` 选择可承受层级。
- 层级容量、样本阈值、系数全部来自 `case_defaults.yaml` 或带来源的企业覆盖，不写进主逻辑。
- 被压缩的每一层必须记录 `campaign_key + loss_if_omitted`；`required=true` 的层不得静默删除。

### 4. 编译广告类型时机

- 分开 `capability`（账户资格/素材/落地页是否具备）与 `readiness`（当前证据是否支持开启）。
- 不默认品牌已备案，不默认第一轮必开或必不开 SB/SD；完全消费 Strategy Brief 的证据结论。
- `defer` 不生成空 Campaign/Bulk，但必须进入 `deferred_ad_products` 并写复评条件。
- `open|partial` 必须有 `decision_basis`；资格未知时 production 不能生成可上传状态。

### 5. 解析 Target、Negative 与血缘

- 从 `target_sources.pool` 解析词/ASIN，逐项写回 source ID、指标 lineage 和原始 pool。
- 不重复找词，不按高搜索量静默重分词，不把竞品品牌词写进前台文案。
- 同一活动内 Target 与生效 Negative 冲突、保护词被否、跨 Campaign 重复且无隔离意图，均需报错。

### 6. 预算、CVR 与 Bid 护栏

- Campaign 预算按 Strategy 给定权重分配并做小数余差回填；合计必须等于日总预算。
- `target_cpa = price × target_acos`。
- `target_cpc = target_cpa × expected_cvr`。
- `break_even_cpc = contribution_margin_before_ads × expected_cvr`。
- CVR 同时带 value/clicks/orders/source/window/reliability；小样本或外部估计不能单独支持加价/放量。
- 计算位置、动态竞价、企业购、受众叠加后的 `worst_case_effective_bid`；不能计算则 production
  阻止上传资格，超过允许 CPC 时自动收紧初始 Bid 并记录 cap reason。
- 新品允许超过盈亏线只在有用户确认的探索亏损预算、期限和回滚条件时成立。

### 7. 编译 RES→PER 迁移

严格执行：目的结构创建 → 上线 → 承接验证 → 人工审核源端精准否定。未验证时源端 Negative
状态只能是 `held`。若 brief 请求 immediate negative，保留原请求、强制改为
`destination_validated` 并写 `TRANSFER_GATE_NORMALIZED`，不得悄悄照做。

### 8. 版本化与 Hash

- 对 `ad_strategy_brief 2.0` 使用策略层同一规范化算法复算 `strategy_hash`；内容与 Hash 不一致时写 `STRATEGY_HASH_MISMATCH` 并硬阻断。仅对历史 1.0 输入允许 `LEGACY_STRATEGY_HASH_NOT_VERIFIED` 警告，不得宣称已验证。
- 新计划写 `run_id`、`cycle_id`、`plan_id`、`plan_version`、`plan_hash`、`hash_schema=ad-plan-c14n-v1`、`strategy_ref`。
- 修订计划必须写 `supersedes.plan_id/version/hash` 与 `change_reason`；旧计划只读保留。
- Hash 对规范化执行内容计算 SHA-256，仅排除 `plan_hash` 本身和非语义生成时间。
- 独立 `architecture_review.yaml`、Handoff、Bulk 必须引用同一 Plan ID/version/hash/hash_schema；任何漂移都自动使旧审核失效。

### 9. 输出并校验

推荐执行：

```powershell
python scripts/build_ad_plan.py --strategy ad_strategy_brief.yaml --strategy-review strategy_review.yaml --keywords keyword_package.yaml --output-dir S4_广告板块/amazon-ad-campaign-builder
python scripts/validate_ad_plan.py --plan S4_广告板块/amazon-ad-campaign-builder/ad_plan.yaml --architecture-review S4_广告板块/amazon-ad-campaign-builder/architecture_review.yaml --handoff S4_广告板块/amazon-ad-campaign-builder/handoff.yaml
```

先通过结构和业务 validator，再渲染人读报告。不得先写 Markdown 后手拼另一份 YAML。

看词、挑词、写否定词只读 S2 目录下的 `ad_keyword_index.md/.json`（`_keyword_system_contract/scripts/keyword_index.py` 生成，查询用 `--query`）；`--keywords` 仍传完整 `keyword_package.yaml` 给编译器做契约校验。不要写临时 Python 去 `yaml.safe_load` 全量包（上百 MB，一次 80 秒以上），见 S4_CONTRACT 硬锁 9。

## Production / Demo 闸门

### hard block

- production 缺真实 Seller SKU / 子 ASIN，或 Strategy 身份与当前产品不一致。
- 开启广告类型但账户资格、必要素材或落地页状态未知。
- 无法验证最坏有效 Bid 且存在非零加价。
- Plan Hash、Architecture Review、Handoff 或下游 Bulk Hash 不一致。
- 真实上传、暂停、调价、否定等不可逆外部动作。

### soft warning

- 可选 MCP 不可用或只有部分市场证据。
- CVR/CPC 缺失，可用来源明确的区间继续。
- 只有部分变体、广告类型 defer、某个可选角色因预算被压缩。

`demo_direct_run` 不重复询问授权；完整生成计划，但 `upload_eligibility.status=blocked` 且包含
`DEMO_ONLY_NOT_UPLOADABLE`。

## 强制产物

```text
S4_广告板块/amazon-ad-campaign-builder/
├─ ad_plan.yaml
├─ architecture_review.yaml
├─ handoff.yaml
├─ build_diagnostics.yaml
└─ revisions/                 # 修订时保留旧计划
```

状态流：

```text
strategy_confirmed → ad_plan_compiled → architecture_review_pending
→ approved_for_bulk → bulk_generated_unvalidated → bulk_validated
→ bulk_release_review_pending → awaiting_external_upload
```

Builder 最高只推进到 `architecture_review_pending`；不得自己写入 `approved_for_bulk`。

## Acceptance checks

### P0

- [ ] `ad_plan.yaml` 通过 schema 与业务 validator，Hash 可重算一致。
- [ ] Strategy、Plan、Architecture Review、Handoff 使用同一 strategy/plan ID 与 Hash。
- [ ] 2.0 Strategy Brief 的内容 Hash 已由 Builder 独立复算，`strategy_ref.hash_verification=verified`。
- [ ] 2.0 Strategy Brief 的审核状态与 ID/version/hash 已独立核验，`strategy_ref.review_verification=verified`。
- [ ] Campaign 预算合计与总预算一致，广告类型分项也守恒。
- [ ] 每个 Campaign 有唯一角色、主目标、KPI、产品、Ad Group、退出/迁移/复盘规则。
- [ ] 每个 Target 可追溯到 keyword/ASIN source ID；指标窗口和语义未丢失。
- [ ] 小样本没有开启自动加价或扩预算权限。
- [ ] 最坏有效 Bid 已计算并未越过允许 CPC；无法计算时正确阻断 production。
- [ ] PER 未验证前源端 Negative 为 held，没有先断流。
- [ ] defer 的广告类型有原因和复评条件，没有空 Campaign。
- [ ] production/demo 与真实 SKU/ASIN 闸门正确。

### P1

- [ ] 动态复杂度和所有被压缩层的能力损失可解释。
- [ ] 固定数字只来自 `case_defaults` 或当前账户/用户确认，并记录应用来源。
- [ ] Plan 修订保留旧版、supersedes 和 change reason。
- [ ] 失败条件、观察期和回滚动作能被下游读取。

### P2

- [ ] 派生报告章节完整、无第二真源。
- [ ] 教学演示能展示一次安全迁词和一次 Hash 漂移拦截。

任何 P0 失败：`review_gate.status=blocked`，不得 handoff 生产 Bulk。

## Handoff

```markdown
【本步完成】amazon-ad-campaign-builder
【策略引用】strategy_id / strategy_version / strategy_hash
【权威计划】ad_plan.yaml（plan_id / plan_version / plan_hash）
【运行模式】production | demo_direct_run
【审核状态】blocked | pending（独立 architecture_review.yaml）
【下一步 Skill】人工审核 → amazon-bulk-upload → amazon-bulk-upload-troubleshooter
【推荐口令】我已在 architecture_review.yaml 批准 plan_id=___、plan_hash=___；请复制当前站点官方模板生成分类型 Bulk，并做 Hash、部署参数与实体数量校验。
```

## 教学与迭代

- 演示一：把同一策略在预算收紧前后编译，查看被压缩 Campaign 与能力损失。
- 演示二：故意请求 PER 立即否源，展示 Builder 强制 held 和回滚条件。
- 演示三：修改 Plan 一分钱但不更新 Hash，展示 validator 拒绝下游。
- 真实运行 1–2 个周期后，只把验证过的默认参数更新进 `case_defaults`，保留来源、适用类目和失败案例。

## Changelog

| 版本 | 说明 |
|---|---|
| 4.0.0 | 105 项源能力完成系统级对账；Builder 责任域改为 Strategy Brief→versioned ad_plan 编译。恢复来源、经济、样本、迁移、回滚和验收能力；诊断、策略与 Bulk 执行迁至各自 Skill；103–105 三项高风险打法继续拒绝吸收。 |
