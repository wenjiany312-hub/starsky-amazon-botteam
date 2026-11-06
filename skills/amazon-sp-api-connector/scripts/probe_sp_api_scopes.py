#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SP-API 授权范围探针（2026-09-17）：A店铺这类“接通了但不知有何权限”时用。

在本机跑（B电脑），读本机已有配置文件，不打印任何密钥，只输出每个域：
ok（有权限）/ forbidden（403，无此角色）/ auth_failed（401，先修凭据）/ error。
缺的角色去 Amazon 开发者控制台 Roles 页补勾，不用猜。

用法：
  python probe_sp_api_scopes.py --config <sp_api_config.yaml> --seller-id S --marketplace <市场ID>
"""
from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
from pathlib import Path


def _load_client():
    path = Path(__file__).resolve().parent / "sp_api_client.py"
    spec = importlib.util.spec_from_file_location("_sp_probe_client", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _probe(client, label: str, func) -> dict:
    try:
        func()
        return {"domain": label, "result": "ok"}
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "status", None)
        name = str(exc).split(" ")[0]
        if code == 403 or "forbidden" in name.lower():
            return {"domain": label, "result": "forbidden",
                    "hint": "开发者后台 Roles 页补勾对应 API 板块后重测"}
        if code == 401 or "authentication" in name.lower() or "credentials" in name.lower():
            return {"domain": label, "result": "auth_failed",
                    "hint": "先修凭据（refresh_token/站点映射），不是缺角色"}
        return {"domain": label, "result": "error", "detail": name[:80]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", required=True)
    ap.add_argument("--seller-id", required=True)
    ap.add_argument("--marketplace", required=True, help="市场ID，如 ATVPDKIKX0DER（美）/ A1F83G8C2ARO7P（英）")
    ap.add_argument("--out", default="", help="可选：结果 JSON 写到 run 目录")
    a = ap.parse_args(argv)
    mod = _load_client()
    try:
        client = mod.SpApiClient(mod.load_config(a.config))
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "fail", "error": "config_invalid"}, ensure_ascii=False))
        return 2
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("America/Los_Angeles")
    now = datetime.datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    start = now - datetime.timedelta(days=7)
    start_iso, end_iso = start.isoformat(), now.isoformat()
    results = [
        _probe(client, "participations", client.participations),
        _probe(client, "sales(orderMetrics)", lambda: client.sales(start_iso, end_iso, "America/Los_Angeles")),
        _probe(client, "inventory", client.inventory),
        _probe(client, "listings-read", lambda: client.listings(a.seller_id)),
        _probe(client, "listings-write(PATCH干跑)",
               lambda: _dry_patch(client, a.seller_id)),
    ]
    summary = {"ok": sum(1 for r in results if r["result"] == "ok"),
               "total": len(results), "results": results}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if a.out:
        try:
            p = Path(a.out)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return 0


def _dry_patch(client, seller_id: str) -> None:
    """写权限干跑：故意不带 confirm，期望 confirm_required 即证明写通道可达且被门控拦下。"""
    try:
        client.patch_listing_item(seller_id, "PROBE_SKU", "PROBE_TYPE",
                                  [{"op": "replace", "path": "/attributes/item_name", "value": "x"}])
    except Exception as exc:  # noqa: BLE001
        name = str(exc).split(" ")[0]
        if name == "confirm_required":
            return
        raise


if __name__ == "__main__":
    raise SystemExit(main())
