# -*- coding: utf-8 -*-
"""
沙龙 HTML 报告 · 统一渲染组件库
================================
⛔ 存在的理由：本 Skill 原来只有 SKILL.md，没有任何模板资产 ——
   于是每跑一次，模型就得现场重写一套样式。上下文一压缩，写出来的就缩水。
   实测后果（2026-08-07）：同一个 run 里三份报告三套 CSS 变量，
   S4 广告架构只有 17KB / 6 section，契约下限是 50KB / 12 section。

用法：
    from salon_report import Report
    r = Report("标题", "副标题", meta=[("run","..."), ...])
    s = r.section("s-gate", "一、量化闸门")
    s.kpis([("SP Campaign","4",None,"ok"), ...])
    s.table(["项","值"], [["a","b"]])
    r.write("out.html")

三条布局硬规则（SKILL.md §4.0，已内建，⛔ 不要改）：
  ① body 用 overflow-x:clip，⛔ 不用 hidden（hidden 会夺走 sticky 的定位基准）
  ② .layout 限宽并居中，⛔ 限宽不放在 .main 上
  ③ 每个 <table> 必包 .tablewrap —— 本库由 table() 自动完成，漏不了
"""
import io, os, re, html
import base64
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urlsplit

CSS = """
:root{--bg:#f5f7fb;--card:#fff;--ink:#182433;--muted:#667085;--line:#dbe3ec;
--nav:#182433;--nav2:#22303f;--accent:#0f766e;--accent-bg:#e6f5f3;
--blue:#2563eb;--blue-bg:#eaf1fe;--warn:#b45309;--warn-bg:#fdf3e6;
--danger:#b42318;--danger-bg:#fdeceb;--ok:#177245;--ok-bg:#e8f5ee;
--radius:10px;--shadow:0 1px 3px rgba(24,36,51,.06),0 8px 24px rgba(24,36,51,.05)}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);overflow-x:clip;
 font:14px/1.62 var(--body-font)}
code,.mono{font-family:var(--code-font);font-size:.92em}
code{background:#eef2f7;padding:1px 5px;border-radius:4px}

/* ② .layout 限宽居中 —— ⛔ 不要把 max-width 挪到 .main */
.layout{display:grid;grid-template-columns:262px minmax(0,1fr);gap:26px;
 max-width:1780px;margin:0 auto;padding:22px 24px 90px}
.main{width:100%;min-width:0}
@media(max-width:980px){.layout{grid-template-columns:1fr;padding:16px}.side{display:none}}

.side{position:sticky;top:22px;align-self:start;max-height:calc(100vh - 44px);overflow:auto}
.side .box{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
 padding:14px 12px;box-shadow:var(--shadow)}
.side a{display:block;padding:6px 10px;border-radius:7px;text-decoration:none;color:var(--muted);
 font-size:13px;border-left:3px solid transparent;margin-bottom:1px}
.side a:hover{background:#f1f5f9;color:var(--ink)}
.side a.on{background:var(--accent-bg);color:var(--accent);border-left-color:var(--accent);font-weight:600}
.side .lbl{font-size:11px;letter-spacing:2px;color:#9aa5b4;padding:8px 10px 4px}
.side .brand{font-size:11px;color:#9aa5b4;padding:0 10px 8px}

.hero{background:linear-gradient(135deg,var(--nav),var(--nav2));color:#fff;
 border-radius:var(--radius);padding:26px 30px;margin-bottom:22px;box-shadow:var(--shadow)}
.hero h1{margin:0 0 6px;font-size:27px;line-height:1.3}
.hero .sub{color:#b6c2d1;font-size:15px}
.hero .meta{margin-top:14px;display:flex;flex-wrap:wrap;gap:8px}
.hero .meta span{background:rgba(255,255,255,.11);border:1px solid rgba(255,255,255,.16);
 border-radius:20px;padding:3px 12px;font-size:12px}

section{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
 padding:22px 26px;margin-bottom:20px;box-shadow:var(--shadow);scroll-margin-top:20px}
section>h2{margin:0 0 4px;font-size:20px;line-height:1.35}
section>.note{color:var(--muted);font-size:13.5px;margin:0 0 16px}
h3{font-size:15px;margin:20px 0 8px}

.kpirow{display:grid;grid-template-columns:repeat(auto-fit,minmax(158px,1fr));gap:12px;margin:12px 0}
.kpi{background:#fafbfd;border:1px solid var(--line);border-radius:9px;padding:12px 14px}
.kpi .k{font-size:11.5px;letter-spacing:.6px;color:var(--muted)}
.kpi .v{font-size:25px;font-weight:700;line-height:1.25;font-variant-numeric:tabular-nums}
.kpi .s{font-size:11.5px;color:var(--muted);margin-top:2px}
.kpi.ok .v{color:var(--ok)} .kpi.warn .v{color:var(--warn)}
.kpi.danger .v{color:var(--danger)} .kpi.blue .v{color:var(--blue)}

.barrow{margin:9px 0}
.barrow .top{display:flex;justify-content:space-between;font-size:13px;margin-bottom:4px;gap:12px}
.barrow .top b{font-variant-numeric:tabular-nums;white-space:nowrap}
.bar-track{height:9px;background:#eef2f7;border-radius:6px;overflow:hidden}
.bar-fill{height:100%;border-radius:6px;background:var(--accent)}
.bar-fill.blue{background:var(--blue)} .bar-fill.warn{background:var(--warn)}
.bar-fill.danger{background:var(--danger)} .bar-fill.muted{background:#9aa5b4}

/* ③ 每个表都由 table() 包进 .tablewrap */
.tablewrap{max-width:100%;overflow:auto;border:1px solid var(--line);border-radius:9px;margin:11px 0}
/* 行数多的表给一个滚动窗口，配合吸顶表头 —— ⛔ 没有 max-height 时 sticky 不生效 */
.tablewrap.tall{max-height:74vh}
table{width:100%;min-width:680px;border-collapse:separate;border-spacing:0;font-size:13.5px}
.grid2 .tablewrap table,.grid3 .tablewrap table{min-width:420px}
th,td{padding:8px 12px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}
/* 3.0.25: a long second column squeezed the label column to one character per line (判/定/项/目). */
th:first-child,td:first-child{min-width:6em}
th{background:#eef3f9;font-weight:600;color:#33455c;font-size:12.5px;white-space:nowrap}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover td{background:#fafbfd}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}

/* ⭐ 表头吸顶：往下翻还能看见哪列是哪列 */
thead th{position:sticky;top:0;z-index:3;box-shadow:inset 0 -1px 0 var(--line)}
/* ⭐ 首列冻结：横向拉的时候还知道这行是哪个词 */
.tablewrap.freeze tbody td:first-child,
.tablewrap.freeze thead th:first-child{position:sticky;left:0;z-index:2;background:var(--card)}
.tablewrap.freeze thead th:first-child{z-index:4;background:#eef3f9}
.tablewrap.freeze tbody tr:hover td:first-child{background:#fafbfd}
.tablewrap.freeze tbody td:first-child::after{content:"";position:absolute;top:0;right:0;bottom:0;
 width:6px;transform:translateX(100%);pointer-events:none;
 background:linear-gradient(to right,rgba(24,36,51,.09),transparent)}
/* 宽表里单元格不要挤成一列一个字 */
.tablewrap.wide td,.tablewrap.wide th{white-space:normal;min-width:82px}
.tablewrap.wide td:first-child{min-width:150px}

details{border:1px solid var(--line);border-radius:9px;margin:12px 0;background:#fafbfd}
details>summary{cursor:pointer;padding:11px 15px;font-weight:600;font-size:13.5px;list-style:none}
details>summary::-webkit-details-marker{display:none}
details>summary::before{content:"▸ ";color:var(--accent)}
details[open]>summary::before{content:"▾ "}
details>.inner{padding:0 15px 13px}
details .tablewrap{margin-top:0}

.callout{border-radius:9px;padding:12px 16px;margin:11px 0;border-left:4px solid}
.callout.ok{background:var(--ok-bg);border-color:var(--ok)}
.callout.warn{background:var(--warn-bg);border-color:var(--warn)}
.callout.danger{background:var(--danger-bg);border-color:var(--danger)}
.callout.info{background:var(--blue-bg);border-color:var(--blue)}
.callout b{color:inherit}

.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}
.grid3{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px}

.pill{display:inline-block;padding:1px 9px;border-radius:20px;font-size:11.5px;font-weight:600;
 border:1px solid;white-space:nowrap}
.pill.ok{background:var(--ok-bg);color:var(--ok);border-color:#aad9bd}
.pill.warn{background:var(--warn-bg);color:var(--warn);border-color:#f0d09a}
.pill.danger{background:var(--danger-bg);color:var(--danger);border-color:#f3bcbc}
.pill.muted{background:#eef2f7;color:var(--muted);border-color:var(--line)}

.tree{font-family:Consolas,ui-monospace,monospace;font-size:13px;background:#fafbfd;
 border:1px solid var(--line);border-radius:9px;padding:15px 18px;overflow-x:auto;margin:12px 0}
.tree .lv0{font-weight:700;color:var(--nav)}
.tree .lv1{color:var(--accent);font-weight:600}
.tree .lv2{color:var(--muted)}
.tree .bud{color:var(--blue);font-weight:600}
.tree .none{color:var(--danger);font-weight:600}

.chartwrap{background:#fafbfd;border:1px solid var(--line);border-radius:9px;padding:14px;margin:12px 0}
.chartwrap .ttl{font-size:12.5px;color:var(--muted);margin-bottom:6px}
.foot{text-align:center;color:var(--muted);font-size:12.5px;padding:26px 0 8px}

.mdbody{margin:6px 0}
.mdbody h1,.mdbody h2,.mdbody h3{margin:16px 0 8px;line-height:1.35}
.mdbody h1{font-size:18px}.mdbody h2{font-size:16px}.mdbody h3{font-size:15px}
.mdbody p{margin:8px 0}
.mdbody blockquote{margin:10px 0;padding:10px 14px;border-left:4px solid var(--accent);
 background:var(--accent-bg);border-radius:0 8px 8px 0}
.mdbody ul,.mdbody ol{margin:8px 0 8px 1.35em;padding:0}
.mdbody li{margin:4px 0}
.mdbody pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.55 Consolas,monospace;
 background:#f1f5f9;border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin:10px 0}
.mdbody .tablewrap{margin:10px 0}
"""

