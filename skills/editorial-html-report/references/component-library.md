# 可复用组件库

每个组件 = HTML 片段 + CSS + 用途说明。**所有组件都在 Nico HTML 实战验证过**。

按章节内容类型选用，**不要全部都用**。一份典型报告用 5-7 个组件足够。

---

## 一、基础骨架

### 1.1 顶部滚动进度条

```html
<div class="progress-bar"><div id="progress"></div></div>
```

```css
.progress-bar {
  position: fixed; top: 0; left: 0; right: 0;
  height: 3px; z-index: 100; background: transparent;
}
.progress-bar > div {
  height: 100%; width: 0;
  background: var(--accent);
  transition: width 0.1s ease;
}
```

```js
window.addEventListener('scroll', () => {
  const h = document.documentElement;
  document.getElementById('progress').style.width =
    (h.scrollTop / (h.scrollHeight - h.clientHeight)) * 100 + '%';
});
```

### 1.2 双栏布局（TOC + 主体）

```html
<div class="layout">
  <nav class="toc">...</nav>
  <main>...</main>
</div>
```

```css
.layout {
  display: grid;
  grid-template-columns: 240px minmax(0, 760px);
  gap: 4rem;
  max-width: 1280px;
  margin: 0 auto;
  padding: 0 2rem;
}

@media (max-width: 980px) {
  .layout {
    grid-template-columns: 1fr;
    gap: 0;
    padding: 0 1.5rem;
  }
  .toc { display: none; }
}
```

### 1.3 侧栏 TOC（scroll-spy 高亮）

```html
<nav class="toc" aria-label="目录">
  <div class="toc-label">CONTENTS / 目录</div>
  <ol>
    <li><a href="#ch1">章节1</a></li>
    <li><a href="#ch2">章节2</a></li>
  </ol>
</nav>
```

```css
.toc {
  position: sticky; top: 2rem;
  align-self: start;
  padding-top: 8rem;
  font-family: var(--sans);
  font-size: 13px;
  line-height: 1.7;
  max-height: calc(100vh - 4rem);
  overflow-y: auto;
}
.toc-label {
  font-size: 10px; letter-spacing: 0.2em;
  text-transform: uppercase;
  color: var(--muted);
  margin-bottom: 1.2rem;
  font-weight: 700;
}
.toc ol { list-style: none; counter-reset: toc; }
.toc li { counter-increment: toc; margin-bottom: 0.6rem; }
.toc a {
  color: var(--ink-soft);
  text-decoration: none;
  display: block;
  padding-left: 1.8rem;
  position: relative;
  transition: color 0.2s;
}
.toc a::before {
  content: counter(toc, decimal-leading-zero);
  position: absolute; left: 0;
  color: var(--gold); font-weight: 700;
}
.toc a:hover, .toc a.active { color: var(--accent); }
.toc a.active::before { color: var(--accent); }
```

```js
const tocLinks = document.querySelectorAll('.toc a');
const sections = document.querySelectorAll('section.chapter');
function syncToc() {
  const scrollY = window.scrollY + 200;
  let current = sections[0]?.id;
  sections.forEach(s => { if (s.offsetTop <= scrollY) current = s.id; });
  tocLinks.forEach(a => {
    a.classList.toggle('active', a.getAttribute('href') === '#' + current);
  });
}
window.addEventListener('scroll', syncToc, { passive: true });
syncToc();
```

### 1.4 封面（大标题 + 元信息）

```html
<header class="cover">
  <div class="cover-label">报告类型 / 子类</div>
  <h1>主标题<br>副标题部分<em>重点词</em></h1>
  <p class="subtitle">一句话概括内容是什么</p>
  <dl class="cover-meta">
    <div><dt>作者</dt><dd>...</dd></div>
    <div><dt>日期</dt><dd>...</dd></div>
    <div><dt>来源</dt><dd>...</dd></div>
    <div><dt>版本</dt><dd>...</dd></div>
  </dl>
</header>
```

```css
.cover {
  border-top: 4px double var(--ink);
  border-bottom: 1px solid var(--border);
  padding: 3rem 0;
  margin-bottom: 4rem;
}
.cover-label {
  font-family: var(--sans);
  font-size: 11px; letter-spacing: 0.25em;
  text-transform: uppercase;
  color: var(--accent); font-weight: 700;
  margin-bottom: 1.5rem;
}
.cover h1 {
  font-family: var(--serif);
  font-weight: 900;
  font-size: clamp(2rem, 4.8vw, 3.4rem);
  line-height: 1.15;
  letter-spacing: -0.01em;
  margin-bottom: 1.2rem;
}
.cover h1 em {
  font-style: normal;
  color: var(--accent);
  font-weight: 900;
}
.cover .subtitle {
  font-size: 1.15rem;
  color: var(--muted);
  margin-bottom: 2.5rem;
  line-height: 1.6;
}
.cover-meta {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 1rem 2rem;
  font-family: var(--sans);
  font-size: 13px;
  padding-top: 1.5rem;
  border-top: 1px solid var(--border);
}
.cover-meta dt {
  font-size: 10px;
  letter-spacing: 0.15em;
  text-transform: uppercase;
  color: var(--muted);
  font-weight: 700;
  margin-bottom: 0.25rem;
}
.cover-meta dd { margin-bottom: 0.5rem; }
```

