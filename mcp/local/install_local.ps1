# 星空亚马逊 Bot Team · 在本机（Windows）安装本地 MCP —— OpenBot / StarSky 用
#   1688_scraper（1688 抓取，需要 Python 依赖 + Chromium）
#   selection_knowledge_base / ads_knowledge_base / promotion_knowledge_base（知识库，纯标准库）
# 可以重复运行：1688 登录态（pw_user_data）和已下载的 Chromium 都会保留。
# 不用管理员权限，不装系统软件包。
# 用法：powershell -ExecutionPolicy Bypass -File <kit>\mcp\local\install_local.ps1
#   环境变量：KIT（团队包目录）、WORK（工作目录）、STARSKY_1688_DIR（复用已有的 1688 安装目录）

$OutputEncoding = [Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'

function Ok($m)   { Write-Host "[OK]   $m" }
function Warn($m) { Write-Host "[WARN] $m" }
function Die($m)  { Write-Host "[FAIL] $m"; exit 1 }

$self = Split-Path -Parent $MyInvocation.MyCommand.Path
$KIT  = if ($env:KIT) { $env:KIT } else { (Resolve-Path (Join-Path $self '..\..')).Path }
$WORK = if ($env:WORK) { $env:WORK } else { Join-Path (Split-Path -Parent $KIT) 'work' }
$DEST = if ($env:STARSKY_1688_DIR) { $env:STARSKY_1688_DIR } else { Join-Path $WORK 'mcp\starsky-1688' }
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $DEST 'ms-playwright'

Write-Host '== 0. 路径 =='
Write-Host "   团队包 KIT  = $KIT"
Write-Host "   工作目录 WORK = $WORK"
if (-not (Test-Path (Join-Path $KIT 'mcp\local\1688'))) { Die "KIT 指错了：$KIT 下面没有 mcp\local\1688" }
New-Item -ItemType Directory -Force -Path $WORK | Out-Null

Write-Host '== 1. 检查 Python =='
$PY3 = $null
foreach ($cand in @('py', 'python', 'python3')) {
    $cmd = Get-Command $cand -ErrorAction SilentlyContinue
    if (-not $cmd) { continue }
    if ($cand -eq 'py') {
        & py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { $PY3 = 'py -3'; break }
    } else {
        & $cmd.Source -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { $PY3 = $cmd.Source; break }
    }
}
if (-not $PY3) { Die '本机没有 Python 3.10 以上。先从 python.org 装好（安装时勾 Add to PATH），再重跑本脚本' }
# 统一成一个可执行文件的绝对路径，后面注册 MCP 要用
if ($PY3 -eq 'py -3') {
    $PYEXE = (& py -3 -c "import sys; print(sys.executable)").Trim()
} else { $PYEXE = $PY3 }
Ok "$(& $PYEXE -V)（$PYEXE）"

Write-Host '== 1b. 看看本机是不是已经装过 1688 MCP =='
$existing = Join-Path $env:USERPROFILE '.starsky-claude\mcp\1688'
if (-not $env:STARSKY_1688_DIR -and (Test-Path (Join-Path $existing 'server.py'))) {
    Warn "星空亚马逊 Agent 已经在本机装过 1688 MCP：$existing"
    if (Test-Path (Join-Path $existing 'pw_user_data')) {
        Warn '那里还留着 1688 登录态（pw_user_data）。想复用、不想重新扫码登录，就改用：'
        Write-Host "     `$env:STARSKY_1688_DIR='$existing'; powershell -ExecutionPolicy Bypass -File `"$self\install_local.ps1`""
        Warn '本次仍按新目录安装，两边互不影响；也可以直接在 OpenBot 里注册上面那个已有目录。'
    }
}

Write-Host "== 2. 准备 1688 MCP 目录 $DEST =="
New-Item -ItemType Directory -Force -Path $DEST | Out-Null
foreach ($f in @('server.py', 'requirements.txt', 'smoke_test.py')) {
    $src = Join-Path $KIT "mcp\local\1688\$f"
    if (-not (Test-Path $src)) { Die "缺文件 $f" }
    Copy-Item $src (Join-Path $DEST $f) -Force
}
Ok "server.py 已更新（登录态保留在 $DEST\pw_user_data）"

Write-Host '== 3. 虚拟环境和依赖 =='
$VPY = Join-Path $DEST '.venv\Scripts\python.exe'
if (-not (Test-Path $VPY)) {
    & $PYEXE -m venv (Join-Path $DEST '.venv')
    if ($LASTEXITCODE -ne 0) { Die '建虚拟环境失败' }
}
& $VPY -m pip install -q --upgrade pip 2>$null | Out-Null
& $VPY -m pip install -q -r (Join-Path $DEST 'requirements.txt')
if ($LASTEXITCODE -ne 0) { Die 'pip 安装 mcp / playwright 失败（看上面的报错，多半是网络）' }
Ok '依赖已装'

Write-Host '== 4. Chromium =='
& $VPY -m playwright install chromium
if ($LASTEXITCODE -ne 0) { Die 'Chromium 下载失败（看上面的报错，多半是网络）' }
$launch = @'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    b.close()
print("chromium launch ok")
'@
$launchFile = Join-Path $DEST '_launch_test.py'
Set-Content -Path $launchFile -Value $launch -Encoding UTF8
& $VPY $launchFile
if ($LASTEXITCODE -ne 0) {
    Warn 'Chromium 无界面启动失败，再试一次有界面的启动'
    (Get-Content $launchFile) -replace 'headless=True', 'headless=False' | Set-Content $launchFile -Encoding UTF8
    & $VPY $launchFile
    if ($LASTEXITCODE -ne 0) { Die 'Chromium 还是启动不了。把上面的报错原文交给老板；本脚本不用管理员权限，也不会替你装系统软件包' }
    Ok 'Chromium 能启动（有界面）'
} else { Ok 'Chromium 能启动' }
Remove-Item $launchFile -ErrorAction SilentlyContinue

Write-Host "== 4b. 技能脚本运行环境 $WORK\py =="
$WPY = Join-Path $WORK 'py\Scripts\python.exe'
if (-not (Test-Path $WPY)) {
    & $PYEXE -m venv (Join-Path $WORK 'py')
    if ($LASTEXITCODE -ne 0) { Die '建技能脚本虚拟环境失败' }
}
& $WPY -m pip install -q --upgrade pip 2>$null | Out-Null
& $WPY -m pip install -q -r (Join-Path $KIT 'scripts\requirements.txt')
if ($LASTEXITCODE -ne 0) {
    Warn '技能脚本依赖没装全（看上面的报错，多半是网络）。知识库和 1688 不受影响；报告生成类脚本会跑不起来，网络好了重跑本脚本'
} else { Ok "技能脚本依赖已装（$WPY）" }
$probe = @'
import importlib.util, sys
need = ['yaml', 'openpyxl', 'PIL', 'bs4', 'requests', 'Crypto', 'numpy', 'pandas', 'fitz', 'jsonschema', 'cv2', 'rapidocr']
bad = [m for m in need if importlib.util.find_spec(m) is None]
print('缺：' + '、'.join(bad) if bad else 'import ok：' + '、'.join(need))
sys.exit(1 if bad else 0)
'@
$probeFile = Join-Path $DEST '_import_probe.py'
Set-Content -Path $probeFile -Value $probe -Encoding UTF8
& $WPY $probeFile
if ($LASTEXITCODE -ne 0) { Warn '技能脚本依赖导入自检没通过（见上面缺的库）' }
Remove-Item $probeFile -ErrorAction SilentlyContinue

Write-Host '== 5. 启动脚本（终端里手动跑用）=='
$runPs1 = Join-Path $DEST 'run.ps1'
@"
`$env:PLAYWRIGHT_BROWSERS_PATH = '$($env:PLAYWRIGHT_BROWSERS_PATH)'
`$env:SALON_WORKSPACE_ROOT = '$WORK'
`$env:PYTHONUTF8 = '1'
& '$VPY' '$DEST\server.py'
"@ | Set-Content -Path $runPs1 -Encoding UTF8
Ok $runPs1

Write-Host '== 6. 冒烟测试（只启动服务、列工具，不打开任何网站）=='
$smoke = Join-Path $KIT 'mcp\local\smoke_stdio.py'
& $PYEXE $smoke $VPY "$DEST\server.py"
if ($LASTEXITCODE -ne 0) { Die '1688_scraper 冒烟测试失败' }
foreach ($kb in @('selection-kb', 'ads-kb', 'promotion-kb')) {
    & $PYEXE $smoke $PYEXE (Join-Path $KIT "mcp\local\$kb\server.py") '--kb' (Join-Path $KIT "mcp\local\$kb\kb")
    if ($LASTEXITCODE -ne 0) { Warn "$kb 冒烟测试失败" }
}

Write-Host ''
Write-Host '== 7. 安装完成。在 OpenBot「你的应用」→「添加自定义项」逐个添加，传输方式选 STDIO =='
Write-Host '表单里「启动命令」只填程序本身，参数一个一个加；「工作目录」一律留空（填了只有 Claude / Codex 助手能用）。'
Write-Host ''
Write-Host '[1] 名称：1688_scraper'
Write-Host "    启动命令：$VPY"
Write-Host "    参数：$DEST\server.py"
Write-Host '    环境变量：PYTHONUTF8=1'
Write-Host "              PLAYWRIGHT_BROWSERS_PATH=$($env:PLAYWRIGHT_BROWSERS_PATH)"
Write-Host "              SALON_WORKSPACE_ROOT=$WORK"
Write-Host '    工具超时建议 300 秒'
foreach ($pair in @(@('selection_knowledge_base', 'selection-kb'), @('ads_knowledge_base', 'ads-kb'), @('promotion_knowledge_base', 'promotion-kb'))) {
    $n = if ($pair[0] -eq 'selection_knowledge_base') { 2 } elseif ($pair[0] -eq 'ads_knowledge_base') { 3 } else { 4 }
    Write-Host ''
    Write-Host "[$n] 名称：$($pair[0])"
    Write-Host "    启动命令：$PYEXE"
    Write-Host "    参数：$(Join-Path $KIT "mcp\local\$($pair[1])\server.py")"
    Write-Host '          --kb'
    Write-Host "          $(Join-Path $KIT "mcp\local\$($pair[1])\kb")"
    Write-Host '    环境变量：PYTHONUTF8=1'
}
Write-Host ''
Write-Host '每条填完点「测试连接」，看到「已连接 · N 个工具」再保存。'
Write-Host "技能脚本一律用 $WPY 运行，写进 $WORK\team.md。"
Write-Host "1688 第一次抓详情要老板本人扫码登录（login_1688），登录态留在 $DEST\pw_user_data，升级不丢。"