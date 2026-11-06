---
name: lingxing-erp-connector
version: "1.0.0"
edition: starsky-v2
description: >
  领星 ERP 数据连接器。通过领星开放平台 OpenAPI 拉取亚马逊店铺后台数据,包括店铺列表、Listing、FBA 库存、订单/销量、广告活动、广告关键词、广告投放和搜索词数据。
  适合用于店铺巡检、断货预警、广告健康周检、利润核算和多 Agent 数据后端。
  属 S5: 运营板块的数据源 Skill：用户说「店铺巡检 / 今日巡检 / FBA 预警 / 断货预警」时先由入口 starsky-amazon-agent
  进 S5，再由巡检编排或入口调用本 Skill 取数；用户明确说“从领星拉数据”“领星库存/销量/广告数据”时可直接使用。
  凭据未配置时,引导用户按 references/领星接入清单.md 接入;不得虚构数据。

data_source:
  modes:
    - mode: api
      priority: 1
      fallback: true
      description: "领星 ERP 官方 OpenAPI,需要用户本地配置 AppID/AppSecret 和 IP 白名单"
    - mode: file
      priority: 2
      fallback: false
      description: "用户已导出的领星报表或脚本落盘 JSON"
  note: "凭据和原始接口数据不得进入公开分享包、报告或聊天消息。接口不可用时必须如实降级。"

input:
  required:
    - name: lingxing_config
      type: yaml
      description: "~/.starsky-codex/config/lingxing_config.yaml（用户目录，插件升级不丢；旧版技能根目录的仍兼容）,包含 AppID/AppSecret、店铺 sid 映射和可选 report_root"
  optional:
    - name: date_range
      type: user_text
      description: "销量、订单、广告等接口的日期范围"
    - name: sid
      type: number
      description: "领星店铺 ID,可由 --smoke 店铺列表获取"

output:
  format: files
  schema_version: "1.0"
  structured:
    - name: raw_json
      type: json
      description: "领星接口原始或归一化 JSON,默认落到 report_root/lingxing/{日期}"
    - name: report
      type: markdown_html
      description: "断货预警、广告健康周检、每日巡检等报告"
    - name: findings
      type: json
      description: "统一异常 finding 输出,供日报卡片或下游巡检流程使用"
    - name: feishu_card
      type: message
      description: "可选。把当天 findings 推成飞书群卡片(scripts/feishu_push.py);未配置 webhook 时不推送,不影响巡检本身"
---

# 领星 ERP 数据连接器

## 我的身份

我是亚马逊店铺巡检的数据后端连接器,负责把领星 ERP 的官方后台数据稳定拉下来,交给断货预警、广告诊断、销量巡检和利润分析使用。我的价值不是“生成结论”,而是保证数据来源清楚、凭据安全、接口失败时不编造。

## 什么时候该找我

- 用户说“从领星拉数据”“用 ERP 数据巡检”“查领星库存/销量/广告”
- 店铺巡检需要后台数据,但不能登录亚马逊卖家后台或浏览器环境
- 需要 FBA 库存、Listing 状态、ASIN 日销量、订单明细、广告活动、广告关键词、广告投放、搜索词等数据
- 自动化日报/周报需要一个可重复运行的数据后端

## 安全边界

- 只调用领星官方 OpenAPI,不登录亚马逊卖家后台,不操作浏览器指纹环境。
- AppID/AppSecret 只能保存在本地 `~/.starsky-codex/config/lingxing_config.yaml`（不要放插件目录：升级会换目录）。
- 分享包、课程包、公开仓库不得包含真实 `lingxing_config.yaml`、真实店铺 sid、Seller ID、原始接口返回 JSON 或报告缓存。
- 日志和报告不得打印 AppSecret 或完整 token。
- 凭据缺失、权限不足、IP 白名单未通过、接口报错时,必须标注“领星数据不可用”,并停止对应后台数据结论;不得用模拟数据补空。

## 首次接入

1. 安装依赖:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

2. 登录领星 ERP,用超级管理员账号进入:设置 -> 业务配置 -> 开放接口。
3. 在开放后台获取 AppID/AppSecret,并配置当前机器的直连出口 IP 白名单。
4. 复制配置模板:

```powershell
Copy-Item .\references\lingxing_config模板.yaml .\lingxing_config.yaml
```

5. 填写 `lingxing_config.yaml` 后做冒烟测试:

```powershell
python .\scripts\lingxing_client.py --smoke
```

成功标志:获取 access token 成功,并打印店铺列表。随后把需要巡检的店铺 sid 填入 `stores`。

更详细步骤见 `references/领星接入清单.md`。

## 家族巡检配置（跌破红线 / 价格异动 / 星级下跌 / 疑集中差评）

`python scripts/make_patrol_config.py` 自动生成 `~/.starsky-codex/config/巡检配置.yaml`：领星在售 Listing 按父体分组，floor_price 默认取成本表（`_s5_ops_contract/scripts/s5_cost_table.py --set <站点> <表>`）的保本售价（毛利=0）。用户手改的最低价和昵称重新生成时保留；新上架子体自动补上。

## 能力清单