### 1.5 章节头（大编号 + 标题 + 副线）

```html
<section class="chapter" id="ch1">
  <div class="chapter-header">
    <div class="chapter-num">01</div>
    <h2 class="chapter-title">章节标题</h2>
    <div class="chapter-divider"></div>
  </div>
  <!-- 章节内容 -->
</section>
```

```css
section.chapter {
  margin-bottom: 5rem;
  scroll-margin-top: 2rem;
}
.chapter-header { margin-bottom: 2.5rem; }
.chapter-num {
  font-family: var(--display);
  font-size: 4rem; font-weight: 900;
  color: var(--gold);
  line-height: 0.9;
  letter-spacing: -0.02em;
}
.chapter-title {
  font-family: var(--serif);
  font-size: 2rem; font-weight: 900;
  line-height: 1.25;
  margin-top: 0.5rem;
}
.chapter-divider {
  width: 3rem; height: 2px;
  background: var(--accent);
  margin: 1.2rem 0 0;
}
```

---

## 二、内容组件

### 2.1 电梯演讲卡（摘要 / TL;DR）

```html
<div class="elevator">
  <p>摘要文字，<strong>重点用 strong 标记</strong>，会自动加金色下划色块。</p>
</div>
```

```css
.elevator {
  background: linear-gradient(180deg, var(--paper-warm) 0%, var(--paper) 100%);
  border-left: 5px solid var(--accent);
  padding: 2.5rem;
  position: relative;
}
.elevator::before {
  content: "100 字";   /* 可自定义 - "摘要" / "TL;DR" / "100字" */
  position: absolute;
  top: -12px; left: 2rem;
  background: var(--accent);
  color: white;
  font-family: var(--sans);
  font-size: 11px;
  letter-spacing: 0.15em;
  font-weight: 700;
  padding: 4px 12px;
  border-radius: 2px;
}
.elevator p {
  font-family: var(--serif);
  font-size: 1.15rem;
  line-height: 1.85;
}
.elevator strong {
  color: var(--accent);
  font-weight: 700;
  background: linear-gradient(180deg, transparent 65%, var(--gold-soft) 65%);
  padding: 0 2px;
}
```

### 2.2 论点卡片网格（核心观点 / 议程）

```html
<div class="thesis-grid">
  <div class="thesis-card">
    <div class="thesis-num">01</div>
    <div class="thesis-body">
      <h3>论点标题</h3>
      <p>论点简介，1-2 行</p>
      <span class="arg">论证方式 · 案例 / 数据</span>
    </div>
  </div>
</div>
```

```css
.thesis-grid { display: grid; gap: 1rem; }
.thesis-card {
  display: grid;
  grid-template-columns: 80px 1fr;
  gap: 1.5rem;
  padding: 1.5rem;
  background: white;
  border: 1px solid var(--border);
  border-radius: 6px;
  transition: transform 0.2s, box-shadow 0.2s;
}
.thesis-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 16px rgba(0,0,0,0.08);
  border-color: var(--gold);
}
.thesis-num {
  font-family: var(--display);
  font-size: 3.5rem; font-weight: 900;
  color: var(--gold);
  text-align: center;
  line-height: 0.9;
}
.thesis-body h3 {
  font-family: var(--serif);
  font-size: 1.15rem; font-weight: 700;
  margin-bottom: 0.5rem;
}
.thesis-body p {
  font-size: 15px;
  color: var(--ink-soft);
  line-height: 1.7;
}
.thesis-body .arg {
  display: inline-block;
  margin-top: 0.5rem;
  font-family: var(--sans);
  font-size: 12px;
  color: var(--muted);
  padding: 2px 8px;
  background: var(--paper-warm);
  border-radius: 3px;
}
```

### 2.3 行动清单（4 档分级）

```html
<div class="action-group">
  <div class="action-group-header">
    <span class="action-badge a">A</span>
    <div>
      <div class="action-group-title">立即可做</div>
      <div class="action-group-sub">无需准备资源</div>
    </div>
  </div>
  <ul class="action-list a">
    <li><strong>动作1</strong>说明<span class="cite">P页码</span></li>
    <li>动作2，可嵌套子列表：
      <ul>
        <li>子项1</li>
        <li>子项2</li>
      </ul>
    </li>
  </ul>
</div>
```

