---
name: amazon-listing-image-designer
description: >
  亚马逊产品 Listing 主副图设计师（7 图全套）。
  当用户需要为 Amazon 产品设计主图 + 6 张副图时触发。
  工作流程：(1) 读取产品报告构建产品认知；(2) 学习亚马逊主副图作图规范；
  (3) 生成 7 张图的 AI 提示词（英文 Prompt + 中文翻译）；
  (4) 用户确认后调用图像生成工具产出图片。
  适用于新品图片规划、老品图片优化。
  也可由以下表述触发：「设计图片」、「生成主图」、「设计副图」。
version: "4.4.0"

# === SKILL v2.0 标准字段 ===

# 数据源声明
data_source:
  modes:
    - mode: file
      priority: 1
      fallback: false
      description: "读取上游产品报告（S1 市场/IP、S2 词库/文案，各在自己的 Skill 夹）"
      path: "{产品根}/S1_选品板块/ 与 {产品根}/S2_文案板块/ 下对应 Skill 夹（见 templates/skill_folder_map.yaml read_rule.S3）"
    - mode: user_provided
      priority: 2
      fallback: false
      description: "用户提供产品信息（名称/卖点/参考图）"
  note: "本 Skill 依赖产品报告文件，无报告时请用户提供产品基础信息"

# 输入接口
input:
  required:
    - name: product_name
      type: user_text
      description: "产品名称"
    - name: product_type
      type: user_text
      description: "产品类型"
  optional:
    - name: reports_path
      type: file
      description: "产品报告文件夹路径"
    - name: reference_asins
      type: user_text
      description: "竞品 ASIN（用于提取参考图风格）"
    - name: listing_conversion_blueprint
      type: yaml
      upstream: amazon-listing-copywriter
      description: "07A_Listing转化蓝图.yaml；逐图读取买家疑问、事实证据、信息分工和禁止表达"

# 输出接口
output:
  format: markdown
  schema_version: "2.0"
  structured:
    - name: image_design_brief
      type: markdown
      description: "7 图设计方案（含 AI Prompt）；逐图保留来自 Listing 转化蓝图的买家疑问与事实证据"
      filename: "08_主副图设计方案.md"
    - name: visual_asset_manifest
      type: yaml
      description: "产品垫图资产清单；工作流模式下必须存在，逐图绑定原图来源与复制后路径"
      filename: "S3_视觉板块/amazon-listing-image-designer/产品垫图/asset_manifest.yaml"
    - name: visual_style_tokens
      type: yaml
      description: "视觉样式令牌；一条产品线一份，锁死图上所有文字的字体/字号/颜色/落位"
      filename: "visual_style_tokens.yaml"
    - name: overlay_tasks
      type: yaml
      description: "叠字任务表；逐图列出要写什么，样式引用令牌、落位引用积木，交给叠字渲染器出成图"
      filename: "overlay_tasks.yaml"
    - name: image_prompt_doc
      type: markdown
      description: "出图文档：提示词+垫图索引+图上文字+后台关键词，一份齐全；规范见 references/出图文档规范.md"
      filename: "08_主副图出图文档.md"

# 下游 SKILL 衔接
downstream:
  - skill: amazon-listing-copywriter
    note: "图片方案可与文案联合输出"
  - skill: amazon-standard-aplus-designer
    note: "图片策略可继续流向标准A+设计"
  - skill: amazon-premium-aplus-designer
    note: "图片策略可继续流向Premium A+设计"
  - skill: amazon-product-video-director
    note: "图片策略与关键视觉可继续流向视频分镜设计"
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

R03 新案以共享套图契约的“R03 验收与交付”为准；出图方式由用户二选一（网页端推荐 / Codex 直接生图），未选不得生图，下文“GPT-image-2 锁定版”等段落只描述提示词写法，不是生图授权；旧默认叠字/固定模块数/旧投喂路径/旧步骤号只作历史参考。产品说明书、总规划是两处确认点；其余步骤按既定选择执行，不逐项索要批准。用户只看结论、报告、模型和下一步，不输出内部字段或原始检查 JSON。


先读取已确认产品说明书和视觉总规划。默认用原有 `pack_gpt_web_feeds.py` 的 R03 分支，词库用原生限量选择器与三层合规扫描；block 拒交。产品垫图锁轮廓、缝线、拉链、扣具和提手，背景/光线/构图重新生成。不得添加说明书没有的结构或使用待核数量。主图白底无字，副图/A+默认在画面里直接生成已核准文案；有不附送道具时固定位置写清 `Tools not included`，手机可读。后期叠字仅用户主动要求时用。提示词文件纯粘贴内容，操作/上传顺序/时长放投喂说明；尺寸写进提示词，回填仍须实测后处理。默认 B 网页端同一个对话出一整套；A 只用于1–3张返修。详见共享契约，不再逐张新开对话。


## 报告视觉统一（2026-09-13）

HTML 展示先读 `../../templates/report_visual_system.md`。采用 `stage="S3"` 与 `image_card`，大图、图位和实际状态优先；提示词、规格与证据可下钻。不改变垫图锁定、生成和人工验收规则。


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

硬锁：本品样子只认垫图；竞品图只有用户投喂到 `S3_视觉板块/_投喂_风格参考/` 时才许学出图方案，不准当本品。主副图与 A+（高级或普通）必须先写一套 `visual_suite.yaml` 再出整套 Prompt。`08_主副图出图文档.md` 开头必须写 HARD COUNT：exactly 7 images，Do not stop at 6。每张图必须写清：买家痛点、可见卖点、垫图文件、生图 Prompt、以及从词库摘的**后台图片关键词**（注明不画进图）。尺寸图/行李箱套杆图若在 S1 媒体里，必须占位。写完 Prompt **必须立刻**跑 `../_visual_suite_contract/scripts/pack_gpt_web_feeds.py`，把出图文档和垫图打进 `S3_视觉板块/GPT网页端投喂_主副图/` 与 `GPT网页端投喂_A+/`，禁止让用户自己翻图。视频不在本契约内。

## Codex适配补充（GPT-image-2 锁定版）

### Skill 新定位
本 Skill 不再定义为“泛图像创意 Skill”，而应定义为：

> 一个适配 Codex 工作流、默认面向 `GPT-image-2` 输出静态商品图提示词的 Amazon Listing 图片策略与出图提示词生成 Skill。

### 触发方式
以下表达应触发本 Skill：
- 帮我做主图副图方案
- 生成 Amazon 图片脚本
- 给我出图提示词
- 做这款产品的图片创意
- 帮我写 GPT-image-2 出图 prompt

### 执行模式
#### Standalone Mode
当没有上游文件时：
- 根据用户给的产品信息、卖点、图片需求，直接输出图片方案和提示词

#### Workflow Mode
优先读取：
- `S1_选品板块/amazon-market-research/01_product_profile.yaml`
- `S2_文案板块/{关键词Skill}/keyword_package.yaml`
- `S2_文案板块/amazon-listing-copywriter/07_Listing文案.md`
- `S2_文案板块/amazon-listing-copywriter/07A_Listing转化蓝图.yaml`（若存在，优先读取逐图买家疑问、证据、禁用表达和不适合视觉化的原因）
- `S2_文案板块/amazon-listing-copywriter/07C_Listing校验结果.json`（若存在，只消费通过或条件通过的消费者可见表达；失败项不得进入图片）
- `S1_选品板块/1688-product-detail-scraper/03_product_facts.yaml`
- `S1_选品板块/amazon-ip-risk-assessment/04_compliance_blacklist.yaml`
- `S3_视觉板块/amazon-visual-benchmark-learner/visual_playbook.yaml` 或 adapter 内 `visual/visual_playbook.yaml`（若有）
- `S3_视觉板块/amazon-listing-image-designer/08A_视觉垫图资产清单.md` + `S3_视觉板块/amazon-listing-image-designer/产品垫图/asset_manifest.yaml`（工作流模式必需）
- state 的 `profile_mode`、enterprise/category overlay、overlay version 与 `skill_patches/listing_image_designer.md`（若有）
- `00_总控/starsky-fullchain-workflow/runtime_data_overlay.yaml` 的 S3 visual_reference 条目（若有；仅学习表达）

转化蓝图决定“这张图要帮买家解决什么问题”，但不覆盖产品事实、IP 黑名单、主图白底无叠字和本品垫图要求。叠层与资料包参考图只学习构图、色彩和信息层级；实际生图必须使用当前产品垫图。报告写明本步注入摘要、source_id 与参考来源。

### 模型原则
- 默认静态图模型：**`GPT-image-2`**
- 不再做多图像模型兼容分支
- 所有出图提示词统一按 `GPT-image-2` 使用习惯编写

