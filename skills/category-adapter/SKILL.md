---
name: category-adapter
description: >
  类目/企业策略适配器与运行路由器（实战版）。投喂企业资料、类目、标/非标形态、SOP、
  历史词库、广告打法和视觉参考，生成 enterprise_profile + category_profile + routing +
  playbook_overlay + skill_patches，供 salon-workflow 与业务 Skill 运行时组合成逻辑 V2。
   触发：接入我的企业、接入我的类目、加载资料包、生成企业V2、生成叠层、企业适配、
   任意类目资料夹、多层级类目路径、多类目路由、更新企业版本。
  作者坚哥。生态：Codex。
version: "2.3.0"
edition: starsky-v2
edition_label: 实战版
release_tag: "2026-08-01"
author: 坚哥
author_id: jiangge
brand:
  wechat_mp: 跨境者说干货
  knowledge_planet: 星空的跨境圈子
  series: Codex亚马逊实战
  ecosystem: codex
---

# 类目 · 企业适配器 · 实战版

## 统一资料接入约束（00C，必读）

开始本 Skill 前，若当前产品 run 存在 00C 资料接入报告或 Overlay，必须先读它们，应用其中的可用来源、事实边界、合规约束和 MCP 补数计划；再读取本任务产物并执行。用户已提供未整理资料但没有 00C 时，先执行资料接入，不要求用户整理。读取 ../../templates/data_intake_read_contract.md 作为权威规则。

## 出品与归属（实战版）

| 项 | 内容 |
|---|---|
| 发行 | **实战版**（**2026-08-01**） |
| 作者 | **坚哥** |
| 公众号 | **跨境者说干货** |
| 知识星球 | **星空的跨境圈子** |
| 价值 | **基础版工作流 + 你们的沉淀 → 快速企业落地** |

---

## 原则与版本模型

```text
不复制 12 套 Skill
→ 生成 Overlay / Patch
→ 总控与业务 Skill 运行时注入
```

```text
Core Skill
+ Category Overlay
+ Enterprise Overlay
+ Product Run Facts
= 本次运行的逻辑 V2
```

用户界面只显示「通用模式 / 加载资料包」与用户自己的类目名称；不得为了命名方便复制整套 Core Skill。Core 的合规、标题、状态和 Bulk 修复应自动惠及所有资料包和企业版本。

包内 `playbooks/` 是少量可选示例种子，只能由用户显式指定；它们不是可选类目清单、自动映射表或任意类目的前置条件。

---

## 输入

- 资料包根目录（可选；只索引用户明确提供的目录）  
- 输入你的类目名称（可自由填写，也可为层级路径）  
- 类目（或自由描述）  
- 形态：标品 / 准标品 / 非标  
- 市场默认 US；阶段 新品/老品  
- 禁区、竞品 ASIN、成功样板（可选）  
- 企业 SOP：禁用词、品牌词、预算习惯（可选）  
- 企业身份：企业名称/ID、站点、现有类目、默认类目（企业模式必需）  
- 历史资产：词库、Listing、广告报表/打法、供应商 SOP、视觉参考（可选）  
- 可选店铺基线：近 30/60/90 天销量、流量、CVR、广告、库存和退货，用于上线后巡检，不作为新品选品硬门槛。  

企业资料可来自用户粘贴或指定目录。只索引用户明确提供的资料；不得扫描无关公司目录。资料包源目录默认只读，生成的 manifest/overlay 写入当前产品 run。每条落入长期 adapter 的规则必须记录来源文件、时间窗、适用范围或用户确认。

---

## 输出目录

```text
adapters/{enterprise_id}/
  enterprise_profile.yaml
  routing.yaml
  shared/
    brand_rules.yaml
    compliance_rules.yaml
    supplier_rules.yaml
  categories/{category_id}/
    category_profile.yaml
    playbook_overlay.yaml
    keyword/
    listing/
    advertising/
    supplier/
    visual/
      refs_main/                 # 只用于学习表达
      refs_aplus/                # 只用于学习表达
      visual_playbook.yaml
      learning_report.md
    skill_patches/
      supplier_evaluation.md
      product_detail_scraper.md
      ip_risk_assessment.md
      market_research.md
      keyword_research.md
      listing_copywriter.md
      listing_image_designer.md
      premium_aplus_designer.md
      ad_campaign_builder.md
      product_development_prd.md
      launch_plan.md
      store_patrol.md
  CHANGELOG.md
  smoke_test_prompt.md
```

