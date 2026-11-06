"""Structured S1 reader report. Calculations belong to the native cost engine."""
from __future__ import annotations
import hashlib
import html
import os
from pathlib import Path
import re
import base64
import importlib.util

CHAPTERS=('市场分析与机会','产品与货源','IP 与合规','推广难度','成本与定价','风险与翻转条件','采购落地清单')
MARKET_SECTIONS=('市场容量','为什么是这个站点','需求与痛点','竞争格局','切入点与机会')
LIGHTS={'green':'🟢','yellow':'🟡','orange':'🟡','red':'🔴','gray':'⚪','pending':'⚪'}
IP_LABELS={'trademark':'商标 / 品牌词','design':'外观专利','utility':'发明专利','image':'图片 / 素材权属','compliance':'合规声明'}
_NATIVE_MODULES={}


def native_display_models(home,ct,product,ruler):
    """Use native CT/IP normalizers; old evidence bytes are never rewritten."""
    def module(relative,name):
        path=Path(home)/relative;key=(str(path),hashlib.sha256(path.read_bytes()).hexdigest())
        if key not in _NATIVE_MODULES:
            spec=importlib.util.spec_from_file_location(name,path)
            result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
            _NATIVE_MODULES[key]=result
        return _NATIVE_MODULES[key]
    ct_module=module('skills/1688-product-detail-scraper/scripts/product_ct.py','r01_report_native_ct')
    ip_module=module('skills/amazon-ip-risk-assessment/scripts/compliance_ruler.py','r01_report_native_ip')
    projected=ct_module.from_product_facts(product,ct)
    if not ruler.get('summary_card') and ruler:
        ruler=dict(ruler,summary_card=ip_module.summary_card_from_state(ruler))
    return projected,ruler


def value(item):
    return item.get('value') if isinstance(item,dict) and 'value' in item else item


def cell(item):
    item=value(item)
    if item is None or item=='':return '—'
    if isinstance(item,str) and item in {'null','None','missing','data_gap'}:return '—'
    if isinstance(item,bool):return '是' if item else '否'
    if isinstance(item,(int,float)):
        return str(int(item)) if item == int(item) else format(item,'.2f')
    if isinstance(item,dict):return '；'.join(str(k)+'：'+cell(v) for k,v in item.items() if k not in ('raw','image_data','bytes_base64'))
    # Items often end with '。'; joining them with '；' printed '。；' between every reason.
    if isinstance(item,(list,tuple)):return '；'.join(cell(x).rstrip('。；') if len(item)>1 else cell(x) for x in item)
    return str(item).replace('|','／').replace('\n',' ')


def table(headers,rows,limit=10):
    rows=list(rows)
    if not rows:return '—\n'
    text='\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join('---' for _ in headers)+' |']+
        ['| '+' | '.join(cell(v) for v in row)+' |' for row in rows[:limit]])+'\n'
    if len(rows)>limit:text+='其余 '+str(len(rows)-limit)+' 项保留在结构化来源与阶段回执中。\n'
    return text


def quality_issues(text):
    """Compare paragraph and hierarchical section bodies; retain original labels."""
    heads=list(re.finditer(r'^(#{1,6})[ \t]+(.+)$',text,re.M))
    seen={};issues=[]
    for i,head in enumerate(heads):
        end=next((h.start() for h in heads[i+1:] if len(h[1])<=len(head[1])),len(text))
        raw=text[head.end():end];body=raw.strip();start=head.end()+len(raw)-len(raw.lstrip())
        candidates=[(body,start)]
        # Parent sections include children for A14/A16 subtree comparison, but
        # their paragraphs are not a second occurrence of the child's text.
        children=any(h.start()<end for h in heads[i+1:i+2])
        if not children:
            position=start
            for index,part in enumerate(re.split(r'(\n\s*\n)',body)):
                if index%2==0:candidates.append((part,position))
                position+=len(part)
        for paragraph,position in candidates:
            normalized=re.sub(r'\s+','',paragraph)
            if len(normalized)<200:continue
            digest=hashlib.sha256(normalized.encode()).hexdigest()
            prior=seen.get(digest)
            if prior and prior[1]!=position:
                message='重复段落（≥200字）：'+prior[0]+' = '+head[2]
                if message not in issues:issues.append(message)
            else:seen[digest]=(head[2],position)
    labels=[]
    pattern=r'(?m)^\s*(?:\*\*)?(?:唯一结论[：:]\s*|经济性[：:]\s*)?(观望，暂不投入|不建议直接投入|数据不足，禁止决策|有条件测试候选|建议投入|有条件做|不做|做)(?=[。；：:\s*（]|$)'
    for match in re.finditer(pattern,text):
        if match[1] not in labels:labels.append(match[1])
    if len(labels)>1:issues.append('结论冲突：'+' / '.join(labels))
    return issues


def required_field_issues(text,kind='stage'):
    required={
        'stage':([(f'M{n:02}',rf'^## M{n:02}\s') for n in range(19)] if re.search(r'^## M00\s',text,re.M) else [(x,re.escape(x)) for x in CHAPTERS])+[(x,r'^###\s+'+re.escape(x)) for x in MARKET_SECTIONS]+[('唯一结论',r'唯一结论[：:]\s*\S+')],
        'market':[(x,re.escape(x)) for x in MARKET_SECTIONS],
        'keyword':[('关键词','关键词|keyword'),('搜索量','搜索量|search.volume'),('相关性','相关|relevance'),('来源','来源|source')],
        'listing':[('标题','标题|title'),('五点','五点|bullet'),('描述','描述|description'),('搜索词','搜索词|search.term')],
        'ad':[('预算','预算|budget'),('架构','架构|campaign'),('竞价','竞价|bid'),('审核','审核|approval')]}
    issues=['缺必备字段：'+label for label,pattern in required.get(kind,[]) if not re.search(pattern,text,re.M|re.I)]
    # R05 owner clarified that full analysis outranks the old 400-line cap.
    # Required fields, duplicate sections and contradictory verdicts still gate.
    return issues


