# S2 路径：词库 → 文案 → 固定报告

前置：S1 已确定具体 SKU、产品事实、确认竞品池和 IP/声明限制，并满足阶段交接要求。继承已确认决定；用户只改字段时不重新扩池或启动整条流程。

## 1. 接入已确认输入

读取 00C/overlay（若有）、当前事实、确认竞品池、S1 IP 与企业限制。限制在词库分类、文案用途和广告候选形成前生效，不能拖到写文案的最后一步。

竞品不足以支持某项判断时记录影响。扩池按 [竞品候选池契约](../../_keyword_system_contract/references/competitor_candidate_pool_contract.md)：入口词确认→宿主真实采集并全部登记→确定性脚本初筛→只判灰区→脚本05A表由本人确认→现有分层与确认证据门禁。不按固定数量凑池，不把未取到样本推断成细分市场狭窄。候选与已确认竞品分开；不把旧通过摘要当作门禁输入。

## 2. 选择来源与检查导表

沿用用户选择或 manifest 的 chosen_keyword_skill；仅一个来源已有导表时直接识别该来源。用户未选择来源时默认优先 SIF；不得覆盖其“西柚找词”“卖家精灵”或“仅用导表/不调用”的明确指令。

标准接入目录：

```text
S2_文案板块/_投喂_关键词导出/SIF/
S2_文案板块/_投喂_关键词导出/卖家精灵/
S2_文案板块/_投喂_关键词导出/西柚找词/
```

用户已提供其他路径时先读取并登记/映射，不要求为目录习惯重复上传。没有导表且当前授权范围内没有真实可调用来源时写 waiting_keyword_export，提供已确认 ASIN 与具体导表入口；不编词。

| 选择 | Skill |
|---|---|
| SIF | amazon-sif-keyword-research |
| SellerSprite / 卖家精灵 | amazon-sellersprite-keyword-research |
| 西柚洞察 | amazon-xiyou-keyword-research |

多来源分别保存词包。跨源数据放独立证据，不平均成同一个搜索量或 CVR。选定的 package_id/version/hash 决定正式下游引用。

## 3. 标准化、事实资格和用途

继承 Keyword Package 2.0 原始数据、来源、null、lineage 与 Hash 契约；每条词保持 keyword_id。先确认产品词根、主分类与多标签，再判 direct/adjacent/mismatch/unknown、声明许可、文案/广告用途，最后按可比指标排序。

待证/禁用词留研究记录，但退出当前可用文案池和默认广告池。已确认自有品牌可保留在标题；竞品品牌与兼容用途按 S1 具体限制复核。广告否定需范围、匹配方式和误伤检查。

场景库存在时继承其问题与 fact_refs，没有则记录缺口；不设卡片数量、inferred 比例或保留率门槛。

## 4. MCP 增量与季节窗口

检查本任务实际工具与 schema；配置、暴露、协议和业务返回分态。用户限制 export-only 时不调用。否则优先补可用高价值词，其次分歧词、季节敏感词；调用失败记录缺口、已完成范围和可重试入口。

默认尝试两个完整年度的可比历史；不足就降低结论确定性，不伪造趋势。保存原始返回、来源/日期/周期/单位/语义与脱敏调用记录。同源增量用新 source_id，跨源增量放独立 evidence/context，不覆盖主词包源值。

## 5. 写 Listing 并生成固定报告

调用 amazon-listing-copywriter：买家问题 → 本品事实 → 可支持的收益/边界。英文消费者字段、中文译文、内部待核与事实/VOC分开。Title/Highlights/五点/Description/ST 及交付的 Q&A 都进入校验。

S2 正式报告入口：

```powershell
python scripts/s2_report.py --package "<keyword_package.yaml>" --listing "<listing.json>" --context "<context.json>" --output-dir "<report-dir>"
```

只有词库时省略 listing，有基线才加 baseline。字段和参数见插件 `templates/s2_report_input.md` 与 `--help`。固定词库八节、Listing 十节；不按 KB/图表数判质量，不写产品临时 HTML 生成器。

输出 keyword_report.html、listing_report.html、listing.md、keywords.csv、report_model.json、validation.json、manifest.json，按是否有 Listing 输入生成对应视图。路由可映射到旧的 06/07 文件名，但须记录映射与指纹，不另起正文。报告只读输入；正式用途资格需写回词包、更新 Hash 后再交下游。

## 6. S2 验收与交接

| 检查 | 通过依据 |
|---|---|
| 词包 | 原始行与唯一词守恒，全部研究词可定位，来源/时间/单位/null/Hash可复算 |
| 用途 | 当前事实/IP限制在文案和默认广告池都生效，池 ID 可回到词条与依据 |
| 文案 | 完整字段或具体缺口；英文/中文分开；计数、ST去重与真实覆盖可复算 |
| 报告 | 同轮模型生成八节词库与十节 Listing，manifest 对应当前输入，关键风险可见 |
| 人工审阅 | 可选，不阻塞交付：用户审过就记录，没审就在交付卡提醒“上架前自行过一遍”；不出逐条审阅清单 |
| 下游 | S3 引用已审文案与边界；S4 只读已选 Hash，广告执行仍独立授权 |

产物就位、模板通过、业务审核与外部发布分别记录。任何未通过项说明影响和继续入口；不以完整版式掩盖事实缺口。

## 每步用原生命令（不要另写核对脚本）

第 2 步 `competitor_candidate_pool.py` + `s2_competitor_gate.py`；第 3 步 `s2_export_card.py card|intake`；第 4 步 `sif_clean.py --run`（卖家精灵/西柚按各自 Skill）；第 5 步 `s2_ct_mapping.py`；第 6 步 `s2_report.py --listing`；第 7 步 `s2_compliance_gate.py`。原生命令自带来源、哈希和状态核对：不要另写“核对原表/保护项/最终核验”脚本，也不要为了绕过报错改已安装插件。原生命令报错时按报错里的下一步做；报错没说清的，用一句话告诉用户并停下。
