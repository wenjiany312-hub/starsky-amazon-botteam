"""Small, local R01 writes. Never prune pre-existing business evidence."""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time


def selected_bytes(path, selector=None):
    """Read raw bytes or canonical UTF-8 JSON at a JSON Pointer.

    The caller must apply its normal product-root boundary check to path.
    Selecting stage data keeps unrelated card/ack updates outside its digest.
    """
    raw=Path(path).read_bytes()
    if selector is None:return raw
    if not isinstance(selector,str) or not selector.startswith('/'):
        raise ValueError('selector must be a JSON Pointer beginning with /')
    value=json.loads(raw.decode('utf-8-sig'))
    try:
        for token in selector[1:].split('/'):
            token=token.replace('~1','/').replace('~0','~')
            if isinstance(value,list):
                if not token.isdecimal():raise ValueError('invalid JSON Pointer array index')
                value=value[int(token)]
            else:
                value=value[token]
    except (KeyError,IndexError,TypeError) as error:
        raise ValueError('JSON Pointer does not select an existing value') from error
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf8')


def selected_sha256(path, selector=None):
    return hashlib.sha256(selected_bytes(path,selector)).hexdigest()


def write_if_changed(path, content):
    """Return whether bytes changed; identical content does not touch the file."""
    path = Path(path)
    content = content.encode('utf8') if isinstance(content, str) else bytes(content)
    if path.is_file() and path.read_bytes() == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return True


def write_step_snapshot(root, stage, step, payload, *, kind='final', decision_id=None, now=None):
    """One final snapshot per step; explicit decisions have immutable identities.

    Drafts belong in the working receipt, not history. The stable final slot is
    replaced during regenerations, including the ten-minute coalescing window.
    Existing historical folders are never visited, removed, or migrated.
    """
    if not re.fullmatch(r'S[1-5]', str(stage)) or not isinstance(step, int) or step < 1:
        raise ValueError('invalid stage or step')
    if kind not in ('final', 'decision') or (kind == 'decision' and not decision_id):
        raise ValueError('snapshot requires final or a named user decision')
    stamp = time.time() if now is None else float(now)
    suffix = 'final' if kind == 'final' else 'decision-' + hashlib.sha256(str(decision_id).encode()).hexdigest()[:20]
    path = Path(root).resolve() / '_history/r01-generated' / stage / f'step-{step:02}-{suffix}.json'
    previous = json.loads(path.read_text(encoding='utf8')) if path.exists() else None
    if previous and previous.get('payload') == payload:
        return path
    if previous and kind == 'decision':
        raise ValueError('a user decision snapshot cannot be overwritten')
    start = previous.get('window_started_at', stamp) if previous else stamp
    if stamp - start >= 600:
        start = stamp
    document = {'schema_version': 1, 'stage': stage, 'step': step, 'kind': kind,
                'decision_id': decision_id, 'window_started_at': start,
                'generated_at': stamp, 'payload': payload}
    write_if_changed(path, json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    return path
