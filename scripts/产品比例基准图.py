#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
产品比例基准图 v1.0
====================================================================
把多件产品按**真实尺寸**缩放好、并排站在同一基线上，生成一张比例基准图。

为什么需要
--------------------------------------------------------------------
实测（2026-08-10 棉签棉垫收纳罐）：让模型自己画多件产品同框，
它**不遵守产品之间的相对大小** —— 结果一高一低全凭想象，看着就别扭。

根因和字体那个问题是同一个，只是低一层：
    文字层：模型自己编字体   → 已治（令牌 + 代码绘制）
    几何层：模型自己编大小   → 本脚本治

⛔ 光在 Prompt 里写「A 高 11.5cm、B 高 8cm」没用 —— 模型不会量尺子，
   它只会理解「一个高一个矮」。**给它一张按真实比例摆好的图当参考，才有锚点。**

这张图的三个用途：
  1. 当垫图/参考图喂给生图模型，比任何文字描述都准
  2. 人眼一看就知道真实比例长什么样，出图后好对照
  3. 后续做「空场景 + 产品合成」时，这就是合成用的素材与缩放依据

用法
--------------------------------------------------------------------
    python 产品比例基准图.py 比例配置.yaml

依赖：pillow + pyyaml

作者：坚哥｜跨境者说干货 · 星空的跨境圈子｜Codex亚马逊实战
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    import yaml
    from PIL import Image, ImageChops, ImageDraw, ImageFont
except ImportError as e:                                     # pragma: no cover
    sys.exit(f"[缺依赖] {e}\n  pip install pillow pyyaml")

BUNDLED_FONTS = Path(__file__).resolve().parent.parent / "assets" / "fonts"


