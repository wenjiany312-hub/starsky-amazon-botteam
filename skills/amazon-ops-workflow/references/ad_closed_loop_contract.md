# 广告小系统闭环契约

> 适用：`amazon-ops-workflow` 场景 A/B/C 的广告段，以及场景 E 路由广告深挖时。
> 读取时机：进入广告段、恢复旧广告产物、处理 Amazon processing report、判断 S4 状态前必须完整读取。

## 1. 目标与边界

权威链：

```text
strategy
→ builder
→ architecture review
→ bulk generator
→ validator
→ bulk release review
→ await external upload
→ processing report
→ monitor
→ analyzer
→ strategy revision
```

总控只做路由、状态、校验和审核组织：

- 不从分析 Markdown 直接拼 Bulk。
- 不把 validator pass 当作上传授权。
- 不登录账户替用户上传。
- 不因路径存在把步骤写 completed。
- 不把 Demo 占位文件提升为生产文件。

## 2. 稳定标识

| 标识 | 产生者 | 生命周期 | 规则 |
|---|---|---|---|
| `run_id` | 总控 | 整个产品 run | 产品或 marketplace 改变时新建 |
| `cycle_id` | 总控 | 一次广告建站/优化闭环 | processing 后的新策略仍属于当前 cycle；新 plan 执行时开下一 cycle |
| `strategy_id` | strategy | 同一策略族 | 修订递增 `strategy_version`，不得复用旧 hash |
| `strategy_hash` | strategy | 单一策略版本 | 由 canonical YAML 规范化后计算 |
| `plan_id` | builder | 同一计划族 | 计划修订递增 `plan_version` |
| `plan_hash` | builder | 单一计划版本 | 报告、Bulk、审核和结果必须引用同一值 |
| `bulk_id` | generator | 单个 Bulk 候选 | 每次重生成新 ID，包含 `bulk_version` |
| `bulk_hash` | generator | 单个工作簿字节内容 | 修改任一单元格后必须重算并重新验证 |
| `submission_id` | 用户/Amazon 回执 | 一次外部提交 | 无回执不得补造 |

Hash 字段不得使用文件修改时间、文件名或人工备注替代。若 YAML 序列化会导致顺序漂移，使用稳定键序列计算 canonical hash，并记录 `hash_algorithm` 与 `canonicalization_version`。

## 3. 权威产物矩阵

| 阶段 | 生产者 | 必需产物 | 完成所需证据 |
|---|---|---|---|
| strategy | `amazon-ad-strategy-architect` | `ad_strategy_brief.yaml`、策略 MD、`strategy_review.yaml`、`handoff.yaml` | strategy schema pass；strategy review=`approved_for_builder`；hash 一致 |
| builder | `amazon-ad-campaign-builder` | `ad_plan.yaml`、`campaign_architecture.yaml`、架构 MD/HTML | source strategy hash 一致；plan schema/数量/预算守恒通过 |
| architecture review | 总控 + 人工/演示自检 | `architecture_review.yaml` | Production=`approved_for_bulk`；Demo=`approved_for_demo_bulk`；reviewed plan hash 等于当前 plan hash |
| bulk generator | `amazon-bulk-upload` | 官方模板副本、`bulk_manifest.yaml`、entity reconciliation | source plan hash 一致；真实模板来源可追溯；文件 hash 已记录 |
| validator | `amazon-bulk-upload-troubleshooter` | `bulk_validation.yaml`、必要时错误明细 | status=`pass`；P0/P1 error=0；validated plan/bulk hash 一致 |
| release review | 总控 + 人工 | `bulk_release_review.yaml`、上传说明 | Production=`approved_for_external_upload`；Demo=`demo_not_releasable` |
| external upload | 用户 | `external_upload_receipt.yaml` 或 Amazon processing report | marketplace、submission、bulk hash/文件、时间可对应 |
| processing | troubleshooter | 原始 processing report + `bulk_processing_result.yaml` | accepted/partial/rejected 行数守恒；失败行可定位 |
| monitor | 总控/巡检 | `monitoring_plan.yaml`、`monitoring_snapshot_*.yaml`、`change_context_log.yaml` | 生效基线、观察窗口、事件同窗、真实报表范围 |
| analyzer | `amazon-ad-report-analyzer` | `diagnostic_evidence_card.yaml`、`ad_analysis.yaml`、`optimization_actions.yaml` | source cycle/plan、窗口、归因和数量守恒通过 |
| revision | strategy | 新版 strategy + `feedback_record.yaml` | supersedes、结果/失败/回滚、new strategy hash 完整 |

