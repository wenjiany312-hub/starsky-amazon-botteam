---
name: amazon-market-research
description: >
  亚马逊美国站选品/市场穿透调研（实战版）。当用户或总控需要：选品评估、类目能不能做、
  竞品穿透、市场可行性、生成 product_profile、进入难度与财务测算时使用。
  触发表述：「选品调研」「市场分析」「竞品穿透」「这个类目能不能做」「深挖第1名」
  「输出市场报告和产品画像」「amazon-market-research」。
  MCP 优先级：生产首选 Sorftime（市场）+ SIF（词库/广告信号）；西柚、卖家精灵为可加装，有则交叉验证。
  产出 01_市场分析报告.md/.html（脚本出数 + market_narrative.yaml 判断）+ 01_product_profile.yaml。外部 MCP 须用户自己配置，不随插件安装。
  可被 starsky-fullchain-workflow 调度。生态：OpenAI Codex。
version: "3.8.0"
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

存回包统一用 `<python> scripts/save_mcp_response.py --root "<产品根>" --tool sorftime__<工具名> --args '<调用参数JSON>' --response-file <回包文件或->`（R06.1）：它按下面的约定落到正确目录、带上参数，重复保存不重复计数；宿主没有某工具时用 `--server sorftime --inventory-file <工具清单JSON>` 存工具清单作证据。不要自拟聚合文件名，也不要只把回包留在 Node/REPL 暂存里——验收看不到。

市场事实输入约定（`scripts/s1_market_facts.py --help` 同步维护）：回包目录为 `<产品根>/S1_选品板块/amazon-market-research/运行数据/`；文件名以 `__<工具名>.json` 结尾；标准输入只存解开信封后的 `data`；`competitors` 依赖 `01_product_profile.yaml` 的 `competitor_asins.direct`。完整工具信封是兼容读取格式，不以文件数或数组顺序推定自然位次；缺字段要保存实际回包，工具不存在要保存当前宿主工具清单。不可核验不计达标，不替代可补采的数据。

data 数组对应的查询参数保存在同目录 `tool_calls.index.json`，格式为 `{"calls":[{"source":"文件名__工具名.json","tool":"工具名","args":{"keyword":"词根","search_time":"2026-09","page":1}}]}`。历史覆盖须有同查询、月份及分页关联，自然位次须有关键词归属；缺参数时保留数据缺口，不猜文件名前缀，也不合并成 Top100。完整信封则直接读取其 `tool/args`。

执行S1前先按 `scripts/s1_roadmap.py` 解析真实产品根：新案 `r01.m2` 必须读 `templates/s1_m2_execution.md`，按新九步的需求门、定位确认、全图CT和页面数据先算规则执行；旧案 `legacy.m1` 保留原步骤、编号和门禁，不自动迁移。旧正文的第6步回填等待只属于legacy.m1，不能覆盖新版5→6异步厂家确认。所有用户决定绑定真实user_reply，不把本文或脚本默认值写成用户授权。M1紧凑落盘、原生字节与immutable decision/proof继续生效。

R05 新案 `s1_execution.category_comparison_contract: r05.adjacent.v1` 的第3步必须按上述模板先确定真实目标/直接父节点，写入 `02_demand_validation.yaml.category_comparison_plan`（实际amz_site、target_node_id、parent_node_id、同月query_start/query_date），再运行 `scripts/s1_research_gates.py --root "产品根" --continue-demand "S1_选品板块/amazon-market-research/02_demand_validation.yaml"`。原需求已通过也必须执行：入口读取完整真实 `category_tree`，选择3个不同同父相邻子类（每项保留理由，排除目标自身），实际取得3份同站同月 `category_report`，落盘 `comparison_scope.yaml` 与完整源回包/参数/请求和响应标识/SHA；`s1_market_facts.py` 和 C3 按来源复核数值及身份，不接受自填数量。默认选真实siblings；人工指定 selected 也必须通过树证据验证。仅完整真实树证明无相邻子类可标有证据的NA，工具错误、空或不完整发现、1–2份报表均不行；「接受低需求」不能解除该门。旧案没有新契约时保留原流程，不自动补采或改历史。

## R09 市场分析报告：脚本出数 + AI 写判断（2026-10-23）

`01_市场分析报告.md` 和同名 `.html` 由插件生成，**不要手写、不要直接改这两个文件**，也不要另起 `03_…需求验证报告.md`、`04_…选款与对标.md` 之类的报告来装分析——第 3、4 步的判断全部写进同目录的 `market_narrative.yaml`，重新生成后就进报告。

- **脚本出数**（`scripts/s1_market_overview.py`，只读已保存回包）：类目大盘（目标节点 `category_report`：Top100 月销量/销售额、Top3 商品/品牌/卖家占比、亚马逊自营、品牌/卖家 Top10、星级与评论数分层、新品、均价）、24 个月 `category_trend`（同比只用完整月份）、相邻类目、关键词搜索量与同比、同款型池（价格带 × 销量矩阵、分位、头部占比、上架时间分层、低评论有量的新链接）、结构性难度、评论样本数、流量词。
- **AI 写判断**（`market_narrative.yaml`）：执行摘要、每节「读法」、`form_review` 形态判定、定价建议、属性注册表、Top3–5 可跟进/应避开、关键词机会、画像、痛点 P-xxx（原话必须与留存评论一致，报告会自动核对）、价值点、事实/推断/待验证、难度双法与竞品信号判读、SWOT、破局表、结论与最强反方。
- **必填三项**：`summary.verdict`（做 / 有条件做 / 暂缓 / 不做）、`summary.breakthrough`（一句话破局）、`counterargument.argument`（最强反方）。其余没写的段落显示「待补写」，不拦流程。
- **三层口径**：类目大盘、同款型池（主分析口径，10–20 个同用途同形态商品）、严格同款（只用于需求门 6.2 节）。严格同款不足不影响用同款型池做竞争分析，但结论要保留这条缺口。

固定命令（`<python>` 为本插件运行环境，`<产品根>` 为产品文件夹）：

1. 第 3 步必须取到目标节点的 `category_report` 与 `category_trend(SalesCount)`：走 `s1_research_gates.py --continue-demand` 时自动补拉（存为 `运行数据/target_category_*.json`）；否则直接调用 Sorftime 后用 `save_mcp_response.py` 存档。
2. `<python> scripts/s1_market_report.py template "<产品根>"` 打印填写骨架：已列出与查询词最相关的未判定候选商品，逐个把 `tier` 改成 `same_type` / `reference` / `excluded` 并写理由；目标 10–20 个 `same_type`。已有 `market_narrative.yaml` 时只改对应字段，不要整份覆盖。
3. 紧凑案例写入：`r01_native.py execute --root "<产品根>" --step <3或4>`，请求里的 `inputs` 放 `{"S1_选品板块/amazon-market-research/market_narrative.yaml": {"yaml": {...}}}`；执行后报告自动重新生成。非紧凑案例直接写文件后运行 `<python> scripts/s1_market_report.py render "<产品根>"`。
4. `<python> scripts/s1_market_report.py check "<产品根>"`：列出必填缺失、待补写的读法、没核对上的原话和数据缺口；`ok:false` 时先补必填再发步骤卡。

`02_需求证据一页纸.md` 已取消：续拉的每次成功与原始报错只存 `运行数据/需求续拉记录.json`，需求门结果以人话出现在报告 6.2 节。

## 报告视觉统一（2026-09-13）

HTML 使用 `../../templates/report_visual_system.md` 的 S1 产品决策布局。阅读分组不改变 M00–M18 固定模块顺序；产品首屏、门槛判断与来源证据继续校验。


