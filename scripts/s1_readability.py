"""Lossless S1 paragraph layout; preserve links, tables and original wording."""
import collections
import re
from decimal import Decimal, ROUND_HALF_UP
from html.parser import HTMLParser
from s1_acceptance_contract import FX_CONTEXT_RE

_MACHINE_PATTERNS = (
    ('哈希', r'(?i)\b[0-9a-f]{64}\b'),
    ('内部字段', r'\bselector\b|/calls/|\br\d+\.[a-z_]+\.\d+\b'),
    ('英文状态', r'\b(?:data_gap|met|calculated|missing|null|None|True|False|unknown|pending|partial|deferred|incomplete|not_calculable)\b'),
    ('机器话术', r'本轮结构化来源(?:尚)?未提供|已存原始回包未覆盖要求|去重规则'),
)

_DECIMAL = re.compile(r'(?<![\d.A-Za-z_])[-+]?([$¥￥€£])?\s*[-+]?(?P<number>(?:\d{1,3}(?:,\d{3})+|\d+)\.\d+)(?![\d.A-Za-z_])')
_FX = re.compile(FX_CONTEXT_RE.pattern+r'|兑换率|exchange\s*rate|CNY\s*/\s*USD', re.I)


def numeric_lines(text, render=False):
    """Apply typed display precision, including Markdown column headings.

    A currency sign/percent always takes precedence over a nearby FX label.
    Untyped measurements retain the prior three-place limit; source data is
    never rounded here. Currency/percentage labels work in headers or row labels.
    """
    headers=[]; previous_cells=[]; output=[]; invalid=False
    for line in text.splitlines():
        cells=line.split('|') if line.startswith('|') else [line]
        if line.startswith('|') and re.fullmatch(r'[|:\s-]+',line):
            headers=previous_cells
        elif not line.startswith('|'):
            headers=[]
        result=[]
        for index,cell in enumerate(cells):
            def replace(match):
                nonlocal invalid
                before=re.split(r'[；;，,。]',cell[:match.start()])[-1]
                after=cell[match.end():]
                rate_unit=bool(re.match(r'\s*(?:元/美元|人民币/美元|CNY/USD|USD/CNY)',after,re.I))
                label=(headers[index] if index<len(headers) else '')
                if index==2 and len(cells)>2:label+=' '+cells[1]
                money=not rate_unit and bool(match[1] or re.search(r'[$¥￥€£]\s*[-+]?$',before) or re.match(r'\s*(?:%|％|美元|元|人民币)',after) or
                      re.search(r'售价|现价|价格|金额|单价|成本|百分比|毛利率|占比|比例',label) or
                      re.search(r'(?:售价|价格|金额|成本|毛利率)\s*$',before))
                row_label_fx=(index==2 and len(cells)>2 and bool(_FX.search(cells[1])))
                fx=not money and bool(_FX.search(before) or row_label_fx or (index<len(headers) and _FX.search(headers[index])))
                places=4 if fx else 2 if money else 3
                raw_number=match['number'];value=Decimal(raw_number.replace(',',''));integer=value==value.to_integral_value()
                # Two-place currency display (e.g. $13.00) is an amount.
                # Bare integer counts and the single .0 suffix are disallowed.
                currency_two_places=money and not re.match(r'\s*[%％]',after) and raw_number.endswith('.00')
                invalid=invalid or (integer and not currency_two_places) or len(raw_number.split('.')[1])>places
                if not render:return match[0]
                rounded=value.quantize(Decimal(1).scaleb(-places),rounding=ROUND_HALF_UP)
                formatted=format(rounded,',.'+str(places)+'f') if ',' in raw_number else format(rounded,'f')
                prefix=match[0][:-len(raw_number)]
                return prefix+(formatted.rstrip('0').rstrip('.') if '.' in formatted else formatted)
            result.append(_DECIMAL.sub(replace,cell))
        output.append('|'.join(result) if line.startswith('|') else result[0])
        previous_cells=cells
    return '\n'.join(output), invalid


