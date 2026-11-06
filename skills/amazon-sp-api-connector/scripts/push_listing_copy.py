#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文案一键推送（B店铺实测需求，2026-09-17）：确认后把 07 文案 PATCH 进现有 Listing。

默认 dry-run：只读线上值、保存本地完整审核提案，不写Amazon。
推送必须同时满足：--confirm + --reviewed-by + --reviewed-at（对应文案推送门的人工复核）。
历史版只有可选JSONL和手工核对。R02要求已保存的proposal、匹配旧状态及身份/patch摘要，先持久化完整备份再写并实际回读；回滚须 --rollback --confirm 与人工审核，回滚后同样回读。H批次接入最终合规证明门禁。

用法：
  python push_listing_copy.py --config <sp_api_config.yaml> --seller-id S --sku SKU \\
      --product-type PRODUCT_TYPE --marketplace ATVPDKIKX0DER \\
      --patch-json <07文案转出的patch数组.json> --listing-json <07B已审核文案.json> \\
      --reviewed-by <复核人> --reviewed-at <YYYY-MM-DD> [--confirm] [--log <run/...jsonl>]
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import os
import re
import tempfile
import datetime
import importlib.util
import json
import sys
from pathlib import Path


def _load_client():
    path = Path(__file__).resolve().parent / "sp_api_client.py"
    spec = importlib.util.spec_from_file_location("_sp_push_client", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _summarize(value) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= 90 else text[:90] + "…"


IDENTITY_KEYS = ('seller_id','sku','marketplace','product_type')
WRITABLE = frozenset(['item_name','bullet_point','product_description','generic_keyword'])


def digest(value):
    return 'sha256:'+hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def durable(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as out:
            json.dump(value,out,ensure_ascii=False,indent=2,allow_nan=False);out.write('\n');out.flush();os.fsync(out.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name): os.unlink(name)


def identity_checked(identity):
    if set(identity)!=set(IDENTITY_KEYS) or any(not isinstance(identity[k],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}' if k=='product_type' else r'[A-Za-z0-9_-]{1,64}',identity[k]) for k in IDENTITY_KEYS):
        raise ValueError('listing_identity_invalid')
    return copy.deepcopy(identity)


def checked_patches(patches):
    if not isinstance(patches,list) or not 1<=len(patches)<=8: raise ValueError('patches_invalid')
    names=[]
    for patch in patches:
        if not isinstance(patch,dict) or set(patch)!={'op','path','value'} or patch['op']!='replace': raise ValueError('replace_only')
        name=str(patch['path']).removeprefix('/attributes/')
        if patch['path']!='/attributes/'+name or name not in WRITABLE or name in names: raise ValueError('attribute_not_writable_or_duplicate')
        if len(json.dumps(patch['value'],allow_nan=False).encode())>8192: raise ValueError('value_too_large')
        if name=='product_description':
            if not isinstance(patch['value'],list) or not patch['value'] or any(not isinstance(row,dict) or not isinstance(row.get('value'),str) for row in patch['value']):
                raise ValueError('description_structured_plaintext_required')
            for row in patch['value'] if isinstance(patch['value'],list) else []:
                if isinstance(row,dict) and re.search(r'<[^>]*>',str(row.get('value',''))): raise ValueError('description_plaintext_required')
        names.append(name)
    return names


def read_exact(client, identity):
    item=client.get_listing_item(identity['seller_id'],identity['sku'],identity['marketplace'])
    if not isinstance(item,dict) or item.get('sku')!=identity['sku'] or not isinstance(item.get('attributes'),dict):
        raise ValueError('existing_sku_not_found_manual_creation_required')
    summaries=item.get('summaries')
    matches=[row for row in summaries if isinstance(row,dict) and row.get('marketplaceId')==identity['marketplace']] if isinstance(summaries,list) else []
    if len(matches)!=1 or matches[0].get('productType')!=identity['product_type']:
        raise ValueError('listing_summary_identity_missing_ambiguous_or_mismatch')
    return copy.deepcopy(item['attributes'])


def source_bindings(identity, patches, reviewed_listing, effective_rules=None, *, listing_hash=None, rules_hash=None):
    """Derive required hashes from current copy/rules, never trust supplied hashes.

    CLI rereads the reviewed listing file on each operation. H will additionally
    bind fresh compliance proof at the existing write seam.
    """
    if not isinstance(reviewed_listing,dict) or not isinstance(reviewed_listing.get('skus'),list):
        raise ValueError('reviewed_listing_source_required')
    skus=[sku for sku in reviewed_listing['skus'] if isinstance(sku,dict) and sku.get('sku')==identity['sku']]
    if len(skus)!=1: raise ValueError('reviewed_listing_sku_missing_or_duplicate')
    sku=skus[0]
    home=Path(__file__).resolve().parents[3]
    spec=importlib.util.spec_from_file_location('_sp_copy_binding_rules',home/'skills/amazon-listing-copywriter/scripts/validate_listing_copy.py')
    validator=importlib.util.module_from_spec(spec);spec.loader.exec_module(validator)
    description=validator.description_value(sku,strict_aliases=True)
    description=validator.description_plaintext(description) if isinstance(description,str) else None
    source_fields={'item_name':sku.get('title'),'bullet_point':sku.get('bullets'),
                   'product_description':description,'generic_keyword':sku.get('search_terms')}
    for patch in patches:
        name=patch['path'].split('/')[-1];source=source_fields[name]
        expected=source if name=='bullet_point' else [source]
        value=patch['value']
        if not isinstance(expected,list) or not expected or any(not isinstance(x,str) or not x.strip() for x in expected):
            raise ValueError('reviewed_listing_field_missing:'+name)
        if not isinstance(value,list) or any(not isinstance(row,dict) or not isinstance(row.get('value'),str) or
                (row.get('marketplace_id') is not None and row['marketplace_id']!=identity['marketplace']) for row in value) or [row['value'] for row in value]!=expected:
            raise ValueError('patch_not_from_reviewed_listing:'+name)
    if effective_rules is not None and (not isinstance(effective_rules,dict) or not effective_rules):
        raise ValueError('effective_rules_source_invalid')
    rules=validator.listing_rules(effective_rules)
    derived={'listing_hash':digest(reviewed_listing),'rules_hash':digest(rules)}
    for key,claimed in [('listing_hash',listing_hash),('rules_hash',rules_hash)]:
        if claimed is not None and (not isinstance(claimed,str) or not re.fullmatch(r'sha256:[0-9a-f]{64}',claimed) or claimed!=derived[key]):
            raise ValueError(key+'_does_not_match_current_source')
    return derived


def prepare_review(client, identity, patches, path, *, reviewed_listing=None, effective_rules=None, listing_hash=None, manual_fields=None, rules_hash=None):
    identity=identity_checked(identity);names=checked_patches(patches)
    bindings=source_bindings(identity,patches,reviewed_listing,effective_rules,listing_hash=listing_hash,rules_hash=rules_hash)
    before=read_exact(client,identity)
    if any(name not in before for name in names): raise ValueError('old_attribute_missing_replace_rollback_unavailable')
    proposal={'schema':'listing-review-2','identity':identity,'patches':copy.deepcopy(patches),
              'before':before,'before_relevant':{name:before[name] for name in names},
              'rollback_patches':[{'op':'replace','path':'/attributes/'+name,'value':before[name]} for name in names],
              'permitted_fields':names,'manual_fields':list(manual_fields or []),**bindings,
              'compliance_status':'awaiting_final_H_gate',
              'diff':[{'attribute':name,'old':before[name],'new':patch['value']} for name,patch in zip(names,patches)],
              'created_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    proposal['proposal_hash']=digest(proposal)
    durable(path,proposal)
    return proposal


def compliance_module():
    home=Path(__file__).resolve().parents[3]
    spec=importlib.util.spec_from_file_location('_sp_h_compliance',home/'scripts/compliance_scan.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod


def write_subject(proposal, listing, context, rollback=False):
    identity=proposal['identity']
    if not isinstance(context,dict) or not context.get('product_id'):
        raise ValueError('compliance_product_context_required')
    for key in ['seller_id','marketplace','product_type']:
        if context.get(key)!=identity[key]: raise ValueError('compliance_identity_mismatch:'+key)
    if listing.get('product_id') not in (None,context['product_id']) or listing.get('marketplace') not in (None,identity['marketplace']):
        raise ValueError('compliance_listing_identity_mismatch')
    subject=copy.deepcopy(listing)
    if rollback:
        sku=next(s for s in subject['skus'] if s['sku']==identity['sku'])
        names={'item_name':'title','bullet_point':'bullets','product_description':'description','generic_keyword':'search_terms'}
        for patch in proposal['rollback_patches']:
            name=patch['path'].split('/')[-1]; values=[row['value'] for row in patch['value']]
            if name=='product_description':
                for key in ['description','product_description','long_description','description_html']: sku.pop(key,None)
            sku[names[name]]=values if name=='bullet_point' else values[0]
            if name=='product_description':
                import html
                sku['description_html']='<p>'+html.escape(values[0]).replace('\n','<br>')+'</p>'
    return subject


def create_write_proof(proposal, listing, context, *, series=None, rules=None, rollback=False):
    if not series or not Path(series).is_dir(): raise ValueError('compliance_current_series_required')
    scanner=compliance_module(); subject=write_subject(proposal,listing,context,rollback)
    return scanner.make_receipt(subject,series=series,context=context,rules=rules,
        proposal={'review':proposal,'rollback':rollback},allowed_fields=proposal['permitted_fields'],
        mapping=context.get('ct_mapping'),editorial=True)


def execute_review(client, path, identity, patches, *, confirm=False, reviewed_by='', reviewed_at='', rollback=False, compliance_gate=None, reviewed_listing=None, effective_rules=None, compliance_receipt=None, compliance_context=None, series=None):
    if confirm is not True or not reviewed_by.strip() or not reviewed_at.strip(): raise ValueError('human_review_and_confirm_required')
    proposal=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    claimed=proposal.pop('proposal_hash',None)
    if claimed!=digest(proposal): raise ValueError('proposal_hash_mismatch')
    proposal['proposal_hash']=claimed
    if proposal['identity']!=identity_checked(identity) or proposal['patches']!=patches: raise ValueError('proposal_scope_or_patch_changed')
    names=checked_patches(patches)
    if names!=proposal['permitted_fields']: raise ValueError('permitted_fields_changed')
    current_bindings=source_bindings(identity,patches,reviewed_listing,effective_rules)
    for key,value in current_bindings.items():
        if not isinstance(proposal.get(key),str) or not re.fullmatch(r'sha256:[0-9a-f]{64}',proposal[key]) or proposal[key]!=value:
            raise ValueError('reviewed_source_binding_stale_or_missing:'+key)
    # H installs the mandatory fresh compliance proof at this single write seam.
    # A callback must evaluate this bound proposal, including identity/patches/hash.
    if not isinstance(compliance_receipt,dict): raise ValueError('compliance_proof_required')
    if not series or not Path(series).is_dir(): raise ValueError('compliance_current_series_required')
    scanner=compliance_module(); subject=write_subject(proposal,reviewed_listing,compliance_context,rollback)
    checked=scanner.verify_current_compliance(compliance_receipt,listing=subject,series=series,context=compliance_context,
        rules=effective_rules,proposal={'review':proposal,'rollback':rollback},allowed_fields=names,
        mapping=compliance_context.get('ct_mapping'),editorial=True)
    if not checked['ok']: raise ValueError('compliance_proof_required:'+','.join(checked['errors']))
    if compliance_gate is not None and compliance_gate(copy.deepcopy(proposal)) is not True: raise ValueError('compliance_proof_required')
    current=read_exact(client,identity)
    expected={p['path'].split('/')[-1]:p['value'] for p in patches} if rollback else proposal['before_relevant']
    if any(current.get(name)!=expected[name] for name in names): raise ValueError('old_state_changed_new_review_required')
    operations=proposal['rollback_patches'] if rollback else patches
    checked_patches(operations)
    receipt_path=Path(str(path)+('.rollback.json' if rollback else '.execution.json'))
    receipt={'identity':identity,'proposal_hash':claimed,'permitted_fields':names,'reviewed_by':reviewed_by,'reviewed_at':reviewed_at,
             'rollback':rollback,'before':current,'operations':operations,'status':'prepared_backup','business_execution':True}
    # This full old-state/rollback receipt must exist durably before any PATCH.
    durable(receipt_path,receipt)
    try:
        result=client.patch_listing_item(identity['seller_id'],identity['sku'],identity['product_type'],operations,marketplace=identity['marketplace'],confirm=True)
        receipt['submission']=result;receipt['status']='submitted'
        durable(receipt_path,receipt)
    except Exception as exc:
        receipt['status']='write_failed';receipt['error_type']=type(exc).__name__;durable(receipt_path,receipt);raise
    try:
        actual=read_exact(client,identity);desired={p['path'].split('/')[-1]:p['value'] for p in operations}
        receipt['readback']=actual
        receipt['mismatched_fields']=[name for name,value in desired.items() if actual.get(name)!=value]
        receipt['status']='verified' if not receipt['mismatched_fields'] else 'pending_readback'
    except Exception as exc:
        receipt['status']='pending_readback';receipt['readback_error_type']=type(exc).__name__
    if isinstance(result,dict) and (result.get('status') in {'INVALID','ERROR','REJECTED'} or any(isinstance(issue,dict) and issue.get('severity')=='ERROR' for issue in result.get('issues',[]) if isinstance(result.get('issues'),list))):
        receipt['status']='rejected'
    durable(receipt_path,receipt)
    return receipt


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    for name in ['config','seller-id','sku','product-type','marketplace','patch-json']: ap.add_argument('--'+name,required=True)
    ap.add_argument('--proposal',help='Default: patch-json.review.json; create by dry-run, inspect before confirm.')
    ap.add_argument('--listing-json',required=True,help='Actual reviewed listing JSON; reread before confirmation.')
    ap.add_argument('--rules-json',help='Current effective rule overrides; absent derives current production resolver.')
    ap.add_argument('--compliance-proof');ap.add_argument('--compliance-context');ap.add_argument('--series')
    ap.add_argument('--listing-hash');ap.add_argument('--rules-hash');ap.add_argument('--manual-fields-json')
    ap.add_argument('--reviewed-by',default='');ap.add_argument('--reviewed-at',default='')
    ap.add_argument('--confirm',action='store_true');ap.add_argument('--rollback',action='store_true');ap.add_argument('--log',default='')
    a=ap.parse_args(argv)
    try:
        mod=_load_client();client=mod.SpApiClient(mod.load_config(a.config))
        patches=json.loads(Path(a.patch_json).read_text(encoding='utf-8-sig'))
        reviewed_listing=json.loads(Path(a.listing_json).read_text(encoding='utf-8-sig'))
        effective_rules=json.loads(Path(a.rules_json).read_text(encoding='utf-8-sig')) if a.rules_json else None
        identity={'seller_id':a.seller_id,'sku':a.sku,'product_type':a.product_type,'marketplace':a.marketplace}
        path=a.proposal or a.patch_json+'.review.json'
        if not a.confirm:
            if a.rollback: raise ValueError('rollback_requires_confirm_and_review')
            manual=json.loads(Path(a.manual_fields_json).read_text(encoding='utf-8-sig')) if a.manual_fields_json else []
            result=prepare_review(client,identity,patches,path,reviewed_listing=reviewed_listing,effective_rules=effective_rules,listing_hash=a.listing_hash,rules_hash=a.rules_hash,manual_fields=manual)
            print(json.dumps({'status':'preview','proposal':str(path),'proposal_hash':result['proposal_hash'],'diff':result['diff'],'manual_fields':result['manual_fields']},ensure_ascii=False,indent=2))
        else:
            result=execute_review(client,path,identity,patches,confirm=a.confirm,reviewed_by=a.reviewed_by,reviewed_at=a.reviewed_at,rollback=a.rollback,reviewed_listing=reviewed_listing,effective_rules=effective_rules, compliance_receipt=json.loads(Path(a.compliance_proof).read_text(encoding='utf-8-sig')) if a.compliance_proof else None, compliance_context=json.loads(Path(a.compliance_context).read_text(encoding='utf-8-sig')) if a.compliance_context else None,series=a.series)
            print(json.dumps({'status':result['status'],'sku':a.sku,'receipt':str(path)+('.rollback.json' if a.rollback else '.execution.json')},ensure_ascii=False))
        _write_log(a.log,{'ts':datetime.datetime.now(datetime.timezone.utc).isoformat(),'sku':a.sku,'dry_run':not a.confirm,
                          'status':result.get('status','preview'),'proposal_hash':result.get('proposal_hash'),'proposal':str(path)})
        return 0
    except Exception as exc:
        print(json.dumps({'status':'fail','error':str(exc)[:160]},ensure_ascii=False));return 2


def _write_log(path: str, record: dict) -> None:
    if not path:
        return
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