```css
.action-group { margin-bottom: 2rem; }
.action-group-header {
  display: flex;
  align-items: baseline;
  gap: 1rem;
  margin-bottom: 1.2rem;
  padding-bottom: 0.5rem;
  border-bottom: 1px solid var(--border);
}
.action-badge {
  font-family: var(--display);
  font-size: 2rem; font-weight: 900;
  line-height: 1;
}
.action-badge.a { color: var(--green); }
.action-badge.b { color: var(--amber); }
.action-badge.c { color: var(--indigo); }
.action-badge.d { color: var(--danger); }
.action-group-title {
  font-family: var(--serif);
  font-size: 1.1rem; font-weight: 700;
}
.action-group-sub {
  font-family: var(--sans);
  font-size: 12px;
  color: var(--muted);
}
.action-list { list-style: none; }
.action-list li {
  padding: 0.9rem 1rem 0.9rem 2.5rem;
  position: relative;
  font-size: 15.5px;
  line-height: 1.7;
  border-left: 3px solid transparent;
  background: white;
  margin-bottom: 0.5rem;
  border-radius: 0 4px 4px 0;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06);
}
.action-list.a li { border-left-color: var(--green); }
.action-list.b li { border-left-color: var(--amber); }
.action-list.c li { border-left-color: var(--indigo); }
.action-list.d li {
  border-left-color: var(--danger);
  background: #fef5f4;
}
.action-list li::before {
  content: "☐";
  position: absolute;
  left: 1rem; top: 0.65rem;
  color: var(--muted);
  font-size: 18px;
}
.action-list.d li::before {
  content: "✕";
  color: var(--danger);
  font-weight: 900;
}
.action-list li strong { color: var(--accent); font-weight: 700; }
.cite {
  font-family: var(--sans);
  font-size: 11px;
  color: var(--muted);
  background: var(--paper-warm);
  padding: 1px 6px;
  border-radius: 3px;
  margin-left: 0.5rem;
  white-space: nowrap;
}
```

### 2.4 趋势 bucket（短中长期）

```html
<div class="trend-bucket short">
  <div class="trend-bucket-header"><span class="trend-icon">🔥</span>短期信号</div>
  <div class="trend-card">
    <h4>信号 1 ｜ 标题</h4>
    <div class="trend-quote">引用的原话<span class="cite">P页码</span></div>
    <dl class="trend-meta">
      <dt>影响</dt><dd>...</dd>
      <dt>动作</dt><dd>...</dd>
    </dl>
  </div>
</div>
```

```css
.trend-bucket { margin-bottom: 2rem; }
.trend-bucket-header {
  display: flex; align-items: center; gap: 0.8rem;
  margin-bottom: 1rem;
  padding: 0.6rem 1rem;
  border-radius: 4px;
  font-family: var(--sans);
  font-weight: 700;
}
.trend-bucket.short .trend-bucket-header { background: #fef2ed; color: var(--accent); }
.trend-bucket.mid .trend-bucket-header { background: #fef4e1; color: var(--amber); }
.trend-bucket.long .trend-bucket-header { background: #eef2fb; color: var(--indigo); }
.trend-icon { font-size: 1.3rem; }
.trend-card {
  background: white;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 1.5rem;
  margin-bottom: 1rem;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06);
}
.trend-card h4 {
  font-family: var(--serif);
  font-size: 1.05rem;
  font-weight: 700;
  margin-bottom: 0.75rem;
}
.trend-quote {
  font-family: var(--serif);
  font-style: italic;
  color: var(--ink-soft);
  background: var(--paper-warm);
  border-left: 3px solid var(--gold);
  padding: 0.8rem 1rem;
  margin: 0.6rem 0;
  font-size: 14.5px;
  border-radius: 0 4px 4px 0;
}
.trend-meta {
  display: grid;
  grid-template-columns: 90px 1fr;
  gap: 0.5rem 1rem;
  font-size: 14px;
  margin-top: 0.6rem;
}
.trend-meta dt {
  font-family: var(--sans);
  font-size: 11px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--muted);
  font-weight: 700;
  padding-top: 3px;
}
```

### 2.5 警告框（时效 / 风险提醒）

```html
<div class="time-warn">
  <div class="time-warn-title">⚠ 时效性提醒</div>
  <p>说明文字</p>
  <ul>
    <li>需要持续关注的点</li>
  </ul>
</div>
```

```css
.time-warn {
  background: #fffbeb;
  border: 1px solid #fde68a;
  border-radius: 6px;
  padding: 1.2rem 1.5rem;
  margin-top: 2rem;
}
.time-warn-title {
  font-family: var(--sans);
  font-weight: 700;
  color: var(--amber);
  margin-bottom: 0.5rem;
  display: flex; align-items: center;
  gap: 0.5rem;
}
.time-warn ul { list-style: none; padding-left: 0; }
.time-warn li {
  padding: 0.3rem 0 0.3rem 1.5rem;
  position: relative;
  font-size: 14.5px;
}
.time-warn li::before {
  content: "→";
  position: absolute; left: 0;
  color: var(--amber);
}
```

