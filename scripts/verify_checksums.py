#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""团队包完整性校验（跨平台，只用标准库）。

CHECKSUMS.txt 每行是 `<sha256>  <相对路径>`，路径一律用正斜杠。
Mac / Linux：python3 <kit>/scripts/verify_checksums.py <kit>
Windows：    py -3 <kit>\\scripts\\verify_checksums.py <kit>

退出码：0 = 全部通过；1 = 有文件缺失、摘要不符或校验表读不到。
默认逐个文件打印 OK / FAIL；--quiet 只打印失败项和最后一行汇总。
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

CHUNK = 1024 * 1024


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(CHUNK), b''):
            digest.update(block)
    return digest.hexdigest()


def read_table(table: Path):
    """返回 [(相对路径, 期望摘要)]；容忍 CRLF、空行和 `*路径` 的二进制标记。"""
    entries = []
    for number, line in enumerate(table.read_text(encoding='utf-8-sig').splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2 or len(parts[0]) != 64:
            raise ValueError(f'CHECKSUMS.txt 第 {number} 行格式不对：{line[:80]}')
        digest, name = parts[0].lower(), parts[1].strip()
        if name.startswith('*'):
            name = name[1:]
        entries.append((name.replace('\\', '/'), digest))
    return entries


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='校验团队包 CHECKSUMS.txt（Mac / Windows 通用）')
    parser.add_argument('kit', nargs='?', default=None, help='团队包目录（默认：本脚本所在目录的上一级）')
    parser.add_argument('--quiet', action='store_true', help='只打印失败项和汇总')
    args = parser.parse_args(argv)

    kit = Path(args.kit).expanduser().resolve() if args.kit else Path(__file__).resolve().parent.parent
    table = kit / 'CHECKSUMS.txt'
    if not table.is_file():
        print(f'❌ 找不到校验表：{table}')
        return 1
    try:
        entries = read_table(table)
    except (OSError, ValueError) as exc:
        print(f'❌ 校验表读不了：{exc}')
        return 1
    if not entries:
        print('❌ 校验表是空的。')
        return 1

    failed, extra = [], []
    for name, expected in entries:
        target = kit / name
        if not target.is_file():
            failed.append((name, '文件不见了'))
            print(f'FAIL {name}  文件不见了')
            continue
        try:
            actual = sha256_of(target)
        except OSError as exc:
            failed.append((name, f'读不了：{exc}'))
            print(f'FAIL {name}  读不了：{exc}')
            continue
        if actual != expected:
            failed.append((name, '摘要不符'))
            print(f'FAIL {name}  摘要不符')
        elif not args.quiet:
            print(f'OK   {name}')

    listed = {name for name, _ in entries} | {'CHECKSUMS.txt'}
    for path in sorted(kit.rglob('*')):
        if path.is_file() and path.relative_to(kit).as_posix() not in listed:
            extra.append(path.relative_to(kit).as_posix())

    print(f'\n共 {len(entries)} 个文件：通过 {len(entries) - len(failed)}，失败 {len(failed)}。')
    if extra:
        print(f'⚠️ 另有 {len(extra)} 个不在校验表里的文件（安装器写的水印、你自己加的产物都会这样，不算失败）：')
        for name in extra[:10]:
            print('   + ' + name)
        if len(extra) > 10:
            print(f'   …… 还有 {len(extra) - 10} 个')
    if failed:
        print('❌ 校验未通过。把上面的 FAIL 行原样交给老板，不要继续搭建。')
        return 1
    print('✅ 校验全部通过。')
    return 0


if __name__ == '__main__':
    sys.exit(main())