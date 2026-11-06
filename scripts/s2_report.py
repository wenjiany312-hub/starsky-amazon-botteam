#!/usr/bin/env python3
"""Fixed, offline S2 review reports. Rendering never edits or approves inputs."""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import html
import importlib.util
import io
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HOME = Path(__file__).resolve().parents[1]
TEMPLATE_VERSION = 's2-unified-3.0'
SCHEMA_VERSION = 's2-report-2'
KEYWORD_SECTIONS = ['decision', 'tiers', 'placement', 'advertising', 'enrichment', 'diagnostics', 'worksheet', 'evidence']
KEYWORD_NAV = ['结论', '核心与分层', '埋词位置', '广告分池', '补数与季节', '词库体检', '选词工作表', '证据']
LISTING_SECTIONS = ['summary', 'identity', 'bullets', 'description', 'search_terms', 'qa', 'scenes', 'coverage', 'changes', 'evidence']
LISTING_NAV = ['概况', '标题亮点', '五点', '描述', 'ST', 'Q&A', '场景库', '埋词', '版本对比', '证据']
FIELD_LABELS = {'title': 'Title · 标题', 'item_highlights': 'Item Highlights · 商品亮点',
                'description': 'Product Description · 长描述', 'search_terms': 'Search Terms · 后台搜索词',
                **{f'bullets.{i}': f'B{i} · 五点描述' for i in range(1, 6)},
                **{f'qa.{i}.{p}': f'Q{i} · {"问题" if p == "question" else "回答"}' for i in range(1, 11) for p in ('question', 'answer')}}
POOL_LABELS = {'title_candidates': '标题', 'highlights_candidates': '亮点', 'bullet_feature': '五点·功能',
               'bullet_scenario': '五点·场景', 'description_aplus': '描述 / A+', 'search_terms_candidates': 'ST',
               'attributes': '属性', 'qa': '买家问答', 'fitment_review': '兼容性复核',
               'exact': '精准测试候选', 'exact_p1': '新品精准测试候选', 'exact_p2': '精准测试候选',
               'phrase_feature': '功能词组扩展', 'phrase_scenario': '场景词组扩展', 'broad_discovery': '探索观察'}
DISPOSITION_LABELS = {'retained': '保留（可用）', 'observe': '观察/待核', 'brand_isolated': '品牌隔离',
                      'compliance_negative': 'IP/合规排除', 'low_relevance_negative': '低相关排除'}
SCROLL_ROWS = 12
REVIEW_KIND = {'retained': '清洗保留', 'observe': '观察', 'brand_isolated': '品牌隔离', 'compliance_negative': 'IP/合规',
               'low_relevance_negative': '低相关'}
AD_CARD_TITLES = {'exact_p1': '一级词 · 新品精准主攻', 'exact_p2': '二级词 · 精准扩展', 'phrase_feature': '功能/材质词组扩展',
                  'phrase_scenario': '场景/人群词组扩展', 'broad_discovery': '探索观察（Broad）', 'observe': '观察 · 默认不投'}
METRIC_LABELS = {'weekly_search_volume': '周搜索量', 'monthly_search_volume': '月搜索量',
                 'search_volume': '来源搜索量', 'cpc_mid': '第三方 CPC 参考', 'cpc': '来源 CPC',
                 'cvr': '来源 CVR', 'relevance_score': '来源相关性', 'aba_rank': 'ABA 排名'}


def dependency(name, relative):
    path = HOME / relative
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def operation_runtime():
    return dependency('report_operation_rules','scripts/platform_rules.py').operation_runtime()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return 'sha256:' + hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def rules_fingerprint():
    paths = ['scripts/compliance_scan.py', 'scripts/compliance_operation.py', 'scripts/keyword_asset.py', 'scripts/platform_rules.py', 'templates/amazon_platform_rules.yaml', 'templates/compliance_base_lexicon.yaml', 'scripts/s2_report.py', 'scripts/s2_search_terms.py', 'scripts/s2_ct_mapping.py', 'skills/_keyword_system_contract/scripts/keyword_eligibility.py',
             'skills/_keyword_system_contract/scripts/keyword_package_contract.py',
             'skills/amazon-listing-copywriter/scripts/validate_listing_copy.py']
    return digest({p: hashlib.sha256(operation_runtime().read_bytes(HOME/p)).hexdigest() for p in paths})


def load(path):
    text = Path(path).read_text(encoding='utf-8-sig')
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml
        except ImportError as exc:
            raise ValueError('YAML 需要 PyYAML；也可使用 JSON 语法，无需额外依赖。') from exc
        value = yaml.safe_load(text)
    if not isinstance(value, dict):
        raise ValueError('报告输入必须为 JSON/YAML object')
    return value


def strings(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [str(x) for x in value if x is not None and str(x)]
    return [str(value)]


def field_texts(sku):
    out = {key: sku.get(key) for key in ('title', 'item_highlights', 'search_terms')}
    bullets = sku.get('bullets', [])
    for i in range(5):
        out[f'bullets.{i+1}'] = bullets[i] if isinstance(bullets, list) and i < len(bullets) else None
    out['description'] = dependency('s2_description_reader','skills/amazon-listing-copywriter/scripts/validate_listing_copy.py').description_value(sku)
    out['description_html'] = sku.get('description_html')
    if isinstance(sku.get('attributes'), dict):
        out.update({'attributes.'+str(k):v for k,v in sku['attributes'].items()})
    if isinstance(sku.get('qa'), list):
        for i, qa in enumerate(sku['qa'], 1):
            for part in ('question', 'answer'):
                out[f'qa.{i}.{part}'] = qa.get(part) if isinstance(qa, dict) else None
    return out


def seasonality_model(context):
    result = []
    # An explicit as_of is required; wall clock is never a hidden input.
    as_of = str(context.get('as_of') or '')
    for raw in (context.get('seasonality') or {}).get('series', []):
        months, warnings = {}, []
        for point in raw.get('points', []):
            month, value = str(point.get('month') or ''), point.get('value')
            if not re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])', month):
                warnings.append('存在无效月份，已排除')
                continue
            if month in months:
                warnings.append(f'{month} 重复，不能重复计入周期')
                months[month] = None
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                value = None
            months[month] = value
        if months:
            lo, hi = min(months), max(months)
            first = int(lo[:4])*12 + int(lo[5:])-1
            last = int(hi[:4])*12 + int(hi[5:])-1
            for number in range(first, last+1):
                months.setdefault(f'{number//12:04}-{number%12+1:02}', None)
        complete = []
        for year in sorted({m[:4] for m in months}):
            if as_of and year < as_of[:4] and all(months.get(f'{year}-{m:02}') is not None for m in range(1, 13)):
                complete.append(year)
        consecutive = any(int(b) == int(a) + 1 for a, b in zip(complete, complete[1:]))
        if not consecutive:
            warnings.append('不足两个连续完整年度；不据此断言稳定季节峰值')
        if not all(raw.get(k) for k in ('source', 'metric', 'unit')):
            warnings.append('来源、指标语义或单位不全，暂不作同比')
            consecutive = False
        comparison, peaks = None, []
        if consecutive:
            previous, current = [(a, b) for a, b in zip(complete, complete[1:]) if int(b) == int(a)+1][-1]
            paired = []
            for month in range(1, 13):
                before, after = months[f'{previous}-{month:02}'], months[f'{current}-{month:02}']
                paired.append({'month': month, 'before': before, 'after': after,
                               'yoy_percent': round((after/before-1)*100, 2) if before else None})
            comparison = {'years': [previous, current], 'months': paired,
                          'note': '同源同指标逐月同比；基期为零不计算百分比，不代表销量或未来预测。'}
            for year in (previous, current):
                peak = max(months[f'{year}-{month:02}'] for month in range(1, 13))
                peaks.append({'year': year, 'value': peak,
                              'months': [m for m, v in sorted(months.items()) if m.startswith(year+'-') and v == peak] if peak else []})
        result.append({**{k: raw.get(k) for k in ('keyword_id', 'source', 'metric', 'unit')},
                       'points': [{'month': m, 'value': v} for m, v in sorted(months.items())],
                       'complete_years': complete, 'two_complete_years': consecutive,
                       'year_comparison': comparison, 'observed_peaks': peaks,
                       'warnings': list(dict.fromkeys(warnings)),
                       'conclusion': raw.get('conclusion') or '需结合促销、缺货及产品适配人工解释变化。'})
    return result


