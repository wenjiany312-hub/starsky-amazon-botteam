# -*- coding: utf-8 -*-
"""starsky-promotion-kb 冒烟测试：初始化 → tools/list → kb_status → 场景规则 → 诊断。"""
import json, subprocess, sys
from pathlib import Path

PKG = Path(__file__).resolve().parent
PY = sys.executable
kb = PKG / "kb"

proc = subprocess.Popen([PY, str(PKG / "server.py"), "--kb", str(kb)],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, text=True, encoding="utf-8")

def send(obj):
    proc.stdin.write(json.dumps(obj) + "\n"); proc.stdin.flush()

def recv():
    line = proc.stdout.readline()
    return json.loads(line) if line.strip() else None

send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
      "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                 "clientInfo": {"name": "smoke", "version": "1.0"}}})
init = recv()["result"]
print("server:", init["serverInfo"]["name"], init["serverInfo"]["version"])

send({"jsonrpc": "2.0", "method": "notifications/initialized"})
send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
tools = [t["name"] for t in recv()["result"]["tools"]]
assert "kb_status" in tools and "kb_scenario_rules" in tools, f"工具缺失: {tools}"
print("tools:", len(tools))

send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
      "params": {"name": "kb_status", "arguments": {}}})
st = json.loads(recv()["result"]["content"][0]["text"])
assert (st.get("stats") or {}).get("cards"), "kb_status无卡数"
print("cards:", st["stats"]["cards"])
proc.stdin.close()
proc.wait(timeout=15)
print("[PROMOTION-KB SMOKE PASS]")
