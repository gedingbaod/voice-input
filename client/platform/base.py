"""平台抽象基类与跨平台共享实现。

设计原则（改动前必读）：
  1. **核心与平台解耦**：client/core.py（录音状态机）与 client/app.py（主流程）
     只依赖本文件定义的抽象基类，绝不 import 任何具体平台模块。
  2. **共享实现下沉**：三平台行为一致的逻辑（sounddevice 录音、阻塞式
     主循环、纯文本反馈）放在本文件，平台子类直接继承，避免重复代码。
  3. **平台差异隔离**：每个 OS 一个文件（linux.py / macos.py / windows.py），
     只 override 真正不同的部分。
  4. **新增平台 = 实现一个 BasePlatform 子类 + 在 REGISTRY 注册**，
     核心代码零改动。

音频常量（三平台统一，ASR 服务端的硬性要求）：
  - 16kHz / mono / float32 / 30ms 帧（480 samples）
"""
from __future__ import annotations

import sys
import threading
from abc import ABC, abstractmethod
from typing import Callable, Optional

import numpy as np

# ---- 音频常量（全项目唯一出处，其他模块从这里 import）----
SAMPLERATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = SAMPLERATE * FRAME_MS // 1000  # 480


# =====================================================================
# 热键
# =====================================================================

class BaseHotkey(ABC):
    """全局热键监听。实现类必须保证 on_press 回调不被阻塞。"""

    @abstractmethod
    def register(self, key: str, on_press: Callable[[], None]) -> None:
        """注册全局热键。key 用 pynput 语法（'<f9>'、'<f8>' 等）。
        平台实现若不用 pynput，需自行做语法映射。"""

    @abstractmethod
    def run(self) -> None:
        """阻塞主线程直到退出（KeyboardInterrupt / 平台退出信号）。"""


# =====================================================================
# 录音
# =====================================================================

class BaseAudioCapture(ABC):
    """16kHz mono float32 录音，30ms/帧回调。"""

    @abstractmethod
    def start(self, on_frame: Callable[[np.ndarray], None]) -> None:
        """开始录音。回调运行在驱动/库的音频线程里——必须快进快出，
        只允许把帧塞进队列之类的 O(1) 操作。"""

    @abstractmethod
    def stop(self) -> None:
        """停止录音，回调不再触发。幂等：重复调用无害。"""


class SharedSoundDeviceAudio(BaseAudioCapture):
    """基于 sounddevice(portaudio) 的录音 —— macOS/Linux/Windows 通用。

    三平台唯一的差别是系统弹的麦克风授权对话框，代码层面一致，
    所以直接作为共享实现。平台子类无特殊需求时直接复用本类。
    """

    def __init__(self):
        self._stream = None
        self._cb: Optional[Callable[[np.ndarray], None]] = None

    def start(self, on_frame: Callable[[np.ndarray], None]) -> None:
        import sounddevice as sd  # 延迟 import，避免无录音需求时强依赖
        self._cb = on_frame

        def _cb(indata, frames, time_info, status):
            if status:
                print(f"[audio] {status}", file=sys.stderr)
            if self._cb is not None:
                # indata: (N, 1) float32 → 展平成一维
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


# =====================================================================
# 文本注入
# =====================================================================

class BaseInjector(ABC):
    """把识别好的文本送到当前光标位置（通常：写剪贴板 + 模拟粘贴键）。"""

    @abstractmethod
    def inject(self, text: str) -> None:
        """失败不抛异常，只打日志（核心流程不允许被注入失败打断）。"""


# =====================================================================
# 焦点窗口探测
# =====================================================================

class BaseWindowProbe(ABC):
    """判断当前焦点窗口是否是终端（决定粘贴用哪种按键）。"""

    @abstractmethod
    def is_terminal(self) -> bool:
        """True = 终端类窗口；探测失败一律返回 False（走 GUI 分支最安全）。"""


# =====================================================================
# 用户反馈
# =====================================================================

