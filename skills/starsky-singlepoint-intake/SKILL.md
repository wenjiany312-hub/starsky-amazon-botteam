---
name: starsky-singlepoint-intake
description: >
  单点任务接入与续调（内部 Skill）。不直接响应用户口令：由入口 starsky-amazon-agent 在用户选定
  单点任务并给出一个锚点（ASIN/SKU、链接、现有文案、报表、图片、产品目录）后调度，或在「继续」且
  workflow_state.entry.mode = single_point 时续调。负责：写 00B 单点任务接入单与外接资料清单、
  按锚点与任务选目标专业 Skill、要求目标 Skill 先读 00C Overlay 再补 MCP、单点完成后给出
  「下一步可选 Skill」而不自动扩成全链。选择卡、启动语识别、自然口令直达都已并入入口 Skill。生态：Codex。作者坚哥。
version: "4.6.0"
edition: starsky-v2
edition_label: 实战版
release_tag: "2026-08-29"
author: 坚哥
author_id: jiangge
brand:
  wechat_mp: 跨境者说干货
  knowledge_planet: 星空的跨境圈子
  series: Codex亚马逊实战
  ecosystem: codex
---

# 单点任务接入 · 第二版

> ⚠️ **本 Skill 不是入口。** 星空插件的唯一用户入口是 `starsky-amazon-agent`：选择卡、启动语（含旧口令「启动实战 …」）、全链/单点判别、「全」「只」容错、段名归一、歧义反问、《日常口令直达》全部在那里。本 Skill 只在入口把用户交过来之后干活：写 00B 接入单、选目标 Skill、单点续调。⛔ 不出第二张选择卡、不重复认路、不重新问 A/B/C。

## 出品与归属（实战版）

| 项 | 内容 |
|---|---|
| 发行 | **实战版**（**2026-08-01**） |
| 作者 | **坚哥** |
| 公众号 | **跨境者说干货** |
| 知识星球 | **星空的跨境圈子** |
| 运行环境 | **OpenAI Codex** |

---

## 入口在哪里（本 Skill 不做路由）

选择卡、`启动实战 …` 系列旧口令、「全」「只」判别、段名变体归一（选品≈市场调研≈S1，文案≈词库≈Listing≈S2，视觉≈图≈A+≈视频≈S3，广告≈投放≈Bulk≈S4，运营≈巡检≈上线后≈S5）、含「选品」的歧义反问、《日常口令直达》——全部由 `starsky-amazon-agent` 负责。入口把用户交到这里时，已经带着三样东西：

1. `entry.mode = single_point` 与任务名（只做哪一块 / 哪个细分任务）；
2. 一个启动锚点（ASIN/SKU、链接、现有文案、报表、图片或产品目录），可能附带未整理资料；
3. 用户已选的 A/B/C 轨道（单点任务通常是 B 老运营）。

本 Skill 从这里开始：先接入资料（00C，若有），写 00B 接入单，交给目标专业 Skill。以下情况直接续跑、不再问：

- 已有 `workflow_state.yaml` 且用户说「继续」、`entry.mode = single_point` → 读 state 的 `single_point_progress` 与 `next_step` 沿用。
- 用户在单点收尾后指定「继续当前单点任务，调用 <Skill>」→ 按《单点可以按需继续调用 Skill》。

## 路由规则

### 1. 整条工作流（本 Skill 不经手）

全链由入口 `starsky-amazon-agent` 直接交给 `starsky-fullchain-workflow`（入口写 `entry.mode: workflow`、`entry.task: full_chain`）。用户在单点任务中途说要「转全链 / 从头跑」时，本 Skill 交回入口出全链总览，⛔ 不自己初始化全链 state。

### 2. 单点情景：先启动，再补数

单点不是“资料收齐后才能开始”的表单。设置 entry.mode: single_point 后，按以下顺序执行：

1. 读取用户给出的一个启动锚点：产品目录、ASIN/SKU、Amazon/1688 链接、现有文案、广告报表、本品图片或产品方向均可；可同时接收任何未整理资料。
2. 若有用户资料，先执行统一资料接入：只读扫描、保留 unclassified、生成 00_总控/starsky-fullchain-workflow/00C_资料接入与优化建议报告.md 与 00_总控/starsky-fullchain-workflow/00C_data_intake_overlay.yaml。没有资料则写 data_intake.status=not_requested，直接继续，不阻断。
3. 再扫描 workflow_state.yaml、各板块 Skill 夹里的既有产物和 00_产品资料/，并写入 00_总控/starsky-singlepoint-intake/00B_单点任务接入单.md 与 00_总控/starsky-singlepoint-intake/00B_external_reference_manifest.yaml。
4. 立即交给用户选定的目标 Skill。目标 Skill 必须先读 00C 报告和 Overlay，再读 00B/已有产物，之后才用与本任务相关的已接通 MCP 拉取补充数据。
5. 把真实调用的 MCP、拿到的字段、未能取得的字段和影响写入 mcp_refresh 与数据解释窗口；不能把推断写成已拉取的事实。
6. 任务完成后保持 single_point_progress 为 active。用户可结束，也可指定另一个 Skill 继续当前单点任务；不得自动把单点扩成全链。

