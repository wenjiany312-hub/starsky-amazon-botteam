"""Optional local costs. Read workbooks only; recompute inputs, never cached totals.

load_costs binds one supplied workbook/store/market. estimate_daily consumes
already aggregated SKU/day sales and one store/day advertising total.
"""
import csv
from collections import Counter, defaultdict
from datetime import date
import hashlib
import io
import math
from pathlib import Path
import re

MARKETS={'US':'USD','UK':'GBP'}
INPUTS=('purchase_cny','freight_cny','fba_fee','commission_rate','fx_cny_per_currency','vat_rate','digital_service_fee_rate')
HEADERS={'店铺':'source_store_label','SKU':'sku','ASIN':'asin','售价(站点币)':'listed_price',
         '采购成本(元)':'purchase_cny','头程(元/件)':'freight_cny','FBA配送费(站点币)':'fba_fee',
         '其他费用(站点币)':'other_fee','佣金率':'commission_rate','目标利润率':'target_margin','姿态':'posture'}
LIMITATIONS=['estimate_not_settlement','refunds_storage_taxes_and_other_unprovided_fees_not_reconciled',
             'current_cost_assumptions_applied_to_reported_day','uk_sales_tax_and_commission_basis_must_be_verified']


def _market(value):
    value=str(value or '').strip().upper()
    return 'UK' if value=='GB' else value


def _number(value):
    if isinstance(value,bool) or value is None or value=='': return None
    try:
        number=float(value)
        return number if math.isfinite(number) and number>=0 else None
    except (ValueError,TypeError): return None


def _round(value):
    return round(value,8) if value is not None else None


def _components(row,sales,units,tax_basis='gross_including_vat',commission_base_sales=None):
    landed=(row['purchase_cny']+row['freight_cny'])/row['fx_cny_per_currency']*units
    fba=row['fba_fee']*units
    commission=(sales if commission_base_sales is None else commission_base_sales)*row['commission_rate']
    vat=0 if tax_basis=='net_excluding_vat' else sales*row['vat_rate']/(1+row['vat_rate'])
    service=(commission+fba)*row['digital_service_fee_rate']
    other=row['other_fee']*units
    return {'landed_cost':landed,'fba_fee':fba,'commission':commission,'vat':vat,
            'digital_service_fee':service,'other_fee':other,'contribution':sales-landed-fba-commission-vat-service-other}


def _normalize(raw,store,market,currency):
    row={k:raw.get(k) for k in ('sku','asin','source_store_label','source_row','posture')}
    row.update(store=store,market=market,currency=currency,warnings=[],missing_fields=[])
    for key in (*INPUTS,'other_fee','target_margin','listed_price','posture_adjustment','target_acos_min','target_acos_max'):
        row[key]=_number(raw.get(key))
    # Posture adjustments may legitimately be negative.
    try:
        adjustment=float(raw.get('posture_adjustment'))
        row['posture_adjustment']=adjustment if math.isfinite(adjustment) else None
    except (TypeError,ValueError): row['posture_adjustment']=None
    if raw.get('other_fee') in (None,''):
        row['other_fee']=0
        row['warnings'].append('other_fee_blank_assumed_zero')
    row['missing_fields']=[k for k in (*INPUTS,'other_fee') if row[k] is None]
    if row['fx_cny_per_currency']==0: row['missing_fields'].append('fx_cny_per_currency_positive')
    for key in ('commission_rate','vat_rate','digital_service_fee_rate'):
        if row[key] is not None and row[key]>1: row['missing_fields'].append(key+'_out_of_range')
    if not isinstance(row['sku'],str) or not row['sku'].strip(): row['missing_fields'].append('sku')
    row['warnings'].extend(raw.get('_warnings',[]))
    if raw.get('_blocked'): row['missing_fields'].append('source_model_unverified')
    row['state']='needs_data' if row['missing_fields'] else 'ready'
    row.update(landed_unit_cost=None,unit_gross_profit=None,break_even_acos=None,target_acos=None)
    if row['state']=='ready':
        row['landed_unit_cost']=_round((row['purchase_cny']+row['freight_cny'])/row['fx_cny_per_currency'])
        if row['listed_price'] is not None and row['listed_price']>0:
            row['unit_gross_profit']=_round(_components(row,row['listed_price'],1)['contribution'])
            row['break_even_acos']=_round(row['unit_gross_profit']/row['listed_price'])
            target_keys=('target_margin','posture_adjustment','target_acos_min','target_acos_max')
            if all(row[k] is not None for k in target_keys) and 0<=row['target_acos_min']<=row['target_acos_max']<=1 and row['target_margin']<=1:
                row['target_acos']=_round(max(row['target_acos_min'],min(row['target_acos_max'],row['break_even_acos']-row['target_margin']+row['posture_adjustment'])))
    if row['target_acos'] is None: row['warnings'].append('target_acos_unavailable_missing_price_or_target_inputs')
    return row


