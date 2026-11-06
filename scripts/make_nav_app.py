"""Create the system entry for the navigation float: Mac ~/Applications/星空导航.app (Spotlight / Dock), Windows desktop shortcut.

Every launch reads the install record, so it always runs the newest installed plugin; the product is the one
the float opened last. If the float is already open, launching again brings it to the front.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import plistlib
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
from plugin_freshness import install_record  # noqa: E402

APP_DIR = Path.home() / ".starsky-codex"
# 一台电脑可能同时装 Codex 版和 Claude Code 版：入口按宿主分名，互不覆盖（Spotlight 搜「星空导航」两个都能找到）
HOST_LABEL = "Claude Desktop" if APP_DIR.name.endswith("claude") else "Codex"
APP_NAME = f"星空导航（{HOST_LABEL}）"
APP = Path.home() / "Applications" / f"{APP_NAME}.app"

NAV_START = r'''
root = globals().get("project_root")
if not root:
    try:
        root = json.loads(Path(last).read_text(encoding="utf-8")).get("root")
    except (OSError, ValueError):
        root = None
try:
    rec = json.loads(Path(record).read_text(encoding="utf-8-sig"))
except FileNotFoundError:
    if not globals().get("fallback_home"):
        raise
    rec = dict(home=fallback_home, python=fallback_python)
python = rec["python"]
entry = Path(rec["home"])
home = Path(rec.get("source") or rec["home"])
unlock = entry / "locked" / "git_unlock.py"
if unlock.is_file():
    options = dict(capture_output=True, text=True, encoding="utf-8", timeout=120)
    if sys.platform == "win32":
        options["creationflags"] = 0x08000000
    result = subprocess.run([python, "-B", str(unlock)], **options)
    lines = result.stdout.strip().splitlines()
    ready = json.loads(lines[-1]) if lines else dict(status="blocked", message="正式版解锁未返回结果")
    if result.returncode or ready.get("status") != "ready":
        raise RuntimeError(ready.get("message", "星空导航解锁失败"))
    home = Path(ready["source"])
    python = ready.get("python") or python
nav_script = home / "scripts" / "nav_float.py"
if not nav_script.is_file():
    raise FileNotFoundError("导航脚本缺失，请运行安装与更新：" + str(nav_script))
pythonw = Path(python).with_name("pythonw.exe")
exe = str(pythonw) if sys.platform == "win32" and pythonw.is_file() else python
args = [exe, "-B", str(nav_script), "--home", str(home)]
if root and Path(root).is_dir():
    args += ["--root", root]
options = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
if sys.platform == "win32":
    options["creationflags"] = 0x00000008 | 0x00000200
else:
    options["start_new_session"] = True
subprocess.Popen(args, **options)
'''

LAUNCHER = r'''#!/bin/bash
# 星空导航：读安装记录，总是打开最新装的插件版本；已开着就唤到前面。
PY="{python}"
[ -x "$PY" ] || PY="$(command -v python3)"
exec "$PY" - "{record}" "{last}" <<'EOF'
import json, os, subprocess, sys
from pathlib import Path
record, last = sys.argv[1:3]
''' + NAV_START + r'''
EOF
'''


# Windows：一个 .pyw（无黑窗口）读安装记录启动悬浮窗 + 桌面快捷方式指向它。和 Mac 一样每次读安装记录，更新后自动跑最新版。
PYW = r'''import json, os, subprocess, sys
from pathlib import Path
record, last = r"{record}", r"{last}"
''' + NAV_START


def project_launcher(record: Path, home: Path, python: str) -> str:
    """The product entry shares desktop resolution but keeps its own product root."""
    header = ('import json, os, subprocess, sys\nfrom pathlib import Path\n'
              f'record = {str(record)!r}\nlast = ""\n'
              'project_root = str(Path(__file__).resolve().parents[1])\n'
              f'fallback_home = {str(home)!r}\nfallback_python = {python!r}\n')
    body = '\n'.join('    ' + line for line in NAV_START.strip().splitlines())
    return (header + 'try:\n' + body + '\nexcept Exception as exc:\n'
            '    print("星空导航未启动：" + str(exc), flush=True)\n'
            '    print("请先运行安装与更新，再到产品的 00_导航 文件夹双击启动器。", flush=True)\n'
            '    raise SystemExit(1)\n')


def shortcut_script(target: str, arguments: str, workdir: str, icon: str = "") -> str:
    """PowerShell 建桌面快捷方式；名字含中文，所以整段走 -EncodedCommand（UTF-16LE base64），不受控制台编码影响。"""
    def q(value: str) -> str:
        return value.replace("'", "''")
    return (f"$d=[Environment]::GetFolderPath('Desktop');$s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d '{APP_NAME}.lnk'));"
            f"$s.TargetPath='{q(target)}';$s.Arguments='{q(arguments)}';$s.WorkingDirectory='{q(workdir)}';"
            + (f"$s.IconLocation='{q(icon)},0';" if icon else "") +
            "$s.Description='星空导航悬浮窗';$s.Save()")


def make_windows(record: Path) -> int:
    try:
        python = json.loads(record.read_text(encoding="utf-8-sig"))["python"]
    except (OSError, ValueError, KeyError):
        print(json.dumps({"ok": False, "error": f"找不到安装记录 {record}，请先运行安装与更新"}, ensure_ascii=False))
        return 1
    APP_DIR.mkdir(parents=True, exist_ok=True)
    pyw = APP_DIR / "open_nav_float.pyw"
    pyw.write_text(PYW.format(record=record, last=APP_DIR / "nav_float_last.json"), encoding="utf-8")
    icon_source = HERE.parents[1] / "assets/starsky-nav-v1.ico"
    icon = APP_DIR / "StarskyNav.ico"
    if icon_source.is_file():
        shutil.copy2(icon_source, icon)
    pythonw = Path(python).with_name("pythonw.exe")
    exe = str(pythonw if pythonw.is_file() else python)
    import base64
    encoded = base64.b64encode(shortcut_script(exe, f'"{pyw}"', str(APP_DIR), str(icon) if icon.is_file() else "").encode("utf-16-le")).decode()
    run = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded], capture_output=True, text=True)
    ok = run.returncode == 0
    print(json.dumps({"ok": ok, "launcher": str(pyw), **({"use": "桌面上的「星空导航」双击打开；可右键固定到任务栏"} if ok else {"error": "桌面快捷方式没建成：可以直接双击 " + str(pyw)})},
                     ensure_ascii=False))
    return 0 if ok else 1


def main() -> int:
    if sys.platform == "win32":
        return make_windows(install_record(HERE.parents[1]))
    if sys.platform != "darwin":
        print(json.dumps({"ok": False, "error": "仅支持 macOS / Windows"}, ensure_ascii=False))
        return 1
    record = install_record(HERE.parents[1])
    try:
        python = json.loads(record.read_text(encoding="utf-8-sig"))["python"]
    except (OSError, ValueError, KeyError):
        print(json.dumps({"ok": False, "error": f"找不到安装记录 {record}，请先运行安装与更新"}, ensure_ascii=False))
        return 1
    APP_DIR.mkdir(parents=True, exist_ok=True)
    legacy = Path.home() / "Applications" / "星空导航.app"  # 旧版本不分宿主的同名入口：它只可能指向最后装的那个宿主，删掉换成分名的
    if legacy.exists():
        shutil.rmtree(legacy, ignore_errors=True)
    launcher = APP_DIR / "open_nav_float.sh"
    launcher.write_text(LAUNCHER.format(python=python, record=record, last=APP_DIR / "nav_float_last.json"), encoding="utf-8")
    launcher.chmod(0o755)
    APP.parent.mkdir(parents=True, exist_ok=True)
    if APP.exists():
        shutil.rmtree(APP)
    script = f'do shell script "/bin/bash " & quoted form of "{launcher}" & " >/dev/null 2>&1 &"'
    subprocess.run(["osacompile", "-o", str(APP), "-e", script], check=True, capture_output=True)
    icon_source = HERE.parents[1] / "assets/starsky-nav-v1.icns"
    if icon_source.is_file():
        resources = APP / "Contents/Resources"
        resources.mkdir(parents=True, exist_ok=True)
        shutil.copy2(icon_source, resources / "StarskyNav.icns")
        plist_path = APP / "Contents/Info.plist"
        with plist_path.open('rb') as handle:
            properties = plistlib.load(handle)
        properties['CFBundleIconFile'] = 'StarskyNav.icns'
        with plist_path.open('wb') as handle:
            plistlib.dump(properties, handle)
    print(json.dumps({"ok": True, "app": str(APP), "launcher": str(launcher),
                      "use": "Spotlight 搜「星空导航」回车，或把它拖到程序坞；可在「快捷指令」里给它绑快捷键"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
