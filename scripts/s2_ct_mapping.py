"""Source-bound CT interpretation and S2 coverage. Never invent product facts."""
from __future__ import annotations

import copy
import hashlib
import json
import re

NEW_FIELDS = ('uses', 'audiences', 'scenarios', 'pain_points')
KNOWN = {'page_extracted', 'supplier_confirmed', 'sample_measured', 'user_confirmed', 'user_attested',
         'review_extracted'}  # R09 S1-R31: pain points taken from S1 review quotes, bound by voc_refs
# Written, then flagged in the report for the seller to confirm with the factory or a sample.
VERIFY_NOTE = '待核实：未经实物或厂家确认，上架前请核对'
PERFORMANCE = re.compile(r'waterproof|water.?resistant|puncture.?proof|wear.?resistant|防水|耐磨|防刺穿', re.I)
PLAN = {'uses': ['highlight', 'bullet_2', 'qa'], 'audiences': ['bullet_4', 'attributes.audience'],
        'scenarios': ['bullet_4', 'description', 'attributes.scenario'], 'pain_points': ['bullet_1', 'description'],
        'material': ['bullet_3', 'attributes.material'], 'dimensions': ['bullet_2', 'attributes.size'],
        'colors': ['title', 'attributes.color'], 'package_contents': ['bullet_5', 'qa'],
        'features': ['highlight', 'bullet_1'], 'structure': ['highlight', 'bullet_1']}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def tokens(text):
    return [w[:-3]+'y' if w.endswith('ies') else w[:-2] if w.endswith(('ches', 'shes')) else
            w[:-1] if w.endswith('s') and not w.endswith('ss') else w
            for w in re.findall(r'[a-z0-9]+', str(text).casefold())]


def matches(text, term):
    a, b = tokens(text), tokens(term)
    return bool(b) and any(a[i:i+len(b)] == b for i in range(len(a)-len(b)+1))


def as_list(value):
    return value if isinstance(value, list) else [] if value is None or value == '' else [value]


def ct_nodes(ct):
    """Keep field IDs stable; source dictionaries and historical notes are untouched."""
    for key in ('material', 'dimensions', 'weight', 'colors', 'package_contents', 'compatible_with', *NEW_FIELDS):
        node = ct.get(key) or {}
        if not isinstance(node, dict): node = {'value': node, 'source_status': 'missing'}
        yield key, node
    structure = ct.get('structure') or {}
    if structure.get('mount'): yield 'structure.mount', structure['mount']
    for key, values in [('structure.features', structure.get('features') or []), ('features', ct.get('features') or [])]:
        if isinstance(values, dict):
            # {value: [...], source, source_status}: each listed feature inherits the node's source and status.
            shared = {k: v for k, v in values.items() if k != 'value'}
            values = [dict(shared, value=item) for item in as_list(values.get('value'))]
        for i, node in enumerate(values):
            yield f'{key}.{i}', node if isinstance(node, dict) else {'value': node, 'source_status': 'missing'}


def node_state(key, node):
    status = str(node.get('source_status') or node.get('status') or 'missing')
    value = node.get('value', node.get('main_cn', node.get('item_cn', node.get('label'))))
    source = node.get('source') or node.get('evidence_refs') or node.get('source_refs') or node.get('evidence')
    performance = bool(PERFORMANCE.search(str(value) + ' ' + str(node.get('en_terms', []))))
    state = 'missing'
    if value is not None and value != '' and value != [] and status != 'missing':
        # Scanned/inferred/target facts are written with a verify note, never held back as a gate.
        state = 'known' if status in KNOWN and source else 'illustrated' if status in {'image_extracted', 'user_selected_and_image_extracted'} and source else 'unverified'
    # Reviews/VOC must be explicitly source-bound; inferred pain points are written as unverified.
    if key == 'pain_points' and state in {'known', 'illustrated'} and not node.get('voc_refs'):
        state = 'unverified'
    tested = status == 'sample_measured' and bool(node.get('test_evidence') or node.get('evidence_refs'))
    if performance and not tested and state != 'missing': state = 'pending'
    permission = '禁止' if state in {'missing', 'pending'} else '弱化表述' if key == 'uses' and not tested else '可写'
    return state, permission, status, value, source


