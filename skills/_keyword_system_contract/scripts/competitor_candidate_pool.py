#!/usr/bin/env python3
"""Deterministic competitor candidates from registered host responses. No MCP or network."""
from __future__ import annotations
import argparse,copy,hashlib,json,math,re,unicodedata
from collections import Counter
from pathlib import Path
import yaml

SCORER_VERSION='0.1.0'
BASE_SCORES={'direct':6,'variant':4,'substitute':2,'accessory':1,'irrelevant':0}
CLASSES=set(BASE_SCORES)
MATCH={'direct':'same_form','variant':'same_category','substitute':'substitute','accessory':'accessory','irrelevant':'irrelevant','gray':'unknown','own_family':'unknown'}
TYPES={'serp','bsr','name','new','seed','enrich','user_added'}

def encoded(value):return (json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode('utf8')
def sha(raw):return hashlib.sha256(raw).hexdigest()
def load(path):
 text=Path(path).read_text(encoding='utf-8-sig')
 try:return json.loads(text)
 except json.JSONDecodeError:return yaml.safe_load(text)

def safe(root,relative):
 root=Path(root).resolve();p=(root/relative).resolve();p.relative_to(root);return p

def norm(text):
 words=re.findall(r'[a-z0-9]+',unicodedata.normalize('NFKC',str(text)).casefold())
 return ' '.join(w[:-3]+'y' if w.endswith('ies') and len(w)>4 else w[:-1] if w.endswith('s') and not w.endswith('ss') and len(w)>3 else w for w in words)

def contains(text,term):return ' '+norm(term)+' ' in ' '+norm(text)+' '
def finite(value):return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)

def product_rows(value):
 """Prefer a single MCP representation, so content + structuredContent cannot double count."""
 if isinstance(value,str):
  try:return product_rows(json.loads(value))
  except (ValueError,TypeError):return []
 if isinstance(value,list):return [r for item in value for r in product_rows(item)]
 if not isinstance(value,dict):return []
 if re.fullmatch('[A-Z0-9]{10}',str(value.get('asin',''))) and value.get('title'):return [value]
 for key in ['structuredContent','result','data','products','items','content','text']:
  if key in value:
   rows=product_rows(value[key])
   if rows:return rows
 return []

def features(row,profile):
 title=row.get('title','');form=profile.get('form_tokens') or {}
 shapes=[t for t in form.get('required_any',[]) if contains(title,t)];containers=[t for t in form.get('container_any',[]) if contains(title,t)]
 kw=[t for t in profile['entry_keywords'] if contains(title,t)]
 bridges=set(profile.get('form_bridge_tokens') or [])|{w for q in profile['entry_keywords'] for w in norm(q).split()}
 adjacent=False
 words=norm(title).split()
 for shape in shapes:
  for container in containers:
   for left,right in [(norm(shape).split(),norm(container).split()),(norm(container).split(),norm(shape).split())]:
    for start in range(len(words)):
     if words[start:start+len(left)]!=left:continue
     end=start+len(left)
     for gap in range(3):
      if set(words[end:end+gap])<=bridges and words[end+gap:end+gap+len(right)]==right:adjacent=True
 nodes={str(x[1]) for x in row.get('subcategory') or [] if isinstance(x,list) and len(x)>1}
 target_nodes={str(x) for x in profile.get('target_node_ids') or []}
 price=row.get('price');target=profile.get('target_price');ratio=price/target if finite(price) and price>0 and finite(target) and target>0 else None
 band=profile.get('price_band') or [.33,3.]
 f={'F_FORM':bool(shapes and containers),'F_KW':len(kw),'F_VARIANT':any(contains(title,t) for t in profile.get('variant_tokens') or []),'F_ACC':any(contains(title,t) for t in profile.get('accessory_tokens') or []),'F_GOODS':any(contains(title,t) for t in profile.get('contains_goods_tokens') or []),'F_OWN':norm(row.get('brand','')) in {norm(x) for x in profile.get('own_brands') or []} or row['family_id'] in profile.get('own_parent_asins',[]) or row['asin']==profile.get('target_asin'),'F_NODE':bool(nodes&target_nodes) if nodes and target_nodes else None,'F_PRICE':band[0]<=ratio<=band[1] if ratio is not None else None,'F_FREQ':row['distinct_keyword_count']>=2}
 f['F_SUB']=any(contains(title,t) for t in profile.get('substitute_tokens') or []) and not f['F_FORM']
 f['F_SHAPE_CONTEXT']=bool(kw or adjacent)
 codes=[key for key,value in f.items() if value is True]+['F_KW:'+str(len(kw))]
 if f['F_PRICE'] is False:codes.append('F_PRICE:outside')
 if f['F_NODE'] is False:codes.append('F_NODE:different')
 return f,sorted(codes),{'form_tokens':shapes,'container_tokens':containers,'entry_keywords':kw,'price_ratio':ratio}

