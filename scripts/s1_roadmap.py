"""Resolve S1 steps without renumbering an existing product case."""
from copy import deepcopy
from pathlib import Path

import yaml

CURRENT = 'r01.m2'
LEGACY = 'legacy.m1'


def execution_snapshot(stage):
    return {'roadmap_version': stage.get('roadmap_version', CURRENT),
            'roadmap_steps': deepcopy(stage['steps'])}


def resolve_roadmap(home, root=None, state=None):
    home = Path(home)
    roadmap = yaml.safe_load((home / 'templates/stage_roadmap.yaml').read_text(encoding='utf-8-sig'))
    if state is None:
        path = Path(root) / 'workflow_state.yaml' if root is not None else None
        state = yaml.safe_load(path.read_text(encoding='utf-8-sig')) if path and path.is_file() else {}
    s3 = (state or {}).get('s3_execution')
    if s3 is not None and s3.get('roadmap_version') != 'r03.s3':
        if s3.get('roadmap_version') not in (None, 'legacy.s3.r02'):
            raise ValueError('unknown S3 roadmap version')
        roadmap['stages']['S3'] = deepcopy(yaml.safe_load(
            (home/'templates/stage_roadmap_s3_legacy.yaml').read_text(encoding='utf-8-sig'))['S3'])
        routing_path=home/'templates/model_routing.yaml'
        routing=yaml.safe_load(routing_path.read_text(encoding='utf-8-sig')) if routing_path.is_file() else {}
        legacy_roles=(routing or {}).get('legacy_by_step',{}).get('S3',{})
        for step in roadmap['stages']['S3']['steps']:
            if str(step['n']) in legacy_roles:
                step['control']['model_ref']=['legacy_by_step','S3',str(step['n']),'recommended']
    s4=(state or {}).get('s4_execution')
    if s4 is not None and s4.get('roadmap_version')!='r04.s4':
        if s4.get('roadmap_version') not in (None,'legacy.s4.r03'):raise ValueError('unknown S4 roadmap version')
        roadmap['stages']['S4']=deepcopy(yaml.safe_load((home/'templates/stage_roadmap_s4_legacy.yaml').read_bytes())['S4'])
        routing=yaml.safe_load((home/'templates/model_routing.yaml').read_bytes()) if (home/'templates/model_routing.yaml').is_file() else {}
        for step in roadmap['stages']['S4']['steps']:
            if str(step['n']) in (routing or {}).get('legacy_by_step',{}).get('S4',{}):step['control']['model_ref']=['legacy_by_step','S4',str(step['n']),'recommended']
    execution = (state or {}).get('s1_execution')
    if execution is None:
        return roadmap
    version = execution.get('roadmap_version', LEGACY)
    if version not in (CURRENT, LEGACY):
        raise ValueError('unknown S1 roadmap version: ' + str(version))
    policy = roadmap['stages']['S1']
    if version == LEGACY:
        policy = yaml.safe_load((home/'templates/stage_roadmap_s1_legacy.yaml').read_text(encoding='utf-8-sig'))['S1']
    if execution.get('roadmap_steps') is not None:
        steps = deepcopy(execution['roadmap_steps'])
        if (not isinstance(steps, list) or len(steps) != 9 or
                [s.get('n') for s in steps] != list(range(1, 10)) or
                len({s.get('control', {}).get('id') for s in steps}) != 9):
            raise ValueError('invalid S1 roadmap snapshot')
        roadmap['stages']['S1']['steps'] = steps
    elif version == LEGACY:
        legacy = yaml.safe_load((home / 'templates/stage_roadmap_s1_legacy.yaml').read_text(encoding='utf-8-sig'))
        roadmap['stages']['S1'] = deepcopy(legacy['S1'])
    else:
        raise ValueError('versioned S1 roadmap snapshot is missing')
    roadmap['stages']['S1']['roadmap_version'] = version
    # Keep historical numbering/content; shared safety policy comes from the
    # installed route for that version, never an untrusted old snapshot flag.
    policies = {s['control']['id']: s for s in policy['steps']}
    if version==CURRENT:
        for step in roadmap['stages']['S1']['steps']:
            current=policies.get(step['control']['id'],{})
            for key in ('nav_receipt','artifacts'):
                if key in current:step[key]=deepcopy(current[key])
    for step in roadmap['stages']['S1']['steps']:
        current = policies.get(step['control']['id'], {})
        for key in ('deferrable', 'skippable', 'deferral_impact', 'unavailable_capabilities'):
            if key in current: step[key] = deepcopy(current[key])
            else: step.pop(key, None)
    return roadmap
