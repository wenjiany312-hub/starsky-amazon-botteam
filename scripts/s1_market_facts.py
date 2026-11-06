#!/usr/bin/env python3
"""Canonical S1 market facts. Raw input remains immutable; report consumers share this snapshot."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import yaml
from s1_acceptance_contract import floors, capability_counts

INPUT_CONTRACT = '''输入约定：
1. 回包目录：<产品根>/S1_选品板块/amazon-market-research/运行数据/。
2. 文件名以 __<工具名>.json 结尾，例如 comp_B0XXXXXXXX__product_detail.json。
3. 标准输入只存解开信封后的 data；查询参数写入同目录 tool_calls.index.json：{"calls":[{"source":"文件名__工具名.json","tool":"工具名","args":{"keyword":"词根","search_time":"2026-09","page":1}}]}。历史覆盖必须有同查询/月参数，自然位次必须有关键词归属；缺参数不猜文件名前缀、不合并为达标。完整回包须带 tool/args。
4. competitors 依赖 01_product_profile.yaml 的 competitor_asins.direct；显式列出同形态ASIN，不能靠文件数充数。
'''

OUT = Path("S1_选品板块/amazon-market-research/01_market_facts.json")
CRITERIA_KEYS = ("strongest_counterargument", "pass_hold_stop", "allowable_landed_cost", "coverage_downgrade", "unknown_attributes", "normalized_unit_price", "cohort_comparison", "listing_age", "monthly_trend", "latest_order_date")

def number(value, positive=False):
    if isinstance(value, dict): value = value.get("value")
    if value is None or isinstance(value, bool): return None
    try: n = float(value)
    except (ValueError, TypeError): return None
    return n if math.isfinite(n) and n >= 0 and (not positive or n > 0) else None

def format_cell(value):
    if value is None or value == "": return "未取得"
    if isinstance(value, dict): return "；".join(f"{k}：{format_cell(v)}" for k, v in value.items()) or "未取得"
    if isinstance(value, (list, tuple)):
        if all(isinstance(v, (list, tuple)) and len(v)==2 for v in value):
            return "；".join(f"≥{format_cell(v[0])}件：¥{format_cell(v[1])}" for v in value) or "未取得"
        return "、".join(format_cell(v) for v in value) or "未取得"
    if isinstance(value, (float,int)) and number(value) is None: return "未取得"
    return str(value).replace("|", "／").replace("\n", " ")

def unwrap(payload):
    if isinstance(payload, dict) and "response" in payload: return unwrap(payload["response"])
    if isinstance(payload, dict) and isinstance(payload.get("content"), list):
        texts=[x.get("text", "") for x in payload["content"] if isinstance(x,dict) and x.get("type")=="text"]
        for t in texts:
            try: return unwrap(json.loads(t))
            except (ValueError,TypeError): pass
        return "\n".join(texts)
    if isinstance(payload, dict) and "data" in payload: return payload["data"]
    return payload

def read(path):
    if not path.exists(): return {}
    try: return yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    except (ValueError,yaml.YAMLError): return {}

def classify_product(row):
    title = str(row.get("title") or "").lower()
    # A storage container for kits/tools is not itself a kit. Judge the sold object.
    if re.search(r"needle\s*minder|needleminder|\b(?:box|bobbins?|winder|stand|huggers?)\b|绕线板|收纳盒", title):
        return "excluded", "辅料、支架或其他形态"
    storage = bool(re.search(r"\b(?:bag|bags|tote|pouch|case)\b|收纳[包袋]|项目包", title))
    kit = bool(re.search(r"\b(?:embroidery|cross stitch|needlepoint)\s+(?:starter\s+)?kits?\b|刺绣套件|绣花套件|制作套装", title))
    bundle = bool(re.search(r"\b(?:with|includes?|including)\s+(?:\d+\s+)?(?:patterns?|threads?|needles?|hoops?|tools?)\b|含(?:线材|针线|图案)|配套(?:线材|针线)", title))
    storage_for = bool(re.search(r"kits?\s+storage|bag\s+only|bag.{0,35}\bfor\b|收纳[包袋]", title))
    if (kit and not storage_for) or (storage and bundle and not storage_for): return "excluded", "制作套件或含工具材料组合品"
    if storage and re.search(r"embroidery|cross stitch|needlepoint|thread|floss|刺绣|绣线|十字绣",title): return "same_form", "刺绣或线材收纳包，空包内容需逐款核验"
    if re.search(r"\b(?:box|bobbins?|winder|stand|huggers?|needles?|scissors|mat|hoops?)\b|五金|绕线板|收纳盒",title): return "excluded", "辅料、支架或其他形态"
    return "needs_review", "标题不足以确认同形态"

def classify_supplier(row):
    if str(row.get("unit", "")).strip() in {"根", "米"}: return "excluded", "按根或米销售，非目标成品包"
    title=str(row.get("title") or "")
    if re.search(r"五金|金属杆|螺丝",title) and not re.search(r"收纳包|箱包",title): return "excluded", "五金类目不匹配"
    if re.search(r"包|袋|箱包|\bbag\b",title,re.I): return "same_form", "包袋形态参考；定制能力须独立核实"
    return "needs_review", "缺产品形态证据"

def supplier_price_reference(candidate, selected_form=None, selected_sku=None):
    skus=[(str(s.get("name") or ""),number(s.get("price"),True)) for s in candidate.get("skus",[]) if isinstance(s,dict)]
    skus=[s for s in skus if s[1] is not None]
    match=[s for s in skus if s[0]==selected_sku] if selected_sku else []
    if not match and selected_form: match=[s for s in skus if str(selected_form).lower() in s[0].lower()]
    result={"offer_id":str(candidate.get("offer_id") or ""),"url":candidate.get("url"),"sku_name":None,"unit_price_cny":None,"price_min_cny":None,"price_max_cny":None,"price_basis":"unavailable","page_price":"未取得"}
    if match:
        name,price=sorted(match,key=lambda s:(s[1],s[0]))[0]
        result.update(sku_name=name,unit_price_cny=price,price_min_cny=price,price_max_cny=price,price_basis="exact_sku" if selected_sku==name else "same_form_reference",page_price=f"¥{price:.2f}（{name}；页价参考）")
    else:
        prices=[s[1] for s in skus] or [n for n in [number(candidate.get("min_price"),True),number(candidate.get("max_price"),True)] if n is not None]
        if prices:
            low,high=min(prices),max(prices)
            result.update(price_min_cny=low,price_max_cny=high,price_basis="sku_range",page_price=f"¥{low:.2f}–{high:.2f}（SKU页价区间）")
    return result

def quantile(values, q):
    values=sorted(values)
    if not values:return None
    pos=(len(values)-1)*q; low=int(pos); high=min(low+1,len(values)-1)
    return round(values[low]+(values[high]-values[low])*(pos-low),2)

def canonicalize_competitors(rows):
    rows=copy.deepcopy(rows)
    # One complete record per ASIN. Never fill fields from different snapshots.
    by_asin={}
    for row in sorted(rows,key=lambda r:(int(r.get("source_priority",0)), str(r.get("observed_at") or ""),str(r.get("source") or "")),reverse=True):
        asin=str(row.get("asin") or "")
        if asin and asin not in by_asin:
            for k in ("price_usd","monthly_sales","reviews","fba_fee","weight_g"):row[k]=number(row.get(k),k=="price_usd")
            row["parent_asin"]=row.get("parent_asin") or asin
            by_asin[asin]=row
    normalized=sorted(by_asin.values(),key=lambda r:r["asin"])
    groups={}
    for row in normalized:groups.setdefault(row["parent_asin"],[]).append(row)
    reps=[]; duplicates=[]
    for parent,children in groups.items():
        # Deterministic representative: usable price first, then smallest ASIN.
        rep=sorted(children,key=lambda r:(r["price_usd"] is None,r["asin"]))[0]
        for row in children:
            row["counted"]=row is rep
            row["representative_asin"]=rep["asin"]
            row["counting_note"]="父体代表" if row is rep else "同父体，不重复计入"
            if row is not rep:duplicates.append(row)
        reps.append(rep)
    prices=[r["price_usd"] for r in reps if r["price_usd"] is not None]
    sales=[r["monthly_sales"] for r in reps if r["monthly_sales"] is not None]
    return {"competitors":normalized,"representatives":reps,"duplicates":duplicates,"price_bands":{k:quantile(prices,q) for k,q in [("p25",.25),("p50",.5),("p75",.75),("p90",.9)]},"price_sample_count":len(prices),"monthly_sales_total":sum(sales) if sales else None,"sales_sample_count":len(sales),"dedup_rule":"先选每ASIN完整快照，再按parent_asin去重；优先有效价格，其次ASIN字典序最小。其他子体保留但不参与分位、销量和样本数。"}

def validate_criteria(criteria):
    errors=[]
    for key in CRITERIA_KEYS:
        value=criteria.get(key)
        if not isinstance(value,dict):errors.append(f"{key}: missing structured criterion");continue
        if value.get("status") not in {"available","not_calculable","pending"}:errors.append(f"{key}: invalid status")
        if not value.get("source"):errors.append(f"{key}: missing source")
        if value.get("status")=="available" and value.get("value") is None:errors.append(f"{key}: missing value")
        if value.get("status")!="available" and not value.get("reason"):errors.append(f"{key}: missing reason")
    return errors

def _source(path, root, dates):
    rel=path.relative_to(root).as_posix()
    return {"source":rel,"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"observed_at":dates.get(rel)}

def _normalized(row, source, priority=0):
    return {"asin":row.get("asin") or row.get("Asin"),"parent_asin":row.get("parent_asin"),"title":row.get("title"),"brand":row.get("brand"),"price_usd":number(row.get("price"),True),"monthly_sales":number(row.get("monthly_sales_volume",row.get("monthly_sale_volume"))),"reviews":number(row.get("review_count",row.get("rating_count"))),"fba_fee":number(row.get("fba_fee")),"online_date":row.get("online_date"),"package_size_cm":row.get("package_size_cm"),"weight_g":number(row.get("weight_g")),"source_priority":priority,"raw":row,**source}


THEMES = Path("S1_选品板块/amazon-market-research/review_themes.yaml")
# S1-R31: category-neutral defaults. The old list was written for a yarn bag (tassels, zippers, embroidery, tiles)
# and labelled a window-screen kit's buyers as "外观配色与个性表达". Each case can write its own themes file.
GENERIC_PAINS = [
    ('坏了 / 不耐用', r"\b(?:broke|broken|fell apart|stopped working|tore|ripped|came apart|snapped|wore out)\b"),
    ('尺寸 / 适配不符', r"(?:too (?:small|big|short|long|narrow|wide|thin|thick)|doesn.t fit|did not fit|didn.t fit|wrong size)"),
    ('材质薄 / 廉价感', r"(?:\bthin\b|flimsy|cheap(?:ly)? (?:made|material|feel)|poor quality)"),
    ('安装 / 使用麻烦', r"(?:hard to (?:install|use|assemble|cut)|difficult to (?:install|use)|instructions? (?:were|was|are) (?:unclear|missing|useless))"),
    ('与图片 / 描述不符', r"(?:not as (?:pictured|described|shown|advertised)|different (?:from|than) (?:the )?(?:picture|photo|description))"),
    ('缺件 / 少配件', r"(?:missing (?:parts?|pieces?|screws?)|didn.t come with|did not come with)"),
]
GENERIC_MOTIVES = [
    ('好用 / 易装', r"easy to (?:use|install|assemble|apply|cut)"),
    ('结实耐用', r"(?:sturdy|durable|well.made|holds up|strong)"),
    ('尺寸合适', r"(?:fits? (?:perfectly|great|well)|perfect size|right size)"),
    ('性价比', r"(?:great value|worth the (?:money|price)|good price|affordable|inexpensive)"),
    ('外观好看', r"(?:looks? (?:great|nice|good)|beautiful|cute|stylish)"),
    ('送礼', r"\bgift\b"),
]


def theme_definitions(root=None):
    """The case's own themes (review_themes.yaml: pains / motives, each {name, pattern}) or the generic ones."""
    path = Path(root) / THEMES if root else None
    if not path or not path.is_file():
        return GENERIC_PAINS, GENERIC_MOTIVES, 'generic'
    doc = read(path) or {}
    out = []
    for key in ('pains', 'motives'):
        rows = []
        for row in doc.get(key) or []:
            try:
                re.compile(str(row['pattern']))
            except (KeyError, TypeError, re.error) as error:
                raise ValueError(f'{THEMES.as_posix()} 的 {key} 主题「{row.get("name") if isinstance(row, dict) else row}」匹配词写错了：{error}')
            rows.append((str(row['name']), str(row['pattern'])))
        out.append(rows or (GENERIC_PAINS if key == 'pains' else GENERIC_MOTIVES))
    return out[0], out[1], 'case'


