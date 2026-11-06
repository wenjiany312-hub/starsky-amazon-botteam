"""Read-only acceptance display shared by cards, Tk navigation and local HTML.

Unknown counts are None, never zero. Delivery progress cannot grant acceptance.
"""
from pathlib import Path
import json
import re

ACCEPTED_STATUSES = frozenset({'accepted', 'accepted_with_real_data', 'accepted_with_deferred'})


_S2_GATE_CACHE = {}


def _s2_gate_key(root, state, home):
    """Fingerprint of everything the S2 gate reads; None disables caching."""
    import hashlib, os
    try:
        base = Path(root).resolve()
        parts = [str(base), json.dumps({'s2': (state or {}).get('s2_execution'),
                 'acc': ((state or {}).get('stage_acceptance') or {}).get('S2')}, ensure_ascii=False, sort_keys=True, default=str)]
        for folder in (base / 'S2_文案板块', base / '00_总控', Path(home) / 'scripts', Path(home) / 'templates'):
            for dirpath, _, names in os.walk(folder):
                for name in names:
                    st = os.stat(os.path.join(dirpath, name))
                    parts.append(f'{dirpath}/{name}:{st.st_mtime_ns}:{st.st_size}')
        for name in ('workflow_state.yaml',):
            st = (base / name).stat(); parts.append(f'{name}:{st.st_mtime_ns}:{st.st_size}')
        return hashlib.sha256('\n'.join(sorted(parts)).encode()).hexdigest()
    except (OSError, TypeError, ValueError):
        return None


