"""Deterministic, lossless CSV/TSV intake. All cells remain strings."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import io
import json
from pathlib import Path, PureWindowsPath


def safe_path(product_root, relative):
    """Reject foreign paths, traversal and symlinks/junctions before IO."""
    root = Path(product_root).resolve(strict=True)
    text = str(relative)
    windows = PureWindowsPath(text)
    parts = text.replace('\\', '/').split('/')
    if (not text or windows.drive or windows.root or text.startswith('/')
            or any(p in {'', '.', '..'} or ':' in p for p in parts)):
        raise ValueError('unsafe_relative_path')
    path = root.joinpath(*parts)
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
            raise ValueError('symlink_path_rejected')
    try:
        path.resolve().relative_to(root)
    except ValueError:
        raise ValueError('path_outside_product_root') from None
    return path


def digest_bytes(value):
    return hashlib.sha256(value).hexdigest()


def file_binding(root, relative):
    path = safe_path(root, relative)
    if not path.is_file():
        raise ValueError('missing_artifact')
    raw = path.read_bytes()
    return {'path': path.relative_to(Path(root).resolve()).as_posix(), 'sha256': digest_bytes(raw), 'bytes': len(raw)}


def read_table(root, relative, encoding='utf-8-sig'):
    path = safe_path(root, relative)
    if path.suffix.lower() not in {'.csv', '.tsv'}:
        raise ValueError('only_csv_tsv_allowed')
    if encoding not in {'utf-8-sig', 'utf-8', 'utf-16', 'gb18030'}:
        raise ValueError('unsupported_encoding')
    raw = path.read_bytes()
    try:
        reader = csv.reader(io.StringIO(raw.decode(encoding), newline=''),
                            delimiter='\t' if path.suffix.lower() == '.tsv' else ',', strict=True)
        headers = next(reader)
        normalized = [' '.join(h.split()) for h in headers]
        if not normalized or any(not h for h in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError('blank_or_colliding_headers')
        rows = []
        for row in reader:
            if len(row) != len(headers):
                raise ValueError('ragged_row_at_' + str(reader.line_num))
            rows.append(row)
    except (UnicodeError, csv.Error, StopIteration):
        raise ValueError('invalid_table_encoding_or_csv') from None
    source = {'path': path.relative_to(Path(root).resolve()).as_posix(),
              'sha256': digest_bytes(raw), 'bytes': len(raw), 'encoding': encoding}
    return source, headers, normalized, rows


def write_json_new(path, value):
    with path.open('x', encoding='utf-8', newline='\n') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write('\n')


def clean_table(product_root, input_relative, output_relative, *, encoding='utf-8-sig'):
    output = safe_path(product_root, output_relative)
    if output.exists():
        raise ValueError('output_must_be_new_directory')
    source, headers, normalized, rows = read_table(product_root, input_relative, encoding)
    output.mkdir(parents=True, exist_ok=False)
    with (output / 'normalized.csv').open('x', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(normalized)
        writer.writerows(rows)
    write_json_new(output / 'raw_rows.json', {'source': source, 'original_headers': headers, 'rows': rows})
    relative = output.relative_to(Path(product_root).resolve()).as_posix()
    quality = {'schema_version': 1, 'status': 'deterministic_completed', 'source': source,
               'row_count': len(rows), 'column_count': len(headers),
               'duplicate_row_count': sum(n - 1 for n in Counter(tuple(r) for r in rows).values()),
               'missing_empty_cells': {h: sum(row[i] == '' for row in rows) for i, h in enumerate(normalized)},
               'header_mapping': [{'index': i, 'original': h, 'normalized': normalized[i]} for i, h in enumerate(headers)],
               'cell_policy': 'all_strings_unchanged_no_dedup_no_fill_no_currency_or_unit_conversion',
               'artifacts': [file_binding(product_root, relative + '/normalized.csv'),
                             file_binding(product_root, relative + '/raw_rows.json')]}
    write_json_new(output / 'quality.json', quality)
    return {'status': quality['status'], 'row_count': len(rows), 'duplicate_row_count': quality['duplicate_row_count'],
            'source_sha256': source['sha256'], 'normalized': relative + '/normalized.csv',
            'quality': relative + '/quality.json', 'raw_rows': relative + '/raw_rows.json'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('product_root')
    parser.add_argument('input_relative')
    parser.add_argument('output_relative')
    parser.add_argument('--encoding', default='utf-8-sig')
    args = parser.parse_args()
    try:
        summary = clean_table(args.product_root, args.input_relative, args.output_relative, encoding=args.encoding)
    except (ValueError, OSError) as exc:
        print(json.dumps({'status': 'failed', 'error_type': type(exc).__name__}))
        return 1
    print(json.dumps(summary, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
