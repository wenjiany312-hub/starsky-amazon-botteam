---
name: amazon-ops-console
description: >
  S5 本地运营终端。汇总 Amazon Ads API 与 SP-API 的只读快照，生成店铺巡检、广告和文案建议、
  人工审核账本及候选导出；适用于今日巡检、每日运营看板和审核队列，不执行后台修改。
metadata:
  version: "1.0.0"
  edition: starsky-v2
---

# Amazon Ops Console

## 边界

本 Skill 提供本地 S5 链路：`只读采集 → 可复算快照 → 诊断建议 → 人工审核 → 候选导出`。首版没有广告、Listing、库存或订单写接口；批准只表示允许导出候选，不表示已执行。没有后台回执与后续快照时，不评价优化效果。

Amazon Ads API 提供广告事实，SP-API 提供销售、订单、库存和 Listing 事实。两域分别记录权限、来源、站点、币种、时区和采集状态，不能用一域的成功替代另一域。领星或文件可以按域替代，但沿用用户选择，不静默混源。SIF 只提供市场信号，不能替代店铺或广告账户事实。

## 配置与命令

从 [config.template.yaml](config.template.yaml) 建立版本目录外的个人配置。凭据只通过外置配置所指向的环境变量、`.env` 或 token 文件读取，不写入 Skill、命令或聊天。详细运行口径读 [references/operations.md](references/operations.md)；需要核对安全与关联边界时读 [references/security-and-context.md](references/security-and-context.md)。

首次使用先执行 `prepare` 初始化 Data App 项目与依赖路径；空模板本身不能直接启动看板。初始化命令由当前包的 CLI 提供后，以 `python ops_console.py --config PATH prepare` 为准。随后使用：

```text
python ops_console.py --config PATH collect
python ops_console.py --config PATH scheduled-run
python ops_console.py --config PATH serve
python ops_console.py --config PATH status
python ops_console.py --config PATH export-approved
```

`serve` 只监听 `localhost:9766`。`collect` 读取已选站点和来源并更新本地快照；`status` 显示采集新鲜度与缺口；`export-approved` 只导出审核账本中的已批准候选，不调用后台执行。导出前仍要核对当前对象、范围和现值；首版不把导出视为后台执行。

## 桌面启动器（S5运营终端）

用户说「打开S5终端 / S5看板打不开了」时，用安装记录中的 Python 跑 `skills/amazon-ops-console/scripts/make_s5_launcher.py`（默认写到 `~/.starsky-codex/ops_workspace`，只写启动文件不碰配置），再让用户双击账本目录里的 `start-s5-console.bat`（会自动建桌面 `S5运营终端` 快捷方式，图标随包）。启动器先读 `console_storeA/console_config.json`，缺失就直说缺店铺配置，不伪造；已在跑（9766 有监听）就直接开浏览器，不重复起服务。

## 巡检判断

- 采集跟随站点当地午夜：US 用 America/Los_Angeles，UK 用 Europe/London，自动处理夏令时；北京时间 09:00 汇总现有完整日数据。`scheduled-run` 只采集新闭日或未完成来源；“当地今天截至当前”单列，不与闭日混写。定时任务须在用户本机另行登记，安装插件不会自动创建。
- 广告转化按明确的 7 天归因字段解释。昨日闭日、今日实时与 7 天归因成熟度是三种口径，不能用即时结果判定完整转化。
- 每站、每来源、每日期、每归因窗口分开保存。美元与英镑不直接汇总，销售额与广告归因销售额不互换。
- 无类目规则、禁用词库、产品事实或经济执行卡时，输出 `data_gap` 和补充项；不生成具体调价、止损或文案替换值。
- 多 ASIN 广告组、缺少原生 ID 映射或 profile/marketplace 不一致时，阻止产品级建议。建议必须带对象、现值、拟改值、证据、规则版本、观察窗和状态。

看板覆盖九页：店铺总览、出单明细、广告诊断、商品与文案、文案建议、库存补货、成本与收益、待审核、优化复盘。出单明细按 SKU 显示数量与金额，SKU 涉及订单数不能相加冒充全店独立订单数。补货按交期、安全缓冲与完整日销量估算，缺在途不生成最终采购量。

文案保留标题、亮点、五点、ST、长描述的接口返回状态和版本。亮点未取得时保持空，不猜测属性；用户可在页面“商品亮点 · 人工录入”中保存、修改或清空本地值。手录记录按店铺、站点、Seller、ASIN、SKU 隔离，保留历史与版本冲突检查；刷新采集不覆盖，不表示已提交 Amazon。SIF 优先，Sorftime/西柚备用；第三方词量、流量份额和排名采样不能写成本店真实访问量。

成本与收益只使用用户维护的成本文件；读 [references/costs.md](references/costs.md)。成本缺失或覆盖不全时保持空值并展示覆盖率，不把估算称为结算利润。

普通用户只需看到数据日期、来源、缺口、建议、审核状态和候选导出结果。底层请求、凭据路径、重试与安全实现仅在排错或审计时展示。

## 可选 MCP 入口

需要让 Codex 读取同一本地运营终端时，可运行 `python ops_mcp.py --config PATH`，只暴露 `ops_status`、`ops_patrol`、`ops_report` 三个只读工具。它复用同一份外置配置与只读快照，不提供审核、导出或后台执行工具，也不会自动新增、覆盖或修复用户的 Codex MCP 配置；是否登记由用户明确选择。
