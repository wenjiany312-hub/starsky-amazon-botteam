#!/usr/bin/env python3
"""合规尺子：出口禁限 / 美国与亚马逊资质 / 高侵权品类 / 商标陷阱 / 必做检索，一张表看能不能做。

  <python> <skill>/scripts/compliance_ruler.py --run "<产品根>" --init   生成 04A_ruler_inputs.yaml（属性待判、检索待做）
  <python> <skill>/scripts/compliance_ruler.py --run "<产品根>"          出 04A_合规尺子.md + 04A_compliance_ruler.yaml

初筛，不是法律意见；只亮灯，不替用户判“做/不做”。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote_plus

import yaml

HERE = Path(__file__).resolve()
RULES = HERE.parents[1] / 'references' / 'compliance_ruler.yaml'
OUT = Path('S1_选品板块') / 'amazon-ip-risk-assessment'
INPUTS, MD, MODEL = '04A_ruler_inputs.yaml', '04A_合规尺子.md', '04A_compliance_ruler.yaml'
GENERATOR = 'compliance_ruler.py'
VERSION = '1.1'
LIGHT_ORDER = {'red': 3, 'orange': 2, 'yellow': 1, 'green': 0, 'gray': -1}
LIGHT_ICON = {'red': '🔴', 'orange': '🟠', 'yellow': '🟡', 'green': '🟢', 'gray': '⚪'}
LIGHT_WORD = {'red': '硬门槛', 'orange': '要资质', 'yellow': '要规避', 'green': '未命中', 'gray': '待核'}
SEARCH_DONE = {'searched', 'user_todo'}
SOURCES = [Path('S1_选品板块/amazon-market-research/01_product_profile.yaml'),
           Path('S1_选品板块/1688-product-detail-scraper/03_product_facts.yaml'),
           Path('S1_选品板块/amazon-market-research/05_cost_inputs.yaml')]


def load_yaml(path):
    try:
        data = yaml.safe_load(Path(path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return {}
    return data if isinstance(data, dict) else {}


def strings(node, out):
    if isinstance(node, str):
        text = node.strip()
        if text and not re.fullmatch(r'[\w./:-]*\.(ya?ml|md|html|json|png|jpe?g)|https?://\S+|[0-9a-f]{16,}', text):
            out.append(text)
    elif isinstance(node, dict):
        for value in node.values():
            strings(value, out)
    elif isinstance(node, list):
        for item in node:
            strings(item, out)
    return out


def product_terms(root):
    profile = load_yaml(root / SOURCES[0])
    terms = []
    for key in ('core_roots', 'core_keywords', 'product_type', 'product_name', 'product'):
        value = profile.get(key)
        terms += value if isinstance(value, list) else [value] if value else []
    return [t for t in dict.fromkeys(str(t).strip() for t in terms if t) if re.search(r'[A-Za-z]', t)][:5]


# S1-R5/R15: the order to try each required search before it may be left to the user. USPTO and Google pages are
# script-rendered, so curl gets nothing back — use the host's browser tool or a web search instead.
CHANNELS = {
    'uspto_trademark': ['浏览器工具打开 links 里的 tmsearch.uspto.gov，逐个查 queries（页面靠脚本渲染，curl 抓不到）',
                        '网页搜索：每个词搜 "<词> trademark USPTO"，看 USPTO 或公开商标镜像站的记录号与状态'],
    'design_patent': ['Sorftime MCP patent_search_from_keyword：keyword=每个查询词，country=["US"]，patent_type=[4]；相近的再用 patent_request 看附图',
                      '浏览器工具打开 links 里的 Google Patents（外观）'],
    'utility_patent': ['Sorftime MCP patent_search_from_keyword：keyword=功能词，country=["US"]，patent_type=[1,2]；相关的用 patent_request 看权利要求',
                       '浏览器工具打开 links 里的 Google Patents'],
    'image_reverse': ['Sorftime MCP patent_search_from_image：image=本品主图 URL，query_mode=1，country=["US"]（只覆盖外观专利，450 credits/次）',
                      '浏览器工具打开 Google Lens 上传本品主图，看是否某品牌独有外观'],
    'schedule_a': ['网页搜索 links 里的 "Schedule A" + 词 + lawsuit', '浏览器工具打开命中的诉讼页，记案号、法院、日期'],
}


def search_links(entry, terms):
    if 'url_template' in entry:
        return [entry['url_template'].replace('{q}', quote_plus(t)) for t in terms] or [entry['url_template'].split('?')[0]]
    return [entry['url']]


def skeleton(root, rules):
    scope = scoped_product(root)
    scanned = scope.pop('scan_text')
    terms = product_terms(root)
    return {
        'schema_version': VERSION, 'generator': GENERATOR, 'product': '', 'marketplace': 'US',
        'scan_text': list(dict.fromkeys(scanned)),
        **scope,
        'extra_text': [],
        'attributes': {key: {'value': None, 'evidence': '', 'label': label} for key, label in rules['attributes'].items()},
        'searches': [{'id': s['id'], 'title': s['title'], 'queries': terms, 'links': search_links(s, terms),
                      'channels': CHANNELS.get(s['id'], []),
                      'status': 'not_searched', 'date': '', 'result': '', 'reason': ''} for s in rules['required_searches']],
        'legend': 'attributes.*.value 填 true/false/null（是/否/待核），evidence 写本品依据；searches.*.status 填 searched（写 date、result）'
                  '或 user_todo（写 reason，并按 channels 逐个试过、tried 写每个渠道卡在哪；只有确需登录或付费库才留给用户）；'
                  '用 save_web_evidence.py 登记，不要手改',
    }


PRODUCT_FIELDS = ('product', 'product_type', 'product_type_en', 'product_name', 'core_roots',
                  'core_keywords', 'description', 'title', 'selling_points', 'materials', 'bom', 'BOM',
                  'target_spec', 'features', 'function', 'power', 'claims', 'package', 'components', 'closure')
UNCONFIRMED = re.compile(r'未知|未确认|待确认|未定|待定|未选|需核|非发现|不是已证明|'
                         r'\b(?:unknown|unconfirmed|pending|not confirmed)\b', re.I)


def product_text(node):
    """Only product claims, never metadata, references, exclusions or field names."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, list):
        return [text for item in node for text in product_text(item)]
    if not isinstance(node, dict):
        return []
    if node.get('status') in ('research_candidate', 'competitor_page_claim', 'excluded', 'reference'):
        return []
    if re.search(r'竞品|参考|待复核', str(node.get('label', ''))):
        return []
    keys = PRODUCT_FIELDS + ('label', 'root_words', 'name', 'material', 'component')
    return [text for key in keys if key in node for text in product_text(node[key])]


