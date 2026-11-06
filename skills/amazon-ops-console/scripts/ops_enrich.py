"""Optional costs and product-bound observations for reviewed local snapshots."""
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import math
from zoneinfo import ZoneInfo

from ops_costs import load_costs, estimate_daily
from ops_rules import check_listing, keyword_coverage
from ops_collect import load_ads_config, load_sp_config


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):
        return None


def verified(base, info):
    if not isinstance(info,dict):
        return None
    name=info.get('file',info.get('path'))
    if not isinstance(name,str):
        return None
    try:
        base=Path(base).resolve();path=(base/name).resolve()
        if Path(name).is_absolute() or not path.is_relative_to(base) or not path.is_file() or path.stat().st_size>128*1024*1024:
            return None
        data=path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=info.get('sha256'):
            return None
        return json.loads(data.decode('utf-8-sig'))
    except (OSError,ValueError,TypeError):
        return None


def _timestamp(value):
    try:
        stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
        return stamp if stamp.tzinfo is not None else None
    except (AttributeError,TypeError,ValueError):
        return None


def _period(binding):
    try:
        zone=ZoneInfo(binding['timezone']);period=binding['period']
        first=date.fromisoformat(period['startDate']);last=date.fromisoformat(period['endDate'])
        if first>last or (last-first).days>31: return None
        start=datetime.combine(first,datetime.min.time(),zone)
        end=datetime.combine(last+timedelta(days=1),datetime.min.time(),zone)
        return start,end,zone
    except (KeyError,TypeError,ValueError):
        return None


def _fresh_timestamp(value,bounds,reference_time):
    stamp,reference=_timestamp(value),_timestamp(reference_time)
    if stamp is None or reference is None or bounds is None: return False
    _,end,zone=bounds
    # This is enrichment of the current closed-day run, not promotion of retained history.
    return end<=stamp<=reference and stamp.astimezone(zone).date()==reference.astimezone(zone).date()==end.date()


def _fresh_row(row,bounds,reference_time):
    return (row.get('complete') is True and row.get('stale') is not True
            and _fresh_timestamp(row.get('dataCollectedAt'),bounds,reference_time)
            and _timestamp(row.get('dataCollectedAt'))==_timestamp(row.get('sourceCheckedAt')))


def _completed_report_job(manifest,kind,context,bounds,reference_time):
    """Report completion is independent of entity-list pagination completeness."""
    job=(manifest.get('jobs') or {}).get(kind) or {}
    request=job.get('request') or {};config=request.get('configuration') or {}
    report_type,groups={'campaign_adgroups':('spCampaigns',['campaign','adGroup']),
                        'search_terms':('spSearchTerm',['searchTerm'])}[kind]
    columns=config.get('columns')
    required={'campaignId','adGroupId','date','cost',context.get('sales_field'),context.get('orders_field')}
    if kind=='search_terms': required.update(('keywordId','searchTerm'))
    return (job.get('status')=='COMPLETED' and isinstance(job.get('report_id'),str) and bool(job['report_id'])
            and _fresh_timestamp(job.get('downloaded_at'),bounds,reference_time)
            and request.get('startDate')==context.get('start_date') and request.get('endDate')==context.get('end_date')
            and config.get('adProduct')=='SPONSORED_PRODUCTS' and config.get('reportTypeId')==report_type
            and config.get('groupBy')==groups and config.get('timeUnit')=='DAILY' and config.get('format')=='GZIP_JSON'
            and isinstance(columns,list) and all(isinstance(v,str) for v in columns) and required.issubset(columns))


