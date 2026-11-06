#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
产品色彩审计 v1.0
====================================================================
测同一套图里产品主色有没有漂，超阈值就拦下。

为什么需要
--------------------------------------------------------------------
每张图是模型独立生成的，产品颜色会各画各的。实测（2026-08-06 宠物雨衣）：
同一件黄色雨衣，02 是饱和亮黄，03 变成发灰的浅黄，04 又回到饱和黄。
买家在图库里翻，看到的是「到底哪个是实物颜色」—— 这比字体不一致更伤转化，
而且**肉眼一张张看很难发现**，并排才看得出来。

叠字两层分离解决不了这个 —— 那治的是文字层。这里治产品层。

怎么测
--------------------------------------------------------------------
在 CIELab 空间按**色度 (a,b) 匹配、忽略明度 L**：
同一件黄雨衣的受光面和阴影面 L 差很多，但 a/b 基本一致。
只看 a/b 才能把「打光不同」和「颜色画错了」区分开 —— 前者正常，后者是缺陷。

指标：
  Δab   产品主色相对基准的色度偏移。这是核心指标
  ΔL    明度偏移，仅供参考（打光差异会让它偏大，不作为判定依据）
  跨图极差  一套图里 Δab 的最大差值 —— **买家实际感知到的就是这个**

判定（Δab 近似等价于 ΔE 的色度分量）：
  < 2   基本看不出
  2-5   并排能看出
  > 5   明显不一致

只依赖 pillow + pyyaml，不需要 numpy —— 学员机器上直接能跑。

用法
--------------------------------------------------------------------
    python 产品色彩审计.py {产品根}/S3_视觉板块/amazon-listing-image-designer/overlay_tasks.yaml
    python 产品色彩审计.py ... --fix        # 轻微漂移就地校正（大漂移拒绝，见下）
    python 产品色彩审计.py ... --report 色彩审计.yaml

退出码：0 通过｜1 有图超阈值｜2 配置问题

