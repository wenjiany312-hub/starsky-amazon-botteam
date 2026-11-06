"""Complete, gated single-source packages <-> persistent series assets.

No raw CSV normalizer may call this as proof of product qualification. Public
CLI consumers receive counts/links; full records are a script-only interface.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

HOME = Path(__file__).resolve().parents[1]
UNITS = {'searches/week': 'weekly', 'searches/month': 'monthly', 'weekly': 'weekly', 'monthly': 'monthly'}


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def source_identity(source):
    result = {key: copy.deepcopy(source[key]) for key in ['provider', 'subject', 'asin', 'asins', 'asin_scope', 'profile_id', 'sha256'] if key in source}
    result.setdefault('subject', 'unknown')
    result['reference'] = source.get('relative_path') or source.get('raw_path') or source.get('reference')
    if source.get('tool_name'): result['tool'] = source['tool_name']
    result['source_id'] = source['source_id']
    return result


def asset_records(package, *, sku=None):
    provider = package['source_mode']
    sources = {s['source_id']: s for s in package['data_sources']}
    guard = load_module('s2_asset_eligibility', HOME/'skills/_keyword_system_contract/scripts/keyword_eligibility.py')
    result = []
    for word in package['records']:
        observed = list(dict.fromkeys(link['source_id'] for link in word.get('lineage', []) if isinstance(link, dict)))
        metrics = word.get('metrics') or {}
        observed += [m['source_id'] for m in metrics.values() if isinstance(m, dict) and m.get('source_id') not in observed]
        for sid in observed:
            source = sources.get(sid)
            if not source or source.get('provider') != provider:
                raise ValueError('source-bound asset slice requires matching provider and source_id')
            src = source_identity(source)
            decision = guard.evaluate_record(word, guard.package_restrictions(package), strict_product_evidence=True,
                authoritative_context={'product_id': package.get('product_id'), 'marketplace': package['marketplace'], 'ct_mapping': (package.get('analysis') or {}).get('ct_mapping'), **({'sku': sku} if sku else {})})
            refs = list(dict.fromkeys(word.get('fact_refs', []) + word.get('ct_refs', [])))
            positive = bool(refs) and decision['listing_eligible']
            permission = {'copy': {'allowed': decision['copy_permission'] if positive else '禁止',
                                  'reason': '; '.join(decision['reasons']) or '本品词根与来源事实支持', 'fact_refs': refs},
                          'ads': {'allowed': '可投' if refs and decision['ad_eligible'] else '仅观察',
                                  'reason': '; '.join(decision['reasons']) or '本品词根候选，后续策略审核', 'fact_refs': refs}}
            name = 'brand' if word.get('disposition') == 'brand_isolated' else 'negative' if word.get('disposition') in {
                'compliance_negative', 'low_relevance_negative'} else 'master'
            observations = []
            for key, metric in metrics.items():
                if not isinstance(metric, dict) or metric.get('source_id') != sid: continue
                # A metric object is retained independently, not coalesced with another source/window.
                observation = {'source': src, 'observed_at': source.get('as_of'), 'period': copy.deepcopy(metric.get('period') or source.get('period')),
                               'metric_name': key, 'value': metric.get('value'), 'original_unit': metric.get('unit'),
                               'semantic': metric.get('semantic'), 'raw': metric.get('raw'), 'status': metric.get('status'),
                               'missing_reason': metric.get('missing_reason')}
                if key in {'weekly_search_volume', 'monthly_search_volume', 'search_volume'}:
                    observation['search_volume'] = metric.get('value')
                    observation['unit'] = UNITS.get(metric.get('unit'))
                    if metric.get('value') is not None and observation['unit'] is None:
                        # Preserve unknown units as raw observations; do not assert comparable volume.
                        observation['search_volume'] = None
                        observation['missing_reason'] = 'search_volume_unit_unknown'
                else:
                    observation[{'cpc_mid': 'cpc'}.get(key, key)] = metric.get('value')
                observations.append(observation)
            # Bundle only identical source/date/period/unit observations so E's
            # latest-metric projection keeps CPC/concentration alongside demand.
            groups = {}
            for observation in observations:
                key = json.dumps([observation.get('period'), observation.get('unit')], sort_keys=True)
                target = groups.setdefault(key, {'source': src, 'observed_at': observation['observed_at'],
                    'period': observation.get('period'), 'unit': observation.get('unit'), 'original_metrics': {}})
                target['original_metrics'][observation['metric_name']] = observation
                for field in ['search_volume', 'aba_rank', 'cpc', 'top3_click_share', 'top3_conversion_share']:
                    if field in observation: target[field] = observation[field]
            # Non-volume fields from the same export period may accompany each
            # explicitly-unit-bearing demand record, never another source/month.
            for target in groups.values():
                for other in groups.values():
                    if other.get('unit') is None and other['period'] == target['period']:
                        for field in ['aba_rank', 'cpc', 'top3_click_share', 'top3_conversion_share']:
                            if field in other: target[field] = other[field]
            observations = list(groups.values())
            row = {'term': word['keyword'], 'marketplace': package['marketplace'], 'list': name,
                   'chinese': word.get('cn_translation'), 'source': src, 'metrics': observations,
                   'semantic_tags': list(word.get('tags') or []), 'ct_refs': list(word.get('ct_refs') or []),
                   'permissions': permission, 'skus': list(word.get('skus') or []),
                   'notes': [copy.deepcopy(word.get('retention_reason') or {}), *copy.deepcopy(word.get('notes') or [])],
                   'lineage': [copy.deepcopy(link) for link in word.get('lineage', []) if link.get('source_id') == sid]}
            observed_effects = [obs for obs in word.get('effect_observations', [])
                                if (obs.get('source') or {}).get('source_id') == sid]
            if observed_effects:
                # Keep explicit empty dimensions as independent observations;
                # flattening with another nonempty dimension loses revocations.
                for observation in observed_effects:
                    for dimension in observation['effect_dimensions']:
                        event = copy.deepcopy(row)
                        event['effect_tags'] = [{dimension: tag[dimension], 'source': copy.deepcopy(tag.get('source') or src)}
                            for tag in observation['effect_tags'] if dimension in tag and tag[dimension] not in (None, '', [])]
                        event['effect_dimensions'] = [dimension]
                        event['effect_observed_at'] = observation.get('observed_at') or source.get('as_of')
                        result.append(event)
            else:
                result.append(row)
    return result


def validate_gates(package, input_path):
    contract_dir = HOME/'skills/_keyword_system_contract/scripts'
    gate_dir = contract_dir if (contract_dir/'s2_competitor_gate.py').is_file() else HOME.parents[1]/'release-overrides'
    if not (gate_dir/'s2_competitor_gate.py').is_file(): raise ValueError('asset gate modules unavailable')
    prior = list(sys.path)
    try:
        sys.path.insert(0, str(gate_dir))
        gate = load_module('s2_competitor_gate', gate_dir/'s2_competitor_gate.py')
        # Export gate imports the companion validator by its public name.
        previous = sys.modules.get('s2_competitor_gate'); sys.modules['s2_competitor_gate'] = gate
        try: exports = load_module('s2_asset_export_gate', gate_dir/'s2_export_intake_gate.py')
        finally:
            if previous is None: sys.modules.pop('s2_competitor_gate', None)
            else: sys.modules['s2_competitor_gate'] = previous
        result = gate.validate_package_gate(package, input_path)
        if result['status'] == 'pass': result = exports.validate_exports(package, input_path, result['confirmed_asins'])
    finally: sys.path[:] = prior
    if result['status'] != 'pass': raise ValueError('asset gate: '+'; '.join(result['errors'][:8]))


def merge_package(series, package, *, input_path, sku=None, now=None, validated=None):
    """validated: the pass result finalize just produced for this same package object, so the full
    19k-word validation is not run a second time before the write (it is re-run after the write)."""
    series = Path(series).resolve(); input_path = Path(input_path).resolve()
    if not input_path.is_relative_to(series) or not (series/'workflow_state.yaml').is_file():
        raise ValueError('asset gate: package must be in the real series root')
    validate_gates(package, input_path)
    for source in package.get('data_sources', []):
        relative = source.get('relative_path') or source.get('raw_path')
        original = (series/str(relative or '')).resolve()
        expected = str(source.get('sha256') or '').removeprefix('sha256:')
        if not relative or not original.is_relative_to(series) or not original.is_file() or hashlib.sha256(original.read_bytes()).hexdigest() != expected:
            raise ValueError('asset source hash/path mismatch: '+str(source.get('source_id')))
    for word in package.get('records', []):
        qualified = word.get('qualified_copy') or {}
        if qualified.get('allowed'):
            evidence = (series/str(qualified.get('source') or '')).resolve()
            if not evidence.is_relative_to(series) or not evidence.is_file() or hashlib.sha256(evidence.read_bytes()).hexdigest() != qualified.get('sha256'):
                raise ValueError('qualified CT source hash/path mismatch: '+str(word.get('keyword_id')))
    contract = load_module('s2_asset_contract', HOME/'skills/_keyword_system_contract/scripts/keyword_package_contract.py')
    checked = validated if (validated or {}).get('status') == 'pass' else contract.validate_package(
        package, package.get('source_mode'), series=series)
    if checked['status'] != 'pass': raise ValueError('incomplete keyword package: '+'; '.join(checked['errors'][:8]))
    kw = load_module('s2_asset_store', HOME/'scripts/keyword_asset.py')
    records=asset_records(package, sku=sku)
    scanner=load_module('s2_asset_h_scan',HOME/'scripts/compliance_scan.py')
    ip_rows,_=scanner.ip_entries(series)
    brands=[(row['term'],row['source']) for row in ip_rows if row.get('is_brand')]
    for word in package.get('records',[]):
        for brand in word.get('brand_hits') or []:
            if isinstance(brand,str):
                # finalize may target a new output path; bind the verified input
                # package and row lineage, not nonexistent future file bytes.
                brands.append((brand,{'reference':str(input_path),'package_hash':package.get('package_hash'),
                    'keyword_id':word.get('keyword_id'),'lineage':word.get('lineage') or []}))
    for term,source in brands:
        records.append({'term':term,'list':'brand','marketplace':package['marketplace'],
            'source':{'provider':'S1_IP_or_S2_brand_review','subject':'competitor',**source},
            'permissions':{'copy':{'allowed':'禁止','reason':'品牌隔离','fact_refs':[]},
                           'ads':{'allowed':'仅观察','reason':'品牌投放须单独审核，不从文案禁用推断许可','fact_refs':[]}},
            'notes':['源品牌词自动积累，未授予广告权限']})
    checked=scanner.scan_fields({str(i):row['term'] for i,row in enumerate(records)},series=series,evidence={
        'product_id':package.get('product_id'),'marketplace':package['marketplace'],'sku':sku})
    for i,row in enumerate(records):
        hits=[h for h in checked['hits'] if h['field']==str(i)]
        if any(h['level']=='block' for h in hits):
            row['permissions']['copy'].update(allowed='禁止',reason='H合规拦截：'+','.join(h['entry_id'] for h in hits if h['level']=='block'))
            # Audit only which rule blocked the term; the full layer sources are already pinned by layer_hashes and
            # copying them into every word made the asset grow by hundreds of MB per revision.
            row['notes'].append({'compliance_hits':[{k:h[k] for k in ('entry_id','level','found','reason') if k in h} for h in hits],
                                 'layer_hashes':checked['layer_hashes']})
    asset = kw.merge(series, records, sku=sku, now=now)
    # Script readback uses select, while each package keeps its own original metrics.
    selected = {(r['marketplace'], r['term_normalized']): r for rows in kw.select_many(series, sku=sku, readonly=True).values()
                for r in rows if r['marketplace'] == package['marketplace'].upper()}
    sliced = copy.deepcopy(package)
    for word in sliced['records']:
        row = selected.get((package['marketplace'].upper(), kw._normalize(word['keyword'])))
        if row:
            word['asset_ref'] = {'marketplace': row['marketplace'], 'term_normalized': row['term_normalized'], 'first_seen': row['first_seen'],
                                 'revision': asset['revision'], 'source_ids': [link['source_id'] for link in word['lineage']]}
            word['asset_ref']['permissions'] = copy.deepcopy(row['permissions'])
    active = {(r['marketplace'], r['term_normalized']) for rows in asset['lists'].values() for r in rows}
    sliced['series_asset'] = {'path': '_词库资产/keyword_master.yaml', 'revision': asset['revision'],
        'unique_words': len(active), 'historical_pool_records': sum(len(rows) for rows in asset['lists'].values()),
        'slice_source': package['source_mode'], 'slice_count': len(sliced['records']),
        'context_policy': 'Use bounded select or human report links; do not dump full master into model context.'}
    return sliced
