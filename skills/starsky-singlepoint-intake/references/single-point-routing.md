# 单点情景路由、统一资料接入、MCP 补数与按需续调规则

本参考文件服务于 starsky-singlepoint-intake。它把“我只想改一个地方”的自然语言请求，转成一个可持续扩展的单点任务；用户可直接投喂未整理资料，先形成 00C 资料接入与优化建议，再进入具体 Skill。

## 1. 固定处理顺序

1. 全链 / 单点的判别与选择卡由入口 `starsky-amazon-agent` 完成；到本 Skill 时 `entry.mode` 已是 `single_point`。若 state 里没有这个值，交回入口，不猜测、不自己出卡。
2. 单点时，识别 task、target_skill 与一个启动锚点。锚点可以是产品目录、ASIN/SKU、链接、现有文案、广告报表、本品图片或产品方向；任何未整理资料都可同时接入。
3. 有用户原始资料时，先只读建立 00C_资料接入与优化建议报告.md 与 00C_data_intake_overlay.yaml：分类、保留 unclassified、区分当前事实/历史参考、提出推荐 Skill 与 MCP 补数计划。没有资料时记录 data_intake.status=not_requested，不阻断。
4. 再读取 workflow_state.yaml、各板块 Skill 夹里的既有产物、00_产品资料/ 和本轮外接资料；每个来源写入 00_总控/starsky-singlepoint-intake/00B_external_reference_manifest.yaml。
5. 写 00B_单点任务接入单.md，并立即把任务交给 target_skill。不要等待“资料齐全”。
6. target_skill 先读取 00C、00B、既有产物和 mcp_refresh，再按本任务调用已接通的 MCP 补充市场、页面、竞品、关键词、1688 或其他可获取数据。
7. 只有 MCP、00C 来源、已有文件与用户原话都无法取得、且该事实确实影响当前结论时，才追问用户；非关键缺数写为条件性结论或 demo_default。
8. entry.routing_status 写为 active。任务结束后保留 single_point_progress，向用户展示可继续调用的 Skill，但不自动串联。

外部资料默认只读。当前 run 的接入单、manifest、MCP 日志和 workflow_state 只写到产品目录，不写回用户的历史资料夹。

## 2. 路由矩阵

| task | 常见用户说法 | target_skill | 启动锚点（任选一个） | MCP 补数与事实边界 |
|---|---|---|---|---|
| market_research | 只选品、这个类目能不能做、只要市场报告 | amazon-market-research | 站点+方向、类目、参考 ASIN 或链接 | 补市场、竞品、价格和需求；预算/供应链可后补 |
| listing_optimization | 只改 Listing、老品文案没转化、只优化问题 | amazon-listing-copywriter | ASIN、SKU、详情链接或现有文案 | 补页面、竞品、关键词、可验证卖点；不把未经确认的参数写进前台 |
| ad_diagnosis | 广告烧钱、优化广告、看报表 | amazon-ad-report-analyzer | SP/SB/SD 广告报表 | MCP 补词、竞品、类目、竞价参考；真实花费、订单和 ACOS 仍以广告报表为准 |
| ad_architecture | 新建广告、搭 SP 结构、广告从零开始 | amazon-ad-campaign-builder | ASIN/SKU、产品链接、目录或方向 | 补词、竞品、市场信号；预算/利润可标注默认值，缺真实 SKU 只出不可上传演示版 |
| image_optimization | 只改图、主图不好、做七图 | amazon-listing-image-designer | 本品图、ASIN、详情链接或产品目录 | 补页面事实、竞品视觉和关键词；缺本品素材只出方案/提示词 |
| product_video | 只做视频、做 SBV/A+ 视频、做分镜 | amazon-product-video-director | 本品图、ASIN、详情链接或产品目录 | 补产品事实、场景和视觉资料；缺可用本品图只出分镜/投喂包 |
| keyword_research | 只做词库、清洗词表、做 keyword package | amazon-sif-keyword-research、amazon-sellersprite-keyword-research 或 amazon-xiyou-keyword-research | 方向、竞品 ASIN、链接或词表 | 按导出文件来源选择；对应 MCP/API 有则补词与指标；缺市场报告只影响覆盖边界说明 |
| ip_compliance | 只做 IP、词能不能用、图片会不会违规 | amazon-ip-risk-assessment | 产品名、关键词、文案、图片或链接 | 补商标词、品牌、声明和图片风险；生产级产品 IP 决策仍需硬闸门 |
| supplier_evaluation | 只找供应商、评估 1688 | alibaba-supplier-evaluation | 1688 链接、供应商候选或产品方向 | 优先本地 1688 MCP 拉取商品/供应商信息；报价表可后补 |
| difficulty_check | 这个词竞争大吗、新品能进吗、运营难度、评论区间、广告效率 | amazon-competition-difficulty-assessor | 一个核心词 + 站点；可附竞品导出表或 ASIN 列表 | 三级取数：Sorftime/SIF/卖家精灵 MCP → 用户导出表 → 列字段等回填；阈值读 templates/s1_thresholds.yaml；只出双法判定与保守销量，不出立项终裁 |
| competitor_authenticity | 这竞品销量是真的吗、是不是刷的、他的打法能不能复制 | amazon-gray-tactic-detector | ASIN、链接或竞品表 | 只判读公开信号（刷/合并/烧广告/品牌/侵权），不提供任何操作；侵权终裁仍归 amazon-ip-risk-assessment |
| seasonal_calendar | 现在备什么节日的货、Q4 档期、万圣节做什么、季节选品 | amazon-seasonal-selection | 站点 + 当前月；可附类目 | 档期读 templates/seasonal_calendar.yaml；去年同月用 MCP 或用户导出验证；出候选后【等你】选方向，再交 market_research |
| variant_plan | 变体怎么设计、做差异化、礼品还是入门套装、包装怎么做 | amazon-variant-packaging-planner | 产品方向、ASIN 或现有 S1 产物 | 读 profile / product_facts / 黑名单；变体级销量只在有数据时写，否则标未验证；作 PRD 与文案上游 |

