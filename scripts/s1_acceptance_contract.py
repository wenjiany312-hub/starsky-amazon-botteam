"""Shared coverage floors and evidence-checked unavailable-field semantics."""
import hashlib
import json
from pathlib import Path
import re
import yaml

FX_CONTEXT_RE=re.compile(r'汇率|换汇|USD\s*/?\s*CNY|\bCNY\b|fx_rate_snapshot|effective_purchase_rate',re.I)
CAPABILITY_TOOLS={'peak_top100':'product_search_from_history','organic_top5':'keyword_search_results','ad_keyword_count':'product_traffic_terms'}
FIELDS={'peak_top100':{'asin'},'organic_top5':{'position','rank','organic_position','organic_rank'},
        'ad_keyword_count':{'exposure_position','latest_ad_position','latest_ad_exposure_time','ad_position','is_ad'}}


def floors(mode='full_delivery'):
    config=yaml.safe_load((Path(__file__).resolve().parents[1]/'templates/s1_acceptance_floors.yaml').read_bytes())
    values=config.get(mode,config['full_delivery'])
    if not values or any(type(v) is not int or v<1 for v in values.values()):raise ValueError('S1验收下限必须为正整数')
    return dict(values)


def tool_name(name):return str(name).split('__')[-1]


def decode(value):
    if isinstance(value,dict):
        if 'response' in value:return decode(value['response'])
        if isinstance(value.get('structuredContent'),(dict,list)):return decode(value['structuredContent'])
        if 'data' in value:return decode(value['data'])
        if 'result' in value:return decode(value['result'])
        for block in value.get('content') or []:
            if isinstance(block,dict) and block.get('type')=='text':
                try:return decode(json.loads(block['text']))
                except (ValueError,KeyError,TypeError):pass
    return value


def pointer(value,selector):
    for part in selector.lstrip('/').split('/') if selector else []:
        part=part.replace('~1','/').replace('~0','~')
        value=value[int(part)] if isinstance(value,list) else value[part]
    return value