@operation_runtime().boundary
def build_model(package, listing=None, context=None, baseline=None, scenes=None, *, series_root=None):
    context = context or {}
    st_completion=dependency('s2_st_completion','scripts/s2_search_terms.py')
    series_root=st_completion.logical_series_root(series_root)
    for label, doc in [('context', context), ('listing', listing or {})]:
        for key in ('product_id', 'marketplace'):
            if doc.get(key) and package.get(key) and str(doc[key]).casefold() != str(package[key]).casefold():
                raise ValueError(f'{label}.{key} 与词包身份不一致，不能混合不同产品或站点')
    source_listing=copy.deepcopy(listing)
    completion=st_completion.complete_listing(listing,package,context,series_root=series_root)
    listing=completion['listing']
    guard = dependency('s2_eligibility', 'skills/_keyword_system_contract/scripts/keyword_eligibility.py')
    compliance_sessions={}
    def current_layer_context(sku=None):
        key=sku if isinstance(sku,str) else None
        if key not in compliance_sessions:
            compliance_sessions[key]=guard._compliance_module().ScanSession(series_root,key,package.get('marketplace'))
        return {'series_root':str(series_root) if series_root else None,'_compliance_session':compliance_sessions[key]}
    validator = dependency('s2_copy_validator', 'skills/amazon-listing-copywriter/scripts/validate_listing_copy.py')
    effective_rules = validator.listing_rules(context.get('listing_rules'))
    contract = dependency('s2_package_contract', 'skills/_keyword_system_contract/scripts/keyword_package_contract.py')
    facts = {str(f['id']): f for f in context.get('facts', []) if isinstance(f, dict) and f.get('id')}
    if len(facts) != len(context.get('facts', [])):
        raise ValueError('事实 ID 缺失或重复，不能确定引用的是哪条证据')
    confirmed = {fid for fid, fact in facts.items() if fact.get('status') in {'verified', 'user_confirmed', 'page_extracted', 'image_extracted', 'user_selected_and_image_extracted', 'supplier_confirmed', 'sample_measured'}
                 and fact.get('source') and fact.get('copy_permission') != '禁止'}
    synthetic_demo = package.get('run_mode') == 'demo_direct_run' and context.get('fixture_kind') == 'synthetic_not_product_evidence'
    if synthetic_demo:
        confirmed |= {fid for fid, fact in facts.items() if fact.get('status') == 'synthetic' and fact.get('source')}
    decisions = context.get('decisions') or {}
    restrictions = guard.package_restrictions(package) + (context.get('restrictions') or [])
    keywords, global_issues, effective_records, confirmed_records = [], [], {}, set()
    for receipt in completion['receipt']['variants']:
        # target_gap = every safe new word used without repetition; it is shown on the ST field, not a to-do.
        if receipt['status'] not in {'complete','target_gap'}:
            global_issues.append(f'SKU {receipt["sku"]} · ST自动补齐未就绪：{receipt["output_bytes"]}/{receipt["maximum"]}字节；{receipt["status"]}。保留输入风险，需使用有许可来源修正，不索取用户填词。')
    seen_ids = set()
    for rec in package.get('records', []):
        kid = str(rec.get('keyword_id') or '')
        if not kid or kid in seen_ids:
            raise ValueError('keyword_id 缺失或重复，不能生成可追溯报告')
        seen_ids.add(kid)
        decision = decisions.get(kid, {})
        effective = copy.deepcopy(rec)
        # Context can add facts and a restriction, never erase source restrictions.
        for key in ('product_match', 'product_qualification'):
            if key in decision and key not in effective:
                effective[key] = decision[key]
        if decision.get('fact_refs'):
            effective['fact_refs'] = strings(rec.get('fact_refs')) + strings(decision['fact_refs'])
        eligibility = guard.evaluate_record(effective, restrictions, strict_product_evidence=True,
                    authoritative_context={**current_layer_context(),'product_id': package.get('product_id'), 'marketplace': package.get('marketplace'),
                                           'sku': package.get('sku') or package.get('seller_skus'),
                                           'ct_mapping': (package.get('analysis') or {}).get('ct_mapping')})
        refs = strings(decision.get('fact_refs') or rec.get('fact_refs'))
        reasons = strings(eligibility.get('reasons'))
        qualified = bool(refs) and set(refs) <= confirmed
        effective_records[kid] = effective
        if qualified:
            confirmed_records.add(kid)
        if eligibility['status'] == 'usable' and not qualified:
            eligibility = {**eligibility, 'status': 'pending', 'listing_eligible': False, 'ad_eligible': False}
            reasons.append('缺少本品已确认事实引用，先保留研究记录')
        if decision.get('reason'):
            reasons.insert(0, decision['reason'])
        placements = [POOL_LABELS.get(k, k) for k, ids in (package.get('listing_pools') or {}).items() if kid in ids]
        ads = [POOL_LABELS.get(k, k) for k, ids in (package.get('pools') or {}).items() if kid in ids and k in contract.POSITIVE_AD_POOLS]
        keywords.append({'keyword_id': kid, 'keyword': rec.get('keyword') or rec.get('normalized_keyword') or '',
                         'translation_zh': rec.get('cn_translation') or '',
                         'product_root': decision.get('product_root') or rec.get('product_root') or '',
                         'primary_type': decision.get('primary_type') or rec.get('primary_type') or '待分类',
                         'tags': decision.get('tags') or rec.get('tags') or rec.get('six_dimensions') or [],
                         'status': eligibility['status'], 'listing_eligible': eligibility['listing_eligible'],
                         'ad_eligible': eligibility['ad_eligible'], 'fact_refs': refs,
                         'copy_permission': eligibility.get('copy_permission') or ('可写' if eligibility['listing_eligible'] else '禁止'),
                         'listing_destinations': placements if eligibility['listing_eligible'] else [],
                         'ad_uses': list(dict.fromkeys(ads)) if eligibility['ad_eligible'] else [],
                         'proposed_listing_destinations': placements, 'proposed_ad_uses': ads,
                         # Values only: semantics, periods and units live once per source in the package.
                         'metrics': {k: (m.get('value') if isinstance(m, dict) else m) for k, m in (rec.get('metrics') or {}).items()},
                         'lineage': [f'{l.get("source_id")}#{l.get("sheet", "")}:{l.get("row", "")}' if isinstance(l, dict) else str(l)
                                     for l in (rec.get('lineage') or [])], 'reasons': list(dict.fromkeys(reasons)),
                         'source_disposition': rec.get('disposition'),
                         'restricted': bool(rec.get('claim_restricted') or rec.get('brand_hits')
                                            or rec.get('disposition') in {'brand_isolated', 'compliance_negative'}),
                         'disposition_reason': (rec.get('retention_reason') or {}).get('text') or '',
                         'needs_review': bool(rec.get('needs_review')), 'core_hits': rec.get('core_hits') or [],
                         'pools': [k for k, ids in {**(package.get('listing_pools') or {}), **(package.get('pools') or {})}.items() if kid in ids]})
    counts = Counter(r['status'] for r in keywords)
    listings = []
    old_skus = (baseline or {}).get('skus', [])
    old_index = {str(s.get('sku') or s.get('asin') or ''): s for s in old_skus}
    sku_ids = set()
    for sku in (listing or {}).get('skus', []):
        sku_id = str(sku.get('sku') or sku.get('asin') or '')
        if not sku_id or sku_id in sku_ids:
            raise ValueError('Listing 需要唯一 SKU/ASIN，不能按顺序合并文案')
        sku_ids.add(sku_id)
        texts = field_texts(sku)
        brand = str(sku.get('brand') or context.get('brand') or '').strip()
        old_texts = field_texts(old_index[sku_id]) if sku_id in old_index else {}
        review_meta = sku.get('review_metadata') or {}
        if not isinstance(review_meta, dict) or not isinstance(review_meta.get('fields', {}), dict):
            raise ValueError('review_metadata.fields 必须是字段名到审阅注解的对象')
        annotations = review_meta.get('fields') or {}
        fields, changes, issues = [], [], []
        remaining_checks = []
        bound_product = (context.get('sku_product_map') or {}).get(sku_id)
        if bound_product and str(bound_product) != str(package.get('product_id')):
            raise ValueError('SKU 与当前产品的映射不符：' + sku_id)
        if not bound_product or context.get('product_id') != package.get('product_id'):
            issues.append('缺少本轮产品与 SKU 的明确映射，尚不能完成文案验收')
        allowed_skus = strings(package.get('seller_skus') or package.get('sku') or package.get('seller_sku'))
        if allowed_skus and sku_id not in allowed_skus:
            raise ValueError('Listing SKU 不属于词包的商品范围：' + sku_id)
        sku_keywords = []
        for row in keywords:
            kid = row['keyword_id']
            scoped_eligibility = guard.evaluate_record(effective_records[kid], restrictions, strict_product_evidence=True,
                    authoritative_context={**current_layer_context(sku_id),'product_id': package.get('product_id'), 'marketplace': package.get('marketplace'), 'sku': sku_id,
                                           'ct_mapping': (package.get('analysis') or {}).get('ct_mapping')})
            sku_keywords.append({**row, 'listing_eligible': scoped_eligibility['listing_eligible'] and kid in confirmed_records})
        index = {r['keyword_id']: r for r in sku_keywords}
        for key, raw in texts.items():
            text = raw if isinstance(raw, str) else ''
            visible = validator.description_plaintext(text) if key == 'description' else text
            note = annotations.get(key) or {}
            if not isinstance(note, dict):
                raise ValueError(f'review_metadata.fields.{key} 必须是对象')
            refs = strings(note.get('fact_refs'))
            field_issues = strings(note.get('pending_items'))
            field_notes = []
            if not visible.strip():
                field_issues.append('尚未提供英文正文')
            if not note.get('translation_zh'):
                field_issues.append('缺中文审阅译文')
            if not refs or not set(refs) <= confirmed:
                field_issues.append('本品事实引用缺失、未确认或找不到来源')
            if note.get('review_status') != 'reviewed':
                field_issues.append('尚未人工审阅')
            scoped = guard.evaluate_record({'keyword': visible, 'disposition': 'retained'}, restrictions,
                    authoritative_context={**current_layer_context(sku_id),'product_id': package.get('product_id'), 'marketplace': package.get('marketplace'), 'sku': sku_id})
            for rule in scoped.get('matched_restrictions', []):
                detail=restrictions[rule] if isinstance(rule,int) and 0<=rule<len(restrictions) else {}
                # R02 G2: newly source-bound specifications may be written while
                # preserving historical sample-pending restrictions verbatim.
                # This narrow exception cannot approve a performance assertion.
                terms=strings(detail.get('terms'))
                performance=re.compile(r'waterproof|resistant|proof|durable|guarantee|防水|耐磨|防刺穿|保证',re.I)
                supports=[fid for fid in refs if fid.startswith(('CT:material','CT:dimensions','CT:weight','CT:colors','CT:features.','CT:structure.features.'))
                          and facts.get(fid,{}).get('status') in {'page_extracted','image_extracted','user_selected_and_image_extracted','sample_measured','supplier_confirmed'}
                          and facts[fid].get('source') and re.fullmatch(r'(?:sha256:)?[a-fA-F0-9]{64}',str(facts[fid].get('sha256') or ''))
                          and facts[fid].get('copy_permission')=='可写'
                          and all(guard.term_matches(str(facts[fid].get('text') or ''),term) for term in terms)]
                if terms and supports and detail.get('kind')=='claim' and detail.get('status')=='pending' and not performance.search(' '.join(terms)):
                    field_notes.append('页面/图片规格可写；上架前样品核对。历史待核记录保留：'+str(detail.get('reason') or detail.get('claim'))+'；本轮来源：'+', '.join(supports))
                    continue
                field_issues.append('本品声明限制待核：' + str(detail.get('claim') or detail.get('terms') or rule)
                                    + '；'+str(detail.get('reason') or '见来源限制记录'))
            # Only restricted research terms are banned from copy; a low-relevance record like 'box' is not.
            for row in sku_keywords:
                if row['restricted'] and guard.term_matches(visible, row['keyword']):
                    field_issues.append('正文含受限词（品牌/IP/待证声明）：' + row['keyword'])
            coverage = []
            for kid in strings(note.get('keyword_ids')):
                if kid not in index:
                    field_issues.append('关键词引用不存在：' + kid)
                    continue
                row = index[kid]
                # Amazon indexes ST together with the visible fields, so an ST plan is met by words anywhere in the listing.
                scope = visible
                match = validator.keyword_coverage(scope, row['keyword'])
                coverage.append({'keyword_id': kid, 'keyword': row['keyword'], **match})
                if not row['listing_eligible']:
                    field_issues.append('引用了待核/排除关键词：' + row['keyword'])
                if match['status'] == 'absent':
                    field_issues.append('计划埋词未出现在正文：' + row['keyword'])
                elif key in {'title','item_highlights'} and match['status'] != 'exact':
                    field_issues.append('标题/亮点要求完整词组出现，当前仅分词覆盖：'+row['keyword'])
            assigned = {r['keyword_id'] for r in coverage}
            for row in sku_keywords:
                if row['listing_eligible'] and row['keyword_id'] not in assigned:
                    match = validator.keyword_coverage(visible, row['keyword'])
                    if match['status'] != 'absent':
                        coverage.append({'keyword_id': row['keyword_id'], 'keyword': row['keyword'], 'assignment': 'detected_in_copy', **match})
            field = {'field': key, 'label': FIELD_LABELS.get(key, key), 'en': visible, 'raw': text,
                     'translation_zh': note.get('translation_zh') or '', 'chars': len(visible) if isinstance(raw, str) else None,
                     'raw_chars': len(text) if isinstance(raw, str) else None, 'utf8_bytes': len(visible.encode('utf-8')) if isinstance(raw, str) else None,
                     'coverage': coverage, 'notes':field_notes, 'buyer_questions': strings(note.get('buyer_questions')),
                     'coverage_counts': {'complete': sum(c['status']=='exact' for c in coverage),
                                         'tokens_only': sum(c['status']=='tokens' for c in coverage)},
                     'rule': title_rule(effective_rules['title'], brand) if key == 'title' else effective_rules.get('bullets' if key.startswith('bullets.') else key, {}),
                     'fact_refs': refs, 'voc_refs': strings(note.get('voc_refs')),
                     'fact_summary': [fid + ' · ' + str(facts[fid].get('text') or '事实内容未填写') + ' · 来源：' + str(facts[fid].get('source') or '未提供')
                                      if fid in facts else fid + ' · 未找到事实' for fid in refs], 'issues': field_issues}
            fields.append(field)
            if key=='title':
                field['notes'].append(brand_note(field['rule'], visible))
            if key=='search_terms':
                receipt=sku.get('search_terms_completion') or {}
                field['notes'].append(f'ST {receipt.get("output_bytes")}/{effective_rules["search_terms"]["max"]} 字节；自动补齐：'+('已批准核心词回退'+('（含重复）' if receipt.get('core_repetition_used') else '') if receipt.get('fallback_used') else '优先新词')+'。达到长度不代表算法增益或业务验收。')
                if receipt.get('status')=='target_gap':
                    field['notes'].append('可用增量词不足：已写完全部有来源、未在前台出现、未受限的新词，不重复凑长度；'
                        '受限候选：'+('、'.join((receipt.get('shortfall') or {}).get('blocked_candidates') or []) or '无'))
            issues.extend(f'{key}: {i}' for i in field_issues)
            if key in old_texts and old_texts[key] != raw:
                changes.append({'field': key, 'before': old_texts[key], 'after': raw,
                                'reason': note.get('change_reason') or '待补改动理由',
                                'coverage_before': [{ 'keyword_id': r['keyword_id'], **validator.keyword_coverage(str(old_texts[key] or ''), r['keyword'])} for r in coverage],
                                'coverage_after': coverage})
        checked = validator.validate_sku(sku, strings((listing or {}).get('blacklist_terms')), profile='r02_s2', rules=effective_rules, series=series_root, compliance_evidence={**context, **(context.get('compliance_evidence') or {}), 'sku':sku_id, 'root':str(series_root) if series_root else context.get('root')})
        review = validator.validate_review_metadata(sku, package.get('records', []))
        by_field = {f['field']: f for f in fields}
        for check in checked['checks']:
            if check['level'] not in {'fail', 'warn'}:
                continue
            info = check.get('details') or {}
            targets = strings(info.get('field')) + list((info.get('fields') or {}).keys())
            name = check['check']
            if name.startswith('search_terms'):
                targets.append('search_terms')
            elif name.startswith('highlights'):
                targets.append('item_highlights')
            elif name.startswith('title'):
                targets.append('title')
            elif info.get('bullet_index'):
                targets.append(f'bullets.{info["bullet_index"]}')
            elif name == 'bullet_length_balance':
                targets += [f'bullets.{i}' for i in range(1, 6)]
            message = check['message']
            if name == 'bullet_length_target':
                length = (by_field.get(f'bullets.{info.get("bullet_index")}') or {}).get('chars')
                message = f'五点长度 {length} 字符，项目目标 '+ '–'.join(map(str,effective_rules['bullets']['target']))+'：补具体规格、结构细节或使用场景，写到目标区间。'
            elif name == 'bullet_length_balance':
                message = f'五条长度相差 {info.get("spread")} 字符，建议控制在约 60 以内。'
            elif name == 'search_terms_fill':
                unused = unused_st_tokens(texts, sku_keywords)
                if not unused:
                    # Every eligible word is already in the visible copy or ST: short ST is exhausted, not under-filled.
                    by_field['search_terms'].setdefault('notes', []).append(
                        f'ST {info.get("bytes")}/{effective_rules["search_terms"]["max"]} 字节：合格新词已用尽；项目目标缺口仍保留，不以无关词填充。')
                    message = '合格新词已用尽，ST 未达到项目目标；不代表平台规则失败或目标已通过。'
                else: message = (f'ST 只用了 {info.get("bytes")}/{effective_rules["search_terms"]["max"]} 字节，还有 {len(unused)} 个合格新词没用上：'
                           + ' '.join(unused[:40]) + '（不重复标题/五点，不放品牌与待证词）。')
            elif name == 'search_terms_visible_overlap':
                message = 'ST 与前台复用词：' + '、'.join(info.get('tokens', []))+'；自动回退可复用已批准核心词，避免重复仍是质量建议，不代表算法增益。'
            elif name == 'description_length_target':
                message = f'长描述 {info.get("chars")} 字符，项目目标 '+ '–'.join(map(str,effective_rules['description']['target']))+'：按“是什么→解决什么→规格细节→使用场景→使用与包装”补足。'
            elif name == 'search_terms_token_repeat':
                message = 'ST 重复词：' + '、'.join(info.get('tokens', [])) + '；已批准核心词可自动回退复用，保留质量提醒，品牌和证据规则仍有效。'
            elif name == 'title_brand_reserve':
                message = (f'品牌未定：标题 {info.get("chars")} 字符，加上品牌预留 {info.get("reserve")} 字符会超过上限；'
                           f'请把标题正文写到 {info.get("room")} 字符以内，或先确定品牌。')
            elif name == 'title_brand_first':
                message = '自有品牌 ' + str(info.get('brand')) + ' 应放在标题最前面。'
            elif name.endswith('_length') or name == 'search_terms_utf8_bytes':
                message = '字段长度需修正：' + message
            elif name == 'internal_process_notes':
                message = '消费者字段含内部流程备注，请移到审阅区。'
            elif name == 'blacklist_terms':
                message = '命中已配置的禁用词：' + '、'.join(info.get('matches', []))
            attached = False
            for target in targets:
                target = target.removeprefix('review_metadata.fields.').removesuffix('.translation_zh')
                if target in by_field:
                    by_field[target]['issues'].append(message)
                    attached = True
            if not attached:
                remaining_checks.append('['+name+'] '+message)
        for f in fields:
            f['issues'] = list(dict.fromkeys(f['issues']))
        if checked['status'] == 'fail':
            issues.extend(c['message'] for c in checked['checks'] if c['level'] == 'fail')
        qa_count = len(sku['qa']) if isinstance(sku.get('qa'), list) else 0
        if qa_count != effective_rules['qa']['count']:
            issues.append(f'S2 要求 {effective_rules["qa"]["count"]} 条 Q&A（供 S3 高级 A+ 问答模块），当前 {qa_count} 条')
            remaining_checks.append('[qa_count] '+issues[-1])
        card_issues={field['field']+': '+message for field in fields for message in field['issues']}
        check_messages={check['message'] for check in checked['checks']}
        count_message=issues[-1] if qa_count != effective_rules['qa']['count'] else None
        remaining_checks += [message for message in issues if message not in card_issues and message not in check_messages and message!=count_message]
        remaining_checks += ['[review.'+check['check']+'] '+check['message'] for check in review['checks']
                             if check['level']!='pass' and (check.get('details') or {}).get('field') not in by_field]
        listings.append({'sku': sku_id, 'variant':sku.get('variant') or sku_id, 'asin': sku.get('asin'), 'fields': fields, 'changes': changes, 'qa_count': qa_count,
                         'qa_intents': [q.get('intent') for q in (sku.get('qa') if isinstance(sku.get('qa'),list) else []) if isinstance(q,dict)], 'qa_required':effective_rules['qa']['count'],
                         'sku_level_issues':list(dict.fromkeys(remaining_checks)),
                         'baseline_available': sku_id in old_index, 'issues': list(dict.fromkeys(issues)),
                         'validation': checked, 'review_validation': review})
    package_check = contract.validate_package(package,series=series_root)
    if package_check.get('errors'):
        global_issues.append('词包合约仍有缺口；报告可供复核，不代表阶段验收通过')
    if not context.get('policy_profile'):
        global_issues.append('未指定站点/类目的文案规则配置')
    elif context['policy_profile'] != 'US_non_media_2026_07' or str(package.get('marketplace')).upper() != 'US':
        global_issues.append('该站点/类目规则适用性待核；当前计数只采用项目保守上限，不宣称符合全部后台要求。')
    if synthetic_demo:
        global_issues.append('合成演示：事实和指标为模板测试输入，不是实际商品证据，不可上架/投放。')
    scene_words = [w for s in scene_model(scenes, keywords) for w in s['keywords']]
    missing = sorted({w['ref'] for w in scene_words if w['status'] == 'missing'})
    if missing:
        global_issues.append(f'场景库引用了 {len(missing)} 个当前词包里没有的关键词（多为旧词包 ID），需按当前词包重建场景库')
    # A word that still exists but is pending/excluded is not the scene's real support word either;
    # a scene "backed" entirely by such words silently loses its evidence with no signal in the report.
    unusable = sorted({f"{w['keyword']}（{w['status']}）" for w in scene_words if w['keyword'] and w['status'] != 'usable'})
    if unusable:
        global_issues.append(f'场景库引用了 {len(unusable)} 个当前词包里存在但不可用（pending/excluded）的关键词，需换成可用词：' + '；'.join(unusable[:20]))
    if not listings:
        global_issues.append('尚未提供 Listing，保留固定缺口页')
    if any(s['issues'] or s['review_validation']['status'] != 'pass' for s in listings):
        global_issues.append('文案仍有逐字段待处理项')
    model = {'schema_version': SCHEMA_VERSION, 'template_version': TEMPLATE_VERSION, 'rules_fingerprint': rules_fingerprint(),
             'fingerprints': {'package': digest(package), 'listing': digest(listing), 'source_listing':digest(source_listing), 'context': digest(context), 'baseline': digest(baseline), 'scenes': digest(scenes)},
             'effective_listing':listing,'search_terms_completion':completion['receipt'],
             'generation':{'series_root':str(Path(series_root).resolve()) if series_root else None},
             'meta': {'product_name': context.get('product_name') or package.get('product_id') or '待填写产品',
                      'edition': context.get('edition') or 'operating', 'sp_api_connected': context.get('sp_api_connected') is True,
                      'business_execution': context.get('business_execution') is True, 'replay_mode': context.get('replay_mode'),
                      'marketplace': context.get('marketplace') or package.get('marketplace') or '待填写站点',
                      'source': package.get('source_mode'), 'package_id': package.get('package_id'),
                      'package_version': package.get('package_version'), 'package_hash': package.get('package_hash'),
                      'listing_revision': (listing or {}).get('listing_revision') or '未标注文案版本',
                      'policy_profile': context.get('policy_profile'), 'as_of': context.get('as_of')},
             'counts': {'raw': (package.get('conservation') or {}).get('raw_rows'), 'unique': len(keywords),
                        'usable': counts['usable'], 'pending': counts['pending'], 'excluded': counts['excluded']},
             'keywords': keywords, 'listings': listings, 'facts': list(facts.values()),
             'listing_rules': effective_rules,
             'restrictions': restrictions, 'data_sources': package.get('data_sources') or [],
             'mcp': package.get('mcp') or {'status': 'unknown'}, 'seasonality': seasonality_model(context),
             'analysis': package.get('analysis') or {}, 'conservation': package.get('conservation') or {},
             'ct_mapping': dependency('s2_ct_map', 'scripts/s2_ct_mapping.py').map_ct(
                 context.get('product_ct') or {}, package.get('records') or [], listing,
                 previous=context.get('previous_ct_mapping')) if context.get('product_ct') is not None else context.get('ct_mapping') or {},
             'pools': {'listing': package.get('listing_pools') or {}, 'ads': package.get('pools') or {}},
             'negatives': [{k: n.get(k) for k in ('term', 'match_type', 'reason', 'hits', 'examples', 'status')}
                           for n in package.get('negative_records') or []],
             'asins': [a.get('asin') for a in package.get('asin_records') or []],
             'scenes': scene_model(scenes, keywords),
             'next_actions': strings(context.get('next_actions')),
             'evidence': context.get('evidence') or [],
             'validation': {'status': 'needs_review' if global_issues else 'ready_for_review',
                            'issues': global_issues,
                            'package_check': {'status': package_check.get('status'), 'errors': package_check.get('errors', []),
                                              'warning_count': len(package_check.get('warnings') or []),
                                              'computed_hash': package_check.get('computed_hash')},
                            'business_acceptance': 'not_granted', 'external_upload': 'not_authorized'}}
    permission_by_id={row['keyword_id']:row for row in keywords}
    for row in model['ct_mapping'].get('rows',[]):
        pairs=list(zip(row.get('keyword_ids',[]),row.get('keywords',[])))
        row['writable_keywords']=[term for kid,term in pairs if permission_by_id.get(kid,{}).get('listing_eligible')]
        row['research_only_keywords']=[term for kid,term in pairs if not permission_by_id.get(kid,{}).get('listing_eligible')]
    for impacted in model['ct_mapping'].get('affected_fields', []):
        for sku in listings:
            if sku['sku'] != impacted.get('sku'): continue
            for field in sku['fields']:
                if field['field'] != impacted.get('field'): continue
                issue='CT 来源或事实已变化；此字段需重写并重新审核，旧覆盖不可复用。'
                field['issues'].append(issue);sku['issues'].append(field['field']+': '+issue)
                model['validation']['status']='needs_review'
                if issue not in model['validation']['issues']: model['validation']['issues'].append(issue)
    compliance = dependency('s2_compliance', 'scripts/compliance_scan.py')
    model['compliance_context'] = context
    model['compliance'] = compliance.make_receipt(listing, series=series_root, context=context,
        rules=effective_rules, mapping=model['ct_mapping'], editorial=True)
    model['rule_review_due'] = dependency('s2_platform_rules', 'scripts/platform_rules.py').review_due()
    if model['compliance']['status'] != 'pass':
        model['validation']['status'] = 'needs_changes'
        model['validation']['issues'].append(('禁用/品牌/侵权词命中 '+str(model['compliance']['blocked_count'])+' 处；' if model['compliance']['blocked_count'] else '未命中禁用/品牌/侵权词；')
            +'另有 '+str(len(model['compliance']['editorial_failures']))+' 项写作提示（见各字段“审阅提示”和“上架前检查”）。审阅确认后再上架。')
    model['model_hash'] = digest(model)
    return model


