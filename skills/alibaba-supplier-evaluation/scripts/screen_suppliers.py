#!/usr/bin/env python3
"""Screen supplier evidence into a sidecar without changing original evidence."""
import argparse
import html
import copy
from datetime import date
import re
import json
from pathlib import Path
import sys
import yaml
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # page_scores.py ships beside this script
from s1_market_facts import classify_supplier, supplier_price_reference, format_cell

CONFIRMATION_KINDS = {"supplier_confirmation", "factory_audit", "sample_report", "signed_quote"}

DIMENSIONS = [('D1','资质信誉',.20),('D2','价格竞争力',.20),('D3','产品质量信号',.15),
              ('D4','供应链柔性',.15),('D5','跨境服务',.15),('D6','定制能力',.10),('D7','风险控制',.05)]

FOLLOW_UP = {
    'D1':'补营业执照、店铺认证页及验厂记录；问厂家：「请问实际生产主体是谁，可否提供营业执照和工厂现场视频？」',
    'D2':'补同一目标SKU的含税阶梯报价单；问厂家：「请问按目标尺寸、里料与数量，含包装单价及报价有效期是多少？」',
    'D3':'补目标样品和质检页；问厂家：「请问可否寄样，并提供缝线、拉链、色牢度和尺寸公差检验记录？」',
    'D4':'补生产排期、MOQ及补货周期页；问厂家：「请问首单和补货各需多少天，最低起订量和旺季产能是多少？」',
    'D5':'补出口包装、税票及交付服务说明；问厂家：「请问是否支持跨境装箱标签、开票和指定货代交货？」',
    'D6':'补定制案例页和目标款打样方案；问厂家：「请问目标尺寸、材质与里料能否定制，打样费用、周期及版型归属如何约定？」',
    'D7':'补合同、收款主体及售后条款；问厂家：「请问质量不符、延期交货和知识产权争议如何退换或赔付？」',
}


def dimension_observation(row,key):
    shop=row.get('shop_signal') or {};reviews=row.get('review_signals') or {}
    tiers=row.get('tiers') or [];skus=row.get('skus') or []
    if key=='D1' and shop:
        return '页面认证企业：'+str(shop.get('authCompanyName') or row.get('company'))+'；服务分 '+str(shop.get('sellerSlrServiceScore') or '—')+'；近3个月回头率 '+str(shop.get('byrRepeatRate3m') or '—')+'。页面标识不等于验厂。'
    if key=='D2' and row.get('min_price') is not None:
        return '页面起价 ¥'+format(float(row['min_price']),'.2f')+'；'+str(len(tiers))+' 档阶梯。仅作现款比较，改尺寸、改里料后的单价未确认。'
    if key=='D3' and reviews:
        all_count=next((x.get('count') for x in reviews.get('commonTagNodeList') or [] if x.get('name')=='全部'),None)
        tags='、'.join(str(x.get('name'))+' '+str(x.get('count'))+'次' for x in (reviews.get('impressionTagNodeList') or [])[:3])
        return '页面评价 '+str(all_count or '—')+' 条；'+tags+'。为页面评价信号，未核验目标样品缝线与拉链。'
    if key=='D4' and tiers:
        return '页价最低起订 '+str(min(t['min_qty'] for t in tiers if t.get('min_qty') is not None))+' 件；'+str(len(skus))+' 个SKU记录。目标款交期、补货周期未确认。'
    if key=='D5':return '标题或搜索词提及跨境不证明可履约；出口包装、税票与交付服务尚待厂家确认。'
    if key=='D6' and skus:
        custom=[str(s.get('name')) for s in skus if '定制' in str(s.get('name'))]
        return ('页面选项：'+'、'.join(custom)+'；' if custom else '已抓到现款SKU；')+'本轮目标规格、材质、适配和打样仍需逐项确认。'
    if key=='D7' and (row.get('risk_note') or row.get('reason')):return str(row.get('risk_note') or row['reason'])
    return '尚无可核实记录，需补取该维度证据。'