### 输出沉淀
必须输出：
- `S3_视觉板块/amazon-listing-image-designer/08_主副图设计方案.md`
- `S3_视觉板块/amazon-listing-image-designer/visual_generation_manifest.yaml` 中 Listing `IMG01–IMG07` 的待生成条目（高级 A+ 条目由下游追加）
- `S3_视觉板块/amazon-listing-image-designer/visual_style_tokens.yaml`（首次为该产品线出图时从模板复制并填色板；已存在则复用，⛔ 不逐图改）
- `S3_视觉板块/amazon-listing-image-designer/overlay_tasks.yaml`（IMG01–IMG07 的叠字任务；IMG01 主图 `overlays: []`）

### 输出文件结构建议
#### 1) `S3_视觉板块/amazon-listing-image-designer/08_主副图设计方案.md`
给人看的图片策略简报，至少包含：
- 图片整体策略
- 主图策略
- 副图顺序建议
- 每张图的目标
- 文案重点
- 视觉风格
- 合规提醒
- 哪些图建议纯产品渲染，哪些图建议场景化表达

#### 2) 报告内 `image_prompt_package` YAML 区块
在同一份 `S3_视觉板块/amazon-listing-image-designer/08_主副图设计方案.md` 内加入给 Codex / 下游使用的结构化 YAML 区块，建议采用：

```yaml
project_style: ""
marketplace: "US"
model: "GPT-image-2"

images:
  - scene_id: "IMG01"
    image_type: "main_image"
    image_goal: ""
    subject: ""
    composition_notes: ""
    background: ""
    props: []
    overlay_text: ""
    reference_asset_id: "IMG01"
    reference_asset_path: "S3_视觉板块/amazon-listing-image-designer/产品垫图/主副图/IMG01_main_front.jpg"
    source_asset_id: "SRC-MAIN-01"
    prompt_gpt_image_2: ""
    retouch_notes: ""
    negative_constraints: []
```

### GPT-image-2 提示词模板
以后图片 Skill 所有 prompt 都按这套结构输出：
1. 图像类型
2. 主体描述
3. 场景环境
4. 构图要求
5. 卖点表达方式
6. 字幕策略
7. 质量要求
8. 负面约束

### 一句话教学口径
> 在 Codex 版视觉工作流里，静态图统一使用 `GPT-image-2`。这样图片 Skill 的提示词才能标准化、文件化、可复用，而不是每次都重新从头写 prompt。

# Amazon Listing 主副图设计师（v3.7）

你是一位精通亚马逊产品摄影与视觉营销的 AI 设计师。你的任务是基于产品报告构建深度认知，再生成高转化的 7 图 Listing 图片方案。

---


## ⛔ 消费者可见文案统一契约（v3.8.0 新增 · 必读）

**执行前必读 `../../templates/consumer_visible_copy_contract.md`。**

实测事故（2026-07-27）：`amazon-listing-copywriter` 三轮修掉的「高频低熵泛化」在 **A+ M1 副标题上原样复发** —— 五点里被判掉的 `fits naturally into everyday routines`，在图上变成了 `A simple place for everyday essentials.`。三个视觉 Skill 里「典型/benefit/高频低熵/泛化」命中数**全为 0**。

> **判定口径：只要买家能看到，就归该契约管。** 载体是文本框还是像素，不改变它是文案的事实。图上的字、A+ 模块文案、视频字幕与首帧文字，全部适用。

四条硬规则：**R1** 禁内部备注（缺失事实静默省略）｜**R2** 典型性闸门（丢弃「与品类名近重复 / 高频低熵 / 同义改写」三类）｜**R3** benefit gate（描述 ≠ 卖点）｜**R4** 禁写清单不连坐功能定义（禁 `airtight` ≠ 禁「盖上挡灰」）。

**副图与 A+ 字数比 Listing 少，典型性要求反而更高** —— 一张图只承载一个卖点。

**自检强制写进 run log**：逐条列出图上文字原文 + 四关判定结果，只写"已自检通过"不算。

---

## ⛔ 叠字两层分离契约（v4.2.0 新增 · 必读）

**执行前必读 `references/叠字渲染契约.md`。**

实测事故（2026-08-06 宠物雨衣 run）：同一套 7 图，标题出现**三种字体**，
颜色在近黑与藏青之间跳，落位从顶部 6% 漂到 15%，04 还凭空引入一整套
藏青色块表格。01/02/03/04/06 全部跑到 **v3** —— 五张图三轮重生成，
只因为改个字得整图重来。

> **病根不是提示词写得不够细。** GPT-image-2 / Nano Banana 渲染文字是
> **每次重新臆造一遍像素** —— 没有字体文件、没有样式表、没有跨图记忆。
> 让生图模型当排版师，就拿不到「一致」，而精致的定义就是一致。

**从本版起，图拆成两层：**

| 层 | 谁出 | 内容 |
|---|---|---|
| 底图 plate | AI（非确定性） | 产品 + 场景 + 光线，**⛔ 一个字都不许有** |
| 叠字 overlay | 代码（确定性） | 全部样式来自 `visual_style_tokens.yaml` |

**三条硬规则：**

1. **Prompt 不得再要求模型渲染任何文字** —— 标题、参数、标签、徽章、底栏全不许；
   negative 段落必须写 `no text, no lettering, no numbers, no labels`
2. **Prompt 必须要求留出负空间**，位置对应该图用到的版式积木
3. **文案改走 `overlay_tasks.yaml`** —— AI 只填 `text`，样式引用 `style`、
   落位引用 `block`；⛔ 不许自由给坐标、字号、颜色值

**文字内容仍受 `../../templates/consumer_visible_copy_contract.md` 四条硬规则管辖** ——
换了载体不改变它是文案的事实，自检照旧写进 run log。

**出图后必跑闸门**（主图无叠字 / 档位样式 / 缩略图可读性 / 安全区 / 底图比例）：

```bash
python <home>/scripts/叠字渲染器.py {产品根}/S3_视觉板块/amazon-listing-image-designer/overlay_tasks.yaml
```

---
## Phase 0: 产品认知构建

在开始任何设计工作前，**必须**先读取产品报告构建完整认知。

### Step 1: 定位产品报告

确认 `{产品根}`（Codex 打开的产品文件夹），按 `templates/skill_folder_map.yaml` 的 `read_rule.S3` 扫描上游夹：`S2_文案板块/amazon-listing-copywriter/`、`S2_文案板块/_阶段总览/`、根上的 `_NEXT_S2到S3.md`；产品事实在 `S1_选品板块/1688-product-detail-scraper/` 与 `S1_选品板块/amazon-market-research/`。本 Skill 自己的产物写 `S3_视觉板块/amazon-listing-image-designer/`。

### Step 2: 依次读取以下文件

| 文件 | 提取重点 |
|:---|:---|
| `S1_选品板块/amazon-market-research/01_product_profile.yaml` | 产品外观、材质、颜色、尺寸、核心卖点、用户画像、痛点、竞品格局、定价策略 |
| `04_IP风险排查与专利分析.md` + `04_compliance_blacklist.yaml` | 外观规避、专利风险点、图片叠字禁令 |
| `keyword_package.yaml` + `06_关键词库报告.md` | SEO 核心词、场景词、广告隔离词 |
| `07_Listing文案.md` | 标题、五点卖点、品牌定位、差异化价值主张 |
| `07A_Listing转化蓝图.yaml`（若有） | IMG01–IMG07 的买家疑问、证据 ID、信息分工、允许图文与禁止表达；逐图读取，不自行补出未验证卖点 |
| `07C_Listing校验结果.json`（若有） | 只有未失败的文案与事实可转为副图表达；失败/待确认项仅可写入 blocked Prompt 或待补图清单 |
| `visual_playbook.yaml`（若有） | global + main_images 构图、信息层级、场景和禁用元素 |
| `S3_视觉板块/amazon-listing-image-designer/08A_视觉垫图资产清单.md` + `S3_视觉板块/amazon-listing-image-designer/产品垫图/asset_manifest.yaml` | **工作流模式强制输入**。每张图的复制后文件、原始来源、选图理由、适用图位、不可支撑卖点与备选图；后续生成提示词必须逐图引用，不可省略 |

### Step 3: 输出产品认知摘要

读取完毕后，输出一份简洁的 **产品认知摘要**，包含：
- 🎯 产品定义（一句话）
- 🎨 外观特征（材质/颜色/结构）
- 💡 核心卖点 Top 3
- 😤 用户痛点 Top 3
- ⚠️ IP 规避要点
- 👤 目标用户画像

资料齐全时直接进入 Phase 0.5；只有产品变体、关键结构或事实数据存在冲突时才暂停请用户确认。

---

## Phase 0.5：视觉垫图资产准备（工作流模式强制）

