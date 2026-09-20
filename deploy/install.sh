#!/usr/bin/env bash
# 安装/卸载 voice-input 的 macOS 开机自启（LaunchAgent）
#
#   ./install.sh    安装并启动（登录后自动运行）
#   ./install.sh -u 卸载（停止 + 移除）
#
# 架构：launchd → /usr/bin/open -W -a VoiceInput.app → 编译的启动器(Mach-O)
#       → python -m client
# 关键点：
#   1. App 必须经 open(LaunchServices) 拉起，权限才能关联
#   2. 主可执行文件是编译出来的 Mach-O 并做 ad-hoc 签名 ——
#      裸 python/shell 无法通过 macOS 辅助功能信任
#   3. python 路径等配置在 Contents/Resources/launcher.conf，
#      改配置不需要重新编译/签名（签名一变授权就失效！）
#   4. 只有改了 deploy/src/launcher.c 才需要重新编译（--rebuild）
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
LABEL="com.voice-input.client"
APP="$HERE/VoiceInput.app"
PLIST_SRC="$HERE/com.voice-input.client.plist"
PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/voice-input.log"

PY="$(command -v python3)"

if [ "${1:-}" = "-u" ]; then
    echo "[uninstall] 停止并移除 ${LABEL}"
    launchctl unload "$PLIST_DST" 2>/dev/null || true
    rm -f "$PLIST_DST"
    echo "[uninstall] 完成（日志保留在 ${LOG}）"
    exit 0
fi

echo "[install] python   : $PY"
echo "[install] App      : $APP"

mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" \
         "$(dirname "$PLIST_DST")" "$(dirname "$LOG")"

# 1) 写配置（可随时改，不影响签名）
cat > "$APP/Contents/Resources/launcher.conf" <<CONF
$PY
$ROOT
$HOME/Library/Logs/voice-input.log
CONF

# 2) 编译启动器（仅当需要时）
BIN="$APP/Contents/MacOS/VoiceInput"
if [ "${1:-}" = "--rebuild" ] || [ ! -x "$BIN" ]; then
    echo "[install] 编译启动器（需要 Xcode Command Line Tools）"
    xcrun clang -O2 -o "$BIN" "$HERE/src/launcher.c" || {
        echo "[install] ✗ 编译失败（装 CLT: xcode-select --install）" >&2
        exit 1
    }
    # 3) ad-hoc 签名（签名后不要再随便重编译！会使辅助功能授权失效）
    codesign -f -s - "$APP" >/dev/null 2>&1 || echo "[install] ⚠ codesign 失败（继续，但辅助功能可能不生效）"
else
    echo "[install] 启动器已存在（改 launcher.c 后用 --rebuild 重新编译）"
fi

# 4) launchd plist（经 open 拉起 App）
sed -e "s|@APP@|$APP|g" -e "s|@ROOT@|$ROOT|g" -e "s|@HOME@|$HOME|g" \
    "$PLIST_SRC" > "$PLIST_DST"

# 5) 幂等加载
pkill -f "python3 -u -m client" 2>/dev/null || true
launchctl unload "$PLIST_DST" 2>/dev/null || true
launchctl load "$PLIST_DST"
sleep 3

if launchctl list | grep -q "$LABEL"; then
    echo "[install] ✓ 已启动（登录后自启；日志: tail -f ${LOG}）"
else
    echo "[install] ✗ 启动失败，查看日志: ${LOG}" >&2
    exit 1
fi

cat <<EOF

⚠ 权限（仅首次；换 launcher.c 重编译后要重做）：
  系统设置 → 隐私与安全性 → 辅助功能 → 若列表里已有 VoiceInput/语音输入，
  先删掉旧条目再重新添加（路径选本 App）→ 打开开关。
  然后重启服务: launchctl kickstart -k gui/$(id -u)/$LABEL
EOF
