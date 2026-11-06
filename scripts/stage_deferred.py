"""Shared, fail-closed deferral policy for all stage handoffs."""
from pathlib import Path


def decision_valid(decision):
    return (isinstance(decision, dict) and decision.get('source') == 'user'
            and str(decision.get('message') or '').strip().lower()
            not in {'', '继续', '好', 'ok', '下一步'})


def receipt_error(step, receipt):
    outcome = receipt.get('outcome')
    if outcome not in {'deferred', 'skipped'}:
        return None
    name = step['name']
    permitted = step.get('deferrable') is True if outcome == 'deferred' else step.get('skippable') is True
    if not permitted:
        return f'{name}不允许后补或跳过；这是必需验收项，请补齐并通过验收。'
    if not decision_valid(receipt.get('decision')):
        options = ['补齐']
        if step.get('deferrable'): options.append('明确回复后补')
        if step.get('skippable'): options.append('明确回复不做')
        return f'{name}：后补/跳过需要明确的用户选择回执；请选择'+' / '.join(options)+'。'
    return None


def inspect(home, state, stage):
    """Re-read current route; an old status alone cannot grant transfer."""
    from s1_roadmap import resolve_roadmap
    route = resolve_roadmap(home, state=state)['stages'][stage]
    steps = {s['control']['id']: s for s in route['steps']}
    flow = state.get(stage.lower()+'_execution') or {}
    pending, errors = [], []
    for record in flow.get('records') or []:
        if record.get('outcome') not in {'deferred', 'skipped'}:
            continue
        step = steps.get(record.get('step'))
        if not step:
            errors.append('后补步骤不在当前路线图中，请重新核验')
            continue
        error = receipt_error(step, record)
        if not record.get('receipt_id'):
            error = f'{step["name"]}缺少有效回执记录，请重新核验后补选择'
        if error:
            errors.append(error)
        elif record['outcome'] == 'deferred':
            pending.append({'step': record['step'], 'name': step['name'],
                            'receipt_id': record['receipt_id'],
                            'unavailable_capabilities': step.get('unavailable_capabilities') or [],
                            'impact': step.get('deferral_impact') or '下一阶段不得将此项标为已具备，补齐后独立验收。'})
    for identity, choice in (flow.get('optional_decisions') or {}).items():
        step = steps.get(identity)
        error = receipt_error(step, {**choice, 'decision': choice}) if step else '未知的可选步骤'
        if error: errors.append(error)
    return {'items': pending, 'errors': errors}


def downstream_errors(home, state, plan):
    """A deferred media asset must not silently become an available ad format."""
    current = inspect(home, state, 'S3')
    unavailable = {cap for item in current['items'] for cap in item['unavailable_capabilities']}
    errors = list(current['errors'])
    for campaign in plan.get('campaigns') or []:
        if ('sb_video' in unavailable and campaign.get('ad_product') == 'SB'
                and campaign.get('sb_format') in {'video', 'brand_video'}
                and campaign.get('status') not in {'deferred', 'disabled', 'not_ready'}):
            errors.append('S3 视频仍在待补：SB 视频广告不可标为可用，请补齐视频验收或保留为未就绪。')
    return errors
