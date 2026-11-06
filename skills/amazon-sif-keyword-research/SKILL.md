---
name: amazon-sif-keyword-research
version: "4.2.0"
edition: starsky-v2
description: >
  SIF 单源关键词研究与 Keyword Package 2.0 生产 Skill。读取 SIF 导出、产品事实、
  合规黑名单和已确认竞品池，保留 SIF 17 字段、Sheet2 反向污染学习、流量集中度、
  季节窗口、Listing 分池与广告候选分池；输出带 source_id/SHA-256/as_of/period/null
  语义、数量守恒、Hash 和 Builder 血缘的独立词包。只处理 SIF，不静默混入卖家精灵
  或西柚。触发：SIF 关键词、SIF 词库清洗、keyword_package、六维分类、广告分池。
---

# SIF 关键词研究 4.0

## 目标与职责

把 SIF 原始证据转成可审计、可修订、可被 Listing 与广告系统复用的单源关键词资产，不只挑选少量热词。

只提供来源清楚的候选池和市场信号；不得决定最终 Campaign 数、预算、Bid 或 Placement。把经营决策交给 Strategy，把执行计划编译交给 Builder。

## S2 共同判断与固定报告

必读 `../../templates/keyword_report_layout.md`：三源共用八节报告和逐词判断方法；Listing 固定十节，详见 Copywriter 的报告契约。先读当前 S1/企业 IP 与声明限制，再做本品匹配、文案用途、广告用途，最后按同源同窗指标排序。

每词记录产品词根、主分类（产品词/属性词/其他）、多标签（功能/规格/材质/场景/人群/兼容/痛点）、direct/adjacent/mismatch/unknown 与 fact_refs。D1–D6 保留兼容；主分类统计唯一词，多标签不能重复计入唯一词数。场景、保留率和研究卡不设数量或比例下限。

继承用户或 manifest 选择；仅一个导表来源就沿用它，没有来源选择时默认优先 SIF。用户明确“仅用导表/不调用”时不调 MCP。运行前读本任务工具与 schema，分别记录配置/授权、工具暴露、协议与业务返回；只据本轮真实业务返回写 used/partial。

同源 MCP 增量用新 source_id 保留原始返回和脱敏调用摘要。跨源补数放独立 evidence/context，不能冒名填主包空值。优先补本品可用的高价值词、分歧词、季节敏感词；失败显示缺口、影响和可重试入口。

季节分析默认尝试两个完整年度可比历史；只有一年降低确定性，只有一周不判全年峰值。历史与当前榜分开；搜索量合计称“选定词集合的搜索量”，不称去重买家流量。

正式入口为插件根 `scripts/s2_report.py --package <path> --context <path> --output-dir <dir>`；有文案加 `--listing`，有基线加 `--baseline`。参数/JSON 见 `../../templates/s2_report_input.md` 和 `--help`。不写每个产品自己的 HTML 生成器。

同轮生成 keyword_report.html、keywords.csv、report_model.json、validation.json、manifest.json；文案输入另生成 listing_report.html、listing.md。旧 06/07 文件名可由路由映射并记录指纹；不按 KB、表格或进度条数验收。正式用途资格必须写入词包并更新 Hash，不能只改报告 context 后送广告。

## 固定执行：scripts/sif_clean.py（不手写清洗代码）

清洗、分池、MCP 补数和报告上下文全部由本 Skill 的 `scripts/sif_clean.py` 完成，同一输入永远得到同一词包。禁止现场写 `_fix_*.py`、手改词包记录或手写报告 context。用安装记录里的 Python，产品根带引号：

