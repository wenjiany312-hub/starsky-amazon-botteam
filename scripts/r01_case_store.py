#!/usr/bin/env python3
"""Exact-byte compact case storage and disposable native filesystem views.

Receipts are containers, never replacements for source hashes. resolve() returns
the logical path; native code must run inside materialize(). No original case
file is removed. Temporary IO and persistent case IO are reported separately.
"""
from __future__ import annotations

import base64
import binascii
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import re
import subprocess
import sys
import tempfile
import threading
import time
import types

import yaml


class CaseStoreError(ValueError):
    pass


def validate_receipt_seals(document):
    """Reject internally contradictory seals without treating history as current."""
    files = document.get('business_files') or {}
    pointers = []
    stage = document.get('s1_stage_data') or {}
    if stage.get('external_path'):
        pointers.append((stage['external_path'], stage))
    proof = document.get('report_proof') or {}
    if proof.get('path') and proof.get('sha256'):
        pointers.append((proof['path'], proof))
    for relative, item in pointers:
        current = files.get(relative)
        if current and (current.get('sha256') != item.get('sha256') or
                        ('size' in current and 'size' in item and current['size'] != item['size'])):
            raise CaseStoreError('conflicting receipt fingerprints / 回执指纹冲突：' + relative)


_REGISTRY_KEY = '_starsky_r01_case_store_shared_registry'
if _REGISTRY_KEY not in sys.modules:
    shared = types.ModuleType(_REGISTRY_KEY)
    shared.views = {}
    shared.locks = {}
    shared.guard = threading.RLock()
    shared.audit_installed = False
    sys.modules[_REGISTRY_KEY] = shared
_SHARED = sys.modules[_REGISTRY_KEY]
_MEDIA = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.svg', '.bmp', '.tiff',
          '.tif', '.avif', '.mp4', '.mov', '.webm', '.avi', '.mkv', '.m4v'}
_PRIVATE = {'.git', '__pycache__', '.keys', 'keys', '.ssh', '.credentials'}
_ENV_VIEW = 'STARSKY_R01_VIEW_ROOT'
_ENV_LOGICAL = 'STARSKY_R01_LOGICAL_ROOT'
# Resolve before installing the audit hook. tempfile.gettempdir() takes a
# non-reentrant initialization lock and itself opens probe files; calling it
# from an open audit hook can deadlock its first use in a native subprocess.
_TEMP_ROOT = Path(tempfile.gettempdir()).resolve()


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(raw):
    return json.loads(raw.decode('utf-8-sig'))


def _json_bytes(obj):
    def extra(value):
        if isinstance(value, Path):
            return str(value)
        raise TypeError(f'not JSON serializable: {type(value).__name__}')
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2, default=extra) + '\n').encode('utf-8')