### 2.6 术语表（暗色表头斑马纹）

```html
<div class="glossary-wrap">
  <table class="glossary">
    <thead>
      <tr><th>术语</th><th>解释</th><th>类比 / 关键说明</th></tr>
    </thead>
    <tbody>
      <tr><td class="term">术语 A</td><td>解释</td><td>类比</td></tr>
    </tbody>
  </table>
</div>
```

```css
.glossary-wrap { overflow-x: auto; }
table.glossary {
  width: 100%;
  border-collapse: collapse;
  font-size: 14.5px;
  background: white;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06);
  border-radius: 6px;
  overflow: hidden;
}
table.glossary th {
  background: var(--ink);
  color: white;
  padding: 0.8rem 1rem;
  text-align: left;
  font-family: var(--sans);
  font-size: 12px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  font-weight: 700;
}
table.glossary td {
  padding: 0.85rem 1rem;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
  line-height: 1.65;
}
table.glossary tr:nth-child(even) td { background: #fafaf5; }
.term {
  font-family: var(--sans);
  font-weight: 700;
  color: var(--accent);
  white-space: nowrap;
}
```

### 2.7 金句墙（杂志风 drop quote）

```html
<div class="quote-wall">
  <div class="quote">
    <blockquote>原话引用，要够长，1-3 行。</blockquote>
    <div class="quote-meta">— 作者 · 页码</div>
    <div class="quote-ctx"><strong>上下文</strong>这句话出现的场景</div>
    <div class="quote-val"><strong>价值</strong>为什么值得记住</div>
  </div>
</div>
```

```css
.quote-wall .quote {
  margin-bottom: 2.5rem;
  position: relative;
  padding: 1.5rem 0 1.5rem 3rem;
}
.quote::before {
  content: "“";
  position: absolute; left: 0; top: -1rem;
  font-family: var(--display);
  font-size: 5rem;
  color: var(--gold);
  line-height: 1;
}
.quote blockquote {
  font-family: var(--serif);
  font-size: 1.3rem;
  font-weight: 700;
  line-height: 1.65;
  margin-bottom: 0.8rem;
  letter-spacing: -0.005em;
}
.quote .quote-meta {
  font-family: var(--sans);
  font-size: 13px;
  color: var(--accent);
  margin-bottom: 0.8rem;
  font-weight: 600;
}
.quote .quote-ctx,
.quote .quote-val {
  font-size: 14.5px;
  color: var(--ink-soft);
  line-height: 1.7;
  margin-bottom: 0.4rem;
}
.quote .quote-ctx strong,
.quote .quote-val strong {
  font-family: var(--sans);
  font-size: 11px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--muted);
  margin-right: 0.4rem;
}
```

### 2.8 Persona / 情境卡片

```html
<div class="persona-thinks">
  <div class="persona-think">
    <h4>如果你是<em>初创卖家</em></h4>
    <p>建议内容...</p>
  </div>
</div>
```

```css
.persona-thinks { display: grid; gap: 1rem; }
.persona-think {
  background: white;
  border: 1px solid var(--border);
  border-left: 4px solid var(--gold);
  border-radius: 0 6px 6px 0;
  padding: 1.2rem 1.5rem;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06);
}
.persona-think h4 {
  font-family: var(--serif);
  font-size: 1rem;
  font-weight: 700;
  margin-bottom: 0.4rem;
}
.persona-think h4 em {
  color: var(--accent);
  font-style: normal;
}
.persona-think p {
  font-size: 14.5px;
  color: var(--ink-soft);
  line-height: 1.7;
}
```

### 2.9 折叠面板（附录 / 长尾内容）

```html
<details class="appendix" open>
  <summary>展开 N 条原文摘录</summary>
  <div class="appendix-body">
    <ul>
      <li><span class="page">P页码</span>内容</li>
    </ul>
  </div>
</details>
```

```css
details.appendix {
  margin-top: 2rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: white;
}
details.appendix summary {
  cursor: pointer;
  padding: 1rem 1.5rem;
  font-family: var(--sans);
  font-weight: 700;
  list-style: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
details.appendix summary::-webkit-details-marker { display: none; }
details.appendix summary::after {
  content: "+";
  font-size: 1.5rem;
  color: var(--accent);
  transition: transform 0.2s;
}
details.appendix[open] summary::after {
  transform: rotate(45deg);
}
details.appendix .appendix-body {
  padding: 0 1.5rem 1.5rem;
  border-top: 1px dashed var(--border);
}
details.appendix ul { list-style: none; padding-left: 0; }
details.appendix ul li {
  padding: 0.5rem 0 0.5rem 4rem;
  position: relative;
  font-size: 14px;
  line-height: 1.7;
  border-bottom: 1px dashed var(--border);
}
details.appendix ul li:last-child { border-bottom: none; }
details.appendix ul li .page {
  position: absolute; left: 0; top: 0.5rem;
  font-family: var(--sans);
  font-size: 11px;
  color: var(--gold);
  font-weight: 700;
  letter-spacing: 0.05em;
}
```

