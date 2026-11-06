# Amazon Ads Bulk 错误目录

版本：1.0（2026-08-09）

错误目录提供“检测与归属”，不授权自动修改后台。

| code | 现象/检测 | 常见根因 | 安全处理方向 | 责任层 |
|---|---|---|---|---|
| PLAN_HASH_MISMATCH | 计划 hash 无法重算 | 计划被改、算法漂移、选错版本 | 恢复正确版本或生成新版本；不重写历史 hash | Builder/策略 |
| PLAN_REVIEW_HASH_MISMATCH | 审核 hash 不同 | 审核后计划变化 | 旧审核 stale，重新展示差异并审核 | 总控/人审 |
| PLAN_RUN_MODE_MISMATCH | Demo plan 被按 production 调用 | CLI/manifest 试图升级模式 | 保持 Demo 永久不可上传；重新创建真实 production plan | Builder/总控 |
| PLAN_UPLOAD_ELIGIBILITY_BLOCKED | plan 自身阻止上传 | Demo 标识或 Builder blocker 未解决 | 回到 Builder 解决 blocker，不覆盖状态 | Builder |
| ARCHITECTURE_REVIEW_AMBIGUOUS | flat 与 nested 审核并存 | legacy approval 可能覆盖 canonical decision | 只保留 flat canonical 审核件 | 总控/人审 |
| ARCHITECTURE_REVIEW_INCOMPLETE | 批准态缺审核人/时间/通过 checks | 审核件只改了 status | 完成九项检查并记录带时区时间 | 人审 |
| BULK_RELEASE_REVIEW_MUST_BE_SEPARATE | architecture review 内嵌 release | 生成前与生成后审核混层 | 拆成独立 bulk_release_review.yaml，并在技术通过后审核 | 总控/人审 |
| COMPILE_CONTEXT_REVIEW_MISSING | Production campaigns 有 context 但架构审核未锁 SHA | 部署参数未审 | 将 context 内容与 SHA 展示给审核者，批准后重生成 | 总控/人审 |
| COMPILE_CONTEXT_REVIEW_MISMATCH | context SHA 与架构审核不同 | 审核后改 start/state/SKU 映射 | 旧批准失效；重审精确 context | 总控/人审 |
| COMPILE_CONTEXT_HASH_MISMATCH | Validator 收到的 context 与 manifest 不同 | 选错文件或生成后修改 | 使用生成时原文件，或新版本重生成 | Generator/Validator |
| COMPILE_START_DATE_INVALID | start_date 不是有效 YYYYMMDD 日历日期 | 缺值、格式错或如 20260231 | 明确真实启用日并重新审核 context SHA | 运营/人审 |
| TEMPLATE_HASH_MISMATCH | template_ref 与文件不同 | 选错模板、文件被改 | 重新锁定当前账户模板 | Generator |
| TEMPLATE_HEADERS_MISSING | 精确列缺失 | 大小写、Locale、模板版本不匹配 | 读实际表头/Config，修 adapter；不猜列名 | Adapter |
| HEADER_ROW_MUTATED | 候选首行与模板不同 | 插 Demo 备注、增加 trace 列、误改表头 | 重新从干净模板生成 | Generator |
| REAL_ID_REQUIRED | Update/Archive 主 ID 非数字 | 临时 ID 被用于已有实体 | 从后台快照取得真实 ID，再出新计划 | Builder |
| PARENT_CAMPAIGN_BROKEN | 临时 Campaign 无父行 | 子行引用错误/父行漏写 | 修 row-operation 图后重生成 | Builder |
| PARENT_AD_GROUP_BROKEN | 临时 Ad Group 无父行 | 组 ID 拼错或漏建组 | 修 row-operation 图后重生成 | Builder |
| PLAN_TO_BULK_ROW_MISMATCH | action 逐字段不一致 | 手改 XLSX、manifest 过期 | 不就地补；从当前计划重生成 | Generator |
| COUNT_MISMATCH | 实体/Operation 总数不守恒 | 部分行漏写、过滤类型错误 | 对账 action_id，修计划或生成器 | Builder/Generator |
| BUDGET_SUM_MISMATCH | Campaign 预算合计不同 | 漏 Campaign 行或计划漂移 | 回到计划审查预算，不在校验器改值 | 策略/Builder |
| PRODUCTION_SKU_ASIN_UNVERIFIED | Product Ad Create 缺真实子标识 | 父 SKU、占位 SKU、ASIN 未映射 | 补真实子 SKU→ASIN；Demo 则显著标记 | 用户/Builder |
| ARCHIVE_REVIEW_REQUIRED | 有 Archive 无精确批准 | 把重名修复误当归档授权 | 展示实体/action 清单后人工确认 | 人审 |
| ARCHIVE_REVIEW_INCOMPLETE | Archive 批准缺审核身份或时间 | 只填 action_id | 补非空审核人和带时区时间 | 人审 |
| BULK_RELEASE_REVIEW_STALE | 批准的 Bulk SHA 不同 | 校验后文件又变化 | 重新校验并审核精确文件 | 人审 |
| RETRY_COMPILE_CONTEXT_REQUIRED | canonical campaigns 重试缺原 context | 无法确定原 action 编译结果 | 传入 validation.compile_context_ref 锁定的文件 | Troubleshooter |
| RETRY_COMPILE_CONTEXT_HASH_MISMATCH | 重试 context 与 validation 不同 | action_id/部署参数可能漂移 | 禁止映射；恢复原 context | Troubleshooter |
| DEMO_VALUE_IN_PRODUCTION | 生产文件含 Demo marker | Demo 复用为生产 | 重新用真实标识和生产模板生成 | Generator |
| CAPABILITY_BLOCK_ADAPTER_MISSING | SB/SD 无 adapter | 误以为同一工作簿即可复用 SP | 实现类型专属适配器后再启用 | Skill 工程 |
| processing_partial_failure | 成功/失败并存 | 平台逐行处理 | 只重试失败 action；先获取成功实体真实 ID | Troubleshooter |
| processing_report_unresolved | 无法识别报告列/状态 | Locale/模板变化 | 人工确认列映射并更新 adapter，不猜 | Troubleshooter |

## 不再采用的历史修法

- 通用写入 `baby/electric/makeup` 等案例否词：跨类目污染。
- 同名 Campaign 一律自动归档：外部破坏性越权。
- 每次用时间戳新建：会掩盖本应 Update 的对象。
- Demo 在首行插备注：破坏官方表头。
- 整体上传失败就整体重传：可能重复局部成功实体。
- 看见后台 Campaign 就推断全部关键词/PAT 成功：缺处理报告证据。