> ⚠️ Sorftime MCP 实测参数口径（2026-08-29 冒烟验证）：Amazon 类工具用 `amz_site`；**关键词类工具（keyword_detail / keyword_extends / keyword_list 等）站点参数是 `keyword_support_site`**，传 `amz_site` 会报错。评论工具 `product_reviews` 返回字段为 `star_rating`/`content`；1688 供应商候选优先用本机 `1688_scraper.search_offers`（Sorftime `ali1688_product_search` 噪声大时不要死磕）。

# 亚马逊选品 · 市场穿透 · 实战版

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
| 在工作流中的位置 | 总控 `starsky-fullchain-workflow` 的 **market_research** 步 |

> 打开本文件即可确认作者与来源。

---

## 0. 角色与边界

你是**选品市场分析专员**：用实时数据回答「这个方向能不能做、怎么定价、壁垒在哪、下游要什么画像」。

| 做 | 不做 |
|---|---|
| MCP 拉竞品/类目/销量结构 | 不编造 BSR、月销、评论数 |
| 难度分、财务、画像、破局点 | 不跳过数据空缺假装精确 |
| 落盘报告 + **独立** product_profile.yaml | 不替代 IP / 词库 / 文案 Skill |
| 收尾给出下一步 Skill 与口令 | S 场景不直接跳 IP/关键词；市场后先供应链短名单 |

---

## 0.5 ⛔ S1 固定模块契约要用的三项新数据（v2026-08-10 新增 · 必取）

**先读 `../../templates/s1_report_module_contract.yaml`。**

S1 综合报告从本版起是**固定模块结构**（M00–M18，顺序不许调），由
`starsky-fullchain-workflow/scripts/verify_s1_acceptance.py` 的 **C8** 机检。
其中三个模块的数据**必须在本步取到**，否则 S1 报告写不出来、C8 直接判不合格：

### ① 价格分位 P25 / P50 / P75 / P90（M09）

⛔ **只给中位数没有参考性。** 中位数相同的两个类目，价格结构可以完全不同 ——
一个是 $9 附近挤成一团，另一个是 $5 和 $30 两头分布。做的是哪一档，决定
FBA 占比、广告能不能打平、要不要走高客单。

取 Top100 售价样本，输出四个分位 + 每档一句大白话解释，
并**声明价格口径**：可见售价还是结账价、含不含 coupon、样本量多少。

### ② 双重增长基准（M04）——「跑赢上级类目 + 跑赢站点大盘」

⛔ **细分自己涨不算数。** 必须同时回答两问：

| 要证明 | 缺了会怎样 |
|---|---|
| 跑赢**直接上级类目** | 分不清是这个细分在涨，还是整个类目在涨、它只是被带着走 |
| 跑赢 **Amazon 站点大盘** | 分不清是这个类目在涨，还是平台整体在涨 |

实测反例：核心词搜索量同比 **+120.2%**，但上级类目销量同比只有 **+7.6%** ——
增长集中在某个表达和头部体系上，新品不会自动受益。

输出四栏表：`比较对象 | 可用时间窗 | 当前证据 | 跑赢结论`。
**任一基准取不到就如实写「数据缺失／无法比较」** ——
C8 会检查：只要有基准缺失，顶部判定就不许是「建议投入」。
⛔ 不许用不同口径的数硬凑成"可比"。

### ③ 替代市场候选（M15，仅当判定为观望/不做时）

判定负面时必须给横向（同人群其他品类）与纵向（同需求链上下游）替代候选，
但**一律标「只做候选，不作投资推荐」「待独立完整调研」** ——
替代候选只跑了名称检索和类目概览，没做资格清洗、三年增长、真实退货、
成本与合规，拿它直接换市场是另一个坑。

⛔ **工具节点映射错误的候选必须显式排除**，并写明排除理由。
实测踩过：查「电压转换器」被工具映射到玩具类目，若不排除就会用错误节点的
容量制造出一个根本不存在的机会。

---

## 1. 触发

- 独立：`帮我做 XX 选品调研` / `分析竞品 ASIN …` / `这个类目能不能做`  
- 总控：`继续，深挖推荐第 1 名，调用 amazon-market-research…`  
- 沙龙演示：在已有 `00_选品推荐报告.md` 后深挖某一方向  

---

## 2. 数据源（实战版 · MCP 主路径）

| 优先级 | 来源 | 用途 |
|---:|---|---|
| 1 | **Sorftime MCP（生产首选，用户自配）** | 类目体量、竞品、价格带、集中度、机会信号 |
| 2 | **SIF MCP（生产首选，交叉验证）** | niche、竞争、需求侧信号；不替代 Sorftime 类目容量 |
| 3 | **西柚 / 卖家精灵 MCP（可加装）** | 有则交叉验证；没有不挡首选，也**不得**代替「可以不配 Sorftime」 |
| 4 | 用户已给的 ASIN 列表 / 1688 成本 | 补规格与成本，**不替代**市场 MCP |
| 5 | 仅当首选 MCP 连续失败 | 生产停并指引配置；演示才允许标注「数据降级」+ `source_fallback` |

**禁止：** 未调用 MCP 却写「根据 Sorftime/市场数据」。  
**标注：** 每个关键数字旁可注数据源（如 `来源: Sorftime`）。

### MCP 失败处理

探测：工具清单中存在名称含 sorftime / sif / xydc|xiyou / sellersprite / 1688 的工具即视为对应 MCP 已出现（本机注册名参考：sorftime、sif-mcp / sif_mcp、xydc-mcp、sellersprite-01、1688_scraper）；必须先调一次轻量工具验通道。生产缺 Sorftime 不得改口称已用市场数据。

1. 首选 Sorftime 重试 1 次；SIF 有则交叉验证。  
2. 西柚 / 卖家精灵有则一并调用，**不得**用它们顶替缺失的 Sorftime。  
3. 首选仍失败：生产输出阻塞说明 + 配置指引，**不编完整假表**。  
4. 若用户坚持用局部已知 ASIN 做「结构推演」，所有推断标 `AI推断/待 MCP 验证`，并标 `source_fallback`。

---

## 3. 策略叠层（实战版）

若工作区或 `workflow_state.yaml` 中存在：

- `category_profile.yaml` / `category_overlay` / `playbooks/*.yaml` / `adapters/**/playbook_overlay.yaml`  
- `skill_patches/market_research.md`  
- `enterprise_profile.yaml` / `enterprise_overlay` / `routing.yaml`（企业模式）  
- `00_总控/starsky-fullchain-workflow/enterprise_data_manifest.yaml` + `00_总控/starsky-fullchain-workflow/runtime_data_overlay.yaml` 的 S1 条目（若当前 run 已加载资料包）

资料包只读并按 overlay 点名文件读取，不重新扫描根目录。企业的利润门槛、供应商偏好和类目经验可加严/细化建议，但不得覆盖 MCP 事实、当前商品 `03_product_facts.yaml`、Amazon 合规和真实财务。报告头部写资料包 source_id、时间窗和使用边界；无资料包走通用规则。

则**先读再执行**，可调整：

- 选品加权（季节、认证、迭代速度等）  
- 额外关注的竞争维度  
- 合规预警线索（仍以 IP Skill 为准）  

无叠层 → 大众基础版。

---

## 4. 输入

| 输入 | 必须? | 说明 |
|---|---|---|
| 产品方向 / 类目词 / 中文品名 | 是（或有 ASIN） | 来自总控候选第 N 名或用户指定 |
| 市场 | 默认 US | 可覆盖 |
| 竞品 ASIN（1–20） | 强烈建议 | MCP 可补全 |
| 采购成本 / 1688 | 建议 | 财务才真实 |
| 用户硬条件 | 建议 | 新手/非季节/价格带等，用于结论对齐 |
| 叠层路径 | 可选 | 类目打法 |
| `runtime_data_overlay.yaml` 的 S1 条目 | 可选 | 历史产品案例、预算、供应链/SOP；只作验证重点和敏感性参考 |

