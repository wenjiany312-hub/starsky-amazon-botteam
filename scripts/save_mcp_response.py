"""把一次 MCP 工具回包按 S1 约定存好（R06.1 AS-02）。

S1 验收只认真实回包：数量、来源和参数都从 `S1_选品板块/amazon-market-research/运行数据/` 里读。
3.0.22 的案例里助手自拟文件名、把变体回包只放在 Node 暂存里，验收于是看不到已经取到的数据。
这个小工具让“存回包”只有一种写法：

    python scripts/save_mcp_response.py --root "<产品根>" --tool sorftime__product_variations \
        --args '{"asin":"B0XXXXXXX","amz_site":"US"}' --response-file 回包.json
    （回包也可以从标准输入传入：--response-file -）

同一工具的所有调用存进 `mcp_calls__<工具名>.json`：{"calls": {"<键>": {"tool", "args", "response", "saved_at", "sha256"}}}。
键由工具名和参数决定，同一查询的最新结果写入 calls，不重复计数；被替换的不同回包或证据保留在 history。回包原样保存（能解析成 JSON 就存 JSON），不做任何改写。
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import math
import json
import os
import re
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

FOLDER = Path('S1_选品板块/amazon-market-research/运行数据')
_TOOL = re.compile(r'^(?:mcp__)?(?:(?P<server>[A-Za-z0-9_-]+?)__)?(?P<tool>[a-z][a-z0-9_]*)$')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def parse_tool(name, server=None):
    match = _TOOL.match(str(name or '').strip())
    if not match:
        raise ValueError(f'工具名不合法：{name}（例：sorftime__product_variations 或 product_variations --server sorftime）')
    server = match['server'] or server
    if not server:
        raise ValueError('缺少数据源名：写成 sorftime__product_variations，或加 --server sorftime')
    return server, match['tool']


@contextmanager
def _file_lock(path, timeout=15.0):
    """Lock the stable sidecar, never the JSON inode replaced by each save.

    Closing the descriptor (including process exit) releases the OS lock. Keep
    the sidecar on disk: unlinking it could let waiters lock different inodes.
    """
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError('文件锁超时必须是非负有限秒数')
    lock_path = path.with_name(path.name + '.lock')
    deadline = time.monotonic() + timeout
    with lock_path.open('a+b') as stream:
        while True:
            try:
                if os.name == 'nt':
                    import msvcrt
                    stream.seek(0)
                    # Windows permits locking a byte beyond EOF, so an empty
                    # sidecar needs no racy initialization or truncation.
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f'等待 MCP 回包文件锁超时（{timeout:g} 秒）：{lock_path}') from exc
                time.sleep(min(0.05, remaining))
        yield


def _atomic_write(path, document):
    # Serialize before opening a temporary; failures leave the original intact.
    text = json.dumps(document, ensure_ascii=False, indent=2) + '\n'
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def save(root, tool, args, raw, *, server=None, now=None, evidence=None, lock_timeout=15.0):
    """存一次调用，返回 {path, key, replaced, calls}。evidence：自动入档时记下证据级别和来源（脚本哈希、调用号等）。"""
    server, short = parse_tool(tool, server)
    if not isinstance(args, dict):
        raise ValueError('参数必须是 JSON 对象，例如 {"asin": "B0XXXXXXX"}')
    if raw is None or (isinstance(raw, (str, bytes)) and not raw.strip()):
        raise ValueError('回包是空的；工具报错也请把完整报错原样保存')
    if isinstance(raw, bytes):
        raw = raw.decode('utf-8-sig')
    text = raw if isinstance(raw, str) else canonical(raw)
    try:
        response = json.loads(text) if isinstance(raw, str) else raw
    except ValueError:
        response = text  # 非 JSON（比如纯文本报错）也原样保留
    root = Path(root).resolve()
    if not (root / 'S1_选品板块').is_dir():
        raise ValueError(f'不是产品根（缺 S1_选品板块/）：{root}')
    folder = root / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'mcp_calls__{short}.json'
    with _file_lock(path, lock_timeout):
        document = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {'calls': {}}
        if not isinstance(document, dict) or not isinstance(document.get('calls'), dict):
            raise ValueError(f'{path.name} 不是本工具写的格式，未改动')
        key = short + '_' + hashlib.sha256(canonical(args).encode()).hexdigest()[:12]
        # 兼容旧消费者的最新查询索引；完整调用历史另外保留，不因同参数重调而丢证据。
        previous = document['calls'].get(key)
        if previous and previous.get('tool') != f'mcp__{server}__{short}':
            key = short + '_' + hashlib.sha256(canonical([server, args]).encode()).hexdigest()[:12]
            previous = document['calls'].get(key)
        replaced = key in document['calls']
        if previous and (previous.get('sha256') != hashlib.sha256(text.encode('utf-8')).hexdigest()
                         or previous.get('evidence') != evidence):
            history = document.setdefault('history', [])
            if previous not in history:
                history.append(previous)
        document['calls'][key] = {'tool': f'mcp__{server}__{short}', 'args': args, 'response': response, 'response_text': text,
                                  'saved_at': (now or datetime.now(timezone.utc)).isoformat(timespec='seconds'),
                                  'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest()}
        if evidence:
            document['calls'][key]['evidence'] = evidence
        _atomic_write(path, document)
        return {'path': path.relative_to(root).as_posix(), 'key': key, 'replaced': replaced, 'calls': len(document['calls'])}


def save_inventory(root, server, tools, *, now=None):
    """存当前宿主某个数据源的工具清单。验收据此认定“宿主确实没有这个工具”，而不是凭一句话。"""
    names = [str(t.get('name') if isinstance(t, dict) else t) for t in tools or []]
    if not names or not re.fullmatch(r'[A-Za-z0-9_-]+', str(server or '')):
        raise ValueError('工具清单不能为空，且要写数据源名（--server）')
    root = Path(root).resolve()
    if not (root / 'S1_选品板块').is_dir():
        raise ValueError(f'不是产品根（缺 S1_选品板块/）：{root}')
    folder = root / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'{server}_tool_inventory.json'
    path.write_text(json.dumps({'server': server, 'tools': names, 'saved_at': (now or datetime.now(timezone.utc)).isoformat(timespec='seconds')},
                               ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return {'path': path.relative_to(root).as_posix(), 'tools': len(names)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', required=True, type=Path, help='产品根（含 S1_选品板块/）')
    parser.add_argument('--tool', help='工具名，如 sorftime__product_variations')
    parser.add_argument('--server', help='数据源名（工具名里没写时用；存工具清单时必填）')
    parser.add_argument('--args', help='调用参数，JSON 对象')
    parser.add_argument('--lock-timeout', type=float, default=15.0, help='等待回包文件锁的秒数，默认 15；超时明确报错')
    parser.add_argument('--response-file', help='回包文件；- 表示标准输入')
    parser.add_argument('--inventory-file', help='改为保存宿主工具清单：JSON 数组（工具名或 {name}）')
    options = parser.parse_args(argv)
    try:
        if options.inventory_file:
            result = save_inventory(options.root, options.server, json.loads(Path(options.inventory_file).read_text(encoding='utf-8-sig')))
            print(json.dumps({'ok': True, **result}, ensure_ascii=False))
            return 0
        if not (options.tool and options.args and options.response_file):
            raise ValueError('保存回包需要 --tool、--args、--response-file')
        args = json.loads(options.args)
        raw = sys.stdin.buffer.read() if options.response_file == '-' else Path(options.response_file).read_bytes()
        result = save(options.root, options.tool, args, raw, server=options.server, lock_timeout=options.lock_timeout)
    except (OSError, ValueError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps({'ok': True, **result}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
