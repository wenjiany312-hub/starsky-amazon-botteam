---
name: amazon-ads-api-connector
description: >
  Amazon Ads API 只读接入与 Sponsored Products 报表采集。用于 S5 广告复盘的授权、profile 发现、
  异步报表续拉和本地建议，也用于 S4 新品期的账户事实输入（profile/站点/币种/时区/SB-SD 资格证据 +
  账户级基线参照）；不用于 SP-API 订单/库存/Listing，也不修改广告后台。
metadata:
  version: "1.0.0"
  edition: starsky-v2
---

# Amazon Ads API 只读连接器

## 边界

本 Skill 的链路是 `授权 → profiles → SP 报表 → 本地分析 → 待审核建议`。首版只支持 Sponsored Products；SB/SD 必须标 `unsupported`，不能用 SP 数据代表全账户。接口没有创建、改价、改预算、暂停或归档能力，所有建议保持 `pending approval`，由用户人工执行或批准后另行生成 Bulk 候选。

Amazon Ads API 是广告账户事实源。Selling Partner API（SP-API）仍是订单、库存、Listing 等独立来源；其可用性不能证明广告数据可用。S5 广告数据可选 `amazon_ads_api`、`lingxing` 或 `feed_folder`，沿用用户已选来源，不静默替换、混加或自动合并。

## S4 新品期输入（strategy-architect 可选调用，不硬拦）

新品尚无 Campaign 时，本连接器不拉报表，只给两样账户事实：

1. `profiles` 就绪检查：站点/币种/时区是否与本品一致、SB/SD 资格证据，S4 的 capability/readiness 直接引用，不再只靠申报。
2. 账户级基线参照：账户有历史投放才拉（无历史就写 `unknown`，不编造），只作新品出价 sanity 参考，必须标注“账户历史参照≠本品预测”，不得进入允许 CPC/CPA 护栏计算。

未配置连接器时 S4 照常走 SIF/导出继续，本节整段跳过。

## 模型中立

本 Skill 是 CLI 脚本（`scripts/ads_api.py`），宿主只要能跑 shell + Python 就能调：Codex 原生、Pi、opencode 第三方模型一视同仁。S4/S5 的 Skill 不得以“模型不是官方”跳过本连接器；能不能调只看账户配没配，不看模型是谁。

## 使用前

需要配置或授权时先读 [references/setup.md](references/setup.md)，从 [references/config.template.yaml](references/config.template.yaml) 复制个人配置。默认路径是 `~/.starsky-codex/config/amazon_ads_api.yaml`；也接受 `STARSKY_ADS_CONFIG`，用户根目录随 `STARSKY_HOME` 变化。多站点使用独立配置，例如美国 `amazon_ads_api.yaml`、英国 `amazon_ads_api_uk.yaml`，CLI 每次显式传 `--config`。S5 可发现 `amazon_ads_api*.yaml`，但必须让用户选定站点/配置，不能自动合并。

区域只能是 `NA`、`EU`、`FE`。profile、国家、币种、时区均来自真实 profile 响应，不猜测、不默认选择第一个账号。用 `expected_country` 校验配置与 profile：美国为 `US`，英国为 `UK`，同时兼容接口返回 `GB`。账号尚未配置时写 `not_configured`；配置文件存在只写 `needs_verification`，不能宣称可用。

每份配置可以用 `credential_file` 指向站点自己的本地 `.env` 文件，并通过 `profile_id_env`、`client_id_env`、`client_secret_env`、`refresh_token_env` 指定变量名。支持 `AMAZON_ADS_*` 与同名短前缀 `ADS_*`。连接器逐客户端读取 `.env`；不得 shell 执行该文件，也不得把值写入全局 `os.environ`，避免美英凭据互相污染。配置、`.env` 与每站独立 `token_file` 都位于发布包之外。

## CLI

使用安装器配备的 Python，在本 Skill 的 `scripts/` 下执行：

```text
python ads_api.py --config CONFIG authorize
python ads_api.py --config CONFIG profiles
python ads_api.py --config CONFIG collect --profile-id ID --start YYYY-MM-DD --end YYYY-MM-DD --attribution-days 7 --all-campaigns --out RUN
python ads_api.py --config CONFIG analyze --input RUN/review_input.json --card CARD.yaml --out RUN
```

归因窗口只能显式选择 `7` 或 `14`。采集范围必须二选一：`--all-campaigns`，或重复传入 `--campaign-id ID`。报告提供方可能仍按整个 profile 生成原始下载，归一化产物必须按已选 campaign scope 过滤并记录该事实。不得省略 profile、区域或日期范围。

报表是异步任务；同一命令、同一 `RUN` 再执行会读取已保存的 report ID 并续拉，不重复创建。`--wait-seconds 0..60` 控制本次最多等待时间，默认 `0`。`pending` 是正常中间态；只有完整下载并通过元数据校验的报表才能进入完整建议。

## 采集与建议纪律

- 原始报表、归一化五层与 source metadata 分开保存；保留 profile、region、country、currency、timezone、日期闭区间、归因天数、campaign scope、report ID、采集时间和 SHA256。
- 拒绝混 profile、混区域、混币种、混日期、混归因窗口的合并。不同层级不能相加冒充账户总计。
- 缺失值保持 `null`；零销售 ACOS 为 `null`。Amazon 接受并返回明确销售字段前，不以 purchases 代替销售额计算 ACOS。
- 执行卡中的 CVR 与止损阈值必须由用户填写；无值保持 `null`，不采用隐藏默认值。
- 动作至少保留 `native_id`、`layer`、`source_context`、`status`。搜索词没有可直接修改的 bid；建议必须指向真实可执行对象。
- 不在聊天、命令行参数或日志中出现 client secret、refresh token、access token、授权码、认证响应正文或预签名下载 URL。

真实账号验收要分层记录：Ads API 访问申请、LWA 授权、profile 发现、报表真实数据分别验证。某次本地美英联调已经验证两个站点 profiles 成功，并有四类 SP 请求被接口接受、进入异步等待；这只证明该次指定账号与请求状态，不代表报告已经下载完成，也不代表其他客户已验证。个人案例中补充 advertiser profile 曾帮助申请获批，只能作为经验，不能承诺审核结果。