### 2.10 推荐衔接卡片

```html
<div class="recommend-block">
  <h4>分类标题</h4>
  <ul>
    <li>推荐项 1</li>
    <li>推荐项 2</li>
  </ul>
</div>
```

```css
.recommend-block {
  background: white;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 1.5rem;
  margin-bottom: 1rem;
}
.recommend-block h4 {
  font-family: var(--sans);
  font-size: 13px;
  font-weight: 700;
  color: var(--accent);
  letter-spacing: 0.1em;
  text-transform: uppercase;
  margin-bottom: 0.8rem;
}
.recommend-block ul { list-style: none; padding-left: 0; }
.recommend-block li {
  padding: 0.4rem 0 0.4rem 1.2rem;
  position: relative;
  font-size: 14.5px;
}
.recommend-block li::before {
  content: "▸";
  position: absolute; left: 0;
  color: var(--gold);
}
```

### 2.11 标签（tag）

```html
<span class="tag tag-good">✓ 好</span>
<span class="tag tag-warn">⚠ 注意</span>
<span class="tag tag-danger">✕ 风险</span>
```

```css
.tag {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 12px;
  font-family: var(--sans);
  font-size: 12px;
  margin-right: 0.5rem;
  font-weight: 500;
}
.tag-good { background: #e8f3ee; color: var(--green); }
.tag-warn { background: #fdf2e1; color: var(--amber); }
.tag-danger { background: #fde8e6; color: var(--danger); }
```

### 2.12 页脚（footer）

```html
<footer>
  <dl class="meta-grid">
    <div><dt>生成方式</dt><dd>...</dd></div>
    <div><dt>来源</dt><dd>...</dd></div>
    <div><dt>原文件</dt><dd>...</dd></div>
  </dl>
  <p>免责声明 / 版权说明</p>
</footer>
```

```css
footer {
  border-top: 1px solid var(--border);
  padding-top: 2rem;
  margin-top: 4rem;
  font-family: var(--sans);
  font-size: 12px;
  color: var(--muted);
  text-align: center;
  line-height: 1.7;
}
footer .meta-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 1rem;
  text-align: left;
  padding: 1.5rem 0;
  border-bottom: 1px solid var(--border);
  margin-bottom: 1rem;
}
footer dt {
  font-weight: 700;
  color: var(--ink-soft);
  margin-bottom: 0.2rem;
}
```

---


### 2.13 SVG 折线图（时间序列）

用于月度趋势、周维度曲线等 ≥6 个数据点的时间序列。**不引外部图表库**，内联 SVG 即可。

```html
<figure class="chart-block">
  <figcaption>类目月度销量趋势（2024-07 ~ 2026-06，2025-01=100）</figcaption>
  <svg viewBox="0 0 760 200" class="line-chart" role="img"
       aria-label="24个月销量指数趋势，8月为峰值，当前2026年7月">
    <!-- 基准线 100 -->
    <line x1="0" y1="100" x2="760" y2="100" class="baseline"/>
    <text x="4" y="96" class="axis-label">100</text>
    <!-- 数据折线：points = 各月 (x, 200-指数) -->
    <polyline class="series" fill="none" points="0,93 33,79 66,100 99,113 …"/>
    <!-- 峰值标注 -->
    <circle cx="33" cy="79" r="4" class="peak"/>
    <text x="33" y="70" class="peak-label" text-anchor="middle">8月 120.8</text>
    <!-- 当前月竖虚线 -->
    <line x1="726" y1="0" x2="726" y2="200" class="now-line"/>
    <text x="726" y="16" class="now-label" text-anchor="end">今天</text>
  </svg>
</figure>
```

```css
.chart-block{margin:2rem 0}
.chart-block figcaption{font-size:.85rem;color:var(--muted);margin-bottom:.5rem}
.line-chart{width:100%;height:auto;overflow:visible}
.line-chart .series{stroke:var(--accent);stroke-width:2;stroke-linejoin:round}
.line-chart .baseline{stroke:var(--border);stroke-dasharray:3 3}
.line-chart .peak{fill:var(--accent)}
.line-chart .peak-label{font-size:11px;fill:var(--accent);font-weight:600}
.line-chart .now-line{stroke:var(--warn);stroke-dasharray:4 4}
.line-chart .now-label{font-size:11px;fill:var(--warn)}
.line-chart .axis-label{font-size:10px;fill:var(--muted)}
```

**要点**：`viewBox` 自适应不写死宽高；`aria-label` 必填并写清结论；**峰值与当前位置必须标注**（不标就等于没给决策信息）。原始数据可另放一个 `<details>` 折叠表供核对。

---

### 2.14 CSS 横向条形图（占比 / 横比）