def inspect_html(text):
    from bs4 import BeautifulSoup
    soup=BeautifulSoup(text,'html.parser');issues=[]
    front=soup.select('section[data-report-version="2"]')
    if len(front)!=1:return ['新首屏缺失或重复']
    front=front[0]
    sections=soup.select('#report-content > section')
    if not sections or sections[0] is not front:issues.append('产品与结论首屏必须是正文第一个section')
    decisions=soup.select('[data-decision]')
    decision=decisions[0] if len(decisions)==1 else None
    labels={'invest':'做','conditional':'有条件做','hold':'暂缓（Hold）','reject':'不做'}
    if decision is None or decision not in front.descendants or decision.get('data-decision') not in labels:
        issues.append('缺唯一明确判定；存在缺失、重复或冲突结论')
    elif decision.get_text(strip=True)!='唯一结论：'+decision.get('data-decision-label', labels[decision['data-decision']]):
        issues.append('唯一结论文字与结构化判定冲突')
    if len(re.findall(r'唯一结论\s*[：:]',soup.get_text(' ',strip=True)))!=1:
        issues.append('全页唯一结论缺失或重复')
    for node in front.find_all(True):
        style=re.sub(r'\s+','',node.get('style','')).lower()
        if node.has_attr('hidden') or 'display:none' in style or 'visibility:hidden' in style:issues.append('首屏关键内容被隐藏');break
    if len(front.select('.m2-answer'))!=4:issues.append('缺四张决策答案卡')
    if len(front.select('.m2-actions li'))!=3:issues.append('缺你要做的3件事')
    if len(front.select('.m2-ct dt'))<10:issues.append('CT关键字段不全')
    if front.select_one('img') is None and front.select_one('.m2-image.missing') is None:issues.append('缺当前产品主图或具体图片缺失说明')
    keys = tuple(f'M{n:02}' for n in range(19)) if soup.find(id='M00') else ('market','product','ip','promotion','cost','risk','purchase')
    for key in keys:
        if soup.find(id=key) is None:issues.append('缺正文模块：'+key)
    appendix=soup.select_one('.m2-appendix')
    if not soup.find(id='M00') and (appendix is None or appendix.has_attr('open')):issues.append('附录必须默认折叠')
    if '<meta charset="utf-8">' not in text.splitlines()[0]:issues.append('首行缺UTF-8声明')
    return issues


def _missing(label,reason='本轮结构化来源尚未提供'):
    return label+'：—（补充方法见文末待补清单）。'


def _money(x):
    return '$'+format(x,'.2f') if isinstance(x,(int,float)) and not isinstance(x,bool) else '未取得'


def _pct(x):
    return format(x,'.1%') if isinstance(x,(int,float)) and not isinstance(x,bool) else '未取得'


def decision_summary(decision,acceptance):
    blockers=list(dict.fromkeys(str(x) for x in (decision.get('blockers') or [])))
    state=decision.get('decision')
    final = decision.get('final_verdict')
    code=('hold' if str(final).lower() in ('hold','暂缓（hold）') or state in ('hold','research_pending','watch') else
          'reject' if state in ('stop','abort','reject') else 'invest' if state in ('go','invest') and not blockers else 'conditional')
    accepted=acceptance.get('accepted') is True
    return {'code':code,'label':'暂缓（Hold）' if code=='hold' else final or {'reject':'不做','invest':'做','conditional':'有条件做'}[code],
        'conditions':blockers or (['尚未形成决策；补齐对应证据后复核，当前不新增投入。'] if state is None else []),
        'accepted':accepted,'pending_count':acceptance.get('count'),'condition_count':len(blockers),
        'issues':list(acceptance.get('issues') or []),'purchase_authorized':decision.get('purchase_authorized') is True}


def decision_headline(decision):
    """Expose the existing classification without replacing the business verdict."""
    canonical={'reject':'不做','invest':'做','conditional':'有条件做','hold':'暂缓（Hold）'}[decision['code']]
    label=decision['label']
    if label==canonical:return label
    boundary='当前条件未满足：' if decision['code']=='conditional' else '原结论：'
    return canonical+'（'+boundary+label+'）'


def unique_review_quotes(text,seen=None):
    """Retain long verbatim evidence once and link repeated uses to that quote."""
    if seen is None:seen={}
    def replace(match):
        quote=match[0];key=re.sub(r'\s+','',quote)
        if len(key)<200:return quote
        if key in seen:return '[同条原话，见引用'+str(seen[key])+'](#review-quote-'+str(seen[key])+')'
        index=len(seen)+1;seen[key]=index
        return '<a id="review-quote-'+str(index)+'"></a>\n\n'+quote
    return re.sub(r'^> 原话：[^\n]+',replace,text,flags=re.M)


