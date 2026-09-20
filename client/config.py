"""配置：CLI 参数 + 环境变量 + 默认值。

优先级（高 → 低）：
  1. 命令行参数
  2. 环境变量（VOICE_INPUT_*）
  3. 默认值（指向 Mac 主机上的 omlx）
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

from .asr_client import ASRConfig
from .core import VADConfig
from .punctuator import DEFAULT_PUNCT_MODEL


# 默认 ASR 服务端：Mac 主机的 omlx（局域网 IP，所有平台统一）
DEFAULT_BASE_URL = "http://192.168.31.54:9999/v1"
DEFAULT_API_KEY = "123456"
# Qwen3-ASR：LLM 型 ASR，原生输出标点 + 术语大小写规范，单模型即可
# 1.7B 精度明显优于 0.6B（0.6B 实测错误率偏高），热态延迟仍 <0.5s
DEFAULT_MODEL = "Qwen3-ASR-1.7B-8bit"
# 固定语言避免短音频 auto-detect 抖动（omlx issue #1117）
DEFAULT_LANGUAGE = "zh"


@dataclass
class Config:
    asr: ASRConfig
    vad: VADConfig
    hotkey: str
    show_config: bool  # 启动时打印当前配置
    punct: bool        # 是否做标点修复
    punct_model: str   # 标点修复用的 LLM（跑在同一台 omlx 上）
    mode: str          # "whole" 整段录音（默认）| "vad" 流式断句
    sound: bool        # 提示音
    notify: bool       # 通知横幅


def load_config(argv: list[str] | None = None) -> Config:
    ap = argparse.ArgumentParser(
        prog="voice-input",
        description="F9 说话 → 自动粘贴到当前光标（连 Mac omlx ASR）",
    )
    ap.add_argument("--base-url", default=None,
                    help=f"ASR 服务端 base_url（默认 {DEFAULT_BASE_URL}）")
    ap.add_argument("--api-key", default=None,
                    help=f"ASR API key（默认 {DEFAULT_API_KEY}）")
    ap.add_argument("--model", default=None,
                    help=f"ASR 模型名（默认 {DEFAULT_MODEL}）")
    ap.add_argument("--hotkey", default=None,
                    help="全局热键（pynput 语法，默认 <f9>）")
    ap.add_argument("--mode", choices=["whole", "vad"], default=None,
                    help="whole=按F9录整段再按F9停止后一次识别（默认，"
                         "不切句不丢字）；vad=说话停顿自动断句流式出字")
    ap.add_argument("--sil-ms", type=int, default=None,
                    help="静音门限 ms（默认 700；说话停顿久、句子被切太碎就调大，"
                         "如 900/1200；想快点出字就调小）")
    ap.add_argument("--energy", type=float, default=None,
                    help="能量阈值（默认 0.005）")
    ap.add_argument("--language", default=None,
                    help="强制指定语言（zh/en/ja 等；默认自动识别）")
    ap.add_argument("--prompt", default=None,
                    help="Whisper 风格 prompt（omlx 会映射到 backend biasing）")
    ap.add_argument("--no-sound", action="store_true",
                    help="关闭提示音（默认开启）")
    ap.add_argument("--no-notify", action="store_true",
                    help="关闭通知中心横幅（默认开启）")
    ap.add_argument("--debug", action="store_true",
                    help="打印实时 RMS / VAD 状态")
    ap.add_argument("--no-punct", action="store_true",
                    help="关闭标点修复（默认已关闭；Qwen3-ASR 原生输出标点）")
    ap.add_argument("--punct", action="store_true",
                    help="开启标点修复（仅当 ASR 模型无标点输出时需要，如 SenseVoiceSmall）")
    ap.add_argument("--punct-model", default=None,
                    help="标点修复用的 LLM 模型名（跑在 omlx 上）")
    ap.add_argument("--show-config", action="store_true",
                    help="打印当前生效配置后退出（用于排查）")
    args = ap.parse_args(argv)

    base_url = (
        args.base_url
        or os.environ.get("VOICE_INPUT_BASE_URL")
        or DEFAULT_BASE_URL
    )
    api_key = (
        args.api_key
        or os.environ.get("VOICE_INPUT_API_KEY")
        or DEFAULT_API_KEY
    )
    model = (
        args.model
        or os.environ.get("VOICE_INPUT_MODEL")
        or DEFAULT_MODEL
    )
    hotkey = (
        args.hotkey
        or os.environ.get("VOICE_INPUT_HOTKEY")
        or "<f9>"
    )
    sil_ms = (
        args.sil_ms
        if args.sil_ms is not None
        else int(os.environ.get("VOICE_INPUT_SIL_MS", "700"))
    )
    energy = (
        args.energy
        if args.energy is not None
        else float(os.environ.get("VOICE_INPUT_ENERGY", "0.005"))
    )
    language = (
        args.language
        or os.environ.get("VOICE_INPUT_LANGUAGE")
        or DEFAULT_LANGUAGE
    )
    prompt = (
        args.prompt
        or os.environ.get("VOICE_INPUT_PROMPT")
        or None
    )
    debug = args.debug or _env_bool("VOICE_INPUT_DEBUG", False)
    mode = (
        args.mode
        or os.environ.get("VOICE_INPUT_MODE")
        or "whole"
    )
    # 标点修复默认关闭（Qwen3-ASR 原生带标点）；显式 --punct 或环境变量才开
    punct = args.punct or _env_bool("VOICE_INPUT_PUNCT", False)
    punct_model = (
        args.punct_model
        or os.environ.get("VOICE_INPUT_PUNCT_MODEL")
        or DEFAULT_PUNCT_MODEL
    )

    return Config(
        asr=ASRConfig(
            base_url=base_url,
            api_key=api_key,
            model=model,
            language=language,
            prompt=prompt,
        ),
        vad=VADConfig(
            silence_ms=sil_ms,
            energy_threshold=energy,
            debug=debug,
        ),
        hotkey=hotkey,
        show_config=args.show_config,
        punct=punct,
        punct_model=punct_model,
        mode=mode,
        sound=not args.no_sound,
        notify=not args.no_notify,
    )


def _env_bool(key: str, default: bool) -> bool:
    v = os.environ.get(key)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def print_config(cfg: Config, platform_name: str) -> None:
    """打印当前生效配置（人类可读）。"""
    print("=" * 50, file=sys.stderr)
    print(f"platform       : {platform_name}", file=sys.stderr)
    print(f"hotkey         : {cfg.hotkey}", file=sys.stderr)
    print(f"asr.base_url   : {cfg.asr.base_url}", file=sys.stderr)
    print(f"asr.model      : {cfg.asr.model}", file=sys.stderr)
    if cfg.asr.language:
        print(f"asr.language   : {cfg.asr.language}", file=sys.stderr)
    if cfg.asr.prompt:
        print(f"asr.prompt     : {cfg.asr.prompt[:60]!r}", file=sys.stderr)
    print(f"vad.silence_ms : {cfg.vad.silence_ms}", file=sys.stderr)
    print(f"vad.energy     : {cfg.vad.energy_threshold}", file=sys.stderr)
    print(f"vad.debug      : {cfg.vad.debug}", file=sys.stderr)
    print(f"mode           : {cfg.mode}", file=sys.stderr)
    print(f"punct          : {cfg.punct} ({cfg.punct_model})",
          file=sys.stderr)
    print("=" * 50, file=sys.stderr)
