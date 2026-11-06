---
name: amazon-sellersprite-keyword-research
version: "4.1.0"
edition: starsky-v2
description: >
  卖家精灵单源关键词研究与 Keyword Package 2.0 生产 Skill。消费 SellerSprite/
  ExpandKeywords 导出和可选的本轮 SellerSprite MCP，保留月搜索量、月购买量、
  purchaseRate、SPR、标题密度、PPC 建议、Top ASIN、适配/型号复核、Listing 分池与
  广告候选池；所有值带 source_id/SHA-256/as_of/month/null 语义。purchaseRate 只作
  卖家精灵平台购买信号，不冒充 Amazon 广告 CVR。触发：卖家精灵词表、ExpandKeywords、
  SellerSprite 关键词、六维分类、keyword_package、广告分池。
---

# SellerSprite 关键词研究 4.0

## 目标与职责

把 Excel 中的需求、购买信号、竞争、PPC 与 ASIN 线索清洗成可审计的卖家精灵单源词包；不得把平台字段改名包装成 Amazon 账户事实。

让产物服务 Listing、Strategy 和 Builder；不得在本 Skill 决定最终 Campaign、预算、Bid、位置或上传审核。

## S2 共同判断与固定报告

必读 `../../templates/keyword_report_layout.md`：三源共用八节报告和逐词判断方法；Listing 固定十节，详见 Copywriter 的报告契约。先读当前 S1/企业 IP 与声明限制，再做本品匹配、文案用途、广告用途，最后按同源同窗指标排序。

每词记录产品词根、主分类（产品词/属性词/其他）、多标签（功能/规格/材质/场景/人群/兼容/痛点）、direct/adjacent/mismatch/unknown 与 fact_refs。D1–D6 保留兼容；主分类统计唯一词，多标签不能重复计入唯一词数。场景、保留率和研究卡不设数量或比例下限。

继承用户或 manifest 选择；仅一个导表来源就沿用它，没有来源选择时默认优先 SIF。用户明确“仅用导表/不调用”时不调 MCP。运行前读本任务工具与 schema，分别记录配置/授权、工具暴露、协议与业务返回；只据本轮真实业务返回写 used/partial。

同源 MCP 增量用新 source_id 保留原始返回和脱敏调用摘要。跨源补数放独立 evidence/context，不能冒名填主包空值。优先补本品可用的高价值词、分歧词、季节敏感词；失败显示缺口、影响和可重试入口。

季节分析默认尝试两个完整年度可比历史；只有一年降低确定性，只有一周不判全年峰值。历史与当前榜分开；搜索量合计称“选定词集合的搜索量”，不称去重买家流量。

正式入口为插件根 `scripts/s2_report.py --package <path> --context <path> --output-dir <dir>`；有文案加 `--listing`，有基线加 `--baseline`。参数/JSON 见 `../../templates/s2_report_input.md` 和 `--help`。不写每个产品自己的 HTML 生成器。

同轮生成 keyword_report.html、keywords.csv、report_model.json、validation.json、manifest.json；文案输入另生成 listing_report.html、listing.md。旧 06/07 文件名可由路由映射并记录指纹；不按 KB、表格或进度条数验收。正式用途资格必须写入词包并更新 Hash，不能只改报告 context 后送广告。

## 执行前必读

1. `../_keyword_system_contract/references/keyword_package_2_contract.md`。
2. `references/sellersprite_source_contract.md`。
3. 当前 product profile/facts、确认竞品池、fitment 清单和合规黑名单。

## 运行边界

- `production`：可进入 Strategy/Builder 审核，但词包永远不授权上传。
- `demo_direct_run`：写 `DEMO_ONLY_NOT_UPLOADABLE`；演示 fitment/预算参数不能冒充生产事实。

无 SellerSprite MCP 不阻断 Excel 清洗。无产品事实时先做结构、字段和重复诊断，相关性、Listing/广告核心池保持 conditional。

## 标准作业流程

