# Amazon Ads 平台事实边界

来源基线：用户本地保存的 Amazon Ads 官方学习快照，获取日期 2026-08-09。它用于纠正历史课程中的平台陈述；生产运行仍以当日官方文档和当前账户控制台/导出为准。

## 分类标签

- `platform_current_fact`：当前官方明示的平台事实。
- `official_recommendation`：官方通用建议，不是所有账户的硬门槛。
- `account_verified`：当前账户界面、资格或导出实测。
- `case_default`：课程/企业案例参数。
- `inference`：运营推断，必须验证。

## 边界矩阵

| 主题 | 可保留事实/建议 | 生产需核验 | 禁止硬编码 |
|---|---|---|---|
| SP | Auto、Manual keyword/product、negative；Auto 可含 close/loose/substitutes/complements | 账户/站点/ASIN eligibility、Featured Offer、当前 targeting 控件 | 固定 8 Campaign、所有新品固定同一顺序 |
| SB | 可服务认知、考虑、获客、忠诚、销售；支持关键词、商品/类目等定位；资格涉及 Brand Registry 等 | 当前品牌资格、格式、落地页、素材、theme targeting 与否定能力 | “品牌资格不是闸门”“SB 不探索”“只能吃 SP 已验证词” |
| SD/display | 可与 SP 形成不同漏斗触点，contextual/audience 能力按账户提供 | 当前站点资格、contextual/audience/video、计费与归因 | “首轮一律不开”“新品唯一有效打法是竞品 ASIN” |
| 时机 | 不同广告产品对应不同目标，可组合测试 | 利润、库存、目标、素材、预算、测量能力 | “必须先 SP 跑 14–30 天才允许 SB/SD” |
| 匹配 | SP keyword 常见 broad/phrase/exact | 当期语义匹配行为、SB/SD 实际选项 | Exact 永远必须最高 Bid、固定词数 |
| 否定 | SP 官方材料明确 negative phrase/exact 和 Campaign/Ad Group 层级 | SB/SD 支持层级、当前控制台 | 10 clicks 0 单必否、3 单必迁移、跨类型无条件同步 |
| 日预算 | 预算按月平均；账户可能允许高流量日超过平均日预算 | additional-spend 设置、币种和预算规则 | 把设置值当严格单日封顶；固定美元预算 |
| 动态竞价 | SP up-and-down 可按实时信号提高/降低 | 当前最大 uplift、placement 与策略叠加方式 | 新品前两周一律 up-and-down |
| Placement | SP 可区分 TOS/ROS/Product Pages；调整上限与规则会变 | 当前站点上限、off-Amazon/其他控制项 | 固定 TOS/PP 百分比或声称基础 Bid 就是最大 Bid |
| 测量 | KPI 应由 awareness/consideration/purchase/loyalty 等目标决定；报告维度依产品而异 | 归因窗、NTB、view-through、报表列定义 | 按客单价自行选择 7/14 天归因 |
| Search term impression share | 账户级、按搜索词和日期的广告展示份额/排名信息 | 当前报告列、市场、时间粒度 | 当成自然份额、Campaign 份额或销量线性因果 |
| SB creative | Collection、Store Spotlight、Video 等格式和落地页能力 | 当前最低 ASIN/子页、时长、比例、编码、审核 | Product Collection 永久固定 ASIN 数；SBV 永久固定比例；Video 一定更优 |
| Bulk | Amazon 提供批量操作能力 | 当前账户刚下载模板、sheet、字段、枚举 | 永久固定表头；永久要求 SP/SB/SD 三个工作簿 |

## 官方快照污染与冲突

- 预算旧页的 25% 表述与更新页的 25%/100% 账户设置存在版本差异，采用较新页面并在账户核验。
- 通用 keyword 页面提到的否定类型可能比 SP 专门指南更宽，生产按具体广告产品与控制台核验。
- 个别自动定向示例有串位、动态竞价页含占位文本、SB 页面含抓取噪音；这些段落不进入规则。

## 账户验证清单

任何生产计划至少记录：marketplace、account/portfolio 范围、ad product eligibility、当前报表归因列、预算额外花费设置、竞价与位置叠加规则、当前下载模板 hash。未核验的能力可以形成策略草案，但不能声称“账户可执行”。