建议路径可以随企业目录调整，但 artifact key、ID/hash 和语义不得改变。

## 4. 状态枚举

```yaml
allowed_statuses:
  - ad_not_started
  - strategy_collecting_evidence
  - strategy_review_pending
  - strategy_reviewed
  - plan_building
  - architecture_review_pending
  - architecture_changes_requested
  - architecture_approved_for_bulk
  - bulk_generating
  - bulk_generated_unvalidated
  - bulk_validation_failed
  - bulk_validated
  - bulk_release_review_pending
  - bulk_release_changes_requested
  - demo_bulk_validated_not_uploadable
  - awaiting_external_upload
  - processing_report_pending
  - processing_unmatched
  - processing_rejected
  - processing_partial
  - processing_accepted
  - monitoring
  - cycle_analyzed
  - strategy_revised
  - cycle_complete
  - blocked_production_fields
  - blocked_ip_decision
  - blocked_external_action
```

不得使用模糊状态 `done`、`bulk_complete`、`uploaded`；它们无法区分验证、审核、外部提交和 processing 结果。

## 5. 合法 transition

```text
ad_not_started
→ strategy_collecting_evidence
→ strategy_review_pending
→ strategy_reviewed
→ plan_building
→ architecture_review_pending
→ architecture_changes_requested → plan_building
→ architecture_approved_for_bulk
→ bulk_generating
→ bulk_generated_unvalidated
→ bulk_validation_failed → bulk_generating 或 plan_building
→ bulk_validated
→ bulk_release_review_pending
→ bulk_release_changes_requested → bulk_generating 或 plan_building
```

Production 分支：

```text
bulk_release_review_pending
→ awaiting_external_upload
→ processing_report_pending
→ processing_unmatched → processing_report_pending
→ processing_rejected → bulk_generating/plan_building
→ processing_partial → bulk_generating（仅失败实体）
→ processing_accepted
→ monitoring
→ cycle_analyzed
→ strategy_revised
→ cycle_complete
```

Demo 分支：

```text
bulk_validated
→ demo_bulk_validated_not_uploadable
```

Demo 没有任何指向 `awaiting_external_upload` 的合法 transition。

## 6. Strategy 完成谓词

```yaml
strategy_ready_for_builder:
  all:
    - ad_strategy_brief.status == valid
    - strategy_report.status == valid
    - strategy_review.strategy_review.status in [approved_for_builder, approved_for_demo_builder]
    - strategy_review.strategy_review.approved_strategy_id == ad_strategy_brief.strategy_id
    - strategy_review.strategy_review.approved_strategy_version == ad_strategy_brief.strategy_version
    - strategy_review.strategy_review.approved_strategy_hash == ad_strategy_brief.strategy_hash
    - product_identity_match == true
    - unresolved_hard_blocks == 0
```

Raw reports 需要 analyzer 时，strategy 保持 `strategy_collecting_evidence`；analyzer 的入口证据不算本轮上线后 `cycle_analyzed`。

## 7. Architecture review

`architecture_review.yaml` 最低字段：

