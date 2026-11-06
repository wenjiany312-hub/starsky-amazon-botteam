# Keyword System Contract 2.0

本目录是三条关键词证据线共享的非触发型契约包，不是第四个关键词 Skill。

- `amazon-sif-keyword-research`
- `amazon-sellersprite-keyword-research`
- `amazon-xiyou-keyword-research`

三条线分别生成独立的 `keyword_package_<source>.yaml`，不得静默混表。本目录只统一结构、Hash、校验和交给广告闭环的字段语义。

关键文件：

- `references/keyword_package_2_contract.md`：业务与交接契约。
- `templates/keyword_package_2.template.yaml`：完整模板。
- `templates/keyword_package_2.schema.json`：结构 Schema。
- `templates/keyword_package_selection.template.yaml`：三条独立来源包进入下游前的单包选择记录。
- `templates/keyword_package_selection.schema.json`：单包选择结构与 Hash 约束。
- `templates/keyword_data_source_log.template.yaml`：逐文件/逐调用来源日志。
- `scripts/keyword_package_contract.py`：零业务依赖的 Hash 与业务校验器；PyYAML 缺失时接受 JSON 语法的 `.yaml`（JSON 是 YAML 1.2 子集）。
- `tests/test_keyword_package_contract.py`：来源隔离、守恒、指标语义、MCP 真实性、Demo 边界与 Hash 回归。

本目录必须与三个关键词 Skill 一起发布；不应单独安装或注册为 Skill。


R02 完整词包 finalize 必须传 `--series <真实系列根>`；分层竞品确认、来源文件hash及完整词包校验通过后才入 `_词库资产`，三条来源线共用 `scripts/s2_keyword_asset_adapter.py`。只取 bounded select 摘要或正式报告链接，禁止读取/粘贴整个 master 进对话。接口与 CT 有限用途边界见 `docs/keyword_asset_contract.md`。竞品按 BSR 10 + 新品榜 10 取数，一边不够用另一边的同类候选补位；不得填无关ASIN。
