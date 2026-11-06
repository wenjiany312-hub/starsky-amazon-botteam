---
name: amazon-visual-benchmark-learner
description: >
  亚马逊类目视觉基准学习器（实战版）。
  用 Sorftime 找出类目内卖得好/卖得差的同款竞品，人工下载其主副图与页面 PDF，
  AI 按销量分层对比，拆出可执行的构图规律，写成 visual_playbook.yaml 供出图 Skill 消费。
  工作流程：(1) 类目筛样 + 分层抽样 → 下载清单；(2) 人工下载（含页面打印 PDF）；
  (3) 抽取 A+ 模块；(4) 分层对比学习；(5) 产出 visual_playbook.yaml + 学习报告。
  它不出图，只产出「这个类目该怎么拍」的规范。
  也可由以下表述触发：「学竞品图」、「视觉基准」、「学头部怎么拍」、「BSR 图片学习」、「出 visual_playbook」。
version: "1.2.0"
edition: starsky-v2
edition_label: 实战版
release_tag: "2026-08-01"
author: 坚哥
author_id: jiangge
brand:
  wechat_mp: 跨境者说干货
  knowledge_planet: 星空的跨境圈子
  series: Codex亚马逊实战
  ecosystem: codex
---

# 亚马逊类目视觉基准学习器 · 实战版

## 统一资料接入约束（00C，必读）

开始本 Skill 前，若当前产品 run 存在 00C 资料接入报告或 Overlay，必须先读它们，应用其中的可用来源、事实边界、合规约束与 MCP 补数计划。资料包内若已有 `视觉参考/` 目录及其索引层（`_下载清单.csv`、`_下载说明.md`、既有 `visual_playbook.yaml`），**先读索引层再决定是否重新采样**。读取 `../../templates/data_intake_read_contract.md` 作为权威规则。

## 出品与归属（实战版）

| 项 | 内容 |
|---|---|
| 发行 | **实战版**（**2026-08-01**） |
| 作者 | **坚哥** |
| 公众号 | **跨境者说干货** |
| 知识星球 | **星空的跨境圈子** |
| 价值 | **让"学头部怎么拍"从感觉变成可审计的规律** |

---


## ⛔ 主链默认不跑（2026-09-09）

S3 默认改走本品转化套图，见 `../_visual_suite_contract/SUITE_CONTRACT.md`。

只有用户把风格参考图放到 `S3_视觉板块/_投喂_风格参考/` 并明确说「学这些图的出图方案」时，才执行本 Skill。禁止自动抓 BSR / 新品榜 / 确认池竞品图。学到的只是出图方案，本品样子永远只认垫图。

## 定位：它只学，不出图

```text
本 Skill：类目里谁值得学 → 学到了什么规律 → 写成 visual_playbook.yaml
出图 Skill：读 visual_playbook.yaml + 本品垫图 → 出 Prompt / 出图
```

**不生成任何类目专属的出图 Skill。** 学到的是**类目资产**，载体是 `visual_playbook.yaml` 叠层；出图逻辑归 `amazon-listing-image-designer` / `amazon-premium-aplus-designer`。换类目只换 playbook，Core Skill 不动。

---

## 输入

- 类目 node_id 或类目名称（必需其一）
- 对标本品描述：材质、结构、尺寸、价格带（用于筛"真同款"与判断可迁移性）
- 已有样本目录（可选；有则跳过采样直接学习）
- 样本量偏好（默认 6，最少 3，建议 8-12）

无 node_id 时先用 `category_name_search` 解析。

---

## Phase 1：筛样与分层抽样

### 1.1 拉类目 Top100

```text
category_report(node_id) → top100_products
```

字段含 asin / title / monthly_sales_volume / price / star_rating / review_count / gross_profit_margin / category_rank。
**注意：`category_report` 不返回图片字段**，图必须靠 Phase 2 人工下载。

### 1.2 筛"真同款"

按标题关键词双重过滤：**品类词** ∩ **形态/材质词**。例：棉签罐 = `q-tip|cotton swab|cotton ball|apothecary` ∩ `clear|glass|acrylic|transparent`。