---

## 5. 输出（强制落盘）

工作目录：用户指定 或 总控 `product_runs/...` 或当前项目。

报告统一写入 `{产品根}/S1_选品板块/amazon-market-research/`；主报告固定为 `01_市场分析报告.md` 与同名 `.html`（插件生成，判断写在 `market_narrative.yaml`），`01_product_profile.yaml` 同目录。旧案已有的 `01_市场穿透报告.md` 会同步刷新为同一内容。

| 文件 | 说明 |
|---|---|
| `01_市场分析报告.md` / `.html` | 给人读的完整市场分析报告（插件生成；HTML 带趋势与价格分布图） |
| `market_narrative.yaml` | AI 写的判断与读法，见本文件「R09 市场分析报告」 |
| `01_product_profile.yaml` | **独立文件**（不要只塞在 md 末尾；md 末可再附一份摘要 YAML） |
| `05_cost_inputs.yaml` → `05_成本账与广告可推性.md` / `05_cost_model.yaml` / `05_利润测算表.xlsx` | 6.4 固定脚本产物；供应商回填后重跑即修订 |

报告页脚：

```text
—— 由 Codex Skill「amazon-market-research」生成｜实战版｜作者坚哥｜跨境者说干货 · 星空的跨境圈子
```

若存在 `workflow_state.yaml`，更新：

```yaml
completed_steps:  # 追加 market_research
current_step: "supplier"   # 或 ip_compliance（若跳过供应链）
artifacts:
  market_report: "S1_选品板块/amazon-market-research/01_市场分析报告.md"
  product_profile: "S1_选品板块/amazon-market-research/01_product_profile.yaml"
anchors:
  entry_difficulty: "<0-1 或 文案分档>"
  recommended_price: "<$xx–$yy>"
  breakeven_acos: "<xx%>"
next_step:
  step_id: "supplier"
  skill: "alibaba-supplier-evaluation"
  instruction: "基于市场报告和 product_profile，用中文产品关键词搜索 1688，输出至少 5 家供应商短名单"
```

独立使用时如果用户明确说“先不看供应链”，可直接给 IP 口令；但沙龙 S 场景默认必须先 supplier。

---

## 6. 调研 SOP

### 6.1 模式

| 模式 | 识别 | 要点 |
|---|---|---|
| **新品/方向穿透** | 类目词、条件选品第 N 名、1688 | MCP 扫类目+竞品；成本来自 1688/用户 |
| **老品/竞品诊断** | 自有或竞品 ASIN | MCP 拉该 ASIN 与对标；可跳过 1688 |

### 6.2 步骤一 · 锁定分析对象

- 写清：产品方向英文核心表达、对标 ASIN 列表、是否套装/变体思维  
- 用户条件对齐：价格带、认证、季节、新手约束 → 结论必须回应这些条件  

### 6.3 步骤二 · 竞争格局（MCP）

**必须输出：**

1. **竞品数据表**（至少 Top 8–15，能拉多少写多少，不足则说明）  
   列：ASIN、品牌、售价、评分、评论数、BSR/类目、月销或销额（若有）、上架时间、备注  
2. **价格带 × 销量矩阵**  
   分档统计：ASIN 数、均价、销量/销额占比 → **建议定价区间 + 理由**  
3. **头部结构**  
   Top3 销量/销额占比、Amazon 自营占比、低评商品是否仍有量（新手友好信号）  
4. **Top 3 竞品反向工程（不得只写一句结论）**  
   每个竞品独立输出：ASIN/品牌/价格/评分评论/上架时间、标题结构、主卖点、变体策略、自然/广告/品牌流量结构、核心流量词、标题覆盖词、未覆盖机会词、广告位/PAT 迹象、可跟进点和应避开点。真实字段与推断必须分栏；没有流量字段时写“未取到”，不得编占比。  
5. **关键词覆盖重叠率与空白机会**  
   使用已取到的标题、SIF/卖家精灵/西柚洞察词或 Sorftime 关键词信号，输出核心词 × Top3 覆盖矩阵。Top3 全未覆盖且有强需求信号为三星；2 家未覆盖且有中等需求为二星；仅 1 家未覆盖或需求弱为一星。无关键词数据时列待验证假设并标 `AI推断/待关键词验证`，不得省略整个模块。  
6. **结构洞见**  
   点击集中、转化集中、品牌词占比、广告位出现等只有数据才给数字；无数据给验证动作，不给伪精确值。  
7. **竞品信号判读（调 `amazon-gray-tactic-detector`，v3.8.0 起）**  
   对 Top 3 反向工程对象与后续要进难度分母的参照 ASIN，逐个用公开信号判读：疑似刷 / 合并变体 / 烧广告（广告词占比 >80%）/ 品牌流量（品牌词几百以上搜索量）/ 侵权平替。每个 ASIN 标「可作参照 / 仅证明市场存在 / 不可参照」；`不可参照` 的在反向工程里写「不可复制」，并从 6.6.1 的难度分母剔除。产物落 `S1_选品板块/amazon-gray-tactic-detector/01B_竞品信号判读.md` + `competitor_signal_flags.yaml`。查不到回评节奏 / 品牌词量时写 `unknown`，不写「非刷」。只判读，不给任何操作。

### 6.4 步骤三 · 财务测算（固定脚本 `scripts/s1_cost_model.py`，不手算）

成本账、毛利率档位、允许到岸成本、广告可推性全部由脚本算，同一输入同一结果。禁止现场手写公式或另写计算代码。用安装记录里的 Python，产品根带引号：