def esc(x):
    return html.escape(str(x), quote=False)


def display_cell(value):
    """Preserve deliberate HTML strings, format structured cells without repr()."""
    if value is None: return '未取得'
    if isinstance(value, dict):
        return '；'.join(esc(k) + '：' + display_cell(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return '；'.join(display_cell(v) for v in value)
    return str(value)


def attr(x):
    """Escape plain values used in HTML attributes; raw body HTML remains supported."""
    return html.escape(str(x), quote=True)


def _details_fallback(content):
    """Keep even caller-supplied details readable without JavaScript, without rewriting content."""
    offsets, offset = [], 0
    for line in content.splitlines(keepends=True):
        offsets.append(offset)
        offset += len(line)
    class Details(HTMLParser):
        def __init__(self):
            super().__init__()
            self.changes = []

        def handle_starttag(self, tag, attrs):
            if tag != 'details' or 'open' in dict(attrs) or 'm2-appendix' in dict(attrs).get('class','').split():
                return
            original = self.get_starttag_text()
            line, col = self.getpos()
            start = offsets[line - 1] + col
            self.changes.append((start, start + len(original),
                original[:-1] + ' open data-initial-open="false">'))
    parser = Details()
    parser.feed(content)
    for start, end, replacement in reversed(parser.changes):
        content = content[:start] + replacement + content[end:]
    return content


def _inline_md(text):
    s = esc(text)
    s = re.sub(r'`([^`]+)`', r'<code>\1</code>', s)
    s = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', s)
    s = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'<i>\1</i>', s)
    def link(match):
        image, label, destination = match.group(1, 2, 3)
        destination = html.unescape(destination).strip('<>')
        from urllib.parse import urlsplit
        scheme = urlsplit(destination).scheme.lower()
        safe_data = image and re.match(r'^data:image/(?:png|jpeg|webp);base64,', destination)
        if scheme not in ('', 'http', 'https') and not safe_data:
            return label
        if any(ord(c) < 32 for c in destination): return label
        target = attr(destination)
        if image:
            return '<img loading="lazy" src="%s" alt="%s" style="max-width:100%%;height:auto">' % (target, attr(html.unescape(label)))
        external = ' target="_blank" rel="noreferrer"' if scheme in ('http','https') else ''
        return '<a href="%s"%s>%s</a>' % (target, external, label)
    s = re.sub(r'(!?)\[([^\]]+)\]\(([^)\n]+)\)', link, s)
    return s


def _is_table_sep(line):
    cells = [c.strip() for c in line.strip().strip('|').split('|')]
    return bool(cells) and all(re.fullmatch(r':?-{3,}:?', c.replace(' ', '')) for c in cells)


def _split_row(line):
    return [c.strip() for c in line.strip().strip('|').split('|')]


def markdown_html(text):
    """把常见报告 Markdown 渲成与正文同一套表格/标题，禁止用 <pre> 堆原文。"""
    if not text:
        return '<div class="mdbody"><p class="note">（空）</p></div>'
    lines = str(text).replace('\r\n', '\n').replace('\r', '\n').split('\n')
    buf, i, n = [], 0, len(lines)
    while i < n:
        line = lines[i]
        # Only generator-owned empty review anchors are accepted as HTML.
        # Arbitrary elements and attributes still pass through escaping.
        anchor = re.fullmatch(r'<a id="(review-quote-[1-9][0-9]*)"></a>', line)
        if anchor:
            buf.append('<a id="%s"></a>' % anchor[1]); i += 1
            continue
        disclosure = re.fullmatch(r'\s*<details(?:\s+open)?><summary>(.*?)</summary>\s*', line)
        if disclosure:
            end, depth = i + 1, 1
            while end < n:
                depth += len(re.findall(r'<details(?:\s+open)?>',lines[end]))
                depth -= len(re.findall(r'</details>',lines[end]))
                if depth == 0: break
                end += 1
            if depth == 0:
                opened = ' open' if re.match(r'\s*<details\s+open>',line) else ''
                buf.append('<details%s><summary>%s</summary><div class="inner">%s</div></details>' %
                    (opened, _inline_md(disclosure[1]), markdown_html('\n'.join(lines[i+1:end]))))
                i = end + 1
                continue
        if line.startswith('```'):
            fence, i = [], i + 1
            while i < n and not lines[i].startswith('```'):
                fence.append(lines[i]); i += 1
            i += 1
            buf.append('<pre>%s</pre>' % esc('\n'.join(fence)))
            continue
        if re.match(r'^\s*\|', line) and i + 1 < n and _is_table_sep(lines[i + 1]):
            headers = _split_row(line)
            seps = _split_row(lines[i + 1])
            i += 2
            rows = []
            while i < n and re.match(r'^\s*\|', lines[i]) and not _is_table_sep(lines[i]):
                rows.append(_split_row(lines[i])); i += 1
            align = ['n' if s.strip().startswith('-') and s.strip().endswith(':') else 'l' for s in seps]
            align = (align + ['l'] * len(headers))[:len(headers)]
            sub = Section('_', '_')
            sub.table(headers, [[_inline_md(c) for c in r] + [''] * (len(headers) - len(r)) for r in rows], align)
            buf.append(''.join(sub.buf))
            continue
        m = re.match(r'^(#{1,3})\s+(.+)$', line)
        if m:
            buf.append('<h%d>%s</h%d>' % (len(m.group(1)), _inline_md(m.group(2)), len(m.group(1))))
            i += 1
            continue
        if line.startswith('>'):
            quote = []
            while i < n and lines[i].startswith('>'):
                quote.append(lines[i][1:].lstrip()); i += 1
            buf.append('<blockquote>%s</blockquote>' % _inline_md(' '.join(quote)))
            continue
        if re.match(r'^[-*]\s+', line) or re.match(r'^\d+\.\s+', line):
            ordered = bool(re.match(r'^\d+\.\s+', line))
            items = []
            while i < n and (re.match(r'^\d+\.\s+', lines[i]) if ordered else re.match(r'^[-*]\s+', lines[i])):
                items.append(re.sub(r'^([-*]|\d+\.)\s+', '', lines[i]))
                i += 1
            tag = 'ol' if ordered else 'ul'
            buf.append('<%s>%s</%s>' % (tag, ''.join('<li>%s</li>' % _inline_md(x) for x in items), tag))
            continue
        if re.match(r'^---+$', line.strip()):
            buf.append('<hr>'); i += 1
            continue
        if not line.strip():
            i += 1
            continue
        para = [line]
        i += 1
        while i < n and lines[i].strip() and not re.match(r'^(#{1,3}\s+|```|[>*-]\s+|\d+\.\s+|\s*\|)', lines[i]) and not re.match(r'^---+$', lines[i].strip()):
            para.append(lines[i]); i += 1
        buf.append('<p>%s</p>' % _inline_md(' '.join(x.strip() for x in para)))
    return '<div class="mdbody">%s</div>' % ''.join(buf)

class Section(object):
    def __init__(self, sid, title, note=None, group=None):
        self.id, self.title, self.note, self.buf = sid, title, note, []
        self.group = str(group) if group is not None else 'overview'
        self._table_count = 0

    def raw(self, h):
        self.buf.append(h); return self

    def p(self, text):
        return self.raw('<p style="margin:9px 0">%s</p>' % text)

    def h3(self, t):
        return self.raw('<h3>%s</h3>' % esc(t))

    def kpis(self, items):
        """items: [(label, value, sub|None, tone|None)] tone: ok/warn/danger/blue"""
        cells = []
        for it in items:
            lab, val = it[0], it[1]
            sub = it[2] if len(it) > 2 else None
            tone = it[3] if len(it) > 3 else None
            cells.append('<div class="kpi%s"><div class="k">%s</div><div class="v">%s</div>%s</div>' % (
                ' ' + tone if tone else '', esc(lab), val,
                '<div class="s">%s</div>' % esc(sub) if sub else ''))
        return self.raw('<div class="kpirow">%s</div>' % ''.join(cells))

    def bar(self, label, value, maxv, right=None, tone=None):
        pct = 0 if not maxv else max(0.0, min(100.0, 100.0 * float(value) / float(maxv)))
        return self.raw(
            '<div class="barrow"><div class="top"><span>%s</span><b>%s</b></div>'
            '<div class="bar-track"><div class="bar-fill%s" style="width:%.1f%%"></div></div></div>' % (
                esc(label), esc(right if right is not None else value),
                ' ' + tone if tone else '', pct))

    # 超过这么多行就给滚动窗口 + 吸顶表头；超过这么多列就冻结首列并放宽列宽
    TALL_ROWS, WIDE_COLS = 12, 7

    def table(self, headers, rows, align=None, minw=None, searchable=False):
        """align: 每列 'l'/'n'（n = 右对齐数字列）
        searchable 为可选离线搜索；规则二维表可在窄屏显示字段标签。
        不规则行保留全部单元格，仍使用横向滚动。"""
        headers, rows = list(headers), [[display_cell(c) for c in row] for row in rows]
        ncol = len(headers)
        align = list(align or [])
        align += ['l'] * (max([ncol] + [len(row) for row in rows]) - len(align))
        safe_stack = bool(headers) and all(len(row) == ncol for row in rows) and not any(
            re.search(r'\b(?:rowspan|colspan)\s*=|<\s*table\b', str(cell), re.I)
            for row in rows for cell in row)
        th = ''.join('<th scope="col"%s>%s</th>' % (' class="n"' if align[i] == 'n' else '', esc(h))
                     for i, h in enumerate(headers))
        trs = []
        for r in rows:
            tds = ''.join('<td%s%s>%s</td>' % (
                ' class="n"' if align[i] == 'n' else '',
                ' data-label="%s"' % attr(headers[i]) if safe_stack else '',
                '<span class="cell-value">%s</span>' % c if safe_stack else c)
                for i, c in enumerate(r))
            trs.append('<tr>%s</tr>' % tds)
        cls = ['tablewrap']
        if safe_stack:
            cls.append('stackable')
        wide = ncol >= self.WIDE_COLS
        # ⛔ 行数不是对的判据：宽表每格都折行，5 行也能占满一屏 —— 宽表一律给滚动窗口
        if len(rows) > self.TALL_ROWS or (wide and len(rows) > 3): cls.append('tall')
        if wide: cls += ['wide', 'freeze']
        # 列宽随列数走：列多了还挤在 680px 里，每列就只剩几十像素，中文会一列一个字
        if minw is None and ncol >= self.WIDE_COLS:
            minw = max(680, 96 * ncol + 70)
        style = ' style="min-width:%dpx"' % minw if minw else ''
        self._table_count += 1
        table_id = 'table-%s-%d' % (self.id, self._table_count)
        controls, empty = '', ''
        if searchable:
            controls = ('<div class="table-search-controls js-only"><label for="search-%s">搜索表格</label>'
                '<input id="search-%s" type="search" data-table-search="%s" aria-label="搜索%s表格" '
                'aria-controls="%s" placeholder="输入关键词筛选…" autocomplete="off">'
                '<span class="table-search-count" id="count-%s" aria-live="polite">显示 %d / %d 行</span></div>'
                % (attr(table_id), attr(table_id), attr(table_id), attr(self.title),
                   attr(table_id), attr(table_id), len(rows), len(rows)))
            empty = ('<p class="table-no-results" id="empty-%s" hidden>没有匹配项，请调整搜索词。</p>'
                     % attr(table_id))
        table_class = ' class="mobile-stack"' if safe_stack else ''
        table_identifier = ' id="%s"' % attr(table_id) if searchable else ''
        return self.raw(controls + '<div class="%s"><table%s%s%s><thead><tr>%s</tr></thead>'
                        '<tbody>%s</tbody></table></div>' %
                        (' '.join(cls), table_identifier, table_class, style, th, ''.join(trs)) + empty)

    def details(self, summary, inner_html, open_=False):
        return self.raw('<details open data-initial-open="%s"><summary>%s</summary><div class="inner">%s</div></details>'
                        % ('true' if open_ else 'false', esc(summary), inner_html))

    def image_card(self, src, title, caption='', status='待审核'):
        """S3 图位卡：本地/栅格 data 图内嵌；远程地址只给来源链接，不自动联网。"""
        source = str(src) if src is not None else ''
        image, source_link = '', ''
        mime_types = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                      '.webp': 'image/webp', '.gif': 'image/gif', '.avif': 'image/avif'}
        if source.startswith('//') or any(ord(char) < 32 for char in source):
            raise ValueError('图片地址不可包含协议相对地址或控制字符')
        if source.lower().startswith('data:'):
            if not re.fullmatch(r'data:image/(?:png|jpeg|webp|gif|avif);base64,[A-Za-z0-9+/=\s]+', source, re.I):
                raise ValueError('图位卡只接受 base64 栅格图片，不接受可执行内容')
            image = source
        elif re.match(r'^https?://', source, re.I):
            if not urlsplit(source).netloc:
                raise ValueError('图片来源 URL 缺少主机')
            source_link = '<a href="%s" target="_blank" rel="noopener noreferrer">打开图片来源（联网）</a>' % attr(source)
        elif source:
            if re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', source) and not re.match(r'^[A-Za-z]:[\\/]', source):
                raise ValueError('图片地址协议不受支持')
            path = Path(source)
            if not path.is_file():
                raise ValueError('本地图片不存在: %s' % source)
            mime = mime_types.get(path.suffix.lower())
            if not mime:
                raise ValueError('图位卡只接受 PNG/JPEG/WebP/GIF/AVIF 栅格图片')
            image = 'data:%s;base64,%s' % (mime, base64.b64encode(path.read_bytes()).decode('ascii'))
        visual = '<img src="%s" alt="%s" loading="lazy" decoding="async">' % (attr(image), attr(title)) if image else (
            '<div class="image-placeholder">%s</div>' % ('远程图片未嵌入，请下载后重新生成' if source_link else '待补图片'))
        # Status remains supplied evidence, never approval inferred from image existence.
        return self.raw('<figure class="image-card">%s<figcaption><div class="image-card-heading">'
                        '<h3>%s</h3><span class="image-status pill muted">%s</span></div>'
                        '<p>%s</p>%s</figcaption></figure>' %
                        (visual, esc(title), esc(status), esc(caption), source_link))

    def details_table(self, summary, headers, rows, align=None, open_=False):
        sub = Section('_', '_')
        sub.table(headers, rows, align)
        return self.details(summary, ''.join(sub.buf), open_)

    def markdown(self, text):
        return self.raw(markdown_html(text))

    def details_markdown(self, summary, text, open_=False):
        return self.details(summary, markdown_html(text), open_)

    def callout(self, kind, html_):
        return self.raw('<div class="callout %s">%s</div>' % (kind, html_))

    def tree(self, lines):
        return self.raw('<div class="tree">%s</div>' % '<br>'.join(lines))

    def donut(self, pct, center, caption, tone='#0f766e'):
        r, c = 52, 60
        circ = 2 * 3.14159265 * r
        off = circ * (1 - max(0.0, min(1.0, pct / 100.0)))
        return self.raw(
            '<div class="chartwrap"><div class="ttl">%s</div>'
            '<svg viewBox="0 0 120 120" width="132" height="132" role="img" aria-label="%s">'
            '<circle cx="%d" cy="%d" r="%d" fill="none" stroke="#eef2f7" stroke-width="14"/>'
            '<circle cx="%d" cy="%d" r="%d" fill="none" stroke="%s" stroke-width="14" '
            'stroke-linecap="round" stroke-dasharray="%.2f" stroke-dashoffset="%.2f" '
            'transform="rotate(-90 %d %d)"/>'
            '<text x="60" y="58" text-anchor="middle" font-size="21" font-weight="700" fill="#182433">%s</text>'
            '<text x="60" y="76" text-anchor="middle" font-size="10" fill="#667085">%s</text>'
            '</svg></div>' % (esc(caption), esc(caption), c, c, r, c, c, r, tone,
                              circ, off, c, c, esc(center), esc(caption[:14])))

    def hbars_svg(self, title, pairs, tone='#2563eb'):
        """pairs: [(label, value)] —— 画成 svg 条形图"""
        if not pairs: return self
        mx = max(v for _, v in pairs) or 1
        rowh, padl, w = 22, 190, 640
        h = rowh * len(pairs) + 12
        parts = []
        for i, (lab, v) in enumerate(pairs):
            y = i * rowh + 8
            bw = (w - padl - 70) * (float(v) / mx)
            parts.append('<text x="0" y="%d" font-size="11.5" fill="#46546a">%s</text>' % (y + 10, esc(lab[:30])))
            parts.append('<rect x="%d" y="%d" width="%.1f" height="12" rx="3" fill="%s"/>' % (padl, y + 1, bw, tone))
            parts.append('<text x="%.1f" y="%d" font-size="11" fill="#667085">%s</text>' % (padl + bw + 6, y + 10, '{:,}'.format(v)))
        return self.raw('<div class="chartwrap"><div class="ttl">%s</div>'
                        '<svg viewBox="0 0 %d %d" width="100%%" role="img" aria-label="%s">%s</svg></div>'
                        % (esc(title), w, h, esc(title), ''.join(parts)))

    def render(self):
        note = '<p class="note">%s</p>' % self.note if self.note else ''
        return '<section id="%s" class="report-section" data-group="%s"><h2>%s</h2>%s%s</section>' % (
            attr(self.id), attr(self.group), esc(self.title), note, _details_fallback(''.join(self.buf)))