def _xlsx(data,market):
    from openpyxl import load_workbook
    workbook=load_workbook(io.BytesIO(data),read_only=True,data_only=False,keep_links=False)
    try:
        sheet=workbook['成本表'];parameter_rows=list(workbook['参数'].values)
        params={r[0]:r[1] for r in parameter_rows[1:] if len(r)>1 and r[0]}
        if len(params)!=sum(bool(r[0]) for r in parameter_rows[1:]): raise ValueError('duplicate_parameters')
        fx=[(k,v) for k,v in params.items() if str(k).startswith('汇率')]
        if len(fx)!=1: raise ValueError('fx_parameter_missing')
        currency=re.search(r'1\s+(USD|GBP)\s*=',fx[0][0])
        if not currency or currency[1]!=MARKETS[market]: raise ValueError('market_currency_mismatch')
        postures={r[3]:r[4] for r in parameter_rows[1:] if len(r)>4 and r[3]}
        common={'fx_cny_per_currency':fx[0][1],'target_acos_min':params.get('目标ACOS下限'),
                'target_acos_max':params.get('目标ACOS上限'),'vat_rate':0,'digital_service_fee_rate':0}
        if market=='UK':
            common.update(vat_rate=params.get('VAT率（含税售价里要交出去的税）'),
                          digital_service_fee_rate=params.get('数字服务费率（按 佣金+FBA 计）'))
        iterator=sheet.iter_rows(values_only=True)
        headers=next(iterator)
        if any(h not in headers for h in HEADERS): raise ValueError('cost_headers_missing')
        if len(set(headers))!=len(headers): raise ValueError('duplicate_headers')
        rows=[]
        for line,values in enumerate(iterator,2):
            if not any(v is not None for v in values[:16]): continue
            source=dict(zip(headers,values));raw={key:source.get(header) for header,key in HEADERS.items()}
            raw.update(common,source_row=line,posture_adjustment=postures.get(raw.get('posture')))
            # Verify the financial formula model; do not evaluate Excel or use cached outputs.
            expected=f'=IF(Q{line}="","",H{line}-Q{line}-K{line}-L{line}-H{line}*M{line}'
            if market=='UK': expected+=f'-(H{line}*M{line}+K{line})*参数!$B$8-H{line}*参数!$B$7/(1+参数!$B$7)'
            expected+=')'
            if len(values)<18 or values[17]!=expected:
                raw['_blocked']=True;raw['_warnings']=['gross_profit_formula_model_mismatch']
            rows.append(raw)
        labels={r['source_store_label'] for r in rows if r.get('source_store_label')}
        if len(labels)!=1 or any(not r.get('source_store_label') for r in rows): raise ValueError('ambiguous_source_store')
        return rows,{'raw':params,'posture_adjustments':postures,'model':'supplied_uk_vat_service_fee' if market=='UK' else 'supplied_us_no_vat_line'},currency[1]
    finally: workbook.close()


