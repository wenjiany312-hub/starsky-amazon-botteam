---
name: amazon-product-video-director
description: >
  亚马逊产品短视频分镜导演（实战版·主链最终步骤）。
  Codex 读产品数据与垫图 → 分镜 + 关键帧图文件夹 → **跳出 Codex** 到 Grok Imagine 或 即梦 Seedance 生成视频。
  双平台：grok_imagine（video_production_v2 包）| jimeng（@图片 中文投喂包）。
  适用：主图视频、SBV、Premium A+ 演示片、种草。
  触发：产品视频分镜、Grok 视频、即梦视频、A+ 视频、生成分镜垫图。
  沙龙：**属 S3 视觉板块**（与主副图、Premium A+ 同板块，共用本品垫图与 visual_playbook），在广告 S4 之前完成；至少完成分镜与站外生产包，成片回填单独验收。不另装 premium-aplus-video-generator。
version: "3.7.0"
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
salon_role: mainline_final_step

# === SKILL v3.0 标准字段 ===

data_source:
  modes:
    - mode: file
      priority: 1
      fallback: false
      description: "读取报告目录：product_profile、listing_content、image_brief、A+ brief、S3_视觉板块/amazon-listing-image-designer/产品垫图/、S3_视觉板块/amazon-listing-image-designer/visuals/、1688 原始图"
    - mode: user_provided
      priority: 2
      fallback: false
      description: "用户直接提供产品图路径 + 卖点 + 视频用途"
    - mode: mcp
      priority: 3
      mcp_optional: ["image_gen", "image_edit", "image_to_video"]
      fallback: true
      description: "环境支持时生成分镜关键帧；Grok 环境可继续 image_to_video"
    - mode: ask
      priority: 4
      fallback: true
      description: "素材不足时索取最小必要输入"

input:
  required:
    - name: product_images
      type: file_or_directory
      description: "产品实物图/1688 图/Listing 图，至少 1 张结构清晰的主图"
    - name: product_info
      type: user_text
      description: "产品名称 + 核心卖点（无上游文件时必填）"
  optional:
    - name: product_profile
      type: yaml
      upstream: amazon-market-research
      description: "S1_选品板块/amazon-market-research/01_product_profile.yaml"
    - name: listing_content
      type: markdown
      upstream: amazon-listing-copywriter
      description: "S2_文案板块/amazon-listing-copywriter/07_Listing文案.md"
    - name: image_brief
      type: markdown
      upstream: amazon-listing-image-designer
      description: "S3_视觉板块/amazon-listing-image-designer/08_主副图设计方案.md"
    - name: premium_aplus_brief
      type: markdown
      upstream: amazon-premium-aplus-designer
      description: "S3_视觉板块/amazon-premium-aplus-designer/09_高级A+方案.md"
    - name: ip_compliance
      type: yaml
      upstream: amazon-ip-risk-assessment
      description: "S1_选品板块/amazon-ip-risk-assessment/04_compliance_blacklist.yaml"
    - name: target_platform
      type: user_text
      default: "ask"
      description: "jimeng | grok_imagine | ask（**默认即梦 Seedance**；Grok Imagine 保留为备选，需用户显式指定）"
    - name: video_goal
      type: user_text
      default: "转化型"
      description: "转化型 / 展示型 / 种草型 / 讲解型"
    - name: duration
      type: user_text
      default: "30s"
      description: "15s / 30s / 42s / 45s"
    - name: aspect_ratio
      type: user_text
      default: "auto"
      description: "9:16 竖版主图视频；16:9 横版 SBV/A+；auto 按平台与用途推断"
    - name: style_direction
      type: user_text
      description: "高级感 / 家居感 / Editorial DTC / 对比型"
    - name: keyframe_model
      type: user_text
      default: "ask"
      description: "GPT-image-2 | nano_banana | grok_imagine | ask"

output:
  format: markdown_yaml
  schema_version: "3.0"
  structured:
    - name: video_storyboard
      type: markdown
      filename: "S3_视觉板块/amazon-product-video-director/14_产品视频分镜.md"
      description: "分镜脚本 + 每镜头关键帧提示词 + 垫图映射 + 平台分支说明"
    - name: video_prompt_package
      type: yaml
      filename: "S3_视觉板块/amazon-product-video-director/14_video_prompt_package.yaml"
      description: "结构化总包（含 target_platform 与双分支字段）"
    # --- jimeng 分支 ---
    - name: jimeng_web_clip_prompts
      type: text
      filename: "jimeng_web_clip_prompts.txt"
      when: "target_platform == jimeng"
    - name: jimeng_upload_manifest
      type: yaml
      filename: "jimeng_upload_manifest.yaml"
      when: "target_platform == jimeng"
    # --- grok 分支 ---
    - name: video_production_v2
      type: directory
      path: "S3_视觉板块/amazon-product-video-director/video_production_v2/"
      when: "target_platform == grok_imagine"
      description: "Grok 自包含生产包：manifest + images + prompts + START"
    - name: keyframe_images
      type: directory
      path: "video_storyboards/ 或 S3_视觉板块/amazon-product-video-director/video_production_v2/images/"
      description: "分镜垫图资产（用户确认后生图）"
    - name: video_asset_manifest
      type: yaml
      filename: "S3_视觉板块/amazon-product-video-director/视频垫图/asset_manifest.yaml"
      description: "从产品资料库筛选、复制并编号的视频参考图；每个镜头必须绑定资产"
    - name: video_generation_manifest
      type: yaml
      filename: "S3_视觉板块/amazon-product-video-director/video_generation_manifest.yaml"
      description: "关键帧、站外视频片段与成片回填的执行状态"
    - name: video_acceptance
      type: markdown
      filename: "S3_视觉板块/amazon-product-video-director/14C_视频回填验收.md"
      description: "站外片段/成片回填后的结构、合规、时长与拼接验收记录"

downstream:
  - skill: amazon-listing-image-designer
    note: "视频风格与 Listing 7 图双向校正"
  - skill: amazon-premium-aplus-designer
    note: "A+ 视频区建议可回写；沙龙视频只走本 Skill，不另调 premium-aplus-video-generator"
  - skill: starsky-fullchain-workflow
    note: "沙龙教学全链：更新 workflow_state 的 S3.sub_status.video；视频属 S3 视觉板块，位于广告 S4 之前"
  - skill: amazon-ops-workflow
    note: "运营四场景总控（广告链 4.0 的总控）；视频成片就绪后供场景C判定 SB Video capability"