def review_themes(reviews, root=None):
    """Observable phrase counts, not a claim of exhaustive sentiment coding.

    Restrict complaints to explicit negative phrases. A positive mention of a
    zipper or fabric never becomes negative evidence merely via a keyword.
    """
    pains, motives, origin = theme_definitions(root)
    def rows_for(definitions, label):
        result=[]
        for name, pattern in definitions:
            hits=[row for row in reviews if re.search(pattern, str(row.get('content') or ''), re.I|re.S)]
            if not hits:continue
            example=hits[0]
            result.append({label:name,'count':len(hits),'share':format(len(hits)/len(reviews),'.1%'),
                'quote':example['content'], 'source':example.get('asin'),
                'review_source':example.get('source'), 'evidence_level':'observed_phrase'})
        return sorted(result,key=lambda row:-row['count'])
    return {'pain_points':rows_for(pains,'pain'),'purchase_motives':rows_for(motives,'motive'),
            'review_sample_count':len(reviews),'negative_rating_count':sum(isinstance(r.get('star_rating'),(int,float)) and r['star_rating']<=3 for r in reviews),
            'theme_source': origin,
            'theme_method':'逐条短语匹配，主题可重叠；占比以全部已取得评论为分母，不等于全部买家投诉率。'
                           + ('主题按本案 review_themes.yaml。' if origin == 'case' else '主题是通用默认；按品类写 review_themes.yaml 会更准。')}