### Step 0｜运行身份、来源与月份

生成 `run_id/package_id/version`。逐文件登记相对路径、SHA-256、Sheet、导出 ASIN、站点、`as_of`、`period.month`、原始行数、字段单位和缺失率。

SellerSprite 的月搜索量、月购买量和 purchaseRate 必须保留来源月份。没有月份的值保留但标 `period_missing`，不能把它当当前月事实。

### Step 1｜单源与 MCP 真实性

本线只接受 SellerSprite 文件和 SellerSprite MCP。旧版“平级 MCP 可代验”取消：SIF、Sorftime、西柚信号应在 Strategy 的其他来源账本并列，不能写入本词包补空。

`mcp.status=used/partial` 必须有本轮真实调用日志；工具未暴露写 `skipped`，调用失败写 `failed`。MCP 返回和 Excel 原值用不同 source_id 并列，不覆盖。

### Step 2｜工作簿识别与标准化

识别主关键词表、`Unique Words`、`Asin` 和 Notes；保存实际 Sheet 名。清理中英文表头、货币、百分号、空格和范围；保留 source keyword 与原始值。

归一化同词合并为一个 record，所有原始行留 lineage。空值是 `null + missing_reason`；未返回与 0 严格区分。

### Step 3｜产品/位置/适配覆盖

动态检查：core、position、material、feature、target_object、fitment/spec、language/typo、brand。

车辆/设备型号、年份、部件号、尺寸和兼容声明只有在当前确认清单支持时才可进 Listing 或正式 Exact；否则进入 `fitment_review` 或 observe。不得用高搜索量替代适配事实。

### Step 4｜污染、品牌、IP 与守恒

使用词/短语边界；核心根优先。竞品/未许可品牌进入隔离与 brand review；已确认自有品牌可用于 Title，兼容词按 S1 具体范围复核。平台禁用词保留限制依据；`blocked_claim` 标 `claim_restricted` 并保留研究记录，但退出当前可用文案与默认广告候选。广告否定另审范围、匹配方式与误伤。

每个 unique record 进入且只进入一个 disposition。守恒公式由统一 validator 复算；未知去向不得 handoff。

### Step 5｜指标语义与竞品覆盖

- `monthly_search_volume` 是月度需求信号，不改成周搜。
- `monthly_purchases` 是平台字段，不等于本品销量。
- `purchase_rate` 保留原值，semantic 必须为 `sellersprite_purchase_rate_not_amazon_ads_cvr`。
- 即使 `purchase_rate = monthly_purchases/monthly_search_volume` 可复算，它也不进入 `metrics.cvr`。
- SPR、标题密度、需供比、广告竞品数、Top3/总点击与转化份额都是平台竞争/集中度信号；不改名成 CVR、销量或利润。

共同覆盖仍只使用导出观察 ASIN 与已确认竞品池交集；Top10/相关 ASIN 不自动成为已确认竞品。

### Step 6｜六维和 Listing 分池

D1 材质/结构、D2 功能/机制、D3 场景、D4 人群、D5 对象、D6 意图逐词记录。Agents 旧版的 D1=品类/D6=属性不再使用，避免三条源线字段冲突。

先通过当前 SKU 的事实、匹配与声明许可，才进入 Title、Highlights、Bullet feature/scenario、Description/A+、ST candidates、Attributes、Q&A 可用池；未确认进入 fitment review/observe。ST 这里只给候选，最终词去重、去禁用品牌和 249 UTF-8 bytes 由 Copywriter 验证。

### Step 7｜广告候选池

待证/禁用词退出默认投放候选；按事实与用途资格输出 `exact/exact_p1/exact_p2/phrase_feature/phrase_scenario/broad_discovery/observe`、Negative 和 ASIN pools。P1/P2 是词包证据层级，不等于 Amazon 账户出单验证。

PPC bid/range 仅是外部信号。SPR、title density、purchaseRate 的任何固定阈值都标 case default 或企业规则；不能据此在词库层直接决定高价抢位、暂停或最终 Bid。