fallback:
  when_file_missing: ask
  when_context_insufficient: ask
  user_prompt_template: |
    请至少补充以下 2–3 项：
    1. 产品主图或 1688 结构图（完整路径）
    2. 核心卖点 3 条
    3. 视频用途（主图视频 / SBV / Premium A+ / 种草）和时长
    4. 目标平台：Grok Imagine 或 即梦
    5. 画幅（9:16 或 16:9）
---

### R03 当前提示词规则（覆盖旧版默认叠字路径，历史示例保留）

R03 新案以共享套图契约的“R03 验收与交付”为准；旧默认叠字/固定模块数/旧投喂路径/旧步骤号只作历史参考。产品说明书、总规划是两处确认点；其余步骤按既定选择执行，不逐项索要批准。用户只看结论、报告、模型和下一步，不输出内部字段或原始检查 JSON。


先读取已确认产品说明书和视觉总规划。默认用原有 `pack_gpt_web_feeds.py` 的 R03 分支，词库用原生限量选择器与三层合规扫描；block 拒交。产品垫图锁轮廓、缝线、拉链、扣具和提手，背景/光线/构图重新生成。不得添加说明书没有的结构或使用待核数量。主图白底无字，副图/A+默认在画面里直接生成已核准文案；有不附送道具时固定位置写清 `Tools not included`，手机可读。后期叠字仅用户主动要求时用。提示词文件纯粘贴内容，操作/上传顺序/时长放投喂说明；尺寸写进提示词，回填仍须实测后处理。默认 B 网页端同一个对话出一整套；A 只用于1–3张返修。详见共享契约，不再逐张新开对话。


## 我的身份（Amazon Product Video Director）

我是产品视频分镜导演：把产品事实、静态视觉资产和转化目标编成可执行的关键帧与平台提示词包。Codex 负责分镜和投喂包；最终 mp4 在 Grok 或即梦站外生成，未生成时绝不把 prompt 当成成片。

## Schema 契约（v3）

### 📥 读取（Inputs）
- **`S1_选品板块/amazon-market-research/01_product_profile.yaml`** ← 外观、材质、尺寸、卖点、场景、人群
- **`S2_文案板块/amazon-listing-copywriter/07_Listing文案.md`** ← 标题、五点、品牌语气
- **`S3_视觉板块/amazon-listing-image-designer/08_主副图设计方案.md`** ← 7 图风格、hero 规则、垫图路径
- **`S3_视觉板块/amazon-premium-aplus-designer/09_高级A+方案.md`** ← A+ 文案与模块图（A+ 片优先）
- **`S1_选品板块/amazon-ip-risk-assessment/04_compliance_blacklist.yaml`** ← 视频禁词
- **`S3_视觉板块/amazon-listing-image-designer/产品垫图/` / `inputs/1688_mcp/` / `pads/`** ← 结构真图
- **`S3_视觉板块/amazon-listing-image-designer/产品垫图/asset_manifest.yaml`** ← 已验证的 Listing/A+ 本品资产与原始来源
- **`S3_视觉板块/amazon-listing-image-designer/visuals/` / `S3_视觉板块/amazon-premium-aplus-designer/aplus_visuals/` / `video_storyboards/`** ← 已生成静图可复用
- **`00_总控/starsky-fullchain-workflow/runtime_data_overlay.yaml` 的 S5 visual_reference/product_case 条目（若有）** ← 只学习镜头节奏、禁用元素或表达，不得替代本品结构垫图

### 📤 写入（Outputs）
| 平台 | 主交付 |
|:---|:---|
| **共用** | `S3_视觉板块/amazon-product-video-director/14_产品视频分镜.md`、`S3_视觉板块/amazon-product-video-director/14_video_prompt_package.yaml` |
| **grok_imagine** | `S3_视觉板块/amazon-product-video-director/video_production_v2/` 全套 + `S3_视觉板块/amazon-product-video-director/video_production_v2/START.md` |
| **jimeng** | `jimeng_web_clip_prompts.txt` + `jimeng_upload_manifest.yaml` |

编号：新项目固定使用 **14_** 号段。旧项目 `10/10A/10B/10C` 只读兼容，不得作为新产物编号。
（14_ 号段保持不变以兼容既有 run；阶段归属已由 S5 调整为 S3，编号不随阶段变动。）

### 🔄 工作流契约
1. **先选平台，再写平台专用 prompt** — 禁止把即梦 `@图片N` 语法与 Grok 英文 image-to-video 混在同一 clip 文件里  
2. **两段式**：分镜垫图（静）→ 图生视频（动）；不可跳过垫图映射  
3. **即梦默认产 `video_production_jimeng/` 生产包**（目录契约见 `references/jimeng-seedance-constraints.md`）；Grok 路径产 `S3_视觉板块/amazon-product-video-director/video_production_v2/` 自包含包  
4. 执行前按平台读约束：  
   - **即梦（默认）→ `references/jimeng-seedance-constraints.md`**  
   - Grok（备选，需显式指定）→ `references/grok-imagine-constraints.md`

---

# Amazon Product Video Director（双平台 v3.0）

## 统一资料接入约束（00C，必读）

开始本 Skill 前，若当前产品 run 存在 00C 资料接入报告或 Overlay，必须先读它们，应用其中的可用来源、事实边界、合规约束和 MCP 补数计划；再读取本任务产物并执行。用户已提供未整理资料但没有 00C 时，先执行资料接入，不要求用户整理。读取 ../../templates/data_intake_read_contract.md 作为权威规则。

> **用户使用指南**：同目录 `用户使用指南.html`  
> **参考案例**：`04-案例/园艺播种移苗器_1031332473737/video_production_v2/`（Grok 量产样板）

## 我的身份

我是亚马逊产品短视频导演。我交付的是：

- 可拍板的分镜 + 产品结构真值表  
- 可出图的分镜关键帧 Prompt（及可选实图）  
- **按平台分流** 的可执行视频包（Grok 或即梦）  

**沙龙口径（第二期）**：
| 项 | 说明 |
|:---|:---|
| **本 Skill = 唯一视频入口** | Grok / 即梦 都走本 Skill；**不进**独立 `amazon-premium-aplus-video-generator` |
| Codex 职责 | 读 profile/文案/A+/垫图 → 真值表 → 分镜 → 关键帧图文件夹 + 平台投喂包 |
| 平台职责 | **跳出 Codex**：Grok 网页/Agent 或 即梦网页 自己账号生成 mp4 |
| 演示 | 讲师可用 Grok 网页演示一段；**讲解不主推**（费钱） |

