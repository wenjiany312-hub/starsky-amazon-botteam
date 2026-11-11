#!/usr/bin/env bash
# 星空亚马逊 Bot Team · 在本机（macOS / Linux）安装本地 MCP —— OpenBot / StarSky 用
#   1688_scraper（1688 抓取，需要 Python 依赖 + Chromium）
#   selection_knowledge_base / ads_knowledge_base / promotion_knowledge_base（知识库，纯标准库）
# 可以重复运行：1688 登录态（pw_user_data）和已下载的 Chromium 都会保留。
# 不用 sudo，不装系统软件包。
# 用法：bash <kit>/mcp/local/install_local.sh
#   环境变量：KIT（团队包目录）、WORK（工作目录）、STARSKY_1688_DIR（复用已有的 1688 安装目录）
set -u

SELF="$(cd "$(dirname "$0")" && pwd)"
KIT="${KIT:-$(cd "$SELF/../.." && pwd)}"
WORK="${WORK:-$(cd "$KIT/.." && pwd)/work}"
DEST="${STARSKY_1688_DIR:-$WORK/mcp/starsky-1688}"   # 放在 kit 外面：升级团队包不丢环境和登录态
export PLAYWRIGHT_BROWSERS_PATH="$DEST/ms-playwright"
ok()   { echo "✅ $*"; }
warn() { echo "⚠️ $*"; }
die()  { echo "❌ $*"; exit 1; }

echo "== 0. 路径 =="
echo "   团队包 KIT  = $KIT"
echo "   工作目录 WORK = $WORK"
[ -d "$KIT/mcp/local/1688" ] || die "KIT 指错了：$KIT 下面没有 mcp/local/1688"
mkdir -p "$WORK" || die "工作目录建不了：$WORK"

echo "== 1. 检查 Python =="
command -v python3 >/dev/null || die "本机没有 python3。先装 Python 3.10 以上（macOS：python.org 安装包或 brew install python）"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' || die "需要 Python 3.10 以上，当前 $(python3 -V)"
PY3="$(command -v python3)"
ok "$("$PY3" -V)（$PY3）"

echo "== 1b. 看看本机是不是已经装过 1688 MCP =="
EXISTING="$HOME/.starsky-claude/mcp/1688"
if [ -z "${STARSKY_1688_DIR:-}" ] && [ -f "$EXISTING/server.py" ]; then
  warn "星空亚马逊 Agent 已经在本机装过 1688 MCP：$EXISTING"
  if [ -d "$EXISTING/pw_user_data" ]; then
    warn "那里还留着 1688 登录态（pw_user_data）。想复用、不想重新扫码登录，就改用："
    echo "     STARSKY_1688_DIR=\"$EXISTING\" bash \"$SELF/install_local.sh\""
    warn "本次仍按新目录安装，两边互不影响；也可以直接在 OpenBot 里注册上面那个已有目录。"
  fi
fi

echo "== 2. 准备 1688 MCP 目录 $DEST =="
mkdir -p "$DEST" || die "建不了 $DEST"
for f in server.py requirements.txt smoke_test.py; do
  cp "$KIT/mcp/local/1688/$f" "$DEST/" || die "缺文件 $f"
done
ok "server.py 已更新（登录态保留在 $DEST/pw_user_data）"

echo "== 3. 虚拟环境和依赖 =="
if [ ! -x "$DEST/.venv/bin/python" ]; then
  "$PY3" -m venv "$DEST/.venv" || die "建虚拟环境失败"
fi
VPY="$DEST/.venv/bin/python"
"$VPY" -m pip install -q --upgrade pip >/dev/null 2>&1
"$VPY" -m pip install -q -r "$DEST/requirements.txt" || die "pip 安装 mcp / playwright 失败（看上面的报错，多半是网络）"
ok "依赖已装"

echo "== 4. Chromium =="
# 先只下载浏览器本体；本机一般已经有图形环境，不需要补装系统库，也不用 sudo
"$VPY" -m playwright install chromium || die "Chromium 下载失败（看上面的报错，多半是网络）"
launch_test() {
  "$VPY" - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    b.close()
print("chromium launch ok")
PY
}
if launch_test; then
  ok "Chromium 能启动"