单点任务的启动锚点、MCP 补数、连续调用、状态值和交接格式，以 references/single-point-routing.md 及 templates/single_point_intake.schema.yaml 为准。

## 单点任务：启动锚点 + MCP 补数

| 用户选择 | 目标 Skill | 启动锚点（给一个即可） | 优先 MCP 补充与事实边界 |
|---|---|---|---|
| 只选品/市场调研 | amazon-market-research | 站点 + 类目/产品方向，或一个参考 ASIN | 拉市场、竞品、价格带和需求信号；预算/供应商可后补 |
| 只优化 Listing/老品问题 | amazon-listing-copywriter | ASIN、SKU、Listing 链接或现有文案任选一个 | 先补页面、竞品、关键词和可验证卖点；未证实参数不能写进前台 |
| 只优化广告（已有跑数） | amazon-ad-report-analyzer | SP/SB/SD 广告报表；目标 ASIN/筛选条件可后补 | MCP 可补词、竞品、类目和竞价参考；真实跑数诊断不能用 MCP 推测替代广告报表 |
| 只新建广告架构 | **amazon-ad-strategy-architect**（⛔ 广告链 4.0 起的唯一入口） | ASIN/SKU、产品链接、产品目录或产品方向任选一个 | 先补词、竞品和市场信号；预算/利润可用标注默认值，缺真实 SKU 只出不可上传演示版。策略层出 `ad_strategy_brief` + `strategy_review` 后才进 campaign-builder |
| 只生成 Bulk 上传文件 | amazon-bulk-upload | 已批准的 `ad_plan` + `architecture_review` + 当前账户官方模板 | 只支持 `SP_V1`；产出停在 `bulk_release_review`，⛔ 本系统不替你点上传 |
| 只改主副图 | amazon-listing-image-designer | 本品图片、ASIN、Listing 链接或产品目录任选一个 | 先补页面事实、竞品视觉与关键词；没有本品素材只能出方案/提示词，不能称已出成图 |
| 只做产品视频 | amazon-product-video-director | 一张本品图、ASIN、Listing 链接或产品目录任选一个 | 先补产品事实、场景和视觉资料；没有可用本品图时只出分镜/投喂包 |
| 只做词库 | amazon-sif-keyword-research、amazon-sellersprite-keyword-research 或 amazon-xiyou-keyword-research | 产品方向、竞品 ASIN、链接或已导出词表任选一个 | 按文件来源选择对应 Skill；MCP/API 有则补词与指标，缺完整市场报告只限制词库覆盖说明 |
| 只做 IP/合规 | amazon-ip-risk-assessment | 产品名、关键词、拟用文案、图片或链接任选一个 | 先检商标词、品牌、声明与图片风险；生产级产品 IP 决策仍是硬闸门 |
| 只评供应链 | alibaba-supplier-evaluation | 1688 链接、候选供应商或产品方向任选一个 | **必走**本地 `1688_scraper` MCP 抓 offer；Skill 只写报告；报价表可后补 |

“只优化问题”默认先识别为 Listing/老品问题；若用户给的是广告报表，路由到广告诊断；若只给图片，路由到图片优化。无法判断时只追问一个问题：**你想先改变买家看到的文案、广告花费，还是视觉素材？**

## 单点可以按需继续调用 Skill

单点任务不是一次性终点。每个目标 Skill 结束后，都要向用户给出“下一步单点选择”，由用户自己决定是否继续：

- Listing 完成后：可继续调 amazon-listing-image-designer、amazon-ad-report-analyzer 或关键词 Skill。
- 市场调研完成后：可继续调 IP、词库、供应链或 Listing Skill。
- 广告诊断完成后：⛔ **不要直接调 campaign-builder** —— Analyzer 只出动作候选，必须先回
  `amazon-ad-strategy-architect` 形成新版策略与 `strategy_review`，再进 campaign-builder →
  amazon-bulk-upload → amazon-bulk-upload-troubleshooter。也可继续调 amazon-listing-copywriter。
- 图片完成后：可继续调 A+ 或 amazon-product-video-director。

用户口令示例：

    继续当前单点任务，调用 amazon-listing-image-designer。
    先读取 00B 外接资料清单和已有 Listing 文案；使用 MCP 补充可验证的页面与竞品信息，
    只优化主副图，不启动整条工作流。

继续调用时，新的目标 Skill 必须先读 00C 报告与 Overlay，再读 00B 接入单、外接资料 manifest、已有阶段产物和 mcp_refresh 日志；复用已获得事实，只为新任务补拉相关数据。用户没有明确选择下一个 Skill 时，不能擅自串联。

## 统一资料接入层、外接资料与 MCP 补数接口

入口可接入：未整理的产品目录、单文件、附件、截图、表格、视频、链接、ASIN/SKU、当前 Listing、广告报表、产品图片/视频、竞品资料、词表和 VOC。资料源不需要迁移或整理，原件只读；AI 先生成 00C 报告，明确哪些资料可作为当前事实、哪些只能作历史参考、哪些未知、建议先调用什么 Skill、MCP 应补什么数据。