def apply_demand_artifacts(root, facts, detail_snapshots=()):
    """Adapt the real M2 product, review and trend artifacts; verify raw selectors.

    Legacy filename adapters remain intact. Counts are derived from observed
    rows, never a producer's declared count or a missing-file default.
    """
    path = Path(root) / OUT.parent / '02_demand_validation.yaml'
    if not path.is_file():
        return facts
    from s1_research_gates import sourced, select, review_identity
    document = read(path)
    cache = {}
    rows = []
    sources = {path.relative_to(root).as_posix()}
    def observed(cell):
        answer = sourced(cell, root, cache)
        sources.add(cell['source']['path'])
        return answer
    for item in document.get('same_form_top10') or []:
        price, sales, reviews = (observed(item[key]) for key in ('price', 'monthly_sales', 'review_count'))
        source = item['price']['source']
        parent_pointer = source['selector'].rsplit('/', 1)[0]
        raw = select(cache[(Path(root)/source['path']).resolve()], parent_pointer) if parent_pointer else {}
        raw = raw if isinstance(raw, dict) else {}
        priority=2 if any(raw.get(key) is not None for key in ('brand','fba_fee','online_date','weight_g','package_size_cm')) else 1
        row = _normalized(raw, {'source': source['path'], 'observed_at': item['price'].get('time_window')}, priority)
        row.update(asin=item['asin'], parent_asin=item.get('parent_asin') or raw.get('parent_asin'),
                   price_usd=number(price, True), monthly_sales=number(sales), reviews=number(reviews),
                   form=item.get('form'), audience=item.get('audience'))
        rows.append(row)
    if rows:
        requested={row['asin'] for row in rows}
        complete=[row for row in detail_snapshots if row['asin'] in requested]
        facts.update(canonicalize_competitors([*(facts.get('competitors') or []), *complete, *rows]))
        facts['coverage']['competitors'].update(observed=len(facts['competitors']), sources=sorted(sources))
    reviews = {}
    for item in document.get('reviews') or []:
        text = observed(item['text'])
        identity = review_identity(item['text'], item, root, cache)
        pointer=item['text']['source']
        parent_pointer=pointer['selector'].rsplit('/',1)[0]
        raw_review=select(cache[(Path(root)/pointer['path']).resolve()], parent_pointer) if parent_pointer else {}
        reviews[identity] = {'asin': item['asin'], 'id': item['id'], 'content': text,
            'star_rating': raw_review.get('star_rating') if isinstance(raw_review,dict) else None,
            'review_date':raw_review.get('review_date') if isinstance(raw_review,dict) else None, 'source':pointer}
    merged=list(facts.get('reviews') or []);parents={row['asin']:row.get('parent_asin') or row['asin'] for row in facts.get('competitors',[])}
    def text_key(row):
        return (parents.get(row.get('asin'),row.get('asin')),row.get('review_date'),re.sub(r'\s+',' ',row.get('content') or '').strip())
    old_texts={text_key(row):row for row in merged}
    for row in reviews.values():
        old=old_texts.get(text_key(row))
        # Reconcile duplicate acquisition paths, not different genuine buyers
        # who happen to write the same short text at different raw locations.
        if old is not None and not (old.get('id') and row.get('id') and old['id']!=row['id']):
            old.setdefault('additional_sources',[]).append(row.get('source'))
        else:
            merged.append(row)
    facts['reviews'] = merged
    facts['coverage']['reviews'].update(observed=len(merged), sources=sorted(set(facts['coverage']['reviews'].get('sources') or [])|sources))
    trends = {}
    for cohort in document.get('top3_trends') or []:
        facts.setdefault('trend_by_asin', {})[cohort['asin']] = []
        for item in cohort.get('months') or []:
            sales = observed(item['sales'])
            facts['trend_by_asin'][cohort['asin']].append({'month': item['month'], 'sales': sales})
            trends.setdefault(item['month'], []).append(sales)
    if trends:
        facts['monthly_trend'] = [{'month': month, 'sales': sum(values), 'sample_count': len(values)} for month, values in sorted(trends.items())]
        facts['coverage']['monthly_trend'].update(observed=len(trends), sources=sorted(sources))
    keyword = document.get('core_keyword') or {}
    if keyword.get('monthly_searches'):
        searches = observed(keyword['monthly_searches'])
        facts['demand_evidence'] = [{'provider': 'Sorftime', 'keyword': keyword.get('term'),
            'value': searches, 'metric': 'monthly_search_volume',
            'data_date': keyword['monthly_searches']['time_window'], 'source': keyword['monthly_searches']['source']['path']}]
        facts['coverage']['keyword_signals'].update(observed=len({keyword['term']} if keyword.get('term') else set()), sources=sorted(sources))
    for key in ('competitors', 'reviews', 'monthly_trend', 'keyword_signals'):
        coverage = facts['coverage'][key]
        coverage['status'] = 'met' if coverage['observed'] >= coverage['required'] else 'data_gap'
        coverage['reason'] = None if coverage['status'] == 'met' else f"已取得 {coverage['observed']} 项，仍需补足 {coverage['required']} 项；新上架产品保留实际可用月份。"
    analysis = facts.setdefault('market_analysis', {})
    positioning = document.get('validated_positioning') or {}
    analysis.update(audience=positioning.get('audience'), positioning=positioning.get('form'))
    analysis.update(review_themes(facts['reviews'], root))
    dates=[str(row.get('observed_at') or '')[:10] for row in facts.get('competitors',[]) if row.get('observed_at')]
    as_of=next((date for date in dates if re.fullmatch(r'\d{4}-\d{2}-\d{2}',date)),None)
    if as_of:
        from datetime import date
        end=date.fromisoformat(as_of)
        dated=[]
        for row in facts.get('competitors',[]):
            listing=(row.get('raw') or {}).get('listing_date') or row.get('online_date')
            try:age=(end-date.fromisoformat(str(listing))).days
            except (ValueError,TypeError):continue
            if age>=0:dated.append(age)
        analysis['new_products_top20']=f'{sum(age<=365 for age in dated)} / {len(dated)} 个可核实上架日样本（当前池，非全站Top20）'
    analysis['seasonality']='仅依据当前样本的逐月销量；新上架样本月份不齐，不能据此宣称全年稳定'
    # M2 saves several MCP calls inside one response file. The older flat-file
    # adapter otherwise reports zero even when genuine variation replies exist.
    variation_queries={}
    for response_path in sorted((path.parent/'运行数据').glob('*.json')):
        saved=read(response_path)
        if not isinstance(saved,dict):continue
        entries=saved.get('calls') or saved
        if not isinstance(entries,dict):continue
        for key,entry in entries.items():
            if not isinstance(entry,dict) or not str(entry.get('tool') or '').endswith('__product_variations'):continue
            args=entry.get('args') or entry.get('arguments') or {}
            queried=args.get('asin')
            data=entry.get('decoded') or unwrap(entry.get('response') or entry)
            if isinstance(data,dict):data=data.get('data')
            if not queried or not isinstance(data,list):continue
            children=[row for row in data if isinstance(row,dict) and (row.get('asin') or row.get('Asin'))]
            if not children:continue
            rel=response_path.relative_to(root).as_posix();sources.add(rel)
            variation_queries[str(queried)]={'asin':str(queried),'children':children,'source':rel,'call':str(key)}
    if variation_queries:
        facts['variation_details']=list(variation_queries.values())
        node=facts['coverage']['variations'];node.update(observed=len(variation_queries),sources=sorted({r['source'] for r in variation_queries.values()}))
        node['status']='met' if node['observed']>=node['required'] else 'data_gap'
        node['reason']=f"已取得 {node['observed']} 个查询样本的真实变体明细；目标 {node['required']} 个样本。子体条数不当作查询样本数。"
    reps = facts.get('representatives') or []
    sales = sorted([row['monthly_sales'] for row in reps if row.get('monthly_sales') is not None], reverse=True)
    if sales and sum(sales):
        analysis['concentration'] = f"当前同形态样本前五占比 {sum(sales[:5])/sum(sales):.1%}；样本口径"
    revenue = [r['monthly_sales']*r['price_usd'] for r in reps if r.get('monthly_sales') is not None and r.get('price_usd') is not None]
    if revenue:
        analysis['monthly_revenue'] = f"${sum(revenue):,.2f} / 月（当前同形态样本估算）"
    for relative in sorted(sources):
        source_path = Path(root)/relative
        if source_path.is_file() and not any(item.get('source') == relative for item in facts['source_manifest']):
            facts['source_manifest'].append(_source(source_path, root, {}))
    return facts