### 资料包接入与长期 adapter 的区别

- **加载资料包（默认轻量路径）**：总控读取用户给出的根目录和自由类目名称，生成当前 run 的 `enterprise_data_manifest.yaml` 与 `runtime_data_overlay.yaml`。不创建 adapter、不复制资料包、不创建类目 Skill。
- **长期 adapter（本 Skill 的沉淀路径）**：只在用户希望把已经验证的规则、阈值、品牌表达或 SOP 长期复用时，才建立 `adapters/{enterprise_id}/`。先输出差异与证据清单，用户审核后写入新版本。
- **工具包 playbook（可选示例）**：用户显式提供路径时才读取，不能由类目名称自动匹配。

`enterprise_profile.yaml` 对齐 `templates/enterprise_profile.schema.yaml`。  
`routing.yaml` 对齐 `templates/profile_routing.schema.yaml`。  
`playbook_overlay` 字段对齐包内 `adapters/_template/playbook_overlay.yaml`。  
`category_profile.yaml` 严格遵循包内 `templates/category_profile.schema.yaml`。  
视觉规范严格遵循包内 `templates/visual_playbook.example.yaml`。

### 视觉投喂（可选，企业极需要）

用户可同时提供：

- 主副图参考文件夹路径 → 在当前 run manifest 中只读索引；长期 adapter 只保存来源指针或经用户确认的可复用资料  
- 高级 A+ 参考文件夹路径 → 同上  

然后执行或调度**视觉学习**：分析头部表达 → 写 `visual_playbook.yaml`（学构图/信息层级，不抄 LOGO）。  
后续主副图 / A+ Skill **强制读**该 playbook 再出 prompt。

参考图只学习构图、色彩、信息层级和场景表达；不得复制竞品 LOGO、商标、包装或独特视觉资产。实际出图仍必须绑定当前产品的 `S3_视觉板块/amazon-listing-image-designer/产品垫图/asset_manifest.yaml`。

### 运行模式与路由

用户界面只有两种表达：

1. **通用模式**：不加载资料包，使用强母版通用规则。
2. **加载资料包**：读取用户给出的根目录与自由类目名称，按阶段形成当前 run 的临时叠层。

为兼容历史 state，内部仍可写 `base / category / enterprise_category`：无资料包为 `base`、显式种子为 `category`、已索引资料包或长期 adapter 为 `enterprise_category`。不得把这三个内部值展示成用户必选项，也不得把 `routes` 当类目 allow-list。

发现多个资料夹时，根据本次产品意图匹配或让用户补一次自由类目名称；不得静默把上次类目套到新产品。若用户未填类目，允许延后到 `condition_select` 后解析。

选择结果写入 `workflow_state.yaml`：`profile_mode`、`enterprise_id`、`category_id`、`routing_profile`、`enterprise_overlay`、`category_overlay`、`overlay_version`、`profile_selection`，以及 `enterprise_data_pack`、`active_data_injection`、`stage_acceptance`。资料包本身的 manifest 必须写在当前 run。

### CHANGELOG 必须写

相对大众基础版改了什么（词维/广告/合规/视觉）。

### 阈值、默认值与 execution profile（0711 融合）

企业/类目叠层必须把三类内容分开保存：

1. `hard_rules`：平台合规、IP 黑名单、当前产品事实和企业明确禁区；
2. `validated_thresholds`：有历史数据/案例证据的 CVR、ACOS、MOQ、退货、库存覆盖、供应商评分等阈值，必须带 source_id、适用类目、时间窗和版本；
3. `defaults`：缺数时的演示或起始参数，必须可被当前运行覆盖，不能伪装成硬规则。

新增 `execution_profile`：描述所需能力（MCP/文件、参考图保持、局部编辑、双端尺寸、视频平台、是否允许演示占位），不硬编码某个模型名、价格或永久可用性。实际运行记录 chosen_tool/model/version。

