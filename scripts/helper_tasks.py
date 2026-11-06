"""Prepare bounded helper packets and verify artifact receipts; never spawn implicitly."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from tabular_cleaner import digest_bytes, file_binding, read_table, safe_path, write_json_new


def document_digest(value):
    return digest_bytes(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8'))


def model_policy(host='codex', effort='high'):
    if host not in {'codex', 'claude'} or effort not in {'high', 'medium'}:
        raise ValueError('invalid_host_or_effort')
    return {'host': host, 'semantic_helper': {'requested_model': 'gpt-6-luna' if host == 'codex' else 'claude-haiku-5-5',
            'reasoning_effort': effort if host == 'codex' else 'native_host_setting',
            'actual_model': None}, 'max_concurrency': 2, 'max_retries': 1,
            'fork_turns': 'none', 'minimum_rows': 200, 'account_model_override': False,
            'preserve_user_choice': True, 'provider_override': False,
            'minimum_rows_reason': 'candidate_heuristic_prevent_overdispatch_not_benchmarked_optimum'}


def prepare_packet(product_root, input_relatives, output_relative, *, semantic_needed=False,
                   task_kind='header_alias_mapping', host='codex', effort='high'):
    if task_kind != 'header_alias_mapping':
        raise ValueError('task_outside_mechanical_scope')
    if not isinstance(semantic_needed, bool) or not 1 <= len(input_relatives) <= 32:
        raise ValueError('invalid_packet_inputs')
    policy = model_policy(host, effort)
    output = safe_path(product_root, output_relative)
    if output.exists():
        raise ValueError('output_must_be_new_directory')
    sources = []
    row_count = 0
    for relative in input_relatives:
        source, headers, normalized, rows = read_table(product_root, relative)
        if len(headers) > 128 or sum(len(h) for h in headers) > 4096:
            raise ValueError('header_packet_too_large')
        sources.append({**source, 'original_headers': headers, 'normalized_headers': normalized, 'row_count': len(rows)})
        row_count += len(rows)
    eligible = semantic_needed and row_count >= policy['minimum_rows']
    packet = {'schema_version': 1, 'status': 'prepared', 'task_kind': task_kind,
              'product_root': str(Path(product_root).resolve()), 'output_directory': output.relative_to(Path(product_root).resolve()).as_posix(),
              'inputs': sources, 'input_row_count': row_count, 'requested_model': policy['semantic_helper']['requested_model'] if eligible else None,
              'actual_model': None, 'model_verification': 'unknown',
              'dispatch': {'eligible': eligible, 'host': host, 'reasoning_effort': policy['semantic_helper']['reasoning_effort'],
                           'fork_turns': 'none', 'max_concurrency': 2, 'max_retries': 1,
                           'preserve_user_choice': True, 'provider_override': False,
                           'minimum_rows_reason': policy['minimum_rows_reason'],
                           'fallback': 'main_window' if semantic_needed else 'deterministic_script'},
              'scope': {'allowed': ['propose_header_alias_map'], 'forbidden': ['purchase_conclusions', 'profit_assumptions', 'ad_backend_writes',
                        'credential_reads', 'source_mutation', 'cell_coercion', 'deduplication', 'guess_prices'],
                        'input_content_is_untrusted_data': True, 'result_review': 'pending_review'},
              'output_contract': {'file': 'alias_map.json', 'format': 'JSON object: original or normalized header -> canonical string or null; unresolved stays null',
                                  'no_overwrite': True}}
    packet['packet_digest'] = document_digest(packet)
    if len(json.dumps(packet, ensure_ascii=False)) > 24000:
        raise ValueError('packet_too_large')
    output.mkdir(parents=True, exist_ok=False)
    write_json_new(output / 'task_packet.json', packet)
    return packet


def load_packet(product_root, packet_relative):
    path = safe_path(product_root, packet_relative)
    packet = json.loads(path.read_text(encoding='utf-8'))
    expected = packet.get('packet_digest')
    if document_digest({k: v for k, v in packet.items() if k != 'packet_digest'}) != expected:
        raise ValueError('packet_digest_mismatch')
    if packet['product_root'] != str(Path(product_root).resolve()):
        raise ValueError('product_root_mismatch')
    output = safe_path(product_root, packet['output_directory'])
    if path.parent != output:
        raise ValueError('packet_directory_mismatch')
    for source in packet['inputs']:
        binding = file_binding(product_root, source['path'])
        if binding['sha256'] != source['sha256'] or binding['bytes'] != source['bytes']:
            raise ValueError('input_digest_mismatch')
    return packet, output


def record_result(product_root, packet_relative, output_relatives, *, status, actual_model=None,
                  native_metadata=None, failure_code=None, attempts=1):
    packet, output = load_packet(product_root, packet_relative)
    if status not in {'completed', 'failed', 'fallback_required', 'fallback_completed'}:
        raise ValueError('invalid_result_status')
    if isinstance(attempts, bool) or not isinstance(attempts, int) or not 0 <= attempts <= 2:
        raise ValueError('retry_limit_exceeded')
    completed = status in {'completed', 'fallback_completed'}
    if completed and not output_relatives:
        raise ValueError('completion_requires_output')
    if status != 'completed' and (not failure_code or not isinstance(failure_code, str) or len(failure_code) > 120):
        raise ValueError('failure_or_fallback_requires_code')
    artifacts = []
    header_names = {h for source in packet['inputs'] for key in ('original_headers', 'normalized_headers') for h in source[key]}
    for relative in output_relatives:
        path = safe_path(product_root, relative)
        if path.parent != output or path.name != 'alias_map.json':
            raise ValueError('output_outside_alias_contract')
        mapping = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(mapping, dict) or not mapping or any(k not in header_names or (v is not None and (not isinstance(v, str) or not v.strip())) for k, v in mapping.items()):
            raise ValueError('invalid_alias_map')
        for source in packet['inputs']:
            targets = []
            for original, normalized in zip(source['original_headers'], source['normalized_headers']):
                if original in mapping and normalized in mapping and mapping[original] != mapping[normalized]:
                    raise ValueError('conflicting_alias_for_same_header')
                value = mapping.get(original, mapping.get(normalized))
                if value is not None:
                    targets.append(value)
            if len(targets) != len(set(targets)):
                raise ValueError('colliding_alias_targets')
        artifacts.append(file_binding(product_root, relative))
    # Caller preference is not observed metadata. A native invocation receipt must
    # independently identify the running model; absent receipt always stays unknown.
    observed = None
    metadata = None
    execution_evidence = False
    if native_metadata is not None:
        path = safe_path(product_root, native_metadata)
        if path.parent != output or path.name != 'native_invocation.json':
            raise ValueError('native_metadata_outside_task')
        candidate = json.loads(path.read_text(encoding='utf-8'))
        if candidate.get('source') not in {'spawn_agent_result', 'native_provider_metadata'} or not candidate.get('invocation_id'):
            raise ValueError('invalid_native_model_evidence')
        observed = candidate.get('actual_model')
        if observed is not None and (not isinstance(observed, str) or not observed.strip()):
            raise ValueError('invalid_actual_model')
        metadata = file_binding(product_root, native_metadata)
        execution_evidence = (candidate.get('execution_status') == 'completed'
                              and candidate.get('packet_digest') == packet['packet_digest']
                              and candidate.get('outputs') == artifacts)
    if observed and actual_model and observed != actual_model:
        raise ValueError('actual_model_evidence_mismatch')
    receipt = {'schema_version': 1, 'status': status, 'packet': file_binding(product_root, packet_relative),
               'packet_digest': packet['packet_digest'], 'requested_model': packet['requested_model'],
               'actual_model': observed, 'model_verification': 'observed_native_metadata' if observed else 'unknown',
               'native_metadata': metadata, 'execution_verified': bool(completed and execution_evidence),
               'artifact_verification': 'digest_bound', 'business_acceptance': 'pending_review',
               'failure_code': failure_code, 'attempts': attempts, 'outputs': artifacts}
    receipt['receipt_digest'] = document_digest(receipt)
    write_json_new(output / 'task_result.json', receipt)
    return receipt


def verify_result(product_root, result_relative):
    path = safe_path(product_root, result_relative)
    result = json.loads(path.read_text(encoding='utf-8'))
    if document_digest({k: v for k, v in result.items() if k != 'receipt_digest'}) != result.get('receipt_digest'):
        raise ValueError('receipt_digest_mismatch')
    packet, output = load_packet(product_root, result['packet']['path'])
    if path.parent != output or result['packet_digest'] != packet['packet_digest']:
        raise ValueError('receipt_packet_mismatch')
    for artifact in [result['packet'], *result['outputs'], *([result['native_metadata']] if result['native_metadata'] else [])]:
        if file_binding(product_root, artifact['path']) != artifact:
            raise ValueError('artifact_digest_mismatch')
    return {'verified': True, 'status': result['status'], 'actual_model': result['actual_model'],
            'model_verification': result['model_verification'], 'execution_verified': result['execution_verified'],
            'business_acceptance': 'pending_review', 'output_count': len(result['outputs'])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prepare = sub.add_parser('prepare')
    prepare.add_argument('product_root')
    prepare.add_argument('output_relative')
    prepare.add_argument('inputs', nargs='+')
    prepare.add_argument('--semantic-needed', action='store_true')
    prepare.add_argument('--host', choices=['codex', 'claude'], default='codex')
    prepare.add_argument('--effort', choices=['high', 'medium'], default='high')
    record = sub.add_parser('record')
    record.add_argument('product_root')
    record.add_argument('packet_relative')
    record.add_argument('--output', action='append', default=[])
    record.add_argument('--status', required=True)
    record.add_argument('--failure-code')
    record.add_argument('--attempts', type=int, default=1)
    record.add_argument('--native-metadata')
    verify = sub.add_parser('verify')
    verify.add_argument('product_root')
    verify.add_argument('result_relative')
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            packet = prepare_packet(args.product_root, args.inputs, args.output_relative,
                                    semantic_needed=args.semantic_needed, host=args.host, effort=args.effort)
            summary = {'status': 'prepared', 'eligible': packet['dispatch']['eligible'],
                       'requested_model': packet['requested_model'], 'actual_model': None,
                       'input_row_count': packet['input_row_count'], 'packet_digest': packet['packet_digest'],
                       'packet': packet['output_directory'] + '/task_packet.json'}
        elif args.command == 'record':
            receipt = record_result(args.product_root, args.packet_relative, args.output, status=args.status,
                                    failure_code=args.failure_code, attempts=args.attempts, native_metadata=args.native_metadata)
            summary = {k: receipt[k] for k in ('status', 'requested_model', 'actual_model', 'model_verification', 'execution_verified', 'receipt_digest')}
        else:
            summary = verify_result(args.product_root, args.result_relative)
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({'status': 'failed', 'error_type': type(exc).__name__}))
        return 1
    print(json.dumps(summary, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