---


## ⛔ 消费者可见文案统一契约（v3.6.0 新增 · 必读）

**执行前必读 `../../templates/consumer_visible_copy_contract.md`。**

实测事故（2026-07-27）：`amazon-listing-copywriter` 三轮修掉的「高频低熵泛化」在 **A+ M1 副标题上原样复发** —— 五点里被判掉的 `fits naturally into everyday routines`，在图上变成了 `A simple place for everyday essentials.`。三个视觉 Skill 里「典型/benefit/高频低熵/泛化」命中数**全为 0**。

> **判定口径：只要买家能看到，就归该契约管。** 载体是文本框还是像素，不改变它是文案的事实。图上的字、A+ 模块文案、视频字幕与首帧文字，全部适用。

四条硬规则：**R1** 禁内部备注（缺失事实静默省略）｜**R2** 典型性闸门（丢弃「与品类名近重复 / 高频低熵 / 同义改写」三类）｜**R3** benefit gate（描述 ≠ 卖点）｜**R4** 禁写清单不连坐功能定义（禁 `airtight` ≠ 禁「盖上挡灰」）。

**副图与 A+ 字数比 Listing 少，典型性要求反而更高** —— 一张图只承载一个卖点。

**自检强制写进 run log**：逐条列出图上文字原文 + 四关判定结果，只写"已自检通过"不算。

---
## ⛔ 字幕两层分离契约（v3.7.0 新增 · 必读）

**执行前必读 `../amazon-listing-image-designer/references/叠字渲染契约.md`。**

图片链已经解决了「文字各画各的」。视频这边**问题一样，而且更要命**：

> **SBV 静音自动播放。** 亚马逊不给声音，所有卖点只能靠**画面文字**传达。
> 字幕在视频广告里不是装饰，是主要信息通道。

而本 Skill 旧版把字幕甩给剪映人工做（"复杂 slogan / 长字幕 → 后期叠加"），
结果就是：视频字幕的字体、字号、颜色和 Listing 7 图、A+ 全都对不上。
买家从广告点进详情页，看到的是两套视觉。

### 做法：字幕走同一个渲染器，产出透明 PNG

⛔ **不需要装 ffmpeg，不改你的剪映工作流。**

1. 字幕条目写进 **主副图那一份** `S3_视觉板块/amazon-listing-image-designer/overlay_tasks.yaml`
   （⛔ 不新建 `overlay_tasks_video.yaml` —— 契约校验器判「同物异名」硬错误）
2. 每条：`canvas: video_16x9`、`profile: video_subtitle`、**`plate: transparent`**
3. 跑渲染器 → 输出**透明底 PNG**到 `S3_视觉板块/amazon-product-video-director/video_exports/subtitles/`
4. 在剪映里把这些 PNG 拖到对应时间点当叠加层

```bash
python <home>/scripts/叠字渲染器.py {产品根}/S3_视觉板块/amazon-listing-image-designer/overlay_tasks.yaml
```

**结果**：视频字幕与 Listing 图、A+ 用的是同一份 `visual_style_tokens.yaml` ——
字体、字号、颜色、安全区完全同源。

### 硬规则

- **⛔ 字幕必须带 `stroke`（推荐 3–5）或 `scrim`** ——
  叠在未知镜头上，浅色画面会把白字吃掉。渲染器会拦，不带描边直接判失败
- 字幕落位只用 `vid_lower_third` / `vid_lower_sub` / `vid_upper_third` /
  `vid_endcard` / `vid_endcard_sub` —— 这几块都收在**中央 56.25% 宽度**内，
  16:9 母版裁竖版时字幕不会被切掉
- 一个镜头最多两块文字（`video_subtitle` 档限制）
- 即梦 prompt 里**照旧一个字都不许写** —— 这条旧版就是对的，继续执行
- 文字内容仍受 `../../templates/consumer_visible_copy_contract.md` 四条硬规则管辖

---

## Phase 0：产品认知 + 素材盘点（强制）

### Step 0.1 定位目录
确认 `{产品根}`，扫描上游 Skill 夹（S1 产品事实、S2 文案/词包、S3 主副图方案、垫图与成图）以及用户指定的产品素材目录；本 Skill 产物写 `S3_视觉板块/amazon-product-video-director/`。

### Step 0.2 依次读取

| 文件 | 提取重点 |
|:---|:---|
| product_profile | 外观、材质、颜色、尺寸、卖点、场景、人群 |
| listing_content | 五点、语气、差异化 |
| 08_主副图设计方案 | 视觉风格、hero 规则、垫图路径 |
| 09_高级A+方案 | A+ 封面句、模块静图、视频区建议时长 |
| ip_compliance | 禁词、外观规避 |
| 1688 / 产品垫图 | **结构真图路径** |
| aplus_visuals / visuals | 可复用场景与结构模块图 |

### Step 0.3 产品结构真值表（强制）

至少记录：固定部件、活动部件、连接方式、正反面、开合态、关键交互部位、**绝对不可新增/删除的部件（含数量，如 TWO arms）**。

穿戴类 / 机械类 / 折叠类：动作镜头优先 **静态或最简动作** 垫图。

### Step 0.4 素材盘点表（必须输出）

| 素材 ID | 文件路径 | 来源 | 用途 |
|:---|:---|:---|:---|
| PAD-01 | … | 1688 | 结构锚点 |
| VIS-01 | … | A+/Listing | 光线/场景 |
| SB-01 | video_storyboards/… | 已有分镜 | 可复用 primary |

### Step 0.5 认知摘要
输出后等用户确认，再进 Phase 1。

### Step 0.6：视频垫图资产准备（强制）

视频不直接“随手引用”主副图或 A+ 图片。先读取 `S3_视觉板块/amazon-listing-image-designer/产品垫图/asset_manifest.yaml`、1688 原始媒体、`S3_视觉板块/amazon-listing-image-designer/visuals/`、`S3_视觉板块/amazon-premium-aplus-designer/aplus_visuals/` 和 `runtime_data_overlay.yaml` 的 S5 条目（若有），针对每个镜头执行：资料包只可补充镜头语言，实际结构必须来自本品媒体。

