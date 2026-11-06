---
name: amazon-brand-store-designer
version: "1.1.0"
edition: starsky-v2
description: >
  亚马逊品牌旗舰店（Brand Store / Storefront）设计与优化，适用所有站点。支持从 0 建店、已有店改版、局部优化：
  先做商品矩阵与现店盘点，规划首页分流和品类页选购，每一块映射到后台真实模块（带文本的图片 → 叠加文本、商品、商品网格…），
  先验证一个样块再扩展；产出上传包：从这里开始.html（店铺效果预览 + 按块照搭 + 核对 + 状态链）、按后台尺寸导出的图片、
  搭建步骤、页面结构、商品矩阵、现店盘点、修改记录。触发：旗舰店、品牌旗舰店、Brand Store、店铺首页、Storefront、店铺改版。
  装有 starsky-amazon-agent 时是 S3 第 11 步（可选）。
metadata:
  author: 坚哥
  created: 2026-09-28
---

# 品牌旗舰店设计与优化（S3 第 11 步 · 可选 · 所有站点通用）

先读 `../_brand_visual_contract/references/brand_store_playbook.md`（方法与反例）和 `brand_specs.yaml`（模块与尺寸）。站点只决定语言和用词，模块逻辑、版式、验收一样。

## 先问两句

1. 三选一：「1 = 现在做　2 = 以后再做（记为待办，随时说「做旗舰店装修」补）　3 = 不做」。回 2 记 `deferred`（写用户原话），回 3 记 `skipped`（写原因），都进第 12 步；以后再做的项一直显示在悬浮窗「✨ 可选待办」和交接里。S3 走完后用户说「做旗舰店装修」时直接从这里开始，不重走 S3 流程。
2. 「从 0 建店、已有店改版，还是只改某一页/某一块？」→ 写进 `mode`：new_store / redesign / partial。

## 资料（已有的先读，不反复索要）

| 要什么 | 用途 |
|---|---|
| 站点、品牌、店铺链接（改版时） | 语言与用词；改版先逐页盘点 |
| 全部在售产品线与子 ASIN、系列、款式 | 商品矩阵；长期主推与当前在售分开写 `role` / `status`（在售 / 缺货 / 停售 / 待核） |
| Logo 原文件、已认可的主副图 / A+ / 场景图 | 复用，不重画 Logo；本项目 S3 已出的图脚本会列进参考图 |
| 用户想保留的风格、要解决的问题 | style 与各页 task |
| 当前后台编辑器截图（选中「带文本的图片」后的设置界面） | 模块实测；没有就标概念预览 |

只有 1–2 个产品时可以用同系列模拟产品看效果（`simulated: true`，网页和清单标「模拟」），不得写成已在售。

## 固定执行

```text
1. <python> <home>/skills/_brand_visual_contract/scripts/brand_preview.py --run "<产品根>" --kind store --init
   → S3_视觉板块/amazon-brand-store-designer/11_brand_store.yaml（首页骨架是两块已被认可的「叠加文本」入口）
2. 助手改 YAML：
   - mode；brand（name、tagline、meta_description、logo 400×400、header 3000×600 全店共用一张）；style
   - products 商品矩阵；redesign 时 audit 现店盘点（page / observed / action / note）
   - pages：每页 name、task（买家进来要解决什么）、sections → tiles：
     · 首页产品线入口用 image_text_overlay：title（后台标题）、button（Shop now）、link（asin 或 page + target）、
       baked_text（图内辅助文案，不写标题）、image（prompt / ref / alt / master 母版号）、fallback（做不到时的替代）
     · 品类页：场景入口（复用首页同一 master）→ 选购说明（image_text_beside 或 text）→ product 商品模块 / product_grid（≥4 件）
     · verified：module / desktop / mobile / link，实测后逐项改 true
   - sample_block：先在后台验证的那一块；accepted：用户认可设计后改 true；revision_log：每轮反馈只改了什么、保留什么
3. <python> …/brand_preview.py --run "<产品根>" --kind store
   → 旗舰店上传包/从这里开始.html（效果预览可切页、后台照填逐块、核对、状态链）+ 01_上传图片/ + 02_文案与链接/
   → GPT网页端投喂_旗舰店/（按母版出图，标题按钮不画进图）+ GPT网页端回填_旗舰店/
4. 打开从这里开始.html 给用户，发可点链接。【等你】认可或修改 → 改 YAML（version +1、revision_note）→ 重跑。
5. 先让用户在后台做样块并截图反馈 → 更新 verified → 认可后出其余图：整夹去 ChatGPT 网页端 / Codex App 出图改图，
   按母版号命名下载，原样放回填夹，说「旗舰店回填好了」→ 重跑导出。
```

- 退出码 2 = 有「必须改」：停售款还在、按钮写系列却链单品、图中文字重复标题、一页两个商品网格、未知模块、缺页头等。
- 回填图按母版号（S01…）或图位号（P1_T01_…、S0_Header、S0_Logo）命名即可自动对号；一个母版会导出到所有用到它的图位。

## 完成标准（按状态链，不说笼统的「完成」）

- 本步交付至少到「设计已认可」+ 上传包就绪；样块的模块、双端、链接实测结果写进 verified。
- 后台预览、提交审核、上线分别由用户实际操作后记录（`submitted` / `published`），助手不代替、不推断。

## 硬边界

- 不用竞品品牌、竞品图，不写功效宣称或未核实的尺寸数字；不编造销量排名（没有报表不写 Best Sellers 当标题）。
- 不替用户在后台搭建、提交或发布；未上传不说已上线。


## R03 里程碑 2 当前接口

本版优先执行 `../starsky-fullchain-workflow/references/R03_当前视觉与交接.md` 的里程碑2章节。A+父体一套、桌面先验收再手机及上传清单；品牌仅本人明确请求后经独立可选入口，不重置已交付阶段；视频按真实证据A/B/C分档、1+1上限、可后补。所有结果按当前原生验收记录显示，生成/导出/配置成功不等于通过。
