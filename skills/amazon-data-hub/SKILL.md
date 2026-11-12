---
name: amazon-data-hub
description: >
  团队取店铺数据、交店铺改动方案的唯一入口。店铺数据（订单、库存、费用、Listing、广告、账户健康）统一从数据底座
  （星空旗舰 AI 智能广告终端，跑在老板自己的电脑上，Mac 或 Windows）读；数据来源可以是店铺自接的 SP-API / Ads API，也可以是领星 / 优麦云 / 赛狐 ERP。
  任何改店铺的动作只交方案，由老板在审核报告里本人输入「确认推送」。触发：要店铺数据、日报、库存、利润、广告报表、账户健康、推送方案。
metadata:
  version: "0.3.0"
---

# 团队数据底座（amazon-data-hub）

## 红线（任何时候都不变）
1. **不碰凭据**：不要、不读、不转述 SP-API / Ads API / 领星 / 优麦云 / 赛狐的任何密钥和凭据文件。老板把密钥发进对话，就提醒他撤回，改在数据底座的接入向导里填。
2. **只读 + 出方案**：你可以读数据、算、写建议；不调用任何推送接口，不替老板勾选，不替老板输入「确认推送」。
3. **写入只走审核报告**：方案由运营总监用 `plan import` 交给数据底座，生成审核报告链接发给老板。没有老板确认推送后的回读结果，不写「已上线 / 已生效」。
4. **数据要标新鲜度**：引用任何数字都写上：经营单元、数据来源（自接 / 领星 / 优麦云 / 赛狐）、数据日期。超过 2 天没更新的，标「⚠️ 数据旧」，不拿来做闸门决定。

## 先看配置
读 `{{WORK}}/config/data_sources.yaml`（没有就从 `{{KIT}}/config/data_sources.yaml` 复制一份）：
- `units`：有哪些经营单元，每类数据从哪来，能不能推送
- `hub.access`：本平台怎么连数据底座
- 某类数据写的是 `none`，就如实说「这个单元没接这类数据」，不要编

## 怎么连
| 平台 | 做法 |
|---|---|
| OpenBot / StarSky | 在本机运行数据底座命令 |
| Grok Bot | **只有运营总监**通过「Execution on this computer」在老板电脑上执行命令；每条命令老板点批准。其他角色要数据就找运营总监，或者读 `{{WORK}}/data/hub/<日期>/<单元>/` 里运营总监放好的文件 |

**Grok 上的运营总监怎么执行**：
1. 第一次：先看 `{{WORK}}/config/data_sources.yaml` 的 `hub.os`（mac / windows，没填就问老板一次再写进去）；然后在老板电脑上读 `~/.starsky-codex/current.json`（没有就 `~/.starsky-claude/current.json`），只取 `python` 和 `home` 两个字段，记进 `{{WORK}}/team.md` 的「数据底座」一节；**这两个文件之外，Mac 上别的文件一律不读**
2. 以后：`<python> <home>/skills/starsky-ads-terminal/scripts/terminal.py <命令>`，把输出原样存到 `{{WORK}}/data/hub/<日期>/`
3. 命令被老板拒绝、老板电脑离线、超时：不重试第二次，如实写进日报「数据底座离线」，改用前一天的数据并标「⚠️ 数据旧」

### Mac / Windows 两种写法（按 `hub.os` 选，下面的命令表都按 Mac 写）
| 用途 | Mac（终端） | Windows（PowerShell） |
|---|---|---|
| 安装记录 | `~/.starsky-codex/current.json`（或 `~/.starsky-claude/current.json`） | `$env:USERPROFILE\.starsky-codex\current.json`（或 `.starsky-claude`） |
| 运行数据底座 | `"<python>" "<home>/skills/starsky-ads-terminal/scripts/terminal.py" status` | `& "<python>" "<home>\skills\starsky-ads-terminal\scripts\terminal.py" status` |
| 解压资料包 | `unzip -o ~/Downloads/<SKU 短号>_上架资料包.zip -d ~/星空上架资料包/` | `Expand-Archive -Force -LiteralPath "$env:USERPROFILE\Downloads\<SKU 短号>_上架资料包.zip" -DestinationPath "$env:USERPROFILE\星空上架资料包"` |
| 资料包路径 | `~/星空上架资料包/<SKU>/listing` | `"$env:USERPROFILE\星空上架资料包\<SKU>\listing"` |

- 路径有空格或中文一律加引号；Windows 上命令报「不是可识别的命令」或中文乱码，把原文写进回执，不要换别的命令硬试
- 两边白名单一样：只有表里这几类，加上下面命令表里的数据底座命令

