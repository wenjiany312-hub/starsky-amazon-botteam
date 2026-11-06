#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""亚马逊选品知识库 CLI（amazon-selection-knowledge-learner）

机械部分全在这里：建库、来源接入与文本抽取、官方站点抓取（白名单 + robots）、任务单、卡片校验、
索引与覆盖度、检索、导出给星空选品 Skill 的知识包、会话收尾。**读懂内容并写知识卡是模型的事**。

零强依赖：PyYAML / pypdf / openpyxl 存在则用，缺失则降级（frontmatter 用内置极简解析器；PDF/XLSX 标 needs_*）。

用法（<kb> = 知识库根目录）：
  python kb.py init     --kb <kb> [--name "星空选品知识库"]
  python kb.py ingest   --kb <kb> --path <文件或目录> [--authority B] [--tag topic=sp_bidding] [--max 50]
  python kb.py fetch    --kb <kb> (--url <u> | --urls-file <f> | --seeds) [--authority A] [--max 10]
  python kb.py plan     --kb <kb> [--budget-sources 6] [--budget-topics 3] [--prefer A|B|C]
  python kb.py validate --kb <kb>
  python kb.py index    --kb <kb>
  python kb.py query    --kb <kb> [--tag selection_mode=精铺 --tag selection_phase=难度判定 ...] [--text "否词"] [--type playbook] [--min-confidence 0.5] [--limit 20] [--format json|md]
  python kb.py export   --kb <kb> [--out knowledge_pack_for_selection.yaml]
  python kb.py close    --kb <kb> --session <SESSION-ID> --sources SRC-0001,SRC-0002 --cards 8 [--notes "..."]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import html as _html
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
TEMPLATES = SKILL_ROOT / "templates"

try:
    import yaml as _yaml  # type: ignore
except ImportError:  # pragma: no cover
    _yaml = None

TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".json", ".jsonl", ".ndjson", ".yaml", ".yml", ".log", ".srt", ".vtt"}
HTML_EXT = {".html", ".htm"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
MEDIA_EXT = {".mp4", ".mp3", ".m4a", ".wav", ".mov", ".mkv"}
DEFAULT_ALLOWLIST = {
    "sellercentral.amazon.com": "A",
    "sellercentral.amazon.cn": "A",
    "sell.amazon.com": "A",
    "sell.amazon.cn": "A",
    "brandservices.amazon.com": "A",
    "sellercentral.amazon.co.uk": "A",
    "sellercentral.amazon.de": "A",
}
UA = "xk-selection-knowledge-learner/1.0 (+personal study; respects robots.txt)"


# ============================================================ 通用
def now_iso() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def today() -> str:
    return _dt.date.today().isoformat()


def sha256_bytes(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for ch in iter(lambda: f.read(1 << 20), b""):
            h.update(ch)
    return "sha256:" + h.hexdigest()


def read_text_any(p: Path) -> str:
    b = p.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", errors="replace")


def _jsonable(obj):
    """PyYAML 会把 2026-08-09 解析成 date；统一转成 ISO 字符串，集合/元组转列表。"""
    if isinstance(obj, (_dt.date, _dt.datetime)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v) for v in obj]
    return obj


def jdumps(obj, **kw) -> str:
    kw.setdefault("ensure_ascii", False)
    return json.dumps(_jsonable(obj), **kw)


def load_yaml(p: Path):
    text = read_text_any(p)
    if text.lstrip().startswith("{") or text.lstrip().startswith("["):
        return json.loads(text)
    if _yaml:
        return _jsonable(_yaml.safe_load(text))
    return simple_yaml(text)


def dump_yaml(obj, p: Path) -> None:
    """机器生成的 .yaml 一律写 JSON 语法（JSON ⊂ YAML 1.2）：有无 PyYAML 的机器都能无歧义读取、跨机器搬库不出错。
    人手维护的 taxonomy.yaml / learning_plan.yaml / 卡片 frontmatter 仍是普通 YAML。"""
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(jdumps(obj, indent=1) + "\n", encoding="utf-8")


# ---- 极简 YAML（无 PyYAML 时用；覆盖卡片 frontmatter 用到的子集）----
def _scalar(v: str):
    v = v.strip()
    if v == "" or v in ("null", "~", "None"):
        return None
    if v in ("true", "True"):
        return True
    if v in ("false", "False"):
        return False
    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
        return v[1:-1]
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        return [_scalar(x) for x in _split_top(inner)] if inner else []
    if v.startswith("{") and v.endswith("}"):
        inner = v[1:-1].strip()
        out = {}
        for part in _split_top(inner):
            if ":" in part:
                k, vv = part.split(":", 1)
                out[k.strip()] = _scalar(vv)
        return out
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


def _split_top(s: str) -> list[str]:
    out, depth, cur, quote = [], 0, "", None
    for ch in s:
        if quote:
            cur += ch
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            cur += ch
        elif ch in "[{":
            depth += 1
            cur += ch
        elif ch in "]}":
            depth -= 1
            cur += ch
        elif ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return [x.strip() for x in out]


def _join_flow_lines(lines: list[str]) -> list[str]:
    """把跨多行的 {…} / […] 流式结构并成一行（按括号深度），供极简解析器处理。"""
    out, buf, depth = [], "", 0
    for ln in lines:
        seg = ln if not buf else " " + ln.strip()
        buf += seg
        quote = None
        for ch in ln:
            if quote:
                if ch == quote:
                    quote = None
                continue
            if ch in "\"'":
                quote = ch
            elif ch in "[{":
                depth += 1
            elif ch in "]}":
                depth -= 1
        if depth <= 0:
            out.append(buf)
            buf, depth = "", 0
    if buf:
        out.append(buf)
    return out


def simple_yaml(text: str):
    lines = [ln.rstrip("\n") for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    lines = [re.sub(r"\s+#.*$", "", ln) if not re.search(r"[\"'].*#.*[\"']", ln) else ln for ln in lines]
    lines = _join_flow_lines(lines)

    def indent_of(ln: str) -> int:
        return len(ln) - len(ln.lstrip(" "))

    def is_map_start(s: str) -> bool:
        # `key: value` 且不是流式/引号开头；冒号后必须是空格或行尾（避免把 URL 里的冒号当键）
        return bool(re.match(r"^[A-Za-z0-9_\-\u4e00-\u9fff.]+:(\s|$)", s)) and not s.startswith(("[", "{", '"', "'"))

    def parse_block(i: int, indent: int):
        result = None
        while i < len(lines):
            ln = lines[i]
            cur = indent_of(ln)
            if cur < indent:
                break
            if cur > indent:  # 归属于上一级未消费的缩进（容错跳过）
                i += 1
                continue
            body = ln.strip()
            if body.startswith("- ") or body == "-":
                if result is None:
                    result = []
                item = body[2:].strip() if body != "-" else ""
                if item and is_map_start(item):
                    # 把 "- key: v" 视为在 indent+2 处开始的映射，后续同缩进行属于同一项
                    lines[i] = " " * (indent + 2) + item
                    sub, i = parse_block(i, indent + 2)
                    result.append(sub)
                else:
                    result.append(_scalar(item) if item else None)
                    i += 1
            else:
                if result is None:
                    result = {}
                if not is_map_start(body):
                    i += 1
                    continue
                k, v = body.split(":", 1)
                k = k.strip()
                if v.strip() == "":
                    # 嵌套块：以下一行的实际缩进为准
                    nxt = lines[i + 1] if i + 1 < len(lines) else None
                    nxt_indent = indent_of(nxt) if nxt is not None else indent
                    if nxt is not None and nxt_indent > indent:
                        sub, i = parse_block(i + 1, nxt_indent)
                        result[k] = sub if sub is not None else {}
                    elif nxt is not None and nxt_indent == indent and nxt.strip().startswith("- "):
                        # PyYAML 默认的“无缩进序列”：列表项与键同缩进
                        sub, i = parse_list_at(i + 1, indent)
                        result[k] = sub
                    else:
                        result[k] = None
                        i += 1
                else:
                    result[k] = _scalar(v)
                    i += 1
        return result, i

    def parse_list_at(i: int, indent: int):
        items = []
        while i < len(lines):
            ln = lines[i]
            if indent_of(ln) != indent or not ln.strip().startswith("- "):
                break
            item = ln.strip()[2:].strip()
            if item and is_map_start(item):
                lines[i] = " " * (indent + 2) + item
                sub, i = parse_block(i, indent + 2)
                items.append(sub)
            else:
                items.append(_scalar(item) if item else None)
                i += 1
        return items, i

    obj, _ = parse_block(0, 0)
    return obj or {}


def parse_frontmatter(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    fm_text, body = m.group(1), m.group(2)
    fm = _yaml.safe_load(fm_text) if _yaml else simple_yaml(fm_text)
    return _jsonable(fm or {}), body


def kb_paths(kb: Path) -> dict:
    return {
        "root": kb, "manifest": kb / "kb_manifest.yaml", "taxonomy": kb / "taxonomy.yaml", "plan": kb / "learning_plan.yaml",
        "cards": kb / "cards", "playbooks": kb / "playbooks", "sources": kb / "sources", "src_manifest": kb / "sources" / "manifest.yaml",
        "extracted": kb / "sources" / "extracted", "raw": kb / "sources" / "raw", "allowlist": kb / "sources" / "allowlist.yaml",
        "sessions": kb / "sessions", "index": kb / "knowledge_index.yaml", "coverage": kb / "coverage_report.md",
        "conflicts": kb / "conflicts.md", "gaps": kb / "gaps.yaml", "log": kb / "learning_log.md", "changelog": kb / "CHANGELOG.md",
        "validation": kb / "validation_report.json", "pack": kb / "knowledge_pack_for_selection.yaml",
    }


def require_kb(kb: Path) -> dict:
    P = kb_paths(kb)
    if not P["manifest"].exists():
        raise SystemExit(f"[kb] {kb} 不是知识库（缺 kb_manifest.yaml）。先 `kb.py init --kb {kb}`")
    return P


def load_src_manifest(P: dict) -> dict:
    return load_yaml(P["src_manifest"]) if P["src_manifest"].exists() else {"sources": []}


def next_id(prefix: str, existing: list[str]) -> str:
    nums = [int(m.group(1)) for x in existing for m in [re.match(rf"{prefix}-(\d+)", str(x))] if m]
    return f"{prefix}-{(max(nums) + 1 if nums else 1):04d}"


# ============================================================ init
def cmd_init(a) -> int:
    kb = Path(a.kb).resolve()
    P = kb_paths(kb)
    if P["manifest"].exists():
        print(f"[kb] 已存在：{kb}")
        return 0
    for d in ("cards", "playbooks", "extracted", "raw", "sessions"):
        P[d].mkdir(parents=True, exist_ok=True)
    for tpl in ("taxonomy.yaml", "learning_plan.yaml"):
        (kb / tpl).write_text((TEMPLATES / tpl).read_text(encoding="utf-8"), encoding="utf-8")
    dump_yaml({"schema_version": "1.0", "name": a.name or kb.name, "created": today(), "updated": today(),
               "skill": "amazon-selection-knowledge-learner", "stats": {"sources": 0, "cards": 0, "playbooks": 0}}, P["manifest"])
    dump_yaml({"sources": []}, P["src_manifest"])
    dump_yaml({"domains": DEFAULT_ALLOWLIST, "note": "域名 → 默认 authority。加第三方站点时给 C；厂商站点给 V。抓取前会查 robots.txt。"}, P["allowlist"])
    dump_yaml({"gaps": [], "note": "plan 会追加未覆盖主题；模型学习中发现的开放问题也记这里（含 why / 需要什么来源）"}, P["gaps"])
    P["conflicts"].write_text("# 冲突登记\n\n| # | 冲突主题 | 卡 A | 卡 B | 差异 | 裁决/处理 | 日期 |\n|---|---|---|---|---|---|---|\n", encoding="utf-8")
    P["log"].write_text(f"# 学习日志\n\n- {today()} 建库（amazon-selection-knowledge-learner）\n", encoding="utf-8")
    P["changelog"].write_text(f"# CHANGELOG\n\n## {today()}\n- 建库\n", encoding="utf-8")
    (kb / "README.md").write_text(
        f"# {a.name or kb.name}\n\n亚马逊选品知识库（由 amazon-selection-knowledge-learner 维护）。\n\n"
        "```text\n"
        "kb_manifest.yaml           库元数据与统计\n"
        "taxonomy.yaml              受控词表（标签枚举 / 证据层级 / 引文纪律）\n"
        "learning_plan.yaml         学习大纲（主题 × min_cards × seed_urls）→ 驱动自主进化\n"
        "sources/manifest.yaml      来源登记（SHA-256 / 类型 / 层级 / 状态）\n"
        "sources/extracted/         抽取文本（SRC-xxxx.md，模型阅读用）\n"
        "sources/raw/               抓取的官方页面原件（仅回溯，不对外分发）\n"
        "cards/                     知识卡（KC-xxxx.md，一卡一知识点，带出处/标签/置信度）\n"
        "playbooks/                 场景手册（PB-*.md，≥5 张卡综合）\n"
        "knowledge_index.yaml       索引（index 命令生成）\n"
        "coverage_report.md         覆盖度（维度矩阵 × 大纲主题）\n"
        "conflicts.md / gaps.yaml   冲突登记 / 开放问题\n"
        "knowledge_pack_for_selection.yaml 导出给星空选品 Skill 的知识包（export 命令）\n"
        "sessions/                  每次学习任务单与收尾记录\n"
        "learning_log.md            学习日志\n"
        "```\n", encoding="utf-8")
    print(jdumps({"status": "ok", "kb": str(kb), "created": ["cards/", "playbooks/", "sources/", "sessions/", "taxonomy.yaml", "learning_plan.yaml", "kb_manifest.yaml"]}, ensure_ascii=False, indent=1))
    return 0


# ============================================================ 文本抽取
def html_to_text(raw: str) -> str:
    s = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header|form|iframe)[^>]*>.*?</\1>", " ", raw)
    s = re.sub(r"(?is)<!--.*?-->", " ", s)
    s = re.sub(r"(?i)<h([1-6])[^>]*>", lambda m: "\n" + "#" * int(m.group(1)) + " ", s)
    s = re.sub(r"(?i)</h[1-6]>", "\n", s)
    s = re.sub(r"(?i)<li[^>]*>", "\n- ", s)
    s = re.sub(r"(?i)<(p|div|section|article|tr|table|ul|ol|blockquote)[^>]*>", "\n", s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)<td[^>]*>", " | ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = _html.unescape(s)
    s = re.sub(r"[ \t\u00a0]+", " ", s)
    s = re.sub(r"\n\s*\n\s*\n+", "\n\n", s)
    return s.strip()


def docx_text(p: Path) -> str:
    with zipfile.ZipFile(p) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab/>", "\t", xml)
    return _html.unescape(re.sub(r"<[^>]+>", "", xml))


def pptx_text(p: Path) -> str:
    out = []
    with zipfile.ZipFile(p) as z:
        slide_re = re.compile(r"ppt/slides/slide(\d+)\.xml")
        slides = sorted([n for n in z.namelist() if slide_re.match(n)], key=lambda n: int(slide_re.match(n).group(1)))
        for n in slides:
            xml = z.read(n).decode("utf-8", errors="replace")
            texts = re.findall(r"<a:t>(.*?)</a:t>", xml, re.S)
            num = slide_re.match(n).group(1)
            joined = "\n".join(_html.unescape(t) for t in texts)
            out.append("\n--- slide " + num + " ---\n" + joined)
    return "\n".join(out)


def xmind_text(p: Path) -> str:
    with zipfile.ZipFile(p) as z:
        names = z.namelist()
        if "content.json" in names:
            data = json.loads(z.read("content.json").decode("utf-8", errors="replace"))
            out = []

            def walk(node, depth=0):
                if isinstance(node, dict):
                    t = node.get("title")
                    if t:
                        out.append("  " * depth + "- " + str(t))
                    for ch in (node.get("children") or {}).get("attached", []) if isinstance(node.get("children"), dict) else []:
                        walk(ch, depth + 1)
                    if "rootTopic" in node:
                        walk(node["rootTopic"], depth)
                elif isinstance(node, list):
                    for x in node:
                        walk(x, depth)

            walk(data)
            return "\n".join(out)
        if "content.xml" in names:
            xml = z.read("content.xml").decode("utf-8", errors="replace")
            return "\n".join(_html.unescape(t) for t in re.findall(r"<title[^>]*>(.*?)</title>", xml, re.S))
    return ""


def extract_text(p: Path) -> tuple[str | None, str]:
    """返回 (text, status)。status: ok | needs_pypdf | needs_openpyxl | needs_vision | needs_transcript | unsupported | error:<msg>"""
    ext = p.suffix.lower()
    try:
        if ext in TEXT_EXT:
            return read_text_any(p), "ok"
        if ext in HTML_EXT:
            return html_to_text(read_text_any(p)), "ok"
        if ext == ".docx":
            return docx_text(p), "ok"
        if ext == ".pptx":
            return pptx_text(p), "ok"
        if ext == ".xmind":
            return xmind_text(p), "ok"
        if ext == ".pdf":
            try:
                from pypdf import PdfReader  # type: ignore
            except ImportError:
                return None, "needs_pypdf"
            r = PdfReader(str(p))
            return "\n".join(f"\n--- page {i + 1} ---\n" + (pg.extract_text() or "") for i, pg in enumerate(r.pages)), "ok"
        if ext in (".xlsx", ".xlsm"):
            try:
                import openpyxl  # type: ignore
            except ImportError:
                return None, "needs_openpyxl"
            wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
            out = []
            for ws in wb.worksheets:
                out.append(f"\n--- sheet {ws.title} ---")
                for row in ws.iter_rows(values_only=True):
                    if any(c not in (None, "") for c in row):
                        out.append("\t".join("" if c is None else str(c) for c in row))
            return "\n".join(out), "ok"
        if ext in IMG_EXT:
            return None, "needs_vision"
        if ext in MEDIA_EXT:
            return None, "needs_transcript"
        return None, "unsupported"
    except Exception as e:  # noqa: BLE001
        return None, f"error:{str(e)[:120]}"


# ============================================================ ingest
def register_source(P: dict, man: dict, *, path_or_url: str, kind: str, sha: str, size: int, authority: str, tags: dict,
                    text: str | None, status: str, extra: dict | None = None) -> dict:
    if text:
        text = text.encode("utf-8", errors="ignore").decode("utf-8")  # pypdf 可能抽出孤立代理字符，UTF-8 写盘会崩
    ids = [s["source_id"] for s in man["sources"]]
    sid = next_id("SRC", ids)
    entry = {"source_id": sid, "acquisition": kind, "locator": path_or_url, "sha256": sha, "size_bytes": size,
             "authority": authority, "tags": tags, "registered_at": now_iso(), "status": "pending" if text else status,
             "extract_status": status, "chars": len(text) if text else 0, "chunks": math.ceil(len(text) / 3000) if text else 0,
             "extracted_path": None, "ingested_at": None, "cards": []}
    if extra:
        entry.update(extra)
    if text:
        head = (f"---\nsource_id: {sid}\nlocator: \"{path_or_url}\"\nauthority: {authority}\nsha256: \"{sha}\"\nextracted_at: {now_iso()}\n"
                f"chars: {len(text)}\nchunks: {entry['chunks']}\nnote: 抽取文本仅供本库学习回溯；官方页面不得对外再分发\n---\n\n")
        out = P["extracted"] / f"{sid}.md"
        out.write_text(head + text, encoding="utf-8")
        entry["extracted_path"] = f"sources/extracted/{sid}.md"
    man["sources"].append(entry)
    return entry


def cmd_ingest(a) -> int:
    kb = Path(a.kb).resolve()
    P = require_kb(kb)
    man = load_src_manifest(P)
    known = {s["sha256"] for s in man["sources"]}
    root = Path(a.path)
    if not root.exists():
        raise SystemExit(f"[kb] 路径不存在：{root}")
    files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file() and not any(part.startswith(".") for part in p.parts))
    if a.include:
        rx = re.compile(a.include, re.I)
        files = [p for p in files if rx.search(str(p.relative_to(root)) if root.is_dir() else p.name)]
    if a.exclude:
        rx = re.compile(a.exclude, re.I)
        files = [p for p in files if not rx.search(str(p.relative_to(root)) if root.is_dir() else p.name)]
    tags = {}
    for kv in a.tag or []:
        k, _, v = kv.partition("=")
        tags.setdefault(k.strip(), []).append(v.strip())
    added, skipped_dup, by_status = [], 0, Counter()
    for fp in files:
        if a.max and len(added) >= a.max:
            break
        if fp.suffix.lower() in (".sha256", ".lock") or fp.name in ("SHA256SUMS.txt", "Thumbs.db"):
            continue
        sha = sha256_file(fp)
        if sha in known:
            skipped_dup += 1
            continue
        text, status = extract_text(fp)
        if text is not None and len(text.strip()) < 40:
            text, status = None, "empty"
        elif text is not None and fp.suffix.lower() in (".pdf", ".pptx", ".ppt") and len(text.strip()) < 500:
            # 文字层过薄的 PDF/PPT 多为图片课件：不进待学队列，提示 OCR / vision 后再学
            text, status = None, "thin_needs_ocr"
        entry = register_source(P, man, path_or_url=str(fp), kind="file", sha=sha, size=fp.stat().st_size, authority=a.authority,
                                tags=tags, text=text, status=status, extra={"filename": fp.name, "ext": fp.suffix.lower(),
                                                                                "root": str(root), "relative": str(fp.relative_to(root)) if root.is_dir() else fp.name})
        known.add(sha)
        added.append(entry)
        by_status[status] += 1
    dump_yaml(man, P["src_manifest"])
    _bump_stats(P)
    print(jdumps({"status": "ok", "root": str(root), "scanned": len(files), "added": len(added), "skipped_duplicate": skipped_dup,
                      "extract_status": dict(by_status), "pending_total": sum(1 for s in man["sources"] if s["status"] == "pending"),
                      "needs_attention": [{"source_id": e["source_id"], "file": e["filename"], "why": e["extract_status"]} for e in added if e["extract_status"] != "ok"][:20],
                      "next": "python kb.py plan --kb <kb>  → 拿任务单开始学"}, ensure_ascii=False, indent=1))
    return 0


# ============================================================ fetch
def fetch_url(url: str, timeout: int = 40) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        ctype = resp.headers.get("Content-Type", "")
        return resp.read(), ctype


def robots_ok(url: str) -> tuple[bool, str]:
    u = urllib.parse.urlparse(url)
    rp = urllib.robotparser.RobotFileParser()
    robots = f"{u.scheme}://{u.netloc}/robots.txt"
    try:
        rp.set_url(robots)
        rp.read()
        ok = rp.can_fetch(UA, url) and rp.can_fetch("*", url)
        return ok, "allowed" if ok else "disallowed_by_robots"
    except Exception as e:  # noqa: BLE001
        return True, f"robots_unreadable({str(e)[:40]})"


def cmd_fetch(a) -> int:
    kb = Path(a.kb).resolve()
    P = require_kb(kb)
    man = load_src_manifest(P)
    allow = load_yaml(P["allowlist"]).get("domains", DEFAULT_ALLOWLIST)
    urls: list[str] = []
    if a.url:
        urls += a.url
    if a.urls_file:
        urls += [ln.strip() for ln in read_text_any(Path(a.urls_file)).splitlines() if ln.strip() and not ln.startswith("#")]
    if a.seeds:
        plan = load_yaml(P["plan"])
        for mod in plan.get("modules", []):
            for t in mod.get("topics", []):
                urls += t.get("seed_urls") or []
    urls = list(dict.fromkeys(urls))
    known_urls = {s.get("locator") for s in man["sources"] if s.get("acquisition") == "web"}
    results, done = [], 0
    for url in urls:
        if a.max and done >= a.max:
            break
        host = urllib.parse.urlparse(url).netloc.lower()
        if host not in allow:
            results.append({"url": url, "status": "skipped_not_in_allowlist", "hint": f"加到 {P['allowlist'].name} 的 domains 并给 authority"})
            continue
        if url in known_urls and not a.refresh:
            results.append({"url": url, "status": "skipped_already_fetched"})
            continue
        ok, why = robots_ok(url)
        if not ok:
            results.append({"url": url, "status": why})
            continue
        try:
            raw, ctype = fetch_url(url)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            results.append({"url": url, "status": f"fetch_failed:{str(e)[:80]}"})
            continue
        text = html_to_text(raw.decode("utf-8", errors="replace")) if "html" in ctype or raw[:200].lower().find(b"<html") >= 0 else raw.decode("utf-8", errors="replace")
        title_m = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw.decode("utf-8", errors="replace"))
        title = _html.unescape(title_m.group(1)).strip() if title_m else url
        sha = sha256_bytes(raw)
        authority = a.authority or allow.get(host, "C")
        entry = register_source(P, man, path_or_url=url, kind="web", sha=sha, size=len(raw), authority=authority, tags={},
                                text=text if len(text.strip()) >= 200 else None, status="ok" if len(text.strip()) >= 200 else "thin_or_js_rendered",
                                extra={"title": title[:200], "fetched_at": now_iso(), "content_type": ctype[:60], "robots": why})
        (P["raw"] / f"{entry['source_id']}.html").write_bytes(raw)
        results.append({"url": url, "status": "ok" if entry["extracted_path"] else "thin_or_js_rendered", "source_id": entry["source_id"], "chars": entry["chars"], "title": title[:80]})
        done += 1
        time.sleep(a.delay)
    dump_yaml(man, P["src_manifest"])
    _bump_stats(P)
    print(jdumps({"status": "ok", "requested": len(urls), "fetched": done, "results": results,
                      "note": "raw/ 原件仅回溯；thin_or_js_rendered 的页面需在浏览器另存为 HTML 后走 ingest"}, ensure_ascii=False, indent=1))
    return 0


