# -*- coding: utf-8 -*-
"""
Sorftime / SIF MCP 直连桥接脚本（不依赖原生 MCP 工具注入）。

用途：
  部分客户端会话未注入 mcp__* 工具时，可在已授权的环境中使用本脚本。
  本脚本按 MCP streamable HTTP 协议调用服务端；成功不代表当前会话已原生注入工具。
  sif_mcp 在 config.toml 里可以是 stdio 中转、没有 url；此时自动走
  https://mcp.sif.com/mcp，密钥读 SIF_MCP_TOKEN / SIF_MCP_SECRET_KEY。

用法：
  python sorftime_mcp.py [--server <name>] list [过滤词]        # 列出工具
  python sorftime_mcp.py [--server <name>] schema <tool>        # 查看入参结构
  python sorftime_mcp.py [--server <name>] call <tool> '<json>' # 调用工具

服务器：
  默认 sorftime；可选 sif_mcp / sellersprite-01。
  地址和 http_headers 自动从 ~/.codex/config.toml 的 [mcp_servers.<server>] 读取，
  密钥不出现在命令和输出里。

示例：
  python sorftime_mcp.py call get_time '{}'
  python sorftime_mcp.py call category_tree amz_site=US
  python sorftime_mcp.py --server sif_mcp call ping '{}'
  python sorftime_mcp.py --server sif_mcp call market_get_keyword_demand keyword=rug amz_site=US
"""

import json
import os
import re
import sys
import urllib.request

SIF_SERVERS = {"sif_mcp", "sif-mcp", "sif"}
SIF_DEFAULT_URL = "https://mcp.sif.com/mcp"
SIF_SECRET_ENVS = ("SIF_MCP_TOKEN", "SIF_MCP_SECRET_KEY")


def env_or_user(name):
    value = os.environ.get(name)
    if value:
        return value
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as handle:
                value = winreg.QueryValueEx(handle, name)[0]
            if value:
                return str(value)
        except OSError:
            pass
    return None


def _server_block(text, server):
    escaped = re.escape(server)
    return re.search(
        r"(?s)\[mcp_servers\.(?:\"%s\"|'%s'|%s)\]\s*(.*?)(?=\r?\n\[|\Z)"
        % (escaped, escaped, escaped),
        text,
    )


def read_server_config(server):
    env_url = os.environ.get("MCP_URL")
    cfg_path = os.path.expanduser(r"~\.codex\config.toml")
    with open(cfg_path, "r", encoding="utf-8") as f:
        text = f.read()
    m = _server_block(text, server)
    if not m:
        raise SystemExit("config.toml 中未找到 [mcp_servers.%s] 段" % server)
    block = m.group(1)
    u = re.search(r"url\s*=\s*['\"]([^'\"]+)['\"]", block)
    if u:
        url = env_url or u.group(1)
    elif server in SIF_SERVERS:
        url = env_url or os.environ.get("STARSKY_SIF_MCP_URL") or SIF_DEFAULT_URL
    elif env_url:
        url = env_url
    else:
        raise SystemExit("[mcp_servers.%s] 段缺少 url" % server)
    headers = {}
    hm = re.search(
        r"(?s)\[mcp_servers\.(?:\"%s\"|'%s'|%s)\.http_headers\]\s*(.*?)(?=\r?\n\[|\Z)"
        % (re.escape(server), re.escape(server), re.escape(server)),
        text,
    )
    if hm:
        for kv in re.finditer(r"([A-Za-z0-9_-]+)\s*=\s*['\"]([^'\"]+)['\"]", hm.group(1)):
            headers[kv.group(1)] = kv.group(2)
    bm = re.search(r"bearer_token_env_var\s*=\s*['\"]?([A-Za-z0-9_]+)['\"]?", block)
    if bm:
        token = env_or_user(bm.group(1))
        if token:
            headers["Authorization"] = "Bearer " + token
    if server in SIF_SERVERS and not any(k.lower() == "secret-key" for k in headers):
        for name in SIF_SECRET_ENVS:
            token = env_or_user(name)
            if token:
                headers["secret-key"] = token
                break
        else:
            raise SystemExit("SIF 环境变量 SIF_MCP_TOKEN / SIF_MCP_SECRET_KEY 未设置")
    return url, headers