def _market(facts,inputs,narrative=None,link='../amazon-market-research/01_市场分析报告.md'):
    """Stage-report summary only (R09): the full analysis lives in the market report."""
    narrative=narrative or {};overview=facts.get('market_overview') or {};summary=narrative.get('summary') or {}
    readings=narrative.get('readings') or {};category=overview.get('category') or {};trend=overview.get('category_trend') or {}
    stats=((overview.get('pool') or {}).get('stats')) or {};size=category.get('size') or {};conc=category.get('concentration') or {}
    keywords=overview.get('keywords') or [];core=keywords[0] if keywords else {}
    from s1_market_report import num,money,pct,signed,PENDING
    more=f'完整分析见[市场分析报告](<{link}>)。'
    capacity=[]
    if size.get('top100_units'):capacity.append(f"类目 Top100 月销 {num(size['top100_units'])} 件、月销售额 {money(size.get('top100_revenue'))}")
    if stats.get('units'):capacity.append(f"同款型池 {num(stats.get('skus'))} 个 SKU 月销 {num(stats['units'])} 件")
    if trend.get('ytd_yoy') is not None:capacity.append(f"类目今年同比 {signed(trend['ytd_yoy'])}")
    if not capacity and facts.get('monthly_sales_total'):capacity.append(f"同款样本月销 {num(facts['monthly_sales_total'])} 件")
    demand=[]
    if core.get('monthly_searches'):demand.append(f"核心词 {core['keyword']} 月搜索 {num(core['monthly_searches'])}"+(f"，今年同比 {signed(core['ytd_yoy'])}" if core.get('ytd_yoy') is not None else ''))
    pains=narrative.get('pains') or []
    if pains:demand.append('主要痛点：'+'、'.join(str(p.get('theme')) for p in pains[:3] if p.get('theme')))
    competition=[]
    if conc.get('top3_product') is not None:competition.append(f"类目 Top3 商品销量占比 {pct(conc['top3_product'],False)}，亚马逊自营 {pct(conc.get('amazon_owned'),False)}")
    if stats.get('top3_share') is not None:competition.append(f"同款型池 Top3 占比 {pct(stats['top3_share'])}，评论中位数 {num(stats.get('review_median'))}")
    q=stats.get('price_quantiles') or facts.get('price_bands') or {}
    if q.get('p50'):competition.append(f"价格中位 {money(q['p50'])}（P25 {money(q.get('p25'))}，P75 {money(q.get('p75'))}）")
    opportunity=[x for x in (summary.get('breakthrough'),narrative.get('pricing')) if x]
    bodies=['；'.join(capacity) or PENDING,
            summary.get('site_reason') or PENDING,
            '；'.join(demand) or PENDING,
            '；'.join(competition) or PENDING,
            '；'.join(str(x) for x in opportunity) or PENDING]
    extra=[readings.get('scale') or readings.get('trend'),None,readings.get('voc'),readings.get('concentration') or readings.get('head'),None]
    return [{'title':name,'body':cell(body)+('。'+str(note) if note else '')+'\n\n'+more} for name,body,note in zip(MARKET_SECTIONS,bodies,extra)]


def market_report(facts, inputs, *, compact_quotes=True, root=None, narrative=None):
    """R09 market report: script numbers + AI narrative (s1_market_report)."""
    import s1_market_report
    if root is not None:
        return s1_market_report.render(root,facts,inputs,narrative)[0]
    return s1_market_report.render_from_facts(facts,inputs,narrative)


def _ip(ruler):
    cards=ruler.get('summary_card') or []
    if isinstance(cards,dict):cards=[dict(v,id=k) for k,v in cards.items()]
    byid={r.get('id'):r for r in cards}
    # Legacy final searches are evidence, not evidence of clearance.
    aliases={'trademark':'uspto_trademark','design':'design_patent','utility':'utility_patent','image':'image_reverse'}
    searches={r.get('id'):r for r in ruler.get('searches') or []}
    rows=[]
    for key,label in IP_LABELS.items():
        item=byid.get(key)
        if item:rows.append((label,LIGHTS.get(item.get('light'),'⚪'),item.get('conclusion'),item.get('needs_user')))
        else:
            legacy=searches.get(aliases.get(key)) or {}
            rows.append((label,'⚪',legacy.get('result') or _missing('最终结论卡',legacy.get('reason') or '最终状态未提供'),legacy.get('reason') or '补充最终核查状态'))
    return rows


def _ct_rows(ct,product):
    real=product.get('real_facts') or {}
    material=ct.get('material') or {}
    def known(item):return value(item) not in (None,'',[],{})
    def pick(item,*alternatives):return next((x for x in (item,)+alternatives if known(x)),None)
    return [('材质',pick(material.get('main_cn'),real.get('material'))),('尺寸（cm）',pick(ct.get('dimensions') if ct.get('actual_supplier_selected') is not False else None,real.get('dimensions_cm'))),
        ('重量（g）',pick(ct.get('weight'),real.get('weight_g'))),('单包（cm）',ct.get('package_dimensions_cm') or real.get('packaged_dimensions_cm') or real.get('package_dimensions_cm')),
        ('含包装重量',ct.get('package_gross_weight_kg') or real.get('package_gross_weight_kg')),
        ('装箱数',ct.get('carton_qty') or real.get('carton_units') or real.get('carton_qty')),('外箱尺寸（cm）',ct.get('carton_dimensions_cm') or real.get('carton_dimensions_cm')),
        ('整箱毛重（kg）',ct.get('carton_gross_weight_kg') or real.get('carton_gross_kg') or real.get('carton_gross_weight_kg')),
        ('采购价（CNY）',ct.get('price') or real.get('price_cny') or real.get('purchase_price_cny')),('MOQ',ct.get('moq') or real.get('moq')),
        ('自检',ct.get('self_checks'))]


