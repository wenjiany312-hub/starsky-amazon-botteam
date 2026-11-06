#!/usr/bin/env python3
"""从 Amazon 商品页打印 PDF 中抽取 A+ 模块图。

用法：
    python extract_aplus_from_pdf.py <benchmark目录>
    python extract_aplus_from_pdf.py <benchmark目录> --dry-run

目录结构约定：
    benchmark_YYYYMMDD_品名/
    ├── _下载清单.csv
    └── {ASIN}/
        ├── _page_print.pdf      ← 输入
        └── aplus_01.jpg …       ← 输出

判定规则（实测于 Bathroom Canisters 类目 6 个 ASIN）：
    宽 940–3000 且 高 ≥300  → A+ 模块（970=标准A+，1464=Premium A+）
    宽 ≥3000               → 页面横幅，排除
    640×368                → 视频缩略图，排除
    679×NNN                → 商品主图缩略，排除

依赖：PyMuPDF（import fitz）
"""
import argparse
import os
import sys

MIN_W, MAX_W, MIN_H = 940, 3000, 300


def extract(asin_dir, dry_run=False):
    """返回 (抽出张数, 尺寸列表)。无 PDF 时返回 (None, [])。"""
    import fitz

    pdf = os.path.join(asin_dir, "_page_print.pdf")
    if not os.path.exists(pdf):
        return None, []

    doc = fitz.open(pdf)
    seen, hits = set(), []
    for pno in range(len(doc)):
        for xref, *_ in doc[pno].get_images(full=True):
            if xref in seen:
                continue
            seen.add(xref)
            try:
                info = doc.extract_image(xref)
            except Exception:
                continue
            w, h = info["width"], info["height"]
            if MIN_W <= w < MAX_W and h >= MIN_H:
                hits.append((w, h, info["image"]))

    if not dry_run:
        for k, (_, _, blob) in enumerate(hits, 1):
            with open(os.path.join(asin_dir, "aplus_%02d.jpg" % k), "wb") as f:
                f.write(blob)
    doc.close()
    return len(hits), [(w, h) for w, h, _ in hits]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("benchmark_dir")
    ap.add_argument("--dry-run", action="store_true", help="只报告不写文件")
    args = ap.parse_args()

    root = args.benchmark_dir
    if not os.path.isdir(root):
        sys.exit("目录不存在: %s" % root)

    asins = sorted(
        d for d in os.listdir(root)
        if os.path.isdir(os.path.join(root, d)) and not d.startswith("_")
    )
    if not asins:
        sys.exit("未找到 ASIN 子目录")

    total, no_pdf, no_aplus = 0, [], []
    for a in asins:
        n, dims = extract(os.path.join(root, a), args.dry_run)
        if n is None:
            no_pdf.append(a)
            print("%-13s 无 _page_print.pdf —— 跳过" % a)
            continue
        if n == 0:
            no_aplus.append(a)
        total += n
        print("%-13s A+ %d 张  %s" % (a, n, ", ".join("%dx%d" % d for d in dims)))

    print("\n合计 %d 张%s" % (total, "（dry-run，未写盘）" if args.dry_run else ""))
    if no_pdf:
        print("缺 PDF: %s" % ", ".join(no_pdf))
    if no_aplus:
        print("无 A+（属正常，记为 aplus:0）: %s" % ", ".join(no_aplus))


if __name__ == "__main__":
    main()