def scoped_product(root):
    profile = load_yaml(root / SOURCES[0])
    facts = load_yaml(root / SOURCES[1])
    selected = facts.get('selected_product')
    if not isinstance(selected, dict):
        variant = facts.get('selected_variant')
        selected = dict(variant) if isinstance(variant, dict) else {}
        if variant:
            selected.setdefault('sku', variant if isinstance(variant, str) else variant.get('sku_id'))
        if not selected:
            selected = {'sku': None, 'description': facts.get('product') or profile.get('product_type') or ''}
        elif selected.get('sku'):
            for key in PRODUCT_FIELDS:
                if key in facts:
                    selected.setdefault(key, facts[key])
    # A procurement facts file declares SKU scope explicitly; profile-only legacy runs still work.
    result = {'scan_text': product_text(profile), 'scan_scope': 'target_product_fields',
              'reference_products': facts.get('candidate_offers') or []}
    if facts:
        selected.setdefault('target_spec', facts.get('target_spec') or {})
        result['selected_product'] = selected
        result['scan_text'] = product_text(selected)
        result['scan_scope'] = 'selected_sku' if selected.get('sku') else 'target_spec_only'
    return result


def matcher(word):
    word = str(word).strip()
    if re.search(r'[一-鿿]', word):
        return lambda text: word in text
    pieces = [p for p in re.split(r'[\s-]+', word.lower()) if p]
    last = pieces[-1]
    if len(last) > 3 and last.endswith('s') and not last.endswith('ss'):
        pieces[-1] = last[:-1]
    # "Q-tips" also matches "qtip" / "q tips"; plural and hyphen/space variants are the same mark.
    pattern = re.compile(r'(?<![a-z0-9_])' + r'[\s-]?'.join(re.escape(p) for p in pieces) + r'(e?s)?(?![a-z0-9_])')
    def matches(text):
        normalized = text.lower()
        if word.lower() == 'lead':
            normalized = re.sub(r'\blead[\s_-]*time\b', '', normalized)
        if word.lower() == 'pattern':
            normalized = re.sub(r'\b(?:a4\s+pattern|pattern\s+(?:storage|pocket|compartment))\b', '', normalized)
        return bool(pattern.search(normalized))
    return matches