def legacy_cost_view(cost):
    """Display existing conditional numbers verbatim; never recalculate old models."""
    scenarios=[]
    for scenario in cost.get('scenarios') or []:
        estimates=[scenario] if scenario.get('status')=='complete' else scenario.get('conditional_cases') or [scenario.get('conditional_estimate') or {}]
        for estimate in estimates:
            scenarios.append({'label':str(scenario.get('label') or scenario.get('id') or '')+' / '+str(estimate.get('case_label') or '旧费用口径'),
                'price':scenario.get('price'),'pre_ad_profit':estimate.get('contribution'),'pre_ad_margin':estimate.get('gross_margin'),'post_ad_margin':None,
                'landed_cost':estimate.get('landed_cost'),'fees':estimate.get('fees') or {},'cpc_rows':estimate.get('cpc_by_cvr') or [],'pressure':estimate.get('ad_pressure') or {}})
    first=scenarios[0] if scenarios else {}
    source='05_cost_model.yaml；旧引擎条件情景，不是已核验实际费用'
    keywords=first.get('pressure',{}).get('keywords') or cost.get('keywords') or []
    cpc=next((r.get('break_even_cpc') for r in first.get('cpc_rows') or [] if r.get('cvr')==cost.get('reference_cvr')),None)
    promotion={'keywords':[dict(r,price=first.get('price'),break_even_cpc=cpc) for r in keywords if isinstance(r,dict)],'conclusion':first.get('pressure',{}).get('label')}
    from s1_cost_engine import FIELD_LABELS,STATUS_LABELS
    labels={**FIELD_LABELS,'referral':'佣金','inbound_placement':'入库配置费',
            'return_loss':'退货损失','total':'费用合计','fba_status':'FBA费用依据'}
    statuses={**STATUS_LABELS,'table_estimate':'费表估算','conditional':'条件估算',
              'user_provided':'用户填写','unavailable':'未核验','unknown':'待核'}
    fees=[]
    for key,val in first.get('fees',{}).items():
        is_status=key.endswith('_status')
        fees.append({'label':labels.get(key,key),'value':statuses.get(val,val) if is_status else val,
                     'unit':'状态' if is_status else 'USD/件','source':source,'status':'conditional'})
    return {'result':{'landed_cost':first.get('landed_cost'),'pre_ad_margin':first.get('pre_ad_margin')},
        'warnings':[source],'blocks':{
            'landed':{'rows':[{'label':x.get('item'),'value':x.get('cny'),'unit':'CNY/件','source':source,'status':'conditional'} for x in (cost.get('landed') or {}).get('parts_cny') or []]},
            'amazon':{'rows':fees},
            'price_ladder':{'scenarios':scenarios}},'promotion':promotion}


