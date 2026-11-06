# 数据底座 · SP-API 新建 Listing 模块规格 v0.1（P2）

> 给：星空旗舰 AI 智能广告终端开发（实现方不是团队包）。日期：2026-11-05
> 结论：**亚马逊 SP-API 允许直接新建 Listing**（Listings Items API `putListingsItem`）。现在做不到，是因为终端还没写这个模块，不是亚马逊限制。

## 0. 亚马逊那边的前提（逐条核对，缺一条就做不了）
| # | 前提 | 怎么查 / 怎么补 |
|---|---|---|
| 1 | 开发者资料和 App 都勾了 **Product Listing** 角色 | 终端现在能用 PATCH 改五点，说明大概率已有；新建前再用 VALIDATION_PREVIEW 跑一次确认 |
| 2 | 品类不受限 | `getListingsRestrictions`（Listings Restrictions API）：返回 APPROVAL_REQUIRED 就先去后台申请类目 / 品牌授权 |
| 3 | 有 UPC/EAN，或品牌 + 产品类型已获 GTIN 豁免 | 同一个接口带 `brand` + `productType` 时会检查 GTIN 豁免，符合条件的可以自动批准 |
| 4 | 品牌：没备案的新品牌，新建 ASIN 可能报 5461（要先申请品牌授权，有的批准只给 96 小时建品窗口） | 品牌备案是最稳的路 |
| 5 | 图片是**亚马逊能公开访问的 https 地址**（S3 / CloudFront 或公开服务器） | 本机图片要先上传到图床，见第 3 节 |

## 1. 新命令
```
terminal.py listing create --pack "<上架资料包目录>" --check     # 只校验：取产品类型定义 → 组装 → VALIDATION_PREVIEW，不落库
terminal.py listing create --pack "<上架资料包目录>"             # 生成「新建 Listing 审核报告」，返回链接（不提交）
terminal.py listing status --sku <Seller SKU>                  # 回读：状态、ASIN、问题清单
```
提交只发生在审核报告页：老板本人输入「确认推送」→ `putListingsItem` → 自动回读。团队（Bot）只能用 `--check` 和生成报告。

## 2. 流程
1. **读资料包**：`listing.json`（标题、五点、描述、搜索词、品牌、产品类型、属性、价格、数量、配送方式、物流模板）、`images/`（主图 + 副图，已过出图工三项复检和 AI 人物标记）、`gtin`（或豁免说明）
2. **限制检查**：`getListingsRestrictions`（有 ASIN 时按 ASIN；新 ASIN 按 brand + productType）→ 受限就停，报告写原因和申请链接
3. **取 schema**：Product Type Definitions API `getDefinitionsProductType`（marketplaceIds=ATVPDKIKX0DER、requirements=LISTING、带 sellerId 以拿到物流模板枚举）→ 本机缓存 7 天
4. **组装**：把资料包映射成 attributes（`item_name`、`brand`、`bullet_point`、`product_description`、`generic_keyword`、`externally_assigned_product_identifier` / `supplier_declared_has_product_identifier_exemption`、`main_product_image_locator`、`other_product_image_locator_1..8`、`purchasable_offer`、`fulfillment_availability`、`merchant_shipping_group`、`condition_type`…）；按 schema 本地校验必填、枚举、长度
5. **预校验**：`putListingsItem?mode=VALIDATION_PREVIEW`，同步拿到错误，不落库
6. **审核报告**：逐项展示「将要提交的值」+ 预校验结果 + 风控官 G2 结论 + 质检官结论；有错误不能确认
7. **提交**：老板确认推送 → `putListingsItem`（requirements=LISTING）；记录 submissionId
8. **回读**：每 5 分钟 `getListingsItem`（includedData=summaries,issues,attributes），最多 2 小时：拿到 ASIN 和状态（DISCOVERABLE / BUYABLE）、issues；写回团队台账 `upload_receipt_ref`
9. **FBA 品**：可售后再走 FBA 入库（不在本模块）

## 3. 图片托管
- 亚马逊只接受它能公开访问的地址，不能直接传本机文件
- 建议：老板自己的 S3 / CloudFront（桶只放上架图，文件名用 SKU + 序号 + 内容 hash），提交后保留 30 天
- 密钥同样只在接入向导里填；报告里只显示图片缩略图和地址，不显示密钥

## 4. 红线
1. 新建只走审核报告 + 老板本人「确认推送」，Bot 不能触发提交
2. **只用 putListingsItem 新建**；已有 SKU 一律拒绝 put（put 会整条覆盖，没带的属性会被删掉），已有 Listing 只能走现有的 PATCH 文案链路
3. 一次报告最多 5 个 SKU；第一次真实提交只放 1 个
4. 变体（父子体）第二期再做：先父后子，`parentage_level`、`variation_theme`、`child_parent_sku_relationship`
5. 提交记录、回读结果进终端的操作日志，可追溯

## 5. 团队侧改动（团队包，模块上线后改）
- `config/data_sources.yaml`：`listing_create: manual_sc` → `hub_review`
- SKU 状态机：PREVIEW → 闸门 2 → `upload_channel = hub_api` → SUBMITTED（回执 = 回读结果）
- 文案算法师交付物增加 `listing.json`（按本规格字段），创意美工 / 出图工的图片按 `images/` 命名

## 6. 验收
- 用 A 店 1 个真实新品：`--check` 0 错误 → 报告 → 确认推送 → 2 小时内回读到 ASIN 和 BUYABLE（FBM）或 DISCOVERABLE
- 对已有 SKU 执行 create 必须被拒绝
- 受限品类返回 APPROVAL_REQUIRED 时停在第 2 步，报告给出申请链接

## 参考（亚马逊官方文档）
- Create or fully update a listing：https://developer-docs.amazon.com/sp-api/docs/create-a-listing
- Submit Listings data（图片、价格、库存、变体、物流模板）：https://developer-docs.amazon.com/sp-api/docs/submit-listings-data
- Listings Items API 用例（VALIDATION_PREVIEW）：https://developer-docs.amazon.com/sp-api/docs/listings-items-api-v2021-08-01-use-case-guide
- Listings Restrictions：https://developer-docs.amazon.com/sp-api/docs/get-listings-restrictions
