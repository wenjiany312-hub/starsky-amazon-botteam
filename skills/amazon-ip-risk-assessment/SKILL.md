---
name: amazon-ip-risk-assessment
description: >
  亚马逊 IP/合规风险排查（实战版）。在关键词与文案前（或文案/图片定稿前）排查：
  商标词、竞品品牌、EPA/FDA/绝对化/夸大声明、图片叠字、外观相似初筛。
  触发：「IP排查」「合规检查」「商标检索」「能不能用这个词」「fix_required」。
  产出 04_IP 报告 + 04_compliance_blacklist.yaml，供词库/文案消费；总控在本步后等人决策。
  生态：OpenAI Codex。
version: "3.4.0"
edition: starsky-v2
edition_label: 实战版
release_tag: "2026-08-01"
author: 坚哥
author_id: jiangge
brand:
  wechat_mp: 跨境者说干货
  knowledge_planet: 星空的跨境圈子
  series: Codex亚马逊实战
  ecosystem: codex
---

## R01 M2 新案九步执行（旧案原进度）

执行S1前先按 `scripts/s1_roadmap.py` 解析真实产品根：新案 `r01.m2` 必须读 `templates/s1_m2_execution.md`，按新九步的需求门、定位确认、全图CT和页面数据先算规则执行；旧案 `legacy.m1` 保留原步骤、编号和门禁，不自动迁移。旧正文的第6步回填等待只属于legacy.m1，不能覆盖新版5→6异步厂家确认。所有用户决定绑定真实user_reply，不把本文或脚本默认值写成用户授权。M1紧凑落盘、原生字节与immutable decision/proof继续生效。

# IP · 合规风险排查 · 实战版

## 统一资料接入约束（00C，必读）

开始本 Skill 前，若当前产品 run 存在 00C 资料接入报告或 Overlay，必须先读它们，应用其中的可用来源、事实边界、合规约束和 MCP 补数计划；再读取本任务产物并执行。用户已提供未整理资料但没有 00C 时，先执行资料接入，不要求用户整理。读取 ../../templates/data_intake_read_contract.md 作为权威规则。

## 出品与归属（实战版）

| 项 | 内容 |
|---|---|
| 发行 | **实战版**（**2026-08-01**） |
| 作者 | **坚哥** |
| 公众号 | **跨境者说干货** |
| 知识星球 | **星空的跨境圈子** |
| 运行环境 | **OpenAI Codex** |
| 工作流位置 | `starsky-fullchain-workflow` 的 **ip_compliance → ip_decision** |

---

## 0. 角色

你是上架前的**合规闸门**，不是律师意见书。

| 做 | 不做 |
|---|---|
| 五维+量化风险、替代表达、黑名单 YAML | 不提供可上庭的专利法律结论 |
| 读 profile 的 brand_risks | 不跳过竞品品牌隔离 |
| 强制输出决策选项给用户/总控 | 不替用户默认 accept_risk 后拓词 |

**红线：** 宁可不发，别带 #1 / 竞品名 / 无认证抗菌杀虫宣称上架。

---

## 1. 触发与输入

**触发：** IP排查、合规检查、商标、能不能写 organic/certified…  
**输入优先：**

| 输入 | 来源 |
|---|---|
| `01_product_profile.yaml` | market-research |
| 竞品 ASIN / 标题 | profile 或 MCP |
| 文案草稿（若有） | listing 或用户 |
| 产品图（若有） | 1688/本地 |
| 叠层 / 资料包 compliance 提示 | playbook 或 `runtime_data_overlay.yaml` 的 S1 条目 |

无文案时：对**品类+竞品品牌+常见宣称**做前置排查（沙龙标准顺序：词库前先跑）。

---

## 2. 策略叠层

若有 enterprise/category overlay、`runtime_data_overlay.yaml` 的 S1 compliance/sop 来源或 `skill_patches/ip_risk_assessment.md` → 将可追溯规则合并进检查清单；资料包只能加严或补充，不能放宽 Core 合规和已识别 IP 风险。读取 `03_product_facts.yaml` 作为当前产品事实，并在报告写 source_id、时间窗与使用边界。  
任意类目的附加规则必须来自当前产品事实、可追溯资料或用户确认，不能因预置类目示例而被限制。

---

## 3. 排查维度