def classify(f):
 if f['F_OWN']:return 'own_family'
 if f['F_ACC'] and not f['F_FORM']:return 'accessory'
 if not f['F_FORM'] and f['F_KW']==0 and not f['F_SUB']:return 'irrelevant'
 if f['F_SUB'] or f['F_GOODS'] and not f['F_FORM']:return 'substitute'
 if f['F_FORM'] and f['F_GOODS']:return 'gray'
 # A brand containing "roll" is not proof of a roll-up product, even outside the price band.
 if f['F_FORM'] and not f['F_SHAPE_CONTEXT'] and not f['F_KW']:return 'irrelevant'
 if f['F_FORM'] and (f['F_VARIANT'] or f['F_PRICE'] is False or f['F_NODE'] is False):return 'variant'
 if f['F_FORM'] and f['F_SHAPE_CONTEXT'] and not f['F_VARIANT'] and f['F_PRICE'] is not False and f['F_NODE'] is not False:return 'direct'
 return 'gray'

def reason(row):
 f=row['features'];detail=row['feature_details'];ratio=detail['price_ratio'];price=row.get('price')
 parts=['被'+str(row['distinct_keyword_count'])+'个入口词命中（'+' / '.join(row['keyword_hits'])+'）']
 if detail['form_tokens'] or detail['container_tokens']:parts.append('标题形态词 '+','.join(detail['form_tokens'])+'；收纳词 '+','.join(detail['container_tokens']))
 parts.append('类目'+('一致' if f['F_NODE'] is True else '不同' if f['F_NODE'] is False else '未知'))
 parts.append('价格 $'+str(price)+'，为本品 '+str(round(ratio,2))+' 倍' if ratio is not None else '价格暂无数据')
 parts.append('品牌 '+str(row.get('brand') or '未知')+(' 属本品家族' if f['F_OWN'] else '；本品品牌关系按画像核对'))
 parts.append('特征 '+'、'.join(row['feature_codes']))
 parts.append({'direct':'直接竞品','variant':'同类不同款','substitute':'替代方案','accessory':'配件耗材','irrelevant':'无关','gray':'待判','own_family':'同品牌家族'}[row['class6']])
 return '；'.join(parts)