# ============================================================ cards
def load_cards(P: dict) -> list[dict]:
    cards = []
    for fp in sorted(P["cards"].glob("*.md")):
        try:
            fm, body = parse_frontmatter(read_text_any(fp))
        except Exception as e:  # noqa: BLE001
            cards.append({"_file": fp.name, "_error": f"frontmatter 解析失败：{e}"})
            continue
        if not fm:
            cards.append({"_file": fp.name, "_error": "无 frontmatter"})
            continue
        fm["_file"] = fp.name
        fm["_body"] = body
        m = re.search(r"##\s*结论[^\n]*\n+(.+?)(?:\n\s*\n|\n##|\Z)", body, re.S)
        fm["_statement"] = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
        cards.append(fm)
    return cards


def load_playbooks(P: dict) -> list[dict]:
    out = []
    for fp in sorted(P["playbooks"].glob("PB-*.md")):
        try:
            fm, body = parse_frontmatter(read_text_any(fp))
            fm["_file"] = fp.name
            out.append(fm)
        except Exception:  # noqa: BLE001
            continue
    return out


def word_count(s: str) -> int:
    s = str(s or "")
    cjk = len(re.findall(r"[\u4e00-\u9fff]", s))
    words = len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]*", s))
    return cjk + words


def cmd_validate(a) -> int:
    kb = Path(a.kb).resolve()
    P = require_kb(kb)
    tax = load_yaml(P["taxonomy"])
    man = load_src_manifest(P)
    src_ids = {s["source_id"] for s in man["sources"]}
    cards = load_cards(P)
    errors, warns = [], []
    ids = Counter()
    tag_enums = tax.get("tags", {})
    auth = tax.get("authority", {})
    ktypes = set(tax.get("knowledge_type", {}).keys())
    statuses = set(tax.get("status", []))
    qmax = int((tax.get("rules") or {}).get("quote_max_words", 60))
    for c in cards:
        f = c.get("_file")
        if c.get("_error"):
            errors.append(f"{f}: {c['_error']}")
            continue
        cid = str(c.get("id") or "")
        if not re.fullmatch(r"KC-\d{4,}", cid):
            errors.append(f"{f}: id 缺失或格式错（KC-0001）")
        ids[cid] += 1
        if cid and f != f"{cid}.md":
            warns.append(f"{f}: 文件名应为 {cid}.md")
        for req in ("title", "knowledge_type", "authority", "confidence", "status", "created", "updated", "tags", "sources"):
            if req not in c:
                errors.append(f"{cid or f}: 缺字段 {req}")
        if c.get("knowledge_type") not in ktypes:
            errors.append(f"{cid}: knowledge_type `{c.get('knowledge_type')}` 不在枚举")
        au = str(c.get("authority") or "")
        if au not in auth:
            errors.append(f"{cid}: authority `{au}` 不在 A/B/C/D/V")
        else:
            lo, hi = auth[au]["confidence_range"]
            try:
                cf = float(c.get("confidence"))
                if not (lo <= cf <= hi):
                    errors.append(f"{cid}: confidence {cf} 超出 {au} 级范围 [{lo},{hi}]")
            except (TypeError, ValueError):
                errors.append(f"{cid}: confidence 非数值")
        if c.get("status") not in statuses:
            errors.append(f"{cid}: status `{c.get('status')}` 不在枚举")
        tags = c.get("tags") or {}
        if not isinstance(tags, dict):
            errors.append(f"{cid}: tags 必须是映射")
            tags = {}
        for must in ("selection_mode", "entry_path", "selection_phase", "decision_gate", "topic"):
            if not tags.get(must):
                errors.append(f"{cid}: tags.{must} 必填（不适用填 [通用]）")
        for dim, vals in tags.items():
            if dim not in tag_enums:
                errors.append(f"{cid}: 未知标签维度 {dim}")
                continue
            for v in (vals if isinstance(vals, list) else [vals]):
                if v not in tag_enums[dim]:
                    errors.append(f"{cid}: tags.{dim} 值 `{v}` 不在枚举（改 taxonomy.yaml 或换词）")
        srcs = c.get("sources") or []
        if not isinstance(srcs, list) or not srcs:
            errors.append(f"{cid}: sources 至少一条")
        for s in srcs if isinstance(srcs, list) else []:
            if not isinstance(s, dict):
                errors.append(f"{cid}: sources 条目必须是映射")
                continue
            if s.get("source_id") not in src_ids:
                errors.append(f"{cid}: source_id `{s.get('source_id')}` 未在 sources/manifest.yaml 登记")
            if not s.get("locator"):
                warns.append(f"{cid}: source `{s.get('source_id')}` 缺 locator（页码/章节/时间码）")
            if word_count(s.get("quote")) > qmax:
                errors.append(f"{cid}: 摘录超过 {qmax} 词（{word_count(s.get('quote'))}）——只摘要不镜像")
            if not s.get("date"):
                warns.append(f"{cid}: source `{s.get('source_id')}` 缺 date")
        if c.get("knowledge_type") == "threshold" and not c.get("parameters"):
            errors.append(f"{cid}: threshold 卡必须有 parameters（value/unit/note）")
        if c.get("knowledge_type") == "inference" and "验证" not in c.get("_body", ""):
            warns.append(f"{cid}: inference 卡正文应写“如何验证”")
        if not c.get("_statement"):
            errors.append(f"{cid}: 正文缺 `## 结论` 段或结论为空")
        for ref in (c.get("conflicts_with") or []) + (c.get("related") or []):
            if not re.fullmatch(r"KC-\d{4,}", str(ref)):
                warns.append(f"{cid}: 引用 `{ref}` 不是卡 ID")
        if c.get("status") == "reviewed" and au in ("B", "C") and len(srcs) < 2:
            warns.append(f"{cid}: {au} 级卡 reviewed 建议 ≥2 源印证")
    for cid, n in ids.items():
        if n > 1:
            errors.append(f"重复 id：{cid} ×{n}")
    all_ids = set(ids)
    for c in cards:
        for ref in (c.get("conflicts_with") or []) + (c.get("related") or []):
            if str(ref) not in all_ids:
                warns.append(f"{c.get('id')}: 引用的 {ref} 不存在")
    # 手册
    for pb in load_playbooks(P):
        cited = pb.get("cards_cited") or []
        if len(cited) < 5:
            warns.append(f"{pb['_file']}: 手册引用卡 <5（{len(cited)}），按规则应先攒卡")
        for ref in cited:
            if str(ref) not in all_ids:
                errors.append(f"{pb['_file']}: 引用卡 {ref} 不存在")
    rep = {"validator": "kb.py validate 1.0", "checked_at": now_iso(), "cards": len(cards), "result": "PASS" if not errors else "FAIL",
           "errors": errors, "warnings": warns}
    P["validation"].write_text(jdumps(rep, indent=2), encoding="utf-8")
    print(jdumps(rep, ensure_ascii=False, indent=1))
    return 0 if not errors else 1


