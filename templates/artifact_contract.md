# 星空 V2.1 · 产物与交接权威契约

目录权威：`templates/skill_folder_map.yaml`。本文只定义**每个产物叫什么、落在哪个夹**。

## 目录（V2.1 落盘规则）

- Codex 打开**产品根**。用户选完板块后，用 `scripts/init_stage_folders.py` 静默建夹。
- 板块夹：`S1_选品板块` … `S5_运营板块`（官方名里的冒号不能当文件夹名）。
- Skill 子夹：**必须与插件 `skills/<name>/` 目录名完全一致**。该 Skill 的产物只写进自己的夹。
- 完整路径固定为：

```text
{产品根}/{板块夹}/{skill-dir}/{canonical 文件名}
```

例：`amazon-market-research` 的市场报告写到 `S1_选品板块/amazon-market-research/01_市场穿透报告.md`。

- 企业投喂原件：`00_产品资料/`（不是 Skill）。供应商回填、用户补充的原件也放这里（如 `00_产品资料/供应商回填/`）。
- 00A / 00C / Overlay / 选品推荐：`00_总控/starsky-fullchain-workflow/`；单点任务接入单（00B）：`00_总控/starsky-singlepoint-intake/`。
- 板块综合报告、阶段验收、跨 Skill 的综合件：`{板块夹}/_阶段总览/`。
- HTML：**一律** `{板块夹}/github-tabler-html-report/HTML/`，全包只有这一个 HTML 家。
- 关键词导出投喂：`S2_文案板块/_投喂_关键词导出/{SIF|卖家精灵|西柚找词}/`（用户半辅助导出，不是 Skill）。
- 过程性临时文件（脚本、临时 JSON、渲染器）：`{板块夹}/{skill-dir}/运行数据/`。
- `workflow_state.yaml` 与 `_NEXT_S*到S*.md` 留在产品根。
- Skill 不得在产品根、不得在别人的 Skill 夹新增 canonical 产物。
- ⛔ 产品根不再有 `生成报告/`、`产品资料/`、`产品垫图/`、`关键词导出/`、`visuals/`、`aplus_visuals/`、`video_exports/`、`视频垫图/`、`bulk/`、`运行数据/`、`运行输入/` 这些旧顶层目录。

## 目录（历史口径，仅读旧包）

旧沙龙把报告写在 `{产品目录}/生成报告/`，媒体写在 `产品资料/`，Bulk 写在根上 `bulk/`。读取旧项目时把旧路径记录到 `legacy_artifacts`，**不得一边写旧名一边声称符合新契约**；新 run 一律按下表落盘。

2.4.0 起两个编排 Skill 改名：`amazon-salon-workflow` → `starsky-fullchain-workflow`，`codex-amazon-salon-quickstart` → `starsky-singlepoint-intake`。2.4.0 之前建的产品夹里 `00_总控/` 下是旧名子夹：读上游时新旧两个夹都找，新产物只写新名夹，不搬旧文件。

## Canonical 产物

「归属夹」= `{板块夹}/{skill-dir}`，文件写进该夹内；表中带子目录的（如 `HTML/`、`巡检/`）为夹内子目录。

### 00 · 总控

| step_id | 归属夹 | 文件 |
|---|---|---|
| intake_interview | `00_总控/starsky-fullchain-workflow/` | `00A_选品需求确认单.md` |
| single_point_intake | `00_总控/starsky-singlepoint-intake/` | `00B_单点任务接入单.md`、`00B_external_reference_manifest.yaml` |
| data_intake | `00_总控/starsky-fullchain-workflow/` | `00C_资料接入与优化建议报告.md`、`00C_data_intake_overlay.yaml` |
| enterprise_data_ingest | `00_总控/starsky-fullchain-workflow/` | 用户资料包（`00_产品资料/`，只读）→ `enterprise_data_manifest.yaml`、`runtime_data_overlay.yaml`、`企业资料包注入说明.md` |
| condition_select | `00_总控/starsky-fullchain-workflow/` | `00_选品推荐报告.md`、`00_product_selection.yaml`、`00_L1市场筛选_data_source_log.yaml` |

