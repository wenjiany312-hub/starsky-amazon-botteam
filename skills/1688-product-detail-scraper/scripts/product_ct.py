#!/usr/bin/env python3
"""S1 产品 CT：选定款到底是什么、不是什么（材质 / 结构 / 安装 / 颜色 / 适配）。

市场调研画像（01_product_profile.yaml 的 materials）写的是「市场上有哪些材质」，不是本品。
选款定下来以后，用 1688 页面、图片、厂家回复把本品拍成一张 CT：S2 词库据此把「不是本品」的
材质 / 形态 / 功能词挡在标题、五点和广告池外，S3 视觉、S4 否定词也读同一份。

  python product_ct.py --run <产品根> --init   # 生成 03_product_ct.yaml（预填 1688 事实与市场材质）
  python product_ct.py --run <产品根>          # 校验 → 03_产品CT.md + 03_product_ct_status.json；退出码 2 = 必须改
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import importlib.util
import copy
import math
from pathlib import Path

import yaml

DIR = 'S1_选品板块/1688-product-detail-scraper'
CT = DIR + '/03_product_ct.yaml'
MD = DIR + '/03_产品CT.md'
STATUS = DIR + '/03_product_ct_status.json'
FACTS = DIR + '/03_product_facts.yaml'
PROFILE = 'S1_选品板块/amazon-market-research/01_product_profile.yaml'
MARKET_FACTS = 'S1_选品板块/amazon-market-research/01_market_facts.json'

SOURCE_STATUS = {'page_extracted', 'supplier_confirmed', 'sample_measured', 'user_confirmed', 'user_attested',
                 'image_extracted', 'image_inferred', 'ai_inferred', 'review_extracted', 'missing'}
VERIFIED = {'page_extracted', 'image_extracted', 'supplier_confirmed', 'sample_measured'}
USER = {'user_confirmed', 'user_attested'}
# 常见主材质。单一主材质（exclusive）时，这里除本品外的都自动算「不是本品」。
MATERIALS = ['bamboo', 'wood', 'wooden', 'walnut', 'acacia', 'teak', 'oak', 'stainless steel', 'steel', 'metal', 'iron',
             'aluminum', 'aluminium', 'brass', 'copper', 'plastic', 'acrylic', 'ceramic', 'porcelain', 'glass', 'marble',
             'granite', 'stone', 'concrete', 'resin', 'silicone', 'rubber', 'diatomite', 'diatomaceous', 'fabric', 'cotton',
             'linen', 'leather', 'rattan', 'wicker', 'jute', 'felt', 'velvet', 'cardboard']
# 本品属于某一类时，这一类的统称不算冲突（不锈钢 → metal 可用；硅藻土 → stone 可用）。
FAMILIES = {'metal': {'stainless steel', 'steel', 'iron', 'aluminum', 'aluminium', 'brass', 'copper'},
            'wood': {'wood', 'wooden', 'walnut', 'acacia', 'teak', 'oak'},
            'wooden': {'wood', 'wooden', 'walnut', 'acacia', 'teak', 'oak'},
            'stone': {'marble', 'granite', 'diatomite', 'diatomaceous', 'concrete'}}

TEMPLATE_HEAD = """# 产品 CT：本品到底是什么、不是什么。S2 词库 / S3 视觉 / S4 否定词都以这份为准。
# 01_product_profile.yaml 的 materials 是「市场上有哪些材质」，不是本品；本品只看这里。
# source_status：page_extracted（1688 页面） | supplier_confirmed（厂家确认） | sample_measured（样品实测）
#   | user_confirmed / user_attested（用户确认） | image_inferred（看图推断） | ai_inferred（推演） | missing
# 英文词（*_en / en_terms）写买家会搜的说法，用小写；S2 按这些词判断一个搜索词是不是在说本品。
"""


def load(path):
    try:
        return yaml.safe_load(Path(path).read_text(encoding='utf-8-sig')) or {}
    except FileNotFoundError:
        return {}


def toks(text):
    return re.findall(r"[a-z0-9]+", str(text or '').lower())


def has(tokens, phrase):
    p = toks(phrase)
    return bool(p) and any(tokens[i:i + len(p)] == p for i in range(len(tokens) - len(p) + 1))


def terms(value):
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    return [str(v).strip().lower() for v in value if str(v or '').strip()]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def market_materials(profile):
    out = []
    for item in profile.get('materials') or []:
        if isinstance(item, dict):
            out.append({'label': str(item.get('label') or ''), 'roots': [str(r) for r in item.get('root_words') or []]})
        elif item:
            out.append({'label': str(item), 'roots': []})
    return out


def material_hints(node, path=''):
    """03_product_facts.yaml 里跟材质有关的原话，只做 --init 提示。"""
    hits = []
    if isinstance(node, dict):
        for k, v in node.items():
            here = f'{path}.{k}' if path else str(k)
            if re.search(r'material|材质|材料|formula', str(k), re.I) and isinstance(v, (str, int, float)):
                hits.append(f'{here}: {v}')
            else:
                hits += material_hints(v, here)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            hits += material_hints(v, f'{path}[{i}]')
    return hits


def scan_images(inventory, reader):
    """Call the host's image reader for every image; never invent OCR results."""
    images = []
    for source in dict.fromkeys(inventory):
        try:
            observation = reader(source)
            if not isinstance(observation, dict) or observation.get('status') != 'scanned':
                raise ValueError(str((observation or {}).get('error') or 'image reader did not return a scan'))
            images.append({**observation, 'source': source})
        except Exception as error:
            images.append({'source': source, 'status': 'failed', 'error': str(error), 'fields': {}})
    return {'complete': bool(images) and all(row['status'] == 'scanned' for row in images), 'images': images}


