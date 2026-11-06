"""SP-API read domains + one gated listing-write (patch_listing_item only).
Never call Ads routes. Writes require explicit confirm=True plus caller-side
review proof; the CLI enforces dry-run default and reviewer identity.

Transport is the sibling Ads client's six-argument callable:
(method, url, headers, body_bytes_or_None, timeout_seconds, max_bytes)
-> (status, response_headers, response_bytes). Injected transports must reject
redirects. Default transport disables redirects and proxies and bounds reads.
"""
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
from urllib.parse import urlencode
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ENDPOINTS = {
    'NA': 'https://sellingpartnerapi-na.amazon.com',
    'EU': 'https://sellingpartnerapi-eu.amazon.com',
    'FE': 'https://sellingpartnerapi-fe.amazon.com',
}
TOKEN_URL = 'https://api.amazon.com/auth/o2/token'
PARTICIPATIONS = '/sellers/v1/marketplaceParticipations'
SALES = '/sales/v1/orderMetrics'
INVENTORY = '/fba/inventory/v1/summaries'
LISTINGS = '/listings/2021-08-01/items/'
DEFINITIONS = '/definitions/2020-09-01/productTypes/'


def _ads_module():
    path = Path(__file__).resolve().parents[2] / 'amazon-ads-api-connector/scripts/ads_api_client.py'
    spec = importlib.util.spec_from_file_location('_sp_ads_transport', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_ADS = _ads_module()


class SpApiError(Exception):
    """Only category and HTTP status; never embeds body, tokens, or request URL."""
    def __init__(self, code, status=None):
        self.code = code
        self.status = status
        super().__init__(code + (f' (HTTP {status})' if status else ''))


def _field(config, name, required=False):
    env = config.get(name + '_env')
    if env is not None and (not isinstance(env, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', env)):
        raise SpApiError('config_invalid')
    # A connection's file is authoritative: never borrow from process or another file.
    source = config.get('_file_credentials') if '_file_credentials' in config else os.environ
    value = source.get(env) if env else config.get(name)
    if value is not None and (not isinstance(value, str) or '\r' in value or '\n' in value):
        raise SpApiError('config_invalid')
    if required and (not value or not value.strip()):
        raise SpApiError('credentials_missing')
    return value


def _validated(config):
    if not isinstance(config, dict) or config.get('region') not in ENDPOINTS:
        raise SpApiError('config_invalid')
    config = dict(config)
    endpoint = _field(config, 'endpoint')
    if endpoint and endpoint.rstrip('/') != ENDPOINTS[config['region']]:
        raise SpApiError('credential_region_mismatch')
    config['endpoint'] = ENDPOINTS[config['region']]
    market = _field(config, 'marketplace_id')
    if market and not re.fullmatch(r'[A-Za-z0-9]{1,64}', market):
        raise SpApiError('marketplace_invalid')
    config['marketplace_id'] = market
    seller = config.get('seller_id')
    if seller and (not isinstance(seller, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', seller)):
        raise SpApiError('seller_id_invalid')
    for name in ('client_id', 'client_secret', 'refresh_token'):
        _field(config, name)  # Reject malformed mappings without needing credentials for discovery.
    for key, default, maximum in (('max_pages', 100, 1000), ('page_size', 20, 20)):
        value = config.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise SpApiError('config_invalid')
        config[key] = value
    return config


def load_config(path):
    """Read YAML/JSON and an optional independent .env without changing os.environ.

region is explicit. Five mappings are supported: client_id_env,
client_secret_env, refresh_token_env, marketplace_id_env, endpoint_env.
Legacy nested lwa credentials are accepted; marketplace/country is not guessed.
The returned dictionary contains private credential data: never log it.
"""
    try:
        path = Path(path).expanduser().resolve()
        raw = path.read_text(encoding='utf-8-sig')
        if raw.lstrip().startswith('{'):
            config = json.loads(raw)
        else:
            import yaml
            config = yaml.safe_load(raw)
        if not isinstance(config, dict):
            raise ValueError()
        if isinstance(config.get('lwa'), dict):
            for name in ('client_id', 'client_secret', 'refresh_token'):
                if name not in config and name in config['lwa']:
                    config[name] = config['lwa'][name]
        if config.get('credential_file'):
            file_path = Path(config['credential_file']).expanduser()
            if not file_path.is_absolute():
                file_path = path.parent / file_path
            config['_file_credentials'] = _ADS.read_credential_file(file_path)
        return _validated(config)
    except SpApiError:
        raise
    except _ADS.AdsApiError:
        raise SpApiError('credential_file_invalid') from None
    except Exception:
        raise SpApiError('config_invalid') from None


class SpApiClient:
    def __init__(self, config, transport=None, sleeper=None):
        self.config = _validated(config)
        self.base_url = ENDPOINTS[self.config['region']]
        # Reuse only bounded JSON transport; Ads credential refresh/routes are never called.
        self._wire = _ADS.AdsClient({'region': self.config['region']}, transport, sleeper)
        self._access_token = None

    def _json(self, method, url, headers, body=None):
        try:
            value = self._wire._json(method, url, headers, body)
        except _ADS.AdsApiError as error:
            raise SpApiError(error.code, error.status) from None
        if not isinstance(value, dict):
            raise SpApiError('response_contract_mismatch')
        if value.get('errors'):
            raise SpApiError('api_response_error')
        return value

    def _refresh(self):
        form = {'grant_type': 'refresh_token'}
        for field in ('client_id', 'client_secret', 'refresh_token'):
            form[field] = _field(self.config, field, required=True)
        result = self._json('POST', TOKEN_URL,
                            {'Content-Type': 'application/x-www-form-urlencoded'},
                            urlencode(form).encode())
        token = result.get('access_token')
        if not isinstance(token, str) or not token or '\r' in token or '\n' in token:
            raise SpApiError('authentication_failed')
        self._access_token = token

    def request(self, method, path, params=None):
        """GET allowlist only; params are encoded, never interpreted as a URL."""
        allowed = isinstance(path, str) and (
            path in (PARTICIPATIONS, SALES, INVENTORY)
            or re.fullmatch(re.escape(LISTINGS) + r'[A-Za-z0-9_-]{1,64}', path)
            or re.fullmatch(re.escape(LISTINGS) + r'[A-Za-z0-9_-]{1,64}/[A-Za-z0-9_-]{1,64}', path)
            or path == DEFINITIONS.rstrip('/')
            or re.fullmatch(re.escape(DEFINITIONS) + r'[A-Za-z0-9_-]{1,128}', path))
        if method != 'GET' or not allowed:
            raise SpApiError('endpoint_not_allowed')
        try:
            query = urlencode(params or {}, doseq=True)
        except Exception:
            raise SpApiError('invalid_request') from None
        url = self.base_url + path + ('?' + query if query else '')
        for attempt in range(2):
            if not self._access_token:
                self._refresh()
            try:
                return self._json('GET', url,
                                  {'x-amz-access-token': self._access_token, 'Accept': 'application/json'})
            except SpApiError as error:
                if error.status == 401 and attempt == 0:
                    self._access_token = None
                    continue
                raise

    def _marketplace(self):
        market = self.config.get('marketplace_id')
        if not market:
            raise SpApiError('marketplace_required')
        return market

    def participations(self):
        """Return the raw marketplace participation response dictionary."""
        result = self.request('GET', PARTICIPATIONS)
        if not isinstance(result.get('payload'), list):
            raise SpApiError('response_contract_mismatch')
        return result

    def sales(self, start_iso, end_iso, time_zone, asin=None, sku=None):
        """Raw Day aggregates; interval includes start and excludes end.

Absolute ISO timestamps must have offsets consistent with the IANA time zone.
Monetary values and currency codes remain unmodified in the response payload.
ASIN and SKU are mutually exclusive exact filters; empty/invalid filters fail
closed rather than becoming whole-store queries. Neither filter means the
original whole-store scope. unitCount, orderItemCount and orderCount are distinct;
SKU unit costs require SKU-filtered unitCount, never whole-store orderCount.
"""
        if asin is not None and sku is not None:
            raise SpApiError('sales_filters_mutually_exclusive')
        if asin is not None and (not isinstance(asin, str) or not re.fullmatch(r'[A-Z0-9]{10}', asin)):
            raise SpApiError('asin_invalid')
        if sku is not None and (not isinstance(sku, str) or not sku.strip() or not sku.isprintable()):
            raise SpApiError('sku_invalid')
        product_filter = {'asin': asin} if asin is not None else {'sku': sku} if sku is not None else {}
        try:
            if not all(isinstance(value, str) and 'T' in value for value in (start_iso, end_iso)):
                raise ValueError()
            start, end = (datetime.fromisoformat(value.replace('Z', '+00:00')) for value in (start_iso, end_iso))
            if start.utcoffset() is None or end.utcoffset() is None or end <= start:
                raise ValueError()
            zone = timezone.utc if time_zone == 'UTC' else ZoneInfo(time_zone)
            if any(value.astimezone(zone).utcoffset() != value.utcoffset() for value in (start, end)):
                raise ValueError()
        except ZoneInfoNotFoundError:
            raise SpApiError('timezone_unavailable') from None
        except Exception:
            raise SpApiError('sales_interval_invalid') from None
        result = self.request('GET', SALES, {
            'marketplaceIds': self._marketplace(),
            'interval': start_iso + '--' + end_iso,
            'granularity': 'Day', 'granularityTimeZone': time_zone, **product_filter,
        })
        if not isinstance(result.get('payload'), list):
            raise SpApiError('response_contract_mismatch')
        return result

    def _pages(self, path, params, kind):
        result = {'rows': [], 'complete': False, 'page_count': 0,
                  'raw_pages': [], 'error': None,
                  'marketplace_id': self._marketplace(), 'region': self.config['region']}
        seen_tokens = set()
        if kind == 'listings':
            result['number_of_results'] = None
            result['search_result_limit'] = 1000
        for _ in range(self.config['max_pages']):
            try:
                page = self.request('GET', path, params)
                payload = page.get('payload') if kind == 'inventory' else page
                if not isinstance(payload, dict):
                    raise SpApiError('response_contract_mismatch')
                rows = payload.get('inventorySummaries' if kind == 'inventory' else 'items')
                if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                    raise SpApiError('response_contract_mismatch')
                if kind == 'listings':
                    count = page.get('numberOfResults')
                    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                        raise SpApiError('response_contract_mismatch')
                    previous = result['number_of_results']
                    if previous is not None and count != previous:
                        raise SpApiError('listing_count_changed')
                    result['number_of_results'] = count
                pagination = page.get('pagination', {})
                if not isinstance(pagination, dict):
                    raise SpApiError('response_contract_mismatch')
                token = pagination.get('nextToken')
                if token is not None and (not isinstance(token, str) or not token):
                    raise SpApiError('response_contract_mismatch')
                result['raw_pages'].append(page)
                result['rows'].extend(rows)
                result['page_count'] += 1
                # The Listings search API only allows paging through 1000 SKUs.
                # Hitting that boundary cannot establish whole-store completeness,
                # even if Amazon stops returning nextToken or reports exactly 1000.
                if kind == 'listings' and (len(result['rows']) >= 1000 or
                                          (not token and result['number_of_results'] >= 1000)):
                    raise SpApiError('listing_search_limit')
                if not token:
                    if kind == 'listings' and len(result['rows']) != result['number_of_results']:
                        raise SpApiError('listing_count_mismatch')
                    result['complete'] = True
                    return result
                if token in seen_tokens:
                    raise SpApiError('pagination_cycle')
                seen_tokens.add(token)
                params = dict(params, **{'nextToken' if kind == 'inventory' else 'pageToken': token})
            except SpApiError as error:
                if not result['page_count']:
                    raise
                result['error'] = {'code': error.code, 'status': error.status}
                return result
        result['error'] = {'code': 'page_limit', 'status': None}
        return result

    def inventory(self):
        """Return rows, complete, page_count, raw_pages and safe partial error.

Inventory is FBA only; do not infer merchant-fulfilled stock or zero missing values.
"""
        market = self._marketplace()
        return self._pages(INVENTORY, {'marketplaceIds': market,
                           'granularityType': 'Marketplace', 'granularityId': market,
                           'details': 'true'}, 'inventory')

    # 文案可写字段白名单（标题/五点/长描述/后台关键词）。其他属性名拒绝，
    # 防止把 07 文案写进错误字段。
    WRITABLE_ATTRIBUTES = frozenset(['item_name', 'bullet_point', 'product_description', 'generic_keyword'])
    _SHEET_RE = re.compile(r'[A-Za-z0-9_-]{1,64}')
    _PTYPE_RE = re.compile(r'[A-Za-z0-9_-]{1,128}')
    _ATTR_RE = re.compile(r'/attributes/([A-Za-z][A-Za-z0-9_]*)')

    def _mutate(self, method, path, body):
        """PATCH allowlist: listings-item paths only. Anything else rejected."""
        if method != 'PATCH' or not re.fullmatch(
                re.escape(LISTINGS) + r'[A-Za-z0-9_-]{1,64}/[A-Za-z0-9_-]{1,64}(?:\?marketplaceIds=[A-Z0-9-]+)?', path):
            raise SpApiError('endpoint_not_allowed')
        try:
            encoded = json.dumps(body).encode()
        except Exception:
            raise SpApiError('invalid_request')
        if len(encoded) > 64 * 1024:
            raise SpApiError('request_too_large')
        url = self.base_url + path
        for attempt in range(2):
            if not self._access_token:
                self._refresh()
            try:
                return self._json(method, url,
                                  {'x-amz-access-token': self._access_token,
                                   'Accept': 'application/json',
                                   'Content-Type': 'application/json'},
                                  encoded)
            except SpApiError as error:
                if error.status == 401 and attempt == 0:
                    self._access_token = None
                    continue
                raise

    def patch_listing_item(self, seller_id, sku, product_type, patches, marketplace=None, confirm=False):
        """Single-SKU listing patch behind an explicit confirm gate.

        patches: [{'op': 'replace', 'path': '/attributes/<name>', 'value': ...}],
        at most 8 ops, names limited to WRITABLE_ATTRIBUTES. Without
        confirm=True raises SpApiError('confirm_required') before any network use."""
        if confirm is not True:
            raise SpApiError('confirm_required')
        for label, value, rx in (('seller_id', seller_id, self._SHEET_RE),
                                 ('sku', sku, self._SHEET_RE),
                                 ('product_type', product_type, self._PTYPE_RE)):
            if not isinstance(value, str) or not rx.fullmatch(value):
                raise SpApiError(label + '_invalid')
        if not isinstance(patches, list) or not 1 <= len(patches) <= 8:
            raise SpApiError('patches_invalid')
        for op in patches:
            if not isinstance(op, dict) or op.get('op') != 'replace':
                raise SpApiError('patches_invalid')
            m = self._ATTR_RE.fullmatch(str(op.get('path', ''))) if isinstance(op.get('path'), str) else None
            if not m or m.group(1) not in self.WRITABLE_ATTRIBUTES:
                raise SpApiError('attribute_not_writable')
            try:
                if len(json.dumps(op.get('value')).encode()) > 8 * 1024:
                    raise SpApiError('value_too_large')
            except SpApiError:
                raise
            except Exception:
                raise SpApiError('patches_invalid')
        market = marketplace or self._marketplace()
        if not isinstance(market, str) or not re.fullmatch(r'[A-Z0-9-]+', market):
            raise SpApiError('marketplace_invalid')
        return self._mutate(
            'PATCH', LISTINGS + seller_id + '/' + sku + '?marketplaceIds=' + market,
            {'productType': product_type, 'patches': patches})

    def listings(self, seller_id=None):
        """All available listing pages for an explicitly supplied seller ID."""
        seller_id = seller_id if seller_id is not None else self.config.get('seller_id')
        if not seller_id:
            raise SpApiError('seller_id_required')
        if not isinstance(seller_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', seller_id):
            raise SpApiError('seller_id_invalid')
        return self._pages(LISTINGS + seller_id, {
            'marketplaceIds': self._marketplace(), 'pageSize': self.config['page_size'],
            'includedData': 'summaries,attributes,issues,offers,fulfillmentAvailability',
        }, 'listings')

    def get_listing_item(self, seller_id, sku, marketplace):
        """Narrow same-SKU readback; never create a missing listing."""
        for label,value in [('seller_id',seller_id),('sku',sku),('marketplace',marketplace)]:
            if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',value):
                raise SpApiError(label+'_invalid')
        return self.request('GET',LISTINGS+seller_id+'/'+sku,
                            {'marketplaceIds':marketplace,'includedData':'summaries,attributes,issues'})

    def search_product_types(self, keywords, locale='en_US'):
        """Read-only productType search by item keywords (Product Type Definitions API)."""
        words=[w.strip() for w in (keywords if isinstance(keywords,list) else [keywords]) if isinstance(w,str) and w.strip()]
        if not words or len(words)>10 or any(not re.fullmatch(r"[A-Za-z0-9 '&-]{1,64}",w) for w in words) or not re.fullmatch(r'[a-z]{2}_[A-Z]{2}',locale):
            raise SpApiError('definition_search_invalid')
        result=self.request('GET',DEFINITIONS.rstrip('/'),{'marketplaceIds':self._marketplace(),'keywords':','.join(words),'locale':locale})
        return [{'name':t.get('name'),'display_name':t.get('displayName')} for t in result.get('productTypes') or [] if isinstance(t,dict) and t.get('name')]

    def definition(self, product_type, locale, requirements='LISTING'):
        """Return PTD metadata plus bounded schema JSON downloaded without LWA."""
        if (not isinstance(product_type,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',product_type)
                or not isinstance(locale,str) or not re.fullmatch(r'[a-z]{2}_[A-Z]{2}',locale)
                or requirements not in ('LISTING','LISTING_PRODUCT_ONLY')):
            raise SpApiError('definition_scope_invalid')
        result=self.request('GET',DEFINITIONS+product_type,{'marketplaceIds':self._marketplace(),'locale':locale,'requirements':requirements})
        schema=result.get('schema')
        operation=schema.get('link') if isinstance(schema,dict) else None
        if not isinstance(operation,dict) or not isinstance(operation.get('resource'),str) or operation.get('verb') != 'GET':
            raise SpApiError('definition_schema_link_missing')
        link=operation['resource']
        try:
            parsed=urlsplit(link); host=(parsed.hostname or '').lower()
            valid_host=bool(re.fullmatch(r'(?:[a-z0-9.-]+\.)?s3(?:[.-][a-z0-9-]+)?\.amazonaws\.com',host))
            if (parsed.scheme!='https' or not valid_host or parsed.username or parsed.password or parsed.fragment
                    or parsed.port not in (None,443)):
                raise SpApiError('definition_schema_url_rejected')
            raw=self._wire._send('GET',link,{},max_bytes=16*1024*1024)
            try: downloaded=json.loads(raw)
            except Exception: raise SpApiError('definition_schema_invalid_json') from None
            if not isinstance(downloaded,dict): raise SpApiError('definition_schema_invalid_json')
        except SpApiError: raise
        except ValueError:
            raise SpApiError('definition_schema_url_rejected') from None
        except _ADS.AdsApiError as error:
            raise SpApiError(error.code,error.status) from None
        except Exception: raise SpApiError('definition_schema_url_rejected') from None
        return {'definition':result,'schema':downloaded,'schema_bytes':raw}
