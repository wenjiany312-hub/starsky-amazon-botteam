# SP-API 只读 Python 接口

实现：`scripts/sp_api_client.py`。公开配置不含凭据。`load_config(Path)` 支持 YAML/JSON、独立 `credential_file` 和映射字段；文件里的值只供本连接使用，不写入进程环境，不借用其他连接的值。返回字典含私密数据，禁止打印或保存到快照。旧 `lwa` 嵌套凭据可读取，但 `region` 和 `marketplace_id` 必须明确，不能仅凭 `marketplace: US` 猜账号映射。

区域固定为 NA/EU/FE 对应的 `sellingpartnerapi-na/eu/fe.amazon.com`。配置声明的 endpoint 必须匹配该区域。配置只表示待验证；只有真实调用及响应验证才能证明对应域可用。

```python
config = load_config(config_path)
client = SpApiClient(config)
participations = client.participations()
sales_raw = client.sales(start_iso, end_iso, time_zone)
fba_result = client.inventory()
listing_result = client.listings(seller_id=None)
```

- `participations()`：返回原始响应字典，`payload` 是参与站点列表。
- `sales(start_iso, end_iso, time_zone)`：返回原始响应字典，`payload` 是日聚合列表。区间含首不含尾，时间戳须有时区偏移；`granularity=Day`，IANA 时区必须与边界偏移一致。响应里的销售金额、币种、订单数保持原样；不能将销售总额当作广告归因销售。
- `inventory()`：仅 FBA 库存汇总，`details=true`。不推断自配送库存，不把缺字段补零。
- `listings(seller_id=None)`：使用显式参数或配置里的已验证 seller_id；缺失时报 `seller_id_required`，不影响其他域。请求 summaries、attributes、issues、offers、fulfillmentAvailability；page_size 默认为 20，允许 1–20。

后两项返回：

```json
{
  "rows": [],
  "complete": true,
  "page_count": 1,
  "raw_pages": [],
  "error": null,
  "marketplace_id": "原始已配置站点 ID",
  "region": "NA"
}
```

真实空列表也必须来自成功响应。首个页面失败直接抛出 `SpApiError(code, status)`；后续页失败保留已取得页面，`complete=false`，`error={code,status}`。重复 nextToken、页面上限（默认 100，可设 1–1000）均视为不完整。原始页保留以供本地证据落盘；包含分页游标，不能作为公开日志输出。

只允许四类 GET：marketplaceParticipations、orderMetrics、inventory summaries、按 sellerId 搜索 Listing。唯一 POST 是 LWA refresh token 交换。认证失败最多重新授权一次，429/5xx 有界重试；长 Retry-After 返回 retry_deferred。错误只含类别和 HTTP 状态，不带响应正文、token 或请求 URL。没有广告、订单明细/个人信息或后台写接口。

传输测试注入：`transport(method, url, headers, body_bytes_or_None, timeout_seconds, max_bytes) -> (status, headers, bytes)`，必须禁止重定向。默认复用固定相邻 Ads 客户端的安全传输层；没有调用 Ads 业务接口。IANA 时区需要系统 zoneinfo 数据或 Python tzdata，缺失时返回 timezone_unavailable，禁止猜测偏移。

## 官方契约来源

- [销售日聚合与区间规则](https://developer-docs.amazon.com/sp-api/reference/getordermetrics)
- [FBA 汇总和 nextToken](https://developer-docs.amazon.com/sp-api/reference/getinventorysummaries)
- [Listing 搜索及分页字段](https://developer-docs.amazon.com/sp-api/reference/searchlistingsitems)

本轮采用虚构凭据和 HTTP 响应夹具验证；真实权限、真实字段、站点映射和限流需逐域验证。
