#!/usr/bin/env python3
"""星空导航悬浮窗（rc27：单窗切项目）。

只读文件，不连 Codex：轮询 <产品根>/workflow_state.yaml +
插件 templates/stage_roadmap.yaml + model_routing.yaml，随时可关，不影响 Codex。标准库 tkinter only。

- 未开始：上手页（三步：点按钮复制口令 → 粘贴到 Codex 回车 → 回来看进度）+ S1–S5 一览，一键复制。
- 已开始：五阶段进度 + 当前一句话/下一步/待你做；点左侧 S1–S5 看该阶段每步在哪（▶当前/✓完成）、下一步是什么——导航 HTML 的底层步骤直接嵌在窗内，不用开浏览器。
- 气泡：点“收成气泡”缩到屏幕边缘小条（🧭 S1 4/9），鼠标移上去或点一下弹回；离开窗口超时自动收（--idle 秒，0=不自动收；“📌 保持展开”可关掉自动收）。
- 跟着项目启动：Codex 里进入阶段时问一句要不要弹（见 starsky-navigator E）；产品根 `00_导航/打开导航悬浮窗.bat/.command` 双击即起，路径已填好。

用法（由 Codex 告诉用户，助手填好真实路径）：
    python "<home>/scripts/nav_float.py" --root "<产品根>" [--home "<home>"] [--interval 2.0]

无界面自检（测试/排错用，不开窗口）：
    python "<home>/scripts/nav_float.py" --root "<产品根>" --home "<home>" --check
    python "<home>/scripts/nav_float.py" --root "<产品根>" --home "<home>" --check --stage S2
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from model_display import ModelDisplay, navigation_label as nav_model_label
from stage_acceptance_summary import acceptance_summary, acceptance_label
from s1_roadmap import resolve_roadmap

STAGE_ORDER = ["S1", "S2", "S3", "S4", "S5"]
STAGE_LABEL = {
    "S1": "S1 · 选品板块",
    "S2": "S2 · 文案板块",
    "S3": "S3 · 视觉板块",
    "S4": "S4 · 广告板块",
    "S5": "S5 · 运营板块",
}
# 悬浮窗窄列用二字简称：S1 选品 / S2 文案 / S3 视觉 / S4 广告 / S5 运营
STAGE_SHORT = {"S1": "选品", "S2": "文案", "S3": "视觉", "S4": "广告", "S5": "运营"}
# 一键复制的启动口令：文案照抄入口 Skill《日常口令直达》，不自创。
STARTERS = [
    ("新上架快速通道", "启动星空 S1"),
    ("老品诊断修复", "老品诊断修复"),
    ("全链", "启动星空全链"),
]


_YAML_CACHE: dict = {}


def load_yaml(path: Path) -> dict:
    """Parsed once per file version: the window polls every few seconds and must not re-read an unchanged state."""
    try:
        import yaml
    except ImportError:
        return {}
    try:
        st = Path(path).stat()
        key = (st.st_mtime_ns, st.st_size)
        hit = _YAML_CACHE.get(str(path))
        if hit and hit[0] == key:
            return hit[1]
        loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
        data = yaml.load(Path(path).read_text(encoding="utf-8-sig"), Loader=loader) or {}
        _YAML_CACHE[str(path)] = (key, data)
        return data
    except (OSError, ValueError, yaml.YAMLError):
        return {}


def time_ago(mtime: float, now: float | None = None) -> str:
    now = time.time() if now is None else now
    delta = max(0, int(now - mtime))
    if delta < 60:
        return "刚刚"
    if delta < 3600:
        return f"{delta // 60} 分钟前"
    if delta < 86400:
        h, m = divmod(delta // 60, 60)
        return f"{h} 小时 {m} 分前" if m else f"{h} 小时前"
    return datetime.fromtimestamp(mtime).strftime("%m-%d %H:%M")


def _stage_spec(roadmap: dict, stage: str) -> tuple[list, int]:
    spec = (roadmap.get("stages", {}) or {}).get(stage, {}) or {}
    steps = spec.get("steps", []) or []
    try:
        total = int(spec.get("total_steps", len(steps)) or len(steps) or 1)
    except (TypeError, ValueError):
        total = len(steps) or 1
    return steps, max(total, 1)


def _step_name(step: dict, path: str) -> str:
    if path == "supply_first" and step.get("supply_first_name"):
        return str(step["supply_first_name"])
    return str(step.get("name", ""))


def _step_what(step: dict, path: str) -> str:
    if path == "supply_first" and step.get("supply_first"):
        return str(step["supply_first"])
    if step.get("condition_first") and path == "condition_first":
        return str(step["condition_first"])
    return _step_name(step, path)


def _artifacts(step: dict) -> list:
    raw = step.get("artifacts") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(a).strip() for a in raw if str(a).strip()]


def _output_name(artifact: str) -> str:
    """'S1_选品板块/x/01_市场穿透报告.md' -> '01_市场穿透报告.md'；'00_选品推荐报告.md 内候选表' 保持原样。"""
    return artifact.replace("\\", "/").split("/")[-1]


_OUTPUT_CACHE: dict = {}
MISS_RECHECK = 120.0  # 找不到的产出：案例没变时最多 2 分钟再找一次


def _case_signature(root: Path) -> tuple:
    """案例有没有变：进度文件和各阶段回执目录的修改时间。只 stat，不读内容。"""
    sig = [_state_mtime(root)]
    for folder in sorted(Path(root).glob("00_总控/*/运行数据/流程控制/S*/receipts")):
        try:
            sig.append((str(folder), folder.stat().st_mtime_ns))
        except OSError:
            continue
    return tuple(sig)


def resolve_output(root: Path, artifact: str) -> Path | None:
    """找不到的结果也记住：旧案例有几份报告只在回执里，每次重找要把几十份回执读一遍并验封，
    悬浮窗每 2 秒刷新一次就会一直卡着（3.0.23 Mac 实测单次 2 秒多）。案例变了才重找。"""
    key = (str(root), artifact)
    hit = _OUTPUT_CACHE.get(key)
    now = time.time()
    if hit and hit[1] is not None and hit[1].exists() and now - hit[0] < 20:
        return hit[1]
    sig = _case_signature(root)
    if hit and hit[1] is None and hit[2] == sig and now - hit[0] < MISS_RECHECK:
        return None
    found = _resolve_output(root, artifact)
    _OUTPUT_CACHE[key] = (now, found, sig)
    return found


def s1_scope_only(root: Path, state=None) -> bool:
    """Use the staged flow helper; source checkouts retain the same conservative fallback."""
    try:
        from s1_flow import s1_scope_only as check_scope
        return check_scope(root, state)
    except (ImportError, AttributeError):
        doc = load_yaml(root / 's1_decision_inputs.yaml')
        scope = doc.get('scope')
        if scope is None:
            state = state or load_yaml(root / 'workflow_state.yaml')
            for node in (state, state.get('entry') or {}, state.get('s1_execution') or {}):
                scope = next((node[k] for k in ('execution_scope', 'task_scope', 'scope') if node.get(k) is not None), None)
                if scope is not None:
                    break
        scope = scope.get('value') if isinstance(scope, dict) else scope
        return str(scope).strip().lower() in ('s1_only', 's1', 'only_s1', '仅s1', '仅 s1')


def _resolve_output(root: Path, artifact: str) -> Path | None:
    """找产品目录里真实存在的产出文件；同名 HTML 优先（用户更爱看网页版）。只读，找不到返回 None。"""
    if '/' in artifact.replace('\\','/'):
        direct=(root/artifact).resolve()
        return direct if direct.is_relative_to(root.resolve()) and direct.exists() else None
    from s1_report_links import resolve_project_link
    relocated, missing = resolve_project_link(root, root / 'README.md', artifact)
    if not missing and relocated and relocated != artifact:
        resolved = (root / relocated).resolve()
        if resolved.is_relative_to(root.resolve()) and resolved.is_file():
            return resolved
    name = _output_name(artifact).split(" ")[0]
    if not name or "." not in name:
        return None
    candidates = []
    rel = artifact.replace("\\", "/").split(" ")[0]
    if "/" in rel:
        candidates.append(root / rel)
    import re as _re
    pattern = _re.sub(r"\{[^}]*\}", "*", name)  # 模板名如 供应商产品报告_{商品ID}.html → 通配
    if pattern != name:
        candidates = []
    try:
        skip = {"运行数据", "snapshots", "_history", "_备份"}
        found = [p for p in root.rglob(pattern) if not skip & set(p.parts)
                 and not any(part.startswith('.s1-') for part in p.parts)]
        candidates += sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)[:3]
    except OSError:
        pass
    for c in candidates:
        if c.is_file():
            html = c.with_suffix(".html")
            return html if html.is_file() else c
    return None


def detect_done(root: Path, step: dict) -> bool:
    """某步没有回执，但它列的产物文件已经真实存在——多半是流程走完了但没经过 workflow_flow.py record。
    只读、保守：要求列出的、看起来是具体文件名（带扩展名）的产物全部能在磁盘上找到，
    只有一条模糊描述（不带扩展名，比如"01_... 成本段"）时不算数，宁可漏检也不乱报已完成。
    """
    named = [a for a in _artifacts(step) if "." in _output_name(a).split(" ")[0]]
    if not named:
        return False
    return all(resolve_output(root, a) is not None for a in named)


def detect_done_set(root: Path, steps: list) -> set:
    out = set()
    for s in steps:
        try:
            n = int(s.get("n"))
        except (TypeError, ValueError):
            continue
        if detect_done(root, s):
            out.add(n)
    return out


VIEWABLE = {".md", ".html", ".htm", ".csv", ".pdf", ".xlsx"}


def latest_records(flow: dict) -> dict:
    """控制 id → 该步最近一次回执（重跑/修订后以最新为准）。"""
    return {str(r.get("step")): r for r in (flow.get("records") or []) if isinstance(r, dict)}


def _web_first(files: list) -> list:
    """The web report is what people open; it goes first and says so. Other names stay untouched."""
    for f in files:
        if f.get("path", "").lower().endswith((".html", ".htm")) and not f["name"].endswith("（网页报告）"):
            f["name"] += "（网页报告）"
    return sorted(files, key=lambda f: not f.get("path", "").lower().endswith((".html", ".htm")))


def step_outputs(root: Path, step: dict, record: dict | None, reached: bool) -> list:
    """已执行的步骤列控制器记下的真实产物（能阅读的格式全列）；没有回执才按路线图名称去找。"""
    if record and reached:
        receipt=record.get('receipt') or {}
        from r01_case_store import step_report_catalog
        reports=step_report_catalog(root, record)
        if reports:
            files=[]
            for item in reports:
                report=(root/item['path']).resolve()
                if report.is_relative_to(root.resolve()) and report.is_file():
                    files.append({'name':item['label'],'path':str(report)})
            return _web_first(files)
        human=record.get('human_report') or (receipt.get('human_report') if isinstance(receipt,dict) else None)
        if human:
            report=(root/str(human)).resolve()
            if not report.is_relative_to(root.resolve()):return []
            return [{'name':report.name,'path':str(report) if report.is_file() else ''}]
        files, seen = [], set()
        for item in (record.get("artifacts") or {}).values():
            rel = item.get("path") if isinstance(item, dict) else item
            p = root / str(rel) if rel else None
            if p and p.is_file() and p.suffix.lower() in VIEWABLE and p not in seen:
                seen.add(p)
                files.append({"name": p.name, "path": str(p)})
        if files:
            return _web_first(files)
    outputs = [a for a in _artifacts(step) if not a.endswith((".yaml", ".yml", ".json"))] or _artifacts(step)
    # A roadmap "artifact" is sometimes a narrative pointer ('01_product_profile.yaml 成本段 / 03B 复算')
    # rather than a real filename; those never resolve to a file and would show up as a fake gap.
    # A step with nothing filename-shaped just has no separate report to chase — leave it empty.
    outputs = [a for a in outputs if "/" in a or "." in _output_name(a).split(" ")[0]]
    files = []
    for a in outputs:
        found = resolve_output(root, a) if reached else None
        files.append({"name": _output_name(a), "path": str(found) if found else ""})
    return _web_first(files)


def step_folders(root: Path, step: dict) -> list:
    """Upload packs and return folders a step works with: exists or not, and how many files are in it."""
    out = []
    for item in step.get("folders") or []:
        if not isinstance(item, dict) or not item.get("path"):
            continue
        folder = root / str(item["path"])
        if "*" in str(item["path"]):  # 带轮次号的文件夹（R06_…/05_上传包）：取最新一轮
            try:
                hits = sorted((p for p in root.glob(str(item["path"])) if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)
            except (OSError, ValueError):
                hits = []
            folder = hits[0] if hits else root / str(item["path"]).replace("*", "")
        count = 0
        if folder.is_dir():
            try:
                count = sum(1 for f in folder.rglob("*") if f.is_file() and not f.name.startswith("."))
            except OSError:
                pass
        out.append({"label": str(item.get("label") or folder.name), "path": str(folder),
                    "exists": folder.is_dir(), "count": count})
    return out


def _done_set(flow: dict, total: int, stage: str = '') -> set:
    done = set()
    for rec in flow.get("records", []) or []:
        if stage == 'S3' and flow.get('roadmap_version') == 'r03.s3' and rec.get('outcome') not in {'complete','deferred','skipped'}:
            continue
        try:
            done.add(int(rec.get("n")))
        except (TypeError, ValueError):
            continue
    if flow.get("delivery_status") == "complete" and (stage != "S2" or flow.get("roadmap_version") == "r02.s2"):
        done.add(11 if stage == 'S3' and flow.get('roadmap_version') == 'r03.s3' else total)
    return done


def _stages_overview(roadmap: dict, state: dict, current: str | None, root: Path | None = None) -> list:
    out = []
    for s in STAGE_ORDER:
        steps, total = _stage_spec(roadmap, s)
        flow = state.get(s.lower() + "_execution", {}) or {}
        try:
            cursor = int(flow.get("cursor", 0))
        except (TypeError, ValueError):
            cursor = 0
        recorded = _done_set(flow, total, s)
        # root is given: a product opened straight into a shared/older folder can have real work on
        # disk with no matching record (see detect_done) — count that in too, or the tab stays "0/9"
        # forever even though the step's own report already exists.
        detected = detect_done_set(root, steps) if root is not None and s != "S2" else set()
        done = recorded | detected
        summary=acceptance_summary(root,state,s) if root else {}
        out.append({"stage": s, "label": STAGE_LABEL.get(s, s)+(' ⚠' if summary.get('review_pending') else ''), "short": STAGE_SHORT.get(s, s)+(' ⚠' if summary.get('review_pending') else ''),
                    "cursor": cursor, "total": total,
                    "done": len(done & set(range(1, total + 1))),
                    "detected_only": len((detected - recorded) & set(range(1, total + 1))),
                    "accepted": summary.get('accepted',False) and not summary.get('review_pending'),
                    "review_pending":summary.get('review_pending',False),
                    "current": s == current})
    return out


STALE_MINUTES = 30
RESUME_CMD = "继续当前阶段：先读取已保存状态和现有报告，从停下的那一步接着做。"


def turn_state(pause: str, cursor: int, done: set, total: int, age_seconds: float | None,
               acceptance: dict | None = None, stage: str = '', blocked_reason: str = '') -> dict:
    """谁的回合：waiting（⚠️ 要你拍板且本步未完成）/ done / stale（久无更新）/ running。纯函数，可机检。"""
    text = (pause or "").strip()
    blocking = text.startswith("⚠️") or text.startswith("要你")
    clean = text.lstrip("⚠️").strip()
    if blocked_reason:
        return {'status': 'waiting', 'status_text': '等你拍板',
                'wait_text': str(blocked_reason).lstrip('⚠️').strip()}
    if cursor >= total and total and set(range(1, total + 1)) <= done:
        if not (acceptance or {}).get('accepted'):
            return {'status': 'acceptance_pending',
                    'status_text': f'{stage} {total}/{total} 已交付 · {acceptance_label(acceptance or {})}'.strip(),
                    'wait_text': '先补：' + ' · '.join((acceptance or {}).get('issues', [])[:3])
                                 if (acceptance or {}).get('issues') else '请完成验收并补齐缺项'}
        return {"status": "done", "status_text": "本阶段完成：复制交接口令，换新会话进下一阶段", "wait_text": ""}
    if blocking and cursor not in done:
        return {"status": "waiting", "status_text": "等你拍板", "wait_text": clean}
    if age_seconds is not None and age_seconds > STALE_MINUTES * 60:
        return {"status": "stale", "status_text": f"已 {int(age_seconds // 60)} 分钟没有进展", "wait_text": ""}
    return {"status": "running", "status_text": "助手在做，停下时再找你", "wait_text": ""}


OPTIONAL_VISUALS = [
    ("brand_story", "品牌故事", "S3_视觉板块/amazon-brand-story-designer/10_preview_status.json", "做品牌故事"),
    ("brand_store", "品牌旗舰店", "S3_视觉板块/amazon-brand-store-designer/11_preview_status.json", "做旗舰店装修"),
]


def optional_visuals(root: Path, state: dict) -> list:
    """S3's optional shop visuals, derived from step records plus each package's status file — never a second ledger."""
    branch_flow=state.get('s3_execution',{}) or {};modern=branch_flow.get('roadmap_version')=='r03.s3'
    records = latest_records(branch_flow)
    for identity,task in branch_flow.get('optional_tasks',{}).items():records[identity]={'outcome':task.get('status')}
    out = []
    for cid, label, status_rel, cmd in OPTIONAL_VISUALS:
        outcome = (records.get(cid) or {}).get("outcome")
        try:
            st = json.loads((root / status_rel).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            st = {}
        path = ""
        if st:
            total = int(st.get("slots") or 0)
            done = int((st.get("counts") or {}).get("returned") or 0)
            if st.get("errors"):
                status = "方案待修改"
            elif total and done >= total:
                status = "上传包已齐"
            else:
                status = f"方案已出 · 图 {done}/{total}"
            start = root / str(st.get("package") or "") / "从这里开始.html"
            path = str(start) if st.get("package") and start.is_file() else ""
        elif outcome == "skipped":
            status = "不做"
        elif outcome == "deferred":
            status = "以后再做"
        else:
            status = ('制作中' if cid in branch_flow.get('optional_tasks',{}) else '未做') if modern else '未开始'
        out.append({"id": cid, "label": label, "status": status, "cmd": cmd, "path": path,
                    "pending": status not in ("上传包已齐", "不做", "未做")})
    return out


def optional_line(items: list) -> str:
    pending = [f"{i['label']}（{i['status']}，说「{i['cmd']}」）" for i in items if i["pending"]]
    return "；".join(pending)


def s1_report_summary(root: Path,state: dict) -> dict:
    """Read the rendered report's decision; never derive a second business verdict."""
    root=Path(root).resolve()
    pointer=(state.get('s1_decision') or {}).get('compact_receipt')
    candidates=([root/pointer] if isinstance(pointer,str) else [])+[root/'S1_选品板块/_阶段总览/s1_stage_report.json']
    for path in candidates:
        if not path.resolve().is_relative_to(root) or not path.is_file():continue
        document=load_yaml(path);data=document.get('s1_stage_data') or {}
        if data.get('external_path'):
            external=(root/data['external_path']).resolve()
            if not external.is_relative_to(root) or not external.is_file():continue
            raw=external.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=data.get('sha256') or len(raw)!=data.get('size'):continue
            data=json.loads(raw)
        model=data.get('stage_model') or document.get('stage_model') or document
        summary=(model.get('report_v2') or {}).get('decision_summary') or {}
        if not summary.get('label'):continue
        valid=True
        for relative,expected in (data.get('outputs_sha256') or {}).items():
            source=(root/relative).resolve()
            if not source.is_relative_to(root) or not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest()!=expected:valid=False;break
        md=root/'S1_选品板块/_阶段总览/阶段_S1_选品可行性研究报告.md'
        page=root/'S1_选品板块/github-tabler-html-report/HTML/阶段_S1_选品可行性研究报告.html'
        if valid and md.is_file() and page.is_file():
            marker='唯一结论：'+summary['label']
            if marker in md.read_text(encoding='utf8') and marker in page.read_text(encoding='utf8'):return summary
    return {}


