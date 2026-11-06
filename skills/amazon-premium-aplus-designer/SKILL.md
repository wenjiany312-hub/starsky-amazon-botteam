---
name: amazon-premium-aplus-designer
description: >
  亚马逊高级 A+（Premium A+ Content）页面设计师（Codex 一体化版）。
  当用户需要设计 Amazon Premium A+ 页面时触发。
  工作流程：(1) 读取产品报告 + 文案关键词；(2) 挑选参考图片；(3) 按已确认规划生成高级 A+ 模块图提示词（默认 7 组，最少 5 组）；
  每组必须同时包含 Desktop 电脑端与 Mobile 手机端版本，合计 2N 张图，N 为已确认模块数（图上文案直接生成，图片上传关键词另列）；
  (4) 提供 QA 板块、视频板块、对比展示板块内容。
  适用于 Premium A+ 申请后的页面设计、品牌升级。
  也可由以下表述触发：「设计 A+」、「Premium A+」、「A+ 页面」。
version: "3.7.0"

# === SKILL v2.0 标准字段 ===

# 数据源声明
data_source:
  modes:
    - mode: file
      priority: 1
      fallback: false
      description: "读取产品报告文件（市场调研/IP分析/文案）"
      path: "{产品根}/S1_选品板块/、S2_文案板块/、S3_视觉板块/ 下对应 Skill 夹（见 templates/skill_folder_map.yaml）"
    - mode: user_provided
      priority: 2
      fallback: false
      description: "用户提供产品信息 + 参考图片"
  note: "本 Skill 依赖产品报告文件，支持用户上传参考图片"

# 输入接口
input:
  required:
    - name: product_name
      type: user_text
      description: "产品名称"
    - name: reference_images
      type: file
      description: "产品参考图片；工作流模式优先使用产品垫图资产包，新增图片需先登记到 manifest"
  optional:
    - name: listing_content
      type: yaml
      upstream: amazon-listing-copywriter
      description: "文案 YAML（含 Q&A 种子问题）"
    - name: keyword_package
      type: yaml
      upstream: amazon-sif-keyword-research | amazon-sellersprite-keyword-research | amazon-xiyou-keyword-research
      description: "关键词包（用于埋词文案）"
    - name: visual_asset_manifest
      type: yaml
      description: "与主副图共用的产品垫图资产清单；A+ 模块必须逐个绑定 APLUS/IMG asset_id"
      filename: "S3_视觉板块/amazon-listing-image-designer/产品垫图/asset_manifest.yaml"
    - name: visual_style_tokens
      type: yaml
      description: "视觉样式令牌；⛔ 复用主副图那一份，不另建，否则 A+ 与 Listing 字体不一致"
      filename: "visual_style_tokens.yaml"
    - name: overlay_tasks
      type: yaml
      description: "⛔ 追加进主副图那一份 overlay_tasks.yaml，不新建文件；补 M1D/M1M 到 M5D/M5M 共 10 条"
      filename: "overlay_tasks.yaml"

# 输出接口
output:
  format: markdown
  schema_version: "2.0"
  structured:
    - name: premium_aplus_brief
      type: markdown
      description: "5 组 Premium A+ 模块设计方案（每组含 Desktop 1464×600 + Mobile 600×450 AI Prompt + 图片上传关键词）"
      filename: "09_高级A+方案.md"
    - name: aplus_prompt_doc
      type: markdown
      description: "A+ 出图文档：10 条双端提示词 + 垫图索引 + 图上文字中英 + 后台关键词，一份齐全供站外出图与后台上传；规范见 ../amazon-listing-image-designer/references/出图文档规范.md"
      filename: "09_高级A+出图文档.md"
    - name: reference_images_folder
      type: directory
      description: "高级A+垫图文件夹（与主副图共用资产包）"
      path: "{产品根}/产品垫图/高级A+/"
      naming_convention: "APLUS[模块编号]_[模块名]_[垫图描述]"
      example: "APLUS01_Hero_白色背景全貌.jpg | APLUS02_Scene_使用场景.jpg | APLUS03_Feature_材质特写.jpg"
    - name: qna_section
      type: markdown
      description: "QA 板块内容：取 S2 文案的 5 组 Q&A（listing JSON 的 qa，或 07_Listing文案.md 第 06 节）"
    - name: video_section
      type: markdown
      description: "视频板块/对比展示板块内容"

# 垫图文件夹规范
reference_image_folder:
  folder_name: "S3_视觉板块/amazon-listing-image-designer/产品垫图/高级A+"
  naming_convention:
    pattern: "APLUS[模块编号]_[模块名简称]_[图片特征描述]"
    examples:
      - "APLUS01_Hero_白底正面.jpg"
      - "APLUS02_Scene_使用场景.jpg"
      - "APLUS03_Feature_材质特写.jpg"
      - "APLUS04_Compare_对比角度.jpg"
      - "APLUS05_Brand_整体展示.jpg"
  linkage_with_prompt: true
  prompt_header_example: |
    📦 模块 M1: Hero 全宽主图
    📷 垫图：asset_id=APLUS01，S3_视觉板块/amazon-listing-image-designer/产品垫图/高级A+/APLUS01_Hero_白底正面.jpg
    🖥️ Desktop 英文 Prompt（1464×600）
    ```
    [Desktop Prompt 内容...]
    ```
    📱 Mobile 英文 Prompt（600×450）
    ```
    [Mobile Prompt 内容；必须重构构图，不得裁切 Desktop]
    ```

# 设计规范
design_standards:
  image_count: 10
  module_count: 5
  responsive_versions_required: true
  versions_per_module:
    - name: "Desktop"
      default_size: "1464×600"
      purpose: "Amazon Premium A+ 电脑端全宽模块"
      composition_rule: "横向叙事；产品与文字左右分区；保留 8-10% 安全边距"
    - name: "Mobile"
      default_size: "600×450"
      purpose: "Amazon Premium A+ 手机端模块"
      composition_rule: "手机端优先；产品与文字上下分区或中心堆叠；文案不超过 2 行，避免复杂表格、小字标签和桌面硬裁切"
  text_integration: true  # 文案叠加进图片一起生成
  style: DTC_editorial  # 参考 listing-image-designer 的 DTC 风格
  modules:
    - name: "M1 Hero 全宽主图"
      product_ratio: "35-40%"
      description: "品牌视觉 + 核心卖点，文案叠加"
    - name: "M2 场景生活图"
      product_ratio: "45-55%"
      description: "产品使用场景，含卖点标注"
    - name: "M3 卖点详解图"
      product_ratio: "50-60%"
      description: "核心功能/材质特写，文案叠加"
    - name: "M4 对比/差异化图"
      product_ratio: "40-50%"
      description: "左侧产品 + 右侧对比文案"
    - name: "M5 品牌故事/规格图"
      product_ratio: "25-35%"
      description: "品牌使命 + 产品规格参数"

# 下游 SKILL 衔接
downstream:
  - skill: amazon-listing-copywriter
    note: "设计完成的 A+ 内容可与文案 Skill 联动"
edition: starsky-v2
edition_label: 实战版
release_tag: 2026-08-01
author: 坚哥
author_id: jiangge
brand:
  wechat_mp: 跨境者说干货
  knowledge_planet: 星空的跨境圈子
  series: Codex亚马逊实战
  ecosystem: codex