def review_fingerprint(state,stage):
    import hashlib
    flow=state.get(stage.lower()+'_execution') or {};node=dict((state.get('stage_acceptance') or {}).get(stage) or {});node.pop('user_review',None)
    return hashlib.sha256(json.dumps({'acceptance':node,'records':flow.get('records'),'validation':flow.get('validation')},ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def record_review(root,state,stage,decision):
    if stage not in {'S1','S2','S3','S4'}:raise ValueError('本轮不改S5验收')
    if decision.get('source')!='user' or not str(decision.get('message') or '').strip() or decision.get('outcome') not in {'pass','pass_with_deferred','not_reviewed'}:raise ValueError('需要本人选择通过 / 带后补通过 / 先不验收')
    flow=state.get(stage.lower()+'_execution') or {}
    if flow.get('delivery_status')!='complete':raise ValueError('先交付本阶段报告，再记录阶段验收选择')
    summary=acceptance_summary(root,state,stage)
    if decision['outcome']!='not_reviewed' and not summary['accepted']:raise ValueError('硬门槛未通过，不能用阶段验收选择覆盖：'+ '；'.join(summary['issues']))
    if decision['outcome']=='pass_with_deferred' and summary['status']!='accepted_with_deferred':raise ValueError('没有符合路线图且有明确回执的待补项，不能伪造带后补通过')
    if decision['outcome']=='pass' and summary['status']=='accepted_with_deferred':raise ValueError('存在已授权待补项，请选择带后补通过，不隐藏待补')
    node=state.setdefault('stage_acceptance',{}).setdefault(stage,{})
    proof={**decision,'snapshot_sha256':review_fingerprint(state,stage)}
    old=node.get('user_review') or {}
    if all(old.get(k)==v for k,v in proof.items()):return old
    from datetime import datetime,timezone
    proof['recorded_at']=datetime.now(timezone.utc).isoformat();node['user_review']=proof;return proof


def _issues(value):
    if not isinstance(value, list):
        return []
    return [str(item.get('message') or item.get('label') or item.get('reason') or '')
            if isinstance(item, dict) else str(item) for item in value if item]


# 十个结构化判据的中文名（与 s1_criteria.KEYS 同步，tests/test_stage_acceptance_wording.py 校验）。
CRITERION_LABELS = {
    'strongest_counterargument': '最强反方观点', 'pass_hold_stop': '通过 / 暂缓 / 终止结论',
    'allowable_landed_cost': '可承受落地成本', 'coverage_downgrade': '覆盖不足降级说明',
    'unknown_attributes': '未知属性清单', 'normalized_unit_price': '标准化单价', 'cohort_comparison': '同类对比',
    'listing_age': '上架时长', 'monthly_trend': '月度趋势判读', 'latest_order_date': '最晚下单日'}
CRITERION_PROBLEMS = {
    'missing structured criterion': '还没写入结构化判读', 'invalid status': '状态值无效',
    'missing source': '缺来源', 'missing value': '缺数值', 'missing reason': '缺原因说明'}


def _friendly(issue):
    """按失败原因的文字选措辞，不按键名一刀切：
    “缺结构化判读”不是“数据没拉”，把它写成“还没拉”会把人引去重采已有的数据。"""
    text = re.sub(r'^\s*\[C\d+\]\s*', '', issue).strip()
    labels = {'reviews': '竞品评论还没拉', 'monthly_trend': '月度趋势还没拉',
              'category_comparison': '类目对比待补', 'variations': '变体数据待补',
              'peak_top100': '旺季榜单待补', 'organic_top5': '自然前五待核对',
              'ad_keyword_count': '广告关键词待补'}
    if '缺单项分' in text:
        return '方向筛选的 5 维评分缺失'
    criterion = re.match(r'([a-z_]+)\s*[:：]\s*(.*)$', text)
    # “键：已拉/应拉” 是数据覆盖（C3），不是判读字段；monthly_trend 两种都有，所以按后面的文字分。
    if criterion and criterion.group(1) in CRITERION_LABELS and not re.match(r'\d+\s*/\s*\d+', criterion.group(2)):
        rest = criterion.group(2).strip()
        for problem, wording in CRITERION_PROBLEMS.items():
            if rest.startswith(problem):
                return CRITERION_LABELS[criterion.group(1)] + wording
        return CRITERION_LABELS[criterion.group(1)] + '：' + rest  # 其他原因保留原文，只把英文键换成中文名
    for key, label in labels.items():
        if re.match(re.escape(key) + r'\s*[:：]', text):
            ratio = re.search(r'\d+\s*/\s*\d+', text)
            return label + (f'（{ratio.group()}）' if ratio else '')
    return text


def acceptance_summary(root, state, stage):
    """Return {accepted, status, count, issues}; read logs only within root.

    Explicit stage issues/count take precedence, then validator JSON errors or
    historical S1 FAIL text. Warnings do not inflate the blocking failure count.
    """
    state = state or {}
    if stage == 'S2':
        from s2_roadmap import project_state
        state = project_state(state)
    node = (state.get('stage_acceptance') or {}).get(stage) or {}
    validation = ((state.get(stage.lower() + '_execution') or {}).get('validation') or {})
    status = str(node.get('status') or 'pending')
    issues = _issues(node.get('issues') or node.get('errors'))
    count = node.get('count', node.get('blocking_count'))
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        count = None
    if not issues:
        issues = _issues(validation.get('errors'))
    text = validation.get('output') if isinstance(validation.get('output'), str) else ''
    if not issues and not text and validation.get('log'):
        try:
            base = Path(root).resolve()
            path = (base / str(validation['log'])).resolve()
            path.relative_to(base)
            text = path.read_text(encoding='utf-8-sig')
        except (OSError, ValueError, TypeError):
            pass
    if not issues and text:
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, dict):
            issues = _issues(data.get('errors'))
        else:
            issues = re.findall(r'^\s*✗\s*(.+)$', text, flags=re.M)
            match = re.search(r'FAIL\s*[（(](\d+)[）)]', text)
            if match and count is None:
                count = int(match.group(1))
            if not issues and validation.get('exit_code') == 2:
                issues = re.findall(r'^\s*\?\s*(.+)$', text, flags=re.M)
    blocked = state.get('blocked_reason') if state.get('current_stage') in (None, stage) else None
    # S2's blocked_reason is a presentation summary of its enumerated issues.
    if blocked and str(blocked) not in issues and not (stage == 'S2' and issues):
        issues.append(str(blocked))
    if issues and not (stage == 'S2' and 'remediations' in node):
        count = max(count or 0, len(issues))
    accepted = (status in ACCEPTED_STATUSES and validation.get('exit_code') in (None, 0)
                and not count and not issues)
    if stage == 'S2' and (accepted or (state.get('s2_execution') or {}).get('delivery_status') in {'complete','awaiting_card'}):
        import importlib.util
        home=Path(__file__).resolve().parents[1]
        spec=importlib.util.spec_from_file_location('_acceptance_h_gate',home/'scripts/s2_compliance_gate.py')
        gate=importlib.util.module_from_spec(spec);spec.loader.exec_module(gate)
        # The S2 gate takes seconds and the navigator asks several times per refresh;
        # reuse the last result while the state and the S2/control files are unchanged.
        key=_s2_gate_key(root,state,home)
        cached=_S2_GATE_CACHE.get(key) if key else None
        if cached is not None:
            current=cached
        else:
            current=gate.check_stage(root,state,home=home)
            if current['ok']:
                from s2_roadmap import current_review
                review=current_review(root,state)
                if not review['ok']:current=gate.review_result(review['errors'],checked=current)
            if key:
                _S2_GATE_CACHE.clear(); _S2_GATE_CACHE[key]=current
        if not current['ok'] and current.get('status') == 'review_pending' and not current.get('blocked_count'):
            # S2-R14 / P2：合规与字段都过、只差人工审阅时，卡片写“可进入人工审阅”，不写“按修改清单改 N 处”
            return {'accepted': True, 'status': 'review_pending', 'count': 0, 'issues': [], 'review_pending': True,
                    'label': '可进入人工审阅', 'review_todos': current.get('errors') or []}
        if not current['ok']:
            accepted=False; node=gate.presentation(current);status=node['status'];issues=node['errors'];count=node['count']
        elif not accepted and (state.get('s2_execution') or {}).get('delivery_status') == 'complete':
            return {'accepted': True, 'status': 'review_pending', 'count': 0, 'issues': [], 'review_pending': True,
                    'label': '可进入人工审阅', 'review_todos': current.get('review_todos') or []}
    if accepted and status == 'accepted_with_deferred':
        import stage_deferred
        current = stage_deferred.inspect(Path(__file__).resolve().parents[1], state, stage)
        if current['errors'] or not current['items']:
            accepted = False
            issues += current['errors'] or ['后补验收缺少有效回执记录']
            count = len(issues)
    if accepted:
        reviewed=node.get('user_review') or {};current=reviewed.get('snapshot_sha256')==review_fingerprint(state,stage)
        pending=stage in {'S1','S2','S3','S4'} and current and reviewed.get('outcome')=='not_reviewed'
        return {'accepted': True, 'status': status, 'count': 0, 'issues': [],'review_pending':pending,**({'label':'先不验收'} if pending else {})}
    friendly = [_friendly(item) for item in issues]
    priority = lambda item: (4 if any(label in item for label in CRITERION_LABELS.values()) else 0 if '竞品评论' in item
                             else 1 if '月度趋势' in item else 2 if '5 维评分' in item else 3)
    friendly = sorted(dict.fromkeys(friendly), key=priority)
    extras={key:node[key] for key in ('remediations','blocked_count','label','action','next') if key in node}
    return {**extras,'accepted': False, 'status': status, 'count': count, 'issues': friendly}


def acceptance_label(summary):
    """Compact human status; pending approval is different from a failed gate."""
    count = summary.get('count')
    if summary.get('label'):return summary['label']
    if summary.get('status') == 'review_pending':return '待人工审阅'
    if summary.get('status') == 'data_gap':return '验收数据待核验'
    if summary.get('status') == 'needs_changes':
        return f'需修改 {count} 处' if count is not None else '需修改（待核验）'
    return f'验收未通过（{count} 项）' if count else '验收待完成'
