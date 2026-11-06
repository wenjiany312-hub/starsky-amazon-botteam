#!/usr/bin/env python3
"""Source-bound automatic ST completion. No network, facts, permission grants or writes to Amazon.

Priority: current permitted asset/package novelty, source-confirmed CT/research
context, approved core reuse, then bounded approved-core repetition. Repetition
is an explicit project fallback, not an Amazon indexing/ranking claim.
"""
from __future__ import annotations

import argparse
import copy
from collections import Counter
import hashlib
import html
import importlib.util
import json
from pathlib import Path
import re
import sys

HOME=Path(__file__).resolve().parents[1]
VERSION='r02.st1'
STOP={'a','an','and','as','at','by','for','from','in','of','on','or','the','to','with'}
FACT_STATES={'verified','user_confirmed','user_attested','supplier_confirmed','sample_measured','page_extracted','image_extracted','user_selected_and_image_extracted'}
PERFORMANCE=re.compile(r'waterproof|water.?resistant|puncture.?proof|wear.?resistant|durable|fits all|universal fit|guarantee|best|#1|防水|耐磨|防刺穿|全适配',re.I)


def module(name,relative):
    spec=importlib.util.spec_from_file_location(name,HOME/relative)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod


def operation_runtime():
    return module('st_operation_rules','scripts/platform_rules.py').operation_runtime()


def digest(value):
    return 'sha256:'+hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def logical_series_root(root):
    """Reuse CompactCase's authenticated view mapping; never persist temporary roots."""
    if root is None:return None
    name='r01_case_store'
    store=sys.modules.get(name)
    if store is None:
        spec=importlib.util.spec_from_file_location(name,HOME/'scripts/r01_case_store.py')
        store=importlib.util.module_from_spec(spec);sys.modules[name]=store;spec.loader.exec_module(store)
    return store.logical_root(root)


def words(text):
    return re.findall(r"[^\W_]+(?:['’-][^\W_]+)*",str(text).casefold(),re.UNICODE)


def known_fact(fact):
    return isinstance(fact,dict) and fact.get('status') in FACT_STATES and bool(fact.get('source')) and fact.get('copy_permission') not in {'禁止','弱化表述'}


def asset_snapshot(package,context,series_root=None):
    series_root=logical_series_root(series_root)
    binding=context.get('st_asset_binding')
    if not binding and series_root and (Path(series_root)/'_词库资产/keyword_master.yaml').is_file():
        binding={'series_root':str(series_root),'product_id':package.get('product_id'),'marketplace':package.get('marketplace')}
    if not binding:return None
    if not isinstance(binding,dict) or binding.get('product_id')!=package.get('product_id') or str(binding.get('marketplace','')).upper()!=str(package.get('marketplace','')).upper():
        raise ValueError('ST asset binding product/marketplace mismatch')
    series=logical_series_root(binding['series_root']);path=series/'_词库资产/keyword_master.yaml'
    kw=module('st_fresh_asset','scripts/keyword_asset.py')
    # select_many performs one persisted load; reproject the same returned
    # sku_states with E's selector, rather than loading the large master per SKU.
    native=kw.read_snapshot(series)
    return {'module':kw,'native':native,'path':str(path),'sha256':native.sha256}