def compose(model,*,inputs,ct,product,ruler,cost,checklist,acceptance):
    facts=model.get('facts') or {};decision=decision_summary(model.get('decision') or {},acceptance)
    native=cost.get('unified_cost') or legacy_cost_view(cost);result=native.get('result') or {};promotion=native.get('promotion') or {}
    narrative={}
    if model.get('root'):
        from s1_market_overview import load_narrative
        narrative=load_narrative(model['root'])
    market=_market(facts,inputs,narrative);ip=_ip(ruler);ctrows=_ct_rows(ct,product)
    selected=product.get('selected_offer') or ct.get('selected_offer') or {}
    offer=selected.get('url') if isinstance(selected,dict) else 'https://detail.1688.com/offer/'+selected+'.html' if isinstance(selected,str) and selected.isdigit() else None
    offerid=selected.get('offer_id') if isinstance(selected,dict) else selected
    competitors=facts.get('representatives') or facts.get('competitors') or []
    chapter2=table(['CT 字段','当前值','来源 / 状态'],[(k,v,(v.get('source') or v.get('source_status') or v.get('status')) if isinstance(v,dict) else None) for k,v in ctrows if k!='自检'],20)
    checks=ct.get('self_checks') or []
    check_status={'pass':'通过','fail':'异常','warning':'待核','pending':'待核','missing':'待补','unknown':'待核','unavailable':'未核验'}
    chapter2+='\n包装与箱规自检：\n\n'+table(['项目','状态','说明'],[(index,check_status.get(item.get('status'),'待核'),item.get('message') or '缺少中文自检说明，需复核原始记录') for index,item in enumerate(checks,1) if isinstance(item,dict)],20)
    chapter2+='\n1688 链接：'+(offer if offer and re.match(r'https://detail\.1688\.com/offer/\d+\.html$',offer) else '缺失：尚未绑定真实所选货源链接')+'\n\n'
    chapter2+=table(['货源','SKU / 形态','页面价','依据'],[(r.get('supplier_name') or r.get('company') or r.get('offer_id'),r.get('sku_name') or r.get('shape'),r.get('page_price'),r.get('reason')) for r in facts.get('supplier_shortlist') or []],5)
    chapter2+='\n厂家确认清单：\n'+('\n'.join('- '+cell(x) for x in (ct.get('supplier_questions') or ct.get('manufacturer_questions') or ct.get('confirmation_questions') or [])[:8]) or _missing('厂家确认问题'))
    promos=promotion.get('keywords') or []
    promotion_body=table(['关键词','参考 CPC','对应售价','保本 CPC','所需转化率','判断','来源'],[(r.get('keyword'),_money(r.get('cpc')),_money(r.get('price')),_money(r.get('break_even_cpc')),_pct(r.get('required_cvr')),r.get('reason') or r.get('level'),r.get('source')) for r in promos])
    barriers=promotion.get('barriers') or {}
    from s1_cost_present import promotion_summary
    promotion_body+='\n推广判断：'+cell(promotion_summary(native) if cost.get('unified_cost') else promotion.get('conclusion'))+'\n\n'+table(['指标','当前事实'],[('自然前5评论中位数',barriers.get('organic_top5_review_median',promotion.get('organic_top5_review_median'))),('Top20一年内新品占比',_pct(barriers.get('top20_under_1yr_share',promotion.get('new_products_top20_ratio'))))])
    cost_body='本章只展示统一成本引擎输出；缺失费用不按零处理。\n\n'
    from s1_cost_present import condition_table
    cost_body+=condition_table(native)+('\n' if condition_table(native) else '')
    cost_current=cost.get('_source_current') is True and bool(cost.get('unified_cost'))
    if not cost_current:
        cost_body+='历史条件情景：以下数字来源未核验或已经失效，不代表当前成本；当前结果待重算。\n\n'
        cost_body+=table(['历史情景数值','原模型输出（当前不可采用）'],[('落地成本',_money(result.get('landed_cost'))),('广告前毛利率',_pct(result.get('pre_ad_margin'))),('目标售价',_money(result.get('target_price_pre_ad')))])+'\n'
    for key,title in [('landed','单件落地成本'),('amazon','亚马逊费用'),('target_price','目标毛利反推售价'),('price_ladder','价格阶梯 × 毛利'),('competitiveness','竞争力判定'),('test_plans','测款方案')]:
        block=(native.get('blocks') or {}).get(key) or {};cost_body+='### '+title+'\n\n'
        cost_body+=table(['项目','数值','单位','来源 / 状态'],[(r.get('label'),r.get('value'),r.get('unit'),cell(r.get('source'))+' / '+cell(r.get('status'))) for r in block.get('rows') or []],24)+'\n'
        if key=='price_ladder':cost_body+=table(['价位','售价','广告前毛利','广告前毛利率','广告后净利率'],[(r.get('label'),_money(r.get('price')),_money(r.get('pre_ad_profit')),_pct(r.get('pre_ad_margin')),_pct(r.get('post_ad_margin'))) for r in block.get('scenarios') or []],6)
        if key=='test_plans':cost_body+=table(['方案','标价','券比例','成交价','说明'],[(r.get('label'),_money(r.get('list_price')),r.get('coupon_percent'),_money(r.get('realized_price')),r.get('note')) for r in block.get('plans') or []],6)
    if native.get('warnings'):cost_body+='\n统一引擎警示：'+'；'.join(cell(x) for x in native['warnings'])+'\n'
    cost_body+='\n[打开毛利计算器（从导航“💰 毛利计算”进入）](#margin-calculator)\n'
    from s1_cost_present import render_assumptions
    cost_body+='\n'+render_assumptions(native)
    risk=table(['条件 / 风险','当前行动'],[(x,'补充对应证据后重新生成本报告') for x in decision['conditions']],10)
    risk+='\n允许动作：'+cell((model.get('decision') or {}).get('next_action'))+'\n\n采购授权：'+('已有原始授权，仍按原授权范围执行。' if decision['purchase_authorized'] else '未授权；本报告与清单不新增采购或广告授权。')
    purchase='本节为采购准备清单，未勾选不阻断研究流程；不合格样品回第5步换货源。\n\n'
    for group in checklist.get('groups') or []:
        purchase+='### '+str(group['title'])+'\n\n'
        for item in group.get('items') or []:
            purchase+='- ['+('x' if item.get('checked') else ' ')+'] '+item['label']+'\n'
    chapters=[{'id':'market','title':CHAPTERS[0],'subsections':market,'body':'\n\n'.join('### '+s['title']+'\n\n'+s['body'] for s in market)},
        {'id':'product','title':CHAPTERS[1],'body':chapter2},{'id':'ip','title':CHAPTERS[2],'body':table(['项目','灯','最终结论','需要你做'],ip)},
        {'id':'promotion','title':CHAPTERS[3],'body':promotion_body},{'id':'cost','title':CHAPTERS[4],'body':cost_body},
        {'id':'risk','title':CHAPTERS[5],'body':risk},{'id':'purchase','title':CHAPTERS[6],'body':purchase}]
    # R09: the stage report keeps a summary; the full analysis is the market report.
    competition=result.get('competitiveness') or {}
    front={'ct_rows':ctrows,'offer_url':offer,'ip_rows':ip,'image':None,
        'answers':[{'id':'market','title':'② 为什么选','light':'yellow','metrics':[str(len(competitors))+'个竞品样本',str((facts.get('coverage',{}).get('reviews') or {}).get('observed',0))+'条评论'],'text':cell((inputs.get('demand') or {}).get('positioning'))},
            {'id':'ip','title':'③ 会不会侵权','light':'yellow' if any(r[1]!='🟢' for r in ip) else 'green','metrics':[str(sum(r[1]!='🟢' for r in ip))+'项待核'],'text':'以最终结论卡和人工待核项为准'},
            {'id':'promotion','title':'④ 好不好推','light':'yellow','metrics':[str(len(promos))+'个关键词',cell(promotion.get('conclusion'))],'text':'CPC与所需转化率由统一引擎提供'},
            {'id':'cost','title':'⑤ 赚不赚钱','light':competition.get('level','gray'),'metrics':['落地 '+_money(result.get('landed_cost')),'毛利 '+_pct(result.get('pre_ad_margin')),'目标价 '+_money(result.get('target_price_pre_ad'))],'text':cell(competition.get('reason'))}],
        'actions':(decision['conditions']+['核对厂家确认清单与样品规格','核对IP待核事项和本轮定位','补齐需求与成本来源后复核'])[:3]}
    if not cost_current:
        front['answers'][-1].update(light='gray',metrics=['当前结果待重算'],text='历史条件情景见成本章；来源未核验或已失效')
        front['answers'][-2].update(light='gray',metrics=['当前推广测算待更新'],text='历史情景见推广章；成本来源未核验或已失效')
        chapters[3]['body']='历史条件情景：关联成本来源未核验或已经失效，以下推广数值不可作为当前结论。\n\n'+chapters[3]['body']
    elif cost.get('cost_basis')=='conditional':
        front['answers'][-1]['text']='条件测算：'+front['answers'][-1]['text']
    for component in (model.get('decision') or {}).get('components') or []:
        image=value(component.get('image')) or component.get('image_url') or component.get('image_path') or component.get('image_data')
        if image:front['image']=image;break
    if not front['image'] and str(offerid or '').isdigit() and model.get('root'):
        base=Path(model['root'])/'S1_选品板块/1688-product-detail-scraper'
        # download_media writes <save_dir>/主图/main_01.*; save_dir is documented as 媒体/ but is
        # often 媒体/offer_<id>/. A shared 媒体/ only counts when its source JSON is this offer.
        oid=str(offerid);media=base/'媒体'
        folders=[media/('offer_'+oid)/'主图',media/oid/'主图',base/('offer_'+oid)/'主图',media/oid,
                 # S1-R16: the 1688 scraper skill saves to 00_产品资料/1688_<offer>/媒体/主图/.
                 Path(model['root'])/'00_产品资料'/('1688_'+oid)/'媒体'/'主图']
        source=media/'1688_source.json'
        if source.is_file() and oid in source.read_text(encoding='utf-8',errors='ignore'):folders.append(media/'主图')
        pictures=[p for folder in folders for p in sorted(folder.glob('main_01.*'))]
        for picture in pictures:
            mime={'.jpg':'jpeg','.jpeg':'jpeg','.png':'png','.webp':'webp'}.get(picture.suffix.lower())
            if mime and picture.resolve().is_relative_to(Path(model['root']).resolve()) and picture.is_file() and picture.stat().st_size<=8*1024*1024:
                front['image']='data:image/'+mime+';base64,'+base64.b64encode(picture.read_bytes()).decode()
                front['image_source']=picture.relative_to(Path(model['root'])).as_posix();break
    front['ct_rows']=ctrows+[('1688 链接',offer),('对标 ASIN',[x.get('asin') for x in competitors[:3]])]
    main=native.get('main_basis') or {}
    if main and cost_current:
        shown=main['result']
        scenarios=main.get('scenarios') or [] if main.get('kind')=='user_condition' else []
        if len(scenarios)>3:
            # 3.0.25: sixteen scenarios listed in the card made it the tallest block on the page.
            ranked=sorted(scenarios,key=lambda row:(row['result'].get('pre_ad_margin') is None,row['result'].get('pre_ad_margin') or 0))
            best,worst=ranked[-1],ranked[0]
            text=(f"{len(scenarios)} 套情景：最好 {best['label']} 毛利 {_pct(best['result'].get('pre_ad_margin'))}，"
                  f"最差 {worst['label']} 毛利 {_pct(worst['result'].get('pre_ad_margin'))}；大字为最差一套，完整表见成本章；实际利润待核。")
        else:
            text='主口径：'+(('；'.join(row['label']+' 毛利 '+_pct(row['result'].get('pre_ad_margin')) for row in scenarios)+'（代表值取利润最低的一项）')
                             if scenarios else main['name'])+'；实际利润待核。'
        front['answers'][-1].update(metrics=['落地 '+_money(shown.get('landed_cost')),'毛利 '+_pct(shown.get('pre_ad_margin')),'目标价 '+_money(shown.get('target_price_pre_ad'))],
                                    text=text)
    overview=facts.get('market_overview') or {}
    size=((overview.get('category') or {}).get('size') or {});pool=((overview.get('pool') or {}).get('stats') or {})
    trend=overview.get('category_trend') or {}
    if size.get('top100_units') or pool.get('skus'):
        from s1_market_report import num,signed
        reason=narrative.get('summary',{}).get('breakthrough') if isinstance(narrative.get('summary'),dict) else None
        front['answers'][0].update(metrics=[m for m in ('类目月销 '+num(size.get('top100_units'))+' 件' if size.get('top100_units') else None,
                                                     f"同款型 {num(pool.get('skus'))} 个" if pool.get('skus') else None) if m],
                                   text=reason or ('类目今年同比 '+signed(trend['ytd_yoy']) if trend.get('ytd_yoy') is not None else '详见市场分析报告'))
    if cost.get('unified_cost') and cost_current:
        from s1_promotion_model import headline
        sentence=headline(cost['unified_cost'])
        if sentence:front['answers'][2].update(metrics=[str(len(promos))+'个关键词'],text=sentence)
    manifest=model.get('source_manifest') or {}
    appendix='数据来源与时间：'+cell(model.get('evidence_date'))+'。原始数据与历史内容保留，不以旧结论代替当前判定。\n\n'
    identity=model.get('plugin_identity') or {}
    host={'codex':'Codex','claude':'Claude','source_preview':'源码预览'}.get(identity.get('host'),'源码预览')
    appendix+='运行插件：'+host+' · '+str(identity.get('version') or '版本未知')+'。\n\n'
    appendix+=table(['覆盖项目','已取得 / 目标','状态与原因'],[(k,str(x.get('observed'))+' / '+str(x.get('required')),str(x.get('status') or '')+'；'+str(x.get('reason') or '来源未给出原因')) for k,x in (facts.get('coverage') or {}).items() if isinstance(x,dict)],9)+'\n'
    for index,source in enumerate(model.get('merged_research_sources') or [],1):
        path=source.get('archive_path')
        if path:appendix+='- [历史研究记录 '+str(index)+'](<'+str(Path('../../')/path).replace('\\','/')+'>)\n'
    appendix+=table(['来源文件','SHA-256'],[(path,digest) if digest else (Path(path).stem+'（本轮未提供）','未提供') for path,digest in manifest.items()],8)+'\n知识库依据与验收自检：完整引用、逐项缺口、历史版本保留在阶段回执与源文件。\n'
    from kb_report_contract import stage_basis
    basis=stage_basis(model.get('root'))
    appendix+=('\n## 知识库依据\n\n<details><summary>本阶段引用了 '+str(basis.count('\n- '))+' 张知识卡（点开查看用途）</summary>\n\n'
               +basis+'\n\n</details>\n')
    from s1_readability import reader_projection
    seen_quotes={}
    for chapter in chapters:
        chapter['body']=reader_projection(unique_review_quotes(chapter['body'],seen_quotes))
    return {'schema_version':2,'product_name':product.get('product') if product.get('selection_basis',{}).get('source')=='user' else model.get('product_name'),
        'decision_summary':decision,'front':front,'chapters':chapters,'appendix':appendix,'purchase_checklist':checklist}