def evaluation_report(document):
    """Seven dimensions retain unavailable evidence; unknown is never scored zero."""
    rows = document.get('candidates') or []
    companies = {str(row.get('company') or '').strip() for row in rows if row.get('company')}
    complete = bool(rows)
    cooperator = _cooperator_mode(document)
    paged = sum(1 for row in rows if row.get('page_signals'))
    if cooperator:
        text = ['# 合作供应商评估', '', '结论：评估你已合作的供应商；分数按 1688 页面可见信号计算，厂家确认后再更新，不等于验厂或采购批准。',
                f'关键数字：{len(companies)} 家合作供应商，其中 {paged} 家取得 1688 页面并已评分。',
                '要你决定的事：按下面的待确认问题向供应商索取目标款报价、包规和样品。', '']
    else:
        text = ['# 供应商综合评估', '', '结论：区分页面候选与已核验厂家，未知信息不能支持采购。',
                f'关键数字：{len(companies)} 家具名供应商，{len(rows)} 个商品候选（{paged} 家取得页面详情并按页面信号评分）。',
                '要你决定的事：先核实目标款打样、完整报价与包规，再选厂家。', '']
    requirements = document.get('requested_design_requirements') or []
    if requirements:
        text+=['## 目标规格与候选差距','']
        for row in requirements:
            label=(' · '.join(str(row[key]) for key in ('id','requirement') if row.get(key)) or '目标项待补') if isinstance(row,dict) else str(row)
            text.append('- '+label)
        text+=['','每家后面的页面事实、风险及待核问题，按这些目标逐项比较；现货相似不等于目标款已证实。','']
    for row in rows:
        title = str(row.get('company') or '公司名称待核') + ' · ' + str(row.get('offer_id') or '')
        text += ['## ' + title, '', '| 项目 | 页面或已确认事实 |', '|---|---|',
                 '| 商品 | ' + str(row.get('title') or '—').replace('|','／') + ' |',
                 '| 页面价格 | ' + ('¥'+format(float(row['min_price']),'.2f') if row.get('min_price') is not None else '—') + ' |',
                 '| MOQ | ' + str(row.get('moq') or row.get('min_order') or min((x['min_qty'] for x in row.get('tiers') or [] if x.get('min_qty') is not None),default='—')) + ' |',
                 '| 材质 | ' + str(row.get('material') or '—') + ' |',
                 '| 尺寸与包规 | ' + str(row.get('dimensions') or row.get('package') or '—') + ' |']
        for sku in row.get('skus') or []:
            if isinstance(sku, dict) and (sku.get('sku') or sku.get('role')):
                price = sku.get('price_cny'); price = price.get('value') if isinstance(price, dict) else price
                value = lambda item: item.get('value') if isinstance(item, dict) else item
                size = lambda item: '×'.join(format(float(x),'g') for x in value(item)) + 'cm' if isinstance(value(item), list) and value(item) else None
                parts = [('页价 ¥'+format(float(price),'g')) if isinstance(price,(int,float)) else None,
                         ('合作报价 ¥'+format(float(sku['quote_price_cny']),'g')) if isinstance(sku.get('quote_price_cny'),(int,float)) else None,
                         ('商品重 '+format(float(value(sku['product_weight_g'])),'g')+'g') if isinstance(value(sku.get('product_weight_g')),(int,float)) else None,
                         ('页面包装 '+size(sku['page_package_cm'])) if size(sku.get('page_package_cm')) else None,
                         ('压缩包装 '+size(sku['compact_package_cm'])) if size(sku.get('compact_package_cm')) else None,
                         ('毛重 '+format(float(value(sku['package_gross_weight_kg'])),'g')+'kg') if isinstance(value(sku.get('package_gross_weight_kg')),(int,float)) else '毛重待确认']
                text.append('| 研究 SKU | ' + str(sku.get('sku') or sku.get('name') or '—').replace('|','／') + ' · ' + ' · '.join(x for x in parts if x) + ' |')
        text += ['', '| 维度 | 权重 | 评分（1–5分） | 依据或待补项 |', '|---|---:|---:|---|']
        total = 0
        valid = True
        for key, label, weight in DIMENSIONS:
            evidence = (row.get('scores') or {}).get(key) or {}
            score = evidence.get('score') if isinstance(evidence,dict) else None
            ok = isinstance(score,(int,float)) and not isinstance(score,bool) and 1 <= score <= 5 and bool(evidence.get('source'))
            valid = valid and ok
            total += score*weight if ok else 0
            page = ok and evidence.get('basis') == '页面信号'
            text.append('| '+label+' | '+format(weight,'.0%')+' | '+(format(score,'g') if ok else '待核')+' | '+
                        ((str(evidence.get('reason') or '原始依据已登记')+('（页面信号，待厂家确认）' if page else '；来源：'+str(evidence['source'])))
                         if ok else dimension_observation(row,key)+' '+FOLLOW_UP[key])+' |')
        complete = complete and valid
        page_based = any(((row.get('scores') or {}).get(k) or {}).get('basis') == '页面信号' for k,_,_ in DIMENSIONS)
        text += ['', '综合得分：'+((format(total,'.2f')+' / 5 分'+('（含页面信号分，厂家确认后更新）' if page_based else '')) if valid else '证据未齐，不计算综合分。'),
                 '推荐或排除理由：'+str(row.get('recommendation') or row.get('risk_note') or row.get('reason') or
                                    ('你的合作供应商；按页面信号评分，目标款报价、包规和样品待确认。' if cooperator else '保留为页面参考；目标款和定制能力尚待确认。')),
                 '价格阶梯：'+('；'.join('≥'+str(t.get('min_qty'))+' 件：¥'+format(float(t['unit_price_cny']),'.2f') for t in row.get('tiers') or [] if t.get('unit_price_cny') is not None) or
                              (('页面各 SKU ¥'+format(row['page_signals']['min_price'],'g')+'–'+format(row['page_signals']['max_price'],'g')+'（'+str(row['page_signals']['sku_count'])+' 个 SKU，按 SKU 定价）；目标款含包装报价待确认。')
                               if (row.get('page_signals') or {}).get('min_price') is not None else '待补完整报价及对应 SKU。')),
                 '商品链接：'+str(row.get('url') or '待补'), '']
    screening=document.get('screening') or {}
    if cooperator and not any(screening.get(key) for key in ('excluded','needs_review')):
        screening=None  # nothing was screened out: no empty search table in a cooperator check
    text+=(['## 搜索与排除记录','','| 商品 | 判定 | 原因 |','|---|---|---|'] if screening is not None else [])
    screening=screening or {}
    for key,label in [('excluded','排除'),('needs_review','待核')]:
        for row in screening.get(key) or []:
            text.append('| '+str(row.get('offer_id') or row.get('title') or '未标商品').replace('|','／')+' | '+label+' | '+
                        str(row.get('reason') or row.get('risk_note') or '筛选依据待补').replace('|','／')+' |')
    if not cooperator and not any(screening.get(key) for key in ('excluded','needs_review')):
        text+=['','留存筛选表未列排除候选；不把未取得的结果当作搜索后零排除。']
    text += ['','## 待补清单', '', '补齐各家资质、质量、交期、跨境服务、定制和风险证据；页面广告承诺不等于厂家已确认。',
             ('合作供应商不做 5 家择优；想要备选厂家时再按同款搜索比较。' if cooperator else
              '少于 5 家具名候选时继续搜索；未抓详情的搜索候选不能算完整货源核验。'), '']
    asks = [FOLLOW_UP[key].split('问厂家：',1)[-1] for key,_,_ in DIMENSIONS
            if any(((row.get('scores') or {}).get(key) or {}).get('basis') == '页面信号' for row in rows)]
    if asks:
        text += ['## 向供应商确认的问题', ''] + ['- '+a for a in asks[:8]] + ['']
    return {'markdown':'\n'.join(text), 'candidate_company_count':len(companies), 'scores_complete':complete}