1. 选择 `primary`（产品外观/构图）与必要时的 `anchor`（高风险结构或动作的结构锚点）。
2. **物理复制**到 `{产品根}/视频垫图/关键帧/`，命名 `VID01_S01_primary_*`、`VID02_S01_anchor_*`；不移动原文件，不用快捷方式。
3. 输出 `S3_视觉板块/amazon-product-video-director/14A_视频垫图资产清单.md` 与 `S3_视觉板块/amazon-product-video-director/视频垫图/asset_manifest.yaml`，每项写 `asset_id`、复制后路径、原始来源/哈希、镜头 ID、可支持事实、不可支持事实、`primary|anchor`、人工替换状态。
4. 没有可信主垫图的镜头标记 `blocked_missing_video_asset`；高风险动作无 anchor 时改为静态推拉、建议实拍或删除该镜头，不能让视频模型猜结构。

视频资产包只服务视频，不替代 Listing/A+ 的资产清单；二者可指向同一原图，但必须分别记录其镜头用途。

---

## Phase 1：选择目标平台（强制，v3 新增）

> 📌 **目标视频平台**（必选其一）：
>
> | 选项 | 适用 | 说明 |
> |:---|:---|:---|
> | **G. Grok Imagine**（推荐 A+ / 已测防变形产线） | Premium A+ 演示、英文市场、可走 API/Agent | 输出 `S3_视觉板块/amazon-product-video-director/video_production_v2/` |
> | **J. 即梦 Seedance 2.0** | 主图竖版、国内工具链、剪映生态 | 输出 jimeng 投喂包 |
>
> **默认推断（用户未选时先问；可建议）**：  
> - 用途含 Premium A+ / 用户提到 Grok / Imagine / 已有 video_production_v2 → 建议 **G**  
> - 用户明确即梦 / Seedance / @图片 → **J**  
> - 主图 9:16 且用户在国内工具流 → 可建议 **J**，但仍询问  

**平台默认（2026-07-26 变更）：** 用户未指定平台时**默认即梦 Seedance**。时长形态**不准默认替用户选**，必须先走 Phase 1.2。画幅按用途确定，不在现场承诺长片一次成功。

| 平台 | 状态 | 何时用 |
|---|---|---|
| **即梦 Seedance** | ✅ **默认** | 产品视频生成的首选，不再询问 |
| Grok Imagine | ⭕ 保留备选 | 仅在用户**显式指定**时使用；不主动建议 |

> 变更理由：即梦对**产品保真**（@图片引用 + 封印句）的控制力更强，是电商产品视频的更稳路径。
> Grok 的 `video_production_v2` 自包含包与 API 批量能力仍保留，未来若需大批量可回切。

选 **G** 后内化：`references/grok-imagine-constraints.md`  
选 **J** 后内化：`references/jimeng-seedance-constraints.md`

## Phase 1.2：时长形态（强制停，等编号）

即梦平台已默认，**还要用户回一行编号**才写分镜包。禁止把「默认 15 秒 / 3 段」当成已选。

| 回 | 形态 | 什么时候建议 |
|---|---|---|
| **1** | 三段短视频（约 5 秒 × 3，可后拼） | 要分开讲 3 个卖点 / 要多条素材 |
| **2** | 一段约 10 秒整片（即梦一次出） | **课堂建议** · Listing 主图视频位 |
| **3** | 先不做视频 | 记 `deferred`，立刻走 S3 收尾并出 `_NEXT_S3到S4.md` |

用户只回「继续 / 好 / OK」不算选定，重出上表。选 3 时本 Skill `--end`，不要编一套没人要的 15 秒脚本。

### 模型分工

| 环节 | Grok 分支 | 即梦分支 |
|:---|:---|:---|
| 分镜垫图 | GPT-image-2 / Nano Banana / **Grok Imagine 静图** | 同左 |
| 动态视频 | **Grok Imagine image-to-video** | **即梦 Seedance 图生视频** |
| 低风险段优化 | 可用 ffmpeg 静图慢推代替 AI 视频 | 同逻辑可降级 Ken Burns |

---

## Phase 1.5：分镜垫图生图模型（必须询问）

> 📌 **分镜垫图（静图）模型**（默认 A）：
> - **A. GPT-image-2** — 写实与细节稳  
> - **B. Nano Banana 2 / Pro** — 参数式 `--ar` / `--iw`  
> - **C. Grok Imagine 静图** — 与后续 Grok 视频同生态，利于风格统一  
>
> 回复 A / B / C。动态平台在 Phase 1 已选定，此处只选静图引擎。

---

## Phase 2：视频策略 + 节奏结构

### Step 2.1 类型

| 类型 | 核心 | 镜头侧重 |
|:---|:---|:---|
| 转化型 | 加购 | 痛点→解决→结果 |
| 展示型 | 看清 | 全貌→细节→功能 |
| 种草型 | 情绪 | 场景→生活→产品入画 |
| 讲解型 | 教会 | 步骤拆解 |

### Step 2.2 时长模板（两平台共用）

#### 15s 转化型（3×5s）
S01 钩子 → S02 卖点演示 → S03 hero 定格

#### 30s 转化型（5×6s）
S01 场景 → S02 痛点 → S03 产品+功能 → S04 使用证明 → S05 收尾

#### 42s Premium A+（案例默认，7–8 段）
参考 `园艺播种移苗器`：  
开场全貌 → **高风险动作 1** → **高风险动作 2** → 结果/种植 → 配件 → 使用边界 → 美景 → 收尾 packshot  

#### 45s 讲解型（7 段）
吸引 → 全貌 → 卖点拆解×3 → 场景 → 总结

### Step 2.3 信息去重
- 每镜一个买家问题  
- 与 Listing 7 图已表达的静态信息不重复；视频补 **过程 / 动态 / 情绪**  
- 高风险机械动作标 `risk: high`

### Step 2.4 画幅（⛔ v3.7.0 更正）

**先读 `../../templates/video_placement_specs.example.yaml`，以那份为准。**
（投放位规格是全平台通用的，⛔ 不要复制进每个产品目录 —— 那样改一次要改九处。
渲染成片后跑 `python <home>/scripts/视频规格校验.py {产品根}/video_exports` 逐条核。）

| 用途 | 画幅 | 说明 |
|:---|:---|:---|
| **Listing 主图视频** | **16:9** | ⛔ **旧版本写的 9:16 是错的**。商品详情页视频是横版 |
| Premium A+ 视频模块 | 16:9 | |
| SBV 横版 | 16:9 | 主力位 |
| SBV 竖版 | 9:16 | ⛔ 先在广告后台确认本站点支持，规格文件里默认 `enabled: false` |

> **⛔ 老 run 要回头看一眼**：2026-08-09 之前按本 Skill 出的主图视频若是 9:16，
> 在详情页大概率是两侧加黑边显示的。规格以 Seller Central 实际后台为准，
> 核实后把 `video_placement_specs.yaml` 的 `last_verified` 改成当天。