用于 Top-N 份额、集中度、多对象单指标对比。纯 CSS，无 JS。

```html
<div class="bar-chart">
  <div class="bar-row">
    <span class="bar-label">Top3 产品份额</span>
    <div class="bar-track"><div class="bar-fill" style="--v:27.3%"></div></div>
    <span class="bar-value">27.3%</span>
  </div>
  <div class="bar-row">
    <span class="bar-label">Top3 品牌份额</span>
    <div class="bar-track"><div class="bar-fill" style="--v:38.4%"></div></div>
    <span class="bar-value">38.4%</span>
  </div>
  <div class="bar-row">
    <span class="bar-label">Amazon 自营</span>
    <div class="bar-track"><div class="bar-fill low" style="--v:1.2%"></div></div>
    <span class="bar-value">1.2%</span>
  </div>
</div>
```

```css
.bar-chart{display:flex;flex-direction:column;gap:.6rem;margin:1.5rem 0}
.bar-row{display:grid;grid-template-columns:minmax(7rem,10rem) 1fr auto;
         align-items:center;gap:.75rem}
.bar-label{font-size:.9rem;color:var(--muted)}
.bar-track{height:.6rem;background:var(--border);border-radius:99px;overflow:hidden}
.bar-fill{height:100%;width:var(--v);background:var(--accent);border-radius:99px}
.bar-fill.low{background:var(--muted)}
.bar-fill.warn{background:var(--warn)}
.bar-value{font-variant-numeric:tabular-nums;font-weight:600;font-size:.9rem}
@media(max-width:620px){.bar-row{grid-template-columns:1fr auto}
  .bar-track{grid-column:1/-1;order:3}}
```

**要点**：条长必须等于数值比例，不能只当装饰；数值用 `tabular-nums` 对齐；超阈值的条用 `.warn` 变色。

---

### 2.15 区间条 + 阈值标线

用于"下限-上限"型数据（补货提前期 58-86 天、盈亏 ACOS 区间、价格带），配一条阈值参照线。

```html
<div class="range-block">
  <div class="range-head"><span>补货总提前期</span><b>58 – 86 天</b></div>
  <div class="range-track">
    <div class="range-span" style="--from:38%;--to:57%"></div>
    <div class="range-mark" style="--at:40%"><span>首批覆盖 60 天</span></div>
  </div>
  <p class="range-note">覆盖 60 天 &lt; 最坏提前期 86 天 → 存在 26 天断货窗口</p>
</div>
```

```css
.range-block{margin:1.5rem 0}
.range-head{display:flex;justify-content:space-between;align-items:baseline;
            font-size:.9rem;margin-bottom:.5rem}
.range-track{position:relative;height:1.6rem;background:var(--border);border-radius:6px}
.range-span{position:absolute;left:var(--from);right:calc(100% - var(--to));
            top:0;bottom:0;background:var(--accent);opacity:.75;border-radius:6px}
.range-mark{position:absolute;left:var(--at);top:-.3rem;bottom:-.3rem;
            border-left:2px dashed var(--warn)}
.range-mark span{position:absolute;left:.4rem;top:-1.2rem;font-size:.72rem;color:var(--warn)}
.range-note{font-size:.82rem;color:var(--muted);margin-top:.5rem}
```

**要点**：`range-note` 必须写**结论**（"存在 26 天断货窗口"），不能只画区间让读者自己比。

---

### 2.16 横向时间轴

用于有先后顺序的决策节点：最晚下单日 vs 今天、0-7/8-30/31-60 天节奏、14 天广告调优节点。

```html
<ol class="timeline">
  <li class="tl-item"><span class="tl-dot"></span>
    <div><b>2026-05-07 ~ 06-04</b><p>最晚下单区间（8月峰值 − 58~86天提前期）</p></div></li>
  <li class="tl-item is-now"><span class="tl-dot"></span>
    <div><b>2026-07-26 · 今天</b><p class="tl-warn">已过下单窗口 → 2026 旺季来不及</p></div></li>
  <li class="tl-item is-future"><span class="tl-dot"></span>
    <div><b>2026-08</b><p>类目峰值月（指数 117–121）</p></div></li>
</ol>
```

```css
.timeline{list-style:none;padding:0;margin:1.5rem 0;position:relative}
.timeline::before{content:"";position:absolute;left:.45rem;top:.5rem;bottom:.5rem;
                  width:2px;background:var(--border)}
.tl-item{position:relative;padding-left:2rem;margin-bottom:1.2rem}
.tl-dot{position:absolute;left:0;top:.35rem;width:1rem;height:1rem;border-radius:50%;
        background:var(--bg);border:2px solid var(--border)}
.tl-item.is-now .tl-dot{border-color:var(--warn);background:var(--warn)}
.tl-item.is-future .tl-dot{border-style:dashed}
.tl-item b{font-variant-numeric:tabular-nums}
.tl-item p{margin:.2rem 0 0;font-size:.88rem;color:var(--muted)}
.tl-warn{color:var(--warn)!important;font-weight:600}
```

