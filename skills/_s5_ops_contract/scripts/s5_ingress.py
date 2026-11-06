# -*- coding: utf-8 -*-
"""S5 第 1 步：分业务域的数据状态。静态配置不等于真实调用。"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


def _home() -> Path:
    return Path(os.environ.get("STARSKY_HOME") or (Path.home() / ".starsky-codex")).expanduser()


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")).expanduser()


def _ops_root() -> Path:
    raw = (os.environ.get("STARSKY_OPS_ROOT") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return _home() / "ops_workspace"


def _product_root(explicit: str | None) -> Path | None:
    raw = (explicit or os.environ.get("STARSKY_PRODUCT_ROOT") or "").strip()
    return Path(raw).expanduser() if raw else None


def _read_yaml(path: Path) -> dict:
    if not path.is_file() or yaml is None:
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _nonempty(value) -> bool:
    return bool(str(value or "").strip())


def probe_lingxing() -> dict:
    path = _home() / "config" / "lingxing_config.yaml"
    data = _read_yaml(path)
    lx = data.get("lingxing") if isinstance(data.get("lingxing"), dict) else {}
    app_id = _nonempty(lx.get("app_id"))
    app_secret = _nonempty(lx.get("app_secret"))
    stores = [s for s in (data.get("stores") or []) if isinstance(s, dict)]
    patrol = [s for s in stores if s.get("patrol")]
    if not path.is_file():
        status = "not_configured"
    elif not app_id or not app_secret:
        status = "no_credential"
    elif not patrol:
        status = "no_patrol_store"
    else:
        status = "configured"
    return {
        "path": "lingxing",
        "status": status,
        "config_present": path.is_file(),
        "app_id_present": app_id,
        "store_count": len(stores),
        "patrol_store_count": len(patrol),
        "secret_redacted": True,
    }


def _sp_client_module():
    path = Path(__file__).resolve().parents[2] / "amazon-sp-api-connector" / "scripts" / "sp_api_client.py"
    spec = importlib.util.spec_from_file_location("_s5_sp_api_client", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _probe_sp_connection(path: Path, client) -> dict:
    metadata = _read_yaml(path)
    result = {"name": path.stem, "region": metadata.get("region") if metadata.get("region") in {"NA", "EU", "FE"} else None,
              "config_present": path.is_file(), "secret_redacted": True}
    lwa = metadata.get("lwa") if isinstance(metadata.get("lwa"), dict) else metadata
    legacy_lwa = _nonempty(lwa.get("client_id") or metadata.get("lwa_client_id")) and _nonempty(
        lwa.get("refresh_token") or metadata.get("refresh_token"))
    if not metadata.get("region") and legacy_lwa:
        result.update(status="configured_unverified", lwa_present=True,
                      validation="pending_explicit_region")
        return result
    try:
        data = client.load_config(path)
        present = lambda field: _nonempty(client._field(data, field))
        has_lwa = present("client_id") and present("client_secret") and present("refresh_token")
        result.update(status="configured_unverified" if has_lwa else "no_credential",
                      lwa_present=has_lwa, validation="pending_business_call")
    except Exception:
        result.update(status="invalid_config", lwa_present=False, validation="invalid_config")
    return result


def probe_sp_api() -> dict:
    folder = _home() / "config"
    candidates = sorted(p for p in folder.glob("sp_api_config*.yaml") if p.is_file())
    legacy = _ops_root() / "00_连接" / "sp_api_config.yaml"
    if legacy.is_file() and legacy not in candidates:
        candidates.append(legacy)
    if not candidates:
        return {"path":"sp_api", "status":"not_configured", "config_present":False,
                "connections":[], "lwa_present":False, "aws_keys_required":False,
                "note":"只用 LWA；需显式验证 region、marketplace 与业务域。", "secret_redacted":True}
    client = _sp_client_module()
    connections = [_probe_sp_connection(path, client) for path in candidates]
    statuses = {item["status"] for item in connections}
    precedence = ("configured_unverified", "no_credential", "invalid_config")
    status = next(value for value in precedence if value in statuses)
    return {"path":"sp_api", "status":status, "config_present":True, "connections":connections,
            "lwa_present":any(item["lwa_present"] for item in connections), "aws_keys_required":False,
            "note":"只用 LWA；旧配置可识别但 region 未显式时保持 validation pending。",
            "secret_redacted":True}


def probe_feed(product_root: Path | None) -> dict:
    folders = [_ops_root() / "_投喂_巡检报表"]
    if product_root:
        folders.append(product_root / "S5_运营板块" / "_投喂_巡检报表")
    files = []
    for folder in folders:
        if not folder.is_dir():
            continue
        files.extend(p for p in folder.rglob("*") if p.is_file() and p.name != ".gitkeep")
    newest = max(files, key=lambda p: p.stat().st_mtime) if files else None
    return {
        "path": "feed_folder",
        "status": "has_files" if files else "empty",
        "file_count": len(files),
        "newest_name": newest.name if newest else None,
        "degraded_only": True,
    }


def _bundled_kb(folder: str) -> bool:
    """Claude Code edition registers the knowledge bases from the plugin's own .mcp.json."""
    root = Path(__file__).resolve().parents[3]
    return (root / ".claude-plugin").is_dir() and (root / "mcp" / folder / "server.py").is_file()


