---
name: github-tabler-html-report
version: "3.8.0"
description: >
  将市场调研、经营分析或选品报告转换为高信息密度、离线可打开的单文件 HTML 报告。
  视觉系统参考 Tabler 的布局，默认清爽青绿与阶段阅读导航，适用于需要
  决策摘要、指标卡、表格和数据图的商业报告场景。
edition: starsky-v2
edition_label: 实战版
release_tag: 2026-08-01
author: 坚哥
author_id: jiangge
brand:
  wechat_mp: 跨境者说干货
  knowledge_planet: 星空的跨境圈子
  series: Codex亚马逊实战
  ecosystem: codex
---

## 报告视觉统一（2026-09-13）

S1/S3/S4 及独立静态归档报告先读 `../../templates/report_visual_system.md`，使用随包 Report 的清爽青绿主题及阶段阅读分组；保留完整报告、离线与打印全文。S2 仍走前述固定模板入口。下文提到的左侧目录/深色头部是历史视觉说明，由本节的新视觉约定替代，业务模块与自检要求保持。


## 出品与归属（实战版）

| 项 | 内容 |
|---|---|
| 发行 | **实战版**（发布标签 **2026-08-01**） |
| 作者 | **坚哥** |
| 公众号 | **跨境者说干货** |
| 知识星球 | **星空的跨境圈子** |
| 运行环境 | **OpenAI Codex**（`~/.codex/skills`） |
| 包路径 | 随「星空亚马逊 Agent」插件分发（`skills/` 目录） |

> 打开本文件即可确认作者与来源；转载或改装请保留本表。

---

# GitHub Tabler HTML Report

## S2 专用路由

收到 S2 词库或 Listing 任务时，先读 `../../templates/s2_report_input.md` 和 `../../templates/keyword_report_layout.md`，调用插件根 `scripts/s2_report.py --package PATH --context PATH --output-dir DIR`；有文案加 `--listing PATH`，有基线加 `--baseline PATH`。参数以 `--help` 为准。

S2 使用规范模型与随包固定模板：词库八节、Listing 十节；输出 keyword_report.html、listing_report.html、listing.md、keywords.csv、report_model.json、validation.json、manifest.json（文案视图按是否有 listing 输入）。路由可映射为旧 06/07 名称并记录指纹，不能另改正文或再写产品临时 HTML 生成器。

S2 验收看字段、事实/IP资格、计数去重、实际覆盖、输入指纹与关键风险可见性；不按文件大小、表格/进度条数量或保留率验收。正文与中文直接可读，长证据可折叠。缺依赖/输入按正式入口错误补齐，不能绕过。

**以下组件调用、自由组织章节、minimum 与干净窗口说明适用于非 S1/S2 报告。** S2 在当前任务从结构化输入重渲染，保留模板版本与第三方许可。

## S1 目的导向七章报告

S1先读 `../../templates/s1_product_front_contract.md` 和 `../../templates/s1_report_module_contract.yaml`，通过插件根 `scripts/s1_stage_report.py --root <实际产品根>` 生成MD与HTML；紧凑流程由控制器传真实 `--compact-receipt`。正式报告使用同一report_v2唯一结论、产品图/CT/四答案卡/三动作与七章正文，不从子MD复制全文。市场五节必备事实缺失须说明原因，采购清单可保存但不授权；保留完整深度，不设正文行数上限，去重/冲突与C10源Hash保留。下文历史S1体积/KPI/M00模块说明不再作为当前生成要求；旧renderer API仅兼容旧调用，S2–S5按各自契约。

## 我的身份

我是面向业务决策者的 HTML 报告设计师。我的目标不是把 Markdown 原样塞进网页，而是把结论、
风险、关键数字和行动项组织成可以在 3 分钟内读懂、也可以下钻核对的决策报告。

## 何时使用

- 用户要求把 Markdown、Word 导出的文本、CSV 结论或业务分析做成“好看、可分享”的 HTML 报告。
- 报告需要兼顾桌面展示、手机查看和离线打开。
- 内容包含多个分析章节、数值表格、关键 KPI、库存/利润/CPC 等可视化对比。

