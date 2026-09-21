#!/usr/bin/env bash
# 安装/卸载 voice-input 的 macOS 开机自启（LaunchAgent）
#
#   ./install.sh    安装并启动（登录后自动运行）
#   ./install.sh -u 卸载（停止 + 移除）
#
# 架构：launchd 直接跑 python -m client（不经 App bundle）。
#
# 权限说明（2026-09-21 排查结论）：
#   - 辅助功能（F9 热键）：把 python 解释器本身加入 系统设置→隐私与安全性→
#     辅助功能 即可；首次启动日志出现 "not trusted" 时按提示添加。
#   - 麦克风：首次按 F9 录音时弹窗授权即可。
#   - 为什么不用 App bundle：经 App bundle 启动会导致菜单栏 NSStatusItem
#     图标不可见（accessory 上下文问题）；直跑 python 图标/热键/录音全正常。
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
LABEL="com.voice-input.client"
PLIST_SRC="$HERE/com.voice-input.client.plist"
PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/voice-input.log"

PY="$(command -v python3)"

if [ "${1:-}" = "-u" ]; then
    echo "[uninstall] 停止并移除 ${LABEL}"
    launchctl unload "$PLIST_DST" 2>/dev/null || true
    rm -f "$PLIST_DST"
    pkill -f "python3 -u -m client" 2>/dev/null || true
    echo "[uninstall] 完成（日志保留在 ${LOG}）"
    exit 0
fi

echo "[install] python   : $PY"
echo "[install] 项目目录 : $ROOT"

mkdir -p "$(dirname "$PLIST_DST")" "$(dirname "$LOG")"

# 生成 launchd plist（填入本机 python / 项目 / home 路径）
sed -e "s|@PY@|$PY|g" -e "s|@ROOT@|$ROOT|g" -e "s|@HOME@|$HOME|g" \
    "$PLIST_SRC" > "$PLIST_DST"

# 幂等加载：先停旧实例再重载
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

⚠ 首次权限（仅一次）：
  1) 辅助功能：若日志出现 "not trusted"，到 系统设置 → 隐私与安全性 →
     辅助功能 → 点 + → 添加 python 解释器:
       $PY
     然后重启服务: launchctl kickstart -k gui/$(id -u)/$LABEL
  2) 麦克风：首次按 F9 录音会弹窗，点允许。
EOF