# ============================================================ index / coverage
def _bump_stats(P: dict) -> None:
    man = load_yaml(P["manifest"])
    src = load_src_manifest(P)
    man["stats"] = {"sources": len(src["sources"]), "sources_pending": sum(1 for s in src["sources"] if s["status"] == "pending"),
                    "cards": len(list(P["cards"].glob("KC-*.md"))), "playbooks": len(list(P["playbooks"].glob("PB-*.md")))}
    man["updated"] = today()
    dump_yaml(man, P["manifest"])


def topic_coverage(P: dict, cards: list[dict]) -> list[dict]:
    plan = load_yaml(P["plan"])
    by_topic = Counter()
    for c in cards:
        for t in ((c.get("tags") or {}).get("topic") or []):
            by_topic[t] += 1
    rows = []
    for mod in plan.get("modules", []):
        for t in mod.get("topics", []):
            have = by_topic.get(t["id"], 0)
            need = int(t.get("min_cards", 1))
            rows.append({"module": mod["id"], "topic": t["id"], "name": t["name"], "priority": int(t.get("priority", 3)),
                         "have": have, "need": need, "gap": max(0, need - have), "seed_urls": t.get("seed_urls") or []})
    return rows


def cmd_index(a) -> int:
    kb = Path(a.kb).resolve()
    P = require_kb(kb)
    cards = [c for c in load_cards(P) if not c.get("_error")]
    tax = load_yaml(P["taxonomy"])
    warn_days = int((tax.get("rules") or {}).get("freshness_warn_days", 365))
    man = load_src_manifest(P)
    src_by_id = {s["source_id"]: s for s in man["sources"]}
    idx = []
    for c in cards:
        newest = None
        for s in c.get("sources") or []:
            d = str((s or {}).get("date") or "")
            if re.match(r"\d{4}-\d{2}-\d{2}", d):
                newest = max(newest or d, d)
        stale = False
        if newest and c.get("knowledge_type") in ("mechanism", "policy"):
            try:
                stale = (_dt.date.today() - _dt.date.fromisoformat(newest[:10])).days > warn_days
            except ValueError:
                stale = False
        idx.append({"id": c.get("id"), "title": c.get("title"), "knowledge_type": c.get("knowledge_type"), "authority": c.get("authority"),
                    "confidence": c.get("confidence"), "status": c.get("status"), "tags": c.get("tags"), "statement": c.get("_statement"),
                    "parameters": c.get("parameters") or {}, "sources": [{"source_id": s.get("source_id"), "locator": s.get("locator"), "date": s.get("date"),
                                                                          "authority": src_by_id.get(s.get("source_id"), {}).get("authority")} for s in (c.get("sources") or []) if isinstance(s, dict)],
                    "conflicts_with": c.get("conflicts_with") or [], "related": c.get("related") or [], "starsky_hooks": c.get("starsky_hooks") or [],
                    "newest_source_date": newest, "stale": stale, "file": f"cards/{c['_file']}"})
    pbs = load_playbooks(P)
    dump_yaml({"schema_version": "1.0", "generated_at": now_iso(), "cards": idx,
               "playbooks": [{"id": p.get("id"), "title": p.get("title"), "scenario": p.get("scenario"), "status": p.get("status"), "cards_cited": p.get("cards_cited") or [], "file": f"playbooks/{p['_file']}"} for p in pbs]},
              P["index"])

    # coverage
    rows = topic_coverage(P, cards)
    dims = ("selection_mode", "entry_path", "selection_phase", "decision_gate", "knowledge_type_", "authority_")
    L = [f"# 覆盖度报告 · {kb.name}", "", f"- 生成 {now_iso()} ｜ 卡 {len(cards)} ｜ 手册 {len(pbs)} ｜ 来源 {len(man['sources'])}（待学 {sum(1 for s in man['sources'] if s['status'] == 'pending')}）", ""]
    L += ["## 一、大纲主题覆盖（gap>0 = 待学）", "", "| 模块 | 主题 | 优先级 | 已有 | 需要 | 缺口 |", "|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: (r["priority"], -r["gap"])):
        L.append(f"| {r['module']} | {r['topic']} {r['name']} | P{r['priority']} | {r['have']} | {r['need']} | {'**' + str(r['gap']) + '**' if r['gap'] else '✅'} |")
    tot_need = sum(r["need"] for r in rows)
    tot_have = sum(min(r["have"], r["need"]) for r in rows)
    L += ["", f"大纲完成度：{tot_have}/{tot_need} = {tot_have / max(1, tot_need):.0%}", ""]
    L += ["## 二、场景矩阵（selection_mode × selection_phase，卡数）", ""]
    pts = tax["tags"]["selection_mode"]
    sts = tax["tags"]["selection_phase"]
    L.append("| selection_mode \\ selection_phase | " + " | ".join(sts) + " |")
    L.append("|---|" + "---|" * len(sts))
    for pt in pts:
        row = []
        for st in sts:
            n = sum(1 for c in cards if pt in ((c.get("tags") or {}).get("selection_mode") or []) and st in ((c.get("tags") or {}).get("selection_phase") or []))
            row.append(str(n) if n else "·")
        L.append(f"| {pt} | " + " | ".join(row) + " |")
    L += ["", "## 三、分布", ""]
    for dim in ("selection_mode", "entry_path", "selection_phase"):
        cnt = Counter(v for c in cards for v in ((c.get("tags") or {}).get(dim) or []))
        L.append(f"- {dim}：" + " ｜ ".join(f"{k} {v}" for k, v in cnt.most_common()) if cnt else f"- {dim}：（无）")
    L.append("- knowledge_type：" + " ｜ ".join(f"{k} {v}" for k, v in Counter(c.get("knowledge_type") for c in cards).most_common()))
    L.append("- authority：" + " ｜ ".join(f"{k} {v}" for k, v in Counter(c.get("authority") for c in cards).most_common()))
    L.append("- status：" + " ｜ ".join(f"{k} {v}" for k, v in Counter(c.get("status") for c in cards).most_common()))
    stale = [c["id"] for c in idx if c["stale"]]
    L += ["", f"## 四、时效告警（mechanism/policy 来源 > {warn_days} 天）", "", ("、".join(stale) if stale else "无"), ""]
    conflicts = [(c["id"], c["conflicts_with"]) for c in idx if c["conflicts_with"]]
    L += ["## 五、冲突卡", "", *(f"- {a} ↔ {'、'.join(map(str, b))}" for a, b in conflicts)] if conflicts else ["## 五、冲突卡", "", "无"]
    P["coverage"].write_text("\n".join(L) + "\n", encoding="utf-8")

    # playbooks TOC
    (P["playbooks"] / "README.md").write_text("# 场景手册目录\n\n" + ("\n".join(f"- [{p.get('title')}]({p['_file']}) — {p.get('status')}，引用 {len(p.get('cards_cited') or [])} 卡" for p in pbs) or "（尚无手册；同一场景 ≥5 张卡后用 templates/playbook.template.md 综合）") + "\n", encoding="utf-8")

    # gaps 追加
    gaps = load_yaml(P["gaps"]) if P["gaps"].exists() else {"gaps": []}
    existing = {g.get("topic") for g in gaps.get("gaps", []) if isinstance(g, dict)}
    for r in rows:
        if r["gap"] and r["topic"] not in existing:
            gaps.setdefault("gaps", []).append({"topic": r["topic"], "name": r["name"], "gap_cards": r["gap"], "priority": r["priority"], "seed_urls": r["seed_urls"], "added": today(), "status": "open"})
    for g in gaps.get("gaps", []):
        if isinstance(g, dict) and g.get("status") == "open":
            r = next((x for x in rows if x["topic"] == g.get("topic")), None)
            if r and r["gap"] == 0:
                g["status"] = "closed"
                g["closed"] = today()
    dump_yaml(gaps, P["gaps"])
    _bump_stats(P)
    print(jdumps({"status": "ok", "cards": len(cards), "playbooks": len(pbs), "curriculum_completion": f"{tot_have}/{tot_need}",
                      "open_gaps": sum(1 for r in rows if r["gap"]), "stale_cards": stale, "outputs": ["knowledge_index.yaml", "coverage_report.md", "gaps.yaml", "playbooks/README.md"]}, ensure_ascii=False, indent=1))
    return 0


# ============================================================ plan / close
def _active_sessions(P: dict, max_age_h: float = 6.0) -> list[dict]:
    """sessions/.active/<SESSION>.json：plan 登记、close 摘除；超过 max_age_h 视为遗留。"""
    d = P["sessions"] / ".active"
    out = []
    if d.exists():
        for f in d.glob("*.json"):
            try:
                j = json.loads(f.read_text(encoding="utf-8"))
                started = _dt.datetime.fromisoformat(j.get("started_at"))
                j["age_h"] = round((_dt.datetime.now() - started).total_seconds() / 3600, 1)
                j["stale"] = j["age_h"] > max_age_h
                out.append(j)
            except Exception:  # noqa: BLE001
                continue
    return out


def _register_session(P: dict, sid: str, picked: list[str]) -> None:
    d = P["sessions"] / ".active"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sid}.json").write_text(json.dumps({"session": sid, "started_at": now_iso(), "sources": picked, "host": os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "?"}, ensure_ascii=False), encoding="utf-8")


