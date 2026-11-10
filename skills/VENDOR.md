# 星空技能同步记录

| 项 | 内容 |
|---|---|
| 来源 | 星空亚马逊 Agent 源码 `_Codex公开发布候选/source/starsky-amazon-agent`（skills、templates、scripts、mcp） |
| 同步日期 | 2026-11-04 |
| 来源 CHANGELOG 顶部 | 3.0.18（2026-10-17） |
| 说明 | ⚠️ OpenBot 里装的版本可能更新（OpenBot 目录还读不到）。拿到 OpenBot 目录后，以 OpenBot 为准重新同步，并更新这张表 |
| 共 | 47 个技能（不含本团队自己的 3 个） |

这些技能原来是给 Codex / Claude 插件写的，里面有「交回 starsky-amazon-agent」「换板块新开对话」「Codex 插件路径」这类宿主说明。**在 Bot Team 里以各平台运行环境说明为准**（见 `adapters/<平台>/runtime.md` 的「星空技能的读法」）。技能里的相对路径（`../../templates`、`../_visual_suite_contract`）要求整个 `skills/`、`templates/`、`scripts/` 保持原来的目录结构。

| 技能 | 版本 | 发布标签 |
|---|---|---|
| `1688-product-detail-scraper` | 3.9.0 | 2026-08-01 |
| `_ad_launch_contract` | — | — |
| `_brand_visual_contract` | — | — |
| `_keyword_system_contract` | 1.0.0 | — |
| `_s5_ops_contract` | — | — |
| `_stage_ux_contract` | — | — |
| `_visual_suite_contract` | — | — |
| `alibaba-supplier-evaluation` | 3.4.1 | 2026-08-01 |
| `amazon-ad-campaign-builder` | 4.0.0 | — |
| `amazon-ad-report-analyzer` | 4.0.1 | — |
| `amazon-ad-strategy-architect` | 1.1.0 | — |
| `amazon-ads-api-connector` | 1.0.0 | — |
| `amazon-brand-store-designer` | 1.1.0 | — |
| `amazon-brand-story-designer` | 1.0.0 | — |
| `amazon-bulk-upload` | 4.1.0 | — |
| `amazon-bulk-upload-troubleshooter` | 4.1.0 | — |
| `amazon-competition-difficulty-assessor` | 1.1.0 | — |
| `amazon-gray-tactic-detector` | 1.1.0 | — |
| `amazon-ip-risk-assessment` | 3.4.0 | 2026-08-01 |
| `amazon-launch-plan` | 1.0.0 | — |
| `amazon-listing-copywriter` | 4.0.0 | — |
| `amazon-listing-image-designer` | 4.4.0 | 2026-08-01 |
| `amazon-market-research` | 3.8.0 | 2026-08-01 |
| `amazon-ops-console` | 1.0.0 | — |
| `amazon-ops-workflow` | 4.1.0 | — |
| `amazon-premium-aplus-designer` | 3.7.0 | — |
| `amazon-product-development-prd` | 1.0.0 | — |
| `amazon-product-video-director` | 3.7.0 | 2026-08-01 |
| `amazon-seasonal-selection` | 1.1.0 | — |
| `amazon-sellersprite-keyword-research` | 4.1.0 | — |
| `amazon-sif-keyword-research` | 4.2.0 | — |
| `amazon-sp-api-connector` | 1.2.0 | — |
| `amazon-standard-aplus-designer` | 3.3.0 | 2026-08-01 |
| `amazon-store-patrol-orchestrator` | 1.1.0 | — |
| `amazon-trend-signal-scanner` | 1.1.0 | — |
| `amazon-variant-packaging-planner` | 1.1.0 | — |
| `amazon-visual-benchmark-learner` | 1.2.0 | 2026-08-01 |
| `amazon-xiyou-keyword-research` | 4.1.0 | — |
| `category-adapter` | 2.3.0 | 2026-08-01 |
| `editorial-html-report` | 3.4.0 | 2026-08-01 |
| `github-tabler-html-report` | 3.8.0 | 2026-08-01 |
| `jingpu-product-selection` | 1.0.0 | — |
| `lingxing-erp-connector` | 1.0.0 | — |
| `starsky-amazon-agent` | 2.3.0 | — |
| `starsky-fullchain-workflow` | 2.19.0 | 2026-08-29 |
| `starsky-navigator` | 1.0.0 | — |
| `starsky-singlepoint-intake` | 4.6.0 | 2026-08-29 |

## 发行叠加层（2026-11-09 补，rc10 起）

星空亚马逊 Agent 正式发行时，在 `source/` 之上还叠加 `release-overrides/`（`*.py` 一律落到 `scripts/`）和 `installer/host_support.py`（落到 `scripts/`）。11-04 同步只拿了 `source/`，rc9 里有 7 个模块缺失：报告生成、证据校验、S1 验收这些脚本一跑就是 ImportError（`s1_cost_model.py`、`s5_optimize.py` 不报错，但会静默跳过知识库依据）。

| 项 | 内容 |
|---|---|
| 来源 | `_Codex公开发布候选`，git `49e91ed`（2026-11-05） |
| 对应正式发行 | 星空 Codex 3.0.44（2026-11-06）；落点以该发行包为准 |
| 原则 | 只带被 import 到的模块（含间接依赖），不整目录搬；版本取 `release-overrides/`，和本包其余脚本同出 `source/` 那一棵树 |

| 包内位置 | 来源 | sha256 前 12 位 |
|---|---|---|
| `scripts/kb_ui.py` | release-overrides/kb_ui.py | ca83788fe800 |
| `scripts/kb_plan.py` | release-overrides/kb_plan.py | 57d67d2ab899 |
| `scripts/kb_amend.py` | release-overrides/kb_amend.py（kb_ui 间接依赖） | 013d150b2397 |
| `scripts/kb_observations.py` | release-overrides/kb_observations.py（kb_ui / verify_kb_evidence 间接依赖） | bdeef9bcbabf |
| `scripts/verify_kb_evidence.py` | release-overrides/verify_kb_evidence.py | 570bbbcb1306 |
| `scripts/workflow_checks.py` | release-overrides/workflow_checks.py | c3646e43b876 |
| `scripts/s1_flow.py` | release-overrides/s1_flow.py | dfc3e423be96 |
| `scripts/host_support.py` | installer/host_support.py | 4bf68a13647e |
| `skills/github-tabler-html-report/assets/s1_product_front.py` | release-overrides/s1_product_front.py（3.0.44 发行包放在这里，`verify_s1_acceptance.py` 按这个路径找） | f3f8ab498b75 |
| `scripts/s1_report_v3.py` | source/starsky-amazon-agent/scripts/s1_report_v3.py（11-04 同步漏掉；s1_product_front 依赖） | bdc53d813345 |

第三方依赖清单：`scripts/requirements.txt`（与 Agent `installer/requirements.txt` 同一组库），Grok 上由 `mcp/local/install_grokbot.sh` 装进 `{WORK}/py`。
以后再同步星空技能：先同步，再跑 `python build/import_check.py .`，有缺失就按上面的落点补。构建时同一检查对打包副本再跑一次，解析不到直接失败。