### 3.0 合规尺子（固定脚本，先跑）

先用 `scripts/compliance_ruler.py` 出一张「能不能做」的尺子，再做下面 D1–D6 的逐对象排查。用安装记录里的 Python，产品根带引号：

```text
1. <python> <skill>/scripts/compliance_ruler.py --run "<产品根>" --init
   → S1_选品板块/amazon-ip-risk-assessment/04A_ruler_inputs.yaml（自动收集画像、1688 事实、关键词文本，生成检索链接）
2. 助手只填两类内容：
   - attributes：23 个属性使用 true/false/null（是/否/待核）；不确定必须填 null，不能把未知保守写成 true。evidence 保留选定 SKU/BOM/目标规格依据，冲突写入 input_conflicts，待核为灰灯并列入待厂家确认清单，不参与最高灯。
   - selected_product：写采购 SKU、描述、materials/bom、attributes；未选 SKU 时 sku 为 null，target_spec 只存用户目标。reference_products 单列参考款，不能把竞品、排除字段、参考 SKU、lead_time 或其他字段名扫描成本品事实。竹木/Lacey 规则只读本品 BOM 或明确材料属性；能收纳木绣框不等于包的材质含木。
   - 参考货源有印花但未选 SKU 时只给“选 SKU 时避开印花款”提醒，不能认定本品授权 IP 红灯。选定款真实危险材质或授权 IP 的明确证据仍亮相应风险灯。
   - D2 检索失败或无权限时，为每项输出可操作自查卡（检索链接、输入词、步骤、截图回传 schema），状态为“待你查”。回传字段必须含 search_id、日期、词、筛选条件、结果数量、记录号、结果链接、截图、SKU、判断和覆盖限制。
   - 程序接口不能凭“官方 API 存在”算已查。2026-10-14 核对：USPTO TSDR/ODP 需要 API key，TSDR 是已知记录状态/文档接口，不能代替商标关键词搜索；PatentsView 官方文档要求 X-Api-Key，并公告暂缓新 key。只用已获授权配置，不能读取或索要聊天中的密钥。
   - 接口来源：[USPTO TSDR](https://www.uspto.gov/trademarks/apply/check-status-view-documents/trademark-bulk-data)、[ODP](https://data.uspto.gov/apis/bulk-data/search)、[PatentsView 官方文档](https://github.com/PatentsView/PatentSearch-API/blob/main/docs/docs/Search%20API/SearchAPIReference.md)。searches_complete 仅代表真实全部已查；search_plan_complete 只表示已查或已安排自查。
   - searches：每项按 04A 里的 `channels` 顺序试（专利先 Sorftime `patent_search_from_keyword` / `patent_search_from_image`，再浏览器开 Google Patents；商标用浏览器工具开 tmsearch 或网页搜索）。**不要用 curl 抓 USPTO / Google**：页面靠脚本渲染，抓回来不是数据。
     查到写 status: searched + date + result（写看到了什么，如「前 20 条无外形接近的 D 专利」「toothbrush holder 无在册商标」），用 `save_web_evidence.py` 登记。
     channels 都试过仍查不了（确需登录或付费库），才 `--status user_todo --reason ... --tried ...`，每个试过的渠道一条：用了什么、卡在哪；不得静默留 not_searched，也不能没试就整项丢给用户。
   - 可把供应商标题、拟用卖点、拟用品牌名追加到 extra_text 一起扫描。
3. <python> <skill>/scripts/compliance_ruler.py --run "<产品根>"
   → 04A_合规尺子.md（给人看）+ 04A_compliance_ruler.yaml（下游读）

4. 完成原始检索记录后运行 `<python> <skill>/scripts/sync_ip_report.py --run "<产品根>"`。
   它重算尺子、同步唯一的 `04_IP风险排查与专利分析.md`；原报告按内容哈希保存在同目录 `_history/`，来源与修订写入 `04_ip_report_sync.json`。不改黑名单和历史关键词，不将外观全空矩阵写成伪评分。
   知识卡只从本轮 S1/08 的响应中核验 `response_sha256`、卡 ID 和本模块用途；不相关/未验证引用退出正文并记入修订记录，不因历史报告引用过就自动沿用。
   研究交付可含灰色待核及可执行自查卡；`procurement_cleared`、`publication_cleared` 均不得由此脚本自动放行。
```

