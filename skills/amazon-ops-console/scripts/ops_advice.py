"""Product-bound SP diagnostics and unapproved testing drafts; never writes Ads.

Only verified campaign/ad-group report rows supply product metrics. Search-term
rows remain a separate view and are never added to keyword/target/group totals.
Budget and bid are intentionally unresolved. SIF can supplement keyword context,
but cannot establish account performance or conversion.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta
import math
from pathlib import Path
import unicodedata
from zoneinfo import ZoneInfo

import ops_collect as ops
import ops_enrich as enrichment
from ops_rules import check_listing

RULE_VERSION = 'product-sp-advice-1.0'


def _normal(value):
    return unicodedata.normalize('NFKC', str(value or '')).strip().casefold()


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (ValueError, TypeError):
        return None


def _hash(value):
    return ops.sha(ops.encoded(value))


def _query(label, rows, evidence, definition):
    files = {(_normal(e.get('path')), e.get('sha256')): e for e in evidence if isinstance(e, dict)}
    return {'rows': rows, 'source': {'label': label, 'provider': 'Amazon Ads API + verified local inputs',
        'files': list(files.values()), 'metricDefinitions': [{'label': label, 'definition': definition}],
        'evidenceFlow': [{'title': '产品关联', 'detail': '已验证单一 SKU 广告组；多产品组不分摊'},
                         {'title': '人工审核', 'detail': '建议未批准，预算与竞价待确认，没有后台写入'}]}}


def _metrics(rows, context, identity):
    fields = {'spend': 'cost', 'adSales': context.get('sales_field'), 'adOrders': context.get('orders_field'),
              'clicks': 'clicks', 'impressions': 'impressions'}
    issues = []
    keys = [identity(row) for row in rows]
    if len(set(keys)) != len(keys):
        issues.append('duplicate_performance_row')
    result = {}
    for output, source in fields.items():
        values = [_number(row.get(source)) for row in rows]
        result[output] = sum(values) if rows and all(v is not None for v in values) and not issues else None
    if rows and any(result[k] is None for k in fields):
        issues.append('performance_metric_missing')
    result['acos'] = result['spend'] / result['adSales'] if result['spend'] is not None and result['adSales'] else None
    result['cvr'] = result['adOrders'] / result['clicks'] if result['adOrders'] is not None and result['clicks'] else None
    return result, sorted(set(issues))


def _attribution(context, reference_time):
    result = {'days': context.get('attribution_days'), 'orders_field': context.get('orders_field'),
              'sales_field': context.get('sales_field'), 'start_date': context.get('start_date'),
              'end_date': context.get('end_date'), 'mature': False, 'evaluated_at': reference_time,
              'note': '近期归因可能补记；订单字段保持 API 原始口径，成熟不代表利润已结算。'}
    try:
        days = context['attribution_days']
        if isinstance(days, bool) or not isinstance(days, int) or days < 1:
            raise ValueError()
        reference = datetime.fromisoformat(reference_time.replace('Z', '+00:00'))
        if reference.tzinfo is None:
            raise ValueError()
        mature_after = date.fromisoformat(context['end_date']) + timedelta(days=days + 1)
        result['fully_mature_after'] = mature_after.isoformat()
        result['mature'] = reference.astimezone(ZoneInfo(context['timezone'])).date() >= mature_after
    except (KeyError, ValueError, TypeError):
        result['note'] = '归因天数、证据区间或时区缺失，成熟度未知。'
    return result


def _search_signature(row, context, snapshot=False):
    return (str(row.get('campaignId')), str(row.get('adGroupId')),
            str(row.get('nativeId' if snapshot else 'keywordId')),
            row.get('name' if snapshot else 'searchTerm'), row.get('date'),
            *(_number(row.get(key)) for key in (('spend', 'adSales', 'adOrders', 'clicks', 'impressions') if snapshot
              else ('cost', context.get('sales_field'), context.get('orders_field'), 'clicks', 'impressions'))))


def _verified_searches(run, manifest, snapshot, market, context):
    info = (manifest.get('files') or {}).get('search_terms')
    rows = enrichment.verified(run / 'ads', info)
    query = snapshot.get('queries', {}).get('ad_detail') or {}
    files = (query.get('source') or {}).get('files') or []
    if not isinstance(rows, list) or not any(e.get('sha256') == info.get('sha256') for e in files if isinstance(e, dict)):
        return [], []
    signatures = Counter(_search_signature(row, context, True) for row in query.get('rows', [])
                         if row.get('market') == market and row.get('layer') == 'search_term'
                         and not row.get('stale') and row.get('complete') is not False)
    valid = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        signature = _search_signature(row, context)
        if signatures[signature] > 0:
            valid.append(row)
            signatures[signature] -= 1
    return valid, [{'path': str((run / 'ads' / info['path']).resolve()), 'sha256': info['sha256']}]


def _duplicate_check(entities, manifest, mapping, product, keyword):
    rows = entities.get('keywords') if isinstance(entities, dict) else None
    groups = mapping.get('single_sku_groups', {})
    found = []
    unknown_mapping = False
    for row in rows or []:
        group = (str(row.get('campaignId')), str(row.get('adGroupId')))
        if str(row.get('matchType')).upper() != 'EXACT' or str(row.get('state')).upper() == 'ARCHIVED':
            continue
        if group not in groups:
            unknown_mapping = True
        elif groups[group] == product and _normal(row.get('keywordText')) == _normal(keyword):
            found.append({'campaignId': str(row.get('campaignId')), 'adGroupId': str(row.get('adGroupId')),
                          'keywordId': str(row.get('keywordId')), 'state': row.get('state')})
    coverage = (isinstance(rows, list) and manifest.get('state')=='complete'
                and (manifest.get('params') or {}).get('all_campaigns') is True
                and not unknown_mapping and not any('keywords' in str(i) or 'campaigns' in str(i)
                for i in manifest.get('issues', [])))
    # A report-scoped map cannot prove absence in campaigns outside its scope.
    all_campaigns = {str(r.get('campaignId')) for r in (entities or {}).get('campaigns', [])
                     if str(r.get('state')).upper() != 'ARCHIVED'}
    mapped_campaigns = {key[0] for key in mapping.get('group_skus', {})}
    coverage = coverage and all_campaigns.issubset(mapped_campaigns)
    return {'state': 'existing_exact' if found else 'not_found_in_complete_snapshot' if coverage else 'incomplete',
            'complete': bool(coverage), 'existing': found,
            'note': '仅当前已采集 SP 精准词与商品映射；不宣称跨未覆盖账号/广告类型完成查重。'}


def _sif_signals(config, snapshot, market, asin, sku, keyword):
    from ops_keywords import normalize
    query = snapshot.get('queries', {}).get('keyword_metrics') or {}
    root = Path(config['workspace']).resolve()
    signals=[]
    for row in query.get('rows', []):
        if (row.get('provider')!='SIF' or row.get('store')!=config['store'] or row.get('market')!=market
                or row.get('asin')!=asin or row.get('sku') not in (None,sku) or row.get('stale')
                or _normal(row.get('keyword'))!=_normal(keyword)): continue
        info={'path':row.get('evidence_file'),'sha256':row.get('evidence_sha256')}
        envelope=enrichment.verified(root,info)
        if not isinstance(envelope,dict): continue
        if any(envelope.get(k)!=row.get(k) for k in ('store','market','asin','provider','collected_at')): continue
        stamp,reference=enrichment._timestamp(row.get('collected_at')),enrichment._timestamp(snapshot.get('generatedAt'))
        if not stamp or not reference or stamp>reference: continue
        try: raw_rows=normalize(envelope)
        except (KeyError,ValueError,TypeError): continue
        matched=next((r for r in raw_rows if all(r.get(k)==row.get(k) for k in ('keyword','search_volume','market_cpc'))),None)
        if matched is None: continue
        signals.append({'provider':'SIF','basis':'third_party_estimate_not_account_performance',
            'keyword':matched['keyword'],'search_volume_estimate':matched.get('search_volume'),
            'cpc_estimate':matched.get('market_cpc'),'evidence':[info]})
    return signals


def enrich_advice(config, snapshot):
    """Return a copied snapshot with product_ad_diagnostics/ad_recommendations.

