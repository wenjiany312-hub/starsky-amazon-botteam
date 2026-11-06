#!/usr/bin/env python3
"""Persistent series keyword asset. No network access or Amazon mutations.

``keyword_master.yaml`` is the transaction authority (including all four lists).
Sidecars are human-readable, revisioned projections, never competing authorities.
Absent sidecars are harmless; malformed canonical data fails closed.
"""
from __future__ import annotations

import argparse
import builtins
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unicodedata

import yaml
import importlib.util

# Safe C implementations preserve YAML scalar types and large-asset throughput.
SAFE_LOADER = getattr(yaml, 'CSafeLoader', yaml.SafeLoader)
SAFE_DUMPER = getattr(yaml, 'CSafeDumper', yaml.SafeDumper)

SCHEMA_PATH = Path(__file__).resolve().parents[1] / 'templates/keyword_asset_schema.yaml'
LIST_FILES = {'negative': 'negative_terms.yaml', 'brand': 'brand_terms.yaml', 'asin': 'asin_targets.yaml'}


@lru_cache(maxsize=1)
def _schema():
    return yaml.load(SCHEMA_PATH.read_text(encoding='utf-8'), Loader=SAFE_LOADER)


def _day(value=None):
    if value is None:
        return date.today().isoformat()
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return date.fromisoformat(str(value)[:10]).isoformat()


def _normalize(term):
    return ' '.join(unicodedata.normalize('NFKC', str(term)).strip().strip('"\u201c\u201d').casefold().split())


def _path(series, relative):
    root = Path(series).resolve()
    target = root / '_词库资产' / relative
    # Validate the stable parent first so lock creation/removal is not part
    # of path canonicalization during concurrent Windows imports.
    resolved = target.parent.resolve() / target.name
    if target.is_symlink():
        resolved = target.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError('keyword asset path escapes series root')
    return target


SOURCE_KEYS = ('source', 'sources', 'references')


def _source_id(source):
    return hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()[:16]


