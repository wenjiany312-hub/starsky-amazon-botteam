---
name: amazon-sp-api-connector
version: "1.2.0"
edition: starsky-v2
description: >
  亚马逊 SP-API 连接器（读全域 + 文案单 SKU  gated 推送）。本版只用 LWA，不要求 AWS keys。
  与 Amazon Ads API 分域：本 Skill 不读取广告表现，也不替代用户已选的广告数据来源。
---

# Amazon SP-API 连接器（读 + 文案推送）

## 什么时候用

- 用户已经能在卖家中心申请 SP-API / LWA
- 需要读取或和领星并列校验 Marketplace、销售日聚合、Listing、FBA
- 没有领星、但有官方只读授权时，作为 S5 第二条数据管道

## 前置（LWA，不要 AWS keys）

1. 在 Amazon 开发者控制台创建 SP-API 应用，拿到 LWA `client_id` / `client_secret`。
2. 完成卖家授权，拿到 `refresh_token`。
3. 从 `references/sp_api_config.template.yaml` 复制本机指针配置到 `~/.starsky-codex/config/sp_api_config.yaml`，用 `credential_file` 指向私有 `.env`；美英连接分别映射各自的 refresh token、endpoint、marketplace ID。不要把真实值发到对话或公开包。
4. 确认授权范围包含目标站点。使用 `scripts/sp_api_client.py` 的 `participations()`、`sales()`、`inventory()`、`listings()` 分域读取；通道成功不等于其他域可用，按域单独记 `callable` / `data_gap`。seller_id 未验证时仅阻止 Listing，禁止猜测。
5. 先读 [Python 接口与完整性契约](references/python-client.md)。分页结果只有 `complete=true` 才能作为完整快照；销售区间含首不含尾，保留实际币种及站点时区。

旧说明里「必须填 AWS Access Key」已经作废。本版禁止再向用户索要 AWS keys。

## 安全

- 配置只放本机 `~/.starsky-codex/config/`。
- 公开包、课程包、GitHub 不得带真实 refresh token。
- 日志脱敏。文件存在不等于本轮验证通过。

## 和领星的关系

| 数据 | 本期主源 | SP-API |
|---|---|---|
| 广告花费 / 广告归因 | Ads API、领星或用户导出三选一 | 不提供 |
| 订单 / 销售 / 库存 / Listing | 领星或 SP-API | 并列校验，不静默替换 |
| 诊断知识 | 广告知识库 MCP | 无 |

## 文案一键推送（2026-09-17：B店铺实测需求）

读永远随便读；**写只有一条**：`scripts/push_listing_copy.py` 把已复核的 07 文案 PATCH 进现有 Listing（标题/五点/长描述/后台关键词四个白名单字段）。

硬门（缺一就停）：文案推送门已过（`reviewed_by + reviewed_at`）→ 用户明确回“确认推送” → 默认先 dry-run 看线上/待推 diff → 单 SKU 先行 → 回执核对 issues → 回滚用旧值再跑一次。批量多 SKU 逐个来，不许循环自动全推。

```text
python scripts/push_listing_copy.py --config <sp_api_config.yaml> --seller-id S --sku SKU --product-type PT --marketplace <ID> --patch-json <07转出的patch数组.json> --reviewed-by <复核人> --reviewed-at <YYYY-MM-DD> [--confirm] [--log <run/...jsonl>]
```

## 授权范围探针（2026-09-17：A店铺“接通了但不知有何权限”时用）

在本机跑，只报通/禁，不打印密钥：

```text
python scripts/probe_sp_api_scopes.py --config <sp_api_config.yaml> --seller-id S --marketplace <市场ID>
```

逐域实测 participations/sales/inventory/listings-read/listings-write（干跑），输出 ok/forbidden/auth_failed。缺的角色去开发者控制台 Roles 页补勾，不用猜；401 先修凭据。

## 本 Skill 不做什么

- 推送门之外不写 Listing、不改库存、不改广告
- 不以 SP-API 授权或调用成功宣称 Amazon Ads API 已获批、已授权或报表可用
- 不把 Marketplace smoke 文件存在当成业务域已通
- 不把本机 D 盘沙龙产物拷进插件包