def readability_issues(text):
    """C12: inspect visible report prose, preserving the original text."""
    body = re.split(r'^##\s+附录\b', text, maxsplit=1, flags=re.M)[0]
    body = re.sub(r'<details\b[^>]*>.*?</details\s*>', '', body, flags=re.S|re.I)
    body = re.sub(r'^>\s*原话[：:].*$', '', body, flags=re.M)
    # R06 C5: provenance hashes are visible only in the evidence index.
    body = re.sub(r'(?ms)(^## M17\s[^\n]*\n)(.*?)(?=^## |\Z)',
                  lambda m: m[1]+re.sub(r'(?i)\b[0-9a-f]{64}\b','已绑定摘要',m[2]),body)
    issues=[label for label, pattern in _MACHINE_PATTERNS if re.search(pattern, body)]
    if numeric_lines(body)[1]:issues.append('数字格式')
    return issues


class Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def plain(value):
    parser = Text()
    parser.feed(value)
    return ''.join(parser.parts)


def balanced(value):
    stack = []
    for closing, tag, tail in re.findall(r'<(/?)([\w-]+)([^>]*)>', value):
        if tag in ('br', 'img', 'hr', 'input', 'wbr') or tail.rstrip().endswith('/'):
            continue
        if closing:
            if not stack or stack.pop() != tag:
                return False
        else:
            stack.append(tag)
    return not stack


def chunks(value, limit=120):
    result, pending = [], ''
    for part in re.split(r'(?<=[。；！？，])', value):
        pending += part
        if len(plain(pending)) >= limit and balanced(pending):
            result.append(pending)
            pending = ''
    if pending:
        result.append(pending)
    return result


def readable_html(body):
    def paragraph(match):
        attrs, inner = match[1], match[2]
        if len(plain(inner)) < 160 or re.search(r'\b(id|data-front)=', attrs):
            return match[0]
        parts = chunks(inner, 90)
        if len(parts) < 2 or not all(balanced(p) for p in parts):
            return match[0]
        return '<ul class="s1-reading-points">' + ''.join('<li>' + p + '</li>' for p in parts) + '</ul>'
    revised = re.sub(r'<p([^>]*)>(.*?)</p>', paragraph, body, flags=re.S)
    if plain(revised) != plain(body):
        raise ValueError('排版改变正文，已拒绝写入')
    return revised


def readable_markdown(text):
    lines, fenced = [], False
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith(('```', '~~~')):
            fenced = not fenced
        # Do not split tables, code, links, metadata, headings or nested lists.
        special = fenced or line.startswith(('#', '|', '>', '-', '*', ' ', '\t')) or '](' in line or '`' in line
        if not special and len(line.strip()) >= 160:
            end = '\n' if line.endswith('\n') else ''
            lines.append('\n\n'.join(chunks(line.rstrip('\n'), 90)) + end)
        else:
            lines.append(line)
    return ''.join(lines)


_STATE_LABELS = {'data_gap':'待补证','met':'已取得','calculated':'测算值','missing':'未取得','null':'—',
                 'None':'—','True':'是','False':'否','deferred':'待核','incomplete':'资料未齐',
                 'available':'已取得','not_calculable':'暂不能计算','pending':'待确认','unknown':'未知',
                 'confirmed':'已确认','not_verified':'待核验','page_advertising':'页面描述',
                 'partial':'部分完成',
                 'assumed':'假设值','conditional':'条件测算','page_reference':'页面参考'}