def _cooperator_mode(document):
    """The user named suppliers they already work with (enterprise data): evaluate those, no five-way search."""
    return document.get('source_mode') in ('user_specified_cooperator', 'enterprise_cooperator') or (
        document.get('selection_source') == 'user' and bool(document.get('suppliers')) and not document.get('screening'))


def _normalize_candidates(document):
    """Accept the cooperator schema too (suppliers / name / offer_url); 3.0.25 showed '0 家' for it."""
    candidates = document.setdefault('candidates', [])
    known = {str(row.get('offer_id')) for row in candidates}
    for row in document.get('suppliers') or []:
        if isinstance(row, dict) and str(row.get('offer_id')) not in known:
            candidates.append(dict(row)); known.add(str(row.get('offer_id')))
    for row in candidates:
        row.setdefault('company', row.get('name') or row.get('supplier_name'))
        row.setdefault('url', row.get('offer_url'))
    return candidates


def attach_page_scores(root, candidates):
    """Score every candidate whose 1688 page was saved; scores the assistant wrote with a source win."""
    from page_scores import signals, score
    root = Path(root); scraper = root/'S1_选品板块/1688-product-detail-scraper'
    pages = {}
    for row in candidates:
        offer = str(row.get('offer_id') or '')
        hits = sorted(p for p in scraper.rglob('1688_source.json') if offer and (offer in p.parts or 'offer_'+offer in p.parts)) if offer else []
        if hits:
            try:pages[offer] = signals(json.loads(hits[0].read_bytes()), hits[0].relative_to(root).as_posix())
            except (OSError, ValueError):pass
    for row in candidates:
        sig = pages.get(str(row.get('offer_id') or ''))
        if not sig:continue
        peers = [s['min_price'] for o, s in pages.items() if o != str(row.get('offer_id'))]
        quotes = [q for q in [row.get('quote_price_cny')] + [s.get('quote_price_cny') for s in row.get('skus') or [] if isinstance(s, dict)]
                  if isinstance(q, (int, float))]
        page, _ = score(sig, peers, min(quotes) if quotes else None)
        scores = row.setdefault('scores', {})
        for key, value in page.items():
            mine = scores.get(key)
            if not (isinstance(mine, dict) and isinstance(mine.get('score'), (int, float)) and mine.get('source')):
                scores[key] = value
        row['page_signals'] = {k: v for k, v in sig.items() if k not in ('title',)}
        row.setdefault('company', sig.get('company'))
        row.setdefault('title', sig.get('title'))
        if row.get('min_price') is None and sig.get('min_price') is not None:row['min_price'] = sig['min_price']
        if row.get('moq') is None and sig.get('moq') is not None:row['moq'] = int(sig['moq'])
        shop = row.setdefault('shop_signal', {})
        shop.setdefault('authCompanyName', sig.get('company')); shop.setdefault('sellerSlrServiceScore', sig.get('service_score'))
        shop.setdefault('byrRepeatRate3m', sig.get('repeat_rate'))
    return candidates


