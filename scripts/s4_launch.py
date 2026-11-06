"""S4 helpers for the existing compiler, report and Bulk workflow. Never calls account APIs."""
from __future__ import annotations
import copy,hashlib,importlib.util,json,re,unicodedata,sys,math
from datetime import datetime,timedelta,timezone
from pathlib import Path
import yaml
HOME=Path(__file__).resolve().parents[1]


def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def now():return datetime.now(timezone.utc).isoformat()
MARKETPLACE_TZ={'US':'America/Los_Angeles','CA':'America/Los_Angeles','MX':'America/Los_Angeles','BR':'America/Sao_Paulo','UK':'Europe/London','GB':'Europe/London',
    'DE':'Europe/Berlin','FR':'Europe/Paris','IT':'Europe/Rome','ES':'Europe/Madrid','NL':'Europe/Amsterdam','SE':'Europe/Stockholm','PL':'Europe/Warsaw',
    'JP':'Asia/Tokyo','AU':'Australia/Sydney','SG':'Asia/Singapore','IN':'Asia/Kolkata','AE':'Asia/Dubai','SA':'Asia/Riyadh'}
def first_start_date(marketplace):
    """Tomorrow in the marketplace's time zone: a sheet uploaded later the same day must not start in the past."""
    from zoneinfo import ZoneInfo
    zone=ZoneInfo(MARKETPLACE_TZ.get(str(marketplace or 'US').upper(),'America/Los_Angeles'))
    return (datetime.now(zone)+timedelta(days=1)).strftime('%Y%m%d')
def module(relative,name='s4'):
    path=HOME/relative
    for parent in [HOME/'scripts',path.parent,HOME.parents[1]/'release-overrides']:
        if parent.is_dir() and str(parent) not in sys.path:sys.path.insert(0,str(parent))
    spec=importlib.util.spec_from_file_location('_r04_'+name,path);m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m);return m

def builder():return module('skills/amazon-ad-campaign-builder/scripts/ad_plan_lib.py','builder')
def safe(root,relative):
    root=Path(root).resolve();p=(root/relative).resolve();p.relative_to(root);return p

def output_path(root,role):
    route_file=HOME/'templates/stage_roadmap.yaml'
    if not route_file.is_file():route_file=HOME.parents[1]/'release-overrides/stage_roadmap.yaml'
    route=yaml.safe_load(route_file.read_bytes())['stages']['S4']
    return safe(root,route['artifact_paths'][role])

def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    text=yaml.safe_dump(value,allow_unicode=True,sort_keys=False) if isinstance(value,dict) else value
    path.write_text(text,encoding='utf8');return path


def architecture_fingerprint(plan):
    p=copy.deepcopy(plan)
    for key in ['plan_hash','sku_mapping_confirmation','sku_asin_map','launch_status','upload_eligibility']:p.pop(key,None)
    for key in ['seller_skus','child_asins']:p.get('product',{}).pop(key,None)
    for campaign in p.get('campaigns') or []:campaign.pop('advertised_skus',None)
    return digest(p)


def validate_approved_architecture(plan,review):
    if review.get('approved_architecture_sha256')!=architecture_fingerprint(plan):raise ValueError('已批准方案发生变化，不能仅补SKU放行')
    return True

def validate_package_source(root,plan,required=False):
    binding=plan.get('keyword_package_source') or {}
    if not binding:
        if required:raise ValueError('方案不是由原生 s4_launch.py prepare 生成（缺原始词包来源），不能出表；用 prepare 重新生成方案、本人批准后再出表，不要另做简报或直接调编译器')
        return
    if root is None:raise ValueError('复核词包来源需要当前产品目录，不能只凭旧索引续跑')
    try:
        path=safe(root,binding['path'])
        if file_sha(path)!=binding['sha256'] or binding['package_ref']!=plan['keyword_package_ref']:
            raise ValueError('原始词包来源已变化，先重建方案并重新审核；不能只刷新报告或补SKU')
    except (OSError,KeyError,TypeError) as error:raise ValueError('原始词包来源缺失或无效，先补齐并审核方案') from error


def approve_plan(plan,decision,root=None):
    lib=builder()
    # With a product folder the plan must come from prepare, or the later Bulk step refuses it
    # after the user has already approved; say so here instead.
    validate_package_source(root,plan,required=root is not None)
    if plan.get('flow_version')!='r04.s4' or plan.get('plan_hash')!=lib.compute_plan_hash(plan):raise ValueError('方案内容已变化，重新生成后批准')
    if decision.get('source')!='user' or not str(decision.get('message') or '').strip() or decision.get('plan_hash')!=plan['plan_hash']:raise ValueError('需要本人批准当前方案，旧批准不可复用')
    errors=lib.validate_plan(plan)['errors']
    if errors:raise ValueError('方案技术检查尚未通过：'+'；'.join(errors))
    if not plan.get('campaigns'):raise ValueError('方案没有可执行的广告活动，不能批准空方案')
    p=copy.deepcopy(plan);reasons=p.get('upload_eligibility',{}).get('reasons') or []
    hard=[r for r in reasons if r!='PRODUCTION_MISSING_SELLER_SKU' and not r.startswith('LAUNCH_BID_CONFLICT_REQUIRES_CHOICE:')]
    if hard:raise ValueError('方案仍有硬门槛：'+'；'.join(hard))
    conflicts=[c for c in p['campaigns'] if c['bid_guardrail'].get('launch_decision_required')]
    if conflicts:
        choice=decision.get('launch_choice')
        if choice=='improve_margin':raise ValueError('先改善利润并重算方案，再批准')
        if choice not in {'accept_market_loss','slow_low_bid'}:raise ValueError('市场出价与利润承受力冲突，请选封顶亏损投放 / 改善利润 / 低价慢跑')
        for c in conflicts:
            g=c['bid_guardrail'];multiplier=g.get('total_effective_multiplier') or 1.
            if choice=='accept_market_loss':g['user_bid_ceiling']=max(g['user_bid_ceiling'],g['worst_case_effective_bid'])
            else:g.update(initial_bid=round(g['user_bid_ceiling']/multiplier,2),worst_case_effective_bid=g['user_bid_ceiling'])
            if choice=='slow_low_bid':
                for group in c['ad_groups']:
                    for target in group.get('targets') or []:
                        if target.get('bid') is not None:target['bid']=min(target['bid'],g['initial_bid'])
            g['launch_decision_required']=False;g['launch_choice']=choice;g['no_impressions_rule']['ceiling']=g['user_bid_ceiling']
    p['upload_eligibility']={'status':'blocked','reasons':['PRODUCTION_MISSING_SELLER_SKU']}
    p['plan_hash']=lib.compute_plan_hash(p)
    checks=['identity_scope','campaign_role_and_goal','daily_budget_reconciliation','profit_and_inventory_guardrails','maximum_effective_bid','placement_and_bidding','targeting_source_traceability','negative_relevance_and_res_per_safety','sp_sb_sd_eligibility']
    review={'schema_version':'2.0','artifact_type':'architecture_review','status':'approved_for_bulk','plan_id':p['plan_id'],'plan_version':p['plan_version'],'plan_hash':p['plan_hash'],'hash_schema':p['hash_schema'],
        'reviewed_by':'user','reviewed_at':now(),'decision':copy.deepcopy(decision),'checks':{k:'pass' for k in checks},'prelaunch':True,'input_plan_hash':plan['plan_hash'],'approved_architecture_sha256':architecture_fingerprint(p)}
    return p,review

