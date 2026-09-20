"""macOS 用户体验反馈：菜单栏状态 + 系统提示音 + 通知中心。

- 菜单栏：空闲 🎤 / 录音中 🔴（rumps；未安装则静默降级）
- 提示音：afplay 系统音（非阻塞 Popen；--no-sound 可关）
- 通知：osascript display notification（--no-notify 可关）

线程模型：所有 on_* 方法从任意线程调用都安全；rumps 主循环跑在主线程
（通过 run_forever() 接管），状态更新经 rumps.Timer 主线程调度或直接改
title（rumps 支持跨线程改 title，内部有锁）。
"""
from __future__ import annotations

import subprocess
import sys

from .base import BaseFeedback

try:
    import rumps  # type: ignore
    _HAS_RUMPS = True
except Exception:  # pragma: no cover
    rumps = None
    _HAS_RUMPS = False

# 系统提示音（/System/Library/Sounds/）
_SND_START = "/System/Library/Sounds/Pop.aiff"
_SND_STOP = "/System/Library/Sounds/Tink.aiff"
_SND_RESULT = "/System/Library/Sounds/Glass.aiff"
_SND_ERROR = "/System/Library/Sounds/Sosumi.aiff"

TITLE_IDLE = "🎤"
TITLE_REC = "🔴"


def _play(path: str) -> None:
    """非阻塞播放系统音。失败静默。"""
    try:
        subprocess.Popen(
            ["afplay", path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:  # noqa: BLE001
        pass


def _notify(title: str, body: str) -> None:
    """macOS 通知中心横幅。失败静默。"""
    # body 里的双引号转义
    body = body.replace("\\", "\\\\").replace('"', '\\"')
    title = title.replace('"', '\\"')
    try:
        subprocess.Popen(
            ["osascript", "-e",
             f'display notification "{body}" with title "{title}"'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:  # noqa: BLE001
        pass


class MacFeedback(BaseFeedback):
    """提示音 + 通知 + 菜单栏。sound/notify 开关由构造方传入。"""

    def __init__(self, sound: bool = True, notify: bool = True):
        self.sound = sound
        self.notify = notify
        self._app: "rumps.App | None" = None
        if _HAS_RUMPS:
            try:
                self._app = rumps.App("语音输入", title=TITLE_IDLE)
                self._app.menu = [
                    "状态：空闲",
                    None,  # separator
                    "按 F9 开始 / 停止录音",
                    None,  # separator
                    rumps.MenuItem("退出", callback=lambda _s: rumps.quit_application()),
                ]
            except Exception as e:  # noqa: BLE001
                print(f"[ui] rumps init failed: {e}", file=sys.stderr)
                self._app = None

    # ---------- Feedback 协议 ----------

    def on_recording_start(self) -> None:
        if self.sound:
            _play(_SND_START)
        self._set_state(recording=True)

    def on_recording_stop(self) -> None:
        if self.sound:
            _play(_SND_STOP)

    def on_result(self, text: str) -> None:
        if self.sound:
            _play(_SND_RESULT)
        if self.notify and text:
            preview = text if len(text) <= 40 else text[:40] + "…"
            _notify("语音输入", preview)
        self._set_state(recording=False)

    def on_error(self, msg: str) -> None:
        if self.sound:
            _play(_SND_ERROR)
        if self.notify:
            _notify("语音输入出错", msg[:60])

    # ---------- 内部 ----------

    def _set_state(self, recording: bool) -> None:
        if self._app is None:
            return
        try:
            self._app.title = TITLE_REC if recording else TITLE_IDLE
            item = self._app.menu.get("状态：空闲")
            if item is None:
                item = self._app.menu.get("状态：录音中")
            if item is not None:
                item.title = "状态：录音中" if recording else "状态：空闲"
        except Exception:  # noqa: BLE001
            pass

    # ---------- 主循环 ----------

    def run_forever(self) -> None:
        """接管主线程（rumps 菜单栏）。菜单退出后返回。"""
        if self._app is not None:
            self._app.run()
        else:
            super().run_forever()  # 无 rumps：基类的阻塞实现
