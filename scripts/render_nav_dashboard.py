#!/usr/bin/env python3
"""Render the persistent Starsky navigator dashboard (offline single-file HTML).

Reads templates/stage_roadmap.yaml (steps/Skill/MCP/pause/artifacts),
templates/model_routing.yaml (task roles and execution policy), and
product-root workflow_state.yaml (cursor/records/path), then
writes <product_root>/00_导航/导航.html. No network, no CDN, no secrets.

Usage (run by the assistant, never by hand in chat):
    python "<home>/scripts/render_nav_dashboard.py" --root "<产品根>" --home "<home>" [--stage S1]
"""
from __future__ import annotations

import argparse
import html
import json
import os
import shlex
import sys
from pathlib import Path


STAGE_ORDER = ["S1", "S2", "S3", "S4", "S5"]
STAGE_LABEL = {
    "S1": "S1 · 选品板块",
    "S2": "S2 · 文案板块",
    "S3": "S3 · 视觉板块",
    "S4": "S4 · 广告板块",
    "S5": "S5 · 运营板块",
}


sys.path.insert(0, str(Path(__file__).resolve().parent))
from nav_float import latest_records, load_yaml, snapshot as float_snapshot, step_outputs, s1_scope_only  # noqa: E402
from make_nav_app import project_launcher  # noqa: E402
from plugin_freshness import install_record  # noqa: E402
from model_display import ModelDisplay, navigation_label as nav_model_label  # noqa: E402
from r01_persistence import write_if_changed  # noqa: E402
from stage_acceptance_summary import ACCEPTED_STATUSES, acceptance_summary, acceptance_label  # noqa: E402
from s1_roadmap import resolve_roadmap

# 常用口令：照抄入口 Skill 的口令表，不自创。
COMMANDS = [
    ("启动菜单", "启动星空"),
    ("新上架快速通道", "启动星空 S1"),
    ("写 / 优化 Listing", "启动星空 S2"),
    ("主副图 / A+ / 视频", "启动星空 S3"),
    ("广告搭建与诊断", "启动星空 S4"),
    ("今日巡检", "今日巡检"),
    ("老品诊断修复", "老品诊断修复"),
    ("从停下的地方继续", "继续"),
    ("我到哪了", "导航"),
    ("生成交接口令", "生成交接口令"),
    ("打开悬浮窗", "打开悬浮窗"),
    ("打开使用说明", "打开星空使用说明"),
]


def install_state_path(home: Path) -> Path:
    """Claude 版记录在 ~/.starsky-claude/current.json；Codex 版在 CODEX_HOME/starsky-codex.json。"""
    return install_record(Path(home))


def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def short_skill(skill: str) -> str:
    skill = (skill or "").strip() or "—"
    if "→" in skill:
        skill = skill.split("→")[-1].strip()
    if "/" in skill and " " not in skill:
        skill = skill.split("/")[-1].strip()
    return skill[:28]


def short_mcp(mcp) -> str:
    if not mcp:
        return "无"
    return "·".join(str(x) for x in mcp)[:28]


def progress_cells(n: int, total: int) -> str:
    total = max(int(total or 1), 1)
    n = max(min(int(n or 1), total), 0)
    return "●" * n + "○" * (total - n)


def stage_steps(roadmap: dict, stage: str) -> tuple[list, int, str]:
    stages = (roadmap or {}).get("stages", {})
    spec = stages.get(stage, {})
    steps = spec.get("steps", []) or []
    total = int(spec.get("total_steps", len(steps)) or len(steps) or 1)
    name = str(spec.get("name", stage))
    return steps, total, name


def state_cursor(state: dict, stage: str, total: int) -> tuple[int, str, set]:
    flow = state.get(stage.lower() + "_execution", {}) or {}
    cursor = flow.get("cursor", 1)
    try:
        cursor = int(cursor)
    except (TypeError, ValueError):
        cursor = 1
    cursor = max(1, min(cursor, max(total, 1)))
    path = str(flow.get("path", "condition_first") or "condition_first")
    done = set()
    for rec in flow.get("records", []) or []:
        if stage == 'S3' and flow.get('roadmap_version') == 'r03.s3' and rec.get('outcome') not in {'complete','deferred','skipped'}:continue
        try:
            done.add(int(rec.get("n")))
        except (TypeError, ValueError):
            continue
    if flow.get("delivery_status") == "complete":
        done.add(11 if stage == 'S3' and flow.get('roadmap_version')=='r03.s3' else total)
    return cursor, path, done


