# SIF 来源字段与诊断契约

## 1. 17 字段完整映射

| # | SIF 原字段 | canonical | 单位/语义 |
|---:|---|---|---|
| 1 | 关键词 | `keyword/source_keyword` | 原词 + 归一键 |
| 2 | 中文翻译 | `cn_translation` | 辅助语义，不覆盖英文 |
| 3 | 相关性 | `sif_relevance` | 平台标签，不替代产品事实 |
| 4 | 相关性得分 | `relevance_score` | 平台分数，保留原值 |
| 5 | 周搜索量 | `weekly_search_volume` | 周口径，不擅自转月 |
| 6 | ABA排名 | `aba_rank` | 小值靠前；空/0按源值解释 |
| 7 | 24小时归因的点击转化率 | `cvr_24h` | 第三方 24h 字段，不是账户 CVR |
| 8 | 建议竞价(低) | `cpc_low` | USD 市场信号 |
| 9 | 建议竞价(中) | `cpc_mid` | USD 市场信号 |
| 10 | 建议竞价(高) | `cpc_high` | USD 市场信号 |
| 11 | Top3点击集中度 | `top3_click_share` | 集中结构 |
| 12 | Top3转化集中度 | `top3_conversion_share` | 集中结构，绝不当 CVR |
| 13 | Top4占位率 | `top4_share` | 头部覆盖 |
| 14 | Top8占位率 | `top8_share` | 第一页前部覆盖 |
| 15 | Top16占位率 | `top16_share` | 第一页覆盖 |
| 16 | Top32占位率 | `top32_share` | 前两页覆盖 |
| 17 | Top48占位率 | `top48_share` | 长尾覆盖结构 |

百分数字段统一为 0–1，但 lineage 保留原始文本。`—/N/A/空字符串` 为 null；不改成 0。

## 2. Sheet2 反向污染学习

1. 对“几乎不相关”Sheet 做 token/短语频率。
2. 排除停用词、核心词根、同义词和已确认场景词。
3. 每个候选排除根保存命中量、实际可得的真实例词和 source rows。
4. 短语共现和词边界复核；低置信候选进入 observe。
5. Sheet2 不直接产生全局否词；产品事实和核心词优先。

## 3. 流量集中度

在同一相关词范围和同一搜索量单位内计算：

```text
top_n_share = sum(search_volume of top n) / sum(search_volume of eligible related terms)
keywords_to_80pct = first k where cumulative_share >= 0.80
```

在存在可比数据且有助于判断时输出 Top10/20/50/100 和 80% 词数，附参与分母的词数/总量、排除规则与 source_id；空集合不填零或凑图。集中/分散的分类阈值只能标 `case_default`、企业规则或本轮用户规则；不写成 Amazon/SIF 官方硬规则。

高流量低相关单列，不自动进入 Exact-P1。

## 4. 季节性和窗口

单次 SIF 导出只能描述本窗口。常规/季节/节日驱动的判定必须引用类目趋势、历史窗口或明确事件；无趋势证据写 `unknown`。

记录 `as_of`、period、搜索量单位、窗口局限和复查触发。固定“每月/季度/30 天”只能作案例默认，不是所有产品硬规则。

## 5. CVR 与代理降级

- `cvr_24h` 有值：保留值、填充率、时间/归因语义，标 `third_party_24h_not_amazon_account_cvr`。
- CVR null、CPC 有值：Top3 份额只解释集中结构，不能填进 CVR。
- CVR/CPC 都 null：可根据真实搜索量/相关性决定内容候选或 observe，但不得给伪精确 Bid/CPA。
- `CPA=CPC/CVR` 只有 CVR 语义与分母适用时才计算；第三方 CVR 仅情景，账户 CPA 需广告报表。

## 6. 出价边界

词包只输出 `cpc_low/mid/high` 与来源。以下公式可作为 Strategy 输入说明，不在词库内决定最终 Bid：

```text
allowable_cpc = target_cpa * expected_account_cvr
signal_bid_scenario = external_cpc_signal * intent_factor
```

外部信号高于经济上限时必须揭示冲突，不强行抬价。

## 7. Listing 与 claim

竞品/未许可品牌隔离；已确认自有品牌可进 Title，兼容用途按 S1 具体结论复核。`blocked_claim` 搜索词保留为研究证据并 `claim_restricted=true`，同时退出可用文案与默认广告候选；不得把未证实性能写成本品事实。共同八节、MCP 与季节规则见 `../../../templates/keyword_report_layout.md`。