def candidates(package,context,sku,validator,guard,ctmap,snapshot=None,blacklist=None,series_root=None):
    """Script-only bounded evidence projection. Permissions are consumed, never upgraded."""
    product=package.get('product_id');market=str(package.get('marketplace') or '').upper()
    facts={f['id']:f for f in context.get('facts',[]) if isinstance(f,dict) and f.get('id')}
    restrictions=guard.package_restrictions(package)+(context.get('restrictions') or [])
    ct=context.get('product_ct') or {};ct=ct.get('ct',ct)
    mapping=ctmap.map_ct(ct,package.get('records',[])) if ct else context.get('ct_mapping') or (package.get('analysis') or {}).get('ct_mapping') or {}
    title_ids=set((package.get('listing_pools') or {}).get('title_candidates') or [])
    core_terms={str(r.get('keyword') or '').casefold() for r in package.get('records',[]) if r.get('keyword_id') in title_ids or r.get('primary_type')=='product'}
    blocked=list(context.get('st_block_terms') or [])+list(blacklist or [])
    exact_blocked=[]
    scanner=guard._compliance_module()

    def add_negative(term,row):
        if scanner.negative_copy_mode(row)=='exact':exact_blocked.append(term)
        else:blocked.append(term)
    for record in package.get('records',[]):
        if record.get('disposition') in {'brand_isolated','compliance_negative'}:
            blocked.append(record.get('keyword') or '')
        elif record.get('disposition')=='low_relevance_negative':
            add_negative(record.get('keyword') or '', {'notes':[record.get('retention_reason') or {}]})
        blocked.extend(record.get('brand_hits') or [])
    for row in package.get('negative_records',[]):
        if isinstance(row,dict) and row.get('term'):add_negative(str(row['term']),row)
    wrong=[str(x) for values in (ct.get('not_this') or {}).values() if isinstance(values,list) for x in values]
    empty=any('empty' in str(f.get('text','')).lower() or '空包' in str(f.get('text','')) for f in facts.values() if known_fact(f)) or 'empty' in str(ct.get('package_contents') or '').lower()
    rejected=[];out=[];current_permissions={};source_records={}
    identity={'product_id':product,'marketplace':market,'sku':sku,'ct_mapping':mapping}
    current_series=series_root or (Path(snapshot['path']).parents[1] if snapshot else None)
    identity['series_root']=str(current_series) if current_series else None
    identity['_compliance_session']=guard._compliance_module().ScanSession(current_series,sku,market)

    def text_risks(term):
        risks=[]
        if not isinstance(term,str) or re.search(r'[<>\u3400-\u9fff]',term):risks.append('invalid_ST_text')
        if PERFORMANCE.search(str(term)):risks.append('unsupported_performance')
        risks += ['blacklist_or_wrong_product:'+str(x) for x in blocked+wrong if x and guard.term_matches(term,x)]
        risks += ['traffic_negative_bare_term:'+str(x) for x in exact_blocked if x and scanner.pattern(x,exact=True).search(scanner.normalized(term))]
        if empty and re.search(r'\b(?:with tools|tools included|tool\s+(?:bag\s+)?set|tool kit)\b',str(term),re.I):risks.append('empty_bag_tools_or_set_conflict')
        permission=guard.evaluate_record({'keyword':term,'product_match':'direct'},restrictions,authoritative_context=identity)
        if not permission['listing_eligible']:risks+=permission['reasons']
        return list(dict.fromkeys(risks))

    def qualification(term,refs,raw=None):
        if not isinstance(term,str) or not term.strip():return ['empty_term']
        reasons=text_risks(term)
        if current_permissions.get(' '.join(term.casefold().split()),'可写')!='可写':reasons.append('current_asset_permission_denied')
        if not refs or any(not known_fact(facts.get(ref)) for ref in refs):reasons.append('own_fact_missing_or_not_permitted')
        if reasons:return reasons
        if any(facts[ref].get('product_id') not in (None,product) or facts[ref].get('subject') in {'competitor','market','unknown'} or
               facts[ref].get('marketplace') and str(facts[ref]['marketplace']).upper()!=market for ref in refs):return ['own_fact_identity_mismatch']
        if any(facts[ref].get('skus') and sku not in facts[ref]['skus'] for ref in refs):return ['own_fact_SKU_mismatch']
        effective=copy.deepcopy(raw or {})
        if effective.get('skus') and sku not in effective['skus']:return ['candidate_SKU_mismatch']
        effective.update(keyword=term,fact_refs=list(refs))
        permit=guard.evaluate_record(effective,restrictions,strict_product_evidence=True,authoritative_context=identity)
        if not permit['listing_eligible'] or permit['copy_permission']!='可写':
            return permit['reasons'] or ['current_copy_permission_denied']
        return []

    def add(term,refs,*,tier,source,core=False,kid=None,translation='',raw=None):
        reasons=qualification(term,refs,raw)
        if reasons:
            rejected.append({'term':term,'reason':'; '.join(reasons)});return
        source_records[' '.join(term.casefold().split())]=copy.deepcopy(raw or {})
        out.append({'term':' '.join(term.split()),'tier':tier,'core':core,'keyword_id':kid,'translation_zh':translation,
                    'fact_refs':list(refs),'source':copy.deepcopy(source)})

    def is_core(term,refs):
        return term.casefold() in core_terms or any(ref.startswith(('profile.product_type','profile.core_roots:')) or facts.get(ref,{}).get('role')=='product_core' for ref in refs)

    # Optional fresh E asset snapshot must be explicitly bound to this product.
    if snapshot:
        kw=snapshot['module']
        pools=operation_runtime().cached(('st_asset_views',snapshot['path'],sku),
            lambda:snapshot['native'].select_many(lists=('master','negative','brand'),sku=sku))
        current_permissions={r['term'].casefold():((r.get('permissions') or {}).get('copy') or {}).get('allowed')
                             for r in pools.get('master',[]) if r.get('marketplace')==market}
        blocked.extend(r['term'] for r in pools.get('brand',[]) if r.get('marketplace')==market)
        for row in pools.get('negative',[]):
            if row.get('marketplace')==market:add_negative(row['term'],row)
        for row in pools.get('master',[]):
            permission=(row.get('permissions') or {}).get('copy') or {}
            if row.get('marketplace')!=market or permission.get('allowed')!='可写':continue
            refs=permission.get('fact_refs') or []
            add(row['term'],refs,tier='asset',source={'path':snapshot['path'],'sha256':snapshot['sha256'],'revision':row.get('revision'),
                 'references':row.get('sources') or row.get('source')},core=is_core(row['term'],refs),
                 translation=row.get('chinese') or '',raw={'product_match':'direct',**row})
    for record in package.get('records',[]):
        effective=copy.deepcopy(record);decision=(context.get('decisions') or {}).get(record.get('keyword_id'),{})
        for key in ('product_match','product_qualification'):
            if key not in effective and key in decision:effective[key]=decision[key]
        refs=list(dict.fromkeys((record.get('fact_refs') or [])+(decision.get('fact_refs') or [])))
        effective['fact_refs']=refs
        asset_permission=(((record.get('asset_ref') or {}).get('permissions') or {}).get('copy') or {}).get('allowed')
        if asset_permission not in (None,True,'可写'):continue
        term=record.get('keyword') or ''
        add(term,refs,tier='asset',source={'kind':'source_bound_package','lineage':record.get('lineage') or [],'source_ids':[x.get('source_id') for x in record.get('lineage',[]) if isinstance(x,dict)]},
            core=is_core(term,refs),kid=record.get('keyword_id'),translation=record.get('cn_translation') or '',raw=effective)
    # Existing research terms need product permission + actual own evidence; traffic alone never qualifies.
    for row in context.get('st_context_terms') or []:
        if not isinstance(row,dict) or row.get('copy_permission')!='可写' or row.get('product_id')!=product or str(row.get('marketplace','')).upper()!=market:continue
        refs=row.get('fact_refs') or []
        if not row.get('source') or not re.fullmatch(r'(?:sha256:)?[a-fA-F0-9]{64}',str(row.get('sha256') or '')):continue
        if not all(ref in facts and (facts[ref].get('sha256') or '').removeprefix('sha256:')==str(row['sha256']).removeprefix('sha256:') and
                   guard.term_matches(str(facts[ref].get('text') or ''),str(row.get('term') or '')) for ref in refs):continue
        add(row.get('term'),refs,tier='context',source={'kind':'existing_research','reference':row['source'],'sha256':row['sha256']},translation=row.get('translation_zh') or '',raw={'product_match':'direct',**row})
    for row in mapping.get('rows',[]) if ct else []:
        if row['label'] not in {'uses','audiences','scenarios'} or row['copy_permission']!='可写' or row['state'] not in {'known','illustrated'}:continue
        fact=facts.get(row['ct_ref']) or {}
        if not row['source'] or not re.fullmatch(r'(?:sha256:)?[a-fA-F0-9]{64}',str(row['source_sha256'] or '')) or str(fact.get('sha256') or '').removeprefix('sha256:')!=str(row['source_sha256']).removeprefix('sha256:'):continue
        for term in row.get('en_terms') or []:
            add(term,[row['ct_ref']],tier='context',source={'kind':'CT','reference':row['source'],'sha256':row['source_sha256']},raw={'product_match':'direct','skus':row.get('skus')})
    unique={}
    for row in out:
        key=row['term'].casefold()
        if key not in unique:unique[key]=row
        elif row['core']:unique[key]['core']=True
    def unit_risks(unit,row):
        return qualification(unit,row['fact_refs'],source_records[row['term'].casefold()])
    return list(unique.values()),rejected,text_risks,unit_risks,{'restrictions':restrictions,'mapping':mapping,'blocked':blocked+wrong,'exact_blocked':exact_blocked,'permissions':current_permissions}