- 规则库 `references/compliance_ruler.yaml`：中国出口禁限（两用物项、濒危物种、管制刀具仿真枪、种子土壤活体、带电液体粉末磁性头程）、美国/亚马逊禁售与资质（枪械、电子烟、医疗、补充剂、EPA 抗菌宣称、MoCRA、食品接触、儿童 CPC/CPSIA、婴幼儿、纽扣电池、磁铁、锂电、FCC、UL、头盔、眼镜、汽车安全件、纺织标签、Prop 65、Lacey）、高侵权品类（授权 IP、乐高、网红外观、印花版权）、通用词陷阱 40 个、他牌名、必做检索 5 项。
- 灯：🔴 硬门槛（禁止或需特殊许可）/ 🟠 要资质（检测、证书、审批，费用进成本账 `one_off_cny`）/ 🟡 要规避。**只亮灯，不替用户判放弃**；🔴 必须在 S1 报告 M01/M05 讲清楚。
- 尺子命中的商标陷阱和他牌名必须全部写进 `04_compliance_blacklist.yaml`（陷阱词进 forbidden_terms 并给通用词替代；他牌名进 brand_terms，注明只可作 compatible with 适配说明）。
- 🟠 项需要的检测/证书写进 03A 询盘清单，向厂家要现成报告。

### D1 商标 / 通用名陷阱
以 04A 尺子第二节为起点（Q-tips→cotton swabs、Velcro→hook and loop 等），再对核心词、卖点短语、拟用品牌名做 USPTO 检索；检索状态照抄 04A 第三节，未检索的不得写「无风险」。

### D2 类目违规声明
| 类 | 示例 | 处理 |
|---|---|---|
| EPA/杀虫抗菌 | antibacterial, kills germs, mites, flea | 无认证→禁用 |
| 医疗/FDA | cure, treat, FDA approved | 无证明→禁用 |
| 绝对化 | #1, best, 100% guaranteed | 改写或删除 |
| 无证明材质 | BPA free, organic, food grade | 无证书→禁用或改客观描述 |

### D3 竞品品牌
来自 profile.`brand_risks` + 竞品标题。  
Listing / ST / 通投词：**禁用**；广告品牌拦截池：单独标注，不进 SEO。

### D4 图片叠字
促销词、#1、竞品名、夸大功效 → 表列位置+替代。

### D5 外观相似初筛
与 Top3 比 6–8 维（造型/比例/颜色/结构/纹理/整体印象）打分；>60/80 建议改设计并咨询律师。

必须使用逐对象矩阵，不得只给综合印象：

| 维度 | 本品证据 | Top1 | Top2 | Top3 | vs最相似竞品 0–10 | 风险/可改动作 |
|---|---|---|---|---|---:|---|
| 整体造型 | | | | | | |
| 比例与轮廓 | | | | | | |
| 核心结构/连接 | | | | | | |
| 端部/手柄/开合 | | | | | | |
| 表面纹理 | | | | | | |
| 颜色组合 | | | | | | |
| 装饰元素 | | | | | | |
| 整体视觉印象 | | | | | | |

总分 0–20 低、21–40 中、41–60 较高、61–80 高。41–60 至少提出 3 个可执行差异化改动；61–80 不以换色作为充分规避，建议重设结构并交专业人士复核。所有分数只是外观初筛，不等于专利结论。

### D6 分类号提示（可选）
给出可能的 USPTO 外观/实用分类供后续专业检索，**不声称已检索专利库**。

分类号表必须包含：分类号、分类描述、与本品关联、排查优先级、检索入口和检索状态。若本轮没有真实打开数据库并检索，状态固定写 `not_searched`，报告标题也不得使用“专利检索完成”等措辞；可以给用户后续人工/律师检索清单。

---

## 4. 风险评分

```text
综合 0-100 = 商标/声明/竞品品牌/叠字/外观 加权
0-20 低 | 21-40 中低 | 41-60 中高 | 61-80 高 | 81+ 极高
```

每项必须逐对象记录：被检查对象（词/标题/图片编号/结构）、原始内容、命中规则、证据来源、风险 0–100、P0/P1、替代表达或结构动作、处理后状态。综合分由商标、声明、竞品品牌、图片叠字、外观五项的明确权重算出；权重必须在报告披露，不得凭感觉给一个总分。外观项换算为 `score_over_80 / 80 × 100`。

