"""关键词用途三档（S2-R10）：不再手改 review_decisions.json。

    normal        本品正常词 → 保留，可进文案
    pending_attr  本品词，但带一个还没核实的属性（如尺寸、材质）→ 保留，照写并在审阅区标待核
    substitute    替代品 / 非本品（如 magnetic、retractable 整门）→ 排除，不进文案

    python keyword_use.py --run <产品根> --as substitute --contains magnetic,retractable --reason "磁吸/伸缩整门是替代品"   # 先预览
    python keyword_use.py --run <产品根> --as normal --term "fiberglass screen mesh" --reason "本品网纱材质" --apply          # 写入
写入后重跑 sif_clean.py --run。同名词覆盖旧裁决；品牌 / IP / 声明受限的词不能保留（清洗器也会拒）。
"""
import argparse
import json
import sys
from pathlib import Path

import yaml

OUT = 'S2_文案板块/amazon-sif-keyword-research'
USES = {'normal': ('retained', 'direct', ''), 'pending_attr': ('retained', 'direct', '属性待核：'),
        'substitute': ('excluded', 'substitute', '')}


def records(root):
    path = Path(root) / OUT / 'keyword_package.yaml'
    if not path.is_file():
        raise ValueError('还没有词包：先跑第 4 步 sif_clean.py --run')
    return (yaml.safe_load(path.read_text(encoding='utf-8-sig')) or {}).get('records') or []


def select(recs, terms=(), contains=()):
    norm = lambda s: ' '.join(str(s).casefold().split())
    by = {norm(r.get('normalized_keyword') or r.get('keyword')): r for r in recs}
    picked, missing = [], []
    for t in terms:
        r = by.get(norm(t))
        (picked if r else missing).append(r or t)
    for word in contains:
        w = norm(word)
        picked += [r for k, r in by.items() if w and (' ' + w + ' ') in (' ' + k + ' ') and r not in picked]
    return picked, missing


def plan(root, use, terms=(), contains=(), reason=''):
    if use not in USES:
        raise ValueError('--as 只能是 normal / pending_attr / substitute')
    if not str(reason).strip():
        raise ValueError('要写理由（--reason），裁决会带进审阅区')
    picked, missing = select(records(root), terms, contains)
    disposition, match, prefix = USES[use]
    rows, refused = {}, []
    for r in picked:
        key = r['normalized_keyword']
        if disposition == 'retained' and (r.get('brand_hits') or r.get('compliance_hits') or r.get('claim_restricted')):
            refused.append({'keyword': key, 'why': '含品牌 / IP 或声明受限，不能保留'})
            continue
        rows[key] = {'disposition': disposition, 'product_match': match, 'reason': prefix + reason.strip(), 'by': 'user'}
    return {'use': use, 'decisions': rows, 'not_found': missing, 'refused': refused,
            'weekly_volume': sum(_volume(r) for r in picked if r['normalized_keyword'] in rows)}


def _volume(r):
    v = (r.get('metrics') or {}).get('weekly_search_volume')
    v = v.get('value') if isinstance(v, dict) else v
    return v if isinstance(v, (int, float)) else 0


def apply(root, result):
    path = Path(root) / OUT / 'review_decisions.json'
    doc = json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else {}
    doc.setdefault('keywords', {}).update(result['decisions'])
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return {'written': len(result['decisions']), 'path': path.relative_to(Path(root)).as_posix(), 'next': '重跑 sif_clean.py --run'}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--run', required=True)
    p.add_argument('--as', dest='use', required=True, choices=sorted(USES))
    p.add_argument('--term', action='append', default=[])
    p.add_argument('--contains', default='', help='逗号分隔：词里含这些词根的都选上（先预览再 --apply）')
    p.add_argument('--reason', default='')
    p.add_argument('--apply', action='store_true')
    a = p.parse_args(argv)
    try:
        result = plan(a.run, a.use, a.term, [w for w in a.contains.split(',') if w.strip()], a.reason)
        out = {**result, 'decisions': sorted(result['decisions']), 'applied': False}
        if a.apply and result['decisions']:
            out.update(apply(a.run, result), applied=True)
    except ValueError as exc:
        print(json.dumps({'ok': False, 'message': str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