### S1 · 选品板块（`S1_选品板块/`）

| step_id | 归属夹 | 文件 |
|---|---|---|
| jingpu_selection（可选） | `jingpu-product-selection/` | `00_精铺灵感池.md`、`00_精铺方向初筛.yaml` |
| signal_scan（可选，快速选品卡 F） | `amazon-trend-signal-scanner/` | `SCAN_失衡快扫候选.md`、`signal_scan.yaml` |
| seasonal_selection（可选） | `amazon-seasonal-selection/` | `SEASON_季节档期候选.md`、`seasonal_candidates.yaml` |
| market_research | `amazon-market-research/` | `01_市场穿透报告.md`、`01_product_profile.yaml` |
| difficulty_assessment（market_research 步内） | `amazon-competition-difficulty-assessor/` | `01A_运营难度双法评估.md`、`difficulty_assessment.yaml`、`01A_data_source_log.yaml` |
| competitor_signal_check（market_research 步内） | `amazon-gray-tactic-detector/` | `01B_竞品信号判读.md`、`competitor_signal_flags.yaml` |
| competitor_asin_research | `amazon-market-research/` | `05_竞品ASIN研究报告.md`、`05A_竞品候选确认表.xlsx`、`competitor_asin_pool.yaml`；HTML → `github-tabler-html-report/HTML/05A_竞品候选确认报告.html` |
| supplier | `alibaba-supplier-evaluation/` | `02_1688供应商短名单.md` |
| product_scrape | `1688-product-detail-scraper/` | `03_1688产品资料_{商品ID}.md`、`03_extracted_listing_facts.yaml`、`03C_上架数据解释与续跑说明.md`、媒体子目录（主图/详情图/视频封面） |
| supplier_data_request | `1688-product-detail-scraper/` | `03A_供应商数据补充清单.md` |
| supplier_data_merge | `1688-product-detail-scraper/` | `03B_供应商数据合并与复算.md`、`03_product_facts.yaml` |
| ip_compliance | `amazon-ip-risk-assessment/` | `04_IP风险排查与专利分析.md`、`04_compliance_blacklist.yaml` |
| variant_plan（可选，IP 后、PRD 前） | `amazon-variant-packaging-planner/` | `VARIANT_变体与定位方案.md`、`variant_matrix.yaml` |
| product_prd（可选） | `amazon-product-development-prd/` | `PRD_产品开发需求书.md`、`product_prd.yaml`、`PRD_供应商询价与打样清单.md`、`PRD_样品回填验收.md` |
| phase_s1 | `_阶段总览/` | `阶段_S1_选品可行性研究报告.md`、`decision_record_S1.yaml`、`stage_acceptance.yaml`；HTML → `github-tabler-html-report/HTML/阶段_S1_选品可行性研究报告.html` |

### S2 · 文案板块（`S2_文案板块/`）

| step_id | 归属夹 | 文件 |
|---|---|---|
| keyword_export_prepare | `_投喂_关键词导出/` | `SIF/`、`卖家精灵/`、`西柚找词/` 三个投喂子夹 + `keyword_export_manifest.yaml` |
| keyword_research | `{关键词 Skill}/`（`amazon-sif-keyword-research` / `amazon-sellersprite-keyword-research` / `amazon-xiyou-keyword-research`，**每源一夹**） | `06_关键词库报告.md`、`keyword_package.yaml`、`keyword_package.validation.json`、`06_keyword_data_source_log.yaml`、`06A_合规并集.yaml`、`06B_场景库.yaml`（主线源产出） |
| keyword_html_report | `github-tabler-html-report/HTML/` | `06_关键词库报告.html`（每源一份可用 `06_{SIF|卖家精灵|西柚}_产品词库.html`） |
| keyword_multi_source（多源时必产） | `_阶段总览/` | `06C_三源关键词差异对照.md`；HTML → `github-tabler-html-report/HTML/06C_三源差异对照.html` |
| listing_copy | `amazon-listing-copywriter/` | `07_Listing文案.md`、`07_listing_bullet_ledger.yaml`、`07A_Listing转化蓝图.yaml`、`07B_Listing校验输入.json`、`07C_Listing校验结果.json`；HTML → `github-tabler-html-report/HTML/07_Listing文案.html` |
| phase_s2 | `_阶段总览/` | `阶段_S2_词库与文案总览.md`、`stage_acceptance.yaml` |