## 命令白名单（只能用这些）
| 命令 | 用途 | 谁 |
|---|---|---|
| `status` | 装没装、接入情况、服务状态、有没有问题 | 运营总监 |
| `launch` | 服务没开时打开（老板在电脑前时才用） | 运营总监 |
| `units` | 只读列出经营单元（自接 / 领星 / 优麦云，标明哪些是重复单元） | 运营总监 |
| `export --unit all --summary --stdout` | **日常取数用这条**：只读本机缓存，几秒返回订单、库存、广告、费用、健康、Listing 的汇总 JSON（≤200KB），每类带来源和数据日期；不联网、不推送、不含密钥和买家信息。单个单元用 `--unit A_US`，只要部分数据加 `--what orders,ads`。退出码 0 正常；2 有数据过期（照常输出，`manifest.stale` 列出哪类旧了）；3 单元不存在；4 未接入。规格见 `{{KIT}}/docs/数据底座_只读导出接口规格.md` | 运营总监 |
| `refresh` | 只读刷新：重读订单、广告、ERP 数据，重算诊断和简报（不推送、不发飞书）。**要跑 7～8 分钟**，Grok 会等不到输出；看板每分钟会自动同步，平时不用跑。只在 `export` 退出码为 2、且老板在电脑前时才用，跑完再 `export` 一次 | 运营总监 |
| `plan import "方案.json" --check` | 只校验方案格式 | 运营总监 |
| `plan import "方案.json"` | 生成推送前审核报告，返回链接（**只生成，不推送**） | 运营总监 |
| `unzip -o ~/Downloads/<SKU 短号>_上架资料包.zip -d ~/星空上架资料包/` | 把老板存下来的上架资料包解到固定位置（只限这一个目录） | 运营总监 |
| `listing create --pack ~/星空上架资料包/<SKU>/listing --check` | 新建 Listing 预校验：已有 SKU 拒绝 → 限制检查 → 产品类型 schema → 亚马逊 VALIDATION_PREVIEW。**不落库、不提交** | 运营总监 |
| `listing create --pack ~/星空上架资料包/<SKU>/listing` | 生成「新建 Listing 审核报告」，返回链接（**只生成，不提交**）。一份最多 5 个 SKU | 运营总监 |
| `listing aplus --pack ~/星空上架资料包/<SKU>/aplus` | 生成高级 A+ 审核报告（要先有 ASIN） | 运营总监 |
| `listing brandstory --pack ~/星空上架资料包/<SKU>/brand_story` | 生成品牌故事审核报告（要先有 ASIN） | 运营总监 |
| `listing update --pack ~/星空上架资料包/<SKU>/update` | 生成属性补充审核报告（亮点、长描述、搜索词等） | 运营总监 |
| `listing status --sku <Seller SKU>` | 回读一次：状态、ASIN、问题清单 | 运营总监 |

**禁止**：`api`、`ops`、`import`、`install`、`stop`、`schedule`、`brief`（会发飞书，要老板说了才行），以及 `cat` / `ls` 数据目录、读任何凭据文件、`curl` 本机端口去调推送接口。白名单外的命令一律不执行，要用就先问老板。

## 新建 Listing / 高级 A+ / 品牌故事（R12，2026-11-05 已在 A 店真实走通）
- 资料包格式和分工看 `{{KIT}}/docs/上架资料包规格.md`；四个子包（listing / aplus / brand_story / update）分开放
- 顺序：`--check` 补到 0 错误 → 审核报告 → **老板本人确认推送** → `listing status` 回读到 ASIN → 再做 A+ 和品牌故事
- 回读到 ASIN 和状态之前，不写 SUBMITTED / BUYABLE；A+、品牌故事要等亚马逊审核通过才写 `aplus_live_at`
- `--check` 报错就把原文转给对应角色补字段（属性 → 文案算法师；图片 → 出图工；价格 → 财务管家），不要自己猜值
- **只有 SP-API 能新建**：领星开放接口没有刊登接口；只接了领星或优麦云的店铺，新建照旧走照填包 + 老板后台创建

### 广告（Ads API）
- 广告优化师出 `plan.json` → 运营总监 `plan import "plan.json" --check` → `plan import "plan.json"` 生成审核报告 → 老板确认推送
- 新建活动（SP 手动 / 自动）先建暂停、全部回读一致才开启；同名活动会被拒绝
- 闸门 3 没批的预算不进方案；加预算一律在审核报告里写理由

## ERP（领星 / 优麦云）
- **不要在 Grok 里直接添加领星、优麦云的 MCP 或填它们的密钥**：密钥只填在数据底座的接入向导里，接口都由老板电脑调用（领星 IP 白名单也只认这台电脑的出口 IP）
- 同一个店铺自接了又在 ERP 里：数字合计只算一份，**推送一律走自接那边**；ERP 用来补头程、成本、退货、货件
- 领星：广告只有 SP（没有 SB / SD）；订单金额含税和运费，比官方高 1%～10%，利润按成本表算；不能新建活动、不能改五点和售价
- 优麦云：ACoS 只作方向（归因窗口未核实）；只能推送关键词改竞价、活动日预算只降；会员到期会停止同步
- 引用 ERP 数字时写明来源，例如「领星 · A_US · 2026-11-04」

## 各角色怎么用
- **运营总监**：每天第一次被唤醒时 `status` → `export --unit all --summary --stdout`，原样存到 `{{WORK}}/data/hub/<日期>/`（不跑 `refresh`，见命令表），再按台账合成 HTML 日报（店铺数据 + 团队进度）附件发老板；队友要数据你来取；方案统一由你 `plan import`
- **风控官**：G3 读账户健康 / 异常信号；有红灯就发 HALT，不等别人。
- **仓管物流**：FBA / AWD 库存、在途、补货信号；ERP 有头程数据时一起看。
- **财务管家**：订单金额、结算费用、广告花费；ERP 订单金额含税和运费，跟官方口径对不上时写明口径，利润按成本表算。
- **文案算法师 / 质检官**：上架后读 Listing 回读，跟上架资料包逐项核对。
- **广告优化师**：读逐日报表和诊断，出 plan.json；加预算、开新活动属于资金闸门，必须老板批。
- **复盘分析师**：按 T+1 / 3 / 7 读效果；样本小标「待定」。

## 没接通时
数据底座没装、没接入、服务没开：如实报告 `status` 的原话，告诉运营总监；不要用估算数字冒充店铺数据。新建 Listing、开新广告活动在接口接通前，照旧走上架资料包（老板后台创建）和 Bulk 表格（老板上传）。