def esc(value):
    if value is None or value == '':
        return '未提供'
    return html.escape(str(value), quote=True)


def list_html(items):
    return '<ul>' + ''.join('<li>' + esc(i) + '</li>' for i in items) + '</ul>' if items else '<p class="muted">暂无记录</p>'


def details(label, value):
    return ('<details class="fold inner"><summary>' + esc(label) + '</summary><div class="fold-body"><pre>'
            + esc(json.dumps(value, ensure_ascii=False, indent=2)) + '</pre></div></details>')


def section(sid, title, body):
    """Every h3 and folded block gets an anchor the left-side directory can jump to."""
    n = 0

    def anchor(m):
        nonlocal n
        n += 1
        label = html.escape(html.unescape(re.sub(r'<[^>]+>', '', m.group(1) or m.group(3)).strip()), quote=True)
        if m.group(1) is not None:
            return f'<h3 id="{sid}-{n}" data-toc="{label}">{m.group(1)}</h3>'
        return f'<details class="fold" id="{sid}-{n}" data-toc="{label}"{m.group(2) or ""}><summary>{m.group(3)}'
    body = re.sub(r'<h3>(.*?)</h3>|<details class="fold"( open)?><summary>(.*?)(?=<span class="fold-count">|</summary>)', anchor, body)
    return f'<section id="{sid}" data-section="{sid}"><h2>{esc(title)}</h2>{body}</section>'


def metric_html(metrics):
    parts = []
    for key, node in metrics.items():
        if not isinstance(node, dict):
            node = {'value': node}
        value = node.get('value')
        if key not in METRIC_LABELS:
            continue
        source = node.get('source') or node.get('source_id') or '见来源账本'
        period = node.get('period') or node.get('month') or node.get('window') or '未标周期'
        if isinstance(period, dict):
            period = ' / '.join(str(v) for v in period.values() if v)
        parts.append(f'<div>{esc(METRIC_LABELS[key])}：<b>{esc(value) if value is not None else "未取得"}</b><small>{esc(source)} · {esc(period)}</small></div>')
    return ''.join(parts) or '<span class="muted">无可用指标，不能当作 0</span>'


STATUS_LABELS = {'usable': '可用', 'pending': '待核', 'excluded': '排除'}
AD_PLAYS = {'exact_p1': '新品精准主攻：高相关、量靠前，先小预算 Exact 测点击与转化',
            'exact_p2': '精准扩展：量较小，受控 Exact 或放进调研组',
            'phrase_feature': '功能/材质组合：Phrase 扩展属性入口',
            'phrase_scenario': '场景/人群/对象：Phrase 找新入口',
            'broad_discovery': '干净短词根 Broad 探索，靠否词收口',
            'observe': '事实或数据不足，默认不投'}
LISTING_USES = {'title_candidates': '标题首选核心词', 'highlights_candidates': '商品亮点', 'bullet_feature': '五点·功能/材质',
                'bullet_scenario': '五点·场景/人群', 'description_aplus': '长描述 / A+ 补充', 'search_terms_candidates': '后台 ST 候选（按当前规则去重与计数）',
                'attributes': '后台属性', 'qa': '买家问答 / Rufus 问题', 'fitment_review': '兼容/尺寸类表述，写前复核'}
WORKSHEET_LIMITS = {'pending': 150, 'excluded': 100}
P1_SHOWN = 20


def num(value):
    if value is None or value == '':
        return '—'
    if isinstance(value, bool):
        return esc(value)
    if isinstance(value, int):
        return f'{value:,}'
    if isinstance(value, float):
        return f'{value:,.2f}'
    return esc(value)


def pct(value):
    return '—' if not isinstance(value, (int, float)) or isinstance(value, bool) else f'{value:.0%}'


def mv(row, name):
    node = (row.get('metrics') or {}).get(name)
    return node.get('value') if isinstance(node, dict) else node