def hits(words, texts):
    found = []
    for word in words or []:
        test = matcher(word)
        sample = next((t for t in texts if test(t)), None)
        if sample is not None:
            found.append((str(word), sample[:80]))
    return found


def evaluate(doc, rules):
    selected = doc.get('selected_product')
    texts = (product_text(selected) if isinstance(selected, dict) else
             strings(doc.get('scan_text') or [], [])) + strings(doc.get('extra_text') or [], [])
    attrs, conflicts = {}, []
    overrides = (selected.get('attributes') or {}) if isinstance(selected, dict) else {}
    for key in rules['attributes']:
        node = overrides.get(key, (doc.get('attributes') or {}).get(key, {}))
        value = node.get('value') if isinstance(node, dict) else node
        evidence = (node.get('evidence') or '') if isinstance(node, dict) else ''
        pending = isinstance(node, dict) and node.get('status') in ('pending', 'unknown', 'unconfirmed', 'missing')
        contradictory = value is True and UNCONFIRMED.search(evidence) and (
            key in ('magnet_small', 'plant_animal_material', 'licensed_ip_character') or
            re.match(r'未知|未确认|待确认|unknown|unconfirmed', evidence, re.I))
        if pending or contradictory:
            if isinstance(value, bool):
                conflicts.append({'attribute': key, 'original_value': value, 'evidence': evidence,
                                  'resolution': '待核；未确认依据不能支持是/否'})
            value = None
        attrs[key] = {'value': value if isinstance(value, bool) else None,
                      'evidence': evidence,
                      'label': rules['attributes'][key]}
    items, reminders = [], []
    if isinstance(selected, dict) and not selected.get('sku') and doc.get('reference_products'):
        reminders.append({'id': 'REFERENCE-SKU', 'title': '参考款不代表本品', 'light': 'gray', 'evidence': [],
                          'action': '选 SKU 时避开印花款；需用图案时核实授权，选定后用该 SKU 实拍与 BOM 重查。'})
    for rule in rules['rules']:
        when = rule.get('when') or {}
        by_attr = [a for a in when.get('attributes', []) if attrs.get(a, {}).get('value') is True]
        rule_texts = texts
        if rule['id'] in ('CN-FUMIGATION', 'US-LACEY'):
            # Wood in a hoop to be stored is not wood in the bag. Use its actual BOM only.
            material_source = selected if isinstance(selected, dict) else doc
            rule_texts = strings({k: material_source.get(k) for k in ('materials', 'bom', 'BOM')}, [])
        by_word = hits(when.get('keywords'), rule_texts)
        if not by_attr and not by_word:
            continue
        denied = when.get('attributes') and all(attrs.get(a, {}).get('value') is False for a in when['attributes'])
        evidence = [f'属性：{attrs[a]["label"]}（{attrs[a]["evidence"] or "已判定"}）' for a in by_attr] + \
                   [f'文本含「{w}」：{s}' for w, s in by_word[:3]]
        item = {'id': rule['id'], 'scope': rule['scope'], 'title': rule['title'], 'light': rule['light'],
                'requires': rule['requires'], 'action': rule['action'], 'evidence': evidence}
        if rule['id'] in ('IP-LICENSED', 'IP-ARTWORK') and isinstance(selected, dict) and not selected.get('sku'):
            item['light'] = 'gray'
            item['action'] = '选 SKU 时避开印花款；采购款确定后核实图案和授权。' + rule['action']
            reminders.append(item)
            continue
        if denied and not by_attr:
            item['light'] = 'yellow'
            item['action'] = '产品属性已判定不属于此类：Listing/图片/广告别往这个方向写，否则会被按此类要求。' + rule['action']
            reminders.append(item)
        else:
            items.append(item)
    traps = [{'term': t['term'], 'generic': t['generic'], 'owner': t.get('owner', '注册商标'), 'seen_in': s}
             for t in rules['generic_trademark_traps'] for w, s in hits([t['term']], texts)]
    compat = [{'brand': w, 'seen_in': s} for w, s in hits(rules['compatibility_brands'], texts)]
    if compat and attrs['compatible_other_brand']['value'] is not True:
        attrs['compatible_other_brand']['evidence'] = attrs['compatible_other_brand']['evidence'] or '文本出现他牌名'
    unknown = [a for a, v in attrs.items() if v['value'] is None]
    searches = []
    supplied = {entry['id']: entry for entry in doc.get('searches') or [] if entry.get('id')}
    terms = [str(t) for t in doc.get('search_terms') or []] or [doc.get('product') or 'product name']
    for required in rules['required_searches']:
        entry = dict(supplied.get(required['id']) or {'id': required['id'], 'title': required['title']})
        entry.setdefault('queries', terms)
        if not entry['queries']:
            entry['queries'] = terms
        entry.setdefault('links', search_links(required, entry['queries']))
        entry.setdefault('channels', CHANNELS.get(required['id'], []))
        status = entry.get('status') or 'not_searched'
        problem = ''
        if status == 'searched' and not str(entry.get('result') or '').strip():
            problem = '标了 searched 但没写结果'
        elif status == 'user_todo' and not str(entry.get('reason') or '').strip():
            problem = '标了 user_todo 但没写原因'
        elif status not in SEARCH_DONE:
            problem = '未检索'
        card = self_check_card(entry, required) if status != 'searched' else None
        searches.append({**entry, 'status': status, 'problem': problem, 'self_check_card': card})
    expected = {s['id'] for s in rules['required_searches']}
    missing_ids = sorted(expected - set(supplied))
    worst = max([LIGHT_ORDER[i['light']] for i in items] + [1 if traps or compat else 0])
    overall = 'gray' if worst == 0 and unknown else next(k for k, v in LIGHT_ORDER.items() if v == worst)
    items.sort(key=lambda i: (-LIGHT_ORDER[i['light']], i['id']))
    model = {
        'schema_version': VERSION, 'generator': GENERATOR, 'rules_as_of': rules.get('as_of'),
        'product': doc.get('product') or '', 'marketplace': doc.get('marketplace') or 'US',
        'overall_light': overall,
        'counts': {k: sum(1 for i in items if i['light'] == k) for k in ('red', 'orange', 'yellow')},
        'items': items, 'reminders': reminders, 'trademark_traps': traps, 'compatibility_brands': compat,
        'attributes': attrs, 'unknown_attributes': unknown, 'searches': searches, 'missing_searches': missing_ids,
        'pending_confirmations': [{'attribute': key, **attrs[key]} for key in unknown],
        'input_conflicts': conflicts, 'scan_scope': doc.get('scan_scope', 'selected_sku' if selected else 'product_text'),
        'searches_complete': not missing_ids and all(s['status'] == 'searched' and not s['problem'] for s in searches),
        'search_plan_complete': not missing_ids and all(not s['problem'] for s in searches),
        'blacklist_terms': sorted({t['term'] for t in traps} | {c['brand'] for c in compat}),
    }
    model['summary_card'] = summary_rows(model)
    model['final_status'] = ('red' if any(row['light'] == 'red' for row in model['summary_card']) else
                             'clear' if all(row['light'] == 'green' for row in model['summary_card']) else 'pending')
    return model


