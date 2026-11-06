"""Course-case regression for difficulty_calc.py (课22 / 课48 / 课53 数字)."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import difficulty_calc as dc  # noqa: E402

HEADER = ["asin", "competing", "reviews", "monthly_sales", "organic_rank", "ad_keywords", "total_keywords", "price", "source"]


def write_csv(tmp_path: Path, rows: list[list]) -> Path:
    p = tmp_path / "competitors.csv"
    with p.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        w.writerows(rows)
    return p


def test_clear_makeup_pouch_is_hard(tmp_path: Path):
    """课22：30评均销 154 / 自然前5均销 2685 ≈ 5.7% → 困难。"""
    rows = [
        ["B0NEW00001", 1, 12, 154, "", "", "", 9.99, "course22"],
        ["B0NEW00002", 1, 28, 154, "", "", "", 10.99, "course22"],
        ["B0TOP00001", 0, 3600, 3316, 1, "", "", 7.64, "course22"],
        ["B0TOP00002", 0, 19700, 2900, 2, "", "", 9.99, "course22"],
        ["B0TOP00003", 0, 1100, 2685, 3, "", "", 13.99, "course22"],
        ["B0TOP00004", 0, 782, 2300, 4, "", "", 12.88, "course22"],
        ["B0TOP00005", 0, 918, 2224, 5, "", "", 11.99, "course22"],
    ]
    res = dc.assess(dc.load_rows(write_csv(tmp_path, rows)), keyword="small clear makeup pouch")
    assert res.a_avg == 154.0
    assert res.top5_avg == 2685.0
    assert res.difficulty == pytest.approx(5.74, abs=0.01)
    assert res.verdict_review == "困难"
    assert res.verdict_ad == "数据不足"
    assert "有条件" not in res.verdict_cross or "困难" in res.verdict_review


def test_dog_bandana_bulk_matches_course48_sheet(tmp_path: Path):
    """课48 一页式工作簿：30评 200 / 31-50 135 / 51-100 205 / 前5 482 → 41.49% / 28.01% / 42.53% 简单；
    广告效率 304/18=16.89、491/31=15.84、95/8=11.88、130/21=6.19。"""
    rows = [
        # 评论区间统计行（课48 表上半）
        ["B0R0000001", 1, 20, 200, "", "", "", 29, "course48"],
        ["B0R0000002", 1, 40, 135, "", "", "", 43, "course48"],
        ["B0R0000003", 1, 80, 205, "", "", "", 32, "course48"],
        # 广告效率表行（课48 表下半，独立四个产品；无评论数不进区间统计）
        ["B0A0000001", 1, "", 304, "", 18, 129, 29, "course48"],
        ["B0A0000002", 1, "", 491, "", 31, 140, 43, "course48"],
        ["B0A0000003", 1, "", 95, "", 8, 60, 32, "course48"],
        ["B0A0000004", 1, "", 130, "", 21, 95, 42, "course48"],
        ["B0T0000001", 0, 800, 482, 1, "", "", 24.99, "course48"],
        ["B0T0000002", 0, 600, 482, 2, "", "", 26.99, "course48"],
        ["B0T0000003", 0, 500, 482, 3, "", "", 22.99, "course48"],
        ["B0T0000004", 0, 400, 482, 4, "", "", 27.99, "course48"],
        ["B0T0000005", 0, 300, 482, 5, "", "", 25.99, "course48"],
    ]
    res = dc.assess(dc.load_rows(write_csv(tmp_path, rows)), keyword="dog bandana bulk")
    by_band = {b["band"]: b for b in res.bands}
    assert by_band["30评论"]["difficulty_pct"] == pytest.approx(41.49, abs=0.01)
    assert by_band["31-50评论"]["difficulty_pct"] == pytest.approx(28.01, abs=0.01)
    assert by_band["51-100评论"]["difficulty_pct"] == pytest.approx(42.53, abs=0.01)
    assert res.verdict_review == "简单"

    eff = {r["asin"]: r["efficiency"] for r in res.ad_rows}
    assert eff["B0A0000001"] == pytest.approx(16.89, abs=0.01)
    assert eff["B0A0000002"] == pytest.approx(15.84, abs=0.01)
    assert eff["B0A0000003"] == pytest.approx(11.88, abs=0.01)
    assert eff["B0A0000004"] == pytest.approx(6.19, abs=0.01)
    assert res.verdict_ad == "友好"
    assert res.verdict_cross == "可做"
    # 课34：保守值取 A 与「广告词最少参照」中较小者 → 30评均销 200 vs 广告词最少(8)的 95 → 95
    assert res.conservative_sales == 95.0


def test_packing_cubes_simple(tmp_path: Path):
    """课53：165 / 783 = 21.07% → 简单。"""
    rows = [
        ["B0P0000001", 1, 25, 165, "", "", "", 19.99, "course53"],
        ["B0Q0000001", 0, 5000, 783, 1, "", "", 24.99, "course53"],
        ["B0Q0000002", 0, 4000, 783, 2, "", "", 22.99, "course53"],
        ["B0Q0000003", 0, 3000, 783, 3, "", "", 21.99, "course53"],
        ["B0Q0000004", 0, 2000, 783, 4, "", "", 25.99, "course53"],
        ["B0Q0000005", 0, 1000, 783, 5, "", "", 23.99, "course53"],
    ]
    res = dc.assess(dc.load_rows(write_csv(tmp_path, rows)), keyword="packing cubes")
    assert res.difficulty == pytest.approx(21.07, abs=0.01)
    assert res.verdict_review == "简单"


def test_burn_ads_hard_cap(tmp_path: Path):
    """课23：Febambu 43/8/37 → 广告占比 86% 烧广告硬顶；多数竞品烧广告时广告法判硬顶。"""
    rows = [
        ["B0DJ8Z89YH", 1, 5, 41, "", 37, 43, 12.99, "course23"],
        ["B0BURN0002", 1, 9, 60, "", 50, 58, 11.99, "course23"],
        ["B0C23SGB2M", 1, 900, 3316, 1, 23, 255, 9.99, "course23"],
    ]
    res = dc.assess(dc.load_rows(write_csv(tmp_path, rows)))
    signals = {r["asin"]: r["signal"] for r in res.ad_rows}
    assert signals["B0DJ8Z89YH"] == "烧广告硬顶"
    assert signals["B0C23SGB2M"] == "自然主导"
    assert res.verdict_ad == "烧广告硬顶"


def test_yaml_roundtrip_has_required_keys(tmp_path: Path):
    rows = [
        ["B0P0000001", 1, 25, 165, "", 10, 100, 19.99, "x"],
        ["B0Q0000001", 0, 5000, 783, 1, "", "", 24.99, "x"],
    ]
    res = dc.assess(dc.load_rows(write_csv(tmp_path, rows)), keyword="k")
    y = dc.to_yaml(res)
    for key in ("review_band_method:", "ad_efficiency_method:", "cross_verdict:", "conservative_sales_reference:", "thresholds:"):
        assert key in y
    assert "\ufeff" not in y