### S3 · 视觉板块（`S3_视觉板块/`）

R03 新案的文件表以 `artifact_contract_s3_r03.yaml` 为准：产品说明书在 `00_产品资料/`，规划仍在原主副图 Skill 夹，投喂/回填/最终版在已声明的共用出图目录，阶段报告保持原正式入口。以下表保留给旧案，不要求新案重复生成旧版报告和旧扁平目录。逐图证据、来源指纹与合规仍须通过，不以文件存在代替验收。

| step_id | 归属夹 | 文件 |
|---|---|---|
| visual_learn | `amazon-visual-benchmark-learner/` | 主链默认不跑。仅当用户投喂 `S3_视觉板块/_投喂_风格参考/` 时才写 playbook |
| visual_asset_prepare | `amazon-listing-image-designer/` | `产品垫图/asset_manifest.yaml`、`产品垫图/主副图/IMG01–IMG07`、`产品垫图/高级A+/A01–A05`。不要再给人 `08A_视觉垫图资产清单.md` |
| image_design | `amazon-listing-image-designer/` | `visual_suite.yaml`、`08_主副图出图文档.md`、`S3_视觉板块/GPT网页端投喂_主副图/` |
| visual_generation_ready | `amazon-listing-image-designer/` | `08B_视觉出图执行清单.md`、`visual_generation_manifest.yaml` |
| visual_generation | `amazon-listing-image-designer/` | `visuals/`、`visual_generation_run_log.md`、`叠字渲染日志.yaml` |
| premium_aplus | `amazon-premium-aplus-designer/` | `09_高级A+出图文档.md`、`S3_视觉板块/GPT网页端投喂_A+/`。演示/新品主链默认走这里，不要同时丢 09S |
| standard_aplus（可选） | `amazon-standard-aplus-designer/` | `09S_标准A+方案.md`、`09S_标准A+出图文档.md` |
| product_video | `amazon-product-video-director/` | `14_产品视频分镜.md`、`14_video_prompt_package.yaml` |
| video_asset_prepare | `amazon-product-video-director/` | `14A_视频垫图资产清单.md`、`视频垫图/asset_manifest.yaml`、`视频垫图/关键帧/` |
| video_generation_ready | `amazon-product-video-director/` | `14B_视频执行清单.md`、`video_generation_manifest.yaml` |
| video_production | `amazon-product-video-director/` | `video_production_v2/` 或即梦投喂包、`video_exports/`、`14C_视频回填验收.md` |
| phase_s3 | `_阶段总览/` | `阶段_S3_视觉资产总览.md`、`stage_acceptance.yaml`；HTML → `github-tabler-html-report/HTML/阶段_S3_视觉方案.html` |

### S4 · 广告板块（`S4_广告板块/`）