作者：坚哥｜跨境者说干货 · 星空的跨境圈子｜Codex亚马逊实战
"""
from __future__ import annotations

import argparse
import math
import sys
from datetime import datetime
from pathlib import Path

try:
    import yaml
    from PIL import Image
except ImportError as e:                                    # pragma: no cover
    sys.exit(f"[缺依赖] {e}\n  请先安装：pip install pillow pyyaml")

ANALYZE_WIDTH = 320          # 分析前降采样宽度；测颜色够用，速度快很多
MIN_MATCH_RATIO = 0.005      # 匹配像素占比下限，低于此说明没找到产品


def pixels(im):
    """跨 Pillow 版本取像素。getdata() 在 Pillow 14（2027-10）移除，
    新版用 get_flattened_data()；学员机器上版本不一，两条路都留着。"""
    new = getattr(im, "get_flattened_data", None)
    return list(new()) if new else list(im.getdata())


# ------------------------------------------------------- 色彩空间

def _srgb_to_linear(v: float) -> float:
    v /= 255.0
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _f(t: float) -> float:
    return t ** (1 / 3) if t > 0.008856 else (7.787 * t + 16 / 116)


def rgb_to_lab(rgb) -> tuple[float, float, float]:
    """sRGB → CIELab（D65 白点）。"""
    r, g, b = (_srgb_to_linear(c) for c in rgb[:3])
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 1.00000
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
    fx, fy, fz = _f(x), _f(y), _f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def lab_to_rgb(lab) -> tuple[int, int, int]:
    L, a, bb = lab
    fy = (L + 16) / 116
    fx, fz = fy + a / 500, fy - bb / 200

    def inv(t):
        return t ** 3 if t ** 3 > 0.008856 else (t - 16 / 116) / 7.787
    x, y, z = inv(fx) * 0.95047, inv(fy) * 1.00000, inv(fz) * 1.08883
    r = 3.2406 * x - 1.5372 * y - 0.4986 * z
    g = -0.9689 * x + 1.8758 * y + 0.0415 * z
    b = 0.0557 * x - 0.2040 * y + 1.0570 * z

    def enc(v):
        v = max(0.0, min(1.0, v))
        v = 12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055
        return int(round(max(0.0, min(1.0, v)) * 255))
    return enc(r), enc(g), enc(b)


def hex_to_rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# ------------------------------------------------------- 测量

def measure(path: Path, ref_lab, tol: float):
    """返回 (匹配像素均值Lab, 匹配占比)。按色度匹配，忽略明度。"""
    im = Image.open(path).convert("RGB")
    if im.width > ANALYZE_WIDTH:
        im = im.resize((ANALYZE_WIDTH,
                        max(1, round(im.height * ANALYZE_WIDTH / im.width))),
                       Image.LANCZOS)
    _, ra, rb = ref_lab
    sL = sa = sb = 0.0
    n = 0
    total = im.width * im.height
    for px in pixels(im):
        L, a, b = rgb_to_lab(px)
        if math.hypot(a - ra, b - rb) <= tol:
            sL += L; sa += a; sb += b; n += 1
    if n / total < MIN_MATCH_RATIO:
        return None, n / total
    return (sL / n, sa / n, sb / n), n / total


def correct(path: Path, ref_lab, mean_lab, tol: float, out: Path) -> int:
    """把匹配像素的色度平移回基准。只动 a/b，不动明度 —— 保住打光和材质。"""
    _, ra, rb = ref_lab
    _, ma, mb = mean_lab
    da, db = ra - ma, rb - mb
    im = Image.open(path).convert("RGB")
    px = pixels(im)
    changed = 0
    for i, p in enumerate(px):
        L, a, b = rgb_to_lab(p)
        if math.hypot(a - ra, b - rb) <= tol:
            px[i] = lab_to_rgb((L, a + da, b + db))
            changed += 1
    im.putdata(px)
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out)
    return changed


# ------------------------------------------------------- 主流程

def main() -> None:
    ap = argparse.ArgumentParser(description="产品色彩审计：测同一套图的产品主色漂移")
    ap.add_argument("task", help="叠字任务 YAML（从中读取成图清单）")
    ap.add_argument("--fix", action="store_true", help="轻微漂移就地校正，输出 _色彩校正/")
    ap.add_argument("--report", help="审计报告输出路径（默认 S3_视觉板块/amazon-listing-image-designer/色彩审计.yaml）")
    args = ap.parse_args()

    tp = Path(args.task).resolve()
    if not tp.exists():
        sys.exit(f"[任务表不存在] {tp}")
    spec = yaml.safe_load(tp.read_text(encoding="utf-8"))
    base = tp.parent
    tokens = yaml.safe_load((base / spec["style_tokens"]).read_text(encoding="utf-8"))

    pc = tokens.get("product_colors")
    if not pc or not pc.get("primary"):
        sys.exit("[未配置基准色] 请在 visual_style_tokens.yaml 里填 product_colors.primary\n"
                 "  取色方法：从 产品垫图/ 里最可信的一张实物图上吸产品主色")

    ref_rgb = hex_to_rgb(pc["primary"])
    ref_lab = rgb_to_lab(ref_rgb)
    tol = float(pc.get("match_tolerance", 20.0))
    warn = float(pc.get("max_drift", 8.0))
    max_spread = float(pc.get("max_spread", 4.0))

    print(f"产品色彩审计 · 基准 {pc['primary']} "
          f"(Lab L={ref_lab[0]:.1f} a={ref_lab[1]:.1f} b={ref_lab[2]:.1f})")
    print(f"色度匹配容差 {tol} · 单图 Δab 上限 {warn} · 跨图极差上限 {max_spread}\n")

    rows, fails = [], []
    for it in spec.get("items", []):
        out = base / it["output"]
        if not out.exists():
            continue
        mean, ratio = measure(out, ref_lab, tol)
        if mean is None:
            print(f"  – {it['item_id']:8} 未找到产品像素（匹配占比 {ratio:.2%}），跳过")
            rows.append({"item_id": it["item_id"], "status": "no_match",
                         "match_ratio": round(ratio, 4)})
            continue
        dab = math.hypot(mean[1] - ref_lab[1], mean[2] - ref_lab[2])
        dL = mean[0] - ref_lab[0]
        bad = dab > warn
        mark = "✗" if bad else "✓"
        print(f"  {mark} {it['item_id']:8} Δab={dab:5.2f}  ΔL={dL:+6.2f}  "
              f"占比={ratio:6.2%}  实测={'#%02X%02X%02X' % lab_to_rgb(mean)}")
        rows.append({"item_id": it["item_id"], "status": "drift" if bad else "ok",
                     "delta_ab": round(dab, 2), "delta_L": round(dL, 2),
                     "match_ratio": round(ratio, 4),
                     "measured_hex": "#%02X%02X%02X" % lab_to_rgb(mean),
                     "measured_a": round(mean[1], 2), "measured_b": round(mean[2], 2),
                     "output": it["output"]})
        if bad:
            fails.append(it["item_id"])
            if args.fix:
                if dab > warn * 3:
                    print(f"      ⛔ 漂移过大（Δab={dab:.2f}），拒绝校正 —— "
                          f"这不是偏色，是模型画成了另一个颜色，请重出这张")
                    rows[-1]["fix"] = "refused_too_large"
                else:
                    dst = out.parent / "_色彩校正" / out.name
                    n = correct(out, ref_lab, mean, tol, dst)
                    print(f"      → 已校正 {n:,} 像素 → {dst.relative_to(base.parent)}")
                    rows[-1]["fix"] = f"corrected:{dst.name}"

    # 两个指标测的是两回事，必须分开判：
    #   跨图极差  同一套图里颜色变不变 —— 买家翻图库直接看到，最伤转化
    #   单图 Δab  成图与实物照片差多少 —— 涉及货不对板，但对基准取样很敏感
    #             （AI 出的图普遍比供应商实物照更饱和，基准偏软会让整套都超标）
    # ⛔ 极差必须两两算，不能用「各自离基准的距离」求极差 ——
    # 两张图可能都离基准 10，但方向相反，实际彼此差 20，那种算法会算成 0。
    pts = [r for r in rows if "measured_a" in r]
    spread, pair = 0.0, None
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            d = math.hypot(pts[i]["measured_a"] - pts[j]["measured_a"],
                           pts[i]["measured_b"] - pts[j]["measured_b"])
            if d > spread:
                spread, pair = d, (pts[i], pts[j])
    spread_bad = spread > max_spread

    print(f"\n{'✗' if spread_bad else '✓'} 跨图最大色差 Δab = {spread:.2f}"
          f"（上限 {max_spread}）—— 买家翻图库时感知到的就是这个数")
    if pair:
        print(f"    差得最远的一对：{pair[0]['item_id']} {pair[0]['measured_hex']}"
              f"  vs  {pair[1]['item_id']} {pair[1]['measured_hex']}"
              f"{'   并排就能看出' if spread_bad else ''}")

    rp = Path(args.report) if args.report else base / "色彩审计.yaml"
    rp.write_text(yaml.safe_dump({
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "reference_hex": pc["primary"],
        "match_tolerance": tol,
        "max_drift": warn,
        "max_spread": max_spread,
        "cross_image_spread": round(spread, 2),
        "spread_exceeded": spread_bad,
        "items": rows,
    }, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"报告 → {rp.name}")

    if fails or spread_bad:
        if fails:
            print(f"\n单图超标：{', '.join(fails)}")
            print("  若整套都超标，多半是基准取样偏软（供应商实物照通常比 AI 出图更淡）——")
            print("  先看跨图极差；极差没问题就只是整体偏饱和，调 max_drift 或换基准图。")
        print("\n处理顺序：① 重出问题图，Prompt 里写死产品颜色名（不要只写 hex，模型不认）")
        print("          ② 仍漂就 --fix 轻校正（只平移色度，不动明度）")
        print("          ③ 都不行 → 垫图本身颜色就不一致，回头统一垫图")
        sys.exit(1)
    print("\n全部通过：产品主色在各图之间一致")
    sys.exit(0)


if __name__ == "__main__":
    main()