def build(root,profile,manifest,adjudication=None):
 root=Path(root).resolve();manifest=Path(manifest).resolve();manifest.relative_to(root)
 profile=dict(profile);profile['product_id']=profile.get('product_id') or root.name  # default: the product folder name
 missing=[label for label,ok in (('marketplace（站点，如 US）',profile.get('marketplace')),
     ('entry_keywords（4–5 个已确认入口词，当前 '+str(len(profile.get('entry_keywords') or []))+' 个）',4<=len(profile.get('entry_keywords') or [])<=5)) if not ok]
 if missing:raise ValueError('竞品画像缺字段：'+'；'.join(missing)+'。字段表见 templates/competitor_profile.example.yaml')
 if len({norm(x) for x in profile['entry_keywords']})!=len(profile['entry_keywords']):raise ValueError('入口词不得重复')
 calls=load(manifest);calls=calls.get('calls',[]) if isinstance(calls,dict) else calls
 if not isinstance(calls,list) or not calls:raise ValueError('调用清单为空或格式无效')
 rows={};registered=set();raw_hits=0;warnings=[];input_sha={manifest.relative_to(root).as_posix():sha(manifest.read_bytes())};dirs=set();manifest_rows=[]
 for callnum,call in enumerate(calls):
  args=call.get('arguments') or call.get('args') or {};tool=call.get('tool','');kind=call.get('source_type') or {'keyword_search_results':'serp','category_products':'bsr','product_search_from_name':'name'}.get(tool)
  if kind not in TYPES:raise ValueError('调用缺有效source_type：'+str(call.get('response_path')))
  if not call.get('date') or not tool:raise ValueError('调用缺工具或日期')
  path=safe(root,call['response_path']);relative=path.relative_to(root).as_posix();raw=path.read_bytes();registered.add(relative);dirs.add(path.parent);input_sha[relative]=sha(raw)
  manifest_rows.append({'path':relative,'sha256':sha(raw),'source_type':kind})
  query=str(args.get('keyword') or args.get('name') or args.get('attribute_name') or call.get('query') or '');page=int(args.get('page') or 1)
  if page<1:raise ValueError('页码必须为正整数')
  for order,item in enumerate(product_rows(json.loads(raw.decode('utf-8-sig'))),1):
   if kind=='enrich':count_hit=False
   else:count_hit=True;raw_hits+=1
   asin=item['asin'];current=rows.setdefault(asin,{'asin':asin,'hits':[],'field_conflicts':[],'_dates':{}})
   source={'path':relative,'sha256':sha(raw),'source_type':kind,'query':query,'page':page,'order':order,'date':call['date']}
   if count_hit:current['hits'].append(source)
   current.setdefault('evidence',[]).append(source)
   for field in ['title','brand','price','parent_asin','subcategory','monthly_sale_volume','monthly_sales_volume','online_date','main_image']:
    value=item.get(field)
    if value in (None,'',[]):continue
    if field in current and current[field]!=value:current['field_conflicts'].append({'field':field,'old':current[field],'new':value,'source':source})
    stamp=(str(call['date']),callnum)
    if stamp>=current['_dates'].get(field,('',-1)):current[field]=copy.deepcopy(value);current['_dates'][field]=stamp
 result=[]
 for asin,row in sorted(rows.items()):
  row.pop('_dates');row['family_id']=row.get('parent_asin') or asin;row['family_unverified']=not bool(row.get('parent_asin'))
  hits=row['hits'];row['keyword_hits']=sorted({h['query'] for h in hits if h['query'] and h['source_type'] in {'serp','name'}});row['distinct_keyword_count']=len(row['keyword_hits']);row['hit_count']=len({(h['source_type'],h['query'],h['page']) for h in hits})
  row['best_order']=min(((h['page']-1)*20+h['order'] for h in hits if h['source_type'] in {'serp','name'}),default=None);row['bsr_order']=min((h['order'] for h in hits if h['source_type']=='bsr'),default=None);row['source_flags']=sorted({h['source_type'] for h in hits});row['bsr_only']='bsr' in row['source_flags'] and not {'serp','name'}&set(row['source_flags'])
  row['features'],row['feature_codes'],row['feature_details']=features(row,profile);row['class6']=classify(row['features'])
  ratio=row['feature_details']['price_ratio']
  if ratio is not None and (ratio>10 or ratio<.1):row['field_conflicts'].append({'field':'price','warning':'价格相对本品偏离10倍以上，保留原始USD值，请核对','value':row.get('price')})
  result.append(row)
 gray=[{k:copy.deepcopy(r.get(k)) for k in ['asin','title','brand','price','subcategory','features','feature_codes']} for r in result if r['class6']=='gray'];adj_errors=[]
 if adjudication:
  valid=adjudication.get('gray_todo_sha256')==sha(encoded(gray)) and adjudication.get('model') and adjudication.get('time')
  proposals={r.get('asin'):r for r in adjudication.get('rows',[])}
  for row in result:
   proposal=proposals.get(row['asin'])
   if row['class6']!='gray' or proposal is None:continue
   codes=proposal.get('evidence_codes');note=proposal.get('note_zh','')
   if not valid or proposal.get('class') not in CLASSES or not isinstance(codes,list) or not codes or not set(codes)<=set(row['feature_codes']) or len(note)>30:adj_errors.append(row['asin']+'灰区判定无效，保留待判');continue
   row['class6']=proposal['class'];row['model_note']=note
 for row in result:
  f=row['features'];klass=row['class6'];base=BASE_SCORES.get(klass)
  row['relevance_score']=None if base is None else round(min(10,base+min(row['distinct_keyword_count'],3)+(.5 if f['F_NODE'] else 0)+(.5 if {'serp','bsr'}<=set(row['source_flags']) else 0)),1)
  row['is_target']=klass=='own_family';row['match']=MATCH[klass]
  if klass=='own_family' and f['F_FORM']:row['match']='same_category' if f['F_VARIANT'] or f['F_PRICE'] is False or f['F_NODE'] is False else 'same_form'
  row['match_reason']=reason(row);row['recommendation']='exclude' if klass not in {'direct','variant','gray'} else 'pending' if klass=='gray' else 'benchmark'
 ranked=sorted(result,key=lambda r:(-(r['relevance_score'] if r['relevance_score'] is not None else -1),r['asin']));families=set()
 for row in ranked:
  if row['class6'] in {'direct','variant'} and row['family_id'] not in families and len(families)<20:row['recommendation']='confirm';families.add(row['family_id'])
 enrich=[{'asin':r['asin'],'missing_fields':[name for name in ['parent_asin','subcategory'] if not r.get(name)]} for r in ranked if r['class6']!='irrelevant' and (not r.get('parent_asin') or not r.get('subcategory'))][:int(profile.get('enrich_limit',60))]
 ignores={'candidate_pool.json','candidate_review.draft.json','pool_summary.json','enrich_todo.json','gray_todo.json','调用口径清单.json','竞品门禁_用户确认后.json','竞品门禁_待用户确认.json'}
 extras=sorted({p.relative_to(root).as_posix() for directory in dirs for p in directory.glob('*.json') if p.resolve()!=manifest and p.relative_to(root).as_posix() not in registered and p.name not in ignores})
 if extras:warnings.append('发现未登记响应：'+', '.join(extras)+'；未读取，请宿主补登记实际调用')
 review=[{k:copy.deepcopy(row.get(k)) for k in ['asin','title','match','match_reason','family_id','is_target','class6','relevance_score','distinct_keyword_count','best_order','bsr_order','source_flags','recommendation']}|{'decision':'pending','source':{k:row['evidence'][0][k] for k in ['path','sha256']}} for row in ranked]
 irrelevant=sum(r['class6']=='irrelevant' for r in result)
 if result and irrelevant/len(result)>=0.8:  # S2-R4: almost everything irrelevant usually means the profile tokens are wrong
  warnings.append(f'{irrelevant}/{len(result)} 个候选判“无关”：多半是画像的 form_tokens / entry_keywords 没填对。先用 competitor_profile_draft.py 从 S1 画像生成草稿，核对词表后重跑')
 summary={'raw_hits':raw_hits,'unique_asins':len(result),'dedup_rate':round(1-len(result)/raw_hits,6) if raw_hits else 0,'classes':dict(sorted(Counter(r['class6'] for r in result).items())),'bsr_only':sum(r['bsr_only'] for r in result),'gray':sum(r['class6']=='gray' for r in result),'recommended_confirm':len(families),'shortfall':max(0,20-len(families))}
 return {'scorer_version':SCORER_VERSION,'profile_sha256':sha(encoded(profile)),'input_sha256':input_sha,'input_manifest':{'path':manifest.relative_to(root).as_posix(),'sha256':sha(manifest.read_bytes())},'registered_responses':manifest_rows,'candidates':ranked,'candidate_review':review,'summary':summary,'enrich_todo':enrich,'gray_todo':[g for g in gray if next(r for r in result if r['asin']==g['asin'])['class6']=='gray'],'gray_input_sha256':sha(encoded(gray)),'unregistered_files':extras,'warnings':warnings,'adjudication_errors':adj_errors}

