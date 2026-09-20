"""macOS 平台实现。

组件：
  - MacHotkey        pynput GlobalHotKeys（需"辅助功能"权限）
  - MacAudio         继承 SharedSoundDeviceAudio（sounddevice 三平台通用）
  - MacInjector      pbcopy 写剪贴板 + osascript 发 Cmd+V
  - MacWindowProbe   osascript 查前台应用名判断是否终端

权限（首次运行，详见 README）：
  1. 系统设置 → 隐私与安全 → 辅助功能 → 允许运行 python 的 App
  2. 麦克风权限弹窗 → 允许
"""
from __future__ import annotations

import subprocess
import sys
import threading
from typing import Callable

from .base import (
    BaseAudioCapture,
    BaseHotkey,
    BaseInjector,
    BasePlatform,
    BaseWindowProbe,
    SharedSoundDeviceAudio,
)

# Mac 终端类应用名关键词（osascript 返回 process name，小写匹配）
_TERMINAL_APP_KEYWORDS = (
    "terminal", "iterm", "warp", "alacritty", "kitty",
    "wezterm", "tilix", "terminator", "hyper", "tabby",
    "powershell", "securecrt",
)


def _run(cmd: list[str], timeout: float = 2.0,
         input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, timeout=timeout, check=False,
        input=input_bytes,
    )


# ---------- 热键 ----------

class MacHotkey(BaseHotkey):
    """pynput GlobalHotKeys 包装。macOS 需辅助功能权限。"""

    def __init__(self):
        self._listener = None
        self._lock = threading.Lock()

    def register(self, key: str, on_press: Callable[[], None]) -> None:
        from pynput import keyboard  # 延迟 import，错误信息更友好
        with self._lock:
            if self._listener is not None:
                self._listener.stop()
            self._listener = keyboard.GlobalHotKeys({key: on_press})
            self._listener.daemon = True
            self._listener.start()

    def run(self) -> None:
        try:
            while True:
                threading.Event().wait(1.0)
        except KeyboardInterrupt:
            pass


# ---------- 录音 ----------

class MacAudio(SharedSoundDeviceAudio):
    """macOS 无特殊需求，直接用 sounddevice 共享实现（会弹麦克风授权）。"""


# ---------- 注入 ----------

class MacInjector(BaseInjector):
    """pbcopy 写剪贴板 + osascript 发 Cmd+V。
    Mac 所有终端（Terminal/iTerm2/Warp/…）都支持 Cmd+V，无需分支。"""

    # osascript 错误 1002 = "not authorized to send keystroke"
    _ERR_NOT_AUTHORIZED = b"1002"

    def inject(self, text: str) -> None:
        if not text:
            return
        try:
            # 1. 写剪贴板（不需要权限）
            r = _run(["pbcopy"], input_bytes=text.encode("utf-8"), timeout=2.0)
            if r.returncode != 0:
                print(f"[inject] pbcopy failed: {r.stderr}", file=sys.stderr)
                return
            # 2. 发 Cmd+V（需要辅助功能权限）
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
                        "         系统设置 → 隐私与安全 → 辅助功能 → "
                        "允许运行 python 的 App，然后重启客户端。",
                        file=sys.stderr,
                    )
                else:
                    print(f"[inject] osascript failed: {err}", file=sys.stderr)
        except subprocess.TimeoutExpired:
            print("[inject] timeout", file=sys.stderr)
        except FileNotFoundError as e:
            print(f"[inject] missing tool: {e}", file=sys.stderr)


# ---------- 焦点窗口探测 ----------

class MacWindowProbe(BaseWindowProbe):
    """osascript 拿前台应用名。"""

    def is_terminal(self) -> bool:
        try:
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


# ---------- 平台聚合 ----------

class MacPlatform(BasePlatform):
    """macOS。feedback 用 rumps 菜单栏 + 系统音 + 通知（见 macos_feedback.py）。"""

    name = "macos"

    def create_hotkey(self) -> BaseHotkey:
        return MacHotkey()

    def create_audio(self) -> BaseAudioCapture:
        return MacAudio()

    def create_injector(self) -> BaseInjector:
        return MacInjector()

    def create_window_probe(self) -> BaseWindowProbe:
        return MacWindowProbe()

    def create_feedback(self, sound: bool = True, notify: bool = True):
        from .macos_feedback import MacFeedback  # 延迟：rumps 可选依赖
        return MacFeedback(sound=sound, notify=notify)

    @property
    def ui_loop(self):
        # rumps 菜单栏接管主线程；feedback 未初始化时走 None（app 会兜底）
        fb = getattr(self, "_feedback", None)
        if fb is not None and hasattr(fb, "run_forever"):
            return fb.run_forever
        return None