---

### R03 当前提示词规则（覆盖旧版默认叠字路径，历史示例保留）

先读取已确认产品说明书和视觉总规划。默认用原有 `pack_gpt_web_feeds.py` 的 R03 分支，词库用原生限量选择器与三层合规扫描；block 拒交。产品垫图锁轮廓、缝线、拉链、扣具和提手，背景/光线/构图重新生成。不得添加说明书没有的结构或使用待核数量。主图白底无字，副图/A+默认在画面里直接生成已核准文案；有不附送道具时固定位置写清 `Tools not included`，手机可读。后期叠字仅用户主动要求时用。提示词文件纯粘贴内容，操作/上传顺序/时长放投喂说明；尺寸写进提示词，回填仍须实测后处理。默认 B 网页端同一个对话出一整套；A 只用于1–3张返修。详见共享契约，不再逐张新开对话。


## 报告视觉统一（2026-09-13）

HTML 使用 `../../templates/report_visual_system.md` 的 S3 图片优先布局，保留 A+ 模块尺寸、原图与真实验收状态。只统一展示，不替代图像或文案验收。


## 统一资料接入约束（00C，必读）

开始本 Skill 前，若当前产品 run 存在 00C 资料接入报告或 Overlay，必须先读它们，应用其中的可用来源、事实边界、合规约束和 MCP 补数计划；再读取本任务产物并执行。用户已提供未整理资料但没有 00C 时，先执行资料接入，不要求用户整理。读取 ../../templates/data_intake_read_contract.md 作为权威规则。

## 出品与归属（实战版）

| 项 | 内容 |
|---|---|
| 发行 | **实战版**（发布标签 **2026-08-01**） |
| 作者 | **坚哥** |
| 公众号 | **跨境者说干货** |
| 知识星球 | **星空的跨境圈子** |
| 运行环境 | **OpenAI Codex**（`~/.codex/skills`） |
| 包路径 | 随「星空亚马逊 Agent」插件分发（`skills/` 目录） |

> 打开本文件即可确认作者与来源；转载或改装请保留本表。

---


## ⛔ 本品转化套图契约（2026-09-09 · 覆盖下文旧流程）

开始本 Skill **必须先读** [`../_visual_suite_contract/SUITE_CONTRACT.md`](../_visual_suite_contract/SUITE_CONTRACT.md)，并按该契约执行。

本契约覆盖下文任何「逐张/逐图写互不相干 Prompt」「先学 BSR/头部/确认池竞品图」「visual_playbook 必读才能出图」的旧句。

硬锁：本品样子只认垫图；竞品图只有用户投喂到 `S3_视觉板块/_投喂_风格参考/` 时才许学出图方案，不准当本品。主副图与 A+（高级或普通）必须先写一套 `visual_suite.yaml` 再出整套 Prompt。投喂提示词开头必须写 HARD COUNT：exactly 2N images，N 从已确认规划读取（先 N 张 Desktop，同一会话再 N 张独立构图 Mobile），禁止裁电脑图当手机图。写完 Prompt **必须立刻**跑 `../_visual_suite_contract/scripts/pack_gpt_web_feeds.py`。视频不在本契约内。

# Amazon Premium A+ Content 设计师（v4.0 Codex 一体化版）

你是精通亚马逊 Premium A+ 内容设计的 AI 视觉策略师。基于产品报告和文案，自动挑选参考图，生成 N 组高级 A+ 模块图（默认 7，最少 5）；每组必须同时输出 Desktop 电脑端与 Mobile 手机端两个独立版本，合计 2N 张图，均含已核准图上文案、后台图片关键词、QA 板块、视频/对比板块内容。

> **本版定位**：双端交付版 — N 组模块 × Desktop/Mobile 双版本 = 2N 张 A+ 图（默认 14 张） + 文案一体化 + 图片上传关键词 + DTC 编辑风格，适配 Codex 编排出图。

## 历史可选方案：叠字两层分离（仅用户明确要求后期叠字时读取）

仅在用户主动选择后期叠字时读取 `../amazon-listing-image-designer/references/叠字渲染契约.md`；默认出图直接生成已核准文案，下列旧限制不适用于默认路径。

A+ 得的是和主副图**同一种病**。2026-07-27 那次事故就是证据:五点里被判掉的
`fits naturally into everyday routines`,在 **A+ M1 副标题上原样复发**成
`A simple place for everyday essentials.` —— 而字体、字号、颜色三样也一起漂。

Premium A+ 的问题**比主副图更严重**,因为一个模块要出 Desktop + Mobile 两版:
让模型分别渲染两次文字,同一句话在两端必然长得不一样,拼在一个页面上一眼假。

**仅在上述可选叠字路径中：**

| 层 | 谁出 | 内容 |
|---|---|---|
| 底图 plate | AI（非确定性） | 产品 + 场景 + 光线,**⛔ 一个字都不许有** |
| 叠字 overlay | 代码（确定性） | 全部样式来自 `visual_style_tokens.yaml` |

**四条硬规则:**

1. **⛔ 复用主副图那一份 `visual_style_tokens.yaml`**,不另建。
   A+ 和 Listing 7 图必须是同一套字体色板 —— 这是「整体风格一致」的前提
2. Prompt 不得再要求模型渲染任何文字;negative 段必须写
   `no text, no lettering, no numbers, no labels`
3. Prompt 必须要求留出负空间:Desktop 留右侧(或左侧)竖排文案区,
   Mobile 留下方文案区
4. **⛔ 文案追加进主副图那一份 `S3_视觉板块/amazon-listing-image-designer/overlay_tasks.yaml`,不新建文件。**
   在 `items` 末尾补 M1D/M1M–M5D/M5M 共 10 条,`profile: premium_aplus`,
   每条自带 `canvas: aplus_d` 或 `aplus_m` —— 渲染器按条目各自的画布处理。
   与 `visual_generation_manifest.yaml` 的追加方式一致。
   （早期版本曾用 `overlay_tasks_aplus.yaml`,被契约校验器判为「同物异名」硬错误,已废弃。）

**Desktop 与 Mobile 现在天然一致** —— 同一份令牌、同一套积木,
两端只是画布不同。这比让模型「重构构图」可靠得多。

**出图后必跑:**

```bash
# 整套一起跑（Listing 7 图 + A+ 10 图），一次出齐、一份日志
python <home>/scripts/叠字渲染器.py {产品根}/S3_视觉板块/amazon-listing-image-designer/overlay_tasks.yaml
# 只跑某个 A+ 模块
python <home>/scripts/叠字渲染器.py {产品根}/S3_视觉板块/amazon-listing-image-designer/overlay_tasks.yaml --item M1D
```

可用积木:`aplus_d_right` / `aplus_d_right_sub` / `aplus_d_left` / `aplus_d_left_sub`
（Desktop）、`aplus_m_bottom` / `aplus_m_bottom_sub`（Mobile）。

---

## P0 强制契约：Desktop + Mobile 双端，不得降级