@operation_runtime().boundary
def complete_listing(listing,package,context=None,*,rules=None,series_root=None):
    """Return effective listing + deterministic provenance. Inputs remain untouched."""
    context=context or {};output=copy.deepcopy(listing or {})
    for doc in (output,context):
        for key in ('product_id','marketplace'):
            if doc.get(key) and package.get(key) and str(doc[key]).casefold()!=str(package[key]).casefold():raise ValueError('ST product/marketplace scope mismatch')
    validator=module('st_rules','skills/amazon-listing-copywriter/scripts/validate_listing_copy.py')
    guard=module('st_eligibility','skills/_keyword_system_contract/scripts/keyword_eligibility.py')
    ctmap=module('st_normalize','scripts/s2_ct_mapping.py')
    effective_rules=validator.listing_rules(rules or context.get('listing_rules'))
    snapshot=asset_snapshot(package,context,series_root) if output.get('skus') else None
    target=effective_rules['search_terms']['target'][0];maximum=effective_rules['search_terms']['max']
    receipts=[]
    for sku in output.get('skus') or []:
        sid=str(sku.get('sku') or sku.get('asin') or '')
        pool,rejected,text_risks,unit_risks,constraints=candidates(package,context,sid,validator,guard,ctmap,snapshot,listing.get('blacklist_terms'),series_root)
        prior_selection = sku.get('core_keyword_selection') or {}
        automatic = (prior_selection.get('method') == 'current_source_bound_core' and
                     sku.get('core_keywords') == [r['term'] for r in prior_selection.get('selected',[])])
        if (not sku.get('core_keywords') or automatic) and not context.get('core_keywords'):
            eligible = [row for row in pool if row['core']]
            # The primary authored core is a small explicit choice, never every
            # core-tagged market term. Prefer the supported product phrase in
            # title and highlights; final H independently checks both fields.
            eligible.sort(key=lambda row:not (guard.term_matches(sku.get('title',''),row['term']) and
                                              guard.term_matches(sku.get('item_highlights',''),row['term'])))
            selected = eligible[:1]
            sku['core_keywords'] = [row['term'] for row in selected]
            sku['core_keyword_selection'] = {'method':'current_source_bound_core','product_id':package.get('product_id'),
                'marketplace':package.get('marketplace'),'sku':sid,'selected':copy.deepcopy(selected),
                'status':'selected' if selected else 'no_supported_core','business_acceptance':'not_granted'}
        current=sku.get('search_terms')
        if not isinstance(current,str):current=''
        front=' '.join(value for key,value in validator.consumer_fields(sku).items() if key!='search_terms' and isinstance(value,str))
        norm=lambda word:ctmap.tokens(word)[0] if ctmap.tokens(word) else word
        used={norm(w) for w in words(validator.description_plaintext(front))}
        source_hash=digest({'version':VERSION,'algorithm':'source_modes_and_rotating_core_v2','pool':pool,'frontend':front,'product':package.get('product_id'),'marketplace':package.get('marketplace'),'sku':sid,'rules':effective_rules,'constraints':constraints})
        incoming_risks=text_risks(html.unescape(current)) if current.strip() else []
        prior=sku.get('search_terms_completion') or {}
        if isinstance(prior,dict) and prior.get('sources_hash')==source_hash and prior.get('output_hash')==digest(current):
            receipts.append(copy.deepcopy(prior));continue
        seed=prior.get('seed_text',current) if isinstance(prior,dict) and prior.get('output_hash')==digest(current) and not incoming_risks else current
        text=seed;added=[];seen={norm(w) for w in words(text)};fallback=False;repetition=False

        def append(unit,row,mode):
            nonlocal text,fallback,repetition
            trial=text+(' ' if text and not text.endswith(' ') else '')+unit
            if not unit or len(trial.encode('utf-8'))>maximum:return False
            risks=unit_risks(unit,row)+text_risks(html.unescape(trial))
            if risks:
                rejection={'term':unit,'reason':'; '.join(dict.fromkeys(risks)),'stage':'append'}
                if rejection not in rejected:rejected.append(rejection)
                return False
            text=trial;seen.update(norm(w) for w in words(unit))
            added.append({'text':unit,'mode':mode,'keyword_id':row.get('keyword_id'),'fact_refs':row['fact_refs'],'source':row['source'],
                          'source_term':row['term'],'translation_zh':row.get('translation_zh') or ''})
            fallback |= mode!='new_vocabulary';repetition |= mode=='core_repetition';return True

        # Keep unsafe/overlength input visible; never strip it to manufacture a compliance pass.
        if len(text.encode())<=maximum:
            for tier in ('asset','context'):
                for row in [r for r in pool if r['tier']==tier]:
                    for word in words(row['term']):
                        if len(text.encode())>=target:break
                        if word not in STOP and not word.isdigit() and norm(word) not in seen|used:append(word,row,'new_vocabulary')
                    if len(text.encode())>=target:break
            core=[r for r in pool if r['core']]
            for row in core:
                if len(text.encode())>=target:break
                if not guard.term_matches(text,row['term']):append(row['term'],row,'core_front_reuse')
            units=[(row['term'],row) for row in core]
            units += [(word,row) for row in core for word in words(row['term']) if word not in STOP and not word.isdigit()]
            # At most maximum byte-sized iterations. Each success adds nonempty whole tokens.
            offset=0
            for _ in range(maximum):
                if len(text.encode())>=target:break
                # Rotate qualified units so fallback cannot repeatedly select
                # only the first phrase through any() short-circuiting.
                for index in range(len(units)):
                    position=(offset+index)%len(units)
                    unit,row=units[position]
                    if append(unit,row,'core_repetition'):
                        offset=(position+1)%len(units)
                        break
                else:break
        counts=Counter(norm(w) for w in words(text));size=len(text.encode('utf-8'))
        final_risks=text_risks(html.unescape(text)) if text.strip() else []
        status='needs_review' if incoming_risks or final_risks else 'complete' if target<=size<=maximum else 'no_safe_core' if not any(r['core'] for r in pool) else 'target_gap'
        receipt={'schema_version':VERSION,'product_id':package.get('product_id'),'marketplace':package.get('marketplace'),'sku':sid,
                 'seed_text':seed,'seed_bytes':len(seed.encode()),'output_hash':digest(text),'output_bytes':size,'sources_hash':source_hash,
                 'target':target,'maximum':maximum,'target_met':target<=size<=maximum,'status':status,'fallback_used':fallback,'core_repetition_used':repetition,
                 'source_mode':'approved_core_fallback' if fallback else 'new_sources' if added else 'provided_retained',
                 'distinct_tokens':len(counts),'repeated_tokens':{w:n for w,n in counts.items() if n>1},'added':added,'rejected':rejected,
                 'incoming_risks':incoming_risks,'final_risks':final_risks,'warnings':['User-authorized approved-core reuse; avoiding repetition remains an Amazon quality preference, not a ranking boost.'] if fallback else [],
                 'reason':'Automatic existing sources → mapped CT/research → approved core reuse → bounded core repetition; no new permission or product facts.',
                 'business_acceptance':'not_granted'}
        sku['search_terms']=text;sku['search_terms_completion']=receipt
        if added:
            meta=sku.setdefault('review_metadata',{}).setdefault('fields',{}).setdefault('search_terms',{})
            base_note=prior.get('base_translation_zh',meta.get('translation_zh') or '') if isinstance(prior,dict) else meta.get('translation_zh') or ''
            receipt['base_translation_zh']=base_note
            translated=list(dict.fromkeys(row['translation_zh'] or row['source_term'] for row in added))
            meta['translation_zh']=base_note+'；自动补齐'+('（含已批准核心词回退）' if fallback else '')+'：'+'；'.join(translated)
            meta['keyword_ids']=list(dict.fromkeys((meta.get('keyword_ids') or [])+[a['keyword_id'] for a in added if a.get('keyword_id')]))
            meta['fact_refs']=list(dict.fromkeys((meta.get('fact_refs') or [])+[ref for a in added for ref in a['fact_refs']]))
            meta['pending_items']=list(dict.fromkeys((meta.get('pending_items') or [])+['ST已自动更新，需按实际输出复核；不代表业务或算法验收。']))
            meta['review_status']='pending'
        receipts.append(copy.deepcopy(receipt))
    return {'listing':output,'receipt':{'schema_version':VERSION,'variants':receipts,'business_execution':False}}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for arg in ('listing','package','context','output-dir'):parser.add_argument('--'+arg,required=True)
    parser.add_argument('--series',help='Real series root; otherwise discover a nearby existing asset only.')
    args=parser.parse_args(argv)
    def load(path):
        text=Path(path).read_text(encoding='utf-8-sig')
        try:value=json.loads(text)
        except json.JSONDecodeError:
            import yaml
            value=yaml.safe_load(text)
        if not isinstance(value,dict):raise ValueError('ST input must be a JSON/YAML object')
        return value
    series=args.series or next((str(parent) for parent in list(Path(args.package).resolve().parents)[:6]
        if (parent/'workflow_state.yaml').is_file() and (parent/'_词库资产/keyword_master.yaml').is_file()),None)
    result=complete_listing(load(args.listing),load(args.package),load(args.context),series_root=series)
    out=Path(args.output_dir);out.mkdir(parents=True,exist_ok=True)
    for filename,value in [('effective_listing.json',result['listing']),('search_terms_completion.json',result['receipt'])]:
        (out/filename).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'output_dir':str(out),'variants':[{'sku':r['sku'],'bytes':r['output_bytes'],'status':r['status'],'fallback':r['fallback_used']} for r in result['receipt']['variants']]},ensure_ascii=False))
    return 0 if all(r['status']=='complete' for r in result['receipt']['variants']) else 1


if __name__=='__main__':raise SystemExit(main())