class SorftimeBridge:
    def __init__(self, url, headers=None):
        self.url = url
        self.headers = headers or {}
        self.session_id = None
        self._rpc_id = 0

    def _post(self, payload, timeout=120):
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        headers.update(self.headers)
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        req = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            sid = resp.headers.get("Mcp-Session-Id")
            if sid:
                self.session_id = sid
            body = resp.read().decode("utf-8", errors="replace")
        return self._parse_sse(body)

    @staticmethod
    def _parse_sse(body):
        """解析 SSE 响应；纯 JSON 时直接解析。返回 JSON 对象或原始串。"""
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                data = line[5:].strip()
                try:
                    return json.loads(data)
                except json.JSONDecodeError:
                    return data
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return body

    def initialize(self):
        self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "codex-mcp-bridge", "version": "1.0"},
                },
            }
        )
        self._post(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        )

    def tools_list(self):
        self._rpc_id += 1
        resp = self._post(
            {"jsonrpc": "2.0", "id": self._rpc_id, "method": "tools/list"}
        )
        if "error" in resp:
            raise RuntimeError(json.dumps(resp["error"], ensure_ascii=False))
        return resp["result"]["tools"]

    def tools_call(self, name, arguments):
        self._rpc_id += 1
        resp = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._rpc_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        if "error" in resp:
            raise RuntimeError(json.dumps(resp["error"], ensure_ascii=False))
        return resp["result"]


def print_result(result):
    for item in result.get("content", []):
        t = item.get("type")
        if t == "text":
            sys.stdout.write(item.get("text", "") + "\n")
        elif t == "image":
            sys.stdout.write("[image content, base64 length=%s]\n" % len(item.get("data", "")))
        else:
            sys.stdout.write(json.dumps(item, ensure_ascii=False) + "\n")
    if result.get("structuredContent") is not None:
        sys.stdout.write(
            json.dumps(result["structuredContent"], ensure_ascii=False, indent=2) + "\n"
        )
    if result.get("isError"):
        sys.exit(2)


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    server = "sorftime"
    argv = []
    i = 0
    while i < len(sys.argv):
        if sys.argv[i] in ("--server", "-s") and i + 1 < len(sys.argv):
            server = sys.argv[i + 1]
            i += 2
            continue
        argv.append(sys.argv[i])
        i += 1
    if len(argv) < 2:
        print(__doc__)
        return
    cmd = argv[1]
    url, headers = read_server_config(server)
    bridge = SorftimeBridge(url, headers)
    bridge.initialize()

    if cmd == "list":
        keyword = argv[2] if len(argv) > 2 else ""
        tools = bridge.tools_list()
        for t in tools:
            name = t.get("name", "")
            if keyword and keyword.lower() not in name.lower():
                continue
            desc = (t.get("description") or "").replace("\n", " ")[:120]
            print("%-48s %s" % (name, desc))
        print("\nTOTAL: %d (filter=%r)" % (len(tools), keyword))
    elif cmd == "schema":
        if len(argv) < 3:
            raise SystemExit("用法: python sorftime_mcp.py schema <tool>")
        tools = bridge.tools_list()
        for t in tools:
            if t.get("name") == argv[2]:
                print(json.dumps(t.get("inputSchema", {}), ensure_ascii=False, indent=2))
                return
        raise SystemExit("工具不存在: %s" % argv[2])
    elif cmd == "call":
        if len(argv) < 3:
            raise SystemExit("用法: python sorftime_mcp.py call <tool> '<json>'")
        name = argv[2]
        if len(argv) > 3 and argv[3].strip().startswith('@'):
            with open(argv[3].strip()[1:], 'r', encoding='utf-8-sig') as af:
                argv[3] = json.dumps(json.load(af))
        if len(argv) > 3 and argv[3].strip():
            try:
                args = json.loads(argv[3])
            except json.JSONDecodeError:
                # PowerShell 传 JSON 常丢失引号，兼容 key=value 形式
                args = {}
                for pair in argv[3:]:
                    if "=" not in pair:
                        continue
                    k, v = pair.split("=", 1)
                    if v.lower() in ("true", "false"):
                        v = v.lower() == "true"
                    else:
                        try:
                            v = int(v)
                        except ValueError:
                            try:
                                v = float(v)
                            except ValueError:
                                pass
                    args[k] = v
                if not args:
                    raise SystemExit(
                        "参数无法解析，请用 JSON 或 key=value 形式，例如: "
                        "call category_tree amz_site=US"
                    )
        else:
            args = {}
        result = bridge.tools_call(name, args)
        print_result(result)
    else:
        raise SystemExit("未知命令: %s" % cmd)


if __name__ == "__main__":
    main()
