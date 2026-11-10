# 星空亚马逊 Bot Team（starsky-botteam）

亚马逊美国站 11 角色 AI 运营团队：选品 → 风控 → 货源 → 利润 → 文案 / 视觉 → 质检 → 上架 → 广告 → 复盘。
一套源码，按平台构建：Grok Bot、OpenBot、StarSky。

- 角色：`roles/`（运营总监、选品侦察员、风控官、仓管物流、财务管家、文案算法师、创意美工、出图工、质检官、广告优化师、复盘分析师）
- 流程：`workflow/`（SKU 状态机、7 道闸门、交接协议、输出排版规范）
- 技能：`skills/`（团队自有 + 星空技能，来源见 `skills/VENDOR.md`）
- MCP：`mcp/`（第三方目录、1688 本地 MCP、3 个知识库 MCP）
- 店铺数据：`docs/数据端接入方案.md`、`config/data_sources.yaml`
- 平台适配：`adapters/<平台>/`

构建：`python build/build.py --platform grokbot --version 1.0.0-rc7 --edition internal`
产物在 `dist/`（不进仓库）。

> **这是明文源码，不推任何公开远端。** 对外发行走公开仓 `wenjiany312-hub/starsky-amazon-botteam` 的加密发行包（payload.enc）+ 设备码授权，和星空亚马逊 Agent 同一套；见 `license/授权接入规格.md`。


授权与使用条款：`license/使用条款.md`（一张授权 = 一台电脑 + 一个 Grok 账号；靠水印追查，更新只给登记过的授权）。
