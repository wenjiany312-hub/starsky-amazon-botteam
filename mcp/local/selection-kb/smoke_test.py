# -*- coding: utf-8 -*-
"""starsky-selection-kb 冒烟：initialize → tools/list → 场景规则 → 诊断。"""
import json, subprocess, sys
from pathlib import Path

PKG = Path(__file__).resolve().parent
PY = sys.executable
kb = PKG / "kb"

proc = subprocess.Popen([PY, str(PKG / "server.py"), "--kb", str(kb)],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, text=True, encoding="utf-8")

def send(obj):
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()

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
assert len(tools) == 8, f"工具数异常: {tools}"
print("tools x8:", ",".join(tools))

send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
      "params": {"name": "kb_scenario_rules",
                 "arguments": {"selection_mode": "精铺", "entry_path": "大盘扫描", "selection_phase": "定模式"}}})
r = recv()["result"]["content"][0]["text"]
assert "KC-" in r, "场景规则未命中卡"
print("scenario_rules: OK")

send({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
      "params": {"name": "kb_diagnose", "arguments": {"symptom": "档期已过"}}})
r = recv()["result"]["content"][0]["text"]
assert "KC-" in r, "诊断未命中"
print("diagnose: OK")

proc.terminate()
print("SMOKE TEST PASS")
