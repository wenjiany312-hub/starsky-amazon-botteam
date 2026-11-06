# 本地 MCP（自己封装，随包分发）

| MCP | 谁用 | 状态 |
|---|---|---|
| 1688_scraper | Warehouse | ⏳ 待从 OpenBot / 星空亚马逊 Agent 同步 |
| selection_kb（选品知识库） | Scout、Guard、Finance、Reviewer | ⏳ 待同步 |
| ads_kb（广告知识库） | Ads、Reviewer | ⏳ 待同步 |
| promotion_kb（促销知识库） | Ads、Reviewer | ⏳ 待同步 |

每个 MCP 放一个子文件夹，里面附 `install.md`，写明本机怎么安装、各平台怎么注册。
Grok Bot 的云电脑上跑不了本机 MCP：知识库在 Grok 版里改成直接读 `knowledge/` 下的数据文件，1688 改用云电脑浏览器访问。
