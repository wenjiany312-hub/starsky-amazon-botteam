# 在 OpenBot 搭建星空亚马逊 Bot Team（v{VER} · {EDITION}）

> 界面口径：2026-11-11 按老板 Mac 上的 OpenBot **0.67.1** 只读核对（助手设置、技能页、应用页的实际文案）。OpenBot 升级后文案可能微调，以界面上同义的按钮为准。

你是老板在 OpenBot 里指定的**主控助手**（建议直接用「运营总监」）。按这个文件在老板电脑上的 OpenBot 里搭出 11 个助手的亚马逊运营团队。**从上到下一口气做完**，每完成一步发一句进度，不要停下来问「要继续吗」。只有校验失败、要老板本人操作、或做不下去时才停。
**搭建全程只读**：不要任何店铺密钥，不登录亚马逊后台，不联系供应商，不花钱。

## 0. 先看 OpenBot 里已经有什么
- 用 `openbot.list_agents` 列出现有助手，用 `openbot.list_local_skills` 列出本地技能，「你的应用」页列出已接的应用。已有的**不改、不删**；同名助手新建时名字后加「·新」并告诉老板
- 2026-11 之前的旧团队（Admin、Scout、Guard 等英文名，或「坚哥」）：只列出来，问老板保留 / 归档
- 老板已有的助手可能带着旧记忆（助手设置 →「记忆」）。**不要自己删**，列出和新规则冲突的条目交老板决定

## 1. 团队包已经放好了
安装器（「安装与更新」）已经把团队包解密写到 `{KIT}`。先校验：
```
python3 {KIT}/scripts/verify_checksums.py {KIT}      # Windows：py -3 {KIT}\scripts\verify_checksums.py {KIT}
```
有任何一行不是 OK 就停下来告诉老板。

## 2. 先读，再动手
`00_先读我.md`、`docs/团队角色介绍.md` → `skills/amazon-team-operating-model/SKILL.md` → `agents/` 下 11 个文件 → `workflow/`、`safety/红线.md`、`config/team_settings.yaml` → `adapter/runtime.md`

## 3. 建工作目录和台账
在 `{WORK}` 下建：`台账/`、`products/`、`reports/`、`messages/`、`config/`、`data/hub/`；把 `{KIT}/config/` 里的 `team_settings.yaml`、`mcp_assignment.yaml`、`data_sources.yaml` 复制到 `{WORK}/config/`（已存在的不覆盖）；台账从 `{KIT}/workflow/sku_registry_template.csv` 复制，删掉示例行。

`{KIT}` 和 `{WORK}` 都在 OpenBot 的**共享文件夹**（`~/OpenBot/Shared/…`）里，所有助手默认都能读写，不用另外加文件夹权限。

## 4. 创建 11 个助手
**OpenBot 支持由助手代建**：用 `openbot.create_agent` 工具，一次建一个，参数给名称、称号/描述、第一条任务（把档案文件里「系统提示词」一节全文作为它的第一条任务发过去）。建完用 `openbot.list_agents` 核对 11 个都在，记下每个助手的 **agent id**（名字不算数）。

代建时要知道的三条限制：
1. 新助手**继承你自己的服务商、模型、推理档**，也继承你自己的访问权限和 Computer Use 限制。要换模型：先 `openbot.list_models`，再 `openbot.update_profile`（或 `openbot.update_agents` 批量设）
2. **只有老板本人能放宽权限**。你和新建的助手都不能把自己或队友的访问权限调高；需要放宽时停下来请老板做（第 7 步）
3. 工具调不通（老板没给你 OpenBot 工具权限）时**不要硬试**：把每个助手的「名称 / 称号 / 描述 / 系统提示词全文」做成可复制文本块交老板，让老板用侧边栏「新建助手」手动建，你继续做后面的步骤

| 名称 | 档案文件 | 职责 |
|---|---|---|
{ROLE_TABLE}

