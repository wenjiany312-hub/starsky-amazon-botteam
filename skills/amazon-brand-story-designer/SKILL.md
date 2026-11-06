---
name: amazon-brand-story-designer
version: "1.0.0"
edition: starsky-v2
description: >
  亚马逊品牌故事（Brand Story，A+ 内容管理器里的品牌轮播 "From the brand"）设计师。用户给产品链接/图片和品牌意图，
  按后台模块规格设计背景（电脑+手机）、Logo 与品牌介绍、焦点图、商品与店铺四宫格、品牌问答，产出一个上传包：
  从这里开始.html（效果预览 + 后台照填一键复制 + 核对）、按后台尺寸导出的图片、可复制文案、应用 ASIN 清单、替代文本与校验清单。
  适用所有站点。替代文本埋 S2 核心词。触发：品牌故事、Brand Story、From the brand、品牌轮播。装有 starsky-amazon-agent 时是 S3 第 10 步（可选）。
metadata:
  author: 坚哥
  created: 2026-09-28
---

# 品牌故事设计（S3 第 10 步 · 可选 · 所有站点通用）

站点只决定语言和用词（美国 English (United States)、英国 English (United Kingdom)…），模块、尺寸逻辑与验收一样。
先读 `../_brand_visual_contract/references/brand_story_playbook.md`（已验证的模块分工、文案与图片规则、常见返工）。一个品牌故事讲一个产品系列，系列差异大就分开做。文字尽量用后台原生字段；图中文字不再重复后台标题。

目标：一次给出**九成可用**的品牌故事。用户打开一个网页就能看效果、提修改；定稿出图后，照网页按后台顺序上传、一键复制文字。

## 先问一句

进入本步先问：三选一：「1 = 现在做　2 = 以后再做（记为待办，随时说「做品牌故事」补）　3 = 不做」。回 2 记 `deferred`（写用户原话），回 3 记 `skipped`（写原因），都进第 11 步；以后再做的项一直显示在悬浮窗「✨ 可选待办」和交接里。S3 走完后用户说「做品牌故事」时直接从这里开始，不重走 S3 流程。

## 输入（按顺序找，缺什么只问一句）

| 要什么 | 从哪来 |
|---|---|
| 品牌名、Logo、想表达的意图、站点 | 用户一句话；Logo 用用户的真实注册 Logo；站点决定语言（UK → English (United Kingdom)） |
| 产品链接 / ASIN、图片 | 用户给；本项目的主副图、A+ 已被脚本列进 `reference_images`，其次产品垫图 |
| 同品牌其他在售 ASIN | 用户给；用于四宫格和「应用 ASIN 候选」。按产品系列分组，不同系列不混在一份品牌故事里 |
| 人群、场景、痛点 | S1 `01_product_profile.yaml`、S2 场景库 `scenes.yaml` |
| 核心词 | 脚本取 S2 词包可用词前 12；**没有 S2 词包**时调 SIF `market_get_asin_keyword_signals` 或西柚 `get_asin_keywords` 查本品核心词，写进 `keywords` |
| 品牌问答 | 优先复用 S2 Listing 的 Q&A（选 2–4 条品牌向的） |
| 禁用词 | S1 `04_compliance_blacklist.yaml`：竞品品牌、功效宣称一律不进图不进字 |

## 固定执行

```text
1. <python> <home>/skills/_brand_visual_contract/scripts/brand_preview.py --run "<产品根>" --kind story --init
   → S3_视觉板块/amazon-brand-story-designer/10_brand_story.yaml（已预填核心词、参考图、模块骨架）
2. 助手只改这份 YAML：
   - marketplace / theme / version / revision_note；brand（name 按品牌备案原样大小写）；style（推荐 1 个风格 + 3 个主色 + why）
   - background：headline / body + desktop（1464×625）、mobile（463×625）两张图的 prompt / ref / alt
   - modules：H1 logo（品牌介绍，首句可做开场）→ H2… focus_image（一张讲一个理由；文字想做进图里就写 embedded_text，
     后台标题正文留空）→ asin_showcase 四宫格（每格 position / asin / label + 166×182 图）→ 需要时 qa
   - application_asins：这份品牌故事挂到哪些 ASIN；store_link：旗舰店参考链接（只作参考，后台原生选取）
   - 文案用站点语言；alt 自然带 1–2 个核心词；中文意思写在 YAML 注释里给用户看
3. <python> …/brand_preview.py --run "<产品根>" --kind story
   → 品牌故事上传包/：从这里开始.html、01_上传图片/、02_文案与ASIN/（后台可复制文案.txt、后台填写.json、应用ASIN清单.csv、
     四格商品绑定.csv、图片替代文本.csv）、图片清单与校验.json、使用说明.txt、修改说明.txt
   → GPT网页端投喂_品牌故事/（参考图 + 出图说明）+ GPT网页端回填_品牌故事/
4. 打开「从这里开始.html」给用户看（Mac：open "<html>"；也可用内置浏览器），把路径作为可点链接发给用户。
   【等你】回「可以」或说要改哪里 → 改 YAML → 重跑第 3 步。改一版就把 version +1、写 revision_note，旧包自动进 _历史版本/。
5. 定稿后用户整夹上传到 ChatGPT 网页端 / Codex App 出图，改图也在那边完成；zip 原样放进回填夹，说「品牌故事回填好了」。
6. 重跑第 3 步：回填图按后台尺寸导出进 01_上传图片/，网页里换成真图，校验清单写入尺寸与 SHA256。
```

- 尺寸与字数读 `../_brand_visual_contract/references/brand_specs.yaml`；公开资料有分歧的项已注明，**上传前以后台弹窗为准**，不一致就改规格表。
- 脚本退出码 2 = 有「必须改」（缺品牌名、缺背景或 Logo 模块、四宫格超 4 格等），修完再给用户。
- 回填图文件名以图位编号开头（B0_Desktop、H1_Logo、H3_1_<ASIN>…）就能自动对号；对不上的列在「核对」里，助手改名后重跑。比例差太多会提示按比例重出。

## 完成标准

- 「必须改」为空；用户回了「可以」或修改已落进 YAML；上传包能打开。
- 回填后 01_上传图片/ 里每个图位都有按规格导出的图，校验清单有尺寸和 SHA256。
- 未回填也可结束本步（`deferred`）：方案、出图包、上传包已就绪，网页里标「待出图」。

## 硬边界

- 不编造品牌历史、认证、销量；写不出来就问用户一句。
- 不用竞品品牌、竞品图，不写功效宣称；生产模式 Logo 必须是真实注册 Logo，演示可占位并标注。
- 不替用户在后台上传、提交或发布；网页和说明里写明「未上传、未提交审核、未发布」。


## R03 里程碑 2 当前接口

本版优先执行 `../starsky-fullchain-workflow/references/R03_当前视觉与交接.md` 的里程碑2章节。A+父体一套、桌面先验收再手机及上传清单；品牌仅本人明确请求后经独立可选入口，不重置已交付阶段；视频按真实证据A/B/C分档、1+1上限、可后补。所有结果按当前原生验收记录显示，生成/导出/配置成功不等于通过。
