# 诊断与动作契约

## 1. 指标

```text
CTR  = Clicks / Impressions
CPC  = Spend / Clicks
CVR  = Orders / Clicks
CPA  = Spend / Orders
ACoS = Spend / Ad Sales
ROAS = Ad Sales / Spend
TACoS = Ad Spend / Total Sales
```

零分母返回 `null + reason`。TACoS 只有拿到同范围、同窗口总销售时才计算；广告销售与自然销售分开保存。

## 2. Wilson CVR 区间

对 `orders <= clicks` 的二项近似，输出 95% Wilson score interval：

```text
p = orders / clicks
center = (p + z²/(2n)) / (1 + z²/n)
margin = z * sqrt(p(1-p)/n + z²/(4n²)) / (1 + z²/n)
```

区间宽度仅表示当前观察值的不确定性，不是类目真实 CVR 的保证。默认解释：

| 95% 区间宽度 | 标签 | 用法 |
|---:|---|---|
| ≤0.10 | comparatively_stable | 可作为多证据之一 |
| >0.10 且 ≤0.20 | directional | 只能支持小步、可逆动作 |
| >0.20 | exploratory | 不作为大幅升 Bid/预算的唯一依据 |

0.10/0.20 是可覆盖的统计解释默认值，不是 Amazon 官方门槛。若订单大于点击，不强行套二项模型。

## 3. 固定诊断顺序

```text
经营目标和异常事件
→ 来源/日期/归因成熟度
→ 账户/产品总体（按来源单元）
→ Campaign
→ Ad Group
→ Target / Match / ASIN
→ Search Term
→ Placement
→ 商品与非广告外因
→ 上一轮动作结果
→ 最小可逆动作
```

先定位层级，再谈调价。一个层级表现差不等于其所有子项都差；一个出单词 ACoS 高也不等于应该砍掉唯一有效入口。

## 4. 四象限

| 象限 | 解释 | 候选动作 | 禁止捷径 |
|---|---|---|---|
| 低 ACoS 出单 | 有效流量 | 保护；预算受限且库存/利润允许时再评估放量 | 仅因低 ACoS 自动加预算 |
| 高 ACoS 出单 | 有价值但成本高 | 拆 CPC/Placement/CVR/角色；小步修订 | 直接暂停 |
| 高点击 0 单 | 浪费候选 | 相关性、延迟、页面、事件复核后否词/降 Bid | 固定美元阈值自动否词 |
| 低点击 0 单 | 样本不足/长尾 | 观察、合并分析或低步长控制 | 逐词频繁操作 |

## 5. 否词

- 明显无关需要产品事实或人工确认，可快速进入审核。
- 相关但低效必须考虑点击样本、转化延迟、客单价、决策周期、来源 Match 和目的端承接。
- “20 点击”只作为通用复核参考，不是所有类目的自动否词门槛。
- 先计算零订单花费占本来源花费比例；占比很小时，否词可能不是最高优先级。
- 否定精准与否定词组分开；词组否定的影响范围更大。

## 6. 预算、Bid 与 Placement

- 预算先看实际花费/设置预算。未触顶时调低设置预算不能改变已获取流量质量。
- Bid 数值应由策略层结合允许 CPC、目标 CPA、当前 CPC/CVR、位置与动态竞价叠加后的最大有效 Bid 计算。
- Placement 只有在相同 Campaign 角色、足够样本、当前竞价策略可解释时才比较。
- 一次限制变化变量；同时改基础 Bid、策略、Placement 和预算会破坏归因。

## 7. RES→PER

出单搜索词可以成为 Exact/PAT 目的端候选，但安全顺序不可改变：

```text
建立目的端
→ 上线
→ 获得承接证据
→ 人工审核源端否定
→ 分阶段 negative exact
→ 继续观察或回滚
```

`destination_status != validated` 时，源端否定必须 `executable=false`。

## 8. Finding 和 Action

Finding 是可复算证据，不是处方。Action 是待审核候选，不是执行命令。

每个 Action 至少有：对象、当前/建议值、数值来源、证据引用、替代解释、前置条件、保护条件、观察期、回滚条件、置信度、人工决策。

没有 target ACoS、盈亏 ACoS、当前 Bid/预算或 Campaign 角色时，建议值保持 `null`，只说明需要审核的方向。

## 9. 上一轮动作

同一对象同一动作若仍为 pending、executed 未完成观察、或 insufficient_data，本轮不得重复建议；改为 hold，并说明还缺什么结果。只有结果成熟或上下文发生变化才重新开动作版本。

## 10. 事实、推断、建议

| 层 | 例子 |
|---|---|
| fact | 当前报表 25 点击、0 单、$20 花费 |
| derivation | 观察 CVR=0，Wilson 上限 X，ACoS null |
| inference | 可能是相关性、页面或归因未成熟 |
| recommendation | 复核相关性后选择否定精准或降 Bid |

禁止把 inference 写成 fact，也禁止把 recommendation 写成已执行结果。