模型按 `config/team_settings.yaml` 的能力档分配：装了 **MagPie**（本机模型网关）就在助手设置 →「助手模型」里按档选；没装 MagPie 就用 OpenBot 当前服务商的默认模型，团队档案里记「未分配」。

## 5. 装技能
OpenBot 的技能是**本机技能库 + 按助手分配**，不是账户共享。两条路，优先第一条：

**A. 你自己装（推荐）**：`openbot.create_skill` 建技能（正文见下），再 `openbot.install_local_skill` 装到指定助手（`agentId` 给目标助手的 id），最后 `openbot.set_skill_enabled` 按档案开关。装之前先 `openbot.list_local_skills` 看同名技能在不在，**已存在的不要覆盖**：读回比对，不一致就告诉老板。

**B. 老板手动装**：技能页 →「你的技能」→ 来源筛选切到「本地」→「添加技能」→ 系统对话框「选择技能文件夹或 ZIP」→ 选 `{KIT}/skills/<技能名>/` 整个文件夹（或它的 zip）。装完在技能详情点「添加到助手」，勾选要用的助手 → 保存。也可以在每个助手的 助手设置 →「技能」里单独开关。

技能包的硬性要求（不满足会被 OpenBot 拒绝）：根目录必须有 `SKILL.md`，且以 YAML frontmatter 开头；**整包和解压后都要小于 10 MB**；不能含符号链接。团队包里的技能都满足，**不要**把 `{KIT}` 整个打成一个技能包。

装好后的落盘位置（核对用，不要手改）：
- 助手工作区：`~/OpenBot/Agents/<agent id>/.agents/skills/<技能名>/`（同一份也出现在 `.claude/skills/`）
- 本机技能库：`~/Library/Application Support/OpenBot/local-skills/<技能 id>/<版本>/bundle.zip`
- 安装清单：助手工作区的 `.openbot/skills-lock.json`
- Windows 对应 `%USERPROFILE%\OpenBot\Agents\…` 和 `%APPDATA%\OpenBot\local-skills\…`

装什么：
- **团队自己的技能**（全文安装）：{OWN_SKILLS}
- **星空专业技能**：技能名和描述照 `SKILL.md` 头部填，正文写「使用这个技能时，读 `{KIT}/skills/<技能名>/SKILL.md` 并照做；技能里的相对路径都相对于那个文件所在的文件夹」
- 其余在 `skills/` 里、但下表没列出的文件夹（合同、参考、编排类）**不用安装**，留在团队包里给其他技能引用
- 每个助手只打开它档案里 `skills` 列出的技能，其他助手关掉

| 技能 | 安装方式 | 给谁用 |
|---|---|---|
{SKILL_TABLE}

{MISSING_LINE}

## 6. 装本地 MCP（1688 抓取 + 3 个知识库）
```
bash {KIT}/mcp/local/install_local.sh          # Windows：powershell -ExecutionPolicy Bypass -File {KIT}\mcp\local\install_local.ps1
```
脚本最后打印 4 条注册信息。在 OpenBot 技能/应用页 →「你的应用」→「添加自定义项」（或「连接自定义 MCP」）逐个添加，**传输方式选 STDIO（本机命令）**，名称照抄：`1688_scraper`、`selection_knowledge_base`、`ads_knowledge_base`、`promotion_knowledge_base`。表单按脚本打印的填：
- **启动命令**：只填程序本身（例如 `/bin/bash` 或那个 venv 里的 `python`），**不要把整条命令行塞进去**
- **参数**：脚本打印的参数逐个「添加参数」，一行一个
- **环境变量**：四个都加 `PYTHONUTF8=1`
- **工作目录**：**留空**（填了目录的服务器只有 Claude / Codex 助手能用，其他服务商会跳过）
- 填完点「测试连接」，看到「已连接 · N 个工具」再保存