def render_md(report,product_name):
    decision=report['decision_summary'];front=report['front']
    warning=('⚠️ 验收未通过（'+str(decision['pending_count'])+' 项）' if decision['pending_count'] else '⚠️ 验收待完成') if not decision['accepted'] else '验收已通过'
    out='# S1 选品可行性研究报告｜'+cell(product_name)+'\n\n**唯一结论：'+decision_headline(decision)+'** · '+warning+'\n\n'
    out+=table(['五个答案','当前事实'],[('① 是什么',product_name)]+[(a['title'],' · '.join(a['metrics'])) for a in front['answers']])+'\n'
    out+='你要做的3件事：\n'+'\n'.join(str(i+1)+'. '+cell(x) for i,x in enumerate(front['actions']))+'\n\n'
    out+='\n\n'.join('## '+str(i+1)+' '+chapter['title']+'\n\n'+chapter['body'] for i,chapter in enumerate(report['chapters']))
    out+='\n\n## 附录\n\n<details><summary>数据来源、证据索引、知识库与验收</summary>\n\n'+report['appendix']+'\n</details>\n'
    return out


def render_front(report,product_name):
    esc=lambda x:html.escape(cell(x).replace('缺失：本轮结构化来源未提供','待补').replace('未取得','待补'),quote=True)
    decision=report['decision_summary'];front=report['front']
    warning='' if decision['accepted'] else '⚠️ 验收未通过（'+str(decision['pending_count'])+' 项）' if decision['pending_count'] else '⚠️ 验收待完成'
    image=front.get('image');picture='<div class="m2-image missing">产品主图缺失：尚未绑定当前款式图片</div>'
    if image and (str(image).startswith(('data:image/','https://')) or not re.match(r'\w+:',str(image))):picture='<img class="m2-image" src="'+esc(image)+'" alt="'+esc(product_name)+'">'
    frontrows=[(k,'；'.join(str(x.get('message') or x.get('status')) for x in v[:2]) if k=='自检' and isinstance(v,list) else v) for k,v in front['ct_rows']]
    ct='<div class="m2-ct"><h3>① 是什么 · 产品 CT</h3><dl>'+''.join('<dt>'+esc(k)+'</dt><dd>'+('<a href="'+esc(v)+'">查看所选货源</a>' if k=='1688 链接' and isinstance(v,str) and re.match(r'https://detail\.1688\.com/offer/\d+\.html$',v) else esc(v))+'</dd>' for k,v in frontrows)+'</dl></div>'
    cards=''.join('<a class="m2-answer" href="#'+a['id']+'"><strong>'+esc(a['title'])+' '+LIGHTS.get(a['light'],'⚪')+'</strong><b>'+esc(' · '.join(a['metrics']))+'</b><span>'+esc(a['text'])+'</span></a>' for a in front['answers'])
    headline=decision_headline(decision)
    return '<section id="s1-product-decision" class="s1front m2-front" data-report-version="2"><div class="m2-verdict"><strong class="decision-label" data-decision="'+decision['code']+'" data-decision-label="'+esc(headline)+'">唯一结论：'+esc(headline)+'</strong><span>'+esc(warning)+'</span></div><div class="m2-product">'+picture+ct+'</div><div class="m2-answers">'+cards+'</div><div class="m2-actions"><b>你要做的3件事</b><ol>'+''.join('<li>'+esc(x)+'</li>' for x in front['actions'])+'</ol></div></section>'