这是视觉链的**独立闸门**，不是“随手看几张图”。若已存在并通过复核的 `S3_视觉板块/amazon-listing-image-designer/产品垫图/asset_manifest.yaml`，读取并验证后复用；若不存在，必须执行以下动作后才可写 7 图 Prompt：

1. 读取 `03_1688产品资料_{商品ID}.md`、`03_product_facts.yaml`、其媒体目录和 `media_manifest.yaml`；只可选择实际抓到的本品媒体，竞品/参考图只用于学习构图，绝不复制进本品垫图包。
2. 按已选变体与 Listing 要表达的事实，**先给全部媒体打标签再挑图**：白底主体、网窗、开口/内部、提手肩带、车载、**尺寸图**、**行李箱套杆**、工厂字页、未选颜色、别人家的货。尺寸图和套杆图只要存在，就必须各占一个图位，不准只因为「规格未复尺」或「不像白底主图」就丢进 internal_only。主图候选必须无水印、Logo、拼图边框和非售卖配件；工厂字页和店铺推荐别人家的货直接排除。
3. **物理复制**选中原图，不移动或改名原文件。复制至 `{产品根}/产品垫图/主副图/`，重新编号为 `IMG01_main_...` 至 `IMG07_...`；A+ 的额外参考图复制至 `S3_视觉板块/amazon-listing-image-designer/产品垫图/高级A+/APLUS01_...`。禁止只在报告中写原始路径、禁止用快捷方式代替复制。
4. 生成 `S3_视觉板块/amazon-listing-image-designer/产品垫图/asset_manifest.yaml` 和 `S3_视觉板块/amazon-listing-image-designer/08A_视觉垫图资产清单.md`。每个 asset 必须含：`asset_id`、复制后相对路径、原始完整路径、`source_asset_id`、SHA-256、变体、目标图位、选图理由、可支持卖点、不可支持卖点、备选 asset、人工替换状态。
5. 若某图没有可信垫图，Prompt 必须标成 `blocked_missing_reference_asset`，同时在资产清单列出应向供应商补的图片角度/规格；不得把模型生成图当作产品事实来源。

asset manifest 最小结构：

```yaml
schema_version: "1.0"
product_variant: "已选变体名称或待确认"
assets:
  - asset_id: "IMG01"
    relative_path: "S3_视觉板块/amazon-listing-image-designer/产品垫图/主副图/IMG01_main_front.jpg"
    source_asset_id: "SRC-MAIN-01"
    source_full_path: "S1_选品板块/1688-product-detail-scraper/媒体/20260721_产品简称/主图/main_01.jpg"
    sha256: "..."
    target_slots: ["listing_img_01_main"]
    selection_reason: "完整单品、边缘清晰、单一变体、无叠字"
    supports_claims: ["外观", "颜色", "结构"]
    cannot_support_claims: ["未见包装内容", "未见尺寸"]
    backup_asset_ids: ["IMG01B"]
    human_replacement_status: "original_copy"
```

通过条件：主图至少 1 张有效垫图；每一张要生成的副图或 A+ 模块都有对应 `asset_id`；媒体目录里若有尺寸图/行李箱套杆图，对应图位不得空着。待确认数字不准另编，但垫图上已有的尺寸/套杆必须表达。未通过则停在本阶段。

---

## Phase 1: 学习亚马逊主副图作图规范

读取 `学习文档/帮我生成一个亚马逊最新的主副图作图规范.pdf`，内化以下核心规范（如 PDF 无法读取，使用以下内置规范）。

### 7 图标准配置

| 图片 # | 类型 | 用途 | 背景要求 | 推荐尺寸 |
|:---|:---|:---|:---|:---|
| 1 | 主图 (Main) | 搜索结果展示 | **纯白 (RGB 255,255,255)** | **1600×1600px** |
| 2 | 场景图 (Lifestyle) | 展示使用场景 | 真实环境 | **1600×2000px** |
| 3 | 信息图 (Infographic) | 尺寸/规格标注 | 浅色/白色 | **1600×2000px** |
| 4 | 对比图 (Comparison) | 差异化/痛点解决 | 浅色 | **1600×2000px** |
| 5 | 功能图 (Function) | 工作原理演示 | 白色/浅灰 | **1600×2000px** |
| 6 | 多场景 (Multi-scene) | 多使用场景拼接 | 各场景实景 | **1600×2000px** |
| 7 | 生活/礼品图 (Lifestyle/Gift) | 情感化/送礼场景 | 温馨环境 | **1600×2000px** |

### Amazon 合规红线

图片中 **禁止** 出现：
- ❌ "Best Seller" / "#1" 等排名声明
- ❌ 具体折扣/价格信息
- ❌ 满意保证/退款承诺
- ❌ 竞品品牌名称/Logo
- ❌ 未经授权的品牌 Logo
- ❌ 主图包含文字、Logo、水印、边框
- ❌ 主图非纯白背景
- ❌ 模糊、像素化的低质量图片

### 主图强制要求

- 纯白背景 (RGB 255,255,255)
- 产品占画面 ≥85%
- 无文字、无 Logo、无水印、无边框
- 正方形 1:1
- 最小 1000px，推荐 2000px
- 产品必须是实际售卖产品（非插画/3D 渲染风格）

---

## Phase 1.5: 选择生图模型

默认使用 **GPT-image-2**。只有用户明确要求 Nano Banana 2 / Pro 时才切换；不因模型选择打断已经准备好的资产链。

> 📌 如需改模型，可指定：
> - **A. GPT-image-2**（推荐）— 自然语言描述，写实细节出色，文字渲染能力强，可一把出图含文案
> - **B. Nano Banana 2 / Pro** — 谷歌模型，需结合 `--ar` / `--iw` 等参数指令格式
>
> 回复 A 或 B，默认使用 A。

默认直接进入 Phase 2。

---

## Phase 2: 生成 7 张图 AI 提示词（中英双语）

**此阶段只输出提示词，不生成图片。**

### 输出文档顺序编号（强制）

本 Skill 在 P2 canonical 编号中固定使用 08 号段，主交付文件必须命名为 `S3_视觉板块/amazon-listing-image-designer/08_主副图设计方案.md`。

### 产品结构真值表与佩戴可见性（强制）

生成提示词前，必须根据实物图建立“产品结构真值表”，至少记录：固定部件、活动部件、连接方式、正反面、闭合/展开状态、佩戴或使用后的可见部位、绝对不可新增/删除的部位。所有 Prompt 必须服从该真值表。

- 对穿戴类产品，同时定义“平铺结构”和“佩戴后可见结构”，不得用平铺状态臆测佩戴效果。
- 侧视图、背视图和动作图必须逐项检查关键结构是否应当可见；被身体、毛发、衣物或道具自然遮挡的部件不得强行显示。
- 不得为了展示卖点而复制绑带、粘扣、开口、旋钮、接口或其他结构。
- 生图后必须逐图对照参考图和真值表做视觉检查；结构不一致的图片不得进入最终交付。

### 垫图与 Prompt 的逐图绑定（强制）

每个 `IMG01`–`IMG07` 章节和 `image_prompt_package.images[]` 必须同时写出：`reference_asset_id`、`reference_asset_path`、`source_asset_id` 与 `backup_asset_ids`。生成执行只能把该复制后的垫图文件作为参考图传入；人工更换垫图后，先更新 manifest 再重新生成对应一图。

主图的垫图只用于还原本品外观，**主图成图仍必须遵守纯白、无叠字、无 Logo/水印、无非售卖道具**。副图的叠字和卖点只可来自已验证的 listing、产品资料和合规清单，不能由素材图猜测。

### 副图文案与构图规范（⛔ 按 visual_profile 分档，v4.1.0）

**动笔写 Prompt 之前，先读档位：**

```
S3_视觉板块/amazon-visual-benchmark-learner/visual_playbook.yaml → global.visual_profile
```

| 读到 | 走哪档 | 这类买家要什么 |
|---|---|---|
| `editorial_dtc` | **A 档**（下方） | 场景和情绪 —— 服饰 · 家居 · 宠物服饰 · 美妆 |
| `spec_dense` | **B 档** | **参数和规格** —— 汽配 · 工具 · 电子 · 五金 |
| `hybrid` | **C 档** | 前几张场景，后几张参数 —— 户外 · 运动 · 母婴 |
| **没有 playbook** | A 档（默认） | ⚠️ 必须在输出里标注「**未加载 playbook，走默认品牌风**」 |

> ⛔ **这是本 Skill 最容易出错的地方。**
> 上一版把 A 档写成了全品类唯一标准，结果视觉学习环节学到的东西**全部被压掉** ——
> learner 明明数出「这个类目 69% 的副图带参数标注」，出图还是一张张留白小清新。
>
> **档位选错，后面写得再细都是白写。**

