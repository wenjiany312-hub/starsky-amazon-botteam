---
name: amazon-fba-freight-line-selection
description: Select the optimal cross-border shipping line (海派/海卡/快递) for an Amazon FBA shipment from a货代 (freight forwarder) price book (typically a multi-sheet xlsx with merged cells, partition-by-zip pricing, and时效 columns). Use when the user gives a destination warehouse code (e.g. IUSF 75241), a货代报价表 (e.g. D:\航线选择\...), and asks for the best/cheapest/fastest line. Output must be a side-by-side comparison of ≥3 candidate lines (最快 / 综合最优 / 最便宜) with price-per-kg,时效, and explicit data-gap callouts. Always saves dual artifacts (.md + .html) to D:\生成的报告\<航线选择|shipping-lines>\<序号>_<warehouse-code>_航线选择分析.{md,html}.
---

# Amazon FBA 航线选择分析（美线/欧线/英线通用模板）

> 这是仓管物流 / Warehouse & Logistics Manager 的核心工作流之一：当用户给出 **目的仓 + 货件基础信息 + 货代报价表** 时，输出"最优线路"推荐 + 备选 + 风险检查。

---

## 1. 触发条件（满足任意一条即可启动）

- 用户 @仓管物流 + 给出 FBA 仓地址或仓库代码（如 `IUSF 75241`、`FTW1`、`LBA1` 等）
- 用户提到"航线选择"、"线路推荐"、"走哪条线"、"最优海派"、"最便宜的渠道"
- 资料夹存在形式：`D:\航线选择\`、`D:\海运报价\`、`D:\货代价目表\` 等含多 sheet xlsx 的目录
- 截图含亚马逊后台货件信息（允许地区 / 箱数 / SKU 数 / 商品数量）

**本 Skill 不适用**于：纯快递直发（DHL/UPS 单票）、海外仓调拨、退仓/弃货。出现这些请改用 `logistics-warehouse-manager` 类目下的其他工作流（如果存在）或直接按 SOUL 自由发挥。

---

## 2. 关键环境事实（写在最前面，避免重复踩坑）

- **`html-report-renderer` SKILL 在当前 Hermes 环境里实际上没有挂载**——`skills_list` 返回 0 条 Amazon / FBA 相关 skill，SOUL.md 中白名单提到的 `02-skills-core/logistics-warehouse-manager` 和 `03-skills-extra/*` 都不可用。**不要调用 `skill_view(name='logistics-warehouse-manager')` 或 `html-report-renderer`，会直接报错 "Skill not found"。**
- 替代方案：**手写一个独立 HTML 副本**（单文件，inline CSS，深色背景，左边框强调，表格用 `<table>` + `nth-child(even)` 斑马纹），命名 `<name>.html` 与 `.md` 同目录。
- 报告开头必须如实标注："本报告基于 [logistics-warehouse-manager / 经验] 撰写（当前 Hermes 环境未挂载该 SKILL，故未调用 Skill）"。

---

## 3. 标准工作流（7 步）

### Step 1 — 锁三件事实
从任务派单消息里抓出：
1. **目的仓**（含邮编 + 州 + 城市，例如 `IUSF - 3351 Balmorhea Dr. 75241 - DALLAS, TX - US`）
2. **货件基础信息**（箱数、SKU 数、商品数量、配送能力 = 标准 / 加急）
3. **货代资料夹路径**（如 `D:\航线选择\`）

如果 ①缺失，回到 Admin 索取；如果 ②缺重量/材积/品类，标记为"资料缺口 1"，后续章节不编造。

### Step 2 — 列出资料夹里所有 xlsx 的 sheet 名
用 openpyxl 读，**不要** pandas（当前 venv 没装）。脚本模板见 `scripts/inspect_xlsx.py`。重点关注：
- 多 sheet 名是否包含"目录/价格快速导航/导航/Kxx/Dxx/Mxx"等模式
- 是否存在"邮编→分区"对照表（通常叫"导航"、"邮编分区"、"ZipCode"、"美国FBA仓库地址"）

### Step 3 — 解决合并单元格 + 展平所有 sheet
货代报价表的 sheet 几乎都有合并单元格（标题横跨多列）。openpyxl 默认只填左上格，其他是 None，**必须**先 `resolve_merged()`，再 `iter_rows(values_only=True)`。

每个 sheet 落盘成 TSV 到 `_raw/` 子目录，方便后续审计校对：
```
D:\生成的报告\航线选择\_raw\K13全美限时达.tsv
D:\生成的报告\航线选择\_raw\K16洛杉矶快线.tsv
D:\生成的报告\航线选择\_raw\导航.tsv
```

### Step 4 — 查邮编→分区
定位目的仓邮编在哪一分区（美西 8-9 / 美中 4-7 / 美东 0-3 / 美西北 97-99）。这一步是后续所有报价能否对号入座的根据。
- 在"导航"sheet 用正则 `\d{5}` 锁定邮编列，向左找仓库代码、向右找分区名
- 至少要找到 2 行交叉验证（如 `IUSF 75241 美中` + `FTW1 75241 美中`）

### Step 5 — 候选线路矩阵
对所有美线 sheet（K13/K16/K21/K25/D20/D35/M25/D23/D33/D36）扫前 10-15 行，提取：
- 渠道类型（海派 = 快递派送 / 海卡 = LTL卡车）
- 卸货港（洛杉矶/纽约/奥克兰/坦帕/萨凡纳）
- 船司（美森 CLX / ZIM / COSCO / OA / WHL/THE/MSC）
- **该邮编分区的报价**（12-50kg 段 + 50.1+ 段）
- 时效（"开船后 X 天"）
- 是否在表内有该仓的**直接报价**（如 `D20 sheet r76 组合七 IUSF 7.7/kg`）

按"是否适配目的仓"分三档：
- ✅ 表内有该分区或该仓的直接报价
- ⚠️ 表内只有相邻分区报价，理论上可达但需向货代二次确认
- ❌ 表内无对应报价（如 D35 纽约线去 Dallas = 绕路）

### Step 6 — 三方案对比（强制结构）
永远输出**至少三方案**，不能只给一个"最优"：
- **方案 A 综合最优**：时效 + 价格平衡点（典型如 K16 海派：12.8/kg，17-19 天）
- **方案 B 最快**：时效敏感（K13 海派：17.4/kg，14 天）
- **方案 C 最便宜**：成本敏感（D20/M25 海卡组合：7.6-7.7/kg，39-40 天）

每个方案表里固定列：渠道、卸货港、报价（分段）、时效、估算运费（用假设总重演示）、优势、风险。

### Step 7 — 资料完整性 + 风险检查 + 下一步动作
**强制**列以下数据缺口（在派单消息 + 截图里 90% 都缺）：
1. 单箱重量 / 材积
2. 货物品类（是否敏感：纺织/电子/汽配/液体/化妆品 → 触发 +1~2 RMB/kg 附加费）
3. 出货地（深圳/广州/宁波/义乌 → 影响 ±0.5 RMB/kg）
4. 截关日 / 开船日（决定实际时效）
5. 是否 FedEx/UPS 偏远邮编（决定 +RMB35/箱）
6. 多品名数量（>5 SKU 每多 1 个 +RMB30 清关费）
7. FBA Reference ID / Box Content（决定贴标策略）
8. 卖家贴标 vs 货代预处理（决定 +服务费）

给出 5-7 条下一步动作（向货代确认哪些字段、补哪些数据、复核哪些方案）。

---

## 4. 报告输出模板

```
# Warehouse Manager｜<目的仓代码> 航线选择分析报告

> 核心结论一句话：当前 <箱数> 箱 / <SKU> SKU / <件数> 件 货件去 <目的仓> = <分区>，
> 在 <货代名> 提供的 N 条美线里，**综合最优是 X（报价、时效）**，**最快是 Y**，**最便宜是 Z**。

1. 货件基础信息（表格）
2. 邮编分区确认（含 sheet 行号引用）
3. 7 条候选线路 × 目的仓 适配性矩阵（✅/⚠️/❌ + 报价 + 时效）
4. 推荐方案 A / B / C（每个含报价、时效、估算运费、优势、风险）
5. 三方案量级对比（同假设总重）
6. 按用户场景的选择建议（5 行场景表）
7. 资料完整性 / 风险检查（缺口表）
8. 下一步动作（5-7 条具体动作）
9. 资料附录（原始 xlsx 路径 + TSV 落盘路径 + 关键 cell 行号引用）

【Warehouse Manager 完成】
```

---

## 5. HTML 副本模板

手写、单文件、inline CSS、约 300-400 行。配色：
- 背景 `#0f1216`，卡片 `#161a21`，边框 `#262b35`，文字 `#e6e9ef`
- 强调色 `#5b8def`（左 border + 标题色）
- 好 `#22c55e` / 警告 `#f59e0b` / 坏 `#ef4444`
- 表格斑马纹 `tr:nth-child(even) td { background: #131820 }`
- 代码 `<code>` 背景 `#1e2532` 文字 `#ffd66e`
- 标题左 border-l + 圆角背景 `#1b2230`

完整示例见 `templates/line-selection-report.html`。

---

## 6. 必引用的辅助文件

- `references/typical-freight-forwarder-pricing-patterns.md` — 常见货代 xlsx 结构（瓴派 TCD / 联运通 / 顺友等）+ 字段含义
- `references/us-fba-zip-to-partition-table.md` — 美西/美中/美东邮编分区 + 典型仓库代码示例
- `templates/line-selection-report.md` — 报告 .md 模板（直接套用）
- `templates/line-selection-report.html` — 报告 HTML 模板（直接套用）
- `scripts/inspect_xlsx.py` — openpyxl 读 xlsx + resolve_merged + 落盘 TSV 脚本

---

## 7. 反模式（不要犯）

- ❌ **只看"价格快速导航"sheet 就推荐**——快速导航只给邮编分区兜底价，丢失了"组合七 IUSF 7.7/kg"这种针对性特价
- ❌ **推荐 D35/K25 纽约线去中南部仓**——绕路 30+ 天，比洛杉矶线贵且慢
- ❌ **编造总运费数字**（如"约 12,000 元"）当用户没给单箱重量时——必须明确标注"假设 840kg 演示"
- ❌ **跳过 FedEx 偏远邮编检查**——28 箱偏远每箱 +RMB35 = +980 RMB，对中小货件影响很大
- ❌ **不保存 _raw/ TSV**——后续审计/校对无法追溯
- ❌ **报告开头不声明"未调用 Skill"**——违反 SOUL.md 任务启动铁律，报告可信度受损
- ❌ **只给 1 个推荐方案**——用户场景多样（时效敏感 vs 成本敏感 vs 稳定补货），必须给三档