不适用：需要实时查询、多人协作编辑或复杂筛选交互的 BI Dashboard。此 Skill 的交付物是可归档的静态报告。

## 参考来源与许可证

- 视觉参考：Tabler Dashboard UI Kit
- GitHub：<https://github.com/tabler/tabler>
- 许可证：MIT。复制或内嵌 Tabler 的 CSS 时，保留其许可证注释，并在报告页脚注明 Tabler（MIT）。
- 本 Skill 不复制 Tabler 的品牌、Logo 或产品素材；只使用其开源布局体系和公开许可的 CSS。

详见 [references/tabler-source.md](references/tabler-source.md)。

## ⛔ 沙龙场景：先读产物契约

在非 S2 实战报告（S1/S3/S4）里被调用时，**必须先读
`../../templates/html_report_contract.yaml`**，它规定了：

- 当前阶段该出**几份** HTML、**叫什么名字**、读**哪些源文件**
- 每份的 **minimum 指标**（size / section / kpi / details / bar / svg / table）
- 每份的 **must_contain 章节清单**
- 生成后要跑的 **self_check_command**

**基准来自 2026-07-26 已验收合格的实测值**，不是拍脑袋定的：

| 报告 | size | section | kpi | details | bar | svg | table |
|---|---:|---:|---:|---:|---:|---:|---:|
| S1 综合可行性 | 92KB | 38 | 7 | 1 | 7 | 3 | 31 |

非 S2 报告按各自 minimum 执行；S2 使用前述固定模板与字段校验，不沿用历史体积或组件数量。

⚠️ **生成后必须自检报数**，把实测值和 minimum 并列给用户看。
⛔ 不达标不得写"已完成"，先查是否发生过上下文压缩，换干净窗口重出。

---

## ⛔ 第 0 步：先用 assets 里的渲染器，不要现场重写样式

**本 Skill 自带一个可复用渲染组件库：**

```
skills/github-tabler-html-report/assets/salon_report.py
```

**⛔ 不许自己从零写 CSS 和 HTML 骨架。** 用法：

```python
import json, os, sys
from pathlib import Path

def plugin_home():
    starsky = Path.home() / ".codex" / "starsky.json"
    if starsky.is_file():
        return json.loads(starsky.read_text(encoding="utf-8"))["home"]
    # 本 Skill 在 skills/github-tabler-html-report/，上两级即插件根
    return str(Path(__file__).resolve().parents[2]) if "__file__" in dir() else os.environ.get("USERPROFILE", "") + r"\plugins\starsky-amazon-agent"

sys.path.insert(0, os.path.join(plugin_home(), "skills", "github-tabler-html-report", "assets"))
from salon_report import Report

r = Report("报告标题", "副标题", meta=["run ...", "marketplace US"])
s = r.section("s-gate", "一、量化闸门", "说明文字")
s.kpis([("SP Campaign","4",None,"ok"), ("投放词","39",None,"ok")])   # KPI 卡
s.bar("已投放", 39, 726, "39 / 726　5.4%", "blue")                    # 进度条
s.table(["列A","列B"], [["a","b"]], align=['l','n'])                  # 自动包 tablewrap
s.details_table("展开全部 N 个", ["#","词"], rows)                     # 折叠区
s.details_markdown("展开 难度双法", md_text)                         # 折叠里的 .md 必须渲成 HTML，禁止 <pre> 堆原文
s.donut(45.5, "$25", "占日预算")                                       # svg 环形图
s.hbars_svg("Top 8", [("词",27797)])                                  # svg 条形图
s.tree(['<span class="lv0">根</span>', '├─ ...'])                     # 结构树
s.callout("danger", "出价栏留空的原因……")                            # 高亮块
st = r.write(out_path)      # 返回自检数字，直接报给用户
```

**为什么必须用它（2026-08-07 实测的代价）：**

> 本 Skill 原来只有 SKILL.md，**没有任何模板资产** —— 于是每跑一次，模型都得现场重写一套样式。
> 上下文一压缩，写出来的就缩水。
>
> 实测后果：同一个 run 里**三份报告三套互不相同的 CSS 变量**，注释写的是 "Tabler-**inspired**"；
> `运行数据/` 下躺着 **4 个临时手写的渲染器**；S4 广告架构只出了 **17KB / 6 section**，
> 契约下限是 **50KB / 12 section**。

