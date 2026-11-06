"""Keep generated report links portable without guessing external local paths."""
import html
import os
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit


def resolve_project_link(root, document, target):
    root, document = Path(root).resolve(), Path(document).resolve()
    raw = html.unescape(target).strip('<>')
    if raw.startswith(('http:', 'https:', 'data:', '#', 'mailto:')):
        return raw, False
    raw = unquote(raw).replace('\\', '/')
    split = urlsplit(raw) if raw.startswith('file:') else None
    if split: raw = split.path
    absolute = raw.startswith('/') or bool(re.match(r'^[A-Za-z]:/', raw))
    candidate = None
    if '_history/' not in raw and ('S1_选品板块/' in raw or any(word in raw for word in ('阶段_S1', '2A_可行性报告', '04_产品开发决策透视'))) and any(word in raw for word in ('纠偏版','后补版','人工复核版','04_产品开发决策透视')):
        from s1_stage_report import MD, HTML
        candidate = root / (HTML if raw.endswith('.html') else MD)
    elif absolute:
        # Cross-machine paths can only be recovered from a known project subtree.
        for anchor in ('S1_选品板块/', '运行数据/', '00_产品资料/', '00_总控/', '00_导航/'):
            if anchor in raw:
                candidate = root / raw[raw.index(anchor):]
                break
    else:
        candidate = document.parent / raw
        if not candidate.is_file() and (root / raw).is_file(): candidate = root / raw
    if candidate:
        candidate = candidate.resolve()
        if candidate.is_relative_to(root):
            from r01_case_store import CompactCase
            store=CompactCase(root)
            logical=store.resolve(candidate)
            if not logical.is_file():
                report=store.human_report_for(candidate)
                if report is not None:
                    return os.path.relpath(report,store.resolve(document).parent).replace('\\','/'),False
        if candidate.is_relative_to(root) and candidate.is_file():
            return os.path.relpath(candidate, document.parent).replace('\\','/'), False
    return (None, True) if absolute else (target, False)


def portable_links(root, document, text):
    def md(match):
        prefix, label, target = match.group(1, 2, 3)
        link, missing = resolve_project_link(root, document, target)
        if missing:
            return f'{label}（参考图片未随项目提供）' if prefix else f'{label}（本地来源未随项目提供）'
        source_suffix=Path(target.strip('<>').split('#',1)[0]).suffix.lower()
        if not prefix and link != target and source_suffix in {'.yaml','.yml','.json','.xlsx'} and Path(link).suffix.lower()=='.md':
            label += '（合并业务报告；原文件可导出）'
        return f'{prefix}[{label}](<{link}>)' if link != target else match[0]
    text = re.sub(r'(!?)\[([^\]\n]*)\]\((<?[^)\n]+>?)\)', md, text)
    def image(match):
        tag = match[0]
        source = re.search(r'\bsrc=["\']([^"\']+)["\']', tag, re.I)
        if not source: return tag
        link, missing = resolve_project_link(root, document, source[1])
        if missing: return '<span class="evidence-gap">参考图片未随项目提供</span>'
        return tag.replace(source[1], html.escape(link, quote=True))
    text = re.sub(r'<img\b[^>]*>', image, text, flags=re.I)
    def href(match):
        link, missing = resolve_project_link(root, document, match[2])
        return 'data-missing-local-link="true"' if missing else match[1] + html.escape(link,quote=True) + match[3]
    return re.sub(r'(\bhref=["\'])([^"\']+)(["\'])', href, text, flags=re.I)


def rewrite_reports(root):
    from sync_s1_decision_reports import write_transaction
    root = Path(root).resolve()
    files = list((root/'S1_选品板块').rglob('*.md')) + list((root/'S1_选品板块').rglob('*.html')) + list(root.glob('README*.md'))
    outputs = {}
    for path in files:
        if any(p in ('_history', '运行数据', '媒体', '知识库依据', 'snapshots') or p.startswith('.') for p in path.relative_to(root).parts): continue
        if path.stem.endswith('_source'): continue
        old = path.read_text(encoding='utf-8-sig')
        new = portable_links(root, path, old)
        if new != old: outputs[path] = new.encode('utf8')
    write_transaction(root, outputs)
    return [str(p.relative_to(root)) for p in outputs]
