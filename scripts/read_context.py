"""Read verified live context for selected routes once; never run business tools."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath


class ContextError(ValueError):
    pass


def _live_path(home, skill, relative):
    if not isinstance(relative, str) or not relative or '\x00' in relative:
        raise ContextError('Invalid context path')
    windows = PureWindowsPath(relative)
    normalized = relative.replace('\\', '/')
    if windows.drive or windows.is_absolute() or PurePosixPath(normalized).is_absolute():
        raise ContextError('Absolute context path is forbidden: ' + relative)
    lexical_parts = PurePosixPath(normalized).parts
    if any('archive' in part.casefold() or part.casefold().startswith('original') for part in lexical_parts):
        raise ContextError('Archive/original context path is forbidden: ' + relative)
    path = (skill / normalized).resolve(strict=True)
    if not path.is_relative_to(home):
        raise ContextError('Context path escapes plugin root: ' + relative)
    if any('archive' in part.casefold() or part.casefold().startswith('original')
           for part in path.relative_to(home).parts):
        raise ContextError('Resolved archive/original context path is forbidden: ' + relative)
    if not path.is_file():
        raise ContextError('Context path is not a file: ' + relative)
    return path


def read_context(home, routes):
    """Validate all selected source bytes before returning complete original text."""
    from plugin_freshness import guard_plugin
    guard_plugin(Path(__file__).resolve().parents[1])
    guard_plugin(home)
    home = Path(home).resolve(strict=True)
    skill = home / 'skills/starsky-amazon-agent'
    index_path = _live_path(home, skill, 'references/context/index.json')
    index = json.loads(index_path.read_text(encoding='utf-8-sig'))
    catalog = {}
    for row in index.get('sections', []):
        relative, digest = row.get('path'), row.get('sha256')
        if relative in catalog and catalog[relative] != digest:
            raise ContextError('Conflicting section hash in context index')
        catalog[relative] = digest
    supporting_hashes = index.get('document_hashes', {})
    chosen = []
    known_routes = index.get('routes', {})
    for route in dict.fromkeys(routes):
        if route not in known_routes:
            raise ContextError('Unknown context route: ' + route)
        for relative in known_routes[route]:
            if relative not in catalog:
                raise ContextError('Route has no indexed live section/hash: ' + route)
            chosen.append((relative, catalog[relative]))
        for field in ['supporting_documents', 'knowledge_documents']:
            for relative in index.get(field, {}).get(route, []):
                chosen.append((relative, supporting_hashes.get(relative)))
    emitted_paths, emitted_hashes, blocks = set(), set(), []
    for relative, expected in chosen:
        path = _live_path(home, skill, relative)
        if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
            raise ContextError('Missing or invalid document hash: ' + relative)
        raw = path.read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        if actual != expected:
            raise ContextError('Context hash mismatch: ' + relative)
        # Verify even duplicate references, then emit each path/content only once.
        if path in emitted_paths or actual in emitted_hashes:
            continue
        emitted_paths.add(path)
        emitted_hashes.add(actual)
        original = raw.decode('utf-8-sig')
        label = path.relative_to(home).as_posix()
        blocks.append('\n===== 插件原文：' + label + ' | sha256=' + actual + ' =====\n' + original)
    return ''.join(blocks)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=Path(__file__).resolve().parents[1], help='Installed plugin root')
    parser.add_argument('--routes', nargs='+', required=True, help='Existing route names, e.g. startup S1 or recovery S1')
    args = parser.parse_args(argv)
    try:
        text = read_context(args.home, args.routes)
    except (ContextError, OSError, UnicodeError, json.JSONDecodeError, TypeError, KeyError) as error:
        parser.exit(2, 'Cannot read context: ' + str(error) + '\n')
    # All verification has completed; failures above never emit partial rules.
    sys.stdout.write(text)
    return 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf8', newline='\n')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf8')
    raise SystemExit(main())