def step_display(step: dict, path: str) -> str:
    if path == "supply_first" and step.get("supply_first_name"):
        return str(step["supply_first_name"])
    return str(step.get("name", ""))


def step_what(step: dict, path: str) -> str:
    if path == "supply_first" and step.get("supply_first"):
        return str(step["supply_first"])
    if step.get("condition_first") and path == "condition_first":
        return str(step["condition_first"])
    return step_display(step, path)


def resume_info(state: dict, roadmap: dict) -> dict:
    """回来一眼懂：上次停在哪、前面干了啥、下一步干啥。缺状态就直说未开始。"""
    current = state.get("current_stage")
    if current not in STAGE_ORDER:
        return {"started": False, "line": "尚未开始：回 Codex 输入 启动星空 S1",
                "next": "启动星空 S1", "action": "先选 S1–S5 或全链"}
    steps, total, _name = stage_steps(roadmap, current)
    cursor, path, done = state_cursor(state, current, total)
    by_n = {}
    for s in steps:
        try:
            by_n[int(s.get("n"))] = s
        except (TypeError, ValueError):
            continue
    last_name = ""
    last_summary = ""
    flow = state.get(current.lower() + "_execution", {}) or {}
    for rec in flow.get("records", []) or []:
        try:
            if int(rec.get("n")) <= cursor:
                last_summary = str(rec.get("summary", "") or last_summary)
        except (TypeError, ValueError):
            continue
    for n in sorted(done & set(range(1, total + 1)), reverse=True):
        if n <= cursor and by_n.get(n):
            last_name = step_display(by_n[n], path)
            break
    nxt = by_n.get(cursor + 1) if cursor < total else None
    if nxt is not None:
        nxt_text = f"第 {cursor + 1} 步 {step_display(nxt, path)}"
        pause = str(nxt.get("pause", "") or "").strip() or "无，我自动走；停顿再找你"
    elif cursor >= total:
        accepted = (state.get('stage_acceptance', {}).get(current, {}).get('status') in ACCEPTED_STATUSES
                    and flow.get('validation', {}).get('exit_code') in (None, 0))
        nxt_text = "完工：复制交接口令换窗" if accepted else '补齐数据并重新验收'
        pause = "复制交接口令，同一产品项目新开任务" if accepted else '补齐数据'
        if current == 'S5' and accepted:
            nxt_text, pause = '本轮结束', '查看本轮报告'
    else:
        nxt_text = f"第 {cursor} 步 {step_display(by_n.get(cursor, {}), path)}".strip()
        pause = str((by_n.get(cursor, {}) or {}).get("pause", "") or "").strip() or "继续本步"
    one = (last_summary or "").split("\n")[0].strip()
    if len(one) > 40:
        one = one[:40] + "…"
    return {"started": True, "stage": current, "cursor": cursor, "total": total,
            "line": f"上次停在 {current} {cursor}/{total} · 前面：{last_name or '—'} · 一句话：{one or '—'}",
            "next": nxt_text, "action": pause[:60]}


def flow_of(state: dict, stage: str) -> dict:
    return state.get(stage.lower() + "_execution", {}) or {}


def record_artifacts(state: dict, stage: str) -> dict:
    arts: dict = {}
    flow = state.get(stage.lower() + "_execution", {}) or {}
    for rec in flow.get("records", []) or []:
        for role, item in (rec.get("artifacts", {}) or {}).items():
            if isinstance(item, dict) and item.get("path"):
                arts.setdefault(role, item["path"])
    return arts


def model_for_step(routing: dict, step: dict) -> str:
    return ModelDisplay(routing).recommendation(step)


