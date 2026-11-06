"""Short-history stock planning reminders only; no purchase order or account writes."""
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
import math
from zoneinfo import ZoneInfo


def _number(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>=0


def enrich_restock(config,snapshot):
    settings=config.get('replenishment') or {}
    lead=settings.get('lead_days',60);safety=settings.get('safety_days',15);review=settings.get('review_days',7)
    cadence_valid=all(isinstance(v,int) and not isinstance(v,bool) and 0<=v<=3650 for v in (lead,safety,review)) and lead>0 and review>0
    queries=snapshot.setdefault('queries',{})
    source_rows=queries.get('sku_sales_daily',{}).get('rows',[])
    products=queries.get('products',{}).get('rows',[])
    zones={m.get('market'):m.get('timezone') for m in config.get('markets',[])}
    grouped=defaultdict(list);market_dates=defaultdict(set)
    for row in source_rows:
        if row.get('isIntraday'): continue
        try: day=date.fromisoformat(row['date'])
        except (ValueError,TypeError,KeyError): continue
        grouped[(row.get('market'),row.get('sku'))].append((day,row))
        market_dates[row.get('market')].add(day)
    duplicate_products=Counter((p.get('market'),p.get('sku')) for p in products)
    result=[]
    for product in products:
        market,sku=product.get('market'),product.get('sku');warnings=[]
        try:
            stamp=datetime.fromisoformat(snapshot['generatedAt'].replace('Z','+00:00'))
            if stamp.tzinfo is None: raise ValueError()
            current_day=stamp.astimezone(ZoneInfo(zones[market])).date()
            last_day=current_day-timedelta(days=1)
        except (ValueError,TypeError,KeyError,AttributeError):
            current_day=last_day=None;warnings.append('snapshot_time_or_market_timezone_missing')
        dates={d for d in market_dates[market] if last_day and d<=last_day}
        first_day=min(dates) if dates else None
        expected_days=(last_day-first_day).days+1 if first_day else 0
        by_date=defaultdict(list)
        for day,row in grouped[(market,sku)]:
            if first_day and first_day<=day<=last_day: by_date[day].append(row)
        valid=[]
        for day,rows in by_date.items():
            if len(rows)!=1: continue
            row=rows[0]
            if (row.get('complete') is True and not row.get('stale') and _number(row.get('units'))
                    and float(row['units']).is_integer()
                    and (not row.get('asin') or not product.get('asin') or row['asin']==product['asin'])):
                valid.append(row)
        coverage=len(valid)/expected_days if expected_days else None
        average=sum(r['units'] for r in valid)/len(valid) if valid and len(valid)==expected_days else None
        if coverage!=1: warnings.append('complete_daily_sales_coverage_missing')
        if expected_days<28: warnings.append('short_history_not_long_term_forecast')
        available=product.get('available')
        if product.get('complete') is not True or product.get('stale') or not _number(available):
            warnings.append('available_inventory_not_current_complete')
        if duplicate_products[(market,sku)]!=1 or not sku: warnings.append('product_identity_ambiguous')
        if not cadence_valid: warnings.append('replenishment_settings_invalid')
        if average==0: warnings.append('zero_sales_does_not_establish_no_demand')
        blocked=any(w!='short_history_not_long_term_forecast' for w in warnings)
        row={'market':market,'sku':sku,'asin':product.get('asin'),'state':'insufficient',
             'sales_window_start':first_day.isoformat() if first_day else None,
             'sales_window_end':last_day.isoformat() if last_day else None,
             'sales_days_expected':expected_days,'sales_days_complete':len(valid),'sales_coverage':coverage,
             'daily_average_units':average,'available':available,'lead_days':lead,'safety_days':safety,'review_days':review,
             'alert_threshold_days':lead+safety if cadence_valid else None,'replenishment_alert':None,
             'inventory_cover_days':None,'expected_stockout_date':None,'latest_start_date':None,
             'reference_qty_before_inbound':None,'reference_qty_label':'未扣在途的备货参考量',
             'confirmed_inbound_qty':None,'confirmed_inbound_eta':None,'actual_reorder_qty':None,
             'purchase_order_created':False,'warnings':warnings,
             'basis_note':'按近期完整日销量粗估，仅供备货排期；零销量不证明无需求。在途数量与到货日未确认，不并入可售库存，不生成采购单或最终补货量。'}
        if not blocked and average and current_day:
            cover=available/average
            # Dates are planning estimates, not promised depletion or arrival dates.
            stockout=current_day+timedelta(days=math.floor(cover))
            row.update(state='planning_estimate',inventory_cover_days=round(cover,2),
                       replenishment_alert=cover<=lead+safety,expected_stockout_date=stockout.isoformat(),
                       latest_start_date=(stockout-timedelta(days=lead+safety)).isoformat(),
                       reference_qty_before_inbound=max(0,math.ceil(average*(lead+safety+review)-available)),
                       latest_start_date_basis='预计缺货日减交期与安全天数，保留安全库存缓冲')
        result.append(row)
    evidence=[]
    for name in ('sku_sales_daily','products'):
        evidence.extend((queries.get(name,{}).get('source') or {}).get('files') or [])
    queries['replenishment']={'rows':result,'source':{'label':'补货排期参考','provider':'Local deterministic planning',
        'files':evidence,'metricDefinitions':[{'label':'补货提醒线','definition':'交期加安全天数；默认60+15天，可按用户配置调整'},
                                             {'label':'备货参考量','definition':'日均销量乘交期、安全与复核天数，减可售库存；未扣在途，非最终采购量'}]}}
    return snapshot
