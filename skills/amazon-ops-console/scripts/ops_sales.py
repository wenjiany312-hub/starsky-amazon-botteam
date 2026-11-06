"""SKU sales drill-down without customer names, addresses or order-level PII."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import json
import math
import time
from zoneinfo import ZoneInfo

from ops_collect import (_read, _domain_view, _sp_scope, load_sp_config, SpApiClient,
                         write_json, sha, _safe_error)


def _stamp(value):
    try:
        stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
        return stamp if stamp.tzinfo is not None else None
    except (AttributeError,ValueError,TypeError): return None


def _bounds(period):
    if not isinstance(period,dict): return None
    start,end=_stamp(period.get('start')),_stamp(period.get('end'))
    return (start,end) if start and end and start<end else None


def _folder(root,market):
    paths=sorted(p for p in (root/'runs').glob('*_'+market) if len(p.name)==11 and p.name[:8].isdigit())
    return paths[-1] if paths else None


def collect_today_skus(config):
    root=Path(config['workspace']);results=[]
    for market_cfg in config['markets']:
        market=market_cfg['market'];run=_folder(root,market)
        if not run: continue
        sp_config=load_sp_config(market_cfg['sp_config'])
        scope=_sp_scope({**sp_config,'store':config['store'],'market':market})
        binding=_read(run/'source_context.json',{}) or {}
        if any(binding.get(k)!=v for k,v in scope.items()) or not scope.get('seller_id'): continue
        today=_domain_view(run,'today_intraday',expected_scope=scope,allow_stale=False)
        listings=_domain_view(run,'listings',expected_scope=scope,allow_stale=False)
        if not today or not listings or today['status'].get('state')!='complete' or listings['status'].get('state')!='complete': continue
        period=today['status'].get('period') or {}
        if not isinstance(period,dict) or not period.get('start') or not period.get('end'): continue
        bounds=_bounds(period);now=datetime.now(timezone.utc);zone=ZoneInfo(market_cfg['timezone'])
        if (not bounds or bounds[0].astimezone(zone).date()!=now.astimezone(zone).date()
                or bounds[0].astimezone(zone).time()!=datetime.min.time() or bounds[1]>now
                or _stamp(today['status'].get('checkedAt')) is None): continue
        base=run/'sp/sku_today';manifest=_read(base/'manifest.json',{}) or {}
        if manifest.get('scope')!=scope or manifest.get('period')!=period:
            manifest={'scope':scope,'period':period,'files':{},'errors':{},'state':'pending'}
        client=SpApiClient(sp_config)
        skus=sorted({r['sku'] for r in listings['value'].get('rows',[]) if isinstance(r.get('sku'),str)})
        for i,sku in enumerate(skus):
            info=manifest['files'].get(sku) or {};path=(base/info.get('file','')).resolve()
            if path.is_relative_to(base.resolve()) and path.is_file() and sha(path.read_bytes())==info.get('sha256'): continue
            if i: time.sleep(2.05)
            try:
                value=client.sales(period['start'],period['end'],market_cfg['timezone'],sku=sku)
                raw=json.dumps({'sku':sku,'result':value},ensure_ascii=False,sort_keys=True,allow_nan=False).encode('utf8')
                name=sha(raw)+'.json';path=base/name
                base.mkdir(parents=True,exist_ok=True)
                if not path.exists(): path.write_bytes(raw)
                manifest['files'][sku]={'file':name,'sha256':sha(path.read_bytes())};manifest['errors'].pop(sku,None)
            except Exception as exc: manifest['errors'][sku]=_safe_error(exc)
            manifest['collected_at']=datetime.now(timezone.utc).isoformat();write_json(base/'manifest.json',manifest)
        manifest['expected_skus']=skus
        manifest['state']='complete' if set(manifest['files'])==set(skus) and not manifest['errors'] else 'partial'
        write_json(base/'manifest.json',manifest);results.append({'market':market,'state':manifest['state'],'skus':len(manifest['files'])})
    return results


def sales_rows(base,manifest,scope,products,intraday,time_zone=None):
    if manifest.get('scope') not in (scope,{k:scope[k] for k in ('marketplace_id','seller_id')}): return []
    bounds=_bounds(manifest.get('period'));stamp=_stamp(manifest.get('collected_at'))
    if not bounds or not stamp or stamp<bounds[1]: return []
    zone=ZoneInfo(time_zone or {'US':'America/Los_Angeles','UK':'Europe/London'}[scope['market']])
    by_sku={}
    for product in products:
        if product.get('market')==scope['market']:
            sku=product.get('sku')
            if sku in by_sku and by_sku[sku].get('asin')!=product.get('asin'): by_sku[sku]={}
            else: by_sku[sku]=product
    rows=[]
    for sku,info in manifest.get('files',{}).items():
        path=(base/info.get('file','')).resolve()
        if not path.is_relative_to(base.resolve()) or not path.is_file() or sha(path.read_bytes())!=info.get('sha256'): continue
        raw=_read(path,{}) or {}
        if raw.get('sku')!=sku: continue
        product=by_sku.get(sku,{})
        result=raw.get('result') or {};payload=result.get('payload')
        if not isinstance(payload,list): continue
        intervals=[];invalid=False
        for entry in payload:
            parts=str(entry.get('interval','')).split('--') if isinstance(entry,dict) else []
            first,last=(_stamp(v) for v in parts) if len(parts)==2 else (None,None)
            if not first or not last or not bounds[0]<=first<last: invalid=True;break
            local_first,local_last=first.astimezone(zone),last.astimezone(zone)
            bucket_end=datetime.combine(local_first.date()+timedelta(days=1),datetime.min.time(),zone)
            # Day labels cover the full day even when the requested tail is partial.
            # Coverage below uses the request cutoff, never the future bucket end.
            valid_intraday=(first==bounds[0] and bounds[1]<=bucket_end
                and last in (bounds[1],bucket_end))
            if (local_first.time()!=datetime.min.time()
                    or (not intraday and (last>bounds[1] or local_last.time()!=datetime.min.time() or (local_last.date()-local_first.date()).days!=1))
                    or (intraday and not valid_intraday)): invalid=True;break
            intervals.append((first,min(last,bounds[1])))
        ordered=sorted(intervals)
        if invalid or len(set(intervals))!=len(intervals) or any(a[1]>b[0] for a,b in zip(ordered,ordered[1:])): continue
        complete=bool(ordered and ordered[0][0]==bounds[0] and ordered[-1][1]==bounds[1]
            and all(a[1]==b[0] for a,b in zip(ordered,ordered[1:]))
            and not result.get('errors') and result.get('complete') is not False
            and not manifest.get('errors') and manifest.get('state')=='complete')
        for entry in payload:
            amount=entry.get('totalSales') or {};interval=str(entry.get('interval',''))
            try: sales=float(amount['amount']) if amount.get('amount') is not None else None
            except (ValueError,TypeError): sales=None
            if sales is not None and not math.isfinite(sales): sales=None
            rows.append({'market':scope['market'],'sku':sku,'asin':product.get('asin'),'title':product.get('title'),
                'date':_stamp(interval.split('--')[0]).astimezone(zone).date().isoformat(),'interval':interval,'isIntraday':intraday,'currency':amount.get('currencyCode'),
                'units':entry.get('unitCount'),'sku_orders':entry.get('orderCount'),'order_items':entry.get('orderItemCount'),
                'sales':sales,'dataCollectedAt':manifest.get('collected_at'),'evidence_sha256':info['sha256'],
                'evidence_file':str(path),'complete':complete,'period':manifest['period'],
                'asOf':manifest['period']['end'], 'dayComplete':not intraday,
                'completenessBasis':'requested_interval_only' if intraday else 'closed_day_interval',
                'order_note':'SKU涉及订单数不可相加当成全店独立订单数'})
    return rows


def enrich_sales(config,snapshot):
    root=Path(config['workspace']);products=snapshot['queries']['products']['rows'];rows=[];coverage=[]
    for item in config['markets']:
        market=item['market'];run=_folder(root,market)
        if not run: continue
        cfg=load_sp_config(item['sp_config']);scope=_sp_scope({**cfg,'store':config['store'],'market':market})
        bound=_read(run/'source_context.json',{}) or {}
        if any(bound.get(k)!=v for k,v in scope.items()): continue
        for intraday,domain in ((False,'sku_sales'),(True,'sku_today')):
            base=run/'sp'/domain;manifest=_read(base/'manifest.json',{}) or {}
            bounds=_bounds(manifest.get('period'));reference=_stamp(snapshot.get('generatedAt'));stamp=_stamp(manifest.get('collected_at'))
            if not bounds or not reference or not stamp or not bounds[1]<=stamp<=reference: continue
            zone=ZoneInfo(item['timezone']);start,end=bounds
            if intraday:
                current=_domain_view(run,'today_intraday',expected_scope=scope,allow_stale=False)
                if (not current or manifest.get('period')!=current['status'].get('period')
                        or start.astimezone(zone).date()!=reference.astimezone(zone).date()
                        or start.astimezone(zone).time()!=datetime.min.time()): continue
            else:
                period=bound.get('period') or {}
                if (start.astimezone(zone).date().isoformat()!=period.get('startDate')
                        or (end.astimezone(zone).date()-timedelta(days=1)).isoformat()!=period.get('endDate')
                        or end.astimezone(zone).date()!=reference.astimezone(zone).date()
                        or start.astimezone(zone).time()!=datetime.min.time() or end.astimezone(zone).time()!=datetime.min.time()): continue
            new=sales_rows(base,manifest,scope,products,intraday,item['timezone']);rows.extend(new)
            for day in sorted({r['date'] for r in new}):
                day_rows=[r for r in new if r['date']==day]
                store=[r for r in snapshot['queries']['sales_daily']['rows'] if r['market']==market and r.get('isIntraday')==intraday and r['date']==day]
                known=all(isinstance(r.get('sales'),(int,float)) and r.get('complete') for r in day_rows)
                reconciled=bool(known and len(store)==1 and isinstance(store[0].get('sales'),(int,float))
                    and store[0].get('complete') is True and not store[0].get('stale')
                    and all(r.get('currency')==store[0].get('currency') and r.get('currency') for r in day_rows)
                    and (not intraday or _stamp(store[0].get('sourceCheckedAt'))==_stamp(current['status'].get('checkedAt')))
                    and abs(sum(r['sales'] for r in day_rows)-store[0]['sales'])<=.02)
                coverage.append({'market':market,'date':day,'isIntraday':intraday,'state':'reconciled' if reconciled else 'partial',
                    'note':'SKU销售额已与全店同日销售核对' if reconciled else 'SKU覆盖或采集时点尚未与全店金额对齐',
                    'sold_skus':sum(isinstance(r.get('units'),(int,float)) and r['units']>0 for r in day_rows),
                    'dataCollectedAt':manifest.get('collected_at')})
    for key,label,data in [('sku_sales_daily','按日期查看出单商品',rows),('sku_sales_coverage','出单商品覆盖核对',coverage)]:
        snapshot['queries'][key]={'rows':data,'source':{'label':label,'provider':'Amazon Sales API by SKU',
            'evidenceFlow':[{'title':'单位区分','detail':'件数来自unitCount；SKU订单数不能相加为全店独立订单数。无客户姓名/地址。'}]}}
    return snapshot