def build_html(home: Path, root: Path, active: str) -> str:
    routing = load_yaml(home / "templates/model_routing.yaml")
    models = ModelDisplay(routing)
    from s2_roadmap import project_state
    state = project_state(load_yaml(root / "workflow_state.yaml"))
    roadmap = resolve_roadmap(home,root,state)
    only_s1 = s1_scope_only(root, state)
    current = state.get("current_stage") if state.get("current_stage") in STAGE_ORDER else None
    active = active if active in STAGE_ORDER else (current or "S1")

    tabs = []
    for stage in STAGE_ORDER:
        steps, total, _name = stage_steps(roadmap, stage)
        cursor, _p, done = state_cursor(state, stage, total)
        cls = "tab active" if stage == active else "tab"
        summary=acceptance_summary(root,state,stage)
        accepted = summary['accepted'] and not summary.get('review_pending')
        mark = ' ⚠' if summary.get('review_pending') else (" ✓" if accepted else " ⚠") if total and done >= set(range(1, total + 1)) else ""
        tabs.append(
            f'<a class="{cls}" href="#{stage}">{esc(STAGE_LABEL[stage])} {cursor}/{total}{esc(mark)}</a>'
        )

    sections = []
    for stage in STAGE_ORDER:
        steps, total, name = stage_steps(roadmap, stage)
        cursor, path, done = state_cursor(state, stage, total)
        acceptance = acceptance_summary(root, state, stage)
        delivery_step=roadmap.get('stages',{}).get(stage,{}).get('delivery_step',total)
        arts = record_artifacts(state, stage)
        if stage == 'S1':
            from s1_report_links import resolve_project_link
            arts = {role: resolve_project_link(root, root / 'README.md', value)[0] or '历史来源未随项目提供'
                    for role, value in arts.items()}
        rows = []
        for step in steps:
            try:
                n = int(step.get("n"))
            except (TypeError, ValueError):
                continue
            title = step_display(step, path)
            what = step_what(step, path)
            skill = str(step.get("skill", "") or "—")
            mcp_list = list(step.get("mcp", []) or [])
            mcp_opt = list(step.get("mcp_optional", []) or [])
            mcp_text = " / ".join(mcp_list) or "无需外部 MCP"
            if mcp_opt:
                mcp_text += "（可选：" + " / ".join(mcp_opt) + "）"
            model = nav_model_label(models.task(model_for_step(routing, step)))
            pause = str(step.get("pause", "") or "")
            if n == delivery_step and not acceptance['accepted']:
                what, pause = '交付报告并完成验收', acceptance_label(acceptance)
                if stage == 'S2':
                    if acceptance.get('action'):pause += '；'+acceptance['action']
                    elif acceptance.get('count') is not None:pause += '；按修改清单改 '+str(acceptance['count'])+' 处'
            artifacts = list(step.get("artifacts", []) or [])
            badge = "done" if n in done else ("current" if n == cursor else "todo")
            flag = "▶ " if n == cursor else ("✓ " if n in done else "")
            if n == delivery_step and (n in done or stage == 'S2' and n == cursor) and not acceptance['accepted']:
                badge, flag = 'acceptance_pending', '⚠ '
            pause_html = f'<span class="pause">⚠️ {esc(pause)}</span>' if pause else '<span class="nopause">—</span>'
            art_items = []
            record = latest_records(flow_of(state, stage)).get(str((step.get("control") or {}).get("id")))
            for o in step_outputs(root, step, record, stage == 'S2' or n in done or n == cursor):
                if (not acceptance['accepted'] or (only_s1 and stage == 'S1')) and '_NEXT_' in str(o.get('path') or o.get('name')):
                    continue
                if o["path"]:
                    found = Path(o["path"])
                    art_items.append(f"<li><a class='file' href='{esc(found.as_uri())}'>📄 {esc(found.name)}</a> <span class='ok'>已生成</span></li>")
                else:
                    art_items.append(f"<li>{esc(o['name'])}</li>")
            art_html = "".join(art_items) or "<li>—</li>"
            rows.append(
                "<article class='step %s' id='%s-%d'>"
                "<h3 title='%s'><span class='step-name'>%s%d · %s</span>"
                "<span class='step-model'>%s</span></h3>"
                "<ul class='step-files'>%s</ul></article>"
                % (badge, stage, n, esc(title + ' · ' + what + ' · ' + skill + ' · ' + pause),
                   esc(flag), n, esc(title), esc(model), art_html)
            )
        bar = progress_cells(len(done & set(range(1, total + 1))), total)
        art_links = "".join(
            f"<li><span class='role'>{esc(role)}</span> <span class='path'>{esc(path)}</span></li>"
            for role, path in arts.items() if acceptance['accepted'] or '_NEXT_' not in str(path)
        ) or "<li>本阶段尚无已落盘产物链接</li>"
        if stage == 'S1':
            from s1_decision_report import validate_output_set
            from s1_stage_report import HTML as OFFICIAL_S1_HTML
            decision_report = root / OFFICIAL_S1_HTML
            if decision_report.is_file() and validate_output_set(root):
                art_links += f"<li><a class='file' href='{esc(decision_report.as_uri())}'>S1 选品可行性研究报告 · 正式入口</a></li>"
            elif decision_report.is_file():
                art_links += '<li>产品开发决策透视：证据或输出已变化，请重新生成。</li>'
        handoff = ((roadmap.get("stages", {}).get(stage, {}).get("handoff", {}) or {}).get("file", ""))
        if stage == 'S3':
            from s3_navigation import summary as visual_summary
            visual = visual_summary(root,home)
            if visual['text']:
                art_links = '<li>'+esc(visual['text'])+'</li>'+''.join(
                    '<li><a class="file" href="'+esc(Path(item['path']).as_uri())+'">'+esc(item['label'])+'</a></li>'
                    for item in visual['links'])
        handoff_html = f"本阶段报告落地即生成 <code>{esc(handoff)}</code>，在同一个产品项目里新开 Codex 会话进入下一阶段，本窗口停止。" if handoff else "本轮巡检任务按实际轮次续用，没有下一阶段。"
        if acceptance.get('review_pending'):
            handoff_html='⚠️ 先不验收（已记录）；硬门槛仍按原生校验，后续独立验收。'
        elif not acceptance['accepted']:
            handoff_html = '⚠️ ' + acceptance_label(acceptance)
            if acceptance['issues']:
                handoff_html += ' · 先补：' + ' · '.join(acceptance['issues'][:3])
        if only_s1 and stage == 'S1':
            handoff_html = (('本阶段完成' if acceptance['accepted'] else '⚠️ ' + acceptance_label(acceptance))
                            + ' · 本次范围：仅 S1。查看决策报告，补证或评估拿样。')
        sections.append(
            "<section class='stage' id='%s'>"
            "<header><h2>%s</h2>"
            "<p class='prog'>进度 %d/%d %s · 路径 %s</p>"
            "<p class='handoff'>%s</p></header>"
            "<div class='cols'><nav class='toc'><ol>%s</ol></nav>"
            "<div class='steps'>%s</div></div>"
            "<footer><h4>本阶段已落盘产物（相对产品根）</h4><ul>%s</ul></footer>"
            "</section>"
            % (stage, esc(STAGE_LABEL[stage] + " · " + name), cursor, total, esc(bar),
               esc(path), handoff_html if acceptance['accepted'] and handoff else esc(handoff_html),
               "".join(
                   "<li><a href='#%s-%d'>%s%d · %s</a></li>" % (
                       stage, int(s.get("n")), "▶ " if int(s.get("n")) == cursor else "",
                       int(s.get("n")), esc(step_display(s, path)))
                   for s in steps if str(s.get("n", "")).isdigit()
               ),
               "".join(rows), art_links)
        )

    import time as _time
    res = resume_info(state, roadmap)
    if only_s1 and current == 'S1':
        res['next'] = '仅 S1：完成当前证据核查后查看产品开发决策报告'
        res['action'] = '补证或评估拿样；采购须另行授权'
    try:
        snap = float_snapshot(root, home)
    except Exception:  # noqa: BLE001 - 导航页不能因为状态解析失败而不出
        snap = {}
    model_resume = ''
    if snap.get('started'):
        model_resume = ('<p class="modelline">🤖 本步：' + esc(snap.get('model', ''))
                        + (' ｜ 下一步：' + esc(snap['next_model']) if snap.get('next_model') else '') + '</p>')
        res['next'], res['action'] = snap.get('next', res.get('next', '')), snap.get('action', res.get('action', ''))
    status_html = ""
    if snap.get("started"):
        icon = {"acceptance_pending": "⚠️", "waiting": "🟡", "running": "🟢", "stale": "⚪", "done": "✅"}.get(snap.get("status"), "🧭")
        detail = snap.get("wait_text") or ""
        status_html = f"<p class='statusline {esc(snap.get('status', ''))}'>{icon} <b>{esc(snap.get('status_text', ''))}</b>{('：' + esc(detail)) if detail else ''}</p>"
        if snap.get('report_summary'):status_html+='<p class="report-decision">唯一结论：'+esc(snap['report_summary']['label'])+'</p>'
    res_line = esc(res.get("line", ""))
    res_next = esc(res.get("next", ""))
    res_action = esc(res.get("action", ""))
    stamp = esc(_time.strftime("%Y-%m-%d %H:%M:%S"))
    commands = [(label, cmd) for label, cmd in COMMANDS
                if cmd != '生成交接口令' or (current and current != 'S5' and acceptance_summary(root, state, current)['accepted'] and not (only_s1 and current == 'S1'))]
    if snap.get('status') == 'acceptance_pending':
        commands.insert(0, ('补齐数据', snap.get('resume_cmd', '补齐验收数据')))
    seasonal_html = '<p class="seasonal">🎄 '+esc(snap.get('seasonal_text',''))+'</p>' if snap.get('seasonal_text') else ''
    copy_records=latest_records(flow_of(state,'S2'))
    copy_report = next((o['path'] for step in roadmap['stages'].get('S2',{}).get('steps',[]) for o in step_outputs(root,step,copy_records.get((step.get('control') or {}).get('id')),True) if o.get('path') and Path(o['path']).name in {'listing_report.html','07_Listing文案.html'}),None)
    if not copy_report:
        copy_report=next((str(root/name) for name in ('listing_report.html','07_Listing文案.html') if (root/name).is_file()),None)
    if copy_report:
        seasonal_html += '<p><a href="'+esc(Path(copy_report).as_uri())+'">📋 复制文案</a></p>'
    status_html += seasonal_html
    cmds_html = "".join(
        f"<button class='cmd' data-cmd='{esc(cmd)}'><b>{esc(label)}</b><span>{esc(cmd)}</span></button>"
        for label, cmd in commands)
    return """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="30">
<title>星空导航 · S1–S5 路线图</title>
<style>
:root{--bg:#f5f7f6;--card:#ffffff;--ink:#25332e;--muted:#6b7a74;--line:#e1e8e5;--brand:#3f7f6e;--brand-d:#2f6558;--warn:#a67c34;--warn-bg:#f8f1e2;--cur:#e7f0ed}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.7 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
header.top{position:sticky;top:0;z-index:5;background:var(--card);border-bottom:1px solid var(--line);padding:12px 18px}
header.top h1{margin:0;font-size:18px}
header.top p{margin:4px 0 0;color:var(--muted);font-size:13px}
nav.tabs{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
a.tab{border:1px solid var(--line);border-radius:999px;padding:5px 12px;text-decoration:none;color:var(--ink);background:#fff;font-size:13px}
a.tab.active{background:var(--brand);border-color:var(--brand);color:#fff}
main{max-width:1180px;margin:0 auto;padding:18px}
section.stage{background:var(--card);border:1px solid var(--line);border-radius:12px;margin:16px 0;overflow:hidden}
section.stage>header{padding:14px 18px;border-bottom:1px solid var(--line);background:#fbfefd}
section.stage h2{margin:0;font-size:17px}
.prog{color:var(--muted);margin:6px 0 0}
.handoff{margin:6px 0 0;font-size:13px}
.cols{display:grid;grid-template-columns:230px 1fr;gap:0}
nav.toc{border-right:1px solid var(--line);padding:12px 6px 12px 18px}
nav.toc ol{margin:0;padding-left:18px;font-size:13px}
nav.toc a{color:var(--ink);text-decoration:none}
nav.toc a:hover{color:var(--brand-d);text-decoration:underline}
div.steps{padding:6px 18px 14px}
article.step{border:1px solid var(--line);border-radius:10px;margin:12px 0;padding:12px 14px;background:#fff}
article.step.current{background:var(--cur);border-color:var(--brand)}
article.step h3{margin:0 0 6px;font-size:15px}
article.step h3{display:flex;gap:8px;align-items:center;min-width:0}
.step-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0;flex:1}
.step-model{white-space:nowrap;font-size:12px;font-weight:400;flex-shrink:0}
.step-files{margin:0;padding-left:16px;font-size:12px;overflow-wrap:anywhere}
article.step.acceptance_pending{border-color:var(--warn);background:var(--warn-bg)}
p.what{margin:0 0 8px}
dl{margin:0;display:grid;grid-template-columns:92px 1fr;gap:4px 10px;font-size:13px}
dt{color:var(--muted)}
dd{margin:0}
.pause{color:var(--warn);background:var(--warn-bg);border-radius:6px;padding:1px 8px}
.nopause{color:var(--muted)}
footer{border-top:1px solid var(--line);padding:10px 18px 14px;font-size:13px}
code{background:#eef3f1;border-radius:6px;padding:0 6px}
section.resume,section.bigblocks{background:var(--card);border:1px solid var(--line);border-radius:12px;margin:16px 0;padding:12px 18px}
section.resume h2,section.bigblocks h2{margin:0 0 6px;font-size:16px}
.statusline{margin:4px 0;padding:6px 10px;border-radius:8px;background:var(--cur)}
.statusline.waiting,.statusline.acceptance_pending{background:var(--warn-bg);color:#6f5424}
.cmds{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:8px}
button.cmd{text-align:left;border:1px solid var(--line);background:#fff;border-radius:10px;padding:8px 10px;cursor:pointer;font:inherit;color:var(--ink)}
button.cmd b{display:block;font-size:13px}
button.cmd span{display:block;color:var(--muted);font-size:12px}
button.cmd:hover{border-color:var(--brand)}
button.cmd.copied{background:var(--cur);border-color:var(--brand)}
button.cmd.copied span::after{content:"  ✓ 已复制";color:var(--brand-d)}
a.file{color:var(--brand-d)}
.ok{color:var(--brand);font-size:12px}
@media (max-width:860px){.cols{grid-template-columns:1fr}nav.toc{border-right:0;border-bottom:1px solid var(--line)}}
@media (max-width:430px){main{padding:8px}div.steps{padding:4px 8px}article.step{padding:8px}article.step h3{font-size:12px;gap:4px}.step-model{font-size:11px}section.stage>header{padding:10px}header.top{padding:10px}.path{overflow-wrap:anywhere}}
@media print{header.top{position:static}section.stage{break-inside:avoid}}
</style>
</head>
<body>
<header class="top">
<h1>🧭 星空导航 · S1–S5 路线图</h1>
<p>只读路线图步数/Skill/MCP/停顿 + 当前 workflow_state.yaml 进度；本页只有步骤与进度，不含任何凭证与登录态。离线可打开，打印恢复全部步骤。挂着看，30 秒自动刷新；以聊天步骤卡与阶段报告为准。</p>
<nav class="tabs">%s</nav>
</header>
<main><section class="resume" id="resume"><h2>📌 回来先看这里</h2>%s<p class="resumeline">%s</p><p class="nextline">⏭ 下一步：%s</p>%s<p class="actionline">🙋 待你：%s</p><p class="stamp">生成时间 %s · 挂着看，30 秒自动刷新</p></section><section class="bigblocks"><h2>💬 常用口令（点一下复制，回 Codex 粘贴）</h2><div class="cmds">%s</div></section>%s</main>
<script>location.hash = location.hash || "#%s";
document.querySelectorAll("button.cmd").forEach(function(b){b.addEventListener("click",function(){
  var t=b.getAttribute("data-cmd");var done=function(){b.classList.add("copied");setTimeout(function(){b.classList.remove("copied")},1200)};
  if(navigator.clipboard){navigator.clipboard.writeText(t).then(done,function(){prompt("复制这句：",t)})}else{prompt("复制这句：",t)}
})});</script>
</body>
</html>""" % ("".join(tabs), status_html, res_line, res_next, model_resume, res_action, stamp, cmds_html, "".join(sections), active)


