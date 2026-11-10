"""构建时导入检查：包里每个 .py 的 import 必须能解析。

能解析的来源只有三类：
1. 标准库（sys.stdlib_module_names，已含 winreg / msvcrt / fcntl 这类平台专用模块，不同平台各自放行）
2. 平台会装的第三方依赖（REQUIREMENT_FILES 两份依赖清单，见下）
3. 包里自带的模块：包内任意位置有 <name>.py 或 <name>/__init__.py

放在 try/except ImportError（或 ModuleNotFoundError / Exception）里的导入算可选导入，解析不到也放行，只计数。
相对导入（from . import x）不查。

注意：这里只查「模块在不在包里」，不查脚本运行时 sys.path 能不能找到它；后者靠构建后实跑脚本验证。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

# 平台运行环境会提供的第三方库（import 名），全部来自 install_grokbot.sh 会装的两份依赖：
#   mcp/local/1688/requirements.txt（1688_scraper 的虚拟环境）
#   scripts/requirements.txt（技能脚本的 {WORK}/py 虚拟环境）
# 发行名 → import 名，只列和发行名不同的
DIST_TO_IMPORT = {'pyyaml': 'yaml', 'pillow': 'PIL', 'beautifulsoup4': 'bs4', 'pycryptodome': 'Crypto',
                  'pymupdf': 'fitz', 'opencv-python-headless': 'cv2', 'opencv-python': 'cv2'}
REQUIREMENT_FILES = ('mcp/local/1688/requirements.txt', 'scripts/requirements.txt')
# 只在测试文件（test_*.py）里允许的库：客户不跑单测，不进运行依赖
TEST_ONLY = {'pytest'}


def third_party(root: Path) -> set[str]:
    names = set()
    for rel in REQUIREMENT_FILES:
        path = root / rel
        if not path.is_file():
            continue
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.split('#', 1)[0].strip()
            if not line:
                continue
            dist = re.split(r'[<>=!~;\[ ]', line, maxsplit=1)[0].strip().lower()
            names.add(DIST_TO_IMPORT.get(dist, dist.replace('-', '_')))
    return names

OPTIONAL_HANDLERS = {'ImportError', 'ModuleNotFoundError', 'Exception', 'BaseException'}


def _optional_nodes(tree: ast.AST) -> set[int]:
    """返回 try 体里、且 except 接 ImportError 类异常的导入节点 id。"""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        names = set()
        for handler in node.handlers:
            if handler.type is None:
                names.add('BaseException')
            elif isinstance(handler.type, ast.Name):
                names.add(handler.type.id)
            elif isinstance(handler.type, ast.Tuple):
                names |= {e.id for e in handler.type.elts if isinstance(e, ast.Name)}
        if names & OPTIONAL_HANDLERS:
            for stmt in node.body:
                for sub in ast.walk(stmt):
                    if isinstance(sub, (ast.Import, ast.ImportFrom)):
                        ids.add(id(sub))
    return ids


def _imports(tree: ast.AST):
    optional = _optional_nodes(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split('.')[0], node.lineno, id(node) in optional
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module.split('.')[0], node.lineno, id(node) in optional


def local_modules(root: Path) -> set[str]:
    names = set()
    for p in root.rglob('*.py'):
        if '__pycache__' in p.parts:
            continue
        names.add(p.stem if p.name != '__init__.py' else p.parent.name)
    return names


def check(root: Path) -> dict:
    root = Path(root)
    local = local_modules(root)
    allowed = set(sys.stdlib_module_names) | third_party(root) | local | {'__future__'}
    missing, optional_missing, syntax = [], 0, []
    files = 0
    for p in sorted(root.rglob('*.py')):
        if '__pycache__' in p.parts:
            continue
        files += 1
        try:
            tree = ast.parse(p.read_bytes(), filename=str(p))
        except SyntaxError as exc:
            syntax.append(f'{p.relative_to(root).as_posix()}:{exc.lineno}')
            continue
        for name, line, optional in _imports(tree):
            if name in allowed or (name in TEST_ONLY and p.name.startswith('test_')):
                continue
            if optional:
                optional_missing += 1
            else:
                missing.append(f'{p.relative_to(root).as_posix()}:{line} import {name}')
    return {'files': files, 'missing': missing, 'optional_missing': optional_missing, 'syntax_errors': syntax}


if __name__ == '__main__':
    import json
    result = check(Path(sys.argv[1]))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(1 if result['missing'] or result['syntax_errors'] else 0)