只要 00C Overlay 存在，后续所有业务 Skill 都必须先读它，再执行当前任务。Overlay 的优先级固定为：当前产品事实/本轮 MCP → 合规/IP/用户硬规则 → 用户历史资料 → 标注默认值或 AI 推断。新资料到达时只更新 00C 和受影响产物，不推倒整个 run。

本套流程按 MCP 已接通运行。入口不再把 MCP 检查或安装作为用户的第一道门槛；目标 Skill 在外部数据有助于当前结论时应主动调用。每次必须记录实际结果：

| 已获取事实 | MCP 补数/自动提取 | 模拟参数 | 缺失及影响 | 继续与修订 |
|---|---|---|---|---|
| 已读到的文件、图片、页面或用户输入 | 实际调用的 MCP、返回字段与证据来源 | demo_default 或条件性假设 | 只限制哪个声明或模块 | 当前如何继续、后补后只重算什么 |

这符合 templates/report_depth_and_demo_policy.md 的 explain_and_continue 政策。MCP 用于补数，不替代用户专属后台数据、真实上传字段、IP 决策或不可逆外部操作的事实确认。

## 交接与护栏

- 入口和每个目标 Skill 完成后固定输出：本步完成、下一步 Skill、推荐口令、MCP 四标签，名称不得变体。
- MCP 标签必须写实际 used 或 skipped 原因；不能一概写 not_required。
- 单点不强迫补跑选品、IP、词库或广告等无关上游；但涉及前台声明、商标/合规、真实上传或不可逆外部操作时，目标 Skill 仍执行相应硬闸门。
- 图片和视频缺少本品素材时，可以交付设计方案、分镜、关键帧或站外投喂包，不能称为已生成最终成品。
- 广告、价格、配送、评论、库存和 Amazon 后台设置不会由入口或 Listing Skill 自动修改。
- 全链仍保持既定顺序：IP → S1 综合可行性 → 竞品确认 → 词库 → Listing → 视觉 → 广告 → Bulk → 产品视频。

## 验收检查

- [ ] 未自己出选择卡、未重复认路；进入时已从入口拿到任务名、锚点与 A/B/C 轨道。
- [ ] 有资料先生成 00C 报告和 Overlay，无资料不阻断。
- [ ] 单点表只要求一个启动锚点，不把补充资料写成前置硬门槛。
- [ ] 目标 Skill 先读取已有产物并主动用 MCP 补数；真实调用或跳过原因均有记录。
- [ ] 后续业务 Skill 先读 00C Overlay，再读当前任务产物、运行时叠层和 MCP 结果。
- [ ] 用户可按需续调下一个 Skill，且复用 00B、已有产物与 mcp_refresh，不自动扩成全链。
- [ ] 00B 接入单、外接资料 manifest、entry、single_point_progress 和 mcp_refresh 一致。
- [ ] 不把缺图片、缺词表或非关键缺数冒充为硬阻塞，也不把方案包误称为已上传/已生成成品。

| 版本 | 说明 |
|---|---|
| 4.6.0 | **降级为单点接入 Skill（9·2）**：description 去掉全部启动语；删除统一选择卡、统一启动语表、自然口令直达、容错识别与废弃口令段（已并入 `starsky-amazon-agent` 的《路由》《日常口令直达》）；全链不经手；只保留单点接入、单点任务表、续调、资料接入与交接护栏。 |
| 4.5.0 | **自然口令直达 + 让位规则**：单点意图明确的自然表述不反问直接路由（见《自然口令直达》表）；本机装有 `starsky-amazon-agent` 时所有启动语让位给星空入口，本 Skill 只在无星空插件的环境生效。收尾交接按 `templates/artifact_contract.md` 的「交接三件套」执行。 |
| 4.4.0 | **统一启动语定稿（沙龙第二期）**：`启动实战全链` / `启动实战 只跑选品·词库文案·视觉·广告` / `接入企业资料包` / `继续`，全链与单段靠「全」「只」这对反义词区分。**废弃 `启动实战选品工作流`** —— 它表示全链，但只想做选品的人会说「启动实战选品」，两句差两个字含义相反；现改为听到含「选品」的歧义启动语必须先反问。新增容错识别表与段名变体归一 |
| 4.3.0 | 单点词库入口新增西柚洞察多 ASIN 对比第三 ERP 线；按导出文件来源路由，不静默混表。 |
| 4.2.0 | 统一资料接入层：未整理资料可直接投喂；00C 报告/Overlay 约束全链与单点后续执行 |
| 4.1.0 | 单点升级为启动锚点 + MCP 主动补数 + 用户按需连续调用 Skill |
| 4.0.0 | 统一首入口：先选全链或单点；单点外接资料 manifest 与状态契约 |
| 3.1.0 | 0711 融合：正确主链、视频最终步骤、PRD/Launch/巡检深水分诊和 HTML 路由 |
| 3.0.0 | 实战版默认全链分诊 |