def render_purchase(checklist):
    esc=lambda x:html.escape(str(x),quote=True)
    text='<p>采购准备清单，未勾选不阻断研究流程；不合格样品回第5步换货源。</p>'
    for group in checklist.get('groups') or []:
        text+='<fieldset><legend>'+esc(group['title'])+'</legend>'
        for item in group.get('items') or []:
            text+='<label class="purchase-item"><input type="checkbox" data-purchase-id="'+esc(item['id'])+'"'+(' checked' if item.get('checked') else '')+'>'+esc(item['label'])+'</label>'
        text+='</fieldset>'
    return text+'<button type="button" id="save-purchase">保存采购进度</button><p id="purchase-status" role="status">从毛利计算器的本地报告入口打开，即可保存到当前产品。</p>'


PURCHASE_JS='''<script>(()=>{const button=document.getElementById('save-purchase');if(!button)return;const status=document.getElementById('purchase-status');const local=location.protocol==='http:'&&location.hostname==='127.0.0.1';const link=document.querySelector('[data-calculator-link]');if(local&&link){link.setAttribute('href','/');link.textContent='打开毛利计算器';}if(!local){button.disabled=true;return;}status.textContent='勾选后保存到当前产品；不会授权采购。';button.addEventListener('click',async()=>{button.disabled=true;const updates={};document.querySelectorAll('[data-purchase-id]').forEach(box=>{updates[box.dataset.purchaseId]=box.checked;});try{const response=await fetch('/api/purchase-checklist',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({updates})});const result=await response.json();if(!response.ok||!result.ok)throw new Error(result.error||'保存失败');status.textContent=result.report_updated?'已保存采购进度，报告已更新。':'已保存采购进度。';}catch(error){status.textContent='未保存：'+error.message;}finally{button.disabled=false;}});})();</script>'''