def _release_session(P: dict, sid: str) -> None:
    f = P["sessions"] / ".active" / f"{sid}.json"
    if f.exists():
        f.unlink()


def cmd_plan(a) -> int:
    kb = Path(a.kb).resolve()
    P = require_kb(kb)
    plan = load_yaml(P["plan"])
    others = [s for s in _active_sessions(P) if not s["stale"]]
    if others:
        print("⚠️ 同一知识库有其他进行中的学习会话（并行写卡会争抢 manifest/索引，建议一次只开一个）：")
        for s in others:
            print(f"   - {s['session']} 开始于 {s['started_at']}（{s['age_h']} 小时前，host={s.get('host')}），正在读 {', '.join(s.get('sources') or []) or '—'}")
        print("   如确认对方已结束未 close：删除 sessions/.active/<SESSION>.json 即可解除。")
    defaults = plan.get("session_defaults", {})
    bs = a.budget_sources or int(defaults.get("budget_sources", 6))
    bt = a.budget_topics or int(defaults.get("budget_topics", 3))
    target = int(defaults.get("target_cards_per_session", 8))
    man = load_src_manifest(P)
    pending = [s for s in man["sources"] if s["status"] == "pending" and s.get("extracted_path")]
    order = {"A": 0, "B": 1, "C": 2, "D": 3, "V": 4}
    # 同层级内容多的先学（薄文件多为图片课件，价值低）
    if a.prefer:
        pending.sort(key=lambda s: (0 if s["authority"] == a.prefer else 1, order.get(s["authority"], 9), -s["chars"]))
    else:
        pending.sort(key=lambda s: (order.get(s["authority"], 9), -s["chars"]))
    picked = pending[:bs]
    cards = [c for c in load_cards(P) if not c.get("_error")]
    rows = [r for r in topic_coverage(P, cards) if r["gap"]]
    rows.sort(key=lambda r: (r["priority"], -r["gap"]))
    topics = rows[:bt]
    next_kc = next_id("KC", [c.get("id") for c in cards])
    sid = "SESSION-" + _dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + hashlib.sha1(f"{time.time_ns()}{os.getpid()}".encode()).hexdigest()[:4]
    L = [f"# 学习任务单 {sid}", "", f"- 知识库：{kb} ｜ 卡现有 {len(cards)} ｜ 待学来源 {len(pending)} ｜ 本单：来源 {len(picked)} / 主题 {len(topics)} ｜ 目标产出 ≥{target} 张卡 ｜ 下一个卡 ID：**{next_kc}**", "",
         "## 一、本次要读的来源（按证据层级排序）", "", "| source_id | 层级 | 类型 | 字数 | 分块 | 文件/URL | 抽取件 |", "|---|---|---|---|---|---|---|"]
    for s in picked:
        L.append(f"| {s['source_id']} | {s['authority']} | {s.get('ext') or s.get('acquisition')} | {s['chars']:,} | {s['chunks']} | {str(s.get('title') or s.get('filename') or s['locator'])[:60]} | `{s['extracted_path']}` |")
    if not picked:
        L.append("| — | | | | | 没有待学来源：用 `ingest --path` 接本地资料或 `fetch --seeds` 拉官方页 | |")
    L += ["", "## 二、本次要补的大纲缺口（自主进化：先 fetch seed_urls，没有 seed 就用宿主 WebSearch 找官方页再 fetch）", "", "| 主题 | 优先级 | 缺口卡数 | seed_urls |", "|---|---|---|---|"]
    for r in topics:
        L.append(f"| {r['topic']} {r['name']} | P{r['priority']} | {r['gap']} | {'; '.join(r['seed_urls']) or '（无 seed，需自主检索官方页）'} |")
    L += ["", "## 三、写卡规则（摘要，全文见 Skill references/card_authoring_rules.md）", "",
          "1. 一卡一知识点，能被下游直接引用；先 `kb.py query --text` 查重，已有则**更新旧卡**（追加 source、上调 confidence、改 updated），不新建",
          "2. 必填：id / title / knowledge_type / authority / confidence(落在层级范围) / status / tags(selection_mode·entry_path·selection_phase·decision_gate·topic 至少) / sources(source_id·locator·quote≤60词·date)",
          "3. 数字一律标 `官方值` 或 `经验值` + 来源日期；不同口径**并列不裁决**，冲突写 conflicts_with + conflicts.md",
          "4. 官方事实 / 课程原文 / 项目推论 三层分清；推论卡必须写“如何验证”",
          "5. 每张卡写 `## 星空接口`：哪个 Skill 哪一步用、对应 case_default 键",
          "6. 同一场景（selection_mode × entry_path × selection_phase）攒够 5 张卡 → 用 templates/playbook.template.md 写手册", "",
          "## 四、收尾", "",
          f"`python kb.py validate --kb \"{kb}\"` PASS → `python kb.py close --kb \"{kb}\" --session {sid} --sources {','.join(s['source_id'] for s in picked) or 'SRC-xxxx'} --cards <N> --notes \"一句话\"`（close 会自动 index 与写日志）", ""]
    P["sessions"].mkdir(exist_ok=True)
    (P["sessions"] / f"{sid}.md").write_text("\n".join(L), encoding="utf-8")
    _register_session(P, sid, [s["source_id"] for s in picked])
    print("\n".join(L))
    print(f"[已写入] {P['sessions'] / (sid + '.md')}")
    return 0


