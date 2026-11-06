"""原生 kb_status 回读：分别核对知识库数据与正在运行的服务代码。

  python scripts/kb_refresh.py check --response-file <原生回包.json>
  python scripts/kb_refresh.py processes

只读，不修改安装、指针或进程。必须传当前聊天自己的原生调用回包，独立连接
成功不能证明当前聊天已刷新。旧版只返回 kb 路径时仍可核对数据版本，但代码
版本标记 unknown；数据跟随成功不等于服务代码升级。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

PLUGIN = 'starsky-amazon-agent'
VERSION_IN_PATH = re.compile(r'/marketplace/versions/([^/]+)/plugins/')  # 兼容旧导入方
IDENTITY = re.compile(r'^(.*)/marketplace/versions/([^/]+)/plugins/starsky-amazon-agent/mcp/'
                      r'(selection|ads|promotion)-kb/(kb|server\.py)$')
PROCESS_PATH = re.compile(r'(?P<root>(?:[A-Za-z]:)?/.*?)/marketplace/versions/(?P<version>[^/\s]+)/plugins/'
                          r'starsky-amazon-agent/mcp/(?P<kind>selection|ads|promotion)-kb/server\.py')

RECOVERY = (
    '恢复步骤（按从轻到重）：',
    '1. 在宿主里新开一个聊天尝试重连，再原生调用 kb_status 回读；宿主可能复用旧进程，新聊天不保证加载新代码。',
    '2. 仍不行：在其他聊天任务结束后，由用户完全退出并重开宿主，再回读 kb_status 核对数据版本和代码版本。',
    '3. 若宿主提供「重新加载 MCP 服务」，可由用户操作；它可能影响其他聊天，生效时机不保证，必须再次原生回读。',
    '只有已加载跟随逻辑的进程才会切换数据 Store；它不会热更新服务代码。升级前已启动且没有该逻辑的旧进程需要重新连接。',
)


def install_root(host='codex'):
    return Path(os.environ.get('STARSKY_HOME') or (Path.home() / ('.starsky-' + host))).expanduser()


def _version(value):
    return (isinstance(value, str) and bool(value.strip()) and value == value.strip()
            and value not in ('.', '..') and not any(c in value for c in '/\\\x00'))


def _installation(root=None, host='codex'):
    result = {'version': None, 'source': None, 'verified': False, 'error': None}
    try:
        home = Path(root or install_root(host)).resolve()
        raw = (home / 'current.json').read_bytes()
        state = json.loads(raw)
        if not isinstance(state, dict) or not _version(state.get('plugin_version')):
            raise ValueError('current.json 缺少有效的插件版本')
        result['version'] = state['plugin_version']
        if 'source' not in state:
            # 老版只存 plugin_version：保留版本比较，但不能宣称安装路径已核实。
            return result
        if not isinstance(state['source'], str) or not state['source']:
            raise ValueError('current.json 缺少有效安装路径')
        source = Path(state['source'])
        expected = home / 'marketplace/versions' / result['version'] / 'plugins' / PLUGIN
        if not source.is_absolute() or source.resolve() != expected or expected.resolve() != expected:
            raise ValueError('current.json 的实际安装路径与声明版本不一致')
        manifest_path = expected / '.codex-plugin/plugin.json'
        if manifest_path.resolve() != manifest_path:
            raise ValueError('插件清单实际路径与版本目录不一致')
        manifest = json.loads(manifest_path.read_bytes())
        if (not isinstance(manifest, dict) or manifest.get('name') != PLUGIN
                or manifest.get('version') != result['version']):
            raise ValueError('安装清单与 current.json 不一致')
        if (home / 'current.json').read_bytes() != raw:
            raise ValueError('回读时安装指针发生变化，请重试')
        result.update(source=str(expected), verified=True)
    except (OSError, ValueError, TypeError, RuntimeError) as error:
        result['error'] = str(error)[:300]
    return result


def installed_version(root=None, host='codex'):
    """兼容旧接口；指针/清单不可信时返回 None 和原因。"""
    info = _installation(root, host)
    return (None if info['error'] else info['version']), info['error']


def _payload(response, depth=0):
    """支持原始正文、MCP text/structuredContent、JSON-RPC；矛盾/错误回包拒绝。"""
    if depth > 8:
        return None
    if isinstance(response, str):
        try:
            response = json.loads(response)
        except ValueError:
            return None
    if not isinstance(response, dict) or response.get('isError') or response.get('error'):
        return None
    if 'kb' in response:
        return response
    if 'result' in response:
        return _payload(response['result'], depth + 1)
    candidates = []
    if 'structuredContent' in response:
        inner = _payload(response['structuredContent'], depth + 1)
        if inner is not None:
            candidates.append(inner)
    if isinstance(response.get('content'), list):
        for part in response['content']:
            if isinstance(part, dict) and part.get('type') == 'text':
                inner = _payload(part.get('text'), depth + 1)
                if inner is not None:
                    candidates.append(inner)
    if not candidates or any(item != candidates[0] for item in candidates[1:]):
        return None
    return candidates[0]


def _identity(path, leaf):
    if not isinstance(path, str) or '\x00' in path:
        return None
    normalized = path.replace('\\', '/').rstrip('/')
    if any(part in ('.', '..') for part in normalized.split('/')):
        return None
    match = IDENTITY.fullmatch(normalized)
    if not match or match[4] != leaf or not _version(match[2]):
        return None
    if not (normalized.startswith('/') or re.match(r'^[A-Za-z]:/', normalized)):
        return None
    return {'root': match[1], 'version': match[2], 'kind': match[3] + '-kb', 'path': normalized}


def _same_path(a, b):
    # 本机已有路径须按实际目标核对，避免版本目录软链产生假阳性。
    try:
        return isinstance(a, (str, Path)) and isinstance(b, (str, Path)) and Path(a).resolve() == Path(b).resolve()
    except (OSError, ValueError, RuntimeError):
        return False


def check_kb_status(response, *, root=None, host='codex'):
    """state 保留 current/stale/unknown；额外分开 data_state/code_state。

    旧回包仅有数据证据时 current 的 verification_scope 为 knowledge_data_only，
    fully_current 始终 False；新回包明确代码旧时整体 stale，即使数据已是 current。
    """
    body = _payload(response)
    installation = _installation(root, host)
    installed = installation['version']
    out = {'state': 'unknown', 'served': None, 'installed': installed,
           'data_state': 'unknown', 'code_state': 'unknown', 'code_version': None,
           'fully_current': False, 'installation_verified': installation['verified'],
           'verification_scope': 'knowledge_data_only'}

    def unknown(message):
        return {**out, 'state': 'unknown', 'message': message, 'recovery': list(RECOVERY)}

    if body is None:
        return unknown('没有一致、成功的原生 kb_status 回包，无法确认版本；请在当前聊天重新原生调用。')
    identity = _identity(body.get('kb'), 'kb')
    if not identity:
        return unknown('知识库回包里读不出完整的安装版本路径，无法确认数据版本。')
    served = identity['version']
    out['served'] = served
    runtime = body.get('runtime')
    if runtime is not None and not isinstance(runtime, dict):
        return unknown('runtime 结构无效，无法核对版本。')
    runtime = runtime or {}
    for key in ('serving_version', 'data_version'):
        if runtime.get(key) is not None and runtime[key] != served:
            return unknown('知识库实际路径与 runtime 声明的数据版本不一致，不能判定已刷新。')
    if runtime.get('data_path') is not None and not _same_path(runtime['data_path'], identity['path']):
        return unknown('kb 与 runtime.data_path 不一致，不能判定已刷新。')
    if not installed:
        return unknown(f'读不到已安装版本（{installation["error"]}），无法确认回读版本。')
    # 对完整安装指针，必须属于同一安装根和同一知识库；旧版无 source 保留版本比较。
    if installation['source']:
        expected_kb = Path(installation['source']).parents[4] / 'marketplace/versions' / served / 'plugins' / PLUGIN / 'mcp' / identity['kind'] / 'kb'
        if not _same_path(identity['path'], expected_kb):
            return unknown('原生回包的知识库不属于此次核对的安装目录，不能只按版本号判定已刷新。')
        try:
            actual_identity = _identity(str(Path(identity['path']).resolve()), 'kb')
            if actual_identity != identity or not Path(identity['path']).is_dir():
                return unknown('知识库路径的实际目标与回包声明不一致，或资料目录已不存在。')
        except (OSError, ValueError, RuntimeError):
            return unknown('无法核实知识库路径的实际目标。')
    out['data_state'] = 'current' if served == installed else 'stale'
    code = _identity(runtime.get('code_path'), 'server.py')
    if runtime.get('code_version') is not None or runtime.get('code_path') is not None:
        if (not code or code['version'] != runtime.get('code_version') or code['kind'] != identity['kind']
                or not _same_path(code['root'], identity['root'])):
            return unknown('服务代码路径与代码版本声明不一致，不能确认代码已升级。')
        if installation['verified']:
            try:
                if (_identity(str(Path(code['path']).resolve()), 'server.py') != code
                        or not Path(code['path']).is_file()):
                    return unknown('服务代码路径实际目标与声明不一致，或代码文件已不存在。')
            except (OSError, ValueError, RuntimeError):
                return unknown('无法核实服务代码路径的实际目标。')
        out.update(code_version=code['version'], code_state='current' if code['version'] == installed else 'stale',
                   verification_scope='knowledge_data_and_code')
    if out['data_state'] == 'stale' or out['code_state'] == 'stale':
        out['state'] = 'stale'
        if out['data_state'] == 'stale':
            message = (f'当前聊天知识库数据版本 {served} 与已安装声明版本 {installed} 不同（可能仍是旧版 {served}，也可能发生回滚）。'
                       '这是版本不一致，不是调用失败；独立直连读到新版不能证明本聊天已刷新。')
        else:
            message = (f'知识库数据已跟随 {installed}，但服务代码仍是 {out["code_version"]}；'
                       '只切换了数据 Store，服务代码尚未升级，需要重连后原生回读。')
        if installation['error']:
            message += ' 安装指针尚未通过校验：' + installation['error']
        return {**out, 'message': message, 'recovery': list(RECOVERY)}
    if installation['error'] or runtime.get('follow') in ('unverified', 'behind'):
        return unknown('数据版本号相同，但安装指针或跟随校验未通过：' + str(installation['error'] or runtime.get('error') or '需重新核对'))
    out['state'] = 'current'
    out['fully_current'] = out['code_state'] == 'current' and installation['verified']
    if out['fully_current']:
        out['message'] = f'当前聊天原生回包的数据和服务代码版本均为已安装版本 {installed}。'
    else:
        out['message'] = f'原生回包的数据版本与已安装声明版本 {installed} 相同；旧版回包或安装记录不足以确认服务代码已升级。'
        out['recovery'] = list(RECOVERY)
    return out


def kb_processes():
    """只列进程启动代码路径；ps 无法看到进程内已切换的 Store。"""
    installed, _ = installed_version()
    try:
        proc = subprocess.run(['ps', '-axo', 'pid=,ppid=,lstart=,command='], capture_output=True, text=True, timeout=15)
        if proc.returncode:
            return None
    except (OSError, subprocess.SubprocessError):
        return None
    rows = []
    for line in proc.stdout.splitlines():
        head = line.split(None, 7)
        if len(head) != 8 or not head[0].isdigit() or not head[1].isdigit():
            continue
        # 命令行中解释器和脚本间有空格；root 可以含空格，因此从绝对路径逐个尝试。
        command = head[7].replace('\\', '/')
        matches = list(re.finditer(r'(?:^|[\s\"\'])([A-Za-z]:/|/)', command))
        match = None
        for start in reversed(matches):
            candidate = PROCESS_PATH.match(command, start.start(1))
            if candidate:
                match = candidate
                break
        if not match:
            continue
        process_installed, _ = installed_version(root=match['root'])
        rows.append({'pid': int(head[0]), 'ppid': int(head[1]), 'started': ' '.join(head[2:7]),
                     'kb': match['kind'] + '-kb', 'version': match['version'], 'code_version': match['version'],
                     'installed': process_installed, 'stale': None if not process_installed else match['version'] != process_installed,
                     'data_version': None})
    return {'installed': installed, 'processes': rows,
            'note': '只列进程启动代码版本，不会结束任何进程；无法从进程命令行判断当前数据 Store。每个聊天须用自己的原生 kb_status 回读。'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    check = sub.add_parser('check')
    check.add_argument('--response-file', required=True)
    check.add_argument('--host', default='codex')
    sub.add_parser('processes')
    options = parser.parse_args(argv)
    if options.command == 'processes':
        result = kb_processes()
        print(json.dumps(result if result is not None else {'error': '读不到进程列表'}, ensure_ascii=False, indent=2))
        return 0 if result is not None else 1
    try:
        raw = sys.stdin.read() if options.response_file == '-' else Path(options.response_file).read_text(encoding='utf-8-sig')
    except (OSError, UnicodeError) as error:
        print(json.dumps({'state': 'unknown', 'message': f'读不到回包文件：{error}'}, ensure_ascii=False))
        return 1
    result = check_kb_status(raw, host=options.host)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['state'] == 'current' else 2


if __name__ == '__main__':
    sys.exit(main())