def image_inventory(root, images_root):
    root = Path(root).resolve(); directory = (root / images_root).resolve()
    if not directory.is_relative_to(root) or not directory.is_dir(): raise ValueError('selected images root is missing or outside product')
    inventory = sorted(path for path in directory.rglob('*') if path.is_file() and path.suffix.lower() in ('.jpg','.jpeg','.png','.webp')
                       and not any(part.lower() in ('video','视频') for part in path.relative_to(directory).parts[:-1]))
    if not inventory: raise ValueError('selected image inventory is empty')
    return root, directory, inventory


def observation_template(root, images_root):
    """逐图识别回执模板（R06.1）：列出所选货源全部主图/详情图并填好 sha256，助手只逐张填识别结果。
    3.0.23 自动实跑第 5 步，助手为找这份回执的格式翻了插件源码。"""
    _, directory, inventory = image_inventory(root, images_root)
    return {'how': '每张图看完后把 status 改成 scanned，并填 fields（只用 allowed_fields 里的键）或 text；'
                   '看过但没有相关信息写 no_relevant_fields: true；看不了写 status: failed 和 error。不要改 source / sha256。',
            'allowed_fields': list(CT_IMAGE_FIELDS),
            'images': [{'source': path.relative_to(directory).as_posix(), 'sha256': sha(path), 'status': 'pending',
                        'fields': {}, 'text': ''} for path in inventory]}


def verify_image_scan(root, images_root, observations):
    """Bind coverage to the actual selected-offer image inventory and hashes."""
    root, directory, inventory = image_inventory(root, images_root)
    supplied = {}; used = set()
    for row in observations.get('images', []):
        source = str(row.get('source') or '')
        if source in supplied: raise ValueError('duplicate image observation: ' + source)
        supplied[source] = row
    images = []
    for path in inventory:
        relative = path.relative_to(directory).as_posix()
        aliases = [key for key in supplied if key in (relative, path.name, path.stem)]
        if len(aliases) > 1: raise ValueError('ambiguous image observation: ' + relative)
        original_sha = sha(path)
        if not aliases:
            images.append({'source':relative, 'sha256':original_sha, 'status':'missing', 'error':'该原图尚未逐张识别', 'fields':{}})
            continue
        key = aliases[0]; used.add(key); row = supplied[key]
        if row.get('status') == 'scanned' and row.get('sha256') != original_sha:
            raise ValueError('image source hash mismatch: ' + relative)
        if row.get('status') == 'scanned' and not row.get('fields') and not row.get('text') and not row.get('no_relevant_fields'):
            raise ValueError('empty scan cannot claim an image was read: ' + relative)
        images.append({**row, 'source':relative, 'sha256':original_sha})
    if set(supplied) != used: raise ValueError('image observations contain sources outside selected inventory')
    return {'complete': all(row.get('status') == 'scanned' for row in images), 'images':images,
        'expected_count':len(inventory), 'images_root':directory.relative_to(root).as_posix(),
        'inventory_sha256':hashlib.sha256(json.dumps({row['source']:row['sha256'] for row in images},sort_keys=True).encode()).hexdigest()}