def reader_projection(text):
    """Render a new reader document without discarding technical source lines.

    This is a generator API, never a migration operation: callers retain old
    raw source bytes and calculate output hashes after rendering. Literal
    quotes and links are preserved; technical provenance goes in a fold.
    """
    output=[];technical=[];depth=0;fenced=False
    # Numeric types need the table header; perform display formatting as a
    # document pass before line-wise prose rendering, protecting source folds.
    numeric_input=[]; protected={}; in_source=False
    for raw in text.splitlines():
        if raw.lstrip().startswith(('```','~~~')):in_source=not in_source
        if in_source or raw.lstrip().startswith(('```','~~~')) or '<details' in raw or depth or raw.startswith('> 原话：'):
            key='SOURCEBLOCK'+str(len(protected))+'END';protected[key]=raw;numeric_input.append(key)
        else:
            def hide(match):
                key='SOURCEBLOCK'+str(len(protected))+'END';protected[key]=match[0];return key
            numeric_input.append(re.sub(r'https?://[^\s)>]+|`[^`]*`',hide,raw))
        depth=max(0,depth+len(re.findall(r'<details\b',raw))-len(re.findall(r'</details>',raw)))
    text=numeric_lines('\n'.join(numeric_input),render=True)[0]
    for key,raw in protected.items():text=text.replace(key,raw)
    depth=0
    for line in text.splitlines():
        if depth or '<details' in line:
            output.append(line)
            depth=max(0,depth+len(re.findall(r'<details\b',line))-len(re.findall(r'</details>',line)))
            continue
        if line.lstrip().startswith(('```','~~~')):
            # 3.0.25 04A_合规尺子: the closing fence stayed in the body, so everything after it rendered as code.
            # Both fences travel with the block into the appendix.
            fenced=not fenced
            technical.append(line)
            continue
        if fenced:
            technical.append(line)
            continue
        if line.startswith('> 原话：'):
            output.append(line)
            continue
        if re.search(r'(?i)SHA[- ]?256|\b[0-9a-f]{64}\b|\bselector\b|/calls/|\br\d+\.[a-z_]+\.\d+\b|去重规则|本轮结构化来源(?:尚)?未提供|已存原始回包未覆盖要求',line):
            technical.append(line)
            if line.startswith('|') and re.search(r'\br\d+\.[a-z_]+\.\d+\b',line) and not re.search(r'(?i)sha|selector|/calls/',line):
                line=re.sub(r'\br\d+\.[a-z_]+\.\d+\b','成本测算',line)
            else:
                continue
        # Keep URLs, code formulas and original keyword lists verbatim. If a
        # reserved machine token is literal evidence, retain its entire line.
        if ('`' in line or re.search(r'关键词.*[：:]|keyword',line,re.I)) and readability_issues(line):
            technical.append(line)
            continue
        tokens={}
        def protect(match):
            key='\x00'+str(len(tokens))+'\x00';tokens[key]=match[0];return key
        rendered=re.sub(r'https?://[^\s)>]+|`[^`]*`',protect,line)
        for key,label in _STATE_LABELS.items():
            rendered=re.sub(r'(?<![A-Za-z0-9_])'+key+r'(?![A-Za-z0-9_])',label,rendered)
        for key,original in tokens.items():rendered=rendered.replace(key,original)
        output.append(rendered)
    if technical:
        output+=['','<details><summary>来源与技术核对附录</summary>','',*technical,'','</details>']
    return '\n'.join(output).rstrip()+'\n'


SOURCE_APPENDIX = '<details><summary>本次生成的原始全文'


def keep_history_once(current, previous, summary):
    """把旧报告原文折叠在新报告后面，但只保留一份最早的原文（R06.1，PL-05 同类问题）。

    旧写法每次重跑都把“新正文 + 整个旧文件”再折叠一层，旧文件本身已含上一层，于是逐次翻倍。"""
    marker = '<details><summary>' + summary + '</summary>'
    start = previous.find(marker)
    if start >= 0:
        inner = previous[start + len(marker):]
        end = inner.rfind('</details>')
        previous = (inner[:end] if end >= 0 else inner).strip('\n') + '\n'
    return current + '\n' + marker + '\n\n' + previous + '\n</details>\n'


def _h2_sections(text):
    """[(标题或 None, 正文)]：按二级标题切段，第一段是标题前的内容。"""
    parts = re.split(r'(?m)^(?=## )', text)
    return [(re.match(r'## [^\n]*', s)[0].strip() if s.startswith('## ') else None, s) for s in parts if s]


def strip_source_appendix(text):
    """去掉 3.0.22 及更早版本附在报告末尾的“本次生成的原始全文”折叠附录（R06.1 PL-05）。

    附录里是整篇原文，所以排版一次就是 2 倍；助手再按“截到最后一个知识库依据、追加、重写”修订时，
    截点落在附录里、附录不再闭合，下一轮又整篇再附一次，于是翻倍（实际案例 16 份）。
    保留第一个附录之前的读者正文；附录之后才追加、读者正文里没有的二级章节（比如后来追加的“本轮用户决定”）
    按出现顺序接到正文后面，同名章节只留最后一次出现。没有附录时原样返回。"""
    start = text.find(SOURCE_APPENDIX)
    if start < 0:
        return text
    head, tail = text[:start].rstrip() + '\n', text[start:]
    tail = re.sub(r'<details><summary>本次生成的原始全文[^<]*</summary>|</details>', '\n', tail)
    known = {title for title, _ in _h2_sections(head) if title}
    extra = {}
    for title, body in _h2_sections(tail):
        if title and title not in known:
            extra.pop(title, None)
            extra[title] = body.rstrip() + '\n'
    if not extra:
        return head
    # 知识库依据固定放最后
    kb = [s for title, s in _h2_sections(head) if title == '## 知识库依据']
    body = ''.join(s for title, s in _h2_sections(head) if title != '## 知识库依据').rstrip() + '\n'
    return body + ''.join('\n' + s for s in extra.values()) + ''.join('\n' + s.rstrip() + '\n' for s in kb)


