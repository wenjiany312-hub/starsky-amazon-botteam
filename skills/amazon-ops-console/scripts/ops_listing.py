"""Version verified SP-API seller listing submissions for the operations console."""
from __future__ import annotations

from copy import deepcopy
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import sys

HERE=Path(__file__).resolve()
spec=importlib.util.spec_from_file_location("_ops_collect_listing",HERE.with_name("ops_collect.py"))
ops=importlib.util.module_from_spec(spec); sys.modules[spec.name]=ops; spec.loader.exec_module(ops)

FIELD_KEYS = {
    "title":"item_name",
    "bulletPoints":"bullet_point",
    "searchTerms":"generic_keyword",
    "longDescription":"product_description",
}
MARKETPLACES={"US":"ATVPDKIKX0DER","UK":"A1F83G8C2ARO7P"}


def _json_bytes(value):
    return (json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False)+"\n").encode("utf8")


def _write_bytes(path, data):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+"."+secrets.token_hex(5)+".tmp")
    try:
        temp.write_bytes(data); os.replace(temp,path)
    finally:
        temp.unlink(missing_ok=True)


def _attribute(attributes, key, marketplace_id=None, locale=None):
    if key not in attributes: return {"state":"not_returned","value":None}
    raw=attributes.get(key)
    values=[]
    for item in raw if isinstance(raw,list) else [raw]:
        if isinstance(item,dict):
            item_market=item.get("marketplace_id") or item.get("marketplaceId")
            language=item.get("language_tag") or item.get("languageTag")
            if item_market and marketplace_id and item_market != marketplace_id: continue
            if language and locale and language.replace("-","_") != locale.replace("-","_"): continue
        value=item.get("value") if isinstance(item,dict) and "value" in item else item
        if value not in (None,"",[],{}): values.append(value)
    if not values: return {"state":"empty","value":None}
    return {"state":"returned","value":values[0] if len(values)==1 else values}


def _diff(previous,current):
    changes=[]
    for field in tuple(FIELD_KEYS) + ("itemHighlights",):
        before=(previous.get("fields") or {}).get(field,{"state":"not_returned","value":None})
        after=current["fields"][field]
        if before != after: changes.append({"field":field,"before":before,"after":after})
    return changes


def _query(label, rows, files):
    return {"source":{"label":label,"provider":"Amazon SP-API Listings Items",
        "coverage":{"startDate":None,"endDate":None},"files":files,
        "metricDefinitions":[],"evidenceFlow":[{"title":"接口语义","detail":"seller_submission_snapshot；不是 buyer page live"},
        {"title":"完整性","detail":"Listings raw SHA-256 与账号范围校验"}]},"rows":rows}


def _highlight_property(schema):
    matches=[]
    for key,value in (schema.get("properties") or {}).items():
        if isinstance(value,dict) and str(value.get("title","")).strip().casefold() == "item highlights".casefold(): matches.append(key)
    return matches[0] if len(matches)==1 else None


def collect_listing_schemas(config):
    cfg=ops._config(config); root=Path(cfg["workspace"]); output={"markets":{}}
    for item in cfg["markets"]:
        market=item["market"]; runs=sorted((root/"runs").glob("*_"+market),reverse=True); rows=[]; errors=[]
        if not runs: output["markets"][market]={"state":"schema_unavailable","errors":[{"code":"listings_missing"}]}; continue
        sp_cfg=ops.load_sp_config(item["sp_config"]); seller=sp_cfg.get("seller_id"); run=runs[0]
        expected=ops._sp_scope({"store":cfg["store"],"market":market,"marketplace_id":sp_cfg.get("marketplace_id"),"seller_id":seller})
        view=ops._domain_view(run,"listings",expected_scope=expected,allow_stale=False)
        if not view: output["markets"][market]={"state":"schema_unavailable","errors":[{"code":"listings_unverified"}]}; continue
        product_types=sorted({s.get("productType") for r in view["value"].get("rows",[]) for s in r.get("summaries",[])
                              if s.get("marketplaceId")==sp_cfg.get("marketplace_id") and isinstance(s.get("productType"),str)})
        client=ops.SpApiClient(sp_cfg); locale=item.get("locale") or ("en_US" if market=="US" else "en_GB")
        folder=root/"listing_schemas"/market; mappings={}
        for product_type in product_types:
            try:
                result=client.definition(product_type,locale); schema=result["schema"]; digest=hashlib.sha256(result["schema_bytes"]).hexdigest()
                path=folder/"schemas"/(hashlib.sha256(product_type.encode()).hexdigest()[:16]+"."+digest+".json")
                if not path.exists(): _write_bytes(path,result["schema_bytes"])
                definition=result["definition"]; version=(definition.get("productTypeVersion") or {}).get("version")
                key=_highlight_property(schema)
                mappings[product_type]={"productType":product_type,"schemaVersion":version,"schemaSha256":digest,
                    "schemaFile":str(path.relative_to(root)),"fields":{"itemHighlights":key} if key else {},
                    "itemHighlightsState":"returned" if key else "schema_unavailable"}
                rows.append({"productType":product_type,"schemaVersion":version,"schemaSha256":digest,"itemHighlightsKey":key})
            except Exception as exc: errors.append({"productType":product_type,"code":getattr(exc,"code","schema_unavailable")})
        mapping={"market":market,"marketplaceId":sp_cfg.get("marketplace_id"),"locale":locale,"generatedAt":datetime.now(timezone.utc).isoformat(),"productTypes":mappings}
        ops.write_json(folder/"listing_field_schema.json",mapping)
        state="complete" if len(rows)==len(product_types) else "data_gap"
        ops.write_json(folder/"manifest.json",{"state":state,"market":market,"marketplaceId":sp_cfg.get("marketplace_id"),"locale":locale,"rows":rows,"errors":errors})
        output["markets"][market]={"state":state,"schemas":len(rows),"errors":errors,"mapping":str(folder/"listing_field_schema.json")}
    return output


