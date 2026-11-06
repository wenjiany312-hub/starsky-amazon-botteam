---
name: amazon-gray-tactic-detector
version: "1.1.0"
edition: starsky-v2
description: >
  竞品灰手段公开信号识别（只判读，不提供任何违规操作）。当用户问这个竞品销量是真的吗、
  是不是刷单、合并变体、烧广告、品牌流量复制不来、大牌平替能不能做时使用。用评论回评、
  AC 词、广告占比、销量评论倒挂、品牌词搜索量等公开特征出决策树，给每个参照 ASIN 打
  可作参照/仅证明市场存在/不可参照。装有 starsky-amazon-agent 时由 S1 的
  amazon-market-research 在竞品穿透时调用，产物写 S1_选品板块/amazon-gray-tactic-detector/。
  禁止输出刷单、测评、直评、合并僵尸、售后卡的任何执行步骤。
metadata:
  author: 基于 2025-07 选品实战班蒸馏（评审10/13/14/17/52）
  created: 2026-09-03
  last_reviewed: 2026-09-03
  review_interval_days: 90
---

# 灰手段识别（纯侦察）

你只做一件事：判断「这条热卖路径能不能当选品依据」。

先读 `references/risk-notes.md`。用户哪怕问「怎么刷」「测评怎么做」，也只回答：本技能不提供执行指引，并转回合规差异化。阈值来源：装有星空插件时读 `<home>/templates/s1_thresholds.yaml`；否则读 `references/thresholds.md`。

## 数据来源（三级，逐级回退，写实）

| 级 | 来源 | 取什么 |
|---|---|---|
| 1 | 市场 MCP：Sorftime（评论数 / 月销 / 上架日期 / `product_reviews` 回评节奏）+ SIF（品牌词搜索量、广告词结构）；可加装卖家精灵（AC 推荐词、流量词占比）、西柚 | 先真实调用一次 |
| 2 | 用户导出表 / 详情页截图 / 评论页观察 | 贴数即判 |
| 3 | 列观察项等回填 | 不编回评数、不编占比 |

「接口没返回」≠「没有该属性」：查不到留评节奏时写 `unknown`，不写「非刷」。

## 五类信号（课10 / 14 / 17 / 52）

| 类型 | 公开特征 | 选品含义 |
|---|---|---|
| 疑似刷单 | 产品无创新 + 非短时季节 + 排除品牌/侵权/性价比后仍解释不了；AC 推荐词很多；每天持续回评；新品留评率 >5% | 销量作废，不当需求 |
| 合并变体 | 老品销量与评论严重倒挂（评很少、卖很多，或评很多、近期突然爆） | 权重是并来的，复制不来 |
| 烧广告 | 广告词占比 >80%，或广告词数远高于同销量竞品 | 窗口靠烧钱，CPC 会打爆精铺 |
| 品牌流量 | 关键词带其品牌且品牌词有几百以上搜索量 | 流量冲品牌来，参考不来 |
| 侵权 / 大牌平替 | 角色 IP、商标近似、明显平替 | 直接淘汰（IP 终裁归 IP 技能） |

老师浓缩判读常见是组合拳：季节 + 合并 + 刷 + 烧广告（课10）。不要只贴一个标签就结束。

## 决策树

```
热卖看起来很好
 ├─ 有品牌词搜索量？ → 复制不来，放弃
 ├─ 侵权/IP？ → 放弃（交 amazon-ip-risk-assessment 终裁）
 ├─ 广告占比 >80%？ → 当烧广告，不立项
 ├─ 排除热点/性价比/季节/创新后仍解释不了
 │    + AC 词多 + 持续回评？ → 疑似刷，销量作废
 ├─ 评论与销量长期倒挂？ → 疑似合并
 └─ 以上都不是 → 才可以把销量交给难度评估
```

## 输出（只给判读）

```markdown
# 竞品信号判读

## 对象与数据来源
## 信号清单
| ASIN | 类型 | 证据 | 置信度（高/中/低） |
## 决策
| ASIN | 可作选品参照 / 仅作市场存在证明 / 完全不可参照 | 理由 |
## 合规替代
（差异化、换词、小步试错——不要给灰产步骤）
```

置信度规则：只有 1 个软信号 = 低；2 个独立信号 = 中；3 个或有明确品牌/侵权 = 高。

## 接入星空亚马逊 Agent · S1 选品板块（编排契约）

装有 `starsky-amazon-agent` 时，本 Skill 是 S1 的**竞品真伪预处理层**，不是独立入口。

**什么时候被调用**
- `amazon-market-research` 在 market_research 步做竞争格局 / Top3 反向工程时，对参照竞品逐个调用；
- `amazon-competition-difficulty-assessor` 锁相似产品前，需要剔除不可参照 ASIN 时调用；
- 入口日常口令「这竞品销量是真的吗 / 是不是刷的 / 他的打法我能复制吗」→ 经 `starsky-singlepoint-intake` 单点路由到本 Skill。

**干什么、干到哪为止**
1. 复用 `competitor_asin_pool.yaml` 与市场穿透报告已有数据，缺回评节奏 / 品牌词量 / 广告占比时按三级取数补。
2. 每个 ASIN 出「可作参照 / 仅证明市场存在 / 不可参照」；`不可参照` 的 ASIN 由 assessor 剔出分母，由 market-research 在 Top3 反向工程里标「不可复制」。
3. 回填 S1 综合报告：M10 竞品穿透（每个竞品加「信号判读」列）、M17 证据 `E-GRAY-001…`。不新开顶级模块。
4. 侵权判定只作线索，终裁归 `amazon-ip-risk-assessment`；不得替代 IP 硬闸门。

**产物落盘**（`templates/skill_folder_map.yaml`）
- `{产品根}/S1_选品板块/amazon-gray-tactic-detector/01B_竞品信号判读.md`
- `{产品根}/S1_选品板块/amazon-gray-tactic-detector/competitor_signal_flags.yaml`（ASIN → 信号 / 置信度 / 参照等级 / 证据 ID）

**Handoff 四标签**
```markdown
【本步完成】amazon-gray-tactic-detector
【下一步 Skill】amazon-competition-difficulty-assessor 或 amazon-market-research
【推荐口令】继续。按 competitor_signal_flags.yaml 剔除不可参照 ASIN 后做难度双法 / 回填 M10。
【MCP】used: <实际调用> / skipped: <原因> / not_required
```

未装插件时按本文独立运行。
