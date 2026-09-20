"""平台抽象接口。

每个 OS 实现一个具体类，提供：
  - Hotkey：全局热键监听（F9）
  - AudioCapture：麦克风录音 → 30ms float32 帧
  - Injector：把识别好的文本送到当前光标位置（剪贴板 + 模拟粘贴）
  - WindowProbe：探测当前焦点窗口是不是终端（决定注入方式）

原则：
  - 平台无关的逻辑（VAD、ASR 调用）放在 client.core / client.asr_client
  - 仅"和 OS 交互"的部分放在 platform 包里
  - 接口尽量小而清晰，方便后续加平台
"""
from __future__ import annotations

from typing import Callable, Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class Hotkey(Protocol):
    """全局热键。回调里不要做阻塞操作。"""

    def register(self, key: str, on_press: Callable[[], None]) -> None:
        """注册单个全局热键（如 '<f9>'）。同对象可重复调用覆盖。"""
        ...

    def run(self) -> None:
        """阻塞直到 KeyboardInterrupt。"""
        ...


@runtime_checkable
class AudioCapture(Protocol):
    """16kHz mono float32 录音。每次 start 会重开一个流。"""

    def start(self, on_frame: Callable[[np.ndarray], None]) -> None:
        """开始录音。每收到一帧（30ms / 480 samples）调用 on_frame(frame)。"""
        ...

    def stop(self) -> None:
        """停止录音。回调不再触发。"""
        ...


@runtime_checkable
class Injector(Protocol):
    """把文本送到当前光标位置。

    通常做法：写剪贴板 + 模拟 Ctrl+V（或 Cmd+V）。
    """

    def inject(self, text: str) -> None:
        """写入剪贴板并触发粘贴。失败不抛异常，只打日志。"""
        ...


@runtime_checkable
class WindowProbe(Protocol):
    """判断当前焦点窗口是否是终端。"""

    def is_terminal(self) -> bool:
        """True 表示当前窗口是终端类应用。失败返回 False。"""
        ...


@runtime_checkable
class Feedback(Protocol):
    """用户反馈（提示音 / 菜单栏状态 / 通知）。所有方法不得阻塞。"""

    def on_recording_start(self) -> None: ...
    def on_recording_stop(self) -> None: ...
    def on_result(self, text: str) -> None: ...
    def on_error(self, msg: str) -> None: ...


class Platform:
    """一组平台实现，工厂方法返回。"""

    def __init__(
        self,
        hotkey: Hotkey,
        audio: AudioCapture,
        injector: Injector,
        window: WindowProbe,
        name: str,
        feedback: Feedback | None = None,
        ui_loop=None,  # Callable[[], None]：占住主线程的 UI 主循环（如 rumps）；None 用 hotkey.run()
    ):
        self.hotkey = hotkey
        self.audio = audio
        self.injector = injector
        self.window = window
        self.name = name
        self.feedback = feedback
        self.ui_loop = ui_loop
