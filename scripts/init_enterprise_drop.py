#!/usr/bin/env python3
"""在当前产品 run 里生成企业资料投喂夹。用户拷完文件后，在对话里回复「已完成」。"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

FOLDERS: list[tuple[str, str, str]] = [
    ("00_共享规则", "S1–S5", "跨品类也成立的判断口径、禁区、内部术语、踩过的坑。"),
    ("供应链", "S1", "报价单、规格书、认证、起订量、交期、工厂沟通记录。"),
    ("关键词", "S2", "SIF / 卖家精灵 / 西柚导出的原始词表。原表别覆盖，清洗结果系统另存。"),
    ("Listing", "S2", "现有标题五点、历史版本、竞品文案样本。"),
    ("产品案例", "S1", "本品完整资料：1688/ASIN、样品图、包装、尺寸重量。"),
    ("合规", "S1", "专利排查、认证要求、平台规则截图。有时效的记上日期。"),
    ("视觉参考", "S3", "垫图、竞品图、品牌规范、历史成图。编号命名更好。"),
    ("广告", "S4/S5", "历史报表、惯用架构、可接受 ACOS。报表按日期归档。"),
    ("_未分类", "00C", "不知道放哪就丢这里。系统在资料接入时分类，不要求你先整理。"),
]

SKIP_NAMES = {"放什么.md", "README_投喂说明.md", "_投喂状态.yaml"}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_text(path: Path, text: str) -> None:
    path.write_text(text.replace("\r\n", "\n").replace("\r", "\n"), encoding="utf-8")


def init_drop(root: Path, category: str) -> dict:
    pack = root / "00_产品资料"
    if category.strip():
        pack = pack / category.strip()
    pack.mkdir(parents=True, exist_ok=True)

    created: list[dict] = []
    for name, stage, hint in FOLDERS:
        folder = pack / name
        folder.mkdir(parents=True, exist_ok=True)
        write_text(
            folder / "放什么.md",
            f"# {name}\n\n主要给：{stage}\n\n{hint}\n\n拷完文件后，回到 Codex 只回复：已完成\n",
        )
        created.append({"folder": name, "path": str(folder), "stage": stage, "hint": hint})

    readme_lines = [
        "# 企业资料投喂夹",
        "",
        "系统已建好空夹。请把文件拷进来，**不必整理得很干净**；不知道放哪就丢 `_未分类`。",
        "",
        "拷完后回到 Codex 对话，只回复：",
        "",
        "```text",
        "已完成",
        "```",
        "",
        "系统会扫描本夹，把接入报告写到 `00_总控/starsky-fullchain-workflow/00C_资料接入与优化建议报告.md`，再继续你选的板块。",
        "",
        "| 夹 | 主要给哪块 | 放什么 |",
        "|---|---|---|",
    ]
    for name, stage, hint in FOLDERS:
        readme_lines.append(f"| `{name}/` | {stage} | {hint} |")
    readme_lines.extend(
        [
            "",
            "不要改夹名。原件请留在这里，系统不会把报告写回这些夹。",
            "",
        ]
    )
    write_text(pack / "README_投喂说明.md", "\n".join(readme_lines) + "\n")

    status_lines = [
        "status: waiting_files",
        f"created_at: {utc_now()}",
        f"pack_root: {pack.as_posix()}",
        "resume_phrase: 已完成",
        "note: 用户拷完文件后在对话回复「已完成」；空夹不算已投喂。",
        "",
    ]
    write_text(pack / "_投喂状态.yaml", "\n".join(status_lines))

    return {
        "pack_root": str(pack),
        "folders": created,
        "status": "waiting_files",
        "resume_phrase": "已完成",
    }


def count_user_files(pack: Path) -> dict:
    by_folder: list[dict] = []
    total = 0
    for name, _, _ in FOLDERS:
        folder = pack / name
        files = []
        if folder.is_dir():
            for p in folder.rglob("*"):
                if p.is_file() and p.name not in SKIP_NAMES:
                    files.append(str(p))
        total += len(files)
        by_folder.append({"folder": name, "count": len(files), "files": files[:20]})
    return {"pack_root": str(pack), "user_file_count": total, "by_folder": by_folder}


def main() -> None:
    parser = argparse.ArgumentParser(description="Init or inspect 产品资料 drop folders.")
    parser.add_argument("--root", default=".", help="product run directory")
    parser.add_argument("--category", default="", help="optional category subfolder name")
    parser.add_argument(
        "--inspect",
        action="store_true",
        help="count user files after 已完成; do not recreate",
    )
    args = parser.parse_args()
    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"product root not found: {root}")

    pack = root / "00_产品资料"
    if args.category.strip():
        pack = pack / args.category.strip()

    if args.inspect:
        if not pack.is_dir():
            raise SystemExit(f"drop folder not found: {pack}")
        print(json.dumps(count_user_files(pack), ensure_ascii=False, indent=2))
        return

    result = init_drop(root, args.category)
    result["status"] = {
        "status": "waiting_files",
        "resume_phrase": "已完成",
        "pack_root": result["pack_root"],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
