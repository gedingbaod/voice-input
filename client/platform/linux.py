"""Linux(X11) 平台实现 —— 从旧版 asr_hotkey.py 迁移。

状态：✅ 已实现（2026-09-20 从原 asr_hotkey.py 逐函数迁移）。

组件：
  - LinuxHotkey       pynput GlobalHotKeys（X11）
  - LinuxAudio        继承 SharedSoundDeviceAudio
  - LinuxInjector     xclip 双写 CLIPBOARD+PRIMARY + xdotool 按键
  - LinuxWindowProbe  xdotool getactivewindow + xprop WM_CLASS
  - LinuxFeedback     铃声（canberra-gtk-play / paplay）+ print 兜底

系统依赖：xdotool xclip x11-utils（xprop）；仅 X11，Wayland 下不工作。

已知坑（迁移自旧版注释）：
  - systemd --user 环境需补 DISPLAY / XAUTHORITY（run.sh 已处理）
  - 终端窗口发 shift+Insert；GUI 发 ctrl+v
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from typing import Callable

from .base import (
    BaseAudioCapture,
    BaseFeedback,
    BaseHotkey,
    BaseInjector,
    BasePlatform,
    BaseWindowProbe,
    PrintFeedback,
    SharedSoundDeviceAudio,
)

# X11 终端 WM_CLASS 关键词（xprop 输出小写匹配）
_TERMINAL_CLASS_KEYWORDS = (
    "terminal", "term", "konsole", "alacritty", "kitty",
    "wezterm", "tilix", "terminator", "xterm", "rxvt",
    "urxvt", "st-256color", "foot", "hyper", "tabby",
)


def _run(cmd: list[str], timeout: float = 2.0,
         input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, timeout=timeout, check=False,
        input=input_bytes,
    )


# ---------- 热键 ----------

class LinuxHotkey(BaseHotkey):
    """pynput GlobalHotKeys（X11 后端）。"""

    def __init__(self):
        self._listener = None
        self._lock = threading.Lock()

    def register(self, key: str, on_press: Callable[[], None]) -> None:
        from pynput import keyboard
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

class LinuxAudio(SharedSoundDeviceAudio):
    """无特殊需求，sounddevice 共享实现。"""


# ---------- 注入 ----------

class LinuxInjector(BaseInjector):
    """xclip 双写剪贴板 + xdotool 模拟按键。
    终端 → Shift+Insert（X11 PRIMARY 传统）；GUI → Ctrl+V。"""

    def __init__(self, window_probe: BaseWindowProbe | None = None):
        # 注入时需要探测目标窗口类型；由平台聚合注入，避免循环依赖
        self._probe = window_probe

    def inject(self, text: str) -> None:
        if not text:
            return
        try:
            # 双写：CLIPBOARD（Ctrl+V 用）+ PRIMARY（Shift+Insert 用）
            for sel in ("clipboard", "primary"):
                subprocess.run(
                    ["xclip", "-selection", sel],
                    input=text.encode("utf-8"), check=True, timeout=2.0,
                    capture_output=True,
                )
            # 按目标窗口类型选粘贴键
            is_term = bool(self._probe and self._probe.is_terminal())
            key = "shift+Insert" if is_term else "ctrl+v"
            subprocess.run(
                ["xdotool", "key", "--clearmodifiers", key],
                check=True, timeout=2.0, capture_output=True,
            )
        except FileNotFoundError as e:
            print(f"[inject] missing tool: {e}（需 xdotool xclip）",
                  file=sys.stderr)
        except subprocess.SubprocessError as e:
            print(f"[inject] failed: {e}", file=sys.stderr)


# ---------- 焦点窗口探测 ----------

class LinuxWindowProbe(BaseWindowProbe):
    """xdotool getactivewindow + xprop WM_CLASS。失败返回 False。"""

    def is_terminal(self) -> bool:
        try:
            wid = subprocess.run(
                ["xdotool", "getactivewindow"],
                capture_output=True, text=True, timeout=1.5, check=True,
            ).stdout.strip()
            if not wid:
                return False
            r = subprocess.run(
                ["xprop", "-id", wid, "WM_CLASS"],
                capture_output=True, text=True, timeout=1.5, check=True,
            )
            cls = r.stdout.lower()
            return any(k in cls for k in _TERMINAL_CLASS_KEYWORDS)
        except (subprocess.SubprocessError, FileNotFoundError):
            return False


# ---------- 反馈 ----------

class LinuxFeedback(PrintFeedback):
    """Linux 提示音：优先 canberra-gtk-play（GNOME/KDE 都带），
    其次 paplay + freedesktop 标准音；都没有则纯文本（继承 PrintFeedback）。
    通知：notify-send（可选）。"""

    _SND = {"start": "bell", "stop": "button-toggle-on",
            "result": "bell", "error": "dialog-error"}

    def __init__(self, sound: bool = True, notify: bool = True):
        self.sound = sound
        self.notify = notify
        self._player = self._pick_player()

    @staticmethod
    def _pick_player() -> list[str] | None:
        for cmd in (["canberra-gtk-play", "-i"], ["paplay"]):
            if shutil.which(cmd[0]):
                return cmd
        return None

    def _beep(self, kind: str) -> None:
        if not (self.sound and self._player):
            return
        try:
            if self._player[0] == "canberra-gtk-play":
                subprocess.Popen(
                    self._player + [self._SND[kind]],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:  # paplay
                subprocess.Popen(
                    self._player + [f"/usr/share/sounds/freedesktop/stereo/"
                                    f"{self._SND[kind]}.oga"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:  # noqa: BLE001
            pass

    def _note(self, title: str, body: str) -> None:
        if not self.notify:
            return
        if shutil.which("notify-send"):
            try:
                subprocess.Popen(
                    ["notify-send", "-a", title, body[:80]],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:  # noqa: BLE001
                pass

    def on_recording_start(self) -> None:
        super().on_recording_start()
        self._beep("start")

    def on_recording_stop(self) -> None:
        super().on_recording_stop()
        self._beep("stop")

    def on_result(self, text: str) -> None:
        super().on_result(text)
        self._beep("result")
        self._note("语音输入", text)

    def on_error(self, msg: str) -> None:
        super().on_error(msg)
        self._beep("error")
        self._note("语音输入出错", msg)


# ---------- 平台聚合 ----------

class LinuxPlatform(BasePlatform):
    """Linux X11。"""

    name = "linux"

    def create_hotkey(self) -> BaseHotkey:
        return LinuxHotkey()

    def create_audio(self) -> BaseAudioCapture:
        return LinuxAudio()

    def create_window_probe(self) -> BaseWindowProbe:
        return LinuxWindowProbe()

    def create_injector(self) -> BaseInjector:
        # 注入器持有窗口探测（决定 Shift+Insert vs Ctrl+V）
        return LinuxInjector(window_probe=self.window)

    def create_feedback(self, sound: bool = True, notify: bool = True) -> BaseFeedback:
        return LinuxFeedback(sound=sound, notify=notify)