**三条布局硬规则（§4.0）已内建在组件库里**：`body{overflow-x:clip}`、`.layout` 限宽居中、
`table()` 自动包 `.tablewrap`（**漏不了**）。用它就不会再踩。

`r.write()` 返回的自检字典直接对照 `templates/html_report_contract.yaml` 的 `minimum`，
**不达标就补内容重出，⛔ 不许写「已优化」交差。**

---

## 标准作业流程

### 1. 先读数据，再决定页面结构

从源报告中抽取以下内容，不能凭空补数：

1. 一句“进入 / 暂缓 / 不进入”的决策与前提。
2. 3-5 个 KPI：例如可行性评分、预算、首批备货、毛利、CPC 或风险等级。
3. 2-3 组最能支持决策的对比数据，每组不超过 5-7 项。
4. 完整正文的章节标题、表格与来源说明。
5. 可合法使用的产品、工厂或实物图片；图片必须标明用途，例如“供应链参考，不可用于 Listing”。

### 2. 用 Tabler 组织信息层级

采用以下结构，不做营销落地页：

```text
固定侧栏：报告名称、章节目录、日期
顶部：报告路径与数据状态
首屏：决策结论、适用场景、关键前提
KPI 行：3-5 个指标卡
数据面板：CPC / 库存 / 毛利等横向条形图
正文：按章节展开，保留原始表格与证据
页脚：数据来源、图片用途、Tabler MIT 致谢
```

### 3. 生成离线单文件

1. 从 Tabler 的官方仓库或 npm CDN 下载指定版本 CSS。
2. 把 CSS 内嵌到 `<style>`；不依赖运行时 CDN。
3. 本地图片转为 `data:` URL 再内嵌；视频不建议放进单文件报告。
4. 用结构化 HTML 渲染 Markdown 表格，不能把 `|` 管道符当正文直出。
5. 报告中的数值图优先使用 HTML/CSS 横向条形图；不必为了静态报告引入大型图表运行时。
6. 写入 UTF-8 HTML，并保留 `<meta name="viewport">`。

### 4. 可视化与版式规则

- 首屏只回答“是否做、为什么、下一步是什么”。
- 主色控制在深色中性背景、青绿主色、橙色风险提示三类；不要使用渐变背景或装饰光球。
- 页面章节不套浮动大卡片；卡片仅用于 KPI、对比项、图片证据和表格容器。
- 长标签采用横向条形图；短标签可用柱状或数值卡。
- 表格容器必须 `overflow-x:auto`，手机不能撑破视口。
- 文字不使用负字距，不用视口宽度缩放字体。
- 用 `@media (max-width: 1040px)` 将侧栏变为横向目录；用 `@media (max-width: 620px)` 将 KPI、图表和双栏结论收为单列。

#### 4.0 三条布局硬规则（v3.3.0 新增，实测踩过）

**实测问题**（2026-07-29，S1 报告）：侧栏滚动时不吸附、宽屏右侧大片空白、
两个表格撑破所在栏。三处都是 CSS 写法问题，不是内容问题。

**① 禁止在 `html` / `body` 上写 `overflow-x:hidden`**

```css
/* ⛔ 错 —— 它让 html 变成新的滚动容器，侧栏的 position:sticky 直接失效 */
html{overflow-x:hidden}
body{overflow-x:hidden}

/* ✅ 对 —— clip 同样防横向滚动，但不创建滚动容器 */
html{scroll-behavior:smooth}
body{overflow-x:clip}
```

这是最容易踩且最难自查的一条：**页面看起来正常，只有滚动时才发现侧栏跟着跑掉了。**
任何祖先元素的 `overflow` 非 `visible`，都会夺走 sticky 的定位基准。

**② `.layout` 必须限宽并居中**

