#!/usr/bin/env python3
"""S2 第 6 步 · 后台必填属性（可选）：有来源就核对，没有就是上架前待办，不阻塞 S2。

三种来源，任选其一，结果都写成 s2_report 的 context.required_attributes：
  SP-API（旗舰版已接店铺）  --sp-config <sp_api.yaml> --search "dog bathrobe"      列出候选商品类型（只读）
                           --sp-config <sp_api.yaml> --product-type PET_APPAREL --out <json>
  已下载的商品类型 schema   --schema-file <schema.json> --product-type PET_APPAREL --out <json>
  都没有                   不运行本脚本；报告“上架前检查”列为待办，用户上架时按后台提示、类目模板或截图补。

只读：不创建、不修改任何商品；不猜商品类型，候选由用户确认。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

HOME = Path(__file__).resolve().parents[1]
# Offer, identifier and fulfilment fields are filled on listing day in Seller Central, not written by S2 copy.
LISTING_DAY = {'condition_type', 'merchant_suggested_asin', 'externally_assigned_product_identifier',
               'supplier_declared_has_product_identifier_exemption', 'fulfillment_availability', 'purchasable_offer',
               'list_price', 'merchant_shipping_group', 'skip_offer', 'merchant_release_date', 'main_offer_image_locator'}


def from_schema(schema, product_type, source, sha256=None, version=None):
    """Product type JSON schema -> context.required_attributes (content fields) plus listing-day fields."""
    required = sorted(str(name) for name in (schema.get('required') or []) if isinstance(name, str))
    return {'product_type': product_type, 'fields': [n for n in required if n not in LISTING_DAY],
            'listing_day_fields': [n for n in required if n in LISTING_DAY],
            'source': source, 'sha256': sha256, 'schema_version': version}


def _client(config):
    path = HOME / 'skills/amazon-sp-api-connector/scripts/sp_api_client.py'
    spec = importlib.util.spec_from_file_location('_starsky_s2_sp_api', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module, module.SpApiClient(module.load_config(config))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--sp-config', type=Path)
    parser.add_argument('--schema-file', type=Path)
    parser.add_argument('--search')
    parser.add_argument('--product-type')
    parser.add_argument('--locale', default='en_US')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args(argv)
    if args.search:
        if not args.sp_config:
            parser.error('--search 需要 --sp-config（旗舰版 SP-API）')
        _, client = _client(args.sp_config)
        found = client.search_product_types([w for w in args.search.split(',')], args.locale)
        print(json.dumps({'candidates': found, 'next': '请用户确认商品类型后再用 --product-type 读取必填属性'}, ensure_ascii=False, indent=2))
        return 0
    if not args.product_type or not args.out:
        parser.error('需要 --product-type 和 --out（或先用 --search 找候选商品类型）')
    if args.schema_file:
        raw = args.schema_file.read_bytes()
        document = from_schema(json.loads(raw), args.product_type, str(args.schema_file), hashlib.sha256(raw).hexdigest())
    elif args.sp_config:
        _, client = _client(args.sp_config)
        result = client.definition(args.product_type, args.locale)
        version = ((result['definition'].get('productTypeVersion') or {}).get('version'))
        document = from_schema(result['schema'], args.product_type, f'SP-API productTypeDefinitions {args.product_type} {args.locale}',
                               hashlib.sha256(result['schema_bytes']).hexdigest(), version)
    else:
        parser.error('需要 --sp-config 或 --schema-file；都没有时不用运行，报告会列为上架前待办')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'written': str(args.out), 'product_type': document['product_type'], 'fields': len(document['fields']),
                      'listing_day_fields': len(document['listing_day_fields']),
                      'next': '把这份 JSON 作为 s2_report 的 context.required_attributes，并设 context.product_type 为同一类型'}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
