#!/usr/bin/env bash
# 星空亚马逊 Bot Team · 在 Grok Bot 云电脑上安装本地 MCP
#   1688_scraper（1688 抓取，需要 Python 依赖 + Chromium）
#   selection_knowledge_base / ads_knowledge_base / promotion_knowledge_base（知识库，纯 Python，不用装依赖）
# 可以重复运行：1688 登录态（pw_user_data）和已装的 Chromium 都会保留。
# 用法：bash /workspace/amazon-ops/kit/mcp/local/install_grokbot.sh
set -u
KIT="${KIT:-/workspace/amazon-ops/kit}"
WORK="${WORK:-/workspace/amazon-ops}"
DEST="$WORK/mcp/starsky-1688"          # 放在 kit 外面：以后升级团队包不会丢环境和登录态
export PLAYWRIGHT_BROWSERS_PATH="$DEST/ms-playwright"
ok() { echo "✅ $*"; }
warn() { echo "⚠️ $*"; }
die() { echo "❌ $*"; exit 1; }

echo "== 1. 检查 Python =="
command -v python3 >/dev/null || die "云电脑上没有 python3"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' || die "需要 Python 3.10 以上，当前 $(python3 -V)"
ok "$(python3 -V)"

echo "== 2. 准备 1688 MCP 目录 $DEST =="
mkdir -p "$DEST"
for f in server.py requirements.txt smoke_test.py; do cp "$KIT/mcp/local/1688/$f" "$DEST/" || die "缺文件 $f"; done
ok "server.py 已更新（登录态保留在 $DEST/pw_user_data）"

echo "== 3. 虚拟环境和依赖 =="
if [ ! -x "$DEST/.venv/bin/python" ]; then
  python3 -m venv "$DEST/.venv" || die "建虚拟环境失败。可能缺 python3-venv：sudo apt-get install -y python3-venv 后重跑"
fi
"$DEST/.venv/bin/python" -m pip install -q --upgrade pip >/dev/null 2>&1
"$DEST/.venv/bin/python" -m pip install -q -r "$DEST/requirements.txt" || die "pip 安装 mcp / playwright 失败（看上面的报错，多半是网络）"
ok "依赖已装"

echo "== 4. Chromium =="
# 先只下载浏览器本体（走 https，不碰 apt）；云电脑本来就装了 Chrome，系统库通常已经齐了
"$DEST/.venv/bin/python" -m playwright install chromium || die "Chromium 下载失败（看上面的报错，多半是网络）"
launch_test() {
  "$DEST/.venv/bin/python" - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    b.close()
print("chromium launch ok")
PY
}
if launch_test; then
  ok "Chromium 能启动（不需要补装系统库）"
else
  warn "Chromium 启动失败，可能缺系统库，尝试补装"
  if sudo -n true 2>/dev/null; then
    # 有的网络下 http 的 debian 源会报 500，先把源改成 https 再装
    for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do
      [ -f "$f" ] && sudo -n sed -i 's|http://deb.debian.org|https://deb.debian.org|g' "$f"
    done
    sudo -n "$DEST/.venv/bin/python" -m playwright install-deps chromium || warn "补装系统库失败（看上面的报错）"
  else
    warn "没有免密 sudo，没法补装系统库"
  fi
  launch_test || die "Chromium 还是启动不了。把上面的报错原文交给老板"
  ok "Chromium 能启动"
fi
if [ -n "${DISPLAY:-}" ]; then ok "图形显示 DISPLAY=$DISPLAY（1688 登录窗口会出现在这块屏幕上）"
else warn "当前终端没有 DISPLAY。1688_scraper 需要有界面的 Chromium：注册 MCP 后如果 login_1688 / get_offer 报显示相关错误，把报错交给老板"; fi

echo "== 4b. 技能脚本运行环境 $WORK/py =="
# 星空技能脚本（报告生成、成本模型、图片后处理等）的依赖，清单在 kit/scripts/requirements.txt
# 放在 kit 外面：升级团队包不用重装；技能脚本一律用 $WORK/py/bin/python 运行
if [ ! -x "$WORK/py/bin/python" ]; then
  python3 -m venv "$WORK/py" || die "建技能脚本虚拟环境失败。可能缺 python3-venv：sudo apt-get install -y python3-venv 后重跑"
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

echo "== 5. 启动脚本 =="
cat > "$DEST/run.sh" <<EOF
#!/usr/bin/env bash
export PLAYWRIGHT_BROWSERS_PATH="$DEST/ms-playwright"
export SALON_WORKSPACE_ROOT="$WORK"
export PYTHONUTF8=1
exec "$DEST/.venv/bin/python" "$DEST/server.py"
EOF
chmod +x "$DEST/run.sh"
ok "$DEST/run.sh"

echo "== 6. 冒烟测试（只启动服务、列工具，不打开任何网站）=="
python3 "$KIT/mcp/local/smoke_stdio.py" bash "$DEST/run.sh" || die "1688_scraper 冒烟测试失败"
for kb in selection-kb ads-kb promotion-kb; do
  python3 "$KIT/mcp/local/smoke_stdio.py" python3 "$KIT/mcp/local/$kb/server.py" --kb "$KIT/mcp/local/$kb/kb" || warn "$kb 冒烟测试失败"
done

cat <<EOF

== 7. 安装完成。请按下面 4 条添加「自定义 MCP 服务器」（by command）==
名称 1688_scraper              命令：bash $DEST/run.sh
名称 selection_knowledge_base  命令：python3 $KIT/mcp/local/selection-kb/server.py --kb $KIT/mcp/local/selection-kb/kb
名称 ads_knowledge_base        命令：python3 $KIT/mcp/local/ads-kb/server.py --kb $KIT/mcp/local/ads-kb/kb
名称 promotion_knowledge_base  命令：python3 $KIT/mcp/local/promotion-kb/server.py --kb $KIT/mcp/local/promotion-kb/kb
环境变量：PYTHONUTF8=1（四个都加）；1688_scraper 工具超时建议 300 秒
云电脑恢复（recovery）后如果 1688_scraper 起不来：重跑本脚本即可，登录态不丢。
EOF