def summary_rows(model):
    searches = {row['id']: row for row in model['searches']}
    result = []
    for kind, search_id, label in [('trademark', 'uspto_trademark', '商标'), ('design', 'design_patent', '外观'),
                                  ('utility', 'utility_patent', '发明'), ('image', 'image_reverse', '图片权属')]:
        search = searches.get(search_id) or {}
        source = search.get('evidence_urls') or search.get('record_urls') or search.get('screenshots') or []
        complete = search.get('status') == 'searched' and not search.get('problem') and bool(search.get('date')) and bool(source)
        assessment = search.get('assessment')
        light = 'red' if complete and assessment in ('collision', 'high_risk', 'red') else (
            'green' if complete and assessment in ('clear', 'isolated', 'original_owned') else 'yellow')
        result.append({'id': kind, 'label': label, 'light': light, 'conclusion': search.get('result') or '缺真实检索记录，待核',
            'needs_user': '' if light == 'green' else search.get('reason') or ('换款 / 接受风险 / 放弃' if light == 'red' else '补检索记录或人工逐项核对'),
            'source': source, 'searched_at': search.get('date')})
    severe = [row for row in model['items'] if row['light'] in ('red', 'orange')]
    pending = model['unknown_attributes'] or model.get('input_conflicts')
    result.append({'id': 'compliance', 'label': '合规声明', 'light': 'red' if any(row['light'] == 'red' for row in severe) else
        'yellow' if pending or severe else 'green', 'conclusion': '；'.join(row['title'] for row in severe) or
        ('产品属性/声明尚待核对' if pending else '已知产品属性未命中禁限规则'),
        'needs_user': '提供本品资质、授权及实际声明依据' if pending or severe else '',
        'source': [{'attribute': key, 'evidence': value['evidence']} for key, value in model['attributes'].items() if value['evidence']]})
    return result