CT_IMAGE_FIELDS = ('material', 'dimensions', 'weight', 'packaging_type', 'package_dimensions_cm',
    'package_gross_weight_kg', 'carton_dimensions_cm', 'carton_qty', 'carton_gross_weight_kg',
    'sku', 'price_tiers', 'moq', 'tax_included', 'sample_fee', 'lining', 'hardware', 'features',
    'certifications', 'customization', 'included_accessories', 'count_conflicts')


def apply_image_observations(document, scan):
    """Merge typed host observations without overwriting measured/user facts."""
    doc = copy.deepcopy(document); sources = doc.setdefault('sources', {})
    conflicts = doc.setdefault('image_conflicts', [])
    for row in scan.get('images', []):
        if row.get('status') != 'scanned': continue
        for key, value in (row.get('fields') or {}).items():
            if key not in CT_IMAGE_FIELDS or value is None: continue
            current = doc.get(key) or {}
            if isinstance(current, dict) and current.get('source_status', current.get('status')) in VERIFIED | USER:
                old = current.get('main_cn') if key == 'material' else current.get('value')
                if old != value: conflicts.append({'field': key, 'existing': old, 'observed': value, 'source': row['source']})
                continue
            source = {'source': row['source'], 'status': 'image_extracted', 'sha256': row.get('sha256')}
            sources[key] = source
            if key == 'material':
                doc[key] = {**current, 'main_cn': value, 'main_en': current.get('main_en') or [],
                            'source_status': 'image_extracted', 'source': row['source'], 'evidence': value}
            else:
                doc[key] = {'value': value, **source, 'source_status': 'image_extracted'}
                if key in ('dimensions', 'package_dimensions_cm', 'carton_dimensions_cm'): doc[key]['unit'] = 'cm'
                if key == 'weight': doc[key]['unit'] = 'g'
    doc.setdefault('package_gross_weight_kg', {'value': None, 'status': 'missing', 'source': None})
    doc['image_scan'] = copy.deepcopy(scan)
    doc['self_checks'] = packaging_checks(doc)
    doc['supplier_questions'] = supplier_questions(doc, doc['self_checks'])
    return doc


