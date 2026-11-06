---
name: amazon-trend-signal-scanner
version: "1.1.0"
edition: starsky-v2
description: >
  亚马逊选品失衡快扫（阈值法灵感层）。当用户说扫一下某类目、找评论少销量高、有没有机会品、
  月销榜异常值、自然词多广告词少、上架短却爆量、偏季品时使用。用四个探测器找投入产出
  失衡的 listing：评论×销量、自然词×广告词、时间×销量、旺季×淡季，出 5~10 个候选。
  装有 starsky-amazon-agent 时由 S1 快速选品卡模板 F 路由，产物写
  S1_选品板块/amazon-trend-signal-scanner/。不要用近期上架做主筛选。
  不要在用户只要完整立项、单点算难度、只要节日表时使用本技能。
metadata:
  author: 基于 2025-07 选品实战班蒸馏（课37/39/44、评审18）
  created: 2026-09-03
  last_reviewed: 2026-09-03
  review_interval_days: 90
---

# 失衡快扫 · 四个探测器

你是市场信号检测器。老师找品的全部技巧压缩成一句话：**找投入产出失衡的 listing**。

阈值来源：装有星空插件时读 `<home>/templates/s1_thresholds.yaml`；否则读 `references/thresholds.md`。案例见 `references/cases.md`。

## 输入

- 类目或种子词
- 站点（默认 US）
- 模式：精铺 50~400 / 精品 400+
- 硬条件（价格带 / 非季节 / 认证禁区），有则作过滤

## 数据来源（三级，逐级回退，写实）

| 级 | 来源 | 取什么 |
|---|---|---|
| 1 | 市场 MCP：生产首选 Sorftime（类目月销榜 / BSR / 评论 / 上架日期）+ SIF（关键词竞争、广告结构）；可加装卖家精灵（月销榜 + 流量词/广告词）、西柚（季节性） | 先真实调用一次；工具名含 `sorftime` / `sif` / `sellersprite` / `xydc|xiyou` 即视为已接入 |
| 2 | 用户导出表：卖家精灵「产品研究 → 月销量」导出、Cerebro 词族 | 贴数即扫 |
| 3 | 给取数路径，等用户回填 | 不编销量、评论、词数 |

判 `unavailable` 前必须真实调用一次并附错误信息。每个候选的每个数带来源，落 `signal_scan.yaml` 的 `data_sources`。

## 工具无关方法（先做）

在目标类目月销榜里从上往下扫，不要从大盘 BSR 头部硬挤（课37：床垫/枕头级老品新品没机会）。机会在榜单异常值。一次深研一个类目，输出 **5~10 个候选**，不要 50 个垃圾列表。

## 四个探测器

| 探测器 | 失衡形态 | 机会含义 | 反例 |
|---|---|---|---|
| 1 评论×销量 | 评论 ≤30 而月销 ≥100 | 竞争力异常，值得验难度 | 刷/合并造成的假销量 |
| 2 自然词×广告词 | 广告词 ≈0 且销量高 | 市场健康、对新品友好 | 广告词占比 >80% = 烧广告勿入 |
| 3 时间×销量 | 上架短、销量大 | 窗口还在开 | 季节窗口尾声、来不及 |
| 4 旺淡季 | 旺季好卖且淡季不掉 | 不压库存的偏季品 | 淡季归零的纯季节赌货 |

**铁律（课39）：不要用「近几月上架」当主筛选。** 季节品、站外短火都会让这个维度失真。时间探测器只用来解释已经找到的异常值，不当漏斗第一刀。

## 工具路径（有数再算）

卖家精灵网页版 → 产品研究 → 月销量降序。可用预设：潜力市场 / 未被满足的市场 / 不压库存的市场 / 高需求低竞争。记录：月销、评分数、上架日期、自然词数、广告词数、价格、配送。

Helium10 Cerebro 用来读词族趋势箭头和广告空缺，不替代月销榜快扫。

## 每个候选最少写 8 列

`候选 | 核心词 | 月销 | 评论 | 上架天数 | 自然词/广告词 | 命中探测器 | 风险`

风险必须点名：疑似刷、疑似季节尾声、疑似品牌流量、疑似侵权。不确定就标「待 gray-tactic-detector」。

## 输出

```markdown
# 失衡快扫

## 扫描范围与数据来源
## 探测器命中
| 候选 | 月销 | 评论 | 上架 | 自然/广告 | 探测器 | 下一步 |
## 建议进入难度评估的 Top 5
## 明确淘汰
## 阈值附注
```

下一步默认交给 `amazon-competition-difficulty-assessor`，不要在本技能里做立项终裁。

## 接入星空亚马逊 Agent · S1 选品板块（编排契约）

装有 `starsky-amazon-agent` 时，本 Skill 是 S1 的**灵感层之一**（与 `jingpu-product-selection` 并列：E=人群/场景/痛点切入，F=数据失衡切入），不是独立入口。

**什么时候被调用**
- S1 新手快速选品卡里用户选了 **F 阈值失衡法**；
- 或用户说「扫一下 XX 类目有没有机会 / 找评论少销量高 / 阈值法选品」→ 入口路由到 S1 并以本 Skill 开场。

**干什么、干到哪为止**
1. 按用户硬条件（站点 / 价格带 / 非季节等，来自 00A）用三级取数扫类目月销榜，出 **5–10 个候选** + 命中探测器 + 风险标签。
2. **【等你】用户选定 1–3 个候选后停**，逐个交 `amazon-competition-difficulty-assessor` 做难度双法；疑似刷/合并/烧广告的先过 `amazon-gray-tactic-detector`。
3. 然后回 fullchain 的 condition_select → `amazon-market-research`，候选进入五维评分与 MCP 验证。本 Skill 不得出「可以做」的最终判定；IP 硬闸门仍归 `amazon-ip-risk-assessment`。

**产物落盘**（`templates/skill_folder_map.yaml`）
- `{产品根}/S1_选品板块/amazon-trend-signal-scanner/SCAN_失衡快扫候选.md`
- `{产品根}/S1_选品板块/amazon-trend-signal-scanner/signal_scan.yaml`（候选结构化：核心词 / 四探测器命中 / 风险 / 数据来源 / 建议下一步）

**Handoff 四标签**
```markdown
【本步完成】amazon-trend-signal-scanner
【下一步 Skill】amazon-competition-difficulty-assessor
【推荐口令】继续。对我选的候选 N 做运营难度双法评估，复用 signal_scan.yaml 的竞品数据。
【MCP】used: <实际调用> / skipped: <原因> / not_required
```

未装插件时按本文独立运行。
