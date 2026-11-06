# -*- coding: utf-8 -*-
"""全店运营账本。默认 ~/.starsky-codex/ops_workspace，与新品 S1–S4 产品夹分开。"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

FOLDERS = (
    "00_连接",
    "01_采集/lingxing",
    "02_问题队列",
    "03_诊断",
    "04_日报",
    "05_产品复盘",
    "06_文案缺口",
    "_投喂_巡检报表/领星导出",
    "_投喂_巡检报表/后台报表",
    "_投喂_文案/现文案",
)


def default_root() -> Path:
    raw = (os.environ.get("STARSKY_OPS_ROOT") or "").strip()
    if raw:
        return Path(raw).expanduser()
    home = Path(os.environ.get("STARSKY_HOME") or (Path.home() / ".starsky-codex"))
    return Path(home).expanduser() / "ops_workspace"


def init_workspace(root: Path | None = None) -> Path:
    root = Path(root or default_root())
    root.mkdir(parents=True, exist_ok=True)
    for rel in FOLDERS:
        (root / rel).mkdir(parents=True, exist_ok=True)
    readme = root / "README.md"
    if not readme.exists():
        readme.write_text(
            "# 星空运营账本（ops_workspace）\n\n"
            "这里放全店巡检、采集和问题队列。不要把领星密钥、飞书 webhook 写进这个目录。\n"
            "凭据只放 `~/.starsky-codex/config/`。\n"
            "某个新品的 S1–S4 课堂材料仍在产品 run 夹；两边用店铺/ASIN 关联，不要混写。\n",
            encoding="utf-8",
        )
    pointer = {
        "schema_version": "1.0",
        "artifact_type": "ops_workspace",
        "root": str(root),
        "initialized_at": datetime.now(timezone.utc).isoformat(),
        "note": "全店运营账本；不是插件缓存，升级插件不会删这里。",
    }
    (root / "workspace.json").write_text(
        json.dumps(pointer, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return root


def main() -> int:
    ap = argparse.ArgumentParser(description="初始化或打印 ops_workspace")
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--root", help="覆盖默认目录")
    args = ap.parse_args()
    root = Path(args.root).expanduser() if args.root else default_root()
    if args.init:
        root = init_workspace(root)
        print("[OPS]", root)
        return 0
    print(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