---

## A 档 · `editorial_dtc` 品牌叙事型

> **设计哲学**：不是把卖点贴到图上，而是让场景、产品、文案一起讲一个更有情绪的故事。参考 Aesop、Allbirds、Casper、Apple editorial campaign —— **留白呼吸感 > 信息堆砌，品牌语气 > 功能罗列，产品存在感 > 装饰氛围**。
>
> **参考实现**：V4（基于迷你便携电磁炉验证）是 **A 档**的标准参考，**不适用于 B / C 档**。

#### 🎨 统一美术方向

| 元素 | 规范 |
|:---|:---|
| **整体气质** | modern American DTC editorial product photography |
| **光线** | soft natural daylight / warm restrained color grading / 避免过硬高对比商业棚拍感 |
| **色彩** | muted whites, oatmeal beige, light stone, natural wood, matte ceramics |
| **文案密度** | 一张图只说一件事；优先一句话，不做功能清单 |
| **版式** | 文字跟着构图走，利用墙面、窗边、台面留白，自然融入场景 |
| **禁止** | ❌ 统一横条 ❌ 统一底栏 ❌ 绿色渐变徽章 ❌ 大量 ✓/✗ 图标 ❌ 每张图同一模板 |

#### 🧭 Product Hero Preservation（强制规则）

> ⛔ **下表是通用规则，右列的电磁炉只是举例。**
> 开跑前必须先把 `{}` 里的东西换成本品的 —— 换不出来说明产品认知没做够，回 Phase 0。

| 规则 | 通用要求 | 电磁炉案例（怎么套） |
|:---|:---|:---|
| **第一眼识别** | 买家 1 秒内必须看清这是 **{产品品类}**，而不是只看到配件或环境 | 看清是电磁炉，不是只看到锅和食物 |
| **产品占比** | 生活方式图 / 功能图中，产品占画面 **22%–32%** 视觉权重，不可过小 | 同左 |
| **关键部位可见** | **{本品最能体现价值的 1–2 个部位}** 必须尽量可见；配件、手部、环境物不可遮挡主体超过 **25%** | 黑色微晶面板 + 前侧旋钮 |
| **交互件露出** | **{买家会摸/会操作的部件}** 至少露出 **70%** | 前侧旋钮 ≥70% |
| **同色规避** | 避免 **{本品主色}** 与 **{周边常见物件}** 融成一片 | 白机身 → 避免配纯白锅，改不锈钢/玻璃/铸铁 |
| **主体占比上限** | **{最常同框的配件}** 不得盖住本品可见面超过 **85%–90%** | 锅直径 ≤ 可见面板的 85–90% |
| **产品区域** | 生活方式图中产品位于**下三分之一核心视觉区** | 同左 |
| **边界分离** | 产品与承载面之间保留柔和阴影或轮廓分离，避免"白贴白、灰贴灰" | 产品/锅具/台面之间 |
| **道具克制** | 前景与产品周围道具必须克制，不抢视觉重心 | 同左 |
| **优先级** | 当情绪氛围与产品识别冲突时，**优先保产品 hero 感** | 同左 |

**换品类怎么填**（举两个例）：

| | 关键部位 | 交互件 | 同色规避 |
|---|---|---|---|
| **宠物雨衣** | 反光条 + 帽檐结构 | 魔术贴/卡扣 | 深色雨衣 → 避免深色背景和深毛色狗 |
| **汽车脚垫** | 卡扣定位孔 + 边缘凸起 | 卡扣 | 黑色脚垫 → 避免全黑车内实拍，用浅色车内或纯色底 |

#### ✍️ Negative Space Reservation（v4.2.0 · 只留白，不写字）

⛔ **旧的 `[EDITORIAL COPY INTEGRATION]` 已废止** —— 它要求模型把文案渲染进图里，
正是七图字体不一致的来源。现在 Prompt 只负责**把位置腾出来**，字由渲染器画。

```
[NEGATIVE SPACE RESERVATION] Reserve a clean, uncluttered negative space area
in the upper-left third of the frame — a plain wall, out-of-focus air, or a
quiet surface. No product parts, no props, and no strong texture in that area;
text will be composited there in post-production. Keep the area tonally even
so dark type reads cleanly on it.
CRITICAL: render NO text, letters, numbers, labels, badges, or bars anywhere
in the image.
```

文案本身写进 `S3_视觉板块/amazon-listing-image-designer/overlay_tasks.yaml`，headline 仍守 A 档口径：
**理想 2–4 个单词，最多 6 个**，一张图只说一件事。

---

## B 档 · `spec_dense` 参数密集型

> **设计哲学**：买家在这个类目里**是来查参数的**，不是来看氛围的。
> 一张图少给一个关键参数，他就去开下一个 listing。
> **信息完整 > 留白呼吸感，看得清 > 有格调。**

**⛔ A 档那些禁令在这里全部反过来** —— 统一底栏、参数区、图标清单、✓/✗ 对比，
在这个档位里**不是廉价，是专业**。买家靠它们快速比价。

#### 🎨 统一美术方向

| 元素 | 规范 |
|:---|:---|
| **整体气质** | clean technical product photography · 类目电商标准视觉，不追求杂志感 |
| **光线** | even studio lighting，**避免强氛围光造成参数区反光看不清** |
| **色彩** | 高对比：白/浅灰底 + 深色文字；强调色只用一种（红或橙）标关键数值 |
| **文案密度** | **每张副图 3–8 个参数点**，该密就密 |
| **版式** | **允许统一底栏 / 侧栏参数区 / 顶部标题条** —— 六张图版式一致，买家扫得快 |
| **必备元素** | 尺寸标注线 · 材质说明 · 兼容性列表 · 包装清单 · ✓/✗ 对比表 · 爆炸图（有结构的话） |
| **禁止** | ❌ 参数写在低对比背景上 ❌ 关键数值只出现在图外文案 ❌ 为了美观牺牲可读性 |

#### 📐 副图信息骨架（B 档推荐序）

```text
主图（纯白）→ 尺寸规格图（标注线 + 数值）→ 材质/工艺特写（带说明）
→ 兼容性/适用范围（清单或图标矩阵）→ 安装或使用步骤（分步编号）
→ 包装清单（摆开 + 数量标注）→ 竞品对比（✓/✗ 表）
```

> 实际取几张按 `visual_playbook.main_images.information_hierarchy` 里学到的来，
> 上面是没学到东西时的兜底骨架。

#### ✍️ Spec Zone Reservation（B 档专用，v4.2.0 · 只留带状区，不写字）

⛔ **旧的 `[SPEC OVERLAY INTEGRATION]` 已废止**。参数标注恰恰是最不能交给模型的 ——
数值会被它「顺手改好看」，六张图的底栏也对不齐。现在底栏由渲染器统一画。

```
[SPEC ZONE RESERVATION] Keep the bottom 15% of the frame clean and tonally
even — a flat surface or plain backdrop with no product parts and no props.
A specification bar will be composited there in post-production. Position the
product so that every part requiring annotation is fully visible and
unobstructed above that zone.
CRITICAL: render NO text, letters, numbers, dimension lines, labels, or
information bars anywhere in the image.
```

参数写进 `overlay_tasks.yaml`：`type: band` 画统一底栏，`barlabel_l/c/r` 写数值，
每图 3–8 项，强调色只给唯一一个决策关键数值。**六张副图底栏落位天然一致** ——
这不再靠模型自觉，是同一份积木坐标画出来的。

⛔ **数值真值锁照旧**：图上每个数字必须来自 S1 Skill 夹里已验证的产品资料（`03_product_facts.yaml`、`01_product_profile.yaml`），
不许目测垫图、不许推算。拿不到就不标。

**⛔ 数值真值锁**：图上标的每一个数字，必须来自 S1 Skill 夹里已验证的产品资料（`03_product_facts.yaml`、`01_product_profile.yaml`），
**不许从垫图上目测，不许推算**。拿不到的参数就不标，不许编。

---

## C 档 · `hybrid` 混合型

> **设计哲学**：前半场讲场景，后半场摆参数。
> 买家先被氛围吸引进来，再靠参数说服自己下单。

**分配规则（6 张副图）：**

| 图序 | 走哪档规范 | 内容 |
|---|---|---|
| 副图 1–2 | **A 档** | 使用场景 · 情绪表达 —— 负责"我想要" |
| 副图 3–4 | **B 档** | 核心参数 · 尺寸材质 —— 负责"我能用" |
| 副图 5–6 | 按 playbook | 学到什么补什么（对比 / 包装 / 真人 / 细节） |

**衔接要求**：前两张的色调和后四张必须**用同一套色板**，
否则六张图看起来像两个品牌的素材拼在一起。

---