广告类必须二分：已有广告报表走 ad_diagnosis；没有跑数、要建结构走 ad_architecture。用户同时想改 Listing 和图片时，先问他想先调用哪个 Skill；完成第一个后可按需继续调用第二个。

选品类同样二分：问「这个词 / 这个品新品能不能分到单」走 difficulty_check（阈值法，单点即可）；问「这个类目 / 方向能不能做」走 market_research（需要完整市场穿透）。difficulty_check 完成后可按需续调 competitor_authenticity 或 market_research，不自动串联。

## 3. MCP 补数契约

本套运行按 MCP 已接通的前提执行。入口不再集中检查安装，也不要求用户先准备“完整资料包”。每次单点任务的目标 Skill 应：

1. 从启动锚点定位对象。
2. 先读取 00C 报告/Overlay、已有报告、图片、页面与 state。
3. 应用 00C 的事实边界、品牌/合规/视觉/广告约束与推荐来源。
4. 选择与问题相关的 MCP 拉取补充数据。
5. 将实际调用写入 mcp_refresh.call_log：用途、MCP 名称、目标对象、返回字段、来源证据、状态和对下游的影响。
6. 不能调用或没有返回时，写 skipped 或 unavailable 的真实原因，继续使用已有证据或只向用户索取不可替代的缺口。

MCP 的作用是补数而非替代真实专属后台事实。例如：

- 需要优化广告的真实花费、订单、ACOS 和搜索词表现，必须来自广告报表或等价的真实后台数据。
- 图片和视频的最终成品仍需要可用本品素材；MCP 不会把竞品图变成用户本品图。
- IP、真实上传、发布、支付、登录/验证码等硬闸门不因 MCP 接通而取消。

## 4. 用户按需续调 Skill

single_point_progress 维护当前单点的连续调用，而不是把用户锁死在第一个 Skill。每次完成后必须显示：

- 当前已完成的 Skill 与可复用产物。
- 可由用户选择的下一步 Skill。
- 每个候选 Skill 会用 MCP 补什么，以及是否存在新的硬闸门。
- 一条可复制的继续口令。

用户可说：

    继续当前单点任务，调用 amazon-listing-image-designer。
    先读 00B 接入单、外接资料清单、已有 Listing 产物和 MCP 日志；
    只优化主副图，并用 MCP 补充可验证的页面/竞品信息。

处理该口令时：

1. single_point_progress.user_requested_next_skill 写入用户选择。
2. 先复用 00C Overlay、references、数据解释窗口和已完成产物，不重新索取机器已经拿到的字段。
3. 把新 Skill 设为 active_skill，记录为 mcp_refresh 的新一轮请求。
4. 完成后把它追加到 completed_skills，继续等待用户选择或结束。

用户未明确选择时，不能自动把 Listing 接到图片、广告接到 Bulk 或图片接到视频。

## 5. 资料引用字段

每条 references 记录至少包含 source_id、source_type、path_or_url、read_status、extracted_fields、allowed_use、affected_outputs 和 notes。

常见 source_type：

- existing_report：已有市场、IP、词库、文案、视觉或广告报告
- listing：当前标题、五点、A+、后台词、前台截图或详情页
- ad_report：后台导出的 xlsx 或 csv
- product_image / product_video：本品实物、1688 或现有 Listing 素材
- asin / sku / url：用于定位产品或外部页面
- mcp_result：MCP 实际返回的结构化数据或页面摘录
- keyword_export / voc / supplier_file：业务补充资料

read_status 只能写实际状态：read、partial、unreadable、missing 或 deferred。不能把未读取来源或 MCP 推断标成已验证事实。

## 6. 缺数和交接边界

依照 templates/report_depth_and_demo_policy.md：

- 缺产品事实：仍可做结构审计、问题清单、文案/视觉方案，但不把未证实参数写进消费者可见声明。
- 缺图片：图片/视频任务仍可出设计或生产包，不称已生成最终图片或视频。
- 缺广告报表：不能做真实跑数诊断；可改走广告架构或先列出需导出的报表。
- 缺真实 SKU：可做演示广告架构或演示 Bulk，必须显著标示不可上传。
- IP、真实上传、对外发布、登录/验证码等硬闸门由目标 Skill 持有；入口不越权放行。

每个 handoff 结尾使用以下四标签：

    【本步完成】<当前 Skill>
    【下一步 Skill】<用户选择的下一个 Skill，或 END>
    【推荐口令】<带已读资料、MCP 补数目标和当前目标的可复制中文指令>
    【MCP】used: <实际调用> / skipped: <未调用及原因> / not_required

## 7. 单点接入单最小结构

00B_单点任务接入单.md 至少有八段：

1. 资料接入结论：00C 路径、原始资料范围、可用信息、约束和推荐 Skill。
2. 选择结果：task、target_skill、启动锚点、产品目录和目标。
3. 已读取资料：逐项列路径/链接、读取状态和可用范围。
4. 自动提取与 MCP 补数：分别列证据、返回字段和不可确认字段。
5. 剩余缺口：分为当前硬缺口、可后补和只影响前台声明的字段。
6. 数据解释窗口：已获取事实、推导、模拟、影响、继续与修订。
7. 连续调用状态：active_skill、completed_skills、可选下一 Skill 与用户选择。
8. 交接：下一步 Skill、口令、MCP 说明和 acceptance_checks。
