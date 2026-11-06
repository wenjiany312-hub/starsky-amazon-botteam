#!/usr/bin/env python3
"""把「待定产品」占位夹改成正式产品夹：product_runs/{日期}_待定产品N → product_runs/{日期}_{站点-价格带-定位}。

  python rename_product_root.py --root "<占位产品根>" --name "US-9.99-16.99-电动牙刷架"

只改夹名，夹里的文件一个不动（workflow_state.yaml 和产物记录的都是相对路径，不用回填）。
悬浮窗记着「上次打开的产品」和历史里的旧路径，改名后同步指到新夹，否则它会找不到项目。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import subprocess
from datetime import date
from pathlib import Path

APP_DIR = Path.home() / ".starsky-codex"
FORBIDDEN = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def clean(name: str) -> str:
    name = FORBIDDEN.sub("-", str(name)).strip(" .-")
    name = re.sub(r"\s+", "-", name)
    return name[:60]


def new_name(old: Path, label: str) -> str:
    m = re.match(r"^(\d{4}-\d{2}-\d{2})_", old.name)
    return f"{m.group(1) if m else date.today().isoformat()}_{clean(label)}"


def repoint(old: Path, new: Path) -> list:
    """悬浮窗记忆里凡是指向旧夹（或旧夹里面）的路径，换成新夹。返回改了哪些文件。"""
    changed = []
    last = APP_DIR / "nav_float_last.json"
    hist = APP_DIR / "nav_float_history.json"

    def swap(value):
        text = str(value)
        candidates = [os.path.abspath(os.path.expanduser(text)), os.path.realpath(text)]
        for real in candidates:
            if real == str(old) or real.startswith(str(old) + os.sep):
                return str(new) + real[len(str(old)):]
        return text
    try:
        data = json.loads(last.read_text(encoding="utf-8"))
        if isinstance(data, dict) and swap(data.get("root", "")) != data.get("root"):
            data["root"] = swap(data["root"])
            last.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            changed.append(str(last))
    except (OSError, ValueError):
        pass
    try:
        items = json.loads(hist.read_text(encoding="utf-8"))
        if isinstance(items, list):
            fixed = [swap(x) if isinstance(x, str) else x for x in items]
            if fixed != items:
                hist.write_text(json.dumps(fixed, ensure_ascii=False), encoding="utf-8")
                changed.append(str(hist))
    except (OSError, ValueError):
        pass
    return changed


def remember_alias(new: Path, old: Path) -> None:
    """R06.1 AS-03：已生成、还没展示的步骤卡里写的是旧夹路径。记下旧路径，流程展示卡片时换成新路径、核对时两种都认，
    助手就不必自己改卡（改了反而核对不过，要多补发一轮）。"""
    state_path = new / "workflow_state.yaml"
    if not state_path.is_file():
        return
    import yaml
    state = yaml.safe_load(state_path.read_text(encoding="utf-8-sig")) or {}
    aliases = [a for a in state.get("root_aliases") or [] if isinstance(a, str)]
    if str(old) not in aliases:
        state["root_aliases"] = aliases + [str(old)]
        temporary = state_path.with_name(state_path.name + ".tmp")
        temporary.write_text(yaml.safe_dump(state, allow_unicode=True, sort_keys=False), encoding="utf-8")
        os.replace(temporary, state_path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True, help="占位产品根（product_runs/日期_待定产品N）")
    ap.add_argument("--name", required=True, help="站点-价格带-一句话定位，例如 US-9.99-16.99-电动牙刷架")
    args = ap.parse_args(argv)
    old = Path(args.root).expanduser().resolve()
    if not old.is_dir():
        print(json.dumps({"ok": False, "error": f"找不到占位产品根：{old}"}, ensure_ascii=False))
        return 2
    if not clean(args.name):
        print(json.dumps({"ok": False, "error": "名字是空的：写 站点-价格带-一句话定位"}, ensure_ascii=False))
        return 2
    target = old.with_name(new_name(old, args.name))
    if target == old:
        print(json.dumps({"ok": True, "old": str(old), "new": str(old), "note": "已经是这个名字"}, ensure_ascii=False))
        return 0
    if target.exists():
        print(json.dumps({"ok": False, "error": f"同名夹已存在：{target.name}；换个名字（例如加一个区分词）"}, ensure_ascii=False))
        return 2
    try:
        old.rename(target)
        try:
            old.symlink_to(target, target_is_directory=True)
        except OSError:
            if os.name != 'nt': raise
            linked = subprocess.run(['cmd', '/c', 'mklink', '/J', str(old), str(target)],
                                    capture_output=True, text=True)
            if linked.returncode != 0: raise OSError('cannot preserve old path as a directory link')
    except OSError as exc:
        if target.is_dir() and not old.exists(): target.rename(old)
        print(json.dumps({"ok": False, "error": f"改名没成功（夹里有文件被打开？先关掉再试）：{exc.__class__.__name__}"}, ensure_ascii=False))
        return 2
    remember_alias(target, old)
    print(json.dumps({"ok": True, "old": str(old), "new": str(target), "float_updated": repoint(old, target),
                      "compatibility_link": str(old), "note": "旧路径保留目录链接；历史文件与链接继续可读"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
