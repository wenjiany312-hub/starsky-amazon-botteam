# 分享前检查清单

- [ ] `lingxing_config.yaml` 没有真实 AppID/AppSecret
- [ ] `lingxing_config.yaml` 的 `feishu.webhook` / `feishu.secret` 为空
      （webhook 泄露=任何人都能往你的飞书群发消息）
- [ ] 没有真实店铺 sid、Seller ID、店铺名称或原始接口数据
- [ ] `references/巡检配置.yaml` 不随包分发（含真实 ASIN 和 sid；只发 `巡检配置模板.yaml`）
- [ ] 没有个人电脑绝对路径
- [ ] `.gitignore` 已排除凭据、缓存、报告和原始 JSON
- [ ] `scripts/__pycache__/` 没有打进压缩包
- [ ] 新用户可按 README 安装依赖
- [ ] 新用户可复制模板并运行 `python .\scripts\lingxing_client.py --smoke`