```text
0. <python> <skill>/scripts/s1_cost_model.py --run "<产品根>" --profile-status
   → needs_ask=true 且本产品 workflow_state 没有 logistics_rates_decision 时：把返回的 card 原样发给用户，【等你】回复。
     - 用户报了数字（如「头程 12 元/kg，包税，报关 180 一票」）→ 拆成 键=值 执行
       <python> <skill>/scripts/s1_cost_model.py --run "<产品根>" --set-profile first_leg_rate_cny=12 duty_included=是 customs_fee_cny_per_shipment=180
       可用键：first_leg_rate_cny、first_leg_basis(kg|cbm)、duty_included(是|否)、duty_rate(如 25%)、
       customs_fee_cny_per_shipment、other_cny_per_shipment、target_net_margin(如 15%)、
       labeling_cny_per_unit、inbound_placement_fee、packaging_cny_per_unit（包装随产品变，默认加 --scope product）；用户说只管本品才加 --scope product。
     - 卡上所有项一次问齐（含包装、贴标、入库配置费）；用户没给的项不再追问，按模拟值算、报告标「模拟」，继续往下。
     - 用户回「用默认」→ 不写配置，按模拟值继续。
     两种都在 workflow_state 写 logistics_rates_decision: user_rates | use_defaults，本产品不再重复问。
   → needs_ask=false：不问，直接往下；步骤卡里一句带过「费率用的是你的物流配置」。
0. <python> <plugin>/scripts/refresh_fx_snapshot.py --run "<产品根>"
   → 保证 <产品根>/fx_rate_snapshot.yaml 存在且在 24 小时内。**必须在 --init 之前**：下一步只在生成输入表时读一次快照，
     先 --init 后补快照会让输入表固化成默认汇率 7.2。取不到网络时如实告诉用户，由用户给 --rate；不得写死汇率或自行编造。
1. <python> <skill>/scripts/s1_cost_model.py --run "<产品根>" --init
   → 生成 S1_选品板块/amazon-market-research/05_cost_inputs.yaml（售价档取自 product_profile，汇率取自 fx_rate_snapshot，
     物流/亚马逊费率取自用户物流配置，缺的是出厂默认值并标 default_assumption）
2. 助手只往 05_cost_inputs.yaml 里填数，每个值写 {value, status, source}：
   - factory.*：取 03_product_facts / 1688 抓取 / 厂家回复；status 用 confirmed | user_attested | page_extracted | demo | ai_inferred
   - ads.keywords：核心词 CPC，优先 SIF 建议竞价 / 西柚 CPC，其次卖家精灵、Sorftime；source 写 sif | xiyou | sellersprite | sorftime
   - ads.cvr_scenarios：有 SIF/西柚本类目真实转化率就替换默认情景
   - amazon.fba_fee：用户给出后台收入计算器数值就填，否则留空由脚本按近似表估
   - market_price_bands：抄本报告 M09 的 P25/P50/P75/P90，脚本据此标每个售价的价格带位置
   - condition_scenarios（R06.1）：用户指定几套采购条件对比时（如「24.99 美元，A 采购 16 元 + 4cm 包装，B 采购 19 元 + 3cm 包装」），
     逐套写 `{id, label, price, inputs: {采购单价: 16, 单包高: 4}, source: 用户原话}`；inputs 用成本输入的中文名或引擎键名。
     重跑后它们就是主口径（代表值取利润最低的一套），第 6 步成本账、第 7 步推广、第 9 步官方阶段报告都会按它呈现。
   - base_pain_points + offers：组合与定价方案，由 `amazon-variant-packaging-planner` 的「组合与定价」一节给出
     （单品之外 2–3 个：轻组合加配件 / 多件装 / 礼盒或定制包装；每个写内容、解决的痛点、配件与包装 ¥/套、组合包装尺寸重量、候选售价）
3. <python> <skill>/scripts/s1_cost_model.py --run "<产品根>"
   → 05_成本账与广告可推性.md（给人看）+ 05_cost_model.yaml（下游读）+ 05_利润测算表.xlsx（带公式，用户可自己改数）
```

- **口径自动判定**：出厂价、单品尺寸重量、头程单价都是 confirmed / user_attested / 用户物流配置时，`cost_basis=real`，可以写「成本账 / 毛利」；否则 `conditional`，只作方向参考，结论措辞按硬规则 5。
- **组合与定价一起比**：单品各售价档和每个组合方案放在同一张尺子表，另出「一B 组合与定价方案」：内容、解决的痛点、比单品多花的到岸成本、价格带位置、毛利率、组合增值倍数（多卖的钱 ÷ 多花的成本，≥3 倍说明加价有空间）。某个组合定为主推时在该方案写 `recommended_price`。
- **尺子，不是判决**：脚本只给指标和档位——广告前毛利率档位（≥40% 很好 / 30–40% 优秀 / 20–30% 一般 / <20% 偏低）、到岸余量、保本 CPC 与核心词 CPC 对照、广告压力（低/中/偏高/高）。不得据此替用户写「放弃」；毛利率高、广告压力偏高的品可以写「可小预算测试」，做不做由用户拍板。目标毛利（默认广告前 40%）同样只是参考：定高一点前期压力小、成功把握大，但不代表更低毛利就不能做，报告里不得把它写成门槛。
- **用户费率只问一次**：头程单价、9810 报关费、包税与否、英国等站点的额外杂费，以及包装、贴标、入库配置费，由第 0 步确认卡在对话里一次问齐，脚本 `--set-profile` 写进 `~/.starsky/物流配置.yaml`（所有产品通用）或 `<产品根>/00_产品资料/物流配置.yaml`（只管本品）。不要让用户自己去改 yaml。之后再填的费率，重跑时脚本会自动替换掉待填表里的模拟默认值；仍是模拟值时报告开头有「⚠️ 模拟费率」提示。
- **给厂家的问题**：报告「还缺什么 · 问厂家」一节可直接复制，并入 03A 询盘清单。
- M12、`anchors.breakeven_acos`、下游广告 bid 一律取 `05_cost_model.yaml`，不重抄不重算。

### 6.5 步骤四 · 用户画像与 VOC

- **4–6 个画像**：画像描述、购买动机、典型搜索表达、价格敏感、购买阻断问题、D1–D6 关联标签。画像占比只有样本支持时才写，否则用优先级，不伪造百分比。  
- **痛点表**：样本来源/样本数、主题、命中频次、严重度、用户原话、结构原因、供应链应对、可否用于前台声明。无评论原文时标“类目假设”，不得写成 VOC 事实。  
- **评论主题按品类定**：脚本默认用通用主题（坏了、尺寸不符、材质薄、安装难、与图不符、缺件）。按本品写 `S1_选品板块/amazon-market-research/review_themes.yaml`（`pains` / `motives`，每条 `{name, pattern}`，pattern 是英文正则），重跑市场事实即按本案主题计数；不要沿用别的品类的主题。CT 初始化会把这些痛点和原话自动带进 `pain_points.voc_refs`。  
- **好评价值点**：样本来源、频次、原话、价值机制、可进入五点/主图/A+ 的位置和证据边界。  
- 痛点与价值点映射到 D2 卖点、D3 场景、D4 人群、D5 对象，供关键词与视觉 Skill 直接消费。  
- **站外讨论线索（Reddit / Quora，Sorftime 接了就调，没接或无返回就跳过，不是门槛）**：看用户在真实使用场景里遇到什么麻烦、试过什么办法、为什么不满意，用来找改良方向、核对卖点是否打中需求。
  - 搜索 ≤4 次：`reddit_post_search` / `quora_question_search` 的 `search_name` 用英文，分别试品类词、使用场景、具体问题（如 `torn window screen`、`screen door hole cat`）；需要时 `post_type="1,3"`（求助、抱怨）。
  - 搜索结果相关性常偏宽，逐条人工判相关，不相关的直接丢掉，不进报告。
  - 读原文 ≤4 帖：Reddit 搜索结果里原帖 URL 多为空，用 `t3_` 帖子 ID 调 `reddit_post_request` + `reddit_comment_request`；Quora 用纯数字 `quora_id` 调 `quora_answer_request`。结论以原文和评论为准，不以接口摘要和情绪分类为准。
  - 发帖/评论日期以原文为准；出现评论早于发帖等异常，标“日期待核”。回包照常保存 `tool / args / queried_at / sha256`。
  - 写进 `market_narrative.yaml` 的 `offsite.threads`（报告第 7 节“需求与痛点”下的 7.5；R09 前的老报告写在 5.5）：平台、帖子 ID、日期、场景、遇到的麻烦、试过的办法、为什么不满意、对本品的启示、相关性（高/中）。每条都是**单帖线索，不代表问题有多普遍**；要进痛点表的频次，须与亚马逊评论交叉核对。
  - 调了但没相关结果：`offsite.none` 写“Reddit 无返回 / Quora 无相关结果（用过的搜索词）”，不编造帖子；Sorftime 没接就不写，报告不出这一节。

### 6.6 步骤五 · 进入难度 + SWOT + 破局

**进入难度（须展示计算过程）：**

```text
难度 = 头部集中度×0.4 + 评论壁垒×0.35 + 价格战×0.25
分档：0–0.3 易 / 0.3–0.5 中 / 0.5–0.7 难 / 0.7+ 不建议
```

```text
头部集中度 = Top3销量或销额占比（0–1；替代信号必须标推断）
评论壁垒 = min(1, Top10评论中位数 / 1000)
价格战 = Top20中低于本品可持续价格下限的竞品占比
```

必须展示样本范围、各分项原始值、归一化值、权重、加权结果和敏感性。某项缺失时不得自动按低风险计分，应给保守/中性两档并说明结论是否改变。

