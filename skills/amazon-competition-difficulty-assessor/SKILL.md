---
name: amazon-competition-difficulty-assessor
version: "1.1.0"
edition: starsky-v2
description: >
  亚马逊运营难度评估（阈值法）。当用户问这个词竞争大吗、新品进场难不难、评论区间怎么算、
  广告效率、相似产品怎么选、小白能不能做时使用。先用外观/评论量级/价格三标准锁定
  相似产品，再交叉评论区间法与广告效率法，任一通过即可。头部异形态不算分母。
  装有 starsky-amazon-agent 时由 S1 选品板块的 amazon-market-research 在市场穿透步调用，
  产物写 S1_选品板块/amazon-competition-difficulty-assessor/。
  不要在用户只要扫市场、只要变体、只要旺季广告时使用。
metadata:
  author: 基于 2025-07 选品实战班蒸馏（课21/22/23/34/48/55）
  created: 2026-09-03
  last_reviewed: 2026-09-03
  review_interval_days: 90
---

# 运营难度双法

你是难度评估官。核心问题只有一个：**新品（≈30 评论状态）在这个词下能不能分到单。**

阈值来源：装有星空插件时读 `<home>/templates/s1_thresholds.yaml`；否则读本技能 `references/thresholds.md`。公式与表头以课22 / 课23 / 课48 为准。案例锚点见 `references/cases.md`。

## 数据来源（三级，逐级回退，写实）

| 级 | 来源 | 取什么 |
|---|---|---|
| 1 | 市场 MCP：生产首选 Sorftime（竞品月销 / 评论 / 价格 / 上架）+ SIF（关键词竞争、`ads_get_asin_ad_structure` 广告词结构）；可加装卖家精灵（流量词 / 广告词）、西柚 | 先真实调用一次；工具名含 `sorftime` / `sif` / `sellersprite` / `xydc|xiyou` 即视为已接入 |
| 2 | 用户导出表：卖家精灵竞品对比 / 流量词、H10 Cerebro、前台截图 | 贴数即算；记录文件名与导出日期 |
| 3 | 列字段等回填 | 只列表头，不编销量、评论、词数 |

判 `unavailable` 前必须真实调用一次并附错误信息。多源同一指标差 >20% 先对口径不合并。每个数带来源，落 `01A_data_source_log.yaml`。

## 第 0 步：切词，不要切错市场

换关键词 = 切出不同竞争子市场。`winter dog bandanas bulk` 与 `summer dog bandanas bulk` 不是同一局。太泛的词无法区分竞争关系，先把词收到可判定的精度。

把该词下的产品分成三类：

1. 相关且竞争
2. 相关但不竞争
3. 不相关

只在「相关且竞争」里选相似产品。

## 第 1 步：锁相似产品（三标准）

同时尽量满足：

1. 和这个词相关、能形成竞争，最好外观接近
2. 评论量级接近我们上架时（新品看低评论段），评分也接近
3. 价格接近

**头部不同款、不同形态不算分母**（课55）。
不要把「用来学搭配的同类竞品」和「用来算难度的相似产品」混成一张表。

## 第 2 步：评论区间法

```
A = 相似产品中 30 评论以内的平均月销量
B = 该词自然排名最高 5 个的平均月销量
难度 = A / B
```

| 难度 | 阈值 |
|---|---|
| 困难 | < 10% |
| 适中 | 10% ~ 15% |
| 简单 | > 15% |

参照品难度明显低于我们自己产品时，该参照难以达成。

同时列出 31-50、51-100 区间作对照，**主结论以 30 评以内为准**（那是新品初始态）。

课48 版式（必须照填）：

| 区间 | 平均销量 | 运营难度% | 判定 |
|---|---|---|---|
| 30评论 |  |  |  |
| 31-50评论 |  |  |  |
| 51-100评论 |  |  |  |
| 自然前5平均销量 |  | — | — |

## 第 3 步：广告效率法

对 3~5 个自然位靠前的相似竞品记录：全部流量词 / 自然词 / 广告词 / 月销。

```
广告效率 = 月销量 ÷ 广告词数
广告词占比 = 广告词 ÷ 全部流量词
```