def build_market_facts(run_dir):
    from r01_case_store import CompactCase
    with CompactCase(run_dir).materialize() as view:
        return _build_market_facts_native(view.root)


def _build_market_facts_native(run_dir):
    root=Path(run_dir); market=root/OUT.parent; rawdir=market/"运行数据"
    profile=read(market/"01_product_profile.yaml")
    inputs=read(root/"s1_decision_inputs.yaml")
    manifest=read(rawdir/"source_manifest.json")
    dates={r.get("path"):r.get("queried_date") for r in manifest if isinstance(r,dict)} if isinstance(manifest,list) else {}
    direct=set((profile.get("competitor_asins") or {}).get("direct",[]))
    if not direct:direct={r.get("asin") for r in (inputs.get("demand") or {}).get("market_price_evidence",[]) if isinstance(r,dict)}
    records=[]; payloads=[]; sources=[]
    for path in sorted(rawdir.glob("*.json")):
        if path.name=="source_manifest.json":continue
        payload=unwrap(read(path)); src=_source(path,root,dates)
        payloads.append((path,payload,src));sources.append(src)
        if isinstance(payload,dict) and payload.get("asin") and ("parent_asin" in payload or path.stem.startswith('product_detail_')):
            records.append(_normalized(payload,src,2))
        elif isinstance(payload,list):
            for row in payload:
                if isinstance(row,dict) and row.get("asin") and "title" in row:
                    records.append(_normalized(row,src,1))
    # Product-detail snapshots outrank category/search snapshots, regardless of their field count.
    for r in records:r["observed_at"]=r.get("observed_at") or ""
    all_data=canonicalize_competitors(records)
    selected=[r for r in all_data["competitors"] if r["asin"] in direct] if direct else [r for r in all_data["competitors"] if classify_product(r)[0]=="same_form"]
    facts=canonicalize_competitors(selected)
    lookup={r["asin"]:r for r in facts["competitors"]}
    ledger=[]
    for path,payload,src in payloads:
        if "categoryproducts" not in path.stem.lower() and "category_products" not in path.stem.lower():continue
        for row in payload if isinstance(payload,list) else []:
            if not isinstance(row,dict) or not row.get("asin"):continue
            r=copy.deepcopy(lookup.get(row["asin"]) or _normalized(row,src,1))
            category,reason=classify_product(r)
            r.update(classification=category,classification_label={"same_form":"同形态","needs_review":"待复核","excluded":"排除"}[category],classification_reason=reason)
            ledger.append(r)
    ledger=list({r["asin"]:r for r in ledger}.values())
    facts["ledger"]=ledger
    facts["ledger_counts"]={k:sum(r["classification"]==k for r in ledger) for k in ("same_form","needs_review","excluded")}
    supplier_doc=read(root/"S1_选品板块/alibaba-supplier-evaluation/02_supplier_evidence.yaml")
    evaluated=read(root/"S1_选品板块/alibaba-supplier-evaluation/02_supplier_evaluation.json")
    if evaluated:
        supplier_doc=evaluated
    selected_form=(profile.get("selection") or {}).get("selected_form") or profile.get("selected_form")
    supplier_rows=[];excluded=[]
    for row in supplier_doc.get("candidates",[]):
        category,reason=classify_supplier(row)
        normalized={**row,"classification":category,"classification_reason":reason,**supplier_price_reference(row,selected_form,profile.get("selected_variant")),"tier_prices":format_cell(row.get("ranges"))}
        (supplier_rows if category=="same_form" else excluded).append(normalized)
    facts.update(supplier_shortlist=supplier_rows,supplier_excluded=excluded,supplier_shortlist_count=len(supplier_rows),supplier_required_count=3)
    product_label=profile.get("product_type") or profile.get("product_name") or "目标产品"
    facts["supplier_search_plan"]={"status":"pending_verification","existing_stock_count":len(supplier_rows),"verified_custom_manufacturers":supplier_doc.get("manufacturers_verified",0),"required_count":3,"queries":[f"{product_label} 定制 隔层 来图打样",f"{product_label} 生产厂家 OEM ODM 打样"],"reason":"现货参考不能证明目标设计可定制；需逐厂留存目标规格、打样、MOQ、隔层定制证据。"}
    demand=[]; review_keys=set(); review_rows={}; variation_parents=set(); nodes=set(); trend=[]; keyword_sources=[]; organic=[]
    for path,payload,src in payloads:
        name=path.stem.lower()
        if isinstance(payload,dict) and payload.get("keyword") and number(payload.get("monthly_search_volume")) is not None:
            demand.append({"provider":"Sorftime","keyword":payload["keyword"],"value":number(payload["monthly_search_volume"]),"metric":"monthly_search_volume","data_date":str(payload.get("update") or src.get("observed_at") or ""),**src});keyword_sources.append(src["source"])
        if name=="sif_demand" and isinstance(payload,dict):
            for row in payload.get("profiles",[]):
                if not isinstance(row,dict):continue
                metric=next((k for k in ("search_volume_week","search_volume_90d","searchVolume") if number(row.get(k)) is not None),None)
                if metric:demand.append({"provider":"SIF","keyword":row.get("keyword"),"value":number(row[metric]),"metric":metric,"data_date":row.get("data_period") or row.get("ctr_data_period") or src.get("observed_at"),**src});keyword_sources.append(src["source"])
        if "review" in name and isinstance(payload,list):
            matched_asins=set(re.findall(r"B0[A-Z0-9]{8}",path.stem))
            if matched_asins and direct and not matched_asins.intersection(direct):continue
            for index,row in enumerate(payload):
                if isinstance(row,dict) and row.get("content"):
                    asin=next(iter(matched_asins),"");key=(asin,row.get('review_date'),row['content'])
                    review_keys.add(key)
                    review_rows[key]={**row,'asin':asin,'source':{'path':src['source'],'decoded_index':index,'decoder':'s1_market_facts.unwrap','field':'content'}}
        if "variation" in name and isinstance(payload,list) and payload:
            for row in payload:
                asin=row.get("asin",row.get("Asin")) if isinstance(row,dict) else None
                if asin in lookup:variation_parents.add(lookup[asin]["parent_asin"])
        if isinstance(payload,dict) and payload.get("node_id") and "top100_monthly_sales_volume" in payload:nodes.add(str(payload["node_id"]))
        if "trend" in name and isinstance(payload,list) and any(isinstance(r,str) and re.match(r"\d{4}-\d{2}=",r) for r in payload):trend=payload
        original=read(path)
        if isinstance(original,dict) and original.get("args",{}).get("position_type")==1:
            organic.append({"keyword":original["args"].get("keyword"),"rows":len(payload) if isinstance(payload,list) else 0,"error":payload if isinstance(payload,str) else None,**src})
    roots={str(x).lower() for x in profile.get("core_roots",[]) if isinstance(x,str)}
    if roots:demand=[d for d in demand if str(d.get("keyword","")).lower() in roots]
    keyword_sources=[d["source"] for d in demand]
    facts["demand_evidence"]=demand;facts["monthly_trend"]=trend;facts["organic_queries"]=organic
    counts={"category_comparison":(len(nodes),"category_report"),"competitors":(len(facts["competitors"]),"product_detail"),"variations":(len(variation_parents),"product_variations"),"reviews":(len(review_keys),"product_reviews"),"monthly_trend":(len(trend),"category_trend"),"keyword_signals":(len(set(keyword_sources)),"keyword_detail / market_get_keyword_trend")}
    coverage={}
    required_floors=floors()
    for key,(observed,tool) in counts.items():
        required=required_floors[key]
        coverage[key]={"observed":observed,"required":required,"tool":tool,"status":"met" if observed>=required else "data_gap","error":None,"reason":None if observed>=required else "已存原始回包未覆盖要求；没有可核实的报错或配额证据，需续拉，不能归因于工具不可用。","next_action":f"调用 {tool} 补足至 {required}；保存参数、完整回包、时间与哈希。"}
    patterns={"reviews":"review", "variations":"variation", "monthly_trend":"trend", "keyword_signals":"keyword", "category_comparison":"category", "competitors":"detail"}
    for key, pattern in patterns.items():
        coverage[key]["sources"]=[src["source"] for path,payload,src in payloads if pattern in path.stem.lower()]
    capabilities,organic_queries=capability_counts(root,payloads,roots)
    facts['organic_queries']=organic_queries
    for key,node in capabilities.items():
        required=required_floors[key];node['required']=required
        if node.get('status')!='not_applicable':
            node.update(status='met' if node['observed']>=required else 'data_gap',error=None)
            node.setdefault('reason','本地没有足够的真实回包证据；需采集或保存宿主工具清单，不推断工具不存在。')
            node.setdefault('next_action',f'调用 {node["tool"]} 补足证据；保留参数、回包、时间和哈希。')
        coverage[key]=node
    facts["coverage"]=coverage
    facts["criteria"]=copy.deepcopy(profile.get("market_criteria") or {})
    facts["criteria_errors"]=validate_criteria(facts["criteria"])
    facts.update(schema_version=1,source_manifest=sources,source_snapshot_policy="同ASIN优先product_detail完整回包；同级选有日期的最新记录；不拼接字段。",input_preservation="原始输入不改写；本文件为可重建侧车。")
    facts['reviews']=list(review_rows.values())
    facts=apply_demand_artifacts(root, facts, detail_snapshots=all_data['competitors'])
    from s1_category_comparison import required, category_coverage
    scope=read(root/'S1_选品板块/amazon-market-research/comparison_scope.yaml')
    if required(root) or scope.get('schema')=='r05.adjacent.v1':
        category=category_coverage(root)
        facts['coverage']['category_comparison']=category
        for source in category['source_manifest']:
            existing=next((i for i,r in enumerate(facts['source_manifest']) if r.get('source')==source['source']),None)
            if existing is None:facts['source_manifest'].append(copy.deepcopy(source))
            else:facts['source_manifest'][existing]=copy.deepcopy(source)
    # PL-02：能由数据算出的判据原生派生；作者在 market_criteria 里亲手写的条目永远优先。
    from s1_criteria import derive_criteria, merge_criteria
    facts['criteria']=merge_criteria(copy.deepcopy(profile.get('market_criteria') or {}), derive_criteria(facts, root, profile))
    facts['criteria_errors']=validate_criteria(facts['criteria'])
    # R09: category/pool/keyword layers for the market report; additive, never replaces gate facts.
    from s1_market_overview import build_overview
    facts['market_overview']=build_overview(root, facts)
    return facts

