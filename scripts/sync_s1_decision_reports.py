"""Synchronize shared S1 decision blocks without replacing historical research."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import subprocess
import sys
from contextlib import contextmanager


@contextmanager
def rollback_transaction(root):
    """Roll back a controller operation, including generated/history/control files.

    An exception after the renderer's own transaction must not strand its new
    receipt next to the old workflow state. The root is an isolated product,
    never a plugin installation; symlinks and private directories are excluded.
    """
    root = Path(root).resolve()
    def files():
        result = {}
        for directory, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if d not in {'.git', '__pycache__', '.keys', 'keys', '.ssh', '.credentials'}
                       and not (Path(directory) / d).is_symlink()]
            for name in names:
                path = Path(directory) / name
                if not path.is_symlink() and path.resolve().is_relative_to(root):
                    result[path] = path.read_bytes()
        return result
    before = files()
    try:
        yield
    except BaseException:
        after = files()
        for path in set(after) - set(before):
            path.unlink()
        for path, raw in before.items():
            if after.get(path) != raw:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
        # Empty directories are not business artifacts, but remove newly made
        # empty history branches so failed attempts do not resemble revisions.
        raise

from s1_readability import readable_html, readable_markdown

START, END = '<!-- S1_DECISION_SHARED_START -->', '<!-- S1_DECISION_SHARED_END -->'
HISTORY = '<!-- S1_HISTORY_SNAPSHOT -->'
CSS = '.s1-reading-points{padding-left:1.3em;line-height:1.85}.s1-reading-points>li{margin:.7em 0}.s1-shared-decision{margin:24px 0;padding:20px;border:1px solid #d4e4df;border-radius:16px;background:#f8fbfa;color:#172e2a;overflow-wrap:anywhere}.s1-shared-decision img{max-width:100%;height:auto}.s1-shared-decision table{display:block;overflow-x:auto;max-width:100%;border-collapse:collapse}.s1-shared-decision th,.s1-shared-decision td{padding:10px;border-bottom:1px solid #d4e4df}.s1-shared-decision p{line-height:1.8;max-width:90ch}.s1-shared-decision details{margin:12px 0}.s1-shared-decision .component-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px}'


def merge_html(original, fragment, digest):
    block = START + '<div class="s1-shared-decision" data-input-sha256="' + digest + '">' + fragment + '</div>' + END
    if START in original and HISTORY in original:
        revised = re.sub(re.escape(START) + '.*?' + re.escape(END), lambda _: block, original, flags=re.S)
    else:
        old = re.sub(re.escape(START) + '.*?' + re.escape(END), '', original, flags=re.S)
        warning = (HISTORY + '<aside role="note" style="margin:24px;padding:18px;border:2px solid #c39640;background:#fff8e7;color:#493912">'
                   '<strong>以下为历史研究快照</strong><p>原始研究、备注与关键词完整保留。旧正文中的报价、成本、利润及决策属于当时版本，'
                   '不代表当前已核验结论；当前组件、成本资格与拿样判断以上方同步透视为准。</p></aside>')
        revised = re.sub(r'(<body\b[^>]*>)', lambda m: m[0] + block + warning, old, count=1)
        if revised == old:
            raise ValueError('HTML 缺少 body，无法安全同步')
    if 'id="s1-shared-style"' not in revised:
        revised = revised.replace('</head>', '<style id="s1-shared-style">' + CSS + '</style></head>', 1)
    return readable_html(revised)


def merge_markdown(original, shared):
    block = START + '\n\n' + shared + '\n\n' + END
    if START in original and HISTORY in original:
        revised = re.sub(re.escape(START) + '.*?' + re.escape(END), lambda _: block, original, flags=re.S)
    else:
        old = re.sub(re.escape(START) + '.*?' + re.escape(END), '', original, flags=re.S)
        warning = HISTORY + '\n\n> 以下为历史研究快照：原文、备注和关键词保留。旧报价、成本与判断不代表当前结论，以上方同步透视为准。\n\n'
        title, sep, rest = old.partition('\n')
        revised = title + '\n\n' + block + '\n\n' + warning + rest if title.startswith('#') else block + '\n\n' + warning + old
    return readable_markdown(revised)


def remove_shared_block(original):
    """Remove only our generated insertion, never the underlying research."""
    revised = re.sub(re.escape(START) + '.*?' + re.escape(END), '', original, flags=re.S)
    revised = re.sub(re.escape(HISTORY) + r'\s*<aside\b.*?</aside>', '', revised, flags=re.S)
    revised = re.sub(re.escape(HISTORY) + r'\s*> 以下为历史研究快照[^\n]*\n?', '', revised)
    return revised


def write_transaction(root, outputs, *, preserve_history=True):
    """Stage all bytes, replace, roll back on errors; keep content-addressed originals."""
    root = Path(root).resolve()
    originals, staged, replaced, backups = {}, {}, [], []
    try:
        for path, content in outputs.items():
            path = Path(path).resolve()
            if not path.is_relative_to(root):
                raise ValueError('输出超出产品根')
            path.parent.mkdir(parents=True, exist_ok=True)
            old = path.read_bytes() if path.exists() else None
            if old == content:
                continue
            originals[path] = old
            if old is not None and preserve_history:
                relative = Path(*('receipts.bak' if p == 'receipts' else p for p in path.relative_to(root).parts))
                backup = root / '.s1-history' / hashlib.sha256(old).hexdigest() / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                if not backup.exists():
                    backup.write_bytes(old)
                    backups.append(backup)
            fd, temp = tempfile.mkstemp(prefix='.s1-sync-', dir=path.parent)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(content)
            staged[path] = Path(temp)
        for path, temp in staged.items():
            os.replace(temp, path)
            replaced.append(path)
    except Exception:
        for path in reversed(replaced):
            if originals[path] is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(originals[path])
        for backup in backups:
            backup.unlink(missing_ok=True)
        raise
    finally:
        for temp in staged.values():
            temp.unlink(missing_ok=True)


def selected_reports(root):
    for path in (root / 'S1_选品板块').rglob('*'):
        if not path.is_file() or path.suffix.lower() not in ('.html', '.md'):
            continue
        if any(p.startswith('.') or p == '_history' for p in path.relative_to(root).parts):
            continue
        name = path.stem
        if (name in ('01_市场穿透报告', '阶段_S1_选品可行性研究报告', '03D_组合CT与40%利润判断', '03_产品CT')
                or name.startswith('03_1688产品资料_')):
            yield path



def prepare_sync_manifest(root, outputs):
    """Prepare an existing native seal for the same output transaction.

    This is pure: the caller commits returned bytes with its generated outputs,
    then runs the unchanged decision validator and rolls everything back on any
    failure. Child reports outside this transaction retain their original seals.
    """
    import s1_decision_report as report
    root=Path(root).resolve();out=root/report.OUTPUT_DIR
    path=out/'s1_report_sync.json'
    if not path.is_file():return {}
    manifest=json.loads(path.read_text(encoding='utf-8-sig'))
    pending={Path(p).resolve():raw for p,raw in outputs.items()}
    if any(not p.is_relative_to(root) for p in pending):
        raise ValueError('同步输出路径越界')
    sealed=manifest.get('reports')
    if not isinstance(sealed,dict):raise ValueError('同步清单缺少报告绑定')
    reports=dict(sealed)
    for relative,expected in sealed.items():
        source=(root/relative).resolve()
        if not source.is_relative_to(root):raise ValueError('同步报告路径越界')
        if source not in pending:
            if not source.is_file():raise ValueError('同步清单中的报告不存在：'+relative)
            if report.digest(source.read_bytes())!=expected:
                raise ValueError('未参与本次生成的旧报告已变化：'+relative)
    for source in selected_reports(root):
        relative=source.relative_to(root).as_posix()
        reports[relative]=report.digest(pending[source] if source in pending else source.read_bytes())
    # Preserve entries that selected_reports does not recognize; never erase an
    # older tracked output merely because current discovery no longer finds it.
    for relative in sealed:
        source=(root/relative).resolve()
        if source in pending:reports[relative]=report.digest(pending[source])
    for source,raw in pending.items():
        if source.suffix.lower() in ('.md','.html') and source.stem=='阶段_S1_选品可行性研究报告':
            reports[source.relative_to(root).as_posix()]=report.digest(raw)
    model_path=out/report.MODEL
    model_raw=pending.get(model_path)
    if model_raw is None:model_raw=model_path.read_bytes()
    model=json.loads(model_raw)
    contract=out/'报告呈现约定.yaml'
    contract_raw=pending.get(contract)
    if contract_raw is None and contract.is_file():contract_raw=contract.read_bytes()
    manifest.update(input_sha256=model['input_sha256'],model_sha256=report.digest(model_raw),
                    presentation_contract_sha256=report.digest(contract_raw) if contract_raw is not None else None,
                    reports=reports)
    return {path:json.dumps(manifest,ensure_ascii=False,indent=2).encode('utf-8')}


def sync(root):
    """Rollback the decision overview AND existing reports if any sync step fails."""
    import s1_decision_report as report
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    lock = root / '.s1-sync.lock'
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.close(fd)
    originals = {}
    try:
        def visible_outputs():
            # Stage rendering can archive correction drafts and add a model.
            # Keep the entire affected document set, not just the old main page.
            return {p for folder in (root / 'S1_选品板块', root / '00_导航')
                    for p in folder.rglob('*') if p.is_file()
                    and p.suffix.lower() in ('.md', '.html', '.json', '.yaml', '.yml')
                    and not any(x == '_history' or x.startswith('.') for x in p.relative_to(root).parts)}
        paths = visible_outputs()
        paths.update(root.glob('README*'))
        paths.update(root / report.OUTPUT_DIR / name for name in
                     (report.MD, report.HTML, report.MODEL, 's1_report_sync.json'))
        originals = {p: p.read_bytes() if p.exists() else None for p in paths}
        return _sync_unlocked(root)
    except BaseException:
        for path in visible_outputs() - set(originals):
            path.unlink(missing_ok=True)
        for path, old in originals.items():
            if old is None:
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(old)
        raise
    finally:
        lock.unlink(missing_ok=True)


def _sync_unlocked(root):
    import s1_decision_report as report
    root = Path(root).resolve()
    result = report.generate(root)
    out = root / report.OUTPUT_DIR
    model = json.loads((out / 's1_decision_model.json').read_text(encoding='utf8'))
    digest = model['input_sha256']
    outputs = {}
    for path in selected_reports(root):
        old = path.read_text(encoding='utf-8-sig')
        if path.suffix.lower() == '.html' and not re.search(r'<body\b', old, re.I):
            raise ValueError('HTML 缺少 body，无法安全同步')
        text = remove_shared_block(old)
        outputs[path] = text.encode('utf8')
    # Each child keeps its own evidence; only the official stage report combines
    # current models. Its MD/HTML are rendered from one section structure.
    from s1_stage_report import MD as stage_md, HTML as stage_html
    stage_result = None
    if (root / report.INPUT).exists():
        write_transaction(root, outputs)
        from s1_stage_report import run as render_stage
        stage_result = render_stage(root,sync_manifest=False)
        from s1_report_links import rewrite_reports
        rewrite_reports(root)
        outputs = {p: p.read_bytes() for p in selected_reports(root)}
        outputs[root / stage_md] = (root / stage_md).read_bytes()
        outputs[root / stage_html] = (root / stage_html).read_bytes()
    manifest = {'schema_version': 1, 'input_sha256': digest,
                'model_sha256': hashlib.sha256((out / 's1_decision_model.json').read_bytes()).hexdigest(),
                'presentation_contract_sha256': hashlib.sha256((out / '报告呈现约定.yaml').read_bytes()).hexdigest() if (out / '报告呈现约定.yaml').exists() else None,
                'reports': {str(p.relative_to(root)).replace('\\', '/'): hashlib.sha256(b).hexdigest() for p, b in outputs.items()}}
    outputs[out / 's1_report_sync.json'] = json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf8')
    write_transaction(root, outputs)
    if not report.validate_output_set(root):
        raise ValueError('决策报告已变化，请重新生成')
    home = Path(__file__).resolve().parents[1]
    nav = {'status': 'not_available_in_source_tree'}
    if (home / 'templates/stage_roadmap.yaml').is_file():
        done = subprocess.run([sys.executable, str(home / 'scripts/render_nav_dashboard.py'),
                               '--root', str(root), '--home', str(home), '--stage', 'S1'],
                              capture_output=True, text=True, encoding='utf8', timeout=45)
        nav = {'status': 'ok' if done.returncode == 0 else 'failed', 'exit_code': done.returncode,
               'path': '00_导航/导航.html'}
        if done.returncode:
            nav['error'] = done.stderr[-1200:]
    return dict(result or {}, ok=True, input_sha256=digest, synchronized_reports=list(manifest['reports']), navigation=nav, stage_report=stage_result)


def refresh_manifest(root):
    """Record an explicit presentation-only edit against unchanged inputs/model."""
    import s1_decision_report as report
    root = Path(root).resolve(); out = root / report.OUTPUT_DIR
    path = out / 's1_report_sync.json'
    manifest = json.loads(path.read_text(encoding='utf8'))
    model_bytes = (out / report.MODEL).read_bytes()
    model = json.loads(model_bytes)
    if report.digest((root / report.INPUT).read_bytes()) != model['input_sha256'] or report.digest(model_bytes) != manifest['model_sha256']:
        raise ValueError('事实输入或决策模型已变化，不能只刷新排版哈希；请重新生成')
    cost = report.load_cost(root)
    _, provenance = report.load_decision_inputs(root)
    if (cost.get('_file_sha256') != model.get('cost_source_sha256')
            or cost.get('_inputs_file_sha256') != model.get('cost_inputs_observed_sha256')
            or provenance['profile_sha256'] != model.get('profile_sha256')):
        raise ValueError('成本或产品事实已变化，请重新生成报告')
    stage_path = out / 's1_stage_report.json'
    if stage_path.exists():
        stage = json.loads(stage_path.read_text(encoding='utf8'))
        for relative, expected in stage.get('source_manifest', {}).items():
            source = (root / relative).resolve()
            if not source.is_relative_to(root):
                raise ValueError('来源路径越界')
            observed = report.digest(source.read_bytes()) if source.is_file() else None
            if observed != expected:
                raise ValueError('报告来源或呈现约定已变化，请重新生成：' + relative)
    contract = out / '报告呈现约定.yaml'
    manifest['presentation_contract_sha256'] = report.digest(contract.read_bytes()) if contract.exists() else None
    manifest['reports'] = {str(p.relative_to(root)).replace('\\', '/'): report.digest(p.read_bytes()) for p in selected_reports(root)}
    write_transaction(root, {path: json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf8')})
    return {'ok': True, 'reports': len(manifest['reports']), 'action': 'presentation_hashes_refreshed'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--refresh-manifest', action='store_true', help='局部排版后刷新同步记录，事实变化时拒绝')
    mode.add_argument('--compact-receipt', help='S1 第9步活动入口：只生成完整阶段 MD、HTML 和指定稳定回执')
    mode.add_argument('--legacy', action='store_true', help='显式历史独立同步；未指定模式的旧调用仍兼容')
    args = parser.parse_args(argv)
    if args.compact_receipt is not None:
        root = Path(args.root).resolve()
        try:
            from r01_case_store import CompactCase
            if not CompactCase(root).exists('s1_decision_inputs.yaml'):
                raise ValueError('缺少 s1_decision_inputs.yaml：按已有真实事实与授权建立输入，未知保留 null；未生成报告，未放行业务验收')
            from s1_stage_report import run as render_stage
            # Never visit legacy sync first: it writes appendices, manifests
            # and history before the compact transaction can run.
            outcome = render_stage(root, compact=True, receipt_path=args.compact_receipt)
        except (OSError, ValueError, KeyError, TypeError) as error:
            print(json.dumps({'ok': False, 'status': 'blocked', 'error': str(error)}, ensure_ascii=False, indent=2))
            return 1
    else:
        outcome = refresh_manifest(args.root) if args.refresh_manifest else sync(args.root)
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