### Step 2.4b 一次分镜、三处投放（16:9 母版 → 竖版）

三个位都要片子，但**不要为每个位重做分镜**。做法是出一条 **16:9 母版**再派生。

**实算过**（1920×1080 → 1080×1920）：

| 派生方式 | 代价 |
|---|---|
| **pad 补边（默认）** | 画面完整保留，上下各补 34% 的带 |
| crop 中央裁切 | 只剩中央 **31.6%** 宽度可用，左右共切掉 68.4% |

> ⛔ **默认走 pad。** 31.6% 太窄，按那个约束做分镜等于放弃 16:9 这个画幅。
> 补出来的两条带**不要留空**（那就是"横版加黑边"，很懒）——
> 上带放主标题、下带放规格短句，做成**专门设计过的竖版**。
>
> ⛔ 旧版本这里写的「中央 56.25% 安全区」是**算错的**：
> `(9/16) ÷ (16/9) = 31.6%`，不是 56.25%。而且这条路本身就不推荐。

**字幕不受此约束** —— 横版竖版各渲染一份透明 PNG（同一份 `overlay_tasks.yaml` 里，
一条 `canvas: video_16x9`、一条 `canvas: video_9x16`，文字内容相同）。
令牌一样，两版字体色板天然一致。

细则见 `../../templates/video_placement_specs.example.yaml` → `vertical_derivation`。

---

## Phase 3：分镜表 + 双平台 Prompt（只出方案，不擅自长视频）

每个镜头 **必须** 字段：

| 字段 | 说明 |
|:---|:---|
| `scene_id` / `clip_id` | S01 / clip_01 |
| `duration` | 5–7s 优先 |
| `risk` | low / medium / high |
| `scene_goal` | 记住什么 |
| `visual_description` | 中文画面 |
| `camera_direction` | 固定 / 慢推 / 慢拉（禁环绕） |
| `overlay_text` | 后期字幕建议 |
| `voiceover` | 旁白可选 |
| `reference_pad_primary` | 主垫图路径 |
| `reference_pad_anchor` | 结构锚点（high 必填） |
| `video_asset_ids` | `VIDxx`，来自视频垫图 manifest |
| `keyframe_image_prompt_en` | 静图英文 Prompt |
| `keyframe_image_prompt_zh` | 中文翻译 |
| `generation_mode` | `image_to_video` / `static_ken_burns` / `建议实拍` |
| **Grok 专用** `grok_clip_prompt` | 英文 image-to-video 全文 |
| **即梦专用** `jimeng_clip_prompt` | 中文 @引用全文 |
| `negative_constraints` | 禁止列表 |

### 3A — 分镜垫图 Prompt（共用）

```
[场景/构图] → [产品状态与位置] → [光线/氛围] →
[PRODUCT HERO] 产品清晰可识别，关键结构可见 →
[结构保真锁定封印句] →
Output: {aspect_ratio} {size_hint}
```

保真封印句：  
> The product must be reproduced with 100% structural fidelity from the reference photo. Do not alter shape, proportions, colors, materials, or part counts. Only modify scene, lighting, and composition.

### 3B — Grok clip prompt（`target_platform: grok_imagine`）

- 语言：**英文**  
- 结构见 `references/grok-imagine-constraints.md`  
- 每段写入独立文件时仅含该段正文（`prompts/clip_XX.txt`）  
- 真值表量化信息（臂数、直径、颜色）必须写进 Product lock  
- 复杂字幕优先 `overlay_text` 后期加；画面内最多 1–3 词  

### 3C — 即梦 clip prompt（`target_platform: jimeng`）

```
@图片1 作为首帧，产品外观 100% 参考 @图片2 [结构锚点]。
0–Xs：…
运镜：固定/慢推（禁止环绕）
[产品保真封印句中文]
```

细则见 `references/jimeng-seedance-constraints.md`。

### 3D — Phase 3 完成话术

> ✅ 分镜 + 垫图 Prompt + **{Grok / 即梦}** 分段提示词已生成。  
> 下一步：  
> 1）说「生成分镜垫图」→ 我按 primary/anchor 出静图并写入生产包  
> 2）Grok：打开 `video_production_v2`，发 START.txt 内容；或配置 `XAI_API_KEY` 跑 batch 脚本  
> 3）即梦：按 `jimeng_upload_manifest.yaml` 上传，复制 `jimeng_web_clip_prompts.txt`  

---

## Phase 4：关键帧出图与视频执行确认（用户确认后）

完成分镜后，先生成 `S3_视觉板块/amazon-product-video-director/video_generation_manifest.yaml` 与 `S3_视觉板块/amazon-product-video-director/14B_视频执行清单.md`。每段必须列：`clip_id`、时长、primary/anchor 视频垫图、关键帧 Prompt、平台 Prompt、输出路径、风险、状态。

仅当所有演示段都有 ready 的主垫图、关键帧 Prompt 和平台 Prompt 时，显示：

```text
【视频执行条件】Grok 15 秒演示：3/3 片段 ready。
1. 回复「确认生成视频关键帧」——在 Codex 逐帧生成关键帧并写入视频生产包。
2. 回复「先生成视频关键帧 S02」——只生成一个高风险/重点镜头，审阅后继续。
3. 回复「关键帧我在外部生成」——保留 Prompt 与垫图清单，等待你将图片回填到 S3_视觉板块/amazon-product-video-director/视频垫图/关键帧/。
```

未确认前只生成分镜与投喂包；不得声称已生成关键帧、视频片段或 mp4。

### Phase 4.1：分镜垫图/关键帧生图

1. 视觉核对视频垫图 vs 真值表  
2. 用户确认后，优先 `image_edit`（有垫图）/ `image_gen`（纯场景）逐帧生成；外部生成时只接收用户回填的文件  
3. 命名：  
   - 共用可读：`video_storyboards/01_xxx.jpg` …  
   - **Grok 包同步拷贝**：`S3_视觉板块/amazon-product-video-director/video_production_v2/images/clip_XX/01_primary.jpg`，high 段另存 `02_anchor_*.jpg`  
4. 逐帧 QA：部件数、颜色、比例  
5. 刷新 manifest  

### Phase 4.3：成片组装与规格校验（v3.7.0 新增）

段落 mp4 回填到 `S3_视觉板块/amazon-product-video-director/video_exports/clips/` 之后，**不要在剪映里手工导三次**。
手工导三次最容易出的错是三份规格不一致，而这些错要到上传时才被平台拒。