筛完不足 8 个时放宽形态词；仍不足则报告"样本不足"，不得用不同形态凑数。

### 1.3 分层抽样（**不取 Top20**）

直接取 Top20 只能学到"头部长什么样"，学不出因果。必须按三维分层：

| 维度 | 分层 | 目的 |
|---|---|---|
| 销量 | 高 / 中 / **低** | 对比"卖得好的和卖得差的差在哪" |
| 与本品同构性 | 同材质同结构优先 | 规律可直接迁移 |
| 毛利率 | 最高 / 最低 | 识别是否存在"另一套打法" |

**低销组必须占样本的 1/3 以上。** 反例比正例更能给出"不该做什么"，而这类结论不依赖因果证明。

### 1.4 产出下载清单

在资料包 `{类目}/视觉参考/benchmark_{日期}_{品名}/` 下建：

- `_下载清单.csv` — ASIN / 分组 / 抽样理由 / 排名 / 月销 / 售价 / 星级 / 评论 / 毛利率 / 品牌 / 属性标签 / 商品链接 / 标题 / 下载状态
- `_下载说明.md` — 下载规则与命名约定
- 每个 ASIN 一个**以 ASIN 命名的空目录**

> **目录名必须是 ASIN。** 这是学习阶段把图片回连到销量/毛利数据的唯一钥匙；目录名错了，这批图就只是一堆图。

---

## Phase 2：人工下载（本 Skill 的必要停点）

**这一步交给人，不要试图自动抓。** 原因：Sorftime `product_detail` 只返回 `main_image` 单张，副图与 A+ 都拿不到；人工还能筛掉误入的不同形态。

命名约定：

```text
{ASIN}/
├── 01_main.jpg       主图
├── 02_aux.jpg …      副图，按 Amazon 图库顺序编号（顺序本身是信息）
├── _page_print.pdf   ★ 商品页 Ctrl+P 另存为 PDF（A+ 从这里抽）
└── aplus_NN.jpg      由 Phase 3 自动生成，无需人工
```

**推荐做法：`Ctrl+P` 打印整页存 PDF**，比手动截图更准 —— A+ 顺序、模块边界完整保留，且将来还能抽 Q&A、评论摘要等其他内容。

⚠️ **Windows 路径长度**：Amazon 页面另存的 PDF 文件名可达 230 字符，加上目录会超过 MAX_PATH 260 导致读取失败。**存入后立即重命名为 `_page_print.pdf`**。

下完在 `_下载清单.csv` 的「下载状态」列改为 `已下载`；未下载的保持原值，学习阶段自动跳过。

---

## Phase 3：抽取 A+ 模块

从 `_page_print.pdf` 抽嵌入图，按尺寸判定：

| 宽度 | 判定 | 动作 |
|---|---|---|
| **940 – 3000 px** 且高 ≥300 | **A+ 模块**（970=标准 A+，1464=Premium A+） | ✅ 存为 `aplus_NN.jpg`，按页面顺序编号 |
| ≥3000（如 4454×2634） | 页面横幅/通用元素 | ✗ 排除 |
| 640×368 | 视频缩略图 | ✗ 排除 |
| 679×NNN | 商品主图缩略 | ✗ 排除（已有更高清的） |

```python
# 参考实现
import fitz
d = fitz.open(pdf); seen=set(); k=0
for pno in range(len(d)):
    for xref,*_ in d[pno].get_images(full=True):
        if xref in seen: continue
        seen.add(xref); i = d.extract_image(xref)
        if 940 <= i["width"] < 3000 and i["height"] >= 300:
            k += 1
            open(f"aplus_{k:02d}.jpg","wb").write(i["image"])
```

无 A+ 的 ASIN 抽出 0 张属正常，记录为 `aplus: 0`，它本身是一条观察数据。

---

## Phase 4：分层对比学习

### 4.1 必查的四组对比

| # | 对比 | 要回答 |
|---|---|---|
| 1 | **同构最高销 vs 同构最低销** | 材质结构相同时，表达差在哪（最干净的对比） |
| 2 | 类目第 1 vs 其余 | 头部的做法是否跨形态成立 |
| 3 | 最高毛利 vs 最低毛利 | 是否存在"高毛利款另一套打法" |
| 4 | 有 A+ vs 无 A+ | A+ 有无是否与销量相关 |