def packaging_checks(doc):
    def val(key):
        node = doc.get(key); return node.get('value') if isinstance(node, dict) else node
    def positive(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0
    def dimensions(key):
        value = val(key)
        return value if isinstance(value, list) and len(value) == 3 and all(positive(x) for x in value) else None
    unit, carton = dimensions('package_dimensions_cm'), dimensions('carton_dimensions_cm')
    qty, gross, weight = val('carton_qty'), val('carton_gross_weight_kg'), val('package_gross_weight_kg')
    checks = []
    if unit and carton and positive(qty):
        units_l = math.prod(unit) * qty / 1000; carton_l = math.prod(carton) / 1000
        checks.append({'id': 'carton_volume', 'status': 'fail' if units_l > carton_l * 1.05 else 'pass',
            'units_l': units_l, 'carton_l': carton_l, 'tolerance': .05,
            'message': '体积装不下' if units_l > carton_l * 1.05 else '单包体积总和在外箱容积内'})
    if all(positive(x) for x in (qty, gross, weight)):
        deviation = abs(gross / qty - weight) / weight
        checks.append({'id': 'carton_weight', 'status': 'warning' if deviation > .15 else 'pass',
                       'deviation': deviation, 'threshold': .15, 'message': '整箱分摊与单包毛重偏差核对'})
    if carton or positive(gross):
        exceeded = bool(carton and max(carton) > 25 * 2.54) or bool(positive(gross) and gross > 50 * .45359237)
        checks.append({'id': 'carton_limits', 'status': 'fail' if exceeded else 'pass',
            'max_side_cm': 63.5, 'max_gross_kg': 22.6796185, 'message': 'FBA/AWD普通外箱25in/50lb核对；超规需重装箱'})
    if unit:
        limits = [45.72, 35.56, 20.32]
        gaps = [abs(x - boundary) / boundary for x, boundary in zip(sorted(unit, reverse=True), limits)]
        checks.append({'id': 'fba_boundary', 'status': 'warning' if min(gaps) < .10 else 'pass',
            'distance_ratios': gaps, 'threshold': .10, 'message': '距美国大号标准尺寸边界不足10%时按最终包装复核'})
    return checks


def supplier_questions(doc, checks):
    groups = [
        ('材质与配件', ('material', 'lining', 'hardware', 'included_accessories')),
        ('单包尺寸与含包装毛重', ('package_dimensions_cm', 'package_gross_weight_kg')),
        ('箱规、装箱数与整箱毛重', ('carton_dimensions_cm', 'carton_qty', 'carton_gross_weight_kg')),
        ('所选SKU阶梯价、MOQ、含税与样品费', ('sku', 'price_tiers', 'moq', 'tax_included', 'sample_fee')),
        ('认证、授权与专利声明', ('certifications',)), ('定制、LOGO、交期', ('customization',))]
    questions = []
    for label, fields in groups:
        missing = [key for key in fields if not doc.get(key) or
            (isinstance(doc[key], dict) and doc[key].get('value', doc[key].get('main_cn')) in (None, '', []))]
        if missing: questions.append('请确认' + label + '，并提供对应规格/图片或书面回复。')
    if any(row['status'] in ('fail', 'warning') for row in checks):
        questions.append('请复核箱规体积、单包/整箱重量及装箱数冲突；外箱超25in或50lb时提供重装箱方案。')
    if doc.get('image_conflicts'): questions.append('页面/图片与已确认字段有冲突，请逐项确认以哪版为准。')
    return questions[:8]


def from_product_facts(facts, ct=None):
    """Read-only projection for legacy reports; never rewrite the old CT bytes."""
    doc = copy.deepcopy(ct or {}); real = facts.get('real_facts') or {}
    mapping = {'dimensions_cm':'dimensions','weight_g':'weight','packaged_dimensions_cm':'package_dimensions_cm',
        'carton_dimensions_cm':'carton_dimensions_cm','carton_units':'carton_qty','carton_gross_kg':'carton_gross_weight_kg',
        'packaging_type':'packaging_type','price_cny':'price_tiers'}
    sources = doc.setdefault('sources', {})
    for old, new in mapping.items():
        node = real.get(old)
        if not isinstance(node,dict) or node.get('value') is None: continue
        doc[new] = {**deepcopy_node(node), 'source_status':node.get('status','missing')}
        sources[new] = {key:node.get(key) for key in ('source','status','observed_at','valid_until')}
        if new in ('dimensions','package_dimensions_cm','carton_dimensions_cm'): doc[new]['unit']='cm'
        if new == 'weight': doc[new]['unit']='g'
    material = real.get('material')
    if isinstance(material,dict) and material.get('value'):
        doc['material'] = {**doc.get('material',{}),'main_cn':material['value'],
            'source_status':material.get('status','missing'),'source':material.get('source')}
        sources['material'] = {key:material.get(key) for key in ('source','status','observed_at','valid_until')}
    packaged = real.get('packaged_weight_g') or {}
    doc['package_gross_weight_kg'] = {**deepcopy_node(packaged),
        'value':packaged['value']/1000 if isinstance(packaged.get('value'),(int,float)) else None}
    if facts.get('product'): doc['product']=facts['product']
    doc['selected_offer'] = deepcopy_node(facts.get('selected_offer') or doc.get('selected_offer'))
    doc['self_checks']=packaging_checks(doc);doc['supplier_questions']=supplier_questions(doc,doc['self_checks'])
    doc['display_only']=True
    return doc


def deepcopy_node(value):
    return copy.deepcopy(value)


def native_step(root):
    execution = load(Path(root) / 'workflow_state.yaml').get('s1_execution') or {}
    return 5 if execution.get('roadmap_version') == 'r01.m2' else 6


def init(root, observations=None):
    delivery_scripts = Path(__file__).resolve().parents[3] / 'scripts'
    previous_paths = list(sys.path)
    try:
        sys.path.insert(0, str(delivery_scripts))
        from r01_native import execute_native
        compact = execute_native(root, lambda view: init(view, observations), step=native_step(root))
    finally:
        sys.path[:] = previous_paths
    if compact is not NotImplemented:
        return compact
    target = root / CT
    if target.is_file():
        return {'status': 'exists', 'ct': CT}
    facts, profile = load(root / FACTS), load(root / PROFILE)
    supplier = facts.get('selected_supplier') or {}
    if not isinstance(supplier, dict):
        supplier = {'name': str(supplier), 'offer_id':facts.get('selected_offer') or ''}
    real = facts.get('real_facts') or {}
    colors = real.get('colors') or []
    if isinstance(colors, dict): colors = colors.get('value') or []
    doc = {
        'product': facts.get('product') or profile.get('product_type') or '',
        'selected_offer': {'offer_id': str(supplier.get('offer_id') or ''), 'url': supplier.get('offer_url') or ''},
        'identity': {'type_cn': profile.get('product_type') or '', 'type_en': terms(profile.get('product_type_en'))},
        'material': {'main_cn': '', 'main_en': [], 'components': [], 'exclusive': True,
                     'source_status': 'missing', 'evidence': ''},
        'structure': {'mount': {'value': '', 'en_terms': [], 'source_status': 'missing'}, 'features': []},
        'dimensions': {'value': '', 'source_status': 'missing'},
        'weight': {'value': '', 'source_status': 'missing'},
        'colors': {'value': list(colors), 'en_terms': [],
                   'source_status': 'page_extracted' if colors else 'missing'},
        'package_contents': {'value': [], 'source_status': 'missing'},
        'compatible_with': {'value': [], 'en_terms': [], 'source_status': 'missing'},
        **{key: {'value': [], 'en_terms': [], 'source_status': 'missing', 'source': '',
                 **({'voc_refs': []} if key == 'pain_points' else {})}
           for key in ('uses', 'audiences', 'scenarios', 'pain_points')},
        'not_this': {'materials': [], 'forms': [], 'features': [], 'colors': []},
        'also_ok': [],
        'market_materials_from_profile': market_materials(profile),
    }
    voc_from_reviews(root, doc)
    hints = material_hints(facts)
    head = TEMPLATE_HEAD
    if hints:
        head += '# 03_product_facts.yaml 里的材质线索：\n' + ''.join(f'#   {h}\n' for h in hints[:8])
    scan = observations or facts.get('image_scan')
    if scan:
        doc = apply_image_observations(doc, scan)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(head + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=120), encoding='utf-8')
    return {'status': 'initialized', 'ct': CT, 'material_hints': hints[:8]}