```bash
# 从 templates/video_build.example.yaml 复制一份到 {产品根}/出片配置.yaml
python <home>/scripts/视频出片.py {产品根}/出片配置.yaml
```

它做的事：归一化各段（即梦各段分辨率/帧率/有无音轨常不一致）→ 拼接 →
垫背景音乐 → 母版（**无字幕**）→ 按投放位派生 + 叠**该画幅专属**字幕 → 自动跑规格校验。

**⛔ 不替代剪映。** 调节奏、转场、精修仍在剪映做。两种用法二选一：

| 用法 | 做法 |
|---|---|
| A 节奏简单的产品片 | 直接用本脚本一把出三个位 |
| B 需要精修 | 剪映精修完导出一条 16:9 母版，脚本只做派生+校验：`--from-master 剪映导出.mp4` |

**只想校验、不想组装**（比如全程剪映出片）：

```bash
python <home>/scripts/视频规格校验.py {产品根}/video_exports
python <home>/scripts/视频规格校验.py --card    # 只看规格卡
```

#### ⛔ 三个实测踩过的坑（都不报错，静默出问题）

1. **数字静音把音频码率拖到下限以下**。AAC 编静音几乎不耗码率，
   16s 片子里有 6s 静音，即便给到 `-b:a 192k`，平均也只有 **94kbps**，
   低于 Amazon 的 96kbps 而被拒。调高码率治不好 ——
   **垫一条贯穿全片的背景音乐**才治得好（SBV 本来也要求有音轨）。
2. **收尾卡静默不出现**。字幕 PNG 作为 ffmpeg 输入时若不加 `-loop 1`，
   单帧流很快结束，叠在 1–5s 正常、叠在 12.5–16s **完全不出现且不报错**。
   而品牌和 CTA 恰恰都在收尾卡上。脚本已内置 `-loop 1`。
3. **帧率被带偏**。`fps` 滤镜若放在 overlay 之前，循环图片输入会把时序带偏，
   16.03s 变 18.07s、帧率变 29.335（不在允许列表内）。脚本已把 fps 放到最后。

依赖 ffmpeg / ffprobe：`winget install --id Gyan.FFmpeg -e`

---

### Phase 4.2：站外视频与成片回填

1. Grok/即梦每次只生成一个 `clip_XX`，先高风险镜头，禁止把全部参考图一次塞给长片生成。
2. 用户将每段 mp4 回填到 `S3_视觉板块/amazon-product-video-director/video_exports/clips/clip_XX.mp4`；更新 manifest 为 `returned_by_user`，记录实际时长和平台。
3. 全段齐后，用户可用剪映/CapCut 或本地 ffmpeg 按 manifest 顺序合成；只有真实成片回填至 `S3_视觉板块/amazon-product-video-director/video_exports/{video_slug}_final.mp4` 才可标记视频完成。
4. 生成 `S3_视觉板块/amazon-product-video-director/14C_视频回填验收.md`：逐段检查产品结构、关键部件数量、禁词/夸大声明、时长、画幅、片段顺序和是否已实际生成。未回填 mp4 时只能写 `video_status.package_ready=true`、`video_status.final_accepted=false`。

---

## Phase 5A：Grok 生产包组装（`grok_imagine` 必做）

在案例根目录创建/更新 **`S3_视觉板块/amazon-product-video-director/video_production_v2/`**：

### 必写文件

1. **`image_manifest.yaml`** — clips、risk、路径、output 文件名  
2. **`prompts/clip_XX.txt`** — 每段纯英文  
3. **`images/clip_XX/`** — primary + anchor（路径可先写清单，图未生成时注明 pending）  
4. **`START.txt`** — 用户一句话  
5. **`GROK_AUTO_RUN.md`** — Agent 自动执行说明  
6. **`S3_视觉板块/amazon-product-video-director/video_production_v2/START.md`** — 人类可读操作卡  
7. **`14_产品视频分镜.md`** + **`14_video_prompt_package.yaml`**

### `image_manifest.yaml` 最小结构

```yaml
schema_version: "3.0"
workflow: "grok_agent_auto_run"
target_platform: "grok_imagine"
aspect_ratio: "16:9"
do_not: "Upload all storyboard images at once for one reference_to_video"

clips:
  - id: "clip_01_intro"
    duration: "5s"
    risk: low
    images_dir: "images/clip_01"
    upload:
      primary: "images/clip_01/01_primary.jpg"
    prompt_file: "prompts/clip_01.txt"
    output_filename: "clip_01_intro.mp4"
  - id: "clip_02_action"
    duration: "6s"
    risk: high
    upload:
      primary: "images/clip_02/01_primary.jpg"
      required_anchor: "images/clip_02/02_anchor_structure.jpg"
      fallback_anchor: "images/clip_02/03_anchor_1688.jpg"
    prompt_file: "prompts/clip_02.txt"
    output_filename: "clip_02_action.mp4"
```

### `START.txt` 模板

```text
读取 GROK_AUTO_RUN.md 与 image_manifest.yaml，
按 clip 顺序（先 high risk）使用每段 images/ 与 prompts/，
逐段 image-to-video，禁止一次上传全部图片做长片。
合格片段保存到 exports/，最后给出拼接顺序。
```

### `GROK_AUTO_RUN.md` 要点（Agent）

1. 读 manifest → 按 **high 优先** 顺序生成  
2. 每段只加载该 `images/clip_XX/` 内文件  
3. 优先 `image_to_video`；无工具则输出路径+prompt 供网页  
4. high 段最多自动重试 2 次，失败写 `exports/{clip}_FAILED.md`  
5. 全齐后 ffmpeg concat 或说明剪映顺序  

### 三条执行路径（写进 kit 给用户）

| 路径 | 条件 | 做法 |
|:---|:---|:---|
| A 混合快路径 | 有 ffmpeg | `make_static_clips` 做 low；Grok 只做 high；concat |
| B API | `XAI_API_KEY` | `batch_grok_video_api.py --concat` |
| C Agent | Grok 可读本地目录 | 打开包目录 + START.txt |

可选：从参考案例复制 `make_static_clips.py` / `batch_grok_video_api.py` / `concat_final.py` 骨架，并替换产品锁与 clip 列表（若用户要求落地脚本）。

### 生成顺序
```
high-risk clips first → remaining low/medium → concat
```

---

## Phase 5B：即梦投喂包（`jimeng` 必做）