def validate_mapping(plan,rows,decision):
    expected=[str(v.get('id') or v.get('variant')) if isinstance(v,dict) else str(v) for v in plan['product'].get('variants') or []]
    if not expected or len(expected)!=len(set(expected)):raise ValueError('SKU 映射缺上游确认的变体清单')
    if decision.get('source')!='user' or not str(decision.get('message') or '').strip():raise ValueError('SKU 对应关系尚未由本人逐行确认（确认文件需 source=user 和本人原话）')
    if decision.get('mapping_hash')!=digest(rows):raise ValueError('SKU 映射与本人确认不一致：mapping_hash 按映射行列表（不含外层 rows）以 ensure_ascii=False、sort_keys=True 序列化后取 SHA-256；行改过就重新请本人逐行确认')
    if sorted(str(r.get('variant')) for r in rows)!=sorted(expected):raise ValueError('每个变体都要明确选投或不投，不能漏填SKU对应关系')
    seen=set();supplier={str(s) for s in plan['product'].get('supplier_sku_ids') or []};selected=[]
    for row in rows:
        if not isinstance(row.get('advertise'),bool):raise ValueError('每个变体必须明确这次投 / 不投')
        if not row['advertise']:continue
        sku=row.get('seller_sku')
        if not isinstance(sku,str) or not sku.strip():raise ValueError('投放变体必须填写 Seller SKU')
        if sku in supplier or re.fullmatch(r'B0[A-Z0-9]{8}',sku,re.I):raise ValueError('不能用1688货号或ASIN代替 Seller SKU')
        if re.search(r'(?i)(?:^|[-_])(demo|example|sample)(?:$|[-_])|示例|演示',sku):raise ValueError('正式流程不能用示例或DEMO SKU')
        if sku in seen:raise ValueError('同一SKU不能对应两个变体，发现重复')
        if (sku!=sku.strip() or any(c.isspace() for c in sku) or unicodedata.normalize('NFKC',sku)!=sku or re.fullmatch(r'\d{12,}',sku)) and sku not in decision.get('format_acknowledged',[]):raise ValueError('SKU格式含空格或全角字符，请对照库存后台专门确认')
        asin=row.get('asin')
        if asin and not re.fullmatch(r'B0[A-Z0-9]{8}',asin):raise ValueError('可选ASIN格式不正确')
        seen.add(sku);selected.append(copy.deepcopy(row))
    if not selected:raise ValueError('本次没有选投商品，不生成空的Bulk表')
    return selected

def bind_skus(plan,review,rows,decision,root=None):
    lib=builder()
    validate_package_source(root,plan)
    if plan.get('plan_hash')!=lib.compute_plan_hash(plan) or review.get('plan_hash')!=plan.get('plan_hash') or review.get('status')!='approved_for_bulk':raise ValueError('原方案尚未批准或批准后已变化，不能只补SKU后出表')
    validate_approved_architecture(plan,review)
    selected=validate_mapping(plan,rows,decision);p=copy.deepcopy(plan);skus=[r['seller_sku'] for r in selected]
    proof={'source':'user','message':decision['message'],'mapping_hash':digest(rows),'rows':copy.deepcopy(rows),'confirmed_at':now(),'decision':copy.deepcopy(decision),'approved_plan_hash':plan['plan_hash']}
    p['sku_mapping_confirmation']=proof;p['sku_asin_map']={r['seller_sku']:r['asin'] for r in selected if r.get('asin')}
    p['product']['seller_skus']=skus;p['product']['child_asins']=[r['asin'] for r in selected if r.get('asin')]
    for c in p['campaigns']:
        variants=c.get('variant_ids') or [r['variant'] for r in selected]
        c['advertised_skus']=[r['seller_sku'] for r in selected if r['variant'] in variants]
        if not c['advertised_skus']:raise ValueError('活动没有对应的选投变体，请重新审定活动范围')
    p.update(launch_status='ready_for_bulk',upload_eligibility={'status':'review_required','reasons':[]});p['plan_hash']=lib.compute_plan_hash(p)
    updated={**copy.deepcopy(review),'plan_hash':p['plan_hash'],'derived_from_approved_plan_hash':plan['plan_hash'],'sku_completion_confirmation':copy.deepcopy(proof),'prelaunch':False}
    if lib.validate_plan(p,updated)['errors']:raise ValueError('补SKU后的技术检查未通过')
    return p,updated,proof


def file_sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def copy_support(root,term):
    """Read only final customer-facing fields; research ledgers are not copy evidence."""
    path=safe(root,'S2_文案板块/amazon-listing-copywriter/07B_Listing校验输入.json')
    if not path.is_file():return None
    doc=json.loads(path.read_text(encoding='utf-8-sig'));positions=[]
    tokens=lambda s:set(re.findall(r'[a-z0-9]+',str(s).casefold()))
    wanted=tokens(term)
    for variant in doc.get('skus') or []:
        found=[]
        for field in ['title','item_highlights','bullets','description']:
            value=variant.get(field) or ''
            values=value if isinstance(value,list) else [value]
            for n,text in enumerate(values,1):
                if wanted and wanted.issubset(tokens(text)):found.append(field+(str(n) if isinstance(value,list) else ''))
        if not found:return None
        positions.append(str(variant.get('sku') or variant.get('variant') or '变体')+':'+','.join(found))
    if not positions:return None
    return {'path':path.relative_to(Path(root)).as_posix(),'sha256':file_sha(path),'listing_position':'；'.join(positions),'image_position':'未核对图位，不宣称已承接'}


