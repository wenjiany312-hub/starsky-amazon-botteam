# 消费者可见文案统一契约

> 版本：1.0.0　建立日期：2026-07-27
> 适用：**任何会被买家看到的文字**，不限于 Listing。
> 强制引用方：`amazon-listing-copywriter`、`amazon-listing-image-designer`、
> `amazon-product-video-director`、`amazon-visual-benchmark-learner`（学到的文案模式）、
> 以及任何后续产出图上文字 / 视频字幕 / A+ 文案的 Skill。

## 为什么建这份契约

实测事故（2026-07-27，run `2026-07-26_pack_test_v2`）：

`amazon-listing-copywriter` v3.8.0–v3.10.0 花了三轮修掉的病，**在 A+ 图上原样复发**：

| 位置 | 文案 | 病名 |
|---|---|---|
| 五点 B5（已修） | `fits naturally into everyday routines` | 高频低熵泛化 |
| **A+ M1 副标题（未修）** | `A simple place for everyday essentials.` | **同一句话的变体** |

**根因**：规则只装进了 Listing Skill，视觉 Skill 三个文件里「典型 / benefit / 高频低熵 / 泛化」命中数**全为 0**。
但 **A+ 图上的字、主图叠字、视频字幕，同样是消费者可见文案**，同样进 Amazon 索引，同样被买家读。

> **判定口径：只要买家能看到，就归本契约管。** 载体是文本框还是像素，不改变它是文案的事实。

---

## 一、四条硬规则（全载体通用）

### R1 · 禁止内部备注（源自 listing-copywriter v3.8.0 §十）

消费者可见的任何文字里**绝对禁止**出现：

稿件状态（this draft / 本稿）、确认状态（not confirmed / 未确认 / 待确认）、
内部流程（before upload / check the sample / 供应商文件 / 箱规）、
自我限定（without claiming / intentionally left out / 不声称）、
数据来源（supplied product facts）、闸门语言（final material gate / 待样品验收）。

**缺失的事实静默省略，不写"我们没确认"。**

### R2 · 典型性闸门（源自 Amazon COSMO 论文，listing-copywriter v3.10.0 §二）

Amazon 自己的知识流水线明确丢弃三类表述（COSMO §3.3.1）：

| 丢弃规则 | 图上文案的典型犯法 |
|---|---|
| 与品类名/标题**编辑距离过近** | `Clear Bathroom Jar`（就是标题的改写） |
| **高频低熵**泛化表述 | `A simple place for everyday essentials.` |
| 语义上只是**同义改写** | `Everyday organization, simplified.` |

判据不是"说得对"，是"**这是不是买这个品的主要理由**"。
COSMO Table 4 刻度：LLM 生成中 44.3% 合理，**只有 35.0% 典型**。

### R3 · benefit gate（源自 listing-copywriter v3.9.0 §十四）

每一处文案逐条问：**这句话让买家多知道了一件"对我有什么好处"的事吗？**

只描述外观、位置、尺寸 —— 是描述，不是卖点。

| ❌ | ✅ |
|---|---|
| `CLEAR BY DESIGN` / `A simple place for everyday essentials.` | `SEE WHEN YOU'RE RUNNING LOW` |
| `Bamboo lid` | `Bamboo lid keeps bathroom dust off` |
| `9.5 × 11.5 cm` | `Fits beside the faucet, not in front of it` |

### R4 · 禁写清单不连坐功能定义（源自 listing-copywriter v3.9.0 §十三）

**禁止声称某项性能 ≠ 禁止陈述该部件的本职功能。**

`airtight` 被禁，不代表「盖上后挡灰」不能写。
每条进禁写清单的词，必须同时判断其功能定义层能不能写，能写的要落到某处文案。

---

## 二、载体差异（各自要额外注意的）

| 载体 | 额外约束 |
|---|---|
| **主图** | 一律不叠字（Amazon 主图规则 + playbook `prohibited_elements`） |
| **副图叠字** | 字数比 Listing 更少 → **典型性要求更高**，一张图只承载一个卖点 |
| **A+ 模块** | 标题 + 副标题各自独立过 R2/R3；`M1 品牌横幅` 的副标题最容易写成空话 |
| **视频字幕 / 口播** | 加一条：**必须能被朗读**（与 Alexa 可朗读测试同源） |
| **视频首帧** | 首帧文字承担缩略图职责，必须是收益不是品类名 |

---

## 三、自检（出图/出片前执行，结果写进 run log）

```
逐条文案过四关：
  R1 内部备注命中数           必须 = 0
  R2 典型性（三类丢弃规则）    逐条判定，命中即重写
  R3 benefit gate             一张图/一个模块至少一处买家收益
  R4 禁写连坐检查             ⛔清单每词是否误封了功能定义
```

**输出要求**：run log 必须有「文案自检」一节，逐条列出图上文字原文 + 四关判定结果。
只写"已自检通过"不算。

---

## 四、与既有契约的关系

| 本契约 | 既有 | 关系 |
|---|---|---|
| R1–R4 | `amazon-listing-copywriter` §十/§二/§十三/§十四 | **同源**：本契约把它们从 Listing 提升为全载体通用 |
| 载体差异表 | 各视觉 Skill 的合规红线 | 并存：红线管能不能出现（Logo/认证/价格），本契约管**写得好不好** |
| 自检 | `visual_generation_run_log.md` | 新增一节，不替代既有验收表 |

—— 沙龙第二版｜作者坚哥｜建立于 2026-07-27 实测事故
