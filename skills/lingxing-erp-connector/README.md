# lingxing-erp-connector

领星 ERP OpenAPI 数据连接 Skill,用于通过官方服务端接口拉取店铺列表、Listing、FBA 库存、订单/销量、广告活动和广告搜索词等数据,给亚马逊店铺巡检、断货预警、广告健康周检和利润分析提供后台数据源。

## 适用场景

- 用领星 ERP 数据做店铺日检/周检
- 拉取 FBA 库存并生成断货预警
- 拉取广告活动数据并生成广告健康报告
- 给多 Agent 或自动化巡检流程提供稳定的数据后端

## 安全边界

- 本 Skill 只调用领星官方 OpenAPI,不登录亚马逊卖家后台,不操作浏览器指纹环境。
- 凭据只允许写入本地 `lingxing_config.yaml`。
- 分享、提交仓库、课程演示时必须使用空模板,不得包含真实 AppID、AppSecret、店铺 sid、Seller ID 或原始接口返回数据。
- 接口不可用、权限不足或凭据未配置时,必须如实标注“领星数据不可用”,不得模拟数据。

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

也可以使用已有 Python 环境,只要已安装 `requests`、`pyyaml`、`pycryptodome`。

## 配置

1. 登录领星 ERP,用超级管理员账号进入:设置 -> 业务配置 -> 开放接口。
2. 在开放后台的 API 信息页获取 AppID 和 AppSecret。
3. 在开放后台配置 IP 白名单。请使用国内直连 IP 查询服务获取当前出口 IP,例如:

```powershell
curl.exe --noproxy "*" http://ip.3322.net
```

4. 复制模板并填写凭据:

```powershell
Copy-Item .\references\lingxing_config模板.yaml .\lingxing_config.yaml
notepad .\lingxing_config.yaml
```

> 填好的 `lingxing_config.yaml` 放在本 Skill 目录即可：重跑插件 `一键安装.bat` 升级时不会覆盖或删除它。

## 冒烟测试

```powershell
python .\scripts\lingxing_client.py --smoke
```

成功标志:能获取 access token,并打印店铺列表。随后把需要巡检的店铺 `sid` 填入 `lingxing_config.yaml` 的 `stores`。

## 常用脚本

```powershell
python .\scripts\lingxing_client.py --smoke
python .\scripts\fba_stock_alert.py
python .\scripts\ads_health_check.py
python .\scripts\daily_patrol_data.py
python .\scripts\feishu_push.py          # 可选:把当天 findings 推到飞书群
```

## 推送到飞书(可选)

不配 `feishu.webhook` 就不推送,巡检本身照常跑。

1. 飞书群 -> 右上角设置 -> 群机器人 -> 添加机器人 -> 自定义机器人 -> 复制 webhook
2. 填入 `lingxing_config.yaml` 顶层 `feishu` 段(开了签名校验的把密钥填 `secret`)
3. 验证:`python .\scripts\feishu_push.py --test`

顺序是**先取数、后推送**:巡检脚本落了 findings,再跑 `feishu_push.py`。
`--dry-run` 只打印卡片不发送(不需要 webhook)。

报告按 V2.1 目录契约落到产品根下的 `S5_运营板块\lingxing-erp-connector\erp_extract\`。脚本按四级优先级找这个目录:`lingxing_config.yaml` 顶层 `report_root` → 环境变量 `STARSKY_PRODUCT_ROOT` → 当前工作目录(或上级)本身就是产品根 → 兜底 `用户目录\starsky_runs\店铺巡检\`(命中兜底会打印提示)。定时任务建议显式配置:

```yaml
report_root: "D:\\产品\\犬用雨衣\\S5_运营板块\\lingxing-erp-connector\\erp_extract"
```

或在计划任务里设置环境变量 `STARSKY_PRODUCT_ROOT=D:\产品\犬用雨衣`,日志会同步落到该产品的 `S5_运营板块\lingxing-erp-connector\运行数据\logs\`。

## 分享前检查

- `lingxing_config.yaml` 为空模板或不随包分发
- `feishu.webhook` / `feishu.secret` 为空(webhook 泄露=任何人能往你的群发消息)
- `.gitignore` 已排除凭据、缓存和原始数据
- `scripts/__pycache__/` 不打包
- 没有真实 AppID、AppSecret、Seller ID、店铺 sid
- 没有个人电脑绝对路径
- 没有原始 API 返回 JSON
