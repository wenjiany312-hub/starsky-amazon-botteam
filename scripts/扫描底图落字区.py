# -*- coding: utf-8 -*-
"""扫描底图各区域的平整度与文字可读性，告诉你字能放哪。

平整度 = 灰度标准差（越小越干净）；渲染器闸门上限 18。
对比度 = 该区域平均色 与 ink(#1A1D21) 的 WCAG 对比度；下限 4.0。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".codex" / "scripts"))
from PIL import Image, ImageStat

INK = (0x1A, 0x1D, 0x21)


def rel_lum(rgb):
    def ch(v):
        v /= 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(x) for x in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = rel_lum(a), rel_lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


p = Path(sys.argv[1])
im = Image.open(p).convert("RGB")
W, H = im.size
print(f"{p.name}  {W}x{H}\n")

# 横向 4 列 × 纵向 3 行，模拟文字可能落的区域
cols = [("左", 0.06, 0.30), ("中左", 0.28, 0.52), ("中右", 0.50, 0.74), ("右", 0.72, 0.94)]
rows = [("上 y26%", 0.22, 0.42), ("中 y45%", 0.42, 0.62), ("下 y66%", 0.62, 0.82)]

print(f"{'区域':<14}{'平整度':>8}{'对比度':>9}   判定")
print("-" * 52)
best = []
for rn, y0, y1 in rows:
    for cn, x0, x1 in cols:
        box = (int(W * x0), int(H * y0), int(W * x1), int(H * y1))
        crop = im.crop(box)
        sd = ImageStat.Stat(crop.convert("L")).stddev[0]
        mean = tuple(round(v) for v in ImageStat.Stat(crop).mean[:3])
        cr = contrast(INK, mean)
        ok = sd <= 18 and cr >= 4.0
        mark = "✓ 可放字" if ok else ("✗ 太花" if sd > 18 else "✗ 对比不足")
        print(f"{cn+' '+rn:<14}{sd:>8.1f}{cr:>9.2f}   {mark}")
        if ok:
            best.append((sd, f"{cn} {rn}"))
    print()

if best:
    best.sort()
    print("最干净的落字区（按平整度排）：")
    for sd, name in best[:4]:
        print(f"   {name}   标准差 {sd:.1f}")
else:
    print("⛔ 没有任何区域同时满足平整度与对比度 —— 这张底图不适合直接叠深色字。")