def _build_enrichment_facts_legacy(directory):
    """Summarize separately dated read-only calls, never overwrite historical product values."""
    directory=Path(directory); calls=[]; reviews={}; variations=[]; categories=[]; traffic=[]; peak=[]; organic=[]
    for path in sorted((directory/"运行数据").glob("*.json")):
        doc=read(path); data=unwrap(doc); tool=doc.get("tool",""); args=doc.get("args") or {}
        call={"tool":tool,"args":args,"queried_at":doc.get("queried_at"),"source":path.relative_to(directory).as_posix(),"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"rows":len(data) if isinstance(data,list) else None,"response_message":data if isinstance(data,str) else doc.get("error")}
        calls.append(call)
        if tool.endswith("__product_reviews") and isinstance(data,list):
            for row in data:
                if isinstance(row,dict) and row.get("content"):
                    # Same review returned on sibling ASINs must not count twice.
                    key=(row.get("review_date"),row.get("title"),row.get("content"))
                    reviews.setdefault(key,{**row,"asin":args.get("asin"),"source":call["source"]})
        if tool.endswith("__product_variations"):
            children = sorted({str(r.get('asin') or r.get('Asin')) for r in data if isinstance(r,dict) and (r.get('asin') or r.get('Asin'))}) if isinstance(data,list) else []
            variations.append({"asin":args.get("asin"),"variant_asins":children,"count":len(children),"status":"returned" if children else "data_gap","source":call["source"],"response_message":call["response_message"]})
        if tool.endswith("__category_report") and isinstance(data,dict):
            categories.append({"node_id":data.get("node_id"),"monthly_sales":number(data.get("top100_monthly_sales_volume")),"monthly_sales_usd":number(data.get("top100_monthly_sales_amount")),"top3_share":number(data.get("top3_product_sales_volume_share")),"query_start":args.get("query_start"),"query_date":args.get("query_date"),"source":call["source"],"queried_at":call["queried_at"]})
        if tool.endswith("__product_traffic_terms") and isinstance(data,list):
            traffic.extend({**r,"asin":args.get("asin"),"page":args.get("page",1),"source":call["source"]} for r in data if isinstance(r,dict))
        if tool.endswith("__product_search_from_history") and isinstance(data,list):
            peak.extend({**r,"requested_node_id":args.get("node_id"),"search_time":args.get("search_time"),"source":call["source"]} for r in data if isinstance(r,dict))
        if tool.endswith("__keyword_search_results"):
            organic.append({"keyword":args.get("keyword"),"observed_rows":len(data) if isinstance(data,list) else 0,"source":call["source"],"reason":"回包无自然位次字段，结果含跨品类产品并按销量降序；不能据数组顺序认定自然前5。"})
    traffic_unique=list({(r["asin"],r.get("keyword")):r for r in traffic}.values())
    # Product-search rank filtering may use another category. Retain all rows and explicitly verify the target node.
    peak_unique=list({r.get("asin"):r for r in peak if r.get("asin")}.values())
    groups=[]
    for row in variations:
        if not row['count'] or not row['asin']: continue
        family=set(row['variant_asins']) | {row['asin']}
        linked=[g for g in groups if g & family]
        for group in linked:
            family.update(group); groups.remove(group)
        groups.append(family)
    return {"schema_version":1,"original_snapshot_modified":False,"comparison_scope":read(directory/"comparison_scope.yaml"),"calls":calls,"review_count":len(reviews),"review_target":100,"reviews":list(reviews.values()),"review_gap_reason":f"product_reviews仅返回近一年已收录评论；已查询{sum(c['tool'].endswith('__product_reviews') for c in calls)}个ASIN，去重{len(reviews)}条，不足目标时保留缺口；具体零行/错误见calls.response_message。","variation_queried_count":len({r["asin"] for r in variations if r["asin"]}),"variation_returned_count":len(groups),"variation_parent_groups":[sorted(g) for g in groups],"variations":variations,"category_comparison":[r for r in categories if not r["query_start"]],"peak_category_statistics":[r for r in categories if r["query_start"]],"peak_products":peak_unique,"peak_product_count":len(peak_unique),"peak_top100_status":"needs_rank_audit","peak_top100_reason":"历史查询限定子类1–100仍返回超过100行；需按目标node_id逐条核对子类rank，不能把全部结果称为Top100。","traffic_terms":traffic_unique,"ad_keyword_observed_counts":{asin:sum(r["asin"]==asin and "ad" in str(r.get("exposure_position","")).lower() for r in traffic_unique) for asin in sorted({r["asin"] for r in traffic_unique})},"ad_keyword_count_basis":"已取页内去重的广告词下限，未证实全分页完结时不称总量","organic_queries":organic,"organic_top5_status":"data_gap"}


def build_enrichment_facts(directory):
    result=_build_enrichment_facts_legacy(directory)
    directory=Path(directory)
    payloads=[(p,unwrap(read(p)),{'source':p.relative_to(directory).as_posix()}) for p in sorted((directory/'运行数据').glob('*.json'))]
    result['capability_coverage'],result['organic_queries']=capability_counts(directory,payloads,set())
    return result


def apply_enrichment_coverage(facts, enrichment, source_prefix):
    """Return a copy with explicit supplementary coverage, preserving historical competitor facts."""
    result=copy.deepcopy(facts)
    def update(key, observed, tools, reason=None, status=None):
        item=result["coverage"][key]
        calls=[c for c in enrichment.get("calls",[]) if any(c.get("tool","").endswith("__"+t) for t in tools)]
        item.update(observed=observed, status=status or ("met" if observed>=item["required"] else "data_gap"), reason=reason,
                    sources=[str(Path(source_prefix)/c["source"]).replace("\\","/") for c in calls],
                    receipt_hashes={str(Path(source_prefix)/c["source"]).replace("\\","/"):c["sha256"] for c in calls},
                    snapshot="supplementary", queried_at=[c.get("queried_at") for c in calls])
        item["error"]=None
    update("reviews",enrichment["review_count"],["product_reviews"],enrichment["review_gap_reason"])
    update("variations",enrichment["variation_returned_count"],["product_variations"])
    # Scope is explicit case evidence, never hardcoded category IDs in shared code.
    adjacent={str(x) for x in (enrichment.get("comparison_scope") or {}).get("node_ids",[])}
    categories=[r for r in enrichment["category_comparison"] if str(r["node_id"]) in adjacent]
    if result['coverage']['category_comparison'].get('contract')!='r05.adjacent.v1':
        update("category_comparison",len({r["node_id"] for r in categories}),["category_report"])
    for key,node in enrichment.get('capability_coverage',{}).items():
        item=result['coverage'][key];required=item['required']
        for stale in ('not_applicable_evidence','status','error','reason','next_action'):item.pop(stale,None)
        item.update(copy.deepcopy(node))
        item['sources']=[str(Path(source_prefix)/p).replace('\\','/') for p in node.get('sources',[])]
        if node.get('not_applicable_evidence'):
            item['not_applicable_evidence']['source']=str(Path(source_prefix)/node['not_applicable_evidence']['source']).replace('\\','/')
        if item.get('status')!='not_applicable':item['status']='met' if item['observed']>=required else 'data_gap'
    criteria=result.get('criteria')
    if isinstance(criteria,dict) and (criteria.get('coverage_downgrade') or {}).get('origin')=='native':
        from s1_criteria import coverage_downgrade
        criteria['coverage_downgrade']=coverage_downgrade(result)
        result['criteria_errors']=validate_criteria(criteria)
    result["supplementary_evidence"]={"source":str(Path(source_prefix)/"enrichment_facts.json").replace("\\","/"),"review_count":enrichment["review_count"],"variation_returned_count":enrichment["variation_returned_count"],"ad_keyword_observed_counts":enrichment["ad_keyword_observed_counts"],"original_snapshot_modified":False}
    return result


def write_market_facts(run_dir):
    from r01_native import execute_native
    handled=execute_native(run_dir,lambda view:write_market_facts(view),step=4)
    if handled is not NotImplemented:return handled
    root=Path(run_dir); result=build_market_facts(root); target=root/OUT
    inputs = list((root/OUT.parent/'运行数据').glob('*.json'))
    inputs += [root/OUT.parent/name for name in ('01_product_profile.yaml', '02_demand_validation.yaml', 'comparison_scope.yaml')]
    inputs += [root/'s1_decision_inputs.yaml']
    result['generator'] = 's1_market_facts.py'
    result['generation_sources'] = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                                    for path in sorted(set(inputs)) if path.is_file()}
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    return result

build=build_market_facts
run=write_market_facts
if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__,epilog=INPUT_CONTRACT,formatter_class=argparse.RawDescriptionHelpFormatter);parser.add_argument("run_dir",type=Path)
    args=parser.parse_args(); result=write_market_facts(args.run_dir)
    print(json.dumps({"output":str(args.run_dir/OUT),"competitors":len(result["competitors"]),"parent_samples":len(result["representatives"]),"criteria_errors":result["criteria_errors"]},ensure_ascii=False))
