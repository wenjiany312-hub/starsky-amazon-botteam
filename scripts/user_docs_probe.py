"""Check delivered user steps and registered commands against the current route."""
from pathlib import Path
import json
import yaml


def verify(home):
    home = Path(home)
    errors = []
    registry = home / 'templates/user_commands.json'
    if not registry.is_file():
        return {'ok': True, 'errors': [], 'status': 'legacy_without_r04_docs'}
    try:
        body = (home / 'docs/使用说明.md').read_text(encoding='utf8')
        stages = yaml.safe_load((home / 'templates/stage_roadmap.yaml').read_bytes())['stages']
        for stage in ('S1', 'S2', 'S3', 'S4'):
            steps = stages[stage]['steps']
            heading = next((line for line in body.splitlines() if line.startswith('## ' + stage + ' ')), '')
            if not heading.endswith(' · ' + str(len(steps)) + ' 步'):
                errors.append('使用说明步数不一致：' + stage)
            for step in steps:
                if f"| {step['n']} | {step['name']} |" not in body:
                    errors.append('使用说明步骤名称不一致：' + stage + '.' + str(step['n']))
        prompts = {c['prompt'] for group in json.loads(registry.read_bytes())['groups'] for c in group['cards']}
        for command in ('填 SKU', '继续 S4', '导入类目打法'):
            if command not in prompts or command not in body:
                errors.append('使用说明口令未注册：' + command)
    except (OSError, ValueError, KeyError, TypeError) as error:
        errors.append('使用说明检查失败：' + str(error))
    return {'ok': not errors, 'errors': errors}