def _encode_sources(value, index):
    """Store each distinct source object once; rows keep {'$src': id}. Every metric used to embed the full
    source (19 ASINs, path, hash), which made a 19k-word asset 700 MB and every step re-parse it."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in SOURCE_KEYS and isinstance(item, dict) and item:
                sid = _source_id(item); index.setdefault(sid, _encode_sources(item, index)); out[key] = {'$src': sid}
            elif key in SOURCE_KEYS and isinstance(item, builtins.list):
                refs = []
                for element in item:
                    if isinstance(element, dict) and element:
                        sid = _source_id(element); index.setdefault(sid, _encode_sources(element, index)); refs.append({'$src': sid})
                    else:
                        refs.append(_encode_sources(element, index))
                out[key] = refs
            else:
                out[key] = _encode_sources(item, index)
        return out
    if isinstance(value, builtins.list):
        return [_encode_sources(item, index) for item in value]
    return value


def _decode_sources(value, index, memo=None):
    memo = {} if memo is None else memo
    if isinstance(value, dict):
        if len(value) == 1 and '$src' in value:
            sid = value['$src']
            if sid not in index:
                raise ValueError('keyword asset source reference missing: ' + str(sid))
            if sid not in memo:
                memo[sid] = _decode_sources(index[sid], index, memo)
            # Shared on purpose: rows are deep-copied before any change (merge, _source, select_many),
            # and copying here cost ~95 s per finalize on a 19k-word asset.
            return memo[sid]
        return {key: _decode_sources(item, index, memo) for key, item in value.items()}
    if isinstance(value, builtins.list):
        return [_decode_sources(item, index, memo) for item in value]
    return value


def _parse(content):
    """JSON first (assets are written as JSON, which is also valid YAML); older YAML assets still load."""
    text = content.decode('utf-8-sig') if isinstance(content, bytes) else content
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        value = yaml.load(text, Loader=SAFE_LOADER)
    if isinstance(value, dict) and isinstance(value.get('source_index'), dict):
        index = value.pop('source_index')
        value = _decode_sources(value, index)
    return value


def _dump_asset(value):
    index = {}
    body = _encode_sources(value, index)
    if index:
        body = {**body, 'source_index': index}
    return json.dumps(body, ensure_ascii=False, separators=(',', ':'), default=str) + '\n'


def _read(path):
    if not path.exists():
        return None
    value = _parse(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError(f'invalid asset mapping: {path.name}')
    return value


def _checked_asset(value):
    if value is None:
        return {'schema_version': '1.0', 'revision': 0, 'records': [], 'lists': {k: [] for k in ['master', *LIST_FILES]}}
    if value.get('schema_version') != '1.0' or not isinstance(value.get('lists'), dict):
        raise ValueError('unsupported or malformed keyword asset schema')
    for name in ['master', *LIST_FILES]:
        if not isinstance(value['lists'].get(name), builtins.list):
            raise ValueError(f'invalid asset list: {name}')
    _reconcile_legacy_classifications(value)
    return value


def _load(series):
    return _checked_asset(_read(_path(series, 'keyword_master.yaml')))


def _operation_runtime():
    spec = importlib.util.spec_from_file_location('_asset_rules_runtime', Path(__file__).with_name('platform_rules.py'))
    rules = importlib.util.module_from_spec(spec); spec.loader.exec_module(rules)
    return rules.operation_runtime()


class AssetSnapshot:
    """Read-only selector views derived from one parsed canonical byte snapshot."""
    def __init__(self, path, content):
        self.path = str(path)
        self.sha256 = hashlib.sha256(content).hexdigest() if content is not None else None
        self.__asset = _checked_asset(_parse(content) if content is not None else None)

    def select_many(self, *, lists=('master', 'negative', 'brand'), readonly=False, **filters):
        """readonly=True returns the cached projection itself, for callers that only read it."""
        runtime = _operation_runtime()
        views = {name: runtime.cached(('keyword_asset_projection',self.path,self.sha256,name,
            json.dumps(filters,sort_keys=True,default=str)),
            lambda name=name: _select(self.__asset, list=name, **filters)) for name in lists}
        return views if readonly else deepcopy(views)

    def summary(self):
        return {'path':self.path,'sha256':self.sha256,'revision':self.__asset['revision'],
                'counts':{name:len(rows) for name,rows in self.__asset['lists'].items()}}

    def seasonal_rows(self, sku=None):
        """Small display projection; never copy full histories/effect observations."""
        result = []
        tags = {'季节','礼品','节日','礼品 / 节日','seasonal','season','gift'}
        for row in self.__asset['lists']['master']:
            if not tags.intersection(row['semantic_tags']) or sku is not None and row['skus'] and sku not in row['skus']:
                continue
            thin = {key: row.get(key) for key in ('term','marketplace','skus','semantic_tags','seasonal','sku_states','list','lifecycle')}
            thin.update(effect_tags=[], metrics=_current_metrics(row), ct_refs=[], placements=[])
            projected = _project_state(thin, sku)
            result.append({key: projected[key] for key in ('term','marketplace','skus','semantic_tags','seasonal','metrics','placements','permissions')})
        return result

    def seasonal_projections(self):
        """Group all seasonal SKU projections from this one native parse."""
        tags = {'季节','礼品','节日','礼品 / 节日','seasonal','season','gift'}
        skus = {sku for row in self.__asset['lists']['master'] if tags.intersection(row['semantic_tags']) for sku in row['skus']}
        return {sku:self.seasonal_rows(sku) for sku in [None,*sorted(skus)]}


def read_snapshot(series):
    """Current operation only; standalone calls always read current file bytes."""
    path = _path(series, 'keyword_master.yaml').resolve()
    runtime = _operation_runtime()
    return runtime.cached(('keyword_asset_snapshot', str(path)),
        lambda: AssetSnapshot(path, runtime.read_bytes(path)))


@contextmanager
def _lock(series):
    path = _path(series, '.merge.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + 15
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            break
        except (FileExistsError, PermissionError):
            # Windows can report EACCES while a previously released lock is
            # pending deletion. It is still contention, never permission to
            # bypass serialization; persistent ACL denial also times out.
            if time.monotonic() >= deadline:
                raise RuntimeError('asset is locked; retry after active import completes (never delete an active lock)')
            time.sleep(0.02)
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        path.unlink(missing_ok=True)



def _atomic(path, value, *, text=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.kw-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(value if text else yaml.dump(value, Dumper=SAFE_DUMPER, allow_unicode=True, sort_keys=False))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def _is_audit_note(note):
    return isinstance(note, dict) and 'compliance_hits' in note and 'code' not in note


def _unique(old, new):
    result = deepcopy(old)
    for item in new:
        if item not in result:
            result.append(deepcopy(item))
    return result


def _source(value):
    if value is None:
        return {'provider': 'manual', 'subject': 'unknown'}
    if isinstance(value, str):
        return {'provider': value, 'subject': 'unknown'}
    result = deepcopy(value)
    result.setdefault('provider', 'manual')
    result.setdefault('subject', 'unknown')
    if result['subject'] not in ['own', 'competitor', 'market', 'unknown']:
        raise ValueError('source subject must distinguish own / competitor / market / unknown')
    return result


def _permissions(raw, old, name):
    defaults = {channel: {'allowed': state, 'reason': 'pending factual and compliance review', 'fact_refs': []} for channel, state in [('copy', '禁止'), ('ads', '仅观察')]}
    result = deepcopy(old or defaults)
    for channel, values in (raw or {}).items():
        if channel not in defaults or not isinstance(values, dict):
            raise ValueError('permission requires copy/ads mapping')
        allowed = values.get('allowed')
        valid = _schema()['permission_values'][channel]
        if allowed not in valid:
            raise ValueError(f'invalid {channel} permission')
        if allowed in ['可写', '弱化表述', '可投', '仅定投'] and (not values.get('reason') or not values.get('fact_refs')):
            raise ValueError('positive permission requires reason and fact_refs')
        result[channel] = deepcopy(values)
    if name in ['brand', 'negative', 'asin']:
        result['copy'] = {'allowed': '禁止', 'reason': f'{name} list isolation', 'fact_refs': []}
    if name == 'negative':
        result['ads'] = {'allowed': '否定', 'reason': 'negative list', 'fact_refs': []}
    return result


def _identity(source):
    """Observation identity excludes changing files/hashes, retains ASIN scope."""
    return json.dumps({key: source.get(key) for key in ['provider', 'subject', 'asin', 'asins', 'asin_scope', 'profile_id']}, sort_keys=True, ensure_ascii=False)


def _stream_identity(source):
    return (_identity(source), source.get('tool'))


def _effect_gaps(row):
    return [{'source_identity': event['source_identity'], 'observed_at': event['observed_at'], 'requires_reimport': True, 'reason': 'empty observation has no identifiable dimension scope', 'action': 'Reimport the originating tool with source.tool and explicit effect_dimensions; old labels remain unconfirmed, not globally revoked.'} for event in row.get('effect_observations', []) if not event['tags'] and not event.get('observed_dimensions')]


def _effect_dimensions(raw, source, tags, observations):
    known = ['sif_tags', 'entry_signal', 'aba_position', 'traffic_dependency']
    if 'effect_dimensions' in raw:
        dimensions = raw['effect_dimensions']
        if not isinstance(dimensions, builtins.list) or not dimensions or any(value not in known for value in dimensions):
            raise ValueError('effect_dimensions must name observed effect label dimensions')
        return dimensions
    if tags:
        return [dimension for dimension in known if any(dimension in tag for tag in tags)]
    dimensions = _schema().get('effect_stream_dimensions', {}).get(source.get('tool'), [])
    for event in observations:
        if event.get('source') and _stream_identity(event['source']) == _stream_identity(source):
            dimensions = _unique(dimensions, event.get('observed_dimensions', []))
        for tag in event['tags']:
            if _stream_identity(tag['source']) == _stream_identity(source):
                dimensions = _unique(dimensions, [dimension for dimension in known if dimension in tag])
    return dimensions


def _current_effects(row):
    # Product identity pairs evidence across tools; independent dimensions
    # decide which observations supersede others. An entry-signal call cannot
    # erase effect/ABA facts that it did not observe.
    latest = {}
    barriers = {}
    for gap in _effect_gaps(row):
        identity = gap['source_identity']
        barriers[identity] = max(barriers.get(identity, ''), gap['observed_at'])
    for event in sorted(row.get('effect_observations', []), key=lambda item: item['observed_at']):
        identity = event['source_identity']
        if not event['tags']:
            # Empty results retire only the explicitly observed dimensions
            # owned by this stream. Older tool-less positives have product/
            # dimension scope but no independent stream ownership; a scoped
            # observation supersedes them too. Other named streams survive.
            # Pre-scope legacy empties remain uncertain.
            for dimension in event.get('observed_dimensions', []):
                key = (identity, dimension)
                if event.get('source'):
                    latest[key] = [tag for tag in latest.get(key, []) if tag['source'].get('tool') and _stream_identity(tag['source']) != _stream_identity(event['source'])]
            continue
        dimensions = {field for tag in event['tags'] for field in tag if field not in ['source', 'observed_at']}
        for dimension in dimensions:
            status = 'needs_reimport' if event['observed_at'] <= barriers.get(identity, '') else 'current'
            latest[(identity, dimension)] = [{dimension: tag[dimension], 'source': tag['source'], 'observed_at': event['observed_at'], 'status': status} for tag in event['tags'] if dimension in tag]
    return [tag for values in latest.values() for tag in values]


def _current_metrics(row):
    latest = {}
    for metric in row['metrics']:
        key = (_identity(metric['source']), metric.get('unit'))
        rank = (metric.get('observed_at', ''), str(metric.get('period', '')))
        if key not in latest or rank >= latest[key][0]:
            latest[key] = (rank, metric)
    return [value[1] for value in latest.values()]


def _project_state(row, sku=None):
    """Return SKU facts, or a conservative aggregate for unscoped consumers."""
    result = deepcopy(row)
    result['current_effect_tags'] = _current_effects(row)
    result['effect_gaps'] = _effect_gaps(row)
    if row.get('lifecycle') != '否定' and any(tag['status'] == 'needs_reimport' for tag in result['current_effect_tags']):
        result['lifecycle'] = '待验证'
        result['lifecycle_reason'] = 'legacy empty observation requires scoped reimport'
    states = row.get('sku_states', {})
    if sku is not None:
        state = states.get(sku, states.get('__series__'))
        selected = [state] if state else []
    else:
        keys = row['skus'] or ['__series__']
        selected = [states[key] for key in keys if key in states]
    if not selected:
        selected = [{'permissions': _permissions(None, None, row['list']), 'ct_refs': [], 'placements': []}]
    result['ct_refs'] = []
    result['placements'] = []
    for state in selected:
        for field in ['ct_refs', 'placements']:
            result[field] = _unique(result[field], state[field])
    result['permissions'] = {}
    for channel, order in [('copy', ['可写', '弱化表述', '禁止']), ('ads', ['可投', '仅定投', '仅观察', '否定'])]:
        values = [state['permissions'][channel] for state in selected]
        result['permissions'][channel] = deepcopy(max(values, key=lambda value: order.index(value['allowed'])))
        if all(value['allowed'] == values[0]['allowed'] for value in values):
            result['permissions'][channel]['fact_refs'] = []
            for value in values:
                result['permissions'][channel]['fact_refs'] = _unique(result['permissions'][channel]['fact_refs'], value.get('fact_refs', []))
    return result


def _update_sku_states(row, previous, raw, incoming_skus, day):
    states = row.setdefault('sku_states', {})
    history = row.setdefault('state_history', [])
    if previous and not states:
        # Old multi-SKU records had no evidence of which child was reviewed.
        # Preserve the original state as history and require a scoped review.
        legacy = {field: deepcopy(previous.get(field, [] if field != 'permissions' else _permissions(None, None, row['list']))) for field in ['permissions', 'placements', 'ct_refs']}
        history.append({'observed_at': previous['last_updated'], 'scope': 'legacy_unresolved', **legacy})
        keys = previous['skus'] or ['__series__']
        for key in keys:
            states[key] = deepcopy(legacy) if len(keys) == 1 else {'permissions': _permissions(None, None, row['list']), 'placements': [], 'ct_refs': []}
    targets = incoming_skus or (['__series__'] if not row['skus'] else [])
    if raw.get('series_wide'):
        targets = ['__series__']
    if not targets and any(field in raw for field in ['permissions', 'placements', 'ct_refs']):
        raise ValueError('SKU-scoped facts require sku/skus, or explicit series_wide')
    for key in targets:
        old = states.get(key)
        state = deepcopy(old) if old else {'permissions': _permissions(None, None, row['list']), 'placements': [], 'ct_refs': []}
        state['permissions'] = _permissions(raw.get('permissions'), state['permissions'], row['list'])
        for field in ['placements', 'ct_refs']:
            state[field] = _unique(state[field], raw.get(field, []))
        state['observed_at'] = day
        if old and day < old.get('observed_at', previous['last_updated'] if previous else day):
            history.append({'observed_at': day, 'scope': key, 'status': 'superseded_import', **state})
            continue
        if old and old != state:
            history.append({'observed_at': day, 'scope': key, **deepcopy(old)})
        states[key] = state
    for state in states.values():
        for position in state['placements']:
            if position not in _schema()['placements']['S2']:
                raise ValueError(f'unsupported S2 placement (future S3/S4 reserved only): {position}')
    projection = _project_state(row)
    for field in ['permissions', 'placements', 'ct_refs']:
        row[field] = projection[field]


def _apply_classification(asset, key, name, day, source, *, fresh_review=False):
    classifications = asset.setdefault('classifications', {})
    identity = json.dumps(key, ensure_ascii=False)
    current = classifications.get(identity)
    event = {'list': name, 'observed_at': day, 'source': _source(source)}
    history = asset.setdefault('classification_history', [])
    entry = {'key': builtins.list(key), **event}
    # The transaction owns this asset; copy only the new event, not the entire
    # growing history on every keyword (quadratic deep-copy at real export size).
    if entry not in history:
        history.append(deepcopy(entry))
    restoring = current is not None and current['list'] != 'master' and name == 'master'
    if (current is None or day >= current['observed_at']) and (not restoring or fresh_review):
        classifications[identity] = event
        current = event
    for rows in asset['lists'].values():
        for row in rows:
            if (row['marketplace'], row['term_normalized']) != key:
                continue
            if not row.get('sku_states'):
                _update_sku_states(row, deepcopy(row), {}, [], day)
            row['active_classification'] = current['list']
            if current['list'] not in ['brand', 'negative', 'asin']:
                continue
            for scope, state in row.get('sku_states', {}).items():
                restricted = _permissions(None, state['permissions'], current['list'])
                if current['list'] == 'brand' and row['list'] != 'brand':
                    restricted['ads'] = {'allowed': '仅观察', 'reason': 'brand advertising requires separate brand-pool review', 'fact_refs': []}
                if restricted != state['permissions']:
                    row.setdefault('state_history', []).append({'observed_at': day, 'scope': scope, **deepcopy(state)})
                    state['permissions'] = restricted
                    state['observed_at'] = max(day, state.get('observed_at', day))
            projection = _project_state(row)
            row['permissions'] = projection['permissions']
            if current['list'] == 'negative':
                row['lifecycle'], row['lifecycle_reason'] = '否定', 'current cross-list negative classification'


def _reconcile_legacy_classifications(asset):
    """Resolve old overlapping pools BEFORE any legacy approval is restored.

    Old storage lacks an authoritative current classification. Existing
    restrictive pools therefore win until an explicit fresh review. Reading
    performs this reconciliation in memory; the next merge persists it.
    """
    classifications = asset.setdefault('classifications', {})
    priority = {'master': 0, 'asin': 1, 'brand': 2, 'negative': 3}
    candidates = {}
    for name, rows in asset['lists'].items():
        for row in rows:
            key = (row['marketplace'], row['term_normalized'])
            if json.dumps(key, ensure_ascii=False) in classifications:
                continue
            entry = candidates.setdefault(key, {'list': name, 'date': row['last_updated']})
            if priority[name] > priority[entry['list']]:
                entry['list'] = name
            entry['date'] = max(entry['date'], row['last_updated'])
    for key, entry in candidates.items():
        _apply_classification(asset, key, entry['list'], entry['date'], {'provider': 'asset_migration', 'subject': 'unknown', 'reference': 'keyword_master.yaml'})


def _lifecycle(row, thresholds):
    if row['list'] == 'negative' or '否定' in row['semantic_tags']:
        return '否定', 'negative classification'
    if row.get('lifecycle') == '待验证' and row.get('lifecycle_reason') != 'legacy empty observation requires scoped reimport':
        return '待验证', 'explicit pending verification'
    current_tags = _current_effects(row)
    if any(tag['status'] == 'needs_reimport' for tag in current_tags):
        return '待验证', 'legacy empty observation requires scoped reimport'
    signals = [t.get('entry_signal') for t in current_tags]
    metrics = row['metrics']
    current_metrics = _current_metrics(row)
    if any(metric.get('top3_click_share') is not None and metric['top3_click_share'] >= thresholds['red_click_share_gte'] for metric in current_metrics):
        return '红海', 'top3 click concentration at or above category red threshold'
    competitive_sources = {_identity(tag['source']) for tag in current_tags if tag.get('entry_signal') == '竞争激烈'}
    if any(_identity(metric['source']) in competitive_sources and metric.get('cpc') is not None and metric.get('category_median_cpc') is not None and metric['cpc'] > metric['category_median_cpc'] for metric in current_metrics):
        return '红海', 'SIF competitive signal and CPC above category median'
    # Decline evidence must be consecutive months from the SAME source/ASIN and unit.
    groups = {}
    for metric in metrics:
        period = str(metric.get('period', ''))
        if len(period) == 7 and metric.get('yoy_growth') is not None:
            try:
                point = date.fromisoformat(period + '-01')
            except ValueError:
                continue
            key = json.dumps([metric['source'], metric.get('unit')], sort_keys=True)
            groups.setdefault(key, {})[point.year * 12 + point.month] = metric['yoy_growth']
    if not row['seasonal'].get('rise_months') and not row['seasonal'].get('peak_months'):
        count = int(thresholds['decline_consecutive_months'])
        for points in groups.values():
            end = max(points)
            if all(points.get(end - n, 0) < 0 for n in range(count)):
                return '衰退', 'consecutive source-bound nonseasonal monthly YoY declines'
    if any(signal in ['优质机会', '可切入'] for signal in signals):
        return '蓝海', 'original SIF opportunity entry signal'
    if row.get('relevance') in ['high', '高'] and any(metric.get('yoy_growth') is not None and metric['yoy_growth'] >= 0 and metric.get('top3_click_share') is not None and metric['top3_click_share'] < thresholds['blue_click_share_lt'] and metric.get('cpc') is not None and metric.get('category_median_cpc') is not None and metric['cpc'] <= metric['category_median_cpc'] for metric in current_metrics):
        return '蓝海', 'high relevance, nonnegative YoY, low concentration, CPC at/below category median'
    if row.get('lifecycle') == '成长':
        return '成长', 'explicit reviewed growth classification'
    return '新发现', 'insufficient evidence for other lifecycle states'


def _upsert(raw, previous, source, day, sku, thresholds):
    term = raw.get('term', raw.get('keyword', raw.get('normalized_keyword', '')))
    norm = _normalize(term)
    marketplace = str(raw.get('marketplace', '')).strip().upper()
    if not norm or not marketplace:
        raise ValueError('term and marketplace are required')
    name = raw.get('list', 'master')
    if name not in ['master', *LIST_FILES]:
        raise ValueError('unknown keyword list')
    row = deepcopy(previous) if previous else {'term': str(term).strip(), 'term_normalized': norm, 'marketplace': marketplace, 'first_seen': day, 'chinese': None, 'sources': [], 'metrics': [], 'effect_tags': [], 'semantic_tags': [], 'seasonal': {'rise_months': [], 'peak_months': [], 'trough_months': [], 'strength': None, 'source': None}, 'placements': [], 'skus': [], 'ct_refs': [], 'notes': [], 'lineage': [], 'history': []}
    src = _source(raw.get('source', source))
    row['list'] = name
    row['last_updated'] = max(day, row.get('last_updated', day))
    row['sources'] = _unique(row['sources'], [src])
    observations = []
    for value in raw.get('metrics', []):
        metric = deepcopy(value)
        if metric.get('search_volume') is not None and metric.get('unit') not in ['weekly', 'monthly']:
            raise ValueError('search_volume must carry weekly/monthly unit')
        metric['source'] = _source(metric.get('source', src))
        metric.setdefault('observed_at', day)
        for field in ['search_volume', 'unit', 'aba_rank', 'cpc', 'top3_click_share', 'top3_conversion_share']:
            metric.setdefault(field, None)
        observations.append(metric)
    row['metrics'] = _unique(row['metrics'], observations)
    tags = []
    for value in raw.get('effect_tags', []):
        tag = deepcopy(value)
        tag['source'] = _source(tag.get('source', src))
        tags.append(tag)
    row['effect_tags'] = _unique(row['effect_tags'], tags)
    observations = row.setdefault('effect_observations', [])
    groups = {}
    for tag in tags:
        groups.setdefault(_stream_identity(tag['source']), []).append(tag)
    if 'effect_tags' in raw and not tags:
        groups[_stream_identity(src)] = []
    for (identity, tool), group in groups.items():
        event_source = group[0]['source'] if group else src
        dimensions = _effect_dimensions(raw, event_source, group, observations)
        observations = _unique(observations, [{'observed_at': _day(raw.get('effect_observed_at', day)), 'source_identity': identity, 'source': deepcopy(event_source), 'observed_dimensions': dimensions, 'tags': group}])
    row['effect_observations'] = observations
    row['current_effect_tags'] = _current_effects(row)
    row['effect_gaps'] = _effect_gaps(row)
    for field in ['semantic_tags', 'notes', 'lineage']:
        row.setdefault(field, [])
        if field == 'notes' and any(_is_audit_note(note) for note in raw.get('notes', [])):
            # A new compliance audit replaces the previous one; each revision used to add another copy.
            row['notes'] = [note for note in row['notes'] if not _is_audit_note(note)]
        row[field] = _unique(row[field], raw.get(field, []))
    incoming_skus = raw.get('skus', [])
    if sku is not None:
        incoming_skus = _unique(incoming_skus, [sku])
    # An existing unrestricted record remains series-wide; a newly SKU-scoped
    # record accumulates child SKUs without applying to unrelated children.
    if previous is None:
        row['skus'] = deepcopy(incoming_skus)
    elif row['skus'] and incoming_skus:
        row['skus'] = _unique(row['skus'], incoming_skus)
    elif not incoming_skus and raw.get('series_wide'):
        row['skus'] = []
    for field in ['chinese', 'seasonal', 'relevance', 'target_type', 'lifecycle']:
        if field in raw:
            if field == 'seasonal':
                row[field].update(deepcopy(raw[field] or {}))
            else:
                row[field] = deepcopy(raw[field])
    _update_sku_states(row, previous, raw, incoming_skus, day)
    row['lifecycle'], row['lifecycle_reason'] = _lifecycle(row, thresholds)
    signature = hashlib.sha256(json.dumps([raw, src, sku], ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    row['history'] = _unique(row['history'], [{'date': day, 'source': src, 'input_sha256': signature}])
    return row


def merge(series, records, *, source=None, now=None, sku=None) -> dict:
    """Upsert into a series, serializing concurrent read-modify-write operations.

    Validate the complete batch before committing. Original observations remain
    append-only and duplicate imports do not multiply identical observations.
    Explicit permissions require source-bound fact references from F/H.
    """
    day = _day(now)
    records = builtins.list(records)
    schema = _schema()
    # Validate every intended target before creating data (including symlinks).
    destinations = ['keyword_master.yaml', *LIST_FILES.values(), 'placement_map.yaml', 'compliance_custom.yaml', f'snapshots/{day[:7]}.yaml', f'词库体检_{day[:7]}.md', 'lifecycle_thresholds.yaml']
    for relative in destinations:
        _path(series, relative)
    with _lock(series):
        asset = _load(series)
        thresholds = dict(schema['lifecycle_thresholds'])
        override = _read(_path(series, 'lifecycle_thresholds.yaml')) or {}
        thresholds.update({k: v for k, v in override.items() if k in thresholds and isinstance(v, (int, float)) and not isinstance(v, bool)})
        for raw in records:
            if not isinstance(raw, dict):
                raise ValueError('keyword record must be a mapping')
            name = raw.get('list', 'master')
            if name not in asset['lists']:
                raise ValueError('unknown keyword list')
            rows = asset['lists'][name]
            norm = _normalize(raw.get('term', raw.get('keyword', raw.get('normalized_keyword', ''))))
            key = (str(raw.get('marketplace', '')).strip().upper(), norm)
            index = next((n for n, r in enumerate(rows) if (r['marketplace'], r['term_normalized']) == key), None)
            row = _upsert(raw, rows[index] if index is not None else None, source, day, sku, thresholds)
            first_dates = [existing['first_seen'] for pool in asset['lists'].values() for existing in pool if (existing['marketplace'], existing['term_normalized']) == key]
            if first_dates:
                row['first_seen'] = min([row['first_seen'], *first_dates])
            if index is None:
                rows.append(row)
            else:
                rows[index] = row
            fresh_review = any(permission.get('allowed') in ['可写', '弱化表述', '可投', '仅定投'] for permission in raw.get('permissions', {}).values())
            _apply_classification(asset, key, name, day, raw.get('source', source), fresh_review=fresh_review)
        asset['records'] = asset['lists']['master']
        asset['counts'] = {name: len(rows) for name, rows in asset['lists'].items()}
        asset['counts']['total'] = sum(asset['counts'].values())
        asset['revision'] += 1
        asset['updated_at'] = day
        asset['lifecycle_thresholds'] = thresholds
        # The master alone is the commit point. Views carry the revision so a
        # crash between their replacements never masquerades as one transaction.
        _atomic(_path(series, 'keyword_master.yaml'), _dump_asset(asset), text=True)
        for name, filename in LIST_FILES.items():
            _atomic(_path(series, filename), _dump_asset({'schema_version': '1.0', 'revision': asset['revision'], 'authority': 'keyword_master.yaml', 'records': asset['lists'][name]}), text=True)
        placements = [{'term': r['term'], 'marketplace': r['marketplace'], 'skus': [] if scope == '__series__' else [scope], 'positions': state['placements']} for r in asset['records'] for scope, state in r.get('sku_states', {}).items() if state['placements']]
        _atomic(_path(series, 'placement_map.yaml'), {'revision': asset['revision'], 'authority': 'keyword_master.yaml', 'placements': placements, 'available': schema['placements']['S2'], 'reserved': {s: schema['placements'][s] for s in ['S3', 'S4']}, 'excluded': schema['excluded_placements']})
        custom = _path(series, 'compliance_custom.yaml')
        if not custom.exists():
            _atomic(custom, {'schema_version': '1.0', 'terms': []})
        _atomic(_path(series, f'snapshots/{day[:7]}.yaml'), _dump_asset(asset), text=True)
        missing = [r['term'] for r in asset['records'] if not r['metrics'] or all(m.get('search_volume') is None for m in r['metrics'])]
        unique_words = len({(r['marketplace'], r['term_normalized']) for rows in asset['lists'].values() for r in rows})
        report = f'# 词库体检 {day[:7]}\n\n资产版本：{asset["revision"]}。唯一词数：{unique_words}；含分类历史的池记录：{sum(len(v) for v in asset["lists"].values())}。\n\n'
        report += '\n'.join(f'- {r["term"]}（{r["term_normalized"]}）：{r["lifecycle"]}；文案{r["permissions"]["copy"]["allowed"]}；广告{r["permissions"]["ads"]["allowed"]}' for r in asset['records'])
        report += '\n\n搜索量缺口：' + ('、'.join(missing) if missing else '无') + '。不同来源、ASIN、周/月口径不合计。\n'
        _atomic(_path(series, f'词库体检_{day[:7]}.md'), report, text=True)
        return deepcopy(asset)


def _matches(value, desired):
    if desired is None:
        return True
    desired = [desired] if isinstance(desired, str) else desired
    actual = value if isinstance(value, builtins.list) else [value]
    return bool(set(actual) & set(desired))


def select(series, sku=None, semantic=None, copy_allowed=None, ads_allowed=None, entry_signal=None, sif_tag=None, lifecycle=None, not_placed_in=None, list='master', limit=None) -> builtins.list[dict]:
    """Read normalized records; never infer product performance from competitor tags."""
    return _select(_load(series), sku=sku, semantic=semantic, copy_allowed=copy_allowed, ads_allowed=ads_allowed,
                   entry_signal=entry_signal, sif_tag=sif_tag, lifecycle=lifecycle, not_placed_in=not_placed_in, list=list, limit=limit)


def select_many(series, *, lists=('master', 'negative', 'brand'), readonly=False, **filters):
    """Same selector and permissions, several lists from one persisted snapshot."""
    return read_snapshot(series).select_many(lists=lists, readonly=readonly, **filters)


def _select(asset, sku=None, semantic=None, copy_allowed=None, ads_allowed=None, entry_signal=None,
            sif_tag=None, lifecycle=None, not_placed_in=None, list='master', limit=None):
    if list not in ['master', *LIST_FILES]:
        raise ValueError('unknown keyword list')
    if limit is not None and (not isinstance(limit, int) or limit < 0):
        raise ValueError('limit must be a nonnegative integer')
    if semantic is not None:
        aliases = {'scene': '场景', 'audience': '人群', 'gift': '礼品', 'season': '季节', 'use': '用途',
                   'material': '材质', 'feature': '功能', 'size': '规格', 'color': '颜色', 'core': '核心'}
        values = [semantic] if isinstance(semantic, str) else semantic
        semantic = builtins.list({item for value in values for item in [value, aliases.get(value, value),
            *[key for key, target in aliases.items() if value == target]]})
    if not_placed_in is not None:
        positions = [not_placed_in] if isinstance(not_placed_in, str) else not_placed_in
        valid = sum(_schema()['placements'].values(), [])
        groups = _schema().get('placement_groups', {})
        unknown = [position for position in positions if position not in valid and position not in groups]
        if unknown:
            raise ValueError(f'unknown placement filter: {unknown}')
        not_placed_in = [item for position in positions for item in groups.get(position, [position])]
    rows = asset['lists'][list]
    result = []
    for row in rows:
        if list != 'master' and row.get('active_classification', list) != list:
            continue
        if sku is not None and row['skus'] and sku not in row['skus']:
            continue
        row = _project_state(row, sku)
        if not _matches(row['semantic_tags'], semantic) or not _matches(row['lifecycle'], lifecycle):
            continue
        current_tags = [tag for tag in _current_effects(row) if tag['status'] == 'current']
        if not _matches([t.get('entry_signal') for t in current_tags], entry_signal):
            continue
        if not _matches([tag for t in current_tags for tag in t.get('sif_tags', [])], sif_tag):
            continue
        permitted = True
        for channel, desired in [('copy', copy_allowed), ('ads', ads_allowed)]:
            state = row['permissions'][channel]['allowed']
            positive = state in (['可写', '弱化表述'] if channel == 'copy' else ['可投', '仅定投'])
            if isinstance(desired, bool):
                permitted = permitted and positive == desired
            elif desired is not None:
                permitted = permitted and _matches(state, desired)
        if not permitted or (not_placed_in is not None and _matches(row['placements'], not_placed_in)):
            continue
        result.append(row)  # already a fresh copy from _project_state
    return result if limit is None else result[:limit]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['select', 'merge'])
    parser.add_argument('--series', required=True)
    parser.add_argument('--records', help='JSON/YAML records file; original evidence is not copied')
    parser.add_argument('--source')
    parser.add_argument('--sku')
    parser.add_argument('--list', default='master', choices=['master', *LIST_FILES])
    parser.add_argument('--limit', type=int, default=20, help='对话摘要上限，默认20，最大100；完整记录使用Python接口')
    for name in ['semantic', 'copy-allowed', 'ads-allowed', 'entry-signal', 'sif-tag', 'lifecycle', 'not-placed-in']:
        parser.add_argument('--' + name, action='append')
    args = parser.parse_args(argv)
    try:
        if args.command == 'merge':
            if not args.records:
                parser.error('merge requires --records')
            raw = yaml.safe_load(Path(args.records).read_text(encoding='utf-8'))
            result = merge(args.series, raw.get('records', []) if isinstance(raw, dict) else raw, source=args.source, sku=args.sku)
            result = {'revision': result['revision'], 'historical_pool_records': result['counts'],
                      'unique_words': len({(r['marketplace'], r['term_normalized']) for rows in result['lists'].values() for r in rows}),
                      'asset': str(Path(args.series)/'_词库资产/keyword_master.yaml')}
        else:
            options = vars(args).copy()
            for key in ['command', 'records', 'source']:
                options.pop(key)
            result = select(**options)
            if args.limit > 100: raise ValueError('CLI limit must be <= 100; use Python select for script processing')
            result = [{**{key: row.get(key) for key in ['term', 'chinese', 'marketplace', 'semantic_tags', 'permissions', 'lifecycle', 'ct_refs']},
                       'source_count': len(row['sources']), 'metric_observation_count': len(row['metrics']),
                       'asset': str(Path(args.series)/'_词库资产/keyword_master.yaml')} for row in result]
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        print(json.dumps({'status': 'error', 'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
