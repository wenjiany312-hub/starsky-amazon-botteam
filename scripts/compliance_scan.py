"""Source-attributed S2 compliance, current-input proofs and manual local rescans.

This is a conservative project guard, not a legal opinion or Amazon algorithm.
No network calls or backend writes. All public verification recomputes current inputs.
"""
from __future__ import annotations
import argparse
import copy
from datetime import date
from functools import lru_cache
import hashlib
import html
from html.parser import HTMLParser
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile
import unicodedata
import uuid
import yaml

HOME = Path(__file__).resolve().parents[1]
VERSION = 'r02.h1'

# Editorial definitions, not VOC or an Amazon algorithm. Aliases share one
# semantic identity, so relabeling five size questions cannot create coverage.
QA_INTENTS = {
    'dimensions': (('dimensions','size','measurement'), r'\b(size|dimension|dimensions|large|big|long|wide|width|length|height|measure)\b', r'\b(cm|mm|inch|inches|feet|foot|metre|meter|dimension|dimensions|width|length|height|measure|measures)\b'),
    'material': (('material','fabric'), r'\b(material|fabric|made|composition)\b', r'\b(material|fabric|made|cotton|plastic|steel|oxford|wood|leather)\b'),
    'contents': (('contents','included','package-contents'), r'\b(include|includes|included|come|comes|contain|contains|contents)\b', r'\b(include|includes|included|empty|without|contain|contains|supplied|only)\b'),
    'use': (('use','illustrated-use','usage','purpose'), r'\b(shown|use|used|usage|purpose|intended)\b', r'\b(shown|shows|display|use|used|store|stores|hold|holds|organize|carry|arrange)\b'),
    'gift': (('gift','gifting'), r'\b(gift|gifting|present|recipient)\b', r'\b(gift|recipient|receiving|present)\b'),
    'care': (('care','cleaning'), r'\b(clean|cleaning|wash|washing|care)\b', r'\b(clean|wash|wipe|damp|dry|care)\b'),
    'assembly': (('assembly','installation'), r'\b(install|installation|assemble|assembly|set up)\b', r'\b(install|assemble|assembly|attach|screw|setup|set up)\b'),
    'capacity': (('capacity','quantity'), r'\b(capacity|how many|how much)\b', r'\b(hold|holds|pockets|slots|capacity|liters|litres|count)\b'),
    'fitment': (('fitment','compatibility'), r'\b(fit|fits|compatible|compatibility)\b', r'\b(fit|fits|compatible|compare|check|dimensions)\b'),
    'color': (('color','colour','finish'), r'\b(color|colour|colors|colours|finish)\b', r'\b(color|colour|black|khaki|white|red|blue|green|finish)\b'),
}


def module(name, relative):
    spec = importlib.util.spec_from_file_location('_h_' + name, HOME / relative)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


@lru_cache(maxsize=1)
def operation_runtime():
    return module('operation_rules','scripts/platform_rules.py').operation_runtime()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def day(today=None):
    return date.fromisoformat(str(today)[:10]) if today else date.today()


def read(path):
    return yaml.load(Path(path).read_text(encoding='utf-8-sig'), Loader=getattr(yaml, 'CSafeLoader', yaml.SafeLoader))


def safe_path(root, path):
    root = Path(root).resolve(); target = (root / path).resolve()
    if not target.is_relative_to(root): raise ValueError('compliance_path_escapes_root')
    return target


def atomic(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(prefix='.h-', dir=path.parent)
    try:
        with os.fdopen(handle, 'w', encoding='utf8') as f:
            f.write(json.dumps(data, ensure_ascii=False, indent=2)+'\n'); f.flush(); os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp): os.unlink(temp)


@lru_cache(maxsize=32768)
def normalized(text):
    for _ in range(8):
        decoded = html.unescape(text)
        if decoded == text: break
        text = decoded
    else:
        if html.unescape(text) != text: raise ValueError('entity_nesting_too_deep')
    text = unicodedata.normalize('NFKC', text).casefold()
    text = ''.join(c for c in text if unicodedata.category(c) != 'Cf')
    return text.translate(str.maketrans({'–':'-', '—':'-', '‑':'-', '’':"'"}))