```text
1. <python> <skill>/scripts/sif_clean.py --run "<产品根>"
   先清洗当前 S1 画像/事实/黑名单、确认竞品池与 SIF 导出，生成本轮 review_queue.json 与 review_queue_context.json（输入文件/来源hash、产品/站点/ASIN/期间、队列hash）。此时只是待审词包，不代表本步/阶段完成；先不调用MCP，不读旧队列作本轮依据。
2. **必做，不能跳**：读 review_queue.json 前列（按周量排序的待判词），逐个裁决写 review_decisions.json。队列非空却没有裁决文件时，第 4 步校验不通过、不能记完成——跳过这一步会让可用词池过小，后面 ST 填不满、五点埋词不足：
   {"keywords": {"<词>": {"disposition": "...", "product_match": "...", "reason": "为什么", "by": "model|user"}},
    "lexicon": {"D3": ["gift", ...]}, "counterarguments": ["..."]}
   不确定的保留 observe。含声明限制/品牌/IP/未确认事实的词不能裁决为 retained，脚本会拒绝。
3. <python> <skill>/scripts/sif_clean.py --run "<产品根>" --plan-mcp
   裁决完成后，重新清洗并生成最终 mcp_plan.json，当前队列及输入绑定也刷新。只有此最终计划用于补数；不能先调MCP再改裁决。宿主核对当前实际可调用的SIF工具与schema，按最终清单参数、实际注入名称原生调用；配置存在不是原生可用证据，Python不能调用宿主注入工具。
   每次原始MCP返回先保存为独立结果文件，再运行：
   <python> <插件根>/scripts/sif_mcp_routing.py record-native --plan <mcp_plan.json> --call-id <call_id> --tool <实际工具名> --result <原始结果JSON>
   成功、合法空返回和失败都保存；追加独立attempt，保留原工具/通道/时间/参数hash/来源期间，不覆写历史。实际调用抛错而没有返回体时，用 --failure-category <安全类别> 代替 --result，并传 --called-at <实际调用ISO时间>。宿主工具不可调用就显示缺口，不能伪造receipt；明确授权拒绝时停止，不通过其他路由绕过。
4. <python> <skill>/scripts/sif_clean.py --run "<产品根>"   （按相同最终计划合并，词包版本 +1，旧版进 _history/）
   全量资格校验时，进度写在本 Skill 夹的 `keyword_package.progress.json`（阶段、已校验 N / 总数、百分比、更新时间）；等待时读这个文件向用户报进度，不要重启进程或重复提交。
   本步不再改裁决/产品资料/原表。若这些输入或采集日期改变，先回前面生成新最终计划，再完成新计划所需调用；不把旧plan回执强行算成功。同一最终计划再次合并不重复调用MCP、不重复入数。
5. <python> <插件根>/scripts/s2_report.py --package keyword_package.yaml --context s2_report_context.json --output-dir <本 Skill 夹>
```

- **材质 / 形态以 S1 产品 CT 为准**：脚本读 `S1_选品板块/1688-product-detail-scraper/03_product_ct.yaml`（`product_ct.py` 产出）。CT 里「不是本品」的材质 / 安装方式 / 功能词（本品硅藻土时的 bamboo、stainless steel、wall mounted…）直接判 `low_relevance_negative`（`attribute_conflict`），不进标题 / 五点 / 广告池，并生成词组否定建议；旧裁决把这类词判 retained 的，脚本不采纳并在摘要 `product_ct.decisions_overridden` 里计数。`01_product_profile.yaml` 的 materials 只代表市场，不当本品。
- 摘要 `product_ct.undecided_modifiers` 是留用词里 CT 还没判的修饰词（wall、shower、cup、travel、cover…）：逐个跟用户或 1688 页面核对「本品是不是」，是就写进 CT（结构/功能 en_terms），不是就进 `not_this`，重跑 `product_ct.py` 再重跑本脚本。缺 CT（摘要 `product_ct.status=missing`）时先回 S1 补 CT，不要靠裁决文件逐词修。
- 原生工具不可用时，在完成裁决后的第3步明确选择桥接 fallback：`--fetch-mcp --mcp-route bridge --mcp-config "<实际宿主配置>"`。Codex传实际CODEX_HOME/config.toml，Claude传其安装根mcp_servers.json，不能误读Codex默认。桥接每任务探测一次；已知运输失败停止整批，权限拒绝不能换通道绕过。修正原因后才显式 --mcp-retry 重探测。不传bridge时--fetch-mcp只输出宿主调用计划，不声称原生可用。两路都不可用则保留缺项/影响/重试入口，再运行第4步合并、第5步产出真实缺口报告。
- 逻辑任务成功数与attempt历史分开；只合并当前产品/来源/站点/期间/完整参数匹配的一份有效成功。旧格式历史receipt不可冒充当前调用；显式hash绑定的离线回放标“历史回放”，不称本轮新联网。
- `s2_report_context.json` 与 `facts_registry.json` 由脚本从 S1 产物生成：事实 ID、状态、限制都有来源。报告只把已确认事实支撑、且无声明/品牌/IP 限制的本品词算「可用」。
- 脚本输出 JSON 摘要（status、守恒、各池数量、review_queue、mcp）；`status=fail` 时按 errors 修输入，不绕过。
- 下面各 Step 是脚本实现的规则，用于解释和审阅结果。

## 执行前必读

1. `../_keyword_system_contract/references/keyword_package_2_contract.md`：统一 Package、Hash、来源隔离与广告交接。
2. `references/sif_source_contract.md`：SIF 17 字段、Sheet2、集中度、季节性和 CVR 边界。
3. 当前 run 的 product profile/facts、竞品确认池、IP/合规黑名单和资料叠层。

## 运行模式