#### 6.6.1 新品进场难度双法（调 `amazon-competition-difficulty-assessor`，v3.8.0 起）

上面的结构性难度分回答「这个市场好不好进」；双法回答另一个问题：**新品（≈30 评论状态）在核心词下能分到多少单**。两者并列呈现，不合并成一个数。

```text
评论区间法：难度 = A / B
  A = 核心词下相似产品（三标准：外观接近 / 评论量级接近 / 价格接近；头部异形态不算分母）中 30 评论以内的平均月销量
  B = 该词自然排名前 5 的平均月销量
  <10% 困难 ｜ 10–15% 适中 ｜ >15% 简单
广告效率法：效率 = 月销量 ÷ 广告词数（观测带 6–17，越高越友好）；广告词占比 >80% = 烧广告硬顶
交叉：两法任一通过即可；保守销量参照取两法较小值
```

- 阈值只读 `../../templates/s1_thresholds.yaml`，正文不复制数字；输出附「按类目校准」。
- 数据复用本步竞品表与 `competitor_asin_pool.yaml`；缺每 ASIN 广告词数时由 assessor 按三级取数（SIF `ads_get_asin_ad_structure` / 卖家精灵流量词 → 用户导出 → 列字段）补，不编词数。
- 6.3 第 7 项标「不可参照」的 ASIN 剔出 A 与 B 的样本，并在表中注明。
- 数据齐后必须跑 `amazon-competition-difficulty-assessor/scripts/difficulty_calc.py`，报告数字与 `difficulty_assessment.yaml` 一致（容差 0.5%）；不手算。
- 产物落 `S1_选品板块/amazon-competition-difficulty-assessor/01A_运营难度双法评估.md` + `difficulty_assessment.yaml` + `01A_data_source_log.yaml`。
- 回填 S1 综合报告：M05 硬门槛写「难度 <10% 且广告效率不过 → 明确不做；阈值 + 实测值 + 达标否」；M10 竞品穿透加子节，标题固定「新品进场难度双法」（⛔ 不得含「市场容量 / 集中度 / 季节性」等其它模块关键字）；M17 证据编号 `E-DIFF-001…`、`E-GRAY-001…`。
- 首批建议采购量与 M06 授权预算：精铺默认首单 30–50 件试错、跑通后一次发 2 个月货，保守销量取双法较小值；这只是起点，仍按 §12 的预算 / MOQ / 交期实验记录计算，不硬编码。

- SWOT 每点尽量带数字  
- **破局切入点** 2–3 条：每条写清证据数字或 VOC、关联 D 标签、具体关键词、Listing 位置、主副图/A+ 模块、变体/规格、建议价格带、供应链动作、广告测试动作和失败阈值。无法被产品事实支持的机会不得写成卖点。  
- 最终结论：**go / go_with_modification / pivot / abort**（建议级，重大决策仍可由总控停人）  

### 6.7 步骤六 · 产品画像 YAML（强制独立文件）

`01_product_profile.yaml` 字段最低集：

```yaml
schema_version: "3.0"
edition: salon-v2
product_type: ""
product_type_en: ""
marketplace: "US"
asin: ""                    # 主对标或空
stage: "新品期"             # 新品期/成长期/成熟期（广告架构分型用）
product_form: "semi_standard"  # standard标品 | semi_standard准标 | non_standard非标
core_roots: []              # 2–5 个英文核心词根
materials: []               # {label, root_words[]} 市场上常见的材质（不是本品）；选款后本品材质写进 03_product_ct.yaml（product_ct.py）
selling_points: []          # {label, root_words[], differentiation_level}
scenarios: []
personas: []                # {label, root_words[], profile}
target_objects: []
intent_signals:
  exploration: { patterns: [], expected_cvr: "" }
  evaluation: { patterns: [], expected_cvr: "" }
  purchase: { patterns: [], expected_cvr: "" }
excluded_roots: []          # 跨品类污染 + 定位冲突
brand_risks: []             # 竞品/大牌名，供 IP 与词库
compliance_blacklist:
  source: "pending_ip_skill"
  epa_words: []
  fda_words: []
  category_sensitive: []
competitor_asins:
  direct: []
  weak: []
  category: []
pricing:
  recommended_range: ""
  floor_price: ""
  ceiling_price: ""
  target_margin: ""
  breakeven_acos: ""
selection:
  entry_difficulty: ""
  recommendation: ""        # go / go_with_modification / pivot / abort
  anchors:
    - ""                    # 给广告/文案用的一句话锚点
data_sources: []            # 如 ["sorftime", "sellersprite"]
generated_by: "amazon-market-research"
author: "坚哥"
```

**规则：**

- `root_words` 英文小写；`label` 可用中文  
- `excluded_roots`、`brand_risks` 尽量全 —— 直接影响词库质量  
- `compliance_blacklist` 完整版以 IP Skill 为准；此处只预填明显项  
- 禁止把示例粘毛器/虚构 ASIN 写进真实交付（模板仅作结构参考）  

---

## 7. 报告结构（01_市场分析报告.md）

> R09 起本结构由插件按「R09 市场分析报告」生成：数字章节自动出，下面各节的判断写进 `market_narrative.yaml` 对应字段（执行摘要→summary，3.4 反向工程→reverse，3.6 属性→attributes，5.x→personas/pains/values/facts_inference，5.5 站外讨论线索→offsite（可选），6.x→difficulty/swot/breakthroughs，7.1→counterargument）。以下保留为内容要求清单。

```markdown
# {产品方向} · 市场穿透报告

## 1. 执行摘要
（能不能做、定价、难度、盈亏 ACOS、一句话破局）

## 2. 分析范围与数据来源
（MCP 名称、市场、条件、时间）

## 3. 竞争格局
### 3.1 竞品数据表
### 3.2 价格带-销量矩阵
### 3.3 头部结构与新手友好度
### 3.4 Top3 反向工程
### 3.5 核心关键词覆盖重叠率与空白机会
### 3.5A 竞品信号判读（可作参照 / 仅证明市场存在 / 不可参照）

## 3.6 动态属性注册表（Unknown 组必留，不重分配）
## 3.7 目标组合交叉表（含反因果声明）
## 3.8 归一化单价与「价格为什么不同」
## 3.9 新品成功/失败对照（winner / survivor / weak 三档）

## 4. 允许成本情景模型（不是利润测算）
（倒算允许到岸成本上限 + 推荐定价 + 广告成本由 CPC/CVR 推导）
## 4.1 30/60/90 天 Gate（Pass / Hold / Stop + 最小样本 + 动作）

## 5. 用户画像与 VOC
### 5.1 画像
### 5.2 痛点
### 5.3 价值点
### 5.4 事实、推断与待验证边界
### 5.5 站外讨论线索（Reddit / Quora，单帖线索，不代表普遍性；无返回时写明）

## 6. 进入难度 · SWOT · 破局
### 6.1 新品进场难度双法（评论区间 / 广告效率，与结构分并列）
## 7. 结论与行动建议
## 7.1 最强反方（反驳论点 + 触发数值 + 触发后动作）
## 8. 证据覆盖率与降级记录
## 9. 下游衔接
（见下一节口令）

---
页脚品牌行
```

---


## ⛔ 选品的终点是「可下单 SKU」，不是品类（v3.6.0）

### 实测缺陷

2026-07-28 宠物用品实测：L1–L3 漏斗跑完，输出「第 1 名：**犬用雨衣（Raincoat）**」——
**停在品类层，没落到具体产品**。而供应商目录里雨衣有两款：