### 4.2 主图逐项拆解

背景 / 产品占比 / 视角与堆叠方式 / **内容物填充状态** / **内容物配色** / 是否叠字 / 是否含赠品 / 盖子或结构展示方式。

> 透明容器类目要特别注意：**产品本身无色，主图唯一色彩来源是内容物**。空置容器在 200px 缩略图上近乎不可见。

### 4.3 副图序与信息层级

记录每个 ASIN 的图序对应什么信息类型，找共同骨架。参考骨架（棉签罐类目实测）：

```text
看清楚（主图）→ 买到什么（包含清单）→ 多大（尺寸）→ 还送什么（赠品）
→ 什么做的（材质特写）→ 谁在用（真人）→ 放哪里（空间场景）
```

### 4.4 A+ 模块序

记录模块数、首屏规格、模块类型序列、场景模块与参数模块的视觉区分方式。

### 4.5 结论分级（强制）

每条结论必须标注置信度，**不得混写**：

| 标记 | 判据 |
|---|---|
| ✅ **已验证** | 全样本一致，或属"无论如何都不该做"的错误（不依赖因果证明） |
| 🔶 **假设** | 有梯度支持但存在反例或混淆变量，需上线后用真实数据验证 |
| ⬜ **待验** | 样本不足以判断 |

### 4.6 视觉风格档位判定（强制，v1.2.0 新增）

**这一步决定出图 Skill 走哪套模板。不判定 = 默认品牌风 = 学了等于没学。**

数样本副图：**多少张带参数标注 / 尺寸线 / 对比表 / 图标清单**，按比例定档：

| 档位 | 判据（样本副图里…） | 典型类目 |
|---|---|---|
| `editorial_dtc` | 带参数标注 **< 30%** | 服饰 · 家居 · 宠物服饰 · 美妆 —— 讲场景和情绪 |
| `hybrid` | **30%–60%** | 户外 · 运动 · 母婴 —— 前几张讲场景，后几张摆参数 |
| `spec_dense` | **> 60%** | 汽配 · 工具 · 电子 · 五金 —— 买家要的就是参数 |

**写进 playbook 必须带证据：**

```yaml
global:
  visual_profile: spec_dense          # ★ 必填，三选一
  visual_profile_evidence: "6 个样本 36 张副图中 25 张带参数标注/尺寸线（69%）"
  visual_profile_confidence: validated | hypothesis
```

> ⛔ **不许凭品类名拍脑袋。必须数样本。**
> 同是"宠物用品"，宠物雨衣是 `editorial_dtc`，宠物电剪很可能是 `spec_dense`。
>
> ⛔ **拿不准就报 `hybrid` + `hypothesis`**，别硬套 `editorial_dtc` ——
> 那个默认值的代价是下游全程走品牌风。

---

## Phase 5：产出

### 5.1 `S3_视觉板块/amazon-visual-benchmark-learner/visual_playbook.yaml`

严格遵循 `templates/visual_playbook.example.yaml` 字段，并**额外补齐**：

```yaml
confidence: hypothesis | validated
sample_size: N
source_references:
  refs_main:
    - {asin, monthly_sales, unit_price_usd, margin_pct, review_count, structure, images, aplus, role}
  confounders_not_controlled: []      # ★ 必填
validation:
  status: draft
  method_when_validated: ""           # 如何用真实数据验证
  known_limitations: []               # ★ 必填
  next_action: ""
```

每条规律带 `evidence`（来自哪几个 ASIN 的第几张图）与 `status`。

### 5.2 `S3_视觉板块/amazon-visual-benchmark-learner/视觉学习报告.md`

必含章节：样本全貌表 / 核心结论 / 分组对比 / A+ 模块 / **对本品的行动清单（带置信度与成本）** / **局限与混淆变量** / 合规边界。

---


## ⛔ 消费者可见文案统一契约（v1.1.0 新增 · 必读）

**执行前必读 `../../templates/consumer_visible_copy_contract.md`。**