def _canonical_hash(value):
    return _hash(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf8'))


RESERVED_RECEIPT_FIELDS = frozenset({'human_report', 'report_proof', 'knowledge', 'validation', 'card', 'ack', 's1_stage_data'})


def step_report_catalog(root, record):
    """Read the one persisted catalogue; state copies are legacy fallbacks only."""
    store = CompactCase(root)
    reference = record.get('receipt_ref') or record.get('raw_ref') or {}
    if reference.get('path'):
        path = store.resolve(reference['path'])
        document = _json(path.read_bytes())
        if 'reports' in document:
            return display_catalog(root, public_report_catalog(document['reports']), reference['path'])
    receipt = record.get('receipt') or {}
    reports = record.get('reports') or (receipt.get('reports') if isinstance(receipt, dict) else []) or []
    if reports:
        return public_report_catalog(reports)
    artifacts = record.get('artifacts') or (receipt.get('artifacts') if isinstance(receipt, dict) else {}) or {}
    paths = []
    for item in artifacts.values():
        rel = item.get('path') if isinstance(item, dict) else item
        if not rel or Path(rel).suffix.lower() not in {'.md', '.html', '.xlsx', '.pdf'}:
            continue
        if re.fullmatch(r'\d{2}(?:_回执)?\.md', Path(rel).name):
            continue
        if rel not in paths and store.resolve(rel).is_file():
            paths.append(rel)
    paths.sort(key=lambda rel: (Path(rel).suffix.lower() != '.html', rel))
    return [{'label': Path(rel).name, 'path': rel} for rel in paths]


def public_report_catalog(reports):
    """Internal compact projections are implementation copies, not deliverables."""
    return [deepcopy(row) for row in reports
            if not str(row.get('path', '')).replace('\\', '/').startswith('00_总控/紧凑业务报告/')]


_OFFER = re.compile(r'(?:offer_|_)(\d{9,})(?:/|\.(?:md|html?)$)')
_SUPPLIER_SUMMARY = ('alibaba-supplier-evaluation/',)
_S1_DELIVERY_STEP = 9


def selected_offer(root):
    """The 1688 offer the user actually chose in S1 (the latest decision wins); None before a choice."""
    try:
        state = yaml.safe_load((Path(root) / 'workflow_state.yaml').read_bytes()) or {}
    except (OSError, yaml.YAMLError):
        return None
    chosen = None
    for row in (state.get('s1_execution') or {}).get('records') or []:
        decision = row.get('decision') if isinstance(row, dict) else None
        if isinstance(decision, dict):
            match = re.search(r'\d{9,}', str(decision.get('selection') or ''))
            if match:
                chosen = match.group()
    return chosen


def display_catalog(root, reports, receipt_path):
    """R06.1 PL-04: what the navigation and cards show for one S1 step; the sealed receipt is not changed.

    The receipt lists every readable file sealed with the step. Shown are only the step's deliverables:
    raw media (patent PDFs) are evidence, not reports; a file appears only at the earliest step that produced it;
    individual 1688 offer reports appear only for the supplier the user selected (before a choice the shortlist and
    evaluation summaries are the comparison); and the delivery step shows only the official stage report."""
    match = re.search(r'(?:^|/)S1/receipts/(\d{2})\.json$', str(receipt_path).replace('\\', '/'))
    if not match:
        return reports
    step = int(match.group(1))
    folder = CompactCase(root).resolve(str(receipt_path)).parent
    earlier = set()
    for number in range(1, step):
        path = folder / f'{number:02}.json'
        try:
            earlier.update(str(row.get('path')) for row in (_json(path.read_bytes()).get('reports') or []))
        except (OSError, ValueError, AttributeError):
            continue
    offer = selected_offer(root)
    rows = []
    for row in reports:
        rel = str(row.get('path', '')).replace('\\', '/')
        if Path(rel).suffix.lower() == '.pdf' or rel in earlier:
            continue
        found = _OFFER.search(rel)
        if found and not rel.startswith(_SUPPLIER_SUMMARY) and '1688' in rel and found.group(1) != offer:
            continue
        rows.append(row)
    if step == _S1_DELIVERY_STEP:
        official = [row for row in rows if Path(str(row.get('path', ''))).name.startswith('阶段_S1')]  # md 与网页版
        if official:
            rows = official
    return rows


def business_report_path(root, stage, step):
    names = {1: '条件确认', 2: '方向筛选', 3: '需求验证门', 4: '选款与对标',
             5: '1688找货与产品CT', 6: '综合成本与定价', 7: '推广难度',
             8: 'IP与合规', 9: '综合报告'} if stage == 'S1' else {}
    state = Path(root) / 'workflow_state.yaml'
    if state.is_file():
        value = yaml.safe_load(state.read_bytes()) or {}
        flow = value.get(stage.lower() + '_execution') or (value.get('flow') or {}).get(stage.lower() + '_execution') or {}
        for row in flow.get('roadmap_steps') or []:
            if row.get('n') == int(step):
                label = row.get('name')
                if isinstance(label, dict):
                    label = label.get(flow.get('path')) or next(iter(label.values()), '')
                names[int(step)] = label or names.get(int(step))
    name = re.sub(r'[<>:"/\\|?*+\s]+', '', str(names.get(int(step)) or f'{stage}步骤{int(step)}'))
    return f'00_总控/紧凑业务报告/{stage}/{int(step):02}_{name}.md'


def verify_migrated_reference(document, selector, expected, root):
    """Reconstruct the original sealed value from exact archived bytes.

    A migration record is an evidence recipe, not a hash exemption. Both the
    old value and its new metadata representation must still match their seals.
    """
    def selected(pointer):
        value = document
        for key in pointer[1:].split('/'):
            key = key.replace('~1', '/').replace('~0', '~')
            value = value[int(key)] if isinstance(value, list) else value[key]
        return value
    for migration in document.get('storage_migrations', []):
        if migration.get('selector') != selector or migration.get('before_sha256') != expected:
            continue
        current = selected(selector)
        if _canonical_hash(current) != migration.get('after_sha256'):
            continue
        restored = {}
        for rel, evidence in migration.get('files', {}).items():
            item = deepcopy(evidence['original_metadata'])
            raw = CompactCase(root).resolve(evidence['path']).read_bytes()
            if _hash(raw) != item.get('sha256'):
                raise CaseStoreError('migration source hash mismatch: ' + rel)
            item['bytes_base64'] = base64.b64encode(raw).decode('ascii')
            restored[rel] = item
        original = deepcopy(migration.get('original_envelope'))
        if original is None:
            original = restored
            current_files = current
        else:
            original['business_files'] = restored
            current_files = current.get('business_files', {})
        # A disk edit is not a lossless format migration.
        if set(current_files) != set(restored) or any(
                current_files[rel].get('sha256') != item['sha256'] for rel, item in restored.items()):
            continue
        decode_business_files(current_files, root)
        if _canonical_hash(original) == expected:
            return True
    return False


def _relative(value):
    text = str(value).replace('\\', '/')
    if not text or PurePosixPath(text).is_absolute() or PureWindowsPath(text).drive:
        raise CaseStoreError(f'path outside case: {value}')
    parts = PurePosixPath(text).parts
    if any(part in ('..', '') for part in parts) or text.startswith('/'):
        raise CaseStoreError(f'path outside case: {value}')
    if any(part.casefold() in _PRIVATE or part.casefold() == '.env' for part in parts):
        raise CaseStoreError(f'private path is not case business data: {value}')
    return PurePosixPath(*parts).as_posix()


def decode_business_files(mapping, root=None):
    """Decode an uncommitted receipt in memory, with original-byte SHA checks."""
    if not isinstance(mapping, dict):
        raise CaseStoreError('business_files must be an object')
    decoded = {}
    folded = set()
    for path, item in mapping.items():
        rel = _relative(path)
        if not _business_path(rel):
            raise CaseStoreError(f'business data cannot replace control metadata: {rel}')
        if rel.casefold() in folded:
            raise CaseStoreError(f'ambiguous case-insensitive business path: {rel}')
        folded.add(rel.casefold())
        if not isinstance(item, dict):
            raise CaseStoreError(f'bad business file entry: {rel}')
        if 'bytes_base64' not in item:
            location = _relative(item.get('path', ''))
            if (location != rel and not location.startswith('_history/')) or not isinstance(item.get('size'), int) or not re.fullmatch('[0-9a-f]{64}', str(item.get('sha256', ''))):
                raise CaseStoreError(f'bad physical source metadata: {rel}')
            if root is not None:
                path = CompactCase(root).resolve(location)
                if not path.is_file():
                    raise CaseStoreError(f'physical source missing: {rel}')
                raw = path.read_bytes()
                if len(raw) != item['size'] or _hash(raw) != item['sha256']:
                    # 3.0.25: the bare message sent the assistant digging through receipts for six minutes.
                    raise CaseStoreError(f'physical source hash mismatch: {rel}。'
                                         '这个文件已被前面步骤封存，产品根里的它被直接改过。不要直接改封存文件：'
                                         '先把它恢复成改动前的内容，再把新内容放进 r01_native.py execute --root <产品根> --step <本步> '
                                         f'请求的 inputs（{{"{rel}": {{"text" 或 "yaml": 新内容}}}}）提交；'
                                         '报告类文件不用手改，重新运行本步原生生成器即可。')
                decoded[rel] = raw
            continue
        try:
            raw = base64.b64decode(item['bytes_base64'], validate=True)
        except (KeyError, ValueError, TypeError, binascii.Error) as exc:
            raise CaseStoreError(f'bad base64 source: {rel}') from exc
        if _hash(raw) != item.get('sha256'):
            raise CaseStoreError(f'business source hash mismatch: {rel}')
        before = item.get('actual_before_hash')
        if before is not None and (not isinstance(before, str) or len(before) != 64
                                   or any(c not in '0123456789abcdef' for c in before)):
            raise CaseStoreError(f'bad actual_before_hash: {rel}')
        decoded[rel] = raw
    return decoded


def seal_receipt_decision(receipt, decision_id, root=None):
    """Pure immutable raw snapshot for a Flow event; the caller writes NN.json.

    The current final slot continues to coalesce. Only explicitly identified
    decisions retain an additional raw version, never each generator revision.
    """
    if not isinstance(decision_id, str) or not decision_id or '/' in decision_id or '~' in decision_id:
        raise CaseStoreError('decision_id must be a nonempty JSON-pointer-safe identity')
    decode_business_files(receipt.get('business_files', {}))
    if not receipt.get('business_snapshot'):
        raise CaseStoreError('decision requires a compact business snapshot')
    result = deepcopy(receipt)
    snapshot = {key: deepcopy(receipt[key]) for key in ('business_snapshot', 'business_files', 'report_proof', 'human_report') if key in receipt}
    decisions = result.setdefault('business_decisions', {})
    if root is not None and receipt.get('business_snapshot', {}).get('schema_version') == 2:
        meta = receipt['business_snapshot']
        prefix = f"_history/{meta['stage']}/{int(meta['step']):02}/d-{_hash(_relative(decision_id).encode())[:12]}"
        store = CompactCase(root)
        for rel, raw in decode_business_files(receipt.get('business_files', {}), root).items():
            location = prefix + '/' + _hash(rel.encode())[:16] + Path(rel).suffix
            target = store.resolve(location)
            if target.exists() and target.read_bytes() != raw:
                raise CaseStoreError('a named user decision cannot be overwritten')
            _write(target, raw)
            snapshot['business_files'][rel] = {'path': location, 'sha256': _hash(raw), 'size': len(raw)}
    if decision_id in decisions and decisions[decision_id] != snapshot:
        raise CaseStoreError('a named user decision cannot be overwritten')
    decisions[decision_id] = snapshot
    return result


def _env_view(root, allow_logical=False):
    a, b = os.environ.get(_ENV_VIEW), os.environ.get(_ENV_LOGICAL)
    if not a or not b:
        return None
    view, real = Path(a).resolve(), Path(b).resolve()
    if root != view and not (allow_logical and root == real):
        return None
    marker = view / '.r01-view.json'
    try:
        valid = _json(marker.read_bytes()) == {'logical_root': str(real), 'view_root': str(view)}
    except (OSError, ValueError):
        valid = False
    return (view, real) if valid else None


def logical_root(root):
    root = Path(root).resolve()
    for view in list(_SHARED.views.values()):
        if root == view.root or root == view.logical_root:
            return view.logical_root
    env = _env_view(root)
    return env[1] if env else root


def is_view(root):
    root = Path(root).resolve()
    return any(root == v.root for v in list(_SHARED.views.values())) or _env_view(root) is not None


def _business_path(rel):
    parts = Path(rel).parts
    return (not any(p.casefold() in _PRIVATE or p.casefold() == '.env' for p in parts)
            and not any(p in ('receipts', '流程控制', '紧凑业务报告') for p in parts)
            and not Path(rel).name.startswith(('.r01-', 'workflow_state.', 'currentnav', 'CURRENT_NAV')))


def _files(root):
    """Inventory without following directory symlinks or reading private data."""
    if not root.exists():
        return {}
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d.casefold() not in _PRIVATE)
        for name in sorted(files):
            path = Path(directory) / name
            if name.casefold() == '.env':
                continue
            if path.is_symlink():
                try:
                    path.resolve().relative_to(root)
                except ValueError as exc:
                    raise CaseStoreError(f'case symlink outside root: {path}') from exc
            result[path.relative_to(root).as_posix()] = path.read_bytes()
    return result