### 输出格式（每张图必须包含以下内容）

#### 📸 图 {N}: {类型名称}

**🇬🇧 英文 Prompt（GPT-image-2 或 Nano Banana 2/Pro，根据用户选择）**
```
[完整英文提示词，含场景描述 + 文案内容及排版要求 + 尺寸]
```

**🇨🇳 中文翻译**
```
[对应的中文翻译]
```

**📝 文案与落位提取表（副图专用，Editorial DTC American Aesthetic）**

> 主图（图 1）无需叠加文字，此项留空。
> 副图（图 2-7）的文案不再默认走固定 overlay 条形模板，而是**根据构图把一句短文案自然融入留白区域**。
> **Editorial DTC 文案原则**：
> 1. 标题精简：只说一件事，**理想 2–4 个单词，最多 6 个单词**
> 2. **情感优先**：优先传递使用后的感受，不先报参数
> 3. **产品优先**：文案不能压过产品主体，不能为了“好看”牺牲识别度
> 4. **合理埋入关键词**：关键词只做自然融入，不强塞
> 5. 功能说明如有需要，优先做成**局部 micro-copy**，贴近对应结构点

| 位置 | 英文文案示例 | 中文翻译 | 风格说明 | 埋入关键词 |
|:---|:---|:---|:---|:---|
| 墙面/窗边/留白区 | [如：`Dinner, simplified.`] | [晚餐，简单一点。] | sentence case / medium weight / 无横条无底栏 / 自然融入构图 | [如：dinner] |
| 局部 micro-copy | [如：`3500W power`] | [3500W功率] | 小字、轻量、贴近结构点、只在必要图中出现 | [如：3500w] |

**✅ 合规备注**
- [本图的合规检查说明]

### 模型 A：GPT-image-2 Prompt 工程规范

- **语言**：英文自然语言描述，具体视觉细节优先（材质、光线方向、角度、氛围）
- **材质描述**：精确到具体用词，如 `crystal-clear GPPS plastic` / `natural bamboo with visible wood grain`
- **光线指定**：具体来源，如 `soft diffused studio light from upper left`
- **文案排版**：⛔ **v4.2.0 起不写进 Prompt** —— Prompt 只按 `visual_profile` 决定
  **把哪块地方留干净**：
  - `editorial_dtc` → 留左上或左下 1/3 负空间
  - `spec_dense` → 留底部 15% 干净带状区
  - `hybrid` → 副图 1–2 留负空间，副图 3–4 留底部带状区
  文案本体一律进 `overlay_tasks.yaml`。
- **Prompt 结构顺序**：`[场景描述]` → `[产品 hero 保护要求]` → `[留白规则：A 档用 NEGATIVE SPACE RESERVATION / B 档用 SPEC ZONE RESERVATION]` → `[产品结构保真锁定封印句]` → `[尺寸/质量标签]`
- **尺寸指定**：Prompt 末尾写 `Output size: 1600x1600px`（主图）或 `Output size: 1600x2000px`（副图）
- **结尾 quality tag**：⛔ **按档位选**
  - `editorial_dtc` → `photorealistic, ultra-high detail, 4K, commercial product photography`
  - `spec_dense` → `photorealistic, ultra-high detail, 4K, technical product photography, crisp legible annotations`
  - `hybrid` → 场景图用前者，参数图用后者
- **排除项**：单独一行列出，v4.2.0 起**必须包含彻底禁字**：
  `no text, no lettering, no numbers, no labels, no badges, no information bars, no watermark, no logo, no shadows behind product`
- **无需** `--ar` / `--iw` 等参数

### 模型 B：Nano Banana 2 / Pro Prompt 工程规范

- **语言**：英文自然语言描述
- **画面比例**：`--ar 1:1`（主图 1600x1600）或 `--ar 4:5`（副图 1600x2000）
- **参考图权重**：`--iw 1.5~2`（垫图时使用）
- **文案处理**：Nano Banana 对文字渲染能力较弱，默认直接生成核准标题/短句；只有用户主动要求后期叠字时才出无字底图；需要文字时，仅加入核准的短 headline 或 micro-copy，并指定放在留白区域或对应结构点附近<br>
  ⚠️ **`spec_dense` 档慎用 Nano Banana** —— 参数标注多、文字要求高，它渲不准。
  这个档位优先 GPT-image，或者在用户主动要求时**出无字底图后在网页端 / Codex App 或设计软件补标注**
- **风格**：⛔ 按档位 —— `editorial_dtc` 用 `photorealistic, 4K, studio lighting, product photography`；
  `spec_dense` 用 `photorealistic, 4K, even technical lighting, clean spec-sheet layout`
- **排除**：`--no blurry, distorted text, watermark, logo`
- **质量参数**：`--quality 2`（如支持）

### 🎯 【核心 Prompt 原则】产品结构保真锁定公式

> ⛔ **适用范围：所有有实物垫图的产品，无一例外。**
> **2026-07-31 实测教训**：原先限定「机械结构 / 折叠件 / 家电」，
> 宠物服装被判为不适用 → 三轮出图三种错法：
> 丢部件（帽子/反光条/D环全没了）→ 部件位置全错 → 连版型和模特品种都换了。
> **只要有 PAD 垫图，就必须用这条公式。**

**⛔ 保真句必须放在 Prompt 开头第一句，不是末尾。**
2026-07-28 那轮 7 张图全部出对，用的就是开头这一句：

```text
Reproduce the {产品简述} with 100% structural fidelity from {PAD编号}.

例：Reproduce the yellow high-neck, two-front-leg dog raincoat
    with 100% structural fidelity from PAD-01.
```

**⛔ 文字清单不得替代垫图。**
`product_truth_locks`、部件清单这类文字描述**只用于出图后自检**，
⛔ 不得作为 Prompt 主体。
理由：**文字描述不了空间关系** ——「背部有反光条」这五个字，
模型不知道在哪一段、多长、什么角度；描述越细，自由发挥的维度越多。
**结构以垫图为唯一真相，文字只补充垫图看不出的信息（材质名、颜色名）。**

**公式结构：**
`[结构保真句] + [场景/动作描述] + [尺寸/比例指令] + [排除项]`

**产品锁定封印句（完整版，接在开头保真句之后）：**
> The product in this image must be reproduced with 100% structural fidelity from the reference photo. Do not alter, reconstruct, hallucinate, or invent any part of the product's shape, proportions, or surface details. Only modify the surrounding scene, lighting, and environment. No logos or brand text on the product body.

**⚠️ 防变形降维策略**：
对于极易变形的动态物理状态（如"拉出手柄"、"伸缩过程"），AI 通常无法正确理解其机械结构。此时**必须弃用动态手部交互描述，改为使用产品的「静态/收纳/闭合」状态（如最短、最紧凑的形态）作为参考垫图，并将其放置于精美的「静物场景」中**（如：放置在沙发上、平铺在桌面上）。这能确保大模型出图时产品外观 100% 完美无变形。

**💡 物理特征精准对齐规则**：
- **严格对照实物参数**：必须基于实际垫图和参数设计细节（如单物理旋钮 vs 双旋转电极、圆形面板 vs 方形面板），不能让 AI 自由散漫脑补。
- **单款变体净化**：如产品存在多款式（白色圆形款 vs 黑色方形款），需先向用户确认首选款；若用户明确指明“只做白色圆形款，不保留黑色款”，必须将全套提示词（主图 + 副图）进行彻底清洗，删除所有与黑色款、触摸屏相关的表述，专注于唯一变体以保证结构输出一致。
- **品牌规避测试优先**：在出图测试阶段，遵循“测试为主，先不考虑品牌”的敏捷策略，在 Prompt 中使用中性的品类通称（如 "portable induction cooktop"）替代硬编码的特定品牌，以便于最大化测试图的出图兼容度。

### 输出格式（每张图必须包含以下 5 项）

#### 📸 图 {N}: {类型名称}

**🇬🇧 英文 Prompt（GPT-image-2 / Nano-banana-2，根据用户选择标注）**
```
[完整英文提示词，可直接复制使用]
```

**🇨🇳 中文翻译**
```
[对应的中文翻译，方便用户理解画面内容和调整方向]
```

**📝 文案与落位提取表（副图专用，Editorial DTC American Aesthetic）**