# ⛔ Inter / Archivo 都没有中文字形，产品名写中文会渲成豆腐块。
#   所以按字符串内容分流：含中日韩字符的走系统中文字体，纯西文走 Inter。
CJK_FONTS = [Path("C:/Windows/Fonts/msyh.ttc"), Path("C:/Windows/Fonts/msyhbd.ttc"),
             Path("C:/Windows/Fonts/simhei.ttf"),
             Path("/System/Library/Fonts/PingFang.ttc"),
             Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")]
LATIN_FONTS = [BUNDLED_FONTS / "Inter-SemiBold.ttf", BUNDLED_FONTS / "Inter-Regular.ttf",
               Path("C:/Windows/Fonts/arialbd.ttf"),
               Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf")]


def _first(cands, size):
    for c in cands:
        if c.exists():
            try:
                return ImageFont.truetype(str(c), size)
            except OSError:
                continue
    return None


def has_cjk(s: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" or "\u3000" <= ch <= "\u303f" for ch in s)


def load_font(size: int, text: str = ""):
    f = _first(CJK_FONTS if has_cjk(text) else LATIN_FONTS, size)
    if f is None:
        f = _first(CJK_FONTS + LATIN_FONTS, size)
    return f or ImageFont.load_default()


def hex_rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def cutout(im: Image.Image, tol: int = 30, work_max_side: int = 1000) -> Image.Image:
    """从四角漫填去掉连通白底；不碰产品内部的白色。

    两处性能坑，都实测过：
      ① alpha 别用逐像素 Python 循环 —— 2048² 是 420 万次迭代。
         改 point + ImageChops 走 C 层后从约 5s 降到 0.10s。
      ② ⛔ **真正的大头是 floodfill 本身** —— PIL 的 ImageDraw.floodfill 是
         纯 Python 实现：2048² 要 15.25s，1024² 只要 4.33s。
         而基准图最终只渲染到约 500px 高，在 2048² 上漫填纯属浪费。
         所以先降采样到 work_max_side 再抠，成品尺寸不受影响。
    """
    im = im.convert("RGB")
    if max(im.size) > work_max_side:
        k = work_max_side / max(im.size)
        im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))),
                       Image.LANCZOS)
    work = im.copy()
    for corner in [(0, 0), (im.width - 1, 0), (0, im.height - 1), (im.width - 1, im.height - 1)]:
        if sum(work.getpixel(corner)) / 3 > 225:
            ImageDraw.floodfill(work, corner, (255, 0, 255), thresh=tol)
    r, g, b = work.split()
    # 洋红标记 = R255 且 G0 且 B255，三个通道逐位与
    mag = ImageChops.multiply(
        ImageChops.multiply(r.point(lambda v: 255 if v == 255 else 0),
                            g.point(lambda v: 255 if v == 0 else 0)),
        b.point(lambda v: 255 if v == 255 else 0))
    out = im.convert("RGBA")
    out.putalpha(ImageChops.invert(mag))
    return out


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("用法: python 产品比例基准图.py 比例配置.yaml")
    cfg_path = Path(sys.argv[1]).resolve()
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    base = cfg_path.parent

    cv = cfg.get("canvas", {})
    W, H = int(cv.get("w", 1600)), int(cv.get("h", 900))
    bg = hex_rgb(cv.get("bg", "#F2F1EF"))
    ink = hex_rgb(cfg.get("ink", "#1A1D21"))
    soft = hex_rgb(cfg.get("ink_soft", "#5A6067"))

    baseline = H * float(cfg.get("baseline_pct", 82)) / 100.0
    tallest_pct = float(cfg.get("tallest_height_pct", 62))
    gap = W * float(cfg.get("gap_pct", 5)) / 100.0

    prods = cfg.get("products", [])
    if not prods:
        sys.exit("[配置为空] products 里至少要有一件产品")

    tallest_cm = max(float(p["height_cm"]) for p in prods)
    px_per_cm = (H * tallest_pct / 100.0) / tallest_cm
    print(f"比例尺：1 cm = {px_per_cm:.2f} px（最高件 {tallest_cm} cm 占画布高 {tallest_pct}%）\n")

    # 抠图 + 按真实高度缩放
    items = []
    for p in prods:
        src = base / p["photo"]
        if not src.exists():
            sys.exit(f"[图片不存在] {p['photo']}")
        im = cutout(Image.open(src))
        bbox = im.getbbox()
        if bbox:
            im = im.crop(bbox)
        target_h = int(round(float(p["height_cm"]) * px_per_cm))
        target_w = int(round(im.width * target_h / im.height))
        im = im.resize((max(1, target_w), max(1, target_h)), Image.LANCZOS)
        items.append((p, im))
        real_ar = float(p.get("width_cm", 0)) / float(p["height_cm"]) if p.get("width_cm") else None
        photo_ar = target_w / target_h
        warn = ""
        if real_ar and abs(photo_ar - real_ar) / real_ar > 0.18:
            warn = (f"   ⚠️ 照片宽高比 {photo_ar:.2f} 与实测 {real_ar:.2f} 差 "
                    f"{abs(photo_ar-real_ar)/real_ar*100:.0f}%（照片可能带透视或裁切）")
        print(f"  {p['name']:<10} {p['height_cm']:>5} cm 高  →  {target_h:>4}px"
              f"   宽 {target_w}px{warn}")

    total_w = sum(im.width for _, im in items) + gap * (len(items) - 1)
    if total_w > W * 0.92:
        k = W * 0.92 / total_w
        items = [(p, im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))),
                               Image.LANCZOS)) for p, im in items]
        px_per_cm *= k
        total_w *= k
        print(f"\n  （整体缩小 {k*100:.0f}% 以放进画布，相对比例不变）")

    canvas = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(canvas)

    # 基线
    d.line([(W * 0.04, baseline), (W * 0.96, baseline)], fill=hex_rgb("#D8D6D2"), width=2)

    ns, ds = int(H * 0.030), int(H * 0.026)

    x = (W - total_w) / 2
    for p, im in items:
        y = baseline - im.height
        canvas.paste(im, (int(x), int(y)), im)
        cx = x + im.width / 2
        t1 = str(p["name"])
        t2 = f"{p['height_cm']}×{p.get('width_cm', '?')} cm"
        f1, f2 = load_font(ns, t1), load_font(ds, t2)
        w1 = d.textlength(t1, font=f1)
        w2 = d.textlength(t2, font=f2)
        d.text((cx - w1 / 2, baseline + H * 0.025), t1, font=f1, fill=ink)
        d.text((cx - w2 / 2, baseline + H * 0.025 + H * 0.038), t2, font=f2, fill=soft)
        x += im.width + gap

    # 右侧比例尺：画一根 10cm 标尺
    ruler_cm = 10
    rh = ruler_cm * px_per_cm
    rx = W * 0.965
    d.line([(rx, baseline), (rx, baseline - rh)], fill=soft, width=3)
    for yy in (baseline, baseline - rh):
        d.line([(rx - 10, yy), (rx + 10, yy)], fill=soft, width=3)
    lbl = f"{ruler_cm} cm"
    f_r = load_font(ds, lbl)
    d.text((rx - d.textlength(lbl, font=f_r) - 16, baseline - rh / 2 - H * 0.02),
           lbl, font=f_r, fill=soft)

    # 顶部说明
    hint = str(cfg.get("title", "产品真实比例基准（同一基线，按实测尺寸缩放）"))
    d.text((W * 0.04, H * 0.05), hint, font=load_font(ns, hint), fill=ink)

    out = base / cfg.get("output", "产品比例基准图.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    print(f"\n基准图 → {out}")
    print("用法：把它作为参考图/垫图喂给生图模型；出图后拿它逐件对照高度关系。")


if __name__ == "__main__":
    main()