DEFAULT_FOOT = (
    "星空亚马逊 Agent · Codex｜github-tabler-html-report｜坚哥｜"
    "跨境者说干货 · 星空的跨境圈子｜Tabler MIT"
)

def _sibling(name):
    """Import a helper shipped next to this file even when the renderer was loaded by file path
    (S3 visual_returns loads it that way, so the assets folder is not on sys.path)."""
    import importlib, importlib.util, sys
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError:
        path = Path(__file__).with_name(name + '.py')
        if not path.is_file():
            raise
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec); sys.modules[name] = module
        spec.loader.exec_module(module)
        return module


_front = _sibling('s1_product_front')
S1_FRONT_CSS, render_front, inspect_front = _front.CSS, _front.render_front, _front.inspect_front
CSS += S1_FRONT_CSS
CSS += Path(__file__).with_name('report_visual.css').read_text(encoding='utf-8')
JS = Path(__file__).with_name('report_visual.js').read_text(encoding='utf-8')

STAGE_GROUPS = {
    'S1': [('overview', '先看结论'), ('comparison', '同款比较'), ('economics', '经济测算'), ('evidence', '限制与证据')],
    'S3': [('overview', '先看方案'), ('images', '图片与图位'), ('evidence', '限制与证据')],
    'S4': [('overview', '先看结论'), ('structure', '结构与预算'), ('keywords', '关键词与承接'),
           ('rules', '出价与复盘'), ('evidence', '限制与证据')],
}


