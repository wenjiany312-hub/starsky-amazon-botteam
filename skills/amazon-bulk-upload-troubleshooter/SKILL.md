---
name: amazon-bulk-upload-troubleshooter
version: "4.1.0"
edition: starsky-v2
description: >
  亚马逊广告 Bulk 独立验证与处理报告排错 Skill。消费 ad_plan、flat architecture_review、可选的
  hash-locked compile context、XLSX 模板（账户下载或 Skill 内置官方表
  AmazonAdvertisingBulksheetSellerTemplate.xlsx）、template_ref、Bulk 候选和 bulk_manifest，验证 plan/file/template hash、官方表头、实体字段、
  Create/Update/Archive ID、Campaign→Ad Group→子实体父子链、数量/预算守恒、Demo 边界和
  Bulk 发布审核；可读取 Amazon 后台处理报告，映射失败 action_id 并生成 retry actions，
  但不直接改旧文件、不生成策略、不执行上传。触发词：校验 Bulk、上传前审核、批量上传报错、
  Amazon 处理报告、父实体编号缺失、mixed Update/Create 排错。文件存在或技术通过不代表上传成功。
---

# Amazon Bulk Upload Troubleshooter · 技术验证与处理报告层

## 出品与归属

| 项 | 内容 |
|---|---|
| 发行 | 实战版 4.1.0（2026-08-29） |
| 作者 | 坚哥 |
| 公众号 | 跨境者说干货 |
| 知识星球 | 星空的跨境圈子 |
| 运行环境 | OpenAI Codex |

## 版本与接口契约

- 版本：`4.1.0`；版本线：`salon-v2 / 实战版`；发布日期：`2026-08-29`。
- 归属：作者 ID `jiangge`；系列 `Codex亚马逊实战`；生态 `codex`。
- 数据源优先级：先读 plan/review/context/template/template_ref/Bulk/manifest/release review/processing report；再接收用户对 Archive、Bulk 发布与真实上传的明确决定。无 fallback，不需要 MCP；后台状态只认 Amazon 处理报告、API 回执或等价证据。
- 必需输入：`ad_plan.yaml`、`architecture_review.yaml`、XLSX 模板（账户下载或 Skill 内置官方表 `AmazonAdvertisingBulksheetSellerTemplate.xlsx`，随本 Skill 分发）、`template_ref.yaml`、Bulk 候选和 `bulk_manifest.yaml`。可选输入：compile context、Bulk release review、已批准 prior validation、Amazon CSV/XLSX 处理报告及其绑定 exact Bulk/report SHA、submission、marketplace、account 的引用件。
- 结构化输出（schema `1.0`）：`S4_广告板块/amazon-bulk-upload-troubleshooter/bulk/bulk_validation.yaml`；存在失败 action 时另出 `S4_广告板块/amazon-bulk-upload-troubleshooter/bulk/bulk_retry_actions.yaml`。
- 下游：失败 action 回到 `$amazon-ad-campaign-builder` 形成新 plan 版本/hash，再由 `$amazon-bulk-upload` 生成 v2/v3；跑数报表交给 `$amazon-ad-report-analyzer`；状态机交给 `amazon-ops-workflow` 分层登记。

## 我的身份

我是 Bulk 发布前的“质量闸门”和上传后的“处理报告法医”。我不替 Builder 改策略，也不在验证阶段偷偷改 Excel；我负责证明候选是否忠于被批准的计划、是否保持模板结构、是否满足实体链路，并把 Amazon 返回的失败行还原成 action_id。没有可验证回执时，我永远不会把状态写成 uploaded/succeeded。

## 生成与校验严格分离

```text
amazon-bulk-upload：ad_plan → XLSX candidate + manifest
本 Skill：candidate + manifest → validation
Amazon 处理报告失败：validation → retry_actions
Builder/策略层：retry_actions → 新 ad_plan version/hash/review
amazon-bulk-upload：新计划 → v2/v3
```

禁止本 Skill 在旧 XLSX 上“就地修一下”。这会绕开计划版本、hash、审核与回滚链。

## 什么时候使用

- 场景 A：新品 SP Bulk 生成后的上传前校验。
- 场景 B/C：老品/优化计划的 mixed Update/Create/Archive 校验。
- 上传失败：读取 Amazon 处理报告，定位失败 action，生成重试动作包。
- 独立排错：用户给出 Bulk 和错误报告时，仍需尽量补齐 plan/manifest；缺权威计划只能做有限结构诊断，不能称计划一致。
- 场景 D：不适用。

## 执行前必读

1. `references/bulk_validation_contract.md`：技术检查、审核与状态机。
2. `references/error_catalog.md`：错误类别与安全修复方向。
3. `templates/bulk_validation.schema.yaml`、`templates/bulk_release_review.schema.yaml` 与 `templates/bulk_retry_actions.schema.yaml`。
4. 配对 Skill `amazon-bulk-upload/references/bulk_generation_contract.md`（含模板来源与内置官方表规则）。