> 主图（图 1）无需叠加文字，此项留空。
> 副图（图 2-7）**必须**在此处生成文案与落位说明，但不再默认使用固定 overlay 模板。
> **Editorial DTC 文案原则**：
> 1. 标题精简：只说一件事，**理想 2–4 个单词，最多 6 个单词**
> 2. **情感优先**：传递"使用后的感受"，而不是先报参数
> 3. **产品优先**：文案不得压过产品主体，不得削弱主体识别度
> 4. **合理埋入关键词**（提升图片 SEO + COSMO 算法权重）
> 5. 如需功能说明，只允许做成**局部 micro-copy**，贴近对应结构点
>
| 位置 | 英文文案示例 | 中文翻译 | 风格说明 | 埋入关键词 |
|:---|:---|:---|:---|:---|
| 墙面/窗边/留白区 | [如：`Dinner, simplified.`] | [晚餐，简单一点。] | sentence case / medium weight / 无横条无底栏 / 自然融入构图 | [如：dinner] |
| 局部 micro-copy | [如：`3500W power`] | [3500W功率] | 小字、轻量、贴近结构点、只在必要图中出现 | [如：3500w] |

**✅ 合规备注**
- [本图的合规检查说明]

---

### 图 1 主图 Prompt 参考模板

```
Product-only photograph on pure white background (RGB 255,255,255).
[产品完整描述：含材质/颜色/结构特征，来自报告提取].
Slight 3/4 angle showing depth and dimension.
Professional studio lighting, zero shadows behind product,
product fills 85% of frame, centered composition.
Square 1:1 aspect ratio, 2000x2000px, photorealistic, 4K,
commercial product photography. --ar 1:1 --no text, watermark, logo, shadow
```

### 图 2-7 副图 Prompt 参考模板（⛔ 按档位选）

#### A 档 · `editorial_dtc`

> ⚠️ 下面的 `{产品名}` `{关键部位}` `{易融背景}` 是占位符，**必须换成本品的**。
> 上一版这里直接写死了 cooktop / black ceramic top / front knob（电磁炉部件），
> 换产品忘了改，出来的图就会莫名其妙。

```
[具体场景描述，关联用户画像和使用场景，让画面自然、真实、有情感共鸣].
[产品在场景中的位置、状态、使用动作].
[周围陪衬物品/环境元素，注意道具克制，不抢产品主体].
[光线: warm natural light / soft diffused window light / golden hour glow].
[情感氛围: cozy / natural / effortless / warm — 避免过度"premium"或"luxury"的生硬描述].
[PRODUCT HERO PRESERVATION] Ensure the {产品名} reads clearly within one second.
{关键部位，如：the black ceramic top and front knob / the reflective strip and
hood / the threaded connector} should remain visible. The product should carry
clear visual weight in frame. Avoid {易融背景，如：cookware that blends into the
white body} ; prefer contrasting materials. Foreground props must not overpower
the product.
[NEGATIVE SPACE RESERVATION] Reserve a clean, uncluttered negative space area
in the {upper-left third / lower-left third} of the frame — a plain wall,
out-of-focus air, or a quiet surface. No product parts, no props, no strong
texture there; text will be composited in post-production. Keep it tonally even.
CRITICAL: render NO text, letters, numbers, labels, badges, or bars anywhere.
[产品结构保真锁定封印句].
Output size: 1600x2000px. photorealistic, ultra-high detail, 4K,
commercial product photography.
```

> ⛔ `{HEADLINE}` 不再进 Prompt。它进 `overlay_tasks.yaml` 的 `text` 字段。

#### B 档 · `spec_dense`

```
[产品在干净背景或极简使用环境中的清晰展示，视角要能看到本图要标注的部位].
[产品状态：完整展示 / 拆解展示 / 局部特写 —— 按本图要传达的参数决定].
[光线: even studio lighting, minimal shadow, no strong ambient tint —
 参数区必须无反光、无阴影遮挡].
[背景: clean white or light neutral gray, high contrast against annotations].
[PRODUCT HERO PRESERVATION] Ensure the {产品名} occupies the primary visual
weight. {关键部位} must be fully visible and unobstructed, since annotations
point to them.
[SPEC ZONE RESERVATION] Keep the bottom 15% of the frame clean and tonally
even — a flat surface or plain backdrop, no product parts, no props. A
specification bar will be composited there in post-production. Position the
product so that every part requiring annotation is fully visible above it.
CRITICAL: render NO text, letters, numbers, dimension lines, labels, or
information bars anywhere.
[产品结构保真锁定封印句].
Output size: 1600x2000px. photorealistic, ultra-high detail, 4K,
technical product photography, crisp legible annotations.
```

> ⛔ **B 档的数值真值锁**：`{最关键的那个数值}` 和所有标注数字，
> 必须来自 S1 Skill 夹里已验证的产品资料（`03_product_facts.yaml` / `01_product_profile.yaml`）—— **不许目测垫图，不许推算**。
> 拿不到就不标。

#### C 档 · `hybrid`

副图 1–2 用 A 档模板，副图 3–4 用 B 档模板，**色板保持一致**。

### 设计策略指引（V4 商用版）

基于产品报告提取的信息，在设计 7 图时遵循以下策略：

1. **图 2 场景图**：选择最核心的使用场景，必须让产品清晰可识别；优先用与机身形成对比的锅具；产品位于下三分之一 hero 区；文案贴合构图留白
2. **图 3 信息图**：像品牌产品细节页，而不是传统参数海报；只保留 3 个必要标注，产品本体要更像 hero object；标注不喧宾夺主
3. **图 4 对比图**：更接近品牌杂志 spread 感，而非硬 split-screen；左图给更多空间不做 50/50；old burner 要明显更旧、更挤、更厚；文案微微偏左，不要机械居中
4. **图 5 功能图**：展示核心功能，但仍需像品牌大片；参数只做局部 micro-copy，不要底部参数条；手部遮挡不超过 25%
5. **图 6 多场景**：主图 45% + 3 辅助格；去掉白底标题块感；标题直接长在拼贴留白中；每格产品 1 秒内可识别
6. **图 7 情感图**：只卖情绪和关系感；功能退后，生活感优先；旋钮至少露出 70%；锅尺寸适中让面板露更多

### 7 图信息去重与 A+ 衔接（强制）

规划前先建立“图片信息联系表”，为每张图记录：买家问题、核心信息、画面类型、文案主题、景别、是否已在其他图片表达。必须满足：

- 7 张图每张只回答一个主要买家问题；相邻两张不得重复同一核心结论。
- 同一卖点最多作为主主题出现一次；再次出现时只能提供新的证据、步骤、参数或使用边界。
- 主图、场景图、结构图、教程图、材质微距、尺寸图、包装/边界图应形成景别和信息节奏变化，避免整套只换背景或模特。
- 交付前输出去重检查表；发现两张图的“买家问题 + 核心信息”相同，必须重做其中一张。
- 向 `amazon-premium-aplus-designer` 交接时，必须同时提供 Listing 7 图内容清单。A+ 不得复述 Listing 已完成的主题，应优先解释测量、步骤、材质、使用边界、洗护、收纳和品牌信任等深层信息。

**👤 人种与模特指定规范**：
- **目标市场强对齐**：若产品在美国销售，所有提示词中的人物模特**必须明确指定为美国人 (American people, multi-ethnic，如 Caucasian / African-American / Hispanic)**，杜绝含糊不清的人像描述，以确保生图结果 100% 符合当地本土消费者的面孔特征与情感共鸣，提高点击与转化。

### 副图文案关键词埋入策略

从 `S2_文案板块/{关键词Skill}/06_关键词库报告.md` 与 `S2_文案板块/{关键词Skill}/keyword_package.yaml` 中提取关键词，按以下优先级埋入副图叠加文案中：

| 优先级 | 关键词类型 | 埋入方式 | 示例 |
|:---|:---|:---|:---|
| 🔴 P0 | 精准转化长尾词 | 只在必要时做成局部 micro-copy | "3500W power" → 图 5 局部小标注 |
| 🟡 P1 | Alexa 场景语义词 | 融入短标题或场景描述 | "easy dinner" → 图 2 headline / scene copy |
| 🟢 P2 | Alexa for Shopping 对话词 | 融入对比图的简短 editorial line | "space-saving" → 图 4 headline |
| 🔵 P3 | 核心品类词 | 自然融入信息图标注 | "induction cooktop" → 图 3 结构标注 |

> **⚠️ 绝对合规红线（必须执行）**：
> 1. 在生成副图叠加文案时，**必须严格对照** `S1_选品板块/amazon-ip-risk-assessment/04_IP风险排查与专利分析.md` 与 `S1_选品板块/amazon-ip-risk-assessment/04_compliance_blacklist.yaml` 中的禁词清单。
> 2. **彻底规避**：绝对化用语（如 100% safe, #1, Best Seller）、医疗/杀菌暗示（如 anti-bacterial, non-toxic 如果受限）、以及过度环保宣称（如 eco-friendly，除非有相关认证，否则改用 "zero paper waste" 等客观描述）。
> 3. 关键词埋入必须自然流畅，不可为了塞词而影响文案可读性。每张副图叠加文案中建议埋入 1-3 个关键词。

