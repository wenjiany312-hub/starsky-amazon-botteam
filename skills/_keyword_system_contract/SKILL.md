---
name: _keyword_system_contract
version: "1.0.0"
edition: starsky-v2
description: >
  三条词库线（SIF / 卖家精灵 / 西柚）共享的 Keyword Package 2.0 契约、Schema 与 Hash 规则。
  不要单独当业务 Skill 调用。跑词库清洗前必须先读本目录 references。
---

# Keyword Package 共享契约

本目录不是业务入口。权威文件：

- `references/keyword_package_2_contract.md`
- `templates/` 下的 schema 与 template
- `scripts/keyword_package_contract.py`

由 `amazon-sif-keyword-research`、`amazon-sellersprite-keyword-research`、`amazon-xiyou-keyword-research` 在执行前读取。

三条线共同规则：
- 本品材质 / 安装方式 / 结构以 S1 产品 CT（`S1_选品板块/1688-product-detail-scraper/03_product_ct.yaml`）为准，CT 的 `not_this` 词不进 Listing 池和正向广告池；`01_product_profile.yaml` 的 materials 只代表市场。
- 词包定稿后跑 `scripts/keyword_index.py --package <keyword_package.yaml>` 生成 `ad_keyword_index.md/.json`，S4 只读这份索引（SIF 线由 sif_clean.py 自动生成）。