def _ensure_desktop_shortcut(bat: Path, root: Path, home: Path) -> str | None:
    """仅在用户明确要求时创建桌面快捷方式。

    按产品分名（StarNav-<产品目录名>），多产品不互盖；每次重渲都刷新一次，
    插件升级后路径自动跟新。只在 Windows 做，Mac 靠 .command；任何失败都只返回
    None（调用方记警告），绝不阻断导航生成。"""
    if sys.platform != "win32":
        return None
    import re as _re
    import tempfile as _tf
    import subprocess as _sp
    try:
        safe = _re.sub(r'[<>:"/\\\\|?*\x00-\x1f]', "_", root.name).strip()[:60] or "导航"
        desk = Path(os.environ.get("USERPROFILE") or str(Path.home())) / "Desktop"
        link = desk / f"StarNav-{safe}.lnk"
        icon = home / "assets" / "starnav.ico"

        def _q(p) -> str:
            return str(p).replace("'", "''")

        ps = (
            "\ufeff$ErrorActionPreference = 'Stop'\n"
            "$w = New-Object -ComObject WScript.Shell\n"
            f"$s = $w.CreateShortcut('{_q(link)}')\n"
            f"$s.TargetPath = '{_q(bat)}'\n"
            f"$s.WorkingDirectory = '{_q(root)}'\n"
            "$s.Hotkey = 'CTRL+ALT+X'\n"
            f"$s.IconLocation = '{_q(icon)},0'\n"
            f"$s.Description = '星空导航 StarNav · {_q(root.name)}'\n"
            "$s.Save()\n"
        )
        with _tf.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8") as f:
            f.write(ps)
            tmp = f.name
        try:
            r = _sp.run(["powershell", "-NoLogo", "-NoProfile", "-ExecutionPolicy",
                           "Bypass", "-File", tmp],
                          capture_output=True, timeout=60)
            if r.returncode == 0 and link.is_file():
                return link.name
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    except (OSError, ValueError):
        pass
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="产品根目录（真实路径）")
    parser.add_argument("--home", required=True, help="插件 home（templates/ 所在目录）")
    parser.add_argument("--stage", choices=STAGE_ORDER, default=None)
    parser.add_argument("--desktop-shortcut", action="store_true",
                        help="明确要求时才在 Windows 桌面创建或刷新快捷方式")
    args = parser.parse_args()
    root = Path(args.root).expanduser().resolve()
    home = Path(args.home).expanduser().resolve()
    if not root.is_dir():
        print(json.dumps({"status": "fail", "errors": ["产品根不存在：" + str(root)]}, ensure_ascii=False))
        return 2
    from s2_roadmap import project_state
    state = project_state(load_yaml(root / "workflow_state.yaml"))
    roadmap = resolve_roadmap(home,root,state)
    if not roadmap.get("stages"):
        print(json.dumps({"status": "fail", "errors": ["缺少 templates/stage_roadmap.yaml 阶段表"]}, ensure_ascii=False))
        return 2
    from s2_roadmap import project_state
    state = project_state(load_yaml(root / "workflow_state.yaml"))
    current = state.get("current_stage") if state.get("current_stage") in STAGE_ORDER else None
    active = args.stage or current or "S1"
    steps, total, _name = stage_steps(roadmap, active)
    cursor, path, _done = state_cursor(state, active, total)
    nav_dir = root / "00_导航"
    nav_dir.mkdir(parents=True, exist_ok=True)
    readme = nav_dir / "README.md"
    if not readme.is_file():
        readme.write_text(
            "# 00_导航\n\n`导航.html` 由星空导航自动生成（路线图 + 当前进度），离线可打开，30 秒自动刷新。\n`打开导航悬浮窗.bat`（Windows）/ `.command`（Mac）双击即起挂着看的悬浮窗，路径已填好本产品根，不用拼命令。\n默认不创建桌面快捷方式；需要时手动运行 `建桌面快捷方式-StarNav.ps1`，或渲染时明确传入 `--desktop-shortcut`。\n不要手改本页；以聊天里的步骤卡与阶段报告为准。\n",
            encoding="utf-8",
        )
    out = nav_dir / "导航.html"
    write_if_changed(out, build_html(home, root, active))
    # Both desktop and project entries resolve source/unlock from the current record.
    py = sys.executable
    console_python = Path(py).with_name("python.exe")
    if sys.platform == "win32" and console_python.is_file():
        py = str(console_python)
    state = install_state_path(home)
    launch_script = nav_dir / "启动导航.py"
    write_if_changed(launch_script, project_launcher(state, home, py))
    bat = nav_dir / "打开导航悬浮窗.bat"
    write_if_changed(bat, (
        '@echo off\r\nchcp 65001 >nul\r\n'
        '"' + py.replace('%', '%%') + '" -B "%~dp0启动导航.py"\r\n'
        'set "NAV_EXIT=%errorlevel%"\r\nif not "%NAV_EXIT%"=="0" pause\r\n'
        'exit /b %NAV_EXIT%\r\n').encode('utf-8'))
    cmd = nav_dir / "打开导航悬浮窗.command"
    write_if_changed(cmd,
        '#!/bin/bash\n'
        'PY=' + shlex.quote(py) + '\n'
        '[ -x "$PY" ] || PY="$(command -v python3)"\n'
        '"$PY" -B "$(dirname "$0")/启动导航.py"\n'
        'NAV_EXIT=$?\nif [ "$NAV_EXIT" -ne 0 ]; then read -r -p "按回车关闭窗口"; exit "$NAV_EXIT"; fi\n'
        'sleep 1; osascript -e \'tell application "Terminal" to close (every window whose name contains "打开导航悬浮窗")\' >/dev/null 2>&1 &\n',
    )
    try:
        os.chmod(cmd, 0o755)
    except OSError:
        pass
    # 桌面快捷方式：手动一键版（StarNav 短名 + 绿指南针图标）。
    # 必须 utf-8-sig（带 BOM），否则 Win PowerShell 5.1 按 GBK 读，中文路径变乱码，快捷方式双击无反应。
    # 快捷方式直接指本产品根的启动 bat，路径自带，不会随版本缓存过期。
    shortcut_ps1 = nav_dir / "建桌面快捷方式-StarNav.ps1"
    write_if_changed(shortcut_ps1,
        "\ufeff$ErrorActionPreference = 'Stop'\n"
        "$w = New-Object -ComObject WScript.Shell\n"
        "$s = $w.CreateShortcut([IO.Path]::Combine([Environment]::GetFolderPath('Desktop'), 'StarNav.lnk'))\n"
        "$s.TargetPath = '" + str(bat).replace("'", "''") + "'\n"
        "$s.WorkingDirectory = '" + str(root).replace("'", "''") + "'\n"
        "$s.Hotkey = 'CTRL+ALT+X'\n"
        "$s.IconLocation = '" + str(home / "assets" / "starnav.ico").replace("'", "''") + ",0'\n"
        "$s.Description = '星空导航 StarNav · " + str(root.name).replace("'", "''") + "'\n"
        "$s.Save()\n"
        "Write-Output 'StarNav 快捷方式已建好（桌面，Ctrl+Alt+X）'\n",
    )
    print(json.dumps({
        "status": "ok",
        "html": out.relative_to(root).as_posix(),
        "launcher_bat": (nav_dir / "打开导航悬浮窗.bat").relative_to(root).as_posix(),
        "launcher_command": (nav_dir / "打开导航悬浮窗.command").relative_to(root).as_posix(),
        "shortcut_ps1": shortcut_ps1.relative_to(root).as_posix(),
        "desktop_shortcut": _ensure_desktop_shortcut(bat, root, home) if args.desktop_shortcut else None,
        "stage": active,
        "cursor": cursor,
        "total": total,
        "path": path,
        "steps": len(steps),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