```yaml
schema_version: "1.0"
run_id: ""
cycle_id: ""
strategy_id: ""
strategy_version: 1
strategy_hash: "sha256:"
plan_id: ""
plan_version: 1
plan_hash: "sha256:"
hash_schema: "ad-plan-c14n-v1"
status: pending  # pending | changes_requested | approved_for_bulk | approved_for_demo_bulk | rejected | blocked
reviewed_at: ""
reviewed_by: "user_or_authorized_reviewer"
checks:
  identity_scope: pending
  campaign_role_and_goal: pending
  daily_budget_reconciliation: pending
  profit_and_inventory_guardrails: pending
  maximum_effective_bid: pending
  placement_and_bidding: pending
  targeting_source_traceability: pending
  negative_relevance_and_res_per_safety: pending
  sp_sb_sd_eligibility: pending
requested_changes: []
decision_record: []
```

Builder 产物存在但 review 缺失时只能写 `architecture_review_pending`。Production 的批准必须来自用户或授权审核者；Demo 可由总控完成同一检查并写 `approved_for_demo_bulk`，不得借此进入生产 release。

## 8. Bulk generation 与 manifest

Generator 输入必须同时包含：

- `ad_plan.yaml` 的精确路径、`plan_id/version/hash`。
- `architecture_review.yaml`：Production 为 `approved_for_bulk`；Demo 为 `approved_for_demo_bulk`。
- 用户当前下载的官方模板及 template hash；若生产模式没有模板，写 `blocked_production_fields`。
- marketplace、账户实体类型、真实子 SKU/ASIN；Demo 可用占位但显著标不可上传。

`bulk_manifest.yaml` 最低字段：

```yaml
schema_version: "1.0"
run_id: ""
cycle_id: ""
bulk_id: ""
bulk_version: 1
run_mode: production
uploadability: candidate_not_yet_validated
source_plan: {plan_id: "", plan_version: 1, plan_hash: "sha256:", hash_schema: "ad-plan-c14n-v1"}
source_review: {status: approved_for_bulk, review_hash: "sha256:"}
template: {path: "", sha256: "", marketplace: "", downloaded_at: ""}
files:
  - {ad_product: SP, path: "", sha256: "", rows: 0}
entity_counts:
  plan: {}
  bulk: {}
reconciliation_status: pending
```

官方模板可能按产品分工作簿或按 sheet 组织；不得硬编码必须一类一个文件，以当前模板验证结果为准。

## 9. Validator 完成谓词

`bulk_validation.yaml` 必须记录：

- validator 版本、运行时间、输入文件 hash。
- source `plan_id/version/hash` 和 `bulk_id/version/hash`。
- template、表头、工作表、操作类型和必填字段结果。
- Campaign→Ad Group→Ad/Product/Target/Negative 父子关系。
- Create/Update/Archive 语义、真实 SKU/ASIN、重复实体和临时 ID。
- 预算合计、Bid、Placement、动态竞价最大有效风险。
- plan→Bulk entity counts、逐类差异和允许差异原因。
- Demo 标识检查。

```yaml
bulk_validated_predicate:
  all:
    - validation.status == pass
    - validation.p0_errors == 0
    - validation.p1_errors == 0
    - validation.validated_plan_hash == current.plan_hash
    - validation.validated_bulk_hash == current.bulk_hash
    - validation.entity_reconciliation == pass
    - validation.template_check == pass
    - validation.identity_check == pass_or_demo_labeled
```

任何单元格改变都会改变 bulk hash，原 validation 自动失效。

## 10. Release review

`bulk_release_review.yaml` 最低字段：

```yaml
schema_version: "1.0"
run_id: ""
cycle_id: ""
run_mode: production
plan: {plan_id: "", plan_version: 1, plan_hash: "sha256:", hash_schema: "ad-plan-c14n-v1"}
bulk: {bulk_id: "", bulk_version: 1, bulk_hash: "sha256:"}
validation: {status: pass, validation_hash: "sha256:"}
status: pending
reviewed_by: ""
reviewed_at: ""
checks:
  real_child_sku_asin: pending
  marketplace_account_scope: pending
  current_official_template: pending
  budget_and_profit_authorized: pending
  plan_bulk_counts_match: pending
  unresolved_high_risk_actions: pending
external_action_authorized: false
```