def write_evaluation(root):
    root = Path(root)
    folder = root/'S1_选品板块/alibaba-supplier-evaluation'
    source = folder/'02_supplier_evidence.yaml'
    if not source.is_file():
        return None
    document = copy.deepcopy(yaml.safe_load(source.read_bytes()) or {})
    for path in sorted(folder.glob('*筛选结果.json')):
        screening=json.loads(path.read_bytes())
        if isinstance(screening,dict) and any(key in screening for key in ('excluded','needs_review')):
            document['screening']=screening
            break
    candidates = _normalize_candidates(document)
    offers = {str(row.get('offer_id')) for row in candidates}
    companies = {str(row.get('company') or '').strip() for row in candidates if row.get('company')}
    raw_folders = [root/'S1_选品板块/1688-product-detail-scraper/运行数据', root/'S1_选品板块/amazon-market-research/运行数据']
    # A cooperator check evaluates the user's own suppliers; search hits of other companies are not added as candidates.
    raw_paths = [] if _cooperator_mode(document) else [p for folder in raw_folders for p in sorted(folder.glob('*.json'))]
    for path in raw_paths:
        data = json.loads(path.read_bytes())
        calls = data.get('calls') if isinstance(data,dict) and isinstance(data.get('calls'),dict) else data
        for call in calls.values() if isinstance(calls,dict) else []:
            if not isinstance(call,dict) or 'search_offers' not in str(call.get('tool') or ''):
                continue
            response = call.get('response') or {}
            content = response.get('structuredContent') or {}
            if isinstance(content,dict) and isinstance(content.get('result'),str):
                content = json.loads(content['result'])
            for row in content.get('offers') or []:
                offer = str(row.get('offer_id') or '')
                title = str(row.get('card_text') or row.get('title') or '')
                names = re.findall(r'[\u4e00-\u9fff（）()A-Za-z0-9]+(?:有限公司|有限责任公司|加工厂)',title)
                company = str(row.get('company') or (names[-1] if names else '')).strip()
                if not company or company in companies or offer in offers or not re.fullmatch(r'\d+', offer):
                    continue
                if classify_supplier({**row,'title':title})[0]!='same_form':
                    continue
                candidates.append({**row,'title':row.get('title') or title,'company':company,'source':path.relative_to(root).as_posix(),
                                   'reason':'搜索页候选，尚未取得完整详情；不得当作目标款已确认厂家。'})
                offers.add(offer);companies.add(company)
                if len(companies) >= 5:
                    break
            if len(companies) >= 5:
                break
        if len(companies) >= 5:
            break
    attach_page_scores(root, candidates)
    result = evaluation_report(document)
    from s1_readability import reader_projection
    result['markdown']=reader_projection(result['markdown'])
    from kb_report_contract import render_native_narrative
    result['markdown']=render_native_narrative(result['markdown'],root,Path(__file__).resolve().parents[3],5,'selection_md')
    output = folder/'02_供应商综合评估.md'
    output.write_text(result['markdown'],encoding='utf8')
    document.update(candidate_company_count=result['candidate_company_count'],scores_complete=result['scores_complete'])
    (folder/'02_supplier_evaluation.json').write_text(json.dumps(document,ensure_ascii=False,indent=2),encoding='utf8')
    for row in candidates:
        offer = str(row.get('offer_id') or '')
        if not re.fullmatch(r'\d+',offer):
            continue
        target = root/f'S1_选品板块/1688-product-detail-scraper/offer_{offer}/供应商产品报告_{offer}.html'
        if target.is_file():
            continue
        target.parent.mkdir(parents=True,exist_ok=True)
        body = evaluation_report({'candidates':[row]})['markdown']
        target.write_text('<!doctype html><meta charset="utf-8"><title>供应商页面初评</title><h1>搜索页初评 · 尚待抓取详情</h1><pre style="white-space:pre-wrap">'+html.escape(body)+'</pre>',encoding='utf8')
    return output