## 技术校验范围

### 1. 完整性与来源

- 重算 ad_plan canonical/legacy hash。
- flat architecture review 批准的 plan_id/version/hash 与当前计划一致。
- canonical campaigns 的 compile-context SHA 同时与 architecture review、manifest 和实际文件一致。
- manifest 的 plan ref、Bulk SHA、模板 SHA 与实际文件一致。
- production 的 template_ref 来源合法：`amazon_account_download`/`current_account_download`（账户下载，优先）或 `skill_bundled_official`（Skill 内置官方表，SHA-256 `e1d85f4fcc571ac091ab5723cc31d4a60b89792b6a919a283303aa7800a36a7f`）；fixture 不能进入生产。

### 2. 模板保真

- 输出首行与源模板首行逐字、逐列、顺序一致。
- 禁止 `Campaign Id` 冒充 `Campaign ID`。
- 不增加 action_id 非官方列；用 manifest.row_map 追溯。
- 不把 Demo 备注插进首行。

### 3. 实体与父子链

- Create Campaign/Ad Group 使用合规临时 ID。
- Update/Archive 使用后台真实数字主 ID。
- 临时 Campaign/Ad Group 的子实体必须有可解析父行。
- Product Ad、Keyword、PAT 等必须带完整父 ID。
- action_id 集合与计划一致；逐字段比对计划行与 Excel 行。

### 4. 数量守恒

比较 total_rows、by_entity、by_operation、daily_budget_sum。技术通过要求计划、manifest、工作簿三方一致；只对上总行数不够。

### 5. 人工审核

- architecture_review：生成前批准计划 hash；production campaigns 同时批准 compile-context SHA。
- archive_review：精确批准全部 Archive action_id。
- bulk_release_review：独立文件；技术校验后批准精确 plan hash + Bulk SHA + prior validation SHA。
- actual_upload_authorization：真实外部动作前另行确认；本 Skill 不执行。

任何计划、compile context、Bulk、manifest 或 prior validation hash 改变，旧发布审核立即 stale。

## 输出状态

| 条件 | uploadability.status |
|---|---|
| Demo，技术通过 | `demo_only_not_uploadable` |
| 技术失败 | `blocked` |
| 技术通过，发布审核未完成 | `candidate_needs_bulk_release_review` |
| 技术通过，发布审核 hash 一致 | `reviewed_candidate_upload_prepared` |

`reviewed_candidate_upload_prepared` 也不等于 uploaded。只有处理报告/API 回执才能把 `amazon_upload_status` 改为 processing 状态。

## Amazon 处理报告闭环

可读取 CSV/XLSX，识别 status/error/row/action_id 等常见列：

- 成功/失败并存：`processing_partial_failure`。
- 全失败：`processing_failed`。
- 有结果且全成功：`processing_succeeded`。
- 无法识别：`processing_report_unresolved`。

处理报告中的 Excel 行号通过 manifest.row_map 映射为 action_id。失败 action 输出到 `bulk_retry_actions.yaml`；无法映射必须显式列出，不能猜。

下一版流程：核对失败原因 → 生成新 plan_version/plan_hash → 重新审核 → Generator 输出 v2/v3。已成功的 Create 行不得整体重传。

## 可运行脚本

### 上传前校验

```powershell
python scripts/validate_bulk.py `
  --plan S4_广告板块/amazon-ad-campaign-builder/ad_plan.yaml `
  --review-gate S4_广告板块/amazon-ad-campaign-builder/architecture_review.yaml `
  --compile-context S4_广告板块/amazon-bulk-upload/bulk/input/bulk_compile_context.yaml `
  --template S4_广告板块/amazon-bulk-upload/bulk/input/current_account_SP_template.xlsx `  # 用内置表时换成插件内 <home>/skills/amazon-bulk-upload/AmazonAdvertisingBulksheetSellerTemplate.xlsx
  --template-ref S4_广告板块/amazon-bulk-upload/bulk/input/template_ref.yaml `
  --bulk S4_广告板块/amazon-bulk-upload/bulk/SP_bulk_upload_20260809_v1.xlsx `
  --manifest S4_广告板块/amazon-bulk-upload/bulk/bulk_manifest.yaml `
  --output S4_广告板块/amazon-bulk-upload-troubleshooter/bulk/bulk_validation.yaml
```

输入（首版 Bulk、manifest、编译上下文、模板）读 `amazon-bulk-upload` 的夹；本 Skill 的校验结果、重试动作、发布审核与重出的 `_v{n}` 版本写自己的夹 `S4_广告板块/amazon-bulk-upload-troubleshooter/bulk/`。

第一遍校验不传 release 参数。人工完成独立发布审核后，第二遍另存 validation 并增加：