def _write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_bytes() == raw:
        return False
    # A single stable pathname, no persistent temp/backup sidecar.
    with path.open('wb') as stream:
        stream.write(raw)
    return True


@contextmanager
def _detached_commit_lock(real):
    """Use the same OS lock when committing an already collected in-memory step."""
    key = str(real).casefold()
    base = (Path(tempfile.gettempdir()) / 'starsky-r01-views' / _hash(key.encode())[:24]).resolve()
    base.mkdir(parents=True, exist_ok=True)
    with (base / 'case.lock').open('a+b') as stream:
        try:
            if os.name == 'nt':
                import msvcrt
                if stream.tell() == 0:
                    stream.write(b'0'); stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise CaseStoreError('case view is in use by another process') from exc
        yield


def verify_report_proof(receipt, root):
    """Verify the real readable report and every source bound to its projection."""
    proof = receipt.get('report_proof')
    if not proof:
        return True
    store = CompactCase(root)
    report = store.resolve(proof.get('path', ''))
    if not report.is_file() or _hash(report.read_bytes()) != proof.get('sha256'):
        raise CaseStoreError(f'report proof hash mismatch: {report}')
    modern = receipt.get('business_snapshot', {}).get('schema_version') == 2
    if modern and any('bytes_base64' in item for item in receipt.get('business_files', {}).values()):
        raise CaseStoreError('report projection cannot contain encoded schema-2 sources')
    decoded = {} if modern else decode_business_files(receipt.get('business_files', {}), root)
    for source in proof.get('sources', []):
        if modern:
            snapshot = source.get('snapshot_path')
            if not snapshot:
                raise CaseStoreError('report projection missing source snapshot: ' + source.get('path', ''))
            decoded[_relative(source['path'])] = store.resolve(snapshot).read_bytes()
        raw = decoded.get(_relative(source.get('path', '')))
        if raw is None or _hash(raw) != source.get('sha256'):
            raise CaseStoreError(f'report source hash mismatch: {source.get("path")}')
    if proof.get('kind') == 's1_stage_report_v1':
        data = receipt.get('s1_stage_data') or {}
        if data.get('external_path'):
            raw = store.resolve(data['external_path']).read_bytes()
            if _hash(raw) != data.get('sha256') or len(raw) != data.get('size'):
                raise CaseStoreError('stage report model hash mismatch')
            data = _json(raw)
        manifest = data.get('source_manifest')
        model = data.get('stage_model') or {}
        if (not isinstance(manifest, dict) or manifest != model.get('source_manifest')
                or _hash(json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode())
                   != proof.get('source_manifest_sha256')
                or model.get('source_manifest_sha256') != proof.get('source_manifest_sha256')):
            raise CaseStoreError('stage report source manifest mismatch')
        outputs = data.get('outputs_sha256') or {}
        if outputs.get(proof['path']) != proof['sha256'] or len(outputs) != 2:
            raise CaseStoreError('stage report output binding mismatch')
        for relative, expected in outputs.items():
            output = store.resolve(relative)
            if not output.is_file() or _hash(output.read_bytes()) != expected:
                raise CaseStoreError(f'stage report output hash mismatch: {relative}')
    if proof.get('projection_version') in (1, 2):
        view = CaseView(Path(proof['source_view_root']), Path(proof.get('source_logical_root') or store.logical_root))
        meta = receipt['business_snapshot']
        bodies = [f"## {source['path']}\n\n" + _report_body(decoded[source['path']], source['path'], view).rstrip('\r\n') + '\n'
                  for source in proof.get('sources', [])]
        if not bodies:
            bodies = ['本步尚未提供可读业务报告。已封存原生输入与输出，业务结论待补齐。\n']
        projection = (f"# {meta['stage']} 第 {int(meta['step'])} 步业务报告\n\n" + '\n'.join(bodies)).encode('utf-8')
        if projection != report.read_bytes():
            raise CaseStoreError(f'report projection differs from sealed native sources: {report}')
    return True


def _project_link(target, rel, view):
    if not target or re.match(r'^[a-zA-Z]+://|^(?:data:|#|mailto:)', target):
        return target
    bare, sep, anchor = target.partition('#')
    path = Path(bare)
    if not path.is_absolute():
        path = view.root / Path(rel).parent / path
    try:
        logical = view.logical_path(path)
    except CaseStoreError:
        return target
    return logical.as_posix() + (sep + anchor if sep else '')


class _ReadableHTML(HTMLParser):
    """Text/table/media projection; the exact complete HTML stays in the receipt."""
    def __init__(self, rel, view):
        super().__init__(convert_charrefs=True)
        self.rel, self.view, self.parts, self.skip, self.links = rel, view, [], 0, []
        self.tables = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('script', 'style', 'head'):
            self.skip += 1
        if self.skip:
            return
        if re.fullmatch(r'h[1-6]', tag):
            self.parts.append('\n\n' + '#' * min(int(tag[1]) + 1, 6) + ' ')
        elif tag == 'table':
            self.parts.append('\n\n')
            self.tables.append(len(self.parts))
        elif tag in ('p', 'div', 'section', 'article', 'tr', 'ul', 'ol'):
            self.parts.append('\n\n' if tag != 'tr' else '\n')
        elif tag in ('th', 'td'):
            self.parts.append(' | ')
        elif tag == 'br':
            self.parts.append('\n')
        elif tag == 'li':
            self.parts.append('\n- ')
        elif tag in ('img', 'video', 'source'):
            source = _project_link(attrs.get('src', ''), self.rel, self.view)
            if source:
                self.parts.append(f'\n\n![{attrs.get("alt", tag)}](<{source}>)\n\n')
        elif tag == 'a':
            self.parts.append('[')
            self.links.append(_project_link(attrs.get('href', ''), self.rel, self.view))

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'head'):
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag == 'table' and self.tables:
            start = self.tables.pop()
            rows = [[cell.strip() for cell in line.strip().strip('|').split('|')]
                    for line in ''.join(self.parts[start:]).splitlines() if line.strip()]
            if rows:
                width = max(map(len, rows))
                rendered = ['| ' + ' | '.join(row + [''] * (width - len(row))) + ' |' for row in rows]
                rendered.insert(1, '| ' + ' | '.join(['---'] * width) + ' |')
                self.parts[start:] = ['\n'.join(rendered) + '\n\n']
        elif tag == 'a' and self.links:
            self.parts.append('](<' + self.links.pop() + '>)')
        elif tag in ('p', 'div', 'section', 'article', 'table') or re.fullmatch(r'h[1-6]', tag):
            self.parts.append('\n\n')

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def _report_body(raw, rel, view):
    text = raw.decode('utf-8-sig')
    # Only the separate readable projection uses display paths. Raw source bytes
    # remain untouched, and the projection has its own recomputable hash.
    text = text.replace(str(view.root), str(view.logical_root)).replace(view.root.as_posix(), view.logical_root.as_posix())
    if Path(rel).suffix.lower() in ('.html', '.htm'):
        parser = _ReadableHTML(rel, view)
        parser.feed(text)
        return re.sub(r'\n[ \t]*\n(?:[ \t]*\n)+', '\n\n', ''.join(parser.parts)).strip() + '\n'
    def link(match):
        target = match.group(2).strip('<>')
        return match.group(1) + '(<'+ _project_link(target, rel, view) + '>)'
    return re.sub(r'(!?\[[^\]]*\])\(([^\n)]+)\)', link, text).rstrip('\r\n') + '\n'