---

## 5. 输出（强制）

所有产物统一写入 `{产品根}/S1_选品板块/amazon-ip-risk-assessment/`。

| 文件 | 内容 |
|---|---|
| `S1_选品板块/amazon-ip-risk-assessment/04A_合规尺子.md` + `04A_compliance_ruler.yaml` | 3.0 固定脚本产物（输入 `04A_ruler_inputs.yaml`） |
| `S1_选品板块/amazon-ip-risk-assessment/04_IP风险排查与专利分析.md` | 人工报告 |
| `S1_选品板块/amazon-ip-risk-assessment/04_compliance_blacklist.yaml` | 机器消费的唯一规范黑名单文件 |

### blacklist 最低结构

```yaml
schema_version: "3.0"
edition: salon-v2
product: ""
overall_score: 0
recommendation: "fix_required"  # fix_required | accept_risk | abort
forbidden_terms: []      # 绝对不可进 Listing/ST/图
sensitive_terms: []      # 需改写
brand_terms_ads_only: [] # 仅广告品牌池
image_text_risks: []
design_similarity:
  score_over_80: 0
  notes: ""
substitutions: []        # {bad, good, where}
data_sources: []
generated_by: amazon-ip-risk-assessment
author: 坚哥
```

页脚：

```text
—— amazon-ip-risk-assessment｜实战版｜坚哥｜跨境者说干货 · 星空的跨境圈子
```

更新 `workflow_state.yaml`：`ip_compliance` 完成；`blocked_reason` 设为等待用户决策；`artifacts.ip_report` 与 `artifacts.compliance_blacklist` 分别写入报告和 `04_compliance_blacklist.yaml` 路径。

---

## 6. 决策闸门（生产决策与演示安全续跑）

生产模式且决策会改变产品、采购或风险承担时，给出三选一并等待用户原文回复：

```text
IP风险决策：fix_required
IP风险决策：accept_risk
IP风险决策：abort
```

| 决策 | 总控/下游行为 |
|---|---|
| fix_required | 黑名单强制进词库/文案过滤，再继续 |
| accept_risk | 记录决策，仍携带已识别风险清单继续 |
| abort | 停止工作流 |

`demo_direct_run`、用户已明确“直接用/测试走流程”，或 state 已有 `interaction_policy.proceed_with_labeled_defaults: true` 时，不重复制造授权障碍：默认采用最安全的 `fix_required` 生成黑名单并继续安全词版下游报告，同时在 state 写 `decision_source: demo_safe_default`。这只允许继续研究、词库、文案和演示视觉，不允许把未整改内容上传、下单或对外发布。用户后续可用 `accept_risk/abort` 覆盖。

因此，闸门阻止的是风险词和风险声明进入下游，不是阻止整条流程输出安全版本。只有 `abort`、无法隔离的产品级高风险或用户明确要求等待专业意见时才硬停。

---

## 7. Handoff

```markdown
【本步完成】amazon-ip-risk-assessment
【产物】S1_选品板块/amazon-ip-risk-assessment/04_IP风险排查与专利分析.md + S1_选品板块/amazon-ip-risk-assessment/04_compliance_blacklist.yaml
【综合分】xx/100 · 建议：fix_required / …

【决策】生产模式需三选一；demo_direct_run 可记录 fix_required 安全默认并继续

【下一步 Skill】starsky-fullchain-workflow（先生成 S1 产品综合可行性报告；不得直接进入关键词）
【推荐口令】IP风险决策：fix_required。立即生成阶段_S1_选品可行性研究报告.md，并用 github-tabler-html-report 生成同名 HTML；报告通过后再进入竞品候选确认池。
【MCP】not_required（本步如实际调用检索工具则写 used: <工具名>）
```

下游：sif/sellersprite keyword、listing-copywriter、image-designer、ad（品牌词仅广告池）。

IP Skill 的直接 handoff 永远是总控/S1；关键词、Listing、视觉和广告是 S1 通过后的间接消费者。

---

## 8. 自检