def render_summary_card(model):
    return '\n'.join(row['label'] + ' ' + LIGHT_ICON[row['light']] + ' ' + row['conclusion'] +
        ('；需要你做：' + row['needs_user'] if row['needs_user'] else '') for row in model['summary_card'])


def summary_card_from_state(model):
    """Normalize a historical final model for display without changing evidence."""
    source = {**model, 'searches':model.get('searches') or [], 'items':model.get('items') or [],
        'unknown_attributes':model.get('unknown_attributes', ['historical_attributes_not_verified']),
        'attributes':model.get('attributes') or {}, 'input_conflicts':model.get('input_conflicts') or []}
    return summary_rows(source)


def quick_screen(doc):
    collision = doc.get('obvious_collision') or {}
    blocked = collision.get('status') == 'confirmed' and bool(collision.get('source')) and bool(collision.get('reason'))
    required = {'uspto_trademark', 'design_patent'}
    searches = [row for row in doc.get('searches', []) if row.get('id') in required]
    complete = required == {row.get('id') for row in searches} and all(
        row.get('status') == 'searched' and row.get('date') and row.get('result') and
        (row.get('evidence_urls') or row.get('screenshots')) for row in searches)
    return {'status': 'blocked' if blocked else 'pass' if complete else 'pending',
        'light': 'red' if blocked else 'green' if complete and all(row.get('assessment') == 'clear' for row in searches) else 'yellow',
        'collision': collision if blocked else None, 'searches': searches, 'timebox_minutes': 5,
        'needs_user': '换款 / 放弃' if blocked else '' if complete else '先补对标品牌商标与产品名+design patent真实快筛'}


QUICK = OUT / '04_ip_quick_screen.yaml'
QUICK_TEMPLATE = {
    'how': '第 4 步 IP 快筛（≤5 分钟）：两项都真实检索后，把 status 改成 searched，填 date（YYYY-MM-DD）、result（检索结论原话）、'
           'evidence_urls 或 screenshots（至少一项）、assessment（clear / similar / conflict）。明显撞车时填 obvious_collision。',
    'searches': [
        {'id': 'uspto_trademark', 'title': '对标品牌商标 + 核心词商标', 'queries': [], 'status': 'pending', 'date': None,
         'result': None, 'evidence_urls': [], 'screenshots': [], 'assessment': None},
        {'id': 'design_patent', 'title': '产品名 + design patent 外观专利', 'queries': [], 'status': 'pending', 'date': None,
         'result': None, 'evidence_urls': [], 'screenshots': [], 'assessment': None}],
    'obvious_collision': {'status': 'none', 'source': None, 'reason': None}}


def save_quick_screen(root, doc):
    """第 4 步原生 IP 快筛（R06.1）：按 quick_screen 规则判定并写 ip_quick_yaml。
    3.0.23 自动实跑：插件没有任何命令生成这个文件，助手每次读源码手拼，第 4 步因此翻源码 31 次。"""
    result = quick_screen(doc)
    path = Path(root) / QUICK
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(result, allow_unicode=True, sort_keys=False), encoding='utf-8')
    return {'ok': True, 'ip_quick_yaml': QUICK.as_posix(), 'status': result['status'], 'light': result['light'],
            'needs_user': result['needs_user']}