def write_outputs(root,profile,manifest,out,adjudication=None):
 root=Path(root).resolve();out=safe(root,Path(out));result=build(root,profile,manifest,adjudication);out.mkdir(parents=True,exist_ok=True)
 pool=out/'candidate_pool.json';pool.write_bytes(encoded(result));draft={'run_id':profile.get('run_id') or profile['product_id'],'product_id':profile['product_id'],'marketplace':profile['marketplace'],'status':'pending_user','candidate_pool_version':SCORER_VERSION,'candidate_pool':{'path':pool.relative_to(root).as_posix(),'sha256':sha(pool.read_bytes())},'search_evidence':result['registered_responses'],'candidate_review':result['candidate_review']}
 (out/'candidate_review.draft.json').write_bytes(encoded(draft));(out/'pool_summary.json').write_bytes(encoded(result['summary']))
 for key in ['enrich_todo','gray_todo']:
  path=out/(key+'.json')
  if result[key]:path.write_bytes(encoded(result[key]))
  elif path.is_file():path.unlink()
 lines=['# 竞品候选确认表','','相关分仅表示同赛道程度，不是威胁分。返回顺序为该响应按月销量的返回顺序，不是搜索排名。','',f"建议 {result['summary']['recommended_confirm']} 个独立款族；缺口 {result['summary']['shortfall']} 个，不用替代品凑数。",'', '| 编号 | 商品 | 价格 / 月销 | 分类 | 相关分 | 命中词数 / 来源 | 理由（证据） | 建议 |','|---|---|---|---|---|---|---|---|']
 for i,row in enumerate(result['candidates'],1):
  if row['class6'] not in {'direct','variant','gray'}:continue
  label={'direct':'直接竞品','variant':'同类不同款','gray':'待判'}[row['class6']]
  values=[i,f"[{row['asin']}](https://www.amazon.com/dp/{row['asin']}) {row['title']}",str(row.get('price','待核'))+' / '+str(row.get('monthly_sale_volume',row.get('monthly_sales_volume','待核'))),label,row['relevance_score'] if row['relevance_score'] is not None else '待判',str(row['distinct_keyword_count'])+' / '+','.join(row['source_flags']),row['match_reason']+'；'+row['evidence'][0]['path'],{'confirm':'建议确认','benchmark':'留作对标','pending':'待判'}[row['recommendation']]]
  # Model adjudication may explain a gray case, but never rewrites evidence reasons.
  if row.get('model_note'):values[-1]+='；备注：'+row['model_note']
  lines.append('| '+' | '.join(str(v).replace('|','／').replace('\n',' ') for v in values)+' |')
 for label,predicate in [('被排除的候选',lambda r:r['class6'] not in {'direct','variant','gray'}),('只在BSR的隐形对手',lambda r:r['bsr_only'])]:
  selected=[r for r in result['candidates'] if predicate(r)];lines+=['','<details><summary>'+label+'（'+str(len(selected))+'）</summary>','']
  for klass in sorted({r['class6'] for r in selected}):
   group=[r for r in selected if r['class6']==klass];lines+=['- '+klass+' '+str(len(group))+'：'+'；'.join(r['asin']+' '+r['title'] for r in group[:5])]
  lines+=['','</details>']
 (out/'05A_竞品候选确认表.md').write_text('\n'.join(lines)+'\n',encoding='utf8');return result

def main():
 p=argparse.ArgumentParser(description=__doc__)
 for name in ['run','profile','manifest','out']:p.add_argument('--'+name,type=Path,required=True)
 p.add_argument('--adjudication',type=Path);a=p.parse_args();result=write_outputs(a.run,load(a.profile),a.manifest,a.out,load(a.adjudication) if a.adjudication else None)
 print(json.dumps({'summary':result['summary'],'warnings':result['warnings'],'adjudication_errors':result['adjudication_errors']},ensure_ascii=False));return 2 if result['enrich_todo'] else 3 if result['gray_todo'] else 0

if __name__=='__main__':
 try:raise SystemExit(main())
 except (ValueError,TypeError,KeyError,OSError) as error:print('输入错误：'+str(error));raise SystemExit(1)
