#!/usr/bin/env python3
"""Compute dual-method entry difficulty from a competitor CSV.

Method 1 (review-band):   difficulty = avg_sales(reviews<=30, competing) / avg_sales(top-5 organic)
Method 2 (ad-efficiency): efficiency = monthly_sales / ad_keywords; ad_share = ad_keywords / total_keywords

Usage:
    python difficulty_calc.py competitors.csv [--out difficulty_assessment.yaml] [--keyword "dog bandana bulk"]

CSV columns (header required, extra columns ignored):
    asin, competing, reviews, monthly_sales, organic_rank, ad_keywords, total_keywords, price, source

- competing: 1 = 相关且竞争（进分母/区间统计）, 0 = 相关不竞争 / 头部异形态（只用于 top-5 若其 organic_rank<=5）
- organic_rank: 自然排名，空则视为未上榜
- ad_keywords / total_keywords: 空则不参与广告效率法
Thresholds are the 2025-07 course values; calibrate per category.
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

REVIEW_BANDS = [
    ("30评论", 0, 30),
    ("31-50评论", 31, 50),
    ("51-100评论", 51, 100),
    ("101-200评论", 101, 200),
    ("201-500评论", 201, 500),
]
HARD_MAX = 0.10       # < 10%  困难
MODERATE_MAX = 0.15   # 10–15% 适中, > 15% 简单
EFFICIENCY_FRIENDLY = 6.0
EFFICIENCY_BAND_HIGH = 17.0
AD_SHARE_BURN = 0.80
TOP_N = 5


@dataclass
class Row:
    asin: str
    competing: bool
    reviews: Optional[int]
    monthly_sales: Optional[float]
    organic_rank: Optional[int]
    ad_keywords: Optional[int]
    total_keywords: Optional[int]
    price: Optional[float]
    source: str = ""

    @property
    def efficiency(self) -> Optional[float]:
        if self.monthly_sales is None or not self.ad_keywords:
            return None
        return self.monthly_sales / self.ad_keywords

    @property
    def ad_share(self) -> Optional[float]:
        if not self.ad_keywords or not self.total_keywords:
            return None
        return self.ad_keywords / self.total_keywords


@dataclass
class Result:
    keyword: str
    top5_avg: Optional[float]
    a_avg: Optional[float]
    difficulty: Optional[float]
    verdict_review: str
    bands: list = field(default_factory=list)
    ad_rows: list = field(default_factory=list)
    burn_share: Optional[float] = None
    median_efficiency: Optional[float] = None
    verdict_ad: str = ""
    verdict_cross: str = ""
    conservative_sales: Optional[float] = None
    notes: list = field(default_factory=list)


def _num(v: str) -> Optional[float]:
    v = (v or "").strip().replace(",", "").replace("$", "")
    if v == "" or v.lower() in {"na", "n/a", "-", "—", "none", "null"}:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _int(v: str) -> Optional[int]:
    n = _num(v)
    return None if n is None else int(round(n))


def load_rows(path: Path) -> list[Row]:
    rows: list[Row] = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        required = {"asin", "competing", "reviews", "monthly_sales"}
        missing = required - {c.strip().lower() for c in (reader.fieldnames or [])}
        if missing:
            raise SystemExit(f"CSV 缺列：{sorted(missing)}；需要至少 {sorted(required)}")
        for raw in reader:
            r = {k.strip().lower(): (v or "") for k, v in raw.items() if k}
            if not r.get("asin", "").strip():
                continue
            rows.append(
                Row(
                    asin=r["asin"].strip(),
                    competing=(r.get("competing", "1").strip() in {"1", "true", "True", "y", "Y", "是"}),
                    reviews=_int(r.get("reviews", "")),
                    monthly_sales=_num(r.get("monthly_sales", "")),
                    organic_rank=_int(r.get("organic_rank", "")),
                    ad_keywords=_int(r.get("ad_keywords", "")),
                    total_keywords=_int(r.get("total_keywords", "")),
                    price=_num(r.get("price", "")),
                    source=r.get("source", "").strip(),
                )
            )
    if not rows:
        raise SystemExit("CSV 没有有效行")
    return rows


def verdict_from_difficulty(d: Optional[float]) -> str:
    if d is None:
        return "数据不足"
    if d < HARD_MAX:
        return "困难"
    if d <= MODERATE_MAX:
        return "适中"
    return "简单"


def assess(rows: list[Row], keyword: str = "") -> Result:
    notes: list[str] = []

    ranked = sorted(
        [r for r in rows if r.organic_rank is not None and r.monthly_sales is not None],
        key=lambda r: r.organic_rank,
    )
    top = ranked[:TOP_N]
    top5_avg = statistics.mean(r.monthly_sales for r in top) if top else None
    if len(top) < TOP_N:
        notes.append(f"自然排名带销量的行只有 {len(top)} 个（需 {TOP_N}），B 值按现有行均值计算")

    competing = [r for r in rows if r.competing and r.reviews is not None and r.monthly_sales is not None]
    a_rows = [r for r in competing if r.reviews <= 30]
    a_avg = statistics.mean(r.monthly_sales for r in a_rows) if a_rows else None
    if not a_rows:
        notes.append("没有 30 评论以内的相似产品，A 值缺失，评论区间法无法判定")

    difficulty = (a_avg / top5_avg) if (a_avg is not None and top5_avg) else None
    verdict_review = verdict_from_difficulty(difficulty)

    bands = []
    for label, lo, hi in REVIEW_BANDS:
        in_band = [r for r in competing if lo <= r.reviews <= hi]
        avg = statistics.mean(r.monthly_sales for r in in_band) if in_band else None
        ratio = (avg / top5_avg) if (avg is not None and top5_avg) else None
        bands.append(
            {
                "band": label,
                "n": len(in_band),
                "avg_sales": None if avg is None else round(avg, 1),
                "difficulty_pct": None if ratio is None else round(ratio * 100, 2),
                "verdict": verdict_from_difficulty(ratio) if in_band else "—",
            }
        )

    ad_rows = []
    effs: list[float] = []
    burn_flags: list[bool] = []
    for r in rows:
        eff = r.efficiency
        share = r.ad_share
        if eff is None and share is None:
            continue
        if eff is not None:
            effs.append(eff)
        if share is not None:
            burn_flags.append(share > AD_SHARE_BURN)
        if share is not None and share > AD_SHARE_BURN:
            signal = "烧广告硬顶"
        elif share is not None and share < 0.20 and (r.monthly_sales or 0) > 0:
            signal = "自然主导"
        elif eff is not None and eff >= EFFICIENCY_FRIENDLY:
            signal = "友好"
        else:
            signal = "谨慎"
        ad_rows.append(
            {
                "asin": r.asin,
                "monthly_sales": r.monthly_sales,
                "ad_keywords": r.ad_keywords,
                "total_keywords": r.total_keywords,
                "efficiency": None if eff is None else round(eff, 2),
                "ad_share_pct": None if share is None else round(share * 100, 1),
                "signal": signal,
                "price": r.price,
            }
        )

    burn_share = (sum(burn_flags) / len(burn_flags)) if burn_flags else None
    median_eff = statistics.median(effs) if effs else None
    if not ad_rows:
        verdict_ad = "数据不足"
        notes.append("没有广告词数据，广告效率法无法判定")
    elif burn_share is not None and burn_share >= 0.5:
        verdict_ad = "烧广告硬顶"
    elif median_eff is not None and median_eff >= EFFICIENCY_FRIENDLY:
        verdict_ad = "友好"
    else:
        verdict_ad = "谨慎"

    cross = cross_verdict(verdict_review, verdict_ad)

    conservative = None
    candidates = [v for v in (a_avg, _min_ad_reference(rows)) if v is not None]
    if candidates:
        conservative = min(candidates)

    return Result(
        keyword=keyword,
        top5_avg=None if top5_avg is None else round(top5_avg, 1),
        a_avg=None if a_avg is None else round(a_avg, 1),
        difficulty=None if difficulty is None else round(difficulty * 100, 2),
        verdict_review=verdict_review,
        bands=bands,
        ad_rows=ad_rows,
        burn_share=None if burn_share is None else round(burn_share * 100, 1),
        median_efficiency=None if median_eff is None else round(median_eff, 2),
        verdict_ad=verdict_ad,
        verdict_cross=cross,
        conservative_sales=None if conservative is None else round(conservative, 1),
        notes=notes,
    )


def _min_ad_reference(rows: list[Row]) -> Optional[float]:
    """课34：评论/外观/价格接近且广告词最少的参照品销量。"""
    cands = [r for r in rows if r.competing and r.ad_keywords is not None and r.monthly_sales is not None]
    if not cands:
        return None
    least_ads = min(cands, key=lambda r: r.ad_keywords)
    return least_ads.monthly_sales


def cross_verdict(review: str, ad: str) -> str:
    if review == "数据不足" and ad == "数据不足":
        return "数据不足，禁止决策"
    review_ok = review in {"简单", "适中"}
    ad_ok = ad in {"友好", "自然主导"}
    if review_ok and ad_ok:
        return "可做"
    if review_ok and ad == "数据不足":
        return "可做（仅评论区间法通过，补广告词数据后复核）"
    if review == "困难" and ad_ok:
        return "有条件可做（评论区间困难，广告效率友好；必须写差异化）"
    if review_ok and ad == "烧广告硬顶":
        return "谨慎（评论区间通过，但市场靠广告顶；销量可能买来的）"
    if review == "困难" and ad == "烧广告硬顶":
        return "放弃"
    if review == "数据不足" and ad_ok:
        return "有条件可做（仅广告效率法通过，补评论区间数据后复核）"
    return "谨慎"


def _yaml_scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v) if isinstance(v, float) else str(v)
    s = str(v).replace('"', '\\"')
    return f'"{s}"'


def to_yaml(res: Result) -> str:
    lines = [
        "schema_version: \"1.0\"",
        "generated_by: \"amazon-competition-difficulty-assessor/scripts/difficulty_calc.py\"",
        f"keyword: {_yaml_scalar(res.keyword)}",
        "thresholds:",
        f"  hard_below_pct: {HARD_MAX * 100:.0f}",
        f"  moderate_upto_pct: {MODERATE_MAX * 100:.0f}",
        f"  efficiency_friendly_min: {EFFICIENCY_FRIENDLY}",
        f"  efficiency_band_high: {EFFICIENCY_BAND_HIGH}",
        f"  ad_share_burn_pct: {AD_SHARE_BURN * 100:.0f}",
        "  note: \"2025-07 课程经验值，按类目校准\"",
        "review_band_method:",
        f"  top5_avg_sales: {_yaml_scalar(res.top5_avg)}",
        f"  a_avg_sales_reviews_le_30: {_yaml_scalar(res.a_avg)}",
        f"  difficulty_pct: {_yaml_scalar(res.difficulty)}",
        f"  verdict: {_yaml_scalar(res.verdict_review)}",
        "  bands:",
    ]
    for b in res.bands:
        lines.append(f"    - band: {_yaml_scalar(b['band'])}")
        lines.append(f"      n: {b['n']}")
        lines.append(f"      avg_sales: {_yaml_scalar(b['avg_sales'])}")
        lines.append(f"      difficulty_pct: {_yaml_scalar(b['difficulty_pct'])}")
        lines.append(f"      verdict: {_yaml_scalar(b['verdict'])}")
    lines += [
        "ad_efficiency_method:",
        f"  median_efficiency: {_yaml_scalar(res.median_efficiency)}",
        f"  burn_share_pct: {_yaml_scalar(res.burn_share)}",
        f"  verdict: {_yaml_scalar(res.verdict_ad)}",
        "  rows:",
    ]
    for r in res.ad_rows:
        lines.append(f"    - asin: {_yaml_scalar(r['asin'])}")
        for k in ("monthly_sales", "ad_keywords", "total_keywords", "efficiency", "ad_share_pct", "signal", "price"):
            lines.append(f"      {k}: {_yaml_scalar(r[k])}")
    lines += [
        f"cross_verdict: {_yaml_scalar(res.verdict_cross)}",
        f"conservative_sales_reference: {_yaml_scalar(res.conservative_sales)}",
        "notes:",
    ]
    lines += [f"  - {_yaml_scalar(n)}" for n in res.notes] or ["  []"]
    if not res.notes:
        lines[-1] = "notes: []"
    return "\n".join(lines) + "\n"


def to_markdown(res: Result) -> str:
    out = [f"# 运营难度双法 · {res.keyword or '(未指定核心词)'}", ""]
    out.append("## 评论区间法")
    out.append("")
    out.append("| 区间 | 样本数 | 平均销量 | 运营难度% | 判定 |")
    out.append("|---|---|---|---|---|")
    for b in res.bands:
        out.append(
            f"| {b['band']} | {b['n']} | {b['avg_sales'] if b['avg_sales'] is not None else '—'} | "
            f"{b['difficulty_pct'] if b['difficulty_pct'] is not None else '—'} | {b['verdict']} |"
        )
    out.append(f"| 自然前{TOP_N}平均销量 | — | {res.top5_avg if res.top5_avg is not None else '—'} | — | — |")
    out.append("")
    out.append(f"主判定（30 评以内）：A={res.a_avg} / B={res.top5_avg} → **{res.difficulty}% → {res.verdict_review}**")
    out.append("")
    out.append("## 广告效率法")
    out.append("")
    out.append("| 竞品 | 月销 | 广告词 | 全部流量词 | 效率 | 广告占比% | 信号 |")
    out.append("|---|---|---|---|---|---|---|")
    for r in res.ad_rows:
        out.append(
            f"| {r['asin']} | {r['monthly_sales']} | {r['ad_keywords']} | {r['total_keywords']} | "
            f"{r['efficiency'] if r['efficiency'] is not None else '—'} | "
            f"{r['ad_share_pct'] if r['ad_share_pct'] is not None else '—'} | {r['signal']} |"
        )
    out.append("")
    out.append(f"中位效率 {res.median_efficiency}，烧广告占比 {res.burn_share}% → **{res.verdict_ad}**")
    out.append("")
    out.append(f"## 交叉裁定：**{res.verdict_cross}**")
    out.append("")
    out.append(f"保守销量参照（取两法较小）：{res.conservative_sales}")
    if res.notes:
        out.append("")
        out.append("## 备注")
        out += [f"- {n}" for n in res.notes]
    out.append("")
    out.append("阈值来自 2025-07 课程经验值，请按当前类目校准。")
    return "\n".join(out) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", type=Path)
    ap.add_argument("--keyword", default="", help="核心关键词，写进产物")
    ap.add_argument("--out", type=Path, default=None, help="difficulty_assessment.yaml 输出路径")
    ap.add_argument("--md", type=Path, default=None, help="可选：同时写 Markdown 表")
    args = ap.parse_args(argv)

    res = assess(load_rows(args.csv), keyword=args.keyword)
    md = to_markdown(res)
    sys.stdout.write(md)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(to_yaml(res), encoding="utf-8")
        sys.stdout.write(f"\nYAML -> {args.out}\n")
    if args.md:
        args.md.parent.mkdir(parents=True, exist_ok=True)
        args.md.write_text(md, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