def cmd_close(a) -> int:
    kb = Path(a.kb).resolve()
    P = require_kb(kb)
    man = load_src_manifest(P)
    done_ids = [x.strip() for x in (a.sources or "").split(",") if x.strip()]
    cards = load_cards(P)
    for s in man["sources"]:
        if s["source_id"] in done_ids:
            s["status"] = "ingested"
            s["ingested_at"] = now_iso()
            s["cards"] = sorted({c.get("id") for c in cards if any((x or {}).get("source_id") == s["source_id"] for x in (c.get("sources") or []) if isinstance(x, dict))})
    dump_yaml(man, P["src_manifest"])
    # 校验 + 索引
    val_rc = cmd_validate(argparse.Namespace(kb=str(kb)))
    cmd_index(argparse.Namespace(kb=str(kb)))
    with P["log"].open("a", encoding="utf-8") as f:
        f.write(f"\n## {a.session} · {today()}\n- 来源完成：{', '.join(done_ids) or '（无）'}\n- 新增/更新卡：{a.cards}\n- 校验：{'PASS' if val_rc == 0 else 'FAIL（见 validation_report.json）'}\n- 备注：{a.notes or '—'}\n")
    with P["changelog"].open("a", encoding="utf-8") as f:
        f.write(f"\n## {today()} {a.session}\n- 卡 +{a.cards}；来源 ingested {len(done_ids)}\n")
    sp = P["sessions"] / f"{a.session}.md"
    if sp.exists():
        with sp.open("a", encoding="utf-8") as f:
            f.write(f"\n---\n**收尾 {now_iso()}**：来源 {len(done_ids)} 完成 ｜ 卡 {a.cards} ｜ 校验 {'PASS' if val_rc == 0 else 'FAIL'} ｜ {a.notes or ''}\n")
    _release_session(P, a.session)
    print(jdumps({"status": "ok" if val_rc == 0 else "closed_with_validation_errors", "session": a.session, "sources_ingested": done_ids, "cards": a.cards}, ensure_ascii=False))
    return val_rc


