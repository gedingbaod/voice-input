"""OpenAI 兼容 ASR 客户端。

调用 omlx / 任何 Whisper 兼容服务（funasr openai_api、whisper.cpp、whisperX 等）。
返回纯文本。出错抛 ASRError。
"""
from __future__ import annotations

import io
import logging
import time
import wave
from dataclasses import dataclass
from typing import Optional

import numpy as np

log = logging.getLogger("voice-input.asr")


class ASRError(Exception):
    """ASR 调用失败（网络 / 鉴权 / 服务器错误）。"""


@dataclass
class ASRConfig:
    """ASR 客户端配置。"""

    base_url: str = "http://192.168.31.54:9999/v1"
    api_key: str = "123456"
    model: str = "Qwen3-ASR-1.7B-8bit"  # 原生带标点；1.7B 精度优于 0.6B
    timeout: float = 30.0
    language: str = "zh"   # 固定语言，避免短音频 auto-detect 抖动
    prompt: Optional[str] = None    # OpenAI Whisper 的初始提示词（omlx 会映射到 backend biasing）


class AsrClient:
    """用 openai SDK 调用 ASR 服务端。"""

    def __init__(self, cfg: ASRConfig):
        self.cfg = cfg
        # 延迟 import：openai SDK 不是 hot path 必要依赖（音频捕获 + VAD 都不需要）
        # 但调用 ASR 必需
        try:
            from openai import OpenAI  # type: ignore
        except ImportError as e:
            raise ASRError(
                "openai SDK 未装。请 pip install openai>=1.0"
            ) from e

        self._client = OpenAI(
            base_url=self.cfg.base_url,
            api_key=self.cfg.api_key,
            timeout=self.cfg.timeout,
        )
        # 暴露给 Punctuator 等同服务器功能复用（只读）
        self.raw_client = self._client

    def health_check(self) -> bool:
        """GET /v1/models；成功 = 通；失败 = 不通。"""
        try:
            self._client.models.list()
            return True
        except Exception as e:
            log.warning("ASR health check failed: %s", e)
            return False

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> str:
        """把 float32 音频识别成文本。返回已 strip 后的字符串。"""
        if audio.size == 0:
            return ""
        wav_bytes = _float32_to_wav_bytes(audio, sample_rate)
        t0 = time.time()
        try:
            resp = self._client.audio.transcriptions.create(
                model=self.cfg.model,
                file=("audio.wav", wav_bytes, "audio/wav"),
                response_format="json",
                language=self.cfg.language,
                prompt=self.cfg.prompt,
            )
        except Exception as e:
            raise ASRError(f"ASR request failed: {e}") from e
        dt = time.time() - t0
        text = (getattr(resp, "text", "") or "").strip()
        log.info("asr: %d samples (%.2fs) -> %d chars in %.2fs",
                 audio.size, audio.size / sample_rate, len(text), dt)
        return text


def _float32_to_wav_bytes(audio: np.ndarray, sample_rate: int) -> bytes:
    """float32 [-1, 1] → 16kHz mono PCM16 WAV bytes。"""
    # 限幅 + 转 int16
    pcm = np.clip(audio, -1.0, 1.0)
    pcm_i16 = (pcm * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)  # int16
        w.setframerate(sample_rate)
        w.writeframes(pcm_i16.tobytes())
    return buf.getvalue()