# ───────────── R06.1 PL-06：报告质量闸门 ─────────────
# 标尺取自旧版陶艺卷包参考答案（阶段报告 1134 行；中间报告最长 330 行 / 206 表格行），留出余量。超出只提示不拦截。
STAGE_REPORT_LINES = 1300
REPORT_LINES = 400
REPORT_TABLE_ROWS = 250
_DUPLICATE_MIN_CHARS = 80


def _comparable(body):
    """比较用的章节正文：去掉标题行、表格分隔行和多余空白，小数去掉末尾的 0（26.50 与 26.5 视为同一写法）。"""
    lines = body.splitlines()[1:]
    lines = [l for l in lines if not re.fullmatch(r'\s*\|?[\s:|-]+\|?\s*', l)]
    text = re.sub(r'<details\b[^>]*>(?:<summary>.*?</summary>)?|</details\s*>', ' ', ' '.join(lines))
    text = re.sub(r'\s+', ' ', text).strip()
    return re.sub(r'(\d+\.\d*?)0+(?!\d)', lambda m: m[1].rstrip('.'), text)


def duplicate_sections(text):
    """[(标题, 份数)]：同名二级章节正文相同的就是重复写入；残留旧版整篇原文附录也算。"""
    found = []
    if SOURCE_APPENDIX in text:
        found.append(('本次生成的原始全文（旧版附录）', text.count(SOURCE_APPENDIX) + 1))
    groups = {}
    for body in re.split(r'(?m)^(?=#{1,2} )', text):  # 一级标题也切开：整篇重复时它夹在两份之间
        title = body.splitlines()[0].strip() if body.startswith('## ') else None
        if title:
            groups.setdefault(title, []).append(_comparable(body))
    for title, bodies in groups.items():
        if title == '## 知识库依据':
            continue  # 引用段由写入器维护，重复只提示（见 size_warnings），不算正文重复
        counts = collections.Counter(b for b in bodies if len(b) >= _DUPLICATE_MIN_CHARS)
        copies = max(counts.values(), default=0)
        if copies > 1:
            found.append((title, copies))
    return found


def size_warnings(name, text):
    lines = text.splitlines()
    rows = sum(1 for l in lines if l.startswith('|') and not re.fullmatch(r'[|:\s-]+', l))
    limit = STAGE_REPORT_LINES if name.startswith('阶段_') else REPORT_LINES
    notes = []
    basis = sum(1 for l in lines if re.fullmatch(r'##[ \t]+知识库依据[ \t]*', l))
    if basis > 1:
        notes.append(f'有 {basis} 段“知识库依据”，宜合并为一段')
    if len(lines) > limit:
        notes.append(f'{len(lines)} 行，超过参考上限 {limit} 行')
    if not name.startswith('阶段_') and rows > REPORT_TABLE_ROWS:
        notes.append(f'{rows} 行表格，超过参考上限 {REPORT_TABLE_ROWS} 行；明细宜放数据文件，报告留摘要')
    return notes


def upsert_section(text, title, body):
    """写入或替换一个二级章节（R06.1）：同名章节只保留这一份，“知识库依据”始终在最后。

    3.0.22 的助手用“截到最后一个知识库依据、追加”的方式写“本轮用户决定”，截点落进旧附录，引发 PL-05 的翻倍。"""
    text = strip_source_appendix(text)
    heading = '## ' + title.strip()
    section = heading + '\n\n' + body.strip() + '\n'
    parts = _h2_sections(text)
    kept = [s for t, s in parts if t != heading and t != '## 知识库依据']
    basis = [s for t, s in parts if t == '## 知识库依据']
    out = ''.join(s.rstrip() + '\n\n' for s in kept).rstrip() + '\n\n' + section
    for s in basis:
        out += '\n' + s.rstrip() + '\n'
    return out
