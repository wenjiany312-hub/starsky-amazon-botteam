# Amazon Ads API 本机配置

## 先区分四层状态

1. **访问申请**：开发者应用已获 Amazon Ads API 权限。
2. **LWA 授权**：目标广告主完成 OAuth，refresh token 已安全保存。
3. **profile 发现**：`profiles` 返回目标账号的国家、币种、时区与区域主机，用户明确选定 profile。
4. **报表可用**：针对该 profile 的最小 SP 报表真实完成并下载。前三层任何一层成功，都不能替代第四层。

补全 advertiser profile 曾在个别申请中帮助获批，这是个人经验，不是保证通过的流程或审核规则。

## 保存配置

从 `config.template.yaml` 复制到本机配置目录。单站默认使用 `amazon_ads_api.yaml`；多站点一站一份，例如 `amazon_ads_api.yaml` 与 `amazon_ads_api_uk.yaml`。S5 会发现同目录下 `amazon_ads_api*.yaml`，但不会替你选择或合并；CLI 始终显式传 `--config`。可用 `STARSKY_ADS_CONFIG` 指向单个默认文件；设置 `STARSKY_HOME` 时，默认配置根随之改变。不要把个人配置、`.env` 或 token 文件放进插件目录。

只填写 `NA`、`EU` 或 `FE`；先留空 `profile_id`。美国配置使用 `region: NA`、`expected_country: US`；英国使用 `region: EU`、`expected_country: UK`，校验时兼容 profile 返回 `GB`。client ID、client secret、profile ID 和 refresh token 从各配置指定的变量读取，变量名可用 `AMAZON_ADS_*` 或对应的 `ADS_*`，不把值写进聊天或 CLI。

## 每站独立 `.env` 指针

`credential_file` 可指向该站点自己的本地 `.env`。连接器为每个客户端单独解析 `KEY=VALUE`；不会通过 shell 执行，不修改全局 `os.environ`。因此两个站点可以使用相同变量名而互不覆盖。以下路径与值均为虚构示例：

```yaml
# C:/example/starsky/config/amazon_ads_api.yaml
region: "NA"
expected_country: "US"
credential_file: "C:/example/private/ads-us.env"
profile_id: null
profile_id_env: "ADS_PROFILE_ID"
client_id_env: "ADS_CLIENT_ID"
client_secret_env: "ADS_CLIENT_SECRET"
refresh_token_env: "ADS_REFRESH_TOKEN"
token_file: "C:/example/private/ads-us.tokens.json"
redirect_uri: "http://127.0.0.1:8765/callback"
```

```yaml
# C:/example/starsky/config/amazon_ads_api_uk.yaml
region: "EU"
expected_country: "UK"
credential_file: "C:/example/private/ads-uk.env"
profile_id: null
profile_id_env: "ADS_PROFILE_ID"
client_id_env: "ADS_CLIENT_ID"
client_secret_env: "ADS_CLIENT_SECRET"
refresh_token_env: "ADS_REFRESH_TOKEN"
token_file: "C:/example/private/ads-uk.tokens.json"
redirect_uri: "http://127.0.0.1:8765/callback"
```

每站必须有不同的 `token_file`。不要在发布源中填写真实用户目录、profile ID 或任何凭据。

Windows 将 token 存在插件外并用当前用户 DPAPI 保护。macOS 使用仅当前用户可读的私有文件（`chmod 600`）；这不应被描述为加密存储。授权源在 `token_file` 与 `refresh_token_env` 之间二选一：已有 refresh token 时可读取环境变量；需要运行 `authorize` 获取新授权时，先取消或改名该配置的 `refresh_token_env` 来源，再生成该站点的 `token_file`。否则旧环境变量 token 仍可能优先被读取，使新授权看似没有生效。切换后只保留本轮选择的授权源。

## 首次只读验证

```text
python ads_api.py --config CONFIG_US authorize
python ads_api.py --config CONFIG_US profiles
python ads_api.py --config CONFIG_UK profiles
```

核对返回的 `profileId`、国家、币种、时区和区域主机后，由用户选定并写入配置。不要自动选择第一项。

随后以一个 profile、一天、明确归因窗口做最小 SP 采集。`collect` 默认不等待；返回 pending 时，原样重复同一条命令和同一输出目录继续轮询。需要在一次调用内短暂等待时显式加 `--wait-seconds 1..60`。

```text
python ads_api.py --config CONFIG collect --profile-id ID --start YYYY-MM-DD --end YYYY-MM-DD --attribution-days 7 --all-campaigns --out RUN
python ads_api.py --config CONFIG analyze --input RUN/review_input.json --card CARD.yaml --out RUN
```

若只分析指定活动，用一个或多个 `--campaign-id ID` 替换 `--all-campaigns`。Amazon 原始报告可能覆盖整个 profile；本地归一化范围和元数据必须保留用户选择的活动集合。

401/403、报表 failure、unsupported column、跨账号错配或缺销售字段都应如实记录为认证失败、contract mismatch 或 `data_gap`。本包没有写广告后台的 API 路由。

每个账号都须分别验证访问权限、LWA 授权、profile 和最终报表文件。某个账号请求已被接受或报表已完成，只能作为该账号、本次范围和日期的证据。
