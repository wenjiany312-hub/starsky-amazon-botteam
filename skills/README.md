# 技能

每个技能放一个文件夹，里面有 `SKILL.md`（头部写 name、description，正文是使用说明）和参考文件。
技能**只维护这一份**，从 OpenBot / 星空亚马逊 Agent 同步过来，同步时保留原来的版本号。

| 技能 | 用的角色 | 状态 |
|---|---|---|
| `1688-product-detail-scraper` | Warehouse | ⏳ 待同步 |
| `alibaba-supplier-evaluation` | Warehouse | ⏳ 待同步 |
| `amazon-ad-campaign-builder` | Ads | ⏳ 待同步 |
| `amazon-ad-report-analyzer` | Ads、Reviewer | ⏳ 待同步 |
| `amazon-ad-strategy-architect` | Ads | ⏳ 待同步 |
| `amazon-ads-api-connector` | Ads | ⏳ 待同步 |
| `amazon-ai-person-disclosure` | Guard、Imageworker、Editor | ✅ 包内 |
| `amazon-brand-store-designer` | Designer | ⏳ 待同步 |
| `amazon-brand-story-designer` | Designer | ⏳ 待同步 |
| `amazon-bulk-upload` | Ads | ⏳ 待同步 |
| `amazon-bulk-upload-troubleshooter` | Ads | ⏳ 待同步 |
| `amazon-competition-difficulty-assessor` | Scout、Reviewer | ⏳ 待同步 |
| `amazon-fba-freight-li` | Warehouse、Finance | ⏳ 待同步 |
| `amazon-gray-tactic-detector` | Guard | ⏳ 待同步 |
| `amazon-image-compliance-check` | Imageworker、Editor | ✅ 包内 |
| `amazon-ip-risk-assessment` | Guard | ⏳ 待同步 |
| `amazon-launch-plan` | Finance | ⏳ 待同步 |
| `amazon-listing-copywriter` | Listing | ⏳ 待同步 |
| `amazon-listing-image-designer` | Designer、Imageworker | ⏳ 待同步 |
| `amazon-market-research` | Scout | ⏳ 待同步 |
| `amazon-premium-aplus-designer` | Designer | ⏳ 待同步 |
| `amazon-product-video-director` | Designer、Imageworker | ⏳ 待同步 |
| `amazon-seasonal-selection` | Scout | ⏳ 待同步 |
| `amazon-sellersprite-keyword-research` | Scout、Listing | ⏳ 待同步 |
| `amazon-sif-keyword-research` | Scout、Listing | ⏳ 待同步 |
| `amazon-sp-api-connector` | Listing | ⏳ 待同步 |
| `amazon-standard-aplus-designer` | Designer | ⏳ 待同步 |
| `amazon-team-operating-model` | 运营总监、Scout、Guard、Warehouse、Finance、Listing、Designer、Imageworker、Editor、Ads、Reviewer | ✅ 包内 |
| `amazon-trend-signal-scanner` | Scout | ⏳ 待同步 |
| `amazon-visual-benchmark-learner` | Designer | ⏳ 待同步 |

同一个技能可能挂给多个角色，但各自用的部分不同：比如 `amazon-listing-image-designer`，Designer 用方案和 Prompt 部分，Imageworker 用出图和回填部分。