### 完成 Phase 2 后的过渡话术

输出所有 7 张图的提示词后，向用户说：

> **✅ 7 张图的 AI 提示词已全部生成（含中英双语）。**
>
> 如果您对提示词满意，并希望生成实际图片，请执行以下操作：
> 1. 📎 在聊天框中上传您的**产品参考图**（实物拍摄图、1688 商品图等）
> 2. 🖼️ 告诉我您要生成哪几张图（如"帮我生成图1~图3"或"全部生成"）
>
> 我将基于您的参考图 + 上方提示词，调用所选模型（GPT-image-2 或 Nano-banana-2）为您生成高质量图片。

---

## Phase 3: 出**视觉生产包**（⛔ v4.3.0 起不在 Codex 内生图）

### ⛔ 为什么改

2026-08-11 实测：在 Codex 内逐张生图，一张要 1–2 分钟以上，还有三重开销 ——
① 每张一轮完整 agent 推理；② **生成的图永久留在上下文里**，之后每一轮都要重新
处理，越跑越慢；③ 改图靠文字描述来回猜，远不如网页端所见即所得。

本 Skill 因此与 `amazon-product-video-director` **统一为同一种模式**：

```text
Codex 负责：读资料 → 挑垫图 → 编号入册 → 写提示词 → 出生产包
站外负责：网页端逐张生图（可并行、可视化迭代）
本地负责：回填后跑闸门验收
```

⛔ **本 Skill 不再调用 `imagegen`。** 出到生产包为止。

### Step 1: 确认垫图资产包已就位

Phase 0.5 已经把本品图从产品资料库**物理复制**到 `S3_视觉板块/amazon-listing-image-designer/产品垫图/主副图/`
并重新编号为 `IMG01_…`–`IMG07_…`，登记在 `S3_视觉板块/amazon-listing-image-designer/产品垫图/asset_manifest.yaml`。

⛔ 这一步是生产包的地基，**不能跳**：
- 每个要出的图位都必须有绑定的 `asset_id`
- 没有可信垫图的图位标 `blocked_missing_reference_asset`，并列出要向供应商补的角度
- 竞品/参考图只用于学习构图，**绝不进本品垫图包**

### Step 2: 写提示词（⛔ 硬上限 80 词）

**2026-08-11 实测验证**：同一张 A+ Hero，350+ 词的长提示词反复出错（产品变形、
比例阶梯、传了正确参考图也不照着画）；砍到 **65 词后一次生成通过**，
且结构保持反而更好。

原因：文字条件和图像条件在抢注意力，**长提示词把参考图的权重挤掉了**。
"每次出问题就加一段约束"这个做法本身，就是让参考图越来越不管用的过程。

**因此本 Skill 的提示词有硬上限：**

| | 规则 |
|---|---|
| 长度 | **≤ 80 词**，目标 60–70 词 |
| 文字只写 | 场景 / 落位 / 留白区 / 尺寸 / 禁字 |
| ⛔ 文字不写 | 产品外观、结构、材质、尺寸数字 —— **全部交给参考图** |
| 出错时 | **一次只加一句**短约束，加完删掉冗余，总长仍守 80 词 |

参考模板（约 65 词）：

```text
<场景一句：材质、光源方向>

The <N> <品类> from reference images 2-<N+1> stand on the <承载面> in the
<落位>, sized exactly as in reference image 1, each with a soft contact shadow
where its base meets the <承载面>.

Keep the <留白区> as empty, evenly lit <背景材质>.

Photorealistic editorial product photography. <宽>x<高>. No text.
```

⛔ **参考图第 1 张固定是比例基准图**（`产品比例基准图.py` 生成），
它替代了所有关于相对大小的文字描述。

**常见症状 → 加哪一句**（一次只加一条）：

| 症状 | 加这句 |
|---|---|
| 排成阶梯状 | `The middle and right items are the same height; only the left one is taller.` |
| 飘着 / 硬边贴上 | `Every base is firmly seated with visible ambient occlusion.` |
| 容器是空的 | `The jars are filled: <逐件写内容物>.` |
| 产品在画面里太小 | `The products fill about <X>% of the frame height.` |
| 留白区混进杂物 | `Nothing in the <区域> — no <逐项列杂物>.` |
| 数量不对 | `Exactly <N> items.` |

---

### Step 2b: 产品外观只交给参考图

**实测教训（2026-08-11）**：提示词里既写「参考图是产品外观的唯一权威」、
又用文字描述了一遍产品长什么样 —— **两者冲突时模型偏向文字**。
结果是：用户上传了正确的产品照片，模型依然按文字里的错误描述画，
反复重传照片也纠正不过来。

因此产品段落只写**身份对应**，不写外观：

```text
[PRODUCTS — IDENTITY ONLY]
  Item A = reference image 2   (leftmost)
  Item B = reference image 3   (middle)
⛔ The reference photos are the ONLY source for each item's shape, lid,
   texture, openings and contents. This prompt deliberately gives no verbal
   description of their appearance — do not infer or invent any detail that
   is not visible in the reference photo.
```

多产品同框时另见 `references/多产品同框出图约束.md`（比例、接触阴影、画布尺寸）。

### Step 3: 生成视觉生产包

```bash
python <home>/scripts/视觉生产包.py {产品根}/生产包配置.yaml
```

产出 `image_production/`：

| 内容 | 说明 |
|---|---|
| `START.md` | 操作卡：传哪些参考图、什么顺序、每张生成多大、下载后存哪 |
| `refs/` | 参考图，**已按上传顺序编号**；第 1 张固定是比例基准图 |
| `prompts/IMGxx.txt` | 每张一个纯文本提示词，直接复制 |
| `回填清单.yaml` | 下载后的落盘路径与文件名，供本地闸门读取 |

⛔ 生产包里的每条提示词**必须在开头注明本图用哪个 `asset_id` / 哪张参考图**，
让用户在网页端一眼知道该传哪张，不用回头翻 manifest。

### Step 4: 站外生成（用户自己做）

- **按 `gen_size` 出大图，不要直接出交付尺寸** —— 实测直接生成 600×450 会糊，
  大图降采样反而更锐。交付尺寸由本地渲染器导出。
- **同一个对话出完整套图**；1–3张返修沿用同一份产品事实。
- **副图文案直接生成在图里，主图无字**。仅用户主动要求时，才走以下历史叠字验收分支。

### Step 5: 回填后本地验收

```bash
python <home>/scripts/扫描底图落字区.py <底图>          # 落字区够不够干净
python <home>/scripts/叠字渲染器.py S3_视觉板块/amazon-listing-image-designer/overlay_tasks.yaml  # 叠字 + 五道闸门
python <home>/scripts/产品色彩审计.py S3_视觉板块/amazon-listing-image-designer/overlay_tasks.yaml # 跨图颜色一致性
```

人工再看两条：**与比例基准图对照产品高度关系**、**产品底缘有没有落实**。

### Step 6: 状态分态（⛔ 不许混为一谈）

| 状态 | 含义 |
|---|---|
| `package_ready` | 生产包齐全（垫图+提示词+回填清单），**站外还没生成** |
| `plates_returned` | 用户已把底图回填到指定路径 |
| `accepted` | 叠字渲染器退出码 0，且人工两条通过 |

⛔ 生产包齐全**不等于**图已生成；图已生成**不等于**验收通过。

### 改图

改图在 ChatGPT 网页端 / Codex App 里完成（自带局部改图）；改好的图原样放回对应回填夹，助手保留原图、修订版另存，不覆盖。

---

## 💡 使用示例

**用户**："帮我设计[你的产品]的 Listing 7 图"

**Agent 工作流**：
1. → 读取 `报告文件/[你的产品]/` 全部 4 个报告
2. → 输出产品认知摘要，等用户确认
3. → 学习主副图规范
4. → 询问用户选择生图模型（A/B）
5. → 生成 7 张图提示词（中英双语），等用户确认
6. → 用户上传产品图 → 调用 generate_image 生成图片

---

## 上下游 Skill 关系

### 上游（输入来源）
- `amazon-market-research` → 提供产品画像、用户痛点、竞品图片分析
- `amazon-listing-copywriter` → 提供文案结构、五点卖点（用于副图文案埋词）
- `amazon-sif-keyword-research` / `amazon-sellersprite-keyword-research` / `amazon-xiyou-keyword-research` → 提供关键词库（用于副图文案关键词埋入）
- `amazon-ip-risk-assessment` → 提供合规禁词清单（副图文案排雷）

### 下游（输出去向）
- `amazon-standard-aplus-designer` → 消费本 Skill 的图片资产用于 A+ 页面
- `amazon-premium-aplus-designer` → 消费本 Skill 的图片资产用于高级 A+ 页面