def vol(row):
    for name in ('weekly_search_volume', 'search_volume', 'monthly_search_volume'):
        value = mv(row, name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
    return None


def by_volume(rows):
    return sorted(rows, key=lambda r: -(vol(r) or -1))


def txt(value):
    return '—' if value is None or value == '' or value == [] else esc(value)


EMPTY_CELLS = {'—', '未提供', '', '—%', '— / —'}


def table(headers, rows, cls='data'):
    if not rows:
        return '<p class="muted">暂无记录</p>'
    # A column with no data in any row is noise to the reader; drop it (the data stays in keywords.csv).
    keep = [i for i in range(len(headers)) if i == 0 or any(str(row[i]).strip() not in EMPTY_CELLS for row in rows if i < len(row))]
    headers = [headers[i] for i in keep]
    rows = [[row[i] for i in keep if i < len(row)] for row in rows]
    head = ''.join(f'<th>{esc(h)}</th>' for h in headers)
    body = ''.join('<tr>' + ''.join(f'<td data-label="{esc(h)}">{c if c != "未提供" else "—"}</td>' for h, c in zip(headers, row)) + '</tr>' for row in rows)
    box = ' scrollbox' if len(rows) > SCROLL_ROWS else ''
    return f'<div class="tablewrap{box}"><table class="{cls}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def fold(title, body, count=None, opened=False):
    """Detail that stays one click away; the summary line says what and how many."""
    badge = f'<span class="fold-count">{count}</span>' if count is not None else ''
    return f'<details class="fold"{" open" if opened else ""}><summary>{esc(title)}{badge}</summary><div class="fold-body">{body}</div></details>'


def reason_groups(keywords):
    """Shared rule reasons count as themselves; one-off review verdicts group by status and disposition."""
    shared = Counter(r['disposition_reason'] for r in keywords if r['disposition_reason'])
    groups, samples = Counter(), defaultdict(list)
    for r in keywords:
        reason = r['disposition_reason']
        if not reason:
            continue
        if shared[reason] > 1:
            key = reason
        else:
            kind = REVIEW_KIND.get(r.get('source_disposition'), r.get('source_disposition') or '未分类')
            key = f'逐词复核裁决 · {STATUS_LABELS[r["status"]]} · {kind}'
            if len(samples[key]) < 3:
                samples[key].append(r['keyword'])
        groups[key] += 1
    return [(key, n, samples.get(key, [])) for key, n in groups.most_common()]


def word(r):
    return f'<b>{esc(r["keyword"])}</b><small>{esc(r["translation_zh"])}</small>'


def examples(rows, n=8):
    return '、'.join(f'{esc(r["keyword"])}（{num(vol(r))}）' for r in by_volume(rows)[:n]) or '—'


def pool_rows(model, names, index):
    return {name: [index[k] for k in ids if k in index] for name, ids in names.items()}


def keyword_rows(rows, member=None):
    member = member or {}
    headers = ['词与中文', '周搜索量', 'CPC中 / Top3点击', '文案用途', '广告用途', '状态与理由']
    out = []
    for r in rows:
        ads = bool(r['ad_eligible'] and r['ad_uses'])
        cells = [word(r), num(vol(r)), f'{num(mv(r, "cpc_mid") or mv(r, "cpc"))} / {pct(mv(r, "top3_click_share"))}',
                 esc(' / '.join(r['listing_destinations'])) if r['listing_eligible'] else '<span class="muted">暂不进入文案</span>',
                 esc(' / '.join(r['ad_uses'])) if ads else '<span class="muted">暂不投放</span>',
                 f'<span class="tag {r["status"]}">{STATUS_LABELS[r["status"]]}</span> ' + esc(r['disposition_reason'] or '；'.join(r['reasons'][:2]))]
        out.append(f'<tr data-keyword-id="{esc(r["keyword_id"])}" data-status="{r["status"]}" data-copy="{int(r["listing_eligible"])}" data-ads="{int(ads)}"'
                   f' data-in="{esc(" ".join(member.get(r["keyword_id"], [])))}">'
                   + ''.join(f'<td data-label="{h}">{c}</td>' for h, c in zip(headers, cells)) + '</tr>')
    return ('<div class="tablewrap scrollbox tall"><table id="keyword-table"><thead><tr>' + ''.join(f'<th>{h}</th>' for h in headers)
            + '</tr></thead><tbody>' + ''.join(out) + '</tbody></table></div>')


def worksheet_rows(model):
    groups = {s: by_volume([r for r in model['keywords'] if r['status'] == s]) for s in STATUS_LABELS}
    shown = groups['usable'] + groups['pending'][:WORKSHEET_LIMITS['pending']] + groups['excluded'][:WORKSHEET_LIMITS['excluded']]
    return shown, {s: len(v) for s, v in groups.items()}


def keyword_view(model):
    """Everything the keyword report shows, computed once so HTML and Markdown say the same thing."""
    index = {r['keyword_id']: r for r in model['keywords']}
    analysis, counts = model['analysis'], model['counts']
    usable = by_volume([r for r in model['keywords'] if r['status'] == 'usable'])
    listing = pool_rows(model, model['pools']['listing'], index)
    ads = pool_rows(model, model['pools']['ads'], index)
    mcp_out = analysis.get('mcp') or {}
    season = analysis.get('seasonality') or {}
    conclusions = []
    if usable:
        leaders = '、'.join(f'{r["keyword"]}（{vol(r) or 0:,}）' for r in usable[:3])
        conclusions.append(f'本品可用 {len(usable)} 个词，周搜索量合计 {sum(vol(r) or 0 for r in usable):,}（选定词集合，非去重买家）；'
                           f'量最大的入口：{leaders}。')
    else:
        conclusions.append('当前没有可直接用于文案/广告的本品词：先处理下方待核事项，不要用待核词硬写。')
    if season.get('status') == 'from_mcp':
        peaks = '、'.join(f'{m} 月（{n} 词）' for m, n in season.get('peak_months', [])[:3])
        conclusions.append(f'季节：{season.get("keywords")} 个主词的历史峰值集中在 {peaks}；强季节 {season.get("strength_distribution", {}).get("高", 0)} 词。')
    elif season:
        conclusions.append('季节：' + str(season.get('reason') or '未取得趋势证据，不判断全年峰值。'))
    for c in (analysis.get('claim_unlock') or [])[:1]:
        if c.get('weekly_volume'):
            conclusions.append(f'声明限制「{c["label"]}」挡住了 {c["keywords"]} 个本品词、周搜索量 {c["weekly_volume"]:,}：样品实测通过即可解锁。')
    conc = (analysis.get('traffic_concentration') or {}).get('retained') or {}
    if conc.get('status') == 'computed':
        conclusions.append(f'流量结构：前 10 个可用词占 {pct(conc.get("top10_share"))}，覆盖 80% 流量需要 {conc.get("keywords_to_80pct")} 个词。')
    return {'index': index, 'usable': usable, 'listing': listing, 'ads': ads, 'mcp': mcp_out, 'season': season,
            'conclusions': conclusions, 'actions': model['next_actions'] or model['validation']['issues'][:3],
            'conc': conc, 'counts': counts}


def core_rows(model, view):
    roots = [f for f in model['facts'] if str(f.get('id', '')).startswith('profile.core_roots:')]
    trends = {str(t.get('keyword')).casefold(): t for t in view['mcp'].get('root_trends') or []}
    by_word = {r['keyword'].casefold(): r for r in model['keywords']}
    rows = []
    for f in roots:
        root = f['id'].split(':', 1)[1]
        rec, trend = by_word.get(root.casefold()), (trends.get(root.casefold()) or {}).get('latest') or {}
        rows.append([f'<b>{esc(root)}</b>', num(vol(rec)) if rec else '未在导出中', STATUS_LABELS.get(rec['status'], '—') if rec else '—',
                     num(trend.get('keyword_search_volume')), num(trend.get('ext_search_volume')),
                     esc(trend.get('exact_keyword_demand_pct') or '—'), esc(trend.get('longtail_demand_pct') or '—'),
                     esc(f.get('status_reason') if f.get('status') == 'pending' else '已确认')])
    return rows


def unlock_rows(items):
    return [[esc(i.get('label')), num(i.get('keywords')), num(i.get('weekly_volume')), esc('、'.join(i.get('examples') or []))] for i in items]


def use_card(title, note, rows, pool, chips=10):
    """One place a word can go: how many, how to use them, the biggest words as chips; the full list is the worksheet filtered to this pool."""
    rows = by_volume(rows)
    chip = ''.join(f'<span class="chip" title="{esc(r["translation_zh"])}">{esc(r["keyword"])}<small>{num(vol(r))}</small></span>'
                   for r in rows[:chips]) or '<span class="muted">暂无候选</span>'
    full = ''
    if len(rows) > chips:
        full = (f'<button type="button" class="see-all" data-show-pool="{esc(pool)}" data-label="{esc(title)}">'
                f'在选词工作表查看全部 {len(rows)} 个 →</button>')
    return (f'<article class="use-card"><div class="use-head"><h3>{esc(title)}</h3><span class="fold-count">{len(rows)} 词</span></div>'
            + (f'<p class="muted">{esc(note)}</p>' if note else '') + f'<div class="chips">{chip}</div>{full}</article>')


def render_keyword(model):
    view = keyword_view(model)
    counts, analysis, idx = model['counts'], model['analysis'], view['index']
    kpis = '<div class="kpirow">' + ''.join(f'<div class="kpi"><small>{label}</small><strong>{num(counts[k])}</strong></div>'
                                            for k, label in [('raw', '原始行'), ('unique', '唯一词'), ('usable', '本品可用'), ('pending', '待核'), ('excluded', '排除')]) + '</div>'
    tier_of = {kid: name for name in ('exact_p1', 'exact_p2') for kid in model['pools']['ads'].get(name, [])}
    top = [[word(r), num(vol(r)), esc({'exact_p1': '一级词', 'exact_p2': '二级词'}.get(tier_of.get(r['keyword_id']), '未分级')),
            esc(' / '.join(LISTING_USES.get(d, d) for d in r['listing_destinations'][:3]) or '—'),
            esc(' / '.join(POOL_LABELS.get(u, u) for u in r['ad_uses'][:2]) or '—')] for r in view['usable'][:10]]
    sections = [section('decision', '01 · 结论与现在要处理的事', kpis + list_html(view['conclusions'])
                        + '<h3>主力词速览 · 周搜索量前 10 的可用词</h3>' + table(['词与中文', '周搜索量', '层级', '文案位置', '广告池'], top)
                        + '<h3>现在要处理的事</h3>' + list_html(view['actions'])
                        + '<p class="muted">可用资格按本品事实与 S1 限制判定，不按流量高低；广告用途是测试候选，仍需利润、库存、预算与人工审批。</p>')]
    rules = analysis.get('pool_rules') or {}
    p1 = by_volume(view['ads'].get('exact_p1', []))
    p2 = by_volume(view['ads'].get('exact_p2', []))
    tier_headers = ['词与中文', '周搜索量', 'CPC 中', 'Top3 点击集中', '24h 转化(第三方)', '文案位置']
    tier = lambda rows: [[word(r), num(vol(r)), num(mv(r, 'cpc_mid') or mv(r, 'cpc')), pct(mv(r, 'top3_click_share')),
                          pct(mv(r, 'cvr_24h')), esc(' / '.join(r['listing_destinations']) or '—')] for r in rows]
    sections.append(section('tiers', '02 · 核心词 → 一级词 → 二级词',
        '<h3>核心词根（产品身份）</h3>' + table(['词根', '导出周量', '状态', 'MCP 精确词量', '词根总量', '精确占比', '长尾占比', '事实状态'], core_rows(model, view))
        + f'<h3>一级词 Exact-P1 · 共 {len(p1)} 个</h3><p class="muted">门槛：周搜索量 ≥ {num(rules.get("p1_threshold_weekly"))}（{esc(rules.get("p1_basis"))}）；'
          f'下表是周搜索量前 {min(len(p1), P1_SHOWN)} 个，其余展开查看；24h 转化是第三方字段，不是账户 CVR。</p>'
        + table(tier_headers, tier(p1[:P1_SHOWN]))
        + (fold(f'其余一级词（第 {P1_SHOWN + 1}–{len(p1)} 个）', table(tier_headers, tier(p1[P1_SHOWN:])), len(p1) - P1_SHOWN) if len(p1) > P1_SHOWN else '')
        + fold(f'二级词 Exact-P2 · 共 {len(p2)} 个（周搜索量前 30；全部见 keywords.csv）', table(tier_headers, tier(p2[:30])), len(p2))))
    sections.append(section('placement', '03 · 埋词位置（候选，写完在文案报告对账）',
        '<p class="muted">每个位置按周搜索量列出可用候选，标签里的数字是周搜索量；实际写进文案的词和覆盖方式以 Listing 报告为准。竞品品牌、待证声明词不会出现在任何位置。</p>'
        + (f'<div class="warning">{esc(empty_pool_note(model, view))}</div>' if empty_pool_note(model, view) else '')
        + render_semantic_pools(model.get('analysis') or {}) + '<div class="use-grid">' + ''.join(use_card(LISTING_USES.get(name, name), '', rows, name) for name, rows in view['listing'].items()) + '</div>' + ('<h3>卖点覆盖表 · CT → 关键词 → 文案</h3>' + render_ct_mapping(model['ct_mapping']) if model.get('ct_mapping') else '')))
    ad_cards = ''.join(use_card(AD_CARD_TITLES.get(name, POOL_LABELS.get(name, name)), AD_PLAYS.get(name, ''), rows, name)
                       for name, rows in view['ads'].items() if name != 'exact')
    negs = [n for n in model['negatives']]
    neg_rows = [[esc(n['term']), esc({'phrase': '词组否定', 'exact': '精确否定'}.get(n['match_type'], n['match_type'])), num(n.get('hits')),
                 txt(n.get('reason')), txt('、'.join(n.get('examples') or []))] for n in negs[:40]]
    sections.append(section('advertising', '04 · 广告分池（交给 S4 策略，不是投放授权）',
        '<div class="use-grid">' + ad_cards + '</div>'
        + fold('否定建议（先对照真实搜索词报告复核）', table(['词', '方式', '命中词数', '理由', '例词'], neg_rows), len(negs))
        + '<h3>商品定投候选（确认竞品）</h3><p>' + (esc('、'.join(model['asins'])) or '暂无') + '</p>'
        + '<div class="warning">以上是分池证据。出价、预算、Campaign 结构由 S4 策略结合利润与库存决定；品牌/IP 词不会自动变成全账户否定。</div>'))
    sections.append(section('enrichment', '05 · MCP 补数与季节', render_enrichment(model, view)))
    appendix = '<p class="muted">以下是附录：待核词、排除词、判定原因和来源证据，供复核用，默认折叠。</p>'
    sections.append(section('diagnostics', '06 · 附录 · 词库体检', appendix + fold('展开词库体检（数量守恒、判定原因、待核与排除明细）', render_diagnostics(model, view))))
    shown, totals = worksheet_rows(model)
    filters = ('<div class="filters" role="group" aria-label="关键词用途筛选">' + ''.join(
        f'<button data-filter="{key}" aria-pressed="{str(key == "usable").lower()}">{label}</button>' for key, label in
        [('usable', '可用词'), ('all', '全部'), ('copy', '文案'), ('new_ads', '新品广告'), ('pending', '待核'), ('excluded', '排除')])
        + '<label>查找词 <input id="keyword-search" type="search" placeholder="英文或中文"></label></div><p id="filter-count" aria-live="polite"></p>')
    note = (f'<p class="muted">本页列出全部可用词 {totals["usable"]} 个、待核词量最高的 {min(totals["pending"], WORKSHEET_LIMITS["pending"])}/{totals["pending"]} 个、'
            f'排除词量最高的 {min(totals["excluded"], WORKSHEET_LIMITS["excluded"])}/{totals["excluded"]} 个；全部 {len(model["keywords"])} 个唯一词及原始字段见 '
            '<a href="keywords.csv" download>keywords.csv</a>。</p>')
    member = defaultdict(list)
    for group in ('listing', 'ads'):
        for name, ids in model['pools'][group].items():
            for kid in ids:
                member[kid].append(name)
    sections.append(section('worksheet', '07 · 附录 · 选词工作表', fold('展开选词工作表（可筛选全部可用、待核、排除词）', filters + note + keyword_rows(shown, member)
                            + '<p id="empty-filter" hidden>当前筛选没有词条。可切换“待核”查看需要补充的事实。</p>', totals['usable'])))
    sections.append(section('evidence', '08 · 附录 · 来源与证据', fold('展开来源、哈希与指纹', evidence_compact(model))))
    return shell(model, '关键词库报告', 'keyword', sections)


def mcp_summary(mcp, provider='SIF'):
    calls = mcp.get('calls') or []
    if 'logical_total' in mcp:
        total=mcp['logical_total'];success=mcp['logical_success'];missing=mcp['logical_missing']
        failed=sum(c.get('result_status')=='failed' and c.get('channel')=='direct_bridge' for c in calls)
        label='（历史回放）' if mcp.get('evidence_mode')=='historical_replay' else ''
        semantic=mcp.get('semantic_missing',0)
        native='；原生通道完成' if success==total and mcp.get('effective_channels')==['native_mcp'] else ''
        return f'{provider} 补数 {success}/{total} 成功，缺 {missing}'+label+native+(f'；{semantic}项为空或缺业务字段' if semantic else '')+(f'；桥接{failed}次失败记录已保留' if failed else '')
    success = sum(c.get('result_status', c.get('status')) in {'success', 'partial'} for c in calls)
    missing = [str(c.get('tool_name') or '调用') for c in calls if c.get('result_status', c.get('status')) not in {'success', 'partial'}]
    return f'{provider} 补数 {success}/{len(calls)} 成功，缺 {len(missing)}' + ('：'+'、'.join(missing[:3]) if missing else '') + ('；尚未调用' if not calls else '')


def season_action(profile):
    phase = str(profile.get('current_phase') or '').casefold()
    if phase in {'falling', 'declining', 'post_peak', '下降', '回落'}:
        return '需求回落，复核季节位置并撤下过期文案；广告保留观察'
    return profile.get('action_hint') or '缺当前阶段依据，暂不判断切换时机'



def render_semantic_pools(analysis):
    labels = {'scene':'场景', 'audience':'人群', 'gift_season':'礼品/季节', 'attribute':'属性', 'opportunity':'机会'}
    return table(['语义候选池','词数','缺口'], [[labels.get(name,name),len(row.get('keyword_ids',[])),esc(row.get('missing_reason') or '—')]
        for name,row in (analysis.get('semantic_pools') or {}).items()])

def render_ct_mapping(mapping):
    rows = []
    for row in mapping.get('rows', []):
        covered = row.get('covered')
        badge = '<span style="color:#287344">已覆盖</span>' if covered else '<span style="color:#b91c1c">未覆盖</span>'
        rows.append([esc(row.get('label')), esc(str(row.get('value') or '缺失')), esc(row.get('source_status')),
                     esc('、'.join(row.get('writable_keywords', [])[:20]) or '无已获文案许可词')
                     + (f'<small>另有{len(row.get("research_only_keywords",[]))}个研究关联，未获文案许可</small>' if row.get('research_only_keywords') else ''), esc('、'.join(f"{f['sku']}:{f['field']}" for f in row.get('fields', [])) or
                     '规划：'+'、'.join(row.get('field_plan', []))), badge+' '+esc(row.get('missing_reason') or '')])
    body = '<p class="muted">词形关联仅供研究；下列仅展示已获文案许可词，图示用途仍须弱化。规格可由真实来源直接支持，词形相似不授予本品许可。</p>'+table(['CT字段', '信息', '来源状态', '已获文案许可词', '字段位置', '覆盖/缺口'], rows)
    if mapping.get('changed_refs'):
        body += '<p class="warning">CT已变化，需要重写：'+esc('、'.join(mapping['changed_refs']))+'</p>'
    return body


def render_enrichment(model, view):
    mcp, mcp_out, season = model['mcp'], view['mcp'], view['season']
    body = '<p>'+esc(mcp_summary(mcp, str(model.get('meta', {}).get('source') or 'SIF').upper()))+'</p>'
    if season.get('status') == 'from_mcp':
        strength = ' · '.join(f'{k} {v} 词' for k, v in (season.get('strength_distribution') or {}).items())
        body += ('<h3>季节判断</h3>' + list_html([f'峰值月份分布：{"、".join(f"{m}月×{n}" for m, n in season.get("peak_months", []))}',
                                            f'季节强度：{strength}',
                                            f'历史覆盖年数：{"、".join(str(y) for y in season.get("coverage_years", []))}', season.get('note', '')]))
    else:
        body += '<p class="muted">' + esc(season.get('reason') or '未取得季节证据') + '</p>'
    demand = [[f'<b>{esc(p.get("keyword"))}</b>', esc((p.get('trend') or {}).get('direction')), pct((p.get('trend') or {}).get('yoy_change')),
               esc((p.get('seasonality') or {}).get('strength')), esc('、'.join((p.get('seasonality') or {}).get('peak_months') or [])),
               esc(p.get('current_phase')), '—' if str(p.get('current_phase')).casefold() in {'falling', 'declining', 'post_peak'} else num(p.get('weeks_to_peak')), esc(season_action(p))] for p in (mcp_out.get('demand_profiles') or [])[:15]]
    body += '<h3>主词需求与时机（SIF 需求诊断）</h3>' + table(['词', '趋势', '同比', '季节强度', '峰值月', '当前位置', '距峰值(周)', '行动提示'], demand)
    body += '<p class="muted">调用证据见 <a href="report_model.json">report_model.json</a> 的 mcp.calls，原始尝试继续保留。</p>'
    roots = [[f'<b>{esc(t.get("keyword"))}</b>', num((t.get('latest') or {}).get('keyword_search_volume')), num((t.get('latest') or {}).get('ext_search_volume')),
              esc((t.get('latest') or {}).get('exact_keyword_demand_pct')), esc((t.get('latest') or {}).get('longtail_demand_pct')), esc(t.get('summary') or '')]
             for t in mcp_out.get('root_trends') or []]
    body += fold('词根：精确词 vs 长尾', table(['词根', '精确词量', '词根总量', '精确占比', '长尾占比', '解读'], roots), len(roots))
    comp_parts = []
    for c in mcp_out.get('competition') or []:
        comps = [[esc(x.get('asin')), num(x.get('price')), num(x.get('rating')), num(x.get('review_count')), esc(x.get('monthly_orders') or '—'),
                  pct(x.get('organic_share')), pct(x.get('sp_share'))] for x in (c.get('top_competitors') or [])[:5]]
        comp_parts.append(f'<h4>{esc(c.get("keyword"))}</h4><p>集中度 {esc(c.get("concentration_level"))} · 流量模式 {esc(c.get("competition_mode"))} · '
                          f'{esc(c.get("primary_angle") or "")}</p>' + table(['ASIN', '价格', '评分', '评论', '月销档', '自然份额', 'SP 份额'], comps))
    if comp_parts:
        body += fold('头部竞争格局（按主词）', ''.join(comp_parts), len(comp_parts))
    charts = ''.join('<article class="copy-card"><h4>' + esc(s.get('keyword_id')) + ' · 月度搜索量</h4>' + chart(s) + list_html(s['warnings'])
                     + details('两年逐月同比', s['year_comparison']) + '</article>' for s in model['seasonality'][:4])
    return body + (fold('月度历史趋势图', charts, len(model['seasonality'][:4])) if charts else '')


def render_diagnostics(model, view):
    analysis, cons, counts = model['analysis'], model['conservation'], model['counts']
    disp = cons.get('disposition_counts') or {}
    conc = view['conc']
    fill = (analysis.get('metric_fill_rates') or {}).get('retained') or {}
    dims = analysis.get('dimension_counts') or {}
    names = {'D1': 'D1 材质/结构', 'D2': 'D2 功能/卖点', 'D3': 'D3 场景', 'D4': 'D4 人群', 'D5': 'D5 对象', 'D6': 'D6 意图'}
    review_size = analysis.get('review_queue_size', 0)
    summary = [f'数量守恒：原始 {num(counts["raw"])} 行 → 唯一 {num(counts["unique"])} 词 → 可用 {num(counts["usable"])} · 待核 {num(counts["pending"])} · 排除 {num(counts["excluded"])}。']
    if conc.get('status') == 'computed':
        summary.append(f'流量集中度：前 10 个可用词占 {pct(conc.get("top10_share"))}，覆盖 80% 流量需要 {num(conc.get("keywords_to_80pct"))} 个词。')
    empty = [names.get(d, d) for d, v in dims.items() if not v]
    if empty:
        summary.append('空维度：' + '、'.join(empty) + '（可用词里没有命中该维度的英文词根，可在 review_decisions.json 的 lexicon 补英文词根后重跑）。')
    for c in (analysis.get('claim_unlock') or [])[:2]:
        summary.append(f'声明「{c.get("label")}」挡住 {num(c.get("keywords"))} 词、周量 {num(c.get("weekly_volume"))}，验证后可解锁。')
    summary.append(f'待复核队列：{num(review_size)} 词' + ('，需在 review_decisions.json 裁决后重跑清洗。' if review_size else '，已清空。'))
    body = '<h3>体检摘要</h3>' + list_html(summary)
    body += fold('数量守恒明细', table(['项', '数量'], [[esc(k), num(cons.get(k))] for k in ('raw_rows', 'blank_keyword_rows', 'invalid_rows', 'normalized_valid_rows', 'unique_keywords', 'merged_duplicate_rows')]
                                    + [[esc(DISPOSITION_LABELS.get(k, k)), num(v)] for k, v in disp.items()]))
    groups = reason_groups(model['keywords'])
    body += fold('判定原因分布', table(['原因', '词数', '例词'], [[esc(k), num(n), esc('、'.join(ex)) or '—'] for k, n, ex in groups]), len(groups))
    if conc.get('status') == 'computed':
        body += fold('流量集中度（可用词）', table(['Top10', 'Top20', 'Top50', 'Top100', '覆盖 80% 所需词数', '分母'],
            [[pct(conc.get('top10_share')), pct(conc.get('top20_share')), pct(conc.get('top50_share')), pct(conc.get('top100_share')),
              num(conc.get('keywords_to_80pct')), f'{num(conc.get("denominator_terms"))} 词 / {num(conc.get("denominator_volume"))}']]))
    body += fold('字段填充率（可用词）', table(['字段', '有值', '填充率'], [[esc(METRIC_LABELS.get(k, k)), f'{v.get("provided")}/{v.get("total")}', pct(v.get('rate'))] for k, v in fill.items()]), len(fill))
    body += fold('六维入口（可用词内）', table(['维度', '命中词根（次数）'], [[esc(names.get(d, d)), esc('、'.join(f'{w}×{n}' for w, n in v) or '—')] for d, v in dims.items()]), len(dims))
    for title, key, head, limit in (('声明限制：验证后可解锁', 'claim_unlock', '声明', None), ('事实待确认的词', 'fact_pending_unlock', '待确认事实', None),
                                     ('品牌隔离（仅人工审核后定投）', 'brand_isolated', '品牌', 15), ('IP/平台禁用', 'compliance_blocked', '禁用词', 10),
                                     ('S1 排除词根', 'excluded_roots', '词根', 15)):
        items = (analysis.get(key) or [])[:limit] if limit else (analysis.get(key) or [])
        body += fold(title, table([head, '词数', '周搜索量', '例词'], unlock_rows(items)), len(items))
    high = [[esc(h.get('keyword')), num(h.get('weekly_search_volume')), esc(h.get('reason'))] for h in analysis.get('high_volume_low_relevance') or []]
    body += fold('高量但不属于本品（不因流量大升级）', table(['词', '周搜索量', '原因'], high), len(high))
    review = by_volume([r for r in model['keywords'] if r['needs_review']])[:20]
    body += fold(f'待复核队列（前 20）', '<p class="muted">在 review_decisions.json 写明裁决与理由后重跑清洗，改动会写进词包并更新 Hash。</p>'
                 + table(['词与中文', '周搜索量', '当前判定'], [[word(r), num(vol(r)), esc(r['disposition_reason'])] for r in review]), review_size)
    pollution = [[esc(p.get('root')), num(p.get('hits')), esc('、'.join(p.get('examples') or []))] for p in analysis.get('sheet2_pollution_roots') or []]
    body += fold('“几乎不相关”表里的污染词根（只观察，不直接否定）', table(['词根', '命中词数', '例词'], pollution), len(pollution))
    counter = list(analysis.get('counterarguments') or [])
    if conc.get('status') == 'computed':
        counter.append(f'可用词量集中在前 {conc.get("keywords_to_80pct")} 个词：长尾扩投边际收益有限，先验证头部词的点击与转化。')
    cvr = fill.get('cvr_24h') or {}
    if cvr.get('rate') is not None and cvr['rate'] < 0.5:
        counter.append(f'24h 转化字段填充率只有 {pct(cvr["rate"])}：不能据此推算 CPA 或出价上限，需账户报表。')
    body += '<h3>最强反方：为什么不直接扩投</h3>' + list_html(counter)
    return body


def evidence_compact(model):
    sources = [[esc(s.get('source_id')), esc(s.get('acquisition_method')), esc(s.get('relative_path') or s.get('tool_name') or s.get('raw_path')),
                esc(s.get('as_of')), esc(str(s.get('sha256') or '')[:19])] for s in model['data_sources']]
    facts = [[esc(f.get('id')), esc(f.get('text')), f'<span class="tag {"usable" if f.get("status") in {"verified", "user_confirmed"} else "pending"}">{esc(f.get("status"))}</span>',
              esc(f.get('status_reason') or f.get('source'))] for f in model['facts']]
    return ('<h3>待处理</h3>' + list_html(model['validation']['issues'])
            + fold('数据来源', table(['source_id', '方式', '文件/工具', '截至', 'SHA-256'], sources), len(sources))
            + fold('本品事实登记（规格来源与性能验证分级）', table(['事实 ID', '内容', '状态', '依据'], facts), len(facts))
            + details('S1 与本轮限制', model['restrictions']) + details('输入版本与检查结果', {'meta': model['meta'], 'fingerprints': model['fingerprints'], 'validation': model['validation']}))


ST_STOPWORDS = {'a', 'an', 'and', 'as', 'at', 'by', 'for', 'from', 'in', 'of', 'on', 'or', 'the', 'to', 'with'}


def unused_st_tokens(texts, sku_keywords):
    """Only unrestricted generic tokens, excluding every frontend field/inflection."""
    ct=dependency('s2_st_normalization','scripts/s2_ct_mapping.py')
    norm = lambda w: ct.tokens(w)[0]
    words = lambda t: [w for w in re.findall(r'[a-z0-9]+', str(t or '').lower()) if w not in ST_STOPWORDS and not w.isdigit()]
    used = {norm(w) for k, v in texts.items() for w in words(v)}
    out, seen = [], set()
    for row in sorted((r for r in sku_keywords if r['listing_eligible'] and r.get('copy_permission','可写')=='可写'), key=lambda r: -(vol(r) or 0)):
        for w in words(row['keyword']):
            if norm(w) not in used and norm(w) not in seen:
                seen.add(norm(w))
                out.append(w)
    return out


COPY_LABELS = {'title':'Title', 'item_highlights':'Item Highlights', 'description':'Product Description',
               'description_html':'HTML Description', 'search_terms':'Search Terms',
               **{f'bullets.{i}':f'Bullet {i}' for i in range(1,6)},
               **{f'qa.{i}.{part}':f'{"Q" if part=="question" else "A"}{i}' for i in range(1,11) for part in ('question','answer')}}


def ordered_fields(sku):
    fields={f['field']:f for f in sku['fields']}
    keys=['title','item_highlights',*[f'bullets.{i}' for i in range(1,6)],'description','description_html','search_terms']
    keys += [f'qa.{i}.{part}' for i in range(1,sku.get('qa_count',0)+1) for part in ('question','answer')]
    keys += [k for k in fields if k.startswith('attributes.')]
    return [fields[k] for k in keys if k in fields]


def length_badge(field):
    """Plain words: measured / target and whether it is short, in range or over."""
    if field['chars'] is None: return '<span class="tag pending">未填写</span>'
    rule=field.get('rule') or {}; target=rule.get('target'); limit=rule.get('max')
    st=field['field']=='search_terms'
    measured=field['utf8_bytes'] if st else field['chars']
    unit='字节' if st else '字符'
    text=f'{measured} {unit}'+(f' · 目标 {target[0]}–{target[1]}' if target else '')+(f' · 上限 {limit}' if limit else '')
    if rule.get('brand_reserved'): text+=f' · 另留品牌 {rule["brand_reserved"]}'
    if limit and measured>limit and rule.get('brand_reserved') and measured<=limit+rule['brand_reserved']:
        return f'<span class="tag pending">加品牌会超长 · {esc(text)}</span>'
    if limit and measured>limit: return f'<span class="tag excluded">超出上限 · {esc(text)}</span>'
    if target and measured<target[0]: return f'<span class="tag pending">偏短 {target[0]-measured} {unit} · {esc(text)}</span>'
    return f'<span class="tag usable">达标 · {esc(text)}</span>'


def keyword_groups(field):
    return {status: list(dict.fromkeys(c['keyword'] for c in field['coverage'] if c['status'] == status)) for status in ('exact', 'tokens')}


def keyword_line(field):
    """Which product keywords this field carries, read right under the English and before the Chinese."""
    groups = keyword_groups(field)
    text = '埋词：' + ('、'.join(groups['exact']) or '—')
    if groups['tokens']: text += '；分词覆盖：' + '、'.join(groups['tokens'])
    return text


def used_keywords(field):
    return '<p class="keywords"><b>'+esc(keyword_line(field))+'</b></p>'


def review_fold(field):
    items=list(dict.fromkeys(field.get('issues') or []))+list(dict.fromkeys(field.get('notes') or []))
    if not items: return ''
    return '<details class="fold inner"><summary>审阅提示（'+str(len(items))+'）</summary><div class="fold-body">'+list_html(items)+'</div></details>'


def copy_bundle(sku):
    return '\n\n'.join(COPY_LABELS.get(f['field'], f['field'].replace('attributes.','Attribute '))+': '+(f['en'] or '') for f in ordered_fields(sku))


def title_rule(rule, brand):
    """Brand goes first in the title. Until it is decided, the copy leaves room for it (brand name + one space)."""
    rule = dict(rule or {})
    if brand:
        return {**rule, 'brand': brand}
    reserve = int(rule.get('brand_reserve') or 0)
    if not reserve or not rule.get('max') or reserve >= rule['max']:  # a ceiling this small leaves no room to reserve
        return rule
    target = rule.get('target')
    return {**rule, 'max': rule['max'] - reserve, 'brand_reserved': reserve,
            **({'target': [max(0, target[0] - reserve), target[1] - reserve]} if target else {})}


def brand_note(rule, title):
    if rule.get('brand'):
        first = str(title or '').casefold().startswith(rule['brand'].casefold())
        return '品牌：' + rule['brand'] + ('，已在标题最前面。' if first else '，应放在标题最前面，当前没有。')
    if rule.get('brand_reserved'):
        return (f'品牌未定：上架时在标题最前面加“品牌名 + 空格”；已预留 {rule["brand_reserved"]} 字符，'
                f'正文上限 {rule["max"]} 字符。品牌名超过 {rule["brand_reserved"] - 1} 个字符时，需相应缩短标题。')
    return '品牌未定：上架时在标题最前面加品牌名，并确认总长度不超过上限。'


def field_count(field):
    if field['chars'] is None: return '未提供，暂无计数'
    rule=field.get('rule') or {}; limit=rule.get('max'); target=rule.get('target')
    measured=field['utf8_bytes'] if field['field']=='search_terms' else field['chars']
    unit='原始 UTF-8 字节（含空格/标点，项目口径）' if field['field']=='search_terms' else '字符（含空格）'
    text=f'{measured}'+('/'+str(limit) if limit else '')+' '+unit
    if target: text+=' · 项目目标 '+'–'.join(map(str,target))
    if rule.get('brand_reserved'): text+=f' · 另预留品牌 {rule["brand_reserved"]} 字符'
    return text


def card(field, sku_index, anchor=True, html_field=None):
    fid=f'copy-{sku_index}-{field["field"].replace(".","-")}'
    payload=html.escape(field['en'] or '',quote=True)
    button=f'<button class="copy" data-copy-target="{fid}"'+(' disabled' if not field['en'] else '')+'>复制英文</button>'
    if html_field and html_field.get('en'):
        hid=f'copy-{sku_index}-description-html'
        button+=f'<pre hidden id="{hid}">'+html.escape(html_field['en'],quote=True)+f'</pre><button class="copy" data-copy-target="{hid}">复制 HTML 版</button>'
    title=COPY_LABELS.get(field['field'],field['label'])
    return (f'<article class="copy-card" data-field="{esc(field["field"])}"><h3>{esc(title)}</h3>'
            +'<p class="count">'+length_badge(field)+'</p>'
            +f'<div class="english" id="{fid}" lang="en">{payload}</div>'+button
            +used_keywords(field)
            +('<div class="translation"><b>中文</b><p>'+esc(field['translation_zh'])+'</p></div>' if field.get('translation_zh') else '')
            +review_fold(field)+'</article>')


def attribute_label(field):
    return field['field'].removeprefix('attributes.').replace('_', ' ')


def attribute_checks(fields):
    """(items every attribute shares, {field: its own items}): shared items are said once, above the table."""
    items = {f['field']: list(dict.fromkeys((f.get('issues') or []) + (f.get('notes') or []))) for f in fields}
    shared = [x for x in next(iter(items.values()), []) if all(x in v for v in items.values())] if len(items) > 1 else []
    return shared, {k: [x for x in v if x not in shared] for k, v in items.items()}


def attribute_table(fields, sku_index):
    """Backend attributes are a checklist for listing day: one compact row each, details folded per row."""
    shared, own = attribute_checks(fields)
    rows = []
    for f in fields:
        items = own[f['field']]
        check = ('<details class="inner-mini"><summary>' + str(len(items)) + ' 项待核</summary>' + list_html(items) + '</details>') if items else '—'
        rows.append([esc(attribute_label(f)), esc(f['en'] or '（未提供）'), esc(f.get('translation_zh') or ''), check])
    pid = f'copy-{sku_index}-attributes'
    payload = html.escape('\n'.join(attribute_label(f) + ': ' + (f['en'] or '') for f in fields), quote=True)
    return (('<p class="muted">全部属性共同待核：' + esc('；'.join(shared)) + '</p>' if shared else '')
            + table(['属性', '填写值', '中文', '待核'], rows, cls='data attr-table')
            + f'<pre hidden id="{pid}">{payload}</pre><button class="copy" data-copy-target="{pid}">复制全部属性</button>')


def qa_card(question, answer, sku_index, n):
    """One question and its answer are one block, as buyers read them."""
    pid=f'qa-pair-{sku_index}-{n}'
    text=f'Q: {question["en"] or ""}\nA: {answer["en"] or ""}'
    zh='；'.join(x for x in (question.get('translation_zh'),answer.get('translation_zh')) if x)
    merged={**answer,'issues':list(question.get('issues') or [])+list(answer.get('issues') or []),
            'notes':list(question.get('notes') or [])+list(answer.get('notes') or []),
            'coverage':list(question.get('coverage') or [])+list(answer.get('coverage') or [])}
    return (f'<article class="copy-card" data-field="qa.{n}"><h3>Q&amp;A {n}</h3>'
            +f'<div class="english" lang="en"><p><b>Q:</b> {esc(question["en"])}</p><p><b>A:</b> {esc(answer["en"])}</p></div>'
            +f'<pre hidden id="{pid}">'+html.escape(text,quote=True)+f'</pre><button data-copy-target="{pid}">复制本条问答</button>'
            +used_keywords(merged)
            +('<div class="translation"><b>中文</b><p>'+esc(zh)+'</p></div>' if zh else '')
            +review_fold(merged)+'</article>')


def chart(series):
    points = [p for p in series['points'] if p['value'] is not None]
    if len(points) < 2:
        return '<p class="muted">有效月份不足，不生成趋势图。</p>'
    months = [p['month'] for p in series['points']]
    max_value = max(p['value'] for p in points) or 1
    coordinates = []
    for p in series['points']:
        if p['value'] is None:
            coordinates.append(None)
        else:
            x = 20 + months.index(p['month']) * 560 / max(1, len(months) - 1)
            coordinates.append(f'{x:.1f},{155-p["value"]*130/max_value:.1f}')
    groups = ' '.join(x if x else '|' for x in coordinates).split('|')
    lines = ''.join(f'<polyline fill="none" stroke="#0f766e" stroke-width="3" points="{g.strip()}"/>' for g in groups if g.strip())
    return '<svg viewBox="0 0 600 190" role="img" aria-label="按月份的来源指标趋势，缺失月份断线">' + lines + f'<text x="20" y="185">{esc(months[0])}</text><text x="510" y="185">{esc(months[-1])}</text></svg>'


def evidence_html(model):
    return details('本品事实', model['facts']) + details('S1 与本轮限制', model['restrictions']) + details('数据来源', model['data_sources']) + details('补充证据', model['evidence']) + details('输入版本与检查结果', {'meta': model['meta'], 'fingerprints': model['fingerprints'], 'validation': model['validation']})


COVERAGE_LABELS = {'exact': '原词出现', 'tokens': '分词覆盖', 'absent': '未覆盖'}
QA_INTRO = ('按 Alexa for Shopping（前身 Rufus）买家常问的问题写：优先使用真实 VOC，缺失时明确编辑拟题，答案只用有来源事实，'
            '自然带入本品可用词；未证实的声明不写。S3 高级 A+ 的 Q&A 模块直接取这 5 条，不另写。')


def scene_model(scenes, keywords):
    items = scenes.get('scenes') if isinstance(scenes, dict) else (scenes or [])
    by_id = {k['keyword_id']: k for k in keywords}
    by_word = {k['keyword'].casefold(): k for k in keywords}
    out = []
    for s in items or []:
        words = []
        for ref in s.get('search_terms') or s.get('keyword_ids') or []:
            k = by_id.get(str(ref)) or by_word.get(str(ref).casefold())
            words.append({'ref': str(ref), 'keyword': k['keyword'] if k else None, 'volume': vol(k) if k else None,
                          'status': k['status'] if k else 'missing'})
        pain = s.get('pain') or {}
        out.append({'scene_id': s.get('scene_id'), 'name': s.get('name'), 'persona': s.get('persona'),
                    'pain_codes': pain.get('codes') or [], 'evidence': pain.get('evidence'), 'evidence_detail': pain.get('evidence_detail'),
                    'keywords': words, 'fact_refs': s.get('fact_refs') or [], 'claim_boundary': s.get('claim_boundary')})
    return out


def qa_pairs(sku):
    by_field = {f['field']: f for f in sku['fields']}
    return [(by_field[f'qa.{i}.question'], by_field[f'qa.{i}.answer']) for i in range(1, sku.get('qa_count', 0) + 1)
            if f'qa.{i}.question' in by_field and f'qa.{i}.answer' in by_field]


def listing_status_rows(sku):
    by_field = {f['field']: f for f in sku['fields']}
    rows = []
    for key in ['title', 'item_highlights', *[f'bullets.{i}' for i in range(1, 6)], 'description', 'search_terms']:
        f = by_field.get(key)
        if not f:
            continue
        size = field_count(f)
        count = f['coverage_counts']['complete'] if key in {'title','item_highlights'} else sum(c['status']!='absent' for c in f['coverage'])
        rows.append([esc(f['label']), size if f['chars'] is not None else '未提供', num(count),
                     f'<span class="tag {"pending" if f["issues"] else "usable"}">{len(f["issues"])} 项待处理</span>' if f['issues'] else '<span class="tag usable">通过</span>'])
    rows.append(['Q&A', f'{sku.get("qa_count", 0)}/{sku["qa_required"]} 条', '—', '<span class="tag usable">通过</span>' if sku.get('qa_count') == sku['qa_required']
                 else '<span class="tag pending">数量不符</span>'])
    return rows


def coverage_rows(sku):
    rows = []
    for f in sku['fields']:
        hit = [f'{c["keyword"]}（{COVERAGE_LABELS.get(c["status"], c["status"])}）' for c in f['coverage'] if c['status'] != 'absent']
        miss = [c['keyword'] for c in f['coverage'] if c['status'] == 'absent']
        if hit or miss or f['buyer_questions']:
            rows.append([esc(f['label']), txt('、'.join(hit)), txt('、'.join(miss)), txt('；'.join(f['buyer_questions']))])
    return rows


def render_listing(model):
    intro='<p>每个变体按后台顺序排列：标题 → 亮点 → 五点 → 长描述 → ST → 5 组问答。每个字段右上角标出字数是否达标；文案里用到的未核实规格列在最后的“上架前检查”，上架前请和工厂核对。</p>'
    edition=model['meta'].get('edition')
    if edition=='flagship':
        intro+='<p>旗舰版：'+('可生成推送审核请求，读取线上旧值并由本人确认后再用本地入口执行。' if model['meta'].get('sp_api_connected') else '尚未连接 SP-API，请复制后手工粘贴。')+'</p>'
    else: intro+='<p>运营版：复制后手工粘贴。</p>'
    if model['meta'].get('replay_mode'): intro+='<div class="warning">历史离线软件回放；business_execution=false。S1 未结事项与业务授权仍以原记录为准。</div>'
    sections=[section('summary','概况与待处理',intro+list_html(model['validation']['issues']))]
    for i,sku in enumerate(model['listings']):
        payload=html.escape(copy_bundle(sku),quote=True)
        body=f'<div data-variant="{esc(sku["variant"])}"><h3>{esc(variant_name(sku,i))}</h3>'
        if sku.get('sku_level_issues'):
            # Internal review items stay available but folded, so the copy itself comes first.
            body+='<details class="fold inner" data-sku-issues="'+esc(sku['sku'])+'"><summary>本变体审阅提示（'+str(len(sku['sku_level_issues']))+'）</summary><div class="fold-body">'+list_html(sku['sku_level_issues'])+'</div></details>'
        body+=f'<pre hidden id="copy-all-{i}">{payload}</pre><button data-copy-target="copy-all-{i}">复制整套（按后台字段顺序）</button>'
        if model['meta'].get('edition')=='flagship' and model['meta'].get('sp_api_connected'):
            proposal={'schema':'s2-review-request-1','sku':sku['sku'],'listing_hash':model['fingerprints']['listing'],
                      'fields':{f['field']:f['en'] for f in ordered_fields(sku)},'old_values_status':'not_read',
                      'supported_mappings':{'title':'item_name','bullets':'bullet_point','description':'product_description','search_terms':'generic_keyword'},
                      'manual_only':[f['field'] for f in sku['fields'] if f['field'] in {'item_highlights','description_html'} or f['field'].startswith(('qa.','attributes.'))],
                      'business_execution':False,'next_action':'Read exact SKU using local push CLI to create old-to-new proposal; human review and confirmation required.'}
            body+=f'<script type="application/json" id="review-request-{i}">'+json.dumps(proposal,ensure_ascii=False).replace('<','\\u003c')+'</script>'
            body+=f'<button data-review-request="review-request-{i}">推送到后台 · 生成审核请求</button><p class="muted">此按钮只生成审核请求，未读取线上旧值、未写入。亮点、HTML、问答与其他属性须手工操作；本地 CLI 读取旧值后生成逐字段现在→改成审核，人工确认后才可写。</p>'
        by_field={f['field']:f for f in sku['fields']}
        attributes=[]
        for f in ordered_fields(sku):
            key=f['field']
            if key=='description_html' or key.endswith('.answer'): continue
            if key.startswith('attributes.'): attributes.append(f); continue
            if key.endswith('.question'):
                n=key.split('.')[1]; answer=by_field.get(f'qa.{n}.answer')
                if n=='1': body+='<p class="muted">5 组问答按 Alexa for Shopping（前身 Rufus）买家常问的问题写；没有真实买家原话时为编辑拟题。</p>'
                if answer: body+=qa_card(f,answer,i,n)
                continue
            body+=card(f,i,html_field=by_field.get('description_html') if key=='description' else None)
        if attributes:
            body+=fold('后台属性 · 上架时核对',attribute_table(attributes,i),len(attributes))
        body+=fold('本变体埋词总表',table(['字段','已埋词','计划未出现','买家问题'],coverage_rows(sku)))
        changes=sku['changes']
        body+=fold('本变体新旧版本对比',table(['字段','现在','改成','理由'],[[esc(c['field']),esc(c['before']),esc(c['after']),esc(c['reason'])] for c in changes]) if changes else '<p>未提供同 SKU 旧版本或字段未改变。</p>')
        body+='</div>'
        sections.append(section(f'variant-{i}',f'{i+1:02d} · {variant_name(sku,i)} · 完整文案',body))
    if model.get('ct_mapping'): sections.append(section('ct-coverage','卖点覆盖与缺口',render_ct_mapping(model['ct_mapping'])))
    sections.append(section('compliance','上架前检查',prelisting_html(model)))
    sections.append(section('evidence','来源与审核记录',evidence_compact(model)))
    return shell(model,'Listing 文案审阅','listing',sections)


COMPLIANCE_LABELS = {'intent_shape':'形态','intent_function':'功能','intent_scene':'场景','intent_audience':'人群',
    'intent_gift':'送礼','required_attributes':'必填属性','qa_intents':'五类问答意图','core_phrases':'核心完整词组','ct_coverage':'卖点覆盖说明'}


def compliance_summary(model):
    proof=model.get('compliance') or {};passed=[]
    gate=dependency('s2_remediation','scripts/s2_compliance_gate.py')
    rows=[(r['sku'],r['message'],r['action']) for r in gate.remediation_items(proof)]
    for sku in proof.get('editorial',[]):
        good=[]
        for check in sku['checks']:
            label=COMPLIANCE_LABELS.get(check['id'],check['id'])
            if check['ok']:good.append(label)
        if good:passed.append('SKU '+str(sku['sku'])+'：'+'、'.join(good))
    return {'status':'可进入人工审阅' if proof.get('status')=='pass' else '需修改',
        'blocked':proof.get('blocked_count','未知'),'rows':list(dict.fromkeys(rows)),'passed':passed}


def compliance_lines(model):
    summary=compliance_summary(model)
    lines=['编辑意图与完整性自检，不是 Amazon 算法评分或业务批准。',
        f"状态：{summary['status']}；违规/来源拦截 {summary['blocked']} 处。",'',
        '| SKU / 范围 | 实际缺项 | 下一步 |','|---|---|---|']
    lines += ['| '+' | '.join(str(value).replace('|','／').replace('\n',' ') for value in row)+' |' for row in summary['rows']]
    if not summary['rows']:lines.append('| 全部 | 无当前缺项 | 进入人工业务审阅；不自动上架。 |')
    if model.get('rule_review_due'):lines.append('\n规则复核提醒：核对登记的官方/项目来源；提醒不代表自动失效，不计入内容修改数。')
    todos=list(dict.fromkeys(t['detail'] for t in (model.get('compliance') or {}).get('listing_todos') or []))
    lines += ['',*['上架前待办（不阻塞 S2）· '+t for t in todos],*['已通过 · '+row for row in summary['passed']], '', '[查看完整扫描、原句、来源与当前指纹](report_model.json)']
    return lines


def compliance_evidence_lines(model):
    proof=model.get('compliance') or {}
    lines=['编辑意图与完整性自检，不是 Amazon 算法分或排名承诺。',
           '状态：'+proof.get('status','missing')+'；拦截 '+str(proof.get('blocked_count','未知'))+' 处。']
    lines += ['- 来源核验需修改：'+source+'（缺文件、越界或当前内容与登记哈希不同）' for source in proof.get('source_errors',[])]
    lines += ['- SKU '+str(row.get('sku'))+' · 字段校验 '+row['check']+'：'+row.get('message','需修改') for row in proof.get('field_failures',[])]
    for scan in proof.get('scans',[]):
        for hit in scan['hits']:
            sources=' / '.join(str(x['layer']) for x in hit['sources'])
            lines.append(f"- SKU {scan['sku']} · {hit['field']} · {hit['found']} · {hit['level']} · {sources} · {hit['reason']}；候选替换：{' / '.join(hit['replacements']) or '按真实事实重写'}")
    for sku in proof.get('editorial',[]):
        for check in sku['checks']:
            detail=check['detail']
            if isinstance(detail,list):
                detail='；'.join((str(row.get('field'))+' → '+', '.join(row.get('evidence_refs',[]))) if 'field' in row else (str(row.get('ref'))+'：'+str(row.get('explanation') or row.get('reason'))) for row in detail)
                detail=detail or '无未解释缺口'
            lines.append(f"- SKU {sku['sku']} · {check['id']} · {'通过' if check['ok'] else '需修改'}：{detail}")
    lines += ['- '+r['id']+'：'+r['message'] for r in model.get('rule_review_due',[])]
    return lines


def variant_name(sku, index):
    """Show the seller's SKU; an internal review id (S2-REVIEW-01) is shown as 'variant N (SKU to fill)'."""
    sid=str(sku.get('sku') or ''); variant=str(sku.get('variant') or sid)
    if not sid or re.fullmatch(r'S\d-REVIEW-\d+',sid,re.I):
        return f'变体 {index+1}（SKU 待填）'
    return variant if variant==sid else f'{variant} · SKU {sid}'


def prelisting_html(model):
    """What a seller checks before listing, in plain words; scan internals stay in the fold."""
    proof=model.get('compliance') or {}
    scans=proof.get('scans') or []
    banned=sorted({str(h.get('found') or h.get('term')) for scan in scans for h in scan.get('hits') or [] if h.get('level')=='block'})
    rows=[]
    rows.append(('禁用词 / 品牌词 / 侵权词', '❌ 命中：'+'、'.join(banned) if banned else '✅ 未命中'))
    for i,sku in enumerate(model.get('listings') or []):
        short=[]; over=[]
        for f in ordered_fields(sku):
            if f['field'].startswith(('qa.','attributes.')) or f['field']=='description_html' or f['chars'] is None: continue
            rule=f.get('rule') or {}; target=rule.get('target'); limit=rule.get('max')
            measured=f['utf8_bytes'] if f['field']=='search_terms' else f['chars']
            label=COPY_LABELS.get(f['field'],f['label'])
            if limit and measured>limit: over.append(f'{label} {measured}/{limit}')
            elif target and measured<target[0]: short.append(f'{label} {measured}（目标 {target[0]}–{target[1]}）')
        name=variant_name(sku,i)
        rows.append((f'{name} · 字数', '❌ 超出上限：'+'；'.join(over) if over else '⚠️ 偏短：'+'；'.join(short) if short else '✅ 全部达标'))
        qa=sku.get('qa_count',0); need=sku.get('qa_required',5)
        rows.append((f'{name} · 问答', '✅ '+str(qa)+' 组' if qa==need else f'⚠️ {qa} 组，需要 {need} 组'))
    todos=list(dict.fromkeys(t['detail'] for t in proof.get('listing_todos') or []))
    if todos:
        rows.append(('后台必填属性（上架前待办）','📝 '+'；'.join(todos)))
    pending=[r for r in ((model.get('ct_mapping') or {}).get('rows') or []) if r.get('verify_note')]
    if pending:
        rows.append(('待核实规格（已写入文案）','⚠️ '+'；'.join(f"{r['label']}：{r['value'] if not isinstance(r['value'],list) else '、'.join(map(str,r['value']))}" for r in pending)+'。上架前请和工厂/样品核对。'))
    body='<table><thead><tr><th>检查项</th><th>结果</th></tr></thead><tbody>'+''.join('<tr><td>'+esc(a)+'</td><td>'+esc(b)+'</td></tr>' for a,b in rows)+'</tbody></table>'
    return body+fold('技术明细（扫描原句、来源与指纹）',compliance_html(model))


def compliance_html(model):
    summary=compliance_summary(model)
    body='<p><strong>'+html.escape(summary['status'])+'</strong> · 违规/来源拦截 '+str(summary['blocked'])+' 处。编辑自检不代表算法评分或业务批准。</p>'
    if summary['rows']:
        body+='<table><thead><tr><th>SKU / 范围</th><th>实际缺项</th><th>下一步</th></tr></thead><tbody>'
        body+=''.join('<tr>'+''.join('<td>'+html.escape(str(value))+'</td>' for value in row)+'</tr>' for row in summary['rows'])+'</tbody></table>'
    else:body+='<p>无当前缺项；进入人工业务审阅，不自动上架。</p>'
    body+=list_html(['已通过 · '+row for row in summary['passed']])
    if model.get('rule_review_due'):body+='<p>规则复核提醒：核对登记的官方/项目来源；提醒不代表自动失效，不计入内容修改数。</p>'
    return body+'<details><summary>查看完整扫描与来源依据</summary><p><a href="report_model.json">下载原句、来源与当前指纹</a></p>'+list_html(compliance_evidence_lines(model))+'</details>'


def shell(model, title, kind, sections):
    assets = HOME / 'templates/s2-report'
    css = (assets / 'report.css').read_text(encoding='utf-8')
    js = (assets / 'report.js').read_text(encoding='utf-8')
    ids = KEYWORD_SECTIONS if kind == 'keyword' else [re.search(r'<section id=\"([^\"]+)', sec).group(1) for sec in sections]
    labels = KEYWORD_NAV if kind == 'keyword' else [html.unescape(re.search(r'<h2>(.*?)</h2>',sec).group(1)) for sec in sections]
    items = []
    for i, (sid, label, sec) in enumerate(zip(ids, labels, sections)):
        subs = ''.join(f'<li><a href="#{a}">{t}</a></li>' for a, t in re.findall(r'id="(' + sid + r'-\d+)" data-toc="([^"]*)"', sec))
        active = ' class="active"' if i == 0 else ''
        items.append(f'<li data-for="{sid}"{active}><a href="#{sid}"><span class="toc-no">{i + 1:02d}</span>{esc(label)}</a>'
                     + (f'<ol>{subs}</ol>' if subs else '') + '</li>')
    nav = ('<aside class="toc" id="toc" aria-label="报告目录"><div class="toc-head"><b class="toc-title">目录</b>'
           '<button type="button" id="toc-pin" aria-pressed="true" title="锁定：目录固定在屏幕左侧">锁定</button>'
           '<button type="button" id="toc-toggle" aria-expanded="true" title="收起/展开目录">收起</button></div>'
           '<ol class="toc-list">' + ''.join(items) + '</ol>'
           '<div class="toc-tools"><button type="button" data-fold-all="open">全部展开</button><button type="button" data-fold-all="close">全部折叠</button></div></aside>')
    meta = model['meta']
    stamp = {'schema_version': SCHEMA_VERSION, 'template_version': TEMPLATE_VERSION, 'model_hash': model['model_hash'], 'fingerprints': model['fingerprints'], 'kind': kind}
    marker = json.dumps(stamp, ensure_ascii=False).replace('<', '\\u003c')
    return '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + esc(title) + ' · ' + esc(meta['product_name']) + '</title><style>' + css + '</style></head><body data-template="' + TEMPLATE_VERSION + '" data-kind="' + kind + '"><header class="hero"><div class="eyebrow">星空 · S2 词库与文案</div><h1>' + esc(title) + '</h1><p>' + esc(meta['product_name']) + ' · ' + esc(meta['marketplace']) + ' · ' + esc(meta['source']) + '</p><div class="meta">词包 v' + esc(meta['package_version']) + ' · 文案 ' + esc(meta['listing_revision']) + ' · 数据截至 ' + esc(meta['as_of']) + '</div><span class="tag pending">人工审阅稿</span></header><div class="layout" id="layout">' + nav + '<main>' + ''.join(sections) + '</main></div><footer>固定模板 ' + TEMPLATE_VERSION + ' · 格式校验与业务验收分别记录 · <a href="report_model.json">完整审阅数据</a></footer><div id="copy-status" role="status" aria-live="polite"></div><script type="application/json" id="s2-report-stamp">' + marker + '</script><script>' + js + '</script></body></html>'


def md_table(headers, rows):
    if not rows:
        return ['暂无记录', '']
    cell = lambda v: str(v if v not in (None, '') else '—').replace('|', '／').replace('\n', ' ')
    return ['| ' + ' | '.join(headers) + ' |', '|' + '---|' * len(headers)] + ['| ' + ' | '.join(cell(c) for c in row) + ' |' for row in rows] + ['']


def plain(value):
    return re.sub(r'<[^>]+>', '', html.unescape(value)) if isinstance(value, str) else value


def empty_pool_note(model, view):
    """Usable words with every placement empty reads like a failed clean; say why and what to run."""
    if (model.get('counts') or {}).get('usable') and not any(view['listing'].values()):
        return ('可用词已有 ' + str(model['counts']['usable']) + ' 个，但还没分到任何埋词位置：通常是这些词引用的产品事实（CT）还没对上证据，'
                '或核心词没识别出来。先完成第 5 步 CT 映射后重跑清洗；仍为空就看 keywords.csv 里这些词的“判定原因”。')
    return ''


def markdown_keyword(model):
    view = keyword_view(model)
    counts, analysis = model['counts'], model['analysis']
    fmt = lambda v: plain(num(v))
    lines = [f'# 关键词库报告 · {model["meta"]["product_name"]}', '',
             f'> 人工审阅稿；未授予上架/广告执行授权。词包 v{model["meta"]["package_version"]} · 数据截至 {model["meta"]["as_of"]} · 模板 {TEMPLATE_VERSION}', '',
             '## 01 结论与现在要处理的事', '',
             f'原始 {fmt(counts["raw"])} 行 → 唯一 {fmt(counts["unique"])} 词 → **本品可用 {fmt(counts["usable"])}** · 待核 {fmt(counts["pending"])} · 排除 {fmt(counts["excluded"])}', '',
             *[f'- {c}' for c in view['conclusions']], '', '**现在要处理的事**', '', *[f'{i}. {a}' for i, a in enumerate(view['actions'], 1)], '']
    rules = analysis.get('pool_rules') or {}
    tier = lambda rows: [[f'{r["keyword"]}（{r["translation_zh"]}）', fmt(vol(r)), fmt(mv(r, 'cpc_mid') or mv(r, 'cpc')), plain(pct(mv(r, 'top3_click_share'))),
                          ' / '.join(r['listing_destinations']) or '—'] for r in rows]
    p1, p2 = by_volume(view['ads'].get('exact_p1', [])), by_volume(view['ads'].get('exact_p2', []))
    lines += ['## 02 核心词 → 一级词 → 二级词', '', '### 核心词根', '',
              *md_table(['词根', '导出周量', '状态', 'MCP 精确词量', '词根总量', '精确占比', '长尾占比', '事实状态'], [[plain(c) for c in row] for row in core_rows(model, view)]),
              f'### 一级词 Exact-P1 · 全部 {len(p1)} 个（门槛周量 ≥ {fmt(rules.get("p1_threshold_weekly"))}，{rules.get("p1_basis", "")}）', '',
              *md_table(['词', '周搜索量', 'CPC 中', 'Top3 点击', '文案位置'], tier(p1)),
              f'### 二级词 Exact-P2 · 共 {len(p2)} 个（前 30）', '', *md_table(['词', '周搜索量', 'CPC 中', 'Top3 点击', '文案位置'], tier(p2[:30])),
              '## 03 埋词位置', '', *([empty_pool_note(model, view), ''] if empty_pool_note(model, view) else []), *md_table(['位置', '词数', '代表词（周量）'], [[LISTING_USES.get(n, n), len(rows), plain(examples(rows))] for n, rows in view['listing'].items()]),
              '## 04 广告分池（交给 S4，不是投放授权）', '',
              *md_table(['池', '词数', '打法', '代表词'], [[POOL_LABELS.get(n, n) + f' `{n}`', len(rows), AD_PLAYS.get(n, ''), plain(examples(rows, 6))]
                                                    for n, rows in view['ads'].items() if n != 'exact']),
              f'**否定建议 {len(model["negatives"])} 条**（先对照真实搜索词报告复核）', '',
              *md_table(['词', '方式', '命中词数', '例词'], [[n['term'], n['match_type'], n.get('hits') or '', '、'.join(n.get('examples') or [])] for n in model['negatives'][:40]]),
              '**商品定投候选**：' + ('、'.join(model['asins']) or '暂无'), '']
    mcp_out, season = view['mcp'], view['season']
    lines += ['## 05 MCP 补数与季节', '', mcp_summary(model['mcp']), '',
              '调用证据：[report_model.json](report_model.json) 的 mcp.calls；原始尝试继续保留。', '']
    if season.get('status') == 'from_mcp':
        lines += [f'- 峰值月份分布：{"、".join(f"{m}月×{n}" for m, n in season.get("peak_months", []))}',
                  '- 季节强度：' + ' · '.join(f'{k} {v} 词' for k, v in (season.get('strength_distribution') or {}).items()), '']
    lines += ['### 主词需求与时机', '', *md_table(['词', '趋势', '同比', '季节强度', '峰值月', '当前位置', '距峰值(周)', '行动提示'],
              [[p.get('keyword'), (p.get('trend') or {}).get('direction'), plain(pct((p.get('trend') or {}).get('yoy_change'))), (p.get('seasonality') or {}).get('strength'),
                '、'.join((p.get('seasonality') or {}).get('peak_months') or []), p.get('current_phase'), None if str(p.get('current_phase')).casefold() in {'falling', 'declining', 'post_peak'} else p.get('weeks_to_peak'), season_action(p)]
               for p in (mcp_out.get('demand_profiles') or [])[:15]]),
              '### 词根：精确词 vs 长尾', '', *md_table(['词根', '精确词量', '词根总量', '精确占比', '长尾占比'],
              [[t.get('keyword'), fmt((t.get('latest') or {}).get('keyword_search_volume')), fmt((t.get('latest') or {}).get('ext_search_volume')),
                (t.get('latest') or {}).get('exact_keyword_demand_pct'), (t.get('latest') or {}).get('longtail_demand_pct')] for t in mcp_out.get('root_trends') or []])]
    for c in mcp_out.get('competition') or []:
        lines += [f'### 竞争格局 · {c.get("keyword")}', '', f'集中度 {c.get("concentration_level")} · 流量模式 {c.get("competition_mode")} · {c.get("primary_angle") or ""}', '',
                  *md_table(['ASIN', '价格', '评分', '评论', '月销档'], [[x.get('asin'), x.get('price'), x.get('rating'), x.get('review_count'), x.get('monthly_orders')]
                                                                 for x in (c.get('top_competitors') or [])[:5]])]
    lines += ['## 06 词库体检', '', '（表格见 HTML；此处为要点）', '',
              *[f'- 声明「{c["label"]}」：{c["keywords"]} 词 / 周量 {c["weekly_volume"]:,} 可在验证后解锁' for c in analysis.get('claim_unlock') or []],
              *[f'- 高量非本品：{h["keyword"]}（{fmt(h["weekly_search_volume"])}）— {h["reason"]}' for h in (analysis.get('high_volume_low_relevance') or [])[:10]],
              f'- 待复核队列：{analysis.get("review_queue_size", 0)} 词（review_queue.json）', '']
    shown, totals = worksheet_rows(model)
    usable = [r for r in shown if r['status'] == 'usable']
    lines += ['## 07 选词工作表（可用词全表）', '', f'可用 {totals["usable"]} · 待核 {totals["pending"]} · 排除 {totals["excluded"]}；全部唯一词见 keywords.csv', '',
              *md_table(['词', '中文', '周搜索量', '文案用途', '广告用途'], [[r['keyword'], r['translation_zh'], fmt(vol(r)), ' / '.join(r['listing_destinations']),
                                                                 ' / '.join(r['ad_uses'])] for r in usable]),
              '## 08 来源与证据', '', *md_table(['source_id', '方式', '文件/工具', '截至'], [[s.get('source_id'), s.get('acquisition_method'),
                                                                                s.get('relative_path') or s.get('tool_name'), s.get('as_of')] for s in model['data_sources']]),
              '待处理：', *[f'- {i}' for i in model['validation']['issues']]]
    return '\n'.join(lines) + '\n'


def markdown(model, kind):
    return markdown_keyword(model) if kind == 'keyword' else markdown_listing(model)


def markdown_listing(model):
    lines=[f'# Listing 文案审阅 · {model["meta"]["product_name"]}', '', '人工审阅稿。Alexa for Shopping（前身 Rufus）问答为编辑候选；不代表真实 VOC。', '', *['- '+x for x in model['validation']['issues']], '']
    for sku in model['listings']:
        lines += [f'## SKU {sku["sku"]} · 完整文案','']
        if sku.get('sku_level_issues'):
            lines += [f'**本变体仍需修改 · {sku["variant"]} / {sku["sku"]}**','', *['- '+message for message in sku['sku_level_issues']], '']
        attributes = [f for f in ordered_fields(sku) if f['field'].startswith('attributes.')]
        for f in ordered_fields(sku):
            if not f['field'].startswith('attributes.'): lines += md_field(f)
        if attributes:
            shared, own = attribute_checks(attributes)
            lines += ['### 后台属性 · 上架时核对', '', *(['全部属性共同待核：' + '；'.join(shared), ''] if shared else []),
                      *md_table(['属性', '填写值', '中文', '待核'],
                      [[attribute_label(f), f['en'] or '（未提供）', f.get('translation_zh') or '', '；'.join(own[f['field']]) or '—'] for f in attributes]), '']
        lines += ['### 本变体埋词总表','', *md_table(['字段','已埋词','计划未出现','买家问题'],[[plain(c) for c in r] for r in coverage_rows(sku)]), '### 本变体新旧版本对比','']
        for change in sku['changes']: lines += [f'- {change["field"]}: {change["before"]} → {change["after"]}；{change["reason"]}', '']
        if not sku['changes']: lines+=['无已知字段变更或未提供同 SKU 基线。','']
    lines += ['## 合规扫描与算法匹配度', '', *compliance_lines(model), '']
    lines+=['## 来源与审核记录','', *md_table(['事实 ID','内容','状态'],[[f.get('id'),f.get('text'),f.get('status')] for f in model['facts']])]
    return '\n'.join(lines)+'\n'


def md_field(f):
    groups={status:[c['keyword'] for c in f['coverage'] if c['status']==status] for status in ('exact','tokens','absent')}
    return [f'### {COPY_LABELS.get(f["field"],f["label"])}','', f['en'] or '（未提供）','',f'> **{keyword_line(f)}**',
            f'> 中文：{f["translation_zh"] or "未提供"}',
            f'> 计划未出现：{"、".join(groups["absent"]) or "—"}',f'> {field_count(f)}',
            f'> 事实：{"、".join(f["fact_refs"]) or "—"}',
            *([f'> 待处理：{"；".join(f["issues"])}'] if f['issues'] else []),'']


def csv_text(model):
    buf = io.StringIO(newline='')
    writer = csv.writer(buf)
    names = sorted({k for r in model['keywords'] for k in r['metrics']})
    writer.writerow(['keyword_id','关键词','中文','资格','判定原因','产品词根','主分类','多标签','文案用途','广告用途','理由','事实引用', *names,'来源血缘'])
    def safe(value):
        value = str(value)
        return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value
    for r in model['keywords']:
        writer.writerow([safe(x) for x in [r['keyword_id'],r['keyword'],r['translation_zh'],r['status'],r['disposition_reason'],r['product_root'],r['primary_type'],
                                           '; '.join(r['tags']) if isinstance(r['tags'], list) else canonical(r['tags']),'; '.join(r['listing_destinations']),
                                           '; '.join(r['ad_uses']),'; '.join(r['reasons']),'; '.join(r['fact_refs']),
                                           *['' if r['metrics'].get(n) is None else r['metrics'][n] for n in names],'; '.join(r['lineage'])]])
    return '\ufeff' + buf.getvalue()


@operation_runtime().boundary
def render_bundle(package, listing, context, output_dir, baseline=None, scenes=None, *, series_root=None):
    model = build_model(package, listing, context, baseline, scenes, series_root=series_root)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    documents = {'keyword_report.html': render_keyword(model), 'listing_report.html': render_listing(model),
                 'keywords.md': markdown(model, 'keyword'), 'listing.md': markdown(model, 'listing'),
                 'keywords.csv': csv_text(model), 'report_model.json': json.dumps(model, ensure_ascii=False, indent=2) + '\n',
                 'validation.json': json.dumps(model['validation'], ensure_ascii=False, indent=2) + '\n',
                 'ct_mapping.json': json.dumps(model['ct_mapping'], ensure_ascii=False, indent=2) + '\n',
                 'effective_listing.json':json.dumps(model['effective_listing'],ensure_ascii=False,indent=2)+'\n',
                 'search_terms_completion.json':json.dumps(model['search_terms_completion'],ensure_ascii=False,indent=2)+'\n'}
    manifest = {'template_version': TEMPLATE_VERSION, 'model_hash': model['model_hash'], 'fingerprints': model['fingerprints'], 'generation':model['generation'], 'files': {}}
    for filename, text in documents.items():
        content = text.encode('utf-8')
        (output / filename).write_bytes(content)
        manifest['files'][filename] = hashlib.sha256(content).hexdigest()
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return model


def verify_bundle(root, package=None, listing=None, context=None, baseline=None, scenes=None):
    try:
        with operation_runtime().scope():
            return _verify_bundle(root,package,listing,context,baseline,scenes)
    except (OSError,ValueError,KeyError,TypeError) as exc:
        return {'ok':False,'errors':['产物验证失败：'+str(exc)],'business_acceptance':'not_granted'}


def _verify_bundle(root, package=None, listing=None, context=None, baseline=None, scenes=None):
    root = Path(root)
    errors = []; current=None;model={}
    try:
        manifest = load(root / 'manifest.json')
        model = load(root / 'report_model.json')
        generation=model.get('generation') or {}
        scanner=dependency('verify_s2_compliance','scripts/compliance_scan.py')
        current=scanner.make_receipt(model.get('effective_listing'),series=generation.get('series_root'),
            context=model.get('compliance_context'),rules=model.get('listing_rules'),mapping=model.get('ct_mapping'),editorial=True)
        if current != model.get('compliance'): errors.append('当前合规词库/规则/证据/时效与报告不一致，需重新扫描')
        if manifest.get('generation')!=generation:errors.append('生成系列来源绑定已改变')
        if model.get('rules_fingerprint') != rules_fingerprint():
            errors.append('资格或文案校验规则已更新，需重新生成报告')
        if manifest.get('template_version') != TEMPLATE_VERSION or model.get('template_version') != TEMPLATE_VERSION:
            errors.append('模板版本不符')
        payload = dict(model)
        actual = payload.pop('model_hash', None)
        if actual != digest(payload) or actual != manifest.get('model_hash'):
            errors.append('审阅模型指纹不符')
        required = {'keyword_report.html','listing_report.html','keywords.md','listing.md','keywords.csv','report_model.json','validation.json','ct_mapping.json','effective_listing.json','search_terms_completion.json'}
        if set(manifest.get('files', {})) != required:
            errors.append('产物清单不完整')
        for filename in required:
            p = root / filename
            if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != manifest.get('files', {}).get(filename):
                errors.append('产物已改变或丢失：' + filename)
        if package is not None:
            expected = build_model(package, listing, context, baseline, scenes, series_root=generation.get('series_root'))
            if expected['model_hash'] != model['model_hash']:
                errors.append('报告未对应当前输入或当前校验规则，需重新生成')
        bound_inputs = {}
        for kind, filename in manifest.get('input_paths', {}).items():
            path = Path(filename)
            binding_key='source_listing' if kind=='listing' else kind
            if not path.is_file() or digest(load(path)) != model['fingerprints'].get(binding_key):
                errors.append('来源文件已改变或丢失：' + kind)
            elif kind in {'package','listing','context','baseline','scenes'}:
                bound_inputs[kind] = load(path)
        if 'package' in bound_inputs and not errors:
            rebuilt = build_model(bound_inputs['package'], bound_inputs.get('listing'), bound_inputs.get('context'), bound_inputs.get('baseline'), bound_inputs.get('scenes'),series_root=generation.get('series_root'))
            if rebuilt['model_hash'] != model['model_hash']:
                errors.append('当前输入重算的资格/文案结果与报告不一致')
        # Deterministic regeneration detects stale, reordered or missing visible content too.
        for filename, text in [('keyword_report.html', render_keyword(model)), ('listing_report.html', render_listing(model)), ('keywords.md', markdown(model,'keyword')), ('listing.md', markdown(model,'listing')), ('keywords.csv', csv_text(model))]:
            p = root / filename
            if not p.is_file() or p.read_bytes() != text.encode('utf-8'):
                errors.append('固定模板内容不一致：' + filename)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append('产物验证失败：' + str(exc))
    return {'ok': not errors, 'errors': errors, 'business_acceptance': 'not_granted',
        'current_compliance':current,'validation':model.get('validation') or {}}


def report_bundle_for(path):
    """Resolve an installed compatibility alias without accepting path traversal."""
    path = Path(path).resolve()
    alias = path.with_name(path.name + '.s2.json')
    if alias.is_file():
        record = load(alias)
        root = (path.parent / record['bundle']).resolve()
        if not root.is_relative_to(path.parent):
            raise ValueError('报告别名越界')
        canonical_file = record.get('file')
        if canonical_file not in {'keyword_report.html', 'listing_report.html'}:
            raise ValueError('未知报告别名')
        expected = alias_document((root / canonical_file).read_text(encoding='utf-8'), record['bundle'])
        if path.read_bytes() != expected.encode('utf-8'):
            raise ValueError('报告别名已过期或被修改')
        return root, canonical_file
    if path.name not in {'keyword_report.html', 'listing_report.html'}:
        raise ValueError('缺少固定模板报告清单；请用随包 s2_report.py 重新生成')
    return path.parent, path.name


def alias_document(document, relative_bundle):
    # Only data downloads move; fragment navigation stays on the current page.
    for name in ('keywords.csv', 'report_model.json'):
        document = document.replace(f'href="{name}"', f'href="{html.escape(relative_bundle, quote=True)}/{name}"')
    return document


def verify_report_file(path, kind=None):
    try:
        root, name = report_bundle_for(path)
        if kind and name != f'{kind}_report.html':
            raise ValueError('报告类型不符')
        return verify_bundle(root)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {'ok': False, 'errors': [str(exc)]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', help='Keyword Package 2.0 JSON/YAML，原始输入只读')
    parser.add_argument('--listing', help='含 skus 数组及 review_metadata 的 Listing JSON')
    parser.add_argument('--context', help='本品事实、资格决定、限制和季节证据 JSON/YAML')
    parser.add_argument('--baseline', help='同 SKU 的旧 Listing JSON，可选')
    parser.add_argument('--scenes', help='场景库 scenes.yaml/JSON，可选；进 Listing 报告「场景库」一节')
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--series',help='实际系列根；缺省仅从词包附近已有词库资产有界发现')
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--require-ready', action='store_true', help='尚有业务缺口时返回非零；不授予执行权限')
    args = parser.parse_args(argv)
    package = load(args.package) if args.package else None
    listing = load(args.listing) if args.listing else None
    context = load(args.context) if args.context else {}
    baseline = load(args.baseline) if args.baseline else None
    scenes = load(args.scenes) if args.scenes else None
    if args.verify:
        result = verify_bundle(args.output_dir, package, listing, context, baseline, scenes)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result['ok'] else 1
    stale = dependency('plugin_freshness', 'scripts/plugin_freshness.py').stale_plugin(HOME)
    if stale:
        print(json.dumps({'rendered': False, 'status': 'stale_plugin', 'error': stale}, ensure_ascii=False))
        return 3
    if package is None:
        parser.error('--package is required for rendering')
    series=args.series or next((str(parent) for parent in list(Path(args.package).resolve().parents)[:6]
        if (parent/'workflow_state.yaml').is_file() and (parent/'_词库资产/keyword_master.yaml').is_file()),None)
    model = render_bundle(package, listing, context, args.output_dir, baseline, scenes,series_root=series)
    manifest_path = Path(args.output_dir) / 'manifest.json'
    manifest = load(manifest_path)
    manifest['input_paths'] = {key: str(Path(value).resolve()) for key, value in
                               [('package', args.package), ('listing', args.listing), ('context', args.context), ('baseline', args.baseline), ('scenes', args.scenes)] if value}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'rendered': True, 'validation': model['validation']['status'], 'counts': model['counts'],
                      'effective_listing':str(Path(args.output_dir).resolve()/'effective_listing.json'),
                      'output_dir': str(Path(args.output_dir).resolve())}, ensure_ascii=False))
    return 1 if args.require_ready and model['validation']['status'] != 'ready_for_review' else 0


if __name__ == '__main__':
    raise SystemExit(main())
