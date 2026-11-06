# 广告架构路由规则

## 1. 路由变量

禁止只用“标品/非标”或“上架天数”决定结构。至少结合：

- `product_form`: standard / semi_standard / nonstandard / hybrid。
- `lifecycle`: new / growth / mature / relaunch / seasonal / event。
- `traffic_concentration`: concentrated / distributed / unknown。
- `traffic_entry_count`、头腰部断层、关键词单调性。
- 预算与探索损失承受力、库存覆盖、补货时间。
- 订单/点击样本、管理能力和账户素材/资格。

## 2. Campaign 角色

| 角色 | 主要任务 | 常见输入 | 退出/迁移 |
|---|---|---|---|
| Foundation | 保持核心可重复入口 | 高相关核心词、品牌/自有商品保护 | 只有证据显示失效才收缩 |
| RES | 探索新词、新 ASIN、新属性/场景 | Auto、Broad、Phrase、Category/Product 测试 | 候选进入验证或降控 |
| PER | 承接已经验证的搜索词/ASIN | Exact、小组/单词隔离、已验证 PAT | 按利润/排名/保护目标细分 |
| Rank | 目标词位置或销售速度实验 | 明确目标词和损失预算 | 到期、上限或失败即回滚 |
| Profit | 在稳定订单上压低 CPA/ACoS | 可靠 CVR、利润边界 | 订单韧性下降时恢复 |
| Defense | 品牌、自有 ASIN、核心自然入口保护 | 当前品牌/商品流量证据 | 需评估增量与自然流变化 |
| Retargeting | 再触达浏览/加购/购买人群 | 账户资格、受众规模、基线 | 规模不足或增量不成立则停 |
| Seasonal | 旺季/活动窗口获量与退坡 | 趋势、截止日、库存 | 配送/库存/峰值结束自动退坡 |
| Relaunch | 断货后恢复旧优质流量 | 断货前窗口和当前库存 | 旧流量失效则转新品逻辑 |

一个 Campaign 只有一个主目标与 KPI；同一 Target 出现在多个 Campaign 时必须说明角色差异和防内耗机制。

## 3. 复杂度选择

### simple

适合低预算、低订单、少入口或管理能力有限。一个发现层 + 一个核心/保护层，可加小样本 Product Targeting。必须说明压缩后缺失的可观察性和流量隔离能力。

### standard

RES/PER 分离；Auto/关键词/商品定位按证据拆分；可形成来源清晰的搜索词迁移。

### advanced

只有数据量、预算和管理能力支持时使用：核心词/ASIN隔离、位置实验、品牌/商品防守、受众或大促多层复盘。复杂度不能超过可观测数据量。

## 4. 场景路由

| 信号 | 优先动作 |
|---|---|
| 新品、低订单 | 先 Foundation + RES，限制同时变量，收集有效入口 |
| 成熟且稳定 | 增加 PER/Profit/Defense，保留受控 RES |
| 流量集中、断层高 | 少数核心词/ASIN + 位置实验，预算不足不铺满 |
| 流量分散、非标 | 属性/场景/人群 Phrase/Broad 探索，Exact 保护 |
| 出单集中少数搜索词 | 建 PER 隔离，同时保留探索层 |
| 有效投放过少 | 先增加有效投放，不砍唯一出单入口 |
| 季节峰值临近 | 结合库存、配送截止与退坡时间放量 |
| 断货重启 | 小步激活旧优质流量；失败则切 Launch |

## 5. SP/SB/SD 决策

逐类输出：

```yaml
decision: open | experiment | partial | defer | not_eligible | unknown
capability: {}
eligibility: {}
readiness: {}
goal_fit: {}
evidence_refs: []
missing_or_risk: []
recheck_trigger: []
```

不得把“首轮”写成一律只投 SP，也不得因为可投就默认全开。资格、广告目标、素材、可用关键词/ASIN/受众证据、预算、归因与实验设计共同决定。

## 6. RES→PER 迁移协议

```text
candidate identified
→ destination planned
→ destination created and live
→ destination obtains defined acceptance evidence
→ source exact negative proposed
→ human review
→ staged source negative
→ observe and rollback if needed
```

`destination_status` 只有 `validated` 才允许把源端否定标为 `executable`。目的端失败时撤销源端否定并保留旧结构。

## 7. 否词纪律

- 明显不相关可快速处理，但仍记录语义证据。
- 相关但低效必须考虑样本、归因延迟、购买周期、核心词/自然流保护。
- 否定精准与否定词组分开；词组否定必须检查词根是否会误杀 Foundation/PER。
- 跨广告类型的否定或排除按各产品实际能力处理，不假设字段完全相同。
- 待转移搜索词在目的端承接前不得源端否定。

## 8. 命名

建议结构：

```text
{MARKET}-{ADTYPE}-{ROLE}-{SKU}-{TARGET}-{MATCH}-{GOAL}-{YYYYMMDD}-{REV}
```

名称必须能看出存在理由。命名只是可配置规范，不替代稳定 `campaign_key` 和平台真实 ID。