# ============================================================ query
def cmd_query(a) -> int:
    kb = Path(a.kb).resolve()
    P = require_kb(kb)
    cards = [c for c in load_cards(P) if not c.get("_error")]
    want = defaultdict(set)
    for kv in a.tag or []:
        k, _, v = kv.partition("=")
        want[k.strip()].add(v.strip())
    text = (a.text or "").lower()
    terms = [t for t in re.split(r"[\s,，;；/]+", text) if t]
    scored = []
    for c in cards:
        if a.type and c.get("knowledge_type") != a.type:
            continue
        try:
            conf = float(c.get("confidence") or 0)
        except (TypeError, ValueError):
            conf = 0
        if conf < a.min_confidence:
            continue
        if c.get("status") in ("superseded", "retracted") and not a.include_retired:
            continue
        tags = c.get("tags") or {}
        tag_hits, tag_miss = 0, 0
        for dim, vals in want.items():
            have = set(tags.get(dim) or [])
            if have & vals or "通用" in have:
                tag_hits += 1 if have & vals else 0.5
            else:
                tag_miss += 1
        if want and tag_miss and a.strict:
            continue
        hay = " ".join([str(c.get("title") or ""), c.get("_statement") or "", c.get("_body") or ""]).lower()
        text_hits = sum(1 for t in terms if t in hay)
        if terms and text_hits == 0 and not want:
            continue
        score = tag_hits * 2 + text_hits * 1.5 + conf - tag_miss
        scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    out = []
    for score, c in scored[: a.limit]:
        out.append({"id": c.get("id"), "score": round(score, 2), "title": c.get("title"), "knowledge_type": c.get("knowledge_type"), "authority": c.get("authority"),
                    "confidence": c.get("confidence"), "status": c.get("status"), "statement": c.get("_statement"), "parameters": c.get("parameters") or {},
                    "tags": c.get("tags"), "sources": [{"source_id": s.get("source_id"), "locator": s.get("locator"), "date": s.get("date")} for s in (c.get("sources") or []) if isinstance(s, dict)],
                    "conflicts_with": c.get("conflicts_with") or [], "file": f"cards/{c['_file']}"})
    if a.format == "md":
        for o in out:
            print(f"- **{o['id']}** [{o['authority']}/{o['confidence']}] {o['title']} — {o['statement'][:120]}  ({', '.join(s['source_id'] for s in o['sources'])})")
        if not out:
            print("（无匹配卡）")
    else:
        print(jdumps({"query": {"tags": {k: sorted(v) for k, v in want.items()}, "text": a.text, "type": a.type}, "count": len(out), "cards": out}, ensure_ascii=False, indent=1))
    return 0


