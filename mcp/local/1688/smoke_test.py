# -*- coding: utf-8 -*-
"""1688_scraper MCP stdio 冒烟测试：发送 initialize + tools/list，验证服务可启动。

用法（在本夹执行，install.bat 装好 .venv 后）：
    python smoke_test.py
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRV = os.path.join(HERE, "server.py")
VENV_PY = os.path.join(HERE, ".venv", "Scripts", "python.exe")
if not os.path.exists(VENV_PY):
    VENV_PY = os.path.join(HERE, ".venv", "bin", "python")  # Mac / Linux
PY = VENV_PY if os.path.exists(VENV_PY) else sys.executable

print(f"python: {PY}")
print(f"server: {SRV}")

p = subprocess.Popen([PY, SRV], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1)

def send(obj):
    p.stdin.write(json.dumps(obj) + "\n")
    p.stdin.flush()

send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
      "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                 "clientInfo": {"name": "smoke", "version": "1.0"}}})
send({"jsonrpc": "2.0", "method": "notifications/initialized"})
send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})

got_init, got_tools = False, None
deadline = time.time() + 30
while time.time() < deadline and not (got_init and got_tools):
    line = p.stdout.readline()
    if not line:
        break
    try:
        msg = json.loads(line)
    except Exception:
        continue
    if msg.get("id") == 1:
        got_init = True
        print("initialize 响应:", msg.get("result", {}).get("serverInfo", {}))
    if msg.get("id") == 2:
        got_tools = [t["name"] for t in msg.get("result", {}).get("tools", [])]
        print("tools/list:", got_tools)

p.terminate()
print("结果:", "PASS" if (got_init and got_tools) else "FAIL")
sys.exit(0 if (got_init and got_tools) else 1)