class BaseFeedback(ABC):
    """提示音 / 菜单栏 / 通知。所有方法在任意线程被调用都必须安全、
    非阻塞、不抛异常（核心已做 try 包裹，实现里也别作死）。"""

    def on_recording_start(self) -> None: ...
    def on_recording_stop(self) -> None: ...
    def on_result(self, text: str) -> None: ...
    def on_error(self, msg: str) -> None: ...

    def run_forever(self) -> None:
        """占住主线程的 UI 主循环（菜单栏等）。默认实现：纯阻塞。
        平台若无 UI 需求，无需 override。"""
        try:
            while True:
                threading.Event().wait(1.0)
        except KeyboardInterrupt:
            pass


class PrintFeedback(BaseFeedback):
    """纯终端输出反馈 —— 所有平台的兜底（--no-sound --no-notify 或
    平台 UI 不可用时）。"""

    def on_recording_start(self) -> None:
        print("[feedback] ▶ 录音开始", file=sys.stderr)

    def on_recording_stop(self) -> None:
        print("[feedback] ■ 录音结束", file=sys.stderr)

    def on_result(self, text: str) -> None:
        print(f"[feedback] ✓ 已粘贴: {text[:50]}", file=sys.stderr)

    def on_error(self, msg: str) -> None:
        print(f"[feedback] ✗ 出错: {msg[:80]}", file=sys.stderr)


# =====================================================================
# 平台聚合
# =====================================================================

class BasePlatform(ABC):
    """一个 OS 的全部平台实现。子类按需 override；能复用共享实现的不重写。

    最小实现示例（新平台只需这几行 + 各组件类）：

        class FooPlatform(BasePlatform):
            name = "foo"
            def create_hotkey(self): return FooHotkey()
            def create_audio(self):  return SharedSoundDeviceAudio()  # 复用
            def create_injector(self): return FooInjector()
            def create_window_probe(self): return BaseWindowProbe 的子类()
            def create_feedback(self, sound, notify): return PrintFeedback()
    """

    name: str = "abstract"

    @abstractmethod
    def create_hotkey(self) -> BaseHotkey: ...
    @abstractmethod
    def create_audio(self) -> BaseAudioCapture: ...
    @abstractmethod
    def create_injector(self) -> BaseInjector: ...
    @abstractmethod
    def create_window_probe(self) -> BaseWindowProbe: ...
    @abstractmethod
    def create_feedback(self, sound: bool = True, notify: bool = True) -> BaseFeedback: ...

    # ---- 便捷访问（惰性创建 + 缓存）----

    @property
    def hotkey(self) -> BaseHotkey:
        if not hasattr(self, "_hotkey"):
            self._hotkey = self.create_hotkey()
        return self._hotkey

    @property
    def audio(self) -> BaseAudioCapture:
        if not hasattr(self, "_audio"):
            self._audio = self.create_audio()
        return self._audio

    @property
    def injector(self) -> BaseInjector:
        if not hasattr(self, "_injector"):
            self._injector = self.create_injector()
        return self._injector

    @property
    def window(self) -> BaseWindowProbe:
        if not hasattr(self, "_window"):
            self._window = self.create_window_probe()
        return self._window

    @property
    def feedback(self) -> BaseFeedback:
        if not hasattr(self, "_feedback"):
            self._feedback = self.create_feedback()
        return self._feedback

    def init_feedback(self, sound: bool = True, notify: bool = True) -> BaseFeedback:
        """带开关构造 feedback（app 启动时调用一次，之后走 .feedback 属性）。"""
        self._feedback = self.create_feedback(sound=sound, notify=notify)
        return self._feedback

    @property
    def ui_loop(self) -> Optional[Callable[[], None]]:
        """有 UI 主循环（如 macOS rumps 菜单栏）的平台返回回调，否则 None。
        app.py 据此决定：ui_loop 存在 → 跑它；否则跑 hotkey.run()。"""
        return None
