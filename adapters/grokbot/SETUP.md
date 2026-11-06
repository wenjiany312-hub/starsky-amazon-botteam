# 在 Grok Bot 搭建星空亚马逊 Bot Team（v{VER} · {EDITION}）

你是老板的 **Primary Bot**。按这个文件在老板的账户里搭出 11 个 Bot 的亚马逊运营团队。**从上到下一口气做完**：每完成一步发一句简短进度，**不要停下来问「要继续吗」**。只有这几种情况才停下来等老板：第 1 步校验失败、第 8 步审批规则（要老板本人设置）、任何一步做不下去。
**搭建全程只读**：不要任何店铺密钥，不登录亚马逊后台，不联系供应商，不花钱。

## 0. 先看账户里已经有什么
列出账户里现有的 Bot 和共享技能。
- 如果有 2026 年 8 月那一版团队（坚哥、选品侦察员、文案算法师、创意美工、出图工、广告优化师、财务管家、仓管物流、内容编辑），**不要修改、不要删除**，只列出来问老板：保留 / 归档。新团队另外创建
- 有同名 Bot 的，也不要改它，新建时在名字后面加「·新」，并告诉老板

## 1. 放好团队包
老板会把 `{PKG}.zip` 作为附件发给你。
```
mkdir -p {WORK}
cd {WORK} && unzip -o <附件路径>/{PKG}.zip
# 解压出来是 {WORK}/{PKG}/，改名为 kit（kit 已存在时先停下来问老板，不要覆盖）
mv {WORK}/{PKG} {KIT}
cd {KIT} && sha256sum -c CHECKSUMS.txt | grep -v ': OK$' || echo 校验全部通过
```
有任何一行不是 OK，就停下来告诉老板。

## 2. 先读，再动手
1. `00_先读我.md`、`docs/团队角色介绍.md`
2. `skills/amazon-team-operating-model/SKILL.md`（全队共同规则）
3. `agents/` 下全部 11 个文件
4. `workflow/sku_states.yaml`、`workflow/交接协议.md`、`workflow/gates.yaml`、`workflow/输出排版规范.md`、`safety/红线.md`、`config/team_settings.yaml`

## 3. 建工作目录和台账
```
mkdir -p {WORK}/台账 {WORK}/products {WORK}/reports {WORK}/messages {WORK}/config {WORK}/data/hub
cp -n {KIT}/config/team_settings.yaml {KIT}/config/mcp_assignment.yaml {KIT}/config/data_sources.yaml {WORK}/config/   # 运行设置放在 kit 外面，升级不丢
cp {KIT}/workflow/sku_registry_template.csv {WORK}/台账/sku_registry.csv
sed -i '2d' {WORK}/台账/sku_registry.csv   # 删掉示例行，只留表头
```

## 4. 创建 11 个 Bot
| Name | 档案文件 | Job |
|---|---|---|
{ROLE_TABLE}

对每个 Bot：
1. 按档案卡填 **Name、Job**；**Description** 照抄（没有 Description 栏就写在第一条消息开头）
2. 把档案文件里「系统提示词」一节**全文**作为它的第一条消息发过去，前面加一句：「这是你的长期工作说明。请记住，读完后用一句话说出你的职责。」
3. 让它记住：不确定时重新读 `{KIT}/agents/<自己的 id>.md`
4. 记下每个 Bot 的 **ID**（名字不算数）

建议把 **运营总监** 设为老板的第一联系人。老板已经有 Primary Bot（就是你）的话，问老板要不要换成运营总监。
不能自动创建的，把档案卡和系统提示词做成可复制的文本块交给老板手动建。

## 5. 写 ID 名册，发给运营总监
写 `{WORK}/team.md`（格式见第 10 步），里面有每个角色的 Bot ID。然后用 SendToAgent 告诉运营总监：「团队名册在 {WORK}/team.md，派单用里面的 Bot ID。」

## 6. 安装共享技能
Grok Bot 的技能是账户内所有 Bot 共享的。
- **团队自己的技能**（正文短，**全文安装**）：{OWN_SKILLS}
- **星空专业技能**（正文长、要用相对路径，**存成指针**）：技能名和描述照 `SKILL.md` 头部填，正文写：
  > 使用这个技能时，读 `{KIT}/skills/<技能名>/SKILL.md` 并照做；技能里的相对路径都相对于那个文件所在的文件夹。
- 其余在 `skills/` 里、但下表没有列出的文件夹（合同、参考、编排类）**不用安装**，留在团队包里给其他技能引用
- 已有同名技能的，**不要覆盖**：读回比对，不一致就告诉老板

| 技能 | 安装方式 | 给谁用 |
|---|---|---|
{SKILL_TABLE}

{MISSING_LINE}

装完后，用 SendToAgent 告诉每个 Bot 它自己有哪些技能（见它档案文件头部的 `skills`）。

## 7. 群聊（可选）
Grok Bot 一个群最多 6 个 Bot。派单走 SendToAgent，群聊只是方便老板旁观。老板要的话，建这 3 个：
| 群聊 | 成员 |
|---|---|
| 选品群 | 运营总监、选品侦察员、风控官、仓管物流、财务管家 |
| 内容上架群 | 运营总监、文案算法师、创意美工、出图工、质检官、风控官 |
| 广告复盘群 | 运营总监、广告优化师、复盘分析师、财务管家、仓管物流 |

