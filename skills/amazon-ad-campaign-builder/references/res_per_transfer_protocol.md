# RES → PER 安全迁移协议

## 1. 定义

- **RES**：Research / Exploration，负责发现有效 query 或 ASIN。
- **PER**：Performance / Exact harvesting，负责隔离、验证和稳定承接高价值流量。

迁移目标不是“把词复制过去”，而是确认目的结构已经能接住流量后，再减少源结构重叠。

## 2. 状态机

```text
candidate
→ destination_planned
→ destination_live
→ destination_validated
→ source_negative_review
→ source_negative_active
```

任何跳过 `destination_validated` 的 source negative 请求都必须被 Builder 正规化为 held。

## 3. 必填证据

```yaml
transfer_id: TR-001
source_object: {campaign_key: SP-RES-AUTO, target_id: KW-001}
destination_object: {campaign_key: SP-PER-EXACT, target_id: KW-001}
destination_status: planned | live | validated
validation_rule:
  metric: orders_and_traffic_continuity
  observation_window: null
  success_condition: null
validation_evidence: []
source_negative:
  match_type: exact
  status: held | ready_for_review | active
  activate_after: destination_validated
rollback_rule: {}
```

观察天数、订单/点击阈值由 Strategy 或 `case_defaults` 提供并标来源；协议本身不硬编码。

## 4. 失败和回滚

- 目的端无曝光：先检查 Bid/相关性/预算，源端保持开放。
- 有点击无转化：目的端回到验证或暂停，源端不否。
- 目的端承接后总流量断崖：撤销源端 Negative，检查匹配与位置。
- Query 与保护词/品牌词/核心 Foundation 冲突：停止迁移并人工复核。

Builder 只生成 `held` 或 `ready_for_review`，不执行 Negative。