**要点**：**"今天"必须单独标色**；已错过的窗口用 `.tl-warn` 写明后果，不能只列日期。

---


### 2.17 大表工具条 + 下载按钮（长明细表必备）

用于全量词表、竞品明细、SKU 清单等**行数 >50** 的表格。零 JS，`<a download>` 原生实现。

```html
<div class="table-toolbar">
  <span class="tt-count">全量保留候选词 <b>1,445</b> 条</span>
  <a class="btn-download" href="../06_cleaned_sif_keywords.csv" download>⤓ 下载完整 CSV</a>
</div>
<div class="table-wrap">
  <table class="data-table">…</table>
</div>
```

```css
.table-toolbar{display:flex;justify-content:space-between;align-items:center;
               gap:1rem;flex-wrap:wrap;margin:1.5rem 0 .5rem}
.tt-count{font-size:.9rem;color:var(--muted)}
.tt-count b{color:var(--ink);font-variant-numeric:tabular-nums}
.btn-download{display:inline-flex;align-items:center;gap:.4rem;
              padding:.4rem .9rem;font-size:.85rem;text-decoration:none;
              color:var(--accent);border:1px solid var(--accent);border-radius:4px}
.btn-download:hover{background:var(--accent);color:var(--paper)}
.table-wrap{overflow-x:auto;max-height:70vh;overflow-y:auto}
.table-wrap thead th{position:sticky;top:0;background:var(--paper);z-index:1}
@media print{.btn-download{display:none}.table-wrap{max-height:none;overflow:visible}}
```

**三条要求**：

1. **行数 >50 的表必须配下载按钮**，指向同 run 内的 CSV/XLSX 相对路径 —— 用户要自己下载核对与复用，在网页里翻上千行不现实
2. 表头 `position:sticky` 固定，长表滚动时列名不丢
3. 多维标签**必须分列**（如六维 D1–D6 各占一列），不得挤一列用逗号连接 —— 挤在一起既不能排序也不能筛选

---

### 2.18 高重复表压缩（共性提头 + 差异成网格）

实测反馈：*"往下拉看起来很重复，除了词不一样全部都一样，视觉疲劳。"*
品牌隔离词表 100+ 行，「命中原因」「动作」两列**每行完全相同**。

**判定规则**：表中某列取值重复率 **>80%** 时，该列**不得逐行重复显示**。

```html
<div class="rule-banner">
  <div class="rb-item"><b>命中原因</b><span>Q-tip 变体 / 品牌风险</span></div>
  <div class="rb-item"><b>统一动作</b><span>不进入 Title、Highlights、ST 或普通广告；保留在合规日志</span></div>
  <div class="rb-count">共 <b>127</b> 词</div>
</div>
<div class="term-grid">
  <span>q-tip holder</span><span>qtips holder for bathroom</span><span>q tip jar</span>
  <span>qtip holder</span><span>q tips holder</span><span>q tip container</span>
</div>
```

```css
.rule-banner{display:flex;flex-wrap:wrap;gap:1.2rem;align-items:baseline;
             padding:.9rem 1.1rem;margin:1.2rem 0 .8rem;
             background:var(--cream,#f6f2ea);border-left:3px solid var(--accent);border-radius:4px}
.rb-item{font-size:.88rem}
.rb-item b{display:block;font-size:.72rem;letter-spacing:.06em;
           text-transform:uppercase;color:var(--muted);margin-bottom:.15rem}
.rb-count{margin-left:auto;font-size:.85rem;color:var(--muted)}
.rb-count b{color:var(--accent);font-variant-numeric:tabular-nums}
.term-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(13rem,1fr));
           gap:.35rem .9rem;font-size:.85rem;line-height:1.5}
.term-grid span{padding:.15rem 0;border-bottom:1px dotted var(--border)}
@media(max-width:620px){.term-grid{grid-template-columns:1fr 1fr}}
```

**要点**：

- **共性提到 banner 说一次，表体只放差异部分** —— 127 行 × 3 列压成 1 个 banner + 1 个词网格
- 词量大时用 `auto-fill` 多列网格，同屏能看 5–8 倍的量
- 若差异列不止一个（如词 + 搜索量），仍用表格但只保留有差异的列

> 判断口径：**"这一列每行都一样"就是它不该逐行出现的信号。** 读者已经在表头知道了规则，重复 127 遍只制造疲劳。

---


### 2.19 场景卡（场景库专用）

用于 `06B_场景库.yaml` 的呈现。一个场景一张卡，五段固定结构。

