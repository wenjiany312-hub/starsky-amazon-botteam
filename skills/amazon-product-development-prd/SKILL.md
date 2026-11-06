---
name: amazon-product-development-prd
version: "1.0.0"
description: >
  Amazon 产品开发 PRD 深水 Skill。消费 S1 综合可行性、竞品/VOC、1688 产品事实、IP 黑名单和财务边界，
  把痛点、属性空白、差异化、工艺、成本、MOQ、风险和验收标准写成可打样、可询价、可回填修订的 20 章节 PRD。
  触发：产品开发 PRD、差异化开发、打样需求书、供应商开发规格。S1 后可选，不阻断白牌速通。
edition: starsky-v2
author: 坚哥
brand:
  ecosystem: codex
---

# Amazon Product Development PRD

## 统一资料接入约束（00C，必读）

开始本 Skill 前，若当前产品 run 存在 00C 资料接入报告或 Overlay，必须先读它们，应用其中的可用来源、事实边界、合规约束和 MCP 补数计划；再读取本任务产物并执行。用户已提供未整理资料但没有 00C 时，先执行资料接入，不要求用户整理。读取 ../../templates/data_intake_read_contract.md 作为权威规则。

## 定位与边界

本 Skill 是 S1 通过后的可选深水分支，不改变新品现场主链。适合需要开模、改结构、改材料、做套装或建立长期壁垒的项目；白牌快速验证可跳过，但总控需记录跳过原因。

不臆测工艺极限、专利安全、认证结论和供应商报价；未知项进入 `open_questions` 或验证计划，不把“未抓到”写成“不能做”。

## 输入与证据优先级

1. `阶段_S1_选品可行性研究报告.md` 与同名 HTML；
2. `01_product_profile.yaml`、市场报告、竞品确认池、VOC 原始证据；
3. `03_1688产品资料*.md`、供应商回填、报价和样品记录；
4. `04_compliance_blacklist.yaml`、IP 决策记录；
5. 目标售价、预算、MOQ、交期、包装/FBA 与毛利边界。

事实冲突按 `../../templates/evidence_decision_revision_contract.md` 处理。1688 页面已抓取字段优先自动摘取；用户暂不回填时，以 `observed / inferred / missing` 分层条件性续跑。

## 研究方法

### 八类痛点

逐条保留 evidence_id、频率、严重度、Verified Purchase 权重、产品问题/误用区分：功能失效、结构/尺寸、材质耐用、安装使用、收纳清洁、包装运输、审美场景、价值/预期落差。

### 属性矩阵与竞品范围

默认选 3–8 个代表性竞品做属性矩阵，但这是默认范围而非硬门槛；有效样本不足时说明边界并继续。字段至少包括结构、尺寸、材质、关键部件、安装、兼容、包装、售价、评分/VOC、承诺、弱点和证据。

### 十大差异化方向

从结构、尺寸覆盖、材料、工艺、易用性、套装、包装、场景、人群、服务/说明书十个方向提出候选。每个候选必须走：

`痛点证据 → 属性空白 → 改良方案 → 工艺可行性 → 增量成本/MOQ → IP/合规 → 用户表达 → 广告验证 → 失败阈值`。

## 20 章节 PRD 固定目录

1. 项目摘要与 Go/Conditional/No-go；2. 用户与 JTBD；3. 使用场景；4. VOC 八类；5. 竞品矩阵；6. 属性空白；7. 产品定位；8. 核心规格；9. 结构与部件；10. 材料与工艺；11. 尺寸/公差；12. 颜色/SKU/套装；13. 安装与说明；14. 包装与跌落；15. 质量测试；16. IP/合规；17. 目标成本与 MOQ；18. 打样计划；19. 上线验证；20. 风险、开放问题与修订记录。

每章必须含“目标/事实依据/设计要求/验收方法/失败条件/责任方”，不得只写概念性建议。

## 财务与首批实验

输出主推、对照、premium 三种方案；数量由预算、MOQ、交期、库存覆盖和广告现金需求计算，禁止硬编码 300 件。至少列：采购、包装、头程、FBA、退货准备金、广告、贡献毛利和低/中/高敏感性。

## 输出

- `S1_选品板块/amazon-product-development-prd/PRD_产品开发需求书.md`
- `S1_选品板块/amazon-product-development-prd/product_prd.yaml`
- `S1_选品板块/amazon-product-development-prd/PRD_供应商询价与打样清单.md`
- `S1_选品板块/amazon-product-development-prd/PRD_样品回填验收.md`

样品回填只做影响范围修订：新建 `revision_id`，列受影响规格、成本、Listing、视觉、广告和 Launch Plan，不覆盖旧版本。

## acceptance_checks

- [ ] 八类痛点有 evidence_id、频率/严重度和误用区分。
- [ ] 竞品属性矩阵可回溯，3–8 只是默认范围。
- [ ] 差异化包含工艺、成本、MOQ、IP、表达、验证、失败阈值。
- [ ] 20 章完整，含可测试验收而非空泛文案。
- [ ] 未回填字段分层标注但未导致简版输出。
- [ ] PRD 为可选深水分支，不阻断白牌速通。

## Handoff

完成后回 `starsky-fullchain-workflow`。若需要财务/库存节奏，进入 `amazon-launch-plan`；若样品变化影响 S1/Listing/视觉，按 revision scope 局部回写。
