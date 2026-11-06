---
name: amazon-kb-query
description: 查询本地知识库（选品知识库、广告知识库、促销知识库）。在没有知识库 MCP 的平台（比如 Grok Bot）上，用命令行查询代替 MCP。选品侦察员、风控官、财务管家、广告优化师、复盘分析师需要查打法、阈值、诊断规则、整品节奏时使用。
---

# 本地知识库查询

知识库在团队包的 `mcp/local/` 下，每个库有一个命令行工具 `kb.py`：

| 知识库 | 用途 | 查询命令（`<KIT>` = 团队包根目录） |
|---|---|---|
| 选品知识库 | 选品打法、难度判定、风险品类 | `python3 <KIT>/mcp/local/selection-kb/kb.py query --kb <KIT>/mcp/local/selection-kb/kb --text "关键词" --format md --limit 10` |
| 广告知识库 | 新品期打法、诊断规则、阈值 | `python3 <KIT>/mcp/local/ads-kb/kb.py query --kb <KIT>/mcp/local/ads-kb/kb --text "高点击低转化" --format md --limit 10` |
| 促销知识库 | 断货重推、排名恢复、整品节奏 | `python3 <KIT>/mcp/local/promotion-kb/kb.py query --kb <KIT>/mcp/local/promotion-kb/kb --text "断货重推" --format md --limit 10` |

## 规则
1. **先抽短症状再查**：长段落不要直接当检索词，先抽成「高点击低转化」「新品期出价」这种短词
2. 可以加标签过滤：`--tag product_type=标品 --tag lifecycle_stage=新品期`（广告库）；`--tag selection_mode=精铺`（选品库）
3. 引用时写出卡片编号（比如 `KC-0166`），知识库只给建议，不能当成本账户的真实数据
4. 只用 `query`，**不要**运行 `init`、`ingest`、`fetch`、`close` 等写入命令，除非老板明确要求更新知识库
5. 已经注册了知识库 MCP 的（OpenBot、StarSky，以及按 install_grokbot.sh 注册过的 Grok Bot），优先用 MCP 工具 `kb_scenario_rules`、`kb_diagnose`；本技能是 MCP 不通时的备用办法