def probe_ads_kb() -> dict:
    cfg = _codex_home() / "config.toml"
    text = cfg.read_text(encoding="utf-8-sig") if cfg.is_file() else ""
    installed = "ads_knowledge_base" in text
    dest = _codex_home() / "mcp" / "starsky-ads-kb" / "server.py"
    return {
        "path": "ads_knowledge_base",
        "status": "configured" if _bundled_kb("ads-kb") or (installed and dest.is_file()) else "not_installed",
        "optional": True,
        "writes_ads": False,
    }


def probe_promotion_kb() -> dict:
    cfg = _codex_home() / "config.toml"
    text = cfg.read_text(encoding="utf-8-sig") if cfg.is_file() else ""
    installed = "promotion_knowledge_base" in text
    dest = _codex_home() / "mcp" / "starsky-promotion-kb" / "server.py"
    return {
        "path": "promotion_knowledge_base",
        "status": "configured" if _bundled_kb("promotion-kb") or (installed and dest.is_file()) else "not_installed",
        "optional": True,
        "writes_ads": False,
    }


def _ads_client_module():
    # Load the sibling connector by its fixed plugin path, never from personal config.
    path = Path(__file__).resolve().parents[2] / "amazon-ads-api-connector" / "scripts" / "ads_api_client.py"
    spec = importlib.util.spec_from_file_location("_s5_ads_api_client", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _probe_ads_connection(path: Path, client) -> dict:
    metadata = _read_yaml(path)
    name = metadata.get("connection_name")
    name = name.strip() if isinstance(name, str) and name.strip() else path.stem
    region = metadata.get("region")
    country = metadata.get("expected_country")
    result = {
        "connection_name": name,
        "region": region if region in {"NA", "EU", "FE"} else None,
        "expected_country": country if isinstance(country, str) and len(country) == 2 and country.isalpha() else None,
        "config_present": path.is_file(),
        "advertising_data_verified": False,
        "secret_redacted": True,
    }
    if not path.is_file():
        result["status"] = "not_configured"
        return result
    try:
        data = client.load_config(path)
    except Exception:
        # Never serialize parser exceptions: they may contain credential data.
        result["status"] = "invalid_config"
        return result

    def present(field):
        try:
            return bool(client._credential(data, field))
        except Exception:
            return False

    if not present("client_id") or not present("client_secret"):
        status = "no_credential"
    elif not present("refresh_token"):
        status = "needs_authorization"
    elif not str(data.get("profile_id") or "").isdigit():
        status = "needs_profile"
    else:
        status = "configured_unverified"
    result["status"] = status
    return result


def probe_amazon_ads_api() -> dict:
    override = os.environ.get("STARSKY_ADS_CONFIG")
    if override:
        candidates = [Path(override).expanduser()]
    else:
        folder = _home() / "config"
        candidates = sorted(p for p in folder.glob("amazon_ads_api*.yaml") if p.is_file())
        if not candidates:
            candidates = [folder / "amazon_ads_api.yaml"]
    client = _ads_client_module()
    connections = [_probe_ads_connection(path, client) for path in candidates]
    statuses = {c["status"] for c in connections}
    # Aggregate means at least one connection reached this static setup stage.
    # Per-connection status retains errors and incomplete setup in other files.
    precedence = ("configured_unverified", "needs_profile", "needs_authorization",
                  "no_credential", "invalid_config", "not_configured")
    status = next(value for value in precedence if value in statuses)
    return {"path": "amazon_ads_api", "status": status,
            "config_present": any(c["config_present"] for c in connections),
            "connections": connections, "advertising_data_verified": False,
            "ad_products": ["SPONSORED_PRODUCTS"], "writes_campaigns": False,
            "secret_redacted": True, "note": "先验证 profiles，再拉完指定日期报表；不以配置存在判定可用。"}


def official_api_available() -> bool:
    """官方 API 路线的 Skill 随哪一档带就探测哪一档；没带的版本这一路不存在，卡片里也不出现。"""
    skills = Path(__file__).resolve().parents[2]
    return (skills / "amazon-ads-api-connector").is_dir() and (skills / "amazon-sp-api-connector").is_dir()


def evaluate(product_root: Path | None = None) -> dict:
    api = official_api_available()
    lingxing = probe_lingxing()
    sp_api = probe_sp_api() if api else {"status": "not_available"}
    feed = probe_feed(product_root)
    kb = probe_ads_kb()
    promo = probe_promotion_kb()
    ads_api = probe_amazon_ads_api() if api else {"status": "not_available"}
    configured = (lingxing["status"] == "configured" or sp_api["status"] == "configured_unverified"
                  or ads_api["status"] in {"configured_unverified", "needs_profile"})
    if configured:
        gate = "needs_verification"
        mode = "configured_unverified"
    elif feed["status"] == "has_files":
        gate = "degraded"
        mode = "feed_snapshot_only"
    else:
        gate = "blocked"
        mode = "no_pipeline"
    return {
        "schema_version": "1.1",
        "artifact_type": "s5_ingress",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "gate": gate,
        "mode": mode,
        "paths": {
            "lingxing": lingxing,
            **({"sp_api": sp_api} if api else {}),
            "feed_folder": feed,
            "ads_knowledge_base": kb,
            "promotion_knowledge_base": promo,
            **({"amazon_ads_api": ads_api} if api else {}),
        },
        "advertising_data_verified": False,
        "advertising_sources": (["amazon_ads_api"] if api else []) + ["lingxing", "feed_folder"],
        "sp_api_advertising_source": False,
        "ops_workspace": str(_ops_root()),
    }


def render_card(result: dict) -> str:
    paths = result["paths"]
    lines = [
        "【S5 · 巡检接入检查】",
        f"闸门：{result['gate']}（{result['mode']}）",
        f"领星：{paths['lingxing']['status']} · 巡检店铺 {paths['lingxing']['patrol_store_count']}",
        *([f"Amazon Ads API：{paths['amazon_ads_api']['status']} · SP 广告只读，需验证账号及报表",
           f"SP-API：{paths['sp_api']['status']} · 只需 LWA，不要填 AWS keys"] if "amazon_ads_api" in paths else []),
        f"文件投喂：{paths['feed_folder']['status']} · {paths['feed_folder']['file_count']} 个文件",
        f"广告知识库：{paths['ads_knowledge_base']['status']}（只出建议，不改后台）",
        f"推广知识库：{paths['promotion_knowledge_base']['status']}（断货/掉位时调，不改后台）",
        f"运营账本：{result['ops_workspace']}",
    ]
    for connection in paths.get("amazon_ads_api", {}).get("connections", []):
        lines.append(f"Ads 连接：{connection['connection_name']} · {connection['region'] or '未填区域'} · "
                     f"{connection['expected_country'] or '未填国家'} · {connection['status']}")
    if result["gate"] == "blocked":
        lines.append(
            "【需要你做】广告复盘选择 Ads API、领星或报表文件之一；订单/库存另看领星或 SP-API。"
            "缺授权先完成授权；把报表丢进投喂夹可做一次性快照。"
        )
    elif result["gate"] == "degraded":
        lines.append("【需要你做】本次只按投喂文件出快照，不声称实时店铺健康。")
    else:
        lines.append("【下一步】先对所选来源做真实读取并记录结果，再出巡检或复盘建议。仅有配置时不宣称实时可用。")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="S5 接入闸门")
    ap.add_argument("--product-root")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", help="写入 ingress JSON")
    args = ap.parse_args()
    result = evaluate(_product_root(args.product_root))
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_card(result))
    return 0 if result["gate"] != "blocked" else 2


if __name__ == "__main__":
    raise SystemExit(main())