def verified_campaign_report(run_dir,binding,ads_config,market,reference_time):
    """Verify the account, period and SHA of the current campaign report."""
    result={'state':'blocked','issues':[],'source_context':{},'campaign_rows':[],
            'campaign_sha256':None,'mapping_sha256':None,'group_skus':{},'single_sku_groups':{},'evidence':[]}
    def block(code):
        result['issues'].append(code);return result
    run_dir=Path(run_dir);bounds=_period(binding)
    ads_dir=run_dir/'ads';manifest=_read(ads_dir/'manifest.json') or {}
    context=manifest.get('source_context') or {}
    expected_currency={'US':'USD','UK':'GBP'}.get(market)
    expected_region={'US':'NA','UK':'EU'}.get(market)
    country='UK' if context.get('country')=='GB' else context.get('country')
    if (not bounds or not binding.get('store') or binding.get('market')!=market
            or not re.fullmatch(r'[0-9]+',str(ads_config.get('profile_id') or ''))
            or str(binding.get('ads_profile_id'))!=str(ads_config.get('profile_id'))
            or context.get('source')!='amazon_ads_api' or str(context.get('profile_id'))!=str(ads_config.get('profile_id'))
            or context.get('region')!=ads_config.get('region') or context.get('region')!=expected_region
            or context.get('attribution_days') not in (7,14)
            or context.get('sales_field')!=f"sales{context.get('attribution_days')}d"
            or context.get('orders_field')!=f"purchases{context.get('attribution_days')}d"
            or country!=market or context.get('currency')!=expected_currency or context.get('timezone')!=binding.get('timezone')
            or context.get('start_date')!=(binding.get('period') or {}).get('startDate')
            or context.get('end_date')!=(binding.get('period') or {}).get('endDate')):
        return block('ads_scope_or_period_mismatch')
    if (manifest.get('stale') or not _fresh_timestamp(manifest.get('updated_at'),bounds,reference_time)
            or not _completed_report_job(manifest,'campaign_adgroups',context,bounds,reference_time)):
        return block('ads_report_not_current_complete')
    info=(manifest.get('files') or {}).get('campaign_adgroups') or {}
    rows=verified(ads_dir,info)
    job=manifest['jobs']['campaign_adgroups']
    if (not isinstance(rows,list) or not isinstance(job.get('row_count'),int) or isinstance(job.get('row_count'),bool)
            or len(rows)!=job['row_count']): return block('campaign_report_integrity_failed')
    seen=set();campaigns=set();groups=set()
    for row in rows:
        if not isinstance(row,dict): return block('campaign_report_invalid')
        cid,aid=str(row.get('campaignId') or ''),str(row.get('adGroupId') or '')
        day=row.get('date');key=(cid,aid,day)
        try: valid_date=isinstance(day,str) and date.fromisoformat(day).isoformat()==day
        except (TypeError,ValueError): valid_date=False
        if (not cid.isdigit() or not aid.isdigit() or not isinstance(day,str)
                or not valid_date or not context['start_date']<=day<=context['end_date'] or key in seen):
            return block('campaign_report_scope_or_duplicate')
        seen.add(key);campaigns.add(cid);groups.add((cid,aid))
    return {**result,'state':'complete','source_context':context,'campaign_rows':rows,'campaign_sha256':info['sha256'],
            'batch_state':manifest.get('state'),'batch_issues':manifest.get('issues') or [],
            'entity_coverage_complete':manifest.get('state')=='complete' and not manifest.get('issues'),
            'evidence':[{'path':str(ads_dir/(info.get('path') or info['file'])),'sha256':info['sha256']}]}


