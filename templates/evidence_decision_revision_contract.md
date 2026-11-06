# 沙龙第二版｜证据、决策与局部修订契约

版本：1.0（2026-07-22）

## 1. 目的

所有数据型 Skill 使用同一条链：

```text
source → evidence_record → fact → inference/assumption → decision → action → revision
```

禁止把推断写成事实；也禁止因为部分字段不完整而删除不依赖该字段的分析。

## 2. 事实优先级

同一字段冲突时，按当前商品的直接性和可验证性处理：

```text
样品/检测实测
> 当前商品供应商书面确认
> 当前 1688 offer 结构化页面/SKU/属性
> 当前商品图片或 OCR 可直接观察事实
> Amazon 当前商品/竞品 MCP 数据
> 企业历史资料或类目经验
> AI 推断/demo_default
```

后到高等级事实可以修订早期假设，但不能静默覆盖。旧值进入 `revision_history`。

## 3. evidence_record

```yaml
evidence_id: E-001
field: package_weight_g
value: 580
unit: g
source_type: sample_test | supplier_written | page_confirmed | media_derived | mcp | user_file | ai_inferred | demo_default
source_ref: "相对路径、offer_id、ASIN 或字段路径"
observed_at: "2026-07-22"
confidence: high | medium | low
allowed_use: listing_ready | calculation_ready | conditional_draft | internal_simulation_only | blocked_claim
revision_trigger: "供应商箱规或样品称重"
```

关键数字、消费者可见声明、采购/库存决策和广告财务参数必须有 `evidence_id` 或明确的 assumption id。

## 4. decision_record

```yaml
decision_id: D-001
decision: go | go_with_modification | pivot | abort | fix_required | accept_risk
evidence_ids: [E-001]
assumption_ids: [A-001]
required_changes: []
failure_thresholds: []
decision_source: user | rule | demo_safe_default
decided_at: ""
allowed_next_steps: []
```

`demo_safe_default` 只允许内部研究、条件性文案、演示视觉和 DEMO Bulk；不能代表下单、上传或对外发布授权。

## 5. data_source_log

```yaml
- step: competitor_lookup
  tool_or_file: sorftime
  parameters_summary: "US / keyword / top 30"
  time_window: "latest available"
  returned_count: 27
  truncated: false
  missing_fields: []
  failures: []
  secret_redacted: true
```

不得记录 API Key、AppSecret、Bearer Token、含密钥 URL 或账号凭据。

## 6. 局部修订

```yaml
revision_id: R-001
changed_fields: [package_weight_g]
old_evidence_ids: [E-010]
new_evidence_ids: [E-021]
affected_artifacts:
  - 03_product_facts.yaml
  - 阶段_S1_选品可行性研究报告.md
  - ad_plan.yaml
unaffected_artifacts:
  - 06_关键词库报告.md
status: queued | recalculated | reviewed
```

修订时保留 v1，输出 v2 或修订记录。不能只补包装重量却重洗全部关键词。

## 7. 数据解释窗口

每个业务报告必须说明：已获取事实、自动提取/推导、模拟参数、缺失及影响、继续方式、后补修订入口。解释窗口不创建新的审批门。

## 8. acceptance_checks

- [ ] 关键事实有 evidence_id、来源和允许用途。
- [ ] 冲突字段按优先级处理且旧值留痕。
- [ ] 推断/demo_default 未进入真实上传或消费者可见承诺。
- [ ] 数据来源日志记录返回量、截断、缺失和失败，但无凭据。
- [ ] 后补数据只重算受影响产物。

