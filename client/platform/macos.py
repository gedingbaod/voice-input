"""macOS 平台实现。

依赖：
  - sounddevice（pip install sounddevice）
  - pynput（pip install pynput）
  - 系统命令：pbcopy, osascript（系统自带）

权限：
  - 首次运行需手动授予"辅助功能"（System Settings → Privacy & Security → Accessibility）
    给执行 python 的终端 / IDE，否则 pynput 收不到全局按键。
  - 首次录音会弹麦克风权限请求，正常同意即可。
"""
from __future__ import annotations

import subprocess
import sys
import threading
from typing import Callable

import numpy as np

from .base import AudioCapture, Hotkey, Injector, WindowProbe

# 16kHz mono float32, 30ms 帧
SAMPLERATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = SAMPLERATE * FRAME_MS // 1000  # 480

# Mac 终端类应用名关键词（osascript 拿到的 process name，小写匹配）
_TERMINAL_APP_KEYWORDS = (
    "terminal", "iterm", "warp", "alacritty", "kitty",
    "wezterm", "tilix", "terminator", "hyper", "tabby",
    "powershell", "securecrt",
)


def _run(cmd: list[str], timeout: float = 2.0, input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, timeout=timeout,
        check=False, input=input_bytes,
    )


# ---------- 热键 ----------

class MacHotkey:
    def __init__(self):
        self._listener = None  # pynput GlobalHotKeys 实例
        self._key: str | None = None
        self._cb: Callable[[], None] | None = None
        self._lock = threading.Lock()

    def register(self, key: str, on_press: Callable[[], None]) -> None:
        with self._lock:
            self._key = key
            self._cb = on_press
            if self._listener is not None:
                self._listener.stop()
            # GlobalHotKeys 接受 {key: callback} 字典
            self._listener = _GlobalHotKeys({key: on_press})
            self._listener.daemon = True
            self._listener.start()

    def run(self) -> None:
        # 已 start；这里阻塞直到 KeyboardInterrupt
        try:
            while True:
                threading.Event().wait(1.0)
        except KeyboardInterrupt:
            pass

    def __del__(self):
        try:
            if self._listener is not None:
                self._listener.stop()
        except Exception:
            pass


# 用一个轻子类避免顶层 import 时 pynput 报缺依赖；并便于测试 mock
try:
    from pynput import keyboard as _kb
    _GlobalHotKeys = _kb.GlobalHotKeys
except Exception as _e:  # pragma: no cover
    _GlobalHotKeys = None  # type: ignore
    _IMPORT_ERR = _e


# ---------- 录音 ----------

class MacAudio(AudioCapture):
    def __init__(self):
        self._stream = None
        self._cb: Callable[[np.ndarray], None] | None = None

    def start(self, on_frame: Callable[[np.ndarray], None]) -> None:
        import sounddevice as sd  # 局部 import，便于平台层单测时跳过
        self._cb = on_frame

        def _cb(indata, frames, t, status):
            if status:
                # 不致命，打一行就够
                print(f"[audio] {status}", file=sys.stderr)
            if self._cb is not None:
                # indata: (N, 1) float32
                self._cb(indata.copy().reshape(-1))

        self._stream = sd.InputStream(
            samplerate=SAMPLERATE,
            channels=1,
            dtype="float32",
            blocksize=FRAME_SAMPLES,
            callback=_cb,
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None
                self._cb = None


# ---------- 注入 ----------

class MacInjector(Injector):
    """写剪贴板 + 发 Cmd+V。Mac 终端（Terminal/iTerm2/Warp 等）都支持 Cmd+V。"""

    # osascript 错误 1002 = "not authorized to send keystroke"
    # 通常因为 python 进程没拿到"辅助功能"权限
    _ERR_NOT_AUTHORIZED = b"1002"

    def inject(self, text: str) -> None:
        if not text:
            return
        try:
            # 1. 写剪贴板（不需要任何权限）
            r = _run(["pbcopy"], input_bytes=text.encode("utf-8"), timeout=2.0)
            if r.returncode != 0:
                print(f"[inject] pbcopy failed: {r.stderr}", file=sys.stderr)
                return
            # 2. 发 Cmd+V（需要"辅助功能"权限）
            script = (
                'tell application "System Events" '
                'to keystroke "v" using command down'
            )
            r = _run(["osascript", "-e", script], timeout=2.0)
            if r.returncode != 0:
                err = r.stderr or b""
                if self._ERR_NOT_AUTHORIZED in err:
                    print(
                        "[inject] 剪贴板已写入，但无法发送按键 (错误 1002)。\n"
                        "         请到 系统设置 → 隐私与安全 → 辅助功能，"
                        "把你运行 python 的应用（Terminal / iTerm / VSCode 等）\n"
                        "         加入允许列表，然后重启客户端。\n"
                        "         文本已落到剪贴板，可手动 Cmd+V 验证。",
                        file=sys.stderr,
                    )
                else:
                    print(f"[inject] osascript failed: {err}", file=sys.stderr)
        except subprocess.TimeoutExpired:
            print("[inject] timeout", file=sys.stderr)
        except FileNotFoundError as e:
            print(f"[inject] missing tool: {e}", file=sys.stderr)


# ---------- 焦点窗口探测 ----------

class MacWindowProbe(WindowProbe):
    """用 osascript 拿当前最前应用名。"""

    def is_terminal(self) -> bool:
        try:
            # 'System Events' -> first application process whose frontmost is true
            script = (
                'tell application "System Events" to get name of '
                '(first application process whose frontmost is true)'
            )
            r = _run(["osascript", "-e", script], timeout=1.5)
            if r.returncode != 0 or not r.stdout:
                return False
            name = r.stdout.strip().lower()
            return any(k in name for k in _TERMINAL_APP_KEYWORDS)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False