```css
/* ⛔ 错 —— 第二列吃满剩余宽度，但 main 限死 1440px 且靠左，宽屏右边空一大块 */
.layout{grid-template-columns:260px minmax(0,1fr)}
.main{max-width:1440px}

/* ✅ 对 —— 整体居中，main 撑满第二列 */
.layout{grid-template-columns:260px minmax(0,1fr);max-width:1780px;margin:0 auto}
.main{width:100%;min-width:0}
```

限宽放在 `.layout` 上，不要放在 `.main` 上。

**③ 每个 `<table>` 必须包 `.tablewrap`，栅格栏内的表要降 min-width**

```css
.tablewrap{max-width:100%;overflow-x:auto;border:1px solid var(--line);border-radius:9px}
table{width:100%;min-width:680px}
.grid2 .tablewrap table,.grid3 .tablewrap table{min-width:420px}  /* 窄栏放宽松 */
```

**漏包一个就撑破一处。** 尤其两类位置最容易漏：
- `<details>` 折叠块里的表
- `.grid2` / `.grid3` 栅格栏里的表 —— 680px 的表塞进 40% 宽的栏，必炸

**输出前自检**（三条都要过）：

```
□ grep 'overflow-x:hidden' → 必须 0 处
□ 每张 <table> 的祖先元素 class 列表包含 tablewrap（允许同时有 stackable 等类）；主题字体与检查器共读 templates/report_theme.json
□ .layout 有 max-width 且 margin:0 auto
```

#### 4.1 数据形态 → 组件强制路由（v3.1.0 新增）

**实测问题**：S1 报告生成出 35 张表、309 行 `<tr>`、**0 个 `<svg>`**。上面的规则被当成建议，模型全程走了表格这条最省事的路，结果是"整个看板全是表格，可视化效果不好"。

因此改为**按数据形态强制路由**。生成前先对每个数据块判形态，**命中即必须用对应组件，不得降级为表格**：

| 数据形态 | 判别特征 | **必须用** | 禁止 |
|---|---|---|---|
| **时间序列** | 有月份/日期列且 ≥6 个数据点 | **内联 SVG 折线图 / sparkline** | ❌ 逐行列出的长表 |
| **占比 / 集中度** | 百分比且总和 ≈100%，或 Top-N 份额 | **CSS 横向条形图 / 环形进度** | ❌ 只写数字 |
| **单指标对比** | 3–8 个对象比同一个指标 | **CSS 横向条形图**（条长=数值） | ❌ 纯数字列 |
| **区间 / 阈值** | 有下限-上限，或有红线可对比 | **区间条 + 阈值标线** | ❌ 只写"58-86天" |
| **关键结论数** | 单个决定性数字（盈亏 ACOS、最晚下单日、风险分） | **KPI 数值卡**（大字号 + 状态色） | ❌ 埋在表格单元格里 |
| **时间线 / 节奏** | 有先后顺序的动作（0-7 天 / 14 天节奏 / 下单日 vs 今天） | **横向时间轴** | ❌ 表格 |
| **多维明细** | 对象 ×5 列以上属性 | ✅ **表格是正确选择** | — |

> **判定口径**：一份阶段报告里若时间序列、占比、区间三类数据都存在，
> 而 `<svg>` 数量为 **0**，直接判该 HTML 不合格 —— 不是审美问题，是把可比较的数据降级成了要读者心算的数字。

**本类目实测该改成图的三处**（作为示例）：

| 原表格 | 应改为 |
|---|---|
| 24 个月趋势（24 行表） | **SVG 折线图**，标出峰值月与当前月 |
| 双时间窗集中度对比（Top3% / Top3品牌% / Amazon%） | **横向条形图**，两窗口并排 |
| 最晚下单日 vs 今天 | **时间轴**：峰值月 ← 提前期 → 下单区间 ← 今天（红标"已错过"） |

#### 4.2 SVG 折线图最小实现（无需图表库）

静态报告不引外部图表库。用内联 SVG + `polyline` 即可：

```html
<svg viewBox="0 0 720 180" class="chart" role="img" aria-label="24个月销量趋势">
  <polyline fill="none" stroke="currentColor" stroke-width="2"
            points="0,120 30,96 60,124 …" />
  <!-- 峰值月标点 + 文字；当前月竖虚线 -->
  <circle cx="150" cy="72" r="4"/>
  <line x1="690" y1="0" x2="690" y2="180" stroke-dasharray="4 4"/>
</svg>
```

