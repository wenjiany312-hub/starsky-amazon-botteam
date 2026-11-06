#!/usr/bin/env python3
"""检测星空配置：外部数据 MCP、本地知识库、授权、S5 接入、悬浮窗，一次看清哪些好了、哪些还差、下一步说什么。

  python check_setup.py            # 只读本机配置，不联网
  python check_setup.py --live     # 再联网试一下 Sorftime / SIF 能不能调通（只调 get_time / ping）

不打印任何密钥、授权码，也不显示版本档。输出一份 JSON：items（逐项状态）、lines（可以直接念给用户的清单）、next（下一步）。
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import importlib.util
import json
import os
import re
import sys
import tomllib
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve()
PLUGIN = HERE.parents[1]
APP_HOME = Path(os.environ.get("STARSKY_HOME") or (Path.home() / ".starsky-codex")).expanduser()
IS_CLAUDE = (PLUGIN / ".claude-plugin").is_dir()
ICON = {"ok": "✅", "warn": "⚠️", "todo": "❌"}

# (显示名, 别名, 必须吗, 探测工具)
EXTERNAL = [
    ("Sorftime（市场数据）", ("sorftime", "sorftime-mcp"), True, "get_time"),
    ("SIF（词库 / 广告信号）", ("sif", "sif_mcp", "sif-mcp"), True, "ping"),
    ("西柚洞察", ("xydc", "xydc-mcp", "xiyou", "xiyou_mcp"), False, None),
    ("卖家精灵", ("sellersprite", "sellersprite_mcp", "sellersprite-mcp", "seller_sprite"), False, None),
]
KBS = [("广告知识库", "ads-kb", "ads_knowledge_base"), ("选品知识库", "selection-kb", "selection_knowledge_base"),
       ("产品推广知识库", "promotion-kb", "promotion_knowledge_base")]


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def env_value(name: str):
    try:
        return load_module(PLUGIN / "scripts" / "starsky_mcp.py", "starsky_mcp_chk").env_value(name)
    except Exception:  # noqa: BLE001
        return os.environ.get(name)


def mcp_config_path() -> Path:
    """用户实际登记 MCP 的配置文件（Claude 版构建时这一行换成 Claude 的用户级配置）。"""
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")).expanduser() / "config.toml"


def read_servers(path: Path) -> dict:
    try:
        if path.suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            return {k: v for k, v in (data.get("mcpServers") or {}).items() if isinstance(v, dict)}
        return {k: v for k, v in (tomllib.loads(path.read_text(encoding="utf-8-sig")).get("mcp_servers") or {}).items() if isinstance(v, dict)}
    except (OSError, ValueError):
        return {}


def credential_state(spec: dict) -> str | None:
    """None = 凭据齐；否则返回缺的环境变量名。写死在配置里的密钥不打印，只认「有」。"""
    values = list((spec.get("headers") or {}).values()) + list((spec.get("http_headers") or {}).values())
    for value in values:
        for var in re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", str(value)):
            if not env_value(var):
                return var
    for var in (spec.get("env_http_headers") or {}).values():
        if not env_value(str(var)):
            return str(var)
    var = spec.get("bearer_token_env_var")
    if var and not env_value(str(var)):
        return str(var)
    return None


def mcp_item(label, aliases, required, servers):
    name = next((n for n in aliases if n in servers), None)
    if not name:
        return {"group": "外部数据 MCP", "name": label, "status": "todo" if required else "warn", "detail": "还没接入" + ("（必配）" if required else "（可选）"),
                "fix": "把「星空MCP密钥.txt」填好拖进对话，说「安装星空MCP」" if required else "有账号再接：同样用「星空MCP密钥.txt」，说「安装星空MCP」", "server": None}
    spec = servers[name]
    if spec.get("enabled") is False:
        return {"group": "外部数据 MCP", "name": label, "status": "warn", "detail": "已登记但被关闭", "fix": "在宿主的 MCP 设置里打开它", "server": name}
    missing = credential_state(spec)
    if missing:
        return {"group": "外部数据 MCP", "name": label, "status": "todo", "detail": f"已登记，缺环境变量 {missing}", "fix": f"设置环境变量 {missing} 后重开宿主", "server": name}
    return {"group": "外部数据 MCP", "name": label, "status": "ok", "detail": "已接入", "fix": "", "server": name}


def kb_items(servers):
    items = []
    for label, folder, server in KBS:
        cards = list((PLUGIN / "mcp" / folder / "kb" / "cards").glob("*.md"))
        ready = (PLUGIN / "mcp" / folder / "server.py").is_file() and cards
        registered = IS_CLAUDE or server in servers  # Claude 版随插件自动登记；Codex 版要登记进它的 MCP 配置
        if ready and registered:
            items.append({"group": "本地知识库", "name": label, "status": "ok", "detail": f"{len(cards)} 张卡", "fix": ""})
        elif ready:
            items.append({"group": "本地知识库", "name": label, "status": "todo", "detail": "文件在，但没登记进 Codex", "fix": "运行安装包里的「安装与更新」（会自动登记三个知识库）"})
        else:
            items.append({"group": "本地知识库", "name": label, "status": "todo", "detail": "插件里缺知识库文件", "fix": "重新运行「安装与更新」"})
    return items


def license_item():
    try:
        code = json.loads((APP_HOME / "license.json").read_text(encoding="utf-8-sig")).get("code", "")
        payload = json.loads(base64.urlsafe_b64decode(code.split(".")[1] + "=="))
        expires = date.fromisoformat(payload["expires"])
    except (OSError, ValueError, KeyError, IndexError):
        return {"group": "授权与更新", "name": "授权", "status": "todo", "detail": "没找到授权", "fix": "运行安装包里的「申请授权」，把申请码发给坚哥，拿到授权码后运行「安装与更新」"}
    left = (expires - date.today()).days
    status = "ok" if left >= 30 else "warn"
    tail = "" if left >= 0 else "（已过期：已装版本继续能用，装新版需要续费）"
    return {"group": "授权与更新", "name": "授权", "status": status if left >= 0 else "warn", "detail": f"有效期至 {expires.isoformat()}（还剩 {left} 天）{tail}",
            "fix": "快到期了：联系坚哥续费，续费后运行「检查新版」" if left < 30 else ""}


def s5_items():
    out = []
    try:
        mod = load_module(PLUGIN / "skills" / "_s5_ops_contract" / "scripts" / "s5_connect.py", "s5_connect_chk")
        info = mod.cmd_status(argparse.Namespace())[0]
    except Exception:  # noqa: BLE001
        return out
    out.append({"group": "S5 运营接入", "name": "领星", "status": "ok" if info.get("lingxing") else "warn",
                "detail": ("已接入，巡检店铺：" + "、".join(info.get("patrol_stores") or [])) if info.get("lingxing") else "还没接入（不用 ERP 可以忽略，走手动报表）",
                "fix": "" if info.get("lingxing") else "S5 悬浮窗里点「📘 接入指南」，说「接入领星」"})
    out.append({"group": "S5 运营接入", "name": "飞书日报", "status": "ok" if info.get("feishu") else "warn",
                "detail": ("已接入，定时：" + str(info.get("schedule"))) if info.get("feishu") else "还没接入（可选）",
                "fix": "" if info.get("feishu") else "说「接入飞书」，再说「开启日报」"})
    costs = info.get("cost_tables") or {}
    out.append({"group": "S5 运营接入", "name": "成本表", "status": "ok" if costs else "warn", "detail": ("已登记：" + "、".join(costs)) if costs else "还没有（推荐）",
                "fix": "" if costs else "说「生成成本表」或「毛利大概 30%」"})
    return out


def float_item():
    home = Path.home()
    label = "Claude" if APP_HOME.name.endswith("claude") else "Codex"
    name = f"星空导航（{label}）"
    if sys.platform == "darwin":
        ok = (home / "Applications" / f"{name}.app").exists()
        where = f"Spotlight 搜「{name}」"
    elif sys.platform == "win32":
        ok = any((Path(os.environ.get("USERPROFILE", str(home))) / d / f"{name}.lnk").exists() for d in ("Desktop", "桌面"))
        where = f"桌面上的「{name}」"
    else:
        return None
    return {"group": "悬浮窗", "name": "系统入口", "status": "ok" if ok else "warn", "detail": ("已装好：" + where) if ok else "还没有系统入口",
            "fix": "" if ok else "对助手说「打开悬浮窗」，会生成一个"}


def live_check(servers, results):
    sys.path.insert(0, str(PLUGIN / "scripts"))
    try:
        import starsky_mcp
    except Exception:  # noqa: BLE001
        return
    config = mcp_config_path()
    for item in results:
        probe = next((p for label, _a, _r, p in EXTERNAL if label == item["name"]), None)
        if not probe or item["status"] != "ok" or not item.get("server"):
            continue
        try:
            reply = asyncio.run(starsky_mcp.execute(config, item["server"], "call", probe, {}, timeout=20))
            bad = reply.get("isError")
            item["live"] = "调通了" if not bad else "接口返回错误（密钥可能失效或过期）"
        except BaseException as exc:  # noqa: BLE001 — 网络 / 鉴权失败只报类别，不带服务端原文
            if isinstance(exc, KeyboardInterrupt):
                raise
            item["live"] = "连不上（网络或密钥问题）"
        if item["live"] != "调通了":
            item["status"], item["fix"] = "todo", "检查网络和密钥；密钥失效就重新填「星空MCP密钥.txt」说「安装星空MCP」"
        item["detail"] += "；联网试了一下：" + item["live"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="联网试调 Sorftime / SIF")
    args = ap.parse_args(argv)
    servers = read_servers(mcp_config_path())
    if IS_CLAUDE:  # 桥接登记表也认（安装器写的），用户级配置里没有时兜底
        servers = {**read_servers(APP_HOME / "mcp_servers.json"), **servers}
    externals = [mcp_item(l, a, r, servers) for l, a, r, _p in EXTERNAL]
    if args.live:
        live_check(servers, externals)
    externals.append(mcp_item("1688 抓取", ("1688_scraper",), False, servers))
    items = externals + kb_items(servers) + [license_item()] + s5_items()
    fl = float_item()
    if fl:
        items.append(fl)
    try:
        version = json.loads(next((PLUGIN / d / "plugin.json" for d in (".claude-plugin", ".codex-plugin") if (PLUGIN / d / "plugin.json").is_file())).read_text(encoding="utf-8"))["version"]
    except (StopIteration, OSError, ValueError, KeyError):
        version = ""
    lines, group = [], None
    for it in items:
        if it["group"] != group:
            group = it["group"]
            lines.append(f"【{group}】")
        lines.append(f"{ICON[it['status']]} {it['name']}：{it['detail']}" + (f"　→ {it['fix']}" if it["fix"] else ""))
    counts = {k: sum(1 for i in items if i["status"] == k) for k in ICON}
    must = [i for i in items if i["status"] == "todo"]
    nxt = [f"{i['name']}：{i['fix']}" for i in must] or ["都配好了，直接说「启动星空」"]
    for i in items:
        i.pop("server", None)
    print(json.dumps({"version": version, "host": "claude" if IS_CLAUDE else "codex", "live": bool(args.live), "counts": counts, "items": items,
                      "lines": lines, "next": nxt}, ensure_ascii=False, indent=1))
    return 0 if not must else 2


if __name__ == "__main__":
    sys.exit(main())