def self_check_card(entry, required):
    """A fallback plan is not search evidence. Return a portable, reviewable receipt schema."""
    steps = {
        'uspto_trademark': ['逐个输入核心词、卖点短语和拟用品牌名；分别查精确词与近似拼写。',
                            '查看相关商品/服务与有效记录，打开详情并保存申请/注册号；零结果也保留查询与筛选截图。'],
        'design_patent': ['输入产品名称及结构同义词，筛选美国外观专利。',
                          '打开相似轮廓/分区的附图，记录 D 专利号、权利人和法律状态，再逐图比较。'],
        'utility_patent': ['输入产品名称与分隔、折叠、扣合等实际功能词，筛选美国记录。',
                           '打开相关权利要求，记录专利/公开号、权利人、日期和状态；不能只看标题。'],
        'image_reverse': ['选定 SKU 后上传该 SKU 实拍正面、背面和结构图，勿用供应商多款拼图。',
                          '逐一打开相似结果，记录品牌、原始页面及相似部位；无实拍时保持待你查。'],
        'schedule_a': ['用核心词、品牌名分别组合 "Schedule A" lawsuit 检索。',
                       '打开法院或诉讼文件，记录案号、法院、日期及涉及权利；无结果注明检索覆盖限制。'],
    }
    return {'title': entry.get('title') or required['title'], 'status': '待你查',
            'links': entry.get('links') or [], 'queries': entry.get('queries') or [],
            'steps': steps.get(entry['id'], ['输入查询词，保存查询结果与详情。']),
            'screenshot_return_schema': {'search_id': entry['id'], 'searched_at': 'YYYY-MM-DD',
                'query': '', 'filters': '', 'result_count': None, 'record_ids': [], 'result_urls': [],
                'screenshots': ['查询词和筛选条件截图', '结果列表截图（含零结果）', '相关记录详情截图'],
                'selected_sku': '', 'assessment': '', 'coverage_limits': ''}}


