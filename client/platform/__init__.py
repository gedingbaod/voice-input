"""平台工厂：按 sys.platform 自动选 OS 实现。"""
from __future__ import annotations

import sys

from .base import AudioCapture, Hotkey, Injector, Platform, WindowProbe


def make_platform(sound: bool = True, notify: bool = True):
    """根据当前 OS 返回一组平台实现。"""
    if sys.platform == "darwin":
        from .macos import MacAudio, MacHotkey, MacInjector, MacWindowProbe
        from .macos_feedback import MacFeedback
        feedback = MacFeedback(sound=sound, notify=notify)
        return Platform(
            hotkey=MacHotkey(),
            audio=MacAudio(),
            injector=MacInjector(),
            window=MacWindowProbe(),
            name="macos",
            feedback=feedback,
            ui_loop=feedback.run_forever,  # rumps 菜单栏接管主线程
        )
    if sys.platform.startswith("linux"):
        # 第二批交付
        raise NotImplementedError(
            "Linux 客户端在第二批交付；当前批次仅 macOS"
        )
    if sys.platform == "win32":
        # 第三批交付
        raise NotImplementedError(
            "Windows 客户端在第三批交付；当前批次仅 macOS"
        )
    raise RuntimeError(f"unsupported platform: {sys.platform!r}")


__all__ = [
    "AudioCapture",
    "Hotkey",
    "Injector",
    "Platform",
    "WindowProbe",
    "make_platform",
]
