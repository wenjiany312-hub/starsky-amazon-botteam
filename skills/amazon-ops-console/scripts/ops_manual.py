"""Local-only user-entered Item Highlights with revision-safe history."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import time

HERE=Path(__file__).resolve()
spec=importlib.util.spec_from_file_location("_ops_collect_manual",HERE.with_name("ops_collect.py"))
ops=importlib.util.module_from_spec(spec); sys.modules[spec.name]=ops; spec.loader.exec_module(ops)


class ManualFieldError(Exception):
    def __init__(self,code): self.code=code; super().__init__(code)


def _database(cfg):
    path=Path(cfg["workspace"])/"manual_listing_fields.sqlite3"; path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=10,isolation_level=None)
    # Changing journal mode on a brand-new database can return SQLITE_BUSY even
    # with connect(timeout=...). Retry this initialization, never the user write.
    deadline=time.monotonic()+10
    while True:
        try:
            mode=db.execute("PRAGMA journal_mode").fetchone()[0]
            if mode.lower()!='wal':db.execute("PRAGMA journal_mode=WAL")
            break
        except sqlite3.OperationalError as error:
            if 'locked' not in str(error).lower() and 'busy' not in str(error).lower():
                db.close();raise
            if time.monotonic()>=deadline:
                db.close();raise ManualFieldError('local_store_busy') from None
            time.sleep(.05)
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS current_highlights(
      store TEXT NOT NULL, market TEXT NOT NULL, marketplace_id TEXT NOT NULL, seller_id TEXT NOT NULL,
      asin TEXT NOT NULL, sku TEXT NOT NULL, value TEXT NOT NULL, revision INTEGER NOT NULL,
      updated_at TEXT NOT NULL, content_hash TEXT NOT NULL,
      PRIMARY KEY(store,market,marketplace_id,seller_id,asin,sku));
    CREATE TABLE IF NOT EXISTS highlights_history(
      id INTEGER PRIMARY KEY AUTOINCREMENT, store TEXT NOT NULL, market TEXT NOT NULL,
      marketplace_id TEXT NOT NULL, seller_id TEXT NOT NULL, asin TEXT NOT NULL, sku TEXT NOT NULL,
      value TEXT NOT NULL, revision INTEGER NOT NULL, updated_at TEXT NOT NULL, content_hash TEXT NOT NULL,
      UNIQUE(store,market,marketplace_id,seller_id,asin,sku,revision));
    """)
    return db


def _scope_and_product(cfg,market,asin,sku):
    item=next((m for m in cfg["markets"] if m["market"]==market),None)
    if item is None: raise ManualFieldError("market_not_configured")
    sp=ops.load_sp_config(item["sp_config"]); seller=sp.get("seller_id")
    runs=sorted((Path(cfg["workspace"])/"runs").glob("*_"+market),reverse=True)
    if not runs or not seller: raise ManualFieldError("source_context_missing")
    run=runs[0]; bound=ops._read(run/"source_context.json",{}) or {}
    expected={"store":cfg["store"],"market":market,"marketplace_id":sp.get("marketplace_id"),"seller_id":seller}
    if ops._sp_scope(bound) != expected: raise ManualFieldError("source_context_mismatch")
    view=ops._domain_view(run,"listings",expected_scope=expected,allow_stale=False)
    if not view: raise ManualFieldError("listings_unverified")
    found=False
    marketplace=sp.get("marketplace_id")
    for row in view["value"].get("rows",[]):
        if row.get("sku") != sku: continue
        if any(summary.get("marketplaceId")==marketplace and summary.get("asin")==asin for summary in row.get("summaries",[])):
            found=True; break
    if not found: raise ManualFieldError("listing_identity_not_found")
    return expected


def _validate_identity(asin,sku):
    if not isinstance(asin,str) or not asin or len(asin)>64 or not isinstance(sku,str) or not sku or len(sku)>128:
        raise ManualFieldError("listing_identity_invalid")


def save_manual(config,market,asin,sku,value,expected_revision):
    cfg=ops._config(config); _validate_identity(asin,sku)
    if not isinstance(value,str) or len(value)>2000: raise ManualFieldError("value_invalid")
    if isinstance(expected_revision,bool) or not isinstance(expected_revision,int) or expected_revision<0:
        raise ManualFieldError("revision_invalid")
    scope=_scope_and_product(cfg,market,asin,sku); now=datetime.now(timezone.utc).isoformat()
    content_hash=hashlib.sha256(json.dumps({"scope":scope,"asin":asin,"sku":sku,"value":value},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    db=_database(cfg)
    try:
        db.execute("BEGIN IMMEDIATE")
        params=(scope["store"],market,scope["marketplace_id"],scope["seller_id"],asin,sku)
        row=db.execute("SELECT revision FROM current_highlights WHERE store=? AND market=? AND marketplace_id=? AND seller_id=? AND asin=? AND sku=?",params).fetchone()
        current=row[0] if row else 0
        if current != expected_revision: raise ManualFieldError("revision_conflict")
        revision=current+1
        db.execute("INSERT INTO highlights_history(store,market,marketplace_id,seller_id,asin,sku,value,revision,updated_at,content_hash) VALUES(?,?,?,?,?,?,?,?,?,?)",params+(value,revision,now,content_hash))
        db.execute("INSERT INTO current_highlights(store,market,marketplace_id,seller_id,asin,sku,value,revision,updated_at,content_hash) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(store,market,marketplace_id,seller_id,asin,sku) DO UPDATE SET value=excluded.value,revision=excluded.revision,updated_at=excluded.updated_at,content_hash=excluded.content_hash",params+(value,revision,now,content_hash))
        db.execute("COMMIT")
        return {"market":market,"asin":asin,"sku":sku,"value":value,"revision":revision,"updatedAt":now,"contentHash":content_hash,"source":"manual_local"}
    except Exception:
        try: db.execute("ROLLBACK")
        except sqlite3.Error: pass
        raise
    finally: db.close()


def get_manual(config,market,asin,sku):
    cfg=ops._config(config); _validate_identity(asin,sku); scope=_scope_and_product(cfg,market,asin,sku); db=_database(cfg)
    try:
        row=db.execute("SELECT value,revision,updated_at,content_hash FROM current_highlights WHERE store=? AND market=? AND marketplace_id=? AND seller_id=? AND asin=? AND sku=?",
            (scope["store"],market,scope["marketplace_id"],scope["seller_id"],asin,sku)).fetchone()
        if not row: return None
        return {"market":market,"asin":asin,"sku":sku,"value":row[0],"revision":row[1],"updatedAt":row[2],"contentHash":row[3],"source":"manual_local"}
    finally: db.close()


def enrich_manual(config,snapshot):
    cfg=ops._config(config); result=deepcopy(snapshot); rows=[]; products=((result.get("queries") or {}).get("products") or {}).get("rows",[])
    for product in products:
        try: item=get_manual(cfg,product.get("market"),product.get("asin"),product.get("sku"))
        except ManualFieldError: continue
        if item:
            rows.append(item); product["manualItemHighlights"]=item["value"]; product["manualItemHighlightsRevision"]=item["revision"]
    result.setdefault("queries",{})["manual_listing_fields"]={"source":{"label":"手动 Listing 字段","provider":"local SQLite","coverage":{"startDate":None,"endDate":None},"files":[],"metricDefinitions":[],"evidenceFlow":[{"title":"来源","detail":"用户本地手动录入；非 Amazon/PTD/Category Listings 字段"}]},"rows":rows}
    return result