def verified_product_map(run_dir,binding,ads_config,market,reference_time):
    """Return only current account/period/SHA-bound product groups; blocked results are empty."""
    result=verified_campaign_report(run_dir,binding,ads_config,market,reference_time)
    if result['state']!='complete': return result
    def block(code):
        return {**result,'state':'blocked','issues':[code],'source_context':{},'campaign_rows':[],
                'campaign_sha256':None,'mapping_sha256':None,'group_skus':{},'single_sku_groups':{},'evidence':[]}
    run_dir=Path(run_dir);bounds=_period(binding)
    rows=result['campaign_rows'];context=result['source_context']
    campaigns={str(r['campaignId']) for r in rows}
    groups={(str(r['campaignId']),str(r['adGroupId'])) for r in rows}
    mapping_dir=run_dir/'ads_product_map';mapping_manifest=_read(mapping_dir/'manifest.json') or {}
    if (mapping_manifest.get('scope')!={'profile_id':str(ads_config['profile_id']),'market':market}
            or mapping_manifest.get('state')!='complete' or mapping_manifest.get('errors') or mapping_manifest.get('error')
            or mapping_manifest.get('stale') or mapping_manifest.get('source_report_sha256')!=result['campaign_sha256']
            or mapping_manifest.get('mapping_campaign_scope')!=sorted(campaigns)
            or not _fresh_timestamp(mapping_manifest.get('collected_at'),bounds,reference_time)):
        return block('product_mapping_scope_or_report_mismatch')
    map_info=mapping_manifest.get('file') or (mapping_manifest.get('files') or {}).get('product_ads')
    mapping=verified(mapping_dir,map_info)
    if isinstance(mapping,dict): mapping=mapping.get('product_ads',mapping.get('rows'))
    if not isinstance(mapping,list): return block('product_mapping_integrity_failed')
    group_skus=defaultdict(set)
    for ad in mapping:
        if not isinstance(ad,dict): return block('product_mapping_invalid')
        cid,aid=str(ad.get('campaignId') or ''),str(ad.get('adGroupId') or '')
        if cid not in campaigns or not aid.isdigit() or not all(isinstance(ad.get(k),str) and ad[k] for k in ('asin','sku')):
            return block('product_mapping_invalid')
        if (cid,aid) in groups: group_skus[(cid,aid)].add((ad['asin'],ad['sku']))
    return {**result,'state':'complete','source_context':context,'campaign_rows':rows,
            'mapping_sha256':map_info['sha256'],'group_skus':dict(group_skus),
            'single_sku_groups':{k:next(iter(v)) for k,v in group_skus.items() if len(v)==1},
            'evidence':[*result['evidence'],{'path':str(mapping_dir/(map_info.get('path') or map_info['file'])),'sha256':map_info['sha256']}]}


def _query(label, rows, evidence, definition):
    return {'rows':rows,'source':{'label':label,'provider':'Local deterministic analysis',
        'evidenceFlow':[{'title':'来源','detail':definition}],
        'files':evidence,'metricDefinitions':[{'label':label,'definition':definition}]}}


def _run_dir(root,market):
    candidates=sorted(p for p in (root/'runs').glob('*_'+market) if re.fullmatch(r'\d{8}_'+market,p.name))
    return candidates[-1] if candidates else None


def _sp_domain(run_dir,name):
    base=run_dir/'sp'/name;status=_read(base/'status.json')
    if isinstance(status,dict):
        return verified(base,status)
    status=_read(run_dir/'sp/status.json') or {}
    return verified(run_dir/'sp',(status.get('domains') or {}).get(name))