每个群发第一条消息：「这是{群名}。派单走私聊，这里只同步进度；只回复 @ 到自己的消息；要老板批准的事转到运营总监私聊。」

## 8. 审批规则（请老板本人设置）
请老板打开 **Settings → General → Auto-review**（或 Settings → Permissions），加上：

**Ask first（先问老板）**
1. 在 sellercentral.amazon.com、advertising.amazon.com 上的任何提交、保存、上传、删除
2. 向供应商发消息、下单、付款（1688、阿里巴巴、旺旺）
3. 任何购买、付款、订阅
4. 发送邮件、Slack 消息、X 帖子
5. 删除 `{WORK}/` 以外的文件，或者任何外部数据

**Allow automatically（可以自动做）**
1. 读取已连接的数据工具（SIF、Sorftime 等）
2. 浏览 amazon.com 前台、1688 公开页面
3. 在 `{WORK}/` 下创建和修改文件；运行 `{KIT}/` 下的脚本和知识库查询

没设置的，团队档案里记「未设置」，团队保持**只读模式**。

## 9. 接数据工具
按 `mcp/grokbot_接入说明.md`：老板自己买的工具（SIF、Sorftime 等）通过连接器或插件接入，**密钥用安全密钥卡填**，不让老板把密钥贴进对话。没接的记「未接入」，不影响搭建。
搭建完成后，老板可以随时把 MCP 清单（可以照 `mcp/我的MCP清单_模板.md` 填，也可以直接给截图或配置片段）拖给运营总监，由运营总监按技能 `amazon-mcp-onboarding` 一次接好、分配、体检。

## 9b. 装本地 MCP（1688 抓取 + 3 个知识库）
```
bash {KIT}/mcp/local/install_grokbot.sh
```
脚本最后会打印 4 条注册命令。逐条在对话里「添加自定义 MCP 服务器（by command）」，名称照抄：`1688_scraper`、`selection_knowledge_base`、`ads_knowledge_base`、`promotion_knowledge_base`；环境变量都加 `PYTHONUTF8=1`。老板点确认后，在 Settings → Plugins → Yours 核对 4 个都在。
然后请老板打开 **Settings → Computer → Your computer → Route traffic through this computer**（1688 会屏蔽云服务器 IP，打开后看到的是老板电脑的网络；需要老板电脑上的 Grok Bot 桌面端开着）。
脚本任何一步报 ❌ 就停下来，把报错原文交给老板。

## 10. 团队档案 `{WORK}/team.md`
只写**实际核对过**的：
```
# 星空亚马逊 Bot Team · 团队档案
- 创建时间：
- 团队包版本：{VER}（{EDITION}）
- 角色 → Bot ID：运营总监 / 选品侦察员 / 风控官 / 仓管物流 / 财务管家 / 文案算法师 / 创意美工 / 出图工 / 质检官 / 广告优化师 / 复盘分析师
- 旧团队（8 月版）：保留 / 归档 / 没有
- 技能：<名称 → 全文 / 指针 / 已存在一致 / 不一致>
- 群聊：<名称 → ID>（没建就写「未建」）
- 审批规则：已设置 / 未设置（未设置 = 只读模式）
- 数据工具：<名称 → 已接入 / 未接入>
- 本地 MCP：1688_scraper / selection_knowledge_base / ads_knowledge_base / promotion_knowledge_base → 已注册 / 未注册；Route traffic：开 / 关
- 出图方式：image_mode = in_team（第 11 步第 6 项不通过就改成 outsource）
- 自动化等级：L1
```

## 11. 只读验证
1. 私聊运营总监：「发一份台账日报。」→ 预期：中文交付卡，台账 0 行
2. 私聊运营总监：「用 SendToAgent 让风控官用一句话分别解释 IP-DES 和 HAZMAT，把它的回答转给我。」→ 预期：派单成功，拿到回复
3. 私聊财务管家：「假设售价 $24.99、采购 ¥18.50、包装后 0.36 kg、FBM 国内直发，列出你还缺哪些数据才能算利润。」→ 预期：列出缺项，不编数字
4. 私聊广告优化师：「用知识库查一下『新品期 出价』，给我 3 条建议并写出卡片编号。」→ 预期：命令行查询成功，带 KC 编号
5. 私聊运营总监：「按报告模板做一份空的批次报告 HTML，作为附件发给我。」→ 预期：能打开的 HTML 附件
6. 私聊出图工：「试着生成一张白底的测试图：一个白色陶瓷马克杯。生成不了就直接说。」→ 能生成：老板决定要不要把 `visual.image_mode` 改成 `in_team`；生成不了：保持 `outsource`

## 12. 交回执
一张表交给老板：11 个 Bot（ID、状态）、技能（状态）、群聊、审批规则、数据工具、6 项验证结果；并确认「搭建全程只读，没有要密钥，没有动店铺」。
全部通过后告诉老板：
> 团队搭好了。私聊运营总监，发 `docs/试跑口令.txt` 里的口令，就能开始第一轮选品试跑。

有任何一项没通过，写清楚是哪一项、下一步怎么补，不要说「已就绪」。