else
  warn "Chromium 起不来，再试一次有界面的启动（本机装的是桌面系统，通常这样就能用）"
  "$VPY" - <<'PY' || die "Chromium 还是启动不了。把上面的报错原文交给老板；本脚本不用 sudo，也不会替你装系统软件包"
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=False)
    b.close()
print("chromium headed launch ok")
PY
  ok "Chromium 能启动（有界面）"
fi

echo "== 4b. 技能脚本运行环境 $WORK/py =="
# 星空技能脚本（报告生成、成本模型、图片后处理等）的依赖，清单在 kit/scripts/requirements.txt
if [ ! -x "$WORK/py/bin/python" ]; then
  "$PY3" -m venv "$WORK/py" || die "建技能脚本虚拟环境失败"
fi
"$WORK/py/bin/python" -m pip install -q --upgrade pip >/dev/null 2>&1
if "$WORK/py/bin/python" -m pip install -q -r "$KIT/scripts/requirements.txt"; then
  ok "技能脚本依赖已装（$WORK/py/bin/python）"
else
  warn "技能脚本依赖没装全（看上面的报错，多半是网络）。知识库和 1688 不受影响；报告生成类脚本会跑不起来，网络好了重跑本脚本"
fi
"$WORK/py/bin/python" - <<'PY' || warn "技能脚本依赖导入自检没通过（见上面缺的库）"
import importlib.util, sys
need = ['yaml', 'openpyxl', 'PIL', 'bs4', 'requests', 'Crypto', 'numpy', 'pandas', 'fitz', 'jsonschema', 'cv2', 'rapidocr']
bad = [m for m in need if importlib.util.find_spec(m) is None]
print('缺：' + '、'.join(bad) if bad else 'import ok：' + '、'.join(need))
sys.exit(1 if bad else 0)
PY

echo "== 5. 启动脚本（终端里手动跑用）=="
cat > "$DEST/run.sh" <<EOF
#!/usr/bin/env bash
export PLAYWRIGHT_BROWSERS_PATH="$DEST/ms-playwright"
export SALON_WORKSPACE_ROOT="$WORK"
export PYTHONUTF8=1
exec "$VPY" "$DEST/server.py"
EOF
chmod +x "$DEST/run.sh"
ok "$DEST/run.sh"

echo "== 6. 冒烟测试（只启动服务、列工具，不打开任何网站）=="
"$PY3" "$KIT/mcp/local/smoke_stdio.py" bash "$DEST/run.sh" || die "1688_scraper 冒烟测试失败"
for kb in selection-kb ads-kb promotion-kb; do
  "$PY3" "$KIT/mcp/local/smoke_stdio.py" "$PY3" "$KIT/mcp/local/$kb/server.py" --kb "$KIT/mcp/local/$kb/kb" || warn "$kb 冒烟测试失败"
done

cat <<EOF

== 7. 安装完成。在 OpenBot「你的应用」→「添加自定义项」逐个添加，传输方式选 STDIO ==
表单里「启动命令」只填程序本身，参数一个一个加；「工作目录」一律留空（填了只有 Claude / Codex 助手能用）。

[1] 名称：1688_scraper
    启动命令：$VPY
    参数：$DEST/server.py
    环境变量：PYTHONUTF8=1
              PLAYWRIGHT_BROWSERS_PATH=$DEST/ms-playwright
              SALON_WORKSPACE_ROOT=$WORK
    工具超时建议 300 秒

[2] 名称：selection_knowledge_base
    启动命令：$PY3
    参数：$KIT/mcp/local/selection-kb/server.py
          --kb
          $KIT/mcp/local/selection-kb/kb
    环境变量：PYTHONUTF8=1

[3] 名称：ads_knowledge_base
    启动命令：$PY3
    参数：$KIT/mcp/local/ads-kb/server.py
          --kb
          $KIT/mcp/local/ads-kb/kb
    环境变量：PYTHONUTF8=1

[4] 名称：promotion_knowledge_base
    启动命令：$PY3
    参数：$KIT/mcp/local/promotion-kb/server.py
          --kb
          $KIT/mcp/local/promotion-kb/kb
    环境变量：PYTHONUTF8=1

每条填完点「测试连接」，看到「已连接 · N 个工具」再保存。
技能脚本一律用 $WORK/py/bin/python 运行，写进 $WORK/team.md。
1688 第一次抓详情要老板本人扫码登录（login_1688），登录态留在 $DEST/pw_user_data，升级不丢。
EOF