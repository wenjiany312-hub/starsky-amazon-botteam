#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
产品合成器 v1.0
====================================================================
把**真实产品抠图**按实测厘米比例合成到**空场景底图**上，并程序化生成
接触阴影与投影 —— 让产品既 100% 不变形、比例 100% 精确，又落得实。

为什么要这一层
--------------------------------------------------------------------
实测（棉签棉垫收纳罐，2026-08-10 ~ 08-11）反复撞的两堵墙：

  ① **产品变形**：只要让模型重画产品，它就会改结构。传了正确参考图也没用 ——
     长提示词（350+ 词、7 个约束块）会把参考图的权重挤掉，文字赢了。
  ② **比例失真**：模型不会量尺子，三件产品的高矮全凭想象。

这两条**提示词治不了**，因为根因是"产品像素由模型重新生成"。
本脚本的做法是让模型只画空场景（没有产品，就没有变形可言，提示词还能极短），
产品像素直接来自真实照片。

而空场景 + 抠图合成的老问题是「飘」—— 实测产品底部把台面压暗只有 9.3 灰阶，
真实物体应有 ≥12。所以本脚本必须自己画阴影，这是它存在的核心理由：

    contact shadow  底部紧贴的暗环（环境光遮蔽），决定"落没落地"
    cast shadow     按光源方向拉出的投影，决定"光照可信不可信"

⛔⛔ 适用范围（2026-08-11 实测教训，先读完再决定用不用）
--------------------------------------------------------------------
**只对不透明产品有效。透明 / 半透明产品不要用本脚本。**

实测反例：亚克力透明收纳罐。抠图合成后 ——
  · 罐体内部仍是原白底棚拍的白色，不会透出场景墙面
  · 边缘留白色残留光晕
  · 棚拍正面光与场景侧窗光对不上
  · 结果明显不如让模型直接画

原因：**透明物体的像素里编码着它原来的背景。** 把它从白底抠出来贴到
米色墙前，物理上就是错的 —— 你贴过去的是"白墙前的罐子"，不是"米色墙前的罐子"。

  可用：不透明产品（服饰、箱包、五金、家电、实色塑料件…）
  ⛔ 不可用：玻璃 / 亚克力 / 透明塑料 / 纱质 / 任何能看穿的东西
        → 这类走「模型直接画 + 短提示词 + 参考图」，见
          references/多产品同框出图约束.md

用法
--------------------------------------------------------------------
    python 产品合成器.py 合成配置.yaml

依赖：pillow + pyyaml

作者：坚哥｜跨境者说干货 · 星空的跨境圈子｜Codex亚马逊实战
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

try:
    import yaml
    from PIL import Image, ImageChops, ImageDraw, ImageFilter
except ImportError as e:                                     # pragma: no cover
    sys.exit(f"[缺依赖] {e}\n  pip install pillow pyyaml")


def cutout(im: Image.Image, tol: int = 30, work_max_side: int = 1400) -> Image.Image:
    """四角漫填去白底。⛔ 先降采样再漫填 —— PIL 的 floodfill 是纯 Python，
    2048² 要 15 秒，1400 以内快得多，而成品还要缩小，细节不吃亏。"""
    im = im.convert("RGB")
    if max(im.size) > work_max_side:
        k = work_max_side / max(im.size)
        im = im.resize((int(im.width * k), int(im.height * k)), Image.LANCZOS)
    work = im.copy()
    for c in [(0, 0), (im.width - 1, 0), (0, im.height - 1), (im.width - 1, im.height - 1)]:
        if sum(work.getpixel(c)) / 3 > 225:
            ImageDraw.floodfill(work, c, (255, 0, 255), thresh=tol)
    r, g, b = work.split()
    mag = ImageChops.multiply(
        ImageChops.multiply(r.point(lambda v: 255 if v == 255 else 0),
                            g.point(lambda v: 255 if v == 0 else 0)),
        b.point(lambda v: 255 if v == 255 else 0))
    out = im.convert("RGBA")
    out.putalpha(ImageChops.invert(mag))
    bbox = out.getbbox()
    return out.crop(bbox) if bbox else out


def contact_shadow(size: tuple[int, int], w: int, strength: float, blur: float) -> Image.Image:
    """底部紧贴的暗环。这一层决定"落没落地"，⛔ 比投影更重要。"""
    layer = Image.new("L", size, 0)
    d = ImageDraw.Draw(layer)
    h = max(3, int(w * 0.13))
    cx, cy = size[0] // 2, size[1] - h // 2
    d.ellipse([cx - w * 0.48, cy - h / 2, cx + w * 0.48, cy + h / 2],
              fill=int(255 * strength))
    return layer.filter(ImageFilter.GaussianBlur(blur))


