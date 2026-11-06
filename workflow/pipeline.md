# 全链路流程（美国站 FBA + FBM）

```
老板下指令 → 运营总监
  S1   选品侦察员选品（标出倾向：FBM 测款 / 直接 FBA）    只读
  G1   风控官选品风控（FBA 另查危险品、禁运、保质期）──✗──► 淘汰（附原因代码）
  S3   仓管物流货源 + 财务管家利润（FBM 和 FBA 各算一遍）  只读
  🔒 GATE1 老板确认选品和履约方式
  S4   文案算法师写文案 ∥ 创意美工视觉设计 → 出图工出图 + 三项复检 → 质检官质检
  G2   风控官上架前风控 ──✗──► 打回文案算法师
  S5   文案算法师上架预检（VALIDATION_PREVIEW，只检查不提交）
  🔒 GATE2 老板抽检 + 看预检报告
  S6   文案算法师正式上架 → 等到 BUYABLE（可售）
         └ 直接 FBA 的品：仓管物流先建好 FBA 货件，入仓后才算可售
  S7   创意美工设计 A+ / 品牌故事 → 老板确认 → 出图工出图复检（可选，只限有品牌备案的品）
  🔒 GATE3 确认广告预算（上限由财务管家给出）
  S8   广告优化师开新品期广告
  S9   复盘分析师第 3/7/14/30 天复盘 → 写回知识库
         → 放量 / 优化 / 暂停 / 下架 / 转 FBA / 补货 / 清仓
  S10  FBA 运营：转 FBA、补货、清仓（财务管家先测算 → 🔒 老板确认 → 仓管物流执行）
  G3   风控官每天监控账户健康 → 碰到红线就 HALT（暂停）整条链路
```

交接原则：每个角色只处理停在上一个状态的 SKU，做完只能把它改成下一个状态；淘汰或打回时，必须写明原因。

## 状态机速查（详细定义见 sku_states.yaml）

```
CANDIDATE →(G1a) G1_PASS / G1_CONDITIONAL → SOURCED →(G1b 图片 IP) IP_CLEARED ⇄ NEED_INPUT → 🔒AWAIT_GATE1
→ APPROVED_1 → CONTENT_WIP（文案 ∥ 图片）→ QA_CHECK → G2_CHECK → PREVIEW → 🔒AWAIT_GATE2
→ APPROVED_2 → [AWAIT_UPLOAD] → SUBMITTED → [FBA_INBOUND] → BUYABLE（∥ A+ 支线：创意美工设计 → 出图工出图）
→ 🔒AWAIT_GATE3 → APPROVED_3 → [ADS_AWAIT_UPLOAD] → ADS_LIVE → REVIEWING
→ SCALE / OPTIMIZING / PAUSED / STEADY / 🔒转FBA / 🔒补货 / 🔒清仓 / 🔒下架
方括号 [ ] 表示只有特定情况才会经过：用表格上传时，或者走 FBA 时
任何一步都可能 → REJECTED（必须写原因）或 ON_HOLD（老板搁置）
```