# ============================================================ export
def cmd_export(a) -> int:
    kb = Path(a.kb).resolve()
    P = require_kb(kb)
    cards = [c for c in load_cards(P) if not c.get("_error") and c.get("status") not in ("superseded", "retracted")]
    man = load_yaml(P["manifest"])
    scenarios = defaultdict(list)
    for c in cards:
        t = c.get("tags") or {}
        for pt in t.get("selection_mode") or ["通用"]:
            for st in t.get("entry_path") or ["通用"]:
                for ad in t.get("selection_phase") or ["通用"]:
                    scenarios[f"{pt}|{st}|{ad}"].append(c)

    def rule(c):
        return {"card_id": c.get("id"), "type": c.get("knowledge_type"), "statement": c.get("_statement"), "authority": c.get("authority"),
                "confidence": c.get("confidence"), "parameters": c.get("parameters") or {}, "decision_gate": (c.get("tags") or {}).get("decision_gate"),
                "competition_band": (c.get("tags") or {}).get("competition_band"), "sources": [f"{s.get('source_id')}@{s.get('locator')}" for s in (c.get("sources") or []) if isinstance(s, dict)],
                "hooks": c.get("starsky_hooks") or [], "conflicts_with": c.get("conflicts_with") or []}

    pack = {
        "schema_version": "1.0", "generated_at": now_iso(), "kb": {"name": man.get("name"), "root": str(kb), "cards_total": len(cards)},
        "consumer_contract": "amazon-selection-knowledge-learner/references/knowledge_consumption_contract.md",
        "how_to_use": ["按 selection_mode|entry_path|selection_phase 取 scenarios[key].rules（找不到精确键时回退到含“通用”的键）",
                       "阈值全部是候选口径：authority A 可作官方机制/政策；B/C 为经验值；D 为推论——写进选品裁定时带 evidence_refs 与 authority",
                       "用户利润账、合规黑名单、当前站点规则 优先于本包任何数字"],
        "scenarios": {k: {"rules": [rule(c) for c in sorted(v, key=lambda x: (-(float(x.get("confidence") or 0)), x.get("id")))]} for k, v in sorted(scenarios.items())},
        "diagnostics": [rule(c) for c in cards if c.get("knowledge_type") == "diagnostic"],
        "policies": [rule(c) for c in cards if c.get("knowledge_type") == "policy"],
        "formulas": [rule(c) for c in cards if c.get("knowledge_type") == "formula"],
        "thresholds": [rule(c) for c in cards if c.get("knowledge_type") == "threshold"],
        "anti_patterns": [rule(c) for c in cards if c.get("knowledge_type") == "anti_pattern"],
        "glossary": [{"card_id": c.get("id"), "term": c.get("title"), "definition": c.get("_statement")} for c in cards if c.get("knowledge_type") == "glossary"],
        "conflicts_registry": "conflicts.md",
        "playbooks": [{"id": p.get("id"), "title": p.get("title"), "scenario": p.get("scenario"), "file": f"playbooks/{p['_file']}", "cards_cited": p.get("cards_cited") or []} for p in load_playbooks(P)],
    }
    out = Path(a.out) if a.out else P["pack"]
    if not out.is_absolute():
        out = kb / out
    dump_yaml(pack, out)
    print(jdumps({"status": "ok", "out": str(out), "cards": len(cards), "scenarios": len(scenarios), "diagnostics": len(pack["diagnostics"]),
                      "thresholds": len(pack["thresholds"]), "policies": len(pack["policies"]), "playbooks": len(pack["playbooks"])}, ensure_ascii=False, indent=1))
    return 0


# ============================================================ main
def main() -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(description="亚马逊选品知识库 CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init"); p.add_argument("--kb", required=True); p.add_argument("--name"); p.set_defaults(fn=cmd_init)
    p = sub.add_parser("ingest"); p.add_argument("--kb", required=True); p.add_argument("--path", required=True)
    p.add_argument("--authority", default="B", choices=["A", "B", "C", "D", "V"]); p.add_argument("--tag", action="append"); p.add_argument("--max", type=int)
    p.add_argument("--include", help="只接入相对路径匹配此正则的文件（不区分大小写），如 '选品|蓝海|精铺|爆款'"); p.add_argument("--exclude", help="排除相对路径匹配此正则的文件")
    p.set_defaults(fn=cmd_ingest)
    p = sub.add_parser("fetch"); p.add_argument("--kb", required=True); p.add_argument("--url", action="append"); p.add_argument("--urls-file"); p.add_argument("--seeds", action="store_true")
    p.add_argument("--authority", choices=["A", "B", "C", "D", "V"]); p.add_argument("--max", type=int); p.add_argument("--delay", type=float, default=2.0); p.add_argument("--refresh", action="store_true"); p.set_defaults(fn=cmd_fetch)
    p = sub.add_parser("plan"); p.add_argument("--kb", required=True); p.add_argument("--budget-sources", type=int); p.add_argument("--budget-topics", type=int); p.add_argument("--prefer", choices=["A", "B", "C"]); p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("validate"); p.add_argument("--kb", required=True); p.set_defaults(fn=cmd_validate)
    p = sub.add_parser("index"); p.add_argument("--kb", required=True); p.set_defaults(fn=cmd_index)
    p = sub.add_parser("query"); p.add_argument("--kb", required=True); p.add_argument("--tag", action="append"); p.add_argument("--text"); p.add_argument("--type")
    p.add_argument("--min-confidence", type=float, default=0.0); p.add_argument("--limit", type=int, default=20); p.add_argument("--format", choices=["json", "md"], default="json")
    p.add_argument("--strict", action="store_true", help="标签维度全部命中才返回"); p.add_argument("--include-retired", action="store_true"); p.set_defaults(fn=cmd_query)
    p = sub.add_parser("export"); p.add_argument("--kb", required=True); p.add_argument("--out"); p.set_defaults(fn=cmd_export)
    p = sub.add_parser("close"); p.add_argument("--kb", required=True); p.add_argument("--session", required=True); p.add_argument("--sources"); p.add_argument("--cards", type=int, default=0); p.add_argument("--notes"); p.set_defaults(fn=cmd_close)

    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
