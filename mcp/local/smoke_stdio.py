#!/usr/bin/env python3
"""通用 stdio MCP 冒烟测试：启动命令 → initialize → tools/list → 打印工具名。不调用任何工具。
用法：python3 smoke_stdio.py <命令> [参数...]
"""
import json, subprocess, sys, time

cmd = sys.argv[1:]
if not cmd:
    print("用法：python3 smoke_stdio.py <命令> [参数...]"); sys.exit(2)
name = " ".join(cmd)[-60:]
p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                     text=True, encoding="utf-8", bufsize=1)
def send(o):
    p.stdin.write(json.dumps(o) + "\n"); p.stdin.flush()
send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
      "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "1.0"}}})
send({"jsonrpc": "2.0", "method": "notifications/initialized"})
send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
init, tools = None, None
deadline = time.time() + 60
while time.time() < deadline and tools is None:
    line = p.stdout.readline()
    if not line:
        break
    try:
        m = json.loads(line)
    except Exception:
        continue
    if m.get("id") == 1: init = m.get("result", {}).get("serverInfo", {})
    if m.get("id") == 2: tools = [t.get("name") for t in m.get("result", {}).get("tools", [])]
p.terminate()
if init is not None and tools:
    print(f"✅ PASS {name} → {init.get('name', '')} 工具：{', '.join(tools)}"); sys.exit(0)
err = ""
try:
    err = p.stderr.read()[-800:]
except Exception:
    pass
print(f"❌ FAIL {name}（initialize={'ok' if init is not None else '无响应'}, tools={tools}）\n{err}"); sys.exit(1)