def evaluate(root):
    """Read the receipt-backed native view, including standalone S2/S5 callers."""
    module=sys.modules.get('r01_case_store')
    if module is None:
        path=Path(__file__).resolve().parents[3]/'scripts/r01_case_store.py'
        spec=importlib.util.spec_from_file_location('r01_case_store',path)
        module=importlib.util.module_from_spec(spec);sys.modules['r01_case_store']=module
        spec.loader.exec_module(module)
    supplement = None
    path = Path(root) / 'S2_文案板块/_补充/ct_supplement.yaml'        # S2 补充层（S2-R2），不改 S1 CT 文件
    if path.is_file():
        supplement = yaml.safe_load(path.read_text(encoding='utf-8-sig')) or {}
    with module.CompactCase(root).materialize() as view:
        return _evaluate_native(view.root, supplement)


def _evaluate_native(root, supplement=None):
    """读 CT 算出「本品词 / 不是本品的词」；不写文件，S2 直接调用。"""
    root = Path(root)
    path = root / CT
    if not path.is_file():
        return {'status': 'missing', 'errors': ['缺产品 CT：先 product_ct.py --init，按 1688 页面/图片/厂家回复填好再运行'],
                'warnings': [], 'ours': {}, 'not_this': {}}
    ct = load(path)
    if supplement:
        spec = importlib.util.spec_from_file_location('_ct_supplement', Path(__file__).resolve().parents[3] / 'scripts/s2_ct_supplement.py')
        sup = importlib.util.module_from_spec(spec); spec.loader.exec_module(sup)
        ct = sup.merge(ct, supplement)
    profile = load(root / PROFILE)
    errors, warnings = [], []
    material = ct.get('material') or {}
    main_en = terms(material.get('main_en'))
    comp_en = [t for c in material.get('components') or [] if isinstance(c, dict) for t in terms(c.get('material_en'))]
    also_ok = terms(ct.get('also_ok'))
    status = str(material.get('source_status') or 'missing')
    if not str(material.get('main_cn') or '').strip() or not main_en:
        errors.append('材质没搞清楚：material.main_cn / main_en 必填（看 1688 页面参数、详情图、厂家回复；拿不准写 ai_inferred 并说明依据）')
    if status not in SOURCE_STATUS:
        errors.append(f'material.source_status 不认识：{status}')
    elif status == 'missing' and main_en:
        errors.append('材质写了但 source_status 还是 missing：写清来源（page_extracted / supplier_confirmed / ai_inferred …）')
    elif status in {'ai_inferred', 'image_inferred'}:
        warnings.append('材质只是推断：词库照此挡掉冲突词，但 Listing / 图片不得把材质写成已验证事实；拿到厂家确认后改 source_status 重跑')
    identity = ct.get('identity') or {}
    type_en = terms(identity.get('type_en'))
    if not type_en:
        errors.append('identity.type_en 必填：本品在 Amazon 上叫什么（如 toothbrush holder）')
    structure = ct.get('structure') or {}
    mount = structure.get('mount') or {}
    if not str(mount.get('value') or '').strip():
        errors.append('structure.mount 必填：台面放置 / 壁挂 / 吸盘 / 粘贴 / 悬挂…（决定 wall mounted 这类词能不能用）')
    feature_en = [t for f in structure.get('features') or [] if isinstance(f, dict) for t in terms(f.get('en_terms'))]
    colors = ct.get('colors') or {}
    compat = ct.get('compatible_with') or {}
    ours = {'type': type_en, 'material': main_en + comp_en, 'mount': terms(mount.get('en_terms')),
            'features': feature_en, 'colors': terms(colors.get('en_terms')), 'compatible': terms(compat.get('en_terms')),
            'also_ok': also_ok}
    for key in ('uses', 'audiences', 'scenarios', 'pain_points'):
        ours[key] = terms((ct.get(key) or {}).get('en_terms'))
    our_words = {w for group in ours.values() for t in group for w in toks(t)}
    our_material_phrases = main_en + comp_en + also_ok
    declared = ct.get('not_this') or {}
    not_this = {k: terms(declared.get(k)) for k in ('materials', 'forms', 'features', 'colors')}
    if material.get('exclusive', True) and main_en:
        allowed_generic = {fam for fam, members in FAMILIES.items()
                           if any(has(toks(m), x) or has(toks(x), m) for m in our_material_phrases for x in members | {fam})}
        for m in MATERIALS:
            if m in allowed_generic or any(has(toks(o), m) or has(toks(m), o) for o in our_material_phrases):
                continue
            if m not in not_this['materials']:
                not_this['materials'].append(m)
    for kind, items in not_this.items():
        clash = [t for t in items if any(has(toks(o), t) for group in ours.values() for o in group)]
        if clash:
            errors.append(f'not_this.{kind} 与本品自相矛盾：{", ".join(clash)}')
            not_this[kind] = [t for t in items if t not in clash]
    # 画像里的市场材质必须判清：要么是本品，要么进了「不是本品」。
    undecided = []
    for item in market_materials(profile):
        for root_word in item['roots']:
            tokens = toks(root_word)
            is_ours = any(has(tokens, o) for o in our_material_phrases)
            is_not = any(has(tokens, t) for t in not_this['materials'])
            if not is_ours and not is_not:
                undecided.append(f"{item['label']}（{root_word}）")
    if undecided and main_en:
        errors.append('画像里的市场材质没判：' + '、'.join(undecided) + '。是本品就写进 material，不是就写进 not_this.materials')
    field_status = {k: str((ct.get(k) or {}).get('source_status') or 'missing')
                    for k in ('dimensions', 'weight', 'colors', 'package_contents', 'compatible_with',
                              'uses', 'audiences', 'scenarios', 'pain_points')}
    field_status['material'] = status
    field_status['mount'] = str(mount.get('source_status') or 'missing')
    missing = [k for k, v in field_status.items() if v == 'missing']
    if missing:
        warnings.append('还没摸清：' + '、'.join(missing) + '（不挡流程；相关数字不得上图/进文案）')
    self_checks = packaging_checks(ct)
    warnings.extend(row['message'] for row in self_checks if row['status'] in ('warning', 'fail'))
    questions = supplier_questions(ct, self_checks)
    return {'status': 'fail' if errors else 'pass', 'errors': errors, 'warnings': warnings,
            'product': ct.get('product'), 'material_cn': material.get('main_cn'), 'material_status': status,
            'mount': mount.get('value'), 'field_status': field_status, 'ours': ours, 'our_words': sorted(our_words),
            'not_this': not_this, 'ct': ct, 'self_checks': self_checks, 'supplier_questions': questions,
            'image_scan': ct.get('image_scan'), 'sources': ct.get('sources', {})}