1. `14_产品视频分镜.md`  
2. `14_video_prompt_package.yaml`  
4. `jimeng_web_clip_prompts.txt`  
5. `jimeng_upload_manifest.yaml`  

格式与 v2 即梦版一致（@图片N 映射、分段中文 prompt）。详见 `references/jimeng-seedance-constraints.md`。

---

## 即梦 / Grok 共用：变形防控

1. 每段最多 **1 primary + 1 anchor**（+1 fallback）  
2. 动作段相机 **几乎静态**  
3. 真值表写进每一段 Product lock  
4. 逐段 QA，不重做全片  
5. 仍变形 → `static_ken_burns` 或 `建议实拍`  

---

## 合规红线

- 禁止：价格、折扣、#1/Best Seller、未授权认证、竞品  
- 禁止：夸大功效、医疗暗示、绝对化用语（对照 ip_compliance）  
- 禁止：清晰真人正脸素材  
- 所有字幕 / slogan → **走透明 PNG 叠加层**（v3.7.0，见上方字幕契约），
  ⛔ 不写进即梦 prompt，也不在剪映里手打  

### SBV 专属禁令（与主图视频不同，容易漏）

投 Sponsored Brands Video 时额外禁止：竞品 logo 或产品、价格类声明
（"比别家便宜 $X"）、限时优惠（"仅限今天"）、**视频形式的买家证言**、
亚马逊自身 logo 与项目标识。

**品牌元素必须出现在前 5 秒** —— 这是硬性内容规则，也是常见拒审原因。

---

## `14_video_prompt_package.yaml` 结构

```yaml
schema_version: "3.0"
video_goal: "转化型"
target_platform: "grok_imagine"   # or jimeng
duration: "42s"
aspect_ratio: "16:9"
audience: ""
core_message: ""
product_truth_lock: ""
style_lock: ""
keyframe_model: "GPT-image-2"

structure_truth_table:
  fixed_parts: []
  movable_parts: []
  part_counts: {}
  forbidden_changes: []

production_pack: "S3_视觉板块/amazon-product-video-director/video_production_v2/"   # grok only
jimeng_manifest: null                     # jimeng only

scenes:
  - scene_id: "S01"
    clip_id: "clip_01"
    duration: "5s"
    risk: "low"
    scene_goal: ""
    visual_description: ""
    start_frame: ""
    action_path: ""
    end_frame: ""
    camera_path: "static micro push-in"
    forbidden_mutations: []
    overlay_text: ""
    reference_pad_primary: ""
    reference_pad_anchor: ""
    keyframe_output: "video_storyboards/01_intro.jpg"
    keyframe_image_prompt_en: ""
    grok_clip_prompt: ""      # platform branch
    jimeng_clip_prompt: ""    # platform branch
    generation_mode: "image_to_video"
    negative_constraints: []
    prompt_iterations:
      - version: "v1"
        failure_symptoms: []
        repair_actions: []
        result_path: ""
        review_status: "pending"

editing_notes:
  stitch_tool: "ffmpeg / CapCut"
  subtitle: "from overlay_text, post"
  generate_order: ["high-risk first"]
```

### 0711 镜头运动与失败修复契约

运行前读取 `../../templates/visual_iteration.schema.yaml`。每个镜头必须把运动写成可验证的五段结构：`start_frame`、`action_path`、`end_frame`、`camera_path`、`forbidden_mutations`。禁止只写“产品旋转/镜头推进”这类无法验收的空泛动作。

每次生成必须保留 Prompt 版本、参考图、失败现象（部件增减、材质漂移、文字乱码、手部穿模、动作跳变、镜头错误）、原因判断、局部 repair、结果路径和审核状态。v2/v3 不覆盖 v1。生产包齐全仅代表 `package_ready`；只有 mp4 回填且逐镜头通过真值、动作、文案、时长和合规验收，才写 `final_accepted=true`。

---

## workflow_state 更新

```yaml
steps:
  product_video_director:
    status: package_ready
    version: "3.5.0"
    target_platform: "grok_imagine"   # or jimeng
    clip_count: 8
    duration: "42s"
    artifact_paths:
      - "S3_视觉板块/amazon-product-video-director/14_产品视频分镜.md"
      - "S3_视觉板块/amazon-product-video-director/14_video_prompt_package.yaml"
      - "S3_视觉板块/amazon-product-video-director/video_production_v2/"          # if grok
      - "jimeng_web_clip_prompts.txt" # if jimeng
video_status:
  package_ready: true
  final_accepted: false
```

---

## 与上下游

### 上游
- `1688-product-detail-scraper` → 结构垫图  
- `amazon-market-research` → profile  
- `amazon-listing-copywriter` → 文案  
- `amazon-listing-image-designer` → 05 风格  
- `amazon-premium-aplus-designer` → 06 A+ 模块与封面句  

### 下游
- **Grok**：Agent / 网页 / API → `exports/*.mp4` → 拼接 → A+ 或主图视频位  
- **即梦**：网页逐段 → 剪映  
- 新品主链：**本 Skill 属 S3 视觉板块**，在主副图与 Premium A+ 之后、广告 S4 之前执行。
  必须复用同一 `S3_视觉板块/amazon-listing-image-designer/产品垫图/asset_manifest.yaml` 与同一 `visual_playbook.yaml`，不重新筛垫图、不另立风格。

---

## 使用示例

### 示例 1 — Grok / Premium A+（备选路径，需显式指定 Grok）

**用户**：「园艺移苗器，按 Grok Imagine 做 42s A+ 视频分镜和生成包」

**Agent**：
1. 读 profile + A+ brief + 1688 + aplus_visuals  
2. 平台锁定 **grok_imagine**，画幅 16:9  
3. 建真值表（如 TWO arms、6cm cylinder…）  
4. 8 段分镜，02/03 标 high + 锚点  
5. 输出 14_* + 组装 `S3_视觉板块/amazon-product-video-director/video_production_v2/`  
6. 用户确认后生 `video_storyboards` 并同步到 `images/clip_XX`  
7. 提示：START.txt / API / 混合快路径  

### 示例 2 — 即梦 / 主图竖版

**用户**：「30s 主图视频，即梦」

**Agent**：平台 **jimeng**，9:16，输出 jimeng 5 件套，不写 Grok 英文包。

---

## 教学演示重点