def snapshot(root: Path, home: Path) -> dict:
    """给 --check 和悬浮窗共用的快照，永不抛异常（缺文件就按未开始处理）。"""
    models = ModelDisplay(load_yaml(home / "templates/model_routing.yaml"))
    from s2_roadmap import project_state
    state = project_state(load_yaml(root / "workflow_state.yaml"))
    roadmap = resolve_roadmap(home,root,state)
    stage = state.get("current_stage")
    if stage not in STAGE_ORDER:
        stage = None
    state_path = root / "workflow_state.yaml"
    updated = time_ago(state_path.stat().st_mtime) if state_path.is_file() else "—"
    age = (time.time() - state_path.stat().st_mtime) if state_path.is_file() else None
    stages = _stages_overview(roadmap, state, stage, root)
    starters = [{"label": label, "cmd": cmd} for label, cmd in STARTERS]
    inferred_stage = False
    if not stage:
        # No current_stage recorded at all — a folder opened straight from a shared workspace, or a
        # run that never went through workflow_flow.py record, can still have real reports on disk
        # (see detect_done). Pick the furthest stage that shows any file-detected progress instead of
        # always saying "not started" while S1/S2 reports already sit right there in the folder.
        with_progress = [s["stage"] for s in stages if s["done"] > 0]
        if with_progress:
            stage = with_progress[-1]
            inferred_stage = True
        else:
            return {"ok": True, "started": False, "stage": None,
                    "line1": "尚未开始：按下面三步启动，约 1 分钟",
                    "howto": ["1. 点下面任一按钮复制口令", "2. 粘贴到 Codex 回车", "3. 回来这里看进度"],
                    "starters": starters, "stages": stages,
                    "updated": updated, "root": str(root)}
    steps, total = _stage_spec(roadmap, stage)
    flow = state.get(stage.lower() + "_execution", {}) or {}
    recorded_done = _done_set(flow, total, stage)
    detected_done = detect_done_set(root, steps) if stage != "S2" else set()
    done = recorded_done | detected_done
    try:
        cursor = int(flow.get("cursor", 0)) or 0
    except (TypeError, ValueError):
        cursor = 0
    if cursor <= 0:
        cursor = (max(done) + 1) if done else 1  # 没有回执可翻页时，跳到检测到的最后一步之后
    cursor = max(1, min(cursor, total))
    path = str(flow.get("path", "condition_first") or "condition_first")
    records = flow.get("records", []) or []
    last_summary = ""
    for rec in records:
        try:
            if int(rec.get("n")) <= cursor:
                last_summary = str(rec.get("summary", "") or last_summary)
        except (TypeError, ValueError):
            continue
    if not last_summary and (detected_done - recorded_done):
        n_gap = len(detected_done - recorded_done)
        last_summary = (f"从文件检测到 {n_gap} 步产物已存在但没有走 record 登记（可能是旧会话直接生成"
                        "文件、没调用 workflow_flow.py）；这里先按检测到的文件显示进度，建议让当时的会话"
                        "补一次记录。")
    by_n = {}
    for s in steps:
        try:
            by_n[int(s.get("n"))] = s
        except (TypeError, ValueError):
            continue
    cur_step = by_n.get(cursor, {})
    nxt = by_n.get(cursor + 1) if cursor < total else None
    cur_name = _step_name(cur_step, path) if cur_step else ""
    nxt_name = _step_name(nxt, path) if nxt else ("完工出口令换窗" if cursor >= total else "")
    nxt_skill = str((nxt or {}).get("skill", "")) if nxt else ""
    pause = str(((nxt or cur_step) or {}).get("pause", "") or "").strip()
    one = (last_summary or "").split("\n")[0].strip()
    if len(one) > 40:
        one = one[:40] + "…"
    bar = "■" * len(done & set(range(1, total + 1))) + "□" * (total - len(done & set(range(1, total + 1))))
    action = pause[:60] if pause else ("复制交接口令换窗" if cursor >= total else "无，我自动走；停顿再找你")
    only_s1 = stage == 'S1' and s1_scope_only(root, state)
    if only_s1 and cursor >= total:
        nxt_name = '仅 S1：查看决策报告，补证或评估拿样'
        action = '仅完成 S1 交付；采购须另行授权'
    acceptance = acceptance_summary(root, state, stage)
    turn = turn_state(str(cur_step.get("pause", "") or ""), cursor, done, total, age, acceptance, stage,
                      state.get('blocked_reason') or '')
    if turn['status'] == 'done' and (only_s1 or stage == 'S5'):
        turn['status_text'] = '本阶段完成' if only_s1 else '本轮巡检完成'
    model_hints = {'model': models.task(models.recommendation(cur_step)),
                   'next_model': models.next(models.following(roadmap, stage, cursor), within_stage=bool(nxt))}
    model_hints={key:nav_model_label(value) for key,value in model_hints.items()}
    next_text = f"第 {cursor + 1} 步 {nxt_name}".strip() if nxt else (nxt_name if only_s1 else "完工：复制交接口令换窗")
    if only_s1 and cursor >= total:
        model_hints.pop('next_model', None)
    if state.get('blocked_reason'):
        action = str(state['blocked_reason'])
    resume_cmd = RESUME_CMD
    if cursor >= total and not acceptance['accepted']:
        next_text = acceptance.get('next') or ('回到第 6 步修改文案，再执行当前合规与审阅' if stage == 'S2' else '补齐数据并重新验收')
        nxt_name = next_text
        action = acceptance.get('action') or (('按修改清单改 '+str(acceptance['count'])+' 处') if stage == 'S2' and acceptance.get('count') is not None else '补齐数据')
        model_hints.pop('next_model', None)
        resume_cmd = f'补齐 {stage} 验收数据：先读取验收日志和已有报告，补齐缺项后重新验收。'
        if stage == 'S2':
            turn.update(status=acceptance.get('status','needs_changes'),status_text=acceptance_label(acceptance),wait_text=action)
            resume_cmd=action+'：先读取当前报告与核验清单，不沿用旧通过状态。'
        if turn['status'] in {'acceptance_pending','needs_changes','review_pending','data_gap'}:
            bar = '■' * max(0, total - 1) + '⚠'
    elif cursor >= total and stage == 'S5':
        next_text, action = '本轮结束', '查看本轮报告'
        nxt_name = next_text
        model_hints.pop('next_model', None)
    if stage == 'S3' and flow.get('roadmap_version') == 'r03.s3' and flow.get('delivery_status') == 'complete':
        model_hints.pop('next_model', None)
        if acceptance['accepted']:
            next_text = nxt_name = '复制交接口令，换新会话进入 S4'
            action = '品牌故事 / 旗舰店有需要再做'
            turn.update(status='done', status_text='核心视觉交付完成', wait_text='')
        else:
            next_text = nxt_name = '补齐必需验收项后交接'
            action = '；'.join(acceptance.get('issues') or []) or '查看验收记录并补齐缺项'
            turn.update(status='acceptance_pending',status_text=acceptance_label(acceptance),wait_text=action)
    from seasonal_display import for_product, display_text
    seasonal = for_product(root,state)
    report_summary=s1_report_summary(root,state) if stage=='S1' else {}
    if report_summary:one='唯一结论：'+report_summary['label']
    from s3_navigation import summary as visual_summary
    visual = visual_summary(root,home) if stage == 'S3' else {}
    return {"ok": True, "started": True, "stage": stage,
            "stage_label": STAGE_LABEL.get(stage, stage),
            "cursor": cursor, "total": total, "path": path,
            "step": cur_name, "bar": bar,
            "one_line": one or "暂无本步一句话，先看下一步",
            "report_summary":report_summary, "seasonal":seasonal, "seasonal_text":display_text(seasonal),
            "visual_summary":visual,
            "next": next_text,
            "next_skill": nxt_skill, "action": action,
            "line1": f"上次停在 {stage} {cursor}/{total} · {updated} · 下一步：{nxt_name or '换窗'}",
            "starters": starters, "stages": stages, "resume_cmd": resume_cmd, "acceptance": acceptance,
            "stage_inferred": inferred_stage, "detected_gap": len(detected_done - recorded_done),
            "optional_line": optional_line(optional_visuals(root, state)) if _reached_s3_optional(state, stage, cursor) else "",
            "updated": updated, "root": str(root), **turn, **model_hints}