| step_id | 归属夹 | 文件 |
|---|---|---|
| ad_intake | `amazon-ad-strategy-architect/` | `11A_广告参数确认单.md`、`ad_strategy_input.yaml` |
| ad_strategy | `amazon-ad-strategy-architect/` | `ad_strategy_brief.yaml`、`10_广告策略决策.md`、`economics_scenarios.yaml`、`strategy_review.yaml`、`diagnostic_evidence_card.yaml`、`feedback_record.yaml`、`change_context_log.yaml`、`handoff.yaml` |
| ad_architecture | `amazon-ad-campaign-builder/` | `11_新品广告架构.md`、`11B_流量放大对照板.md`、`ad_plan.yaml`、`architecture_review.yaml`、`handoff.yaml` |
| ad_html | `github-tabler-html-report/` | **Bulk 前强制** `HTML/11_新品广告报告.html` |
| bulk | `amazon-bulk-upload/bulk/` | 先确认 Skill 根目录 `AmazonAdvertisingBulksheetSellerTemplate.xlsx`；再 `SP_bulk_upload_{date}_v1.xlsx`、`bulk_manifest.yaml` |
| ad_report_intake | `_投喂_广告报表/` | 上线 3–5 天后用户放入的 Amazon 导出 |
| ad_optimize_1 | `amazon-ad-report-analyzer/` | `阶段1优化.md` + HTML；审过才出下一版 Bulk |
| ad_upload_support | `amazon-bulk-upload-troubleshooter/bulk/` | `Bulk上传报错分析_{date}.md`、`SP_bulk_upload_{date}_v{n}.xlsx`、`bulk_validation.yaml`、`bulk_retry_actions.yaml` |
| ad_diagnosis | `amazon-ad-report-analyzer/` | `13_广告报表诊断_{date}.md`、`13A_广告优化变更建议.yaml`、`ad_analysis.yaml`、`optimization_actions.yaml`、`monitoring_plan.yaml`、`diagnostic_evidence_card.yaml`；HTML → `github-tabler-html-report/HTML/13_广告分析_{date}.html` |
| launch_plan（可选） | `amazon-launch-plan/` | `Launch_Plan.md`、`launch_plan.yaml`、`Launch_计划实际偏差表.md` |
| phase_s4 | `_阶段总览/` | `阶段_S4_广告与上传总览.md`、`stage_acceptance.yaml`；HTML → `github-tabler-html-report/HTML/阶段_S4_广告架构.html` |

### S5 · 运营板块（`S5_运营板块/`）

| step_id | 归属夹 | 文件 |
|---|---|---|
| ops_state | `amazon-ops-workflow/` | `ops_state.yaml` |
| store_patrol | `amazon-store-patrol-orchestrator/巡检/` | `{date}_店铺巡检报告.md`、`{date}_patrol_snapshot.yaml`、`{date}_待审核动作清单.yaml`；HTML → `github-tabler-html-report/HTML/{date}_店铺巡检报告.html` |
| erp_extract（可选） | `lingxing-erp-connector/erp_extract/` | 领星拉取的原始/清洗数据 |
| phase_s5 | `_阶段总览/` | `Ops_首次店铺快照.md`、`stage_acceptance.yaml` |

`01A` / `01B` 是 market_research 步内的子产物（同 `03A` / `03B` 先例）；`SCAN_` / `SEASON_` / `VARIANT_` 大写前缀给 S1 可选方法层（同 `PRD_` 先例）。`12` 号段固定给 Bulk 文件与上传支持，`13` 号段固定给广告跑数诊断，`14` 号段固定给产品视频（视频属 **S3: 视觉板块**）。兼容读取旧项目的 `10/10A/10B/10C` 视频产物与 `06V_video_prompt_package.yaml`、`阶段_S5_产品视频总览.md`，但新项目只写 `14` 号段、总览归 S3。

兼容旧名只允许用于读取历史项目；新产物必须使用 canonical 名。

## 多来源关键词（V2 口径：三线并行，不合并）

- 每个词源各走各的 Skill、各写各的夹：`S2_文案板块/{关键词 Skill}/keyword_package.yaml` + `06_关键词库报告.md`，每源一份。
- ⛔ 不合并成一张表、不取平均、不选「更准的数」；多源时必须额外产出 `S2_文案板块/_阶段总览/06C_三源关键词差异对照.md`（见 `html_report_contract.yaml`）。
- 下游（场景库 / 文案 / S3 / S4）**只消费** `keyword_export_manifest.yaml` 里 `downstream_line` 指定的那一条线；未指定时问用户选主线，⛔ 不静默混表。
- 源标识统一小写英文 key：`sif` / `sellersprite` / `xiyou`；报告标题与 HTML 文件名可用中文短名（SIF / 卖家精灵 / 西柚）。

