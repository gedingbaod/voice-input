"""主入口：装平台 → 装 ASR → 跑主循环。

用法：
  python -m client
  python -m client --debug
  python -m client --base-url http://192.168.31.54:9999/v1
  python -m client --show-config

依赖：sounddevice, pynput, openai, numpy
"""
from __future__ import annotations

import logging
import sys
import threading

from .asr_client import ASRError, AsrClient
from .config import load_config, print_config
from .core import StreamRecognizer
from .platform import make_platform


def _setup_logging(debug: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    cfg = load_config(argv)
    platform = make_platform(sound=cfg.sound, notify=cfg.notify)
    _setup_logging(cfg.vad.debug)

    print_config(cfg, platform.name)

    # --show-config：打印完就退出（用于排查）
    if cfg.show_config:
        return 0

    # ASR 客户端
    try:
        asr = AsrClient(cfg.asr)
    except ASRError as e:
        print(f"[fatal] {e}", file=sys.stderr)
        return 2

    # 启动时探活
    print(f"[init] probing ASR at {cfg.asr.base_url} ...", file=sys.stderr,
          flush=True)
    if not asr.health_check():
        print(
            f"[fatal] cannot reach ASR at {cfg.asr.base_url}\n"
            f"        请确认 Mac 上的 omlx 在跑（默认端口 9999）",
            file=sys.stderr,
        )
        return 3
    print("[init] ASR OK", file=sys.stderr, flush=True)

    # 主状态机
    punctuator = None
    if cfg.punct:
        from .punctuator import Punctuator
        punctuator = Punctuator(
            client=asr.raw_client, model=cfg.punct_model)

    recognizer = StreamRecognizer(
        asr=asr,
        audio=platform.audio,
        injector=platform.injector,
        vad_cfg=cfg.vad,
        punctuator=punctuator,
        mode=cfg.mode,
        feedback=platform.feedback,
    )

    def on_hotkey():
        if not recognizer.running:
            recognizer.start()
        else:
            # stop 阻塞 → 后台线程跑
            threading.Thread(target=recognizer.stop, daemon=True,
                             name="stop").start()

    # 注册热键
    platform.hotkey.register(cfg.hotkey, on_hotkey)
    print(
        f"就绪。按 {cfg.hotkey.upper()} 开始/停止录音（菜单栏 🎤 / Ctrl+C 退出）",
        file=sys.stderr, flush=True,
    )

    try:
        # 优先用平台 UI 主循环（macOS = rumps 菜单栏）；否则阻塞在热键循环
        if platform.ui_loop is not None:
            platform.ui_loop()
        else:
            platform.hotkey.run()
    except KeyboardInterrupt:
        print("\nbye", file=sys.stderr)
        try:
            recognizer.stop()
        except Exception:  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