STATUS_CN = {'page_extracted': '1688 页面', 'supplier_confirmed': '厂家确认', 'sample_measured': '样品实测',
             'image_extracted': '图片明确标注',
             'user_confirmed': '用户确认', 'user_attested': '用户认可', 'image_inferred': '看图推断',
             'ai_inferred': '推演', 'missing': '未知'}


def render(result):
    ct = result['ct']
    material = ct.get('material') or {}
    structure = ct.get('structure') or {}
    fs = result['field_status']
    rows = [('材质', f"{material.get('main_cn') or '—'}（{', '.join(terms(material.get('main_en'))) or '—'}）", fs['material']),
            ('安装 / 摆放', f"{(structure.get('mount') or {}).get('value') or '—'}", fs['mount']),
            ('尺寸', str((ct.get('dimensions') or {}).get('value') or '—'), fs['dimensions']),
            ('重量', str((ct.get('weight') or {}).get('value') or '—'), fs['weight']),
            ('颜色', '、'.join(map(str, (ct.get('colors') or {}).get('value') or [])) or '—', fs['colors']),
            ('包装清单', '、'.join(map(str, (ct.get('package_contents') or {}).get('value') or [])) or '—', fs['package_contents']),
            ('适配', '、'.join(map(str, (ct.get('compatible_with') or {}).get('value') or [])) or '—', fs['compatible_with'])]
    for comp in material.get('components') or []:
        if isinstance(comp, dict):
            rows.insert(1, (f"部件 · {comp.get('part') or ''}", str(comp.get('material_cn') or '—'), str(comp.get('source_status') or 'missing')))
    for key, label in [('uses', '用途（页面展示/待样品验证）'), ('audiences', '使用人群'), ('scenarios', '使用场景'), ('pain_points', '痛点（真实VOC）')]:
        rows.append((label, str((ct.get(key) or {}).get('value') or '缺失'), fs.get(key, 'missing')))
    lines = [f"# 产品 CT · {result.get('product') or ''}", '',
             '选定款到底是什么、不是什么。S2 词库、S3 视觉、S4 否定词都以这份为准；市场调研画像里的材质只代表市场。', '',
             '## 是什么', '', '| 项 | 本品 | 来源 |', '|---|---|---|']
    lines += [f'| {k} | {v} | {STATUS_CN.get(s, s)} |' for k, v, s in rows]
    feats = [f for f in structure.get('features') or [] if isinstance(f, dict)]
    if feats:
        lines += ['', '结构 / 功能：' + '；'.join(f"{f.get('item_cn') or ''}（{', '.join(terms(f.get('en_terms')))}，{STATUS_CN.get(str(f.get('source_status')), f.get('source_status'))}）" for f in feats)]
    nt = result['not_this']
    lines += ['', '## 不是什么（S2 词库挡掉，S4 可作否定词）', '', '| 类 | 词 |', '|---|---|']
    for kind, label in (('materials', '材质'), ('forms', '形态 / 安装'), ('features', '功能'), ('colors', '颜色')):
        if nt.get(kind):
            lines.append(f"| {label} | {', '.join(nt[kind])} |")
    if result['errors']:
        lines += ['', '## 必须改', ''] + [f'- {e}' for e in result['errors']]
    if result['warnings']:
        lines += ['', '## 提醒', ''] + [f'- {w}' for w in result['warnings']]
    if result.get('sources'):
        lines += ['', '## 全图规格来源', '', '| 字段 | 图片/页面 | 状态 |', '|---|---|---|']
        lines += [f"| {key} | {node.get('source')} | {node.get('status')} |" for key, node in result['sources'].items()]
    if result.get('self_checks'):
        lines += ['', '## 包装与箱规自检', ''] + [f"- {row['status']}：{row['message']}" for row in result['self_checks']]
    if result.get('supplier_questions'):
        lines += ['', '## 厂家确认清单（异步，不等报价）', ''] + [f'{n}. {question}' for n, question in enumerate(result['supplier_questions'], 1)]
    return '\n'.join(lines) + '\n'


