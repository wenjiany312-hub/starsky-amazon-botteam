#!/usr/bin/env python3
"""R01 one cost engine. JSON in / JSON out; no business approvals or network I/O.

Input percentages use the approved page units (15 means 15%); output ratios
use 0..1. Currency amounts remain unrounded until display/target-price ceiling.
Unknown critical values propagate as null, never as free goods or free FBA.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import math
from pathlib import Path

import yaml

VERSION = 'r01.cost.2'
ENGINE_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
HOME = Path(__file__).resolve().parents[1]
CUFT = 28316.846592
OZ_KG = .028349523125
MINIMUM = {'US': .30, 'UK': .25, 'DE': .30, 'JP': 30., 'AU': .30}
NUMERIC = set('buy tax qty pack dom oneoff L W H g pcs cL cW cH cKg frate div customs clear deliv duty vat fx price coupon comm fba inbound srate months tacos cpc cvr ret resell reserve target p25 p50 p75 ltsR ltsM other awdS awdM awdIn awdOut awdT couponFixed couponRate targetPost referralMin'.split())
DEFAULTS = dict(tax=0, qty=120, pack=0, dom=0, oneoff=0, pcs=0, cL=0, cW=0,
                cH=0, cKg=0, fmode='kg', frate=8, div=6000, customs=200,
                clear=0, deliv=0, dutyInc=False, duty=None, vatReg=False,
                coupon=0, comm=15, fba=None, inbound=0, months=1, admode='tacos',
                tacos=10, cpc=None, cvr=10, ret=0, resell=50, reserve=0,
                target=40, targetPost=None, p25=None, p50=None, p75=None,
                ltsR=0, ltsM=0, other=0, awd=False, awdS=.48, awdM=2,
                awdIn=1.4, awdOut=1.4, awdT=1.4, couponFixed=0, couponRate=0)
FIELD_LABELS=dict(buy='采购单价',tax='开票税点',qty='首批件数',pack='包装贴标箱唛',dom='国内运费',
    oneoff='打样开模检测',L='单包长',W='单包宽',H='单包高',g='单包毛重',pcs='每箱件数',
    cL='外箱长',cW='外箱宽',cH='外箱高',cKg='外箱毛重',fmode='头程计费方式',frate='头程单价',
    div='体积重除数',customs='报关费',clear='清关费',deliv='派送费',dutyInc='头程包税',
    duty='进口关税率',vatReg='已注册税号',vat='增值税税率',fx='汇率',price='售价',
    coupon='优惠券比例',comm='佣金率',fba='FBA配送费',inbound='入库配置费',srate='月仓储费率',
    months='平均在库月数',admode='广告测算方式',tacos='广告费占销售额',cpc='点击单价',
    cvr='广告转化率',ret='退货率',resell='退货可再售比例',reserve='额外销售预留',target='广告前目标毛利',
    targetPost='广告后目标毛利',p25='竞品P25',p50='竞品P50',p75='竞品P75',ltsR='长期仓储附加费率',
    ltsM='超龄月数',other='其他费用',awd='AWD中转',awdS='AWD仓储费率',awdM='AWD存放月数',
    awdIn='AWD入库处理费',awdOut='AWD出库处理费',awdT='AWD至FBA运输费',
    couponFixed='券活动固定费',couponRate='券成交费率',referralMin='最低佣金',
    purchase='采购含税价',packaging='包装贴标',domestic='国内运费分摊',one_off='打样检测摊销',
    freight='头程运费',clearance='清关分摊',delivery='派送分摊',import_vat='未抵扣进口税',
    commission='佣金',storage='仓储费',long_term_storage='长期仓储费',coupon_fee='券平台费',
    returns_reserve='退货及额外预留',dimensions='单包尺寸')
STATUS_LABELS={'user_attested':'用户认可','confirmed':'已确认','page_extracted':'页面提取',
    'image_extracted':'图片提取','default_assumption':'待确认','unlabeled':'待标来源',
    'missing':'待补齐','calculated':'已测算','official_index_table_estimate':'官方表估算',
    'manual_override':'手动覆盖','fx_snapshot':'汇率快照','user_edit_pending':'本次修改',
    'fixture_evidence':'案例证据','scenario':'测款情景','market_data':'市场数据','mixed_sources':'多项来源',
    'user_profile':'用户配置','demo':'示例'}


def canonical_hash(obj):
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def number(value, name):
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        raise ValueError(f'{FIELD_LABELS.get(name,name)} 应为数字')
    try:
        value = float(value)
    except (ValueError, TypeError):
        raise ValueError(f'{FIELD_LABELS.get(name,name)} 应为数字') from None
    if not math.isfinite(value) or value < 0:
        raise ValueError(f'{FIELD_LABELS.get(name,name)} 不能是负数或非有限数')
    return value


def load_fees(market):
    path = HOME / 'templates' / 'fee_tables' / f'{market}.yaml'
    raw = path.read_bytes()
    table = yaml.safe_load(raw)
    return table, {'path': f'templates/fee_tables/{market}.yaml',
                   'sha256': hashlib.sha256(raw).hexdigest(), 'updated_at': table['updated_at'],
                   'sources': copy.deepcopy(table.get('sources') or [table.get('source')]),
                   'status': table.get('status', 'official_index_table_estimate')}


def validate_provenance(model,root=None):
    """Read-only freshness gate: data cannot outlive its engine/fee dependencies."""
    issues=[]
    if not isinstance(model,dict):return {'ok':False,'issues':['缺统一成本模型']}
    if model.get('provenance',{}).get('engine_sha256') != hashlib.sha256(Path(__file__).read_bytes()).hexdigest():
        issues.append('成本引擎已变化，需重算')
    market=model.get('marketplace')
    if market not in MINIMUM:
        issues.append('成本模型站点无效')
    else:
        _,current=load_fees(market)
        declared=model.get('fee_provenance') or {}
        if declared.get('path')!=current['path'] or declared.get('sha256')!=current['sha256']:
            issues.append('费率表引用/内容已变化，需重算')
    if root is not None:
        allowed={'00A_quick_select.yaml','00_总控/starsky-fullchain-workflow/00A_quick_select.yaml',
                 'S1_选品板块/amazon-market-research/01_market_facts.json'}
        allowed.update(f'S1_选品板块/{folder}/{name}' for folder in ('amazon-market-research','1688-product-detail-scraper')
                       for name in ('03_product_facts.yaml','03_product_ct.yaml'))
        import importlib.util
        import sys
        store_path=(HOME/'scripts/r01_case_store.py').resolve()
        key='starsky_cost_case_store_'+hashlib.sha256(str(store_path).encode()).hexdigest()[:16]
        if key not in sys.modules:
            spec=importlib.util.spec_from_file_location(key,store_path)
            module=importlib.util.module_from_spec(spec);sys.modules[key]=module;spec.loader.exec_module(module)
        store=sys.modules[key].CompactCase(root)
        for relative,expected in model.get('provenance',{}).get('case_sources',{}).items():
            if relative not in allowed:
                issues.append('成本来源不在允许的产品输入中');continue
            try:actual=hashlib.sha256(store.read_bytes(relative)).hexdigest()
            except (OSError,ValueError):actual=None
            if actual!=expected:issues.append('成本来源已变化，需重算：'+relative)
    return {'ok':not issues,'issues':issues}


def estimate_fba(price, dimensions_cm, weight_kg, table, date):
    """Official US non-apparel standard sizes only; exact surcharge retained."""
    if not table or not dimensions_cm or weight_kg is None:
        return None, {'status': 'missing', 'reason': 'FBA需手填或完整尺寸重量'}
    if not table['valid_from'] <= str(date) <= table['valid_until']:
        return None, {'status': 'missing', 'reason': '计算日期超出费用表有效期'}
    length, width, height = sorted((v / 2.54 for v in dimensions_cm), reverse=True)
    oz = weight_kg / OZ_KG
    if length <= 15 and width <= 12 and height <= .75 and oz <= 16:
        tier, key, ship = 'small_standard', 'small_standard_oz', oz
    elif length <= 18 and width <= 14 and height <= 8 and oz <= 320:
        tier, key = 'large_standard', 'large_standard_oz'
        ship = max(oz, length*width*height/139*16)
    else:
        return None, {'status': 'missing', 'reason': '超出已收录US非服装标准尺寸费率，请手填后台值'}
    peak = table['peak_from'] <= str(date) <= table['valid_until']
    column = 1 if price < 10 else 2 if price <= 50 else 3
    base = next((row[column] for row in table['peak' if peak else 'non_peak'][key]
                 if ship <= row[0] + 1e-9), None)
    if base is None:
        return None, {'status': 'missing', 'reason': '计费重超出已收录费率档，请手填后台值'}
    surcharge = table['fuel_surcharge_rate'] if str(date) >= table['fuel_surcharge_from'] else 0
    return base*(1+surcharge), {'status': 'official_index_table_estimate', 'base_fee': base,
                                'surcharge_rate': surcharge, 'size_tier': tier,
                                'shipping_oz': ship, 'season': 'peak' if peak else 'non_peak',
                                'evaluation_date': str(date), 'note': '非服装估算；后台实测可覆盖'}


def freight_cost(basis, rate, divisor, carton, pcs, gross_kg, unit, unit_kg):
    """Shared by legacy producer and current calculator, in CNY/unit."""
    if rate is None or divisor <= 0:
        return None, '缺头程单价或有效体积重除数', False
    if carton and pcs and (gross_kg is not None or basis == 'cbm'):
        volume = math.prod(carton)
        cost = volume/1e6*rate/pcs if basis == 'cbm' else max(gross_kg, volume/divisor)*rate/pcs
        return cost, f'外箱体积 {volume/1e6:.5f} m³ / {pcs:g}件；max实重/体积重；{basis}', True
    if unit and unit_kg is not None:
        volume = math.prod(unit)
        cost = volume/1e6*rate if basis == 'cbm' else max(unit_kg, volume/divisor)*rate
        return cost, '单包计费（外箱数据不完整）', False
    return None, '缺尺寸重量', False


def legacy_landed(p):
    factory = p['unit_price']*(1+p['tax'])*p.get('mult', 1)
    parts = [('出厂价'+(f'（×{p["mult"]:g}）' if p.get('mult',1) != 1 else ''), factory),
             ('组合配件/定制包装', p.get('extra',0)), ('包装',p['packaging']), ('贴标',p['labeling']),
             ('工厂→货代',p['domestic']/p['qty']), ('报关',p['customs']/p['qty']),
             ('其他每票杂费',p['other']/p['qty']), ('头程',p['first_leg']),
             ('打样开模检测摊销',(p['one_off']+p.get('extra_one_off',0))/p['qty'])]
    duty = 0 if p['duty_included'] else (factory+p.get('extra',0))/p['fx']*(p['duty_rate'] or 0)
    return sum(v for _,v in parts)/p['fx']+duty, parts, duty


def legacy_fees(price, referral_rate, referral_min, fba, storage, placement, return_rate):
    """Explicit M1 reserve convention; old cases retain their original semantics."""
    referral=max(price*referral_rate,referral_min)
    return {'referral':referral,'fba':fba,'storage':storage,'inbound_placement':placement,
            'return_loss':price*return_rate,
            'total':referral+(fba or 0)+storage+placement+price*return_rate}


def from_cost_inputs(doc):
    """Lossless source cells -> canonical page-unit adapter; no arithmetic policy."""
    p, state = {}, {}
    mapping={
        'buy':('factory','unit_price_cny',1),'tax':('factory','tax_surcharge_rate',100),
        'qty':(None,'first_batch_qty',1),'fx':(None,'fx',1),
        'pcs':('factory','carton_qty',1),'cKg':('factory','carton_gross_kg',1),
        'g':('factory','unit_package_weight_kg',1000),'dom':('factory','domestic_freight_cny_per_shipment',1),
        'oneoff':('factory','one_off_cny',1),'frate':('logistics','first_leg_rate_cny',1),
        'div':('logistics','volumetric_divisor',1),'customs':('logistics','customs_fee_cny_per_shipment',1),
        'clear':('logistics','clearance_cny_per_shipment',1),'deliv':('logistics','other_cny_per_shipment',1),
        'duty':('logistics','duty_rate',100),'comm':('amazon','referral_rate',100),
        'referralMin':('amazon','referral_min',1),'fba':('amazon','fba_fee',1),
        'inbound':('amazon','inbound_placement_fee',1),'srate':('amazon','storage_per_cuft_month',1),
        'months':('amazon','storage_months',1),'reserve':('amazon','return_loss_rate',100),
        'ret':('amazon','return_rate',100),'resell':('amazon','resell_rate',100),
        'coupon':('amazon','coupon_rate',100),'target':(None,'target_pre_ad_margin',100),
        'targetPost':(None,'target_post_ad_margin',100)}
    def cell(group,key):
        value=(doc.get(group) or {}).get(key) if group else doc.get(key)
        return copy.deepcopy(value) if isinstance(value,dict) else {'value':value,'status':'unlabeled','source':''}
    for key,(group,name,mult) in mapping.items():
        node=cell(group,name)
        if node['value'] is not None:
            p[key]=number(node['value'],name)*mult
            state[key]={**node,'value':p[key]}
        elif key in ('buy','tax','g','fx','qty','fba','duty'):
            p[key]=None
    for name,keys in [('unit_package_cm',('L','W','H')),('carton_cm',('cL','cW','cH'))]:
        node=cell('factory',name)
        if node['value'] is not None:
            values=node['value']
            if not isinstance(values,(list,tuple)) or len(values)!=3:
                raise ValueError(name+' 需要三个尺寸')
            for key,value in zip(keys,values):
                p[key]=number(value,key)
                state[key]={**node,'value':p[key]}
    package=cell('factory','packaging_cny_per_unit');label=cell('factory','labeling_cny_per_unit')
    if package['value'] is not None and label['value'] is not None:
        p['pack']=number(package['value'],'packaging')+number(label['value'],'labeling')
        state['pack']={'value':p['pack'],'status':package.get('status') if package.get('status')==label.get('status') else 'mixed_sources',
                       'source':' + '.join(str(n.get('source') or '') for n in (package,label)),
                       'components':[package,label]}
    else:
        p['pack']=None
    for key,group,name in [('dutyInc','logistics','duty_included'),('vatReg','logistics','vat_registered')]:
        node=cell(group,name)
        if node['value'] is not None:
            p[key]=node['value']
            state[key]={**node,'value':p[key]}
    basis=cell('logistics','first_leg_basis')
    if basis['value'] is not None:
        p['fmode']=basis['value'];state['fmode']=basis
    if doc.get('target_margin_pre_ad') is not None and 'target' not in p:
        n=cell(None,'target_margin_pre_ad');p['target']=float(n['value'])*100;state['target']={**n,'value':p['target']}
    if isinstance(doc.get('calculator_inputs'),dict):
        p.update(copy.deepcopy(doc['calculator_inputs']))
        for key in doc['calculator_inputs']:
            state.pop(key,None)
        state.update(copy.deepcopy(doc.get('calculator_input_state') or {}))
    scenarios=doc.get('price_scenarios') or []
    preferred=next((s for s in scenarios if s.get('recommended')),None) or (scenarios[0] if scenarios else None)
    if preferred and 'price' not in p:
        p['price']=preferred['price'];state['price']={'value':p['price'],'status':'scenario','source':'price_scenarios.'+str(preferred.get('id',''))}
    bands=doc.get('market_price_bands') or {}
    for key in ('p25','p50','p75'):
        node=bands.get(key)
        value=node.get('value') if isinstance(node,dict) else node
        if value is not None:
            p[key]=value;state[key]={'value':value,'status':'market_data','source':bands.get('source','market_price_bands')}
    return {'marketplace':doc.get('marketplace','US'),'inputs':p,'input_state':state,
            'evaluation_date':doc.get('evaluation_date') or str(dt.date.today()),
            'keywords':copy.deepcopy((doc.get('ads') or {}).get('keywords') or []),
            'promotion_variants':copy.deepcopy(doc.get('promotion_variants') or []),
            'competitors':copy.deepcopy(doc.get('competition_barriers') or {}),
            'test_prices':copy.deepcopy(doc.get('test_prices') or []),
            'condition_scenarios':copy.deepcopy(doc.get('condition_scenarios') or [])}


def enrich_from_case(root,payload):
    """Read typed current inputs in a materialized native view; never infer gross from net."""
    root=Path(root);payload=copy.deepcopy(payload);sources={}
    values=payload.setdefault('inputs',{});states=payload.setdefault('input_state',{})
    aliases={'unit_price_cny':('buy',1),'purchase_price_cny':('buy',1),
             'package_gross_weight_kg':('g',1000),'unit_package_weight_kg':('g',1000),
             'packaged_weight_g':('g',1),'carton_qty':('pcs',1),'carton_units':('pcs',1),
             'carton_gross_weight_kg':('cKg',1),'carton_gross_kg':('cKg',1)}
    dimensions={'unit_package_cm':('L','W','H'),'package_dimensions_cm':('L','W','H'),
                'packaged_dimensions_cm':('L','W','H'),'carton_dimensions_cm':('cL','cW','cH'),
                'carton_cm':('cL','cW','cH')}
    def source_file(relative):
        path=root/relative
        if not path.is_file():return None
        raw=path.read_bytes();sources[relative]=hashlib.sha256(raw).hexdigest()
        return json.loads(raw.decode('utf-8-sig')) if path.suffix=='.json' else yaml.safe_load(raw.decode('utf-8-sig'))
    def put(key,value,node,relative):
        current=states.get(key,{})
        # Preserve explicit cost inputs, including labelled scenarios; case facts are fallback
        # evidence and must not overwrite a complete kit with one component (S1-R12), nor a known value with null.
        if values.get(key) is not None and current.get('status') not in (None, 'missing', 'default_assumption', 'unlabeled'):
            return
        if value is None and values.get(key) is not None:
            return
        values[key]=value
        states[key]={'value':value,'status':node.get('status',node.get('source_status','page_extracted')),
                     'source':str(node.get('source') or relative),
                     **{k:node[k] for k in ('observed_at','valid_until') if k in node}}
    def walk(obj,relative):
        if not isinstance(obj,dict):return
        for name,item in obj.items():
            node=item if isinstance(item,dict) else {'value':item};value=node.get('value')
            if name in aliases:
                key,factor=aliases[name]
                if value is None and isinstance(item,dict) and 'value' in item:
                    put(key,None,node,relative)
                elif isinstance(value,(int,float)) and not isinstance(value,bool):
                    put(key,value*factor,node,relative)
            elif name in dimensions and isinstance(value,(list,tuple)) and len(value)==3:
                for key,value in zip(dimensions[name],value):put(key,value,node,relative)
            if isinstance(item,dict):walk(item,relative)
    for folder in ('S1_选品板块/amazon-market-research','S1_选品板块/1688-product-detail-scraper'):
        for name in ('03_product_facts.yaml','03_product_ct.yaml'):
            relative=f'{folder}/{name}';obj=source_file(relative)
            if obj:walk(obj,relative)
    for relative in ('00_总控/starsky-fullchain-workflow/00A_quick_select.yaml','00A_quick_select.yaml'):
        obj=source_file(relative)
        if isinstance(obj,dict) and obj.get('target_margin_pre_ad') is not None:
            put('target',number(obj['target_margin_pre_ad'],'target_margin_pre_ad')*100,
                {'status':(obj.get('field_sources') or {}).get('target_margin_pre_ad','default_assumption'),'source':relative},relative)
    relative='S1_选品板块/amazon-market-research/01_market_facts.json';obj=source_file(relative)
    if isinstance(obj,dict):
        for key,value in (obj.get('price_bands') or {}).items():
            if key in ('p25','p50','p75') and value is not None:
                put(key,value,{'status':'market_data','source':relative+' / price_bands.'+key},relative)
    payload['case_sources']=sources
    return payload


def _at_price(p, market, table, date, price):
    """All landed/fees/returns/ads arithmetic lives here, including target probes."""
    realized = price*(1-p['coupon']/100)
    unit = [p.get(k) for k in ('L','W','H')]
    dims = unit if all(v is not None and v > 0 for v in unit) else None
    kg = p.get('g')/1000 if p.get('g') is not None else None
    carton = [p.get(k) or 0 for k in ('cL','cW','cH')]
    carton_ok = p['pcs'] and min(carton) > 0 and p.get('cKg') is not None and p['cKg'] > 0
    leg, leg_note, used_carton = freight_cost(p['fmode'], p['frate'], p['div'],
                                             carton if carton_ok else None, p['pcs'],
                                             p['cKg'], dims, kg)
    missing = [k for k in ('buy','tax','fx','qty','pack','dom','oneoff','customs','clear','deliv')
               if p.get(k) is None or (k in ('fx','qty') and p[k] <= 0)]
    if leg is None:
        missing.append('freight')
    if not p['dutyInc'] and p.get('duty') is None:
        missing.append('duty')
    landed = duty = import_vat = None
    parts = {}
    if not missing:
        fx, qty = p['fx'], p['qty']
        purchase = p['buy']*(1+p['tax']/100)
        parts = {'purchase':purchase/fx, 'packaging':p['pack']/fx,
                 'domestic':p['dom']/qty/fx, 'one_off':p['oneoff']/qty/fx,
                 'freight':leg/fx, 'customs':p['customs']/qty/fx,
                 'clearance':(p['clear'] if market != 'US' else 0)/qty/fx,
                 'delivery':p['deliv']/qty/fx}
        goods = (purchase+p['pack'])/fx
        duty = 0 if p['dutyInc'] else goods*p['duty']/100
        import_vat = ((goods+leg/fx+duty)*p['vat']/100
                      if market != 'US' and not p['dutyInc'] and not p['vatReg'] else 0)
        parts.update(duty=duty, import_vat=import_vat)
        landed = sum(parts.values())
    fba = p.get('fba')
    fba_proof = {'status':'manual_override', 'source':'input_state.fba'}
    if fba is None:
        fba, fba_proof = estimate_fba(realized, dims, kg, table.get('fba') if market=='US' else None, date)
    if fba is None:
        missing.append('fba')
    if not dims:
        missing.append('dimensions')
    commission = max(p.get('referralMin', MINIMUM[market]),realized*p['comm']/100)
    sales_vat = realized/(1+p['vat']/100)*(p['vat']/100) if market!='US' and p['vatReg'] else 0
    ft3 = math.prod(dims)/CUFT if dims else None
    storage = ft3*p['srate']*p['months'] if ft3 is not None and p.get('srate') is not None and p.get('months') is not None else None
    awd = 0
    if market=='US' and p['awd']:
        count = p['pcs'] or 1
        box_ft3 = math.prod(carton)/CUFT if carton_ok else (ft3*count if ft3 is not None else None)
        awd = (box_ft3*(p['awdS']*p['awdM']+p['awdT'])+p['awdIn']+p['awdOut'])/count if box_ft3 else None
    lts = ft3*p['ltsR']*p['ltsM'] if ft3 is not None and p.get('ltsR') is not None and p.get('ltsM') is not None else None
    coupon_fee = (p['couponFixed']/p['qty']+realized*p['couponRate']/100) if p['coupon'] and p.get('qty') else 0
    fees = {'commission':commission, 'fba':fba, 'inbound':p['inbound'], 'storage':storage,
            'long_term_storage':lts, 'awd':awd, 'other':p['other'], 'coupon_fee':coupon_fee}
    amazon = sum(fees.values()) if all(v is not None for v in fees.values()) else None
    missing.extend(k for k,v in fees.items() if v is None)
    returns = (p['ret']/100*((1-p['resell']/100)*landed+fba+min(5, commission*.2))
               +realized*p['reserve']/100) if landed is not None and fba is not None else None
    pre = realized-sales_vat-landed-amazon-returns if not missing and amazon is not None else None
    ad = realized*p['tacos']/100 if p['admode']=='tacos' else (p['cpc']/(p['cvr']/100) if p.get('cpc') is not None and p.get('cvr') else None)
    post = pre-ad if pre is not None and ad is not None else None
    return {'price':price, 'realized_price':realized,'landed_cost':landed,'landed_parts':parts,
            'first_leg_cny':leg,'first_leg_note':leg_note,'used_carton':bool(used_carton),
            'fba_fee':fba,'fba_proof':fba_proof,'commission':commission,'sales_vat':sales_vat,
            'import_vat':import_vat,'amazon_fees':amazon,'fee_parts':fees,'returns_reserve':returns,
            'pre_ad_profit':pre,'pre_ad_margin':pre/realized if pre is not None and realized>0 else None,
            'ad_spend':ad,'post_ad_profit':post,'post_ad_margin':post/realized if post is not None and realized>0 else None,
            'missing':sorted(set(missing))}


def _target(p, market, fees, date, after=False):
    target = p['targetPost'] if after and p.get('targetPost') is not None else p['target']
    if target is None or target >= 100:
        return None
    key = 'post_ad_margin' if after else 'pre_ad_margin'
    def margin(price):
        return _at_price(p,market,fees,date,price)[key]
    lo, hi = .01, max(2000,p['price']*20)
    test = margin(hi)
    if test is None or test < target/100:
        return None
    for _ in range(100):
        mid=(lo+hi)/2
        value=margin(mid)
        if value is None or value < target/100:
            lo=mid
        else:
            hi=mid
    return math.ceil((hi-1e-10)*100)/100


_LABEL_KEYS={label:key for key,label in FIELD_LABELS.items() if key in NUMERIC}


def _condition_scenarios(payload,p,market,fees,date,assumptions):
    """User-chosen condition scenarios (R06.1 PL-07), e.g. A = 采购单价 16 + 单包高 4, B = 采购单价 19 + 单包高 3.

    Each changes named inputs (engine keys or their Chinese labels) and is priced like the main basis, with the same
    unknown-cost assumptions. Nothing here is a supplier quote."""
    rows=[]
    for index,item in enumerate(payload.get('condition_scenarios') or [],1):
        if not isinstance(item,dict):raise ValueError('条件情景必须是对象')
        label=str(item.get('label') or item.get('id') or f'情景{index}').strip()
        changes={}
        for key,value in (item.get('inputs') or {}).items():
            field=key if key in NUMERIC else _LABEL_KEYS.get(key)
            if field is None or field=='price':raise ValueError(f'条件情景「{label}」里的「{key}」不是可设置的成本输入；售价请写在 price')
            changes[field]=number(value,FIELD_LABELS.get(field,field))
        if not changes:raise ValueError(f'条件情景「{label}」没有写要改的输入')
        price=number(item.get('price',p['price']),'条件情景售价')
        if price is None or price<=0:raise ValueError(f'条件情景「{label}」的售价必须大于0')
        trial={**p,**assumptions,**changes}
        result=_at_price(trial,market,fees,date,price)
        result.update(target_price_pre_ad=_target(trial,market,fees,date),target_price_post_ad=_target(trial,market,fees,date,True))
        rows.append({'id':str(item.get('id') or index),'label':label,'price':price,
                     'changes':[{'field':FIELD_LABELS.get(k,k),'key':k,'value':v,'before':p.get(k)} for k,v in changes.items()],
                     'source':str(item.get('source') or '用户指定'),'status':'conditional','result':result})
    return rows


def calculate(payload, *, as_of=None, price_scenarios=None):
    if not isinstance(payload, dict):
        raise ValueError('输入必须是JSON object')
    market=str(payload.get('marketplace','US')).upper()
    if market not in MINIMUM:
        raise ValueError('本轮支持 US/UK/DE/JP/AU')
    fees, proof=load_fees(market)
    date=str(as_of or payload.get('evaluation_date') or dt.date.today())[:10]
    dt.date.fromisoformat(date)
    explicit=copy.deepcopy(payload.get('inputs') or {})
    p={**DEFAULTS, 'vat':fees['vat_rate']*100,'comm':fees['referral_rate']*100,
       'referralMin':fees['referral_min'],
       'fx':fees.get('default_fx_cny_per_currency',6.69),
       'srate':(fees.get('storage_per_cuft_month') or {}).get('peak' if 10<=int(date[5:7])<=12 else 'non_peak',0),
       **explicit}
    for key in NUMERIC:
        if key in p:
            p[key]=number(p[key],key)
    for key in ('coupon','comm','referralMin','vat','ret','resell','reserve','tacos','couponFixed','couponRate'):
        if p.get(key) is None:raise ValueError(FIELD_LABELS.get(key,key)+'待补齐，不能当作零费用')
    if p.get('price') is None or p['price']<=0:
        raise ValueError('售价必须大于0')
    if p['div'] is None or p['div']<=0:
        raise ValueError('体积重除数必须大于0')
    for key in ('coupon','tax','comm','ret','resell','reserve','target','vat','tacos','cvr'):
        if p.get(key) is not None and p[key]>100:
            raise ValueError(f'{FIELD_LABELS.get(key,key)} 需在0..100之间')
    for key in ('dutyInc','vatReg','awd'):
        if type(p.get(key)) is not bool:
            raise ValueError(f'{key} 需要JSON boolean')
    if p['fmode'] not in ('kg','cbm') or p['admode'] not in ('tacos','cpc'):
        raise ValueError('计费或广告模式无效')
    # Unattested defaults are scenario parameters, never evidence of free costs.
    unknown_costs = []
    for key in ('pack', 'inbound'):
        node = (payload.get('input_state') or {}).get(key) or {}
        evidence_status = {'confirmed','user_attested','user_profile','page_extracted','fixture_evidence','user_edit_pending'}
        components = node.get('components') or []
        attested = (node.get('status') in evidence_status and bool(node.get('source'))) or (
            node.get('status') == 'mixed_sources' and components and all(c.get('status') in evidence_status and c.get('source') for c in components))
        if p.get(key) is None or key not in explicit or not attested:
            unknown_costs.append(key)
            p[key] = None
    r=_at_price(p,market,fees,date,p['price'])
    r.update(target_price_pre_ad=_target(p,market,fees,date),
             target_price_post_ad=_target(p,market,fees,date,True))
    target=r['target_price_pre_ad']
    level=('unknown' if target is None or not p.get('p50') or not p.get('p75') else
           'green' if target<=p['p50'] else 'yellow' if target<=p['p75'] else 'red')
    competition={'level':level,'reason':{'unknown':'缺目标价或真实P50/P75','green':'目标价不高于P50',
                    'yellow':'目标价介于P50与P75','red':'目标价高于P75'}[level],
                 **{k:p.get(k) for k in ('p25','p50','p75')}}
    r['competitiveness']=competition
    r['break_even_acos']=r['pre_ad_margin']
    r['break_even_cpc']=r['pre_ad_profit']*p['cvr']/100 if r['pre_ad_profit'] is not None and p.get('cvr') else None
    r['first_batch_capital']=r['landed_cost']*p['qty'] if r['landed_cost'] is not None and p.get('qty') else None
    r['all_sold_profit']=r['post_ad_profit']*p['qty'] if r['post_ad_profit'] is not None and p.get('qty') else None
    r['roi']=r['all_sold_profit']/r['first_batch_capital'] if r['first_batch_capital'] and r['all_sold_profit'] is not None else None
    state={k:{'value':v,'status':'default_assumption','source':'R01 prototype scenario default'} for k,v in p.items()}
    for key in explicit:
        state[key]={'value':p[key],'status':'unlabeled','source':'caller input without attestation'}
    for key,node in (payload.get('input_state') or {}).items():
        if key in unknown_costs:
            state[key] = {**copy.deepcopy(node), 'value': None, 'status': 'missing',
                          'original_assumption': copy.deepcopy(node)}
            continue
        if key not in p or not isinstance(node,dict) or node.get('value') != p[key]:
            raise ValueError(f'input_state.{key} 与实际计算值不一致')
        state[key]=copy.deepcopy(node)
    warnings=[]
    if r['missing']:
        warnings.append('实际利润待补：'+'、'.join(FIELD_LABELS.get(k,k) for k in r['missing']))
    dims=[p.get(k) for k in ('L','W','H')]
    if all(v for v in dims) and p['pcs'] and min(p.get(k) or 0 for k in ('cL','cW','cH'))>0:
        if math.prod(dims)*p['pcs']>math.prod(p[k] for k in ('cL','cW','cH'))*1.05:
            warnings.append('件数×单包体积大于外箱体积×1.05，请核实装箱数/尺寸')
    if p.get('g') and p['pcs'] and p['cKg'] and abs(p['cKg']/p['pcs']-p['g']/1000)/(p['g']/1000)>.15:
        warnings.append('外箱分摊毛重与单包重量偏差超过15%')
    if market=='US' and p['awd']:
        warnings.append('AWD默认费率来自第三方资料，请在卖家后台核对')
        if max(p.get(k) or 0 for k in ('cL','cW','cH'))/2.54>25 or (p.get('cKg') or 0)/0.45359237>50:
            warnings.append('AWD箱规超限：每边≤25 in、每箱≤50 lb')
    if market!='US':
        warnings.append('非美站FBA本轮必须手填；VAT/GST按注册和抵扣资格确认')
    if all(v for v in dims) and market=='US':
        edges=sorted([v/2.54 for v in dims],reverse=True)
        if any(abs(v-bound)/bound<.10 for v,bound in zip(edges,[18,14,8])) or (p.get('g') and abs(p['g']/453.59237-1.75)/1.75<.10):
            warnings.append('FBA尺寸/重量距分档边界不足10%，请以后台实测复核')
    basis={'variant_id':'base','pre_ad_profit':r['pre_ad_profit'],'source':'当前售价与采购规格'}
    for variant in payload.get('promotion_variants') or []:
        q={**p,**variant.get('inputs',{})}
        vr=_at_price(q,market,fees,date,q['price'])
        if vr['pre_ad_profit'] is not None and (basis['pre_ad_profit'] is None or vr['pre_ad_profit']<basis['pre_ad_profit']):
            basis={'variant_id':variant['id'],'pre_ad_profit':vr['pre_ad_profit'],'source':variant.get('source','明确变体输入')}
    keywords=[]
    for item in payload.get('keywords') or []:
        cpc=number(item.get('cpc'),'keyword.cpc')
        required=cpc/basis['pre_ad_profit'] if cpc is not None and basis['pre_ad_profit'] and basis['pre_ad_profit']>0 else None
        pressure='unknown' if required is None else 'avoid' if required>.30 else 'hard' if required>.15 else 'test' if required>.10 else 'easy'
        keywords.append({**copy.deepcopy(item),'cpc':cpc,'required_cvr':required,'level':pressure,
                         'reason':{'unknown':'缺CPC/正单件毛利','avoid':'所需转化率超过30%，避开','hard':'转化要求偏高','test':'有压力但可测','easy':'转化要求相对较低'}[pressure],
                         'price':p['price'],'break_even_cpc':basis['pre_ad_profit']*p['cvr']/100 if basis['pre_ad_profit'] is not None and p.get('cvr') else None})
    pressure=max(({'unknown':0,'easy':1,'test':2,'hard':3,'avoid':4}[k['level']] for k in keywords),default=0)
    barriers = copy.deepcopy(payload.get('competitors') or {})
    promotion={'basis':basis,'keywords':keywords,'barriers':barriers,
               'conclusion':'数据不足，暂不判断' if r['missing'] or not keywords or any(barriers.get(k) is None for k in ('organic_top5_review_median','top20_under_1yr_share')) else '仅列保本所需CVR，研究结论由第9步统一给出'}
    def rows(data, unit='站点币/件'):
        return [{'label':FIELD_LABELS.get(k,k),'value':v,'unit':unit,'status':'calculated' if v is not None else 'missing','source':'统一测算'} for k,v in data.items()]
    ladder=[]
    for label,price in [('P25',p['p25']),('P50',p['p50']),('P75',p['p75']),('当前售价',p['price']),('目标价',target)]+[(str(v.get('label','用户测款价')),v.get('price')) for v in payload.get('test_prices') or []]:
        if price is not None and price>0:
            row=_at_price(p,market,fees,date,number(price,'test_price'))
            ladder.append({'label':label,**{k:row[k] for k in ('price','pre_ad_profit','pre_ad_margin','post_ad_margin')}})
    blocks={'landed':{'title':'单件落地成本','rows':rows(r['landed_parts'])+[{'label':'合计','value':r['landed_cost'],'unit':'站点币/件','status':'calculated' if r['landed_cost'] is not None else 'missing','source':VERSION}]},
            'amazon':{'title':'亚马逊费用','rows':rows(r['fee_parts'])+rows({'returns_reserve':r['returns_reserve']})},
            'target_price':{'title':'按目标毛利反推售价','rows':rows({'广告前目标价':target,'广告后目标价':r['target_price_post_ad']})},
            'price_ladder':{'title':'价格阶梯×毛利','rows':[],'scenarios':ladder},
            'competitiveness':{'title':'竞争力判定','rows':rows({'目标价':target,'P50':p['p50'],'P75':p['p75']}),**competition},
            'test_plans':{'title':'测款方案','rows':[], 'plans':[{'label':'直接价','list_price':p['price'],'coupon_percent':p['coupon'],'realized_price':r['realized_price']},
                        {'label':'标价+券（等成交价）','list_price':r['realized_price']/.90,'coupon_percent':10,'realized_price':r['realized_price'],'note':'券平台费需另填；不等于已获广告/采购批准'}]}}
    conditional_tax=[]
    if p.get('tax') is None:
        for percent in payload.get('tax_surcharge_scenarios',[0,3]):
            percent=number(percent,'tax_surcharge_scenario')
            if percent is None or percent>100:raise ValueError('税点情景需0..100')
            trial={**p,'tax':percent}
            conditional_tax.append({'label':f'税点{percent:g}%条件情景（非报价上界）','tax_percent':percent,
                                    'status':'conditional','result':_at_price(trial,market,fees,date,p['price'])})
    conditional_costs=[]
    if unknown_costs:
        import itertools
        ranges = {'pack':[0,1], 'inbound':[0,.60]}
        for values in itertools.product(*(ranges.get(k, [0,1] if k=='pack' else [0,.60]) for k in unknown_costs)):
            assumptions = dict(zip(unknown_costs, (number(v,'费用情景') for v in values)))
            trial={**p,**assumptions}
            name='未核费用情景（'+ '、'.join(FIELD_LABELS[k]+str(v) for k,v in assumptions.items())+'）'
            estimate = _at_price(trial,market,fees,date,p['price'])
            estimate.update(target_price_pre_ad=_target(trial,market,fees,date),
                            target_price_post_ad=_target(trial,market,fees,date,True))
            conditional_costs.append({'basis_name':name,'assumptions':assumptions,
                'result':estimate,
                'assumption_table':[{'field':FIELD_LABELS.get(k,k),'value':v,
                    'kind':'默认假设，非厂家报价或真实上下界' if k in assumptions else state[k]['status'],
                    'source':'仅敏感性情景，未知费用仍待核' if k in assumptions else state[k].get('source')}
                    for k,v in trial.items()]})
    main_result = conditional_costs[-1]['result'] if conditional_costs else r
    name = ('未核费用上界情景（包装1元、入库0.60）' if set(unknown_costs)=={'pack','inbound'} and conditional_costs[-1]['assumptions']=={'pack':1,'inbound':.60}
            else conditional_costs[-1]['basis_name'] if conditional_costs else '当前页面条件情景')
    main_basis={'name':name,'actual_profit':None,'result':main_result,
                'note':'假设上界仅为敏感性参数，不是厂家报价上限；实际费用待核。' if unknown_costs else '输入按来源分类，测算不代表实际利润。'}
    selected=_condition_scenarios(payload,p,market,fees,date,conditional_costs[-1]['assumptions'] if conditional_costs else {})
    if selected:
        # R06.1 PL-07: the user's chosen condition scenarios become the main basis; the most conservative one
        # (lowest pre-ad profit) is the representative figure, the upper-bound case stays as a reference.
        ranked=sorted(selected,key=lambda row:(row['result']['pre_ad_profit'] is None,row['result']['pre_ad_profit'] or 0))
        # 3.0.25: 16 scenarios listed in full made the step card's key line unreadable; name the count and the representative.
        title=('你选定的条件情景：'+' / '.join(row['label'] for row in selected) if len(selected)<=3 else
               f"你选定的 {len(selected)} 套条件情景（代表值取利润最低的 {ranked[0]['label']}）")
        main_basis={'name':title,'kind':'user_condition',
                    'actual_profit':None,'result':ranked[0]['result'],'representative':ranked[0]['id'],'scenarios':selected,
                    'reference_basis':{'name':name,'result':main_result},
                    'note':'按你指定的条件逐项测算，代表值取利润最低的一项；'+('未核费用仍按上界假设，' if unknown_costs else '')+'不代表厂家报价或实际利润。'}
    prices = price_scenarios if price_scenarios is not None else payload.get('price_scenarios', [])
    if isinstance(prices,str):
        try:prices=[number(x.strip(),'售价情景') for x in prices.split(',')]
        except ValueError:raise ValueError('售价情景必须为逗号分隔的正数') from None
    if not isinstance(prices,(list,tuple)) or any(not isinstance(v,(int,float)) or isinstance(v,bool) or not math.isfinite(v) or v<=0 for v in prices):
        raise ValueError('售价情景必须为正数列表')
    price_rows=[]
    trial={**p,**(conditional_costs[-1]['assumptions'] if conditional_costs else {})}
    for price in dict.fromkeys(prices):
        priced=_at_price(trial,market,fees,date,price)
        profit=priced['pre_ad_profit']
        price_rows.append({'price':price,'basis_name':name,'result':priced,'keywords':[
            {**copy.deepcopy(k),'price':price,'required_cvr':k['cpc']/profit if k.get('cpc') is not None and profit and profit>0 else None}
            for k in keywords]})
    return {'schema_version':VERSION,'marketplace':market,'currency':fees['currency'],'evaluation_date':date,
            'inputs':p,'input_state':state,'result':r,'blocks':blocks,'promotion':promotion,
            'warnings':warnings,'missing':r['missing'],'fee_provenance':proof,'conditional_tax_scenarios':conditional_tax,
            'cost_assumption_scenarios':conditional_costs,'main_basis':main_basis,'price_scenarios':price_rows,
            'provenance':{'engine':Path(__file__).name,'engine_sha256':ENGINE_SOURCE_SHA256,'case_sources':copy.deepcopy(payload.get('case_sources') or {}),
                          'input_sha256':canonical_hash(payload),'rounding':'currency targets ceil to .01; intermediate fees remain unrounded'}}


if __name__=='__main__':
    import sys, argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--price-scenarios',help='逗号分隔售价；只派生情景，不改原输入或来源状态')
    args=parser.parse_args()
    try:
        print(json.dumps(calculate(json.load(sys.stdin),price_scenarios=args.price_scenarios),ensure_ascii=False,allow_nan=False))
    except (ValueError, KeyError) as error:
        print(json.dumps({'ok':False,'error':str(error)},ensure_ascii=False))
        raise SystemExit(1)