| 产品 | 大货价 | 实重 | 包装 | 抛重 | 抛实比 |
|---|---:|---:|---|---:|---:|
| 狗110 四脚连帽款 | ¥50 | 377g | 28×25×5 | 583g | 1.55 |
| 狗131 高领双腿款 | ¥18 | 72g | 25×20×2.5 | 208g | 2.89 |

**价格差 2.8 倍、重量差 5 倍** —— 这是两门完全不同的生意。
用户看完报告不知道选了哪个、对应哪个 1688 链接，无法下一步。

### 硬规则：选品必须收敛到可下单单元

`00_product_selection.yaml` 与选品报告**必须**给出以下字段，缺任一项判未完成：

| 字段 | 说明 |
|---|---|
| `product_code` | 供应商目录里的**产品编号**（如「狗110」），不是品类名 |
| `product_name` | 完整产品名 |
| `supplier_offer_url` | **1688 offer 级链接**（`detail.1688.com/offer/{数字}.html`）；无链接时写明「供应商目录未提供，需向供应商索取」 |
| `offer_id` | 从链接提取；无则 `Unavailable` |
| `selected_variant` | 选定的**具体规格档**（尺寸/颜色），不是"全系列" |
| `purchase_price` | 该档的大货价，含起订量条件 |
| `chargeable_weight` | 该档计费重 = max(实重, 体积÷6000) |

### 同品类多款时必须比选，不得跳过

品类选定后，**若该品类下有 ≥2 款候选，必须逐款对比再定一款**，对比维度至少：

`大货价 · 计费重 · 抛实比 · 规格档数 · 亚马逊限价 · 与目标价带的匹配度`

**禁止**以「该品类可行」代替「该产品可行」——
品类有市场不等于这一款能做，两款之间的成本结构可能差数倍。

### 交接给下游时的最小信息集

S1 交给 S2/S3/S4 的 `01_product_profile.yaml` 必须带上上表全部字段。
下游任何一步只要用到「本品事实」，读的都是这一份 —— **品类名喂不下去**。

---

## 7.9 证据级八条硬规则（v3.5.0 · 不可跳过）

**执行前必读 `references/evidence-grade-market-research.md`。**

实测对照外部同行同类目产出：我们 36 章 / 对方 22 章，**章节数我们更多，但七项决定结论可信度的能力全部为 0**（属性注册表 / Unknown组 / 归一化单价 / 成功失败对照 / Pass-Hold-Stop / 允许到岸成本 / 最强反方，grep 命中均为 0）。**章节多 ≠ 结论硬** —— 我们多出的章节大多是流程自述，对方少的那些全是判据。

1. **Unknown 组永不重分配** —— 属性注册表按材质/件数/盖型等主轴展开，未识别值单列 Unknown。份额之和不足 100% 是诚实，凑到 100% 是编造。销量份额与金额份额**必须并列**，两者背离即价格结构信号。
2. **单轴份额不得相乘** —— 必须出二维交叉表定位可验证组合，并声明"不把相关份额解释为单一属性的独立因果"。「少 SKU 高份额」须显式指出是头部单品效应。
3. **多件装品类必给归一化单价** —— 比标价无意义。须写明可算比例，不可算的进 Unavailable，不用均价补。另开「价格为什么不同」一节，明写材质溢价只是相关关系。
4. **必须建 winner / survivor / weak 三档队列** —— 只看赢家是幸存者偏差。声明"横截面控制组，不是因果成功率"。
5. **⛔ 经济模型倒算，不得正算利润** —— 固定售价/佣金/FBA/广告成本（由 CPC/CVR 推导），**倒算允许到岸成本上限**。输出的是采购目标，不是利润预测，且不依赖未确认的采购价。**未取得真实到岸成本前不得把情景模型写成利润结论**；标题写「允许成本情景模型」，禁写「利润测算」。是否已取得由 `05_cost_model.yaml` 的 `cost_basis` 决定：`real` 时可写「成本账 / 广告前毛利率」，`conditional` 时毛利只作方向参考。
   > 实测反例：我们 §22 用状态为 `conflict_requires_supplier_quote` 的 ¥8.00 页面价正算出 12 个情景的贡献率 20.7%–53.6% —— 把假设包装成了结论。
   >
   > **这条管的是结论措辞，不是流程推进。** 用户认可的采购价/头程/包装数据（`user_attested`）**可以进模型**，只需带口径标注（如 `¥5.50，user_attested，未获书面确认`）。不得因为成本只有 `user_attested` 等级就停止倒算、拒绝出成本章节，或把它升级成闸门。
6. **⛔ Gate 必须是 Pass/Hold/Stop 三档 + 最小样本 + 具体动作** —— 回答"钱要不要继续花、什么时候停"，不是"活干完了吗"。三要素缺一不可：最小样本量（防 20 点击下结论）、三档（Hold 给修正机会）、每档对应动作。阈值由本轮数据推导，不套通用经验值。
   > 实测反例：我们 §24「S1 Gate」列的是"八项MCP下限 通过 / Top15详情 通过"，是分析自查表。
7. **⛔ 每份 Go / Conditional Go 必配「最强反方」** —— 结构：反驳论点 + **可证伪的触发数值** + 触发后动作。与风险登记表并存不替代：风险表是"可能出什么问题+怎么控制"，最强反方是"这个结论最可能死在哪+死了怎么认"。
   > 实测反例：我们 §23 风险登记 12 条里 8 条的控制动作是"写入验证清单" —— 那是记账不是控制。
8. **覆盖率量化 + 自动降级** —— 报告须给查询覆盖率/决策覆盖率/字段可用率/快照覆盖。**低于阈值时换输出形态**（改为区间或定性），不是硬报一个数再加免责。提前停止必须写明省了多少调用、代价是什么。

### 四条口径纪律

| # | 纪律 |
|---|---|
| 1 | **类目旺季与本品形旺季必须分开**，不得混用同一个峰值月 |
| 2 | **多源字段不一致则不混算**（排名一致但搜索量差 34–44% → 分列两源） |
| 3 | **Unavailable 不得写成 0** —— 无数据是"待验证空白"，不是"0 需求 / 低竞争" |
| 4 | **调整系数必须显式且限定用途**（如 ×1.4 仅用于方向性排序），价格代理须标明期别 |

补充：VOC 出现率须写"方向性样本，不是全类目投诉率"；1688 展示价含非等价 SKU 与 0 值，不得直接作 BOM 采购价。

### Annotation 与交付状态

- **口径变更开 Annotation 节留痕**，三要素：谁要求改的 / 改成什么口径 / **与 Skill 契约字段名的关系**（字段名与实际口径偏离时必须写，否则复跑会误读）。
- **未完成的验收写在成品页脚**，不是写在交接说明里 —— 读者拿到文件就该知道成色。

---

## 8. 完成后强制 handoff（上下游）

### 8.1 回复用户时使用

```markdown
【本步完成】amazon-market-research
【产物】
- S1_选品板块/amazon-market-research/01_市场分析报告.md
- S1_选品板块/amazon-market-research/01_product_profile.yaml
【关键锚点】
- 进入难度：…（结构分）
- 新品进场难度双法：评论区间 …% → 困难/适中/简单；广告效率 … → 友好/烧广告；交叉 …
- 建议定价：…
- 盈亏平衡 ACOS：…（广告步记住）
- 结论：go / …

【下一步 Skill】alibaba-supplier-evaluation
【推荐口令】继续。基于S1_选品板块/amazon-market-research/01_市场分析报告.md 和S1_选品板块/amazon-market-research/01_product_profile.yaml，用产品关键词去 1688 找至少 5 家供应商，输出 02_1688供应商短名单.md，并推荐第 1 家进入详情抓取。
【MCP】used: <实际数据源> / skipped: <原因>
```

### 8.2 下游消费关系

