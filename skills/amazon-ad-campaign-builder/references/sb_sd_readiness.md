# SP / SB / SD Capability 与 Readiness

## 1. 两层判断

| 层 | 问题 | 示例 |
|---|---|---|
| capability | 账户和素材能否执行 | 资格、品牌状态、Logo、视频、Store/落地页 |
| readiness | 当前证据是否值得执行 | 已验证词/ASIN、目标、预算余量、自然/广告表现 |

任何层都不得默认。`brand_registered`、广告类型资格、素材规格和账户 UI 必须有当前来源；未知写
`unknown`，不能默认 true。

## 2. Strategy Brief 合同

```yaml
ad_product_decisions:
  SB:
    capability:
      status: available | missing_assets | ineligible | unknown
      evidence: []
      missing: []
    readiness:
      decision: open | partial | defer | unknown
      decision_basis: []
      recommended_scope: []
      reevaluate_when: []
```

Builder 只执行该结论：

- `defer`：不生成 Campaign，记录原因与复评条件。
- `partial`：只编译 `recommended_scope` 对应 blueprint。
- `open`：仍需 capability available 与证据。
- `unknown`：demo 可出草案；production blocked。

## 3. 非绝对化纪律

- 不把“第一轮一定不开 SB/SD”写成平台规则；可作为带来源的打法默认值。
- 不把固定转化词数、天数、排名页数、预算利用率写成永久门槛。
- 不宣称 SB 只收割、SD 新品只有一种有效打法；由当前目标和证据决定。
- 不宣称视频一定优于图片；把格式当实验，写基线、预算、停止条件。
- SD 浏览归因与 SP/SB 点击归因分开呈现，不能简单平均 ACoS。
- SP/SB/SD Bulk 模板不同；下游必须按广告类型分文件。

## 4. 跨类型冲突

- 同一关键词在 SP Exact 与 SB 同时投放时，必须说明不同目标/位置及预算边界。
- 同一竞品 ASIN 在 SP PAT 与 SD 同时投放时，必须说明转化与曝光分工。
- 否词同步不是无脑复制；先检查广告类型是否支持、是否会阻断保护流量。
- IP/合规黑名单必须传导，但竞品品牌定向与前台文案使用是两个不同问题。