def render(m):
    icon = LIGHT_ICON
    head = f'{icon[m["overall_light"]]} 最高灯：{LIGHT_WORD[m["overall_light"]]}'
    counts = f'🔴 {m["counts"]["red"]} · 🟠 {m["counts"]["orange"]} · 🟡 {m["counts"]["yellow"]}'
    done = sum(1 for s in m['searches'] if s['status'] == 'searched')
    lines = [f'# 合规尺子{" · " + m["product"] if m["product"] else ""}', '',
             f'> 生成：`{GENERATOR}` v{VERSION}｜规则库 {m["rules_as_of"]}｜站点 {m["marketplace"]}',
             f'> **{head}**｜{counts}｜必做检索 {done}/{len(m["searches"])} 已查'
             + ('' if m['searches_complete'] else '（有未完成项）'),
             '> 🔴 硬门槛=禁止或需特殊许可，常规卖家基本不能做；🟠 要资质=能做但要检测/证书/审批，费用进成本账；'
             '🟡 要规避=改词、改宣传、改设计或补申报；⚪ 待核=缺依据，不参与最高风险灯。', '> 初筛，不是法律意见；**做不做由你拍板**。', '',
             '## 最终五行结论卡', '', render_summary_card(m), '', '## 一、尺子总览', '']
    if m['items']:
        lines += ['| 灯 | 范围 | 项目 | 为什么命中 | 需要什么 | 怎么处理 |', '|---|---|---|---|---|---|']
        for i in m['items']:
            lines.append(f'| {icon[i["light"]]} {LIGHT_WORD[i["light"]]} | {i["scope"]} | {i["title"]} | '
                         f'{"<br>".join(i["evidence"])} | {i["requires"]} | {i["action"]} |')
    else:
        lines.append('规则库未命中出口禁限、资质或高侵权品类。仍需完成第三节检索。')
    lines += ['', '## 二、商标陷阱与他牌名（全部进 04_compliance_blacklist.yaml）', '']
    if m['trademark_traps']:
        lines += ['| 看着像通用词的商标 | 权利人 | 改用 | 出现在 |', '|---|---|---|---|']
        lines += [f'| {t["term"]} | {t["owner"]} | {t["generic"]} | {t["seen_in"]} |' for t in m['trademark_traps']]
    else:
        lines.append('产品文本里未发现规则库中的通用词陷阱（规则库不全，USPTO 检索仍要做）。')
    if m['compatibility_brands']:
        lines += ['', '他牌名（只能以 “compatible with / for 型号” 写在标题或五点的适配说明，不进 ST、不作广告通投词、不用对方 logo，'
                  '并加 “not affiliated with”）：' + '、'.join(c['brand'] for c in m['compatibility_brands'])]
    lines += ['', '## 三、必做检索', '', '| 检索 | 状态 | 查询词 | 结果 / 原因 | 入口 |', '|---|---|---|---|---|']
    status_word = {'searched': '✅ 已查', 'user_todo': '📝 待你查', 'not_searched': '❌ 未查'}
    for s in m['searches']:
        note = s.get('result') if s['status'] == 'searched' else s.get('reason') if s['status'] == 'user_todo' else ''
        note = (note or '') + (f'（{s["problem"]}）' if s['problem'] and s['problem'] != '未检索' else '')
        links = ' '.join(f'[打开]({u})' for u in (s.get('links') or [])[:3])
        lines.append(f'| {s.get("title", s.get("id"))} | {status_word.get(s["status"], s["status"])} | '
                     f'{"、".join(s.get("queries") or []) or "—"} | {note or "—"} | {links} |')
    if m['missing_searches']:
        lines.append(f'\n缺检索项：{"、".join(m["missing_searches"])}')
    for s in m['searches']:
        card = s.get('self_check_card')
        if not card:
            continue
        lines += ['', f'### 待你查：{card["title"]}', '',
                  '入口：' + '；'.join(f'[打开检索]({u})' for u in card['links']),
                  '输入词：' + '；'.join(card['queries'])]
        lines += [f'{n}. {step}' for n, step in enumerate(card['steps'], 1)]
        lines += ['截图回传格式（连同截图文件一起回传，结果未经核验不记已查）：见文末「来源与技术核对附录」。', '', '```yaml',
                  yaml.safe_dump(card['screenshot_return_schema'], allow_unicode=True, sort_keys=False).strip(), '```']
    lines += ['', '接口说明：USPTO TSDR、ODP 和 PatentsView 的程序接口需要认证；未配置认证或未取得结果时使用上方自查卡，不能记作已查。'
              'TSDR 用于已知申请/注册号的状态与文档，不能代替商标关键词排查。'
              '来源：[TSDR](https://www.uspto.gov/trademarks/apply/check-status-view-documents/trademark-bulk-data)、'
              '[ODP](https://data.uspto.gov/apis/bulk-data/search)、'
              '[PatentsView](https://github.com/PatentsView/PatentSearch-API/blob/main/docs/docs/Search%20API/SearchAPIReference.md)。']
    lines += ['', '## 四、产品属性判定', '', '| 属性 | 判定 | 依据 |', '|---|---|---|']
    word = {True: '是', False: '否', None: '⚪ 待核'}
    # 3.0.25: a dozen "否" rows repeated the same long evidence; rows sharing value and evidence become one row.
    groups = {}
    for a in m['attributes'].values():
        groups.setdefault((a['value'], a['evidence'] or '—'), []).append(a['label'])
    for (value, evidence), labels in groups.items():
        lines.append(f'| {"、".join(labels)} | {word[value]} | {evidence} |')
    if m['pending_confirmations']:
        lines += ['', '### 待厂家确认', '', '以下属性缺少本品依据；确认前均为灰灯，不计入最高风险灯：']
        lines += [f'- {a["label"]}：{a["evidence"] or "请提供选定 SKU 的 BOM、结构照片或检测依据。"}'
                  for a in m['pending_confirmations']]
    if m['input_conflicts']:
        lines += ['', '输入中的布尔判定与未确认依据冲突，已转为待核并保留原始依据；未据此认定本品含危险材料或侵权图案。']
    if m['reminders']:
        lines += ['', '## 五、宣传方向提醒（属性已判否，但文本里有相关词）', '']
        lines += [f'- {r["title"]}：{r["evidence"][0] if r["evidence"] else ""}。{r["action"]}' for r in m['reminders']]
    lines += ['', '## 六、M14 摘要（可直接放进 S1 报告）', '',
              f'> 合规尺子：{head}（{counts}）；必做检索 {done}/{len(m["searches"])} 已查。'
              + ('硬门槛：' + '、'.join(i['title'] for i in m['items'] if i['light'] == 'red') + '。' if m['counts']['red'] else '')
              + ('要资质：' + '、'.join(i['title'] for i in m['items'] if i['light'] == 'orange') + '。' if m['counts']['orange'] else '')
              + (f'商标陷阱：{"、".join(t["term"] for t in m["trademark_traps"])}。' if m['trademark_traps'] else '')
              + (f'他牌名仅限适配说明：{"、".join(c["brand"] for c in m["compatibility_brands"])}。'
                 if m['compatibility_brands'] else ''), '']
    return '\n'.join(lines)


