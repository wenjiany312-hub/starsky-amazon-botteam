"""Read-only multi-market collector for the local S5 operations console."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
import time
import uuid
from zoneinfo import ZoneInfo


HERE = Path(__file__).resolve()
SKILLS = HERE.parents[2]


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_ADS_DIR = SKILLS / "amazon-ads-api-connector" / "scripts"
if str(_ADS_DIR) not in sys.path:
    sys.path.insert(0, str(_ADS_DIR))
_ADS_CLIENT = _module("_ops_ads_client", _ADS_DIR / "ads_api_client.py")
_ADS_REPORTS = _module("_ops_ads_reports", _ADS_DIR / "ads_api_reports.py")
_SP = _module("_ops_sp_client", SKILLS / "amazon-sp-api-connector" / "scripts" / "sp_api_client.py")

AdsClient = _ADS_CLIENT.AdsClient
load_ads_config = _ADS_CLIENT.load_config
ads_collect = _ADS_REPORTS.collect
write_json = _ADS_REPORTS.write_json
sha = _ADS_REPORTS.sha
encoded = _ADS_REPORTS.encoded
SpApiClient = _SP.SpApiClient
SpApiError = _SP.SpApiError
load_sp_config = _SP.load_config


def _config(value):
    if isinstance(value, dict):
        cfg = dict(value)
    else:
        path = Path(value).expanduser().resolve()
        raw = path.read_text(encoding="utf-8-sig")
        cfg = json.loads(raw) if raw.lstrip().startswith("{") else __import__("yaml").safe_load(raw)
    if not isinstance(cfg, dict) or not isinstance(cfg.get("markets"), list):
        raise ValueError("console_config_invalid")
    root = Path(cfg.get("workspace", "")).expanduser()
    if not root.is_absolute() or not cfg.get("store"):
        raise ValueError("console_config_invalid")
    for market in cfg["markets"]:
        expected_zone = {"US": "America/Los_Angeles", "UK": "Europe/London"}
        if market.get("market") not in expected_zone or market.get("timezone") != expected_zone.get(market.get("market")):
            raise ValueError("console_config_invalid")
        for key in ("ads_config", "sp_config"):
            if not Path(market.get(key, "")).is_absolute():
                raise ValueError("console_config_invalid")
        if market.get("cost_file") and not Path(market["cost_file"]).is_absolute():
            raise ValueError("console_config_invalid")
    cfg["workspace"] = str(root.resolve())
    return cfg


def _safe_error(exc):
    return {"code": getattr(exc, "code", "local_processing_failed"), "status": getattr(exc, "status", None)}


def _sp_scope(context):
    return {key: context.get(key) for key in ("store", "market", "marketplace_id", "seller_id")}


def _status_record(run_dir, domain):
    """Read current status independently of whether its raw response is usable."""
    base = run_dir / "sp" / domain
    status = _read(base / "status.json", None)
    if status is None:
        base = run_dir / "sp"
        root_status = _read(base / "status.json", {}) or {}
        domains = root_status.get("domains") or {}
        status = domains.get(domain) or (domains.get("today") if domain == "today_intraday" else None)
        if isinstance(status, dict):
            status = {**status, "checkedAt": status.get("checkedAt") or root_status.get("collected_at")}
    if not isinstance(status, dict):
        return {}, base
    status = dict(status)
    # Initial flat artifacts can inherit an independently recorded run binding.
    # Without such a binding they remain readable, but cannot be reused for a new account.
    if "scope" not in status:
        bound = _read(run_dir / "source_context.json", {}) or {}
        if bound:
            status["scope"] = _sp_scope(bound)
    return status, base


def _domain_status(run_dir, domain):
    return _status_record(run_dir, domain)[0]


def _verified_domain_info(run_dir, base, info, expected_scope=None):
    if not isinstance(info, dict) or (expected_scope is not None and info.get("scope") != expected_scope):
        return None
    name, digest = info.get("file"), info.get("sha256")
    if not isinstance(name, str) or Path(name).is_absolute() or not isinstance(digest, str):
        return None
    path = (base / name).resolve()
    if not path.is_relative_to((run_dir / "sp").resolve()) or not path.is_file():
        return None
    try:
        data = path.read_bytes()
        if sha(data) != digest:
            return None
        value = json.loads(data.decode("utf-8-sig"))
        if not isinstance(value, dict):
            return None
        return {"value": value, "path": path, "status": info, "stale": False}
    except (OSError, ValueError):
        return None


def _domain_view(run_dir, domain, expected_scope=None, allow_stale=True):
    status, base = _status_record(run_dir, domain)
    if status.get("state") in ("complete", "data_gap"):
        current = _verified_domain_info(run_dir, base, status, expected_scope)
        if current is not None:
            return current
    if allow_stale:
        previous = status.get("last_healthy")
        if isinstance(previous, dict) and previous.get("state") == "complete":
            view = _verified_domain_info(run_dir, base, previous, expected_scope)
            if view is not None:
                view["stale"] = True
                return view
    return None


def _immutable_raw(folder, value):
    digest = sha(encoded(value))
    name = "raw." + digest + ".json"
    path = folder / name
    if not path.exists():
        write_json(path, value)
    if sha(path.read_bytes()) != digest:
        raise ValueError("immutable_raw_integrity_failed")
    return name, digest


def _save(run_dir, domain, value, state="complete", error=None, period=None, scope=None):
    folder = run_dir / "sp" / domain
    previous = _domain_view(run_dir, domain, expected_scope=scope)
    if previous is not None and previous["status"].get("state") != "complete":
        old_status, old_base = _status_record(run_dir, domain)
        healthy = old_status.get("last_healthy")
        previous = (_verified_domain_info(run_dir, old_base, healthy, scope)
                    if isinstance(healthy, dict) and healthy.get("state") == "complete" else None)
    name, digest = _immutable_raw(folder, value)
    status = {"state": state, "checkedAt": datetime.now(timezone.utc).isoformat(),
              "file": name, "sha256": digest, "error": error, "period": period}
    if scope is not None:
        status["scope"] = dict(scope)
    if state != "complete" and previous is not None and previous["status"].get("state") == "complete":
        # Copy legacy mutable files to a content-addressed file before referencing
        # them as last healthy; the original file and old evidence remain intact.
        old_name, old_digest = _immutable_raw(folder, previous["value"])
        status["last_healthy"] = {key: previous["status"].get(key) for key in
                                  ("state", "checkedAt", "period", "scope")}
        status["last_healthy"].update(file=old_name, sha256=old_digest)
        status["stale"] = True
    write_json(folder / "status.json", status)
    return status


def _checkpoint(run_dir, domain, expected_scope=None):
    """Only current, complete, hash-verified responses may skip a fresh request."""
    view = _domain_view(run_dir, domain, expected_scope=expected_scope, allow_stale=False)
    if view is None or view["status"].get("state") != "complete" or view["value"].get("complete") is False:
        return None
    return view["status"]


def _participates(payload, marketplace):
    for row in payload.get("payload", []):
        mp = row.get("marketplace") or {}
        if str(mp.get("id")) == str(marketplace):
            return True
    return False


def _sku_sales(run_dir, sp, listings, start, today, zone):
    folder = run_dir / "sp" / "sku_sales"; manifest_path = folder / "manifest.json"
    manifest = _read(manifest_path, {}) or {}
    expected_scope = {"marketplace_id":sp.config.get("marketplace_id"), "seller_id":sp.config.get("seller_id")}
    if manifest.get("scope") != expected_scope and (manifest.get("scope") is not None or manifest.get("files")):
        return {"state":"error","error":{"code":"sku_sales_scope_mismatch","status":None}}
    period={"start":start.isoformat(),"end":today.isoformat()}
    # Cached bytes are reusable only for the same verified request interval.
    if manifest.get("period") != period:
        manifest["files"]={}; manifest["errors"]={}
    manifest.update({"market":run_dir.name.rsplit("_",1)[-1], "period":{"start":start.isoformat(),"end":today.isoformat()},
                     "scope":expected_scope, "files":manifest.get("files") or {}, "errors":manifest.get("errors") or {}})
    skus = sorted({r.get("sku") for r in listings.get("rows", []) if isinstance(r.get("sku"), str) and r.get("sku")})
    made_request = False
    for sku in skus:
        info = manifest["files"].get(sku) or {}; path = folder / str(info.get("file", ""))
        if path.is_file() and sha(path.read_bytes()) == info.get("sha256"): continue
        if made_request: time.sleep(2.05)
        try:
            result = sp.sales(start.isoformat(), today.isoformat(), zone, sku=sku)
            name = sha(sku.encode("utf8"))[:16] + ".json"; path = folder / name
            write_json(path, {"sku":sku,"result":result})
            manifest["files"][sku] = {"file":name,"sha256":sha(path.read_bytes())}; manifest["errors"].pop(sku, None)
        except Exception as exc:
            manifest["errors"][sku] = _safe_error(exc)
        made_request = True; manifest["collected_at"] = datetime.now(timezone.utc).isoformat(); write_json(manifest_path, manifest)
    state = "complete" if len(manifest["files"]) == len(skus) and not manifest["errors"] else "data_gap"
    manifest["state"]=state
    write_json(manifest_path, manifest)
    return {"state":state,"checkedAt":manifest.get("collected_at"),"period":manifest["period"],"error":None if state=="complete" else {"code":"sku_sales_partial","status":None}}


def _ads_product_map(run_dir, client, profile_id, scope):
    folder = run_dir / "ads_product_map"; manifest_path = folder / "manifest.json"
    ads_manifest = _read(run_dir / "ads" / "manifest.json", {}) or {}
    report = _verified(run_dir / "ads", ads_manifest, "campaign_adgroups")
    report_info = (ads_manifest.get("files") or {}).get("campaign_adgroups") or {}
    if not isinstance(report, list): return {"state":"pending","error":None}
    campaign_ids = sorted({str(r.get("campaignId")) for r in report if r.get("campaignId") is not None})
    report_sha = report_info.get("sha256")
    old = _read(manifest_path, {}) or {}; expected = {"profile_id":str(profile_id),"market":scope}
    info = old.get("file") or {}; raw = folder / str(info.get("path", ""))
    if (old.get("scope") == expected and old.get("mapping_campaign_scope") == campaign_ids
            and old.get("source_report_sha256") == report_sha and raw.is_file() and sha(raw.read_bytes()) == info.get("sha256")):
        return {"state":old.get("state","complete"),"checkedAt":old.get("collected_at"),"error":old.get("error")}
    existing = _verified(run_dir / "ads", ads_manifest, "product_ads")
    if isinstance(existing, list):
        rows=[{k:r.get(k) for k in ("adId","campaignId","adGroupId","asin","sku")} for r in existing if str(r.get("campaignId")) in set(campaign_ids)]
        raw=folder/"product_ads.json"; write_json(raw,rows)
        manifest={"state":"complete","scope":expected,"mapping_campaign_scope":campaign_ids,"source_report_sha256":report_sha,
                  "collected_at":datetime.now(timezone.utc).isoformat(),"file":{"path":"product_ads.json","sha256":sha(raw.read_bytes())},"source":"verified_ads_manifest"}
        write_json(manifest_path,manifest); return {"state":"complete","checkedAt":manifest["collected_at"]}
    rows=[]; truncated=False; errors=[]
    try:
        for offset in range(0,len(campaign_ids),100):
            batch=campaign_ids[offset:offset+100]; body={"maxResults":100,"campaignIdFilter":{"include":batch}}; seen=set()
            for _ in range(100):
                response=client.request("POST","/sp/productAds/list",body,profile_id=profile_id)
                page=response.get("productAds") if isinstance(response,dict) else None
                if not isinstance(page,list): raise ValueError()
                rows.extend({k:r.get(k) for k in ("adId","campaignId","adGroupId","asin","sku")} for r in page)
                token=response.get("nextToken")
                if not token: break
                if token in seen: raise ValueError()
                seen.add(token); body={"maxResults":100,"campaignIdFilter":{"include":batch},"nextToken":token}
            else: truncated=True; errors.append({"code":"page_limit","batchStart":offset})
        raw=folder/"product_ads.json"; write_json(raw,rows)
        state="data_gap" if truncated else "complete"
        manifest={"state":state,"scope":expected,"mapping_campaign_scope":campaign_ids,"source_report_sha256":report_sha,
                  "collected_at":datetime.now(timezone.utc).isoformat(),"file":{"path":"product_ads.json","sha256":sha(raw.read_bytes())},
                  "error":{"code":"page_limit","detail":"mapping incomplete; do not infer unique SKU"} if truncated else None,"errors":errors}
        write_json(manifest_path,manifest); return {"state":state,"checkedAt":manifest["collected_at"],"error":manifest["error"]}
    except Exception as exc:
        return {"state":"error","error":_safe_error(exc)}


def run(config, as_of=None):
    cfg = _config(config)
    instant = as_of or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    summary = {"store": cfg["store"], "markets": {}, "generatedAt": instant.astimezone(timezone.utc).isoformat()}
    for item in cfg["markets"]:
        market, zone = item["market"], ZoneInfo(item["timezone"])
        local_now = instant.astimezone(zone)
        today = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = today - timedelta(days=8)
        end_day = today - timedelta(days=1)
        run_dir = Path(cfg["workspace"]) / "runs" / (end_day.strftime("%Y%m%d") + "_" + market)
        states = {}; acfg = {}; scfg = {}; sp_scope = None
        try:
            acfg = load_ads_config(item["ads_config"])
            ads_client = AdsClient(acfg)
            result = ads_collect(ads_client, {"profile_id": acfg.get("profile_id"),
                "start_date": start.date().isoformat(), "end_date": end_day.date().isoformat(),
                "attribution_days": 7, "campaign_ids": None, "all_campaigns": True}, run_dir / "ads", 0)
            states["ads"] = {"state": result.get("state", "error"), "period": {"startDate": start.date().isoformat(), "endDate": end_day.date().isoformat()}}
            states["ads_product_map"] = _ads_product_map(run_dir, ads_client, acfg.get("profile_id"), market)
        except Exception as exc:
            states["ads"] = {"state": "error", "error": _safe_error(exc)}
        try:
            scfg = load_sp_config(item["sp_config"]); sp = SpApiClient(scfg)
            sp_scope = _sp_scope({**scfg, "store":cfg["store"], "market":market})
            prior_scope = _read(run_dir / "source_context.json", {}) or {}
            sp_scope_changed = bool(prior_scope and (prior_scope.get("store") != cfg["store"] or
                prior_scope.get("marketplace_id") != scfg.get("marketplace_id") or prior_scope.get("seller_id") != scfg.get("seller_id")))
            checkpoint = None if cfg.get("force_refresh") is True or sp_scope_changed else _checkpoint(run_dir, "participations", expected_scope=sp_scope)
            if checkpoint is not None:
                view = _domain_view(run_dir, "participations", expected_scope=sp_scope, allow_stale=False)
                if not _participates(view["value"], sp.config.get("marketplace_id")): checkpoint = None
            if checkpoint is not None:
                states["participations"] = checkpoint
            else:
                part = sp.participations()
                if not _participates(part, sp.config.get("marketplace_id")):
                    raise SpApiError("marketplace_not_participating")
                states["participations"] = _save(run_dir, "participations", part, scope=sp_scope)
        except Exception as exc:
            states["participations"] = _save(run_dir, "participations", {}, "error", _safe_error(exc), scope=sp_scope)
            summary["markets"][market] = states
            continue
        calls = {
            "sales": lambda: sp.sales(start.isoformat(), today.isoformat(), item["timezone"]),
            "today_intraday": lambda: sp.sales(today.isoformat(), local_now.isoformat(), item["timezone"]),
            "inventory": sp.inventory,
            "listings": sp.listings,
        }
        listing_value = None
        for domain, call in calls.items():
            checkpoint = None if cfg.get("force_refresh") is True or sp_scope_changed or domain == "today_intraday" else _checkpoint(run_dir, domain, expected_scope=sp_scope)
            if checkpoint is not None:
                states[domain] = checkpoint
                if domain == "listings":
                    listing_value = _domain_view(run_dir, "listings", expected_scope=sp_scope, allow_stale=False)["value"]
                continue
            try:
                value = call()
                if domain == "listings": listing_value = value
                partial = isinstance(value, dict) and value.get("complete") is False
                state = "data_gap" if partial else "complete"
                error = value.get("error") if partial else None
                period = ({"start":start.isoformat(),"end":today.isoformat()} if domain == "sales" else
                          {"start":today.isoformat(),"end":local_now.isoformat()} if domain == "today_intraday" else "current snapshot")
                states[domain] = _save(run_dir, domain, value, state, error, period, scope=sp_scope)
            except Exception as exc:
                states[domain] = _save(run_dir, domain, {}, "error", _safe_error(exc), scope=sp_scope)
        if item.get("cost_file") and listing_value is not None:
            states["sku_sales"] = _sku_sales(run_dir, sp, listing_value, start, today, item["timezone"])
        write_json(run_dir / "source_context.json", {"store":cfg["store"],"market":market,"timezone":item["timezone"],
            "period":{"startDate":start.date().isoformat(),"endDate":end_day.date().isoformat()},
            "ads_profile_id":acfg.get("profile_id"),"marketplace_id":scfg.get("marketplace_id"),"seller_id":scfg.get("seller_id")})
        summary["markets"][market] = states
    write_json(Path(cfg["workspace"]) / "collection_status.json", summary)
    build_snapshot(cfg)
    return summary


def _read(path, default=None):
    try: return json.loads(path.read_text(encoding="utf-8"))
    except Exception: return default


def _query(label, provider, rows, files, start=None, end=None, definitions=None):
    return {"source": {"label": label, "provider": provider,
        "coverage": {"startDate": start, "endDate": end}, "files": files,
        "metricDefinitions": definitions or [], "evidenceFlow": [
            {"title":"原始响应","detail":"本地只读采集"},
            {"title":"完整性","detail":"manifest SHA-256 校验"},
            {"title":"看板","detail":"按原生 ID 和日期聚合"}]}, "rows": rows}


def _verified(base, manifest, key):
    info = (manifest.get("files") or {}).get(key)
    if not isinstance(info, dict) or not isinstance(info.get("path"), str): return None
    path = (base / info["path"]).resolve()
    if not path.is_relative_to(base.resolve()) or not path.is_file() or sha(path.read_bytes()) != info.get("sha256"): return None
    return _read(path, None)


def _sum(rows, key):
    try:
        values = [float(r[key]) for r in rows if r.get(key) is not None]
        return sum(values) if len(values) == len(rows) and rows else None
    except (TypeError, ValueError): return None


def _period_text(status):
    value = status.get("period") or status.get("params")
    if not isinstance(value,dict): return str(value) if value else None
    start=value.get("startDate") or value.get("start") or value.get("start_date")
    end=value.get("endDate") or value.get("end") or value.get("end_date")
    return f"{start} — {end}" if start or end else None


def build_snapshot(config):
    cfg = _config(config); root = Path(cfg["workspace"]); old = _read(root / "reviewed_snapshot.json", {}) or {}
    ad_daily, campaigns, ad_detail, sales, products, connections = [], [], [], [], [], []
    rejected_markets = set()
    evidence = {k:[] for k in ("ad_daily","campaigns","ad_detail","sales_daily","products")}
    for item in cfg["markets"]:
        market = item["market"]
        runs = sorted((root / "runs").glob("*_" + market), reverse=True)
        if not runs: continue
        run_dir = runs[0]; ads_dir = run_dir / "ads"
        try:
            current_ads = load_ads_config(item["ads_config"]); current_sp = load_sp_config(item["sp_config"])
        except Exception:
            rejected_markets.add(market)
            connections.append({"market":market,"source":"scope","state":"error","checkedAt":None,"period":None,"error":{"code":"config_invalid","status":None}})
            continue
        bound = _read(run_dir / "source_context.json", {}) or {}
        if (bound.get("store") != cfg["store"] or bound.get("market") != market
                or str(bound.get("ads_profile_id")) != str(current_ads.get("profile_id"))
                or bound.get("marketplace_id") != current_sp.get("marketplace_id")
                or bound.get("seller_id") != current_sp.get("seller_id")):
            rejected_markets.add(market)
            connections.append({"market":market,"source":"scope","state":"scope_mismatch","checkedAt":None,"period":None,"error":{"code":"source_context_mismatch","status":None}})
            continue
        manifest = _read(ads_dir / "manifest.json", {}) or {}; ctx = manifest.get("source_context") or {}
        expected_country = "US" if market == "US" else "UK"
        actual_country = "UK" if ctx.get("country") == "GB" else ctx.get("country")
        raw = _verified(ads_dir, manifest, "campaign_adgroups") if actual_country == expected_country else None
        entities = _verified(ads_dir, manifest, "entities") if actual_country == expected_country else None
        for query_key, artifact in (("ad_daily","campaign_adgroups"),("campaigns","campaign_adgroups"),("ad_detail","keywords"),("ad_detail","targets"),("ad_detail","search_terms")):
            info=(manifest.get("files") or {}).get(artifact)
            if isinstance(info,dict) and _verified(ads_dir,manifest,artifact) is not None:
                evidence[query_key].append({"path":str((ads_dir/str(info["path"])).relative_to(root)),"sha256":info.get("sha256"),"period":f"{ctx.get('start_date')}..{ctx.get('end_date')}"})
        names = {str(r.get("campaignId")): r.get("name") for r in (entities or {}).get("campaigns", [])}
        daily, totals = {}, {}
        for row in raw if isinstance(raw, list) else []:
            key = (str(row.get("campaignId")), row.get("date")); daily.setdefault(key, []).append(row)
        for (cid, day), rows in daily.items():
            values = {"spend":_sum(rows,"cost"), "adSales":_sum(rows,ctx.get("sales_field","sales7d")),
                "adOrders":_sum(rows,ctx.get("orders_field","purchases7d")), "clicks":_sum(rows,"clicks"), "impressions":_sum(rows,"impressions")}
            ad_daily.append({"market":market,"currency":ctx.get("currency"),"date":day,"timezone":ctx.get("timezone"),**values})
            totals.setdefault(cid, []).extend(rows)
        for cid, rows in totals.items():
            campaigns.append({"market":market,"currency":ctx.get("currency"),"timezone":ctx.get("timezone"),
                "campaignId":cid,"campaignName":names.get(cid),"spend":_sum(rows,"cost"),
                "adSales":_sum(rows,ctx.get("sales_field","sales7d")),"adOrders":_sum(rows,ctx.get("orders_field","purchases7d")),
                "clicks":_sum(rows,"clicks"),"impressions":_sum(rows,"impressions")})
        entity_sets = entities or {}; keyword_names={str(r.get("keywordId")):r.get("keywordText") for r in entity_sets.get("keywords",[])}
        target_names={str(r.get("targetId")):r.get("expression") for r in entity_sets.get("targets",[])}
        for artifact, layer in (("keywords","keyword"),("targets","target"),("search_terms","search_term")):
            detail_raw = _verified(ads_dir, manifest, artifact) if actual_country == expected_country else None
            for row in detail_raw if isinstance(detail_raw,list) else []:
                cid=str(row.get("campaignId")); native=str(row.get("keywordId")) if row.get("keywordId") is not None else None
                ad_detail.append({"market":market,"layer":layer,"campaignId":cid,"campaignName":names.get(cid),
                    "adGroupId":str(row.get("adGroupId")) if row.get("adGroupId") is not None else None,
                    "nativeId":native,"name":row.get("searchTerm") or keyword_names.get(native) or target_names.get(native),
                    "date":row.get("date"),"startDate":row.get("startDate"),"endDate":row.get("endDate"),
                    "spend":_sum([row],"cost"),"adSales":_sum([row],ctx.get("sales_field","sales7d")),
                    "adOrders":_sum([row],ctx.get("orders_field","purchases7d")),"clicks":_sum([row],"clicks"),"impressions":_sum([row],"impressions")})
        expected_sp_scope = _sp_scope(bound)
        domain_views = {name: _domain_view(run_dir, name, expected_scope=expected_sp_scope)
                        for name in ("sales", "today_intraday", "inventory", "listings")}
        def domain(name):
            view = domain_views.get(name)
            return view["value"] if view is not None else {}
        def provenance(name):
            view = domain_views.get(name)
            if view is None:
                return {"complete":False, "stale":True, "dataCollectedAt":None}
            status = view["status"]
            return {"complete":not view["stale"] and status.get("state") == "complete"
                                and view["value"].get("complete") is not False,
                    "stale":view["stale"], "dataCollectedAt":status.get("checkedAt"),
                    "sourceCheckedAt":_domain_status(run_dir,name).get("checkedAt")}
        for query_key,name in (("sales_daily","sales"),("sales_daily","today_intraday"),("products","inventory"),("products","listings")):
            view=domain_views.get(name)
            if view is not None:
                evidence[query_key].append({"path":str(view["path"].relative_to(root)),
                    "sha256":view["status"]["sha256"],"period":_period_text(view["status"]),
                    "collectedAt":view["status"].get("checkedAt"),"stale":view["stale"]})
        for intraday, name in ((False, "sales"), (True, "today_intraday")):
            for row in domain(name).get("payload", []):
                money = row.get("totalSales") or {}
                interval = str(row.get("interval", "")); day = interval[:10] or None
                sales.append({"market": market, "currency": money.get("currencyCode"), "date": day,
                    "sales": float(money["amount"]) if money.get("amount") is not None else None,
                    "orders": row.get("orderCount"), "units": row.get("unitCount"), "isIntraday": intraday, **provenance(name)})
        inv = {str(r.get("asin"))+"\0"+str(r.get("sellerSku")): r for r in domain("inventory").get("rows", [])}
        for row in domain("listings").get("rows", []):
            marketplace = "ATVPDKIKX0DER" if market == "US" else "A1F83G8C2ARO7P"
            matches = [s for s in (row.get("summaries") or []) if s.get("marketplaceId") == marketplace]
            summary = matches[0] if matches else {}; asin = summary.get("asin"); key = str(asin)+"\0"+str(row.get("sku")); stock = inv.get(key, {})
            listing_provenance, inventory_provenance = provenance("listings"), provenance("inventory")
            products.append({**listing_provenance,
                "complete":listing_provenance["complete"] and inventory_provenance["complete"],
                "stale":listing_provenance["stale"] or inventory_provenance["stale"],
                "inventoryCollectedAt":inventory_provenance.get("dataCollectedAt"),
                "market": market, "asin": asin, "sku": row.get("sku"), "title": summary.get("itemName"),
                "available": (stock.get("inventoryDetails") or {}).get("fulfillableQuantity"), "totalInventory": stock.get("totalQuantity"),
                "listingStatus": summary.get("status"), "issuesCount": len(row["issues"]) if isinstance(row.get("issues"), list) else None})
        connection_sources = ["ads", "ads_product_map", "participations", "sales", "today_intraday", "inventory", "listings"]
        if item.get("cost_file"): connection_sources.append("sku_sales")
        for source in connection_sources:
            if source == "ads":
                status = _read(ads_dir / "manifest.json", {}) or {}
                if (status.get("files") or {}).get("campaign_adgroups") and _verified(ads_dir, status, "campaign_adgroups") is None:
                    status = {**status, "state":"integrity_error", "last_error":{"code":"artifact_integrity_failed","status":None}}
            elif source == "ads_product_map":
                status = _read(run_dir / "ads_product_map" / "manifest.json", {}) or {}
            elif source == "sku_sales":
                status = _read(run_dir / "sp" / "sku_sales" / "manifest.json", {}) or {}
                if status:
                    status = {**status,"state":"data_gap" if status.get("errors") else "complete"}
            else:
                status = _domain_status(run_dir, source)
                view = _domain_view(run_dir, source, expected_scope=expected_sp_scope)
                if status.get("state") in ("complete", "data_gap") and (view is None or view["stale"]):
                    status = {**status,"state":"integrity_error","error":{"code":"artifact_integrity_failed","status":None}}
                if view is not None and view["stale"]:
                    status = {**status,"stale":True,"lastHealthyAt":view["status"].get("checkedAt")}
            connections.append({"market": market, "source": source, "state": status.get("state", "missing"),
                "checkedAt": status.get("updated_at") or status.get("checkedAt") or status.get("collected_at"), "period": _period_text(status), "error": status.get("error") or status.get("last_error"),
                "stale":status.get("stale",False),"lastHealthyAt":status.get("lastHealthyAt")})
    daily_totals = {}
    for row in ad_daily:
        key = (row.get("market"),row.get("currency"),row.get("date"),row.get("timezone"))
        daily_totals.setdefault(key, []).append(row)
    ad_daily = [{"market":k[0],"currency":k[1],"date":k[2],"timezone":k[3],
        **{field:_sum(rows,field) for field in ("spend","adSales","adOrders","clicks","impressions")}}
        for k,rows in daily_totals.items()]
    queries = {
        "ad_daily": _query("广告日指标", "Amazon Ads API", ad_daily, evidence["ad_daily"]),
        "campaigns": _query("广告活动", "Amazon Ads API", campaigns, evidence["campaigns"]),
        "ad_detail": _query("广告下钻", "Amazon Ads API", ad_detail, evidence["ad_detail"], definitions=[{"label":"连接规则","definition":"仅按 campaignId/adGroupId/nativeId 精确连接；不向 ASIN 分摊"}]),
        "sales_daily": _query("店铺销售", "Amazon SP-API", sales, evidence["sales_daily"], definitions=[{"label":"销售额","definition":"SP-API totalSales；完整日与盘中分开"}]),
        "products": _query("商品", "Amazon SP-API", products, evidence["products"], definitions=[{"label":"可售库存","definition":"FBA inventoryDetails.fulfillableQuantity；缺失为 null"}]),
        "connections": _query("连接状态", "local collector", connections, []),
        "listing_checks": _query("Listing 检查", "not established", [], []),
        "actions": _query("待审核动作", "approval ledger", [], []),
        "comparisons": _query("对比", "not established", [], []),
    }
    states = {(r.get("market"),r.get("source")):r.get("state") for r in connections}
    healthy = lambda market, source: states.get((market,source)) in ("complete","data_gap")
    prior_queries = old.get("queries") or {}
    rules = {"ad_daily":lambda r:not healthy(r.get("market"),"ads"),
             "campaigns":lambda r:not healthy(r.get("market"),"ads"),
             "ad_detail":lambda r:not healthy(r.get("market"),"ads"),
             "products":lambda r:not (healthy(r.get("market"),"inventory") and healthy(r.get("market"),"listings"))}
    # Completed and intraday sales have separate refresh states.
    rules["sales_daily"] = lambda r: not healthy(r.get("market"), "today_intraday" if r.get("isIntraday") else "sales")
    for key, keep in rules.items():
        prior = prior_queries.get(key)
        if isinstance(prior,dict) and isinstance(prior.get("rows"),list):
            current = queries[key]["rows"]
            # Capture current dimensions before appending fallback, retaining all
            # rows of a missing domain rather than only its first previous row.
            current_dimensions = {((n.get("market"), n.get("isIntraday")) if key == "sales_daily" else (n.get("market"),)) for n in current}
            for row in prior["rows"]:
                if row.get("market") in rejected_markets: continue
                dimension = (row.get("market"), row.get("isIntraday")) if key == "sales_daily" else (row.get("market"),)
                has_new = dimension in current_dimensions
                if keep(row) and not has_new:
                    current.append({**row,"stale":True,"complete":False,
                        "staleCollectedAt":row.get("dataCollectedAt") or row.get("staleCollectedAt") or old.get("generatedAt")})
    for key in ("listing_checks","actions","comparisons"):
        if isinstance(prior_queries.get(key),dict): queries[key] = prior_queries[key]
    snapshot = {"id": old.get("id") or "dashboard:" + str(uuid.uuid4()), "surface": "dashboard",
        "title": cfg["store"] + " 运营看板", "generatedAt": datetime.now(timezone.utc).isoformat(),
        "status": "observed" if connections and all(r["state"] in ("complete","data_gap","pending") for r in connections) else "partial", "buildStatus": "complete",
        "filters": [
            {"id":"market","label":"站点","field":"market","defaultValue":"US"},
            {"id":"date","label":"数据日期","field":"date",
             "queryIds":["sales_daily","ad_daily","sku_sales_daily","profit_daily"]}],
        "queries": queries}
    write_json(root / "reviewed_snapshot.json", snapshot)
    return snapshot
