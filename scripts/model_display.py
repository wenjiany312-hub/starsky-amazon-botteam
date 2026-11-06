"""Read-only compact official model recommendations.

Recommendations describe task roles; they never identify or change the active
conversation model. Execution policies stay in routing and are not display text.
"""


import re


class ModelDisplay:
    def __init__(self, routing):
        self.routing = routing or {}

    def stable_main(self):
        return self.routing.get('execution_policy', {}).get('stable_main_model') is True

    def policy_note(self):
        return ''

    def helper_note(self):
        return ''

    def _compact(self, recommendation):
        text = str(recommendation or '—')
        names = self.routing.get('display_names') or {}
        match = re.search(r'(?:GPT-)?(\d+(?:\.\d+)?\s+(?:Sol|Luna|Astra|Terra))|(?:Claude\s+)?((?:Opus|Sonnet|Haiku)\s+\d+(?:\.\d+)?)', text)
        if not match:
            return text
        alias = match.group(1) or match.group(2)
        name = names.get(alias) or (('GPT-' if match.group(1) else 'Claude ') + alias)
        effort = re.search(r'·\s*(极高|极低|最高|高|中|低|Ultra|Max|XHigh)', text[match.end():], re.I)
        return name + ' · ' + (effort.group(1) if effort else self.routing.get('display_effort_default', '中'))

    def task(self, recommendation, legacy_note=''):
        return self._compact(recommendation)

    def next(self, recommendation, within_stage=False):
        return self._compact(recommendation)

    def recommendation(self, step, strict=False):
        current = self.routing
        ref = step['control']['model_ref'] if strict else (step.get('control') or {}).get('model_ref') or []
        for key in ref:
            if strict:
                current = current[key]
            elif not isinstance(current, dict) or key not in current:
                return self._compact(step.get('model') or '—')
            else:
                current = current[key]
        return self._compact(current if ref and (strict or isinstance(current, str)) else step.get('model') or '—')

    def first_step(self, stage):
        return self._compact(self.routing.get('by_step', {}).get(stage, {}).get('1', {}).get(
            'recommended', self.routing.get('by_stage', {}).get(stage, {}).get('window_default', '—')))

    def following(self, roadmap, stage, number, blocked=False):
        steps = roadmap.get('stages', {}).get(stage, {}).get('steps', []) or []
        following = next((step for step in steps if step.get('n') == number + 1), None)
        if following:
            return self.recommendation(following)
        target = 'S5' if stage == 'S5' else 'S' + str(int(stage[1:]) + 1)
        return self.first_step(target)

    def guidance(self, stage, number):
        item = self.routing.get('by_step', {}).get(stage, {}).get(str(number), {})
        return '；'.join(str(value) for value in [item.get('reason'), item.get('escalate_when')] if value)
def navigation_label(value):
    """Navigation-only exception: stored and conversational official names stay untouched."""
    import re
    return re.sub(r'\b(?:GPT-|Claude\s+|Anthropic\s+)','',str(value))

