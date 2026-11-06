# 避免 AI Slop 的具体规则

> "AI slop" = AI 生成的、看似精致实则千篇一律的视觉垃圾。判断标准很简单：**"这看起来像不像 ChatGPT/Claude 用一个 prompt 就能直接吐出来的东西？"** 如果像，就是 slop。

---

## 第一层：禁用清单（这些直接 = AI slop）

### 1.1 字体禁用
```css
/* ❌ 这些是 AI slop 重灾区 */
font-family: 'Inter', sans-serif;
font-family: 'Roboto', sans-serif;
font-family: 'Arial', sans-serif;
font-family: 'Helvetica', sans-serif;
font-family: -apple-system, BlinkMacSystemFont, sans-serif;
font-family: system-ui, -apple-system, sans-serif;
```

```css
/* ✅ 正确：用中文衬线打底 */
font-family: 'Noto Serif SC', 'Source Han Serif SC', 'Songti SC', serif;
```

**例外**：标签、UI 元素（小标签、按钮、菜单）可以用 sans-serif，但要用 `Noto Sans SC` 不要用 Inter。

### 1.2 配色禁用

```css
/* ❌ AI slop 招牌配色 */
background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);  /* 蓝紫渐变 */
background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);  /* 粉紫渐变 */
background: linear-gradient(45deg, #5b21b6, #ec4899);            /* 紫粉对角 */
color: #6366f1;  /* 默认 indigo - 太常见 */
color: #8b5cf6;  /* 默认 violet - 太常见 */
```

```css
/* ✅ 正确：选一个有意图的主色 + 一个锐利强调色 */
:root {
  --ink: #1a1a1a;          /* 主色：深而中性 */
  --accent: #c4382d;       /* 强调：锐利但不刺眼 */
  --gold: #b8956a;         /* 辅色：温暖、独特 */
}
```

**判断方法**：把你的配色截图给"配色像 PPT 模板吗？"如果像，重做。

### 1.3 布局禁用

```css
/* ❌ AI slop 招牌布局 */
.container {
  max-width: 1200px;
  margin: 0 auto;
  text-align: center;        /* 长文档不应居中 */
  padding: 4rem 2rem;
}
.card {
  background: white;
  border-radius: 12px;
  box-shadow: 0 4px 6px rgba(0,0,0,0.1);   /* 千篇一律的中等阴影 */
  padding: 2rem;
}
.card-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);   /* 三等分卡片堆 */
}
```

```css
/* ✅ 正确：不对称、有节奏、左对齐 */
.layout {
  display: grid;
  grid-template-columns: 240px minmax(0, 760px);   /* 不等分 */
  gap: 4rem;
  max-width: 1280px;
  margin: 0 auto;
  padding: 0 2rem;
}
/* 不同类型的卡片用不同的视觉处理 */
.action-card { border-left: 4px solid var(--accent); }
.quote-card { background: var(--paper-warm); border-left: 5px solid var(--gold); }
.warn-card { background: #fffbeb; border: 1px solid #fde68a; }
```

### 1.4 动画禁用

```css
/* ❌ AI slop 动画 */
@keyframes bounce { ... }       /* 弹跳 */
@keyframes rotate { ... }       /* 旋转 */
@keyframes fadeInUp { ... }     /* 飞入 */
.card:hover { transform: scale(1.05) rotate(2deg); }   /* 放大旋转 */
```

```css
/* ✅ 正确：克制的过渡 */
.card { transition: transform 0.2s, box-shadow 0.2s; }
.card:hover {
  transform: translateY(-2px);   /* 轻微上移 */
  box-shadow: 0 4px 16px rgba(0,0,0,0.08);
}
```

### 1.5 图标 / Emoji 滥用

```html
<!-- ❌ 通用 emoji 堆砌 -->
<h2>🚀 我们的目标</h2>
<h2>💡 创新方案</h2>
<h2>🎯 关键指标</h2>
<h2>📊 数据分析</h2>
```

```html
<!-- ✅ Emoji 仅作为视觉锚点，用在分类标签 -->
<div class="trend-bucket short">
  <div class="bucket-header"><span class="icon">🔥</span>短期信号</div>
</div>
<!-- 一份报告 emoji 数量不超过 5-7 个，且每个都有功能性目的 -->
```

---

## 第二层：判断你的 HTML 是不是 slop 的 checklist

逐项过：