然后按 `{WORK}/config/mcp_assignment.yaml` 给每个助手开关（出一张「助手 × MCP」开关表给老板照着点）。
第三方数据（SIF、Sorftime、西柚、卖家精灵）：老板在「你的应用」里自己接，密钥只填在应用的密钥框，不进对话、不进文件。

## 7. 权限与审批（请老板本人设置）
OpenBot **没有** Grok 那种「按网址列 Ask first 规则」的全局设置。权限是**每个助手一份**：
侧边栏选中助手 → **助手设置** →「**权限**」，里面三处：
1. **访问权限**：`仅工作区` / `先询问` / `完全访问` —— 团队一律用「**先询问**」。**不要给「完全访问」**（给了就不再逐次问老板）。注意「仅工作区」只限制写入，读取和联网仍然放行，挡不住对外发消息
2. **文件夹**：`{KIT}`、`{WORK}` 在共享文件夹里，默认已包含；要额外给目录用「添加文件夹…」，要挡掉用「排除文件夹…」
3. **Computer Use**：默认**关掉**（团队不需要控制老板电脑上的应用）；「本地脚本」也保持关闭

逐次审批时，老板对下面这些一律点拒绝（团队提示词里也写了红线，两边都要有）：
- 在 sellercentral.amazon.com、advertising.amazon.com 上的任何提交、保存、上传、删除
- 向供应商发消息、下单、付款（1688、阿里巴巴、旺旺）
- 任何购买、付款、订阅；发送邮件、Slack、X 等对外消息
- 删除 `{WORK}/` 以外的文件

可以放行：读已连接的数据工具、浏览 amazon.com 前台和 1688 公开页、在 `{WORK}/` 下写文件、跑 `{KIT}/` 下的脚本和知识库查询。
老板没设置的助手，团队档案记「未设置」，该助手保持只读。**你自己不能放宽权限，只能请老板改。**

## 8. 数据底座（装了星空亚马逊 Agent 旗舰版的才有）
OpenBot 和数据底座在同一台电脑上：`{WORK}/config/data_sources.yaml` 里 `hub.access.openbot = local`，运营总监按技能 `amazon-data-hub` 的白名单直接在本机终端执行命令，其他助手找运营总监要数据。没装旗舰版就跳过，上架走照填包、广告走 Bulk 表。

## 9. 团队档案 `{WORK}/team.md`
只写实际核对过的：创建时间、团队包版本 {VER}、11 个助手（名称 → agent id → 状态 → 模型）、技能（全文 / 指针 / 已存在一致 / 不一致）、本地 MCP、第三方 MCP、每个助手的访问权限档位、数据底座、自动化等级 L1。

## 10. 只读验证
1. 运营总监：「发一份台账日报。」→ 中文交付卡，台账 0 行
2. 运营总监：「让风控官用一句话分别解释 IP-DES 和 HAZMAT，把回答转给我。」→ 派活成功
3. 财务管家：「假设售价 $24.99、采购 ¥18.50、包装后 0.36 kg、FBM 国内直发，列出你还缺哪些数据才能算利润。」→ 列缺项，不编数字
4. 广告优化师：「用知识库查『新品期 出价』，给 3 条建议并写出卡片编号。」→ 带 KC 编号
5. 运营总监：「按报告模板做一份空的批次报告 HTML。」→ 能点开的 HTML 链接
6. 仓管物流：「调 1688_scraper 的 ping，结果原样给我。」→ ok: true
7. **不要**为了验证去调用卖家精灵等按次扣费的数据工具，只看连没连上

## 11. 交回执
一张表：11 个助手（agent id）、技能、本地 MCP、第三方 MCP、每个助手的权限档位、数据底座、7 项验证结果；确认「搭建全程只读，没有要密钥，没有动店铺，老板原有的助手和设置一个没动」。
全部通过后告诉老板：团队搭好了，私聊运营总监发 `docs/试跑口令.txt` 里的口令开始第一轮选品。