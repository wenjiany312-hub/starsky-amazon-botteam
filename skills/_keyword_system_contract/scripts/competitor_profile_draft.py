"""从 S1 画像生成 S2 第 2 步竞品画像草稿（S2-R4）：字段名就是 competitor_candidate_pool.py 认的那些，用户只核对词表。

    python competitor_profile_draft.py --run <产品根>      # 写 S2_文案板块/_阶段总览/竞品研究/产品画像_草稿.json 并打印待核项

来源：S1 01_product_profile.yaml 的定位核心词 / core_keyword / core_roots、选定款目标价、类目节点、排除词；
S2 补充层的短词根（s2_ct_supplement.py roots）也会用上。拿不准的写进 to_confirm，不编造。
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import yaml

PROFILE = 'S1_选品板块/amazon-market-research/01_product_profile.yaml'
SUPPLEMENT = 'S2_文案板块/_补充/ct_supplement.yaml'
OUT = 'S2_文案板块/_阶段总览/竞品研究/产品画像_草稿.json'
STOP = {'for', 'with', 'and', 'the', 'of', 'in', 'to', 'a', 'an', 'set', 'pack', 'piece', 'pcs', 'inch', 'in'}
CONTAINERS = {'kit', 'set', 'roll', 'box', 'case', 'holder', 'organizer', 'bag', 'pack', 'mat', 'cover', 'replacement', 'mesh', 'refill', 'bundle'}


def _load(path):
    path = Path(path)
    return (yaml.safe_load(path.read_text(encoding='utf-8-sig')) or {}) if path.is_file() else {}


def _words(text):
    return [w for w in re.split(r'[^a-z0-9]+', str(text).lower()) if w and w not in STOP and not w.isdigit()]


def draft(root):
    root = Path(root)
    p = _load(root / PROFILE)
    pos = p.get('positioning') or {}
    roots = p.get('core_roots') or []
    roots = [roots] if isinstance(roots, str) else roots
    extra = _load(root / SUPPLEMENT).get('core_roots') or []
    entry = []
    for k in [*(pos.get('core_keywords') or []), p.get('core_keyword'), *roots, *extra]:
        k = ' '.join(str(k or '').lower().split())
        if k and re.fullmatch(r'[a-z0-9 .\-&]+', k) and k not in entry:
            entry.append(k)
    todo = []
    if len(entry) < 4:
        todo.append(f'entry_keywords 只找到 {len(entry)} 个，需要 4–5 个本品买家最常搜的英文词（可从 S1 关键词或 SIF 词表挑）')
    entry = entry[:5]
    counts = Counter(w for k in entry for w in set(_words(k)))
    shared = [w for w, n in counts.most_common() if n >= max(2, len(entry) // 2) and w not in CONTAINERS]
    containers = sorted({w for k in entry for w in _words(k) if w in CONTAINERS})
    if not shared:
        todo.append('form_tokens.required_any（产品主体词，如 screen / mesh）没能从核心词里自动找出，请补')
    if not containers:
        todo.append('form_tokens.container_any（形态 / 包装词，如 kit / roll / set）没能自动找出，请补')
    style = p.get('selected_style') or {}
    prices = [float(x) for x in re.findall(r'\d+(?:\.\d+)?', str(style.get('target_price_usd') or ''))]
    nodes = re.findall(r'node\s*(\d{4,})', str(pos.get('category') or '')) + [str(n) for n in p.get('target_node_ids') or []]
    if not prices:
        todo.append('target_price（本品目标售价，美元）没找到，请补')
    out = {'product_id': root.name, 'marketplace': p.get('marketplace') or 'US', 'entry_keywords': entry,
           'form_tokens': {'required_any': shared[:4], 'container_any': containers},
           'form_bridge_tokens': [w for w, _ in counts.most_common() if w not in shared and w not in CONTAINERS][:4],
           'target_price': round(sum(prices) / len(prices), 2) if prices else None, 'price_band': [0.33, 3.0],
           'target_node_ids': list(dict.fromkeys(nodes)),
           'substitute_tokens': [str(x).lower() for x in (p.get('excluded_roots') or [])],
           'variant_tokens': [], 'accessory_tokens': [], 'contains_goods_tokens': [],
           'own_brands': [b for b in [p.get('brand')] if b], 'own_parent_asins': [], 'target_asin': None,
           'to_confirm': todo,
           'note': '草稿：由 S1 画像生成，字段与 competitor_candidate_pool.py 一致；核对 entry_keywords 与 form_tokens 后另存为正式画像再生成候选池'}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--run', required=True)
    a = ap.parse_args(argv)
    out = draft(a.run)
    path = Path(a.run) / OUT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'path': OUT, 'to_confirm': out['to_confirm'], 'entry_keywords': out['entry_keywords'],
                      'form_tokens': out['form_tokens'], 'target_price': out['target_price']}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