def run(root, init=False, force=False):
    delivery_scripts = Path(__file__).resolve().parents[3] / 'scripts'
    previous_paths = list(sys.path)
    try:
        sys.path.insert(0, str(delivery_scripts))
        from r01_native import execute_native
        from s1_readability import reader_projection
        compact = execute_native(root, lambda view: run(view, init, force), step=8,
                                 report_paths=[(OUT / MD).as_posix()])
    finally:
        sys.path[:] = previous_paths
    if compact is not NotImplemented:
        return compact
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f'产品根不存在：{root}')
    rules = load_yaml(RULES)
    out = root / OUT
    inputs = out / INPUTS
    if init:
        if inputs.exists() and not force:
            return {'ok': True, 'action': 'kept_existing', 'inputs': str(inputs)}
        out.mkdir(parents=True, exist_ok=True)
        inputs.write_text(yaml.safe_dump(skeleton(root, rules), allow_unicode=True, sort_keys=False), encoding='utf-8')
        return {'ok': True, 'action': 'initialized', 'inputs': str(inputs)}
    if not inputs.is_file():
        raise ValueError(f'缺 {INPUTS}：先用 --init 生成')
    raw = inputs.read_bytes()
    doc = yaml.safe_load(raw.decode('utf-8-sig')) or {}
    # Migrate legacy broad scans in memory. Never rewrite the user's source input or decisions.
    if doc.get('generator') == GENERATOR and not doc.get('scan_scope') and any((root / p).is_file() for p in SOURCES[:2]):
        scope = scoped_product(root)
        if isinstance(doc.get('selected_product'), dict):
            scope['selected_product'] = doc['selected_product']
        doc.update(scope)
    model = evaluate(doc, rules)
    model['source_files_sha256'] = {str(p).replace('\\', '/'): hashlib.sha256((root / p).read_bytes()).hexdigest()
                                  for p in SOURCES[:2] if (root / p).is_file()}
    md = reader_projection(render(model))
    # This deterministic ruler cites its rule source. The narrative sync separately
    # verifies IP-specific knowledge cards; broad stage enrichment reintroduced
    # unrelated market/gray-method citations into this compliance report.
    (out / MD).write_text(md, encoding='utf-8', newline='\n')
    model['inputs_sha256'] = hashlib.sha256(raw).hexdigest()
    model['md_sha256'] = hashlib.sha256(md.encode('utf-8')).hexdigest()
    (out / MODEL).write_text(yaml.safe_dump(model, allow_unicode=True, sort_keys=False), encoding='utf-8')
    receipt = {'generator': GENERATOR, 'schema_version': VERSION,
               'model_sha256': hashlib.sha256((out / MODEL).read_bytes()).hexdigest(),
               'md_sha256': model['md_sha256'], 'inputs_sha256': model['inputs_sha256'],
               'source_files_sha256': model['source_files_sha256']}
    (out / '04A_compliance_receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    # R09: the reading edition (unified report style) is a fixed step-8 output, not an ad-hoc script.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from ip_reading import write_reading_html
        page = write_reading_html(root, model)
    except (ImportError, OSError, ValueError, KeyError, TypeError):
        page = None  # presentation only: the ruler, its Markdown and the blacklist stand without it
    return {'ok': True, 'overall_light': model['overall_light'], 'counts': model['counts'], 'md': str(out / MD),
            **({'html': str(page)} if page else {}),
            'unknown_attributes': model['unknown_attributes'], 'searches_complete': model['searches_complete'],
            'blacklist_terms': model['blacklist_terms']}


def main(argv=None):
    parser = argparse.ArgumentParser(description='合规尺子')
    parser.add_argument('--run', required=True, help='产品根')
    parser.add_argument('--init', action='store_true')
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--quick-template', type=Path, help='第 4 步：生成 IP 快筛填写模板到此文件')
    parser.add_argument('--quick-screen', type=Path, help='第 4 步：读填好的快筛文件，判定并写 ip_quick_yaml')
    args = parser.parse_args(argv)
    try:
        if args.quick_template:
            args.quick_template.write_text(yaml.safe_dump(QUICK_TEMPLATE, allow_unicode=True, sort_keys=False), encoding='utf-8')
            print(json.dumps({'ok': True, 'template': str(args.quick_template)}, ensure_ascii=False))
            return 0
        if args.quick_screen:
            doc = yaml.safe_load(args.quick_screen.read_text(encoding='utf-8-sig')) or {}
            print(json.dumps(save_quick_screen(args.run, doc), ensure_ascii=False, indent=2))
            return 0
        result = run(args.run, args.init, args.force)
    except ValueError as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