| 数据 | 领星接口域 | 用途 | 状态 |
|:--|:--|:--|:--|
| 店铺列表/授权状态 | `/erp/sc/data/seller/lists` (GET) | 连接健康检查、获取 sid | 已封装 |
| FBA 库存/可售天数 | `/erp/sc/routing/fba/fbaStock/fbaList` (POST) | 断货预警、库存健康判断 | 已封装 |
| Listing 状态/价格 | `/erp/sc/data/mws/listing` (POST) | Listing 价格、星级、评论、销量交叉验证 | 已封装 |
| ASIN 日销量 | `/erp/sc/data/sales_report/asinDailyLists` (POST) | 销量脉搏、销售异常诊断 | 已封装 |
| 订单明细 | `/erp/sc/data/mws/orders` (POST) | 订单追踪、退货/异常排查 | 已封装 |
| 广告活动 | `/erp/sc/data/ads/campaigns` (POST) | 广告花费、销售额、ACOS、投放状态 | 已封装 |
| 广告组/关键词/投放/搜索词 | `/erp/sc/data/ads/{adGroups,keywords,targets,queryWords}` (POST) | 否词、拓词、投放结构诊断 | 已封装 |
| 利润/费用 | 财务接口 | 月度利润核算 | 使用前核对官方文档 |

具体字段和必填参数以领星开放平台官方文档为准。接口规则可能变化,遇到参数错误时先核对官方文档。

## 现成脚本

- `scripts/lingxing_client.py`:底层客户端。支持 `--smoke` 拉店铺列表,也支持 `--get <路径> --method POST --params '{...}'` 调试单接口。
- `scripts/fba_stock_alert.py`:FBA 断货预警报告。读取 `stores` 中 `patrol=true` 的店铺,输出 Markdown + HTML,并生成库存域 findings。
- `scripts/ads_health_check.py`:广告健康周检报告。对比本周与上周广告活动,识别烧钱无单、ACOS 超线、预算/投放异常和花费异动。
- `scripts/daily_patrol_data.py`:每日巡检数据后端。拉销售、广告、Listing/家族脉搏,输出 `daily_patrol_data.json` 和 findings。
- `scripts/feishu_push.py`:把当天 findings 推到飞书群。**可选**,不配 webhook 就不推,巡检本身照常跑。

## 推送到飞书(可选)

巡检的最后一公里:异常自己来找人,不用人去翻报告。

1. 飞书群 → 右上角设置 → 群机器人 → 添加机器人 → **自定义机器人** → 复制 webhook 地址
2. 填进 `lingxing_config.yaml` 的 `feishu.webhook`(开了「签名校验」的把密钥填 `feishu.secret`)
3. 验证连通:

```powershell
python .\scripts\feishu_push.py --test
```

4. 日常用法:

```powershell
python .\scripts\feishu_push.py              # 推今天(全店)
python .\scripts\feishu_push.py --marketplace UK
python .\scripts\feishu_push.py --dry-run    # 只打印卡片,先看长什么样(不需要 webhook)
python .\scripts\feishu_push.py --date 2026-08-01
```

分站点定时(北京时间,电脑需开机且已登录):

- 英国站 `07:05` → `scheduled_patrol.ps1 -Marketplace UK`（`Europe/London` 当地昨天）
- 美国站 `15:05` → `scheduled_patrol.ps1 -Marketplace US`（`America/Los_Angeles` 当地昨天）

推送顺序是 **先取数、后推送** —— `daily_patrol_data.py` / `fba_stock_alert.py` 跑完落了 findings,再跑 `feishu_push.py`。

卡片内容:P1/P2/P3 计数 + 结论三选一(今天没事 / 观察 / 需要深挖) + 按店铺分组的 P1/P2 明细(对象、事实、建议动作、影响金额)。

`push_priority` 默认只推 P1+P2,P3 留档不打扰;`push_when_clear: false` 可以让"今天没事"的日子不发消息。

**纪律照旧**:当天没有 findings 文件时,推的是"今日无巡检数据",不编造;webhook 在任何日志里都脱敏打印;推送只是通知,不碰后台。
- `scripts/findings.py` + `rules.yaml`:统一异常 finding 协议和阈值配置。改阈值优先改 `rules.yaml`,不要改脚本。

## 调用规范

- 所有接口调用优先走 `scripts/lingxing_client.py`,避免重复实现 token、签名、代理绕过和限流处理。
- 数据默认落盘到 `report_root/lingxing/{日期}`。`report_root` 可在 `lingxing_config.yaml` 顶层配置。
- 输出报告只能引用已拉到的数据;缺失数据要写明缺失原因。
- 如果下游任务是广告诊断、库存预警、销售巡检或利润核算,先确认对应接口返回成功,再做结论。

## 高级判断和常见坑

- IP 白名单失败通常不是代码问题,先查当前直连出口 IP。
- 电脑开代理时,国外 IP 查询站点可能显示代理出口,不能直接拿来填领星白名单。
- 订单、广告和销量接口通常需要日期范围或 `event_date`;缺参时领星可能返回笼统错误。
- Git Bash 可能把 `/erp/...` 参数当成本机路径转换,调试接口建议使用 PowerShell 或 cmd。
- ACOS 建议用 cost/sales_amount 自算,不要只信接口返回的格式化字符串。
- 自动化巡检要做降噪:低金额、低花费、无影响的异常不要全部推成 P1。

## 分享前检查

- `lingxing_config.yaml` 为空模板或不随包分发。
- `.gitignore` 已排除凭据、缓存、原始 JSON 和报告输出。
- `scripts/__pycache__/` 不打包。
- 文档里没有真实 AppID、AppSecret、Seller ID、店铺 sid、个人电脑路径或固定出口 IP。
- README 中包含安装、配置、冒烟测试和常用脚本。
