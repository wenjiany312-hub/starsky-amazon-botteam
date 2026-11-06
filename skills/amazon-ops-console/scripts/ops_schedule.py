"""Marketplace day boundaries, independent of Beijing's calendar and DST."""
from datetime import datetime,timedelta,timezone
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo
from ops_collect import _read,write_json,_domain_view,_sp_scope,_verified,load_sp_config,load_ads_config

def cadence_rows(config,now=None):
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None: raise ValueError('timezone_required')
    rows=[]
    for item in config['markets']:
        local=now.astimezone(ZoneInfo(item['timezone']))
        midnight=local.replace(hour=0,minute=0,second=0,microsecond=0)
        end=midnight.astimezone(ZoneInfo('Asia/Shanghai'))
        rows.append({'market':item['market'],'timezone':item['timezone'],'last_closed_date':(midnight-timedelta(days=1)).date().isoformat(),
            'last_day_closed_at_beijing':end.isoformat(),'next_close_at_beijing':(midnight+timedelta(days=1)).astimezone(ZoneInfo('Asia/Shanghai')).isoformat(),
            'display':f'北京时间 {end:%H:%M} 跨日采集 · 09:00 汇总',
            'note':'时区按IANA自动切换夏令时；当地一天结束不等于广告归因已成熟。'})
    return rows

def due_markets(config,now=None):
    now=now or datetime.now(timezone.utc);root=Path(config['workspace'])
    status=_read(root/'schedule_state.json',{}) or {};due=[]
    for row in cadence_rows(config,now):
        last=(status.get('markets') or {}).get(row['market'],{})
        if last.get('closed_date')!=row['last_closed_date'] or last.get('state')!='collected':
            due.append(row['market'])
    return due

def record_collection(config,markets,now=None):
    root=Path(config['workspace']);state=_read(root/'schedule_state.json',{}) or {'markets':{}}
    for row in cadence_rows(config,now):
        if row['market'] not in markets: continue
        run=root/'runs'/(row['last_closed_date'].replace('-','')+'_'+row['market'])
        ads=_read(run/'ads/manifest.json',{}) or {}
        ready=False
        try:
            from ops_enrich import verified_campaign_report
            item=next(m for m in config['markets'] if m['market']==row['market'])
            sp=load_sp_config(item['sp_config']);adcfg=load_ads_config(item['ads_config']);bound=_read(run/'source_context.json',{}) or {}
            expected={'store':config['store'],'market':row['market'],'marketplace_id':sp['marketplace_id'],'seller_id':sp['seller_id']}
            stamp=(now or datetime.now(timezone.utc)).isoformat()
            ready=(_sp_scope(bound)==expected and verified_campaign_report(run,bound,adcfg,row['market'],stamp)['state']=='complete'
                and all(_domain_view(run,k,expected_scope=expected,allow_stale=False) is not None for k in ('sales','inventory','listings'))
                and all(_verified(run/'ads',ads,k) is not None and (ads.get('jobs',{}).get(k) or {}).get('status')=='COMPLETED'
                    for k in ('campaign_adgroups','keywords','targets','search_terms')))
        except (KeyError,ValueError,StopIteration,OSError):
            ready=False
        state.setdefault('markets',{})[row['market']]={'closed_date':row['last_closed_date'],'state':'collected' if ready else 'pending_sources',
            'attempted_at':(now or datetime.now(timezone.utc)).isoformat()}
    write_json(root/'schedule_state.json',state)
    return state

def enrich_cadence(config,snapshot):
    snapshot['queries']['collection_cadence']={'rows':cadence_rows(config),'source':{'label':'站点跨日采集安排','provider':'IANA timezone calendar',
        'metricDefinitions':[{'label':'日界','definition':'US America/Los_Angeles；UK Europe/London，转换至Asia/Shanghai。'}]}}
    return snapshot
