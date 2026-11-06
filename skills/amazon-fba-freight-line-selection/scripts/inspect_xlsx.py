"""
inspect_xlsx.py — 通用 xlsx 报价表解析脚本

适用场景：货代/供应商报价 xlsx，含合并单元格、多 sheet、分区/尺寸/重量阶梯报价。
输出：每个 sheet 落盘成 .tsv 到指定 _raw/ 目录，便于后续审计和人工核对。

用法：
    python inspect_xlsx.py <xlsx_path> <output_dir>
    # 示例：
    python inspect_xlsx.py "D:/航线选择/TCD最强线路VIP成本价.xlsx" "D:/生成的报告/航线选择/_raw"
"""
import sys
import os
import openpyxl
import re


def resolve_merged(ws):
    """把 openpyxl 合并单元格中的 None 替换为左上角的值。"""
    merged = {}
    for rng in ws.merged_cells.ranges:
        v = ws.cell(row=rng.min_row, column=rng.min_col).value
        for r in range(rng.min_row, rng.max_row + 1):
            for c in range(rng.min_col, rng.max_col + 1):
                if (r, c) not in merged:
                    merged[(r, c)] = v
    return merged


def render(ws, max_rows=None):
    """渲染一个 sheet 为二维字符串列表（保留空字符串、跳过全空行）。"""
    merged = resolve_merged(ws)
    rows_out = []
    n_rows = ws.max_row if max_rows is None else min(ws.max_row, max_rows)
    n_cols = ws.max_column
    for r in range(1, n_rows + 1):
        cells = []
        for c in range(1, n_cols + 1):
            v = ws.cell(row=r, column=c).value
            if v is None and (r, c) in merged:
                v = merged[(r, c)]
            cells.append("" if v is None else str(v).replace("\n", " | "))
        if any(cells):
            rows_out.append(cells)
    return rows_out


def find_zip_partition(tsv_path, target_zip):
    """在 .tsv 化的"导航"sheet 中查目标邮编归属的 FBA 仓库代码 + 分区。

    返回 [(row_no, fba_code, zip, region), ...]
    """
    hits = []
    with open(tsv_path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            cells = line.rstrip("\n").split("\t")
            zip_idx = None
            for j, c in enumerate(cells):
                if re.fullmatch(r"\d{5}", c.strip()):
                    zip_idx = j
                    break
            if zip_idx is None:
                continue
            zip_code = cells[zip_idx].strip()
            code = cells[zip_idx - 1].strip() if zip_idx - 1 >= 0 else ""
            region = ""
            for j in range(zip_idx + 1, min(zip_idx + 4, len(cells))):
                cc = cells[j].strip()
                if cc in ("美西", "美中", "美东", "美西北"):
                    region = cc
                    break
            if zip_code.startswith(target_zip[:3]) or target_zip in line:
                hits.append((i, code, zip_code, region))
    return hits


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    xlsx_path = sys.argv[1]
    out_dir = sys.argv[2]
    os.makedirs(out_dir, exist_ok=True)

    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    print(f"sheets: {wb.sheetnames}")
    print(f"n_sheets: {len(wb.sheetnames)}")

    for name in wb.sheetnames:
        ws = wb[name]
        rows = render(ws)
        out_path = os.path.join(out_dir, f"{name}.tsv")
        with open(out_path, "w", encoding="utf-8") as f:
            for row in rows:
                f.write("\t".join(row) + "\n")
        print(f"saved: {out_path}  rows={len(rows)}")


if __name__ == "__main__":
    main()