def _guess_stage(title):
    match = re.search(r'^\s*S([134])(?:\b|_)|阶段_S([134])(?:\b|_)', str(title), re.I)
    if match:
        return 'S' + (match.group(1) or match.group(2))
    if re.search(r'选品.*可行性', str(title)):
        return 'S1'
    return ''


def _technical_meta(item):
    """Only explicit hash/version fields may fold; unknown or business context stays visible."""
    text = str(item)
    if re.search(r'审批|审核|批准|未投放|未提交|待确认|TEST_ONLY|not_submitted|pending|approved|rejected|blocked', text, re.I):
        return False
    if isinstance(item, (tuple, list)) and len(item) == 2:
        key = str(item[0]).strip().lower()
    else:
        match = re.match(r'^\s*([\w.-]+)(?:\s*[:=：]\s*|\s+)\S', text)
        if not match:
            return False
        key = match.group(1).lower()
    return key in {'hash', 'sha256', 'sha256sum', 'checksum', 'version', '版本', '版本号'} or key.endswith(('_hash', '_version'))


def elevator_html(stage, has_front, items, groups):
    """左侧电梯：按阅读分组列出全部章节，滚动时高亮当前章节；窄屏自动隐藏，回落到折叠目录。
    items: [(section_id, title, group)]；groups: [(group, label)]"""
    by_group = {}
    for sid, title, group in items:
        by_group.setdefault(group, []).append((sid, title))
    parts = ['<aside class="side elevator" aria-label="报告电梯导航"><div class="box">',
             '<div class="brand">星空报告' + (' · ' + esc(stage) if stage else '') + '</div>',
             '<div class="elevator-progress" aria-hidden="true"><i></i></div>']
    if has_front:
        parts.append('<a class="lift" href="#s1-product-decision">选中产品与结论</a>')
    for group, label in groups:
        rows = by_group.get(group, [])
        if not rows:
            continue
        parts.append('<div class="lbl">' + esc(label) + '</div>')
        for sid, title in rows:
            num = re.match(r'^\s*(M\d+)\s*', str(title))
            text = str(title)[num.end():] if num else str(title)
            parts.append('<a class="lift" href="#%s">%s%s</a>' % (
                quote(str(sid), safe=''), ('<em>' + esc(num.group(1)) + '</em>') if num else '', esc(text or title)))
    parts.append('<a class="lift top" href="#report-content">↑ 回到顶部</a></div></aside>')
    return ''.join(parts)