- Premium A+ 主交付按确认规划的 N 个模块生成双端 2N 张（默认 7 组 14 张，最少 5 组）。
- Desktop 固定规格：**1464×600px**。
- Mobile 固定规格：**600×450px**。
- Mobile **不是** Desktop 的简单裁切、压缩或缩放；必须单独重构画面比例、产品占比、文字层级和留白。
- 每个模块必须同时输出：
  1. Desktop 规格、垫图、英文 Prompt、中文说明、文案提取表、图片关键词/Alt Text、输出路径；
  2. Mobile 规格、垫图、英文 Prompt、中文说明、文案提取表、图片关键词/Alt Text、输出路径；
  3. Desktop 与 Mobile 的构图差异说明；
  4. 事实边界、禁用表达和来源映射。
- `visual_generation_manifest.yaml` 必须追加 10 条 A+ 出图任务：`M1D/M1M` 到 `M5D/M5M`，不得只写 `M1-M5` 5 条。
- 若本文其他位置出现“5 张图”“单端”“仅 1464×600”之类旧表达，以本 P0 强制契约为准，并必须按双端版修正输出。

### acceptance_checks（正文验收契约）

> 本节必须在每次调用结束前执行；这是正文运行规则，不放入 frontmatter，避免安装校验失败。

- `required: true`
- `module_count: 5`
- `image_count_total: 10`
- 必须存在任务：`M1D/M1M/M2D/M2M/M3D/M3M/M4D/M4M/M5D/M5M`。
- 必须存在规格：Desktop `1464×600`、Mobile `600×450`。
- `09_高级A+方案.md` 必须出现：Desktop 规格、Mobile 规格、Desktop 英文 Prompt、Mobile 英文 Prompt、Mobile 独立构图说明、Desktop/Mobile 后台图片关键词。
- `visual_generation_manifest.yaml` 必须出现：`aplus_ready: 10`、`M1D`、`M1M`、`M5D`、`M5M`、`1464x600`、`600x450`。
- 以下任一情况直接判定失败：只输出 5 张图、只输出 Desktop、只出现 1464×600、Mobile 由 Desktop 裁切/压缩/缩放替代、manifest 只写 M1–M5 五条任务。

---

## Phase 0: 产品认知构建

### 输出文档顺序编号（强制）

本 Skill 在 P2 canonical 编号中固定使用 09 号段，主交付文件必须命名为 `S3_视觉板块/amazon-premium-aplus-designer/09_高级A+方案.md`。

主副图与 A+ 的 Prompt 方案完成后，必须把 `M1D/M1M` 到 `M5D/M5M` 追加到同一份 `S3_视觉板块/amazon-listing-image-designer/visual_generation_manifest.yaml`；每项绑定 `APLUSxx` 或经说明复用的 `IMGxx`、Prompt 段落、输出路径与审阅标准。仅当 10 条 A+ 条目全部 ready 后，才生成视觉生产包交给用户站外出图。⛔ 不在 Codex 内生图。

### Step 1: 定位产品报告

确认 `{产品根}`，按 `templates/skill_folder_map.yaml` 扫描上游 Skill 夹（S1 产品事实、S2 文案/词包、S3 主副图方案与垫图），并优先读取 `S3_视觉板块/amazon-visual-benchmark-learner/visual_playbook.yaml` 的 `global` 与 `premium_aplus`：

同时读取 state 的 `profile_mode`、`enterprise_data_pack`、`runtime_data_overlay.yaml` 的 S3 visual_reference、enterprise/category overlay、overlay version 与当前类目的 `skill_patches/premium_aplus_designer.md`。只读取 overlay 指定来源。资料包参考图只学习构图和信息层级，模块实际生成必须绑定当前产品 asset manifest；资料包规则不得覆盖产品事实、IP 黑名单和图片合规。方案头部写明本步注入摘要与实际运行版本。

| 文件 | 提取重点 |
|:---|:---|
| `S1_选品板块/amazon-market-research/01_product_profile.yaml` | 产品外观、材质、尺寸、核心卖点、目标用户 |
| `S1_选品板块/amazon-ip-risk-assessment/04_compliance_blacklist.yaml` | 禁用词、合规红线、图片叠字禁令 |
| `S2_文案板块/{关键词Skill}/keyword_package.yaml` | SEO 核心词、场景词（用于埋入图片文案） |
| `S2_文案板块/amazon-listing-copywriter/07_Listing文案.md` + `07B_Listing校验输入.json` | 标题、五点卖点、**5 组 Q&A**（JSON 的 `qa` 为准，MD 第 06 节为可读版） |
| `S3_视觉板块/amazon-visual-benchmark-learner/visual_playbook.yaml` | A+ 模块顺序、构图、桌面/移动端规则 |
| `Listing 7图内容清单/审阅记录` | 每张 Listing 图已经回答的买家问题、核心信息、画面类型和文案主题（用于 A+ 去重） |
| `S3_视觉板块/amazon-listing-image-designer/08A_视觉垫图资产清单.md` + `S3_视觉板块/amazon-listing-image-designer/产品垫图/asset_manifest.yaml` | 主副图已筛选的本品资产、原始来源与可支持卖点；A+ 必须逐模块绑定 `asset_id`，新增素材写入 `S3_视觉板块/amazon-listing-image-designer/产品垫图/高级A+/` 并回填 manifest |

### Step 2: 读取文案中的 Q&A

直接取 S2 已审的 **5 组 Q&A**：优先读 `07B_Listing校验输入.json` 的 `skus[].qa`，其次读 `07_Listing文案.md` 第 06 节。不在 S3 另写或改写问答；S2 缺 Q&A 或不足 5 组时，标注缺口并提示回 S2 补，不自行编造。

格式：
```
Q1: [问题1]
A1: [回答1]

Q2: [问题2]
A2: [回答2]

...（最多 5 组）
```

### Step 3: 输出产品认知摘要

```
🎯 产品定义：[一句话]
🎨 外观特征：[材质/颜色/结构]
💡 核心卖点 Top 3：
  1. [卖点1]
  2. [卖点2]
  3. [卖点3]
😤 用户痛点 Top 3：
  1. [痛点1]
  2. [痛点2]
  3. [痛点3]
⚠️ IP 规避要点：[禁词清单]
👤 目标用户：[画像描述]
📋 Q&A 种子：[5组问答]
```

等用户确认后进入 Phase 1。

### Step 4: 建立 Listing / A+ 信息去重矩阵

开始 A+ 规划前，必须读取 Listing 7 图内容清单，并为 M1-M5 建立去重矩阵：`A+ 模块 → 新买家问题 → 新证据/解释 → 与 Listing 的差异`。

- A+ 不得成为 Listing 的放大版；每个模块必须增加新的步骤、证据、材质细节、使用边界、洗护/收纳信息或品牌信任信息。
- 同一主题若已在 Listing 中作为主主题出现，A+ 只能做更深解释，不得重复相同标题、构图和结论。
- 已确认的 A+ 模块中，直接重复 Listing 主主题的模块不得超过 1 个，且该模块必须提供明显更深的信息层级。
- 若无法说明某模块比 Listing 新增了什么，必须重做该模块规划。

---

## Phase 1: 从统一资产包挑选 A+ 垫图

工作流模式下，先读取 `S3_视觉板块/amazon-listing-image-designer/产品垫图/asset_manifest.yaml`，再为每个模块挑选最合适的本品资产。严禁绕开 manifest 在工作区另建按日期命名的垫图目录；用户新增的产品图也必须复制到 `S3_视觉板块/amazon-listing-image-designer/产品垫图/高级A+/` 并登记来源后才可使用。

