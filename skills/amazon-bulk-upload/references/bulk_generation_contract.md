# Bulk 生成契约

版本：1.0（2026-08-09）

## 1. 边界

本 Skill 只生成候选文件，不执行 Amazon 上传。生成成功、技术校验通过、人工发布审核通过、用户实际上传、Amazon 处理成功是五种不同状态，禁止合并。

```text
bulk_generated
→ bulk_validated
→ bulk_release_approved
→ upload_prepared
→ awaiting_external_upload
→ processing_report_received
→ backend_reconciled
```

## 2. 计划完整性

每个结构化产物携带：

```yaml
plan_ref:
  plan_id: "ADP-..."
  plan_version: 2
  plan_hash: "sha256:..."
  hash_schema: "ad-plan-c14n-v1"
```

`ad-plan-c14n-v1`：复制完整计划数据树，只移除根层 `plan_hash` 与 `_meta.generated_at`；根层 `hash_schema` 及其他字段全部纳入 hash。所有字符串键和值先做 Unicode NFC；若两个键规范化后冲突则硬停。随后用 JSON 键排序、紧凑编码、UTF-8 计算 SHA-256。

兼容 `legacy-yaml-line-v1`：从原始 UTF-8 文本删除全部 `plan_hash:` 行后哈希其余字节。兼容只为历史读取，下一次语义修订必须迁移，不得重写旧计划的历史 hash。

报告/HTML 排版变化用 `artifact_sha256`，不升级计划版本。预算、Bid、target、negative、placement、Operation 或对象变化才生成新计划版本/hash。

## 3. Canonical campaigns 与部署参数

Builder `ad_plan 2.0` 的 `campaigns[]` 是 Create 架构执行真源。Generator 用确定性 adapter 把它编译为 Campaign、Ad Group、Product Ad、Keyword/PAT、Negative 与 Placement 行；不要求 Builder 额外维护第二份 `bulk_operations`。

编译时才确定、且不改变策略语义的参数放进 `bulk_compile_context.yaml`：

```yaml
start_date: "20260809"
default_state: paused
sku_asin_map: {REAL-CHILD-SKU: B012345678}
```

- production 必须提供 context，并要求 flat `architecture_review.yaml` 的 `approved_compile_context_sha256` 等于该文件实际 SHA-256。
- 缺失或 hash 不一致时 production 硬停；Demo 只给明确 warning，且永久不可上传。
- manifest 与 validation 均保留 `compile_context_ref`；后续 retry 也必须使用同一 hash 的 context。
- `start_date/default_state/sku_asin_map` 缺失或非法时硬停，不从案例或 Listing 猜值。
- Update/Archive 不由 Create compiler 猜测；仍需真实后台 ID、基线证据与 Archive 独立审核。
- `plan.run_mode=demo_direct_run` 或 `upload_eligibility.status=blocked` 绝不能被 CLI `--mode production` 覆盖；Validator 也独立复查这一门。

## 4. Row-operation DSL（显式修订兼容）

对于已有 Update/Archive、处理报告重试或明确的 mixed 修订，`bulk_operations` 中每项对应一行，字段使用 canonical snake_case；模板适配器负责映射当前账户模板的精确表头。它是受控显式动作路径，不是 canonical Create 架构的第二份真源。

通用字段：

| 字段 | 含义 |
|---|---|
| action_id | 稳定、唯一的动作 ID；用于审核、row_map、处理报告和重试 |
| ad_product | SP/SB/SD；每次生成只处理一个类型 |
| product | 默认 Sponsored Products |
| entity | Amazon Entity 精确值 |
| operation | Create / Update / Archive |

SP 支持实体：Campaign、Ad Group、Product Ad、Keyword、Negative Keyword、Campaign Negative Keyword、Product Targeting、Negative Product Targeting、Bidding Adjustment。

## 5. ID 与父子链

- Create Campaign/Ad Group：临时 ID 必须以英文字母开头。
- Update/Archive：实体主 ID 必须是后台真实数字 ID。
- 临时 Campaign ID 的任何子实体必须能找到本文件中的 Campaign Create 行。
- 临时 Ad Group ID 的任何组级子实体必须能找到同一 Campaign 下的 Ad Group Create 行。
- 向已有 Campaign/Ad Group 增加新实体时使用真实父 ID；其来源应在后台基线快照中登记。
- action_id 与 Excel 行号映射写入 manifest，不能把 trace ID 塞进非官方列。