def load_costs(path,store,market):
    """Optional source import. Invalid sources return gaps, never stop other dashboard domains.

    A single-label XLSX is explicitly bound by caller store; CSV includes exact
    store/market per row. Source labels and row positions remain traceable.
    """
    market=_market(market)
    result={'rows':[],'sha256':None,'warnings':[],'store':store,'market':market,
            'currency':MARKETS.get(market),'parameters':{},'state':'not_configured'}
    if not path: return result
    path=Path(path)
    if not path.is_file(): result['warnings'].append('cost_source_missing');return result
    try:
        if not isinstance(store,str) or not store.strip() or market not in MARKETS: raise ValueError('invalid_store_market')
        if path.stat().st_size>20*1024*1024: raise ValueError('cost_source_too_large')
        data=path.read_bytes();result['sha256']=hashlib.sha256(data).hexdigest()
        if path.suffix.lower()=='.xlsx':
            raw,params,currency=_xlsx(data,market)
            result['parameters']=params
            result['warnings'].append('source_store_label_explicitly_bound_by_caller')
        elif path.suffix.lower()=='.csv':
            raw=list(csv.DictReader(io.StringIO(data.decode('utf-8-sig'))));currency=MARKETS[market]
            if any(r.get('store')!=store or _market(r.get('market'))!=market or r.get('currency')!=currency for r in raw): raise ValueError('csv_scope_mismatch')
            for n,r in enumerate(raw,2): r.update(source_row=n,source_store_label=r.get('store'))
            result['parameters']={'model':'explicit_csv_row_parameters'}
        else: raise ValueError('unsupported_cost_format')
        rows=[_normalize(r,store,market,currency) for r in raw]
        counts=Counter(r['sku'] for r in rows)
        for row in rows:
            if counts[row['sku']]>1:
                row['state']='needs_data';row['missing_fields'].append('duplicate_store_market_sku')
                for key in ('landed_unit_cost','unit_gross_profit','break_even_acos','target_acos'): row[key]=None
            row['source_sha256']=result['sha256']
        result.update(rows=rows,currency=currency,state='ready' if rows and all(r['state']=='ready' for r in rows) else 'partial' if rows else 'empty')
        result['input_summary']={'rows':len(rows),'cost_ready_rows':sum(r['state']=='ready' for r in rows),
                                 'missing_fields':dict(Counter(k for r in rows for k in r['missing_fields']))}
    except Exception as error:
        # Source errors may include SKU values in third-party exceptions; emit category only.
        safe={'duplicate_parameters','fx_parameter_missing','market_currency_mismatch','cost_headers_missing','duplicate_headers',
              'ambiguous_source_store','invalid_store_market','cost_source_too_large','csv_scope_mismatch','unsupported_cost_format'}
        code=str(error) if type(error) is ValueError and str(error) in safe else 'cost_source_invalid'
        result.update(state='invalid',rows=[],warnings=[code])
    return result


