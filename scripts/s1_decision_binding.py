"""Fingerprint the product configuration whose costs were explicitly reviewed."""
import argparse
import hashlib
import json
from pathlib import Path


def configuration_hash(data):
    subset = {key: data.get(key) for key in ('components', 'packaging', 'supplier_checks')}
    return hashlib.sha256(json.dumps(subset, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), default=str).encode('utf8')).hexdigest()


if __name__ == '__main__':
    import yaml
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    args = parser.parse_args()
    data = yaml.safe_load((Path(args.root) / 's1_decision_inputs.yaml').read_text(encoding='utf-8-sig'))
    print(json.dumps({'product_configuration_sha256': configuration_hash(data),
                      'instruction': '核对组件、包装、报价与成本输入一致后，再将此摘要填入成本输入；本命令不确认或修改成本。'}, ensure_ascii=False))