```powershell
  --bulk-release-review S4_广告板块/amazon-bulk-upload-troubleshooter/bulk/bulk_release_review.yaml `
  --approved-validation S4_广告板块/amazon-bulk-upload-troubleshooter/bulk/bulk_validation_pre_release.yaml
```

如有后台处理报告，再加：

```powershell
  --processing-report S4_广告板块/amazon-bulk-upload-troubleshooter/bulk/input/amazon_processing_report.xlsx `  # 用户从后台下载后放进来的回填输入
  --processing-report-ref S4_广告板块/amazon-bulk-upload-troubleshooter/bulk/input/processing_report_ref.yaml
```

### 提取重试动作

```powershell
python scripts/build_retry_actions.py `
  --plan S4_广告板块/amazon-ad-campaign-builder/ad_plan.yaml `
  --validation S4_广告板块/amazon-bulk-upload-troubleshooter/bulk/bulk_validation.yaml `
  --compile-context S4_广告板块/amazon-bulk-upload/bulk/input/bulk_compile_context.yaml `
  --output S4_广告板块/amazon-bulk-upload-troubleshooter/bulk/bulk_retry_actions.yaml
```

## 类型能力边界

- 当前共享验证适配器只有 `SP_V1`。
- SB/SD 必须各有当前类型模板和独立适配器；没有时 capability block。
- 不因为同一工作簿里看见 SB/SD Sheet 就宣称适配已完成。

## 高级判断与常见坑

- 文件 hash 不匹配时，先判断是文件被改、manifest 过期还是用户选错版本；不覆盖证据。
- 整体上传失败可能伴随局部成功；必须用处理报告确认，不用“看见 Campaign”推断所有子行状态。
- Campaign 重名时区分应 Update、补丁 Create、新建版本；不自动 Archive。
- AUTO 否词缺失属于计划/逻辑问题，不在验证器里塞通用案例词。
- Amazon 经营报表与上传处理报告不能混用；前者进入 Analyzer，后者进入本 Skill。
- 技术校验通过只证明结构与计划一致，不证明策略有效、账户接受或广告表现良好。
- legacy hash 接受时保留 warning；不能借迁移重写历史版本。

## 数据解释窗口

报告必须说明：实际验证的文件/hash/表头/行数；从 plan/manifest 自动推导的关系；未提供处理报告时的状态边界；失败只影响哪一批 action；用户如何通过新 plan 版本局部重试。缺可选处理报告不阻止上传前技术校验，但阻止任何后台成功结论。

## Acceptance checks

### P0

- [ ] plan、architecture review、compile context（若有）、template、manifest、Bulk 引用一致。
- [ ] 首行表头与源模板逐字一致，错大小写可检出。
- [ ] 逐 action 字段与计划一致，不只比较总数。
- [ ] Campaign→Ad Group→子实体父子链完整，断链可检出。
- [ ] mixed Create/Update 的临时/真实 ID 规则通过。
- [ ] expected_counts 与实际实体/Operation/预算完全一致。
- [ ] Demo 永久不可上传且表头未破坏。
- [ ] Archive 审核绑定精确 action；独立 Bulk release 绑定 plan/Bulk/prior-validation 三 SHA。
- [ ] 没有处理报告时 amazon_upload_status 保持 `not_submitted`。
- [ ] 处理报告失败 action 可映射，无法映射项明确列出。

### P1

- [ ] 失败 action 形成 reviewable retry package，而不是覆盖旧文件。
- [ ] legacy hash 迁移 warning 保留。
- [ ] SB/SD 缺适配器时正确 capability block。
- [ ] 校验结果含技术状态、发布状态、Amazon 处理状态三层。

## Handoff

```markdown
【本步完成】amazon-bulk-upload-troubleshooter
【产物】bulk_validation.yaml；如有失败则 bulk_retry_actions.yaml
【状态】technical=<passed/failed>；release=<pending/approved/stale>；amazon=<not_submitted/processing_*>
【下一步 Skill】通过待审核→amazon-ops-workflow；处理失败→amazon-ad-campaign-builder；跑数→amazon-ad-report-analyzer
【推荐口令】根据 validation 的失败 action 生成新的 ad_plan 版本并重新审核；不要覆盖旧 Bulk。
【MCP】not_required
```

## 版本记录

| 版本 | 说明 |
|---|---|
| 4.1.0 | 与生成器同步：production 接受 `source=skill_bundled_official`（内置官方表 `AmazonAdvertisingBulksheetSellerTemplate.xlsx`，SHA-256 锁定）；账户下载模板仍优先 |
| 4.0.0 | 对账融合：与生成分离；plan/architecture/context/template/manifest/Bulk 多方 hash；逐 action 对账；父子链；mixed；Demo/Archive/独立发布审核；处理报告 action 映射；确定性 validator 与真实 Builder 回归 |
