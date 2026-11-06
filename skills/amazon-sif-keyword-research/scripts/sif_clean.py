#!/usr/bin/env python3
"""SIF 导出 → Keyword Package 2.0 的确定性清洗。

同一输入永远得到同一词包：标准化、合并、IP/品牌/声明隔离、本品匹配、D1–D6、分池、守恒和分析
全部按 S1 产物推导。需要人判断的词写进 review_queue.json，在 review_decisions.json 里显式裁决后重跑。

MCP 补数分三步：--plan-mcp 生成 mcp_plan.json → 模型按清单调用 SIF 工具，把每次原始返回存成
mcp/calls/<call_id>.json → 重跑本脚本，返回按独立 source_id 合并进词包与报告上下文。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import statistics
import subprocess
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import openpyxl
import yaml

HERE = Path(__file__).resolve()
CONTRACT = HERE.parents[2] / '_keyword_system_contract' / 'scripts' / 'keyword_package_contract.py'
PRODUCT_CT = HERE.parents[2] / '1688-product-detail-scraper' / 'scripts' / 'product_ct.py'
BRIDGE = HERE.parents[3] / 'scripts' / 'starsky_mcp.py'

PROFILE = 'S1_选品板块/amazon-market-research/01_product_profile.yaml'
FACTS = 'S1_选品板块/1688-product-detail-scraper/03_product_facts.yaml'
PRODUCT_CT_FILE = 'S1_选品板块/1688-product-detail-scraper/03_product_ct.yaml'
BLACKLIST = 'S1_选品板块/amazon-ip-risk-assessment/04_compliance_blacklist.yaml'
POOL = 'S1_选品板块/amazon-market-research/competitor_asin_pool.yaml'
MANIFEST = 'S2_文案板块/_投喂_关键词导出/keyword_export_manifest.yaml'
OUT = 'S2_文案板块/amazon-sif-keyword-research'

FIELD_MAP = {'关键词': 'keyword', '中文翻译': 'cn_translation', '相关性': 'sif_relevance', '相关性得分': 'relevance_score',
             '周搜索量': 'weekly_search_volume', 'ABA排名': 'aba_rank', '24小时归因的点击转化率': 'cvr_24h',
             '建议竞价(低)': 'cpc_low', '建议竞价(中)': 'cpc_mid', '建议竞价(高)': 'cpc_high',
             'Top3点击集中度': 'top3_click_share', 'Top3转化集中度': 'top3_conversion_share', 'Top4占位率': 'top4_share',
             'Top8占位率': 'top8_share', 'Top16占位率': 'top16_share', 'Top32占位率': 'top32_share', 'Top48占位率': 'top48_share'}
METRICS = {'weekly_search_volume': ('sif_weekly_search_volume', 'searches/week', int),
           'aba_rank': ('sif_aba_rank_lower_is_better', 'rank', int),
           'cvr_24h': ('sif_24h_click_conversion_third_party_not_amazon_account_cvr', 'ratio_0_1', float),
           'cpc_low': ('sif_suggested_bid_low_market_signal', 'USD', float),
           'cpc_mid': ('sif_suggested_bid_mid_market_signal', 'USD', float),
           'cpc_high': ('sif_suggested_bid_high_market_signal', 'USD', float),
           'relevance_score': ('sif_platform_relevance_score_not_product_fact', 'score', float),
           'top3_click_share': ('sif_top3_click_concentration', 'ratio_0_1', float),
           'top3_conversion_share': ('sif_top3_conversion_concentration_not_cvr', 'ratio_0_1', float),
           **{f'top{n}_share': (f'sif_top{n}_placement_share', 'ratio_0_1', float) for n in (4, 8, 16, 32, 48)}}
CONFIRMED = {'verified', 'user_confirmed', 'page_extracted', 'image_extracted', 'supplier_confirmed', 'sample_measured'}
QUOTES = '"“”‘’`´«»「」『』'
FUNCTION_WORDS = {'fit', 'fits', 'for', 'with', 'compatible', 'holds', 'hold'}
STOPWORDS = {'for', 'and', 'the', 'with', 'of', 'to', 'in', 'a', 'an', 'on', 'by', 'or', 'set', 'pack', 'piece', 'pc', 'pcs'}
QUESTION_WORDS = {'how', 'what', 'which', 'can', 'does', 'do', 'will', 'why', 'best'}
COMPAT_TOKENS = {'fit', 'compatible', 'size', 'pt', 'mm', 'inch'}
# Pool caps and the P1 percentile are case defaults: they size the review views, never gate a word out of records.
CAPS = {'title_candidates': 12, 'highlights_candidates': 20, 'bullet_feature': 60, 'bullet_scenario': 60,
        'description_aplus': 80, 'search_terms_candidates': 120, 'attributes': 40, 'qa': 30, 'fitment_review': 40,
        'phrase_feature': 80, 'phrase_scenario': 80, 'broad_discovery': 20}
P1_PERCENTILE = 0.8
REASONS = {'ip_blocked': 'IP/平台禁用词（S1 黑名单）', 'competitor_brand': '竞品/第三方品牌，仅可人工审核后定投',
           'excluded_root': 'S1 排除词根：不同品类形态', 'sif_irrelevant_sheet': 'SIF 判几乎不相关',
           'sif_irrelevant_but_core': 'SIF 判几乎不相关但含本品核心词根，待复核', 'claim_restricted': '含未证实声明，证据到位前不进文案/广告',
           'fact_pending': '依赖的本品事实尚未确认', 'direct_match': '含本品核心词根或对象+形态词',
           'adjacent': '只含部分本品词根，相关性待复核', 'sif_high_no_root': 'SIF 判高相关但不含本品词根，待复核',
           'no_product_root': '不含本品词根且 SIF 相关度中/低', 'review_decision': '人工/模型裁决',
           'excluded_root_with_core': '同时含本品核心词根与 S1 排除词根，待复核',
           'direct_but_sif_low': '含本品词根但 SIF 判低相关（可能是别的品类/品牌），待复核',
           'unexplained_modifier': '含本品词根但带画像解释不了的修饰词（可能是别的材质/品牌/款式），待复核',
           'attribute_conflict': '与 S1 产品 CT 冲突：本品不是这种材质/形态/功能',
           'attribute_conflict_inferred': '与 S1 产品 CT 冲突，但 CT 这一项只是推断，待复核',
           'direct_match_fact_unverified': '含本品词根；依赖的本品事实未经实物/厂家确认，已写入并标待核实',
           'direct_unverified_modifier': '含本品词根且 SIF 判高/中相关；修饰词未在画像中，已写入并标待核实',
           'compliance_review': '命中平台合规触发词，需认证或人工确认'}
CT_STATUS = {'page_extracted': 'page_extracted', 'image_extracted': 'image_extracted', 'supplier_confirmed': 'supplier_confirmed', 'sample_measured': 'sample_measured',
             'user_confirmed': 'user_confirmed', 'user_attested': 'user_confirmed'}
# Function words that never change what product a search means.
NEUTRAL = {'for', 'with', 'and', 'or', 'of', 'the', 'a', 'an', 'to', 'in', 'on', 'my', 'your', '&', 'set'}
GIFT_WORDS = {'gift', 'birthday', 'christmas', 'father', 'dad', 'men', 'man', 'him', 'husband'}
TOOL_FORMS = {'roll', 'pouch', 'organizer', 'wrap', 'carrier', 'storage', 'bag'}
REASONS.update(semantic_candidate='场景/人群/礼品候选，按本品用途复核', missing_relevance='相关度缺失，保留待复核',
               illustrated_use='页面/图片展示用途，样品适配待验证', performance_pending='卖家性能宣传待测试，不作否定依据')


def shared(name):
    path = HERE.parents[3] / 'scripts' / (name + '.py')
    spec = importlib.util.spec_from_file_location('sif_' + name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module



def eligibility_module():
    path = CONTRACT.parent / 'keyword_eligibility.py'
    spec = importlib.util.spec_from_file_location('sif_shared_eligibility', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def qualified_use_terms(keyword, terms):
    return eligibility_module().qualified_use_terms(keyword, terms)


def translate_keyword(keyword, original):
    corrections = {'roll tool bag': '卷式工具包', 'tool wrap': '工具卷包', 'tool carrier': '工具携行包'}
    return corrections.get(keyword.casefold(), original)


# ---------- text ----------

def clean_keyword(raw):
    s = unicodedata.normalize('NFKC', str(raw))
    s = re.sub(r"(?<=[A-Za-z0-9])['’](?=[A-Za-z])", '\u0001', s)
    s = re.sub('[' + re.escape(QUOTES) + "']", ' ', s).replace('\u0001', "'")
    s = re.sub(r'[‐-―]', '-', s)
    return re.sub(r'\s+', ' ', s).strip(' .,;:!?·-_/\\|')


def singular(word):
    word = word.strip('+')
    if word in {'christmas', 'pliers', 'scissors'}:
        return word
    if word.endswith("'s"):
        word = word[:-2]
    if len(word) > 4 and word.endswith('ies'):
        return word[:-3] + 'y'
    if len(word) > 4 and word.endswith('es') and word[:-2].endswith(('box', 'ch', 'sh', 'ss', 'x')):
        return word[:-2]
    if len(word) > 3 and word.endswith('s') and not word.endswith(('ss', 'us', 'is')):
        return word[:-1]
    return word


def toks(text):
    s = clean_keyword(text).lower().replace('-', ' ')
    return [w for w in (singular(t) for t in re.findall(r"[a-z0-9#+]+(?:'[a-z]+)?", s)) if w]


def has_phrase(tokens, phrase):
    n = len(phrase)
    if n > 1 and ''.join(phrase) in tokens:  # 'vault x' ~ 'vaultx'
        return True
    return n > 0 and any(tokens[i:i + n] == phrase for i in range(len(tokens) - n + 1))


def merge_compounds(tokens, singles):
    """'top loader' → 'toploader' when the joined form is a lexicon word."""
    out, i = [], 0
    while i < len(tokens):
        if i + 1 < len(tokens) and tokens[i] + tokens[i + 1] in singles:
            out.append(tokens[i] + tokens[i + 1])
            i += 2
        else:
            out.append(tokens[i])
            i += 1
    return out


def short_id(prefix, text):
    return prefix + hashlib.sha1(text.encode('utf-8')).hexdigest()[:10]


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_ad_index(target, package):
    """S4 只读这份小索引（进池词 + 否定词 + Listing 位置），不再翻上百 MB 的全量包。"""
    spec = importlib.util.spec_from_file_location('keyword_index_shared', CONTRACT.parent / 'keyword_index.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ensure_index(target, package=package)
    return module.INDEX_JSON


def pool_path(root, manifest):
    """The confirmed competitor pool step 2 bound in the export manifest, else the default S1 path, else the S2 copy."""
    ref = (manifest or {}).get('s2_competitor_gate') or {}
    for rel in [ref.get('path'), POOL, 'S2_文案板块/starsky-fullchain-workflow/competitor_asin_pool.yaml']:
        if rel and (Path(root) / rel).is_file():
            return rel
    return None


def load_yaml(path):
    return yaml.safe_load(Path(path).read_text(encoding='utf-8-sig')) or {}


# ---------- S1 inputs → lexicon and fact registry ----------

def claim_terms(claim):
    """'fits toploaders' → ['toploader']；'2400+ cards' → ['2400']；'#1 / best' → ['#1','best']."""
    out = []
    for part in str(claim).split('/'):
        words = toks(part)
        while words and words[0] in FUNCTION_WORDS:
            words = words[1:]
        numbers = [w for w in words if re.fullmatch(r'#?\d+', w)]
        if numbers:
            words = numbers
        elif len(words) > 1 and words[-1] == 'card':
            words = words[:-1]
        if words:
            out.append(' '.join(words))
    return out


def roots_of(item):
    if isinstance(item, dict):
        return [str(r) for r in item.get('root_words') or []], str(item.get('label') or '')
    text = str(item)
    return ([text] if re.search(r'[A-Za-z]', text) else []), text


def load_product_ct(root):
    """S1 产品 CT（本品是什么 / 不是什么）；没有就返回 None，材质退回画像（旧项目）。"""
    if not PRODUCT_CT.is_file():
        return None
    spec = importlib.util.spec_from_file_location('product_ct_shared', PRODUCT_CT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.evaluate(root)
    return None if result['status'] == 'missing' else result


ADS_ONLY_ACTIONS = {'ads_only', 'allow_ads', 'ads_only_pool', 'brand_isolated', 'isolate', 'review_before_ads'}


def brand_rules(blacklist):
    """S1 brand isolation in every format S1 has written: (block_rules, ads_only_names).

    block_rules are (term, reason, channels, variants); unknown actions fail closed as block.
    ads_only_names may only be targeted after manual review, never in copy or the default ad pool."""
    value = blacklist.get('brand_terms')
    ads_only = list(blacklist.get('brand_terms_ads_only') or [])
    blocks = []
    if value is None:
        pass
    elif isinstance(value, dict):  # legacy {ads_only_pool: [...], other: [...]}
        ads_only += list(value.get('ads_only_pool') or [])
        ads_only += list(value.get('other') or [])
    elif isinstance(value, list):
        for index, rule in enumerate(value, 1):
            if isinstance(rule, str):
                ads_only.append(rule)
                continue
            if not isinstance(rule, dict):
                raise ValueError(f'S1 黑名单 brand_terms 第 {index} 条格式无法识别（{type(rule).__name__}）；'
                                 '应为品牌名或 {term, action, channels, reason}，不能当作无品牌限制')
            term = rule.get('term') or rule.get('phrase')
            if not term:
                raise ValueError(f'S1 黑名单 brand_terms 第 {index} 条缺 term；不能当作无品牌限制')
            action = str(rule.get('action') or 'block').strip().lower()
            if action in ADS_ONLY_ACTIONS:
                ads_only.append(rule)
            else:
                reason = rule.get('reason') or '竞品/第三方品牌隔离（S1 黑名单）'
                if not action.startswith('block'):
                    reason += f'；未知动作 {action} 按 block 处理'
                blocks.append((str(term), reason, list(rule.get('channels') or []), list(rule.get('variants') or [])))
    else:
        raise ValueError(f'S1 黑名单 brand_terms 格式无法识别（{type(value).__name__}）；'
                         '应为规则列表或 {ads_only_pool: [...]}，不能当作无品牌限制')
    names = []
    for item in ads_only:
        name = (item.get('term') or item.get('phrase')) if isinstance(item, dict) else item
        if not name:
            raise ValueError('S1 黑名单仅广告品牌池里有一条缺 term；不能当作无品牌限制')
        names.append(str(name))
    return blocks, names


def core_root_entries(profile, ct=None):
    """Accept current identity fields without inventing confirmed legacy facts."""
    legacy = profile.get('core_roots') or []
    legacy = [legacy] if isinstance(legacy, str) else legacy
    if legacy:
        out, seen = [], set()
        for root in legacy:
            if not isinstance(root, str) or not root.strip(): continue
            words = root.split()
            # A long root also matches through its 2+ word prefixes (window screen repair kit -> window screen).
            parts = [root] + [' '.join(words[:j]) for j in range(2, len(words))]
            for part in parts:
                key = ' '.join(part.casefold().split())
                if key not in seen:
                    seen.add(key); out.append((part, PROFILE + '#core_roots', None, 'profile.core_roots:'))
        return out
    entries, seen = [], set()
    identity = ((ct or {}).get('ct') or {}).get('identity') or {}
    candidates = [(profile.get('core_keyword'), PROFILE + '#core_keyword',
                   profile.get('core_keyword_status') or 'derived', 'profile.core_keyword:'),
                  (identity.get('type_en'), PRODUCT_CT_FILE + '#identity.type_en',
                   identity.get('source_status') or 'derived', 'ct.identity:')]
    for value, source, status, prefix in candidates:
        values = value if isinstance(value, list) else [value]
        for root in values:
            if not isinstance(root, str) or not root.strip(): continue
            key = ' '.join(root.casefold().split())
            if key not in seen:
                entries.append((root, source, status, prefix)); seen.add(key)
    return entries


def build_lexicon(profile, blacklist, overrides, ct=None):
    claims = [{'claim': c.get('claim'), 'terms': claim_terms(c.get('claim')), 'reason': c.get('reason'),
               'status': c.get('status'), 'channels': c.get('channels') or []} for c in blacklist.get('restricted_claims') or []]
    claim_phrases = {t: c['claim'] for c in claims for t in c['terms']}
    facts, dims = [], {f'D{i}': [] for i in range(1, 7)}

    def add_fact(fid, text, source, roots=(), kind='identity'):
        # 'top loader' and 'toploader' are the same claim: compare joined forms too.
        hits = sorted({claim_phrases[t] for r in roots for t in claim_phrases
                       if has_phrase(toks(r), toks(t)) or ''.join(toks(t)) in ''.join(toks(r))})
        facts.append({'id': fid, 'text': text, 'source': source, 'kind': kind, 'roots': list(roots),
                      'status': 'pending' if hits else 'user_confirmed',
                      'status_reason': ('S1 声明限制：' + '、'.join(hits) + '，证据到位前不作本品事实') if hits
                      else 'S1 用户选定款的产品画像'})
        return fid

    add_fact('profile.product_type', '产品形态：' + str(profile.get('product_type') or profile.get('product_type_en') or ''),
             PROFILE + '#product_type')
    core = []
    for root, source, status, prefix in core_root_entries(profile, ct):
        fid = add_fact(prefix + root, '核心词根：' + root, source, [root], 'core')
        if status is not None and facts[-1]['status'] != 'pending':
            facts[-1].update(status=status, status_reason='原身份字段兼容；保留来源等级，不推断性能已验证')
            if status in {'derived', 'ai_inferred', 'missing', 'unknown'}:
                # A lexical identity anchor is not a consumer claim dependency.
                # Actual permission still needs source-bound own-product facts.
                facts[-1]['matching_only'] = True
        core.append((root, toks(root), fid))
    objects = []
    for obj in profile.get('target_objects') or []:
        fid = add_fact('profile.target_objects:' + str(obj), '适用对象：' + str(obj), PROFILE + '#target_objects', [str(obj)], 'object')
        objects.append((toks(obj), fid))
        dims['D5'].append((str(obj), fid))
    groups = [('materials', 'D1', '材质/结构'), ('selling_points', 'D2', '功能/卖点'), ('scenarios', 'D3', '场景'), ('personas', 'D4', '人群')]
    conflicts, soft_kinds = [], set()
    if ct:
        # 画像的 materials 是市场材质；选款后本品材质只认 CT，冲突词另列。
        groups = groups[1:]
        field = ct.get('field_status') or {}
        for kind, dim, label, status_key in (('material', 'D1', '材质', 'material'), ('mount', 'D2', '安装/摆放', 'mount'),
                                             ('features', 'D2', '结构/功能', None)):
            for r in ct['ours'].get(kind) or []:
                fid = add_fact(f'ct.{kind}#{r}', f'{label}：{r}（S1 产品 CT）', PRODUCT_CT_FILE + '#' + kind, [r], kind)
                status = CT_STATUS.get(field.get(status_key) if status_key else 'user_confirmed', 'pending')
                if facts[-1]['status'] == 'user_confirmed':
                    facts[-1].update(status=status, status_reason='S1 产品 CT 来源：' + str(field.get(status_key) or 'user_confirmed'))
                dims[dim].append((r, fid))
        for kind, words in (ct.get('not_this') or {}).items():
            conflicts += [(toks(w), w, kind) for w in words if toks(w)]
        soft_kinds = {kind for kind, key in (('materials', 'material'), ('forms', 'mount'))
                      if (ct.get('field_status') or {}).get(key) not in CT_STATUS}
        for kind, dim in [('uses', 'D5'), ('audiences', 'D4'), ('scenarios', 'D3'), ('pain_points', 'D2')]:
            node = (ct.get('ct') or {}).get(kind) or {}
            for term in node.get('en_terms') or []:
                fid = 'CT:'+kind
                if not any(f['id'] == fid for f in facts):
                    facts.append({'id': fid, 'text': str(node.get('value')), 'source': node.get('source') or PRODUCT_CT_FILE+'#'+kind,
                                  'kind': kind, 'roots': list(node.get('en_terms') or []),
                                  'status': node.get('source_status') or 'missing', 'status_reason': 'CT原来源状态'})
                dims[dim].append((term, fid))
    for key, dim, label in groups:
        for item in profile.get(key) or []:
            roots, name = roots_of(item)
            if not roots:
                add_fact(f'profile.{key}:{name}', f'{label}：{name}（无英文词根，不参与匹配）', f'{PROFILE}#{key}', (), key)
            # One fact per root: a restricted root ('2400') must not make its siblings ('bulk') pending.
            for r in roots:
                fid = add_fact(f'profile.{key}:{name}#{r}', f'{label}：{name}（{r}）', f'{PROFILE}#{key}', [r], key)
                dims[dim].append((r, fid))
    for stage, spec in (profile.get('intent_signals') or {}).items():
        for pattern in (spec or {}).get('patterns') or []:
            dims['D6'].append((str(pattern), 'intent:' + stage))
    for dim, words in (overrides.get('lexicon') or {}).items():
        fid = add_fact('review.lexicon:' + dim, f'{dim} 补充词根（审阅裁决）：' + '、'.join(words), 'review_decisions.json#lexicon', words, 'lexicon')
        dims[dim] += [(w, fid) for w in words]
    object_heads = {t[-1] for t, _ in objects if t}
    object_tokens = {w for t, _ in objects for w in t}
    form_nouns = {t[-1] for _, t, _ in core if t} | {w for _, t, _ in core for w in t if w not in object_tokens}
    blocked = []
    for item in blacklist.get('blocked_terms') or []:
        for term in [item.get('term'), *(item.get('variants') or [])]:
            if term:
                blocked.append((toks(term), str(item.get('term')), item.get('reason') or '', item.get('channels') or []))
    brand_blocks, ads_only = brand_rules(blacklist)
    for term, reason, channels, variants in brand_blocks:
        for variant in [term, *variants]:
            if toks(variant):
                blocked.append((toks(variant), term, reason, channels))
    brands = []
    for name in list(ads_only) + list(profile.get('brand_risks') or []):
        t = toks(name)
        if t and t not in [b[0] for b in brands] and not any(has_phrase(t, b[0]) or has_phrase(b[0], t) for b in blocked):
            brands.append((t, str(name)))
    excluded = [(toks(r), str(r)) for r in profile.get('excluded_roots') or [] if toks(r)]
    dim_phrases = {d: [(toks(w), w, fid) for w, fid in words if toks(w)] for d, words in dims.items()}
    claim_list = [(toks(t), t, c) for t, c in claim_phrases.items()]
    phrases = ([t for _, t, _ in core] + [t for t, _ in objects] + [b[0] for b in blocked] + [b[0] for b in brands]
               + [t for t, _ in excluded] + [t for d in dim_phrases.values() for t, _, _ in d] + [t for t, _, _ in claim_list])
    # Every word the S1 profile can vouch for; anything else in a direct match is an unverified modifier.
    known = ({w for _, t, _ in core for w in t} | object_tokens | form_nouns
             | {w for d in dim_phrases.values() for t, _, _ in d for w in t} | NEUTRAL)
    known |= {''.join(t) for d in dim_phrases.values() for t, _, _ in d if len(t) > 1}
    if ct:
        known |= set(ct.get('our_words') or [])
    phrases += [t for t, _, _ in conflicts]
    tool_category = any('tool' in t and set(t) & {'roll', 'bag', 'pouch', 'organizer', 'wrap', 'carrier'} for _, t, _ in core)
    if tool_category:
        form_nouns |= TOOL_FORMS
        known |= TOOL_FORMS | {'tool'}
    if any('tool' in t and 'roll' in t for _, t, _ in core):
        excluded += [(toks(term), term) for term in ('wheeled', 'rolling', 'backpack', 'toolbox', 'tool box')
                     if term not in [label for _, label in excluded]]
    return {'tool_category': tool_category, 'conflicts': conflicts, 'soft_conflict_kinds': soft_kinds, 'core': core, 'objects': objects, 'object_heads': object_heads, 'form_nouns': form_nouns, 'known': known,
            'dims': dim_phrases, 'blocked': blocked, 'brands': brands, 'excluded': excluded, 'claims': claims,
            'claim_phrases': claim_list, 'facts': facts, 'singles': {t[0] for t in phrases if len(t) == 1}}



def merge_fact_registry(original, additions):
    merged = {row['id']: dict(row) for row in original}
    for row in additions:
        merged.setdefault(row['id'], {}).update(row)
    return list(merged.values())

def product_fact_registry(facts_doc):
    out = []
    status_map = {'page_extracted': 'page_extracted', 'image_extracted': 'image_extracted', 'supplier_confirmed': 'supplier_confirmed', 'sample_measured': 'sample_measured',
                  'user_confirmed': 'user_confirmed'}
    for key, fact in ((facts_doc or {}).get('facts') or {}).items():
        if not isinstance(fact, dict):
            continue
        raw = str(fact.get('source_status'))
        out.append({'id': 'facts.' + key, 'text': f'{key}：{json.dumps(fact.get("value"), ensure_ascii=False)}',
                    'source': FACTS + '#' + key, 'kind': 'product_fact', 'roots': [],
                    'status': ('pending' if re.search(r'waterproof|防水|耐磨|防刺穿', key, re.I) and raw != 'sample_measured' else status_map.get(raw, 'pending')), 'status_reason': '1688 事实来源状态：' + raw,
                    'allowed_use': fact.get('allowed_use') or []})
    return out


# ---------- SIF export ----------

def header_key(value):
    return FIELD_MAP.get(re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value or ''))))


def parse_metric(name, raw, source_id, period):
    semantic, unit, kind = METRICS[name]
    base = {'source_id': source_id, 'semantic': semantic, 'unit': unit, 'period': period, 'raw': raw}
    text = '' if raw is None else str(raw).strip()
    if text in {'', '—', '-', '--', 'N/A', 'n/a', 'null', 'None'}:
        return {**base, 'value': None, 'status': 'missing', 'missing_reason': 'source_blank'}
    try:
        value = float(text.rstrip('%').replace(',', '').replace('$', '')) / (100 if text.endswith('%') else 1)
    except ValueError:
        return {**base, 'value': None, 'status': 'invalid', 'missing_reason': 'unparseable'}
    if name == 'aba_rank' and value == 0:
        return {**base, 'value': None, 'status': 'not_observed_zero_rank', 'missing_reason': 'aba_rank_zero_not_ranked'}
    return {**base, 'value': int(round(value)) if kind is int else round(value, 6), 'status': 'provided'}


def read_exports(root, files, marketplace):
    sources, rows, per_sheet = [], [], []
    for n, rel in enumerate(files, 1):
        path = root / rel
        as_of = (re.search(r'(20\d{2}-\d{2}-\d{2})', path.name) or [None, None])[1] or \
            datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).date().isoformat()
        period = {'start': None, 'end': as_of, 'month': as_of[:7], 'window': 'sif_export_latest_week_unspecified'}
        source_id = f'SRC-SIF-FILE-{n:03d}'
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        mapped, sheets = {}, []
        try:
            for ws in wb.worksheets:
                it = ws.iter_rows(values_only=True)
                header = next(it, None) or ()
                cols = {i: header_key(h) for i, h in enumerate(header) if header_key(h)}
                mapped.update({str(header[i]).replace('\n', ''): k for i, k in cols.items()})
                count = 0
                for r, values in enumerate(it, 2):
                    count += 1
                    row = {k: (values[i] if i < len(values) else None) for i, k in cols.items()}
                    rows.append({'source_id': source_id, 'sheet': ws.title, 'row': r, 'period': period,
                                 'irrelevant_sheet': '不相关' in ws.title, **row})
                sheets.append({'sheet': ws.title, 'data_rows': count})
                per_sheet.append({'source_id': source_id, 'sheet': ws.title, 'data_rows': count})
        finally:
            wb.close()
        missing = sorted(set(FIELD_MAP.values()) - set(mapped.values()))
        sources.append({'source_id': source_id, 'provider': 'sif', 'acquisition_method': 'file', 'relative_path': rel,
                        'sha256': 'sha256:' + sha256_file(path), 'sheets': sheets, 'marketplace': marketplace,
                        'as_of': as_of, 'period': period,
                        'units': {k: METRICS[k][1] for k in METRICS}, 'field_mapping': mapped, 'missing_fields': missing})
    return sources, rows, per_sheet


# ---------- classification ----------

def classify(tokens, relevance, irrelevant_sheet, lex):
    tokens = merge_compounds(tokens, lex['singles'])
    blocked = list({label: (label, reason, ch) for t, label, reason, ch in lex['blocked'] if has_phrase(tokens, t)}.values())
    brands = list(dict.fromkeys(label for t, label in lex['brands'] if has_phrase(tokens, t)))
    claims = sorted({c for t, _, c in lex['claim_phrases'] if has_phrase(tokens, t)})
    excluded = [label for t, label in lex['excluded'] if has_phrase(tokens, t)]
    conflict_hits = [(label, kind) for t, label, kind in lex.get('conflicts', []) if has_phrase(tokens, t)]
    # Historical not_this may contain untested seller claims. Retain its source
    # note, but absence of a performance test is not evidence for an ad negative.
    performance_conflicts = [(label, kind) for label, kind in conflict_hits if kind == 'features' and
                             re.search(r'waterproof|water resistant|puncture proof|wear resistant', label, re.I)]
    conflict_hits = [hit for hit in conflict_hits if hit not in performance_conflicts]
    conflicts = sorted({label for label, _ in conflict_hits})
    core = [(r, fid) for r, t, fid in lex['core'] if has_phrase(tokens, t)]
    full_obj = [fid for t, fid in lex['objects'] if t and has_phrase(tokens, t)]
    head_obj = [fid for t, fid in lex['objects'] if t and t[-1] in tokens]
    form = sorted(lex['form_nouns'] & set(tokens))
    dims = {d: sorted({w for t, w, _ in pairs if has_phrase(tokens, t)}) for d, pairs in lex['dims'].items()}
    dim_refs = sorted({fid for d, pairs in lex['dims'].items() if d != 'D6' for t, _, fid in pairs if has_phrase(tokens, t)})
    # A bare head noun ('card') + form ('box') is ambiguous (wedding/index/gift card box); only SIF 高 corroborates it.
    generic = (bool(head_obj) and bool(form) and relevance == '高') or (
        lex.get('tool_category') and 'tool' in tokens and bool(TOOL_FORMS.intersection(tokens)))
    direct = bool(core) or (bool(full_obj) and bool(form)) or generic
    obj = full_obj or head_obj
    refs = [fid for _, fid in core] or (obj[:1] + ['profile.product_type'] if direct else [])
    refs = list(dict.fromkeys(refs + [r for r in dim_refs if r.startswith(('profile.materials', 'profile.selling_points', 'review.lexicon', 'ct.', 'CT:'))]))
    pending = [r for r in refs if lex['registry'].get(r, {}).get('status') not in CONFIRMED]
    adjacent = not direct and bool(obj or form or dim_refs)
    # 'metal trading card box' has the core root but contradicts a PU product; 'taopower ...' is an unlisted brand.
    unexplained = [t for t in tokens if t not in lex['known']]
    gift = 'gift' in tokens
    semantic = gift or bool(dims['D3'] or dims['D4'])
    performance = bool(performance_conflicts) or any(t in {'waterproof', 'waterresistant', 'punctureproof'} for t in tokens)
    if blocked:
        disp, code = 'compliance_negative', 'ip_blocked'
    elif brands:
        disp, code = 'brand_isolated', 'competitor_brand'
    elif excluded:
        wrong_form = lex.get('tool_category') and set(excluded) & {'wheeled', 'rolling', 'backpack', 'toolbox', 'tool box'}
        disp, code = ('observe', 'excluded_root_with_core') if core and not wrong_form else ('low_relevance_negative', 'excluded_root')
    elif conflicts:
        soft = all(kind in lex.get('soft_conflict_kinds', ()) for _, kind in conflict_hits)
        disp, code = ('observe', 'attribute_conflict_inferred') if soft else ('low_relevance_negative', 'attribute_conflict')
    elif performance and (direct or adjacent):
        disp, code = 'observe', 'performance_pending'
    elif direct and claims:
        disp, code = 'observe', 'claim_restricted'
    elif direct and pending:
        # Scanned/target facts are written with a verify note instead of holding the word back.
        disp, code = 'retained', 'direct_match_fact_unverified'
    elif direct and relevance == '低':
        disp, code = 'observe', 'direct_but_sif_low'
    elif direct and unexplained and relevance in {'高', '中'}:
        # SIF already rates it relevant; keep it and list the unexplained modifier for the seller to check.
        disp, code = 'retained', 'direct_unverified_modifier'
    elif direct and unexplained:
        disp, code = 'observe', 'unexplained_modifier'
    elif direct:
        disp, code = 'retained', 'direct_match'
    elif semantic:
        disp, code = 'observe', 'semantic_candidate'
    elif adjacent:
        disp, code = 'observe', 'adjacent'
    elif relevance == '高':
        disp, code = 'observe', 'sif_high_no_root'
    elif not relevance:
        disp, code = 'observe', 'missing_relevance'
    else:
        disp, code = 'low_relevance_negative', 'no_product_root'
    match = ('mismatch' if code in {'excluded_root', 'sif_irrelevant_sheet', 'attribute_conflict'} else 'direct' if direct
             else 'adjacent' if adjacent else 'unknown')
    tags = [label for d, label in (('D1', '材质'), ('D2', '功能'), ('D3', '场景'), ('D4', '人群'), ('D5', '兼容')) if dims[d]]
    if generic: tags += ['形态', '一级']
    if gift: tags.append('礼品')
    if set(tokens) & {'men', 'man', 'dad', 'father', 'him', 'husband', 'mechanic', 'electrician', 'woodworker'}:
        tags.append('人群')
    if set(tokens) & {'christmas', 'halloween', 'thanksgiving', 'valentine'}: tags.append('季节')
    if any(re.fullmatch(r'\d+', t) for t in tokens):
        tags.append('规格')
    return {'disposition': disp, 'reason_code': code, 'product_match': match, 'fact_refs': refs, 'pending_refs': pending,
            'claims': claims, 'blocked': blocked, 'brands': brands, 'excluded': excluded, 'conflicts': conflicts,
            'performance_claims': [label for label, _ in performance_conflicts],
            'core_hits': [r for r, _ in core],
            'unexplained': unexplained,
            'six_dimensions': dims, 'tags': tags,
            'primary_type': '产品词' if direct else '属性词' if (dims['D1'] or dims['D2']) else '其他',
            'review': code in {'sif_irrelevant_but_core', 'adjacent', 'sif_high_no_root', 'excluded_root_with_core', 'direct_but_sif_low',
                                   'unexplained_modifier', 'attribute_conflict_inferred'}}


def undecided_modifiers(retained, known=frozenset(), top=20):
    """留用词里 CT 还没判的修饰词（wall / shower / black…）：本品是就写进 CT，不是就进 not_this，再重跑。"""
    weight, examples = Counter(), {}
    for r in retained:
        words = toks(r['normalized_keyword'])
        # 'tooth brush' 是 toothbrush 的拆写，不算修饰词。
        split = {w for i, w in enumerate(words) for j in (i - 1, i + 1)
                 if 0 <= j < len(words) and (words[min(i, j)] + words[max(i, j)]) in known}
        for w in set(r.get('unexplained_modifiers') or []) - split:
            if len(w) > 2 and not w.isdigit():
                weight[w] += volume(r) or 0
                examples.setdefault(w, []).append((volume(r) or 0, r['keyword']))
    return [{'modifier': w, 'weekly_volume': v, 'keywords': len(examples[w]),
             'examples': [k for _, k in sorted(examples[w], reverse=True)[:4]]} for w, v in weight.most_common(top)]


def reason_text(c):
    text = REASONS[c['reason_code']]
    return text + '：' + '、'.join(c['conflicts']) if c['reason_code'].startswith('attribute_conflict') else text


def volume(rec):
    return (rec['metrics'].get('weekly_search_volume') or {}).get('value')


def apply_decisions(records, decisions, registry):
    by_key = decisions.get('keywords') or {}
    applied, overridden = [], []
    index = {r['normalized_keyword']: r for r in records} | {r['keyword_id']: r for r in records}
    for key, d in by_key.items():
        rec = index.get(key.casefold()) or index.get(key)
        if rec is None:
            raise ValueError('review_decisions 引用了不存在的词：' + key)
        target = d.get('disposition', rec['disposition'])
        refs = list(dict.fromkeys(rec['fact_refs'] + list(d.get('fact_refs') or [])))
        unknown = [r for r in refs if r not in registry]
        if unknown:
            raise ValueError(f'{key} 引用了未登记事实：{unknown}')
        if target == 'retained' and (rec['claim_restricted'] or rec['brand_hits'] or rec['compliance_hits']):
            raise ValueError(f'{key} 含声明限制或品牌/IP，不能裁决为 retained')
        # Unconfirmed facts no longer gate retention: the word is kept and listed for the seller to verify.
        pending = [r for r in refs if registry[r]['status'] not in CONFIRMED]
        if target == 'retained' and rec.get('attribute_conflicts'):
            # 旧裁决按市场材质画像做的；CT 是本品真相，这类裁决不生效，列出来给复核。
            overridden.append({'keyword_id': rec['keyword_id'], 'keyword': rec['keyword'],
                               'conflicts': rec['attribute_conflicts'], 'decision_reason': d.get('reason')})
            continue
        if not d.get('reason'):
            raise ValueError(f'{key} 的裁决缺少理由')
        if target == 'retained' and pending:
            rec['pending_fact_refs'] = list(dict.fromkeys(list(rec.get('pending_fact_refs') or []) + pending))
        rec.update({'disposition': target, 'fact_refs': refs,
                    'product_match': {**rec['product_match'], 'status': d.get('product_match', rec['product_match']['status']),
                                      'fact_refs': refs, 'reason': d['reason']},
                    'retention_reason': {'code': 'review_decision', 'text': d['reason'], 'by': d.get('by', 'model')}})
        applied.append(rec['keyword_id'])
    return applied, overridden


# ---------- analysis ----------

def concentration(recs):
    vols = sorted((v for v in (volume(r) for r in recs) if v), reverse=True)
    total = sum(vols)
    if not total:
        return {'status': 'no_comparable_volume', 'denominator_terms': 0}
    cum, k80 = 0, None
    for i, v in enumerate(vols, 1):
        cum += v
        if k80 is None and cum / total >= 0.8:
            k80 = i
    return {'status': 'computed', 'unit': 'searches/week（选定词集合，非去重买家）', 'denominator_terms': len(vols),
            'denominator_volume': total, **{f'top{n}_share': round(sum(vols[:n]) / total, 4) for n in (10, 20, 50, 100)},
            'keywords_to_80pct': k80, 'label_basis': 'case_default'}


def fill_rates(recs):
    out = {}
    for name in METRICS:
        provided = sum(1 for r in recs if (r['metrics'].get(name) or {}).get('status') == 'provided')
        out[name] = {'provided': provided, 'total': len(recs), 'rate': round(provided / len(recs), 4) if recs else None}
    return out


def pollution_roots(recs, lex):
    known = {w for _, t, _ in lex['core'] for w in t} | lex['object_heads'] | lex['form_nouns'] | STOPWORDS | {'card', 'trading'}
    counter, examples = Counter(), {}
    for r in recs:
        for w in set(toks(r['normalized_keyword'])):
            if w not in known and len(w) > 2 and not w.isdigit():
                counter[w] += 1
                examples.setdefault(w, []).append((volume(r) or 0, r['keyword']))
    return [{'root': w, 'hits': n, 'examples': [k for _, k in sorted(examples[w], reverse=True)[:5]],
             'confidence': 'observe_only', 'note': '候选污染词根，不直接批量否定'} for w, n in counter.most_common(20) if n >= 5]


def unlocks(recs, key, lex_field):
    groups = {}
    for r in recs:
        for label in r.get(key) or []:
            g = groups.setdefault(label, {'label': label, 'keywords': 0, 'weekly_volume': 0, 'examples': []})
            g['keywords'] += 1
            g['weekly_volume'] += volume(r) or 0
            g['examples'].append((volume(r) or 0, r['keyword']))
    for g in groups.values():
        g['examples'] = [k for _, k in sorted(g['examples'], reverse=True)[:6]]
    return sorted(groups.values(), key=lambda g: -g['weekly_volume'])


# ---------- pools ----------

def top(recs, n, pred=lambda r: True):
    return [r['keyword_id'] for r in sorted((r for r in recs if pred(r)), key=lambda r: -(volume(r) or -1))][:n]


def build_pools(records, p1_min):
    eligible = [r for r in records if r['disposition'] == 'retained']
    d = lambda r, *keys: any(r['six_dimensions'].get(k) for k in keys)
    tokset = lambda r: set(toks(r.get('normalized_keyword', r['keyword'])))
    listing = {
        'title_candidates': top(eligible, CAPS['title_candidates'], lambda r: volume(r) != 0 and (bool(r['core_hits']) or '一级' in r['tags'])),
        'highlights_candidates': top(eligible, CAPS['highlights_candidates'], lambda r: d(r, 'D1', 'D2') or '一级' in r['tags']),
        'bullet_feature': top(eligible, CAPS['bullet_feature'], lambda r: d(r, 'D1', 'D2')),
        'bullet_scenario': top(eligible, CAPS['bullet_scenario'], lambda r: d(r, 'D3', 'D4')),
        'description_aplus': top(eligible, CAPS['description_aplus']),
        'attributes': top(eligible, CAPS['attributes'], lambda r: d(r, 'D1') or '规格' in r['tags']),
        'qa': top(eligible, CAPS['qa'], lambda r: bool(tokset(r) & QUESTION_WORDS) or d(r, 'D6')),
        'fitment_review': top(eligible, CAPS['fitment_review'], lambda r: bool(tokset(r) & COMPAT_TOKENS) or d(r, 'D5')),
    }
    listing['search_terms_candidates'] = top(eligible, CAPS['search_terms_candidates'],
                                             lambda r: r['keyword_id'] not in set(listing['title_candidates']))
    qualified = [r['keyword_id'] for r in records if (r.get('qualified_copy') or {}).get('allowed')]
    for key in ('highlights_candidates', 'bullet_feature', 'description_aplus', 'qa'):
        listing[key] = list(dict.fromkeys(listing[key] + qualified))
    with_volume = [volume(r) for r in eligible if volume(r)]
    threshold = p1_min if p1_min is not None else (
        sorted(with_volume)[min(len(with_volume) - 1, int(len(with_volume) * P1_PERCENTILE))] if with_volume else None)
    p1 = [r['keyword_id'] for r in eligible if threshold is not None and (volume(r) or 0) >= threshold]
    p2 = [r['keyword_id'] for r in eligible if volume(r) and r['keyword_id'] not in set(p1)]
    core_short = lambda r: len(tokset(r)) <= 3 and bool(r['core_hits'])
    ads = {'exact_p1': p1, 'exact_p2': p2, 'exact': p1 + p2,
           'phrase_feature': top(eligible, CAPS['phrase_feature'], lambda r: d(r, 'D1', 'D2')),
           'phrase_scenario': top(eligible, CAPS['phrase_scenario'], lambda r: d(r, 'D3', 'D4', 'D5')),
           'broad_discovery': top(eligible, CAPS['broad_discovery'], core_short),
           'observe': [r['keyword_id'] for r in records if r['disposition'] == 'observe']}
    guard = eligibility_module()
    decisions = {r['keyword_id']: guard.evaluate_record(r, strict_product_evidence=True) for r in records
                 if r['disposition'] == 'retained' or r.get('ct_refs')
                 or any(str(ref).startswith('CT:') for ref in r.get('fact_refs', [])) or r.get('qualified_copy')}
    for pools, channel in ((listing, 'listing_eligible'), (ads, 'ad_eligible')):
        for key, ids in pools.items():
            if key != 'observe':
                pools[key] = [kid for kid in ids if decisions.get(kid, {}).get(channel, True)]
    assigned = set().union(*listing.values(), *ads.values())
    for r in eligible:
        if r['keyword_id'] not in assigned:
            r['unassigned_reason'] = '超出审阅视图容量，保留在 records 与 CSV'
    rules = {'p1_threshold_weekly': threshold, 'p1_basis': 'user_arg' if p1_min is not None else f'case_default_p{int(P1_PERCENTILE * 100)}_of_retained',
             'caps': CAPS, 'note': '容量与阈值只决定审阅视图大小，不从 records 删除词'}
    return listing, ads, rules


def semantic_pools(records):
    result = {}
    names = {'scene': {'场景'}, 'audience': {'人群'}, 'gift_season': {'礼品', '季节'},
             'attribute': {'材质', '功能', '规格', '颜色', '用途'}}
    for key, tags in names.items():
        ids = [r['keyword_id'] for r in records if r['disposition'] not in {'compliance_negative', 'low_relevance_negative', 'brand_isolated'}
               and tags.intersection(r.get('tags', []))]
        result[key] = {'keyword_ids': ids, 'missing_reason': None if ids else '当前来源没有通过产品匹配的此类词，需补CT词根或真实导表'}
    opportunity = [r['keyword_id'] for r in records if any(
        (any(label in str(t.get('entry_signal') or '') for label in ('优质机会', '可切入')) or t.get('aba_position') in {'次要', '次要卡位'}) and t.get('source')
        for t in r.get('current_effect_tags', [])) and r['disposition'] not in {'compliance_negative', 'low_relevance_negative', 'brand_isolated'}]
    result['opportunity'] = {'keyword_ids': opportunity, 'missing_reason': None if opportunity else '缺可绑定本词/竞品的当前入场信号或次要卡位证据'}
    return result


def build_negatives(records, excluded_roots, source_ids, conflicts=()):
    negs, exact, phrase = [], [], []
    roots = [(root, 'excluded_root_hits', 'S1 排除词根（不同品类形态）') for _, root in excluded_roots]
    roots += [(term, 'attribute_conflicts', f'S1 产品 CT：本品不是 {term}') for _, term, _ in conflicts]
    for root, field, reason in roots:
        hits = [r for r in records if root in (r.get(field) or []) and not r['irrelevant_sheet']]
        if hits:
            nid = short_id('neg_', 'phrase:' + root)
            negs.append({'negative_id': nid, 'term': root, 'match_type': 'phrase', 'status': 'suggested_review',
                         'reason': reason, 'hits': len(hits),
                         'examples': [r['keyword'] for r in sorted(hits, key=lambda r: -(volume(r) or 0))[:5]],
                         'scope': 'campaign_level_after_search_term_review',
                         'lineage': [{'source_id': h['lineage'][0]['source_id'], 'sheet': h['lineage'][0]['sheet'], 'row': h['lineage'][0]['row']} for h in hits[:5]]})
            phrase.append(nid)
    candidates = sorted((r for r in records if r['retention_reason']['code'] == 'excluded_root' and not r['irrelevant_sheet']),
                        key=lambda r: -(volume(r) or 0))[:30]
    for r in candidates:
        nid = short_id('neg_', 'exact:' + r['normalized_keyword'])
        negs.append({'negative_id': nid, 'term': r['keyword'], 'keyword_id': r['keyword_id'], 'match_type': 'exact',
                     'status': 'suggested_review', 'reason': r['retention_reason']['text'],
                     'scope': 'campaign_level_after_search_term_review', 'lineage': r['lineage'][:1]})
        exact.append(nid)
    return negs, {'exact': exact, 'phrase': phrase}


# ---------- MCP ----------

def select_mcp(records, profile, marketplace):
    retained = sorted((r for r in records if r['disposition'] == 'retained'), key=lambda r: -(volume(r) or 0))
    base = retained or sorted((r for r in records if r['product_match']['status'] == 'direct'), key=lambda r: -(volume(r) or 0))
    words = [r['keyword'] for r in base]
    calls = [{'call_id': 'demand-01', 'tool': 'market_get_keyword_demand',
              'parameters': {'keywords': words[:20], 'country': marketplace},
              'why': '季节性、同比、当前季节位置与距峰值周数（本品可用高量词）'},
             {'call_id': 'history-01', 'tool': 'market_get_keyword_history',
              'parameters': {'keywords': words[:10], 'country': marketplace, 'granularity': 'month'},
              'why': '两个完整年度月度历史，供同比与峰值判断'}]
    for i, root in enumerate(profile.get('core_roots') or [], 1):
        calls.append({'call_id': f'root-{i:02d}', 'tool': 'market_get_keyword_root_trend',
                      'parameters': {'keyword': root, 'country': marketplace, 'granularity': 'month'},
                      'why': '精确词与词根长尾的需求占比'})
    for i, word in enumerate(words[:3], 1):
        calls.append({'call_id': f'competition-{i:02d}', 'tool': 'market_get_keyword_competition',
                      'parameters': {'keyword': word, 'country': marketplace},
                      'why': '头部竞争结构、自然/广告份额与可进入性'})
    return calls


def bridge_payload(result):
    if isinstance(result.get('structuredContent'), dict):
        return result['structuredContent']
    text = ''.join(c.get('text', '') for c in result.get('content') or [] if c.get('type') == 'text')
    try:
        return json.loads(text)
    except ValueError:
        return {'text': text}


def fetch_mcp(out, plan, server, *, route='native', config=None, retry=False):
    return shared('sif_mcp_routing').fetch_bridge(out, plan, server, route=route, config=config, retry=retry, runner=subprocess.run)


def returned_count(tool, resp):
    for key in ('profiles', 'keywords', 'top_competitors', 'dates'):
        if isinstance(resp.get(key), list):
            return len(resp[key])
    return 1 if resp else 0


def merge_mcp(root, out, marketplace, plan=None):
    if plan is None:
        path=out/'mcp_plan.json'
        plan=json.loads(path.read_text('utf-8-sig')) if path.is_file() else {'calls':[],'identity':{'marketplace':marketplace}}
    return shared('sif_mcp_routing').merge_receipts(root,out,marketplace,plan)


MCP_REUSE_DAYS = 7


def _previous_collection_date(out, identity):
    """Keep the earlier collection date when only the day changed, so a rerun on a later day
    does not void receipts already fetched for the same product, marketplace and sources."""
    path = Path(out) / 'mcp_plan.json' if out else None
    try:
        old = (json.loads(path.read_text('utf-8-sig')).get('identity') or {}) if path and path.is_file() else {}
        date = old.get('collection_date')
        if not date or {k: v for k, v in old.items() if k != 'collection_date'} != {k: v for k, v in identity.items() if k != 'collection_date'}:
            return None
        age = (datetime.now(timezone.utc).date() - datetime.fromisoformat(date).date()).days
        return date if 0 <= age <= MCP_REUSE_DAYS else None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def mcp_plan(records, profile, marketplace, product_id, sources, out=None):
    identity={'product_id':product_id,'marketplace':marketplace,
        'collection_date':datetime.now(timezone.utc).date().isoformat(),
        'source_hashes':sorted(str(s.get('sha256') or '') for s in sources),
        'periods':[s.get('period') or {} for s in sources]}
    identity['collection_date']=_previous_collection_date(out,identity) or identity['collection_date']
    return shared('sif_mcp_routing').make_plan(select_mcp(records,profile,marketplace),identity)



SIF_EFFECT_FIELDS = ('sif_tags', 'entry_signal', 'aba_position', 'traffic_dependency')
SIF_EFFECT_TOOLS = {'market_screen_keyword_opportunities', 'market_get_asin_keyword_signals', 'market_get_asin_aba_footprint'}


def bind_sif_effects(records, parsed, sources):
    """Only explicitly returned fields are observations; absent fields are unknown."""
    by_word = {r.get('normalized_keyword') or clean_keyword(r['keyword']).casefold(): r for r in records}
    by_source = {s['source_id']: s for s in sources}
    for record in records:
        record['effect_observations'] = []
        record['current_effect_tags'] = []
    for tool in sorted(SIF_EFFECT_TOOLS):
        for sid, params, response in parsed.get(tool, []):
            source = by_source.get(sid, {})
            identity = {k: source[k] for k in ('source_id', 'provider', 'subject', 'asin', 'asins', 'sha256') if k in source}
            identity['tool'] = tool
            items = response.get('top_keywords') if tool == 'market_get_asin_keyword_signals' else response.get('keywords')
            for original_item in items or []:
                if not isinstance(original_item, dict): continue
                item = dict(original_item)
                if tool == 'market_get_asin_keyword_signals' and 'keyword_tags' in item:
                    item['sif_tags'] = item['keyword_tags']
                if tool == 'market_get_asin_aba_footprint' and item.get('rank') in (1, 2, 3):
                    item['aba_position'] = '首位卡位' if item['rank'] == 1 else '次要卡位'
                word = by_word.get(clean_keyword(item.get('keyword') or '').casefold())
                dimensions = [key for key in SIF_EFFECT_FIELDS if key in item]
                if not word or not dimensions: continue
                for dimension in dimensions:
                    value = item[dimension]
                    effects = [{dimension: value, 'source': identity}] if value not in (None, '', []) else []
                    word['effect_observations'].append({'source': identity, 'effect_dimensions': [dimension],
                        'effect_tags': effects, 'observed_at': source.get('as_of')})
                word.setdefault('lineage', []).append({'source_id': sid, 'note': 'SIF 明确返回的效果维度'})
    # The package/report and persistent asset must use exactly the same current
    # projection. Splitting dimensions preserves an explicit null beside a value.
    spec = importlib.util.spec_from_file_location('sif_effect_projection', HERE.parents[3]/'scripts/keyword_asset.py')
    asset = importlib.util.module_from_spec(spec); spec.loader.exec_module(asset)
    for word in records:
        events = [{'source': obs['source'], 'source_identity': asset._identity(obs['source']),
            'observed_at': obs['observed_at'] or '', 'observed_dimensions': obs['effect_dimensions'],
            'tags': obs['effect_tags']} for obs in word['effect_observations']]
        word['current_effect_tags'] = asset._current_effects({'effect_observations': events})


def asin_of(value):
    found = re.search(r'\b(B0[A-Z0-9]{8})\b', str(value or ''))
    return found.group(1) if found else value


def mcp_analysis(parsed):
    out = {'demand_profiles': [], 'root_trends': [], 'competition': [], 'history_series': []}
    for sid, _, resp in parsed.get('demand', []):
        for p in resp.get('profiles') or []:
            current = p.get('current') or {}
            out['demand_profiles'].append({'source_id': sid, 'keyword': p.get('keyword'), 'demand_structure': p.get('demand_structure'),
                                           'data_coverage': p.get('data_coverage'), 'trend': p.get('trend'),
                                           'seasonality': p.get('seasonality'),
                                           'current_phase': p.get('current_phase') or current.get('season_position'),
                                           'vs_seasonal_baseline': current.get('vs_seasonal_baseline'), 'diagnosis': p.get('diagnosis'),
                                           'weeks_to_peak': p.get('weeks_to_peak'), 'action_hint': p.get('action_hint'),
                                           'seasonal_strength': p.get('seasonal_strength'), 'interpretation': p.get('interpretation'),
                                           'search_ctr_90d': p.get('search_ctr_90d'), 'ctr_data_period': p.get('ctr_data_period')})
    for sid, params, resp in parsed.get('history', []):
        for k in resp.get('keywords') or []:
            points = [{'month': str(d)[:7], 'value': v} for d, v in zip(k.get('dates') or [], k.get('volumes') or [])]
            out['history_series'].append({'source_id': sid, 'keyword': k.get('keyword'), 'granularity': params.get('granularity') or resp.get('granularity'),
                                          'points': points, 'latest': k.get('latest'),
                                          'summary': (k.get('_formatted') or {}).get('anchor_sentence')})
    for sid, params, resp in parsed.get('root_trend', []):
        out['root_trends'].append({'source_id': sid, 'keyword': resp.get('keyword') or params.get('keyword'), 'latest': resp.get('latest'),
                                   'summary': (resp.get('_formatted') or {}).get('anchor_sentence')})
    for sid, params, resp in parsed.get('competition', []):
        comp = resp.get('top_competitors') or []
        structure = resp.get('market_structure') or {}
        out['competition'].append({'source_id': sid, 'keyword': (resp.get('query_context') or {}).get('keyword') or params.get('keyword'),
                                   'data_window': (resp.get('query_context') or {}).get('data_window'),
                                   'market_structure': structure,
                                   'concentration_level': structure.get('concentration_level') or (resp.get('concentration_profile') or {}).get('level'),
                                   'competition_mode': structure.get('competition_mode'),
                                   'primary_angle': (resp.get('strategy_path') or {}).get('primary_angle'),
                                   'summary': (resp.get('_formatted') or {}).get('anchor_sentence'),
                                   'top_competitors': [{**{k: c.get(k) for k in ('total_share', 'organic_share', 'sp_share', 'price',
                                                                                'rating', 'review_count', 'monthly_orders')},
                                                        'asin': asin_of(c.get('asin'))} for c in comp[:10]]})
    return out


def seasonality_summary(mcp_out):
    profiles = mcp_out['demand_profiles']
    if not profiles and not mcp_out['history_series']:
        return {'status': 'unknown', 'reason': '本轮未取得 SIF 历史或需求诊断返回；不从单次导出判断全年峰值'}
    peaks = Counter(m for p in profiles for m in ((p.get('seasonality') or {}).get('peak_months') or []))
    strength = Counter((p.get('seasonality') or {}).get('strength') for p in profiles)
    years = [((p.get('data_coverage') or {}).get('years')) for p in profiles]
    return {'status': 'from_mcp', 'keywords': len(profiles), 'peak_months': peaks.most_common(6),
            'strength_distribution': dict(strength), 'coverage_years': sorted({y for y in years if y is not None}),
            'note': '季节判断来自 SIF 需求诊断与月度历史；只取到一年时降低确定性'}


# ---------- main ----------

def build(root, args):
    """Read all S1/S2 inputs in one view; run() still publishes under real root."""
    module=sys.modules.get('r01_case_store')
    if module is None:
        path=HERE.parents[3]/'scripts/r01_case_store.py'
        spec=importlib.util.spec_from_file_location('r01_case_store',path)
        module=importlib.util.module_from_spec(spec);sys.modules['r01_case_store']=module
        spec.loader.exec_module(module)
    with module.CompactCase(root).materialize() as view:
        return _build_native(view.root,args)


def _build_native(root, args):
    profile = load_yaml(root / PROFILE)
    extra = (load_yaml(root / 'S2_文案板块/_补充/ct_supplement.yaml') if (root / 'S2_文案板块/_补充/ct_supplement.yaml').is_file() else {}).get('core_roots') or []
    if extra:                                     # S2 补充的短核心词根（s2_ct_supplement.py roots），S1 画像文件不动
        legacy = profile.get('core_roots') or []
        legacy = [legacy] if isinstance(legacy, str) else list(legacy)
        profile = {**profile, 'core_roots': legacy + [r for r in extra if r not in legacy]}
    blacklist = load_yaml(root / BLACKLIST)
    facts_doc = load_yaml(root / FACTS) if (root / FACTS).is_file() else {}
    if not (root / MANIFEST).is_file():
        raise SystemExit('缺导出清单 ' + MANIFEST + '：先用 scripts/s2_export_card.py intake <产品根> 登记用户放好的关键词表')
    manifest = load_yaml(root / MANIFEST)
    pool_rel = pool_path(root, manifest)
    if pool_rel is None:
        raise SystemExit('找不到第 2 步确认的竞品池：先完成竞品分层确认（默认位置 ' + POOL + '）')
    pool = load_yaml(root / pool_rel)
    out = root / OUT
    decisions_path = out / 'review_decisions.json'
    decisions = json.loads(decisions_path.read_text(encoding='utf-8-sig')) if decisions_path.is_file() else {}
    marketplace = str(pool.get('marketplace') or profile.get('marketplace') or 'US')
    files = args.export or [f['path'] for f in manifest.get('files') or [] if '/SIF/' in f.get('path', '') or manifest.get('source') == 'sif']
    if not files:
        raise SystemExit('没有 SIF 导出文件：manifest.files 为空，也没有 --export')
    ct = load_product_ct(root)
    args.product_ct = ({'status': ct['status'], 'material': ct.get('material_cn'), 'material_status': ct.get('material_status'),
                        'errors': ct['errors'][:5]} if ct else
                       {'status': 'missing', 'note': '没有 S1 产品 CT：材质按 01_product_profile.materials 当本品，市场材质词可能混进词库'})
    lex = build_lexicon(profile, blacklist, decisions, ct)
    registry_list = lex['facts'] + product_fact_registry(facts_doc)
    registry = {f['id']: f for f in registry_list}
    lex['registry'] = registry
    sources, rows, per_sheet = read_exports(root, files, marketplace)
    confirmed = [str(a) for a in manifest.get('confirmed_asins') or []]
    for source in sources:
        source.update(subject='competitor' if confirmed else 'unknown', asins=confirmed,
                      asin_scope='aggregate_export_not_per_keyword_coverage')

    blank = invalid = 0
    merged, order = {}, []
    for row in rows:
        raw = row.get('keyword')
        if raw is None or not str(raw).strip():
            blank += 1
            continue
        kw = clean_keyword(raw)
        norm = kw.casefold()
        if not re.search(r'\w', norm):
            invalid += 1
            continue
        link = {'source_id': row['source_id'], 'sheet': row['sheet'], 'row': row['row'], 'raw_keyword': str(raw)}
        if norm in merged:
            merged[norm]['lineage'].append(link)
            continue
        merged[norm] = {'row': row, 'keyword': kw, 'lineage': [link]}
        order.append(norm)

    records = []
    for norm in order:
        item = merged[norm]
        row = item['row']
        metrics = {name: parse_metric(name, row.get(name), row['source_id'], row['period']) for name in METRICS}
        tokens = toks(norm)
        c = classify(tokens, str(row.get('sif_relevance') or ''), row['irrelevant_sheet'], lex)
        identity_refs = [ref for ref in c['fact_refs'] if registry.get(ref, {}).get('matching_only')]
        qualified_refs = [ref for ref in c['fact_refs'] if ref not in identity_refs]
        kid = short_id('kw_', norm)
        records.append({
            'keyword_id': kid, 'keyword': item['keyword'], 'normalized_keyword': norm, 'source_keyword': str(row.get('keyword')),
            'cn_translation': translate_keyword(item['keyword'], row.get('cn_translation')), 'source_cn_translation': row.get('cn_translation'),
            'sif_relevance': row.get('sif_relevance'), 'relevance_data_status': 'provided' if row.get('sif_relevance') else 'missing', 'source_sheet': row['sheet'],
            'irrelevant_sheet': row['irrelevant_sheet'], 'metrics': metrics, 'lineage': item['lineage'],
            'disposition': c['disposition'],
            'retention_reason': {'code': c['reason_code'], 'text': reason_text(c)},
            'product_match': {'status': c['product_match'], 'reason': reason_text(c), 'fact_refs': qualified_refs},
            'fact_refs': qualified_refs, 'identity_match_refs': identity_refs,
            'pending_fact_refs': [ref for ref in c['pending_refs'] if ref not in identity_refs],
            'claim_restricted': bool(c['claims']) or c['reason_code'] == 'performance_pending',
            'claim_hits': list(dict.fromkeys(c['claims'] + c['performance_claims'])),
            'historical_performance_exclusions': c['performance_claims'],
            'brand_hits': c['brands'], 'compliance_hits': [b[0] for b in c['blocked']], 'excluded_root_hits': c['excluded'],
            'attribute_conflicts': c['conflicts'], 'core_hits': c['core_hits'], 'unexplained_modifiers': c['unexplained'], 'six_dimensions': c['six_dimensions'], 'tags': c['tags'], 'primary_type': c['primary_type'],
            'needs_review': c['review'],
            'coverage': {'confirmed_competitor_count': len(confirmed), 'competitor_coverage_count': 0, 'competitor_asins': [],
                         'coverage_ratio': 0.0 if confirmed else None, 'coverage_status': 'not_observable_in_export',
                         'coverage_missing_reason': 'SIF 关键词反查导出为跨 ASIN 聚合表，不含逐 ASIN 列；逐词共同覆盖不可计算，比值 0 仅为契约占位'},
        })
    mapping_module = shared('s2_ct_mapping')
    mapping = mapping_module.map_ct((ct or {}).get('ct', {}), records)
    mapped_rows = {r['ct_ref']: r for r in mapping['rows']}
    for r in records:
        references = [row for row in mapping['rows'] if r['keyword_id'] in row['keyword_ids']]
        r['ct_refs'] = [row['ct_ref'] for row in references]
        r['ct_evidence'] = [{key: row[key] for key in ('ct_ref', 'state', 'copy_permission', 'source',
            'source_status', 'source_sha256', 'fingerprint', 'skus', 'voc_refs', 'en_terms')} for row in references]
        r['ct_fingerprints'] = {row['ct_ref']: row['fingerprint'] for row in references}
        for row in references:
            if row['ct_ref'] == 'CT:uses':
                r['tags'] = list(dict.fromkeys(r['tags'] + ['用途']))
                use_terms = ((ct or {}).get('ct', {}).get('uses') or {}).get('en_terms') or []
                scoped_use_terms = qualified_use_terms(r['keyword'], use_terms)
                only_use_claims = all(toks(claim) in [toks(term) for term in use_terms] for claim in r['claim_hits'])
                if (row['copy_permission'] == '弱化表述' and row['state'] in {'known','illustrated'} and row['source_sha256'] and r['disposition'] in {'retained', 'observe'}
                    and scoped_use_terms and only_use_claims and not r['attribute_conflicts'] and not r['excluded_root_hits']):
                    r['qualified_copy'] = {'allowed': True, 'scope': 'illustrated_use_only', 'ct_ref': row['ct_ref'],
                        'source_status': row['source_status'], 'source': row['source'], 'sha256': row['source_sha256'],
                        'illustrated_terms': scoped_use_terms,
                        'reason': '真实图片展示用途；只描述用途，不保证全部尺寸适配'}
                    r['fact_refs'] = list(dict.fromkeys(r['fact_refs'] + ['CT:uses']))
    registry_list = merge_fact_registry(registry_list, [{'id': row['ct_ref'], 'text': str(row['value']), 'source': row['source'], 'status': row['source_status'],
                       'status_reason': row['state'], 'kind': 'product_ct', 'copy_permission': row['copy_permission']}
                      for row in mapping['rows']])
    registry.update({f['id']: f for f in registry_list})
    applied, overridden = apply_decisions(records, decisions, registry)
    # Review may add explicit CT refs after the initial lexical mapping. Bind those
    # refs before both the standalone pool guard and later asset/contract guards.
    for record in records:
        record['ct_refs'] = list(dict.fromkeys(record.get('ct_refs', []) +
            [ref for ref in record.get('fact_refs', []) if str(ref).startswith('CT:')]))
    mapping = mapping_module.map_ct((ct or {}).get('ct', {}), records)
    for record in records:
        references = [row for row in mapping['rows'] if row['ct_ref'] in record['ct_refs']]
        record['ct_evidence'] = [{key: row[key] for key in ('ct_ref', 'state', 'copy_permission', 'source',
            'source_status', 'source_sha256', 'fingerprint', 'skus', 'voc_refs', 'en_terms')} for row in references]
        record['ct_fingerprints'] = {row['ct_ref']: row['fingerprint'] for row in references}
    args.product_ct['decisions_overridden'] = len(overridden)
    args.product_ct['undecided_modifiers'] = [u['modifier'] for u in undecided_modifiers(
        [r for r in records if r['disposition'] == 'retained'], lex['known'], 10)]
    for r in records:
        r['needs_review'] = r['needs_review'] and r['keyword_id'] not in set(applied)
        if (r.get('qualified_copy') or {}).get('allowed'):
            r['historical_retention_reason'] = r['retention_reason']
            r['retention_reason'] = {'code': 'illustrated_use', 'text': REASONS['illustrated_use']}

    guard = eligibility_module()
    for record in records:
        blocked_ct, _ = guard.ct_constraints(record, {'ct_mapping': mapping})
        if blocked_ct and record['disposition'] == 'retained':
            record['historical_retention_reason'] = record['retention_reason']
            record['disposition'] = 'observe'
            record['retention_reason'] = {'code': 'fact_pending', 'text': '; '.join(blocked_ct)}
        if blocked_ct:
            record.setdefault('qualified_copy', {})['allowed'] = False
    # Words kept by the R08 'write it, flag it' rules still pass the copy compliance scan first: a real
    # platform trigger (e.g. 'baby' -> children's product certification) sends them back to observe.
    relaxed = [r for r in records if r['disposition'] == 'retained'
               and r['retention_reason'].get('code') in {'direct_unverified_modifier', 'direct_match_fact_unverified'}]
    if relaxed:
        # One scan for all of them: every separate scan re-reads the series asset.
        scan = shared('compliance_scan').scan_fields({str(i): r['keyword'] for i, r in enumerate(relaxed)}, series=root,
                                                     evidence={'product_id': manifest.get('product_id'), 'marketplace': marketplace})
        for i, record in enumerate(relaxed):
            hits = sorted({h.get('entry_id') or h.get('found') for h in scan.get('hits', [])
                           if h.get('field') == str(i) and h.get('level') == 'block'} - {None})
            if hits:
                record['historical_retention_reason'] = record['retention_reason']
                record['disposition'] = 'observe'
                record['retention_reason'] = {'code': 'compliance_review', 'text': '命中平台合规触发词，需认证或人工确认：' + '、'.join(map(str, hits))}
    listing, ads, pool_rules = build_pools(records, args.p1_min_weekly)
    negs, neg_pools = build_negatives(records, lex['excluded'], [s['source_id'] for s in sources], lex['conflicts'])
    asin_records = [{'asin_id': 'asin_' + a, 'asin': a, 'role': 'confirmed_competitor',
                     'lineage': [{'source_id': sources[0]['source_id'], 'note': 'SIF 反查所用确认 ASIN（keyword_export_manifest.yaml）'}]}
                    for a in confirmed if re.fullmatch(r'[A-Z0-9]{10}', a)]
    product_id = str(pool.get('product_id') or manifest.get('product_id') or profile.get('asin') or 'product')
    plan=mcp_plan(records,profile,marketplace,product_id,sources,out)
    mcp_sources, mcp, parsed = merge_mcp(root, out, marketplace,plan)
    bind_sif_effects(records, parsed, sources + mcp_sources)
    mcp_out = mcp_analysis(parsed)

    counts = Counter(r['disposition'] for r in records)
    related = [r for r in records if not r['irrelevant_sheet']]
    retained = [r for r in records if r['disposition'] == 'retained']
    direct_related = [r for r in related if r['product_match']['status'] == 'direct']
    analysis = {
        'traffic_concentration': {'retained': concentration(retained), 'related_sheet': concentration(related)},
        'metric_fill_rates': {'all': fill_rates(records), 'retained': fill_rates(retained)},
        'competitor_coverage': {'confirmed_competitor_count': len(confirmed), 'confirmed_asins': confirmed,
                                'per_keyword': 'not_observable_in_export',
                                'reason': 'SIF 反查导出不带逐 ASIN 列；批次覆盖见 keyword_export_manifest.yaml#coverage_review'},
        'claim_unlock': unlocks(direct_related, 'claim_hits', 'claims'),
        'fact_pending_unlock': unlocks([{**r, 'pending_labels': [registry[x]['text'] for x in r['pending_fact_refs']]}
                                        for r in direct_related if r['pending_fact_refs'] and not r['claim_hits']], 'pending_labels', None),
        'brand_isolated': unlocks(related, 'brand_hits', None),
        'compliance_blocked': unlocks(related, 'compliance_hits', None),
        'excluded_roots': unlocks(related, 'excluded_root_hits', None),
        'product_ct': args.product_ct,
        'product_ct_overridden_decisions': overridden[:100],
        'product_ct_undecided': undecided_modifiers(retained, lex['known']),
        'product_ct_conflicts': unlocks(related, 'attribute_conflicts', None),
        'high_volume_low_relevance': [{'keyword_id': r['keyword_id'], 'keyword': r['keyword'], 'weekly_search_volume': volume(r),
                                       'disposition': r['disposition'], 'reason': r['retention_reason']['text']}
                                      for r in sorted((r for r in related if r['disposition'] != 'retained'), key=lambda r: -(volume(r) or 0))[:20]],
        'sheet2_pollution_roots': pollution_roots([r for r in records if r['irrelevant_sheet']], lex),
        'reason_counts': dict(Counter(r['retention_reason']['code'] for r in records)),
        'dimension_counts': {d: Counter(w for r in retained for w in r['six_dimensions'][d]).most_common(10) for d in lex['dims']},
        'review_queue_size': sum(1 for r in records if r['needs_review']),
        'review_decisions_applied': len(applied),
        'pool_rules': pool_rules,
        'semantic_pools': semantic_pools(records),
        'ct_mapping': mapping,
        'placement_requirements': [{'keyword_id': r['keyword_id'], 'keyword': r['keyword'], 'one_of': ['title', 'highlight']}
                                   for r in records if r['disposition'] == 'retained' and '一级' in r['tags'] and volume(r) != 0],
        'mcp': mcp_out,
        'seasonality': seasonality_summary(mcp_out),
        'counterarguments': list(decisions.get('counterarguments') or []),
    }
    conservation = {'raw_rows': len(rows), 'blank_keyword_rows': blank, 'invalid_rows': invalid,
                    'normalized_valid_rows': len(rows) - blank - invalid, 'unique_keywords': len(records),
                    'merged_duplicate_rows': len(rows) - blank - invalid - len(records),
                    'disposition_counts': {k: counts.get(k, 0) for k in ('retained', 'observe', 'brand_isolated', 'compliance_negative', 'low_relevance_negative')},
                    'records_count': len(records), 'per_sheet': per_sheet}
    product_id = str(pool.get('product_id') or manifest.get('product_id') or profile.get('asin') or 'product')
    as_of = sources[0]['as_of']
    package = {
        'schema_version': '2.0', 'package_id': f'SIF-{product_id}', 'package_version': 1, 'package_hash': '', 'supersedes': None,
        'change_reason': '', 'run_id': manifest.get('run_id') or pool.get('run_id'),
        's2_competitor_gate': {'path': pool_rel, 'sha256': sha256_file(root / pool_rel)},
        's2_export_intake': {'path': MANIFEST, 'sha256': sha256_file(root / MANIFEST)},
        'run_mode': args.run_mode, **({'demo_label': 'DEMO_ONLY_NOT_UPLOADABLE'} if args.run_mode == 'demo_direct_run' else {}),
        'marketplace': marketplace, 'product_id': product_id, 'source_mode': 'sif', 'source_line_policy': 'isolated',
        'generator': {'script': 'amazon-sif-keyword-research/scripts/sif_clean.py', 'as_of': as_of},
        'data_sources': sources + mcp_sources, 'mcp': mcp, 'records': records,
        'listing_pools': listing, 'pools': ads, 'negative_records': negs, 'negative_pools': neg_pools,
        'asin_records': asin_records, 'asin_pools': {'pat_candidates': [a['asin_id'] for a in asin_records], 'defense_candidates': [], 'brand_review': []},
        'compliance': {'blacklist_source': BLACKLIST,
                       'removed_terms': [{'terms': [b[1]], 'reason': b[2], 'channels': b[3], 'status': 'excluded'} for b in lex['blocked']],
                       'brand_isolated_terms': [{'terms': [b[1]], 'reason': '竞品/第三方品牌：仅人工审核后定投', 'status': 'excluded'} for b in lex['brands']],
                       'claim_restricted_terms': [{'terms': c['terms'], 'claim': c['claim'], 'reason': c['reason'], 'status': 'pending'} for c in lex['claims'] if c['terms']]},
        'conservation': conservation, 'analysis': analysis,
        'handoff': {'strategy_brief_inputs': {'package_ref': {}, 'counts': conservation['disposition_counts'],
                                              'pool_sizes': {k: len(v) for k, v in {**listing, **ads}.items()},
                                              'traffic_concentration': analysis['traffic_concentration']['retained'],
                                              'metric_semantics': {k: METRICS[k][0] for k in METRICS},
                                              'fill_rates': analysis['metric_fill_rates']['retained'],
                                              'seasonality': analysis['seasonality'],
                                              'claim_unlock': analysis['claim_unlock'][:5]},
                    'never_authorizes_external_upload': True},
        'downstream_eligibility': {'external_upload': 'never_authorized_by_keyword_package', 'listing': 'copywriter_review_required',
                                   'ads': 'strategy_review_required'},
        'data_explanation_window': {
            'obtained_facts': [f'{s["source_id"]} {s.get("relative_path") or s.get("tool_name")}' for s in sources + mcp_sources],
            'derived': ['disposition/本品匹配/D1–D6 由 S1 画像、黑名单与 SIF 相关度按 sif_clean.py 规则推导',
                        '流量集中度为选定词集合的周搜索量占比'],
            'simulated': [],
            'missing_and_impact': [f'{name} 缺失率 {1 - v["rate"]:.0%}' for name, v in analysis['metric_fill_rates']['retained'].items()
                                   if v['rate'] is not None and v['rate'] < 0.5]
                                  + ([analysis['seasonality']['reason']] if analysis['seasonality']['status'] == 'unknown' else []),
            'continue_and_revision': ['review_queue.json 中的词在 review_decisions.json 裁决后重跑；MCP 补数后重跑合并']},
        'late_data_revision': {'policy': 'impact_scoped', 'invalidates_downstream_hashes': True},
        'acceptance_checks': {'validation_report': OUT + '/keyword_package.validation.json'},
    }
    context = {'product_id': product_id, 'product_name': (profile.get('orderable_unit') or {}).get('product_name') or profile.get('product_type'),
               'marketplace': marketplace, 'as_of': as_of,
               'policy_profile': 'US_non_media_2026_07' if marketplace.upper() == 'US' else None,
               'sku_product_map': decisions.get('sku_product_map') or {},
               'product_ct': (ct or {}).get('ct', {}), 'ct_mapping': mapping,
               'facts': [{k: f.get(k) for k in ('id', 'text', 'status', 'source', 'status_reason', 'copy_permission')} for f in registry_list],
               'seasonality': {'series': [{'keyword_id': s['keyword'], 'source': s['source_id'], 'metric': 'aba_search_volume',
                                           'unit': 'searches/month', 'points': s['points']}
                                          for s in mcp_out['history_series'] if s.get('granularity') == 'month']},
               'next_actions': next_actions(analysis, mcp), 'evidence': package['data_explanation_window']['obtained_facts']}
    review = [{'keyword_id': r['keyword_id'], 'keyword': r['keyword'], 'cn': r['cn_translation'], 'sif_relevance': r['sif_relevance'],
               'weekly_search_volume': volume(r), 'rule_disposition': r['disposition'], 'reason': r['retention_reason']['text'],
               'core_hits': r['core_hits'], 'unexplained_modifiers': r.get('unexplained_modifiers', []),
               'excluded_root_hits': r['excluded_root_hits']}
              for r in sorted((r for r in records if r['needs_review']), key=lambda r: -(volume(r) or 0))[:150]]
    return package, context, review, registry_list, profile, marketplace


def next_actions(analysis, mcp):
    actions = []
    for c in [c for c in analysis['claim_unlock'] if c['weekly_volume']][:2]:
        actions.append(f'验证「{c["label"]}」：可解锁 {c["keywords"]} 个本品词、周搜索量合计 {c["weekly_volume"]:,}（例：{"、".join(c["examples"][:3])}）')
    if analysis['review_queue_size']:
        actions.append(f'复核 review_queue.json 前列 {min(analysis["review_queue_size"], 150)} 个待判词，写入 review_decisions.json 后重跑')
    if mcp['status'] == 'skipped':
        actions.append('运行 --plan-mcp，按清单调用 SIF 工具补季节与竞争证据后重跑')
    return actions[:3]


def supersede(package, target, change_reason):
    if not target.is_file():
        return
    old = json.loads(target.read_text(encoding='utf-8-sig'))
    if not old.get('package_hash'):
        return
    history = target.parent / '_history'
    history.mkdir(exist_ok=True)
    name = f'keyword_package.{old.get("package_id")}.v{old.get("package_version", 1)}.yaml'
    (history / name).write_text(target.read_text(encoding='utf-8'), encoding='utf-8')
    if old.get('package_id') != package['package_id']:
        # A differently-identified package (e.g. hand-built before this script) is replaced, not versioned.
        package['replaces_package'] = {**{k: old.get(k) for k in ('package_id', 'package_version', 'package_hash')},
                                       'archived_as': '_history/' + name}
        package['change_reason'] = change_reason or '改用 sif_clean.py 确定性清洗重建词包'
    else:
        package['package_version'] = int(old.get('package_version') or 1) + 1
        package['supersedes'] = {k: old.get(k) for k in ('package_id', 'package_version', 'package_hash')}
        package['change_reason'] = change_reason or '重新清洗（sif_clean.py 确定性规则 / 审阅裁决 / MCP 补数）'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--run', required=True, type=Path, help='产品根（含 workflow_state.yaml）')
    ap.add_argument('--export', action='append', help='SIF 导出（相对产品根）；默认读 keyword_export_manifest.yaml#files')
    ap.add_argument('--run-mode', default='production', choices=['production', 'demo_direct_run'])
    ap.add_argument('--p1-min-weekly', type=int, help='Exact-P1 周搜索量门槛；默认取保留词 80 分位（case_default）')
    ap.add_argument('--plan-mcp', action='store_true', help='只写 mcp_plan.json（补数清单），不改词包')
    ap.add_argument('--fetch-mcp', action='store_true', help='按补数清单经直连桥调用 SIF，原始返回落盘后合并进词包')
    ap.add_argument('--mcp-route', choices=['native','bridge'], default='native', help='native仅生成宿主调用计划；bridge须明确传宿主配置')
    ap.add_argument('--mcp-config', type=Path, help='明确的宿主MCP配置；Claude使用自己的mcp_servers.json')
    ap.add_argument('--mcp-retry', action='store_true', help='修复原因后显式重新探测本任务已断路的bridge；不绕过权限')
    ap.add_argument('--mcp-server', default='sif', help='直连桥里 SIF 的登记名（sif / sif_mcp 均可）')
    ap.add_argument('--change-reason', default='')
    args = ap.parse_args(argv)
    sys.path.insert(0, str(BRIDGE.parent))
    from plugin_freshness import stale_plugin
    stale = stale_plugin(HERE.parents[3])
    if stale:
        print(json.dumps({'status': 'stale_plugin', 'error': stale}, ensure_ascii=False))
        return 3
    root = args.run.resolve()
    out = root / OUT
    out.mkdir(parents=True, exist_ok=True)
    try:
        return run(root, out, args)
    except ValueError as exc:
        print(json.dumps({'status': 'fail', 'error': str(exc)}, ensure_ascii=False))
        return 2


def write_review_queue(root, out, package, review):
    """Refresh review evidence before any planning early return; it is not acceptance."""
    target=out/'review_queue.json'
    target.write_text(json.dumps(review,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    paths=(PROFILE,FACTS,PRODUCT_CT_FILE,BLACKLIST,(package.get('s2_competitor_gate') or {}).get('path') or POOL,MANIFEST,
           (out/'review_decisions.json').relative_to(root).as_posix())
    inputs={path:sha256_file(root/path) if (root/path).is_file() else None for path in paths}
    source_inputs=[{key:source[key] for key in ('source_id','provider','subject','asin','asins','sha256','period') if key in source}
                   for source in package['data_sources'] if source.get('acquisition_method')!='mcp']
    binding={'product_id':package['product_id'],'marketplace':package['marketplace'],
             'input_files':inputs,'source_inputs':source_inputs}
    fingerprint=hashlib.sha256(json.dumps(binding,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    context={**binding,'input_fingerprint':fingerprint,'queue_sha256':sha256_file(target),
             'status':'generated_for_review','stage_complete':False}
    path=out/'review_queue_context.json'
    path.write_text(json.dumps(context,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return path.relative_to(root).as_posix()


def run(root, out, args):
    package, context, review, registry, profile, marketplace = build(root, args)
    review_context=write_review_queue(root,out,package,review)
    if args.plan_mcp or args.fetch_mcp:
        plan = mcp_plan(package['records'],profile,marketplace,package['product_id'],
                        [s for s in package['data_sources'] if s.get('acquisition_method')!='mcp'],out)
        (out / 'mcp_plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        if args.plan_mcp:
            print(json.dumps({'status': 'planned', 'mcp_plan': (out / 'mcp_plan.json').relative_to(root).as_posix(),
                              'calls': len(plan['calls']),'review_queue_context':review_context,'stage_complete':False}, ensure_ascii=False))
            return 0
        fetched = fetch_mcp(out, plan, args.mcp_server, route=getattr(args,'mcp_route','native'),config=getattr(args,'mcp_config',None),retry=getattr(args,'mcp_retry',False))
        print(json.dumps(fetched, ensure_ascii=False))
        if fetched['status']=='native_calls_required':return 0
        package, context, review, registry, profile, marketplace = build(root, args)
        review_context=write_review_queue(root,out,package,review)
    target = out / 'keyword_package.yaml'
    if target.is_file():
        old = json.loads(target.read_text(encoding='utf-8-sig'))
        previous = (old.get('analysis') or {}).get('ct_mapping')
        context['previous_ct_mapping'] = previous
        mapping = shared('s2_ct_mapping').map_ct(context.get('product_ct') or {}, package['records'], previous=previous)
        package['analysis']['ct_mapping'] = mapping
        context['ct_mapping'] = mapping
    supersede(package, target, args.change_reason)
    package['handoff']['strategy_brief_inputs']['package_ref'] = {k: package[k] for k in ('package_id', 'package_version', 'package_hash')}
    target.write_text(json.dumps(package, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (out / 's2_report_context.json').write_text(json.dumps(context, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (out / 'facts_registry.json').write_text(json.dumps(registry, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    result = subprocess.run([sys.executable, str(CONTRACT), 'finalize', '--input', str(target), '--expected-source', 'sif',
                             '--report', str(out / 'keyword_package.validation.json'), '--series', str(root)], capture_output=True, text=True)
    check = json.loads((out / 'keyword_package.validation.json').read_text(encoding='utf-8'))
    final = json.loads(target.read_text(encoding='utf-8'))
    index_file = write_ad_index(target, final)
    summary = {'status': check['status'], 'package': target.relative_to(root).as_posix(), 'package_version': final['package_version'],
               'package_hash': final['package_hash'], 'conservation': final['conservation']['disposition_counts'],
               'pools': {k: len(v) for k, v in {**final['listing_pools'], **final['pools']}.items()},
               'review_queue': len(review),'review_queue_context':review_context, 'mcp': final['mcp']['status'], 'ad_index': index_file,
               'product_ct': getattr(args, 'product_ct', None),
               'series_asset': final.get('series_asset'),
               'errors': check['errors'][:10]}
    print(json.dumps(summary, ensure_ascii=False))
    return result.returncode


if __name__ == '__main__':
    sys.exit(main())