要点：`viewBox` 自适应、`stroke="currentColor"` 跟随主题色、必须有 `aria-label`、峰值与当前位置要标注。**数据点仍可保留一份可折叠的原始表**供核对，但主视图必须是图。


#### 4.3 图表化后原表必须折叠或删除（v3.2.0 新增）

**实测问题**：加了 3 张 SVG 后，`<table>` 仍是 35、`<tr>` 仍是 309 —— 24 个月趋势的 26 行表格原封不动留在折线图旁边，同一份数据呈现两遍。**只做加法不做减法，表格墙依然存在。**

数据一旦被图表化，原表**必须二选一**：

| 场景 | 处理 | 写法 |
|---|---|---|
| 图已完整表达（趋势、占比、区间、时间线） | **删除原表** | 图下方一行文字给关键数值即可 |
| 读者可能需要逐值核对（月度原始销量、竞品明细） | **收进 `<details>` 折叠** | `<details><summary>查看原始数据（24 个月）</summary>…表格…</details>` |

**禁止**：图和平铺表格并列出现。这不是"更完整"，是让读者读两遍同样的数据。

```html
<figure class="chart-block">
  <figcaption>类目月度销量趋势</figcaption>
  <svg …>…</svg>
  <p class="chart-note">峰值 2024-08 指数 120.8；当前 2026-07 为未完月。</p>
</figure>
<details class="raw-data">
  <summary>查看原始数据（24 个月完整表）</summary>
  <table>…</table>
</details>
```

**自检加一条**：图表化改造后，`<tr>` 总数应**明显下降**（本例 309 行中，趋势表 26 行 + 时间窗表 5 行应转入折叠）。若 `<svg>` 增加而 `<tr>` 不变，说明只做了加法，判不合格。


#### 4.4 长表必须默认折叠（v3.4.0 新增）

**实测问题**（2026-07-29）：S2 词库 HTML 把 1,111 个词平铺成一张长表，
页面长到没法翻，**信息越全越没人看**。

**硬规则：嵌入已有 `.md` 必须用 `details_markdown` / `markdown` 渲成标题、表格、引用；禁止 `html.escape` 后丢进 `<pre>`。读者点开看到的应是报告版式，不是 Markdown 源码。**

**硬规则：任何超过 30 行的表格，必须拆成「摘要平铺 + 全量折叠」两层。**

| 层 | 内容 | 呈现 |
|---|---|---|
| **摘要层** | Top N（通常 10–20 行），按最关键的指标排序 | 平铺可见 |
| **全量层** | 完整数据 | 收进 `<details>`，**默认收起** |

```html
<figure>
  <figcaption>核心投放词 · 按周搜索量 Top 20</figcaption>
  <div class="tablewrap"><table>…20 行…</table></div>
  <p class="chart-note">全量 1,111 词见下方折叠区。</p>
</figure>

<details class="raw-data">
  <summary>展开全量词库（1,111 词 · 17 字段）</summary>
  <div class="inside">
    <div class="tablewrap"><table>…全量…</table></div>
  </div>
</details>
```

**四条细则：**

1. **`<summary>` 必须写清数量** —— 「展开全量词库（1,111 词）」，
   不写「查看更多」这种没有信息量的文案
2. **分池的表各自独立折叠** —— Listing 池 / Exact / Phrase / Broad / Negative / Brand
   各一个 `<details>`，不要塞进同一个折叠区
3. **摘要层必须说明排序依据** —— 「按周搜索量 Top 20」而不是「部分关键词」
4. **默认全部收起** —— 不写 `open` 属性；读者主动展开才是他真的想看

**同样适用于**：竞品明细（>15 条）、评论样本、逐条 IP 检索记录、
Bulk 表预览、任何逐行原始数据。

> **判断口径**：一屏（约 30 行）放不下的表，就该折叠。
> 报告的价值在于「先给结论，再让人能下钻核对」，不是把所有行都怼到脸上。

