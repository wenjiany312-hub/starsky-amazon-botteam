"""给已登记的知识库证据追加「补记」（R10）：只追加，不改原件、不改回执、不改流程状态。

用在这种情况：本步知识库调用其实有返回，但自动归档当时没有读到（或回读到的是旧版库），登记被降级成
“本步没有调用 / 调用失败”。原登记要保留（它是当时的真实记录，哈希已进回执），不能改写，更不能把旧版调用改称新版；
可以做的是在同一文件夹追加一份带修订号的补记，写清：

  - 原登记和原始回包文件的 SHA-256（证明原件没被动过）；
  - 对话记录里实际看到的调用：证据级别（host_native / code_mode_passthrough）、调用号、脚本哈希、回包哈希；
  - 每次调用当时连着哪个版本的库（取同一聊天里 kb_status 的回读）与已安装版本对比；
  - 修正后的分类：已调用但版本过旧 / 已调用但回包无法核验 / 对话记录里确实没有调用；
  - usable_as_basis：旧版调用不得反过来当作新版依据；补记也不改变本步状态，要把它当依据必须走正式的重新登记。

    python scripts/kb_amend.py --root "<产品根>" --stage S1 --step 1 --note "为什么补记" [--dry-run]

回包副本写在 kb_amendment_<NN>_responses.json（只存对话记录里能证明归属的回包）。补记文件用独占创建，已有的修订号不会被覆盖。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
HOME = HERE.parent
sys.path.insert(0, str(HERE))
import kb_plan  # noqa: E402
import kb_refresh  # noqa: E402
import step_evidence_window as evidence_windows  # noqa: E402

try:
    import host_support as hosts
except ImportError:  # source tree: the installer module is shared with the controllers
    _spec = importlib.util.spec_from_file_location('starsky_host_support', HOME.parents[1] / 'installer/host_support.py')
    hosts = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(hosts)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _iso(ns):
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).isoformat(timespec='seconds') if ns else None


def _receipt_ns(root, stage, step):
    return evidence_windows.sealed_window(root, stage, step)['until_ns']


def revisions(folder):
    return sorted((p for p in Path(folder).glob('kb_amendment_*.yaml') if p.stem.rsplit('_', 1)[-1].isdigit()),
                  key=lambda p: int(p.stem.rsplit('_', 1)[-1]))


def _next_revision(folder):
    # Interrupted writes may leave a response sidecar without its YAML. Keep it
    # as evidence and move past its number rather than getting stuck on retry.
    numbers = []
    for path in Path(folder).glob('kb_amendment_*'):
        suffix = path.name.removeprefix('kb_amendment_')
        for ending in ('.yaml', '_responses.json'):
            if suffix.endswith(ending) and suffix[:-len(ending)].isdigit():
                numbers.append(int(suffix[:-len(ending)]))
    return max(numbers, default=0) + 1


def _write_revision(folder, root, document, responses):
    text = json.dumps(responses, ensure_ascii=False, indent=2) + '\n'
    number = _next_revision(folder)
    while True:
        side = folder / f'kb_amendment_{number:02}_responses.json'
        path = folder / f'kb_amendment_{number:02}.yaml'
        # Exclusive creation reserves the revision even between processes.
        try:
            stream = side.open('x', encoding='utf-8', newline='\n')
        except FileExistsError:
            number += 1
            continue
        owned = [side]
        try:
            with stream:
                stream.write(text)
            document.update(revision=number, responses_file=side.relative_to(root).as_posix(),
                            responses_sha256=_sha(side))
            encoded = yaml.safe_dump(document, allow_unicode=True, sort_keys=False)
            output = path.open('x', encoding='utf-8', newline='\n')
            owned.append(path)
            with output:
                output.write(encoded)
        except BaseException:
            # Only remove files this invocation created, never another writer's
            # revision. A hard process exit can leave files; retries skip them.
            for created in reversed(owned):
                created.unlink(missing_ok=True)
            raise
        return number, path


def latest(root, home, stage, step):
    """本步最新一份补记（已解析），没有返回 None。"""
    plan = kb_plan.plan_step(root, home, stage, step)
    files = revisions(kb_plan.inside(root, plan['evidence_path']).parent)
    if not files:
        return None
    document = yaml.safe_load(files[-1].read_text(encoding='utf-8')) or {}
    if document.get('step') != f'{stage}.{step}':
        raise ValueError('知识库补记的步骤与当前步骤不符')
    original = document.get('original') or {}
    for key in ('evidence', 'responses'):
        relative = original.get(key + '_path')
        if relative and _sha(kb_plan.inside(root, relative)) != original.get(key + '_sha256'):
            raise ValueError('补记引用的原始证据 Hash 不符；保留原件并检查修订历史')
    side = kb_plan.inside(root, document.get('responses_file') or '')
    if not side.is_file() or _sha(side) != document.get('responses_sha256'):
        raise ValueError('知识库补记回包 Hash 不符；不能将该补记作为展示依据')
    return document


def amend(root, home, stage, step, note, *, scan=None, now=None, dry_run=False, host=None):
    from capture_mcp import _evidence, _failed, _kb_server
    host = host or ('claude' if hosts.current_host() == 'claude-code' else 'codex')
    root = Path(root).resolve()
    if not str(note or '').strip():
        raise ValueError('补记要写明原因（--note）')
    plan = kb_plan.plan_step(root, home, stage, step)
    folder = kb_plan.inside(root, plan['evidence_path']).parent
    evidence_path, raw_path = folder / 'kb_evidence.yaml', folder / 'mcp_responses.json'
    if not evidence_path.is_file():
        raise ValueError(f'{stage}.{step} 还没有知识库登记，没有需要补记的内容')
    original = yaml.safe_load(evidence_path.read_text(encoding='utf-8')) or {}
    rows = [r for r in original.get('calls') or []]
    if rows and all(r.get('status') == 'ok' for r in rows):
        raise ValueError('本步登记里的调用都已成功入档，不需要补记')
    window = evidence_windows.sealed_window(root, stage, step)
    since, until = window['since_ns'], window['until_ns']
    if scan is None:
        scan = hosts.mcp_scan(root, since)
        if scan['error']:
            raise ValueError('读取本对话记录失败（不是 MCP 调用失败）：' + scan['error'])
        if not scan['session']:
            raise ValueError('读不到本对话的原生记录，无法补记')
    inside = lambda call: evidence_windows.contains(window, call)
    seen = [c for c in scan['calls'] if inside(c)]
    unverified = [u for u in [*scan['unattributed'], *scan.get('pending', [])] if inside(u)]
    unsupported = [u for u in scan.get('unsupported', []) if inside(u)]
    installed, _ = kb_refresh.installed_version(host=host)
    status_reads = sorted(({'server': c['server'], 'time': c['time_ns'] or 0, 'check': kb_refresh.check_kb_status(c['response'], host=host)}
                           for c in seen if c['tool'] == 'kb_status' and not _failed(c)), key=lambda r: r['time'])
    corrections, responses, used = [], {}, set()
    for index, planned in enumerate(plan['calls']):
        row = next((r for r in rows if _kb_server(r.get('server')) == _kb_server(planned['server'])
                    and r.get('tool') == planned['tool'] and r.get('args') == planned['args']), {})
        if row.get('status') == 'ok':
            continue
        same = [i for i, c in enumerate(seen) if i not in used and _kb_server(c['server']) == _kb_server(planned['server']) and c['tool'] == planned['tool']]
        pick = ([i for i in same if seen[i]['args'] == planned['args']] or [None])[-1]
        entry = {'tool': planned['tool'], 'args': planned['args'], 'original': {'status': row.get('status'), 'error': row.get('error')}}
        if pick is not None:
            used.add(pick)
            call = seen[pick]
            before = [r for r in status_reads if _kb_server(r['server']) == _kb_server(call['server']) and r['time'] <= (call.get('time_ns') or 0)]
            read = before[-1]['check'] if before else None
            served = (read or {}).get('served')
            stale = bool(read and read['state'] == 'stale')
            entry['corrected'] = {
                'status': 'called_stale_version' if stale else 'called_reply_archived_late',
                'served_version': served, 'installed_version': installed,
                'usable_as_basis': False,
                'why': (f'这次调用连着旧版知识库 {served}，已安装 {installed}；旧版调用不能当作新版依据，也不能改写成新版调用。' if stale else
                        '回包现在能从对话记录里核验归属，但本步当时按降级处理；补记不改变本步状态，要引用须走正式重新登记。')}
            if _failed(call):
                entry['corrected'].update(status='called_error', why='宿主记录中存在完整错误回包；已保留原错误，不能作为成功调用依据。')
            entry['evidence'] = _evidence(call)
            text = call['response'] if isinstance(call['response'], str) else json.dumps(call['response'], ensure_ascii=False)
            entry['response_sha256'] = hashlib.sha256(text.encode('utf-8')).hexdigest()
            responses[f'call_{index}'] = call['response']
            entry['response_ref'] = f'#/call_{index}'
        else:
            seen_unverified = [u for u in unverified if _kb_server(u['server']) == _kb_server(planned['server']) and u['tool'] == planned['tool']
                               and (u.get('args') is None or u['args'] == planned['args'])]
            entry['corrected'] = ({'status': 'called_reply_unverifiable', 'reason': seen_unverified[-1]['reason'], 'usable_as_basis': False,
                                   'why': '对话记录里看到了这次调用，但回包无法证明是调用结果本身。'}
                                  if seen_unverified else
                                  {'status': 'not_called_in_record', 'usable_as_basis': False, 'why': '对话记录里本步窗口内没有看到可匹配调用；这不证明外部连接或未记录的调用从未发生。'})
            if seen_unverified and seen_unverified[-1]['reason'] == 'no_result_yet':
                entry['corrected'].update(status='pending', why='已发起调用但还没有完整回包，等待完成后再核验。')
            elif not seen_unverified and unsupported:
                entry['corrected'].update(status='record_unsupported', why='窗口内存在无法解析的调用记录，无法据此断言本项未调用。')
        corrections.append(entry)
    if not corrections:
        raise ValueError('没有需要补记的调用')
    number = _next_revision(folder)
    stale = [c for c in corrections if c['corrected']['status'] == 'called_stale_version']
    summary = (f'{len(corrections)} 项登记需要更正说明：' + '；'.join(f"{c['tool']}→{c['corrected']['status']}" for c in corrections) +
               ('。其中调用有返回，但连着的是旧版知识库，不能作为新版依据。' if stale else '。'))
    document = {
        'schema_version': 1, 'kind': 'kb_evidence_amendment', 'revision': number, 'step': f'{stage}.{step}',
        'created_at': (now or datetime.now(timezone.utc)).isoformat(timespec='seconds'), 'note': str(note).strip(),
        'original': {'evidence_path': evidence_path.relative_to(root).as_posix(), 'evidence_sha256': _sha(evidence_path),
                     'responses_path': raw_path.relative_to(root).as_posix() if raw_path.is_file() else None,
                     'responses_sha256': _sha(raw_path) if raw_path.is_file() else None,
                     'untouched': '原登记与原始回包保持原样，本补记只追加'},
        'window': {**window, 'since': _iso(since), 'until': _iso(until)}, 'installed_version': installed,
        'corrections': corrections, 'summary': summary}
    if dry_run:
        return {'dry_run': True, 'revision': number, 'document': document}
    number, path = _write_revision(folder, root, document, responses)
    return {'dry_run': False, 'revision': number, 'path': path.relative_to(root).as_posix(), 'summary': summary}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--stage', default='S1', choices=['S1', 'S2', 'S3', 'S4', 'S5'])
    parser.add_argument('--step', required=True, type=int)
    parser.add_argument('--note', required=True)
    parser.add_argument('--dry-run', action='store_true')
    options = parser.parse_args(argv)
    try:
        result = amend(options.root, HOME, options.stage, options.step, options.note, dry_run=options.dry_run)
    except (OSError, ValueError, KeyError, yaml.YAMLError, FileExistsError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps({'ok': True, **result}, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == '__main__':
    sys.exit(main())
