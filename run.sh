#!/usr/bin/env bash
# voice_input 语音识别输入法启动脚本。
# 由 voice-input.service 调用；也可以直接手动运行。

set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV="${VOICE_INPUT_VENV:-$HOME/work/test/.venv}"
PY="$VENV/bin/python"

if [ ! -x "$PY" ]; then
    echo "[voice_input] python not found at: $PY" >&2
    exit 1
fi

# 确保 xdotool / xclip 能找到 X server：
# systemd --user 环境下 DISPLAY / XAUTHORITY 可能没被继承，兜底补一下
: "${DISPLAY:=:1}"
: "${XAUTHORITY:=$HOME/.Xauthority}"
export DISPLAY XAUTHORITY

# 用户自定义参数走 VOICE_INPUT_ARGS，比如：
#   export VOICE_INPUT_ARGS="--model sensevoice --sil-ms 400 --hotkey <f9>"
ARGS=${VOICE_INPUT_ARGS:---model sensevoice --sil-ms 400}

cd "$HERE"
exec "$PY" -u asr_hotkey.py $ARGS
