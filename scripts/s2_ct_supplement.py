"""S2 自己的 CT 补充层（S2-R2）：补人群 / 场景 / 痛点 / 用途，不改 S1 已封存的 03_product_ct.yaml。

只补 S1 里空着或来源为 missing 的节点；S1 已有来源的节点原样保留（补充层不能覆盖已确认事实）。
所有读 CT 的地方都用 effective_ct()，第 5 步 context.product_ct 也用 `show` 的输出，几处指纹一致。

    python s2_ct_supplement.py add  --run <产品根> --field scenarios --value "纱门、庭院推拉门、房车" \\
        --en "screen door,patio door,rv screen" --source voc --evidence "S1 评论：... 原话"
    python s2_ct_supplement.py roots --run <产品根> --value "window screen,screen mesh,screen repair kit"   # 补短核心词根
    python s2_ct_supplement.py show --run <产品根>        # 合并后的 CT（JSON），给第 5 步 context.product_ct
"""
import argparse
import copy
import datetime as dt
import json
import sys
from pathlib import Path

import yaml

S1_CT = 'S1_选品板块/1688-product-detail-scraper/03_product_ct.yaml'
SUPPLEMENT = 'S2_文案板块/_补充/ct_supplement.yaml'
FIELDS = {'uses': '用途', 'audiences': '人群', 'scenarios': '场景', 'pain_points': '痛点'}
SOURCES = {'voc': 'voc_quoted', 'user': 'user_attested', 'listing': 'page_extracted', 'assumed': 'ai_inferred'}


def _load(path):
    path = Path(path)
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding='utf-8-sig'))
    return data if isinstance(data, dict) else {}


def _empty(node):
    return (not isinstance(node, dict) or not node.get('value')
            or str(node.get('source_status', node.get('status', 'missing'))) == 'missing')


def merge(ct_doc, supplement):
    """S1 CT 文档（可带外层 ct:）+ 补充层 → 合并后的同形文档；只填空着 / missing 的节点。"""
    doc = copy.deepcopy(ct_doc or {})
    inner = doc.get('ct') if isinstance(doc.get('ct'), dict) else doc
    applied = []
    for key, node in ((supplement or {}).get('fields') or {}).items():
        if key in FIELDS and isinstance(node, dict) and node.get('value') and _empty(inner.get(key)):
            inner[key] = {**(inner.get(key) if isinstance(inner.get(key), dict) else {}), **node}
            applied.append(key)
    if applied:
        doc.setdefault('_supplement', {})['applied'] = applied
    return doc


def effective_ct(root):
    root = Path(root)
    return merge(_load(root / S1_CT), _load(root / SUPPLEMENT))


def add(root, field, value, en_terms, source, evidence, now=None):
    if field not in FIELDS:
        raise ValueError('只能补：' + '、'.join(f'{k}（{v}）' for k, v in FIELDS.items()))
    if source not in SOURCES:
        raise ValueError('来源只能是：voc（评论原话）/ user（你确认）/ listing（页面）/ assumed（假设，待核）')
    if not str(value).strip():
        raise ValueError('value 不能为空')
    if source in ('voc', 'listing') and not str(evidence or '').strip():
        raise ValueError('来源是评论或页面时，--evidence 要写原话或出处')
    root = Path(root)
    s1 = _load(root / S1_CT)
    inner = s1.get('ct') if isinstance(s1.get('ct'), dict) else s1
    if not _empty(inner.get(field)):
        raise ValueError(f'S1 的 CT 已有已确认来源的{FIELDS[field]}，补充层不覆盖；要改请在 S1 reopen 对应步骤')
    path = root / SUPPLEMENT
    doc = _load(path)
    doc.setdefault('schema_version', 1)
    doc.setdefault('fields', {})[field] = {
        'value': str(value).strip(), 'en_terms': [t.strip() for t in str(en_terms or '').split(',') if t.strip()],
        'source_status': SOURCES[source], 'source': SUPPLEMENT + '#' + field, 'evidence': str(evidence or '').strip(),
        'added_at': (now or dt.datetime.now(dt.timezone.utc)).isoformat(timespec='seconds'), 'note': 'S2 补充层，未改 S1 文件；待核项在审阅区列出'}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding='utf-8')
    return {'ok': True, 'field': field, 'label': FIELDS[field], 'path': SUPPLEMENT,
            'next': '重跑第 4 步词库清洗（sif_clean.py --run）和第 5 步映射，用 show 的输出作 context.product_ct'}


def add_roots(root, value):
    """S1 画像只给了长词根时补 2–4 词的短核心词根（英文），第 4 步词库清洗会一起用来匹配。"""
    roots = [' '.join(r.lower().split()) for r in str(value or '').split(',') if r.strip()]
    bad = [r for r in roots if not 1 <= len(r.split()) <= 4 or not all(w.isascii() for w in r.split())]
    if not roots or bad:
        raise ValueError('核心词根写英文、1–4 个词、逗号分隔' + ('；这些不合格：' + '、'.join(bad) if bad else ''))
    path = Path(root) / SUPPLEMENT
    doc = _load(path)
    doc.setdefault('schema_version', 1)
    doc['core_roots'] = list(dict.fromkeys((doc.get('core_roots') or []) + roots))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding='utf-8')
    return {'ok': True, 'core_roots': doc['core_roots'], 'next': '重跑第 4 步词库清洗（sif_clean.py --run）'}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('action', choices=['add', 'roots', 'show'])
    p.add_argument('--run', required=True)
    p.add_argument('--field')
    p.add_argument('--value')
    p.add_argument('--en', default='')
    p.add_argument('--source', default='voc')
    p.add_argument('--evidence', default='')
    a = p.parse_args(argv)
    try:
        out = (add(a.run, a.field, a.value, a.en, a.source, a.evidence) if a.action == 'add'
               else add_roots(a.run, a.value) if a.action == 'roots' else effective_ct(a.run))
    except ValueError as exc:
        print(json.dumps({'ok': False, 'message': str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == '__main__':
    sys.exit(main())