### Step 8｜Hash 和验证

生成 JSON 语法 `.yaml` 并执行：

```powershell
python ..\_keyword_system_contract\scripts\keyword_package_contract.py finalize `
  --input S2_文案板块\amazon-sellersprite-keyword-research\keyword_package.yaml `
  --expected-source sellersprite `
  --report S2_文案板块\amazon-sellersprite-keyword-research\keyword_package.validation.json
```

validator 会拦截跨源 provider、缺 SHA/month、purchaseRate→CVR 偷换、MCP 假 used、null 无原因、数量不守恒、未知 ID/lineage、Demo 无标识和 Hash 漂移。

### Step 9｜报告与局部修订

按统一八节报告展示来源/月/语义、本品资格、用途与证据。全量研究词可定位；fitment、purchaseRate 口径与数据缺口保持清楚，反方只据实际证据记录。

后补 fitment 只重算适配记录、Listing/广告池与引用 Hash；后补某月导出只新增 source/version 并重算受影响指标，不重洗其他平台词包。

## Acceptance checks

### P0

- [ ] 文件 source_id/SHA-256/as_of/month/站点/单位可追溯。
- [ ] provider 全为 SellerSprite；没有跨平台补数。
- [ ] MCP used/partial 有本轮工具、时间、参数摘要、结果和返回量。
- [ ] purchaseRate 未写入 cvr，报告未称 Amazon 广告 CVR。
- [ ] null/0、月/周、SPR/集中度/转化率均未误映射。
- [ ] 原始→有效→去重→五类 disposition→records 守恒。
- [ ] fitment、品牌和合规词未进入不允许的 Listing/广告池。
- [ ] Pool ID、lineage、package Hash 与 handoff 引用一致。
- [ ] Demo/Production、数据解释窗口和 late revision 完整。

### P1

- [ ] Unique Words/位置根覆盖、竞品交集、六维/交叉入口可复核。
- [ ] 固定机会/竞价阈值已标来源，没有伪装平台硬规则。
- [ ] 最强反方说明为什么当前词包不能直接扩大投放。

所有勾选附路径、算式或计数。

## Handoff

```markdown
【本步完成】amazon-sellersprite-keyword-research 4.0
【词包】S2_文案板块/amazon-sellersprite-keyword-research/keyword_package.yaml（package_id/version/hash）
【数据月】<YYYY-MM> / missing=<N>
【MCP】used|partial|skipped|failed（本轮证据）
【守恒】raw=<N> / unique=<N> / retained=<N> / observe=<N> / isolated+negative=<N>
【下一步】选择本包后交 Strategy；Builder 仅消费该 Hash。
【边界】purchaseRate 是 SellerSprite 平台信号，不是 Amazon 广告 CVR；本词包不授权上传。
```

## 版本记录

| 版本 | 说明 |
|---|---|
| 4.0.0 | 单源 Package 2.0；强制月份/null/Hash/MCP 真实性和 purchaseRate 防误映射；保留 fitment、Listing 与广告池；取消平级 MCP 补空和词库内最终 Bid。 |


### R02 分层与持久词库接口

S2 竞品采用同类目 BSR 1–30 / 31–60 / 61–100 的 6/6/5 个与官方新品榜3个；只以相关同类/同款补位并标来源段、补位理由、价格带状态。新品榜不可得有证据才用日期窗口回退；不足报告实际数。旧候选池、原备注保留，新分层需本表用户确认，不能继承旧20池审批或演示默认。

完整单源词包用 `keyword_package_contract.py finalize --series <真实系列根>`，通过分层/来源/完整性门禁后接入系列资产；CSV normalize 不等于入库成功。只取 `keyword_asset.py select --limit 20` 摘要与正式关键词报告链接，禁止将整个 master 读入/粘贴模型上下文。保留原指标与周/月口径，来源不能互补填空。具体接口及 CT 有限用途边界见 `../../docs/keyword_asset_contract.md`。