| 下游 | 消费本 Skill |
|---|---|
| `alibaba-supplier-evaluation` | 产品中文/英文方向、材质、价格带、目标规格、排除路线，用于 1688 搜索与短名单 |
| `amazon-ip-risk-assessment` | brand_risks、竞品、品类敏感线索 |
| `amazon-sif-keyword-research` / `amazon-sellersprite-keyword-research` / `amazon-xiyou-keyword-research` | **整份 profile** + 排除词 + 黑名单预填 |
| `amazon-listing-copywriter` | 痛点、价值点、人群、定价 |
| `amazon-listing-image-designer` | 场景、卖点、人群 |
| `amazon-ad-campaign-builder` | 竞品 ASIN、定价、**breakeven_acos** |
| `starsky-fullchain-workflow` | 更新 state，推进下一步 |
| `amazon-competition-difficulty-assessor` / `amazon-gray-tactic-detector` | 本步内调用：竞品表、竞品池、核心词；两者产物回填 6.6.1 与 3.5A |

### 8.3 上游

| 上游 | 提供 |
|---|---|
| `starsky-fullchain-workflow` | 候选方向、条件、叠层、工作目录 |
| `00_选品推荐报告.md` | 第 N 名方向 |
| 用户 | ASIN、1688、成本 |

---

## 9. 独立使用（不经总控）

最少输入：类目词或 ASIN + 市场 US。  
仍须：MCP 优先、双文件落盘、handoff 指向 **IP → 词库**（不要直接拓词）。

---

## 10. 质量自检

- [ ] 关键数字有 MCP 来源或明确标注推断  
- [ ] 有价格矩阵 + 推荐定价 + 盈亏 ACOS（取自 `05_cost_model.yaml`，脚本算出）  
- [ ] 有毛利率档位 + 保本 CPC 与核心词 CPC 对照 + 广告压力（只给指标，不替用户判放弃）  
- [ ] 有进入难度计算过程  
- [ ] 有新品进场难度双法（01A + difficulty_assessment.yaml，脚本算出，数字一致）；参照 ASIN 有信号判读（01B）  
- [ ] `01_product_profile.yaml` 独立存在且字段齐全  
- [ ] excluded_roots / brand_risks 非空（合理范围内）  
- [ ] 给出下一步 Skill 与可复制口令  
- [ ] 页脚含实战版出品信息  
- [ ] 若有 workflow_state 已更新  

---

## 11. 版本

## 12. 7·18 市场穿透深度恢复契约

运行前读取 `../../templates/report_depth_and_demo_policy.md`。市场报告不能只给“值得做/不值得做”和几张汇总表；必须让读者看清市场结构、竞争逻辑、财务边界和新品如何切入。

### 必须恢复的分析模块

1. **竞品全表**：目标池中每个 ASIN 的标题、品牌、价格、评分、评论、销量/销售额/BSR（仅有真实来源时）、上架时间、变体、核心卖点、差评风险与数据来源。
2. **价格带×销量矩阵**：按实际分布分档，展示 SKU 数、销量/销售额占比、中位数评论、竞争特征和建议进入带；不能只写一个建议售价。
3. **Top 3–5 反向工程**：流量入口、产品结构、视觉/文案、变体、评论护城河、供应链猜测与可复制/不可复制点。
4. **关键词覆盖与需求簇**：核心品类词、功能词、场景词、人群词、对象词，指出头部覆盖、空白和后续反查种子。
5. **VOC 深挖**：画像 4–6 个；正面价值驱动与差评主题分组；有样本时量化频次，无样本时标 `AI推断`，不得伪造评论统计。
6. **新品进入难度**：至少从头部集中、评论壁垒、品牌壁垒、价格挤压、差异化空间、供应链门槛评分，并解释权重和证据。
7. **SWOT 与破局推导**：每个切入点必须沿“需求证据 → 竞品缺口 → 产品改法 → 成本影响 → Listing/视觉表达 → 风险”展开。

### 财务测算必须可复算

```text
landed_cost = purchase + packaging + domestic_freight + first_leg + duty
contribution_before_ads = price - referral_fee - fba_fee - landed_cost - return_allowance
break_even_acos = contribution_before_ads / price
break_even_units = fixed_launch_cost / contribution_before_ads
```

必须列明每个变量来源，至少做售价、采购价、头程、FBA 费的敏感性；缺真实值时用区间和条件性结论。首批预算建议需拆成采购、头程、广告和缓冲金，并验证 MOQ/现金占用。

### 新品运营视角

报告增加：首发变体策略、首批量与补货触发、安全库存逻辑、30/60/90 天验证目标、失败阈值与 pivot 条件。不能用“建议小批量测试”一句带过。

### 0711 VOC 与实验设计增强

运行前同时读取 `../../templates/evidence_decision_revision_contract.md`。VOC 不能只列主题，按下列结构输出：

```yaml
pain_point_id: P-001
category: size | material | structure | durability | usability | scenario | appearance | packaging_service
theme: ""
frequency: null
severity_avg: null
verified_purchase_weighted: false
review_quotes: []
affected_asins: []
structural_cause: ""
product_response: ""
claim_status: listing_ready | sample_pending | hypothesis_only
evidence_ids: []
```

- 一条评论可多标签，但各标签独立计数；优先看频率×严重度交集。
- 区分产品问题和用户误用；Verified Purchase 可加权，但必须披露权重。
- 无评论原文/样本数时，保留完整类目假设分析，但 `frequency/severity` 留空，不编百分比。
- 每个破局点必须连接 `痛点证据 → 属性空白 → 产品改法 → 成本/MOQ/IP → Listing/视觉 → 广告验证 → 失败阈值`。

首批方案采用实验记录，不硬编码数量：

```yaml
- variant: "主推款/对照款/premium 小量款"
  hypothesis: ""
  units: null
  unit_basis: "预算、MOQ、交期、转化与库存风险"
  success_metric: ""
  stop_or_scale_rule: ""
```

数量必须由用户预算、采购/头程/广告分配、MOQ 和安全库存计算；300 件只可作为历史案例，不是默认规则。

### 0711 最强反方与决策去美化

S1 决策前必须写 `strongest_counterargument`：站在“不做这个产品”的角度，选出最可能推翻当前 Go/Modify 的一条证据链，包含 evidence_ids、为何重要、需要什么新数据才能反驳、触发 pivot/abort 的阈值。只写泛泛“竞争激烈”不合格。

反方论证只用于高风险综合判断，不把每个小步骤写成长辩论。最终仍需分别列事实、推断、建议和 decision_record；研究可继续不等于允许下单或作未验证声明。

### acceptance_checks

- [ ] 竞品对象有明细和来源，不以均值替代全表。
- [ ] 价格带、头部集中、评论壁垒、新品窗口均有证据。
- [ ] Top 竞品反向工程包含可复制与不可复制项。
- [ ] Top3 每个竞品均有独立证据卡；核心词覆盖矩阵存在，无真实词数据时已标待 SIF 验证。
- [ ] 财务公式、变量、代入、敏感性和预算占用完整。
- [ ] 画像没有无证据占比；VOC 有样本数、频次和原话，或明确标为类目假设。
- [ ] 难度分项均展示原始值、归一化、权重与缺数敏感性。
- [ ] 破局点已经落到词、图、变体、价格、供应链和广告验证动作。
- [ ] 给出 go/go_with_modification/pivot/abort 的条件，而非模糊建议。
- [ ] product_profile 与正文关键数值、定位、变体和风险一致。
- [ ] 下游供应商关键词、产品规格和竞品 ASIN 种子可直接使用。