- `production`：生成可进入 Strategy/Builder 审核的词包；词包永远不代表上传授权。
- `demo_direct_run`：完整生成，但 `demo_label=DEMO_ONLY_NOT_UPLOADABLE`；不得进入 Production Bulk。

缺少非关键字段进入 `data_explanation_window` 并继续。只有登录/验证码、生产 IP 决策、用户要求可直接上传但缺真实标识/官方模板、不可逆外部动作、继续会伪造事实或违反红线时 hard block。

## 输入与边界

| 输入 | 最低要求 | 缺失处理 |
|---|---|---|
| SIF Excel | 至少一个可识别关键词 Sheet | 无表但本轮可真实调用 SIF MCP 可拉词；否则只说明补数路径，不编词 |
| product scope | marketplace + product_id | Demo 可用显著占位；Production 下游标 conditional |
| product profile/facts | 核心词根、排除词根、事实、适配边界 | 先做结构/字段诊断，策略性分池标待审 |
| competitor pool | 已确认 ASIN 与决定来源 | 无池只报 export_observed，不称 competitor coverage |
| blacklist | 品牌、平台禁用、blocked_claim 分型 | 品牌/敏感词先隔离，不能假装已合规 |

`blocked_claim` 需求词保留在 records 并标 `claim_restricted=true`；未确认/禁用功能退出当前可用文案池和默认广告候选，等待具体事实与用途审核。

## 标准作业流程

### Step 0｜建立运行身份和来源账本

生成 `run_id/package_id/package_version`。对每个输入文件记录相对路径、SHA-256、Sheet、行数、站点、`as_of`、period/month、单位、字段映射和缺失率。

本线 `source_mode=sif`、`source_line_policy=isolated`。发现 SellerSprite/西柚文件时只登记“另一路线存在”，不读取其指标补本线空值。

### Step 1｜MCP 真实性

先检查当前真实工具清单。只有本轮实际调用 SIF 工具且写入 `mcp.calls[]` 才能标 `used/partial`。

- 工具不存在：`skipped + reason=no_callable_sif_tool_this_run`，继续洗 Excel。
- 调用失败：`failed`，记录脱敏错误，继续洗 Excel。
- 调用成功：新证据用新的 `source_id` 并列，不覆盖 Excel。

配置过、旧任务用过、仅看到工具名都不等于本轮 `used`。

### Step 2｜确定性标准化

- 清理表头换行、全半角、货币/百分号、空白和多空格。
- 原词与 `normalized_keyword` 同时保留；归一化同词合并到一个 record，所有原始行进 lineage。
- 空值保留 `null + missing_reason`，0 仅在原始字段明确为 0 时保留。
- 按 `references/sif_source_contract.md` 完整映射 17 字段；未知列进入来源日志，不猜语义。

### Step 3｜污染、品牌、IP 与数量守恒

匹配使用完整词/短语边界；核心词根优先于排除词根。Sheet2 只生成带命中数和真实例词的候选污染词根，低置信候选进入观察，不直接批量否定。

每个唯一词只能进入一个 disposition：`retained / observe / brand_isolated / compliance_negative / low_relevance_negative`。全部 unique records 留在包内；守恒失败不得 handoff。

保留率异常只触发根因审计，不把某个案例比例写成所有类目的硬门槛。

### Step 4｜产品相关性、六维与竞品交集

从当前事实动态生成 D1 材质/结构、D2 功能/机制、D3 场景、D4 人群、D5 对象、D6 意图词典。每个词保留 D1–D6 数组和证据；未分类必须计数、举例和说明影响。

共同覆盖只计算 `export_observed_asins ∩ confirmed_competitor_asins`。分母为 0 时 ratio 为 null，不写 0。

### Step 5｜SIF 专属诊断

- 计算 Top10/20/50/100 流量占比与覆盖 80% 流量所需词数；原始数值是事实，集中/分散标签的阈值标 `case_default` 或企业规则。
- 把高流量低相关词单列，不能因流量大升 Exact。
- 写数据窗口与季节性来源；没有月度/趋势证据时写 unknown，不从单次导出臆测全年。
- `cvr_24h`、Top3 转化集中度、CPC 和搜索量分别保存；绝不相互顶替。

### Step 6｜Listing 分池

先验证当前 SKU 的事实、匹配和声明许可；仅合格词输出可用 ID 池：Title、Highlights、Bullet feature、Bullet scenario、Description/A+、ST candidates、Attributes、Q&A。待核词留 Fitment review/observe，不让高流量绕过资格。

竞品/未许可品牌不进消费者可见池；已确认自有品牌可用于 Title，兼容词按 S1 指定范围复核。ST 这里只提供候选，Copywriter 才做最终词去重、去禁用品牌和 249 UTF-8 bytes 校验。

### Step 7｜广告候选分池

