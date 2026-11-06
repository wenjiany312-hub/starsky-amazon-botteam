"""Metadata-only native updates through the registered compact-case transaction."""
import hashlib
import json
from pathlib import Path
import yaml
from r01_case_store import CompactCase, is_view, decode_business_files


def _old_sources(old, root):
    """Resolve a superseded live source only from its registered old decision."""
    result = {}
    for relative, item in old.get('business_files', {}).items():
        decode_business_files({relative: item})  # Reject malformed metadata before any fallback.
        try:
            result.update(decode_business_files({relative: item}, root))
        except ValueError as original_error:
            for decision in old.get('business_decisions', {}).values():
                saved = decision.get('business_files', {}).get(relative, {})
                if (saved.get('sha256') != item.get('sha256') or saved.get('size') != item.get('size')
                        or not str(saved.get('path', '')).startswith('_history/')):
                    continue
                # The existing decoder checks path containment, SHA and size.
                result.update(decode_business_files({relative: saved}, root))
                break
            else:
                raise original_error
    return result


def mutate(root, stage, number, callback, *, operation):
    root = Path(root).resolve()
    store = CompactCase(root)
    if is_view(root):
        return callback(root)
    state_path = root / 'workflow_state.yaml'
    state = yaml.safe_load(state_path.read_bytes()) if state_path.is_file() else {}
    flow = (state or {}).get(stage.lower() + '_execution') or {}
    if (flow.get('delivery_status') == 'complete' or any(row.get('n') == number for row in flow.get('records', [])) or
            (flow.get('cursor') is not None and flow['cursor'] != number)):
        raise ValueError('已封存完成的步骤须先正式 reopen 重开，不能直接改写知识库元数据')
    if not store.enabled():
        return callback(root)
    receipt_path = root / f'00_总控/starsky-fullchain-workflow/运行数据/流程控制/{stage}/receipts/{number:02}.json'
    from sync_s1_decision_reports import rollback_transaction
    # materialize may migrate legacy inline sources. Invalid input must undo
    # even that preparation, not only commit_step's final replacements.
    with rollback_transaction(root):
        before = receipt_path.read_bytes() if receipt_path.is_file() else None
        with store.materialize() as view:
            historical_outputs = {}
            if before is not None:
                old = json.loads(before)
                digest = hashlib.sha256(before).hexdigest()
                prefix = f'_history/{stage}/{number:02}/metadata/{digest[:16]}'
                original_path = root / (prefix + '/original.json.backup')
                if original_path.is_file() and original_path.read_bytes() != before:
                    raise ValueError('历史回执内容寻址冲突，原历史保留')
                historical_outputs[prefix + '/original.json.backup'] = before
                # A historical envelope must bind immutable bytes, not the live
                # evidence/plan paths that this operation is about to replace.
                for relative, raw in _old_sources(old, root).items():
                    location = f'_history/{stage}/{number:02}/metadata/src/' + hashlib.sha256(raw).hexdigest()[:24] + '.source'
                    previous = root / location
                    if previous.is_file() and previous.read_bytes() != raw:
                        raise ValueError('历史来源内容寻址冲突，原历史保留')
                    historical_outputs[location] = raw
                    old['business_files'][relative] = {'path': location,
                        'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)}
                old['original_receipt_sha256'] = digest
                historical_outputs[prefix + '.json'] = json.dumps(old, ensure_ascii=False, indent=2).encode('utf8')
            result = callback(view.root)
            prepared = view.collect(number, stage=stage, metadata={'operation': operation, 'result': result})
            prepared.outputs.update(historical_outputs)
            committed = store.commit_step(prepared)
    return {**result, 'compact': True, 'receipt_path': committed['receipt_path']}
