"""Fail closed when installed entry, source and current record disagree."""
from __future__ import annotations
import json
import os
from pathlib import Path
MIN_SANDBOX_SCHEMA = 2


def _read(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def plugin_identity(home):
    for host in ('claude', 'codex'):
        manifest = _read(Path(home) / ('.' + host + '-plugin') / 'plugin.json')
        if manifest.get('version'):
            return {'host': host, 'plugin_version': manifest['version']}
    return {}


def install_record(home):
    host = plugin_identity(home).get('host') or ('claude' if 'claude' in str(home).lower() else 'codex')
    return Path(os.environ.get('STARSKY_HOME', str(Path.home() / ('.starsky-' + host)))) / 'current.json'


def upgrade_message(expected, current):
    return (f'星空已更新到 {expected}，但本机还没完成解锁，当前仍是 {current}。'
            '请用支持权限申请的模型重新说『启动星空』并允许，或运行安装包里的『安装与更新』。')


def _installed(home):
    # Layout distinguishes installed copies from inspectable source/test trees.
    # No environment variable can bypass the installed identity gate.
    for path in (Path(home), Path(home).resolve()):
        parts = tuple(p.lower() for p in path.parts)
        if ('plugins' in parts and 'cache' in parts) or ('marketplace' in parts and 'versions' in parts):
            return True
    return False


def stale_plugin(home):
    home = Path(home).resolve()
    if not _installed(home):
        return None
    identity = plugin_identity(home)
    record_path = install_record(home)
    record = _read(record_path)
    expected = identity.get('plugin_version') or '未知版本'
    current = record.get('plugin_version') or record.get('version') or '未安装'
    blocked = upgrade_message(expected, current)
    if not identity or current != expected:
        return blocked
    receipt = _read(record_path.parent / 'runtime/sandbox-ready.json')
    schema = receipt.get('schema')
    if not isinstance(schema, int) or isinstance(schema, bool) or schema < MIN_SANDBOX_SCHEMA:
        return blocked
    if receipt.get('plugin_version') not in (None, expected):
        return blocked
    try:
        source, loaded = (Path(record[key]).resolve(strict=True) for key in ('source', 'home'))
        if home not in (source, loaded):
            return blocked
        if any(plugin_identity(path) != identity for path in (source, loaded)):
            return blocked
    except (OSError, ValueError, TypeError, KeyError):
        return blocked
    return None


def guard_plugin(home):
    message = stale_plugin(home)
    if message:
        raise ValueError(message)


def bind_workflow_identity(home, state):
    guard_plugin(home)
    identity = plugin_identity(home)
    if not identity:
        return ''
    previous = state.get('plugin_identity') or {}
    old_version = previous.get('plugin_version') or previous.get('version')
    new_version = identity['plugin_version']
    notice = ''
    if old_version and old_version != new_version:
        state.setdefault('plugin_identity_history', []).append(dict(previous))
        notice = f'星空执行版本已从 {old_version} 更新到 {new_version}；保留原产品进度和历史报告，后续步骤使用当前版本规则。'
        state['plugin_version_notice'] = notice
    state['plugin_identity'] = identity
    return notice


def current_copy(home, relative):
    """Compatibility lookup only; flows reject old roots instead of dispatching."""
    if not stale_plugin(home):
        return None
    record = _read(install_record(home))
    target = Path(str(record.get('home') or '')) / relative
    return target if target.is_file() else None


def main(argv=None):
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home',type=Path,default=Path(__file__).resolve().parents[1])
    args=parser.parse_args(argv)
    try:
        guard_plugin(Path(__file__).resolve().parents[1])
        guard_plugin(args.home)
    except ValueError as error:
        print(json.dumps({'status':'blocked','message':str(error)},ensure_ascii=False))
        return 2
    # Compatibility diagnostics belong to the installer's explicit check action.
    # Startup and every step preflight report only the active plugin identity.
    print(json.dumps({'status':'ready',**plugin_identity(args.home),'preserved_host_entries':[],
                     'host_entry_notice':''},ensure_ascii=False))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