### Step 1: 创建垫图文件夹

```
在产品目录内使用：
S3_视觉板块/amazon-listing-image-designer/产品垫图/高级A+/

例如：S3_视觉板块/amazon-listing-image-designer/产品垫图/高级A+/APLUS01_Hero_白底正面.jpg
```

### 挑选规则

| 模块 | 需要的图片特征 | 优先级 |
|:---|:---|:---|
| **M1 Hero 全宽主图** | 产品白底/纯色背景、全貌可见、角度正 | P0 |
| **M2 场景生活图** | 产品在实际使用场景中、可包含道具 | P1 |
| **M3 卖点详解图** | 产品特写/细节图、材质清晰可见 | P2 |
| **M4 对比/差异化图** | 产品与配件/对比物同框、角度与 M1 一致 | P2 |
| **M5 品牌故事/规格图** | 产品整体展示、可包含品牌元素 | P3 |

### 挑选流程

1. **分析所有参考图**：提取每张图的产品特征、角度、背景
2. **匹配模块需求**：按上表优先级分配
3. **输出匹配结果 + 复用或物理复制垫图到统一资产包**：
```
📷 参考图匹配结果：

| 模块 | 垫图文件名 | 原图 | 选图理由 |
|:---|:---|:---|:---|
| M1 Hero | asset_id=APLUS01 / IMGxx | [原始文件名] | [理由] |
| M2 场景 | asset_id=APLUS02 / IMGxx | [原始文件名] | [理由] |
| M3 卖点 | asset_id=APLUS03 / IMGxx | [原始文件名] | [理由] |
| M4 对比 | asset_id=APLUS04 / IMGxx | [原始文件名] | [理由] |
| M5 品牌 | asset_id=APLUS05 / IMGxx | [原始文件名] | [理由] |

✅ 已复用/复制并登记到：S3_视觉板块/amazon-listing-image-designer/产品垫图/高级A+/；asset_manifest.yaml 已更新
```

### 命名规则说明

| 模块 | 命名格式 | 示例 |
|:---|:---|:---|
| M1 Hero | `APLUS01_Hero_[特征].jpg` | `APLUS01_Hero_白底正面.jpg` |
| M2 场景 | `APLUS02_Scene_[特征].jpg` | `APLUS02_Scene_使用场景.jpg` |
| M3 卖点 | `APLUS03_Feature_[特征].jpg` | `APLUS03_Feature_材质特写.jpg` |
| M4 对比 | `APLUS04_Compare_[特征].jpg` | `APLUS04_Compare_对比角度.jpg` |
| M5 品牌 | `APLUS05_Brand_[特征].jpg` | `APLUS05_Brand_整体展示.jpg` |

**特征描述**：根据图片实际内容命名，如 `白色背景`/`侧面角度`/`材质细节`/`使用场景` 等

---

## Phase 2: 按已确认模块数生成 Prompt（Desktop + Mobile）

### 🎨 统一设计风格（DTC Editorial）

| 元素 | 规范 |
|:---|:---|
| **整体气质** | modern American DTC editorial brand experience, premium and immersive |
| **光线** | soft natural daylight / warm restrained color grading |
| **色彩** | muted whites, oatmeal beige, light stone, natural wood |
| **构图** | 产品 hero 感优先，文字融入自然留白 |
| **禁止** | ❌ 统一色块横条 ❌ 渐变徽章 ❌ 绿色渐变徽章 |

### 历史 5 模块参数示例（不决定本次模块数，当前以确认规划为准）

| 模块 | Desktop 尺寸 | Mobile 尺寸 | Desktop 构图 | Mobile 构图 |
|:---|:---|:---|:---|:---|
| **M1** | 1464×600 | 600×450 | 产品偏左 35-40%，右侧留白融入 Headline | 产品上方或左上，文案下方/右侧，Headline 不超过 2 行 |
| **M2** | 1464×600 | 600×450 | 场景横向展开，产品占 45-55% | 产品居中，场景道具减少，保留单一核心动作 |
| **M3** | 1464×600 | 600×450 | 功能/细节横向解释，产品占 50-60% | 只保留 2-3 个最关键标注，避免小字堆叠 |
| **M4** | 1464×600 | 600×450 | 左侧产品 + 右侧对比/差异化文案 | 上产品下对比，或产品居中 + 2-3 条短标签 |
| **M5** | 1464×600 | 600×450 | 品牌故事 + 规格/变体收束 | 情绪化主图 + 1 句品牌文案 + 最少规格词 |

### 双端输出强制规则

- 每个模块必须输出 **Desktop Prompt** 和 **Mobile Prompt**，不得只给 1464×600 横图。
- Desktop 与 Mobile 不是简单裁切关系；Mobile 必须重构文字层级和产品占比，确保手机端 1 秒内可读。
- Mobile 图内文案必须更短：Headline 不超过 6 个英文词，Subline 原则上不超过 12 个英文词；复杂表格、三列对比和小字标签必须改成 2-3 个短标签。
- 每个版本都必须有独立的图片上传关键词：`Desktop Image Keywords / Alt Text` 与 `Mobile Image Keywords / Alt Text`。两者可以共享核心词，但至少 2 个短语要反映该版本实际画面差异。
- 输出文件命名必须区分端版本：`M1_Hero_Desktop_描述.png`、`M1_Hero_Mobile_描述.png`。
- 生图工具若无法直接锁定精确像素，必须在生成后本地后处理导出为精确尺寸：Desktop = `1464×600`，Mobile = `600×450`。最终交付不得只保存模型原始尺寸。

### 📝 Prompt 生成模板（Codex 版）

每条 Prompt 遵循以下结构：

```
[场景描述] — 产品在场景中的位置和状态
[周围环境] — 道具有限（≤3件），克制不喧宾
[光线] — soft natural daylight, warm color grading
[PRODUCT HERO] — 产品必须 1 秒内可识别，占比 [X]%
[NEGATIVE SPACE RESERVATION] — v3.6.0：只留白，⛔ 不写字（见下）
[产品结构保真] — 保持参考图中的产品结构不变形
[尺寸] — Desktop: wide format 1464x600 pixels；Mobile: 600x450 pixels with mobile-safe composition
[质量] — photorealistic, ultra-high detail, 4K, cinematic
```

**留白段落原文（Desktop）：**

```text
[NEGATIVE SPACE RESERVATION] Place the product in the left 45% of the frame.
Keep the right 45% as clean, tonally even negative space — plain backdrop or
softly defocused environment, no product parts, no props, no strong texture.
Brand copy will be composited there in post-production.
CRITICAL: render NO text, letters, numbers, labels, badges, or bars anywhere.
```

**留白段落原文（Mobile）：**

```text
[NEGATIVE SPACE RESERVATION] Place the product in the upper 55% of the frame.
Keep the lower 35% as clean, tonally even negative space — plain surface or
backdrop, no product parts and no props. Short brand copy will be composited
there in post-production. This is a mobile-first composition, NOT a crop of
the desktop version: recompose for a 4:3 portrait-leaning frame.
CRITICAL: render NO text, letters, numbers, labels, badges, or bars anywhere.
```