- [ ] 字体不是 Inter / Roboto / Arial / 系统字体
- [ ] 配色不是紫色渐变 / 蓝紫渐变
- [ ] 主色至少有 3 个明确的功能区分（ink / accent / gold）
- [ ] 布局不是纯居中
- [ ] 不同类型的内容用了不同视觉组件（不是清一色白卡片）
- [ ] Emoji 数量克制（< 10 个）且每个有功能
- [ ] 阴影不是清一色 `box-shadow: 0 4px 6px rgba(0,0,0,0.1)`
- [ ] 有至少 1 个有"个性"的细节（金色引号 / 编辑红分隔线 / 暗色表头 / 不对称卡片等）
- [ ] 移动端不是塌缩成手机版而是有专门简化
- [ ] 有 `@media print` 适配

任何一项失败，就是 slop。

---

## 第三层：让作品"有灵魂"的小细节

这些细节单独看不起眼，但加起来就是"有人在设计"和"AI 一键生成"的区别：

### 3.1 排版细节
- **drop cap 首字母**（特别适合长引文 / 章节开头）
- **左侧色条**（替代千篇一律的卡片边框）
- **页码 cite 标签**（小号 sans + 金色，专业感）
- **暗色表头表格**（替代浅灰表头，editorial 感强）
- **斑马纹行**（多列数据时阅读体验提升）

### 3.2 字体细节
- **章节大编号用 display 衬线**（Playfair Display 这种戏剧感字体）
- **标签全大写 + 字间距 `letter-spacing: 0.15em`**（标牌感）
- **行高 1.85**（中文阅读舒适度峰值，不要 1.5）
- **`font-feature-settings: "kern" 1, "liga" 1`**（启用字距和合字）

### 3.3 色彩细节
- **金箔色** `#b8956a` 用作低饱和点缀（引号、序号、分隔线）
- **强调色用在标签底色而非文字**（如标签底浅红 + 文字深红）
- **底色不用纯白用 `#fdfcf7`**（仿牛皮纸感）

### 3.4 互动细节
- **顶部滚动进度条**（3px 高的红线）
- **TOC scroll-spy**（当前章节自动高亮）
- **`<details>` 折叠面板**（替代滚动到底的长附录）
- **`hover` 轻微上移**（不是放大不是旋转）

---

## 第四层：极端避坑（高级）

### 4.1 不要做"假杂志风"
- 强行加大量装饰元素（线条、虚线、点）≠ editorial 风
- 真正的杂志风是 **克制的细节 + 大方的负空间**
- 例子：The New York Times、The Economist、Bloomberg 都不靠装饰，靠 typography 和 grid

### 4.2 不要全屏背景图
- 大背景图 + 半透明覆盖文字 = AI slop 招牌做法
- 真正好的设计宁可纯色 + 优秀排版

### 4.3 不要"对比强烈"为了对比而对比
- 黑底白字 + 巨大字体 + 全大写 ≠ "有冲击力"
- 而是装腔作势
- 真正的力量来自**克制的强调** — 整页平静中突然一个大数字才有冲击

### 4.4 不要复制别的网站的样式
- 看到一个好看的网页就模仿它的配色和字体 = 永远做不出自己的东西
- 应该提炼"为什么这样设计有效"，然后用同样的逻辑做你的版本

---

## 终极判断标准

把你的 HTML 截图，问自己：

> "如果不告诉别人是 AI 做的，他们能不能猜出来？"

如果"能猜出来" — 那就还是 slop，回去改。
如果"猜不出来甚至以为是某家专业工作室的作品" — 才算合格。

---

## 参考：AI Slop vs Editorial 对比

| 维度 | AI Slop 风 | Editorial 风 |
|:---|:---|:---|
| 字体 | Inter 全场 | Noto Serif SC + Playfair 大数字 + Noto Sans SC 标签 |
| 配色 | 蓝紫渐变 + 白底 | 墨黑 + 财税红 + 金箔 + 牛皮纸底 |
| 布局 | 居中三栏卡片 | 左 TOC + 右长文，不对称 |
| 卡片 | 白底 + 圆角 + 阴影 | 不同类型用不同处理（边条 / 底色 / 边框） |
| 标题 | sans 粗体 | display 衬线 + 副线 + 章节大编号 |
| 引用 | 灰色边框块 | 大字号衬线 + 金色巨型引号 + 来源 meta |
| 表格 | 浅灰表头 | 暗黑表头 + 斑马纹 + 行内代码 |
| Emoji | 标题前堆一串 | 只用在分类锚点，5-7 个 |
| 动画 | 飞入/弹跳 | 轻微 hover 上移 |
| 阴影 | 同一种阴影到底 | 不同元素不同处理（很多元素根本不用阴影） |
