"""Resumable SP report collection and evidence-preserving S5 normalization."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import secrets
import time

from ads_api_client import AdsApiError

LAYERS = ('campaigns', 'adgroups', 'keywords', 'targets', 'query_words')
ENTITY_TYPES = {
    'campaigns': ('/sp/campaigns/list', 'campaigns', 'campaignId'),
    'adgroups': ('/sp/adGroups/list', 'adGroups', 'adGroupId'),
    'keywords': ('/sp/keywords/list', 'keywords', 'keywordId'),
    'targets': ('/sp/targets/list', 'targetingClauses', 'targetId'),
    'product_ads': ('/sp/productAds/list', 'productAds', 'adId'),
}
MAX_JSON = 128 * 1024 * 1024
MAX_PAGES = 100  # Local resource limit; not a statement about Amazon's API quota.


def now():
    return datetime.now(timezone.utc).isoformat()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)+'\n').encode('utf8')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + secrets.token_hex(5) + '.tmp')
    try:
        temp.write_bytes(encoded(value))
        for attempt in range(5):
            try:
                os.replace(temp, path)
                break
            except PermissionError:
                # Windows scanners can briefly hold the old manifest during rapid checkpoints.
                if attempt == 4: raise AdsApiError('artifact_write_failed') from None
                time.sleep(0.05 * (attempt + 1))
    finally:
        if temp.exists(): temp.unlink()


def read_json(path):
    try:
        path = Path(path)
        if path.stat().st_size > MAX_JSON: raise ValueError()
        return json.loads(path.read_text(encoding='utf8'))
    except Exception:
        raise AdsApiError('artifact_invalid') from None


def verify_artifacts(output, manifest, required=()):
    output = Path(output).resolve()
    try:
        files = manifest['files']
        if not isinstance(files, dict) or not set(required).issubset(files): raise ValueError()
        for info in files.values():
            path = (output/info['path']).resolve()
            if (not path.is_relative_to(output) or not path.is_file() or path.stat().st_size > MAX_JSON
                    or sha(path.read_bytes()) != info['sha256']): raise ValueError()
    except Exception:
        raise AdsApiError('artifact_integrity_failed') from None


def native_id(value):
    if isinstance(value, bool): return None
    return str(value) if isinstance(value, (str, int)) and re.fullmatch(r'[0-9]+', str(value)) else None


def validate_params(params):
    try:
        p = dict(params)
        p['profile_id'] = native_id(p.get('profile_id'))
        if not p['profile_id']: raise ValueError()
        start, end = date.fromisoformat(p['start_date']), date.fromisoformat(p['end_date'])
        if start > end or (end-start).days > 30: raise ValueError()
        if p.get('attribution_days') not in (7, 14): raise ValueError()
        ids = p.get('campaign_ids')
        if ids is None:
            if p.get('all_campaigns') is not True: raise ValueError()
        else:
            if not isinstance(ids, list) or not ids or any(not native_id(i) for i in ids): raise ValueError()
            if p.get('all_campaigns'): raise ValueError()
            p['campaign_ids'] = sorted(set(str(i) for i in ids))
        return {k: p.get(k) for k in ('profile_id', 'start_date', 'end_date', 'attribution_days', 'campaign_ids', 'all_campaigns')}
    except Exception:
        raise AdsApiError('invalid_scope_or_window') from None


def report_requests(params):
    """Pinned official Postman structure; exact sales column acceptance needs live verification."""
    p = validate_params(params)
    metrics = ['impressions', 'clicks', 'cost', f"purchases{p['attribution_days']}d", f"sales{p['attribution_days']}d"]
    common = ['campaignId', 'adGroupId']
    specs = {
        'campaign_adgroups': ('spCampaigns', ['campaign', 'adGroup'], common, 'DAILY', None),
        'keywords': ('spTargeting', ['targeting'], common+['keywordId', 'keyword', 'matchType'], 'SUMMARY', ['BROAD','PHRASE','EXACT']),
        'targets': ('spTargeting', ['targeting'], common+['keywordId', 'targeting', 'matchType'], 'SUMMARY', ['TARGETING_EXPRESSION','TARGETING_EXPRESSION_PREDEFINED']),
        'search_terms': ('spSearchTerm', ['searchTerm'], common+['keywordId', 'targeting', 'searchTerm'], 'DAILY', None),
    }
    result = {}
    for key, (report_type, group_by, fields, unit, filters) in specs.items():
        config = {'adProduct': 'SPONSORED_PRODUCTS', 'reportTypeId': report_type, 'groupBy': group_by,
                  'columns': fields+metrics+(['date'] if unit == 'DAILY' else ['startDate', 'endDate']),
                  'timeUnit': unit, 'format': 'GZIP_JSON'}
        if filters: config['filters'] = [{'field': 'keywordType', 'values': filters}]
        result[key] = {'name': f"Starsky {key} {p['start_date']} {p['end_date']}",
                       'startDate': p['start_date'], 'endDate': p['end_date'], 'configuration': config}
    return result


def profile_context(client, params):
    profiles = client.profiles()
    found = [p for p in profiles if native_id(p.get('profileId')) == params['profile_id']]
    if len(found) != 1: raise AdsApiError('profile_not_accessible')
    profile = found[0]
    if not all(isinstance(profile.get(k), str) and profile[k] for k in ('countryCode','currencyCode','timezone')):
        raise AdsApiError('profile_metadata_missing')
    # Amazon profiles use UK; users and ISO based configurations may use GB.
    def country_code(value):
        code = str(value or '').strip().upper()
        return 'GB' if code == 'UK' else code
    expected_country = client.config.get('expected_country')
    if expected_country and country_code(expected_country) != country_code(profile['countryCode']):
        raise AdsApiError('profile_country_mismatch')
    return {'source': 'amazon_ads_api', 'ad_product': 'SPONSORED_PRODUCTS', 'region': client.config['region'],
            'profile_id': params['profile_id'], 'country': profile['countryCode'], 'currency': profile['currencyCode'],
            'timezone': profile['timezone'], 'start_date': params['start_date'], 'end_date': params['end_date'],
            'date_boundary': 'inclusive', 'attribution_days': params['attribution_days'],
            'sales_field': f"sales{params['attribution_days']}d", 'orders_field': f"purchases{params['attribution_days']}d",
            'campaign_ids': params['campaign_ids'], 'unsupported_ad_products': ['SPONSORED_BRANDS','SPONSORED_DISPLAY']}


def collect_entities(client, profile_id, campaign_ids):
    result, issues = {}, []
    for key, (endpoint, array_key, id_key) in ENTITY_TYPES.items():
        rows, seen_ids, seen_tokens = [], set(), set()
        body = {'maxResults': 100}
        if campaign_ids: body['campaignIdFilter'] = {'include': campaign_ids}
        for page in range(MAX_PAGES):
            response = client.request('POST', endpoint, body, profile_id=profile_id)
            if not isinstance(response, dict) or not isinstance(response.get(array_key), list):
                raise AdsApiError('entity_schema_mismatch')
            for row in response[array_key]:
                if not isinstance(row, dict) or not native_id(row.get(id_key)):
                    raise AdsApiError('entity_id_invalid')
                value = native_id(row[id_key])
                if value in seen_ids:
                    issues.append('duplicate_entity:' + key)
                    continue
                seen_ids.add(value)
                rows.append(row)
            token = response.get('nextToken')
            if not token: break
            if not isinstance(token, str) or token in seen_tokens:
                raise AdsApiError('pagination_repeated_token')
            seen_tokens.add(token)
            body = {**body, 'nextToken': token}
        else:
            issues.append('pagination_truncated:' + key)
        result[key] = rows
    return result, issues


def unpack_report(data):
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
            decoded = stream.read(MAX_JSON+1)
        if len(decoded) > MAX_JSON: raise AdsApiError('report_too_large')
        rows = json.loads(decoded)
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows): raise ValueError()
        return rows
    except AdsApiError: raise
    except Exception: raise AdsApiError('report_format_invalid') from None


def _number(value):
    if isinstance(value, bool) or value is None: return None
    try:
        n = float(value)
        return n if math.isfinite(n) and n >= 0 else None
    except (ValueError, TypeError): return None


def normalize(raw, entities, context, files, extra_issues=()):
    """Each layer is its own view. Never add campaign, ad-group and keyword totals."""
    issues = list(extra_issues)
    indexes = {key: {native_id(r.get(id_key)): r for r in entities.get(key, [])}
               for key, (_, _, id_key) in ENTITY_TYPES.items()}
    fields = {'impressions':'impressions', 'clicks':'clicks', 'spend':'cost',
              'orders':context['orders_field'], 'sales':context['sales_field']}
    scope = set(context['campaign_ids'] or [])
    layers = {key: [] for key in LAYERS}

    def grouped(kind, keys):
        groups = defaultdict(list)
        seen = set()
        for r in raw[kind]:
            cid = native_id(r.get('campaignId'))
            if scope and cid not in scope: continue
            if not cid or not native_id(r.get('adGroupId')):
                issues.append('report_id_missing:' + kind)
                continue
            if kind in ('campaign_adgroups', 'search_terms'):
                day = str(r.get('date') or '')
                try: valid = context['start_date'] <= date.fromisoformat(day).isoformat() <= context['end_date']
                except ValueError: valid = False
            else:
                day = str(r.get('startDate')) + ':' + str(r.get('endDate'))
                valid = r.get('startDate') == context['start_date'] and r.get('endDate') == context['end_date']
            if not valid:
                issues.append('date_scope_mismatch:' + kind)
                continue
            identity = tuple(str(r.get(k, '')) for k in keys)
            unique = (identity, day)
            if unique in seen:
                issues.append('duplicate_report_row:' + kind)
                continue
            seen.add(unique)
            groups[identity].append(r)
        return groups

    def make(layer, native, rows, kind):
        first = rows[0]
        cid, aid = native_id(first.get('campaignId')), native_id(first.get('adGroupId'))
        entity = indexes[layer].get(native, {}) if layer in indexes else {}
        campaign = indexes['campaigns'].get(cid, {})
        blocked = False
        if layer in indexes:
            blocked = not entity or not campaign
            if layer != 'campaigns': blocked = blocked or native_id(entity.get('campaignId')) != cid
            if layer in ('keywords', 'targets'):
                parent = indexes['adgroups'].get(aid, {})
                blocked = (blocked or native_id(entity.get('adGroupId')) != aid
                           or native_id(parent.get('campaignId')) != cid)
        if blocked: issues.append('entity_join_missing:' + layer)
        metrics = {}
        for output, source in fields.items():
            values = [_number(r.get(source)) for r in rows]
            metrics[output] = sum(values) if all(v is not None for v in values) else None
            if metrics[output] is None: issues.append('metric_missing:' + kind + ':' + source)
        item = {**metrics, 'native_id': native, 'layer': layer, 'campaign_id': cid,
                'adgroup_id': aid if layer != 'campaigns' else None,
                'name': entity.get('name') or entity.get('keywordText') or first.get('searchTerm') or native or '',
                'role': str(campaign.get('targetingType') or 'unknown').lower(),
                'strategy': (campaign.get('dynamicBidding') or {}).get('strategy'),
                'bid': _number(entity.get('defaultBid') if layer == 'adgroups' else entity.get('bid')),
                'state': entity.get('state'), 'data_blocked': blocked,
                'source_context': dict(context),
                'source_fields': dict(fields),
                'evidence': [{'file': files[kind]['path'], 'sha256': files[kind]['sha256'], 'rows': len(rows)}],
                'configuration_note': '实体出价为采集时快照，不代表整个报表窗口的历史出价。'}
        if layer == 'query_words': item['search_term'] = first.get('searchTerm')
        if layer == 'targets': item['report_keyword_id'] = first.get('keywordId')
        return item

    adgroups = grouped('campaign_adgroups', ('campaignId','adGroupId'))
    campaigns = defaultdict(list)
    for (cid, aid), rows in adgroups.items():
        layers['adgroups'].append(make('adgroups', aid, rows, 'campaign_adgroups'))
        campaigns[cid].extend(rows)
    for cid, rows in campaigns.items(): layers['campaigns'].append(make('campaigns', cid, rows, 'campaign_adgroups'))
    for kind, layer in (('keywords','keywords'),('targets','targets'),('search_terms','query_words')):
        keys = ('campaignId','adGroupId','keywordId') + (('searchTerm',) if kind == 'search_terms' else ())
        for identity, rows in grouped(kind, keys).items():
            native = native_id(rows[0].get('keywordId'))
            if not native and layer != 'query_words': issues.append('report_id_missing:' + kind)
            layers[layer].append(make(layer, native, rows, kind))
    return {'schema_version':'1.1', 'artifact_type':'s5_review_input', 'source_context':context,
            'data_quality':{'complete':not issues, 'issues':sorted(set(issues)), 'empty':not any(layers.values()),
                            'note':'SP 范围；不同广告层级不能相加。近期归因仍可能补记，结论需观察。'},
            'layers':layers, 'card':{}, 'pending_actions':[]}


def collect(client, params, output, wait_seconds=0):
    p = validate_params(params)
    if not 0 <= wait_seconds <= 60: raise AdsApiError('invalid_wait')
    output = Path(output).resolve()
    manifest_path = output/'manifest.json'
    context = profile_context(client, p)
    fingerprint = sha(encoded({'params':p, 'source_context':context}))
    if manifest_path.exists():
        manifest = read_json(manifest_path)
        if manifest.get('fingerprint') != fingerprint: raise AdsApiError('resume_scope_mismatch')
        verify_artifacts(output, manifest)
    else:
        if output.exists() and any(output.iterdir()): raise AdsApiError('output_not_empty')
        output.mkdir(parents=True, exist_ok=True)
        manifest = {'schema_version':'1.1','state':'collecting','created_at':now(), 'fingerprint':fingerprint,
                    'params':p, 'source_context':context, 'jobs':{},'files':{},'issues':[]}
    # Prevent simultaneous invocations from submitting duplicate jobs to the same run.
    lock = output/'.collect.lock'
    try: fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError: raise AdsApiError('collection_already_running') from None
    os.close(fd)
    try:
        def save_file(key, path, value):
            write_json(output/path, value)
            manifest['files'][key] = {'path':path,'sha256':sha((output/path).read_bytes())}
        if 'entities' not in manifest['files']:
            entities, issues = collect_entities(client, p['profile_id'], p['campaign_ids'])
            save_file('entities','raw/entities.json', entities)
            # Configuration mapping only: profile lives in source_context, while
            # adId/campaignId/adGroupId/ASIN/SKU remain native in this snapshot.
            # Never allocate group/keyword performance across productAds rows.
            save_file('product_ads', 'raw/product_ads.json', entities['product_ads'])
            manifest['entity_types'] = list(entities)
            manifest['entities_collected_at'] = now()
            manifest['issues'].extend(issues)
            write_json(manifest_path, manifest)
        requests = report_requests(p)
        for kind, request in requests.items():
            job = manifest['jobs'].get(kind)
            if not job:
                response = client.request('POST','/reporting/reports',request,profile_id=p['profile_id'])
                report_id = response.get('reportId') if isinstance(response,dict) else None
                if not isinstance(report_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',report_id):
                    raise AdsApiError('report_id_invalid')
                manifest['jobs'][kind] = {'report_id':report_id,'request':request,'status':'PENDING'}
                write_json(manifest_path, manifest)
        if wait_seconds: time.sleep(wait_seconds)
        for kind, job in manifest['jobs'].items():
            if job['status'] in ('COMPLETED','FAILURE','FAILED'): continue
            response = client.request('GET','/reporting/reports/'+job['report_id'],profile_id=p['profile_id'])
            status = response.get('status') if isinstance(response,dict) else None
            if status not in ('PENDING','PROCESSING','COMPLETED','FAILURE','FAILED'):
                raise AdsApiError('report_status_unknown')
            if status == 'COMPLETED':
                rows = unpack_report(client.download_report(response.get('url')))
                save_file(kind, 'raw/'+kind+'.json', rows)
                job['row_count'] = len(rows)
                job['downloaded_at'] = now()
            job['status'] = status
            # URLs, response bodies and failureReason may contain credentials: never persist them.
            write_json(manifest_path, manifest)
        statuses = [j['status'] for j in manifest['jobs'].values()]
        if any(s in ('FAILURE','FAILED') for s in statuses): manifest['state'] = 'failed'
        elif all(s == 'COMPLETED' for s in statuses):
            raw = {k:read_json(output/manifest['files'][k]['path']) for k in requests}
            entities = read_json(output/manifest['files']['entities']['path'])
            context['collected_at'] = max(j['downloaded_at'] for j in manifest['jobs'].values())
            context['entities_collected_at'] = manifest['entities_collected_at']
            payload = normalize(raw, entities, context, manifest['files'], manifest['issues'])
            save_file('review_input', 'review_input.json', payload)
            manifest['state'] = 'complete' if payload['data_quality']['complete'] else 'data_gap'
        else: manifest['state'] = 'pending'
        manifest.pop('last_error',None)
        manifest['updated_at'] = now()
        write_json(manifest_path, manifest)
        return {'state':manifest['state'],'manifest':str(manifest_path),'jobs':{k:j['status'] for k,j in manifest['jobs'].items()},
                'next':'analyze' if manifest['state'] in ('complete','data_gap') else 'resume_same_command' if manifest['state']=='pending' else 'inspect_report_failure'}
    except AdsApiError as error:
        manifest['last_error'] = {'code':error.code,'status':error.status}
        manifest['state'] = 'retryable_error' if error.code in ('network_error','network_timeout','retry_deferred','rate_limited','service_unavailable') else 'failed'
        write_json(manifest_path, manifest)
        raise
    finally:
        lock.unlink(missing_ok=True)