def enrich_listings(config, snapshot):
    cfg=ops._config(config); result=deepcopy(snapshot); root=Path(cfg["workspace"])
    fields_rows=[]; versions=[]; changes=[]; files=[]
    product_rows=((result.get("queries") or {}).get("products") or {}).get("rows",[])
    for item in cfg["markets"]:
        market=item["market"]; runs=sorted((root/"runs").glob("*_"+market),reverse=True)
        if not runs: continue
        run=runs[0]; sp_cfg=ops.load_sp_config(item["sp_config"]); seller=sp_cfg.get("seller_id")
        schema_doc={}
        category_rows={}; category_source=None
        if item.get("category_listings_file"):
            category_path=Path(item["category_listings_file"])
            if category_path.is_absolute() and category_path.is_file():
                try:
                    with category_path.open("r",encoding="utf-8-sig",newline="") as stream:
                        reader=csv.DictReader(stream); headings={str(h).strip().casefold():h for h in (reader.fieldnames or [])}
                        sku_heading=headings.get("seller sku") or headings.get("sku"); highlight_heading=headings.get("item highlights")
                        if sku_heading and highlight_heading:
                            category_rows={str(r.get(sku_heading)):r.get(highlight_heading) for r in reader if r.get(sku_heading)}
                            category_source={"path":str(category_path),"sha256":hashlib.sha256(category_path.read_bytes()).hexdigest()}
                except Exception: category_rows={}; category_source=None
        if item.get("listing_field_schema"):
            schema_path=Path(item["listing_field_schema"])
            if schema_path.is_absolute(): schema_doc=ops._read(schema_path,{}) or {}
        if schema_doc.get("marketplaceId") != sp_cfg.get("marketplace_id") or schema_doc.get("market") != market:
            schema_doc={}
        bound=ops._read(run/"source_context.json",{}) or {}
        if (bound.get("store")!=cfg["store"] or bound.get("market")!=market or bound.get("marketplace_id")!=sp_cfg.get("marketplace_id")
                or bound.get("seller_id")!=seller): continue
        expected_scope=ops._sp_scope({"store":cfg["store"],"market":market,"marketplace_id":sp_cfg.get("marketplace_id"),"seller_id":seller})
        view=ops._domain_view(run,"listings",expected_scope=expected_scope,allow_stale=False)
        if not view: continue
        status=view["status"]; raw_path=view["path"]; payload=view["value"]; rel=str(raw_path.relative_to(root)); source_sha=status["sha256"]
        files.append({"path":rel,"sha256":source_sha,"period":str(status.get("period") or "seller submission snapshot")})
        for row in payload.get("rows",[]):
            summaries=[s for s in row.get("summaries",[]) if s.get("marketplaceId")==MARKETPLACES[market]]
            if len(summaries)!=1 or not seller: continue
            asin=summaries[0].get("asin"); sku=row.get("sku")
            if not isinstance(asin,str) or not isinstance(sku,str): continue
            attributes=row.get("attributes") or {}; product_type=summaries[0].get("productType")
            locale=item.get("locale")
            fields={name:_attribute(attributes,key,sp_cfg.get("marketplace_id"),locale) for name,key in FIELD_KEYS.items()}
            type_schema=(schema_doc.get("productTypes") or {}).get(product_type,{})
            schema_file=type_schema.get("schemaFile"); schema_sha=type_schema.get("schemaSha256")
            try:
                schema_path=(root/schema_file).resolve()
                valid_schema=(schema_path.is_relative_to(root.resolve()) and schema_path.is_file()
                              and hashlib.sha256(schema_path.read_bytes()).hexdigest()==schema_sha
                              and type_schema.get("productType")==product_type)
            except Exception: valid_schema=False
            if not valid_schema: type_schema={}
            highlight_key=(type_schema.get("fields") or {}).get("itemHighlights")
            fields["itemHighlights"] = (_attribute(attributes,highlight_key,sp_cfg.get("marketplace_id"),locale) if isinstance(highlight_key,str) and highlight_key
                else {"state":"not_returned","value":None,"reason":"schema_unavailable"})
            if fields["itemHighlights"]["state"] == "not_returned" and sku in category_rows:
                value=category_rows[sku]
                fields["itemHighlights"]={"state":"returned" if value else "empty","value":value or None,
                    "source":"category_listings_report","sourceSha256":category_source["sha256"]}
            doc={"schemaVersion":"1.0","artifactType":"seller_submission_snapshot","store":cfg["store"],"market":market,
                "marketplaceId":sp_cfg.get("marketplace_id"),"sellerId":seller,"asin":asin,"sku":sku,"productType":product_type,
                "fieldSchemaVersion":type_schema.get("schemaVersion"),"fields":fields,
                "source":{"path":rel,"sha256":source_sha,"capturedAt":status.get("checkedAt") or status.get("collected_at")}}
            content_hash=hashlib.sha256(_json_bytes({"identity":{k:doc[k] for k in ("store","market","marketplaceId","sellerId","asin","sku")},"fields":fields})).hexdigest()
            identity_hash=hashlib.sha256(_json_bytes({"store":cfg["store"],"market":market,"seller":seller,"asin":asin,"sku":sku})).hexdigest()[:24]
            folder=root/"listing_history"/identity_hash; index_path=folder/"index.json"
            folder.mkdir(parents=True,exist_ok=True)
            index=ops._read(index_path,{"identity":{"store":cfg["store"],"market":market,"sellerId":seller,"asin":asin,"sku":sku},"versions":[],"revisions":[]}) or {}
            if index.get("identity") != {"store":cfg["store"],"market":market,"sellerId":seller,"asin":asin,"sku":sku}: continue
            index.setdefault("versions",[]); index.setdefault("revisions",[]); prior=None
            if index["revisions"]:
                prior=ops._read(folder/index["revisions"][-1]["file"],{}) or {}
            version_file=content_hash[:32]+".json"; immutable=folder/version_file
            if not immutable.exists(): ops.write_json(immutable,{**doc,"version":content_hash})
            existing=next((v for v in index["versions"] if v.get("contentHash")==content_hash),None)
            observed=datetime.now(timezone.utc).isoformat()
            if existing: existing["lastSeenAt"]=observed; existing["observations"]=int(existing.get("observations",1))+1
            else: index["versions"].append({"version":len(index["versions"])+1,"contentHash":content_hash,"file":version_file,"firstSeenAt":observed,"lastSeenAt":observed,"observations":1})
            if not index["revisions"] or index["revisions"][-1]["contentHash"] != content_hash:
                index["revisions"].append({"revision":len(index["revisions"])+1,"contentHash":content_hash,"file":version_file,"observedAt":observed})
            revision=index["revisions"][-1]["revision"]
            ops.write_json(index_path,index); number=next(v["version"] for v in index["versions"] if v["contentHash"]==content_hash)
            identity={"market":market,"sellerId":seller,"asin":asin,"sku":sku}
            fields_rows.append({**identity,"version":revision,"contentVersion":number,"contentHash":content_hash,"fields":fields,"snapshotType":"seller_submission_snapshot"})
            versions.extend({**identity,**entry} for entry in index["revisions"])
            if prior and prior.get("version")!=content_hash:
                for change in _diff(prior,doc): changes.append({**identity,"fromVersion":prior.get("version"),"toVersion":content_hash,**change})
            for product in product_rows:
                if product.get("market")==market and product.get("asin")==asin and product.get("sku")==sku: product["version"]=revision; product["contentHash"]=content_hash
    queries=result.setdefault("queries",{})
    queries["listing_fields"]=_query("Listing 字段",fields_rows,files)
    queries["listing_versions"]=_query("Listing 版本",versions,files)
    queries["listing_changes"]=_query("Listing 变更",changes,files)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description="Collect read-only Product Type Definition schemas")
    parser.add_argument("--config",required=True,type=Path)
    args=parser.parse_args(argv)
    try:
        result=collect_listing_schemas(args.config)
        print(json.dumps(result,ensure_ascii=False,sort_keys=True))
        return 0 if all(v.get("state")=="complete" for v in result["markets"].values()) else 2
    except Exception as exc:
        print(json.dumps({"state":"error","error":{"code":getattr(exc,"code","local_processing_failed")}},ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