- Production 所有检查通过：`status=approved_for_external_upload`，但 `external_action_authorized` 仍为 false；总控转 `awaiting_external_upload`。
- Demo：`status=demo_not_releasable`，转 `demo_bulk_validated_not_uploadable`。
- 用户可以要求修改或取消；不得默认批准。

## 11. 外部上传与 processing report

总控交给用户的回传清单：

1. 实际上传文件名和 SHA-256。
2. marketplace/account profile 的非敏感标识。
3. 提交时间和 Amazon submission/job ID。
4. 原始 processing report，不要手工改列。

处理结果守恒：

```text
submitted rows/entities
= accepted
+ accepted_with_warning
+ rejected
+ ignored/not_applicable
```

若报告粒度与 Bulk 粒度不同，建立 entity key 映射并解释差异。Partial 时：

- 已接受实体保持只读，不重复 Create。
- 只修失败行及其必要父实体。
- 新 Bulk 使用新 `bulk_version/hash`，重新 validator + release review。
- 原 submission 与 processing report 永久保留。

## 12. Monitor、Analyzer 与 Revision

`monitoring_plan.yaml` 至少包含：

- baseline window、effective_at、earliest_read_at、primary_read_at。
- 主 KPI、护栏、样本成熟条件、库存/预算边界。
- 价格、促销、页面、评分评论、库存、竞品、大促事件的同窗记录。
- 动作 ID 到 Campaign/Target 的映射。

观察点 3/7/14 天只能是可解释默认，以下情况必须调整：低点击、长决策周期、归因未成熟、断货、促销或大幅页面变化。

Analyzer 的每个动作至少有：

```yaml
action_id: ""
object_scope: ""
old_value: null
proposed_value: null
evidence_refs: []
sample: {clicks: 0, orders: 0, spend: 0, window: ""}
expected_effect: ""
observe_until: ""
rollback_if: ""
confidence: low
```

Strategy revision 必须回答：上一轮动作执行了吗、效果到了观察窗口吗、结果是成功/无效/反向/受外因污染、下一轮保留/回滚/扩量什么。没有这些证据时不能写 `cycle_complete`。

## 13. 故障路由

| 故障 | 状态 | 返回 |
|---|---|---|
| strategy/hash 不一致 | `strategy_review_pending` | strategy/review |
| plan 报告与 YAML 不一致 | `architecture_changes_requested` | builder |
| architecture review 缺失 | `architecture_review_pending` | 人工审核 |
| 官方模板或真实 SKU 缺失 | `blocked_production_fields` | 用户补字段；Demo 可继续 |
| Bulk schema/父子/数量失败 | `bulk_validation_failed` | generator 或 builder |
| release review 要修改 | `bulk_release_changes_requested` | 按影响面返回 plan 或 Bulk |
| 未收到外部回执 | `awaiting_external_upload` | 停止并等待用户 |
| processing 无法匹配 | `processing_unmatched` | 请求正确 report/receipt |
| processing partial | `processing_partial` | troubleshooter 修失败实体 |
| 样本未成熟 | `monitoring` | 延长观察，不强行优化 |
| analyzer 只有 Markdown 无结构证据 | `monitoring` 或 `cycle_analyzed` 不成立 | analyzer 重建结构产物 |

## 14. 闭环验收

- [ ] A/B/C 使用同一 artifact keys、状态和 transition。
- [ ] 所有 completed 状态有 path/hash/schema/validation/review 证据。
- [ ] Demo 没有到 external upload 的路径。
- [ ] Production release approval 没被写成实际上传授权。
- [ ] Partial processing 只修失败实体，已接受实体不重复 Create。
- [ ] Monitor 与 analyzer 引用实际生效 plan/bulk/submission，不引用草案。
- [ ] Strategy revision 形成新版本并保留上一轮反馈。