> ⛔ Headline / Subline 不再进 Prompt，进 `S3_视觉板块/amazon-listing-image-designer/overlay_tasks.yaml`（与主副图同一份）的 `text` 字段。

### 垫图绑定与事实边界（强制）

每个 M1–M5 模块必须在方案和结构化 prompt 包中列出：`reference_asset_id`、`reference_asset_path`、`source_asset_id`、`backup_asset_ids`、`supports_claims` 与 `cannot_support_claims`。生成时只能使用 manifest 中已登记的复制后文件作为参考图。

- 复用 Listing 的 `IMGxx` 可以，但必须在去重矩阵说明 A+ 新增了什么信息；不能把 Listing 图的同一主题、同一结论再做一遍。
- 新增 A+ 垫图必须以 `APLUS01`–`APLUS05` 编号，复制到 `S3_视觉板块/amazon-listing-image-designer/产品垫图/高级A+/`，更新 `asset_manifest.yaml` 后才可调用生图。
- 图片里不能出现原图未证实的材质、包装配件、尺寸、安装方式、认证或性能效果；缺少原图/数据时停下生成补图清单。

### 文案埋入规则

从 `S2_文案板块/{关键词Skill}/06_关键词库报告.md` 与 `S2_文案板块/{关键词Skill}/keyword_package.yaml` 提取关键词，自然融入图片文案：

| 优先级 | 关键词类型 | 埋入位置 |
|:---|:---|:---|
| 🔴 P0 | 核心品类大词 | M1 Headline、M5 品牌故事 |
| 🟡 P1 | 场景词 | M2 场景图 OCR 文案 |
| 🟢 P2 | 功能特性词 | M3 卖点图、M4 对比表 |

### 每张图片的后台上传关键词（强制）

每个 M1-M5 模块的 Desktop 与 Mobile 版本都必须在 Prompt 和文案表之后分别输出 `后台图片关键词（Image Keywords / Alt Text）`，不得使用泛化占位词或省略任一端。

- 关键词必须同时来自两处：该图片实际可见内容 + `S2_文案板块/{关键词Skill}/06_关键词库报告.md` / `S2_文案板块/{关键词Skill}/keyword_package.yaml` 中与该模块匹配的词。
- 每张图输出 5-10 个英文短语，以英文逗号分隔；优先采用产品词、可见功能词、场景词和目标人群词。
- 只描述图片中真实可见或可由产品资料证实的内容，不得添加画面中不存在的功能、材质、认证或效果。
- 不得填入竞品品牌、促销词、绝对化词、主观排名词或与图片无关的高流量词。
- 10 张图的关键词应随模块内容和端版本变化，避免整套复制同一组词；每组至少包含 2 个该图独有的画面/功能短语。
- 同时给出一行中文对应说明，便于人工核对，但后台上传字段使用英文。

固定输出格式：

```text
Desktop 后台图片关键词（英文，上传用）：keyword phrase 1, keyword phrase 2, ...
Desktop 关键词中文核对：中文短语1、中文短语2、...
Desktop 来源映射：可见画面=[...]；关键词库=[...]

Mobile 后台图片关键词（英文，上传用）：keyword phrase 1, keyword phrase 2, ...
Mobile 关键词中文核对：中文短语1、中文短语2、...
Mobile 来源映射：可见画面=[...]；关键词库=[...]
```

---

## Phase 3: 输出格式

### 每个模块的固定输出骨架（强制）

每个 M1-M5 模块必须按以下顺序输出。旧版单一 `英文 Prompt` 格式不得再作为交付格式使用：

````markdown
### 📦 模块 Mx: [模块名称]

**📐 Desktop 规格**：1464×600px
**📐 Mobile 规格**：600×450px

**📷 垫图**：`asset_id=APLUSxx`（或复用 `IMGxx`）
> 说明：[选图理由 + 可支持卖点 + 不可支持声明]

**🖥️ Desktop 英文 Prompt**
```text
[Desktop Prompt，横向构图，适配电脑端全宽 A+]
```

**📱 Mobile 英文 Prompt**
```text
[Mobile Prompt，重构为手机端安全构图；不是 Desktop 裁切/压缩]
```

**🇨🇳 双端差异说明**
```text
[说明 Desktop 与 Mobile 的构图差异、产品占比差异、文字层级差异和审核注意点]
```

**📝 文案提取表**
| 版本 | 位置 | 英文内容 | 中文 | 埋入关键词 |
|:---|:---|:---|:---|:---|
| Desktop | Headline | ... | ... | ... |
| Mobile | Headline | ... | ... | ... |

**🔍 后台图片关键词（Image Keywords / Alt Text，必填）**
```text
Desktop 后台图片关键词（英文，上传用）：...
Desktop 关键词中文核对：...
Desktop 来源映射：可见画面=[...]；关键词库=[...]

Mobile 后台图片关键词（英文，上传用）：...
Mobile 关键词中文核对：...
Mobile 来源映射：可见画面=[...]；关键词库=[...]
```

**📁 输出路径**
```text
Desktop: S3_视觉板块/amazon-premium-aplus-designer/aplus_visuals/Mx_[module]_Desktop.png
Mobile: S3_视觉板块/amazon-premium-aplus-designer/aplus_visuals/Mx_[module]_Mobile.png
```
````

> 以下 M1-M5 段落仅用于说明模块主题和文案类型；实际交付必须按上方“每个模块的固定输出骨架（强制）”输出 Desktop + Mobile 双版本，不得照抄旧版单端格式。
>
> ⛔ **v3.6.0 追加**：下面示例 Prompt 里的 `[EDITORIAL COPY: 嵌入 ... 48pt]`
> 之类写法**全部作废** —— 那是让模型渲染文字的老做法。示例只看构图与主题，
> 文案一律追加进 `S3_视觉板块/amazon-listing-image-designer/overlay_tasks.yaml`，Prompt 换成上面的
> `[NEGATIVE SPACE RESERVATION]` 段落。

### 📦 模块 M1: Hero 全宽主图

**📐 规格**：1464×600px

**📷 垫图**：`asset_id=APLUS01`（或复用 `IMGxx`）
> 说明：[选图理由]

**🖥️ 英文 Prompt**
```
[超宽画幅构图，产品偏左占 35%，右侧自然留白]
[soft natural daylight, warm color grading, oatmeal background]
[PRODUCT HERO: 产品 1 秒内可识别，占比 35%，位于画面左侧]
[EDITORIAL COPY: 留白区嵌入 Headline，如 "Precision Meets Life"
elegant sans-serif font, white text, 48pt]
[The product in this image must be reproduced with 100% structural
fidelity from the reference photo. Do not alter proportions.]
[wide format 1464x600 pixels, photorealistic, 4K, cinematic]
[No logos on product body]
```

**🇨🇳 中文说明**
```
产品位于画面左侧 1/3 处，右侧为燕麦色自然背景，
嵌入品牌 Headline「Precision Meets Life」。
```

**📝 文案提取表**

| 位置 | 英文内容 | 中文 | 埋入关键词 |
|:---|:---|:---|:---|
| Headline | Precision Meets Life | 精准·尽享 | [品类词] |
| Micro-copy | 3500W Power | 3500W功率 | [参数词] |