def unavailable(root,key,tool,reason,source,kind,selector=None):
    relative=Path(source).as_posix();path=Path(root)/relative
    evidence={'kind':kind,'tool':tool,'source':relative,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    if selector:evidence['selector']=selector
    return {'observed':None,'tool':tool,'status':'not_applicable','error':reason,
            'reason':'宿主工具清单没有该工具' if reason=='tool_not_in_server' else '实际回包没有所需的'+{'organic_top5':'自然位次','ad_keyword_count':'广告曝光区分','peak_top100':'商品标识'}[key]+'字段',
            'sources':[relative],'not_applicable_evidence':evidence,
            'next_action':'当前数据源无法核验；可补带自然位次/广告标记的页面或导出，再重新验收。'}


def collect_calls(payloads):
    calls=[]
    def visit(doc,path,selector=''):
        if isinstance(doc,dict) and doc.get('tool'):
            calls.append({'tool':tool_name(doc['tool']),'args':doc.get('args') or doc.get('arguments') or {},
                          'data':decode(doc),'source':path,'selector':selector})
            return
        if isinstance(doc,dict):
            for name,value in doc.items():
                if isinstance(value,dict) and (value.get('tool') or name=='calls'):
                    token=str(name).replace('~','~0').replace('/','~1');visit(value,path,selector+'/'+token)
    for path,data,src in payloads:
        doc=json.loads(path.read_bytes());before=len(calls);visit(doc,src['source'])
        if len(calls)==before and '__' in path.stem:
            node={'tool':path.stem.split('__')[-1],'args':{},'data':data,'source':src['source'],'selector':''}
            index=path.parent/'tool_calls.index.json'
            if index.is_file():
                matches=[row for row in json.loads(index.read_bytes()).get('calls',[])
                         if row.get('source')==path.name and tool_name(row.get('tool'))==node['tool']]
                if len(matches)==1 and isinstance(matches[0].get('args'),dict):
                    node['args']=matches[0]['args'];node['metadata_source']=str(Path(src['source']).parent/index.name).replace('\\','/')
            calls.append(node)
    return calls


def unverifiable_error(root,key,item):
    """A status string alone can never waive an available or unattempted metric."""
    if key=='category_comparison':
        from s1_category_comparison import coverage_error
        return coverage_error(root,item)
    try:
        e=item['not_applicable_evidence'];tool=CAPABILITY_TOOLS[key]
        if item.get('observed') is not None:raise ValueError('不可核验不能同时声明观测数量')
        if e['tool']!=tool or tool_name(item.get('tool'))!=tool:raise ValueError('工具与覆盖项目不符')
        path=(Path(root)/e['source']).resolve()
        if not path.is_relative_to(Path(root).resolve()):raise ValueError('证据路径越界')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=e['sha256']:raise ValueError('证据哈希已变')
        doc=json.loads(raw)
        # A single missing-field reply cannot override another actual reply
        # proving the same tool has that field, even when its value is null.
        siblings=[(p,decode(json.loads(p.read_bytes())),{'source':p.name}) for p in path.parent.glob('*.json')]
        related=[c for c in collect_calls(siblings) if c['tool']==tool]
        if item['error']=='tool_not_in_server' and related:raise ValueError('已有实际工具回包，不能声明宿主没有工具')
        if item['error']=='tool_returns_no_field':
            for call in related:
                rows=call['data'] if isinstance(call['data'],list) else []
                if any(isinstance(r,dict) and set(r)&FIELDS[key] for r in rows):raise ValueError('其他实际回包具有需要的字段')
        if item['error']=='tool_not_in_server' and e['kind']=='tool_inventory':
            names={tool_name(t.get('name') if isinstance(t,dict) else t) for t in doc['tools']}
            if not names or tool in names:raise ValueError('工具清单不支持不存在的声明')
        elif item['error']=='tool_returns_no_field' and e['kind']=='response_fields':
            selected=pointer(doc,e.get('selector'));declared=selected.get('tool') if isinstance(selected,dict) else None
            if tool_name(declared or path.stem.split('__')[-1])!=tool:raise ValueError('回包工具不符')
            data=decode(selected)
            rows=[r for r in data if isinstance(r,dict)] if isinstance(data,list) else []
            if not rows:raise ValueError('空回包不能证明工具缺字段')
            if set().union(*(set(r) for r in rows)) & FIELDS[key]:raise ValueError('回包实际具有需要的字段')
        else:raise ValueError('不可核验只接受工具清单或实际回包字段证据')
        if not item.get('next_action'):raise ValueError('缺下一步说明')
        return None
    except (OSError,ValueError,KeyError,TypeError,IndexError) as error:return str(error)


def capability_counts(root,payloads,roots):
    calls=collect_calls(payloads)
    results={};queries=[];historical={};ads=set();valid_ads=[];no_fields={}
    for c in calls:
        rows=[r for r in c['data'] if isinstance(r,dict)] if isinstance(c['data'],list) else []
        tool=c['tool'];args=c['args'];source=c['source']
        key=next((k for k,v in CAPABILITY_TOOLS.items() if v==tool),None)
        if key and rows and not set().union(*(set(r) for r in rows)) & FIELDS[key]:no_fields.setdefault(key,[]).append(c)
        if tool=='product_search_from_history' and (args.get('keyword') or args.get('node_id')) and args.get('search_time') and any(r.get('asin') for r in rows):
            scope=json.dumps({k:v for k,v in args.items() if k!='page'},sort_keys=True,ensure_ascii=False)
            historical.setdefault(scope,set()).update(r['asin'] for r in rows if r.get('asin'))
        if tool=='keyword_search_results' and args.get('position_type',1)==1:
            positions={}
            for row in rows:
                value=next((row.get(f) for f in ['organic_position','organic_rank','position','rank'] if row.get(f) is not None),None)
                try:
                    numeric=float(value)
                    if not numeric.is_integer():continue
                    position=int(numeric)
                except (ValueError,TypeError):continue
                if row.get('asin') and position>0:positions[position]=row['asin']
            verified=all(p in positions for p in range(1,6)) and len({positions[p] for p in range(1,6)})==5
            query={'keyword':args.get('keyword'),'rows':len(rows),'positions':positions,'source':source}
            if any(set(row)&FIELDS['organic_top5'] for row in rows):query['organic_order_verified']=verified
            queries.append(query)
        if tool=='product_traffic_terms' and rows and set().union(*(set(r) for r in rows)) & FIELDS['ad_keyword_count']:
            valid_ads.append(c)
            for r in rows:
                label=str(r.get('exposure_position') or '')
                flagged=bool(re.search(r'\bad\b|\bSP\b|\bSB\b|sponsored|广告',label,re.I)) or r.get('is_ad') is True
                flagged=flagged or bool(r.get('latest_ad_exposure_time'))
                for field in ['latest_ad_position','ad_position']:
                    try:flagged=flagged or float(r.get(field) or 0)>0
                    except (TypeError,ValueError):pass
                if flagged and r.get('keyword'):ads.add((args.get('asin'),r['keyword']))
    observed={'peak_top100':sum(len(v)>=100 for v in historical.values()),
              'organic_top5':sum(bool(q.get('organic_order_verified')) and (not roots or str(q['keyword']).lower() in roots) for q in queries),
              'ad_keyword_count':len(ads)}
    for key,tool in CAPABILITY_TOOLS.items():
        selected=[c for c in calls if c['tool']==tool]
        sources=sorted({s for c in selected for s in [c['source'],c.get('metadata_source')] if s})
        node={'observed':observed[key],'tool':tool,'sources':sources}
        usable=any(isinstance(c['data'],list) and any(isinstance(r,dict) and set(r)&FIELDS[key] for r in c['data']) for c in selected)
        if not usable and no_fields.get(key):
            c=no_fields[key][-1];node.update(unavailable(root,key,tool,'tool_returns_no_field',c['source'],'response_fields',c['selector']))
        elif not selected:
            for path in (Path(root)/'S1_选品板块/amazon-market-research/运行数据').glob('*tool*inventory*.json'):
                doc=json.loads(path.read_bytes());names={tool_name(t.get('name') if isinstance(t,dict) else t) for t in doc.get('tools',[])}
                if names and tool not in names:
                    node.update(unavailable(root,key,tool,'tool_not_in_server',path.relative_to(root),'tool_inventory'));break
        if key=='peak_top100' and historical:node['reason']=f'真实历史查询最大{max(map(len,historical.values()))}个不同商品；同查询月份至少100个才计一组，未满需继续分页。'
        if key=='ad_keyword_count' and valid_ads:node['reason']=f'实际回包已能区分广告/自然曝光；已取页发现{len(ads)}个广告词，只是页内下限，非全分页总量。'
        results[key]=node
    return results,queries


# R06.1 AS-02：第 4 步就检查这几项，而不是拖到第 9 步。评论、趋势、类目对比由第 3 步需求门负责。
EARLY_KEYS = ('variations', 'peak_top100', 'organic_top5', 'ad_keyword_count')
EARLY_LABELS = {'variations': '变体数据', 'peak_top100': '旺季榜单', 'organic_top5': '自然前五', 'ad_keyword_count': '广告关键词'}
EARLY_TOOLS = {'variations': 'product_variations', **CAPABILITY_TOOLS}
FACTS_REL = 'S1_选品板块/amazon-market-research/01_market_facts.json'


def early_coverage_errors(root, mode='full_delivery', keys=EARLY_KEYS):
    """与第 9 步 C3 同一判定，提前到第 4 步。没有市场事实文件时返回 None（交给 C3）。"""
    path = Path(root) / FACTS_REL
    if not path.is_file():
        return None
    coverage = (json.loads(path.read_text(encoding='utf-8')).get('coverage') or {})
    limits = floors(mode)
    errors = []
    for key in keys:
        floor, item, label, tool = limits.get(key), coverage.get(key), EARLY_LABELS[key], EARLY_TOOLS[key]
        if floor is None:
            continue
        if not isinstance(item, dict):
            errors.append(f'{label}缺结构化覆盖记录：先运行 s1_market_facts.py')
            continue
        if item.get('status') == 'not_applicable':
            problem = unverifiable_error(root, key, item)
            if problem:
                errors.append(f'{label}的“不可核验”证据无效：{problem}')
            continue
        observed = item.get('observed')
        if isinstance(observed, (int, float)) and observed >= floor:
            continue
        if item.get('tool') and (item.get('error') or item.get('reason')) and (item.get('sources') or item.get('source')) and item.get('next_action'):
            continue  # 已登记的外部失败：保留缺口，第 9 步按待补证处理
        errors.append(f'{label}还没采够（{observed or 0}/{floor}）：用 {tool} 采集，回包用 scripts/save_mcp_response.py 保存后重跑 s1_market_facts.py；'
                      '工具确实不可用时保存工具清单或报错回包作证据，不能空着进入下一步')
    return errors
