# 星空亚马逊 Bot Team

亚马逊美国站 **11 角色 AI 运营团队**：选品 → 风控 → 货源利润 → 文案 / 视觉设计 / 出图 → 质检 → 上架 → 新品广告 → 复盘 → 转 FBA / 补货 / 清仓。

**AI 干活，AI 互相检查，钱和风险由你拍板。**

📖 完整产品介绍（是什么、有哪些技能、配什么数据、能做什么）：打开 **[docs/产品介绍.html](docs/产品介绍.html)**（下载后双击用浏览器打开）

支持平台：Grok Bot、OpenBot；StarSky 后续版本。两个版本都有：

| | 基础运营版 | 旗舰版 |
|---|---|---|
| 最新版本 | 1.0.0-rc.12（正式版） | 1.0.2（正式版） |
| 定位 | 11 角色团队出方案：选品、文案、图片、广告方案、上架资料包 | 基础运营版全部内容 + **本机广告终端**：接入你自己的店铺数据，日常运营、每日简报、审核后推送 |
| 店铺数据 | 不接，没装广告终端也能用 | 读你自己的 Ads API / SP-API，或优麦云、领星；数据只在你自己电脑上 |
| 授权 | 基础版授权码 | 旗舰版授权码（**一张码同时解锁团队和广告终端**；基础版授权码不能装旗舰包，反过来也不行） |
| 发行页 | [v1.0.0-rc12](https://github.com/wenjiany312-hub/starsky-amazon-botteam/releases/tag/v1.0.0-rc12) | [v1.0.2-flagship](https://github.com/wenjiany312-hub/starsky-amazon-botteam/releases/tag/v1.0.2-flagship) |

买旗舰版即包含基础运营版的全部内容，用不用都可以。

---

## 下载

### 旗舰版 1.0.2（推荐）

一个安装包同时含 Grok Bot 和 OpenBot 的团队 + 本机广告终端，装哪个平台由授权码决定，按电脑系统选一个：

| macOS | Windows |
|---|---|
| [Starsky-BotTeam-Flagship-1.0.2-macos.zip](https://github.com/wenjiany312-hub/starsky-amazon-botteam/releases/download/v1.0.2-flagship/Starsky-BotTeam-Flagship-1.0.2-macos.zip) | [Starsky-BotTeam-Flagship-1.0.2-windows.zip](https://github.com/wenjiany312-hub/starsky-amazon-botteam/releases/download/v1.0.2-flagship/Starsky-BotTeam-Flagship-1.0.2-windows.zip) |

装完团队后，安装器会**自动把「星空广告终端」装到你本机**，并在桌面生成三个图标：启动星空广告终端 / 星空每日经营简报 / 停止星空广告终端。双击启动，按接入向导接 ERP、店铺 API、维护成本表，然后就能看店铺数据和每日简报。

### 基础运营版 1.0.0-rc.12

| 平台 | macOS | Windows |
|---|---|---|
| Grok Bot | [Starsky-BotTeam-1.0.0-rc.12-macos.zip](https://github.com/wenjiany312-hub/starsky-amazon-botteam/releases/download/v1.0.0-rc12/Starsky-BotTeam-1.0.0-rc.12-macos.zip) | [Starsky-BotTeam-1.0.0-rc.12-windows.zip](https://github.com/wenjiany312-hub/starsky-amazon-botteam/releases/download/v1.0.0-rc12/Starsky-BotTeam-1.0.0-rc.12-windows.zip) |
| OpenBot | [Starsky-BotTeam-1.0.0-rc.12-openbot-macos.zip](https://github.com/wenjiany312-hub/starsky-amazon-botteam/releases/download/v1.0.0-rc12/Starsky-BotTeam-1.0.0-rc.12-openbot-macos.zip) | [Starsky-BotTeam-1.0.0-rc.12-openbot-windows.zip](https://github.com/wenjiany312-hub/starsky-amazon-botteam/releases/download/v1.0.0-rc12/Starsky-BotTeam-1.0.0-rc.12-openbot-windows.zip) |

不要下载 `Source code (zip)` / `Source code (tar.gz)`，它们不是安装器。校验值见各发行页的 `SHA256SUMS.txt`。

---

## 宿主（团队装在哪）从哪里下载

本产品是装进**你自己的 AI 平台**里用的，平台账号自己注册、费用自己管理：

| 宿主 | 下载 | 说明 |
|---|---|---|
| **Grok Bot** | [x.ai/bot](https://x.ai/bot)（官方产品页；桌面版 Mac / Windows 从页面下载，另有 iOS App） | xAI 的 AI 员工产品，**和 Grok 聊天（grok.com）是两个独立产品，别下错**。团队跑在它的云电脑上；需要你自己的 Grok Bot 订阅（Premium / Super / Ultra 等档，或已有的符合条件的计划已包含），套餐和用量自己管理 |
| **OpenBot** | [github.com/nightly-labs/openbot/releases](https://github.com/nightly-labs/openbot/releases) 或官网 [openbot.run](https://openbot.run) | Mac、Windows 安装包都在 Releases 页。团队跑在你自己的电脑上 |

> ⚠️ OpenBot 只认 **nightly-labs** 这一个（官网 openbot.run）。GitHub 上有别的同名项目（比如 CopilotKit/OpenBot），装错了会在文件夹授权和 MCP 两步全部卡死，请认准仓库作者 nightly-labs。

旗舰版选哪个宿主都可以：Grok Bot 的成果包下载回本机、由广告终端收包；OpenBot 在本机直接读终端数据，对接更紧密。也可以两个都装（授权要分别开通）。

---

## 这套团队能做什么（两个版本共有）

| 板块 | 内容 |
|---|---|
| 选品 | 先访谈你的需求（站点、价位、精铺还是纵深、目标毛利率），确认方向后拉 Sorftime / 卖家精灵数据出市场报告；竞争难度、季节选品、组合与定价、产品开发 PRD |
| 风控 | 侵权与合规排查、类目限制、账户健康，红线一票否决 |
| 货源与利润 | 1688 找厂与供应商评估、头程与 FBA 费用、FBM / FBA 双算、保本 ACoS |
| 内容 | 关键词词库、Listing 文案、主副图与 A+ / 品牌故事设计、出图与三项复检、上架前质检 |
| 上架 | 新建 Listing 资料包、审核报告、本人确认后推送、回读 ASIN |
| 广告 | 新品期广告架构、Bulk 候选、报表诊断与优化建议 |
| 复盘与运营 | 第 3 / 7 / 14 / 30 天复盘、转 FBA、补货、清仓、下架 |

11 个角色：运营总监、选品侦察员、风控官、仓管物流、财务管家、文案算法师、创意美工、出图工、质检官、广告优化师、复盘分析师。51 个技能、4 个本地 MCP（1688 抓取，选品 / 广告 / 促销知识库）。完整清单见 **[产品介绍.html](docs/产品介绍.html)**。

**店铺安全第一**：团队不登录你的卖家后台或广告后台。上架、改价、开广告一律先生成审核报告，由你本人在报告里输入「确认推送」才执行。

---

## 基础运营版

**适合**：先用团队做选品、文案、图片、广告方案和上架资料包，店铺操作自己在后台完成。

**包含**
- 11 个角色的运营团队（Grok Bot / OpenBot）
- 51 个技能
- 4 个本地 MCP：1688 抓取，选品 / 广告 / 促销知识库
- 服务期内的版本更新
- 服务期内 2 次免费换绑

**不包含**
- Grok Bot 或 OpenBot 本身的费用：用你自己的账号，额度用完需自行补充
- SIF、Sorftime、西柚、卖家精灵等第三方数据账号：由你自己购买
- 店铺数据接入、自动上架、开广告、店铺日报：属于旗舰版。基础运营版里团队会出上架照填包和广告 Bulk 表，由你在卖家后台自己操作

**数据底座是可选的**：没装广告终端时，选品照常（靠 SIF / Sorftime 等数据服务），上架和运营拿不到店铺数据。

---

## 旗舰版

**适合**：要让团队读你自己的店铺数据、做日常运营，并把确认过的方案推到店铺。

**比基础运营版多什么**
- **星空广告终端（装在你自己电脑上，随旗舰安装包一并安装）**：桌面双击启动，接入向导接店铺数据，生成每日简报、店铺巡检、广告和文案建议；全程只读，推送只在你本人输入「确认推送」后执行
- **Amazon Ads API 只读接入**：读取你自己的 Sponsored Products 报表，用于广告复盘和新品期账户事实核对；不修改广告后台
- **SP-API 接入**：读取订单、库存、Listing；文案只支持单个 SKU，经你本人确认后推送
- 日常取数读取广告终端的本地导出，不再每次触发整店刷新

### 广告终端怎么用

1. 运行旗舰安装包的「安装与更新」，装完团队后安装器自动装本机广告终端，桌面出现三个图标（启动 / 简报 / 停止）
2. 双击「启动星空广告终端」，按接入向导依次接：ERP（领星 / 优麦云）→ 店铺 API（Ads API / SP-API）→ 维护成本表
3. 接好后看综合看板；「星空每日经营简报」双击直接看今天的经营
4. SP-API / Ads API / 领星 / 优麦云的密钥**只填在接入向导里**，不进对话、不进工作目录、不上传任何云端；数据全部留在你自己电脑上

### 两个平台怎么分工

| | Grok Bot | OpenBot |
|---|---|---|
| 团队跑在哪 | Grok Bot 云端（有云端电脑） | 你自己的电脑，OpenBot 本身就是底座，可直接调用模型 |
| 团队做什么 | 选品、文案、广告、图片等生成，以及其他数据生成的机械性工作 | 同左，并可直接读取本机的广告终端导出 |
| 成果怎么交给终端 | 运营总监把每批成果打成 zip，你下载到本机 `~/Downloads/grok bot team`（Windows：`%USERPROFILE%\Downloads\grok bot team`），再由广告终端一条命令收包核对 | 本机直接读取，对接更紧密 |
| 安装得到什么 | 带授权编号水印的上传包 zip，发给 Grok Bot 里的运营总监搭建 | 写入 OpenBot 目录的 `kit/`，并打印一句搭建口令 |

旗舰版负责店铺侧：接入你自己的店铺数据并执行运营，不依赖基础运营版也能单独工作。

**Grok Bot 的账号是多设备共用的，云端团队只此一份。** 你在 Mac 上把团队升级到新版后，换到 Windows（或手机）登录同一个 Grok 账号，看到的就是已经升级后的团队——**不用、也不要在第二台电脑上重复做 Grok 侧的升级**。需要每台电脑各自安装 / 升级的只有跑在那台电脑上的东西（本机广告终端）。

### 旗舰版的边界

- 广告终端装机若因网络等原因中断，重跑「安装与更新」即可补装，已接入的数据不会动；遇到问题联系作者
- 只读报表目前覆盖 Sponsored Products
- 利润与广告建议是经营估算，没有收益保证

---

## 系统要求

- macOS 12 或更新版本，或 Windows 10 / 11
- Python 3.11 或更新版本（安装器会检查并提示；广告终端的运行环境安装器自动准备）
- 首次安装需要联网

---

## 怎么申请授权

1. 从上面的「下载」表下载安装器（基础运营版 Grok Bot 与 OpenBot 是不同的包；旗舰版一个包两个平台）
2. 解压后双击「申请授权」，记下显示的 **32 位本机申请码**
3. 把「产品名（基础运营版 / 旗舰版）+ 平台（Grok Bot / OpenBot / StarSky）+ 申请码」发给作者
4. 拿到授权码后，双击「安装与更新」，粘贴授权码
5. 按平台安装：
   - **Grok Bot**：安装器生成带授权编号水印的上传包 zip → 发给 Grok Bot 里的运营总监 → 按包里的 `BOOTSTRAP.md` 搭建团队
   - **OpenBot**：见下面「OpenBot 用户怎么装」
   - **StarSky**：后续版本支持

详细步骤：

- [Mac 安装说明](docs/安装说明_Mac.md)
- [Windows 安装说明](docs/安装说明_Windows.md)

---

## 以后怎么更新（两种办法，任选一种）

**办法一（推荐）：双击「检查新版」**

安装器自己去 GitHub 发行页取最新版，下载、校验、安装，你不用手动下载任何东西。

- 团队按平台更新：Grok Bot 生成新的上传包；OpenBot 旧 `kit` 改名备份、`work/` 不动
- 旗舰版连本机广告终端一起更新，**已接入的店铺数据和配置保留**
- **只对会员版有效**。固定版本试用点它会提示「固定版本试用不能安装新版」——这是正常提示，不是出错

**办法二：到发行页下载新安装包，再双击「安装与更新」**

- 在[发行页](https://github.com/wenjiany312-hub/starsky-amazon-botteam/releases)下载对应系统的新版 zip，解压后双击里面的「安装与更新」
- ⚠️ **老安装包里的「安装与更新」只重装它自己那一版，不会去拉新版**。要新版，必须先下载新安装包
- 会员版和固定版本试用都可以这样装（固定版本试用只能装与授权对应的那一版）

**更新后先看安装器打印的「本次更新范围」**（1.0.2 起）：

- **只更新了本机广告终端和安装器** → 团队提示词、技能一个字没变，**不用**给运营总监发口令、也不用把上传包发给云端总监，双击桌面的「启动星空广告终端」即可
- **团队内容（提示词 / 技能）有变化** → Grok Bot 用户把新生成的上传包发给云端运营总监，并让它把 **11 个助手的系统提示词全部重发一遍**（行为改动都在提示词里）；OpenBot 用户按安装器打印的升级口令让运营总监对齐。两种情况 `work/` 都不会被改动

**Grok Bot 团队一份只升级一次**：在任意一台电脑上运行安装、生成新上传包、发给云端运营总监完成升级即可；其他设备登录同一个 Grok 账号，自动看到的就是新版，不用重复操作。

**到期后**：已安装的内容继续可用，只是不再收到新版本。

---

## OpenBot 用户怎么装

OpenBot 版不生成上传包，由安装器把团队包直接写进你的电脑。

**前提**
- 基础运营版：下载 **OpenBot 专用安装包**（`…-openbot-macos.zip` / `…-openbot-windows.zip`），Grok 版安装包只含 Grok 的团队包，用它会提示没有 OpenBot 内容；旗舰版：下载旗舰安装包即可，两个平台都在里面
- 先装好 OpenBot（[nightly-labs/openbot releases](https://github.com/nightly-labs/openbot/releases)，MagPie 可选，用来给每个助手单独配模型）
- 授权码要开通 OpenBot 平台：联系作者，用同一个授权编号重签一张码，到期日不变。老码只有 Grok Bot，安装时选 OpenBot 会被拒绝

**安装**
1. 双击「安装与更新」，粘贴开通了 OpenBot 的授权码，平台选 **OpenBot**
2. 默认装到 `~/OpenBot/Shared/starsky-amazon-botteam`（下称 `kit`），工作目录 `work` 与它同级
3. 配本地 MCP：Mac 运行 `bash kit/mcp/local/install_local.sh`，Windows 运行 `powershell -ExecutionPolicy Bypass -File kit\mcp\local\install_local.ps1`。脚本会装好 4 个本地 MCP 并打印注册参数；**工作目录一律留空**，启动命令只填程序本身，参数一个一个加
4. 在 OpenBot「你的应用」→「添加自定义项」逐个添加这 4 个 MCP，传输方式选 **STDIO**，点「测试连接」看到「已连接 · N 个工具」再保存
5. 在 OpenBot 里对运营总监说「按 kit/SETUP.md 搭建团队」。它会用 `openbot.create_agent` 创建 11 个助手，装本地技能并按角色开开关，再把每个助手的「访问权限」设为「先询问」、文件夹加上 `kit` 和 `work`
6. 1688 第一次抓详情要你本人扫码登录（`login_1688`），登录态存在 `work/mcp/starsky-1688/pw_user_data`，升级不丢

**注意**
- 安装器不会打开 OpenBot，也不会动你已有的助手和设置
- 只有你能改助手权限；助手自己不能把「先询问」放宽成「完全访问」
- 详细步骤见 `kit/SETUP.md`

**升级**：运行「安装与更新」选 OpenBot。旧 `kit` 会改名为 `kit_<旧版本>_备份` 保留，`work/``（台账、产品文件、config）一个字都不动。之后按 `kit/docs/升级说明.md` 让运营总监换提示词、补技能、核对 MCP 和权限。

---

## 已装用户怎么升级到新版

以下是**基础运营版**的升级步骤。**旗舰版**更简单：运行旗舰安装包里的「检查新版」在线升级，或下载新的旗舰安装包运行「安装与更新」，效果一样。都不要删除任何助手、台账和产品文件。

**Grok Bot**：运行「检查新版」→ 下载新版本 → 生成新的上传包 zip → 发给 Grok Bot 里的运营总监，贴下面这段口令（附件就是新上传包）：

```
团队包升级到 1.0.0-rc.12（新增「选品证据方法」）。附件是用「检查新版」或「安装与更新」生成的 rc12 上传包。按升级模式做：不删除任何 Bot、台账、产品文件和已有设置，每步一句进度，中间不用问我。kit 里的文件一律不改。
1. 解压附件到 /workspace/amazon-ops/，原 kit 改名为 kit_<当前版本号>_备份（看现在 kit/team.yaml 里的版本），新文件夹改名为 kit，跑 CHECKSUMS 校验，必须全部通过
2. /workspace/amazon-ops/config/ 里的运行设置都不动
3. 安装新的共享技能 amazon-selection-squad-method：全文安装，内容照 kit/skills/amazon-selection-squad-method/SKILL.md；已有同名的读回比对，以 kit 里的为准
4. 把 11 个 Bot 的系统提示词全部重新发一遍（kit/agents/<id>.md「系统提示词」全文，包括你自己），前面加「这是你更新后的长期工作说明，替换之前的版本」
5. 重跑 bash /workspace/amazon-ops/kit/mcp/local/install_grokbot.sh（已装的不会重装），报一下 4 个本地 MCP 和已接的第三方 MCP 各自通不通；**只看连没连上，不要为了测试去调用卖家精灵等按次扣费的数据工具**
6. 验证：①选品侦察员说出五维打分里权重最高的是哪一维、对标池的门槛；②质检官说出它上架前多查的是什么；③逐个问 11 个 Bot 长期工作说明末尾的版本标记，应全部是 1.0.0-rc.12，授权编号和升级前一致
7. 交升级回执（每个 Bot 的版本号逐个列出）
```

**OpenBot**：运行「安装与更新」选 OpenBot → 旧 kit 自动备份、`work/` 不动 → 按 `kit/docs/升级说明.md` 让运营总监对齐助手。

**不想用口令**：运行「安装与更新」下载新版安装包，上传包照样能生成；升级时不要删除任何助手、台账和产品文件。

---

## 授权边界

授权锁的是**下载、安装和更新**。装进 Bot 平台以后，提示词是明文，可以复制——这一点靠授权编号水印和使用条款约束，不做技术复制保护。

请勿转售、公开分发安装包或授权码。

---

## 授权与使用条款

**安装即表示同意[《使用条款》](docs/使用条款.md)**，全文见该文件。要点：

- **一张授权 = 一台电脑 + 一个正在使用的 Grok 账号**，外加服务期内的更新。申请授权时请把 Grok 账号邮箱一起发给作者登记
- 同一个 Grok 账号在 Mac、Windows、手机上登录使用，不用重新授权；同时用多个 Grok 账号，每个账号一份授权
- 账号被封、换新账号或换电脑时，凭授权编号和设备码申请换绑；Grok 自己重建云电脑、账号没变，不算换绑
- **不允许**：转给他人、转售、公开发布 kit / 角色提示词 / 技能 / 知识库，或装进未登记的 Grok 账号；删除或篡改水印；改名拆分后当作自己的产品出售
- 每个安装包都带授权编号水印；发现外流，按编号追查，停止该授权的更新与服务，不退费
- 本产品只做分析、方案和资料准备，上架、改价、广告等改店铺的操作由你本人确认后执行，经营结果由你自己负责

---

## 关于作者

**坚哥** —— 亚马逊多年卖家出身，公众号「**跨境者说干货**」主理人，知识星球「星空的跨境圈子」发起人。这套团队和广告终端的方法论，都来自实际店铺的一线运营经验。

- 微信公众号：搜索 **跨境者说干货**
- 知识星球：[星空的跨境圈子](https://wx.zsxq.com/group/28882244282441)

---

## 授权与联系

**公开下载，联系坚哥确认购买与本机授权。**

购买流程：加微信备注「购买插件」→ 确认付款 → 下载并解压对应安装包 → 运行「申请授权」并发送本机申请码 → 收到授权码 → 运行「安装与更新」完成激活与安装。不要发送任何店铺凭据。

<p align="center"><a href="docs/assets/个人微信.png"><img src="docs/assets/个人微信联系卡.png" alt="个人微信：扫码添加，备注「购买插件」（只咨询备注「咨询星空」），并注明 Bot Team 基础版或旗舰版。一般 24 小时内回复。点击查看二维码原图。" width="640"></a></p>

<p align="center"><img src="docs/assets/公众号_跨境者说干货.png" alt="微信搜索：跨境者说干货，联系坚哥申请正式版授权" width="640"></p>

个人微信一般 **24 小时内回复**。添加时备注「购买插件」（只咨询备注「咨询星空」），并注明要装的平台（Grok Bot / OpenBot）和版本（基础运营版 / 旗舰版）。

- 微信公众号：搜索 **跨境者说干货**，后台留言「Bot Team 授权」
- 知识星球：[星空的跨境圈子](https://wx.zsxq.com/group/28882244282441)，私信坚哥

---

© 星空 · 保留所有权利。本仓仅发布安装器与发行信息，不包含业务源码。