**🔑 后台图片关键词（Image Keywords / Alt Text，必填）**
```
[产品名] bathroom organizer, wall mount soap dispenser, premium home accessory
```

---

### 📦 模块 M2: 场景生活图

**📐 规格**：1464×600px

**📷 垫图**：`asset_id=APLUS02`（或复用 `IMGxx`）
> 说明：[选图理由]

**🖥️ 英文 Prompt**
```
[A elegant bathroom scene, natural stone countertop, soft morning light]
[产品位于场景中心位置，使用状态，角度清晰]
[道具克制 ≤3 件，如 minimal soap bar, small plant]
[PRODUCT HERO: 产品占比 50%，使用中状态，1 秒可识别]
[EDITORIAL COPY: 嵌入场景文案如 "Your Morning Ritual"
elegant sans-serif, warm white text, 36pt]
[The product in this image must be reproduced with 100% structural
fidelity from the reference photo.]
[wide format 1464x600 pixels, photorealistic, 4K, cinematic]
```

**🇨🇳 中文说明**
```
浴室场景，石材台面，柔和晨光。
产品在使用中，旁边配有简约肥皂和绿植点缀。
文案嵌入「Your Morning Ritual」。
```

**📝 文案提取表**

| 位置 | 英文内容 | 中文 | 埋入关键词 |
|:---|:---|:---|:---|
| Headline | Your Morning Ritual | 晨间仪式感 | [场景词] |

**🔑 后台图片关键词（Image Keywords / Alt Text，必填）**
```
bathroom lifestyle, soap dispenser in use, luxury bathroom accessories, daily routine
```

---

### 📦 模块 M3: 卖点详解图

**📐 规格**：1464×600px

**📷 垫图**：`asset_id=APLUS03`（或复用 `IMGxx`）
> 说明：[选图理由]

**🖥️ 英文 Prompt**
```
[Close-up product shot, centered, showing key material texture]
[产品特写，材质如 stainless steel/silicone 清晰可见]
[背景简洁，柔和渐变，自然光]
[PRODUCT HERO: 产品占比 60%，特写状态，材质细节清晰]
[EDITORIAL COPY: 嵌入功能文案如 "Built to Last"
elegant sans-serif, bold text, white color]
[The product in this image must be reproduced with 100% structural
fidelity from the reference photo. Preserve material texture details.]
[wide format 1464x600 pixels, photorealistic, 4K, macro detail]
```

**🇨🇳 中文说明**
```
产品特写，展示材质和做工细节。
背景简洁，突出产品主体。
文案嵌入「Built to Last」。
```

**📝 文案提取表**

| 位置 | 英文内容 | 中文 | 埋入关键词 |
|:---|:---|:---|:---|
| Headline | Built to Last | 坚固耐用 | [材质/功能词] |

**🔑 后台图片关键词（Image Keywords / Alt Text，必填）**
```
premium material detail, durable construction, quality craftsmanship, close-up view
```

---

### 📦 模块 M4: 对比/差异化图

**📐 规格**：1464×600px

**📷 垫图**：`asset_id=APLUS04`（或复用 `IMGxx`）
> 说明：[选图理由]

**🖥️ 英文 Prompt**
```
[Left side: 产品完整展示，角度与 M1 一致]
[Right side: 留白区域或结构化文案展示对比点]
[分割构图，左右比例 50:50 或 60:40]
[soft natural daylight, clean aesthetic]
[PRODUCT HERO: 产品占比 40%，位于左侧]
[EDITORIAL COPY: 右侧嵌入对比文案如 "The Difference"
and bullet points, elegant layout]
[The product in this image must be reproduced with 100% structural
fidelity from the reference photo. Preserve exact proportions.]
[wide format 1464x600 pixels, photorealistic, 4K, split composition]
```

**🇨🇳 中文说明**
```
左右分割构图：左侧产品，右侧对比文案。
突出差异化卖点，文案自然融入。
```

**📝 文案提取表**

| 位置 | 英文内容 | 中文 | 埋入关键词 |
|:---|:---|:---|:---|
| Headline | The Difference | 差异所在 | — |
| Bullet 1 | Premium Materials | 精选材质 | [材质词] |
| Bullet 2 | Sleek Design | 时尚设计 | [设计词] |

**🔑 后台图片关键词（Image Keywords / Alt Text，必填）**
```
comparison view, product vs ordinary,差异化展示, feature comparison
```

---

### 📦 模块 M5: 品牌故事/规格图

**📐 规格**：1464×600px

**📷 垫图**：`asset_id=APLUS05`（或复用 `IMGxx`）
> 说明：[选图理由]

**🖥️ 英文 Prompt**
```
[产品作为背景元素出现，brand-forward composition]
[整体氛围：品牌叙事，情感传递，不堆砌功能]
[柔和光线，自然色调，minimal 风格]
[PRODUCT HERO: 产品占比 25-30%，作为背景支撑角色]
[EDITORIAL COPY: 嵌入品牌故事文案如 "Designed for Life"
elegant sans-serif, centered or right-aligned, warm tone]
[The product in this image must be reproduced with 100% structural
fidelity from the reference photo. Product as ambient supporting element.]
[wide format 1464x600 pixels, photorealistic, 4K, editorial brand feel]
```

**🇨🇳 中文说明**
```
品牌故事氛围，产品作为背景元素融入。
强调情感价值，不堆砌参数。
文案嵌入「Designed for Life」。
```

**📝 文案提取表**

| 位置 | 英文内容 | 中文 | 埋入关键词 |
|:---|:---|:---|:---|
| Headline | Designed for Life | 为生活而设计 | [品牌定位词] |
| Subline | Quality That Matters | 品质所在 | — |

**🔑 后台图片关键词（Image Keywords / Alt Text，必填）**
```
brand story, quality home products, lifestyle essentials, premium design
```

---

## 📋 Q&A 板块内容（取 S2 的 5 组 Q&A）

原样取 S2 的 5 组问答（JSON `qa` 为准），不改写：

```
## Q&A 板块

以下是买家最常问的问题：

Q1: [问题1]
A1: [回答1]

Q2: [问题2]
A2: [回答2]

...（最多 5 组）
```

> 💡 这些 QA 将展示在 A+ 页面的最后部分，帮助买家解答疑虑，提升转化率。

---

## 📹 视频/对比展示板块内容

```
## 视频板块建议

**视频封面**：产品使用场景截图或专用封面图
**视频时长**：30-60 秒
**内容方向**：
- 产品功能演示
- 使用教程/安装指南
- 品牌故事讲述

**上传位置**：A+ 后台 → 视频模块

---

## 对比展示板块建议

**适用场景**：有多个变体/SKU 的产品

| 对比维度 | [变体A名称] | [变体B名称] | [变体C名称] |
|:---|:---|:---|:---|
| 尺寸/容量 | ... | ... | ... |
| 颜色 | ... | ... | ... |
| 材质 | ... | ... | ... |
| 适合场景 | ... | ... | ... |

> ⚠️ 禁止出现竞品品牌名，只能用"普通款"/"Ordinary"/"传统款"
```

---

## Phase 4: 出**视觉生产包**（⛔ v3.7.0 起不在 Codex 内生图）

### ⛔ 为什么改