可选 `store_baseline` 保存近 30/60/90 天自然与广告流量、CVR、CPC/ACOS、库存、退货和利润基线，供 `amazon-store-patrol-orchestrator` 使用；无基线时巡检照常运行并标首次快照，不阻断新品主链。

### smoke_test_prompt

```text
启动实战全链。加载叠层：adapters/{slug}/playbook_overlay.yaml。用 MCP…
```

---

## 生成逻辑

1. 识别用户是否提供资料包根目录与自由类目名称；未提供时走通用模式，已提供时先建只读 manifest。  
2. 解析用户的类目名称或层级路径；不按“最近种子”自动映射。只有用户显式给出 playbook 路径时才读取示例种子。  
3. 按标/非标调整关键词维、Listing 属性、视觉表达与广告骨架。  
4. 当前 run 内只注入可追溯资料：共享规则、供应商阈值、利润模型、品牌语气、命名规则、视觉/广告参考；核心合规、IP、当前产品事实和本轮 MCP 结果不可覆盖。  
5. 只有用户要求长期沉淀且证据足够时，才为 supplier/product-detail/IP/market/keyword/listing/image/premium A+/ad 写 adapter patch；存在产品开发、Launch 或巡检经验时再写三个深水 patch。  
6. 视觉资料先只读索引；有参考图时生成 current-run visual playbook，无参考图时保持 draft。  
7. 长期 adapter 才写 routing、版本、来源、validation、CHANGELOG 和 smoke test；普通资料包接入直接 handoff 给总控继续。  

### 叠层优先级

优先级由高到低：**当前产品事实/本轮 MCP** → **平台合规、IP 与用户确认硬规则** → **企业资料包历史证据** → **标注默认值**。长期 adapter 是资料包的可选结构化表达，不是更高事实来源。任何叠层都不能覆盖 Amazon 合规、IP 黑名单、真实产品事实和盈亏真实性。冲突时生成冲突清单；只有改变产品路线、IP 风险接受或真实上传时才等待用户确认。

### V2 版本规则

- 首次企业适配：`overlay_version: 1.0.0`，`validation.status: draft`。
- 真实案例通过阶段验收后：标为 `validated`。
- 用户根据本轮结果更新规则：生成 `1.1.0` 等新版本并写 CHANGELOG，不覆盖旧版本证据。
- 只有跨企业普遍成立且经明确审核的规则，才允许提议反哺 Core Skill；不得自动改 Core。

---

## Handoff

```markdown
【本步完成】category-adapter
【产物】当前 run 的 enterprise_data_manifest.yaml + runtime_data_overlay.yaml；如用户审核长期沉淀，再生成 adapters/{enterprise_id}/…
【下一步 Skill】starsky-fullchain-workflow
【推荐口令】启动实战全链。
资料包根目录：{用户提供的根目录}
输入你的类目名称：{用户输入的自由名称}
用 MCP 拉数据。条件：…
【MCP】not_required（本步仅生成本地叠层）
```

无视觉 playbook 但资料包有 refs 时：

```text
启动视觉学习闭环。参考图由当前 run 的 runtime_data_overlay.yaml 的 S3 visual_reference 条目指定。
将学习结果写入本产品 S3_视觉板块/amazon-visual-benchmark-learner/visual_playbook.yaml，不写回资料包源目录。
```

---

## 反哺

用户说「根据刚才词库结果优化企业叠层到 v1.1」→ 先生成规则差异与证据清单，用户确认后更新企业/类目 overlay + CHANGELOG，不改 Core Skill 正文。

| 版本 | 说明 |
|---|---|
| 2.3.0 | 同上：启动语随 quickstart v4.4.0 统一 |
| 2.2.0 | 任意类目资料包接入：用户界面改为根目录 + 自由类目名称；资料源只读、run 内 manifest/overlay、按阶段注入；内置 playbook 改为显式可选示例，不再自动映射或限制类目 |
| 2.1.0 | 0711 融合：硬规则/验证阈值/默认值分层，store baseline，能力型 execution profile，PRD/Launch/巡检深水 patch |
| 2.0.0 | 企业/类目 routing 与运行时叠层 |
