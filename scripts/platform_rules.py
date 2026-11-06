"""Call-time registry: policy ceilings and editorial targets have separate provenance."""
from __future__ import annotations
import copy
from datetime import date
import hashlib
import json
from pathlib import Path
import yaml
import importlib.util
import sys

HOME = Path(__file__).resolve().parents[1]


def operation_runtime():
    name = '_starsky_s2_compliance_operation'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, HOME/'scripts/compliance_operation.py')
        runtime = importlib.util.module_from_spec(spec)
        sys.modules[name] = runtime
        spec.loader.exec_module(runtime)
    return sys.modules[name]


def load_rules(home=None):
    path = Path(home or HOME) / 'templates/amazon_platform_rules.yaml'
    data = yaml.safe_load(operation_runtime().read_bytes(path))
    if not isinstance(data, dict) or not isinstance(data.get('listing'), dict):
        raise ValueError('platform_rule_registry_invalid')
    for entry in data.get('rules', []):
        if not all(key in entry for key in ('id', 'authority', 'sites', 'categories', 'effective', 'source', 'verified', 'review_days', 'affected_stages')):
            raise ValueError('platform_rule_metadata_missing')
    return data


def listing_rules(overrides=None, *, home=None):
    result = copy.deepcopy(load_rules(home)['listing'])
    for field, values in (overrides or {}).items():
        if field not in result or not isinstance(values, dict): raise ValueError('Unknown listing rule: '+str(field))
        for key, value in values.items():
            if key not in result[field]: raise ValueError('Unknown listing rule property: '+str(key))
            if key == 'count' and value != result[field][key]:
                raise ValueError('Runtime overrides cannot change required exact count: '+field)
            if key in {'max','repeat'} and (type(value) is not int or value>result[field][key]):
                raise ValueError('Runtime overrides cannot relax registry ceilings: '+field)
            if key=='target' and (not isinstance(value,(list,tuple)) or len(value)!=2 or value[0]<result[field][key][0] or value[1]>result[field][key][1]):
                raise ValueError('Runtime overrides cannot relax registry targets: '+field)
            result[field][key] = copy.deepcopy(value)
    for field, values in result.items():
        for key, value in values.items():
            if key in {'max', 'count', 'repeat'} and (type(value) is not int or value <= 0): raise ValueError('Invalid positive listing limit: '+field)
            if key == 'target' and (not isinstance(value, (list, tuple)) or len(value)!=2 or any(type(x) is not int or x<0 for x in value) or value[0]>value[1]): raise ValueError('Invalid listing target: '+field)
            if key == 'brand_reserve' and (type(value) is not int or value < 0): raise ValueError('Invalid brand reserve: '+field)
    if not isinstance(result['description_html']['tags'], list) or not set(result['description_html']['tags']) <= {'p','br','b','ul','li'}:
        raise ValueError('HTML safety tags cannot be broadened')
    return result


def fingerprint(home=None):
    return hashlib.sha256(operation_runtime().read_bytes(Path(home or HOME)/'templates/amazon_platform_rules.yaml')).hexdigest()


def review_due(*, home=None, today=None):
    now = date.fromisoformat(str(today)) if today else date.today()
    return [{'id': r['id'], 'message': '规则超过复核周期，需核对来源', 'source': r['source']}
            for r in load_rules(home)['rules'] if r.get('verified') and
            (now-date.fromisoformat(r['verified'])).days > r['review_days']]


if __name__ == '__main__':
    print(json.dumps({'rules': load_rules(), 'review_due': review_due()}, ensure_ascii=False))