- [ ] 先跑了合规尺子：23 个属性均按是/否/待核记录；待核单列，不虚构 true；5 项检索均为 searched 或带原因及自查卡的 user_todo。
- [ ] 尺子命中的商标陷阱/他牌名全部进了 blacklist  
- [ ] 读了 brand_risks / 竞品  
- [ ] P0 项都有替代表达  
- [ ] blacklist 文件落盘  
- [ ] 生产模式给出三选一；演示直跑采用 fix_required 安全默认且未让风险词进入下游  
- [ ] state 更新  

## 9. 版本

## 10. 7·18 IP/合规深度恢复契约

运行前读取 `../../templates/report_depth_and_demo_policy.md`。本 Skill 不是输出几条“注意风险”，而是建立可被关键词、文案、视觉和广告直接执行的风险路由。

### 逐对象排查

对产品名、核心关键词、竞品品牌、供应商标题/详情、包装文字、图片叠字、外观结构逐项列出：原词/原图、风险维度、证据来源、风险等级、允许用途、禁止用途、替代方案和复核动作。

### 外观相似度矩阵

至少从轮廓、结构布局、装饰元素、颜色组合、接口/功能结构、包装呈现六项评分；明确这是初筛，不冒充法律意见。高相似项必须给设计规避动作和需要进一步检索的分类号/关键词。

### 风险量化

```text
risk_score = trademark × weight + claim × weight + brand × weight + image_text × weight + design_similarity × weight
```

报告必须解释权重、P0/P1/P2 阈值、触发 `fix_required` 的条件。没有官方检索结果时标注“未完成官方检索”，不能写“无风险”。

### 合规黑名单必须可执行

`04_compliance_blacklist.yaml` 至少分：`blocked_terms`、`brand_terms`、`restricted_claims`、`image_text_blocklist`、`design_watchpoints`、`safe_replacements`、`allowed_context`。每项包含原因、来源、适用渠道（Listing/ST/PPC/图片/A+）和替代词。

### 演示与生产边界

内部演示不询问素材授权，标注来源和“上线前确认使用权”即可继续。生产模式中的产品级风险决策是 hard block；`demo_direct_run` 可默认 `fix_required` 并把黑名单传给词库、文案和视觉，不重复询问，也不允许风险内容进入上传版。

### acceptance_checks

- [ ] 商标、声明、品牌、图片叠字、外观和分类号六维均有结论。
- [ ] P0/P1/P2 有证据、替代方案和后续动作。
- [ ] 外观矩阵不是一句“相似/不相似”。
- [ ] 未检索不能写“已清零”或“无风险”。
- [ ] blacklist 能被下游按渠道执行，且安全替代词完整。
- [ ] IP 决策写入 state，后续不重复询问。
- [ ] 每个风险对象都有原文/图片/结构证据、命中规则、替代动作和处理状态。
- [ ] 综合分披露五项权重与计算；外观 8 维总分和阈值可复算。
- [ ] 未真实检索 USPTO/专利数据库时明确标 `not_searched`。
- [ ] demo_direct_run 的安全续跑没有放宽上传、采购和对外发布边界。
- [ ] decision_record 含 evidence_ids、required_changes、failure_thresholds、decision_source 与 allowed_next_steps。
- [ ] handoff 指向总控生成 S1 综合可行性报告，而不是直接指向关键词 Skill。

### 0711 decision_record

读取 `../../templates/evidence_decision_revision_contract.md`。IP 决策写入统一 decision_record：

```yaml
decision: fix_required | accept_risk | abort
evidence_ids: []
required_changes: []
failure_thresholds: []
decision_source: user | demo_safe_default
allowed_next_steps:
  - phase_s1_comprehensive_feasibility
```

`allowed_next_steps` 在 S1 完成前不能包含 keyword/listing/visual/ad。

| 版本 | 说明 |
|---|---|
| 3.4.0 | 0711 融合：统一 decision_record，修正直接 handoff 为 IP→S1 综合可行性 MD+Tabler HTML→竞品确认，不再直接指向关键词 |
| 3.3.0 | 三方融合：恢复外观 8 维逐对象矩阵、可复算风险权重、USPTO not_searched 边界，并将 demo_direct_run 改为 fix_required 安全续跑而非重复授权阻塞 |
| 3.0.0 | 实战版：闸门语义、黑名单契约、总控衔接、去掉 v1 叠叙与错误下游顺序 |