Update/Archive 主 ID：

| Entity | 主 ID |
|---|---|
| Campaign | campaign_id |
| Ad Group | ad_group_id |
| Product Ad | ad_id |
| Keyword / Negative Keyword / Campaign Negative Keyword | keyword_id |
| Product Targeting / Negative Product Targeting | product_targeting_id |
| Bidding Adjustment | campaign_id + placement |

## 6. Template ref

本 Skill 随包内置官方 Bulksheet 模板 `AmazonAdvertisingBulksheetSellerTemplate.xlsx`
（SHA-256 `e1d85f4fcc571ac091ab5723cc31d4a60b89792b6a919a283303aa7800a36a7f`）。
production 允许两种模板来源：

- `amazon_account_download` / `current_account_download`：用户从当前广告账户下载的干净模板，有则优先；
- `skill_bundled_official`：Skill 内置官方表，用户未提供账户模板时的默认，标准 template_ref
  见 `templates/template_ref.bundled_official.example.yaml`（hash 已预填）。

`template_ref.yaml` 至少记录：

```yaml
source: amazon_account_download   # 或 skill_bundled_official
downloaded_at: ""                 # 账户模板填下载时间；内置表填分发日期
marketplace: US
locale: en-US
ad_product: SP
sheet_name: Sponsored Products Campaigns
adapter: SP_V1
blank_template: true
file_sha256: sha256:...
```

本地 fixture 只能用于 demo/test。模板来源不是“文件名看起来像官方”，而是来源声明加文件 hash
锁定；这不是 Amazon API 对模板真实性或时效性的证明。若 Amazon 官方改版 Bulksheet 表头，
以账户下载版为准并同步更新内置表。生成器复制模板后才写副本，并再次确认源模板 hash 未变。

## 7. Demo

Demo 标记只能进入：

- 输出文件名；
- Campaign/Ad Group 名；
- Product Ad 占位 SKU；
- manifest、validation 和说明文件。

禁止在第一行插备注、改表头、增加非官方 trace 列或删除 Config/隐藏表。即使技术结构通过，状态仍为 `demo_only_not_uploadable`。

## 8. Architecture review 与 Archive

canonical 审核件是独立 flat `architecture_review.yaml`，顶层直接记录 `plan_id/plan_version/plan_hash/hash_schema/status`。Production 状态必须是 `approved_for_bulk`；Demo 必须是 `approved_for_demo_bulk`。旧 nested `architecture_review/plan_review/plan` 形态只作兼容并输出迁移 warning。

批准状态还要求非空审核人、带时区的 ISO-8601 审核时间，以及规定的九项 `checks` 全部明确通过。Flat 与 nested 同时存在属于歧义输入，必须硬停，不能让 legacy approval 覆盖 canonical rejection。

`bulk_release_review` 不得写进 architecture review；它只能在候选生成并完成第一遍技术校验后作为独立文件产生。

Archive 是外部破坏性动作。计划可以提出，但 production 候选只有在：

```yaml
archive_review:
  status: approved
  approved_action_ids: [ACT-...]
```

与实际 Archive action 精确一致时才能生成。不能用“处理重名”作为自动归档授权。

## 9. 类型能力

SP、SB、SD 的 Sheet、Entity、必填字段和枚举不同。当前只有 `SP_V1`。当计划含 SB/SD 时：

- 本次 SP 生成记录其他类型被跳过的数量；
- 对 SB/SD 独立调用时，如果没有对应模板+适配器，返回 capability block；
- 不允许把 SB/SD 行硬塞进 SP Sheet。

## 10. 数量守恒

生成前后都核对：

- total_rows；
- by_entity；
- by_operation；
- Campaign 行 daily_budget_sum。

任何不一致都停止，不生成“差不多可用”的文件。关键词、PAT、negative、placement 的业务数量已经体现在实体计数与 action_id 明细中。

## 11. Mixed Update/Create

混合文件必须区分：

- Create 行使用临时 ID 或真实父 ID；
- Update 行使用真实实体 ID；
- 同一 action/实体不能同时 Create 和 Update；
- 已局部成功时只为失败 action 生成补丁，不整体重传成功 Create；
- 后台基线快照 hash 与计划/manifest 一起保存；
- Archive 独立审核。

65 行历史型回归只是结构测试，不把 65、4 Campaign 或任何案例数量写成默认策略。