def enrich_snapshot(config,snapshot):
    root=Path(config['workspace']);queries=snapshot['queries']
    costs_rows,profits,checks,observations=[],[],[],[]
    evidence=[];report=['# 店铺巡检与收益估算','',f'生成时间：{datetime.now(timezone.utc).isoformat()}','',
        '所有金额按站点币种分别计算。批准与执行分开；本次没有修改 Amazon 后台。','']
    rules=_read(config.get('listing_rules_file','')) or {}
    for item in config['markets']:
        market=item['market'];run_dir=_run_dir(root,market)
        if run_dir is None:
            continue
        current_sp=load_sp_config(item['sp_config']);current_ads=load_ads_config(item['ads_config'])
        expected_sp={'marketplace_id':current_sp.get('marketplace_id'),'seller_id':current_sp.get('seller_id')}
        context=_read(run_dir/'source_context.json') or {}
        reference_time=snapshot.get('generatedAt');bounds=_period(context)
        if (not all(isinstance(v,str) and v for v in expected_sp.values()) or not bounds
                or context.get('timezone')!=item.get('timezone')
                or context.get('store')!=config['store'] or context.get('market')!=market
                or any(context.get(k)!=v for k,v in expected_sp.items())
                or str(context.get('ads_profile_id'))!=str(current_ads.get('profile_id'))):
            report += [f'## {market}', '- 账号关联尚未验证，暂停成本和产品关键词关联。','']
            continue
        costs=load_costs(item.get('cost_file'),config['store'],market)
        costs_rows.extend({**r,'landed_cost':r.get('landed_unit_cost')} for r in costs['rows'])
        if costs.get('sha256'):
            evidence.append({'name':Path(item['cost_file']).name,'sha256':costs['sha256']})
            (root/'costs').mkdir(exist_ok=True)
            (root/'costs'/f'{market}.json').write_text(json.dumps(costs,ensure_ascii=False,indent=2),encoding='utf8')
        source=run_dir/'sp/sku_sales';manifest=_read(source/'manifest.json') or {}
        period=manifest.get('period') or {}
        sku_collection_complete=(manifest.get('scope')==expected_sp and manifest.get('market')==market
            and manifest.get('state') in (None,'complete') and not manifest.get('errors') and not manifest.get('stale')
            and _timestamp(period.get('start'))==bounds[0] and _timestamp(period.get('end'))==bounds[1]
            and _fresh_timestamp(manifest.get('collected_at'),bounds,reference_time))
        expected_days={(bounds[0].date()+timedelta(days=n)).isoformat() for n in range((bounds[1].date()-bounds[0].date()).days)}
        sku_rows=[];valid_skus=set()
        for sku,info in manifest.get('files',{}).items():
            raw=verified(source,info)
            if not isinstance(raw,dict) or raw.get('sku')!=sku:
                continue
            result=raw.get('result') or {};payload=result.get('payload') if isinstance(result,dict) else None
            if not isinstance(payload,list): continue
            intervals=[];intervals_valid=True
            for entry in payload:
                if not isinstance(entry,dict): intervals_valid=False;break
                parts=str(entry.get('interval','')).split('--')
                first,last=(_timestamp(v) for v in parts) if len(parts)==2 else (None,None)
                if (first is None or last is None or first<bounds[0] or last>bounds[1]
                        or first.astimezone(bounds[2]).time()!=datetime.min.time()
                        or last.astimezone(bounds[2]).time()!=datetime.min.time()
                        or (last.astimezone(bounds[2]).date()-first.astimezone(bounds[2]).date()).days!=1):
                    intervals_valid=False;break
                intervals.append(first.astimezone(bounds[2]).date().isoformat())
            sku_complete=(sku_collection_complete and result.get('complete') is not False and not result.get('errors')
                          and intervals_valid and len(intervals)==len(set(intervals)) and set(intervals)==expected_days)
            if sku_complete: valid_skus.add(sku)
            for row in payload:
                if not isinstance(row,dict): continue
                row_start=_timestamp(str(row.get('interval','')).split('--')[0])
                day=row_start.astimezone(bounds[2]).date().isoformat() if row_start else None
                if day not in expected_days: continue
                amount=row.get('totalSales') or {}
                try:
                    revenue=float(amount['amount']) if amount.get('amount') is not None else None
                except (ValueError,TypeError):
                    revenue=None
                sku_rows.append({'store':config['store'],'market':market,'sku':sku,'date':day,
                    'currency':amount.get('currencyCode'),'sales':revenue,'units':row.get('unitCount'),
                    'sales_tax_basis':item.get('sales_tax_basis','unverified'),'complete':sku_complete,
                    'dataCollectedAt':manifest.get('collected_at'),'sourceCheckedAt':manifest.get('collected_at'),
                    'source_sha256':info.get('sha256')})
        campaign_source=verified_campaign_report(run_dir,context,current_ads,market,reference_time)
        # Aggregate only the verified campaign/ad-group report once; snapshot layers can overlap.
        daily_ads=defaultdict(list)
        for r in campaign_source['campaign_rows']: daily_ads[r['date']].append(r.get('cost'))
        ads=[]
        snapshot_ads_fresh=not any(r.get('stale') or r.get('complete') is False for r in queries.get('ad_daily',{}).get('rows',[]) if r.get('market')==market)
        for day in sorted(expected_days):
            values=daily_ads.get(day,[])
            numeric=all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) and v>=0 for v in values)
            complete=campaign_source['state']=='complete' and numeric and snapshot_ads_fresh
            ads.append({'store':config['store'],'market':market,'date':day,'currency':costs['currency'],
                        'spend':sum(values) if complete else None,'complete':complete,'layer':'store',
                        'source_sha256':campaign_source.get('campaign_sha256')})
        store_sales=[r for r in queries.get('sales_daily',{}).get('rows',[]) if r['market']==market and not r.get('isIntraday')]
        products=[r for r in queries.get('products',{}).get('rows',[]) if r['market']==market]
        expected_skus={r['sku'] for r in products}
        if costs['rows']:
            for row in estimate_daily(costs,sku_rows,ads):
                comparison=[r for r in store_sales if r.get('date')==row['date']]
                matched=(len(comparison)==1 and _fresh_row(comparison[0],bounds,reference_time)
                         and comparison[0].get('currency')==costs['currency'] and comparison[0].get('store',config['store'])==config['store']
                         and isinstance(comparison[0].get('sales'),(int,float)) and not isinstance(comparison[0].get('sales'),bool)
                         and math.isfinite(comparison[0]['sales']) and row['sales'] is not None
                         and abs(comparison[0]['sales']-row['sales'])<=.02)
                row['sku_sales_reconciled']=matched
                if not matched:
                    row['issues'].append('sku_sales_not_reconciled_to_store')
                    row['estimated_profit']=None;row['state']='partial'
                if (not expected_skus or not expected_skus.issubset(valid_skus) or not sku_collection_complete
                        or any(not _fresh_row(p,bounds,reference_time) for p in products)):
                    row['issues'].append('sku_collection_incomplete')
                    row['estimated_profit']=None;row['state']='partial'
                details=row['sku_rows']
                full=details and all(d.get('contribution') is not None for d in details)
                row['product_cost']=sum(d['sales']-d['contribution'] for d in details) if full else None
                coverage=row.get('cost_coverage_sales')
                row['coverage']=f'销售额覆盖 {coverage:.1%}' if coverage is not None else '待核对覆盖率'
                row['basis_note']='按已维护单价及费率估算；退款、仓储与结算费用尚未核对'
                translations={'vat_basis_unverified':'英国销售额是否含税待核对','commission_basis_unverified':'佣金计费基数待核对',
                    'sku_cost_missing_or_incomplete':'部分有销量 SKU 缺成本','sku_sales_not_reconciled_to_store':'SKU 与全店销售尚未对齐',
                    'sku_collection_incomplete':'SKU 销售采集不全','ad_daily_missing':'缺当日广告费'}
                row['gap_notes']='；'.join(translations.get(v,v) for v in row['issues']) or '退款、仓储、SB/SD 及结算费用'
                row['limitations'].append('only_SP_advertising_cost_collected_SB_SD_not_verified')
                profits.append(row)
        # Mapping is separate from performance. A group must identify exactly one SKU.
        mapping_result=verified_product_map(run_dir,context,current_ads,market,reference_time)
        group_skus=mapping_result['group_skus']
        ads_manifest=_read(run_dir/'ads/manifest.json') or {}
        search_info=(ads_manifest.get('files') or {}).get('search_terms') or {}
        search_rows=(verified(run_dir/'ads',search_info) if mapping_result['state']=='complete'
                     and _completed_report_job(ads_manifest,'search_terms',mapping_result['source_context'],bounds,reference_time) else None)
        if isinstance(search_rows,list) and len(search_rows)!=(ads_manifest.get('jobs') or {}).get('search_terms',{}).get('row_count'):
            search_rows=None
        # The view must cite the exact current search-term artifact, and each joined row must exist in it.
        query_files=(queries.get('ad_detail',{}).get('source') or {}).get('files') or []
        search_path=(run_dir/'ads'/str(search_info.get('path') or search_info.get('file') or '')).resolve()
        search_cited=any(isinstance(f,dict) and f.get('sha256')==search_info.get('sha256')
                         and (root/str(f.get('path') or '')).resolve()==search_path for f in query_files)
        raw_search_keys={(str(r.get('campaignId')),str(r.get('adGroupId')),str(r.get('keywordId')),
                          r.get('searchTerm'),r.get('date')) for r in search_rows if isinstance(r,dict)
                         and r.get('date') in expected_days} if isinstance(search_rows,list) and search_cited else set()
        keywords=defaultdict(set)
        for r in queries.get('ad_detail',{}).get('rows',[]):
            if r.get('market')!=market or r.get('stale') or r.get('complete') is False or campaign_source['state']!='complete':
                continue
            if r.get('spend',0) and isinstance(r.get('adOrders'),(int,float)) and r['adOrders']==0:
                observations.append({'market':market,'campaignId':r.get('campaignId'),'name':r.get('name') or r.get('nativeId'),
                    'layer':r.get('layer'),'spend':r['spend'],'clicks':r.get('clicks'),
                    'reason':'期间有花费、暂未归因订单。先检查词意、商品状态与样本，等待近期归因补记。','action':'人工排查，未生成调价'})
            bound=group_skus.get((str(r.get('campaignId')),str(r.get('adGroupId'))),set())
            signature=(str(r.get('campaignId')),str(r.get('adGroupId')),str(r.get('nativeId')),r.get('name'),r.get('date'))
            if r.get('layer')=='search_term' and len(bound)==1 and isinstance(r.get('name'),str) and r['name'].strip() and signature in raw_search_keys:
                asin,sku=next(iter(bound))
                if any(p.get('asin')==asin and p.get('sku')==sku and _fresh_row(p,bounds,reference_time) for p in products):
                    keywords[(asin,sku)].add(r['name'])
        listings=_sp_domain(run_dir,'listings') or {}
        raw_by_sku={r.get('sku'):r for r in listings.get('rows',[])}
        for product in products:
            sku=product['sku'];raw=raw_by_sku.get(sku,{})
            attrs=raw.get('attributes') or {}
            bullets=[r['value'] for r in attrs.get('bullet_point',[]) if isinstance(r,dict) and isinstance(r.get('value'),str)]
            product['bullets']=bullets
            fields={'title':product.get('title') or '',**{f'bullet_{i+1}':v for i,v in enumerate(bullets)}}
            if not rules.get('category_rules_complete'):
                checks.append({'market':market,'sku':sku,'field':'类目规则','term':'待配置','state':'需要人工补充','reason':'还未确认该类目的禁用词、品牌排除与功效证据，不能判定文案合规通过。'})
            for f in check_listing(fields,rules):
                checks.append({'market':market,'sku':sku,'field':f['field'],'term':f['term'],'state':'阻止进入候选' if f['blocked'] else '等待证据复核','reason':f['category']})
            for field,limit in (rules.get('field_limits') or {}).items():
                if field in fields and isinstance(limit,int) and len(fields[field])>limit:
                    checks.append({'market':market,'sku':sku,'field':field,'term':f'{limit} 字符','state':'超过本店约束','reason':f'当前 {len(fields[field])} 字符；这是用户设定的文案要求，需人工改写后再复核。'})
            coverage=keyword_coverage(fields,sorted(keywords[(product.get('asin'),sku)]),rules)
            for word in coverage['missing']:
                checks.append({'market':market,'sku':sku,'field':'标题与五点','term':word,'state':'待核对词意与相关性','reason':'准确关联到单一广告商品的搜索词，当前标题/五点未匹配；不是自动埋词或效果保证。'})
        ready=sum(r['state']=='ready' for r in costs['rows'])
        report += [f'## {market}',f'- 成本维护：{ready}/{len(costs["rows"])} 条 SKU 输入齐全。',
            f'- 库存：已读取 {len(products)} 条商品，{sum(r.get("available")==0 for r in products)} 条 FBA 可售为 0。',
            f'- 商品问题：{sum((r.get("issuesCount") or 0)>0 for r in products)} 条 SKU 带接口问题，需在商品明细核对。',
            '- 广告与文案建议需人工审核；缺成本、税口径或归因成熟度时不生成默认调价。','']
    queries['costs']=_query('已维护的成本',costs_rows,evidence,'原表只读导入，逐行保留来源与 SHA；计算由输入重算，不用缓存毛利。')
    queries['profit_daily']=_query('每日收益估算',profits,evidence,'实际SKU日销量×成本参数，店铺广告费只扣一次；需逐SKU销售与全店销售核对，缺项不显示全店净利润。')
    queries['listing_checks']=_query('商品文案与词覆盖检查',checks,[],'按已确认规则检查；只有单一SKU广告组映射可生成词覆盖线索；需要人工复核。')
    queries['ad_observations']=_query('广告排查线索',sorted(observations,key=lambda r:r['spend'],reverse=True),[],'零归因订单是期间观察，不是自动降价/否词结论；不同层级不可累加。')
    (root/'daily_report.md').write_text('\n'.join(report),encoding='utf-8')
    return snapshot