先过滤待证/禁用及用途不匹配词，再输出 Builder 可读的候选；资格与原始记录分别保留：

```text
exact = exact_p1 ∪ exact_p2
phrase_feature
phrase_scenario
broad_discovery
observe
negative_pools.exact / phrase
asin_pools.pat_candidates / defense_candidates / brand_review
```

P1/P2 表示当前关键词证据层级，不代表账户已经验证转化。初始 Bid 只提供外部 CPC 信号、填充率和经济模型所需输入；最终允许 CPC/Bid 由 Strategy 结合利润与账户 CVR 决定。

### Step 8｜生成、Hash 与验证

按共享模板生成 JSON 语法的 `.yaml`，使无 PyYAML 环境也可校验：

```powershell
python ..\_keyword_system_contract\scripts\keyword_package_contract.py finalize `
  --input S2_文案板块\amazon-sif-keyword-research\keyword_package.yaml `
  --expected-source sif `
  --report S2_文案板块\amazon-sif-keyword-research\keyword_package.validation.json
```

校验失败不进入下游。Package 修改后 Hash 必变，旧 Strategy/Plan 审核失效。

### Step 9｜报告、数据解释与修订

报告按统一八节展示上述结论与证据。全部研究词及原始字段可定位，关键风险保持可见；风险和反方按真实证据记录，不为凑数量编造。

后补文件或字段时保留旧包，递增版本，写 `supersedes` 与 `late_data_revision`；只重算受影响记录、分析和下游 Hash，不重洗无关来源。

## Acceptance checks

### P0

- [ ] Package Schema/Hash 通过，验证报告路径和 computed hash 已记录。
- [ ] 所有文件有 source_id、SHA-256、as_of、period/month、站点和单位。
- [ ] 本包 provider 全为 SIF；无 SellerSprite/西柚指标混入。
- [ ] MCP `used/partial` 有本轮调用证据；否则为 skipped/failed。
- [ ] null 未写 0；`cvr_24h` 未冒充 Amazon 账户 CVR。
- [ ] 原始行、有效行、重复、五类 disposition 与 records 数量守恒。
- [ ] 竞品覆盖分母与确认池一致，零分母返回 null。
- [ ] Listing/广告/否词/ASIN 池 ID 均可解析到 records 和 lineage。
- [ ] Demo 标识、上传边界、数据解释窗口和局部修订字段齐全。

### P1

- [ ] 流量集中度计算可复算，阈值来源未伪装成平台规则。
- [ ] 季节性引用真实趋势/窗口；无证据时未硬判。
- [ ] 高流量低相关、未分类、品牌/IP 和最强反方完整。

勾选必须附算式、路径或计数；空勾无效。

## Handoff

```markdown
【本步完成】amazon-sif-keyword-research 4.0
【词包】S2_文案板块/amazon-sif-keyword-research/keyword_package.yaml（package_id/version/hash）
【来源】SIF files=<N> / MCP=used|partial|skipped|failed
【守恒】raw=<N> / unique=<N> / retained=<N> / observe=<N> / isolated+negative=<N>
【下一步】先由总控/用户选择本包，再交 amazon-ad-strategy-architect；Builder 只读取所选 Hash。
【边界】本词包不授权 Bulk 或外部上传。
```

## 版本记录

| 版本 | 说明 |
|---|---|
| 4.0.0 | 单源 Keyword Package 2.0；新增来源 Hash、MCP 本轮真实性、null/period、守恒 validator、Strategy/Builder 映射、Demo 与局部修订；删除跨源主次/自动兜底和词库内最终 Bid 决策。 |
| 4.2.0 | 清洗固化为 scripts/sif_clean.py：确定性标准化与本品匹配、按词根登记的事实（声明限制只挂到对应词根）、审阅裁决文件、MCP 补数经直连桥落盘合并、报告 context 自动生成。 |


### R02 分层与持久词库接口

S2 竞品采用同类目 BSR 1–30 / 31–60 / 61–100 的 6/6/5 个与官方新品榜3个；只以相关同类/同款补位并标来源段、补位理由、价格带状态。新品榜不可得有证据才用日期窗口回退；不足报告实际数。旧候选池、原备注保留，新分层需本表用户确认，不能继承旧20池审批或演示默认。

完整单源词包用 `keyword_package_contract.py finalize --series <真实系列根>`，通过分层/来源/完整性门禁后接入系列资产；CSV normalize 不等于入库成功。只取 `keyword_asset.py select --limit 20` 摘要与正式关键词报告链接，禁止将整个 master 读入/粘贴模型上下文。保留原指标与周/月口径，来源不能互补填空。具体接口及 CT 有限用途边界见 `../../docs/keyword_asset_contract.md`。