def _reached_s3_optional(state: dict, stage: str, cursor: int) -> bool:
    """Only nag once the S3 flow is at or past the optional steps; the reminder then follows the user into S4/S5."""
    flow=state.get('s3_execution',{})
    if flow.get('roadmap_version')=='r03.s3':return bool(flow.get('optional_tasks'))
    if stage in ("S4", "S5"):
        return bool(state.get("s3_execution"))
    return stage == "S3" and cursor >= 10


S5_ROUTES = {("manual", None): ("手动表格", "part1"), ("erp", "lingxing"): ("领星", "lingxing"), ("erp", "saihu"): ("赛狐", "saihu"), ("erp", "youmaiyun"): ("优麦云", "youmaiyun")}


def s5_intro(root: Path, roadmap: dict, home: Path | None = None) -> dict:
    """S5 顶部：接入指南（第一项，用户先知道有这份指导）+ 用户在进场时选的路线 + 配置是否已完成。
    只显示用户选的路线和它对应的那一节，读取店铺级接入配置。"""
    spec = ((roadmap.get("stages") or {}).get("S5") or {})
    guide = (home / spec["guide"]) if home and spec.get("guide") else None
    guide_path = str(guide) if guide and guide.is_file() else None
    route = {}
    try:
        if home:
            import importlib.util
            script=home/'skills/_s5_ops_contract/scripts/s5_connect.py'
            if not script.is_file():  # 安装目录里没有接入脚本时，用和本窗口同包的那份
                script=Path(__file__).resolve().parents[1]/'skills/_s5_ops_contract/scripts/s5_connect.py'
            spec=importlib.util.spec_from_file_location('_nav_s5_setup',script)
            connector=importlib.util.module_from_spec(spec);spec.loader.exec_module(connector)
            route=connector.read_route(root)
        else:
            route = json.loads((root / "S5_运营板块" / "s5_route.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        route = {}
    label, anchor = S5_ROUTES.get((route.get("route"), route.get("erp") or None), (None, None))
    if not label and route.get("label"):  # 别的版本档的路线：名字和指南小节直接读路线文件，窗口不用认识它
        label, anchor = str(route["label"]), str(route.get("anchor") or "")
    return {"guide_path": guide_path, "guide_link": (guide_path + "#" + anchor) if guide_path and anchor else guide_path,
            "route": label, "config_done": bool(route.get("config_done")) if label else False}


def knowledge_state(root: Path, step: dict, record: dict) -> dict:
    """Display recorded gate results only while their proof and report hashes still match."""
    if not (step.get('kb') or {}).get('calls') and not (step.get('kb') or {}).get('reuse_step'):
        return {'status': 'none', 'label': '– 本步无库', 'refs': []}
    pending = {'status': 'pending', 'label': '⚠ 本步该调用但未调用', 'refs': []}
    checked = record.get('knowledge') or {}
    if checked.get('status') != 'pass': return pending
    artifacts = record.get('artifacts') or {}
    if 'kb_evidence' not in artifacts: return pending
    try:
        for role, value in artifacts.items():
            if role != 'kb_evidence' and not role.startswith(('kb_response_','kb_source_')) and not role.endswith('_md'): continue
            if not isinstance(value, dict): raise ValueError('unhashed legacy record')
            path = (root / value['path']).resolve()
            path.relative_to(root.resolve())
            if hashlib.sha256(path.read_bytes()).hexdigest() != value.get('sha256'):
                raise ValueError('proof changed')
    except (OSError, ValueError, KeyError, TypeError):
        return {**pending, 'label': '⚠ 知识库证据失效，需重新核验'}
    if checked.get('knowledge_status') == 'used':
        return {'status': 'used', 'label': f"✓ 已引用 {checked['card_count']} 张", 'refs': checked.get('refs') or []}
    if checked.get('knowledge_status') == 'unavailable':
        return {'status': 'unavailable', 'label': '⚠ 未接通，已提示并继续', 'refs': []}
    return pending


def stage_detail(root: Path, home: Path, stage: str) -> dict:
    """某阶段的嵌窗详情：每步旗标 + 当前一句话 + 下一步 + 待你做。纯函数，可机检。"""
    models = ModelDisplay(load_yaml(home / "templates/model_routing.yaml"))
    from s2_roadmap import project_state
    state = project_state(load_yaml(root / "workflow_state.yaml"))
    roadmap = resolve_roadmap(home,root,state)
    if stage not in STAGE_ORDER:
        raise ValueError("阶段只能是 S1–S5")
    steps, total = _stage_spec(roadmap, stage)
    flow = state.get(stage.lower() + "_execution", {}) or {}
    path = str(flow.get("path", "condition_first") or "condition_first")
    recorded_done = _done_set(flow, total, stage)
    detected_done = detect_done_set(root, steps) if stage != "S2" else set()
    done = recorded_done | detected_done
    try:
        cursor = int(flow.get("cursor", 0)) or 0
    except (TypeError, ValueError):
        cursor = 0
    if cursor <= 0:
        cursor = (max(done) + 1) if done else 0  # 没回执时跳到检测到的最后一步之后，才能认出真缺口
    records = latest_records(flow)
    acceptance = acceptance_summary(root, state, stage)
    delivery_step = roadmap['stages'][stage].get('delivery_step', total)
    rows = []
    for s in steps:
        try:
            n = int(s.get("n"))
        except (TypeError, ValueError):
            continue
        if n == cursor and flow:
            flag = "current"
        elif n in recorded_done:
            flag = "done"
        elif n in detected_done:
            flag = "detected"  # 文件在，但没走 record：不是漏了，是当时那个会话没登记
        else:
            flag = "todo"
        if n == delivery_step and (n in done or stage == 'S2' and cursor == total) and not acceptance['accepted']:
            flag = 'acceptance_pending'
        pause = " ".join(str(s.get("pause", "") or "").split())
        if n == delivery_step and not acceptance['accepted']:
            pause = acceptance_label(acceptance)
        # 不管这步是不是"该轮到"了都去查一遍文件在不在，才能既认出已完成又指出真缺口，
        # 而不是把"还没走到"和"走到了但没出报告"混成一样的空白。
        record = records.get(str((s.get("control") or {}).get("id"))) or {}
        optional_choice = flow.get('optional_decisions', {}).get(s.get('control',{}).get('id'), {})
        if s.get('optional') and optional_choice.get('outcome') == 'skipped':
            flag = 'skipped'
        if flag == "done" and record.get("outcome") in ("deferred", "skipped"):
            flag = record["outcome"]
        files = step_outputs(root, s, records.get(str((s.get("control") or {}).get("id"))), True)
        if not acceptance['accepted'] or (stage == 'S1' and s1_scope_only(root, state)):
            files = [item for item in files if '_NEXT_' not in str(item.get('path') or item.get('name'))]
        missing = [o["name"] for o in files if not o["path"]]
        if s.get('optional') and (flag == 'skipped' or n > cursor):
            missing = []
        what = '交付报告并完成验收' if n == delivery_step and not acceptance['accepted'] else str(_step_what(s, path))[:80]
        rows.append({"n": n, "name": _step_name(s, path), "what": what,
                     "skill": str(s.get("skill", "")), "flag": flag,
                     "pause": pause[:160], "outputs": files, "missing": missing,
                     "knowledge": knowledge_state(root, s, record),
                     "builtin_tools": s.get('tools', []),
                     "folders": step_folders(root, s),
                     'model': nav_model_label(models.task(models.recommendation(s)))})
    # 已经翻过去的步骤（n <= cursor）却还有报告缺口：这才是真正"漏掉了"，不是还没轮到。
    gaps = [{"n": r["n"], "name": r["name"], "missing": r["missing"]}
            for r in rows if r["n"] <= cursor and r["flag"] not in ("done", "detected") and r["missing"]]
    snap = snapshot(root, home)
    if snap.get("started") and snap.get("stage") == stage:
        one, nxt, act = snap["one_line"], snap["next"], snap["action"]
    else:
        one, nxt, act = "本阶段尚未开始", "回 Codex 按该阶段入口启动", "见上手页三步"
    from seasonal_display import for_product, display_text
    seasonal_text = display_text(for_product(root,state))
    from s3_navigation import summary as visual_summary
    return {"seasonal_text": seasonal_text,"ok": True, "stage": stage, "label": STAGE_LABEL.get(stage, stage),
            "cursor": cursor, "total": total, "path": path,
            "steps": rows, "one_line": one, "next": nxt, "action": act, "gaps": gaps,
            "acceptance": acceptance,
            "report_summary":s1_report_summary(root,state) if stage=='S1' else {},
            "optional": optional_visuals(root, state) if stage == "S3" else [],
            "channels": visual_channels(home,state) if stage == 'S3' else [],
            "visual_summary":visual_summary(root,home) if stage == 'S3' else {},
            "s5": s5_intro(root, roadmap, home) if stage == "S5" else {}}


def visual_channels(home, state):
    """Only active-session tool discovery can expose the opt-in browser driver."""
    import importlib.util
    path = Path(home)/'skills/_visual_suite_contract/scripts/pack_gpt_web_feeds.py'
    if not path.is_file():return [{'id':'B','label':'网页端，同一个对话出整套'}]
    spec=importlib.util.spec_from_file_location('_nav_visual_channels',path)
    packer=importlib.util.module_from_spec(spec);spec.loader.exec_module(packer)
    runtime=state.get('mcp_runtime') or {};flow=state.get('s3_execution') or {}
    tools=runtime.get('available_tools') or [] if (runtime.get('source')=='active_tool_discovery'
        and runtime.get('session_id') and runtime.get('session_id')==flow.get('session_id')) else []
    return packer.available_channels(home=home,oracle_tools=tools)


EDGE_MARGIN = 10
EDGE_SEAM = 3  # QQ 式：藏起后只露 3 像素一条缝，悬停即滑出


def format_rows(info: dict, width_px: int = 340, measure=None) -> list:
    """The actual Tk detail rows, also available for display regression checks."""
    rows = [(f"{info['label']} · 共 {info['total']} 步", "head")]
    if info.get('report_summary'):rows.append(('唯一结论：'+info['report_summary']['label'],'what'))
    visual=info.get('visual_summary') or {}
    if visual.get('text'):
        rows.append((visual['text'],'what'))
        for item in visual['links']:
            rows.append((item['label'],'file',None,item['path']))
    gaps = info.get("gaps") or []
    if gaps:
        miss = "；".join(f"第{g['n']}步缺 " + "、".join(g["missing"][:3]) for g in gaps[:5])
        rows.append((f"⚠️ {len(gaps)} 步走过去了但报告没出全：{miss}", "warn"))
    intro = info.get("s5") or {}
    if intro.get("guide_link"):
        tip = "已选路线，点开看对应那一节" if intro.get("route") else "第一次用先点开：怎么选路线、怎么接入，卡住截图发给助手"
        rows.append((f"📘 接入指南（{tip}）", "file", None, intro["guide_link"]))
        if intro.get("route"):
            done = "✓ 已完成，想换路线回对话说「换路线」" if intro.get("config_done") else "还没完成，点上面的指南照着做"
            rows.append((f"路线：{intro['route']}　⚙ 配置：{done}", "cur" if not intro.get("config_done") else "what"))
        else:
            rows.append(("还没选路线：回对话说「手动报表」或「接领星」「接赛狐」", "curwait"))
    for item in info.get("optional") or []:
        text = f"✨ 店铺视觉可选 · {item['label']}：{item['status']}"
        if item["pending"] and not item["path"]:
            text += f"（随时说「{item['cmd']}」）"
        rows.append((text, "file" if item["path"] else "what", None, item["path"] or None))
    if measure is None:
        measure = lambda value: sum(7 if ord(c) < 128 else 13 for c in value)
    if info.get('seasonal_text'):
        rows.append(('🎄 '+info['seasonal_text'], 'what'))
    for s in info["steps"]:
        pause = (s.get("pause") or "").lstrip("⚠️").strip()
        blocking = pause.startswith("要你")
        marks = {'deferred': ('⏳', 'detected'), 'skipped': ('⏭', 'detected'),
                 'done': ('✓', 'done'), 'detected': ('✓', 'detected'),
                 'acceptance_pending': ('⚠', 'warn'), 'current': ('▶', 'curwait' if blocking else 'cur')}
        mark, tag = marks.get(s['flag'], ('○', 'todo'))
        prefix, suffix = f"{mark} {s['n']} · ", (' · ' + s['model']) if s.get('model') else ''
        name = s['name']
        text = prefix + name + suffix
        while len(name) > 1 and measure(text) > width_px:
            name = name[:-1]
            text = prefix + name + '…' + suffix
        tip = s['name'] + ('\n' + s['what'] if s.get('what') and s['what'] != s['name'] else '')
        if (s.get('knowledge') or {}).get('label'):
            tip += '\n' + s['knowledge']['label']
        if pause:
            tip += '\n' + pause
        rows.append((text, tag, tip))
        if '1688_scraper' in s.get('builtin_tools', []):
            rows.append(('🔧 本步内置工具：1688 货源辅助', 'what'))
        outs = (s.get("outputs") or [])[:8]
        made = [o for o in outs if o.get("path")]
        for o in made:
            rows.append((o["name"], "file", None, o["path"]))
        todo = [o["name"] for o in outs if not o.get("path")]
        if todo:
            rows.append(("产出：" + "、".join(todo), "out"))
        for f in s.get("folders") or []:
            if f["exists"]:
                rows.append((f"📁 {f['label']}（{f['count']} 个文件）", "file", None, f["path"]))
            else:
                rows.append((f"📁 {f['label']}：还没建，做到这一步会自动建", "out"))
    return rows


def dock_geometry(sw: int, sh: int, x: int, y: int, w: int, h: int,
                  margin: int = EDGE_MARGIN) -> tuple:
    """推到上/左/右边缘时算滑出屏幕的位置（只露 EDGE_SEAM 像素）；底部不管（任务栏）。纯函数，可机检。"""
    try:
        sw, sh, x, y, w, h = int(sw), int(sh), int(x), int(y), int(w), int(h)
    except (TypeError, ValueError):
        return None, None
    if w <= 0 or h <= 0 or sw <= 0:
        return None, None
    if x <= margin:
        return "left", f"{w}x{h}+{-w + EDGE_SEAM}+{y}"
    if x + w >= sw - margin:
        return "right", f"{w}x{h}+{sw - EDGE_SEAM}+{y}"
    if y <= margin:
        return "top", f"{w}x{h}+{x}+{-h + EDGE_SEAM}"
    return None, None


def in_hot_zone(px: int, py: int, sw: int, sh: int, side: str, span: tuple,
                thick: int = 10) -> bool:
    """鼠标是否在贴边缝的热区里（缝附近10像素 + 窗口跨度）。纯函数，可机检。"""
    try:
        x0, x1 = int(span[0]), int(span[1])
    except (TypeError, ValueError, IndexError):
        return False
    if side == "top":
        return 0 <= py <= thick and x0 <= px <= x1
    if side == "left":
        return 0 <= px <= thick and x0 <= py <= x1
    if side == "right":
        return sw - thick <= px <= sw and x0 <= py <= x1
    return False


def resolve_root(cli: str | None) -> Path:
    if cli:
        return Path(cli).expanduser().resolve()
    env = os.environ.get("STARSKY_PRODUCT_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    try:
        last = Path.home() / ".starsky-codex" / "nav_float_last.json"
        saved = json.loads(last.read_text(encoding="utf-8"))
        if saved.get("root") and Path(saved["root"]).is_dir():
            return Path(saved["root"])
    except (OSError, ValueError, KeyError):
        pass
    cand = Path.cwd().resolve()
    if (cand / "workflow_state.yaml").is_file():
        return cand
    return cand


def remember_root(root: Path, extra: dict | None = None) -> None:
    try:
        p = Path.home() / ".starsky-codex"
        p.mkdir(parents=True, exist_ok=True)
        data = {"root": str(root)}
        if extra:
            data.update(extra)
        (p / "nav_float_last.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if sys.platform == "win32":
        try:
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x100000, False, pid)
            if not h:
                return False
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        except (AttributeError, OSError, ValueError):
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def lock_path_for(root: Path) -> Path:
    import hashlib
    base = Path(os.environ.get("STARSKY_NAV_LOCKDIR", "") or __import__("tempfile").gettempdir())
    return base / ("starsky-nav-" + hashlib.sha1(str(root).encode()).hexdigest()[:12] + ".lock")


_held_locks: list = []


def summon_path_for(root: Path) -> Path:
    """再次启动时留下的"唤出"信号：已在跑的悬浮窗看到它就展开并置前。"""
    return lock_path_for(root).with_suffix(".summon")


def claim_instance(root: Path) -> bool:
    """同产品根只允许一个悬浮窗；返回 False 表示已有活的在跑。

    用 OS 级文件锁（进程死自动释放）：PID 文件会被系统重用 PID 骗过，
    导致新实例误判“已在运行”而静默退出——双击没反应的根因。"""
    try:
        p = lock_path_for(root)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
        fd = os.open(str(p), flags, 0o644)
    except OSError:
        return True  # 锁机制本身故障时不挡用户
    try:
        if sys.platform == "win32":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, IOError, ImportError):
        try:
            os.close(fd)
        except OSError:
            pass
        return False
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, json.dumps({"root": str(root), "pid": os.getpid()}).encode("utf-8"))
        os.ftruncate(fd, os.lseek(fd, 0, os.SEEK_CUR))
    except OSError:
        pass
    _held_locks.append(fd)  # 进程存续期间一直占着锁
    return True


def recall_extra() -> dict:
    try:
        return json.loads((Path.home() / ".starsky-codex" / "nav_float_last.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _history_path() -> Path:
    return Path.home() / ".starsky-codex" / "nav_float_history.json"


def load_history() -> list:
    try:
        data = json.loads(_history_path().read_text(encoding="utf-8"))
        return [str(x) for x in data if isinstance(x, str)] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save_history(root: Path, limit: int = 20) -> None:
    try:
        p = _history_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        hist = [str(root)] + [x for x in load_history() if x != str(root)]
        p.write_text(json.dumps(hist[:limit], ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _nested_containers(folder: Path) -> list:
    """S1-R9：放产品的大文件夹下面还可能再套一层（项目文件夹/product_runs/<版本文件夹>/product_runs/产品），
    多看一层：直接子文件夹里不是产品根的，它自己和它的 product_runs/ 也算放产品的地方。只 stat，不读内容。"""
    out = []
    for base in (folder, folder / "product_runs"):
        try:
            children = list(base.iterdir())
        except OSError:
            continue
        for child in children:
            try:
                if child.is_dir() and not (child / "workflow_state.yaml").is_file():
                    out += [child, child / "product_runs"]
            except OSError:
                continue
    return out


def find_projects(cur: Path) -> list:
    """单窗切换用的候选：历史（跨盘符也记得）+ 同目录兄弟 + cur 自己下面嵌套的产品（都有 workflow_state.yaml）。

    cur 常被当成"放所有产品的大文件夹"打开（例如用户把 Claude Code 指到一个总目录，
    而不是某个产品自己的文件夹）；这种时候真正的产品根是 cur 的子文件夹（或
    cur/product_runs/ 下一层），不是兄弟目录，所以两边都要扫，否则悬浮窗的项目切换
    菜单里永远看不到已经跑出来的产品。
    """
    out: list = []
    seen: set = set()

    def _add(p) -> None:
        try:
            rp = Path(p).expanduser().resolve()
        except (OSError, ValueError):
            return
        if str(rp) in seen or not rp.is_dir():
            return
        seen.add(str(rp))
        out.append(rp)

    def _scan(folder: Path) -> None:
        try:
            children = sorted(folder.iterdir())
        except OSError:
            return
        for child in children:
            try:
                if (child / "workflow_state.yaml").is_file():
                    _add(child)
            except OSError:
                continue
    for h in load_history():
        _add(h)
        if not (Path(h) / "workflow_state.yaml").is_file():  # 历史里记的是放产品的大文件夹：新案例在它下面
            _scan(Path(h))
            _scan(Path(h) / "product_runs")
            for inner in _nested_containers(Path(h)):
                _scan(inner)
    _add(cur)
    _scan(cur.parent)  # 兄弟目录：cur 本身就是某个产品根时，同级还有别的产品
    if (cur / "workflow_state.yaml").is_file():
        return out  # cur 已经是产品根，没有更下一层的产品要找
    _scan(cur)  # cur 是容器目录：产品直接是它的子文件夹
    if (cur / "product_runs").is_dir():
        _scan(cur / "product_runs")  # cur 是容器目录，产品按约定收在 product_runs/ 下一层
    for inner in _nested_containers(cur):
        _scan(inner)
    return out


def _state_mtime(root: Path) -> float:
    try:
        return (Path(root) / "workflow_state.yaml").stat().st_mtime
    except OSError:
        return 0.0


def active_case(cur: Path, history: list, since: float = 0.0) -> Path | None:
    """自动跟随：已知位置里 workflow_state.yaml 最近被写过的案例，比当前案例和用户上次手动选的时间都新才返回。

    已知位置 = 历史里的产品根及其同级 + 历史里的大文件夹（及其 product_runs/）+ 当前案例同级。
    只看文件修改时间，不读内容；一个也不比当前新就返回 None，悬浮窗保持不动。"""
    seen, found = set(), []

    def _take(folder: Path) -> None:
        try:
            children = list(folder.iterdir())
        except OSError:
            return
        for child in children:
            if str(child) not in seen and _state_mtime(child):
                seen.add(str(child))
                found.append(child)

    for h in [str(cur)] + list(history):
        h = Path(h)
        if (h / "workflow_state.yaml").is_file():
            _take(h.parent)
        else:
            _take(h)
            _take(h / "product_runs")
            for inner in _nested_containers(h):
                _take(inner)
    floor = max(_state_mtime(cur), since)
    best = max(found, key=_state_mtime, default=None)
    if best is None or _state_mtime(best) <= floor:
        return None
    try:
        if best.resolve() == Path(cur).resolve():
            return None
    except OSError:
        return None
    return best


FOLLOW_EVERY, MANUAL_HOLD = 15.0, 1800.0  # 每 15 秒看一次；手动选过项目后 30 分钟内不自动切


def mac_float_everywhere(level: int = 25) -> bool:
    """macOS：让悬浮窗出现在所有桌面空间、并能浮在全屏应用之上（像 QQ 悬浮窗）。
    全屏应用独占一个 Space，普通 -topmost 窗口进不去。失败只返回 False，不影响其他平台。"""
    if sys.platform != "darwin":
        return False
    try:
        import ctypes
        import ctypes.util
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        addr = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
        send = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(addr)
        send_l = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long)(addr)
        send_ul = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)(addr)
        count = ctypes.CFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p)(addr)
        sel = lambda name: objc.sel_registerName(name.encode())
        nsapp = send(objc.objc_getClass(b"NSApplication"), sel("sharedApplication"))
        send_l(nsapp, sel("setActivationPolicy:"), 1)  # 辅助应用：不占 Dock，可浮在全屏应用上
        windows = send(nsapp, sel("windows"))
        # CanJoinAllSpaces | Stationary | IgnoresCycle | FullScreenAuxiliary
        behavior = (1 << 0) | (1 << 4) | (1 << 6) | (1 << 8)
        for i in range(count(windows, sel("count"))):
            w = send_ul(windows, sel("objectAtIndex:"), i)
            send_ul(w, sel("setCollectionBehavior:"), behavior)
            send_l(w, sel("setLevel:"), level)
            send_l(w, sel("setHidesOnDeactivate:"), 0)
        return True
    except (OSError, AttributeError, TypeError, ValueError):
        return False


def run_gui(root: Path, home: Path, interval: float, idle: float, start_min: bool) -> int:
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        print("本机 Python 缺 tkinter，悬浮窗开不起来；改用 00_导航/导航.html 挂着看。", file=sys.stderr)
        return 2
    if tk.TkVersion < 8.6:
        print("本机界面库版本过旧（Tk " + str(tk.TkVersion) + "），悬浮窗会黑屏；改用 00_导航/导航.html 挂着看。"
              "Mac 可安装与专用 Python 同版本的 python-tk（Homebrew）或 python.org 版 Python 后再开。", file=sys.stderr)
        return 2
    try:
        app = tk.Tk()
    except tk.TclError as exc:
        print(f"无图形界面，悬浮窗开不起来（{exc}）；改用 00_导航/导航.html 挂着看。", file=sys.stderr)
        return 2
    app.title("星空导航悬浮窗")
    # Keep the desktop shortcut and navigation window under the same identity.
    icon_dir = home / "assets"
    try:
        if sys.platform == "win32" and (icon_dir / "starsky-nav-v1.ico").is_file():
            app.iconbitmap(default=str(icon_dir / "starsky-nav-v1.ico"))
        elif (icon_dir / "starsky-nav-v1.png").is_file():
            app._starsky_icon = tk.PhotoImage(file=str(icon_dir / "starsky-nav-v1.png"))
            app.iconphoto(True, app._starsky_icon)
    except tk.TclError:
        pass  # An icon must never block navigation.
    app.attributes("-topmost", True)
    # rc22：保留无边框（贴边藏要真滑出屏幕），但补上手动缩放：
    # 右下角 ◢ 拖动改大小 + ＋/－ 按钮步进 + 记住上次大小。
    # 不能再出现“不能拉大、不能缩小”。
    try:
        # macOS 只认窗口建好之后再设的无边框：先设会留下原生标题栏，且不能移出屏幕顶部。
        if sys.platform == "darwin":
            app.update_idletasks()
        app.overrideredirect(True)
    except tk.TclError:
        pass
    try:
        app.resizable(True, True)
        app.minsize(260, 220)
    except tk.TclError:
        pass
    if sys.platform == "win32":
        try:
            app.attributes("-toolwindow", True)
        except tk.TclError:
            pass
    # 青绿主题：跟 00_导航/导航.html 同一套色，告别原生灰。
    if sys.platform == "win32":
        FONT, SMALL, HEAD_FONT = ("Microsoft YaHei", 9), ("Microsoft YaHei", 8), ("Microsoft YaHei", 10, "bold")
    elif sys.platform == "darwin":
        FONT, SMALL, HEAD_FONT = ("PingFang SC", 12), ("PingFang SC", 11), ("PingFang SC", 13, "bold")
    else:
        FONT, SMALL, HEAD_FONT = ("", 10), ("", 9), ("", 12, "bold")
    try:
        _style = ttk.Style(app)
        _style.theme_use("clam")
    except tk.TclError:
        _style = ttk.Style(app)
    _style.configure(".", background="#f5f7f6", foreground="#25332e", font=FONT)
    _style.configure("TFrame", background="#f5f7f6")
    _style.configure("TLabel", background="#f5f7f6", foreground="#25332e", font=FONT)
    _style.configure("Head.TFrame", background="#3f7f6e")
    _style.configure("Head.TLabel", background="#3f7f6e", foreground="#ffffff", font=HEAD_FONT)
    _style.configure("Sub.TLabel", foreground="#6b7a74")
    _style.configure("TButton", padding=(8, 5), font=FONT)
    _style.configure("Primary.TButton", background="#3f7f6e", foreground="#ffffff")
    try:
        _style.map("Primary.TButton", background=[("active", "#2f6558")],
                   foreground=[("active", "#ffffff")])
    except tk.TclError:
        pass
    _style.configure("Bubble.TFrame", background="#3f7f6e")
    _style.configure("Bubble.TLabel", background="#3f7f6e", foreground="#ffffff",
                       font=(SMALL[0], SMALL[1], "bold"))
    _style.configure("green.Horizontal.TProgressbar", background="#3f7f6e",
                       troughcolor="#e1e8e5", borderwidth=0, thickness=8)
    # rc28：项目下拉平时一体显示、不显框，点到才出现输入态。
    try:
        _style.configure("Flat.TCombobox", relief="flat", borderwidth=0,
                           fieldbackground="#f5f7f6", background="#f5f7f6",
                           foreground="#6b7a74", arrowsize=14, padding=(6, 3))
        _style.map("Flat.TCombobox", fieldbackground=[("focus", "#ffffff")],
                     background=[("active", "#e7f0ed")])
    except tk.TclError:
        pass
    try:
        app.configure(background="#f5f7f6")
    except tk.TclError:
        pass
    pin_var = tk.BooleanVar(value=False)
    hide_after_id: list = [None]

    # 气泡是圆角胶囊：窗口底色透明，只画胶囊本身。
    CLEAR = "systemTransparent" if sys.platform == "darwin" else "#010203"
    try:
        if sys.platform == "darwin":
            app.attributes("-transparent", True)
        elif sys.platform == "win32":
            app.attributes("-transparentcolor", CLEAR)
        else:
            CLEAR = "#f5f7f6"
        app.configure(background=CLEAR)
    except tk.TclError:
        CLEAR = "#f5f7f6"
    bubble = tk.Frame(app, bg=CLEAR, highlightthickness=0, bd=0)
    main = ttk.Frame(app)
    bubble_text = tk.StringVar(value="🧭 星空导航")
    mode: list = ["main"]
    restore_geo: list = [None]
    main_geo: list = [None]  # rc22：记住主窗大小，下次展开/轮询不强制缩回 430x620
    edge_cooldown_until: list = [time.time() + 8.0]  # rc22：8秒（原来60秒太长，用户以为坏了）
    dock_info: list = [None]  # {"side":..., "span":(a,b), "sw":..., "sh":...}
    drag_xy: list = [0, 0]
    user_moved: list = [False]  # 只有用户亲手拖过的窗口才允许自动贴边藏；手动点按钮不受限
    resize_xy: list = [0, 0, 0, 0]  # rc22 缩放手柄用：start_x, start_y, start_w, start_h

    def place_bubble() -> None:
        # rc25：气泡要小。之前主窗 minsize(360,520) 没松开，气泡 130x72 缩不下去，
        # 剩个大绿块（就是你截的那种）。先把下限放到 90x40 再定尺寸。
        try:
            app.minsize(90, 40)
        except tk.TclError:
            pass
        try:
            x = app.winfo_screenwidth() - 150
            y = app.winfo_screenheight() // 2
            extra = recall_extra()
            bx, by = extra.get("bubble_x", x), extra.get("bubble_y", y)
            bw, bh = draw_bubble()
            app.geometry(f"{bw}x{bh}+{int(bx)}+{int(by)}")
        except (tk.TclError, TypeError, ValueError):
            pass

    def hide_all() -> None:
        for f in (bubble, main):
            f.pack_forget()

    def start_drag(event) -> None:
        drag_xy[0], drag_xy[1] = event.x_root - app.winfo_x(), event.y_root - app.winfo_y()

    def on_drag(event) -> None:
        user_moved[0] = True
        try:
            app.geometry(f"+{event.x_root - drag_xy[0]}+{event.y_root - drag_xy[1]}")
        except tk.TclError:
            pass

    panel: dict = {"rect": None, "bubble_at": None}

    def show_bubble() -> None:
        cancel_hide()
        try:
            edge["side"] = None
        except NameError:
            pass
        if mode[0] == "main":
            try:
                panel["rect"] = (app.winfo_x(), app.winfo_y(), app.winfo_width(), app.winfo_height())
                _remember_main_size()
            except tk.TclError:
                pass
        mode[0] = "bubble"
        hide_all()
        bubble.pack(fill="both", expand=True)
        try:
            remember_root(root, {"bubble_x": app.winfo_x(), "bubble_y": app.winfo_y()})
        except tk.TclError:
            pass
        place_bubble()
        try:
            app.update_idletasks()
            panel["bubble_at"] = (app.winfo_x(), app.winfo_y())
        except tk.TclError:
            panel["bubble_at"] = None

    def restore_panel_geometry() -> str | None:
        """从气泡展开：没拖过气泡就回到原位原大小；拖过就在气泡旁边展开，且不出屏幕。"""
        if not panel["rect"]:
            return None
        x, y, w, h = panel["rect"]
        try:
            bx, by = app.winfo_x(), app.winfo_y()
            sw, sh = app.winfo_screenwidth(), app.winfo_screenheight()
        except tk.TclError:
            return f"{w}x{h}+{x}+{y}"
        if panel["bubble_at"] and (bx, by) != panel["bubble_at"]:
            if not (0 <= bx < sw and 0 <= by < sh):
                return f"{w}x{h}+{bx}+{by}"  # bubble sits on another display: open the panel there
            x = bx if bx + w <= sw else max(0, bx - w + 100)
            y = max(0, min(by, sh - h - 40))
        return f"{w}x{h}+{x}+{y}"

    def show_main(geo: str | None = None) -> None:
        cancel_hide()
        mode[0] = "main"
        hide_all()
        try:
            app.minsize(260, 220)
        except tk.TclError:
            pass
        main.pack(fill="both", expand=True)
        # rc22：优先用记忆的大小，不强制缩回固定值；默认加高到 700，保证底部按钮不被挤出。
        want = geo or main_geo[0] or restore_geo[0] or "460x700"
        try:
            if geo is None and main_geo[0] is None and restore_geo[0] is None:
                try:
                    extra = recall_extra()
                    mw, mh = int(extra.get("main_w", 460)), int(extra.get("main_h", 700))
                    want = f"{max(260, min(mw, 1200))}x{max(220, min(mh, 1100))}"
                except (TypeError, ValueError):
                    pass
            app.geometry(want)
        except tk.TclError:
            pass

    def pointer_outside() -> bool:
        try:
            x, y = app.winfo_pointerx(), app.winfo_pointery()
            wx, wy = app.winfo_x(), app.winfo_y()
            ww, wh = app.winfo_width(), app.winfo_height()
            return not (wx <= x <= wx + ww and wy <= y <= wy + wh)
        except tk.TclError:
            return False

    def _remember_main_size() -> None:
        # rc22：每次主窗大小变化就记住，下次展开不丢。
        try:
            w, h = app.winfo_width(), app.winfo_height()
            if w >= 200 and h >= 200:
                main_geo[0] = f"{w}x{h}"
                remember_root(root, {**recall_extra(), "main_w": w, "main_h": h,
                                      "bubble_x": app.winfo_x(), "bubble_y": app.winfo_y()})
        except tk.TclError:
            pass

    # ---- QQ 式贴边：拖到左/右/上边缘松手即吸附；鼠标离开 0.5 秒滑出屏幕只留一条缝；碰到缝滑回。----
    SNAP = 28
    edge: dict = {"side": None, "flush": None, "out_since": None, "anim": None}

    def screen_size() -> tuple:
        return app.winfo_screenwidth(), app.winfo_screenheight()

    def on_main_screen() -> bool:
        """Tk only reports the main screen's size. A window dragged onto an extended display lies outside it;
        snapping it to the main screen's edges threw it back there (live demo on two screens, 3.0.23)."""
        try:
            sw, sh = screen_size()
            cx = app.winfo_x() + app.winfo_width() // 2
            cy = app.winfo_y() + app.winfo_height() // 2
        except tk.TclError:
            return True
        return 0 <= cx < sw and 0 <= cy < sh

    def near_edge() -> str | None:
        if not on_main_screen():
            return None  # on another display: no edge snapping, the window stays where it was dropped
        try:
            sw, _sh = screen_size()
            x, y, w = app.winfo_x(), app.winfo_y(), app.winfo_width()
        except tk.TclError:
            return None
        top_snap = SNAP + (32 if sys.platform == "darwin" else 0)  # Mac 顶部有菜单栏
        dist = {"left": x, "right": sw - (x + w), "top": y}
        side = min(dist, key=lambda k: dist[k])
        limit = top_snap if side == "top" else SNAP
        return side if dist[side] <= limit else None

    def flush_pos(side: str) -> tuple:
        sw, _sh = screen_size()
        x, y, w = app.winfo_x(), app.winfo_y(), app.winfo_width()
        return {"left": (0, y), "right": (sw - w, y), "top": (x, 0)}[side]

    def hidden_pos(side: str) -> tuple:
        sw, _sh = screen_size()
        fx, fy = edge["flush"]
        w, h = app.winfo_width(), app.winfo_height()
        return {"left": (-w + EDGE_SEAM, fy), "right": (sw - EDGE_SEAM, fy), "top": (fx, -h + EDGE_SEAM)}[side]

    def slide(tx: int, ty: int, steps: int = 8) -> None:
        if edge["anim"]:
            try:
                app.after_cancel(edge["anim"])
            except tk.TclError:
                pass
        try:
            x0, y0 = app.winfo_x(), app.winfo_y()
        except tk.TclError:
            return
        def frame(i: int) -> None:
            k = i / steps
            k = 1 - (1 - k) ** 3  # ease-out
            try:
                app.geometry(f"+{int(x0 + (tx - x0) * k)}+{int(y0 + (ty - y0) * k)}")
            except tk.TclError:
                return
            edge["anim"] = app.after(14, frame, i + 1) if i < steps else None
        frame(1)

    def snap_after_drag(_e=None) -> None:
        """松手：靠近边缘就吸附对齐，否则取消贴边。"""
        if mode[0] != "main":
            return
        try:
            app.update_idletasks()  # 拿到松手那一刻的真实位置
        except tk.TclError:
            return
        side = near_edge()
        edge["side"], edge["out_since"] = side, None
        if side:
            edge["flush"] = flush_pos(side)
            slide(*edge["flush"], steps=4)
        _remember_main_size()

    def hide_to_edge() -> None:
        if not edge["side"] or pin_var.get() or mode[0] != "main":
            return
        _remember_main_size()
        cancel_hide()
        mode[0] = "edge"
        slide(*hidden_pos(edge["side"]))

    def expand_from_edge() -> None:
        if mode[0] != "edge":
            return
        mode[0] = "main"
        edge["out_since"] = None
        slide(*edge["flush"])

    def dock_to_nearest_edge() -> None:
        """【藏到边上】按钮：贴到最近的边并立即藏起。"""
        if mode[0] != "main":
            return
        try:
            sw, _sh = screen_size()
            x, y, w = app.winfo_x(), app.winfo_y(), app.winfo_width()
        except tk.TclError:
            return
        dist = {"left": x, "right": sw - (x + w), "top": y}
        edge["side"] = min(dist, key=lambda k: dist[k])
        edge["flush"] = flush_pos(edge["side"])
        if pin_var.get():
            slide(*edge["flush"])
            return
        app.geometry(f"+{edge['flush'][0]}+{edge['flush'][1]}")
        hide_to_edge()

    def summon_check() -> None:
        flag = summon_path_for(root)
        if not flag.exists():
            return
        try:
            flag.unlink()
        except OSError:
            pass
        if mode[0] == "bubble":
            show_main(restore_panel_geometry())
        elif mode[0] == "edge":
            expand_from_edge()
        try:
            app.deiconify()
            app.lift()
            app.focus_force()
        except tk.TclError:
            pass
        toast("已唤出")

    def edge_tick() -> None:
        """150ms 一拍：展开且贴着边时，鼠标离开 0.5 秒就藏；藏着时鼠标碰到缝就出来。"""
        summon_check()
        try:
            px, py = app.winfo_pointerx(), app.winfo_pointery()
            if mode[0] == "main" and edge["side"] and not pin_var.get():
                if pointer_outside():
                    edge["out_since"] = edge["out_since"] or time.time()
                    if time.time() - edge["out_since"] > 0.5:
                        hide_to_edge()
                else:
                    edge["out_since"] = None
            elif mode[0] == "edge" and edge["side"]:
                sw, sh = screen_size()
                fx, fy = edge["flush"]
                w, h = app.winfo_width(), app.winfo_height()
                span = (fx, fx + w) if edge["side"] == "top" else (fy, fy + h)
                if in_hot_zone(px, py, sw, sh, edge["side"], span):
                    expand_from_edge()
        except tk.TclError:
            pass
        app.after(150, edge_tick)

    def dock_edge(manual: bool = False) -> bool:
        """兼容旧调用：手动=立即藏到最近边。自动贴边改由 edge_tick 负责。"""
        if manual:
            dock_to_nearest_edge()
            return mode[0] == "edge"
        return False

    def step_size(dw: int, dh: int) -> None:
        """rc22：＋/－ 按钮步进缩放。"""
        try:
            w, h = app.winfo_width(), app.winfo_height()
            w, h = max(260, w + dw), max(220, h + dh)
            app.geometry(f"{w}x{h}")
            _remember_main_size()
        except tk.TclError:
            pass

    def grip_press(event) -> None:
        try:
            resize_xy[0], resize_xy[1] = event.x_root, event.y_root
            resize_xy[2], resize_xy[3] = app.winfo_width(), app.winfo_height()
        except tk.TclError:
            pass

    def grip_drag(event) -> None:
        try:
            dw, dh = event.x_root - resize_xy[0], event.y_root - resize_xy[1]
            w, h = max(260, resize_xy[2] + dw), max(220, resize_xy[3] + dh)
            app.geometry(f"{w}x{h}")
        except tk.TclError:
            pass

    def grip_release(_e=None) -> None:
        _remember_main_size()

    def cancel_hide() -> None:
        if hide_after_id[0] is not None:
            try:
                app.after_cancel(hide_after_id[0])
            except tk.TclError:
                pass
            hide_after_id[0] = None

    def arm_hide() -> None:
        cancel_hide()
        if idle <= 0 or pin_var.get():
            return
        def _fire() -> None:
            hide_after_id[0] = None
            try:
                x, y = app.winfo_pointerx(), app.winfo_pointery()
                wx, wy, ww, wh = app.winfo_x(), app.winfo_y(), app.winfo_width(), app.winfo_height()
                inside = wx <= x <= wx + ww and wy <= y <= wy + wh
            except tk.TclError:
                inside = False
            if not inside:
                show_bubble()
            else:
                arm_hide()
        hide_after_id[0] = app.after(max(1000, int(idle * 1000)), _fire)

    # 贴边后整窗只剩 3 像素缝：鼠标碰到缝（Enter）或点一下就滑回来。
    # <Enter> 绑在顶层：缝太细，子控件事件不可靠。
    app.bind("<Enter>", lambda _e: expand_from_edge() if mode[0] == "edge" else None)

    # ---- 气泡页 ----
    import tkinter.font as tkfont
    bubble_canvas = tk.Canvas(bubble, bg=CLEAR, highlightthickness=0, bd=0, cursor="hand2")
    bubble_canvas.pack(fill="both", expand=True)
    BUBBLE_BOLD = tkfont.Font(family=SMALL[0] or None, size=SMALL[1], weight="bold")
    BUBBLE_SUB = tkfont.Font(family=SMALL[0] or None, size=max(8, SMALL[1] - 1))
    bubble_hover: list = [False]

    def rounded(c, x1, y1, x2, y2, r, **kw):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
               x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return c.create_polygon(pts, smooth=True, splinesteps=24, **kw)

    def draw_bubble() -> tuple:
        lines = (bubble_text.get() or "🧭 星空导航").split("\n")
        top, sub = lines[0], (lines[1] if len(lines) > 1 else "")
        w = max(BUBBLE_BOLD.measure(top), BUBBLE_SUB.measure(sub)) + 30
        h = BUBBLE_BOLD.metrics("linespace") + (BUBBLE_SUB.metrics("linespace") if sub else 0) + 16
        c = bubble_canvas
        c.delete("all")
        c.configure(width=w, height=h)
        fill = "#356e60" if bubble_hover[0] else "#3f7f6e"
        rounded(c, 1, 1, w - 1, h - 1, min(16, h // 2), fill=fill, outline="#2f6558")
        if sub:
            c.create_text(w // 2, 8, text=top, font=BUBBLE_BOLD, fill="#ffffff", anchor="n")
            c.create_text(w // 2, h - 8, text=sub, font=BUBBLE_SUB, fill="#dcebe6", anchor="s")
        else:
            c.create_text(w // 2, h // 2, text=top, font=BUBBLE_BOLD, fill="#ffffff")
        return w, h

    def bubble_refresh(*_a) -> None:
        if mode[0] == "bubble":
            try:
                w, h = draw_bubble()
                app.geometry(f"{w}x{h}")
            except tk.TclError:
                pass

    bubble_text.trace_add("write", bubble_refresh)
    bubble_canvas.bind("<Enter>", lambda _e: (bubble_hover.__setitem__(0, True), bubble_refresh()))
    bubble_canvas.bind("<Leave>", lambda _e: (bubble_hover.__setitem__(0, False), bubble_refresh()))
    # 气泡可拖走：点一下展开，拖动则移动（松手不展开）。
    bubble_moved: list = [False]

    def bubble_press(event) -> None:
        start_drag(event)
        bubble_moved[0] = False

    def bubble_motion(event) -> None:
        bubble_moved[0] = True
        on_drag(event)

    def bubble_click(_e=None) -> None:
        if bubble_moved[0]:
            bubble_moved[0] = False
            return
        show_main(restore_panel_geometry())

    # rc23：点=按下去+松开都没动才算点，拖=动过就不展开。
    # 之前 <Button-1> 和 <ButtonPress-1> 是同一个事件，后绑的把先绑的覆盖掉，
    # 导致按下去的拖动准备动作丢失。改成按/拖/松三个不同事件就不再互盖。
    for child in [bubble_canvas]:
        child.bind("<ButtonPress-1>", bubble_press)
        child.bind("<B1-Motion>", bubble_motion)
        child.bind("<ButtonRelease-1>", bubble_click)

    # ---- 主页（rc.3 重排：状态优先、尺寸自适应、不重复信息）----
    # 状态只用低饱和色点缀文字；标题栏始终品牌绿，不整块变色（用户反馈：橙红太扎眼）。
    BRAND = "#3f7f6e"
    STATUS_COLORS = {"needs_changes": ("#a67c34", "⚠️"), "acceptance_pending": ("#a67c34", "⚠️"),
                     "waiting": ("#a67c34", "🟡"), "running": ("#3f7f6e", "🟢"),
                     "stale": ("#8d9893", "⚪"), "done": ("#2f6558", "✅"), "idle": ("#3f7f6e", "🧭")}

    def status_color(status: str) -> str:
        return STATUS_COLORS.get(status, STATUS_COLORS["idle"])[0]

    class Tip:
        """鼠标悬停显示全文（路径、被截断的停顿说明）。"""
        def __init__(self, widget, text_fn) -> None:
            self.widget, self.text_fn, self.win = widget, text_fn, None
            widget.bind("<Enter>", self.show, add="+")
            widget.bind("<Leave>", self.hide, add="+")

        def show(self, _e=None) -> None:
            text = self.text_fn()
            if not text or self.win:
                return
            try:
                self.win = tk.Toplevel(app)
                self.win.overrideredirect(True)
                self.win.attributes("-topmost", True)
                tk.Label(self.win, text=text, bg="#25332e", fg="#ffffff", font=SMALL, justify="left",
                         wraplength=360, padx=8, pady=6).pack()
                x = self.widget.winfo_rootx()
                y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
                self.win.geometry(f"+{x}+{y}")
                mac_float_everywhere()
            except tk.TclError:
                self.win = None

        def hide(self, _e=None) -> None:
            if self.win:
                try:
                    self.win.destroy()
                except tk.TclError:
                    pass
                self.win = None

    head = tk.Frame(main, bg=status_color("idle"))
    head.pack(fill="x")

    def do_close(_e=None) -> None:
        try:
            cancel_hide()
        except (NameError, tk.TclError):
            pass
        try:
            app.destroy()
        except tk.TclError:
            pass
    closer = tk.Label(head, text="✕", bg=status_color("idle"), fg="#ffffff", font=HEAD_FONT,
                      cursor="hand2", padx=10, pady=2)
    closer.pack(side="right", anchor="n", padx=2, pady=4)
    closer.bind("<ButtonRelease-1>", do_close)
    title = tk.Label(head, text="…", bg=status_color("idle"), fg="#ffffff", font=HEAD_FONT,
                     anchor="w", justify="left")
    title.pack(padx=12, pady=(7, 0), fill="x")
    subtitle = tk.Label(head, text="", bg=status_color("idle"), fg="#dcebe6", font=SMALL,
                        anchor="w", justify="left")
    subtitle.pack(padx=12, pady=(0, 7), fill="x")
    for w in (head, title, subtitle):
        w.bind("<ButtonPress-1>", start_drag)
        w.bind("<B1-Motion>", on_drag)
        w.bind("<ButtonRelease-1>", snap_after_drag)

    # 阶段标签：按宽度自动换行（窄窗只显示 S1…S5 + 进度点），五个永远看得到。
    tabs = tk.Frame(main, bg="#f5f7f6")
    tabs.pack(fill="x", padx=12, pady=(10, 0))
    tab_btns: list = []
    selected_idx: list = [0]
    for _i, _s in enumerate(STAGE_ORDER):
        _b = tk.Label(tabs, text=_s, font=SMALL, cursor="hand2", bg="#ffffff", fg="#25332e",
                      padx=4, pady=4, highlightthickness=1, highlightbackground="#e1e8e5")
        _b.bind("<Button-1>", lambda _e, i=_i: select_stage(i))
        tab_btns.append(_b)

    # 状态卡：谁的回合 + 要做什么 + 下一步，一处说清。
    card = tk.Frame(main, bg="#ffffff", highlightthickness=1, highlightbackground="#e1e8e5")
    card.pack(fill="x", padx=12, pady=(4, 0))
    badge = tk.Label(card, text="", bg="#ffffff", fg=status_color("idle"), font=HEAD_FONT, anchor="w")
    badge.pack(fill="x", padx=12, pady=(8, 2))
    wait = tk.Label(card, text="", bg="#ffffff", fg="#25332e", font=FONT, anchor="w", justify="left")
    wait.pack(fill="x", padx=12)
    dots = tk.Label(card, text="", bg="#ffffff", fg="#3f7f6e", font=SMALL, anchor="w")
    dots.pack(fill="x", padx=12, pady=(6, 0))
    nxt = tk.Label(card, text="", bg="#ffffff", fg="#6b7a74", font=SMALL, anchor="w", justify="left")
    nxt.pack(fill="x", padx=12, pady=(2, 10))
    one = tk.Label(card, text="", bg="#ffffff", fg="#9ba8a3", font=SMALL, anchor="w", justify="left")
    card_state: dict = {"wait_full": ""}
    Tip(wait, lambda: card_state["wait_full"] if len(card_state["wait_full"]) > 60 else "")

    # 状态卡与步骤列表之间的拖动条：往上拉状态卡变矮、步骤区变长；双击收起/展开；高度会记住。
    splitter = tk.Frame(main, bg="#f5f7f6", height=12, cursor="sb_v_double_arrow")
    splitter.pack(fill="x", padx=12)
    grip_line = tk.Frame(splitter, bg="#b9c7c2", height=3, width=44, cursor="sb_v_double_arrow")
    grip_line.place(relx=0.5, rely=0.5, anchor="center")
    split: dict = {"y0": 0, "h0": 0, "height": None}

    def card_natural() -> int:
        kids = [c for c in card.winfo_children() if c.winfo_ismapped()]
        return max((c.winfo_y() + c.winfo_reqheight() for c in kids), default=40) + 10

    def card_min() -> int:
        return badge.winfo_reqheight() + 14

    def set_card_height(h) -> None:
        if h is None or h >= card_natural() - 2:
            card.pack_propagate(True)
            card.configure(height=0)
            split["height"] = None
        else:
            card.pack_propagate(False)
            card.configure(height=max(card_min(), int(h)))
            split["height"] = max(card_min(), int(h))

    def save_card_height() -> None:
        remember_root(root, {**recall_extra(), "card_h": split["height"]})

    def split_press(event) -> None:
        split["y0"], split["h0"] = event.y_root, card.winfo_height()

    def split_drag(event) -> None:
        set_card_height(split["h0"] + event.y_root - split["y0"])

    def split_toggle(_e=None) -> None:
        set_card_height(None if split["height"] else card_min())
        save_card_height()

    for _w in (splitter, grip_line):
        _w.bind("<ButtonPress-1>", split_press)
        _w.bind("<B1-Motion>", split_drag)
        _w.bind("<ButtonRelease-1>", lambda _e: save_card_height())
        _w.bind("<Double-Button-1>", split_toggle)
    Tip(splitter, lambda: "上下拖动调整步骤区高度；双击收起/展开状态卡")
    app.after(400, lambda: set_card_height(recall_extra().get("card_h")))

    # 页脚先占底，步骤列表再吃剩余空间。
    footer = tk.Frame(main, bg="#f5f7f6")
    footer.pack(side="bottom", fill="x")
    detail_wrap = tk.Frame(main, bg="#f5f7f6")
    detail_wrap.pack(fill="both", expand=True, padx=12, pady=(2, 0))
    detail = tk.Text(detail_wrap, height=6, width=40, wrap="word", state="disabled", cursor="arrow",
                     bg="#ffffff", fg="#25332e", relief="flat", padx=8, pady=6,
                     highlightthickness=1, highlightbackground="#e1e8e5", font=FONT,
                     spacing1=3, spacing3=3)
    detail.pack(side="left", fill="both", expand=True)
    try:
        dscroll = ttk.Scrollbar(detail_wrap, orient="vertical", command=detail.yview)
        dscroll.pack(side="right", fill="y")
        detail.configure(yscrollcommand=dscroll.set)
    except tk.TclError:
        pass
    detail.tag_config("head", foreground="#2f6558", font=HEAD_FONT)
    detail.tag_config("cur", foreground="#2f6558", background="#e7f0ed", lmargin1=4, rmargin=4)
    detail.tag_config("curwait", foreground="#6f5424", background="#f8f1e2", lmargin1=4, rmargin=4)
    detail.tag_config("done", foreground="#9ba8a3")
    detail.tag_config("detected", foreground="#7f9c8f", font=SMALL)
    detail.tag_config("warn", foreground="#a67c34", font=SMALL)
    detail.tag_config("what", foreground="#6b7a74", font=SMALL, lmargin2=40)
    detail.tag_config("out", foreground="#9ba8a3", font=SMALL, lmargin2=40)
    detail.tag_config("file", foreground="#2f6558", font=SMALL, underline=True)
    detail.tag_config("todo", foreground="#25332e", lmargin2=22)
    current_detail_stage: list = [None]
    user_picked: list = [False]
    follow: dict = {"next": 0.0, "manual_at": 0.0}  # 自动跟随正在跑的案例
    last_tabs_state: list = [None, None, False]

    # 页脚：主按钮 + 一排小工具 + 项目 + 状态提示。
    btns = tk.Frame(footer, bg="#f5f7f6")
    btns.pack(padx=12, pady=(10, 0), fill="x")
    btn_refs: list = []
    tools_row = tk.Frame(footer, bg="#f5f7f6")
    tools_row.pack(padx=8, pady=(6, 0), fill="x")

    def tool(text: str, cmd, tip: str = ""):
        lb = tk.Label(tools_row, text=text, bg="#f5f7f6", fg="#2f6558", font=SMALL, cursor="hand2", padx=6, pady=3)
        lb.pack(side="left")
        lb.bind("<Button-1>", lambda _e: cmd())
        lb.bind("<Enter>", lambda _e: lb.configure(bg="#e7f0ed"), add="+")
        lb.bind("<Leave>", lambda _e: lb.configure(bg="#f5f7f6"), add="+")
        if tip:
            Tip(lb, lambda: tip)
        return lb

    tool("导航页", lambda: open_dashboard(), "打开 00_导航/导航.html：全部步骤、报告链接")
    def open_margin():
        try:
            launch_margin_calculator(root,home)
            toast("毛利计算器正在打开")
        except OSError as exc:toast("毛利计算器未打开："+str(exc))
    tool("💰 毛利计算",open_margin,"打开当前产品的统一毛利计算器")
    def open_copy_report():
        path = resolve_output(root, "listing_report.html") or resolve_output(root, "07_Listing文案.html")
        if path: open_file(str(path))
        else: toast("文案报告尚未生成")
    tool("📋 复制文案",open_copy_report,"打开文案报告，使用字段复制按钮")
    tool("收成气泡", lambda: show_bubble(), "缩成屏幕边的小气泡，点一下展开")
    tool("藏到边上", lambda: dock_to_nearest_edge(), "贴到最近的屏幕边，只露一条缝，鼠标碰一下回来")
    pin_box = tk.Label(tools_row, text="📌 常驻", bg="#f5f7f6", fg="#9ba8a3", font=SMALL, cursor="hand2", padx=6, pady=3)
    pin_box.bind("<Button-1>", lambda _e: pin_var.set(not pin_var.get()))
    pin_var.trace_add("write", lambda *_a: pin_box.configure(fg="#2f6558" if pin_var.get() else "#9ba8a3",
                                                             bg="#e7f0ed" if pin_var.get() else "#f5f7f6"))
    pin_box.pack(side="left", padx=(4, 0))
    Tip(pin_box, lambda: "点一下切换：常驻时保持展开，不自动收成气泡、不贴边藏")
    helpmark = tk.Label(tools_row, text="?", bg="#f5f7f6", fg="#9ba8a3", font=SMALL, cursor="question_arrow", padx=6)
    helpmark.pack(side="left")
    Tip(helpmark, lambda: "拖顶部彩条移动；推到上/左/右边缘自动藏（只剩一条缝，碰一下回来）；"
                          "右下角 ◢ 拖动缩放。窗口越小显示越精简：窄时只看当前步骤和状态。")
    grip = tk.Label(tools_row, text="◢", bg="#f5f7f6", fg="#9ba8a3", font=FONT, cursor="sizing", padx=4)
    grip.pack(side="right")
    grip.bind("<ButtonPress-1>", grip_press)
    grip.bind("<B1-Motion>", grip_drag)
    grip.bind("<ButtonRelease-1>", grip_release)

    proj_row = tk.Frame(footer, bg="#f5f7f6")
    proj_row.pack(padx=12, pady=(2, 0), fill="x")
    proj_label = tk.Label(proj_row, text="", bg="#f5f7f6", fg="#6b7a74", font=SMALL, cursor="hand2", anchor="w")
    proj_label.pack(side="left", fill="x", expand=True)
    Tip(proj_label, lambda: f"{root}\n点一下切换项目")
    status = tk.Label(footer, text="", bg="#f5f7f6", fg="#9ba8a3", font=SMALL, anchor="w", justify="left")
    status.pack(padx=12, pady=(0, 8), fill="x")
    try:
        save_history(root)
    except (OSError, ValueError):
        pass

    def fit_text(text: str, width_px: int) -> str:
        import tkinter.font as tkfont
        f = tkfont.Font(font=SMALL)
        if f.measure(text) <= width_px:
            return text
        while text and f.measure(text + "…") > width_px:
            text = text[:-1]
        return text + "…"

    def short_name(p: Path) -> str:
        name = p.name
        return name if len(name) <= 34 else name[:33] + "…"

    def select_stage(i: int) -> None:
        try:
            i = int(i)
        except (TypeError, ValueError):
            return
        if 0 <= i < len(STAGE_ORDER):
            selected_idx[0] = i
            user_picked[0] = True
            # Highlight first so the click feels instant; reading state and files happens right after.
            refresh_tabs(last_tabs_state[0], last_tabs_state[1], last_tabs_state[2])
            app.update_idletasks()
            app.after(1, lambda s=STAGE_ORDER[i]: render_detail(s, force=True))

    def tab_text(idx: int, parts) -> str:
        info = parts[idx] if parts and idx < len(parts) else None
        if not info:
            return STAGE_ORDER[idx]
        compact = layout_mode[0] == "compact" or narrow[0]
        prog = f"{info['cursor']}/{info['total']}"
        return f"{info['stage']} {prog}" if compact else f"{info['stage']} {info.get('short', '')} {prog}"

    def refresh_tabs(parts=None, current=None, started: bool = False) -> None:
        for idx, btn in enumerate(tab_btns):
            try:
                btn.configure(text=tab_text(idx, parts))
                is_current = started and STAGE_ORDER[idx] == current
                if is_current:
                    btn.configure(bg="#3f7f6e", fg="#ffffff", highlightbackground="#3f7f6e")
                elif idx == selected_idx[0]:
                    btn.configure(bg="#e7f0ed", fg="#2f6558", highlightbackground="#3f7f6e")
                else:
                    btn.configure(bg="#ffffff", fg="#25332e", highlightbackground="#e1e8e5")
            except tk.TclError:
                pass
        flow_tabs()

    def flow_tabs() -> None:
        """按当前宽度重排：放得下几个就一行几个，其余换行。"""
        try:
            avail = max(120, (tabs.winfo_width() if tabs.winfo_width() > 1 else app.winfo_width() - 24))
            need = max(b.winfo_reqwidth() for b in tab_btns) + 6
            per_row = max(1, min(len(tab_btns), avail // need))
            if per_row == 4:
                per_row = 3  # 4+1 很难看；5 放不下时用 3+2
            for b in tab_btns:
                b.grid_forget()
            for c in range(len(tab_btns)):
                tabs.grid_columnconfigure(c, weight=1 if c < per_row else 0, uniform="tab" if c < per_row else "")
            for i, b in enumerate(tab_btns):
                r, c = divmod(i, per_row)
                b.grid(row=r, column=c, padx=(0 if c == 0 else 3, 0 if c == per_row - 1 else 3), pady=(0, 6), sticky="ew")
        except tk.TclError:
            pass

    def copy_cmd(text: str, label: str) -> None:
        try:
            app.clipboard_clear()
            app.clipboard_append(text)
            toast(f"已复制「{label}」，去 Codex 对话框粘贴回车")
        except tk.TclError:
            status.configure(text=f"复制失败，口令：{text}")

    def rebuild_buttons(cmds: list) -> None:
        for b in btn_refs:
            b.destroy()
        btn_refs.clear()
        for label, cmd in cmds:
            b = tk.Label(btns, text=label, bg="#3f7f6e", fg="#ffffff", font=FONT, cursor="hand2", pady=7)
            b.pack(side="left", expand=True, fill="x")
            b.bind("<Button-1>", lambda _e, c=cmd, lb=label: copy_cmd(c, lb))
            b.bind("<Enter>", lambda _e, w=b: w.configure(bg="#2f6558"), add="+")
            b.bind("<Leave>", lambda _e, w=b: w.configure(bg="#3f7f6e"), add="+")
            btn_refs.append(b)

    def open_dashboard() -> None:
        import subprocess
        import webbrowser
        page = root / "00_导航" / "导航.html"
        try:
            subprocess.run([sys.executable, "-B", str(home / "scripts" / "render_nav_dashboard.py"),
                            "--root", str(root), "--home", str(home)],
                           capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            webbrowser.open(page.as_uri())
            toast("已打开导航页（全部步骤和报告都在那）")
        except Exception as exc:  # noqa: BLE001 - 要给被动用的运营看人话
            status.configure(text=f"打不开导航页：{exc}")

    last_detail: list = [None, None]

    note_tip: dict = {"map": {}, "win": None}

    def open_file(path: str) -> None:
        import subprocess
        import webbrowser
        try:
            if "#" in path:  # 指南直接跳到对应的一节：带锚点只能交给浏览器
                base, frag = path.split("#", 1)
                webbrowser.open(Path(base).as_uri() + "#" + frag)
                toast(f"已打开 {Path(base).name}")
                return
            if sys.platform == "darwin":
                subprocess.Popen(["open", path])
            elif sys.platform == "win32":
                os.startfile(path)  # noqa: S606 - 用户点了自己产品目录里的报告
            else:
                webbrowser.open(Path(path).as_uri())
            toast(f"已打开 {Path(path).name}")
        except (OSError, ValueError) as exc:
            toast(f"打不开：{exc}")

    def show_note(key: str) -> None:
        hide_note()
        text = note_tip["map"].get(key)
        if not text:
            return
        try:
            win = tk.Toplevel(app)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            tk.Label(win, text=text, bg="#25332e", fg="#ffffff", font=SMALL, justify="left",
                     wraplength=320, padx=8, pady=6).pack()
            win.geometry(f"+{app.winfo_pointerx() + 12}+{app.winfo_pointery() + 12}")
            note_tip["win"] = win
            mac_float_everywhere()
        except tk.TclError:
            note_tip["win"] = None

    def hide_note() -> None:
        if note_tip["win"]:
            try:
                note_tip["win"].destroy()
            except tk.TclError:
                pass
            note_tip["win"] = None

    def render_detail(stage: str, force: bool = False) -> None:
        current_detail_stage[0] = stage
        try:
            info = stage_detail(root, home, stage)
        except ValueError as exc:
            info = None
            rows = [(str(exc), "todo")]
        else:
            import tkinter.font as tkfont
            row_font = tkfont.Font(font=FONT)
            rows = format_rows(info, width_px=max(100, detail.winfo_width() - 24), measure=row_font.measure)
        key = repr(rows)
        if key == last_detail[1] and stage == last_detail[0] and not force:
            return  # 内容没变不重绘：不闪、不丢滚动位置
        last_detail[0], last_detail[1] = stage, key
        detail.configure(state="normal")
        detail.delete("1.0", "end")
        note_tip["map"] = {}
        for i, row in enumerate(rows):
            text, tag = row[0], row[1]
            key = f"row{i}"
            tags = (tag, key)
            if len(row) > 2 and row[2]:
                note_tip["map"][key] = row[2]
                detail.tag_bind(key, "<Enter>", lambda _e, k=key: show_note(k))
                detail.tag_bind(key, "<Leave>", lambda _e: hide_note())
            if len(row) > 3 and row[3]:
                detail.tag_bind(key, "<Button-1>", lambda _e, f=row[3]: open_file(f))
                detail.tag_bind(key, "<Enter>", lambda _e: detail.configure(cursor="hand2"), add="+")
                detail.tag_bind(key, "<Leave>", lambda _e: detail.configure(cursor="arrow"), add="+")
            if tag == "file":
                detail.insert("end", "      ↳ ", ("out",))
            elif tag == "out":
                text = "      " + text
            detail.insert("end", text + "\n", tags)
        detail.configure(state="disabled")
        # 当前步自动滚到可见。
        try:
            for tag in ("curwait", "cur"):
                rng = detail.tag_ranges(tag)
                if rng:
                    line = int(str(rng[0]).split(".")[0])
                    detail.see(f"{max(1, line - 3)}.0")  # 当前步上方留几行，别切到半行
                    detail.see(rng[0])
                    break
        except tk.TclError:
            pass

    def switch_root(new, save: bool = True, manual: bool = True) -> bool:
        nonlocal root
        if manual:
            follow["manual_at"] = time.time()
        try:
            nr = Path(str(new)).expanduser().resolve()
        except (OSError, ValueError):
            return False
        if not nr.is_dir():
            status.configure(text=f"目录不存在：{new}")
            return False
        try:
            locked = claim_instance(nr)
        except (OSError, ValueError):
            locked = True
        root = nr
        if save:
            try:
                save_history(nr)
                remember_root(nr)
            except (OSError, ValueError):
                pass
        selected_idx[0] = 0
        user_picked[0] = False
        last_sig[0] = last_sig[1] = None
        last_detail[0] = last_detail[1] = None
        last_tabs_state[:] = [None, None, False]
        try:
            app.title(f"星空导航悬浮窗 · {nr.name}")
        except tk.TclError:
            pass
        if not locked:
            status.configure(text=f"{nr.name} · 已有另一个悬浮窗在跑，这里只看不锁")
        return True

    def browse_root() -> None:
        try:
            from tkinter import filedialog
            d = filedialog.askdirectory(title="选产品根目录（有 workflow_state.yaml 的那个）")
        except (ImportError, tk.TclError):
            return
        if d:
            switch_root(d)

    def project_menu(_e=None) -> None:
        menu = tk.Menu(app, tearoff=0)
        try:
            cands = find_projects(root)
        except (OSError, ValueError):
            cands = [root]
        for p in cands[:15]:
            menu.add_command(label=("✓ " if Path(p) == root else "   ") + Path(p).name,
                             command=lambda q=p: switch_root(q))
        menu.add_separator()
        menu.add_command(label="选择其他文件夹…", command=browse_root)
        try:
            menu.tk_popup(proj_label.winfo_rootx(), proj_label.winfo_rooty() + proj_label.winfo_height())
        finally:
            menu.grab_release()

    proj_label.bind("<Button-1>", project_menu)

    # 尺寸自适应：compact（窄或矮）只看状态卡；medium 加阶段标签；full 再加步骤列表。
    layout_mode: list = ["full"]
    narrow: list = [False]

    def apply_layout(_e=None) -> None:
        if mode[0] != "main":
            return
        try:
            place_grips()
        except (NameError, tk.TclError):
            pass
        try:
            w, h = app.winfo_width(), app.winfo_height()
        except tk.TclError:
            return
        # 高度决定显示多少；宽度窄只让标签和文字变紧凑，不藏步骤列表。
        new = "compact" if h < 330 else ("medium" if h < 470 else "full")
        was_narrow, narrow[0] = narrow[0], w < 340
        wrap = max(150, w - 12 * 2 - 12 * 2 - 4)  # 窗口边距 + 卡片内边距 + 边框
        for lb in (wait, nxt, one):
            lb.configure(wraplength=wrap)
        title.configure(wraplength=max(140, w - 70))
        subtitle.configure(wraplength=max(140, w - 70))
        if new != layout_mode[0]:
            layout_mode[0] = new
            if new == "full":
                if not detail_wrap.winfo_ismapped():
                    splitter.pack(fill="x", padx=12, after=card)
                    detail_wrap.pack(fill="both", expand=True, padx=12, pady=(2, 0))
                if not one.winfo_ismapped():
                    one.pack(fill="x", padx=12, pady=(0, 10))
            else:
                splitter.pack_forget()
                detail_wrap.pack_forget()
                one.pack_forget()
            if new == "compact":
                proj_row.pack_forget()
            elif not proj_row.winfo_ismapped():
                proj_row.pack(padx=12, pady=(2, 0), fill="x", before=status)
            refresh_tabs(last_tabs_state[0], last_tabs_state[1], last_tabs_state[2])
        elif was_narrow != narrow[0]:
            refresh_tabs(last_tabs_state[0], last_tabs_state[1], last_tabs_state[2])
        else:
            flow_tabs()

    app.bind("<Configure>", lambda e: apply_layout() if e.widget is app else None, add="+")

    last_sig: list = [None, None]
    toast_until: list = [0.0]

    def toast(text: str) -> None:
        status.configure(text=text)
        toast_until[0] = time.time() + 4

    def refresh_dashboard_async() -> None:
        """进度一变就在后台重生成 00_导航/导航.html，页面和悬浮窗始终一致。"""
        import subprocess
        try:
            subprocess.Popen([sys.executable, "-B", str(home / "scripts" / "render_nav_dashboard.py"),
                              "--root", str(root), "--home", str(home)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass

    def paint_status(status_key: str) -> None:
        badge.configure(fg=status_color(status_key))

    def follow_active_case() -> None:
        """Codex 里开了新案例或换了案例时，悬浮窗自动切过去；用户刚手动选过就先不动。"""
        now = time.time()
        if now < follow["next"] or now - follow["manual_at"] < MANUAL_HOLD:
            return
        follow["next"] = now + FOLLOW_EVERY
        try:
            found = active_case(root, load_history(), follow["manual_at"])
        except (OSError, ValueError):
            found = None
        if found is not None and switch_root(found, manual=False):
            toast(f"已自动切到正在跑的案例：{found.name}")

    def poll() -> None:
        follow_active_case()
        snap = snapshot(root, home)
        parts = snap.get("stages", [])
        started = bool(snap.get("started"))
        current = snap.get("stage") if started else None
        status_key = snap.get("status", "running") if started else "idle"
        icon = STATUS_COLORS.get(status_key, STATUS_COLORS["idle"])[1]
        paint_status(status_key)
        if started:
            short = STAGE_SHORT.get(snap["stage"], "")
            new_bubble = (f"{icon} {snap['stage']} {snap['cursor']}/{snap['total']}\n"
                            + ("等你拍板" if status_key == "waiting" else short))
        else:
            new_bubble = "🧭 星空导航\n点我启动"
        if bubble_text.get() != new_bubble:
            bubble_text.set(new_bubble)
        sig = repr([(p["stage"], p["cursor"], p["total"], p.get('accepted')) for p in parts]) + str(current) + repr(snap.get('acceptance'))
        if sig != last_sig[0]:
            if last_sig[0] is not None:
                refresh_dashboard_async()
            last_sig[0] = sig
            last_tabs_state[:] = [parts, current, started]
            if started and not user_picked[0]:
                selected_idx[0] = STAGE_ORDER.index(snap["stage"]) if snap["stage"] in STAGE_ORDER else 0
            refresh_tabs(parts, current, started)
        if not started:
            title.configure(text="星空导航 · 还没开始")
            subtitle.configure(text="三步：点下面按钮复制口令 → 粘贴到 Codex 回车 → 回来看进度")
            badge.configure(text="🧭 选一个入口开始")
            wait.configure(text="新品从 S1 选品开始；已在卖的产品用「老品诊断修复」。")
            card_state["wait_full"] = ""
            dots.configure(text="")
            nxt.configure(text="")
            one.configure(text="")
            cmds = [(s["label"], s["cmd"]) for s in snap.get("starters", [])]
        else:
            inferred_tag = "（按文件推断）" if snap.get("stage_inferred") else ""
            title.configure(text=f"{snap['stage']} {STAGE_SHORT.get(snap['stage'], '')} · 第 {snap['cursor']}/{snap['total']} 步{inferred_tag}")
            subtitle.configure(text=snap.get("step", "") + ('\n🤖 本步：' + snap['model'] if snap.get('model') else ''))
            badge.configure(text=f"{icon} {snap.get('status_text', '')}")
            wait_full = snap.get("wait_text") or snap.get("action", "")
            for prefix in ("要你拍板：", "要你拍板:"):
                if status_key == "waiting" and wait_full.startswith(prefix):
                    wait_full = wait_full[len(prefix):].lstrip()
                    break
            card_state["wait_full"] = wait_full
            wait.configure(text=wait_full if len(wait_full) <= 60 else wait_full[:60] + "…（悬停看全文）")
            dots.configure(text=snap.get("bar", "").replace("■", "●").replace("□", "○"), fg=status_color(status_key))
            nxt.configure(text=f"下一步：{snap['next']}" + ('\n🤖 ' + snap['next_model'] if snap.get('next_model') else ''))
            extra = f"\n✨ 可选待办：{snap['optional_line']}" if snap.get("optional_line") else ""
            one.configure(text=f"刚完成：{snap['one_line']}{extra}")
            cmds = [("补齐数据" if status_key == 'acceptance_pending' else "复制继续口令", snap.get("resume_cmd", RESUME_CMD))]
        if cmds != last_sig[1]:
            last_sig[1] = cmds
            rebuild_buttons(cmds)
        render_detail(STAGE_ORDER[selected_idx[0]])
        try:
            room = max(80, app.winfo_width() - 24 - 30)
        except tk.TclError:
            room = 300
        proj_label.configure(text="📁 " + fit_text(root.name, room) + " ▾")
        if time.time() > toast_until[0]:
            status.configure(text=f"{snap.get('updated', '—')}更新 · 每 {interval:g} 秒自动刷新")
        apply_layout()
        app.after(max(500, int(interval * 1000)), poll)

    # 四边四角都能拉伸：无边框窗口没有系统拉伸，自己放几条细边当抓手。
    GRIP = 5
    edge_grips: list = []
    grip_state: dict = {}

    def grip_start(event, side: str) -> None:
        try:
            grip_state.update(side=side, x0=event.x_root, y0=event.y_root, x=app.winfo_x(), y=app.winfo_y(),
                              w=app.winfo_width(), h=app.winfo_height())
        except tk.TclError:
            pass

    def grip_move(event) -> None:
        if not grip_state:
            return
        s = grip_state["side"]
        dx, dy = event.x_root - grip_state["x0"], event.y_root - grip_state["y0"]
        x, y, w, h = grip_state["x"], grip_state["y"], grip_state["w"], grip_state["h"]
        if "e" in s:
            w = max(260, w + dx)
        if "s" in s:
            h = max(220, h + dy)
        if "w" in s:
            nw = max(260, w - dx)
            x, w = x + (w - nw), nw
        if "n" in s:
            nh = max(220, h - dy)
            y, h = y + (h - nh), nh
        try:
            app.geometry(f"{w}x{h}+{x}+{y}")
        except tk.TclError:
            pass

    def grip_end(_e=None) -> None:
        grip_state.clear()
        _remember_main_size()
        if edge["side"]:
            try:
                edge["flush"] = flush_pos(edge["side"])
            except tk.TclError:
                pass

    GRIP_SPECS = [  # side, place kwargs, cursor, color
        ("n", dict(relx=0, rely=0, relwidth=1, height=GRIP), "sb_v_double_arrow", "#3f7f6e"),
        ("s", dict(relx=0, rely=1, relwidth=1, height=GRIP, anchor="sw"), "sb_v_double_arrow", "#f5f7f6"),
        ("w", dict(relx=0, rely=0, width=GRIP, relheight=1), "sb_h_double_arrow", None),
        ("e", dict(relx=1, rely=0, width=GRIP, relheight=1, anchor="ne"), "sb_h_double_arrow", None),
        ("nw", dict(relx=0, rely=0, width=GRIP * 3, height=GRIP), "top_left_corner", "#3f7f6e"),
        ("ne", dict(relx=1, rely=0, width=GRIP * 3, height=GRIP, anchor="ne"), "top_right_corner", "#3f7f6e"),
        ("sw", dict(relx=0, rely=1, width=GRIP * 3, height=GRIP * 3, anchor="sw"), "bottom_left_corner", "#f5f7f6"),
        ("se", dict(relx=1, rely=1, width=GRIP * 3, height=GRIP * 3, anchor="se"), "bottom_right_corner", "#f5f7f6"),
    ]
    for side, place_kw, cursor, color in GRIP_SPECS:
        g = tk.Frame(main, bg=color or "#f5f7f6", cursor=cursor, highlightthickness=0, bd=0)
        g.bind("<ButtonPress-1>", lambda e, s=side: grip_start(e, s))
        g.bind("<B1-Motion>", grip_move)
        g.bind("<ButtonRelease-1>", grip_end)
        edge_grips.append((g, place_kw, side))

    def place_grips() -> None:
        try:
            hh = head.winfo_height() if head.winfo_height() > 1 else 60
        except tk.TclError:
            hh = 60
        for g, kw, side in edge_grips:
            try:
                if side in ("w", "e"):
                    # 左右边从标题栏下方开始，免得在绿色标题栏上出现一条浅色带
                    kw = dict(kw, y=hh, rely=0, relheight=1, height=-hh)
                g.place(**kw)
                g.lift()
            except tk.TclError:
                pass

    place_grips()
    main.bind("<Leave>", lambda _e: None if edge["side"] else arm_hide())
    main.bind("<Enter>", lambda _e: cancel_hide())
    remember_root(root)
    if start_min:
        show_bubble()
    else:
        show_main()
    try:
        app.update_idletasks()
    except tk.TclError:
        pass
    mac_float_everywhere()
    poll()
    edge_tick()
    app.mainloop()
    return 0


def _tk_python_reexec(argv: list[str]) -> None:
    """Dedicated runtimes (e.g. Homebrew Python on macOS) may lack tkinter while the system Python has it
    but lacks PyYAML. Re-launch under a Python that has tkinter, lending it only a private copy of the
    yaml package. Returns only if no alternative exists; run_gui then explains and falls back to HTML."""
    try:
        import tkinter
        if tkinter.TkVersion >= 8.6:
            return
    except ImportError:
        pass
    if os.environ.get("STARSKY_NAV_REEXEC"):
        return
    import shutil
    import subprocess
    lib = Path.home() / ".starsky-navlib"
    try:
        import yaml
        target = lib / "yaml"
        if not target.is_dir():
            lib.mkdir(parents=True, exist_ok=True)
            shutil.copytree(Path(yaml.__file__).parent, target, ignore=shutil.ignore_patterns("*.so", "*.pyd", "__pycache__"))
    except ImportError:
        pass
    candidates = ["/Library/Frameworks/Python.framework/Versions/Current/bin/python3", shutil.which("python3"), shutil.which("py"), "/usr/bin/python3"]
    for cand in candidates:
        if not cand or not Path(cand).is_file() or Path(cand).resolve() == Path(sys.executable).resolve():
            continue
        try:
            # Tk 8.5 (Apple's system Python) draws blank black windows on current macOS; require 8.6+.
            ok = subprocess.run([cand, "-c", "import tkinter,sys;sys.exit(tkinter.TkVersion<8.6)"], capture_output=True, timeout=30).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        if ok:
            env = {**os.environ, "STARSKY_NAV_REEXEC": "1", "PYTHONPATH": str(lib) + os.pathsep + os.environ.get("PYTHONPATH", "")}
            os.execve(cand, [cand, str(Path(__file__).resolve()), *argv], env)


def launch_margin_calculator(root: Path, home: Path):
    """Run the installed shared engine launcher without shell/console windows."""
    import subprocess
    root=Path(root).expanduser().resolve();script=Path(home).resolve()/'scripts/s1_margin_calculator.py'
    if not root.is_dir() or not script.is_file():raise FileNotFoundError('毛利计算器入口或产品目录不存在')
    return subprocess.Popen([sys.executable,'-B',str(script),'open','--root',str(root)],
        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=None, help="产品根目录（真实路径）")
    ap.add_argument("--home", default=None, help="插件 home（templates/ 所在目录）")
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--idle", type=float, default=30.0, help="离开窗口多久自动收成气泡（秒，0=不自动收）")
    ap.add_argument("--minimized", action="store_true", help="启动时直接是气泡")
    ap.add_argument("--check", action="store_true", help="只打印 JSON 快照，不开窗口")
    ap.add_argument("--stage", choices=STAGE_ORDER, default=None, help="--check 联用：打印某阶段嵌窗详情")
    args = ap.parse_args(argv)
    root = resolve_root(args.root)
    home = Path(args.home).expanduser().resolve() if args.home else Path(__file__).resolve().parents[1]
    if args.check:
        if args.stage:
            print(json.dumps(stage_detail(root, home, args.stage), ensure_ascii=False))
        else:
            print(json.dumps(snapshot(root, home), ensure_ascii=False))
        return 0
    if not root.is_dir():
        print("产品根不存在：" + str(root), file=sys.stderr)
        return 2
    if not claim_instance(root):
        # 已有悬浮窗在跑：不再弹"已在运行"，而是把它唤出来（从气泡/贴边展开并置前）。
        try:
            summon_path_for(root).write_text(str(time.time()), encoding="utf-8")
            print("悬浮窗已在运行，已把它唤出到前面。")
        except OSError:
            pass
        return 0
    _tk_python_reexec(list(sys.argv[1:] if argv is None else argv))
    return run_gui(root, home, args.interval, args.idle, args.minimized)


if __name__ == "__main__":
    raise SystemExit(main())