def prepare_launch(root,strategy,package_path,parameters,decision):
    """Compose governed inputs for the existing compiler; never grants keyword permission."""
    root=Path(root).resolve();package_path=Path(package_path).resolve();package_path.relative_to(root)
    if decision.get('source')!='user' or not str(decision.get('message') or '').strip() or decision.get('parameters_hash')!=digest(parameters):raise ValueError('投放盘子需要本人确认当前参数')
    for key in ['daily_total','target_acos','max_loss','test_days']:
        val=parameters.get(key)
        if isinstance(val,bool) or not isinstance(val,(int,float)) or not math.isfinite(val) or val<=0:raise ValueError('投放盘子参数无效：'+key)
    if parameters['target_acos']>1 or int(parameters['test_days'])!=parameters['test_days']:raise ValueError('目标ACoS使用0到1比例，测试天数为整数')
    if parameters.get('max_bid') is not None and (not math.isfinite(float(parameters['max_bid'])) or float(parameters['max_bid'])<=0):raise ValueError('出价上限必须为正数')
    cap=float(parameters['max_bid']) if parameters.get('max_bid') is not None else None
    choices={str(k).casefold():v for k,v in (parameters.get('keywords') or {}).items()}  # term -> {"include": bool, "bid": number}
    for term,choice in choices.items():
        bid=(choice or {}).get('bid')
        if bid is not None and (isinstance(bid,bool) or not isinstance(bid,(int,float)) or not math.isfinite(bid) or bid<=0):raise ValueError('逐词出价必须为正数：'+term)
        if bid is not None and cap is not None and bid>cap+1e-9:raise ValueError(f'{term} 的出价 {bid} 高于本人最高出价 {cap}；先确认改哪一个')
    indexer=module('skills/_keyword_system_contract/scripts/keyword_index.py','index');package=indexer.load_package(package_path);index=indexer.ensure_index(package_path,package=package,force=True)
    lib=builder();s=copy.deepcopy(strategy)
    if s['product']['product_id']!=package.get('product_id') or s['market']['marketplace']!=package.get('marketplace'):raise ValueError('当前产品或站点与词包不一致')
    s.update(schema_version='2.0',flow_version='r04.s4',review_status='parameters_confirmed')
    s['keyword_package_ref']={key:package[key] for key in ['package_id','package_version','package_hash']}
    s['budget']['daily_total']=parameters['daily_total'];s['unit_economics']['target_acos']=parameters['target_acos']
    if s['unit_economics'].get('target_cpa') is not None:s['unit_economics']['target_cpa']=float(s['unit_economics']['selling_price'])*parameters['target_acos']
    s['business_goal'].setdefault('target_kpis',{})['test_days']=parameters['test_days'];s['business_goal'].setdefault('guardrails',{})['max_post_ad_cumulative_loss_usd']=parameters['max_loss']
    s['launch_parameters']={**copy.deepcopy(parameters),'source':'user','message':decision['message']}
    sp=s['ad_product_decisions']['SP']
    if sp['capability'].get('status')=='ineligible':raise ValueError('当前明确不具备SP资格，不能生成投放方案')
    if sp['capability'].get('status')!='available':
        sp['readiness']={'decision':'plan_only','decision_basis':['上架前制作暂停状态上传表；不表示账户/商品资格通过'],'reevaluate_when':['上传前核对资格、可售状态及Featured Offer']}
    s.setdefault('res_per_policy',{'source_negative_after':'destination_validated'})
    s.setdefault('data_explanation_window',{'obtained_facts':['当前S2词包与最终文案'],'derived':['广告单赚亏估算'],'simulated':[],'missing_and_impact':['非本账户实投转化率'],'continue_and_revision':['上传后按既有S5入口复盘']})
    if not s['product'].get('variants'):
        s['product']['variants']=list((s['unit_economics'].get('contribution_by_color') or {}).keys())
    if not s['product'].get('variants'):raise ValueError('缺已确认的产品变体清单，不能猜测SKU映射')
    playbook=choose_playbook(root,s['market']['marketplace']);s['launch_playbook']={k:v for k,v in playbook.items() if k!='profile'}
    specs=((playbook.get('profile') or {}).get('ad_playbook') or {}).get('launch_campaigns') or [{'name':'核心精准','match':'exact','keywords_max':3,'budget_share':.67},{'name':'自动紧密','match':'auto_close','budget_share':.33}]
    if not specs or any(x.get('match') not in {'exact','phrase','broad','auto_close'} or not isinstance(x.get('budget_share'),(int,float)) or not math.isfinite(x['budget_share']) or x['budget_share']<=0 for x in specs):raise ValueError('类目打法活动结构未通过检查，不猜测其含义')
    amounts=parameters.get('campaign_budgets')
    if amounts is not None:  # the user's own split in money, campaign by campaign, instead of a rounded share
        if not isinstance(amounts,list) or len(amounts)!=len(specs) or any(isinstance(a,bool) or not isinstance(a,(int,float)) or a<=0 for a in amounts):raise ValueError(f'各活动日预算要按活动顺序给 {len(specs)} 个正数：'+'、'.join(x['name'] for x in specs))
        if abs(sum(amounts)-parameters['daily_total'])>.005:raise ValueError('各活动日预算之和须等于日预算 '+str(parameters['daily_total']))
        specs=[{**x,'budget_share':a/parameters['daily_total']} for x,a in zip(specs,amounts)]
    records={r['keyword_id']:r for r in package.get('records') or []};pools=package.get('pools') or {};blueprints=[];pending=[]
    unknown=sorted(set(choices)-{str(r.get('keyword') or '').casefold() for r in records.values()})
    if unknown:raise ValueError('逐词设置里有词包没有的词：'+'、'.join(unknown))
    signalfile=root/'S4_广告板块/_阶段总览/机会词来源.yaml'
    captured=yaml.safe_load(signalfile.read_bytes()) if signalfile.is_file() else {}
    opportunities=opportunity_candidates(root,captured.get('input_candidates') or [],captured.get('sources') or [],captured.get('available_sources'))
    current_bids={r['term'].casefold():r['market_bid'] for r in opportunities.get('candidates') or [] if r.get('market_bid')}
    def metric(record,names):
        if any(x in {'cpc','cpc_mid'} for x in names) and record['keyword'].casefold() in current_bids:return current_bids[record['keyword'].casefold()]
        for name in names:
            value=(record.get('metrics') or {}).get(name)
            value=value.get('value') if isinstance(value,dict) else value
            if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>0:return value
        return None
    for n,spec in enumerate(specs,1):
        match=spec['match'];pool=next((p for p in (['exact_p1','exact'] if match=='auto_close' else [match+'_p1',match,'phrase_feature','broad_discovery']) if pools.get(p)),None)
        eligible=[]
        for kid in pools.get(pool,[]):
            r=records[kid];support=copy_support(root,r['keyword'])
            if match!='auto_close' and (choices.get(r['keyword'].casefold()) or {}).get('include') is False:continue  # the user left it out this round
            if support and metric(r,['cpc_mid','cpc']):eligible.append((r,support))
            else:pending.append(r['keyword'])
        eligible=eligible[:int(spec.get('keywords_max') or 3)]
        if not eligible:raise ValueError('缺可承接的已许可关键词或建议竞价；补齐S2文案/词源后继续，不凭空出价')
        def bid_for(r):
            chosen=(choices.get(r['keyword'].casefold()) or {}).get('bid')
            value=chosen if chosen is not None else metric(r,['cpc_mid','cpc'])
            return round(min(value,cap),2) if cap is not None else value
        bids=[bid_for(r) for r,_ in eligible];cvrs=[metric(r,['cvr_24h','cvr']) for r,_ in eligible];cvrs=[x for x in cvrs if x and x<=1]
        # A keyword campaign's default bid covers its keywords; an auto campaign reaches past them,
        # so it starts from the middle of their prices rather than the dearest one.
        signal=max(bids) if match!='auto_close' else sorted(bids)[len(bids)//2]
        if match=='auto_close' and parameters.get('auto_bid') is not None:signal=float(parameters['auto_bid'])
        if cap is not None:signal=round(min(signal,cap),2)
        cvr={'value':min(cvrs) if cvrs else None,'source_type':'third_party','source_id':package['package_id'],'metric_semantic':'third_party_estimate','window':copy.deepcopy((package.get('data_sources') or [{}])[0].get('period') or {})}
        key='SP-LAUNCH-'+str(n);group={'ad_group_key':key+'-AG','name':spec['name'],'purpose':spec['name']}
        if match=='auto_close':
            # Amazon creates all four auto groups for an auto campaign; the ones not in use are written
            # out paused so they do not start at the default bid when the campaign is switched on.
            group['manual_targets']=[{'target_id':'AUTO-'+expr.upper().replace('_','-'),'target_type':'auto','expression':expr,'match_type':'auto','lineage':[{'source_id':'KC-0114'},{'source_id':'KC-0042'}],
                                      **({} if expr=='close_match' else {'state':'paused'})} for expr in ['close_match','loose_match','substitutes','complements']]
        else:
            # The native compiler still evaluates package permission for every manual term.
            group['manual_targets']=[{'target_id':r['keyword_id'],'target_type':'keyword','expression':r['keyword'],'match_type':match,'bid':bid_for(r),'copy_support':support,'lineage':copy.deepcopy(r.get('lineage') or [{'source_id':package['package_id']}])} for r,support in eligible]
        blueprints.append({'campaign_key':key,'campaign_name':s['product']['product_id']+'-'+spec['name'],'ad_product':'SP','role':'res','primary_goal':'launch_test','minimum_complexity':'simple','required':True,'loss_if_omitted':'减少新品数据验证','daily_budget_weight':spec['budget_share'],'bidding_strategy':'fixed','placement_adjustments':{'TOS':0,'PP':0},'expected_cvr':cvr,'bid_signal':{'value':signal,'source_id':package['package_id'],'source_path':package_path.relative_to(root).as_posix(),'as_of':copy.deepcopy(package.get('data_sources') or [])},'effective_bid_factors':{'formula_mode':'multiplicative','dynamic_multiplier':1.,'business_multiplier':1.,'audience_multiplier':1.},'ad_groups':[group],'review_cadence':{'day':3},'launch_policy':'r04.s4'})
    s['architecture']={'campaign_blueprints':blueprints,'transfers':[]};s['strategy_hash']=lib.compute_strategy_hash(s)
    architect=module('skills/amazon-ad-strategy-architect/scripts/ad_strategy_lib.py','architect');valid=architect.validate_strategy(s)
    if not valid['valid']:raise ValueError('投放盘子检查失败：'+'；'.join(valid['errors']))
    review={'status':'approved_for_builder',**{key:s[key] for key in ['strategy_id','strategy_version','strategy_hash']},'decision':copy.deepcopy(decision),'reviewed_by':'user','reviewed_at':now()}
    defaults=lib.load_data(HOME/'skills/amazon-ad-campaign-builder/references/case_defaults.yaml');plan,_,_,diagnostics=lib.build_plan(s,package,defaults,strategy_review=review)
    hard=[x for x in diagnostics['blockers'] if x!='PRODUCTION_MISSING_SELLER_SKU' and not x.startswith('LAUNCH_BID_CONFLICT_REQUIRES_CHOICE:')]
    if not plan.get('campaigns'):hard.append('方案没有可执行活动')
    if hard:raise ValueError('原生方案检查未通过：'+'；'.join(hard))
    plan['keyword_package_source']={'path':package_path.relative_to(root).as_posix(),'sha256':file_sha(package_path),'package_ref':copy.deepcopy(plan['keyword_package_ref'])}
    plan['plan_hash']=lib.compute_plan_hash(plan)
    save(output_path(root,'strategy_brief'),s);save(output_path(root,'strategy_review'),review);save(output_path(root,'plan_yaml'),plan)
    save(output_path(root,'parameters_md'),'# 投放盘子\n\n'+decision['message']+'\n\n'+playbook['label']+'\n')
    save(output_path(root,'architecture_md'),'# 选词与出价\n\n'+ '\n'.join('- '+c['campaign_name']+'：日预算 '+str(c['daily_budget'])+'；固定竞价 '+str(c['bid_guardrail']['initial_bid']) for c in plan['campaigns'])+'\n\n仅采用现有词包许可与实际前台文案共同承接的词；未承接的词留在证据中。\n')
    save(output_path(root,'ad_signal_md'),'# 机会词\n\n'+opportunities['note']+'\n'+ '\n'.join('- '+r['term']+'：'+r['reason'] for r in opportunities.get('candidates') or []))
    report=generate_report(root,s,plan,index=index,risks=['缺第三方转化率，广告单赚亏待补'] if any(c['bid_guardrail']['expected_cvr'].get('value') is None for c in plan['campaigns']) else [])
    save(root/'S4_广告板块/_阶段总览/选词依据.yaml',{'package_ref':s['keyword_package_ref'],'copy_sha256':file_sha(root/'S2_文案板块/amazon-listing-copywriter/07B_Listing校验输入.json'),'excluded_uncovered_words':sorted(set(pending))})
    return {'next_step':5,'report':report,'plan_hash':plan['plan_hash'],'diagnostics':diagnostics}

def choose_playbook(root,marketplace):
    root=Path(root);overlay=root/'00_总控/starsky-fullchain-workflow/00C_data_intake_overlay.yaml'
    fallback={'label':'按通用母版 A：新品稳定出单','knowledge_refs':['KC-0113','KC-0114'],'profile':None}
    if not overlay.is_file():return fallback
    data=yaml.safe_load(overlay.read_bytes()) or {};proof=data.get('ad_playbook_confirmation') or {}
    try:
        source=Path(proof['source']);source=source if source.is_absolute() else safe(root,source)
        profile=safe(root,proof['profile']);doc=yaml.safe_load(profile.read_bytes());play=doc['ad_playbook']
        if proof.get('decision',{}).get('source')!='user' or not proof['decision'].get('message') or proof['source_sha256']!=file_sha(source) or proof['profile_sha256']!=file_sha(profile):return fallback
        state=yaml.safe_load((root/'workflow_state.yaml').read_bytes()) if (root/'workflow_state.yaml').is_file() else {}
        if play.get('status')!='validated' or doc.get('marketplace')!=marketplace or (state.get('category_id') and state['category_id']!=doc.get('category_id')):return fallback
        return {'label':'按类目打法卡：'+str(play['playbook_id']),'profile':doc,'source_sha256':proof['source_sha256'],'knowledge_refs':['KC-0113','KC-0114']}
    except (OSError,KeyError,TypeError,ValueError):return fallback


def import_playbook(root,source,decision):
    root=Path(root).resolve();source=Path(source).resolve()
    if decision.get('source')!='user' or not str(decision.get('message') or '').strip():raise ValueError('类目打法需本人确认资料及本次用途')
    overlay=safe(root,'00_总控/starsky-fullchain-workflow/00C_data_intake_overlay.yaml');data=yaml.safe_load(overlay.read_bytes()) or {}
    entries=(data.get('source_inventory') or {}).get('sources') or []
    if not any((root/str(r.get('path') or '')).resolve()==source for r in entries):raise ValueError('先经00C资料接入登记这份来源，再导入类目打法')
    doc=yaml.safe_load(source.read_bytes()) or {}
    if not isinstance(doc,dict):raise ValueError('先由现有category-adapter将原始资料整理成类目画像，再确认打法')
    play=doc.get('ad_playbook') or {}
    if not doc.get('category_id') or not doc.get('marketplace') or not play.get('playbook_id') or not play.get('sources'):raise ValueError('类目打法缺类目、站点或原资料出处')
    doc=copy.deepcopy(doc);doc['ad_playbook']['status']='validated'
    profile=overlay.parent/'category_profile.yaml';save(profile,doc)
    data['ad_playbook_confirmation']={'source':str(source),'source_sha256':file_sha(source),'profile':profile.relative_to(root).as_posix(),'profile_sha256':file_sha(profile),'decision':copy.deepcopy(decision),'confirmed_at':now()}
    save(overlay,data);return data['ad_playbook_confirmation']


def opportunity_candidates(root,candidates,receipts,available_sources=None):
    if not receipts:
        return {'note':'可用数据源尚未完成检索' if available_sources else '未补机会词：未接数据源','status':'needs_fetch' if available_sources else 'not_connected','candidates':[]}
    raw=[]
    for receipt in receipts:
        source=safe(root,receipt['file'])
        if receipt.get('ok') is not True or not receipt.get('task_id') or not receipt.get('server') or not receipt.get('tool') or receipt.get('sha256')!=file_sha(source):raise ValueError('机会词缺当前真实调用响应或来源已变化')
        raw.append(source.read_text(encoding='utf8').casefold())
    rows=[]
    for item in candidates:
        term=str(item.get('term') or '').strip()
        if not term or not any(term.casefold() in text for text in raw):raise ValueError('候选词不在已保存的数据源响应中')
        supported=False
        for ref in item.get('support') or []:
            if not isinstance(ref,dict) or not ref.get('path'):continue
            path=safe(root,ref['path'])
            if path.is_file() and term.casefold() in path.read_text(encoding='utf8').casefold():supported=True
        row={**copy.deepcopy(item),'status':'candidate','ad_eligible':supported,'ads_permission':'not_granted','reason':'已有图文承接，仍须本轮方案审核与当前词库许可' if supported else '图文尚不能承接，不能加入投放'}
        row.pop('market_bid',None)
        source=item.get('cpc_source') or {}
        if source:
            receipt=receipts[int(source['receipt'])];document=json.loads(safe(root,receipt['file']).read_text(encoding='utf-8-sig'));value=document
            for key in source['pointer'].lstrip('/').split('/'):
                key=key.replace('~1','/').replace('~0','~');value=value[int(key)] if isinstance(value,list) else value[key]
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:raise ValueError('当前数据源建议竞价无效')
            row['market_bid']=value
        rows.append(row)
    return {'status':'candidates_ready','note':'已按当前数据源补机会候选词，不自动授予投放许可','candidates':rows,'sources':copy.deepcopy(receipts)}


def write_opportunities(root,data):
    result=opportunity_candidates(root,data.get('candidates') or [],data.get('sources') or [],data.get('available_sources'))
    save(Path(root)/'S4_广告板块/_阶段总览/机会词来源.yaml',{'input_candidates':copy.deepcopy(data.get('candidates') or []),'sources':copy.deepcopy(data.get('sources') or []),'available_sources':copy.deepcopy(data.get('available_sources'))})
    save(output_path(root,'ad_signal_md'),'# 机会词\n\n'+result['note']+'\n'+ '\n'.join('- '+r['term']+'：'+r['reason'] for r in result.get('candidates') or []))
    return result


def upload_guide(product):
    return [f'先建广告组合：在广告后台手动新建，以产品或系列命名，例如“{product}”。',
        '上传 Bulk 表：广告后台 → 批量操作 → 上传表格。首次表格只含广告活动及其子项，不创建广告组合。',
        '核对结果：对照方案核对活动数、关键词数及成功/失败行；失败处理报告放回原投喂目录。',
        '把活动加进广告组合：勾选这次上传的活动，加入第1步建好的广告组合。',
        '广告组合用于归集产品；广告组是广告活动内部放商品和关键词的一层，二者不同。']


def generate_report(root,strategy,plan,index=None,risks=None):
    import html,shutil
    root=Path(root).resolve();lib=builder()
    validate_package_source(root,plan)
    if plan.get('plan_hash')!=lib.compute_plan_hash(plan):raise ValueError('方案已变化，先重算当前版本')
    if index is None:
        for blueprint in (strategy.get('architecture') or {}).get('campaign_blueprints') or []:
            source=(blueprint.get('bid_signal') or {}).get('source_path')
            candidate=safe(root,source).with_name('ad_keyword_index.json') if source else None
            if candidate and candidate.is_file():
                index=json.loads(candidate.read_text(encoding='utf8'))
                if any(str(index.get('source',{}).get(k))!=str(plan.get('keyword_package_ref',{}).get(k)) for k in ['package_id','package_version','package_hash']):raise ValueError('词库索引与当前方案不一致')
                break
    report_mod=module('skills/github-tabler-html-report/assets/salon_report.py','report');terms=module('scripts/ui_terms.py','terms')
    product=plan['product'].get('product_id') or '当前产品';route=strategy.get('launch_playbook') or choose_playbook(root,plan['market']['marketplace']);campaigns=plan['campaigns'];econ=plan.get('economics') or {}
    cap=(strategy.get('business_goal') or {}).get('guardrails',{}).get('max_post_ad_cumulative_loss_usd')
    cap=cap if cap is not None else (econ.get('exploration_override') or {}).get('max_loss')
    days=(strategy.get('business_goal') or {}).get('target_kpis',{}).get('test_days') or (strategy.get('launch_parameters') or {}).get('test_days')
    def value(v):return '暂无数据' if v is None else f'{float(v):.2f}'
    costs=[];traffic=[]
    words=(index or {}).get('words') or {};words=list(words.values()) if isinstance(words,dict) else words
    wordmap={str(w.get('keyword','')).casefold():w for w in words}
    metric=lambda x:'暂无数据' if x is None else str(x)
    margin=econ.get('contribution_margin_before_ads')
    # Without one combined margin, each variant's own contribution (e.g. L / XL) still answers "what does an order earn".
    by_variant={str(k):v for k,v in ((strategy.get('unit_economics') or {}).get('contribution_by_color') or {}).items() if isinstance(v,(int,float))}
    margins={'':margin} if margin is not None else by_variant
    for c in campaigns:
        g=c['bid_guardrail'];bid=g.get('initial_bid');cvr=(g.get('expected_cvr') or {}).get('value')
        if bid is not None and cvr:costs+=[(name,float(m)-float(bid)/float(cvr)) for name,m in margins.items()]
        for group in c['ad_groups']:
            for target in group.get('targets') or []:
                w=wordmap.get(str(target.get('expression','')).casefold(),{});support=target.get('copy_support') or {}
                planned=target.get('bid',bid);market=w.get('cpc')
                if str(target.get('state') or '').lower()=='paused':action='本轮暂停，不花钱'
                elif isinstance(planned,(int,float)) and isinstance(market,(int,float)) and planned<market:action='拟用价低于建议CPC，可能曝光少；第3天查曝光'
                else:action='固定竞价测数据，第3天查曝光'
                traffic.append([target.get('expression'),target.get('match_type'),support.get('listing_position','待核对实际文案'),support.get('image_position','待核对实际图位'),metric(w.get('sv')),metric(w.get('aba')),metric(market),metric(planned),metric(w.get('cvr')),action])
    worst=min(v for _,v in costs) if costs else None;loss_per_order=-worst if worst is not None and worst<0 else None
    orders=float(cap)/loss_per_order if cap is not None and loss_per_order else None
    conflict=any(c['bid_guardrail'].get('launch_decision_required') for c in campaigns) or bool(loss_per_order)
    model='上架前方案：商品创建好后填入 Seller SKU，即可出上传表。' if plan.get('launch_status')!='ready_for_bulk' else '已确认投放商品；请按上传指引手动操作。'
    report=report_mod.Report(str(product)+' · 新品广告方案',stage='S4',subtitle=model);md=['# '+str(product)+' · 新品广告方案','',model,'']
    def paragraph(identity,title,text):
        text=terms.rewrite(str(text));body='<p>'+html.escape(text)+'</p>'
        if identity in {'strategy','spend','profit'}:body='<div class="kpirow">'+body+'</div>'
        report.section(identity,title).raw(body);md.extend(['## '+title,'',text,''])
    summary='；'.join(str(c['campaign_name'])+'，日预算 '+value(c['daily_budget']) for c in campaigns)
    paragraph('strategy','打法',route['label']+'。'+str(len(campaigns))+'个活动；'+summary+'。投放初期使用固定竞价。')
    paragraph('spend','花多少','日预算 '+value(plan['totals']['daily_budget'])+'；测试 '+(str(days)+'天' if days else '天数待你确定')+'；累计亏损上限 '+value(cap)+'。达到任何一条止损线即停止并复核。')
    before=value(margin) if margin is not None else ('；'.join(name+' '+value(m) for name,m in by_variant.items()) or '暂无数据')
    paragraph('profit','每单赚亏','每单广告前利润 '+before+'；按起始出价和第三方预估转化率，每个广告单最保守约 '+value(worst)+'；亏损上限约能换 '+value(orders)+' 个广告单。是条件估算，非本账户实际业绩。')
    options={'accept_market_loss':'接受新品期亏损按建议价投，累计亏损封顶','improve_margin':'改善单件利润：核对券资格、去券或提价后重算','slow_low_bid':'低价慢跑：曝光更少，测试期可能收集不到足够数据'}
    picked=next((c['bid_guardrail'].get('launch_choice') for c in campaigns if c['bid_guardrail'].get('launch_choice')),None)
    if picked:choices=['已选：'+options.get(picked,picked)]
    elif any(c['bid_guardrail'].get('launch_decision_required') for c in campaigns):choices=list(options.values())
    else:choices=['无需拍板，可直接批准']+(['提醒：按当前出价测算，广告单可能亏损，累计亏损上限封顶'] if loss_per_order else [])
    report.section('decision','要你拍板').raw('<ol>'+''.join('<li>'+html.escape(x)+'</li>' for x in choices)+'</ol>');md+=['## 要你拍板','']+[str(i)+'. '+x for i,x in enumerate(choices,1)]+['']
    report.section('traffic','流量放大对照板').table(['词','匹配','文案位置','图/A+承接','周搜索量','ABA','建议CPC','拟用出价','第三方转化率','新品期动作'],traffic or [['暂无候选']+['暂无数据']*9]);md+=['## 流量放大对照板','']+['- '+terms.rewrite(str(row[0]))+'：'+str(row[1])+'；'+str(row[2])+'；'+str(row[3])+'；周搜索量 '+str(row[4])+'；建议CPC '+str(row[6])+'；拟用出价 '+str(row[7])+'；第三方转化率 '+str(row[8])+'；'+str(row[9]) for row in traffic]+['']
    user_cap=(strategy.get('launch_parameters') or {}).get('max_bid')
    def ceiling(g):
        top=g.get('user_bid_ceiling');start=g.get('initial_bid')
        if user_cap is not None:top=float(user_cap)
        if top is None or start is None or float(top)<=float(start)+1e-9:return '起始价已到上限，第3天无曝光不加价，回来复核'
        return ('本人最高出价 ' if user_cap is not None else '出价上限 ')+value(top)+'，第3天无曝光按15%阶梯加价至此（KC-0146）'
    bids='；'.join(str(c['campaign_name'])+'：起始 '+value(c['bid_guardrail'].get('initial_bid'))+'，'+ceiling(c['bid_guardrail']) for c in campaigns)
    dates=sorted({str(source.get('as_of') or (source.get('period') or {}).get('end') or '日期待核') for b in (strategy.get('architecture') or {}).get('campaign_blueprints') or [] for source in (b.get('bid_signal') or {}).get('as_of') or []})
    paragraph('bids','出价与止损',bids+'。起始参考建议竞价（KC-0053），固定竞价（KC-0105）。保本价只用于赚亏和止损，不作为起始出价。'+('' if user_cap is not None else '没有本人指定上限时按售价×目标ACoS×预估转化率估算。')+'词包数据日期：'+('、'.join(dates) or '待核')+'。')
    guide=upload_guide(product);paragraph('upload','上传步骤','\n'.join(str(i)+'. '+x for i,x in enumerate(guide[:4],1))+'\n'+guide[4])
    missing=list(risks or []);statefile=root/'workflow_state.yaml'
    state=yaml.safe_load(statefile.read_bytes()) if statefile.is_file() else {}
    for name,acceptance in (state.get('stage_acceptance') or {}).items():
        if name not in {'S1','S2','S3'}:continue
        if acceptance.get('status') not in {'accepted','accepted_with_real_data','accepted_with_deferred','conditional_complete'}:missing.append(name+'尚未完成阶段验收')
        if (acceptance.get('user_review') or {}).get('outcome')=='not_reviewed':missing.append(name+'本人选择先不验收')
        for row in acceptance.get('deferred_items') or []:missing.append(name+'待补：'+str(row.get('name') or row.get('step') or row))
    missing+=['尚未确认Seller SKU，不能出表'] if plan.get('launch_status')!='ready_for_bulk' else []
    missing=list(dict.fromkeys(missing))
    if missing:  # one line per gap, not one long sentence
        report.section('risks','风险与缺口').raw('<ul>'+''.join('<li>'+html.escape(terms.rewrite(str(x)))+'</li>' for x in missing)+'</ul>');md+=['## 风险与缺口','']+['- '+terms.rewrite(str(x)) for x in missing]+['']
    else:paragraph('risks','风险与缺口','当前无新增阻断；上传前仍核对可售状态和实际券资格。')
    refs={cid:file_sha(HOME/'mcp/ads-kb/kb/cards'/(cid+'.md')) for cid in ['KC-0053','KC-0105','KC-0113','KC-0114','KC-0146']}
    paragraph('evidence','证据与出处','知识卡：'+', '.join(refs)+'；词包版本 '+str(plan.get('keyword_package_ref',{}).get('package_version'))+'；具体指纹记录在本报告生成回执。')
    mdpath=output_path(root,'report_md');htpath=output_path(root,'report_html');content=terms.rewrite('\n'.join(md));page=terms.rewrite_html(report.render()).replace('</head>','<meta name="starsky-report" content="r04-s4"></head>')
    for path,text in [(mdpath,content),(htpath,page)]:
        if path.is_file() and path.read_text(encoding='utf8')!=text:
            history=mdpath.parent/'历史版本'/file_sha(path)[:16];history.mkdir(parents=True,exist_ok=True);shutil.copy2(path,history/path.name)
        save(path,text)
    bindings={}
    if plan.get('keyword_package_source'):
        source=plan['keyword_package_source'];bindings[source['path']]=source['sha256']
    for candidate in [root/'S2_文案板块/amazon-listing-copywriter/07B_Listing校验输入.json',root/'S4_广告板块/_阶段总览/机会词来源.yaml']:
        if candidate.is_file():bindings[candidate.relative_to(root).as_posix()]=file_sha(candidate)
    captured=root/'S4_广告板块/_阶段总览/机会词来源.yaml'
    if captured.is_file():
        for receipt in (yaml.safe_load(captured.read_bytes()) or {}).get('sources') or []:
            raw=safe(root,receipt['file'])
            if file_sha(raw)!=receipt['sha256']:raise ValueError('机会词来源已经变化，重新抓取并审核')
            bindings[raw.relative_to(root).as_posix()]=receipt['sha256']
    for blueprint in (strategy.get('architecture') or {}).get('campaign_blueprints') or []:
        source=(blueprint.get('bid_signal') or {}).get('source_path')
        if source:
            candidate=safe(root,source).with_name('ad_keyword_index.json')
            if candidate.is_file():bindings[candidate.relative_to(root).as_posix()]=file_sha(candidate)
    proof={'schema_version':'r04.s4.report','generator_sha256':file_sha(__file__),'plan_hash':plan['plan_hash'],'strategy_sha256':digest(strategy),'source_bindings':bindings,'local_knowledge':refs,'outputs':{str(p.relative_to(root)):file_sha(p) for p in [mdpath,htpath]}}
    proofpath=root/'S4_广告板块/_阶段总览/报告生成记录.yaml';save(proofpath,proof)
    return {'md':str(mdpath),'html':str(htpath),'proof':str(proofpath)}


def verify_report(root):
    root=Path(root);errors=[]
    try:
        proof=yaml.safe_load((root/'S4_广告板块/_阶段总览/报告生成记录.yaml').read_bytes());plan=yaml.safe_load(output_path(root,'plan_yaml').read_bytes());strategy=yaml.safe_load(output_path(root,'strategy_brief').read_bytes())
        validate_package_source(root,plan)
        if proof.get('generator_sha256')!=file_sha(__file__) or proof.get('plan_hash')!=builder().compute_plan_hash(plan) or proof.get('strategy_sha256')!=digest(strategy):errors.append('报告所依据的方案、盘子或生成规则已经变化')
        if any(file_sha(safe(root,p))!=h for p,h in proof['outputs'].items()):errors.append('报告内容已变化')
        if any(file_sha(safe(root,p))!=h for p,h in proof.get('source_bindings',{}).items()):errors.append('报告使用的文案、市场数据或词库索引已变化')
        if any(file_sha(HOME/'mcp/ads-kb/kb/cards'/(cid+'.md'))!=h for cid,h in proof['local_knowledge'].items()):errors.append('广告知识卡已变化')
    except (OSError,ValueError,KeyError,TypeError) as error:errors.append(str(error))
    return {'ok':not errors,'errors':errors}


def verify_report_file(path):
    path=Path(path).resolve();stage=next((p for p in path.parents if p.name=='S4_广告板块'),None)
    if stage is None:return {'ok':False,'errors':['报告不在当前产品S4目录']}
    root=stage.parent
    if path!=output_path(root,'report_html').resolve():return {'ok':False,'errors':['报告路径与路线图不一致']}
    checked=verify_report(root);content=path.read_text(encoding='utf8');wanted=['strategy','spend','profit','decision','traffic','bids','upload','risks','evidence']
    ids=re.findall(r'<section[^>]+id=[\"\']([^\"\']+)',content);ordered=[x for x in ids if x in wanted]
    if ordered!=wanted:checked['errors'].append('S4必需内容缺失或顺序错误')
    checked['ok']=not checked['errors'];return checked


def create_bulk(root,plan,review,context=None):
    """Use the bundled official template, existing exporter and independent native validator."""
    import shutil,subprocess,sys
    root=Path(root).resolve();proof=plan.get('sku_mapping_confirmation') or {}
    if plan.get('launch_status')!='ready_for_bulk' or not proof.get('confirmed_at'):raise ValueError('先填写并确认Seller SKU，未确认不出表')
    validate_mapping(plan,proof.get('rows') or [],proof.get('decision') or {})
    validate_package_source(root,plan,required=True)
    validate_approved_architecture(plan,review)
    if plan.get('plan_hash')!=builder().compute_plan_hash(plan) or review.get('plan_hash')!=plan['plan_hash']:raise ValueError('批准方案或SKU内容已经变化')
    for campaign in plan.get('campaigns') or []:
        for group in campaign.get('ad_groups') or []:
            for target in group.get('targets') or []:
                ref=target.get('copy_support') or {}
                if ref.get('path') and file_sha(safe(root,ref['path']))!=ref.get('sha256'):raise ValueError('方案批准后文案承接来源已变化，请重新审核方案')
    out=output_path(root,'bulk_xlsx');manifest=output_path(root,'bulk_manifest');audit_path=root/'S4_广告板块/_阶段总览/bulk_validation.yaml'
    old=yaml.safe_load(manifest.read_bytes()) if manifest.is_file() else {}
    prior_context=root/'S4_广告板块/_阶段总览/bulk_compile_context.yaml'
    same_context=context is None or (prior_context.is_file() and yaml.safe_load(prior_context.read_bytes())==context)
    if same_context and out.is_file() and audit_path.is_file() and (old.get('plan_ref') or {}).get('plan_hash')==plan['plan_hash']:
        audit=yaml.safe_load(audit_path.read_bytes())
        if (audit.get('bulk_file') or {}).get('sha256')=='sha256:'+file_sha(out) and audit.get('uploadability',{}).get('status')=='reviewed_candidate_upload_prepared':return {'xlsx':str(out),'manifest':str(manifest),'validation':str(audit_path)}
    for path in [out,manifest]:
        if path.exists():
            history=root/'S4_广告板块/_阶段总览/历史版本'/file_sha(path)[:16];history.mkdir(parents=True,exist_ok=True)
            target=history/path.name
            if target.exists():
                if file_sha(target)!=file_sha(path):raise ValueError('历史版本冲突，保留当前文件')
                path.unlink()
            else:path.rename(target)
    save(output_path(root,'plan_yaml'),plan)
    context=dict(context or {'start_date':first_start_date(plan['market']['marketplace']),'default_state':'paused'})
    if context.get('default_state')!='paused' and context.get('decision',{}).get('source')!='user':raise ValueError('未明确要求启用时，上传表默认暂停，先核对后手动启用')
    if context.get('default_state')!='paused' and plan.get('ad_product_decisions',{}).get('SP',{}).get('capability',{}).get('status')!='available':raise ValueError('商品投放资格未核对，上架前方案只能出暂停状态表')
    context_path=root/'S4_广告板块/_阶段总览/bulk_compile_context.yaml';save(context_path,context)
    review=copy.deepcopy(review);review.update(approved_compile_context_sha256='sha256:'+file_sha(context_path),compile_context_basis=copy.deepcopy(proof['decision']))
    review_path=output_path(root,'architecture_review');save(review_path,review)
    template=HOME/'skills/amazon-bulk-upload/AmazonAdvertisingBulksheetSellerTemplate.xlsx';target=output_path(root,'bulk_template');target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(template,target)
    ref=yaml.safe_load((HOME/'skills/amazon-bulk-upload/templates/template_ref.bundled_official.example.yaml').read_bytes());ref.update(marketplace=plan['market']['marketplace'],file_sha256='sha256:'+file_sha(target));save(output_path(root,'bulk_template_ref'),ref)
    def run(relative,args,logname):
        log=root/'S4_广告板块/_阶段总览'/logname
        with log.open('wb') as stream:result=subprocess.run([sys.executable,'-B',str(HOME/relative),*map(str,args)],cwd=root,stdout=stream,stderr=subprocess.STDOUT)
        return result.returncode,log
    common=['--plan',output_path(root,'plan_yaml'),'--review-gate',review_path,'--template',target,'--template-ref',output_path(root,'bulk_template_ref'),'--compile-context',context_path]
    code,log=run('skills/amazon-bulk-upload/scripts/generate_bulk.py',common+['--output',out,'--manifest',manifest,'--mode','production','--ad-product','SP'],'bulk_generate.log')
    if code:raise ValueError('原生出表检查未通过，详见 '+str(log))
    validator='skills/amazon-bulk-upload-troubleshooter/scripts/validate_bulk.py';first=output_path(root,'approved_validation')
    check_args=common+['--bulk',out,'--manifest',manifest]
    _,log=run(validator,check_args+['--output',first],'bulk_first_validation.log')
    first_doc=yaml.safe_load(first.read_bytes()) if first.is_file() else {}
    if first_doc.get('technical_status')!='passed' or first_doc.get('hard_blocks'):raise ValueError('技术校验未通过，详见 '+str(log))
    native_manifest=yaml.safe_load(manifest.read_bytes());release={'schema_version':'1.0','run_id':plan['run_id'],'cycle_id':plan['cycle_id'],'run_mode':'production',
        'plan':{k:plan[k] for k in ['plan_id','plan_version','plan_hash']},'bulk':{'bulk_id':native_manifest.get('bulk_id'),'bulk_version':native_manifest.get('bulk_version'),'bulk_hash':'sha256:'+file_sha(out)},
        'validation':{'status':'pass','validation_hash':'sha256:'+file_sha(first)},'status':'approved_for_external_upload','reviewed_by':'user_approved_plan_and_sku_completion','reviewed_at':proof['confirmed_at'],
        'checks':{k:'pass' for k in ['real_child_sku_asin','marketplace_account_scope','current_official_template','budget_and_profit_authorized','plan_bulk_counts_match','unresolved_high_risk_actions']},
        'external_action_authorized':False,'authorization_basis':{'plan':review.get('decision'),'sku':proof['decision']},'asin_requirement':'optional_seller_sku_required'}
    release_path=output_path(root,'bulk_release_review');save(release_path,release)
    _,log=run(validator,check_args+['--output',audit_path,'--bulk-release-review',release_path,'--approved-validation',first],'bulk_final_validation.log')
    final=yaml.safe_load(audit_path.read_bytes()) if audit_path.is_file() else {}
    if final.get('technical_status')!='passed' or final.get('uploadability',{}).get('status')!='reviewed_candidate_upload_prepared':raise ValueError('正式出表放行检查未通过，详见 '+str(log))
    strategy_path=output_path(root,'strategy_brief')
    if strategy_path.is_file():generate_report(root,yaml.safe_load(strategy_path.read_bytes()),plan)
    guide_rows=upload_guide(plan['product']['product_id']);guide='\n'.join(str(i)+'. '+x for i,x in enumerate(guide_rows[:4],1))+'\n\n'+guide_rows[4];save(output_path(root,'closing_md'),'# 上传四步指引\n\n'+guide+'\n\n上传表默认暂停；核对可售状态、预算和匹配后在后台手动启用。上线后批量表放入 S5_运营板块/_投喂_广告报表/，按既有S5入口复盘。\n')
    save(output_path(root,'sku_mapping'),'# Seller SKU 对应关系\n\n'+'\n'.join('- '+r['variant']+'：'+r.get('seller_sku','')+'；'+('投' if r['advertise'] else '不投') for r in proof['rows'])+'\n\n本人已逐行确认；ASIN可选。\n')
    return {'xlsx':str(out),'manifest':str(manifest),'validation':str(audit_path),'compile_context':str(context_path)}


def intake(root,strategy,package_path):
    root=Path(root).resolve();package_path=Path(package_path).resolve();package_path.relative_to(root)
    indexer=module('skills/_keyword_system_contract/scripts/keyword_index.py','index');index=indexer.ensure_index(package_path,force=True)
    paths=[package_path,root/'S2_文案板块/amazon-listing-copywriter/07B_Listing校验输入.json']
    paths+=sorted((root/'S3_视觉板块').glob('**/visual_suite.yaml'))
    overlay=root/'00_总控/starsky-fullchain-workflow/00C_data_intake_overlay.yaml'
    if overlay.is_file():paths.append(overlay)
    inventory=[{'path':p.relative_to(root).as_posix(),'sha256':file_sha(p)} for p in paths if p.is_file()]
    save(output_path(root,'intake_md'),'# 上游接入\n\n'+ '\n'.join('- '+r['path'] for r in inventory)+'\n\n只读当前词库索引、最终文案、视觉清单和已登记类目资料；缺项保留，不视为已验收。\n')
    playbook=choose_playbook(root,strategy['market']['marketplace']);p=strategy['product'];e=strategy['unit_economics']
    traffic=strategy.get('category_traffic') or {};six=(strategy.get('product_classification') or {}).get('six_questions') or {}
    answers=[('流量入口：集中大词还是分散属性/场景词',six.get('traffic_entries') or traffic.get('concentration')),
        ('流量断层：头部到中腰部是否陡降',six.get('traffic_gap') or traffic.get('head_tail_gap')),
        ('品牌/ASIN 垄断：头部点击和转化是否集中',six.get('brand_monopoly') or traffic.get('brand_asin_monopoly')),
        ('关键词单调性：词排名与类目排名是否协同',six.get('keyword_monotonicity') or traffic.get('keyword_monotonicity')),
        ('标品还是非标品：规格功能还是款式场景决策',six.get('product_type') or ({'standard':'偏规格功能决策','nonstandard':'偏款式场景决策','semi_standard':'半标品，需结合真实证据细分'}.get(p.get('form')) if p.get('form_evidence') else None)),
        ('季节、价格战、CPC、评价、库存风险',six.get('category_risks') or traffic.get('category_risks'))]
    def answer(value):
        if value is None or value=='':return '资料待补，不做已验证判断'
        if isinstance(value,dict):return str(value.get('summary') or value.get('answer') or '已有资料，需核对原来源')
        if isinstance(value,list):return '；'.join(str(v.get('summary') or v.get('answer') or '见原资料') if isinstance(v,dict) else str(v) for v in value)
        return str(value)
    sources='；'.join(str(r.get('source_id') or r.get('path') or '原产品判断资料') for r in p.get('form_evidence') or [] if isinstance(r,dict))
    save(output_path(root,'classification_md'),'# 产品判断与打法\n\n'+playbook['label']+'\n\n'+ '\n'.join('- '+name+'：'+answer(value) for name,value in answers)+'\n\n方法依据：本地广告知识卡 KC-0113（B级经验方法）、KC-0114；方法不等于本品已验证事实。缺项按通用母版保守准备，不新增用户停顿。\n\n事实出处：'+(sources or '见上游接入清单；未提供的六问证据保留待补')+'。\n')
    save(root/'S4_广告板块/_阶段总览/上游来源.yaml',{'sources':inventory,'index_source':index['source'],'knowledge_cards':{cid:file_sha(HOME/'mcp/ads-kb/kb/cards'/(cid+'.md')) for cid in ['KC-0113','KC-0114']}})
    return {'intake':str(output_path(root,'intake_md')),'classification':str(output_path(root,'classification_md'))}


REQUIRED_FLAGS={'intake':['strategy','package'],'prepare':['strategy','package','parameters','decision'],'import-playbook':['source','decision'],
    'opportunities':['source'],'approve':['decision'],'sku':['mapping','decision']}


def require_flags(action,args):
    missing=['--'+flag for flag in REQUIRED_FLAGS.get(action,[]) if getattr(args,flag) is None]
    if missing:
        example=' '.join('--'+flag+' <'+flag+'文件>' for flag in REQUIRED_FLAGS[action])
        raise ValueError(f'{action} 缺参数 {"、".join(missing)}；完整写法：s4_launch.py {action} <产品根> {example}')


def load_rows(path):
    """SKU mapping rows: a bare list or {"rows": [...]}; the confirmation hash is over the rows."""
    text=Path(path).read_text(encoding='utf-8-sig')
    data=json.loads(text) if text.lstrip().startswith(('{','[')) else yaml.safe_load(text)
    rows=data.get('rows') if isinstance(data,dict) else data
    if not isinstance(rows,list) or not all(isinstance(r,dict) for r in rows):
        raise ValueError('SKU 映射文件要么是行列表 [{"variant":…,"advertise":true,"seller_sku":…}]，要么是 {"rows":[同样的行]}')
    return rows


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['intake','prepare','report','approve','sku','import-playbook','opportunities']);parser.add_argument('root',type=Path)
    for flag in ['strategy','package','parameters','decision','mapping','source']:parser.add_argument('--'+flag,type=Path)
    args=parser.parse_args();require_flags(args.action,args);root=args.root.resolve();load=lambda p:builder().load_data(p)
    if args.action=='intake':result=intake(root,load(args.strategy),args.package)
    elif args.action=='prepare':result=prepare_launch(root,load(args.strategy),args.package,load(args.parameters),load(args.decision))
    elif args.action=='import-playbook':result=import_playbook(root,args.source,load(args.decision))
    elif args.action=='opportunities':result=write_opportunities(root,load(args.source))
    else:
        plan=load(output_path(root,'plan_yaml'));strategy=load(output_path(root,'strategy_brief'))
        if args.action=='report':result=generate_report(root,strategy,plan)
        elif args.action=='approve':
            plan,review=approve_plan(plan,load(args.decision),root=root);save(output_path(root,'plan_yaml'),plan);save(output_path(root,'architecture_review'),review);result=generate_report(root,strategy,plan)
        else:
            plan,review,_=bind_skus(plan,load(output_path(root,'architecture_review')),load_rows(args.mapping),load(args.decision),root=root);save(output_path(root,'plan_yaml'),plan);save(output_path(root,'architecture_review'),review);result=create_bulk(root,plan,review)
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except (ValueError,KeyError,TypeError,OSError) as error:print('还差一步：'+str(error),file=sys.stderr);raise SystemExit(2)