def listing_fields(listing):
    for sku in (listing or {}).get('skus', (listing or {}).get('variants', [])):
        sid = str(sku.get('sku') or sku.get('sku_id') or '')
        reviews = (sku.get('review_metadata') or sku.get('field_review') or {}).get('fields', {})
        reviews = reviews or sku.get('field_reviews') or {}
        for field in ['title', 'highlight', 'item_highlights', 'description', 'st', 'search_terms']:
            value = sku.get(field)
            if isinstance(value, dict): value = value.get('en') or value.get('text')
            if value: yield sid, field, str(value), reviews.get(field, {})
        for i, value in enumerate(sku.get('bullets') or [], 1):
            if isinstance(value, dict): value = value.get('en') or value.get('text')
            if value: yield sid, f'bullets.{i}', str(value), reviews.get(f'bullets.{i}', reviews.get(f'bullet_{i}', {}))
        for i, qa in enumerate(sku.get('qa') or []):
            yield sid, f'qa.{i}', str(qa.get('question', ''))+' '+str(qa.get('answer', '')), reviews.get(f'qa.{i}', {})
        for field, value in (sku.get('attributes') or {}).items():
            if value: yield sid, 'attributes.'+field, str(value), reviews.get('attributes.'+field, {})


def map_ct(ct, keywords, listing=None, *, previous=None):
    ct = ct.get('ct', ct)
    rows, placements = [], []
    fields = list(listing_fields(listing))
    for key, node in ct_nodes(ct):
        ref = 'CT:'+key
        state, permission, status, value, source = node_state(key, node)
        terms = as_list(node.get('en_terms') or node.get('main_en'))
        matched = [w for w in keywords if ref in w.get('ct_refs', []) or
                   any(matches(w.get('keyword', w.get('term', '')), term) for term in terms)]
        ids = [w.get('keyword_id', w.get('term')) for w in matched]
        covered_fields = []
        for sid, field, text, review in fields:
            if node.get('skus') and sid not in node['skus']: continue
            scoped = [w for w in matched if not w.get('skus') or sid in w['skus']]
            explicit = ref in review.get('ct_refs', []) or ref in review.get('fact_refs', [])
            # Evidence-backed field annotation or complete keyword occurrence is required.
            hit = explicit or any(matches(text, w.get('keyword', w.get('term', ''))) for w in scoped)
            if hit and state not in {'missing', 'pending'}:
                covered_fields.append({'sku': sid, 'field': field, 'ct_refs': [ref]})
                for word in scoped:
                    if matches(text, word.get('keyword', word.get('term', ''))):
                        placements.append({'sku': sid, 'field': field, 'keyword_id': word.get('keyword_id'), 'ct_ref': ref})
        reason = ('VOC 证据缺失' if key == 'pain_points' and not node.get('voc_refs') and state != 'unverified' else
                  '来源/事实待确认' if state in {'missing', 'pending'} else
                  '尚无对应关键词' if not ids else '文案尚未覆盖' if not covered_fields else '')
        rows.append({'ct_ref': ref, 'label': key, 'value': copy.deepcopy(value), 'source': copy.deepcopy(source),
                     'verify_note': VERIFY_NOTE if state == 'unverified' else '',
                     'source_status': status, 'source_sha256': node.get('sha256'), 'state': state,
                     'skus': copy.deepcopy(node.get('skus') or []), 'voc_refs': copy.deepcopy(node.get('voc_refs') or []), 'en_terms': terms,
                     'copy_permission': permission, 'fingerprint': fingerprint(node), 'keyword_ids': ids,
                     'keywords': [w.get('keyword', w.get('term')) for w in matched],
                     'field_plan': PLAN.get(key.split('.')[0], ['description']), 'fields': covered_fields,
                     'covered': bool(covered_fields), 'missing_reason': reason,
                     'rewrite_constraints': ['不写 fits all / 全适配；样品验证前只描述页面展示用途'] if key == 'uses' else []})
    old = {r['ct_ref']: r for r in (previous or {}).get('rows', [])}
    current = {r['ct_ref']: r for r in rows}
    changed = sorted(ref for ref in set(old) | set(current) if previous is not None and
                     old.get(ref, {}).get('fingerprint') != current.get(ref, {}).get('fingerprint'))
    affected = sorted({kid for ref in changed for row in [old.get(ref, {}), current.get(ref, {})] for kid in row.get('keyword_ids', [])})
    affected_fields = []
    for ref in changed:
        for row in [old.get(ref, {}), current.get(ref, {})]:
            for field in row.get('fields', []):
                if field not in affected_fields: affected_fields.append(field)
    return {'schema_version': 'r02.f6', 'rows': rows, 'gaps': [r['ct_ref'] for r in rows if r['missing_reason']],
            'changed_refs': changed, 'affected_keywords': affected, 'affected_fields': affected_fields,
            'comparison_status': 'compared' if previous is not None else 'first_mapping',
            'source_fingerprint': fingerprint(ct), 'placements': placements}