与 `amazon-listing-image-designer` v4.3.0 同步改动。A+ 尤其明显：
一次要出 **2N 张**（N 个已确认模块 × 双端，默认 14 张），在 Codex 内逐张生成时间不可接受，
而且生成的图会永久留在上下文里，越往后越慢。

本 Skill 因此与视频链、主副图链**统一为同一种模式**：

```text
Codex 负责：挑垫图 → 编号入册 → 写双端提示词 → 出生产包
站外负责：网页端逐张生图
本地负责：回填后跑闸门验收
```

⛔ **本 Skill 不再调用 `imagegen`。** 出到生产包为止。
详细理由与操作见 `../amazon-listing-image-designer/SKILL.md` Phase 3。

### Step 1: 读取已登记的 A+ 垫图

无需用户再次上传产品图。AI 从 `S3_视觉板块/amazon-listing-image-designer/产品垫图/asset_manifest.yaml` 读取各模块已绑定的垫图：

1. **复核参考图**：提取并核对已登记的颜色、材质、结构、角度
2. **读取统一资产包**：`S3_视觉板块/amazon-listing-image-designer/产品垫图/asset_manifest.yaml`；只使用其登记的本品图片
3. **自动匹配模块**：按 Phase 1 规则分配到 M1-M5；新增图先物理复制到 `S3_视觉板块/amazon-listing-image-designer/产品垫图/高级A+/`
4. **回填 manifest**：新增图按 `APLUSxx` 重命名、登记来源与可支持卖点后才可出图

```
📁 S3_视觉板块/amazon-listing-image-designer/产品垫图/高级A+/
├── APLUS01_Hero_白底正面.jpg
├── APLUS02_Scene_使用场景.jpg
├── APLUS03_Feature_材质特写.jpg
├── APLUS04_Compare_对比角度.jpg
└── APLUS05_Brand_整体展示.jpg
```

### Step 2: 生成视觉生产包（2N 条任务，不是已生成图片）

```bash
python <home>/scripts/视觉生产包.py {产品根}/生产包配置.yaml
```

⛔ 产出的是 `image_production/`（提示词 + 已编号的参考图 + 回填清单），
**不是成图**。2N 条对应 N 个已确认模块 × Desktop / Mobile 双端。

⛔ **产品外观只交给参考图**：提示词里只写身份对应（`Item A = reference image 2`），
不写外观描述。实测（2026-08-11）文字描述与参考图冲突时模型偏向文字，
导致用户反复上传正确照片也纠正不过来。

⛔ **按 `gen_size` 出大图**：Desktop 出 `1960×803`、Mobile 出 `1600×1200`，
⛔ 不要直接出 1464×600 / 600×450 —— 实测直接生成小图会糊，
大图降采样反而更锐。交付尺寸由 `叠字渲染器.py` 自动导出。

下表是各模块的构图特征，写提示词时按它区分双端：

| 模块 | 端版本 | 建议文件名 | Prompt 特征 |
|:---|:---|:---|:---|
| M1 Hero | Desktop | M1_Hero_Desktop_xxx.png | 横向品牌主视觉，产品偏左，Headline 右侧 |
| M1 Hero | Mobile | M1_Hero_Mobile_xxx.png | 手机端重构，产品与短文案上下/中心分区 |
| M2 Scene | Desktop | M2_Scene_Desktop_xxx.png | 场景横向展开，使用状态 |
| M2 Scene | Mobile | M2_Scene_Mobile_xxx.png | 单一核心动作，减少道具和小字 |
| M3 Feature | Desktop | M3_Feature_Desktop_xxx.png | 材质/功能横向解释 |
| M3 Feature | Mobile | M3_Feature_Mobile_xxx.png | 2-3 个关键标注，避免复杂参数 |
| M4 Compare | Desktop | M4_Compare_Desktop_xxx.png | 横向对比/差异化展示 |
| M4 Compare | Mobile | M4_Compare_Mobile_xxx.png | 上下结构或短标签对比 |
| M5 Brand | Desktop | M5_Brand_Desktop_xxx.png | 品牌叙事，规格/变体收束 |
| M5 Brand | Mobile | M5_Brand_Mobile_xxx.png | 情绪化主图 + 1 句品牌文案 |

### Step 3: 展示审阅

每张图生成后，展示给用户并说明：
- 本次使用的垫图文件路径
- 与 Prompt 的匹配度
- 文案文字渲染效果
- 是否有需要后期微调的元素
- Desktop / Mobile 后台图片关键词是否分别与本图真实可见内容及关键词库一致
- Mobile 版本是否真的重构了构图，而不是简单裁切 Desktop
- Mobile 文字是否在手机端可读，且没有复杂小字/表格
- Desktop 文件是否为精确 `1464×600`，Mobile 文件是否为精确 `600×450`
- 本模块是否提供了 Listing 图片中尚未回答的新信息

### Step 4: 交付生产包并停下

全部 `M1D/M1M` 到 `M5D/M5M` 条目写入 manifest、生产包生成完毕后，显示：

```text
高级 A+ 生产包已就绪：按当前规划统计 2N 条任务（默认 14），并非成图验收通过。
位置：image_production/  —— 先看 START.md
⛔ 本 Skill 到此为止，不在 Codex 内生图。
请在网页端按 START.md 逐张生成，下载后按「回填清单.yaml」放好，
再跑本地闸门验收。
```

- ⛔ **不调用 `imagegen`**。生产包齐全只代表 `package_ready`，不代表图已生成。
- 用户回填后跑：`叠字渲染器.py`（叠字+五道闸门）、`扫描底图落字区.py`、`产品色彩审计.py`。
- 某张要改时保留原图，回填为 `M2_scene_v2` 等新版本。
- 改图在 ChatGPT 网页端 / Codex App 里完成（自带局部改图）；改好的图原样放回对应回填夹，助手保留原图、修订版另存，不覆盖。内容。

### 过渡话术

```
Premium A+ 提示词按当前规划输出 N 组 Desktop + Mobile；成图数量与验收以最终版记录为准。

📷 垫图资产包：S3_视觉板块/amazon-listing-image-designer/产品垫图/asset_manifest.yaml
   已为每个模块绑定 asset_id；新增垫图已复制到 S3_视觉板块/amazon-listing-image-designer/产品垫图/高级A+/

| 模块 | Desktop 输出 | Mobile 输出 | 垫图文件 |
|:---|:---|:---|:---|
| M1 Hero | M1D | M1M | APLUS01 / IMGxx |
| M2 场景 | M2D | M2M | APLUS02 / IMGxx |
| M3 卖点 | M3D | M3M | APLUS03 / IMGxx |
| M4 对比 | M4D | M4M | APLUS04 / IMGxx |
| M5 品牌 | M5D | M5M | APLUS05 / IMGxx |

如需替换图片，请把新图加入产品垫图资产包并更新 manifest。
⛔ 不在 Codex 内生图：跑 `视觉生产包.py` 出 image_production/，按 START.md 到网页端逐张生成。
```

---

## 💡 使用示例

**用户**："帮我设计[你的产品]的高级 A+"

**Agent 工作流**：
1. → 读取产品报告，输出认知摘要
2. → 提取 Q&A（最多 5 组）
3. → 按 N 个模块生成 Prompt，每组同时输出 Desktop + Mobile，共 2N 个出图任务
4. → 汇总 visual_generation_manifest，确认 `aplus_ready: 10` 后，用户确认一键或指定模块 → 生成图片