def _confirmation_gaps(value, label):
    """Validate recorded confirmation provenance, never infer it from page advertising."""
    if not isinstance(value, dict):
        return [f"{label}：缺结构化确认记录"]
    gaps=[]
    if value.get("status") != "confirmed":
        gaps.append(f"{label}：尚未确认")
    if not isinstance(value.get("source"), str) or not value["source"].strip():
        gaps.append(f"{label}：缺原始回复、审厂或样品证据来源")
    if value.get("source_kind") not in CONFIRMATION_KINDS:
        gaps.append(f"{label}：广告页或未知来源不能代替实际确认")
    try:
        observed=date.fromisoformat(str(value.get("observed_at", ""))[:10])
        if observed > date.today():
            gaps.append(f"{label}：确认日期在未来")
    except ValueError:
        gaps.append(f"{label}：缺有效确认日期")
    return gaps

def customization_check(candidate, requirements):
    evidence=candidate.get("customization_evidence")
    gaps=[]
    if not requirements:
        gaps.append("未记录requested_design_requirements，无法逐项核对目标设计")
    if not isinstance(evidence, dict):
        return {"status":"pending", "gaps":gaps+["未取得结构化定制证据；页面广告文案不计为确认"]}
    offer_id=str(candidate.get("offer_id") or "")
    if not offer_id or str(evidence.get("offer_id") or "") != offer_id:
        gaps.append("定制证据未绑定当前offer_id")
    offer_url=evidence.get("offer_url")
    if not isinstance(offer_url,str) or not re.fullmatch(r"https://detail\.1688\.com/offer/"+re.escape(offer_id)+r"\.html(?:\?[^\s]*)?",offer_url):
        gaps.append("定制证据缺与当前offer一致的1688链接")
    for key,label in (("factory_confirmation","实际厂家身份"),("odm_confirmation","ODM定制能力"),("sample_confirmation","目标设计打样能力")):
        gaps.extend(_confirmation_gaps(evidence.get(key),label))
    matches=evidence.get("requirements")
    if not isinstance(matches,dict):matches={}
    for requirement in requirements:
        row=matches.get(requirement)
        gaps.extend(_confirmation_gaps(row,"设计要求 "+requirement))
        if not isinstance(row,dict) or row.get("matched") is not True:
            gaps.append(f"设计要求 {requirement}：未逐项确认匹配")
    return {"status":"verified" if not gaps else "pending", "gaps":gaps}