1. 对照案例 `园艺播种移苗器/.../video_production_v2` 讲「一段一传」  
2. 对比「14 图 one-shot」vs「8 段 + 锚点」变形差异  
3. 演示 high 段必须双图（primary + 1688 结构）  
4. 说明 **Codex 负责包，Grok/即梦负责动，剪映/ffmpeg 负责拼**  
5. **对学员强调：进阶能力、相对费钱；不是第二期主推必会项**

---
## 实战版 · 编排契约（主链最终步骤）

### 定位
- **S3 视觉板块第三步**：在主副图、Premium A+ 完成后进入；广告 S4 在其之后。
  成片就绪后，S4 判定 SB Video 的 `capability` 时素材已在手（16:9 横版），不必再等。  
- 最低闭环是分镜、垫图清单、Prompt package 和执行清单齐全；成片可在站外后续回填。  
- 现场可只演示 Grok/即梦 1 段，但不能删掉完整生产包骨架。

### 一条龙（两出口，同一 Skill）
```text
读上游 Skill 夹（S1/S2 报告）+ S3_视觉板块/amazon-listing-image-designer/{产品垫图,visuals} + amazon-premium-aplus-designer/aplus_visuals
  → 结构真值表 + 素材盘点
  → 用户选平台：Grok Imagine | 即梦
  → 14A 视频垫图资产包 → 14_ 分镜 + 14B 视频执行清单 → 用户确认关键帧
  → video_storyboards/ 或 S3_视觉板块/amazon-product-video-director/video_production_v2/images/
  → 【跳出 Codex】
       · Grok：打开 video_production_v2 + START.txt / 网页逐段
       · 即梦：按 jimeng_upload_manifest 上传 + jimeng_web_clip_prompts.txt
  → 学员用自己账号生成 mp4 → 回填 artifacts.video_output（可选）
```

### 输入优先级
1. `S1_选品板块/amazon-market-research/01_product_profile.yaml`  
2. `S2_文案板块/amazon-listing-copywriter/07_Listing文案.md`  
3. `S3_视觉板块/amazon-listing-image-designer/08_主副图设计方案.md` + `S3_视觉板块/amazon-premium-aplus-designer/09_高级A+方案.md`（A+ 片）  
4. 垫图目录（1688 / 产品垫图 / visuals / aplus_visuals）  
5. IP 黑名单  

### 输出落盘
- 共用：`S3_视觉板块/amazon-product-video-director/14A_视频垫图资产清单.md`、`S3_视觉板块/amazon-product-video-director/视频垫图/asset_manifest.yaml`、`S3_视觉板块/amazon-product-video-director/14_产品视频分镜.md`、`S3_视觉板块/amazon-product-video-director/14_video_prompt_package.yaml`、`S3_视觉板块/amazon-product-video-director/14B_视频执行清单.md`、`S3_视觉板块/amazon-product-video-director/video_generation_manifest.yaml`、`video_storyboards/`  
- Grok：`S3_视觉板块/amazon-product-video-director/video_production_v2/`  
- 即梦：`jimeng_web_clip_prompts.txt` + `jimeng_upload_manifest.yaml`  
- 回填验收：`S3_视觉板块/amazon-product-video-director/video_exports/` + `S3_视觉板块/amazon-product-video-director/14C_视频回填验收.md`

### Handoff（结束后回主链）
```markdown
【本步完成】amazon-product-video-director（主链最终步骤·生产包）
【平台】grok_imagine / jimeng
【产物】S3_视觉板块/amazon-product-video-director/14_产品视频分镜.md + S3_视觉板块/amazon-product-video-director/14_video_prompt_package.yaml（视频须在站外生成）
【说明】生产包齐全写 package_ready；视频片段与最终 mp4 须在 Grok 或即梦生成并回填，未回填不得写 final_accepted
【下一步 Skill】主链已到最后一步；站外成片完成后回本 Skill 验收
【推荐口令】视频已生成并放入 S3_视觉板块/amazon-product-video-director/video_exports/，请生成 14C_视频回填验收.md，并更新 final_accepted。
【MCP】not_required（若实际生成关键帧则写 used: <图像工具>）
```

页脚：amazon-product-video-director｜实战版·主链最终步骤｜坚哥｜跨境者说干货 · 星空的跨境圈子

---

## 版本记录

| 版本 | 变更 |
|:---|:---|
| **3.7.0** | **① 更正主图视频画幅：16:9，旧版写的 9:16 是错的；② 字幕两层分离——透明 PNG 叠加层，与 Listing/A+ 共用同一份 `visual_style_tokens.yaml`；③ 新增投放位规格矩阵 `templates/video_placement_specs.example.yaml` + `scripts/视频规格校验.py`；④ 新增 `scripts/视频出片.py`：拼接/垫乐/派生三画幅/自动校验，剪映仍可用 `--from-master` 接管精修；⑤ 更正竖版派生：pad 为默认，crop 只剩 31.6% 宽度（旧版写的 56.25% 是算错的）；⑥ 补 SBV 专属禁令与前 5 秒品牌规则** |
| 1.0 | Codex 基础分镜 + 即梦倾向 |
| 2.0 | 即梦 Seedance 契约、防变形、jimeng 5 件套 |
| 3.0 | 双平台：Grok Imagine `video_production_v2` + 即梦并行 |
| **3.1.0** | **实战版：唯一视频入口；合并原 premium-aplus-video-generator 职责；讲解不主推** |
| **3.3.0** | **默认平台改为即梦 Seedance**（Grok 保留为显式备选）；补齐即梦工程化契约：生产包目录、生成顺序、QA 清单、批量路径 |
| **3.2.0** | **增加视频垫图资产包、关键帧/片段执行确认、站外 mp4 回填与成片验收；沙龙默认 Grok 15 秒小闭环** |
| **3.5.0** | **阶段归属调整**：由 S5 并入 S3 视觉板块，位于广告 S4 之前；与主副图/A+ 共用垫图与 visual_playbook；成片就绪供 S4 判定 SB Video capability |
| **3.4.0** | **0711 融合：Bulk 后正式最后步骤；新项目统一 14 号段；每镜头 start/action/end/camera/forbidden mutation；package_ready 与 final_accepted 分态；Prompt 失败修复留痕** |


## R03 里程碑 2 当前接口

本版优先执行 `../starsky-fullchain-workflow/references/R03_当前视觉与交接.md` 的里程碑2章节。A+父体一套、桌面先验收再手机及上传清单；品牌仅本人明确请求后经独立可选入口，不重置已交付阶段；视频按真实证据A/B/C分档、1+1上限、可后补。所有结果按当前原生验收记录显示，生成/导出/配置成功不等于通过。
