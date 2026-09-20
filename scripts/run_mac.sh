#!/usr/bin/env bash
# voice-input Mac 启动脚本
#
# 直接运行：./scripts/run_mac.sh
# 调试模式：./scripts/run_mac.sh --debug
#
# 首次运行需要：
#   1. 系统设置 → 隐私与安全 → 辅助功能 → 允许你运行 python 的应用（Terminal/iTerm/VSCode 等）
#   2. 麦克风权限会首次录音时弹出，正常同意即可
#
# 指向的 ASR 服务端（可覆盖）：
#   export VOICE_INPUT_BASE_URL=http://192.168.31.54:9999/v1
#   export VOICE_INPUT_API_KEY=123456

set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

# 默认 python：项目无独立 venv，复用 ~/work/test/.venv（与 Linux 端一致）；
# 如未装，可改用系统 python3 + pip install -r requirements.txt
PY="${VOICE_INPUT_PY:-${VOICE_INPUT_VENV:-$HOME/work/test/.venv}/bin/python}"
if [ ! -x "$PY" ]; then
  PY="$(command -v python3)"
fi

if [ -z "${PY:-}" ] || [ ! -x "$PY" ]; then
  echo "[run_mac] python3 not found" >&2
  exit 1
fi

# 检查关键依赖（缺则尝试装）
for mod in sounddevice numpy pynput openai; do
  if ! "$PY" -c "import $mod" 2>/dev/null; then
    echo "[run_mac] missing dependency: $mod" >&2
    echo "[run_mac] installing: $mod" >&2
    "$PY" -m pip install --user "$mod" || {
      echo "[run_mac] pip install $mod failed; please install manually" >&2
      exit 1
    }
  fi
done

cd "$ROOT"
exec "$PY" -u -m client "$@"
