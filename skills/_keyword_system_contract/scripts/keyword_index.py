#!/usr/bin/env python3
"""S2 词库 → S4 广告用的小索引（ad_keyword_index.json / .md）。

keyword_package.yaml 是全量审计包（上万条记录、上百 MB），扩展名是 yaml、内容是 JSON。
S4 只需要「进了投放池的词 + 否定词 + 埋在 Listing 哪里」，这里一次抽出来，之后秒读。

  python keyword_index.py --package <keyword_package.yaml>            # 生成/刷新（已是最新则跳过）
  python keyword_index.py --package <…> --query --pool phrase_feature # 列一个池
  python keyword_index.py --package <…> --query --words "a|b"         # 查几个词（池外的词回全量包找）
  python keyword_index.py --package <…> --query --negatives           # 否定词
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

INDEX_VERSION = "ad-keyword-index-3"
INDEX_JSON = "ad_keyword_index.json"
INDEX_MD = "ad_keyword_index.md"
METRICS = ("weekly_search_volume", "aba_rank", "cvr_24h", "cpc_low", "cpc_mid", "cpc_high")
SHORT = {"weekly_search_volume": "sv", "aba_rank": "aba", "cvr_24h": "cvr",
         "cpc_low": "cpc_low", "cpc_mid": "cpc", "cpc_high": "cpc_high"}
AD_POOL_ORDER = ("exact_p1", "exact_p2", "phrase_feature", "phrase_scenario", "broad_discovery", "observe")
POOL_LABELS = {"exact_p1": "精准 P1", "exact_p2": "精准 P2", "exact": "精准（P1+P2）",
               "phrase_feature": "词组 · 功能", "phrase_scenario": "词组 · 场景",
               "broad_discovery": "广泛 · 拓词", "observe": "观察"}
MD_ROWS = 40


def load_package(path: str | Path) -> Dict[str, Any]:
    """JSON 优先（0.6 秒）；真 YAML 才走 C 解析器（纯 Python safe_load 读上百 MB 要 80 秒以上）。"""
    text = Path(path).read_text(encoding="utf-8-sig")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        import yaml  # type: ignore
        value = yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: 词包顶层必须是 object")
    return value


def _stamp(path: Path) -> Dict[str, int]:
    st = path.stat()
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


def _metric(record: Dict[str, Any], name: str) -> Any:
    item = (record.get("metrics") or {}).get(name)
    return item.get("value") if isinstance(item, dict) else item


def _row(record: Dict[str, Any], listing: List[str], pools: List[str]) -> Dict[str, Any]:
    match = record.get("product_match") or {}
    row = {"id": record.get("keyword_id"), "keyword": record.get("keyword"),
           "cn": record.get("cn_translation") or ""}
    for name in METRICS:
        row[SHORT[name]] = _metric(record, name)
    row.update({
        "match": match.get("status") if isinstance(match, dict) else match,
        "type": record.get("primary_type") or "",
        "modifiers": list(record.get("unexplained_modifiers") or []),
        "claim_restricted": bool(record.get("claim_restricted")),
        "brand_hits": list(record.get("brand_hits") or []),
        "listing": listing,
        "pools": pools,
    })
    return row


def build_index(package: Dict[str, Any], package_path: Path) -> Dict[str, Any]:
    if not isinstance(package.get('records'), list) or not isinstance(package.get('pools'), dict):
        raise ValueError('旧格式，请重建或迁移：需要 records 列表与 pools 对象；不会沿用旧否定词或出价结论')
    if any(not isinstance(r, dict) or not r.get('keyword_id') or not r.get('keyword') for r in package['records']):
        raise ValueError('词包 records 缺 keyword_id 或 keyword，请重建或迁移')
    if any(not isinstance(v, list) for v in package['pools'].values()):
        raise ValueError('词包 pools 的每个池必须是关键词 ID 列表，请重建或迁移')
    records = {r.get("keyword_id"): r for r in package.get("records") or [] if isinstance(r, dict)}
    ad_pools = {k: list(v or []) for k, v in (package.get("pools") or {}).items()}
    listing_pools = {k: list(v or []) for k, v in (package.get("listing_pools") or {}).items()}
    if any(not isinstance(kid, str) or kid not in records
           for ids in list(ad_pools.values()) + list(listing_pools.values()) for kid in ids):
        raise ValueError('旧格式，请重建或迁移：词池必须引用 records 中有效的 keyword_id，不能使用词文本或悬空 ID；不会沿用旧否定词或出价结论')
    member: Dict[str, List[str]] = {}
    for name, ids in listing_pools.items():
        for kid in ids:
            member.setdefault(kid, []).append(name)
    in_pools: Dict[str, List[str]] = {}
    for name, ids in ad_pools.items():
        for kid in ids:
            in_pools.setdefault(kid, []).append(name)
    words: Dict[str, Dict[str, Any]] = {}
    missing: List[str] = []
    for kid in list(in_pools) + [k for k in member if k not in in_pools]:
        record = records.get(kid)
        if record is None:
            missing.append(kid)
            continue
        words[kid] = _row(record, member.get(kid, []), in_pools.get(kid, []))
    negatives_by_id = {r.get("negative_id"): r for r in package.get("negative_records") or [] if isinstance(r, dict)}
    negatives = []
    for pool, ids in (package.get("negative_pools") or {}).items():
        for nid in ids or []:
            r = negatives_by_id.get(nid) or {}
            negatives.append({"negative_id": nid, "term": r.get("term"), "match_type": r.get("match_type") or pool,
                              "status": r.get("status"), "reason": r.get("reason"), "hits": r.get("hits"),
                              "scope": r.get("scope"), "pool": pool})
    asins_by_id = {r.get("asin_id"): r for r in package.get("asin_records") or [] if isinstance(r, dict)}
    asin_pools = {pool: [{"asin_id": aid, "asin": (asins_by_id.get(aid) or {}).get("asin")} for aid in ids or []]
                  for pool, ids in (package.get("asin_pools") or {}).items()}
    compliance = package.get("compliance") or {}
    return {
        "index_version": INDEX_VERSION,
        "source": {"path": package_path.name, **_stamp(package_path),
                   "package_id": package.get("package_id"), "package_version": package.get("package_version"),
                   "package_hash": package.get("package_hash")},
        "run_id": package.get("run_id"), "run_mode": package.get("run_mode"),
        "marketplace": package.get("marketplace"), "product_id": package.get("product_id"),
        "downstream_eligibility": package.get("downstream_eligibility"),
        "compliance_keys": sorted(compliance) if isinstance(compliance, dict) else [],
        "counts": {"records": len(records), "indexed_words": len(words),
                   "ad_pools": {k: len(v) for k, v in ad_pools.items()},
                   "listing_pools": {k: len(v) for k, v in listing_pools.items()},
                   "negatives": len(negatives), "missing_ids": len(missing)},
        "missing_ids": missing[:50],
        "ad_pools": ad_pools, "listing_pools": listing_pools,
        "negatives": negatives, "asin_pools": asin_pools,
        "words": words,
    }


def _num(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _sort_key(row: Dict[str, Any]):
    sv = row.get("sv")
    return -(sv if isinstance(sv, (int, float)) else -1)


def render_md(index: Dict[str, Any]) -> str:
    src = index["source"]
    lines = [f"# S4 广告词库索引（{index.get('product_id') or ''} · {index.get('marketplace') or ''}）", "",
             f"来源：`{src['path']}` · package v{src.get('package_version')} · `{src.get('package_hash')}`",
             f"全量 {index['counts']['records']} 条，进池/埋词 {index['counts']['indexed_words']} 条。"
             f"广告只用这里的词；查单个词：`keyword_index.py --package … --query --words \"a|b\"`。",
             "投前看「未解释修饰词」和中文：材质、款式、尺寸与本品不符的词（如本品不是竹制却有 bamboo）不投，并回报 S2 修词包。", ""]
    words = index["words"]
    pools = index["ad_pools"]
    for name in [p for p in AD_POOL_ORDER if p in pools] + [p for p in pools if p not in AD_POOL_ORDER and p != "exact"]:
        rows = sorted((words[k] for k in pools[name] if k in words), key=_sort_key)
        lines += [f"## {POOL_LABELS.get(name, name)} `{name}` · {len(rows)} 词", "",
                  "| 词 | 中文 | 周搜索 | ABA | CPC | CVR | 匹配 | 未解释修饰词 | Listing |", "|---|---|---|---|---|---|---|---|---|"]
        for r in rows[:MD_ROWS]:
            lines.append(f"| {r['keyword']} | {r['cn']} | {_num(r['sv'])} | {_num(r['aba'])} | {_num(r['cpc'])} | "
                         f"{_num(r['cvr'])} | {r['match'] or '—'} | {', '.join(r['modifiers']) or '—'} | "
                         f"{', '.join(r['listing']) or '—'} |")
        if len(rows) > MD_ROWS:
            lines.append(f"| …其余 {len(rows) - MD_ROWS} 词见 `{INDEX_JSON}` 或 `--query --pool {name}` | | | | | | | | |")
        lines.append("")
    negatives = index["negatives"]
    lines += [f"## 否定词 · {len(negatives)} 条", "", "| 否定词 | 匹配 | 状态 | 原因 | 命中 |", "|---|---|---|---|---|"]
    lines += [f"| {n['term']} | {n['match_type']} | {n['status'] or '—'} | {n['reason'] or '—'} | {_num(n['hits'])} |"
              for n in negatives]
    asin = {k: v for k, v in index["asin_pools"].items() if v}
    if asin:
        lines += ["", "## ASIN 定向", ""] + [f"- {k}：{', '.join(a['asin'] or a['asin_id'] for a in v)}" for k, v in asin.items()]
    return "\n".join(lines) + "\n"


def index_paths(package_path: Path) -> tuple[Path, Path]:
    return package_path.parent / INDEX_JSON, package_path.parent / INDEX_MD


def is_fresh(package_path: Path) -> bool:
    target, _ = index_paths(package_path)
    try:
        current = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    src = current.get("source") or {}
    return current.get("index_version") == INDEX_VERSION and all(
        src.get(k) == v for k, v in _stamp(package_path).items())


def ensure_index(package_path: str | Path, package: Dict[str, Any] | None = None, force: bool = False) -> Dict[str, Any]:
    package_path = Path(package_path)
    target, md = index_paths(package_path)
    if not force and package is None and is_fresh(package_path):
        return json.loads(target.read_text(encoding="utf-8"))
    index = build_index(package if package is not None else load_package(package_path), package_path)
    target.write_text(json.dumps(index, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    md.write_text(render_md(index), encoding="utf-8")
    return index


def query(index: Dict[str, Any], package_path: Path, args: argparse.Namespace) -> Dict[str, Any]:
    words = index["words"]
    if args.negatives:
        return {"negatives": index["negatives"]}
    if args.pool:
        ids = index["ad_pools"].get(args.pool) or index["listing_pools"].get(args.pool)
        if ids is None:
            return {"error": f"没有这个池：{args.pool}", "pools": sorted(index["ad_pools"]) + sorted(index["listing_pools"])}
        return {"pool": args.pool, "words": [words[k] for k in ids if k in words]}
    wanted = [w.strip() for w in (args.words or "").split("|") if w.strip()]
    by_text = {str(r["keyword"]).casefold(): r for r in words.values()}
    found, outside = [], []
    for w in wanted:
        row = words.get(w) or by_text.get(w.casefold())
        (found.append(row) if row else outside.append(w))
    if outside:
        package = load_package(package_path)
        lookup = {str(r.get("keyword") or "").casefold(): r for r in package.get("records") or []}
        lookup.update({r.get("keyword_id"): r for r in package.get("records") or []})
        for w in outside:
            record = lookup.get(w) or lookup.get(w.casefold())
            found.append({**_row(record, [], []), "disposition": record.get("disposition"),
                          "retention_reason": (record.get("retention_reason") or {}).get("text")}
                         if record else {"keyword": w, "status": "not_in_package"})
    return {"words": found}


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--package", required=True)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--query", action="store_true")
    ap.add_argument("--pool")
    ap.add_argument("--words")
    ap.add_argument("--negatives", action="store_true")
    args = ap.parse_args(argv)
    package_path = Path(args.package)
    if not package_path.is_file():
        print(json.dumps({"status": "missing", "package": str(package_path)}, ensure_ascii=False))
        return 2
    try:
        index = ensure_index(package_path, force=args.force)
    except (ValueError, TypeError, OSError) as error:
        print(json.dumps({'status': 'invalid_package', 'error': str(error), 'package': str(package_path)}, ensure_ascii=False))
        return 2
    if args.query:
        print(json.dumps(query(index, package_path, args), ensure_ascii=False, indent=1))
        return 0
    json_path, md_path = index_paths(package_path)
    print(json.dumps({"status": "ok" if index['counts']['indexed_words'] else "empty", "index": str(json_path), "md": str(md_path), **index["counts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