def _audit(event, args):
    if event != 'open' or not args:
        return
    mode = args[1] if len(args) > 1 else None
    flags = args[2] if len(args) > 2 else 0
    write = (isinstance(mode, str) and any(c in mode for c in 'wax+')) or (
        isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
    if not write or not isinstance(args[0], (str, bytes, os.PathLike)):
        return
    try:
        path = Path(os.fsdecode(args[0])).resolve()
        for view in list(_SHARED.views.values()):
            try:
                rel = path.relative_to(view.root).as_posix()
            except ValueError:
                if path.is_relative_to(_TEMP_ROOT) and not path.is_relative_to(view.logical_root):
                    view._external_temp_write_events.append(path.as_posix())
                continue
            if not rel.startswith('.r01-'):
                view._write_events.append(rel)
    except (OSError, ValueError):
        pass


if not _SHARED.audit_installed:
    sys.addaudithook(_audit)
    _SHARED.audit_installed = True


@dataclass
class PreparedStep:
    logical_root: Path
    stage: str
    step: int
    receipt_path: Path
    report_path: Path
    receipt: dict
    outputs: dict
    media: dict
    before: dict
    temporary_io: dict
    receipt_before: bytes | None
    case_sequence_before: int


class CaseView:
    def __init__(self, root, real, initial=None, packed=None, overrides=None):
        self.root, self.logical_root = Path(root), Path(real)
        self._initial = initial or {}
        self._packed = packed or {}
        self._overrides = overrides or {}
        self._write_events = []
        self._external_temp_write_events = []
        self._materialized_files = 0

    def map_path(self, path):
        return self.root / self._rel(path)

    def logical_path(self, path):
        return self.logical_root / self._rel(path)

    def _rel(self, path):
        if Path(path).is_absolute():
            try:
                return _relative(Path(path).resolve().relative_to(self.root).as_posix())
            except ValueError:
                pass
        return CompactCase(self.logical_root)._rel(path)

    def run_native(self, script, args=(), **kwargs):
        """Run a Python CLI unchanged, logging its actual Python write opens.

        Instrumentation wraps runpy, not native filesystem/hash APIs. stdout,
        stderr and exit codes retain normal subprocess.run semantics. OS writes
        by non-Python grandchildren are outside this audit's stated scope.
        """
        bootstrap = '''import importlib.util,json,os,runpy,sys
from pathlib import Path
helper,script,*args=sys.argv[1:]
spec=importlib.util.spec_from_file_location('_r01_native_io_audit',helper)
mod=importlib.util.module_from_spec(spec);sys.modules[spec.name]=mod;spec.loader.exec_module(mod)
view=mod.CaseView(Path(os.environ['STARSKY_R01_VIEW_ROOT']),Path(os.environ['STARSKY_R01_LOGICAL_ROOT']))
key=str(view.logical_root).casefold();mod._SHARED.views[key]=view
sys.argv=[script,*args];sys.path[0]=str(Path(script).resolve().parent)
try:
    runpy.run_path(script,run_name='__main__')
finally:
    journal=view.root/('.r01-io-'+str(os.getpid())+'.json')
    journal.write_text(json.dumps({'write_open_events':view._write_events,'external_temp_write_events':view._external_temp_write_events}),encoding='utf-8')
'''
        kwargs.setdefault('cwd', self.root)
        return subprocess.run([sys.executable, '-B', '-c', bootstrap, str(Path(__file__).resolve()),
                               str(Path(script).resolve()), *map(str, args)], **kwargs)

    def collect(self, step, report_paths=(), *, stage='S1', receipt_path=None,
                revision=None, metadata=None, receipt_fields=None, kind='final', decision_id=None, now=None):
        return CompactCase(self.logical_root).prepare(
            self, step, report_paths, stage=stage, receipt_path=receipt_path,
            revision=revision, metadata=metadata, receipt_fields=receipt_fields,
            kind=kind, decision_id=decision_id, now=now)

    prepare = collect


class CompactCase:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.logical_root = logical_root(self.root)

    def _rel(self, path):
        value = Path(path)
        if value.is_absolute():
            candidates = [self.logical_root, self.root]
            candidates.extend(v.root for v in list(_SHARED.views.values()) if v.logical_root == self.logical_root)
            for base in candidates:
                try:
                    rel = value.resolve().relative_to(base)
                    return _relative(rel.as_posix())
                except ValueError:
                    pass
            raise CaseStoreError(f'path outside case: {path}')
        rel = _relative(path)
        final = (self.logical_root / rel).resolve()
        try:
            final.relative_to(self.logical_root)
        except ValueError as exc:
            raise CaseStoreError(f'path outside case: {path}') from exc
        return rel

    def resolve(self, path):
        """Return a safe *logical* path. It may exist only inside a receipt."""
        return self.logical_root / self._rel(path)

    def _receipts(self):
        if not self.logical_root.exists():
            return []
        result = []
        # Only formal workflow locations are active. A historical or imported
        # directory named receipts is never a registration.
        directories = sorted(self.logical_root.glob('00_总控/*/运行数据/流程控制/S*/receipts'))
        for directory in directories:
            if (directory.parent.name not in {'S1', 'S2', 'S3', 'S4', 'S5'} or
                    any(p.casefold() in _PRIVATE | {'.s1-history', '_history', 'receipts.bak'}
                        for p in directory.relative_to(self.logical_root).parts) or
                    not directory.resolve().is_relative_to(self.logical_root) or
                    any(p.is_symlink() for p in [directory, *directory.parents] if p != self.logical_root)):
                continue
            for path in sorted(directory.glob('*.json')):
                if path.is_symlink():
                    continue
                try:
                    obj = _json(path.read_bytes())
                except (ValueError, OSError) as exc:
                    raise CaseStoreError(f'cannot read receipt: {path}') from exc
                if isinstance(obj, dict) and 'business_files' in obj:
                    validate_receipt_seals(obj)
                    result.append((path, obj))
        return result

    def _index(self):
        index = {}
        sequences = {}
        for path, obj in self._receipts():
            decoded = decode_business_files(obj['business_files'])
            meta = obj.get('business_snapshot', {})
            try:
                stage, step, revision = str(meta['stage']), int(meta['step']), int(meta['revision'])
                if stage not in {'S1', 'S2', 'S3', 'S4', 'S5'} or step < 1 or revision < 1:
                    raise ValueError()
                seq = meta.get('case_sequence')
                if seq is not None:
                    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
                        raise ValueError()
                    content = _json_bytes({'business_files': obj['business_files'], 'report_proof': obj.get('report_proof')})
                    if seq in sequences and sequences[seq] != content:
                        raise CaseStoreError(f'ambiguous case sequence {seq}: {path}')
                    sequences[seq] = content
                    rank = (1, seq, 0, 0)
                else:
                    rank = (0, int(stage[1:]), step, revision)
            except CaseStoreError:
                raise
            except (KeyError, TypeError, ValueError) as exc:
                raise CaseStoreError(f'ambiguous business revision: {path}') from exc
            verify_report_proof(obj, self.logical_root)
            for rel, item in obj['business_files'].items():
                if rel in obj.get('restored_disk_files', {}):
                    item = obj['restored_disk_files'][rel]
                raw = decoded.get(rel)
                candidate = (rank, raw, item, path)
                old = index.get(rel)
                if old and old[0] == rank and old[2]['sha256'] != item['sha256']:
                    raise CaseStoreError(f'ambiguous business revision for {rel}: {old[3]}, {path}')
                if old is None or rank > old[0]:
                    index[rel] = candidate
        for rel, (rank, raw, item, owner) in list(index.items()):
            physical = self.resolve(rel)
            if raw is None:
                raw = decode_business_files({rel: item}, self.logical_root)[rel]
                index[rel] = (rank, raw, item, owner)
                # An archived entry that remembers its pre-revision state: the physical copy may be that
                # stale original or the sealed version, but never a third one (a later user edit).
                before = item.get('actual_before_hash')
                if before and physical.is_file():
                    actual = _hash(physical.read_bytes())
                    if actual not in (before, _hash(raw)):
                        raise CaseStoreError(f'physical source conflict: {rel}; actual={actual}')
                continue
            if physical.is_file():
                actual = _hash(physical.read_bytes())
                if actual not in (item.get('actual_before_hash'), _hash(raw)):
                    raise CaseStoreError(f'physical source conflict: {rel}; actual={actual}')
            elif item.get('actual_before_hash') is not None:
                raise CaseStoreError(f'physical source conflict: previously present file is missing: {rel}')
        return index

    def restore_legacy(self):
        """Restore missing files only; keep user edits and record every conflict.

        Validate all archived bytes and paths before writing anything. Legacy
        reports remain untouched. Metadata replaces encoded content only after
        every missing file is safely restored.
        """
        rows = self._receipts()
        latest = {}
        decoded_rows = []
        historical = []
        for path, obj in rows:
            before = deepcopy(obj)
            migration_rows = []
            mapping = obj.get('business_files', {})
            decoded = decode_business_files(mapping)
            meta = obj.get('business_snapshot', {})
            rank = (meta.get('case_sequence', 0), meta.get('step', 0), meta.get('revision', 0))
            changed = bool(decoded)
            for identity, snapshot in obj.get('business_decisions', {}).items():
                raw_files = decode_business_files(snapshot.get('business_files', {}))
                if not raw_files:
                    continue
                prefix = f"_history/{meta.get('stage', 'S1')}/{int(meta.get('step', 1)):02}/d-{_hash(_relative(identity).encode())[:12]}"
                archived = {}
                for rel, raw in raw_files.items():
                    location = prefix + '/' + _hash(rel.encode())[:16] + Path(rel).suffix
                    target = self.resolve(location)
                    if target.is_file() and target.read_bytes() != raw:
                        raise CaseStoreError('historical decision source conflict: ' + rel)
                    historical.append((target, raw))
                    archived[rel] = {'path': location, 'sha256': _hash(raw), 'size': len(raw)}
                snapshot['business_files'] = archived
                snapshot.setdefault('business_snapshot', {})['schema_version'] = 2
                old_snapshot = before['business_decisions'][identity]
                migration_rows.append((f'/business_decisions/{identity}', old_snapshot, snapshot, archived))
                changed = True
            # One final source snapshot per step keeps projections verifiable
            # when a later step legitimately revises a shared business file.
            prefix = f"_history/{meta.get('stage', 'S1')}/{int(meta.get('step', 1)):02}/final"
            archives = {}
            for rel, raw in decoded.items():
                location = prefix + '/' + _hash(rel.encode())[:16] + Path(rel).suffix
                historical.append((self.resolve(location), raw))
                archives[rel] = {'path': location, 'sha256': _hash(raw), 'size': len(raw)}
                # Keep the pre-revision state so a later edit of the physical file is still told apart
                # from the stale-but-legitimate original (see _index).
                pre_hash = (mapping.get(rel) or {}).get('actual_before_hash')
                if isinstance(pre_hash, str) and re.fullmatch('[0-9a-f]{64}', pre_hash):
                    archives[rel]['actual_before_hash'] = pre_hash
            for source in obj.get('report_proof', {}).get('sources', []):
                rel = source['path']
                if rel in archives:
                    source['snapshot_path'] = archives[rel]['path']
                elif not source.get('snapshot_path'):
                    # Pre-release R05 metadata-only receipts may be repaired only
                    # from bytes still matching the original source seal.
                    target = self.resolve(rel)
                    if not target.is_file() or _hash(target.read_bytes()) != source['sha256']:
                        raise CaseStoreError('cannot restore report source snapshot: ' + rel)
                    location = prefix + '/' + _hash(rel.encode())[:16] + Path(rel).suffix
                    historical.append((self.resolve(location), target.read_bytes()))
                    source['snapshot_path'] = location
                    changed = True
            proof = obj.get('report_proof') or {}
            old_guide = proof.get('path')
            if old_guide and not proof.get('kind') and re.fullmatch(r'\d{2}(?:_回执)?\.md', Path(old_guide).name):
                target = self.resolve(old_guide)
                if not target.is_file() or _hash(target.read_bytes()) != proof.get('sha256'):
                    raise CaseStoreError('legacy guide hash mismatch: ' + old_guide)
                guide = business_report_path(self.logical_root, meta.get('stage', 'S1'), meta.get('step', 1))
                historical.append((self.resolve(guide), target.read_bytes()))
                proof['path'] = guide
                obj['human_report'] = guide
                changed = True
            if decoded:
                migration_rows.append(('/business_files', before['business_files'], None, archives))
            data = obj.get('s1_stage_data')
            if isinstance(data, dict) and 'stage_model' in data:
                for rel, saved in data.get('preserved_reports', {}).items():
                    if 'bytes_base64' not in saved:
                        continue
                    raw = base64.b64decode(saved['bytes_base64'], validate=True)
                    if _hash(raw) != saved.get('sha256'):
                        raise CaseStoreError('preserved report hash mismatch: ' + rel)
                    history = '_history/S1/09/final/' + _relative(rel)
                    historical.append((self.resolve(history), raw))
                    saved.clear()
                    saved.update(path=history, sha256=_hash(raw), size=len(raw))
                relative = 'S1_选品板块/_阶段总览/s1_stage_report.json'
                raw = _json_bytes(data)
                historical.append((self.resolve(relative), raw))
                obj['s1_stage_data'] = {'external_path': relative, 'sha256': _hash(raw), 'size': len(raw)}
                changed = True
            if changed:
                decoded_rows.append((path, obj, decoded, archives, migration_rows))
            for rel, raw in decoded.items():
                self.resolve(rel)  # Reject external symlinks before any mutation.
                if rel not in latest or rank > latest[rel][0]:
                    latest[rel] = (rank, raw, (mapping.get(rel) or {}).get('actual_before_hash'))
        written, conflicts = [], []
        for target, raw in historical:
            _write(target, raw)
        for rel, (_, raw, pre_hash) in sorted(latest.items()):
            target = self.resolve(rel)
            if target.is_file():
                disk = target.read_bytes()
                # A disk file equal to the revision's recorded pre-revision state was never edited: the
                # sealed revision simply supersedes it (the same rule _index and record apply). Only a
                # third version, i.e. a real user edit, is a conflict that keeps the disk copy.
                if disk != raw and (pre_hash is None or _hash(disk) != pre_hash):
                    conflicts.append(rel)
            else:
                _write(target, raw)
                written.append(rel)
        for path, obj, decoded, archives, migration_rows in decoded_rows:
            for rel in decoded:
                raw = self.resolve(rel).read_bytes()
                # Older owning receipts retain their exact historical version.
                if raw != decoded[rel]:
                    obj['business_files'][rel] = archives[rel]
                    if rel in conflicts:
                        obj.setdefault('restored_disk_files', {})[rel] = {'path': rel, 'sha256': _hash(raw), 'size': len(raw)}
                else:
                    obj['business_files'][rel] = {'path': rel, 'sha256': _hash(raw), 'size': len(raw)}
            obj.setdefault('business_snapshot', {})['schema_version'] = 2
            for selector, old, current, archived in migration_rows:
                current = obj['business_files'] if current is None else current
                old_files = old if selector == '/business_files' else old['business_files']
                current_files = current if selector == '/business_files' else current['business_files']
                if any(current_files[rel]['sha256'] != item['sha256'] for rel, item in old_files.items()):
                    continue
                obj.setdefault('storage_migrations', []).append({
                    'schema_version': 1, 'selector': selector,
                    'before_sha256': _canonical_hash(old), 'after_sha256': _canonical_hash(current),
                    'original_envelope': None if selector == '/business_files' else
                        {key: value for key, value in old.items() if key != 'business_files'},
                    'files': {rel: {'path': archived[rel]['path'],
                                    'original_metadata': {key: value for key, value in item.items() if key != 'bytes_base64'}}
                              for rel, item in old_files.items()}})
            if conflicts:
                obj['restore_warnings'] = ['保留磁盘已有版本：' + rel for rel in conflicts if rel in decoded]
            readable = [rel for rel in obj.get('business_files', {}) if Path(rel).suffix.lower() in {'.md', '.html', '.xlsx', '.pdf'}
                        and not any(part in {'运行数据', '_history'} for part in Path(rel).parts) and self.resolve(rel).is_file()]
            readable.sort(key=lambda rel: (Path(rel).suffix.lower() != '.html', rel))
            obj['reports'] = [{'label': Path(rel).name, 'path': rel} for rel in readable]
            if obj.get('human_report') and self.resolve(obj['human_report']).is_file():
                guide = obj['human_report']
                if not any(row['path'] == guide for row in obj['reports']):
                    obj['reports'].append({'label': Path(guide).name, 'path': guide})
            _write(path, _json_bytes(obj))
        return {'written': written, 'conflicts': conflicts}

    def _max_sequence(self):
        return max((obj.get('business_snapshot', {}).get('case_sequence', 0) for _, obj in self._receipts()), default=0)

    def _active(self):
        for view in list(_SHARED.views.values()):
            if view.logical_root == self.logical_root:
                return view
        env = _env_view(self.root, allow_logical=True)
        return CaseView(*env) if env else None

    def read_bytes(self, path, *, decision_id=None, receipt_path=None):
        rel = self._rel(path)
        if decision_id is not None:
            if receipt_path is None:
                raise CaseStoreError('historical decision reads require an explicit receipt_path')
            obj = _json(self.resolve(receipt_path).read_bytes())
            snapshot = obj.get('business_decisions', {}).get(str(decision_id))
            if not isinstance(snapshot, dict):
                raise CaseStoreError(f'unknown business decision: {decision_id}')
            decoded = decode_business_files(snapshot.get('business_files', {}), self.logical_root)
            for source in snapshot.get('report_proof', {}).get('sources', []):
                if source.get('path') not in decoded or _hash(decoded[source['path']]) != source.get('sha256'):
                    raise CaseStoreError('historical decision report source hash mismatch')
            if rel not in decoded:
                raise FileNotFoundError(f'{rel} is not in decision {decision_id}')
            return decoded[rel]
        active = self._active()
        if active:
            return (active.root / rel).read_bytes()
        item = self._index().get(rel)
        return item[1] if item else self.resolve(rel).read_bytes()

    def read_object(self, path, *, decision_id=None, receipt_path=None):
        raw = self.read_bytes(path, decision_id=decision_id, receipt_path=receipt_path)
        return _json(raw) if str(path).lower().endswith('.json') else yaml.safe_load(raw.decode('utf-8-sig'))

    def human_report_for(self, path):
        """Map a sealed source to its verified real readable report, or None."""
        physical = self.resolve(path)
        if physical.is_file():
            return physical
        entry = self._index().get(self._rel(path))
        if entry is None:
            return None
        receipt = _json(entry[3].read_bytes())
        human = receipt.get('human_report')
        return self.resolve(human) if human else None

    def exists(self, path):
        rel = self._rel(path)
        active = self._active()
        if active:
            return (active.root / rel).exists()
        return rel in self._index() or self.resolve(rel).exists()

    def enabled(self):
        if self._receipts():
            return True
        def opted(obj):
            if isinstance(obj, dict):
                if isinstance(obj.get('s1_execution'), dict) and obj['s1_execution'].get('compact_case') is True:
                    return True
                return any(opted(v) for v in obj.values())
            return False
        for filename in ('workflow_state.yaml', 'workflow_state.json'):
            path = self.logical_root / filename
            if path.is_file():
                obj = _json(path.read_bytes()) if path.suffix == '.json' else yaml.safe_load(path.read_bytes())
                if opted(obj):
                    return True
        return False

    @contextmanager
    def materialize(self, overrides=None):
        overrides = {self._rel(k): bytes(v) for k, v in (overrides or {}).items()}
        blocked = sorted(path for path in overrides if not _business_path(path))
        if blocked:
            # R06.1 AS-04: say which path and where business data belongs.
            raise CaseStoreError('business overrides cannot replace control metadata: ' + ', '.join(blocked) +
                                 '。receipts/流程控制/紧凑业务报告 等目录只放插件自己的流程记录；MCP 原始回包请用 '
                                 'scripts/save_mcp_response.py 存到 S1_选品板块/amazon-market-research/运行数据/，'
                                 '其他业务文件放到对应技能目录（如 S1_选品板块/<技能>/）')
        key = str(self.logical_root).casefold()
        with _SHARED.guard:
            lock = _SHARED.locks.setdefault(key, threading.RLock())
        with lock:
            active = self._active()
            if active:
                for rel, raw in overrides.items():
                    _write(active.root / rel, raw)
                    active._overrides[rel] = raw
                yield active
                return
            base = (Path(tempfile.gettempdir()) / 'starsky-r01-views' / _hash(key.encode())[:24]).resolve()
            base.mkdir(parents=True, exist_ok=True)
            lockfile = (base / 'case.lock').open('a+b')
            try:
                if os.name == 'nt':
                    import msvcrt
                    if lockfile.tell() == 0:
                        lockfile.write(b'0'); lockfile.flush()
                    lockfile.seek(0)
                    try:
                        msvcrt.locking(lockfile.fileno(), msvcrt.LK_NBLCK, 1)
                    except OSError as exc:
                        raise CaseStoreError('case view is in use by another process') from exc
                else:
                    import fcntl
                    try:
                        fcntl.flock(lockfile.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except OSError as exc:
                        raise CaseStoreError('case view is in use by another process') from exc
                target = base / 'case'
                if target.exists():
                    shutil.rmtree(target)
                self.restore_legacy()
                index = self._index()
                original = _files(self.logical_root)
                virtual = dict(original)
                virtual.update({p: entry[1] for p, entry in index.items()})
                virtual.update(overrides)
                view = CaseView(target, self.logical_root, original, index, overrides)
                _SHARED.views[key] = view
                previous = {name: os.environ.get(name) for name in (_ENV_VIEW, _ENV_LOGICAL)}
                try:
                    target.mkdir(parents=True, exist_ok=True)
                    _write(target / '.r01-view.json', _json_bytes({'logical_root': str(self.logical_root), 'view_root': str(target)}))
                    for rel, raw in sorted(virtual.items()):
                        _write(target / rel, raw)
                    view._materialized_files = len(virtual)
                    os.environ[_ENV_VIEW], os.environ[_ENV_LOGICAL] = str(target), str(self.logical_root)
                    yield view
                finally:
                    for name, value in previous.items():
                        if value is None:
                            os.environ.pop(name, None)
                        else:
                            os.environ[name] = value
                    _SHARED.views.pop(key, None)
                    if target.exists():
                        shutil.rmtree(target)
            finally:
                lockfile.close()

    def prepare(self, view, step, report_paths=(), *, stage='S1', receipt_path=None,
                revision=None, metadata=None, receipt_fields=None, kind='final', decision_id=None, now=None):
        if view.logical_root != self.logical_root or not view.root.is_dir():
            raise CaseStoreError('prepare requires an active view of this case')
        if stage not in {'S1', 'S2', 'S3', 'S4', 'S5'} or int(step) < 1:
            raise CaseStoreError('invalid stage or step')
        rp = self.resolve(receipt_path or f'00_总控/starsky-fullchain-workflow/运行数据/流程控制/{stage}/receipts/{int(step):02}.json')
        if rp.parent.name != 'receipts':
            raise CaseStoreError('compact receipt_path must be inside a receipts directory')
        existing = _json(rp.read_bytes()) if rp.is_file() else {}
        prior = existing.get('business_snapshot', {})
        if kind not in ('final', 'decision') or (kind == 'decision' and not decision_id):
            raise CaseStoreError('a decision requires an explicit decision_id')
        fields = dict(receipt_fields or {})
        reserved = sorted(k for k in fields if k.startswith('business_') or k in RESERVED_RECEIPT_FIELDS)
        if reserved:
            # R06.1 PL-09: say which field and what to do instead.
            raise CaseStoreError('reserved receipt field cannot be supplied by a native producer: ' + ', '.join(reserved) +
                                 '。这些字段由插件在封存时自己生成（报告、校验、卡片、回执、阶段数据等），'
                                 '请从回执字段里删掉它们；报告文件放进产物目录，由本步原生生成器写入')
        current = _files(view.root)
        missing = set(view._packed) - set(current)
        if missing:
            raise CaseStoreError('sealed sources deleted by native operation: ' + ', '.join(sorted(missing)))
        files, media = {}, {}
        oldfiles = existing.get('business_files', {})
        # Keep this step's old records too: a second callback may only update one output.
        for rel, item in oldfiles.items():
            files[rel] = dict(item)
        for rel, raw in sorted(current.items()):
            if not _business_path(rel):
                continue
            if '_history' in Path(rel).parts:
                continue
            if Path(rel).suffix.casefold() in _MEDIA:
                if view._initial.get(rel) != raw:
                    media[rel] = raw
                continue
            packed = view._packed.get(rel)
            changed = rel in view._overrides or (packed[1] if packed else view._initial.get(rel)) != raw
            if not changed and rel not in oldfiles:
                continue
            actual = view._initial.get(rel)
            before = _hash(actual) if actual is not None else None
            files[rel] = {'bytes_base64': base64.b64encode(raw).decode('ascii'), 'sha256': _hash(raw),
                          'kind': 'report' if Path(rel).suffix.lower() == '.md' else 'data',
                          'actual_before_hash': before}
        sources = []
        source_snapshots = {}
        bodies = []
        if isinstance(report_paths, (str, Path)):
            report_paths = [report_paths]
        all_reports = [source['path'] for source in existing.get('report_proof', {}).get('sources', [])]
        for path in report_paths:
            rel = self._rel(path)
            if rel not in all_reports:
                all_reports.append(rel)
        for path in all_reports:
            rel = self._rel(path)
            raw = current.get(rel)
            if raw is None:
                raise CaseStoreError(f'native report missing: {rel}')
            try:
                body = _report_body(raw, rel, view)
            except UnicodeError as exc:
                raise CaseStoreError(f'native report is not UTF-8: {rel}') from exc
            if rel not in files:
                item = view._packed.get(rel)
                files[rel] = dict(item[2]) if item else {
                    'bytes_base64': base64.b64encode(raw).decode(), 'sha256': _hash(raw),
                    'kind': 'report', 'actual_before_hash': _hash(view._initial[rel]) if rel in view._initial else None}
            # Content-addressed new snapshots never overwrite the original
            # migration archive or a previous generated revision.
            snapshot_path = (f'_history/{stage}/{int(step):02}/final/' + _hash(rel.encode())[:16]
                             + '-' + _hash(raw)[:16] + Path(rel).suffix)
            source_snapshots[snapshot_path] = raw
            sources.append({'path': rel, 'sha256': _hash(raw), 'snapshot_path': snapshot_path})
            bodies.append(f'## {rel}\n\n' + body.rstrip('\r\n') + '\n')
        if not bodies:
            bodies = ['本步尚未提供可读业务报告。已封存原生输入与输出，业务结论待补齐。\n']
        report_rel = business_report_path(self.logical_root, stage, step)
        report = (f'# {stage} 第 {int(step)} 步业务报告\n\n' + '\n'.join(bodies)).encode('utf-8')
        proof = {'path': report_rel, 'sha256': _hash(report), 'sources': sources,
                 'projection_version': 1, 'source_view_root': str(view.root),
                 'source_logical_root': str(view.logical_root)}
        child_writes = []
        external_writes = list(view._external_temp_write_events)
        for journal in sorted(view.root.glob('.r01-io-*.json')):
            try:
                audit = _json(journal.read_bytes())
                child_writes.extend(audit['write_open_events'])
                external_writes.extend(audit.get('external_temp_write_events', []))
            except (KeyError, ValueError, OSError) as exc:
                raise CaseStoreError(f'cannot validate native IO journal: {journal}') from exc
        metrics = {'materialized_files': view._materialized_files, 'collected_files': len(files),
                   'view_files_after_native': len(current),
                   'framework_control_files': sorted(p for p in current if p.startswith('.r01-')),
                   'persistent_lock_file': str(view.root.parent / 'case.lock'),
                   'write_open_events': len(view._write_events), 'write_paths': sorted(set(view._write_events)),
                   'subprocess_write_open_events': len(child_writes), 'subprocess_write_paths': sorted(set(child_writes)),
                   'external_temp_write_open_events': len(external_writes), 'external_temp_write_paths': sorted(set(external_writes)),
                   'scope': 'Python audit open-write events in the case view and external temp paths for this process/run_native children; non-Python descendant OS writes are not measured. The stable lock contains no business data.'}
        obj = dict(existing)
        obj.update(fields)
        stamp = time.time() if now is None else float(now)
        execution = metadata or {}
        file_state = {rel: {'path': rel, 'sha256': item['sha256'],
                           'size': len(current[rel])} for rel, item in files.items()}
        proof['projection_version'] = 2
        changed = (file_state != existing.get('business_files') or proof != existing.get('report_proof')
                   or _json_bytes(execution) != _json_bytes(prior.get('execution', {}))
                   or any(existing.get(k) != v for k, v in fields.items()))
        previous_revision = int(prior.get('revision', 0))
        revision = int(revision if revision is not None else previous_revision + (1 if changed else 0))
        if revision < 1 or revision < previous_revision or (changed and revision <= previous_revision):
            raise CaseStoreError('ambiguous revision: an update requires a greater revision')
        window = prior.get('window_started_at', stamp)
        if changed and stamp - window >= 600:
            window = stamp
        sequence_before = self._max_sequence()
        sequence = sequence_before + 1 if changed else prior.get('case_sequence', sequence_before + 1)
        obj.update({'business_snapshot': {'schema_version': 1, 'stage': stage, 'step': int(step),
                                         'revision': revision, 'execution': execution,
                                         'case_sequence': sequence,
                                         'window_started_at': window},
                    'business_files': files, 'report_proof': proof, 'human_report': report_rel})
        if kind == 'decision':
            decisions = dict(existing.get('business_decisions', {}))
            snapshot = {k: obj[k] for k in ('business_snapshot', 'business_files', 'report_proof')}
            old = decisions.get(str(decision_id))
            if old and (old['business_files'] != files or old['report_proof'] != proof):
                raise CaseStoreError('a named user decision cannot be overwritten')
            decisions[str(decision_id)] = old or snapshot
            obj['business_decisions'] = decisions
        # R05: persist original bytes, keep only source metadata in receipts.
        physical = decode_business_files(files)
        for rel, item in files.items():
            if rel not in physical:
                physical[rel] = current[rel]
        obj['business_files'] = {rel: {'path': rel, 'sha256': _hash(raw), 'size': len(raw)} for rel, raw in physical.items()}
        obj.pop('restored_disk_files', None)  # This revision explicitly seals the current physical versions.
        obj['business_snapshot']['schema_version'] = 2
        proof['projection_version'] = 2
        readable = [rel for rel in physical if Path(rel).suffix.lower() in {'.md', '.html', '.xlsx', '.pdf'}
                    and not any(part in {'运行数据', '_history'} for part in Path(rel).parts)]
        readable.sort(key=lambda rel: (Path(rel).suffix.lower() != '.html', rel))
        obj['reports'] = [{'label': Path(rel).name, 'path': rel} for rel in readable]
        obj['reports'].append({'label': Path(report_rel).name, 'path': report_rel})
        outputs = {**physical, **source_snapshots, report_rel: report}
        if kind == 'decision':
            prefix = f'_history/{stage}/{int(step):02}/d-{_hash(_relative(str(decision_id)).encode())[:12]}'
            snapshot = {k: deepcopy(obj[k]) for k in ('business_snapshot', 'business_files', 'report_proof')}
            for rel, raw in physical.items():
                location = prefix + '/' + _hash(rel.encode())[:16] + Path(rel).suffix
                target = self.resolve(location)
                if target.exists() and target.read_bytes() != raw:
                    raise CaseStoreError('a named user decision cannot be overwritten')
                outputs[location] = raw
                snapshot['business_files'][rel]['path'] = location
            obj.setdefault('business_decisions', {})[str(decision_id)] = snapshot
        outputs[rp.relative_to(self.logical_root).as_posix()] = _json_bytes(obj)
        return PreparedStep(self.logical_root, stage, int(step), rp, self.resolve(report_rel),
                            obj, outputs, media, dict(view._initial), metrics,
                            rp.read_bytes() if rp.is_file() else None, sequence_before)

    collect = prepare

    def commit_step(self, prepared):
        key = str(self.logical_root).casefold()
        with _SHARED.guard:
            lock = _SHARED.locks.setdefault(key, threading.RLock())
        with lock:
            if self._active():
                return self._commit_step(prepared)
            with _detached_commit_lock(self.logical_root):
                return self._commit_step(prepared)

    def _commit_step(self, prepared):
        if prepared.logical_root != self.logical_root:
            raise CaseStoreError('prepared step belongs to another case')
        receipt_now = prepared.receipt_path.read_bytes() if prepared.receipt_path.is_file() else None
        receipt_rel = prepared.receipt_path.relative_to(self.logical_root).as_posix()
        if receipt_now not in (prepared.receipt_before, prepared.outputs[receipt_rel]):
            raise CaseStoreError('receipt changed concurrently: receipt source conflict')
        if receipt_now != prepared.outputs[receipt_rel] and self._max_sequence() != prepared.case_sequence_before:
            raise CaseStoreError('case sequence source conflict: another step committed after prepare')
        # Recheck exact physical baseline before any mutation. A view must not hide
        # concurrent user edits or an accidental native write to the real root.
        for rel in prepared.receipt['business_files']:
            raw = prepared.outputs[rel]
            path = self.resolve(rel)
            now = path.read_bytes() if path.is_file() else None
            before = prepared.before.get(rel)
            if now != before and now != raw:
                raise CaseStoreError(f'physical source conflict before commit: {rel}')
        persistent, media_writes = [], []
        writes = dict(prepared.outputs)
        writes.update(prepared.media)
        before = {rel: self.resolve(rel).read_bytes() if self.resolve(rel).is_file() else None for rel in writes}
        for rel, raw in writes.items():
            if rel == receipt_rel:
                continue  # Receipt has the stricter prepare-time comparison above.
            if before[rel] not in (prepared.before.get(rel), raw):
                raise CaseStoreError(f'physical output source conflict before commit: {rel}')
        attempted = []
        try:
            for rel, raw in sorted(writes.items()):
                path = self.resolve(rel)
                if before[rel] == raw:
                    continue
                attempted.append(rel)
                if _write(path, raw):
                    (media_writes if rel in prepared.media else persistent).append(rel if rel in prepared.media else str(path))
        except BaseException:
            for rel in reversed(attempted):
                path = self.resolve(rel)
                if before[rel] is None:
                    path.unlink(missing_ok=True)
                else:
                    _write(path, before[rel])
            raise
        return {'report_md': str(prepared.report_path), 'receipt_path': str(prepared.receipt_path),
                'persistent_writes': persistent, 'media_writes': media_writes,
                'temporary_io': prepared.temporary_io, 'receipt': prepared.receipt}

    def export_file(self, path, destination):
        destination = Path(destination).resolve()
        _write(destination, self.read_bytes(path))
        return destination


def prepare_step(view, step, report_paths=(), **kwargs):
    return view.collect(step, report_paths, **kwargs)


def export_file(root, path, destination):
    return CompactCase(root).export_file(path, destination)