CSS='''
.s1-m2 .hero{padding:12px 18px;margin-bottom:8px}.s1-m2 .hero h1{font-size:24px;line-height:1.25;margin:0}.s1-m2 .hero .sub{font-size:13px;margin-top:4px}.s1-m2 .hero .report-kicker{display:none}.s1-m2 .full-index{margin:8px 0}.s1-m2 .m2-ct h3{font-size:14px;margin:4px 0}.s1-m2 .m2-front{margin-top:8px}
.m2-front{padding:14px!important;max-width:100%;box-sizing:border-box}.m2-verdict{display:flex;gap:8px;flex-wrap:wrap;padding:10px;background:#fff4da}.m2-product{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.4fr);gap:14px;align-items:center}.m2-image{max-width:100%;width:100%;height:230px;object-fit:contain}.m2-image.missing{display:grid;place-items:center;color:#7c5e22;background:#f5f5f0}.m2-ct{min-width:0}.m2-ct dl{display:grid;grid-template-columns:80px minmax(0,1fr);gap:2px 8px;font-size:12px;line-height:1.4}.m2-ct dt,.m2-ct dd{margin:0;overflow-wrap:anywhere}.m2-answers{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin:10px 0}.m2-answer{padding:8px;background:#f0f7f5;border:1px solid #d1e5df;border-radius:8px;min-width:0;color:inherit;text-decoration:none}.m2-answer>*{display:block;overflow-wrap:anywhere;font-size:12px}.m2-answer b{font-size:15px;padding:5px 0}.m2-actions{font-size:12px;line-height:1.4}.m2-actions ol{margin:4px 0;padding-left:20px}.report-section{min-width:0}.report-section td,.report-section th{overflow-wrap:anywhere}.m2-appendix{max-width:100%;overflow-wrap:anywhere}@media(max-width:600px){.m2-product{grid-template-columns:minmax(0,1fr)}.m2-image{height:190px}.m2-answers{grid-template-columns:repeat(2,minmax(0,1fr))}.m2-front{padding:10px!important}.m2-ct dl{grid-template-columns:65px minmax(0,1fr)}}
'''
def competition_report(distribution, research_notes=''):
    """Recompute keyword coverage from the saved SIF response, never ad spend."""
    from s1_readability import reader_projection
    lines=['# 竞争复核','','结论：沿用原研究判断；词覆盖不证明花费、效率或可盈利性。',
           '本报告从留存的流量分布重新计算，没有取得新的业务数据。','',
           '## 词覆盖复核','','百分比为广告词数 / 全部词数；自然与广告词可重叠，不能相加。',
           '它不是广告流量占比、花费占比、ACOS或订单占比。','',
           '| 颜色/子体 | 现价 | 全部词数 | 自然词数 | 广告词数 | 广告词覆盖比例 |',
           '|---|---:|---:|---:|---:|---:|']
    for row in distribution.get('asins') or []:
        total=row.get('total');ad=row.get('ad');price=row.get('price')
        ratio=f'{100*ad/total:.1f}%' if isinstance(total,(int,float)) and total>0 and isinstance(ad,(int,float)) else '待核'
        show=lambda value:'待核' if value is None else str(value)
        lines.append('| '+show(row.get('asin'))+' | '+('$'+str(price) if price is not None else '待核')+' | '+
                     show(total)+' | '+show(row.get('natural'))+' | '+show(ad)+' | '+ratio+' |')
    if research_notes:lines+=['','## 沿用的原研究判断','',research_notes]
    return reader_projection('\n'.join(lines))