## Demo 产物命名（合法后缀）

- 没有真实 SKU/ASIN 时的演示产物，文件名在扩展名前插入 `_DEMO_ONLY_NOT_UPLOADABLE`：
  `S4_广告板块/amazon-bulk-upload/bulk/SP_bulk_upload_{date}_v1_DEMO_ONLY_NOT_UPLOADABLE.xlsx`
- 工作表首页与说明文件须同样显著标注。
- ⛔ 同一个 run 里 demo 版与真实版不并存：拿到真实 SKU 后重出新版本号，demo 版移入同夹 `bulk/_archive/`。

## step 三档（校验器口径）

| 档 | 含义 | 校验行为 |
|---|---|---|
| required | 两条 S1 路径都必须有 | 缺 = 不合格 |
| path:condition-first / path:supply-first | 仅该路径必需 | 走另一条路径时不报缺失 |
| optional | 按需触发（标准A+、广告诊断、上传报错、视频成片、S5） | 缺 = 静默 |

`phase_s3` / `phase_s4` 的归档规则：跑了该阶段就必须有 `_阶段总览`，由 `workflow_state.yaml` 已完成 step 反推，不无条件要求。校验器：`scripts/校验产物契约.py <产品根>`，按 `skill_folder_map.yaml` 检查顶层目录、Skill 夹归属与 canonical 文件位置。

## Handoff 四标签

每个业务 Skill 完成时必须输出，名称不得变体：

```markdown
【本步完成】<当前 Skill>
【下一步 Skill】<完整 kebab-case Skill 名或 END>
【推荐口令】<可直接复制的完整中文指令>
【MCP】used: <实际调用> / skipped: <未调用及原因> / not_required
```

`workflow_state.next_step.skill` 必须写完整 Skill 名，不使用 `listing_copywriter` 等下划线简称。

⛔ 只有这 4 个标签，禁止另加 `【产物】` 等自造字段罗列文件；本步产出的文件已经在最近一张步骤卡的 📦产物 区块给过，这里不重复贴。

### 聊天里出现文件链接的通用规则

不管是步骤卡的 📦产物、Handoff 卡还是任何自由回复，只要在聊天里给用户一个可点开的文件，**链接文字只能是文件名（或简短角色名），绝不能把完整路径 / `file://…` 原样裸露成可见文字**（不管是当链接文字还是不加 `[]()` 直接贴一整行路径）：

```markdown
✅ [03A_供应商数据补充清单.md](<file:///Volumes/Tool%20Disk/.../03A_供应商数据补充清单.md>)
❌ file:///Volumes/Tool%20Disk/Starsky-Claude%20/S1_选品板块/.../03A_供应商数据补充清单.md
```

路径含空格或中文时用 `<...>` 包住 URL 部分（Markdown 语法），文字部分永远是短文件名。

## 阶段收尾交接三件套（换窗口时的用户体验契约）

任一板块收尾（或需要换窗口升档/出 HTML）时，回复的**最后一块**必须是交接卡，顺序固定：

1. 「你现在只需要做三步」操作卡：① 新开对话（仍打开本产品根）② 左下角切到下一板块的 `window_default`（读 `templates/stage_intake.yaml` / `templates/model_routing.yaml`）③ 粘贴下面口令。附一句下一板块解决什么。
2. `_NEXT_S*到S*.md` 的口令整段放进代码块，可直接复制。⛔ 只说「文件已生成」不打印，判交接不合格。

文件命名：`_NEXT_S1到S2.md` / `_NEXT_S2到S3.md` / `_NEXT_S3到S4.md` / `_NEXT_S4到S5.md`，S5 收尾写 `_全链闭环自检.md`；全部落产品根，⛔ 不覆盖上一段的口令文件。