```html
<div class="scene-grid">
  <div class="scene-card">
    <div class="sc-hd">
      <div class="sc-name">办公隔间隐私 <em>Cubicle Privacy</em></div>
      <span class="sc-tag">潜在卖点</span>
    </div>
    <dl class="sc-meta">
      <dt>人群</dt><dd>开放办公/居家办公者、需谈话隐私的治疗师</dd>
      <dt>场景</dt><dd>工位墙插、诊室、会议隔间</dd>
      <dt>痛点</dt><dd>同事对话干扰专注 / 谈话被隔壁听到
        <span class="sc-src" data-ev="voc">VOC 41 次</span></dd>
    </dl>
    <div class="sc-benefit">
      <div class="sc-bt">利益点 →</div>
      <ul>
        <li>盖住键盘声和对话，专注不被打断</li>
        <li>为咨询谈话加一层「声音隐私」</li>
        <li>插墙即用，不占桌面</li>
      </ul>
    </div>
    <div class="sc-terms"><b>搜索词</b>
      office sound machine for privacy <i>2.1万</i> ·
      speech privacy machine <i>3,400</i> ·
      white noise for focus work <i>8,900</i></div>
  </div>
</div>
```

```css
.scene-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));
            gap:1rem;margin:1.4rem 0 1.8rem}
.scene-card{background:#fff;border:1px solid var(--border);border-radius:8px;
            padding:1.1rem 1.2rem;box-shadow:var(--sh-sm,0 1px 3px rgba(0,0,0,.07))}
.sc-hd{display:flex;align-items:flex-start;gap:.7rem;margin-bottom:.8rem}
.sc-name{font-family:var(--serif);font-size:1.06rem;font-weight:700;line-height:1.4}
.sc-name em{font-style:normal;color:var(--accent);font-size:.94em;margin-left:.35rem}
.sc-tag{margin-left:auto;flex:0 0 auto;font-size:11px;padding:.16rem .55rem;border-radius:4px;
        background:#fdf3e0;color:#a1690f;font-weight:700;white-space:nowrap}
.sc-tag[data-t="空白机会"]{background:#eaf6ef;color:#166534}
.sc-tag[data-t="竞品已占"]{background:#f4f1ee;color:#7a7268}
.sc-meta{display:grid;grid-template-columns:3.2rem 1fr;gap:.3rem .7rem;
         font-size:13.5px;line-height:1.65;margin-bottom:.85rem}
.sc-meta dt{color:var(--muted);font-weight:700}
.sc-meta dd{margin:0}
.sc-src{display:inline-block;margin-left:.4rem;font-size:10.5px;padding:.05rem .4rem;
        border-radius:3px;background:#eef4f8;color:#42627a;font-weight:700;vertical-align:1px}
.sc-src[data-ev="inferred"]{background:#fdeeee;color:#a33}
.sc-benefit{background:#eef5f9;border-radius:6px;padding:.75rem .9rem;margin-bottom:.75rem}
.sc-bt{font-size:12px;font-weight:800;color:var(--accent);letter-spacing:.06em;margin-bottom:.4rem}
.sc-benefit ul{margin:0;padding-left:1.1rem;font-size:13.5px;line-height:1.7}
.sc-terms{font-size:12.5px;color:var(--muted);line-height:1.7}
.sc-terms b{color:var(--ink);margin-right:.4rem}
.sc-terms i{font-style:normal;color:var(--accent);font-size:.92em}
@media(max-width:640px){.scene-grid{grid-template-columns:1fr}}
```

**四条要求**：

1. **痛点必须带来源角标** —— `data-ev="voc"` 蓝底、`"keyword"` 蓝底、`"inferred"` **红底**。
   红角标多了读者一眼就知道这份场景库是想出来的不是查出来的。
2. **搜索词必须带搜索量** —— 没有量的词不进卡片，进「孤儿词」清单
3. **利益点 ≤3 条** —— 多了就不是场景卡是卖点堆
4. **标签用 `data-t` 区分配色** —— 空白机会绿、竞品已占灰，扫一眼能看出机会分布

> 场景卡是**唯一同时给文案、视觉、广告三段看的东西**，所以它必须自解释：
> 不看 YAML 只看这张卡，就能知道该写什么、该拍什么、该投什么。

---

## 三、组件组合策略

### 短文档（< 1500 字）
- 封面（1.4） + 章节头（1.5）+ 内容组件（按需）+ 页脚（2.12）
- 不需要 TOC、不需要进度条

### 中等文档（1500-5000 字）
- 进度条（1.1）+ 封面（1.4）+ 章节头（1.5）+ 3-5 个内容组件 + 页脚（2.12）
- TOC 可选

### 长文档（> 5000 字）
- 全部基础骨架（1.1-1.5）
- 内容组件按章节类型混搭
- 长尾内容用 2.9 折叠
- 页脚（2.12）

---

## 四、组件搭配的不要做的事

- ❌ 一份报告 12 个章节 = 12 个不同组件（眼花）
- ❌ 一份报告 12 个章节 = 1 种卡片（无聊）
- ✅ 一份报告 12 个章节 = 5-7 种组件循环使用，每种重复 2-3 次
