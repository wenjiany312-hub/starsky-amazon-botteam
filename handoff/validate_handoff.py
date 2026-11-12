#!/usr/bin/env python3
"""handoff 契约校验（只用标准库）。规则见 handoff/README.md。

用法: python3 handoff/validate_handoff.py <handoff目录>
退出码: 0 通过（可能有警告）；1 有错误；2 参数错误
"""
import hashlib
import json
import os
import sys
from datetime import datetime

SCHEMA_VERSION = "1"
BOT_SOURCES = {"grokbot", "openbot", "starsky"}
ENVELOPE_SOURCES = BOT_SOURCES | {"terminal"}
EDITIONS = {"base", "flagship"}
OUTBOX_SUBDIRS = {"products", "reports"}
INBOX_SUBDIRS = {"plans", "results"}
MANIFEST_KEYS = ("schema_version", "source", "edition", "created_at", "files")


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def is_safe_rel(path):
    if not isinstance(path, str) or not path or os.path.isabs(path):
        return False
    return ".." not in path.split("/") and "\\" not in path


def is_aware_iso(value):
    if not isinstance(value, str):
        return False
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return dt.tzinfo is not None


def load_json(path, errors, label):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        errors.append(f"{label} 不是合法 JSON：{e}")
    except OSError as e:
        errors.append(f"{label} 无法读取：{e}")
    return None


def check_envelope(data, rel, errors):
    if not isinstance(data, dict):
        errors.append(f"{rel}：顶层必须是对象")
        return
    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"{rel}：schema_version 必须为 \"{SCHEMA_VERSION}\"")
    if data.get("source") not in ENVELOPE_SOURCES:
        errors.append(f"{rel}：source 不合法（{data.get('source')!r}）")
    if not is_aware_iso(data.get("created_at")):
        errors.append(f"{rel}：created_at 必须是带时区的 ISO 8601 时间")


def walk_files(base):
    for dirpath, _, names in os.walk(base):
        for name in names:
            full = os.path.join(dirpath, name)
            yield os.path.relpath(full, base).replace(os.sep, "/")


def validate(root):
    errors, warnings = [], []

    if not os.path.isdir(root):
        return [f"目录不存在：{root}"], warnings

    # 1. manifest.json
    manifest_path = os.path.join(root, "manifest.json")
    listed = set()
    if not os.path.isfile(manifest_path):
        errors.append("缺少 manifest.json")
    else:
        manifest = load_json(manifest_path, errors, "manifest.json")
        if isinstance(manifest, dict):
            for key in MANIFEST_KEYS:
                if key not in manifest:
                    errors.append(f"manifest.json 缺字段：{key}")
            if manifest.get("schema_version") != SCHEMA_VERSION:
                errors.append(f"manifest.json：schema_version 必须为 \"{SCHEMA_VERSION}\"")
            if manifest.get("source") not in BOT_SOURCES:
                errors.append(f"manifest.json：source 不合法（{manifest.get('source')!r}）")
            if manifest.get("edition") not in EDITIONS:
                errors.append(f"manifest.json：edition 必须为 base 或 flagship")
            if "created_at" in manifest and not is_aware_iso(manifest.get("created_at")):
                errors.append("manifest.json：created_at 必须是带时区的 ISO 8601 时间")

            files = manifest.get("files", [])
            if not isinstance(files, list):
                errors.append("manifest.json：files 必须是数组")
                files = []
            for item in files:
                if not isinstance(item, dict) or not is_safe_rel(item.get("path")) \
                        or not isinstance(item.get("sha256"), str):
                    errors.append(f"manifest.json：files 条目格式错误：{item!r}")
                    continue
                rel = item["path"]
                if not rel.startswith("outbox/"):
                    errors.append(f"manifest.json：只能登记 outbox/ 下的文件：{rel}")
                    continue
                listed.add(rel)
                full = os.path.join(root, rel)
                if not os.path.isfile(full):
                    errors.append(f"登记的文件不存在：{rel}")
                elif sha256_of(full) != item["sha256"]:
                    errors.append(f"sha256 与内容不一致：{rel}")
        elif manifest is not None:
            errors.append("manifest.json 顶层必须是对象")

    # 2. outbox/：每个文件都必须登记，JSON 要满足信封
    outbox = os.path.join(root, "outbox")
    if os.path.isdir(outbox):
        for rel_in in sorted(walk_files(outbox)):
            rel = "outbox/" + rel_in
            name = os.path.basename(rel)
            if name.startswith(".") and name.endswith(".tmp"):
                warnings.append(f"忽略未完成的临时文件：{rel}")
                continue
            top = rel_in.split("/", 1)[0]
            if top not in OUTBOX_SUBDIRS or "/" not in rel_in:
                warnings.append(f"outbox/ 下出现未规定的位置：{rel}")
                continue
            if rel not in listed:
                errors.append(f"未在 manifest 中登记：{rel}")
            if rel.endswith(".json"):
                data = load_json(os.path.join(root, rel), errors, rel)
                if data is not None:
                    check_envelope(data, rel, errors)

    # 3. inbox/：只允许 plans/ 与 results/，JSON 要满足信封
    inbox = os.path.join(root, "inbox")
    if os.path.isdir(inbox):
        for rel_in in sorted(walk_files(inbox)):
            rel = "inbox/" + rel_in
            name = os.path.basename(rel)
            if name.startswith(".") and name.endswith(".tmp"):
                warnings.append(f"忽略未完成的临时文件：{rel}")
                continue
            top = rel_in.split("/", 1)[0]
            if top not in INBOX_SUBDIRS or "/" not in rel_in:
                warnings.append(f"inbox/ 下出现未规定的位置：{rel}")
                continue
            if rel.endswith(".json"):
                data = load_json(os.path.join(root, rel), errors, rel)
                if data is not None:
                    check_envelope(data, rel, errors)

    # 4. 顶层其他目录只给警告
    for entry in sorted(os.listdir(root)):
        if entry in ("manifest.json", "outbox", "inbox", "README.md") or entry.startswith("."):
            continue
        warnings.append(f"顶层出现未规定的条目：{entry}")

    return errors, warnings


def main(argv):
    if len(argv) != 2:
        print("用法: python3 handoff/validate_handoff.py <handoff目录>")
        return 2
    root = os.path.abspath(os.path.expanduser(argv[1]))
    errors, warnings = validate(root)
    for w in warnings:
        print(f"⚠️  {w}")
    for e in errors:
        print(f"❌ {e}")
    if errors:
        print(f"未通过：{len(errors)} 个错误，{len(warnings)} 个警告")
        return 1
    print(f"✅ 通过（{len(warnings)} 个警告）：{root}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