| 版本 | 说明 |
|---|---|
| 3.8.0 | **接入阈值法方法层（2.5.0）**：6.3 第 7 项调 `amazon-gray-tactic-detector` 给参照 ASIN 打真伪等级；6.6.1 调 `amazon-competition-difficulty-assessor` 出「新品进场难度双法」（评论区间 / 广告效率，任一通过），与结构性难度分并列不合并；阈值只读 `templates/s1_thresholds.yaml`；数字必须由 `difficulty_calc.py` 算出；回填 S1 报告 M05 / M10 / M17。来源：2025-07 选品实战班蒸馏 |
| 3.4.0 | 0711 融合：VOC 八类多标签、频率×严重度、evidence 追溯、痛点到改良/表达/广告验证链，以及主推/对照/premium 实验型首批方案 |
| 3.3.0 | 三方融合：恢复 Top3 逐一反向工程、关键词覆盖空白矩阵、画像/VOC 证据表、难度分项口径与破局点落位规则 |
| 3.0.1 | 修正沙龙 S 场景 handoff：市场穿透后先进入 1688 供应商短名单，不默认跳 IP |
| 3.0.0 | 实战版重梳理：MCP 主路径（Sorftime 优先）、总控衔接、叠层、独立 profile、统一 handoff、去掉旧 v1/v2 叠叙与错误 SIF 主数据源表述 |


## S1 市场事实与结构化验收补丁（2026-10-14）

以下规则补充并优先约束既有报告流程，不删除历史备注、关键词清单或取数证据。

R05 C2 执行补充：00 的生产责任属于 Agent。保存真实初筛输入后运行插件根 `python scripts/s1_selection.py --root <产品根> --input <真实selection_yaml相对路径>`；供应先行同样使用真实已落盘输入。紧凑流程在封存前自动调用该生产器，步骤检查校验其与原输入一致。不得要求用户编分来补文件。空分及缺证原因原样保留，权重沿用已约定 D1–D5，缺维不算该候选总分、候选未齐不排整体名次，综合结论继续 Hold。新生成文件不改旧报告或历史哈希。

1. **00 的生产者与闸门**：`jingpu-product-selection` 执行四方向初筛/五维评分时产出 `S1_选品板块/jingpu-product-selection/00_product_selection.yaml`。供应先行或跳过初筛时，由本 Skill 接管此生产责任，写真实 `scoring_weights`、每个候选的五维 `scores`、`weighted_score`、`rank` 与逐项证据来源。缺源分数保持缺失，不能由排序反推分数。00 文件缺失时阻断综合报告验收，不能用 01 profile、方向选择或 2A 决策替代。
2. **唯一事实快照**：01 报告落盘前运行插件根 `scripts/s1_market_facts.py <产品根>`，产出本 Skill 的 `01_market_facts.json`。每个 ASIN 先选一个完整 `product_detail` 快照（同级按来源日期），禁止在主表/台账分别选字段。原回包和原 `s1_decision_inputs.yaml` 不改写。保存来源路径、SHA-256、日期与原值。缺失哨兵 `-1`、空串、None 不进入计算。
3. **父体先去重再统计**：同一 `parent_asin` 优先保留有有效价格的子 ASIN，再按 ASIN 字典序选代表；其他子体仍展示，标为“同父体，不重复计入”。价格分位、销量与样本数一律消费 `representatives`，不得重新对全部子体计算。详情覆盖数量和去重后的市场样本数量分别标明。
4. **形态过滤**：在候选入池前调用共享脚本的 `classify_product`；存 `same_form / needs_review / excluded` 及原因。刺绣制作 kit、含线/针/图案的组合品、五金辅料不得进入项目空包样本。`kits storage bag`、`thread storage bag` 表示被收纳物，不能仅凭 kit/thread 单词排除。标题不足以核实含物时标待复核；不同产品形态需配置相应筛选规则，不得套用空包结果。
5. **结构化必出判据**：`01_product_profile.yaml.market_criteria` 固定键为 `strongest_counterargument`、`pass_hold_stop`、`allowable_landed_cost`、`coverage_downgrade`、`unknown_attributes`、`normalized_unit_price`、`cohort_comparison`、`listing_age`、`monthly_trend`、`latest_order_date`。每项写 `{status: available|not_calculable|pending, source: 相对证据路径, value: 结果}`；后两种状态写具体 `reason`，不杜撰数值。最晚下单日缺提前期时同时列需重算的项。单价写数量基数和单位；空包数量基数为1时显式写入。01 报告固定输出上述十节，数据缺失写“不可计算 + 原因”。综合报告只引用这些已落盘判据，不再下游生成新判据。
   **原生判据（自 R06.1 起）**：`monthly_trend`、`listing_age`、`normalized_unit_price`、`cohort_comparison`、`coverage_downgrade`、`allowable_landed_cost`、`unknown_attributes`、`latest_order_date` 八项由 `scripts/s1_criteria.py` 依据已落盘的事实快照、成本模型和合规尺子确定性算出，写入 `01_market_facts.json` 的 `criteria`（带 `origin: native` 与一行 `summary`），**不需要也不应再手写**；数据不够时它会写“待补证/不可计算 + 缺什么”，不杜撰数值。只有判断型的 `strongest_counterargument`、`pass_hold_stop` 仍由助手或用户写进 `market_criteria`。作者手写的条目永远优先，想让原生接管就删掉那个键。`latest_order_date` 要算出日期，需把厂家生产天数、头程天数、入仓接收缓冲（`production_days`、`transit_days`、`receiving_buffer_days`）由用户、厂家或货代确认后写进 `01_product_profile.yaml` 的 `lead_times`，不得推断。
6. **01 发布前门禁**：运行 `skills/starsky-fullchain-workflow/scripts/verify_s1_acceptance.py <产品根> --market-only`。C2/C3/C4未通过时不得将综合报告标记正式验收通过；允许保留明确标为待补证的研究稿和缺口清单。验收读取结构化字段，不按中文同义词判阻断。
7. **真实覆盖与续跑**：完整交付至少15个竞品详情、10个父体变体、100条去重VOC、3个相关类目横向对比、12个月趋势及旺季Top100对照。保存每次调用 `tool / args / queried_at / response / sha256`。低于要求逐项写已取/所需、实际返回文本或报错、受影响判断、下一次工具与参数；没有错误回包时写“尚未调用/返回样本不足”，不得声称配额耗尽或工具不可用。相邻类目和全类目总体不得与本品容量相加。自然查询必须验证自然位次字段与相关性，数组首5行不自动等于自然前5；广告词未拉完分页只能标已观测下限。历史查询回包若含现期rank日期或混入其他node，保留待核，不能冒充同口径旺季Top100。
8. **补拉独立快照**：新采集放独立带日期目录，使用 `build_enrichment_facts` 汇总为独立 `enrichment_facts.json`，报告列采集时间与覆盖差异，不回填覆盖旧日原始竞品数值。历史备注和关键词仍保留。


### R02 分层与持久词库接口

S2 竞品采用同类目 BSR 1–30 / 31–60 / 61–100 的 6/6/5 个与官方新品榜3个；只以相关同类/同款补位并标来源段、补位理由、价格带状态。新品榜不可得有证据才用日期窗口回退；不足报告实际数。旧候选池、原备注保留，新分层需本表用户确认，不能继承旧20池审批或演示默认。

完整单源词包用 `keyword_package_contract.py finalize --series <真实系列根>`，通过分层/来源/完整性门禁后接入系列资产；CSV normalize 不等于入库成功。只取 `keyword_asset.py select --limit 20` 摘要与正式关键词报告链接，禁止将整个 master 读入/粘贴模型上下文。保留原指标与周/月口径，来源不能互补填空。具体接口及 CT 有限用途边界见 `../../docs/keyword_asset_contract.md`。
