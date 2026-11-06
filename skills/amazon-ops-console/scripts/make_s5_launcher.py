#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S5 运营终端一键启动器生成器（下版推送用）。

把启动器写进运营账本目录（默认 ~/.starsky-codex/ops_workspace），并在桌面建
“S5运营终端”快捷方式（图标随包：skills/amazon-ops-console/assets/s5-console.ico）。

用法（助手填好路径，用户双击启动器即可）：
    python "<home>/skills/amazon-ops-console/scripts/make_s5_launcher.py" [--root <账本目录>]

只写启动文件，不碰店铺配置与凭据；console_config.json 缺失时启动器会直说，不伪造。
Windows 才建 .lnk；其他平台只写 bat/ps1 文件。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
ICON_SRC = SKILL_DIR / "assets" / "s5-console.ico"

BAT = """@echo off
setlocal
title S5 Console
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-s5-console.ps1"
if errorlevel 1 pause
endlocal
"""

# 注意：此内容写盘必须用 utf-8-sig（带 BOM），否则 Win PowerShell 5.1
# 按 GBK 解读，中文提示变乱码（StarNav 快捷方式已踩过这个坑）。
PS1 = """$ErrorActionPreference = "Stop"
$config = Join-Path $PSScriptRoot "console_storeA\\console_config.json"
if (-not (Test-Path -LiteralPath $config)) {
    throw "Missing store config: $config"
}

$roots = @(
    (Join-Path $env:USERPROFILE ".codex\\plugins\\cache\\starskycodex\\starsky-amazon-agent"),
    (Join-Path $env:USERPROFILE ".starsky-codex\\marketplace\\versions")
)
$script = $roots | ForEach-Object {
    if (Test-Path -LiteralPath $_) {
        Get-ChildItem $_ -Recurse -Filter "ops_console.py" -ErrorAction SilentlyContinue
    }
} | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $script) {
    throw "ops_console.py not found. Install Starsky Codex first."
}

$pythonCandidates = @(
    (Join-Path $env:USERPROFILE "Python312\\python.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\\Python\\Python315\\python.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\\Python\\Python312\\python.exe")
)
$python = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $python) {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { $python = $py.Source }
}
if (-not $python) {
    throw "Python not found."
}

$url = "http://127.0.0.1:9766/"
$listening = Get-NetTCPConnection -LocalPort 9766 -State Listen -ErrorAction SilentlyContinue
if (-not $listening) {
    $pythonArgs = if ($python -like "*\\py.exe") {
        @("-3", $script.FullName, "--config", $config, "serve")
    } else {
        @($script.FullName, "--config", $config, "serve")
    }
    Start-Process -FilePath $python -WorkingDirectory $script.DirectoryName -ArgumentList $pythonArgs -WindowStyle Minimized
    $ready = $false
    foreach ($i in 1..20) {
        Start-Sleep -Seconds 1
        try {
            $response = Invoke-WebRequest -Uri ($url + "api/status") -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -eq 200) { $ready = $true; break }
        } catch { }
    }
    if (-not $ready) {
        throw "Port 9766 is not ready. Check the minimized Python window."
    }
}

Start-Process $url
Write-Output "S5 console opened: $url"
Write-Output "Keep the minimized Python window open."
"""


def default_root() -> Path:
    raw = (os.environ.get("STARSKY_OPS_ROOT") or "").strip()
    if raw:
        return Path(raw).expanduser()
    home = Path(os.environ.get("STARSKY_HOME") or (Path.home() / ".starsky-codex"))
    return Path(home).expanduser() / "ops_workspace"


def make_shortcut(target: Path, icon: Path, name: str = "S5运营终端") -> Path | None:
    if os.name != "nt":
        return None
    desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
    link = desktop / f"{name}.lnk"
    ps = (
        "\ufeff$ErrorActionPreference = 'Stop'\n"
        "$w = New-Object -ComObject WScript.Shell\n"
        f"$s = $w.CreateShortcut('{link}')\n"
        f"$s.TargetPath = '{target}'\n"
        f"$s.WorkingDirectory = '{target.parent}'\n"
        f"$s.IconLocation = '{icon},0'\n"
        f"$s.Description = 'S5运营终端'\n"
        "$s.Save()\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8") as f:
        f.write(ps)
        tmp = f.name
    try:
        subprocess.run(
            ["powershell", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", tmp],
            capture_output=True,
            timeout=60,
        )
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return link if link.is_file() else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=None, help="账本目录（默认 ~/.starsky-codex/ops_workspace）")
    ap.add_argument("--no-shortcut", action="store_true", help="只写启动文件，不建桌面快捷方式")
    args = ap.parse_args(argv)
    root = Path(args.root).expanduser() if args.root else default_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / "start-s5-console.bat").write_text(BAT, encoding="utf-8", newline="\r\n")
    (root / "start-s5-console.ps1").write_text(PS1, encoding="utf-8-sig", newline="\n")
    icon = root / "s5-console.ico"
    if ICON_SRC.is_file() and not icon.is_file():
        shutil.copy2(ICON_SRC, icon)
    print(f"[S5] 启动器已写入：{root / 'start-s5-console.bat'}")
    if not args.no_shortcut:
        link = make_shortcut(root / "start-s5-console.bat", icon)
        print(f"[S5] 桌面快捷方式：{link if link else '仅 Windows 建 lnk，已跳过'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