### 独立使用
本 Skill 可独立调用，只需提供：产品基本信息 + 参考图片。建议搭配上游报告获得最佳效果。

---
## 实战版 · 编排契约

### 7·18 视觉深度与演示直跑补充

运行前读取 `../../templates/report_depth_and_demo_policy.md`。7 图方案必须恢复为“图位战略 + 信息层级 + 垫图绑定 + 完整 Prompt + 审阅标准”，不能只写七句提示词。

每张图至少包含：图位目标、购买阶段、核心异议、主标题/副标题、信息优先级、构图、产品占比、场景/道具、尺寸/文字安全区、reference_asset_id、英文 Prompt、中文解释、负向 Prompt、事实证据、合规检查、与其他图的分工、输出路径。存在 `07A_Listing转化蓝图.yaml` 时还必须记录 `buyer_question_id` 与 `source_evidence_ids`；IMG01 必须白底无叠字；IMG02–IMG07 应覆盖核心功能、结构/尺寸、场景、人群/对象、对比或信任、包装/使用，不得七张都讲同一卖点。

演示模式中，用户明确说“生成主副图/直接用”即视为生成指令，不再询问授权；素材权利未核实写 soft warning。结构、尺寸、配件和材质证据不足时不得虚构，可生成 blocked Prompt 与待补图清单。

acceptance_checks：7/7 图位齐全；每图绑定本品资产；卖点不重复；文字可读且符合 Amazon 图片规则；事实与 Listing/product facts 一致；若存在 07A 蓝图则每图可回溯到 buyer_question_id 与证据；生成结果逐张验收并保留 v1/v2，不覆盖原图。

**v4.2.0 追加**：底图零文字；`overlay_tasks.yaml` 覆盖 IMG01–IMG07（IMG01 为空）；
`叠字渲染器.py` 退出码为 0；`叠字渲染日志.yaml` 已生成。四项任一不满足不得标完成。

### 版本
**3.3.0** salon-v2

### 输入（读取顺序）
1. `S2_文案板块/amazon-listing-copywriter/` 内 listing、`07A_Listing转化蓝图.yaml`（若有）、`07C_Listing校验结果.json`（若有）；`S1_选品板块/amazon-market-research/01_product_profile.yaml`  
2. **视觉叠层** `visual_playbook.yaml`（若有）  
3. **参考图**：`参考图/主副图`、state.visual.refs_main 或 `runtime_data_overlay.yaml` 的 S3 visual_reference（只学表达）  
4. 本品垫图：`S3_视觉板块/amazon-listing-image-designer/产品垫图/asset_manifest.yaml` 指向的复制后文件（工作流模式必需）  
5. keyword 场景词、IP 叠字禁令  

### 视觉学习（可先跑，与企业接入口同理）
当用户说「学习参考图」「视觉学习闭环」或存在参考图目录但无 playbook 时：

1. 读取 `参考图/主副图` 内图片路径列表  
2. 分析类目头部表达：主图背景/占比/角度、信息图标注层级、场景类型、类目特有（汽配接口、服装上身等）  
3. 写出 `S3_视觉板块/amazon-visual-benchmark-learner/visual_playbook.yaml` + `S3_视觉板块/amazon-visual-benchmark-learner/视觉学习报告.md`  
4. **学构图与信息层级，禁止复制 LOGO/商标/版权角色；成品必须用本品垫图**  
5. 再生成 05 方案（prompt 嵌入 playbook 风格词）

也可由资料包：`runtime_data_overlay.yaml` 的 S3 visual_reference + 已学好的 `visual_playbook.yaml`；源资料保持只读，学习结果写入当前产品 run。

### 生成执行层

### 0711 视觉迭代账本（强制）

运行前读取 `../../templates/visual_iteration.schema.yaml`。每一张主图/副图从首版 Prompt 到最终通过版都必须写入 `S3_视觉板块/amazon-listing-image-designer/visual_prompt_iterations.yaml`，至少包含：`asset_id`、`truth_constraints`、`prompt_version`、`prompt_text`、`reference_asset_ids`、`failure_symptoms`、`repair_actions`、`result_path`、`review_status`、`final_version`。

失败不是删除重来：产品结构变形、数量错误、文字乱码、比例不实、场景冲突、卖点重复都要记录“现象→原因判断→局部修复→复验”。v2/v3 必须新增版本，不覆盖 v1。最终文件可另存 approved，但审计链保留。

模型名只能作为 `execution_profile` 的可选实现，不得把某个具体模型或价格写成永恒硬门槛。路由应描述能力要求：参考图保持、局部编辑、文字渲染、尺寸/画幅、批量与逐图修订；当前环境按可用工具选择并在运行日志记录实际模型。

迭代账本照旧记录，但 `result_path` 填的是**用户回填的底图路径**，不是 Codex 生成的。

- ⛔ **不调用 `imagegen`**（v4.3.0 起）。出到 `image_production/` 生产包为止，
  由用户在网页端生成后回填。理由见 Phase 3 开头。
- **视觉生产包**：`python <home>/scripts/视觉生产包.py {产品根}/生产包配置.yaml`
- **改图**：改图在 ChatGPT 网页端 / Codex App 里完成（自带局部改图）；改好的图原样放回对应回填夹，助手保留原图、修订版另存，不覆盖。  
- 方案写入 `S3_视觉板块/amazon-listing-image-designer/08_主副图设计方案.md`；执行状态写入 `S3_视觉板块/amazon-listing-image-designer/visual_generation_manifest.yaml` 与 `S3_视觉板块/amazon-listing-image-designer/visual_generation_run_log.md`

### 输出
- `S3_视觉板块/amazon-listing-image-designer/08A_视觉垫图资产清单.md` + `S3_视觉板块/amazon-listing-image-designer/产品垫图/asset_manifest.yaml`（已存在则复核并引用；不存在则先生成）  
- `S3_视觉板块/amazon-listing-image-designer/08_主副图设计方案.md`（注明 profile、overlay version、是否加载 playbook、每图绑定的 asset_id、参考了哪些 ref 文件名）  
- `S3_视觉板块/amazon-listing-image-designer/visual_generation_manifest.yaml`（写入 IMG01–IMG07 的 ready/blocked 条目；不自动开始出图）
- `S3_视觉板块/amazon-visual-benchmark-learner/visual_playbook.yaml`（若本步完成了学习）  
- **`S3_视觉板块/amazon-listing-image-designer/visual_style_tokens.yaml`**（产品线级样式令牌；首次生成，之后复用）  
- **`S3_视觉板块/amazon-listing-image-designer/overlay_tasks.yaml`** + **`S3_视觉板块/amazon-listing-image-designer/叠字渲染日志.yaml`**（叠字任务与运行留痕）  
- **`S3_视觉板块/amazon-listing-image-designer/visuals/listing/plates/`**（无字底图，中间产物，⛔ 不作为交付物）  
- next：`amazon-premium-aplus-designer`（继续吃同一 visual_playbook **与同一份 visual_style_tokens**）

### Handoff

| 版本 | 说明 |
|---|---|
| **4.2.0** | **叠字两层分离：Prompt 只出无字底图并留负空间，文字改由 `叠字渲染器.py` 按 `visual_style_tokens.yaml` 统一绘制；新增 `overlay_tasks.yaml`；废止 EDITORIAL COPY / SPEC OVERLAY INTEGRATION 两段** |
| 3.7.0 | 0723 融合：消费 Listing 转化蓝图与确定性校验结果，逐图回溯买家疑问和事实证据 |
| 3.6.0 | 0711 融合：逐图 Prompt 版本、失败现象、局部修复、最终审计链；模型名降为 execution profile |
| 3.5.0 | 7·18 视觉深度、结构真值和演示直跑恢复 |
```markdown
【本步完成】amazon-listing-image-designer
【视觉资产】已完成视觉垫图筛选、复制、重新编号；manifest：S3_视觉板块/amazon-listing-image-designer/产品垫图/asset_manifest.yaml
【视觉叠层】已加载 / 新学 visual_playbook.yaml
【样式令牌】S3_视觉板块/amazon-listing-image-designer/visual_style_tokens.yaml（产品线级，A+ 与视频沿用同一份）
【叠字】S3_视觉板块/amazon-listing-image-designer/overlay_tasks.yaml；渲染器退出码 0；日志 S3_视觉板块/amazon-listing-image-designer/叠字渲染日志.yaml
【下一步 Skill】amazon-premium-aplus-designer
【推荐口令】先读 skills/_visual_suite_contract/SUITE_CONTRACT.md 与 visual_suite.yaml，用同一份本品垫图和 visual_style_tokens.yaml 一次出主副图+A+ 整套 Prompt；竞品图只有用户投喂才学方案；产品样子锁垫图。
【MCP】not_required
```
