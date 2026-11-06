# 星空报告视觉体系 v1

默认清爽青绿，阅读顺序为结论、重点对象、行动与理由、证据。共用字号、间距、表格和状态样式，各阶段保留自己的业务模块。可切换极简黑白、暖纸阅读以及舒适/紧凑密度；这些切换不改变数据、判断或审核资格。

## 生成入口

S1/S3/S4 静态报告及 S5 独立静态归档使用 `skills/github-tabler-html-report/assets/salon_report.py` 的 `Report`。CSS、JavaScript 随库内嵌到输出 HTML，无需 CDN。S5 实时九页看板保留原 React 应用；此入口仅适用于另行导出的静态归档。不要为单个产品重新手写样式，也不要在生成后修改已绑定指纹的交接产物。

```python
from salon_report import Report

r = Report(title, subtitle, meta=source_metadata, stage="S4")
r.section("s-summary", "结论与待处理事项", group="overview").markdown(summary)
r.section("s-tree", "广告结构", group="structure").tree(architecture_lines)
r.section("s-targets", "投放候选词", group="keywords").table(
    headers, rows, searchable=True)
r.section("s-rules", "预算与观察规则", group="rules").markdown(reviewed_rules)
r.section("s-sources", "限制与证据", group="evidence").markdown(evidence)
r.write(output_path)
```

`title`、表头等普通文本由渲染器转义；`raw`、表格单元格和 `markdown` 的调用应沿用各自文本/HTML 契约。原 `Report/section/table` 调用仍可使用。新报告显式传阶段和阅读分组；旧调用不因无法分类而丢内容。所有章节保持源顺序，完整报告入口可查看全文，未识别章节也保留。

## 各阶段布局

| 阶段 | 首屏 | 阅读分组与内容约束 |
|---|---|---|
| S1 | 产品实图、机会和主要风险、当前决策 | `overview / comparison / economics / evidence`。继续调用 `product_decision`；M00–M18 固定模块及顺序不变，阅读分组仅控制查看范围 |
| S2 词库 | 可用词、文案用途、广告用途、理由 | 继续 `scripts/s2_report.py` 固定八节；来源、时点、适配资格和待核原因可核对 |
| S2 文案 | 英文、中文、计数、埋词和事实依据 | 继续固定十节及版本对照；不换用通用 Report 或另写 HTML |
| S3 | 产品垫图、图位/分镜和待确认内容 | `overview / images / evidence`。用 `Section.image_card(src, title, caption, status)` 展示图片及文字状态；长提示词和规格可折叠 |
| S4 | 计划还是实绩、预算、结构和阻断项 | `overview / structure / keywords / rules / evidence`。首屏保留关键风险、口径与下一步；不得把待确认预算或演示规则呈现为已执行 |
| S5 | 数据日期、销售/广告/库存异常及待审建议 | 保留九页本地 React 看板与下钻，只统一局部颜色、间距、表格和换行；不使用静态长报告替换应用 |

## 图片、表格与证据

- S3 图位卡使用已有产品/垫图文件或已验收图片。明确写“垫图已锁定”“提示词待审”“已生成待确认”等实际状态，不能把提示词或占位图表示成成品。图片应离线可见并配文字说明。
- KPI 标明实际、计划、目标或估算及时间口径；缺失保留缺失。数量由输入提供，不自动补图表凑数。
- 需要检索的关键词/对象明细使用 `searchable=True`。窄屏在安全的规则表格中展示字段标签；含跨行跨列的复杂表保留局部横向滚动。
- Hash、原始参数和长来源可归入证据；会改变当前行动的风险和数据缺口必须在首屏摘要明确表达，不能只藏在证据折叠区。
- “完整报告”和打印恢复全部章节、折叠证据及已筛选表格行。关闭 JavaScript 时保留全文；分组、主题和搜索只是阅读辅助。

## 验收与升级

视觉验收核对原始章节、表格单元格、状态与来源未丢失，桌面/窄屏没有整页横溢，搜索空结果有提示，打印全文完整，离线没有网络依赖。S1 产品首屏和各阶段业务验证器继续执行；S2 保留其固定字段验收。

已有历史报告不批量覆盖。需要新版排版时读取原始结构化输入重新生成到新版本路径，更新该次交接指纹；视觉通过不替代产品、广告或文案的业务审核。
