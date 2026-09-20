"""平台工厂与注册表。

自动按 sys.platform 选择；--platform 参数可强制指定（调试用，比如在
Mac 上强制跑 linux 平台类做静态检查——运行时调用系统命令会失败，
但 import / 构造路径可验证）。

新增平台步骤：
  1. 写 client/platform/<name>.py，实现 XxxPlatform(BasePlatform)
  2. 在下方 REGISTRY 加一行
  3. 完成。核心代码（core.py / app.py）零改动。
"""
from __future__ import annotations

import sys

from .base import (
    BaseAudioCapture,
    BaseFeedback,
    BaseHotkey,
    BaseInjector,
    BasePlatform,
    BaseWindowProbe,
    SharedSoundDeviceAudio,
    PrintFeedback,
)

# sys.platform 值 → 平台类。延迟 import（macos_feedback 的 rumps 是可选依赖）
_REGISTRY_KEYS = {
    "darwin": ("macos", "MacPlatform"),
    "linux": ("linux", "LinuxPlatform"),
    "win32": ("windows", "WindowsPlatform"),
}


def _load_platform_class(name: str) -> type[BasePlatform]:
    if name == "macos":
        from .macos import MacPlatform
        return MacPlatform
    if name == "linux":
        from .linux import LinuxPlatform
        return LinuxPlatform
    if name == "windows":
        from .windows import WindowsPlatform
        return WindowsPlatform
    raise ValueError(f"unknown platform: {name!r}（可选：{list(_REGISTRY_KEYS.values())}）")


def detect_platform_name() -> str:
    """按 sys.platform 返回平台名。"""
    for key, (name, _cls) in _REGISTRY_KEYS.items():
        if sys.platform == key or sys.platform.startswith(key):
            return name
    raise RuntimeError(f"unsupported platform: {sys.platform!r}")


def make_platform(name: str | None = None,
                  sound: bool = True, notify: bool = True) -> BasePlatform:
    """构造平台对象。name=None 自动检测。"""
    name = name or detect_platform_name()
    cls = _load_platform_class(name)
    plat = cls()
    plat.init_feedback(sound=sound, notify=notify)
    return plat


__all__ = [
    "BaseAudioCapture", "BaseFeedback", "BaseHotkey", "BaseInjector",
    "BasePlatform", "BaseWindowProbe", "SharedSoundDeviceAudio",
    "PrintFeedback",
    "detect_platform_name", "make_platform",
]