def cast_shadow(prod: Image.Image, angle_deg: float, length: float,
                strength: float, blur: float) -> tuple[Image.Image, int, int]:
    """按光源方向把产品剪影压扁拉长成投影。返回 (灰度层, x偏移, y偏移)。"""
    a = prod.split()[-1]
    sw = prod.width
    sh = max(3, int(prod.height * length))
    squashed = a.resize((sw, sh), Image.LANCZOS)
    rad = math.radians(angle_deg)
    dx = int(math.cos(rad) * prod.height * length * 0.9)
    pad = abs(dx) + int(blur * 3)
    layer = Image.new("L", (sw + pad * 2, sh + int(blur * 3)), 0)
    layer.paste(squashed.point(lambda v: int(v * strength)), (pad + dx, 0))
    return layer.filter(ImageFilter.GaussianBlur(blur)), pad, 0


def hex_rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("用法: python 产品合成器.py 合成配置.yaml")
    cfg_path = Path(sys.argv[1]).resolve()
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    base = cfg_path.parent

    scene_path = base / cfg["scene"]
    if not scene_path.exists():
        sys.exit(f"[场景底图不存在] {cfg['scene']}")
    scene = Image.open(scene_path).convert("RGBA")
    W, H = scene.size
    print(f"场景 {scene_path.name}  {W}x{H}")

    baseline = H * float(cfg["baseline_pct"]) / 100.0
    prods = cfg.get("products", [])
    if not prods:
        sys.exit("[配置缺 products]")

    # 比例尺：用最高件占画面高度的百分比反推 px/cm
    tallest_cm = max(float(p["height_cm"]) for p in prods)
    px_per_cm = (H * float(cfg["tallest_height_pct"]) / 100.0) / tallest_cm
    print(f"比例尺 1cm = {px_per_cm:.2f}px（最高件 {tallest_cm}cm 占画面高 "
          f"{cfg['tallest_height_pct']}%）\n")

    sh_cfg = cfg.get("shadow", {})
    angle = float(sh_cfg.get("light_angle_deg", 200))   # 200° = 光从左上来，影子偏右下
    c_str = float(sh_cfg.get("contact_strength", 0.55))
    c_blur = float(sh_cfg.get("contact_blur", 6))
    k_len = float(sh_cfg.get("cast_length", 0.28))
    k_str = float(sh_cfg.get("cast_strength", 0.30))
    k_blur = float(sh_cfg.get("cast_blur", 14))
    tint = hex_rgb(sh_cfg.get("color", "#3A332B"))

    shadow_rgba = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    prepared = []

    for p in prods:
        src = base / p["photo"]
        if not src.exists():
            sys.exit(f"[产品图不存在] {p['photo']}")
        im = cutout(Image.open(src))
        th = int(round(float(p["height_cm"]) * px_per_cm))
        tw = max(1, int(round(im.width * th / im.height)))
        im = im.resize((tw, th), Image.LANCZOS)

        cx = W * float(p["center_x_pct"]) / 100.0
        x = int(cx - tw / 2)
        y = int(baseline - th)
        prepared.append((p, im, x, y))

        # 投影（先画，压在产品下面）
        cl, pad, _ = cast_shadow(im, angle, k_len, k_str, k_blur)
        cast_col = Image.new("RGBA", cl.size, tint + (0,))
        cast_col.putalpha(cl)
        shadow_rgba.alpha_composite(cast_col, (x - pad, int(baseline) - cl.height // 2))

        # 接触阴影（更关键：决定落没落地）
        band_h = max(6, int(tw * 0.30))
        ct = contact_shadow((tw, band_h), tw, c_str, c_blur)
        ct_col = Image.new("RGBA", ct.size, tint + (0,))
        ct_col.putalpha(ct)
        shadow_rgba.alpha_composite(ct_col, (x, int(baseline) - band_h + int(band_h * 0.35)))

        real_ar = float(p.get("width_cm", 0)) / float(p["height_cm"]) if p.get("width_cm") else None
        warn = ""
        if real_ar and abs(tw / th - real_ar) / real_ar > 0.18:
            warn = f"   ⚠️ 照片宽高比 {tw/th:.2f} 与实测 {real_ar:.2f} 差 " \
                   f"{abs(tw/th-real_ar)/real_ar*100:.0f}%（照片带透视或裁切？）"
        print(f"  {p['name']:<10} {p['height_cm']:>5}cm → {th:>4}px 高 / {tw:>4}px 宽"
              f"  落位 x={x}{warn}")

    out_img = Image.alpha_composite(scene, shadow_rgba)
    for _p, im, x, y in prepared:
        out_img.alpha_composite(im, (x, y))

    out = base / cfg.get("output", "合成成图.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    out_img.convert("RGB").save(out)
    print(f"\n合成 → {out}")
    print("下一步：跑 扫描底图落字区.py 看落字区，再用 叠字渲染器.py 叠字。")
    print("⛔ 阴影参数（shadow 段）需按场景光线微调；用 _shadow_check 类脚本验证"
          "产品底部压暗是否 ≥12 灰阶。")


if __name__ == "__main__":
    main()