def estimate_daily(costs,sku_sales,ad_daily):
    """Return per-day rows; input lists must be explicit SKU/day and store/day totals.

    Missing/partial cost coverage => no whole-store profit. Optional store on
    input rows, when absent, is bound to costs.store by the caller.
    """
    if not isinstance(sku_sales,list) or not isinstance(ad_daily,list): raise ValueError('daily_rows_required')
    store,market,currency=costs.get('store'),costs.get('market'),costs.get('currency')
    by_day=defaultdict(lambda:{'sales':[],'ads':[]})
    for kind,rows in (('sales',sku_sales),('ads',ad_daily)):
        for row in rows:
            if not isinstance(row,dict): raise ValueError('daily_row_invalid')
            try: day=date.fromisoformat(row['date']).isoformat()
            except (KeyError,TypeError,ValueError): raise ValueError('daily_date_invalid') from None
            by_day[day][kind].append(row)
    index=defaultdict(list)
    for row in costs.get('rows',[]): index[(row.get('store'),row.get('market'),row.get('sku'))].append(row)
    output=[]
    for day,inputs in sorted(by_day.items()):
        issues=[];details=[]
        sku_counts=Counter(r.get('sku') for r in inputs['sales'])
        for row in inputs['sales']:
            sku=row.get('sku');sales,units=_number(row.get('sales')),_number(row.get('units'))
            zero_activity=sales==0 and units==0
            tax_basis=row.get('sales_tax_basis') or 'unverified'
            commission_base_sales=None
            missing=[]
            if _market(row.get('market'))!=market or row.get('currency')!=currency or row.get('store',store)!=store: missing.append('sales_scope_mismatch')
            if row.get('complete') is False: missing.append('sales_incomplete')
            if not isinstance(sku,str) or not sku: missing.append('sku_missing')
            if sku_counts[sku]>1: missing.append('duplicate_sku_day')
            if sales is None or units is None or units!=int(units or 0): missing.append('sales_or_units_missing_invalid')
            if units==0 and sales not in (0,None): missing.append('revenue_without_units')
            candidates=index[(store,market,sku)]
            cost=candidates[0] if len(candidates)==1 else None
            if not zero_activity and (not cost or cost.get('state')!='ready'): missing.append('sku_cost_missing_or_incomplete')
            if market=='UK' and not zero_activity:
                if tax_basis not in ('gross_including_vat','net_excluding_vat'): missing.append('vat_basis_unverified')
                elif tax_basis=='net_excluding_vat':
                    commission_base_sales=_number(row.get('commission_base_sales'))
                    if commission_base_sales is None: missing.append('commission_basis_unverified')
            detail={'sku':sku,'sales':sales,'units':units,'contribution':None,'missing_fields':missing,
                    'zero_activity':zero_activity,'sales_tax_basis':tax_basis if market=='UK' else 'supplied_us_model',
                    'commission_base_sales':commission_base_sales}
            if not missing and zero_activity:
                detail.update({k:0 for k in ('landed_cost','fba_fee','commission','vat','digital_service_fee','other_fee','contribution')})
                detail['warnings']=['zero_activity_costs_deterministically_zero']
            elif not missing:
                basis=tax_basis if market=='UK' else 'gross_including_vat'
                detail.update({k:_round(v) for k,v in _components(cost,sales,units,basis,commission_base_sales).items()})
                detail['warnings']=cost.get('warnings',[])
            details.append(detail);issues.extend(missing)
        ads=inputs['ads'];ad_spend=None
        if len(ads)!=1: issues.append('ad_daily_missing' if not ads else 'duplicate_ad_daily')
        else:
            ad=ads[0]
            if (_market(ad.get('market'))!=market or ad.get('currency')!=currency or ad.get('store',store)!=store
                    or ad.get('layer') not in (None,'store','account') or ad.get('complete') is False): issues.append('ad_scope_or_completeness_mismatch')
            else: ad_spend=_number(ad.get('spend'))
            if ad_spend is None: issues.append('ad_spend_missing_invalid')
        if not details: issues.append('daily_sales_missing')
        revenue_valid=bool(details) and all(d['sales'] is not None for d in details) and not any(k in issues for k in ('duplicate_sku_day','sales_scope_mismatch','sales_incomplete'))
        sales=sum(d['sales'] for d in details) if revenue_valid else None
        covered=[d for d in details if d['contribution'] is not None]
        contribution=sum(d['contribution'] for d in covered) if covered else None
        full=bool(details) and len(covered)==len(details) and ad_spend is not None and not issues
        output.append({'store':store,'market':market,'currency':currency,'date':day,'sales':_round(sales),
                       'covered_contribution':_round(contribution),'ad_spend':ad_spend,
                       'estimated_profit':_round(contribution-ad_spend) if full else None,
                       'cost_coverage_sales':sum(d['sales'] for d in covered)/sales if sales and revenue_valid else (1.0 if full and sales==0 else None),
                       'cost_coverage_sku_rows':len(covered)/len(details) if details else None,
                       'state':'estimate' if full else 'partial','issues':sorted(set(issues)),
                       'sku_rows':details,'cost_source_sha256':costs.get('sha256'),'settlement_profit':False,'limitations':list(LIMITATIONS)})
    return output