def run(root, observations=None):
    delivery_scripts = Path(__file__).resolve().parents[3] / 'scripts'
    previous_paths = list(sys.path)
    try:
        sys.path.insert(0, str(delivery_scripts))
        from r01_native import execute_native
        from s1_readability import reader_projection
        compact = execute_native(root, lambda view: run(view, observations), step=native_step(root),
                                 report_paths=[MD])
    finally:
        sys.path[:] = previous_paths
    if compact is not NotImplemented:
        return compact
    root = Path(root)
    if observations is not None and (root / CT).is_file():
        merged = apply_image_observations(load(root / CT), observations)
        (root / CT).write_text(yaml.safe_dump(merged, allow_unicode=True, sort_keys=False), encoding='utf8')
    result = evaluate(root)
    if result['status'] == 'missing':
        return result, 2
    md = render(result)
    if (delivery_scripts/'kb_ui.py').is_file():
        previous_paths=list(sys.path);sys.path.insert(0,str(delivery_scripts))
        try:
            from kb_ui import enrich_report
            md=enrich_report(md,root,delivery_scripts.parent,'S1',native_step(root))
        finally:sys.path[:]=previous_paths
    md = reader_projection(md)
    (root / MD).write_text(md, encoding='utf-8')
    status = {'generator': 'product_ct.py', 'status': result['status'], 'ct_sha256': sha(root / CT),
              'md_sha256': sha(root / MD), 'errors': result['errors'], 'warnings': result['warnings'],
              'material_cn': result['material_cn'], 'material_status': result['material_status'],
              'mount': result['mount'], 'ours': result['ours'], 'not_this': result['not_this']}
    status.update(self_checks=result['self_checks'], supplier_questions=result['supplier_questions'],
                  image_scan=result['image_scan'], sources=result['sources'])
    (root / STATUS).write_text(json.dumps(status, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return {k: status[k] for k in ('status', 'errors', 'warnings', 'material_cn', 'material_status')} | {'md': MD}, (2 if result['errors'] else 0)


def voc_from_reviews(root, doc):
    """S1-R31: pain points come pre-filled from S1 review quotes (voc_refs); buyers / scenes get the praise quotes
    as candidates to read. The 3.0.29 run left all three as missing although the reviews were already saved."""
    path = Path(root) / MARKET_FACTS
    try:
        analysis = json.loads(path.read_text(encoding='utf-8')).get('market_analysis') or {}
    except (OSError, ValueError):
        return
    pains = [row for row in analysis.get('pain_points') or [] if row.get('quote')][:5]
    if pains:
        doc['pain_points'].update(
            value=[row['pain'] for row in pains], source_status='review_extracted',
            source=MARKET_FACTS + '#market_analysis.pain_points',
            voc_refs=[{'theme': row['pain'], 'asin': row.get('source'), 'count': row.get('count'),
                       'quote': row['quote'], 'review_source': row.get('review_source')} for row in pains])
    praise = [{'theme': row.get('motive'), 'asin': row.get('source'), 'quote': row['quote']}
              for row in analysis.get('purchase_motives') or [] if row.get('quote')][:5]
    for key in ('audiences', 'scenarios'):
        if praise and not doc[key]['value']:
            doc[key]['voc_candidates'] = praise  # read these, then write value / en_terms and a source_status


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--run', required=True, help='产品根目录')
    ap.add_argument('--init', action='store_true')
    ap.add_argument('--image-observations', type=Path, help='宿主逐张识别全部主图/详情图的真实结构化回包；不得伪造已扫描')
    ap.add_argument('--images-root', type=Path, help='所选货源本地主图和详情图共同根；自动枚举校验完整覆盖')
    ap.add_argument('--observation-template', type=Path, help='生成逐图识别回执模板到此文件（需 --images-root），逐张填好后作为 --image-observations 传入')
    args = ap.parse_args(argv)
    root = Path(args.run)
    if args.observation_template:
        if args.images_root is None: ap.error('--observation-template requires --images-root')
        template = observation_template(root, args.images_root)
        args.observation_template.write_text(json.dumps(template, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'ok': True, 'template': str(args.observation_template), 'images': len(template['images'])}, ensure_ascii=False))
        return 0
    observations = load(args.image_observations) if args.image_observations else None
    if observations is not None:
        if args.images_root is None: ap.error('--image-observations requires --images-root; declared complete is not trusted')
        observations = verify_image_scan(root, args.images_root, observations)
    if args.init:
        print(json.dumps(init(root, observations), ensure_ascii=False))
        return 0
    out, code = run(root, observations)
    print(json.dumps(out, ensure_ascii=False))
    return code


if __name__ == '__main__':
    sys.exit(main())