实测事故（2026-07-27）：`amazon-listing-copywriter` 三轮修掉的「高频低熵泛化」在 **A+ M1 副标题上原样复发** —— 五点里被判掉的 `fits naturally into everyday routines`，在图上变成了 `A simple place for everyday essentials.`。三个视觉 Skill 里「典型/benefit/高频低熵/泛化」命中数**全为 0**。

> **判定口径：只要买家能看到，就归该契约管。** 载体是文本框还是像素，不改变它是文案的事实。图上的字、A+ 模块文案、视频字幕与首帧文字，全部适用。

四条硬规则：**R1** 禁内部备注（缺失事实静默省略）｜**R2** 典型性闸门（丢弃「与品类名近重复 / 高频低熵 / 同义改写」三类）｜**R3** benefit gate（描述 ≠ 卖点）｜**R4** 禁写清单不连坐功能定义（禁 `airtight` ≠ 禁「盖上挡灰」）。

**副图与 A+ 字数比 Listing 少，典型性要求反而更高** —— 一张图只承载一个卖点。

**自检强制写进 run log**：逐条列出图上文字原文 + 四关判定结果，只写"已自检通过"不算。

---
## 硬规则

1. **不出图。** 本 Skill 的产物是规范，不是图片。出图走 `amazon-listing-image-designer`。
2. **不生成类目专属 Skill。** 学到的是类目资产，载体是 playbook 叠层。
3. **必须声明混淆变量。** 销量差异常由单价、评论数、上架时间解释，与图片无关。**不得写成"这么拍就能卖 N 件"**。任何未声明混淆变量的报告视为不合格。
4. **竞品图只学表达。** 不得复制进本品垫图，不得提取 LOGO、品牌名、包装、独特字体。实际出图必须绑定本品 `S3_视觉板块/amazon-listing-image-designer/产品垫图/asset_manifest.yaml`。
5. **样本不足如实报告。** n<3 不出 playbook；n<6 时 `confidence` 必须为 `hypothesis`。
6. **不改写资料包源目录以外的既有 playbook。** 已有 playbook 时生成新版本并写差异，不覆盖旧证据。
7. **价格带不匹配的打法不迁移。** 高毛利装饰款的视觉路线不适用于走量款，反之亦然；发现此类差异必须显式提示。

---

## 常见陷阱

| 陷阱 | 后果 | 规避 |
|---|---|---|
| 直接取 Top20 | 只有正例，学不出"不该做什么" | 分层抽样，低销组 ≥1/3 |
| 把销量差归因于图片 | 伪因果，照做反而危险 | 强制声明单价/评论/上架时间 |
| 目录名不用 ASIN | 图片无法回连销量数据 | Phase 1 建目录时锁定 |
| 手动截图 A+ | 顺序错乱、边界丢失 | 存整页 PDF，脚本抽 |
| PDF 原名不改 | Windows MAX_PATH 超限读不了 | 立即改名 `_page_print.pdf` |
| 学完就出图 | 缺本品垫图，只能出空壳 | 出图前确认 `asset_manifest.yaml` 存在 |

---

## MCP

| 工具 | 用途 | 必需 |
|---|---|---|
| `sorftime-mcp.category_name_search` | 类目名 → node_id | 无 node_id 时必需 |
| `sorftime-mcp.category_report` | Top100 + 销量/毛利/评论 | ✅ 必需 |
| `sorftime-mcp.product_detail` | 补单个 ASIN 的 `main_image`、`a_plus`、`package_size_cm` | 可选 |

`product_detail` 只返回主图单张，**不能替代人工下载**。

---

## 输出

- `{类目}/视觉参考/benchmark_{日期}_{品名}/_下载清单.csv` + `_下载说明.md` + 各 ASIN 目录（写在资料包，供复用）
- `S3_视觉板块/amazon-visual-benchmark-learner/visual_playbook.yaml`
- `S3_视觉板块/amazon-visual-benchmark-learner/视觉学习报告.md`
- next：`amazon-listing-image-designer`（吃同一 playbook 出 7 图）→ `amazon-premium-aplus-designer`

### Handoff

