# Bulk 技术校验与状态契约

版本：1.0（2026-08-09）

## 1. 技术校验输入一致

技术校验必须同时读取：

1. `ad_plan.yaml`：canonical campaigns 或显式 mixed actions 的执行真源。
2. 独立 flat `architecture_review.yaml`：批准 plan hash；canonical campaigns 的 production 还批准 compile-context SHA；Archive actions 另按 action_id 审核。
3. XLSX 模板（账户下载或 Skill 内置官方表 `AmazonAdvertisingBulksheetSellerTemplate.xlsx`）+ `template_ref.yaml`：精确列与来源。
4. Bulk XLSX：候选文件。
5. `bulk_manifest.yaml`：plan/template/file hash、row_map、计数。
6. 若 manifest 声明 compile context：同一个 `bulk_compile_context.yaml`。

缺任一必需项只能做有限诊断，不能给生产发布结论。`bulk_release_review.yaml` 与它批准的 prior `bulk_validation.yaml` 属于第二阶段，不得塞回 architecture review。

## 2. 三层状态

```yaml
technical_status: passed | failed
uploadability:
  status: blocked | candidate_needs_bulk_release_review | reviewed_candidate_upload_prepared | bulk_release_stale | bulk_release_changes_requested | bulk_release_rejected | demo_only_not_uploadable
  bulk_release_review: pending | changes_requested | rejected | blocked | approved | demo_not_releasable | stale
  amazon_upload_status: not_submitted | processing_succeeded | processing_failed | processing_partial_failure | processing_report_unresolved
```

禁止把三层压成一个 `complete: true`。文件存在只证明文件存在。

技术失败写 `hard_blocks`；release 过期写 `release_blocks`。Release stale 不得把仍然通过的技术校验改写为 failed。

## 3. 检查顺序

1. plan hash 可重算且与 review/manifest 一致。
2. template_ref hash 与实际模板一致，production 来源合法（`amazon_account_download`/`current_account_download` 账户下载优先，或 `skill_bundled_official` 内置官方表；fixture 只允许 demo）。
3. Bulk hash 与 manifest 一致。
4. Bulk 首行与模板首行精确一致。
5. manifest.row_map 覆盖所有 Operation 数据行，action_id 唯一。
6. 每个 Bulk 行逐字段匹配同 action_id 的计划行。
7. Create/Update/Archive 必填字段和 ID 语义正确。
8. 临时父 ID 图完整。
9. expected_counts 与实际计数/预算一致。
10. Demo、Archive、compile-context 审核状态正确。

## 4. Bulk release review

第一遍技术校验不提供 release review，输出 `technical_status=passed` 与 `bulk_release_review=pending`。人工随后创建独立 `bulk_release_review.yaml`：

```yaml
run_mode: production
plan: {plan_id: "", plan_version: 1, plan_hash: "sha256:"}
bulk: {bulk_hash: "sha256:"}
validation: {status: pass, validation_hash: "sha256:"}
status: approved_for_external_upload
reviewed_by: ""
reviewed_at: ""
external_action_authorized: false
```

第二遍校验必须同时传入 release review 与它批准的 prior validation 文件。校验器复核 plan id/version/hash、Bulk SHA、prior validation SHA、`technical_status=passed`、technical fingerprint、manifest SHA、run mode 与 reviewer/time；任一改变即 `stale`。Demo 只能写 `demo_not_releasable`。Release 通过仍不等于已授权或已执行真实上传。

`approved_for_external_upload` 与 `demo_not_releasable` 都要求非空审核人、带时区的 ISO-8601 审核时间，以及六项 release checks 全部明确通过；空格、非法时间或 pending check 均为 stale。

## 5. 处理报告

Amazon 批量处理可能局部成功。处理报告应保留原文件 hash，并逐行记录：status、error、Amazon 返回 ID（若有）、Excel row/action_id。失败行生成 retry actions；成功行不得在下一版 Create 中重复提交。

处理报告必须配套 sidecar，绑定 exact Bulk SHA、processing report SHA、submission reference、marketplace 与 advertising account；绑定失败不得改变后台状态。列名会随 Locale/版本变化，解析器无法识别时标 `processing_report_unresolved`，不猜测成功率。人工确认列映射后再修订适配器。

## 6. 修订

验证器不直接改 XLSX：

```text
failed action IDs
→ bulk_retry_actions.yaml
→ Builder/策略层确认错误是否改变业务语义
→ 新 ad_plan version/hash
→ 新 architecture_review
→ Generator 生成 v2/v3
```

若只是平台字段/ID 映射修复，`change_reason` 标技术修订；如果预算、Bid、target、negative 或 Operation 变化，则是语义修订。两者都保留旧版。
