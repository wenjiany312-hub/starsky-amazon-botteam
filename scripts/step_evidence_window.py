"""Immutable controller-owned MCP windows; filesystem mtimes are never evidence.

The live window is persisted in workflow state. Each record seals a separate
window in both its event and receipt envelope, bound to the receipt version.
Legacy receipts without these seals remain usable business records, but cannot
establish historical MCP attribution. begin can start a new prospective window.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time
import uuid

import yaml


class WindowUnverifiable(ValueError):
    pass


def _bad(detail):
    raise WindowUnverifiable('step_window_unverifiable：步骤时间边界无法核验（' + detail +
                             '）；未扫描或认领历史调用。旧数据请人工核对；当前步可先 begin 建立新窗口，再执行调用。')


def _ns(value):
    return type(value) is int and value > 0


def start_round(flow, *, origin, now_ns=None):
    """Only init, an explicit reopen, or legacy begin may start a new round."""
    stamp = time.time_ns() if now_ns is None else now_ns
    if not _ns(stamp):
        _bad('新窗口时间无效')
    identity = uuid.uuid4().hex
    flow['evidence_execution_id'] = identity
    flow['evidence_window'] = {
        'schema_version': 1, 'execution_id': identity, 'n': int(flow['cursor']),
        'since_ns': stamp, 'predecessor': None, 'origin': origin,
    }


def begin_window(flow):
    """Do not slide an existing window on repeated begin/ack."""
    if flow.get('evidence_window') is not None:
        _active(flow)
        return ''
    start_round(flow, origin='legacy_begin')
    return '历史步骤没有可核验的固定时间边界；已从本次 begin 建立新窗口，此前调用不能自动归属本步。'


def _active(flow):
    window = flow.get('evidence_window')
    if (not isinstance(window, dict) or window.get('schema_version') != 1
            or window.get('n') != flow.get('cursor')
            or not window.get('execution_id')
            or window.get('execution_id') != flow.get('evidence_execution_id')
            or not _ns(window.get('since_ns'))):
        _bad('当前执行轮次或起点缺失/不一致')
    return window


def seal_event(flow, event, *, now_ns=None):
    """Seal even legacy record, but never invent its unknown historical start."""
    stamp = time.time_ns() if now_ns is None else now_ns
    active = flow.get('evidence_window')
    if active is not None:
        if (not isinstance(active, dict) or active.get('n') != event['n']
                or active.get('execution_id') != flow.get('evidence_execution_id')
                or not _ns(active.get('since_ns'))):
            _bad('提交步骤与当前执行窗口不一致')
    since = active['since_ns'] if active else None
    if not _ns(stamp) or (since is not None and stamp < since):
        _bad('封存时间早于起点或时间无效')
    identity = flow.get('evidence_execution_id') or uuid.uuid4().hex
    flow['evidence_execution_id'] = identity
    seal = {
        'schema_version': 1, 'window_id': uuid.uuid4().hex, 'execution_id': identity,
        'n': event['n'], 'receipt_id': event['receipt_id'],
        'since_ns': since, 'until_ns': stamp,
        'predecessor': deepcopy(active.get('predecessor')) if active else None,
    }
    event['recorded_at_ns'] = stamp
    event['evidence_window'] = seal
    return deepcopy(seal)


def advance_window(flow, event):
    """Use the actual next cursor (including selective queues), never n - 1."""
    if event['outcome'] in {'blocked', 'waiting_external'}:
        return
    seal = event['evidence_window']
    if flow['cursor'] == event['n']:
        flow.pop('evidence_window', None)
        return
    flow['evidence_window'] = {
        'schema_version': 1, 'execution_id': seal['execution_id'],
        'n': flow['cursor'], 'since_ns': seal['until_ns'], 'origin': 'record',
        'predecessor': {k: seal[k] for k in ('n', 'receipt_id', 'window_id')},
    }


def _load(root, stage):
    try:
        state = yaml.safe_load((Path(root) / 'workflow_state.yaml').read_text(encoding='utf-8-sig'))
        flow = state.get(stage.lower() + '_execution') if isinstance(state, dict) else None
        if not isinstance(flow, dict):
            _bad('流程事件缺失')
        return flow
    except (OSError, yaml.YAMLError) as error:
        _bad('流程状态不可读：' + str(error))


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def _verified_seal(root, stage, event, *, require_start):
    seal = event.get('evidence_window')
    if (not isinstance(seal, dict) or seal.get('schema_version') != 1
            or not seal.get('window_id') or not seal.get('execution_id')
            or seal.get('n') != event.get('n') or seal.get('receipt_id') != event.get('receipt_id')
            or not _ns(seal.get('until_ns')) or event.get('recorded_at_ns') != seal.get('until_ns')):
        _bad('封存事件没有固定时间窗口')
    since = seal.get('since_ns')
    if ((since is None and require_start) or (since is not None and
            (not _ns(since) or since > seal['until_ns']))):
        _bad('历史起点缺失或起止倒置')
    expected = (Path('00_总控/starsky-fullchain-workflow/运行数据/流程控制') / stage /
                'receipts' / f"{int(event['n']):02}.json").as_posix()
    ref = event.get('receipt_ref') or {}
    selector = '/receipt_versions/' + event['receipt_id'] + '/receipt'
    if ref.get('path') != expected or ref.get('selector') != selector:
        _bad('回执版本引用不一致')
    path = Path(root) / expected
    if not path.resolve().is_relative_to(Path(root).resolve()):
        _bad('回执路径越界')
    try:
        envelope = json.loads(path.read_text(encoding='utf-8-sig'))
        receipt = envelope['receipt_versions'][event['receipt_id']]['receipt']
        saved = envelope['evidence_windows'][seal['window_id']]
    except (OSError, ValueError, KeyError, TypeError) as error:
        _bad('封存回执或时间版本缺失：' + str(error))
    if saved != seal or ref.get('sha256') != _hash(receipt):
        _bad('封存窗口或回执 Hash 不一致')
    return deepcopy(seal)


def _predecessor(root, stage, flow, window):
    reference = window.get('predecessor')
    if reference is None:
        return
    if not isinstance(reference, dict):
        _bad('前驱引用无效')
    matches = [event for event in flow.get('events', []) if not event.get('superseded')
               and all((event.get('evidence_window') or {}).get(key) == reference.get(key)
                       for key in ('n', 'receipt_id', 'window_id'))]
    if len(matches) != 1:
        _bad('当前执行链前驱事件缺失或不唯一')
    seal = _verified_seal(root, stage, matches[0], require_start=False)
    if (seal['execution_id'] != window['execution_id'] or seal['until_ns'] != window['since_ns']
            or matches[0].get('outcome') in {'blocked', 'waiting_external'}):
        _bad('前驱与本步窗口不相接')


def current_window(root, stage):
    flow = _load(root, stage)
    if flow.get('delivery_status') in {'complete', 'awaiting_card'}:
        _bad('当前步已封存；历史证据请用 kb_amend 追加补记')
    window = deepcopy(_active(flow))
    _predecessor(root, stage, flow, window)
    return window


def sealed_window(root, stage, step):
    flow = _load(root, stage)
    # Reopened steps must not borrow their old execution's envelope.
    events = [event for event in flow.get('events', [])
              if not event.get('superseded') and event.get('n') == int(step)]
    if not events:
        _bad('该步没有有效封存事件')
    seal = _verified_seal(root, stage, events[-1], require_start=True)
    _predecessor(root, stage, flow, seal)
    return seal


def contains(window, call):
    stamp = call.get('time_ns')
    return (_ns(stamp) and stamp >= window['since_ns']
            and (window.get('until_ns') is None or stamp <= window['until_ns']))
