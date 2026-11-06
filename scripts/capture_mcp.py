"""本步 MCP 回包自动入档（R06.1）。

以前每查一次数据源，助手都要把回包手抄进临时文件，再调 save_mcp_response.py；知识库回包也要整段抄进
save_kb_evidence 的输入。3.0.23 自动实跑第 1 步，光抄回包就花了一分半，抄长了还可能抄错。

现在本步调用完数据源和知识库后，只运行一次：

    python scripts/capture_mcp.py --root "<产品根>" [--stage S1] [--step N]

它从当前对话自己的原生记录里，按控制器保存的本轮固定时间窗口读取 MCP 调用和完整回包：
- 数据源回包原样存进 `S1_选品板块/amazon-market-research/运行数据/mcp_calls__<工具>.json`（同 save_mcp_response.py）；
- 按 `kb_plan.py --step N` 的顺序生成知识库登记草稿（回包已填好），助手只填每项的 card_ids 和 usage，
  再 `save_kb_evidence.py --calls <草稿>`。

只读对话记录、不改对话；读不到本对话记录时如实报错，不猜。

两种调用方式都认（R10）：
- 直接 MCP 调用（宿主原样记录调用和回包）→ 证据级别 host_native；
- Codex code mode：MCP 在 `exec` 脚本里 `await tools.mcp__服务__工具(参数)`，回包是脚本自己 text() 打印的，不是宿主原样回包。
  只有脚本完整符合受支持模板且每个 text() 打印完整调用结果（`text(JSON.stringify(r))`、
  Promise.allSettled 结果逐项打印）时才入档；仅打印 structuredContent/content 的投影会缺失错误等元数据，不能完整入档，证据级别 code_mode_passthrough，并记下脚本哈希、调用号、打印内容哈希；
  脚本只打印摘要/拼接字段（如 `{kb: r.structuredContent.kb}`）、用循环或 map 发起调用的，回包不入档，
  报「回包无法核验」——不是「调用失败」，也不是「没调用」。
退出码：0 全部入档；2 有问题（见 problems，每条写明是哪一类）；1 读记录失败。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
HOME = HERE.parent
sys.path.insert(0, str(HERE))
import kb_plan  # noqa: E402
import kb_refresh  # noqa: E402
import sorftime_filter_audit  # noqa: E402
import save_mcp_response  # noqa: E402
import step_evidence_window as evidence_windows  # noqa: E402
from verify_kb_evidence import response_failed, response_ids  # noqa: E402

try:
    import host_support as hosts
except ImportError:  # source tree: the controllers share the installer module
    _spec = importlib.util.spec_from_file_location('starsky_host_support', HOME.parents[1] / 'installer/host_support.py')
    hosts = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(hosts)

CHECKS = {'kb_status', 'kb_list_gaps'}
DATA_SERVERS = ('sorftime', 'sif', 'xydc', 'xiyou', 'sellersprite', 'seller_sprite', '1688')

# 每一类问题单独编码：用户看到的是真实原因，不是一句笼统的“调用失败”。
REASONS = {
    'unsupported_syntax': '脚本不完全符合可核验的调用与打印模板，不能自动确认回包归属',
    'incomplete_projection': '只打印了 structuredContent/content，缺少完整回包和错误元数据',
    'complex_script': '脚本用了循环 / map / 条件 / 函数等写法，分不清哪段打印对应哪次调用',
    'unrecognized_output': '脚本打印的不是调用结果本身（摘要或拼接了字段），不能当原样回包',
    'unparsable_script': '脚本写法读不懂',
    'result_modified': '脚本在打印前改写了调用结果',
    'output_count_mismatch': '脚本打印的段数与调用数对不上',
    'output_format_unknown': '宿主输出格式不认识',
    'no_output_emit': '脚本调用了但没有打印这次调用的结果',
    'args_not_static': '参数不是脚本里写明的固定值（用了运行时变量），无法登记参数',
    'truncated': '回包被宿主截断，不完整，不能入档',
    'not_json': '打印内容不是 JSON.stringify 的结果',
    'not_settled_entry': 'allSettled 结果打印格式不对',
    'script_failed': '脚本执行失败（异常或被中断）',
    'bad_tool_name': '工具名读不出',
    'no_result_yet': '脚本还没有产出结果',
}
SAFE_SCRIPT = ('const r = await tools.mcp__<服务>__<工具>({参数用写死的值}); text(JSON.stringify(r));   '
               '（并行：const r = await Promise.allSettled([tools.…(…), tools.…(…)]); for (const x of r) text(JSON.stringify(x));）')


def _kb_server(server):
    """仅归一已知插件知识库别名，不模糊合并不同数据服务。"""
    name = str(server)
    for library in ('selection', 'ads', 'promotion'):
        short = library + '_knowledge_base'
        if name in (short, 'plugin_starsky-amazon-agent_' + short, 'plugin:starsky-amazon-agent:' + short):
            return short
    return name


def _server_kind(server):
    name = str(server).lower()
    if 'knowledge_base' in name:
        return 'knowledge'
    return 'data' if any(key in name for key in DATA_SERVERS) else 'other'


def step_window(root, stage):
    """当前执行轮次的固定起点；缺失边界绝不退回无界扫描。"""
    window = evidence_windows.current_window(root, stage)
    return window['n'], window['since_ns']


def _parsed(text):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


def _iso(ns):
    return datetime.fromtimestamp((ns or 0) / 1e9, tz=timezone.utc).isoformat(timespec='seconds')


def _failed(call):
    """回包本身是报错：脚本里 rejected / isError / 回包带 error。"""
    return bool(call.get('reply_failed')) or response_failed(_parsed(call['response']))


def _evidence(call):
    row = {'level': call.get('evidence') or 'host_native', 'call_id': call.get('call_id'),
           'time': _iso(call.get('time_ns')), 'time_ns': call.get('time_ns')}
    for key in ('script_sha256', 'script_call_id', 'emit_form', 'printed_sha256', 'evidence_note', 'reply_complete', 'isError'):
        if key in call:
            row[key] = call[key]
    for key in ('raw_response', 'raw_output'):
        if key in call:
            value = call[key]
            row[key] = value
            text = value if isinstance(value, str) else save_mcp_response.canonical(value)
            row[key + '_sha256'] = hashlib.sha256(text.encode('utf-8')).hexdigest()
    return row


def kb_draft(root, stage, step, calls, *, home=HOME, unattributed=(), unsupported=()):
    """按本步知识库计划逐项配对已调用的回包；没调用的项写 error，交给助手补调或说明。

    error 的写法区分三种：本步没有调用 / 调用了但回包无法核验 / 调用返回了报错。"""
    plan = kb_plan.plan_step(root, home, stage, step)
    used, items, missing = set(), [], []
    for planned in plan['calls']:
        same = [i for i, c in enumerate(calls) if i not in used and _kb_server(c['server']) == _kb_server(planned['server']) and c['tool'] == planned['tool']]
        exact = [i for i in same if calls[i]['args'] == planned['args']]
        pick = (exact or [None])[-1]
        label = f"{planned['library']} {planned['tool']} {json.dumps(planned['args'], ensure_ascii=False)}"
        if pick is None:
            seen = [u for u in unattributed if _kb_server(u['server']) == _kb_server(planned['server']) and u['tool'] == planned['tool']
                    and (u.get('args') is None or u['args'] == planned['args'])]
            if seen and seen[-1]['reason'] == 'no_result_yet':
                items.append({'error': f"本步已发起 {planned['library']} {planned['tool']}，正在等待完整回包；完成后重新归档，不要重复调用。", 'capture_state': 'pending'})
                missing.append(f'{label}（已调用，正在等待回包）')
                continue
            if seen:
                why = REASONS.get(seen[-1]['reason'], seen[-1]['reason'])
                items.append({'error': f"本步调用了 {planned['library']} {planned['tool']}，但回包无法核验是这次调用的原样回包（{why}），未登记；"
                                       f"请用这种写法重调一次：{SAFE_SCRIPT}", 'capture_state': 'called_reply_unverifiable'})
                missing.append(f'{label}（已调用，回包无法核验：{why}）')
            elif unsupported:
                items.append({'error': f"本步存在无法识别的调用记录，不能确认 {planned['library']} {planned['tool']} 是否已调用；请先核对记录格式。", 'capture_state': 'record_unsupported'})
                missing.append(f'{label}（记录无法核验）')
                continue
            else:
                items.append({'error': f"本步没有调用 {planned['library']} {planned['tool']}"})
                missing.append(label)
            continue
        used.add(pick)
        call = calls[pick]
        response = _parsed(call['response'])
        stamp = {'ts': _iso(call['time_ns']), 'evidence': _evidence(call)}
        if _failed(call):
            items.append({'error': str(call['response']), **stamp})  # 工具自己返回了报错：原样记作 error，不冒充成功
            continue
        item = {'response': response, **stamp}
        if planned['tool'] in CHECKS:
            item['no_use_reason'] = '库状态 / 缺口检查，只确认库可用，不作为本步判断依据'
        else:
            item.update(card_ids=[], usage='', returned_cards=sorted(response_ids(response)))
        items.append(item)
    return plan, items, missing


def kb_version_check(knowledge, home=HOME):
    """本步里的原生 kb_status 回包读到的版本，是不是已安装版本。没有 kb_status 调用返回 None。"""
    rows = [c for c in knowledge if c['tool'] == 'kb_status' and not _failed(c)]
    if not rows:
        return None
    host = 'claude' if hosts.current_host() == 'claude-code' else 'codex'
    results = [kb_refresh.check_kb_status(c['response'], host=host) for c in rows]
    bad = next((r for r in results if r['state'] == 'stale'), None) or next((r for r in results if r['state'] == 'unknown'), None)
    best = bad or results[-1]
    return {'current': best.get('fully_current', best['state'] == 'current'),
            'data_state': best.get('data_state'), 'code_state': best.get('code_state'),
            'state': best['state'], 'served': best['served'], 'installed': best['installed'],
            'message': best['message'] + (' ' + ' '.join(best.get('recovery') or []) if best['state'] == 'stale' else '')}


def _problem(code, message, **extra):
    return {'code': code, 'message': message, **extra}


def capture(root, stage='S1', step=None, *, calls=None, unattributed=None, home=HOME, kb_out=None):
    root = Path(root).resolve()
    cursor, since = step_window(root, stage)
    step = step or cursor
    # 只归档当前步；历史修订走 kb_amend，避免拿当前窗口写进历史步骤。
    if step != cursor:
        raise ValueError(f'请求 {stage}.{step}，当前步骤是 {stage}.{cursor}；请去掉 --step 或指定当前步。历史证据请用 kb_amend.py 追加补记。')
    problems, scan = [], None
    if calls is None:
        scan = hosts.mcp_scan(root, since)
        if scan['error']:
            raise ValueError('读取本对话记录失败（这是读记录的问题，不是 MCP 调用失败）：' + scan['error'])
        if not scan['session']:
            raise ValueError('读不到本对话的原生记录，无法自动入档；请按 save_mcp_response.py 逐个保存')
        calls, unattributed = scan['calls'], scan['unattributed']
        pending = scan.get('pending') or []
        for row in pending:
            if _server_kind(row.get('server')) != 'other':
                problems.append(_problem('pending', '这次调用尚无完整回包；完成后重新归档，不要重复发起调用。', call_id=row.get('call_id')))
        unattributed = list(unattributed) + pending
        for row in scan['unsupported']:
            problems.append(_problem('format_unsupported', f"本步有 MCP 调用写法本工具读不懂（{REASONS.get(row['reason'], row['reason'])}），"
                                     f"没有入档；请改用这种写法：{SAFE_SCRIPT}", call_id=row['call_id']))
    # Also constrain injected scans; tests/in-process callers cannot bypass the window.
    window = {'since_ns': since}
    calls = [call for call in calls if evidence_windows.contains(window, call)]
    unattributed = [call for call in (unattributed or []) if evidence_windows.contains(window, call)]
    knowledge = [c for c in calls if _server_kind(c['server']) == 'knowledge']
    data = [c for c in calls if _server_kind(c['server']) == 'data']
    other = sorted({c['server'] for c in calls if _server_kind(c['server']) == 'other'})
    saved, failed_calls, notices = {}, [], []
    if stage == 'S1':
        for call in data:
            now = datetime.fromtimestamp((call['time_ns'] or 0) / 1e9, tz=timezone.utc) if call['time_ns'] else None
            try:
                result = save_mcp_response.save(root, f"{call['server']}__{call['tool']}", call['args'], call['response'],
                                                now=now, evidence=_evidence(call))
            except (OSError, ValueError) as error:
                problems.append(_problem('archive_failed', f"{call['server']} {call['tool']} 的回包存盘失败：{error}", call_id=call.get('call_id')))
                continue
            saved[result['path']] = saved.get(result['path'], 0) + 1
            if _failed(call):
                failed_calls.append(f"{call['server']} {call['tool']}")
            else:
                notices.extend(f"{call['tool']}：{text}" for text in sorftime_filter_audit.notices(call['tool'], call['args'], call['response']))
    for row in unattributed:
        if _server_kind(row['server']) == 'other' or row.get('reason') == 'no_result_yet':
            continue
        why = REASONS.get(row['reason'], row['reason'])
        problems.append(_problem('unattributed', f"已调用 {row['server']} {row['tool']}，但回包无法核验是这次调用的原样回包（{why}），未入档；"
                                 f"请用这种写法重调：{SAFE_SCRIPT}", server=row['server'], tool=row['tool'], reason=row['reason'], call_id=row.get('call_id')))
    for call in failed_calls:
        problems.append(_problem('call_failed', f'{call} 的调用返回了报错（已原样入档，需要重试或降级）'))
    plan, items, missing = kb_draft(root, stage, step, knowledge, home=home, unattributed=unattributed,
                                    unsupported=(scan or {}).get('unsupported', []))
    for text in missing:
        if '（已调用，' not in text and '（记录无法核验）' not in text:
            problems.append(_problem('not_called', f'本步计划里的这次调用，对话记录里没有看到（不是调用失败）：{text}'))
    for item in items:
        if item.get('error') and 'response' not in item and 'ts' in item:
            problems.append(_problem('call_failed', '知识库调用返回了报错（原样记作 error）：' + item['error'][:120]))
    kb_version = kb_version_check(knowledge, home)
    if kb_version and not kb_version['current']:
        problems.append(_problem('kb_stale' if kb_version['state'] == 'stale' else 'kb_version_unknown', kb_version['message']))
    out = None
    if items:
        out = Path(kb_out) if kb_out else Path(tempfile.mkdtemp(prefix=f'starsky_kb_{stage}_{step:02d}_')) / 'calls.json'
        out.write_text(json.dumps(items, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    todo = [f"第 {i + 1} 项 {c['library']} {c['tool']}：从 returned_cards 里挑本步用到的卡写进 card_ids，并写 usage；都不用就删掉 usage、写 no_use_reason"
            for i, (c, item) in enumerate(zip(plan['calls'], items)) if 'returned_cards' in item]
    levels = {}
    for call in calls:
        level = call.get('evidence') or 'host_native'
        levels[level] = levels.get(level, 0) + 1
    if not data and not knowledge and not problems:
        problems.append(_problem('not_called', '本步窗口内没有可归档的数据源或知识库调用，未归档任何业务回包。'))
    if stage != 'S1' and data:
        problems.append(_problem('archive_unsupported', f'当前工具尚不支持 {stage} 数据源回包落盘；知识库草稿可单独登记。'))
    return {'step': f'{stage}.{step}', 'status': 'problems' if problems else 'ok', 'problems': problems,
            'data_saved': saved, 'data_calls': len(data), 'kb_calls': len(knowledge),
            'unattributed_calls': len(unattributed), 'notices': notices, 'ignored_other_servers': other, 'evidence_levels': levels,
            'kb_version': kb_version,
            'kb_draft': str(out) if out else None, 'kb_missing': missing, 'kb_todo': todo,
            'next': (f'补完草稿后运行 save_kb_evidence.py --root "{root}" --stage {stage} --step {step} --calls "{out}"'
                     if out else '本步没有知识库计划')}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--stage', default='S1', choices=['S1', 'S2', 'S3', 'S4', 'S5'])
    parser.add_argument('--step', type=int, help='默认当前步')
    parser.add_argument('--kb-out', type=Path, help='知识库登记草稿写到哪（默认系统临时目录）')
    options = parser.parse_args(argv)
    try:
        result = capture(options.root, options.stage, options.step, kb_out=options.kb_out)
    except (OSError, ValueError, KeyError, yaml.YAMLError) as error:
        print(json.dumps({'ok': False, 'status': 'record_unreadable', 'error': str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps({'ok': not result['problems'], **result}, ensure_ascii=False, indent=2))
    return 2 if result['problems'] else 0


if __name__ == '__main__':
    sys.exit(main())