No file writes, approval transitions, API requests, budgets or bids are generated.
Account/config and mapping verification are delegated to the shared enrichment
helper. All queries use evidence already collected in the local workspace.
"""
    cfg = ops._config(config)
    result = deepcopy(snapshot)
    queries = result.setdefault('queries', {})
    diagnostics, plans, evidence = [], [], []
    reference = snapshot.get('generatedAt')
    rules = enrichment._read(cfg.get('listing_rules_file', '')) or {}
    for item in cfg['markets']:
        market = item['market']
        run = enrichment._run_dir(Path(cfg['workspace']), market)
        if run is None:
            continue
        products = [r for r in (queries.get('products') or {}).get('rows', []) if r.get('market') == market]
        costs = [r for r in (queries.get('costs') or {}).get('rows', []) if r.get('market') == market]
        binding = enrichment._read(run / 'source_context.json') or {}
        try:
            ads_config = ops.load_ads_config(item['ads_config'])
            sp_config = ops.load_sp_config(item['sp_config'])
            bound = (all(isinstance(sp_config.get(k), str) and sp_config[k] for k in ('seller_id', 'marketplace_id'))
                     and binding.get('timezone') == item.get('timezone')
                     and binding.get('store') == cfg['store'] and binding.get('market') == market
                     and binding.get('seller_id') == sp_config.get('seller_id')
                     and binding.get('marketplace_id') == sp_config.get('marketplace_id'))
            mapping = enrichment.verified_product_map(run, binding, ads_config, market, reference) if bound else {
                'state': 'blocked', 'issues': ['account_binding_mismatch']}
        except Exception:
            mapping = {'state': 'blocked', 'issues': ['mapping_verification_unavailable']}
        context = mapping.get('source_context') or {}
        groups = mapping.get('single_sku_groups') or {}
        good_map = mapping.get('state') == 'complete'
        manifest = enrichment._read(run / 'ads/manifest.json') or {}
        entities_info = (manifest.get('files') or {}).get('entities')
        entities = enrichment.verified(run / 'ads', entities_info) if good_map else None
        search_rows, search_evidence = _verified_searches(run, manifest, snapshot, market, context) if good_map else ([], [])
        market_evidence = list(mapping.get('evidence') or []) + search_evidence
        if entities is not None and entities_info:
            market_evidence.append({'path': str((run / 'ads' / entities_info['path']).resolve()), 'sha256': entities_info['sha256']})
        evidence.extend(market_evidence)
        attribution = _attribution(context, reference)
        product_groups = defaultdict(list)
        for key, product in groups.items():
            product_groups[product].append(key)
        seen_products = set()
        for product in products:
            asin, sku = product.get('asin'), product.get('sku')
            if not asin or not sku or (asin, sku) in seen_products:
                continue
            seen_products.add((asin, sku))
            selected = set(product_groups.get((asin, sku), []))
            rows = [r for r in mapping.get('campaign_rows', [])
                    if (str(r.get('campaignId')), str(r.get('adGroupId'))) in selected] if good_map else []
            metrics, issues = _metrics(rows, context, lambda r: (str(r.get('campaignId')), str(r.get('adGroupId')), r.get('date')))
            if not selected:
                issues.append('no_verified_single_sku_group')
            issues.extend(mapping.get('issues') or [])
            candidates = [c for c in costs if c.get('store') == cfg['store'] and c.get('sku') == sku
                          and c.get('asin') in (None, asin) and c.get('currency') == context.get('currency')]
            cost = candidates[0] if len(candidates) == 1 else {}
            blockers = []
            if _number(product.get('available')) in (None, 0):
                blockers.append('inventory_unavailable')
            bounds = enrichment._period(binding)
            if (not enrichment._fresh_row(product, bounds, reference)
                    or not enrichment._fresh_timestamp(product.get('inventoryCollectedAt'), bounds, reference)):
                blockers.append('inventory_not_current_or_complete')
            if cost.get('state') != 'ready' or _number(cost.get('break_even_acos')) is None or not cost.get('source_sha256'):
                blockers.append('cost_missing_or_incomplete')
            if market == 'UK' and item.get('sales_tax_basis') not in ('gross_including_vat', 'net_excluding_vat'):
                blockers.append('uk_tax_basis_unverified')
            if issues:
                blockers.append('product_performance_incomplete')
            input_hash = _hash({'version': RULE_VERSION, 'context': context, 'evidence': market_evidence,
                                'product': product, 'cost': cost, 'attribution': attribution,
                                'rules': rules, 'report_sha': mapping.get('campaign_sha256'), 'map_sha': mapping.get('mapping_sha256')})
            object_ = {'store': cfg['store'], 'market': market, 'profile_id': context.get('profile_id'), 'asin': asin, 'sku': sku}
            diagnostic = {**object_, **metrics, 'title': product.get('title'), 'currency': context.get('currency'),
                'groups': [{'campaignId': c, 'adGroupId': a} for c, a in sorted(selected)],
                'mapping_state': 'single_sku_verified' if selected else 'unallocated',
                'break_even_acos': cost.get('break_even_acos'), 'inventory': product.get('available'),
                'attribution': attribution, 'evidence': market_evidence, 'issues': sorted(set(issues)),
                'expansion_blockers': sorted(set(blockers)), 'rule_version': RULE_VERSION, 'input_hash': input_hash,
                'metric_basis': 'campaign_adgroup_report_only; purchases/clicks; no cross-layer sums',
                'zero_order_spend': metrics['spend'] if metrics['adOrders'] == 0 else None,
                'zero_order_search_terms': []}
            diagnostics.append(diagnostic)
            terms = defaultdict(list)
            for row in search_rows:
                if (str(row.get('campaignId')), str(row.get('adGroupId'))) in selected and isinstance(row.get('searchTerm'), str):
                    terms[_normal(row['searchTerm'])].append(row)
            for normal, term_rows in sorted(terms.items()):
                term = term_rows[0]['searchTerm']
                term_metrics, term_issues = _metrics(term_rows, context, lambda r: (
                    str(r.get('campaignId')), str(r.get('adGroupId')), str(r.get('keywordId')), r.get('date'), _normal(r.get('searchTerm'))))
                if term_metrics['adOrders'] == 0 and term_metrics['spend']:
                    diagnostic['zero_order_search_terms'].append({'keyword': term, **term_metrics,
                        'note': '零归因订单仅是观察线索；先查词意、商品状态和归因成熟度，不自动否词。'})
                if not term_metrics['adOrders'] or term_metrics['adSales'] is None or term_issues:
                    continue
                duplicate = _duplicate_check(entities or {}, manifest, mapping, (asin, sku), term)
                existing = duplicate['state'] == 'existing_exact'
                term_blockers = list(blockers)
                if not attribution['mature']:
                    term_blockers.append('attribution_not_mature')
                if duplicate['state'] == 'incomplete':
                    term_blockers.append('exact_inventory_incomplete')
                threshold = _number(cost.get('break_even_acos'))
                if threshold is not None and term_metrics['acos'] is not None and term_metrics['acos'] >= threshold:
                    term_blockers.append('term_acos_not_below_cost_threshold')
                if any(f['blocked'] for f in check_listing({'keyword': term}, rules)):
                    term_blockers.append('keyword_rule_blocked')
                plan = {**object_, 'keyword': term, 'recommended_ad_type': 'SP_REVIEW_EXISTING_EXACT' if existing else 'SP_MANUAL_KEYWORD_EXACT_TEST',
                    'new_ad_candidate': not existing, 'reason': '该产品已出现归因购买的搜索词，适合作为独立精准测试候选；不是效果保证。' if not existing
                    else '同一 SKU 已存在精准词，先核对现有活动状态与覆盖，不重复新建。',
                    'current': {'groups': diagnostic['groups'], 'search_term_metrics': term_metrics, 'existing_exact': duplicate['existing']},
                    'target': {'ad_product': 'SPONSORED_PRODUCTS', 'targeting': 'MANUAL', 'match_type': 'EXACT', 'keyword': term,
                               'single_sku': sku, 'budget': None, 'bid': None},
                    'current_to_target': '已有转化搜索词 → 人工审核单一 SKU 精准独立测试' if not existing else '已有精准广告 → 核对现有广告，不重复开设',
                    'budget': None, 'bid': None, 'currency': context.get('currency'),
                    'readiness': 'blocked' if term_blockers else 'review_existing' if existing else 'pending_review',
                    'blockers': sorted(set(term_blockers)), 'duplicate_check': duplicate,
                    'attribution': attribution, 'evidence': market_evidence, 'rule_version': RULE_VERSION, 'input_hash': input_hash,
                    'approval_status': 'pending approval', 'execution_status': 'not_submitted',
                    'third_party_signals': _sif_signals(cfg, snapshot, market, asin, sku, term)}
                plan['recommendation_hash'] = _hash(plan)
                plan['id'] = 'ad-plan:' + plan['recommendation_hash'][:24]
                plans.append(plan)
    queries['product_ad_diagnostics'] = _query('产品广告诊断', diagnostics, evidence,
        '只汇总已验证单一 SKU 广告组日表现；多 ASIN/SKU 组不分摊，不跨广告层级叠加。')
    queries['ad_recommendations'] = _query('新广告待审核方案', plans, evidence,
        '账户转化搜索词形成精准测试草案；先核对同 SKU 精准重复、库存、成本及归因成熟度，预算和竞价待确认。')
    return result