class TextViews(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.parts=[]; self.attribute_values=[]
    def handle_data(self, data): self.parts.append(data)
    def handle_starttag(self, tag, attrs):
        if tag in {'p','br','li','ul','div','h1','h2','h3','tr'}: self.parts.append(' ')
        self.attribute_values += [v for _,v in attrs if v]
    def handle_endtag(self, tag):
        if tag in {'p','li','ul','div','h1','h2','h3','tr'}: self.parts.append(' ')


def views(text):
    value = normalized(text); parser = TextViews(); parser.feed(value); parser.close()
    # Rendered text keeps inline splits together; hidden/attribute text is also scanned.
    return list(dict.fromkeys([''.join(parser.parts), *parser.attribute_values]))


# Negatives kept only to trim search/ad traffic (low relevance, a reviewer's call) say nothing false about the
# product, so copy isolates the bare term ('bins') without banning phrases that contain it ('drawer organizer bins').
# Anything else - attribute conflicts, excluded roots, irrelevant categories, brands, claims, or no recorded reason -
# keeps word-boundary blocking everywhere. The ad negative itself (scope and match type) is untouched.
TRAFFIC_ONLY_NEGATIVE = {'no_product_root', 'review_decision', 'sif_high_no_root', 'adjacent', 'direct_but_sif_low'}


def negative_copy_mode(row):
    # Only reason notes carry a code; the asset writer also appends {compliance_hits, layer_hashes} audit notes,
    # which say nothing about why the term is negative and must not flip the row back to word blocking.
    codes = {str(n['code']) for n in row.get('notes') or [] if isinstance(n, dict) and n.get('code')}
    return 'exact' if codes and codes <= TRAFFIC_ONLY_NEGATIVE else 'word'


@lru_cache(maxsize=32768)
def pattern(term, compact=False, exact=False):
    term = normalized(term)
    if exact:
        # The whole field is this term, give or take surrounding punctuation ('bins', not 'drawer organizer bins').
        body = r'[\s\-]+'.join(re.escape(p) for p in re.split(r'[\s\-]+', term.strip()) if p)
        return re.compile(r'^[\W_]*(' + body + r')[\W_]*$')
    if compact:
        body = r"[\s\-.'’_]*".join(re.escape(c) for c in term if c.isalnum()) + r"(?:[\s\-.'’_]*s)?"
    else:
        body = r'[\s\-]+'.join(re.escape(p) for p in re.split(r'[\s\-]+', term.strip()) if p)
    return re.compile(r'(?<!\w)' + body + r'(?!\w)')


@lru_cache(maxsize=4)
def base_entries(content):
    data = yaml.load(content, Loader=getattr(yaml, 'CSafeLoader', yaml.SafeLoader)); entries=[]
    groups = list(data['chapters'])
    groups.append({'id':'supplements', 'name':'审核补充', 'source':data['supplements']['source'], 'entries':data['supplements']['entries']})
    for group in groups:
        for i, entry in enumerate(group['entries']):
            entries.append({**entry, 'id':group['id']+':'+str(i), 'layer':'platform_base',
                'source':group.get('source', data['source']), 'chapter':group['name'],
                'match_mode':entry.get('match_mode', group.get('match_mode', 'word'))})
    return entries


def ip_entries(series):
    relative='S1_选品板块/amazon-ip-risk-assessment/04_compliance_blacklist.yaml'
    path=safe_path(series,relative)
    if not path.is_file():
        native = module('native_ip_restore', 'scripts/r01_native.py')
        native.store_module().CompactCase(series).restore_legacy()
    content=operation_runtime().read_bytes(path)
    if content is None:
        state_path = safe_path(series, 'workflow_state.yaml')
        state = read(state_path) if state_path.is_file() else {}
        state = state if isinstance(state, dict) else {}
        execution = state.get('s1_execution') or (state.get('flow') or {}).get('s1_execution') or {}
        completed = any((row.get('n') == 8 or 'ip' in str(row.get('step', '')).lower())
                        and row.get('outcome') in {'complete', 'partial', 'deferred'}
                        for row in execution.get('records', []) if isinstance(row, dict))
        if completed or state.get('s1_decision') or (state.get('stage_acceptance') or {}).get('S1'):
            raise ValueError('找不到 S1 侵权黑名单，先修复再写文案或出图')
        return [], None
    data=yaml.load(content,Loader=getattr(yaml,'CSafeLoader',yaml.SafeLoader)); result=[]; hashed=hashlib.sha256(content).hexdigest()
    if not isinstance(data, dict):
        raise ValueError('S1 侵权黑名单格式无效，先修复再写文案或出图')
    for field in ['blocked_terms','forbidden_terms','brand_terms_ads_only']:
        for item in data.get(field,[]):
            entry={'term':item} if isinstance(item,str) else dict(item)
            if not isinstance(entry.get('term'),str): raise ValueError('malformed_product_ip_term')
            is_brand=field=='brand_terms_ads_only' or '牌' in str(entry.get('reason','')) or entry.get('kind')=='brand'
            result.append({**entry,'id':'ip:'+field+':'+entry['term'],'level':'block','layer':'series_ip',
                'source':{'reference':relative,'sha256':hashed},'match_mode':'compact' if is_brand else 'word', 'is_brand':is_brand})
    brands=data.get('brand_terms') or {}
    for item in brands.get('ads_only_pool',[]) if isinstance(brands,dict) else brands:
        term=item if isinstance(item,str) else item.get('term')
        if term: result.append({'term':term,'id':'ip:brand:'+term,'level':'block','layer':'series_ip','source':{'reference':relative,'sha256':hashed},'match_mode':'compact','is_brand':True})
    return result,hashed


PRODUCT_PROFILE = 'S1_选品板块/amazon-market-research/01_product_profile.yaml'


def product_root_tokens(series):
    """Word sets of this product's S1 core roots ('drawer organizer' → {drawer, organizer})."""
    try:
        content = operation_runtime().read_bytes(safe_path(series, PRODUCT_PROFILE))
    except (OSError, ValueError):
        return []
    if not content:
        return []
    profile = yaml.load(content, Loader=getattr(yaml, 'CSafeLoader', yaml.SafeLoader)) or {}
    roots = profile.get('core_roots') if isinstance(profile, dict) else None
    return [set(re.findall(r'[a-z0-9]+', str(root).casefold())) for root in roots or [] if str(root).strip()]


def layers(series=None, sku=None, marketplace=None):
    key=('compliance_layers',str(Path(series).resolve()) if series else None,digest(sku),marketplace)
    return operation_runtime().cached(key,lambda:_layers(series,sku,marketplace))


def _layers(series=None, sku=None, marketplace=None):
    path = HOME/'templates/compliance_base_lexicon.yaml'
    content = operation_runtime().read_bytes(path); entries = copy.deepcopy(base_entries(content))
    hashes = {'platform_base':hashlib.sha256(content).hexdigest()}
    if series:
        ip,hashed=ip_entries(series);entries+=ip;hashes['series_ip']=hashed
        kw = module('asset', 'scripts/keyword_asset.py')
        # Current classified views come from E's public snapshot API, not sidecar guesses.
        asset=kw.read_snapshot(series)
        snapshot = asset.select_many(lists=('brand','negative'),sku=sku)
        own_roots = product_root_tokens(series)
        for name in ['brand', 'negative']:
            rows = [r for r in snapshot.get(name, []) if not marketplace or r.get('marketplace') == marketplace.upper()]
            hashes[name] = digest(rows)
            for row in rows:
                # A search/ad negative made only of words from this product's own core roots ('drawer' for a
                # drawer organizer) trims broad traffic; it is not a consumer-copy ban on the product's name.
                words = set(re.findall(r'[a-z0-9]+', str(row.get('term_normalized') or row.get('term') or '').casefold()))
                if name == 'negative' and words and any(words <= root for root in own_roots):
                    continue
                entries.append({'id':name+':'+row['term_normalized'], 'term':row['term'], 'level':'block',
                    'layer':'series_'+name, 'source':row.get('sources') or row.get('source') or row.get('lineage') or 'series asset',
                    'match_mode':'compact' if name=='brand' else negative_copy_mode(row), 'replace':[], 'note':'本系列品牌/不相关词限制'})
        hashes['asset'] = asset.sha256
        custom = safe_path(series, '_词库资产/compliance_custom.yaml')
        custom_content=operation_runtime().read_bytes(custom)
        hashes['custom'] = hashlib.sha256(custom_content).hexdigest() if custom_content is not None else None
        if custom_content is not None:
            data = yaml.load(custom_content,Loader=getattr(yaml,'CSafeLoader',yaml.SafeLoader))
            for i, item in enumerate(data.get('terms', [])):
                e = {'term':item} if isinstance(item, str) else dict(item)
                if not e.get('term'): raise ValueError('custom_term_missing')
                entries.append({**e, 'id':'custom:'+str(i), 'level':e.get('level','block'), 'layer':'user_custom', 'source':e.get('source','user custom')})
    return entries, hashes


def proof_for(term, evidence, today):
    for item in evidence.get('proofs', []):
        if not isinstance(item, dict) or item.get('kind') not in {'test_report','certificate','sample_test'} or item.get('status')!='verified' or not item.get('reviewed_by'): continue
        if not all(evidence.get(k) and item.get(k)==evidence[k] for k in ('product_id','sku','marketplace')): continue
        if normalized(term) not in [normalized(x) for x in item.get('claims', [])]: continue
        try:
            if not (day(item['valid_from']) <= today <= day(item['valid_until'])): continue
            path = safe_path(evidence['root'], item['path'])
            content=operation_runtime().read_bytes(path)
            if content is None or hashlib.sha256(content).hexdigest()!=str(item.get('sha256','')).removeprefix('sha256:'): continue
        except (KeyError, ValueError, OSError): continue
        return item['id']
    return None


_TERM_INDEX = {}


def term_index(entries):
    """Skip lexicon entries that cannot match: a word/exact term needs its first word somewhere in the text.

    A full series lexicon is ~10k entries; regex-scanning all of them for each of ~19k keywords is what made
    package finalize take minutes. Compact (brand) terms tolerate inner separators, so they are always scanned.
    """
    cached = _TERM_INDEX.get(id(entries))
    if cached and cached[0] is entries: return cached[1]
    keyed, by_word, always = [], {}, []
    for position, entry in enumerate(entries):
        terms = list(entry.get('terms') or [entry.get('term')]) + list(entry.get('variants') or [])
        keyed.append((entry, terms))
        firsts = set()
        for term in terms:
            first = re.findall(r'\w+', normalized(term)) if isinstance(term, str) and entry.get('match_mode') != 'compact' else []
            if not first: firsts = None; break
            firsts.add(first[0])
        if firsts is None: always.append(position)
        else:
            for word in firsts: by_word.setdefault(word, []).append(position)
    if len(_TERM_INDEX) > 8: _TERM_INDEX.clear()
    _TERM_INDEX[id(entries)] = (entries, (keyed, by_word, always))
    return keyed, by_word, always


class ScanSession:
    """One current native layer snapshot per operation, not a serialized bypass flag."""
    def __init__(self, series=None, sku=None, marketplace=None):
        self.operation=operation_runtime().current()
        self.entries,self.hashes=layers(series,sku,marketplace)


@operation_runtime().boundary
def scan_fields(fields, *, series=None, evidence=None, today=None):
    public_evidence={k:v for k,v in (evidence or {}).items() if k!='_compliance_session'}
    try:key=('compliance_scan',digest([fields,str(series),public_evidence,str(day(today))]))
    except TypeError:return _scan_fields(fields,series=series,evidence=evidence,today=today)
    return copy.deepcopy(operation_runtime().cached(key,lambda:_scan_fields(fields,series=series,evidence=evidence,today=today)))


def _scan_fields(fields, *, series=None, evidence=None, today=None):
    evidence = evidence or {}; current = day(today)
    session=evidence.get('_compliance_session')
    if isinstance(session,ScanSession) and session.operation is operation_runtime().current(): entries,hashes=session.entries,session.hashes
    else: entries, hashes = layers(series, evidence.get('sku'), evidence.get('marketplace'))
    hits=[]; errors=[]
    for field, raw in fields.items():
        if raw is None: continue  # Missing required content is validated by the field contract.
        if not isinstance(raw,str): errors.append('malformed_consumer_field:'+field); continue
        try: texts=views(raw)
        except (ValueError, TypeError): errors.append('invalid_encoded_field:'+field); continue
        found=[]
        keyed, by_word, always = term_index(entries)
        words = {w for text in texts for w in re.findall(r'\w+', text)}
        candidates = sorted(set(always).union(*(by_word.get(w, ()) for w in words)))
        for entry, terms in (keyed[i] for i in candidates):
            for term in terms:
                if not isinstance(term,str) or not term: errors.append('invalid_lexicon_entry'); continue
                for view_index, text in enumerate(texts):
                    for match in pattern(term, entry.get('match_mode')=='compact', entry.get('match_mode')=='exact').finditer(text):
                        level=entry['level']; reason=entry.get('context') or entry.get('requires') or entry.get('note') or level
                        proof=None
                        if level=='conditional':
                            proof=proof_for(term,evidence,current); level='allowed' if proof else 'block'
                            reason='applicable_evidence:'+proof if proof else 'missing_current_applicable_certificate_or_test'
                        if level=='seasonal':
                            window=(evidence.get('seasonal_windows') or {}).get(normalized(term), {})
                            try: active=bool(window.get('source')) and day(window['valid_from'])<=current<=day(window['valid_until'])
                            except (KeyError,ValueError): active=False
                            level='allowed' if active else 'block'; reason='seasonal_active' if active else 'seasonal_window_missing_or_expired'
                        if level=='warn':
                            if normalized(term) in {'treat','treats','treatment','prevent','prevents','prevention','resistant'} and re.search(r'\b(cancer|diabetes|disease|bacteria|microbes|mold|mildew)\b',text):
                                level='block'; reason='medical_or_pesticide_context'
                            elif normalized(term)=='grinder' and re.search(r'\b(herb|weed)\b',text): level='block'; reason='restricted_context'
                        row={'field':field,'found':match.group(), 'span':[match.start(),match.end()], 'view':view_index,
                             'level':level,'original_level':entry['level'],'entry_id':entry['id'],'reason':reason,
                             'sources':[{'layer':entry['layer'],'source':entry.get('source'),'entry_id':entry['id']}],
                             'replacements':entry.get('replace',[]),'proof_id':proof}
                        # Overlapping aliases/layers collapse, keeping the strictest interpretation and all attribution.
                        prior=next((h for h in found if h['view']==view_index and h['span'][0]<row['span'][1] and row['span'][0]<h['span'][1]),None)
                        if prior:
                            prior['sources'] += [s for s in row['sources'] if s not in prior['sources']]
                            if row['level']=='block': prior.update(level='block',reason=reason)
                            prior['span']=[min(prior['span'][0],row['span'][0]),max(prior['span'][1],row['span'][1])]
                        else: found.append(row)
        hits += found
    count=sum(h['level']=='block' for h in hits)+len(errors)
    return {'status':'needs_changes' if count else 'pass','blocked':bool(count),'blocked_count':count,
            'hits':hits,'errors':errors,'layer_hashes':hashes,'checked_on':current.isoformat(),'scanner_version':VERSION}


def listing_fields(sku):
    validator=module('validator','skills/amazon-listing-copywriter/scripts/validate_listing_copy.py')
    fields=validator.consumer_fields(sku)
    # Preserve raw description aliases/invalid attributes so normalization cannot hide unsafe text.
    raw=validator.description_value(sku)
    if raw is not None: fields['description']=raw
    for key in validator.DESCRIPTION_KEYS:
        if key in sku and sku[key]!=raw: fields[key+'.conflict']=sku[key]
    if 'attributes' in sku and not isinstance(sku['attributes'],dict): fields['attributes']=sku['attributes']
    for key,value in (sku.get('attributes') or {}).items() if isinstance(sku.get('attributes'),dict) else []:
        fields['attributes.'+key]=value
    return fields


def editorial_check(sku, context, mapping, *, rules=None):
    effective=module('editorial_rules','scripts/platform_rules.py').listing_rules(rules)
    fields=listing_fields(sku); texts={k:normalized(v) for k,v in fields.items() if isinstance(v,str) and not k.endswith('.source')}
    checks=[]
    def add(key, ok, detail, advisory=False): checks.append({'id':key,'ok':bool(ok),'detail':detail,**({'advisory':True} if advisory else {})})
    # Explicit field/quote annotations: labels alone never prove coverage.
    facts={f['id']:f for f in context.get('facts',[]) if isinstance(f,dict) and f.get('id') and f.get('source')
           and f.get('status') in {'verified','user_confirmed','page_extracted','image_extracted','user_selected_and_image_extracted','supplier_confirmed','sample_measured'}
           and f.get('copy_permission')!='禁止'}
    cues={'shape':r'\b(bag|roll|holder|container|pouch|round|square|bottle|box|robe|bathrobe|garment|wrap-around)\b',
          'function':r'\b(store|stores|storage|organize|organizer|carry|hold|holds|protect|sort)\b',
          'scene':r'\b(workshop|studio|travel|home|kitchen|bathroom|workspace|desk|counter)\b',
          'audience':r'\b(adults|artists|mechanics|dad|dads|men|women|users|makers|people|recipient|recipients)\b',
          'gift':r'\b(gift|gifts|present|presents|gifting)\b'}
    for intent in ['shape','function','scene','audience','gift']:
        annotations=(sku.get('editorial_intents') or {}).get(intent,[])
        if not annotations:
            # Reuse G's actual field evidence. Ordinary gift/recipient language is
            # editorial intent, not a claim that VOC or a demographic study exists.
            for field, annotation in (sku.get('review_metadata') or {}).get('fields',{}).items():
                text=texts.get(field,'')
                if re.search(cues[intent],text):
                    annotations=annotations+[{'field':field,'quote':text,'evidence_refs':annotation.get('fact_refs') or []}]
        supported=[a for a in annotations if isinstance(a,dict) and a.get('quote') and a.get('field') in texts and
                   normalized(a['quote']) in texts[a['field']] and re.search(cues[intent],normalized(a['quote'])) and
                   a.get('evidence_refs') and all(ref in facts for ref in a['evidence_refs'])]
        # Intent cues are category templates and want verified facts; a miss is a review to-do, not a required change.
        add('intent_'+intent, supported, supported or '缺实际字段原句与来源引用', advisory=True)
    schema=context.get('required_attributes')
    attrs=sku.get('attributes') or {}
    names=schema.get('fields') if isinstance(schema,dict) else None
    # Many sellers cannot get the backend attribute schema; it is checked when supplied and otherwise becomes a
    # listing-day to-do (SP-API product type definition, template or backend screenshot), never a block on S2.
    applicable=bool(names and schema.get('source') and schema.get('product_type')==context.get('product_type'))
    missing=[k for k in names or [] if not (isinstance(attrs.get(k),str) and attrs[k].strip())] if applicable else []
    complete=applicable and not missing
    add('required_attributes',complete,
        '适用必填属性已填' if complete else
        ('上架前补填必填属性：'+'、'.join(missing)) if applicable else
        '未提供商品类型属性要求：上架前在后台核对必填属性（旗舰版可用 SP-API 读取商品类型定义；也可用类目模板或后台截图），不阻塞 S2',
        advisory=True)
    qa=sku.get('qa') if isinstance(sku.get('qa'),list) else []
    covered=[]
    for index,q in enumerate(qa,1):
        q=q if isinstance(q,dict) else {}
        question=q.get('question') if isinstance(q.get('question'),str) else ''
        answer=q.get('answer') if isinstance(q.get('answer'),str) else ''
        intent=next((name for name,(aliases,_,_) in QA_INTENTS.items() if q.get('intent') in aliases),None)
        annotations=(sku.get('review_metadata') or {}).get('fields',{})
        refs=q.get('evidence_refs') or list(dict.fromkeys((annotations.get(f'qa.{index}.question') or {}).get('fact_refs',[])+
            (annotations.get(f'qa.{index}.answer') or {}).get('fact_refs',[])))
        actual=bool(intent and re.search(QA_INTENTS[intent][1],normalized(question)) and re.search(QA_INTENTS[intent][2],normalized(answer)))
        supported=bool(refs) and all(ref in facts for ref in refs)
        covered.append({'index':index,'intent':intent,'question':question,'answer':answer,'evidence_refs':refs,
            'ok':actual and supported,'reason':'原句与已有来源覆盖受控编辑意图' if actual and supported else '缺受控意图、相应问答原句或已有来源引用'})
    required=effective['qa']['count']
    good=[row for row in covered if row['ok']]
    add('qa_intents',len(qa)==len(good)==required and len({q['intent'] for q in good})==required and
        len({normalized(q['question']) for q in good})==required and len({normalized(q['answer']) for q in good})==required,
        str(required)+'条问答须实际覆盖不同受控意图并引用已有来源；编号或同意图改写不算，未提供真实VOC时保持编辑拟题', advisory=True)
    checks[-1]['evidence']=covered
    core=sku.get('core_keywords') or context.get('core_keywords') or []
    if isinstance(core,str): core=[core]
    if not isinstance(core,list) or any(not isinstance(k,str) or not k.strip() for k in core): core=[]
    add('core_phrases',bool(core) and all(pattern(k).search(texts.get('title','')) and pattern(k).search(texts.get('item_highlights','')) for k in core),('本次明确选定核心词须分别在标题和亮点完整出现：'+', '.join(core)) if core else '缺本次明确选定主核心词；不将整库 core 标签推断为文案要求')
    gaps=[r for r in mapping.get('rows',[]) if r.get('missing_reason')]
    explanations=context.get('ct_gap_explanations') or {}
    def explained(row):
        value=explanations.get(row['ct_ref']) or row.get('missing_reason')
        return value if isinstance(value,str) and len(value.strip())>=4 and value.strip().casefold() not in {'explained','ok','done','pass'} else None
    add('ct_coverage',bool(mapping.get('rows')) and all(explained(r) for r in gaps),[{'ref':r['ct_ref'],'reason':r['missing_reason'],'explanation':explained(r)} for r in gaps])
    return {'ok':all(c['ok'] for c in checks if not c.get('advisory')),'checks':checks,
            'todos':[{'id':c['id'],'detail':c['detail']} for c in checks if c.get('advisory') and not c['ok']],
            'meaning':'编辑意图与完整性自检；不是Amazon算法评分、排名权重或业务批准'}


MEDIA_ROOT = 'S1_选品板块/1688-product-detail-scraper'


def _media_copy(root, relative, expected):
    base = Path(root).resolve() / MEDIA_ROOT
    if not base.is_dir() or not relative or Path(relative).is_absolute():
        return None
    for candidate in sorted(base.glob('**/' + relative)):
        if not candidate.resolve().is_relative_to(Path(root).resolve()) or not candidate.is_file():
            continue
        content = operation_runtime().read_bytes(candidate)
        if content is not None and hashlib.sha256(content).hexdigest() == expected:
            return content
    return None


def source_fingerprints(value, root):
    """Re-read file-backed CT, schema and evidence references, never cached hashes."""
    rows=[]
    def walk(node):
        if isinstance(node,dict):
            expected=node.get('sha256') or node.get('source_sha256')
            source=node.get('path') or node.get('source')
            if isinstance(source,str) and expected:
                try:
                    path=safe_path(root,source.split('#')[0])
                    content=operation_runtime().read_bytes(path)
                    if content is None:
                        # CT image sources are written relative to the 1688 offer media folder ('主图/main_01.jpg');
                        # find the file with the recorded hash there instead of reporting it missing.
                        content=_media_copy(root,source.split('#')[0],str(expected).removeprefix('sha256:'))
                    actual=hashlib.sha256(content).hexdigest() if content is not None else None
                    rows.append({'source':source,'expected':str(expected).removeprefix('sha256:'),'actual':actual})
                except (OSError,ValueError): rows.append({'source':source,'expected':expected,'actual':None})
            for child in node.values(): walk(child)
        elif isinstance(node,list):
            for child in node: walk(child)
    walk(value)
    return sorted({digest(r):r for r in rows}.values(),key=lambda r:(r['source'],str(r['expected'])))


@operation_runtime().boundary
def make_receipt(listing, *, series=None, context=None, rules=None, today=None, proposal=None, allowed_fields=None, mapping=None, editorial=False):
    context=context or {}; registry=module('rules','scripts/platform_rules.py')
    effective=registry.listing_rules(rules or context.get('listing_rules'))
    scans=[]; checks=[]; field_failures=[]
    for sku in (listing or {}).get('skus',[]):
        ev={**context, **(context.get('compliance_evidence') or {}), 'sku':sku.get('sku')}
        for key in ['product_id','marketplace']: ev[key]=context.get(key) or listing.get(key)
        if series: ev['root']=str(Path(series).resolve())
        scan=scan_fields(listing_fields(sku),series=series,evidence=ev,today=today); scan['sku']=sku.get('sku'); scans.append(scan)
        if editorial:
            checks.append({'sku':sku.get('sku'),**editorial_check(sku,context,mapping or {},rules=effective)})
            validator=module('field_validator','skills/amazon-listing-copywriter/scripts/validate_listing_copy.py')
            checked=validator.validate_sku(sku,listing.get('blacklist_terms') or [],profile='r02_s2',rules=effective,compliance_evidence=ev,series=series)
            field_failures += [{'sku':sku.get('sku'),**c} for c in checked['checks'] if c['level']=='fail' and
                (c['check']!='compliance_scan' or not scan['blocked'])]
            # User requires the conservative raw-byte target before publication;
            # permitted core reuse stays a quality warning, never a block by itself.
            size=len(sku.get('search_terms','').encode('utf8')) if isinstance(sku.get('search_terms',''),str) else 0
            completion=sku.get('search_terms_completion') or {}
            # An honest shortfall (every safe new word used, nothing repeated) is a gap to show, not a block.
            honest_gap=completion.get('status')=='target_gap' and not completion.get('core_repetition_used') and completion.get('output_bytes')==size
            if size<effective['search_terms']['target'][0] and not honest_gap:
                field_failures.append({'sku':sku.get('sku'),'check':'search_terms_project_min','level':'fail','message':'原始 UTF-8 字节未达到项目下限','actual':size})
    evidence_files=source_fingerprints(context,context.get('evidence_root') or series or context.get('root') or '.')
    for proof in (context.get('compliance_evidence') or context).get('proofs',[]):
        try:
            path=safe_path(series or context.get('root','.'),proof.get('path',''))
            content=operation_runtime().read_bytes(path)
            evidence_files.append({'path':str(path),'sha256':hashlib.sha256(content).hexdigest() if content is not None else None})
        except (OSError,ValueError): evidence_files.append({'path':'invalid','sha256':None})
    source_errors=[r['source'] for r in evidence_files if 'expected' in r and r['actual']!=r['expected']]
    count=sum(s['blocked_count'] for s in scans)+len(source_errors)
    failures=[c['id'] for s in checks for c in s['checks'] if not c['ok'] and not c.get('advisory')]
    todos=[{'sku':s.get('sku'),**t} for s in checks for t in s.get('todos') or []]
    receipt={'schema':VERSION, 'listing_hash':digest(listing), 'context_hash':digest(context), 'series':str(Path(series).resolve()) if series else None,
        'registry_hash':registry.fingerprint(),'effective_rules_hash':digest(effective),
        'scanner_hash':hashlib.sha256(operation_runtime().read_bytes(Path(__file__))).hexdigest(), 'evidence_files':evidence_files,'source_errors':source_errors,
        'mapping_hash':digest(mapping), 'editorial_required':editorial, 'proposal_hash':digest(proposal) if proposal is not None else None,
        'allowed_fields':sorted(allowed_fields) if allowed_fields is not None else None, 'scans':scans,'editorial':checks,
        'blocked':bool(count or not scans),'blocked_count':count or (1 if not scans else 0),'editorial_failures':failures,'listing_todos':todos,'field_failures':field_failures,
        'status':'needs_changes' if count or failures or field_failures or not scans else 'pass','checked_on':day(today).isoformat()}
    receipt['proof_hash']=digest(receipt)
    return receipt


def verify_current_compliance(receipt, *, listing, series=None, context=None, rules=None, today=None, proposal=None, allowed_fields=None, mapping=None, editorial=False):
    try:
        current=make_receipt(listing,series=series,context=context,rules=rules,today=today,proposal=proposal,allowed_fields=allowed_fields,mapping=mapping,editorial=editorial)
        errors=[]
        if receipt!=current: errors.append('compliance_proof_stale_or_tampered')
        if current['status']!='pass': errors.append('compliance_or_editorial_needs_changes')
        return {'ok':not errors,'status':'pass' if not errors else 'needs_changes','errors':errors,'blocked_count':current['blocked_count'], 'current':current}
    except (OSError,ValueError,TypeError,KeyError) as exc:
        return {'ok':False,'status':'needs_changes','errors':['compliance_verification_failed:'+str(exc)],'blocked_count':1}


def keyword_permission(row, *, series=None, evidence=None):
    out=copy.deepcopy(row)
    scan=scan_fields({'keyword':row.get('keyword',row.get('term'))},series=series,evidence=evidence)
    if scan['blocked']: out['listing_eligible']=False
    out['ad_eligible']=row.get('ad_eligible',False) is True
    out['compliance_scan']=scan
    return out


def add_custom(series, term, *, source):
    if not isinstance(term,str) or not term.strip() or len(term)>200 or not source: raise ValueError('custom_term_and_source_required')
    kw=module('asset','scripts/keyword_asset.py'); path=kw._path(series,'compliance_custom.yaml')
    with kw._lock(series):
        data=read(path) if path.is_file() else {'schema_version':'1.0','terms':[]}
        if not any(normalized(x if isinstance(x,str) else x['term'])==normalized(term) for x in data['terms']):
            data['terms'].append({'term':term.strip(),'level':'block','source':source,'added_at':date.today().isoformat()})
            kw._atomic(path,data)
    return {'path':str(path),'count':len(data['terms']),'requires_rescan':True}


def rescan_series(series, listings, *, context=None):
    rows=[]; errors=[]
    for row in listings:
        try:
            path=safe_path(series,row['path']); listing=read(path)
            if row['sku'] not in [s.get('sku') for s in listing.get('skus',[])]: raise ValueError('listed_sku_missing')
            receipt=make_receipt(listing,series=series,context=context)
            rows.append({'sku':row['sku'],'path':str(path),'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),**receipt})
        except (OSError,ValueError,KeyError,TypeError) as exc: errors.append({'listing':row,'error':str(exc)})
    path=safe_path(series,'_词库资产/compliance_rescans/'+uuid.uuid4().hex+'.json')
    result={'expected':len(listings),'scanned':len(rows),'complete':bool(listings) and not errors,'listings':rows,'errors':errors,
        'mode':'manual_local_snapshot_no_live_read_or_write','coverage_basis':'provided_listed_series_manifest',
        'record':str(path),'checked_on':date.today().isoformat()}
    atomic(path,result); return result


def main():
    p=argparse.ArgumentParser(description=__doc__); sub=p.add_subparsers(dest='command',required=True)
    add=sub.add_parser('custom-add'); add.add_argument('--series',required=True); add.add_argument('--term',required=True); add.add_argument('--source',default='user command')
    scan=sub.add_parser('scan'); scan.add_argument('--listing',required=True); scan.add_argument('--series'); scan.add_argument('--context'); scan.add_argument('--output',required=True)
    scan.add_argument('--summary',action='store_true',help='Print status, issue count and proof path; keep complete proof in --output')
    rescan=sub.add_parser('rescan-series'); rescan.add_argument('--series',required=True); rescan.add_argument('--listings-manifest',required=True); rescan.add_argument('--context')
    write=sub.add_parser('write-proof')
    for name in ['proposal','listing','context','output']: write.add_argument('--'+name,required=True)
    write.add_argument('--series');write.add_argument('--rollback',action='store_true')
    a=p.parse_args()
    if a.command=='custom-add': result=add_custom(a.series,a.term,source=a.source)
    elif a.command=='rescan-series': result=rescan_series(a.series,read(a.listings_manifest),context=read(a.context) if a.context else None)
    elif a.command=='write-proof':
        push=module('push','skills/amazon-sp-api-connector/scripts/push_listing_copy.py')
        result=push.create_write_proof(read(a.proposal),read(a.listing),read(a.context),series=a.series,rollback=a.rollback)
        atomic(a.output,result)
    else:
        result=make_receipt(read(a.listing),series=a.series,context=read(a.context) if a.context else None); atomic(a.output,result)
    shown={'status':result.get('status'),'issue_count':result.get('blocked_count',0)+len(result.get('editorial_failures',[]))+len(result.get('field_failures',[])),
           'report':a.output} if getattr(a,'summary',False) else result
    print(json.dumps(shown,ensure_ascii=False)); return 1 if result.get('status')=='needs_changes' or result.get('blocked') or result.get('complete') is False else 0


if __name__=='__main__': raise SystemExit(main())