def screen(document, selected_form=None, selected_sku=None):
    requested=document.get("requested_design_requirements") or []
    requirements=[]
    for item in requested if isinstance(requested,list) else []:
        value=item.get("id") if isinstance(item,dict) else item
        if isinstance(value,str) and value.strip() and value.strip() not in requirements:
            requirements.append(value.strip())
    result={"shortlist":[], "excluded":[], "needs_review":[], "required_count":3,
            "requested_design_requirements":requirements}
    for candidate in document.get("candidates",[]):
        category, reason=classify_supplier(candidate)
        evidence=customization_check(candidate,requirements)
        if category != "same_form":
            evidence={"status":"excluded" if category=="excluded" else "pending", "gaps":evidence["gaps"]+[reason]}
        row={**candidate, **supplier_price_reference(candidate, selected_form, selected_sku),
             "classification":category,"reason":reason,"tier_prices":format_cell(candidate.get("ranges")),
             "customization_status":evidence["status"],"customization_gaps":evidence["gaps"]}
        result[{"same_form":"shortlist","excluded":"excluded","needs_review":"needs_review"}[category]].append(row)
    result["stock_reference_count"]=len(result["shortlist"])
    result["matched_count"]=result["stock_reference_count"]  # Backward compatible; never means ODM verified.
    verified=[r for r in result["shortlist"] if r["customization_status"]=="verified"]
    result["customization_verified_offer_count"]=len(verified)
    factory_keys={str(r.get("company") or r["customization_evidence"]["factory_confirmation"].get("factory_id") or r.get("offer_id")).strip().casefold() for r in verified}
    result["customization_verified_count"]=len(factory_keys)
    result["customization_shortfall"]=max(0,result["required_count"]-result["customization_verified_count"])
    result["customization_gaps"]=[{"offer_id":str(r.get("offer_id") or ""),"gaps":r["customization_gaps"]}
                                    for r in result["shortlist"]+result["needs_review"] if r["customization_gaps"]]
    result["gaps"]=([f"已验证定制厂家候选{result['customization_verified_count']}/3，仍需补足{result['customization_shortfall']}家；现货参考不计定制完成。"]
                    if result["customization_shortfall"] else [])
    result["odm_next_action"]="按目标品类、定制隔层、来图打样、OEM/ODM另路查询；逐厂取得身份、ODM、打样及每项设计要求的带日期原始确认。"
    return result

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input",type=Path);parser.add_argument("--selected-form");parser.add_argument("--selected-sku");parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    if args.output.resolve()==args.input.resolve():parser.error("侧车输出不能覆盖原始证据")
    document=yaml.safe_load(args.input.read_text(encoding="utf-8-sig")) or {}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(screen(document,args.selected_form,args.selected_sku),ensure_ascii=False,indent=2),encoding="utf8")