def report_theme_css():
    import json
    config=json.loads((Path(__file__).resolve().parents[3]/'templates/report_theme.json').read_text(encoding='utf8'))
    return ':root{'+''.join('--'+name+':'+value+';' for name,value in config['fonts'].items())+'}'


class Report(object):
    def __init__(self, title, subtitle='', meta=None, foot='', stage=None):
        self.title, self.subtitle = title, subtitle
        self.meta = meta or []
        self.foot = foot or DEFAULT_FOOT
        self.sections = []
        self.product_front = ""
        self.stage = str(stage).upper() if stage is not None else _guess_stage(title)

    def product_decision(self, **fields):
        self.product_front = render_front(**fields)
        return self

    def section(self, sid, title, note=None, group=None):
        s = Section(sid, title, note, group); self.sections.append(s); return s

    def render_sections(self):
        """原顺序、完整内容；分组只用于浏览器增强，不裁剪章节或重新编排业务证据。"""
        return ''.join(s.render() for s in self.sections)

    def render(self):
        is_s1 = self.stage == 'S1' or bool(re.search(r'\bS1\b|阶段_S1|选品.*可行性', self.title, re.I))
        if is_s1 and not self.product_front:
            raise ValueError('S1 报告缺少产品与结论首屏，请先调用 product_decision')
        nav = ''.join('<a href="#%s">%s</a>' % (quote(str(s.id), safe=''), esc(s.title)) for s in self.sections)
        visible_meta = ''.join('<span>%s</span>' % esc(m) for m in self.meta if not _technical_meta(m))
        meta = ''.join('<span>%s</span>' % esc(m) for m in self.meta if _technical_meta(m))
        header_meta = ('<div class="header-meta" aria-label="报告状态与范围">' + visible_meta + '</div>') if visible_meta else ''
        body = self.render_sections()
        present = {s.group for s in self.sections}
        groups = [(group, label) for group, label in STAGE_GROUPS.get(self.stage, [('overview', '先看结论')])
                  if group in present or (group == 'overview' and self.product_front)]
        for section in self.sections:
            if section.group not in {group for group, _ in groups}:
                groups.append((section.group, section.group or '未命名分组'))
        group_nav = ''.join('<button type="button" data-group-button="%s" aria-pressed="false">%s</button>'
                            % (attr(group), esc(label)) for group, label in groups)
        elevator = self.render_elevator(groups)
        technical_meta = ('<details class="technical-meta" open data-initial-open="false">'
                          '<summary>报告版本与来源信息</summary><div class="meta">' + meta + '</div></details>') if meta else ''
        # ⛔ 不用 % 格式化 —— CSS 里全是 % 号（width:100% 等），会把格式化撑爆
        rendered = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>' + esc(self.title) + '</title><style>' + report_theme_css() + CSS + '</style></head>'
                '<body data-theme="mint" data-density="comfort" data-stage="' + attr(self.stage) + '">'
                '<a class="skip-link" href="#report-content">跳到报告正文</a><div class="layout has-elevator">' + elevator +
                '<main class="main"><div class="report-toolbar js-only" aria-label="阅读设置">'
                '<div class="theme-switch" role="group" aria-label="配色主题">'
                '<button type="button" data-theme-button="mint" aria-pressed="true">清爽青绿</button>'
                '<button type="button" data-theme-button="mono" aria-pressed="false">极简黑白</button>'
                '<button type="button" data-theme-button="paper" aria-pressed="false">暖纸阅读</button></div>'
                '<div class="density-switch" role="group" aria-label="阅读密度">'
                '<button type="button" data-density-button="comfort" aria-pressed="true">舒适阅读</button>'
                '<button type="button" data-density-button="compact" aria-pressed="false">紧凑明细</button></div>'
                '<button type="button" data-print-report>打印全文</button></div>'
                '<header class="hero"><div class="report-kicker">星空报告</div><h1>' + esc(self.title) + '</h1>'
                '<div class="sub">' + esc(self.subtitle) + '</div>' + header_meta +
                '</header><nav class="reader-nav js-only" aria-label="阅读分组">' + group_nav +
                '<button type="button" class="all" data-group-button="all" aria-pressed="false">完整报告</button></nav>'
                '<details class="full-index" open data-initial-open="false"><summary>完整目录 · ' + str(len(self.sections)) +
                ' 个章节</summary><nav aria-label="完整目录">' + nav + '</nav></details>'
                '<div id="report-content" tabindex="-1">' + self.product_front + body + '</div>' + technical_meta +
                '<div class="foot">' + esc(self.foot) + '</div></main></div>'
                '<script>' + JS + '</script></body></html>')

        if self.stage in {'S1','S2','S3','S4'}:
            import importlib.util
            path=Path(__file__).resolve().parents[3]/'scripts/ui_terms.py'
            if path.is_file():
                spec=importlib.util.spec_from_file_location('_report_ui_terms',path);terms=importlib.util.module_from_spec(spec);spec.loader.exec_module(terms);rendered=terms.rewrite_html(rendered)
        return rendered

    def render_elevator(self, groups):
        return elevator_html(self.stage, bool(self.product_front),
                             [(s.id, s.title, s.group) for s in self.sections], groups)

    def write(self, path):
        d = os.path.dirname(path)
        if d and not os.path.isdir(d): os.makedirs(d)
        rendered = self.render()
        if not os.path.isfile(path) or io.open(path, 'rb').read() != rendered.encode('utf8'):
            with io.open(path, 'w', encoding='utf-8', newline='\n') as stream:
                stream.write(rendered)
        return self.selfcheck(path)

    @staticmethod
    def upgrade_file(path, backup=True):
        """给已生成的星空报告补上左侧电梯导航（并换成当前样式与脚本）；原文件另存 .bak。"""
        src = io.open(path, encoding='utf-8').read()
        if 'class="side elevator"' in src:
            # 已有电梯：只把样式和脚本刷新到当前版本
            src_old = src
            src = re.sub(r'<aside class="side elevator".*?</aside>', '', src, count=1, flags=re.S).replace('<div class="layout has-elevator">', '<div class="layout">', 1)
            backup = False  # 原始文件的 .bak 在第一次升级时已保存
        sections = re.findall(r'<section id="([^"]+)" class="report-section" data-group="([^"]*)"><h2>(.*?)</h2>', src, re.S)
        if not sections or '<div class="layout">' not in src:
            return {'upgraded': False, 'reason': 'not_a_starsky_report'}
        stage = (re.search(r'data-stage="([^"]*)"', src) or [None, ''])[1]
        labels = dict(STAGE_GROUPS.get(stage, []))
        groups, seen = [], set()
        for _sid, group, _title in sections:
            if group not in seen:
                seen.add(group)
                groups.append((group, labels.get(group, group or '章节')))
        items = [(html.unescape(sid), html.unescape(re.sub(r'<[^>]+>', '', title)), group) for sid, group, title in sections]
        aside = elevator_html(stage, 'id="s1-product-decision"' in src, items, groups)
        out = src.replace('<div class="layout">', '<div class="layout has-elevator">' + aside, 1)
        out = re.sub(r'<style>.*?</style>', lambda _m: '<style>' + CSS + '</style>', out, count=1, flags=re.S)
        out = re.sub(r'<script>(?:(?!</script>).)*</script>(?=</body>)', lambda _m: '<script>' + JS + '</script>', out, count=1, flags=re.S)
        if backup:
            io.open(path + '.bak', 'w', encoding='utf-8').write(src)
        io.open(path, 'w', encoding='utf-8').write(out)
        return {'upgraded': True, 'sections': len(items), 'groups': len(groups)}

    @staticmethod
    def selfcheck(path):
        """契约 self_check_command 的等价实现"""
        s = io.open(path, encoding='utf-8').read()
        return dict(size_kb=os.path.getsize(path) // 1024,
                    section=len(re.findall(r'<section', s)),
                    kpi=len(re.findall(r'class="kpi', s)),
                    details=len(re.findall(r'<details', s)),
                    bar=len(re.findall(r'barrow|bar-fill|barfill', s)),
                    svg=len(re.findall(r'<svg', s)),
                    table=len(re.findall(r'<table', s)),
                    tablewrap=len(re.findall(r'class="tablewrap', s)))


if __name__ == '__main__':
    # 旧报告补电梯导航：python salon_report.py --upgrade 报告1.html [报告2.html …]
    import sys
    if len(sys.argv) > 2 and sys.argv[1] == '--upgrade':
        for f in sys.argv[2:]:
            print(f, Report.upgrade_file(f))
    else:
        print('用法：python salon_report.py --upgrade <报告.html> …')