- 效率观测带 **6~17**，越高越友好（课48：304/18=16.89）
- 占比 **>80%** = 烧广告硬顶
- 广告词极少 + 销量高 = 自然主导，对新品友好

## 第 4 步：交叉裁定

**两法任一通过即可**（课23 / 课34）。

| 评论区间 | 广告效率 | 裁定 |
|---|---|---|
| 简单/适中 | 友好或自然主导 | 可做 |
| 困难 | 友好或自然主导 | 有条件可做，必须写差异化 |
| 简单/适中 | 烧广告硬顶 | 谨慎；销量可能买来的 |
| 困难 | 烧广告硬顶 | 放弃 |

销量预估：两法参照结合，**选相对较少的货量**。

## 脚本（确定性计算，可机检）

数据齐了就跑，不要手算：

```bash
python scripts/difficulty_calc.py <竞品表.csv> --out difficulty_assessment.yaml
```

CSV 列：`asin, competing(1/0), reviews, monthly_sales, organic_rank, ad_keywords, total_keywords, price, source`。
脚本输出区间表、广告效率表、双法判定与 YAML；报告里的数字必须与 YAML 一致（容差 0.5%）。

## 输出

```markdown
# 运营难度评估

## 核心词与竞争切分
- 竞争 / 不竞争 / 不相关
## 相似产品清单（三标准）
## 评论区间表
## 广告效率表
| 竞品 | 月销 | 广告词 | 效率 | 占比 | 信号 |
## 交叉结论
- 困难 / 适中 / 简单
- 新品进场：建议做 / 有条件 / 不做
## 保守销量参照
## 数据来源
## 阈值附注
阈值来自 2025-07 课程经验值，请按当前类目校准。
```

## 接入星空亚马逊 Agent · S1 选品板块（编排契约）

装有 `starsky-amazon-agent` 时，本 Skill 是 S1 的**难度判定层**，不是独立入口，不出「可以做」的终裁。

**什么时候被调用**
- `amazon-market-research` 在 market_research 步做完竞争格局后调用（同一核心词、同一竞品池）。
- 入口日常口令「这个词竞争大吗 / 新品能进吗 / 运营难度」→ 经 `starsky-singlepoint-intake` 单点路由到本 Skill。
- 快速选品卡模板 F（阈值失衡法）：`amazon-trend-signal-scanner` 出候选后，对用户选定的 1–3 个候选逐个调用。

**干什么、干到哪为止**
1. 复用 `01_市场穿透报告.md` 的竞品表与 `competitor_asin_pool.yaml`（有则不重拉），缺广告词数时按三级取数补。
2. 输出双法判定 + 保守销量参照；`amazon-gray-tactic-detector` 若已标某竞品「不可参照」，该竞品剔出分母并注明。
3. 结果回填 S1 综合报告：M05 硬门槛（难度阈值 + 实测值 + 达标否）、M10 竞品穿透子节「新品进场难度双法」、M17 证据 `E-DIFF-001…`。子节标题固定写「新品进场难度双法」，不得含「市场容量 / 集中度 / 季节性」等其它模块关键字。
4. 不改 fullchain 步骤数与停顿点；不替代 market-research 的结构性难度分（两者并列呈现：结构分答「市场好不好进」，双法答「新品能分到多少单」）。

**产物落盘**（`templates/skill_folder_map.yaml`）
- `{产品根}/S1_选品板块/amazon-competition-difficulty-assessor/01A_运营难度双法评估.md`
- `{产品根}/S1_选品板块/amazon-competition-difficulty-assessor/difficulty_assessment.yaml`
- `{产品根}/S1_选品板块/amazon-competition-difficulty-assessor/01A_data_source_log.yaml`

**Handoff 四标签**
```markdown
【本步完成】amazon-competition-difficulty-assessor
【下一步 Skill】amazon-market-research（回填 M05/M10）或 END
【推荐口令】继续。把 01A 的双法判定与保守销量并入市场穿透报告与 S1 综合报告。
【MCP】used: <实际调用> / skipped: <原因> / not_required
```

未装插件时按本文独立运行：产物直接在对话输出，或写到用户指定目录。
