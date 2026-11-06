# 可选成本数据

成本未配置或缺项只影响成本估算，不阻止销售、广告等其他看板。原 XLSX 只读，使用 openpyxl 的 read_only=True、data_only=False；不修改工作簿、不读取缓存计算结果。公开模板为空 CSV，不含任何真实店铺资料。

## 导入

`load_costs(path, store, market)` 返回 `state, rows, sha256, warnings, parameters, input_summary`，同时保留绑定的 store/market/currency。market 支持 US、UK（GB 归一为 UK）。每条成本精确按 store + market + SKU 关联，不通过 ASIN、商品名称或近似 SKU 猜配。

现有中文 XLSX 应含「成本表」「参数」工作表。读取采购、头程、FBA、其他费用、佣金率等输入；参数提供人民币对站点币汇率。单个 XLSX 只允许一个原店铺标签，由调用者明确绑定到 store；保留原标签与行号。多个标签拒绝导入。CSV 每行必须显式填写 store、market、currency，且与调用参数完全匹配。重复 SKU 成本禁止择一使用。

其他费用留空按 0，行级 warnings 保留 `other_fee_blank_assumed_zero`。采购、头程、FBA、佣金、汇率等必需字段留空不会变成 0。目标利润率或姿态缺失只使目标 ACOS 不可用；不会擅用类目默认佣金或默认利润率。

CSV 表头各费用为单件口径，purchase_cny/freight_cny 为人民币，fba_fee/other_fee/listed_price 为站点币。fx_cny_per_currency 表示 1 站点币等于多少人民币。百分率用小数；vat_rate/digital_service_fee_rate 不适用时明确填 0。posture_adjustment 可为负数。没有示例 SKU 或默认财务值。

## 计算模型

采购和头程成本 = 实际当日件数 ×（采购人民币 + 头程人民币）÷ 本表汇率。

FBA 与其他费用 = 实际当日件数 × 单件费用；佣金 = 当日实际销售额 × 本 SKU 佣金率。英国沿用原表：销售 VAT = 含税销售额 × VAT率 ÷（1 + VAT率）；数字服务费 =（佣金 + FBA）× 数字服务费率。数字服务费不能改称费用 VAT。美国原表没有这两项，保留该来源模型，不将它扩展为其他市场的税务事实。

英国每日实际销售必须显式给出 `sales_tax_basis`。`gross_including_vat` 表示已核对为含税销售，按上述原表模型扣销售 VAT；`net_excluding_vat` 表示已核对为不含税销售，不再扣销售 VAT，但必须另给 `commission_base_sales`（该 SKU 当日已核对的佣金计费销售基数），否则返回 `commission_basis_unverified`。该基数不会通过净销售自动反算或 gross-up。默认或显式 `unverified` 返回 `vat_basis_unverified`，不产生全店估算利润。Sales API 的货币总计字段本身不证明税口径。

SKU 广告前贡献 = 销售额 − 上述成本费用。店铺广告费仅在每日汇总扣一次，不按 SKU 重复扣，不把广告归因销售替代店铺实际销售。

导入还根据售价计算单件毛利、盈亏 ACOS 与目标 ACOS；不采用原表计算列缓存。目标 ACOS = 盈亏 ACOS − 目标利润率 + 姿态调整，并按本表上下限夹取。中文 XLSX 的毛利公式必须符合已识别模型，否则该行标为缺数据，防止公式改动被静默忽略。

## 每日估算接口

`estimate_daily(costs, sku_sales, ad_daily)` 返回每日汇总行列表。

- sku_sales 每行必须是已汇总的 SKU/日期数据：market、sku、date（YYYY-MM-DD）、currency、sales、units。store 可选，省略时由调用者绑定 costs.store；显式不一致则阻止估算。complete=False 也会阻止。
- ad_daily 每行必须是已汇总的店铺/日期广告总额：market、date、currency、spend；store 同上。同一天只能一条，不允许把活动、广告组、搜索词的重叠层级相加。
- 两类输入缺失、同日重复、币种不一致、未知 SKU 或必需成本不全，estimated_profit 留 null。返回 covered_contribution、cost_coverage_sales、cost_coverage_sku_rows 与逐 SKU 缺口；部分覆盖不能当成全店净利润。
- 已明确当日 units=0 且 sales=0 的 SKU 标记 zero_activity，其按件和按销售额计算的成本可确定为 0，不因未维护成本或未知税口径阻止其他 SKU 的估算。账号/币种不一致、重复记录等仍阻止。units>0 即使 sales=0 仍须完整成本。
- 没有当日 SKU 销售不会从「近30天销量」推算，也不会以当前售价乘销量冒充实际当日销售额。

输出仅为估算贡献及扣广告后的估算利润，并非结算实收。退款、仓储、其他税费及未提供损耗未逐笔对账；空其他费用按 0 的假设始终披露。英国调用方未核对的日销售税口径须保持 unverified。成本表自身的单件毛利仍可以沿用表内含税售价模型计算，但不能用它证明 API 的实际日销售税口径。每天使用当前成本参数，不声称还原历史批次实际成本。
