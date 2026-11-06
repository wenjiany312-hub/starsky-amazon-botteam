"""Read complete YAML business facts while omitting inline image payloads.

Digests/byte counts refer to the original scalar's UTF-8 bytes (or YAML binary
bytes), not a decoded image. The input-file digest and YAML field path make
each omission reproducible. No business-field length limit is imposed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import yaml


IMAGE_BASE64 = re.compile(
    r'data:image/[a-z0-9.+-]+(?:;[a-z0-9=.+-]+)*;base64,[ \t\r\n]*[a-z0-9+/]+'
    # A continuation is a complete encoding token followed by the end of its
    # line or an explicit text delimiter. Nearby business text stays intact.
    r'(?:[ \t]*\r?\n[ \t]*[a-z0-9+/]+(?==|[ \t]*(?:\r?\n|$|[^\x00-\x7f]|[;,])))*={0,2}',
    re.IGNORECASE)
IMAGE_URI = re.compile(IMAGE_BASE64.pattern + '|' +
                       r'data:image/[a-z0-9.+-]+(?:;[a-z0-9=.+-]+)*,'
                       r'[a-z0-9+/%=._~!$&()*,:;?@-]+', re.IGNORECASE)
IMAGE_PATH = re.compile(r'\.(?:png|jpe?g|webp|gif|svg|bmp|tiff?)(?:[?#].*)?$', re.IGNORECASE)


def sanitize_facts(document, source_sha256):
    """Return the full YAML structure with only image scalars replaced."""
    active = set()

    def marker(value, path):
        raw = value if isinstance(value, bytes) else value.encode('utf8')
        return {'omitted': 'image_payload', 'sha256': hashlib.sha256(raw).hexdigest(),
                'bytes': len(raw), 'encoding': 'binary' if isinstance(value, bytes) else 'utf8',
                'source_sha256': source_sha256, 'field_path': path}

    def visit(value, path, image_field=False):
        if isinstance(value, str):
            # Pure URI scalars can use wrapped YAML block strings.
            if IMAGE_URI.fullmatch(value.strip()):
                return marker(value, path)
            if image_field and not re.match(r'^(?:https?|file)://', value, re.IGNORECASE) and not IMAGE_PATH.search(value):
                return marker(value, path)
            # Preserve all neighboring business text around an embedded URI.
            return IMAGE_URI.sub(lambda match: '[image omitted ' +
                                 json.dumps(marker(match.group(), path), ensure_ascii=False, separators=(',', ':')) + ']', value)
        if image_field and isinstance(value, bytes):
            return marker(value, path)
        if isinstance(value, (dict, list)):
            identity = id(value)
            if identity in active:
                raise ValueError('cyclic YAML cannot be represented as complete business facts')
            active.add(identity)
            try:
                if isinstance(value, dict):
                    return {key: visit(item, path + [str(key)],
                                       str(key).casefold() == 'image_data' or
                                       (image_field and str(key).casefold() in
                                        {'data', 'base64', 'payload', 'bytes', 'content', 'value'}))
                            for key, item in value.items()}
                return [visit(item, path + [number], image_field) for number, item in enumerate(value)]
            finally:
                active.remove(identity)
        return value

    return visit(document, [])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True, help='Read-only source YAML')
    parser.add_argument('--output', type=Path, help='Optional NEW output file; defaults to stdout, never overwrites')
    args = parser.parse_args(argv)
    if args.output is not None and args.output.exists():
        parser.exit(2, 'Refusing to overwrite an existing output or input file.\n')
    try:
        raw = args.input.read_bytes()
        document = yaml.safe_load(raw.decode('utf-8-sig'))
        sanitized = sanitize_facts(document, hashlib.sha256(raw).hexdigest())
        text = yaml.safe_dump(sanitized, allow_unicode=True, sort_keys=False)
        if args.output is None:
            sys.stdout.write(text)
        else:
            with args.output.open('x', encoding='utf8', newline='\n') as destination:
                destination.write(text)
    except (yaml.YAMLError, UnicodeError):
        # Parser diagnostics may contain the complete image scalar; never echo it.
        parser.exit(2, 'invalid YAML or UTF-8 input; payload details suppressed.\n')
    except FileExistsError:
        parser.exit(2, 'Refusing to overwrite an existing output or input file.\n')
    except OSError as error:
        parser.exit(2, 'Cannot read input or create output: ' + type(error).__name__ + '.\n')
    except ValueError as error:
        parser.exit(2, str(error) + '.\n')
    return 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf8')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf8')
    raise SystemExit(main())