---

## 上下游 Skill 关系

### 上游
- `amazon-market-research` → 提供产品基础参数
- `amazon-listing-copywriter` → 提供文案和 **Q&A 种子问题**
- `amazon-sif-keyword-research` / `amazon-sellersprite-keyword-research` / `amazon-xiyou-keyword-research` → 提供关键词库（埋词用）

### 下游
- `amazon-listing-copywriter` → A+ 内容可与文案 Skill 联动

---

## 合规红线

- ❌ 禁止提及促销/折扣/价格
- ❌ 禁止使用竞品品牌名（对比表用"普通款"/"Ordinary"）
- ❌ 禁止引用第三方评论
- ❌ 禁止模糊/低质量图片
- ❌ 禁止使用未经授权的认证标识
- ⚠️ 绝对化用语（100% safe, #1, Best Seller）
- ⚠️ 医疗/杀菌暗示

---
## 实战版 · 编排契约

### 版本
**3.3.0** salon-v2（统一接入产品垫图资产包与受控出图执行）

### 输入（读取顺序）
1. listing、profile、词库卖点  
2. **同一套** `visual_playbook.yaml`（与主副图共享；A+ 用其中 `premium_aplus` 段）  
3. 参考图目录 `参考图/高级A+/`、adapters/…/visual/refs_aplus 或 `runtime_data_overlay.yaml` 的 S3 visual_reference（若 playbook 未学过 A+，可先补学）  
4. `S3_视觉板块/amazon-listing-image-designer/08A_视觉垫图资产清单.md` + **同一套** `S3_视觉板块/amazon-listing-image-designer/产品垫图/asset_manifest.yaml`；A+ 模块逐个绑定 `APLUSxx` 或可复用的 `IMGxx`  

### 视觉学习（A+ 专用补学）
若仅有主副图 playbook、用户新投喂了 A+ 参考：

```text
增量视觉学习：只分析 参考图/高级A+/ ，合并进 visual_playbook.premium_aplus
```

规则同主副图：学模块节奏与信息密度，不抄竞品品牌与版权图。

### 输出

### 0711 双端迭代与验收账本（强制）

Premium A+ 默认 7 个模块、最少 5 个，实际以确认的 N 为准，每模块必须同时交付 Desktop 与 Mobile，最终验收为 `N × 2` 个独立图位；手机端不得只是桌面端缩略图，必须重新排版主体、文字安全区和裁切焦点。

运行前读取 `../../templates/visual_iteration.schema.yaml`，2N 个图位分别记录 `asset_id`、端类型、尺寸、truth constraints、垫图、Prompt v1/v2/v3、失败现象、修复动作、最终路径和审核状态。强制输出或合并写入 `S3_视觉板块/amazon-premium-aplus-designer/visual_prompt_iterations.yaml`；任何一个 Mobile 图位缺失时，不得把 A+ 状态标记为完成。

acceptance_checks：模块数=N（默认 7，最少 5）；Desktop=N；Mobile=N；每端逐图 Prompt 可追溯；结构/数量/文案/安全区通过；失败修订不覆盖旧版本。

**历史可选叠字验收（默认路径不运行）**：底图零文字；`overlay_tasks.yaml` 里已追加 M1D/M1M–M5D/M5M 共 10 条
（**追加进主副图那一份，不新建文件**）；令牌为**复用主副图那一份**；
`叠字渲染器.py` 退出码 0。四项任一不满足不得标完成。
- `S3_视觉板块/amazon-premium-aplus-designer/09_高级A+方案.md`（注明 profile、overlay version、playbook / 参考图来源）  
- `S3_视觉板块/amazon-listing-image-designer/产品垫图/asset_manifest.yaml`（若新增 A+ 垫图，更新来源、SHA-256、图位、支持/禁止表达）  
- `S3_视觉板块/amazon-listing-image-designer/visual_generation_manifest.yaml`（追加 `M1D/M1M` 到 `M5D/M5M` 共 10 条 ready/blocked 条目，等待用户确认）
- 桌面 **1464×600** 与移动 **600×450** 必须分开提示词、分开输出路径、分开图片关键词，禁止桌面图硬压成移动图  
- 默认不在对话内整套生图；A 渠道仅用于 1–3 张选定图位返修：出 `image_production/` 生产包，用户站外生成后回填 `S3_视觉板块/amazon-premium-aplus-designer/aplus_visuals/`；改图在 ChatGPT 网页端 / Codex App 里完成，改好的图原样放回回填夹；保留原图，修订版另存，不覆盖。
- next：`amazon-ad-strategy-architect`（⛔ 不再直连 campaign-builder，见下）

### Handoff

> 下表及其后旧回执为历史记录；新案只执行上方 R03 当前规则。


| 版本 | 说明 |
|---|---|
| **3.6.0** | **叠字两层分离接入 A+：底图无字 + 留负空间，文案改由 `叠字渲染器.py` 按主副图同一份 `visual_style_tokens.yaml` 绘制；M1D/M1M–M5D/M5M 追加进主副图同一份 `overlay_tasks.yaml`；废止 Prompt 内 EDITORIAL COPY 写法。Desktop/Mobile 两端字体天然一致** |
| 3.5.0 | 0711 融合：Premium A+ 强制 5 组 Desktop + 5 组 Mobile，双端逐图迭代与失败修复账本 |
| 3.4.0 | 7·18 双端输出和 A+ 完整模块恢复 |
```markdown
【本步完成】amazon-premium-aplus-designer
【视觉资产】S3_视觉板块/amazon-listing-image-designer/产品垫图/asset_manifest.yaml；M1-M5 均已绑定 asset_id，M1D/M1M 到 M5D/M5M 共 10 条出图任务 ready
【视觉叠层】visual_playbook.premium_aplus
【样式令牌】复用 S3_视觉板块/amazon-listing-image-designer/visual_style_tokens.yaml（与主副图同一份，A+ 与 Listing 字体一致）
【叠字】已追加 M1D/M1M–M5D/M5M 到 S3_视觉板块/amazon-listing-image-designer/overlay_tasks.yaml（与主副图同一份）；渲染器退出码 0
【下一步 Skill】amazon-ad-strategy-architect（⛔ 广告链 4.0 起：策略层 → 架构 → Bulk，不能跳过策略层）
【推荐口令】用 keyword_package、profile 与盈亏 ACOS 先出 ad_strategy_brief；我审核 strategy_review 后再进 campaign-builder
【MCP】not_required
【可选进阶】要 A+/主图视频分镜 → amazon-product-video-director（Grok 或 即梦；站外生成；沙龙讲解不主推）
【备选】无 Premium 资质的学员 → amazon-standard-aplus-designer（普通 A+，规格不同）
```


## R03 里程碑 2 当前接口

本版优先执行 `../starsky-fullchain-workflow/references/R03_当前视觉与交接.md` 的里程碑2章节。A+父体一套、桌面先验收再手机及上传清单；品牌仅本人明确请求后经独立可选入口，不重置已交付阶段；视频按真实证据A/B/C分档、1+1上限、可后补。所有结果按当前原生验收记录显示，生成/导出/配置成功不等于通过。