| 版本 | 说明 |
|---|---|
| 1.0.0 | 首版。分层抽样 + 人工下载 + PDF 抽 A+ + 分层对比 + 置信度分级；棉签罐类目 6 样本实测验证 |

```markdown
【本步完成】amazon-visual-benchmark-learner
【样本】{N} 个 ASIN / {M} 张图（主副图 {x} + A+ {y}）；销量跨度 {min}–{max}
【已验证结论】{全样本一致的规律}
【假设结论】{需上线验证的规律及验证方法}
【产物】S3_视觉板块/amazon-visual-benchmark-learner/visual_playbook.yaml（confidence: {validated|hypothesis}）+ S3_视觉板块/amazon-visual-benchmark-learner/视觉学习报告.md
【局限】{混淆变量与样本缺口}
【下一步 Skill】amazon-listing-image-designer
【推荐口令】读取 S3_视觉板块/amazon-visual-benchmark-learner/visual_playbook.yaml 与 S3_视觉板块/amazon-listing-image-designer/产品垫图/asset_manifest.yaml，出主副图 7 图 Prompt；每图绑定本品 asset_id，不得使用竞品素材。
【MCP】used: sorftime-mcp（category_report / category_name_search）
```

—— Codex 实战工作流｜实战版｜作者坚哥｜跨境者说干货 · 星空的跨境圈子

---

## ⭐ 学习结果必须落成 playbook（2026-07-30 强制）

**实测教训**：2026-07-28 那轮学了 6 个竞品、写了 962 字结论到 `08_主副图方案.md`，
**但没产出 `visual_playbook.yaml`** —— 出图 Skill 读不到，全程仍用 DTC 出厂风格。
**学习结果没覆盖任何东西，等于白学。**

```
⛔ 本 Skill 的唯一交付物是 S3_视觉板块/amazon-visual-benchmark-learner/visual_playbook.yaml
⛔ 只写 md 结论不写 playbook = 未完成
```

字段规范：`templates/visual_playbook_schema.yaml`

### 除了「信息顺序」，必须提取「版式语言」

老版只学「先讲什么后讲什么」，**不够**。同一个信息用图标条讲、用场景块讲、
用对比图讲，出来的效果完全不同。

**七种版式必须逐个判定本类目头部用了哪些、用在第几屏**：

| pattern | 形态 | 判定要点 |
|---|---|---|
| `icon_strip` | 一行 N 个极简图标 + 短标题 | 几个图标/行、标题几个词 |
| `scene_block` | 大图 + 手写体标题 + 2–3 行说明 | 几块、标题句长 |
| `spec_compare` | 同产品 N 个状态并排 | 几档、怎么标注差异 |
| `detail_grid` | 圆形裁切细节矩阵 + 规格标签 | 几格、标签格式 |
| `checklist` | 标题 + N 条 ✓ 短句 | 几条、每条几词 |
| `quantity_structure` | 大数字 + 构成拆解（6×5=30）| 套装品必查 |
| `color_matrix` | N 个颜色变体 + 色名 | 多色品必查 |

⭐ **每个 pattern 要写出 `prompt_fragment`** —— 写不成 prompt 的观察不要放进 playbook。

### 视觉参数要给具体值，不要形容词

```
❌ "信息密度高"           → 出图用不上
✅ info_points_per_module: 5   字号层级 3 级   每块 ≤ 9 词
```

### 本地化差异要单独记

**同一套工作流，不同站点表达完全不同** —— 不是翻译，是重新组织阅读顺序与信息密度。
实测标杆：同一家的美国站万圣节 / 美国站圣诞 / 日本站和服头饰，三套图信息组织各不相同。

### ⛔ 收尾自检：overrides_default_style 至少 3 条

playbook 末尾必须写清「覆盖了出厂风格的哪几项」：
```yaml
overrides_default_style:
  - field: 副图版式
    from: "DTC 默认：单图 + 一句卖点"
    to: "icon_strip 6 图标 + scene_block 3 块"
    evidence: "B0CPYNF77V 第 2/3 段"
```

**这一节为空 = 学习没产生任何影响。出图前必须检查。**
