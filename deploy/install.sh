#!/usr/bin/env bash
# 安装/卸载 voice-input 的 macOS LaunchAgent（登录自启）
#
#   ./install.sh    安装并启动（登录后自动运行）
#   ./install.sh -u 卸载（停止 + 移除）
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.voice-input.client"
PLIST_SRC="$HERE/com.voice-input.client.plist"
PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/voice-input.log"

# 换成当前机器的实际路径（python 解释器 + 项目目录）
PY="$(command -v python3)"
ROOT="$(cd "$HERE/.." && pwd)"
mkdir -p "$(dirname "$PLIST_DST")" "$(dirname "$LOG")"

if [ "${1:-}" = "-u" ]; then
    echo "[uninstall] 停止并移除 $LABEL"
    launchctl unload "$PLIST_DST" 2>/dev/null || true
    rm -f "$PLIST_DST"
    echo "[uninstall] 完成（日志保留在 ${LOG}）"
    exit 0
fi

echo "[install] python   : $PY"
echo "[install] 项目目录 : $ROOT"
# 用 sed 把 plist 里的绝对路径替换成当前机器的实际值
sed -e "s|/opt/homebrew/Caskroom/miniconda/base/bin/python3|$PY|g" \
    -e "s|/Users/zzfoo/SRC/voice-input|$ROOT|g" \
    "$PLIST_SRC" > "$PLIST_DST"

# 已加载过则先卸载再重载（幂等安装）
launchctl unload "$PLIST_DST" 2>/dev/null || true
launchctl load "$PLIST_DST"
sleep 2

# 验证
if launchctl list | grep -q "$LABEL"; then
    echo "[install] ✓ 已启动（登录后自启；日志: tail -f ${LOG}）"
else
    echo "[install] ✗ 启动失败，查看日志: $LOG" >&2
    exit 1
fi

cat <<'EOF'

⚠ 权限提示（仅首次需要）：
LaunchAgent 启动的进程权限挂在 python 可执行文件本身。若 F9 无响应/无法粘贴：
  系统设置 → 隐私与安全 → 辅助功能 → 添加:
    /opt/homebrew/Caskroom/miniconda/base/bin/python3
  （Finder 按 Cmd+Shift+G 输入路径定位；或先卸载 launchd，从终端跑一次让系统弹窗授权）
麦克风同理：隐私与安全 → 麦克风。
EOF