---

### 5. 验证

必须使用浏览器截图检查：

```text
Desktop: 1440 x 1200
Mobile: 390 x 1000
```

检查项：

- 标题、KPI、正文和表格均可读。
- 桌面没有横向溢出；手机没有文字重叠。
- 首屏不使用空洞说明文案，直接呈现报告内容。
- 所有关键数字与原报告一致。
- 图片来源和用途清晰，不能暗示可以直接用于 Amazon Listing。

#### 5.1 可视化密度自检（强制，可机检）

截图之外，生成后必须跑以下统计并写入报告尾部的生成审计：

```bash
f={板块夹}/github-tabler-html-report/HTML/xxx.html
echo "table:$(grep -o '<table' $f|wc -l)  tr:$(grep -o '<tr' $f|wc -l)  svg:$(grep -o '<svg' $f|wc -l)  bar:$(grep -o 'bar-fill\|progress-bar' $f|wc -l)  kpi:$(grep -o 'kpi\|stat-card' $f|wc -l)"
```

**阶段综合报告的下限**：

| 指标 | 下限 | 不达标含义 |
|---|---|---|
| `<svg>` | **≥3** | 时间序列/占比/区间三类数据至少各有一张图 |
| CSS 条形图或环形 | **≥4** | 对比类数据没被可视化 |
| KPI 数值卡 | **≥5** | 关键结论数还埋在表格里 |
| `<table>` ÷ (`<svg>`+条形+KPI) | **≤3** | 表格占比过高，看板变成表格墙 |

**`<svg>` 为 0 直接判不合格**，不接受"内容以文字为主"的解释 —— 24 个月趋势、双时间窗对比、区间阈值这类数据存在时，不出图就是把可比较的信息降级成了要读者心算的数字。

自检不过时**必须重生成**，不得只在报告里注明"可视化待优化"。

## 资深判断与红线

- 不要把“漂亮”误解为大面积渐变、超大标题或营销 Hero。业务报告要优先支持扫描、比较和复核。
- 不要直接复用 1688 的中文促销图作为 Amazon 方案图。它可以在报告中作为供应链证据，但必须标注用途。
- 不要为了图表牺牲可离线打开性。静态报告中，CSS 图表往往比 CDN 图表库更可靠。
- 不要把所有表格都放在首屏。先做结论和指标，明细放在对应章节。
- CSS 与模板的许可证必须可追溯；没有明确许可证的 GitHub 模板不可直接搬用。

## 最小生成提示词

```text
请将 [源报告路径] 制作为单文件离线 HTML。
使用 github-tabler-html-report 的结构：左侧目录、决策首屏、KPI、三组数据对比图、完整正文和来源页脚。
参考 Tabler（MIT）布局并在页脚署名；将 CSS 与允许使用的本地图片内嵌；桌面和手机截图验证后输出到 [目标路径]。
```

---
## 实战版 · 定位

### 版本
**3.7.0** starsky-v2 · 作者坚哥

> 3.7.0 S2 改为 scripts/s2_report.py 的固定六/八节报告；非 S2 保留 salon_report.py。先读 `templates/report_format_contract.yaml`。
> 3.5.0 沙龙场景先读 `templates/html_report_contract.yaml`：各阶段份数/命名/源文件/minimum指标/must_contain 全以契约为准，生成后强制自检报数。
> 3.4.0 新增 §4.4 长表默认折叠：>30 行的表必须拆成「摘要平铺 Top N + 全量收进 details」，
> summary 必须写清数量，分池各自独立折叠，默认全收起。
> 3.3.0 新增 §4.0 三条布局硬规则（sticky 失效 / 宽屏留白 / 表格撑破），附输出前自检。

### 角色
S1/S3/S4 及 S5 独立静态归档（S5 实时九页看板继续使用原 React 应用） 使用 `assets/salon_report.py`；S2 调用 `scripts/s2_report.py`，禁止现场重写样式或产品临时生成器。
`editorial-html-report` 只在用户点名杂志风时使用，不进生产路径。
页脚用渲染器 DEFAULT_FOOT。
