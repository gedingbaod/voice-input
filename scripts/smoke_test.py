"""冒烟测试：验证 client -> omlx -> 剪贴板 全链路。

跑法：
  cd ~/SRC/voice-input
  python3 scripts/smoke_test.py

不需要麦克风权限（用 say 合成的音频作为输入）。
不需要辅助功能权限（pbcopy 不需要）。

测试三件事：
  1. AsrClient.health_check() 能连上 omlx
  2. AsrClient.transcribe(numpy_audio) 返回非空中文文本
  3. MacInjector.inject(text) 把文本写到剪贴板（pbpaste 能读回）
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

# 让 client/ 可 import（不依赖安装）
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from client.asr_client import ASRConfig, AsrClient  # noqa: E402
from client.platform.macos import MacInjector  # noqa: E402


# ---------- 测试用音频生成 ----------

def synth_chinese_audio(text: str, out_wav: Path) -> np.ndarray:
    """用 macOS 'say' 合成中文音频，转 16kHz mono float32。"""
    voice = "Tingting"
    aiff = out_wav.with_suffix(".aiff")
    base = str(aiff.with_suffix(""))
    r = subprocess.run(
        ["say", "-v", voice, "-o", base, text],
        capture_output=True, text=True,
    )
    if r.returncode != 0 or not aiff.exists():
        print(f"[warn] say 合成失败 ({r.stderr.strip()})，改用静音 fallback",
              file=sys.stderr)
        return synth_silence(out_wav, duration_s=2.0)

    # 用 soundfile 读 aiff（libsndfile 后端，原生支持 AIFF-C）
    import soundfile as sf
    audio, sr = sf.read(str(aiff), always_2d=False, dtype="float32")
    aiff.unlink(missing_ok=True)
    if audio.ndim == 2:
        audio = audio.mean(axis=1)

    # afconvert 在新 macOS 强制 22050Hz；线性重采样到 16k
    if sr != 16000:
        audio = _resample_linear(audio, sr, 16000)

    # 写到 out_wav（便于人耳/外部工具听）
    import wave as _wave
    pcm_i16 = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with _wave.open(str(out_wav), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(pcm_i16.tobytes())
    return audio.astype(np.float32)


def _resample_linear(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    """最简线性重采样（冒烟测试够用）。"""
    if sr_in == sr_out:
        return x
    n_in = len(x)
    n_out = int(round(n_in * sr_out / sr_in))
    t_in = np.linspace(0.0, 1.0, n_in, endpoint=False)
    t_out = np.linspace(0.0, 1.0, n_out, endpoint=False)
    return np.interp(t_out, t_in, x).astype(np.float32)


def synth_silence(out_wav: Path, duration_s: float = 2.0,
                  sr: int = 16000) -> np.ndarray:
    """生成 440Hz tone（确保有信号）。"""
    t = np.arange(int(sr * duration_s)) / sr
    audio = 0.3 * np.sin(2 * np.pi * 440 * t).astype(np.float32)
    import wave
    with wave.open(str(out_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((audio * 32767).astype(np.int16).tobytes())
    return audio


# ---------- 测试用例 ----------

def test_health(asr: AsrClient) -> None:
    print("\n=== [1/3] health_check ===", file=sys.stderr)
    ok = asr.health_check()
    print(f"  -> {'OK' if ok else 'FAIL'}", file=sys.stderr)
    assert ok, "ASR 服务端不可达"


def test_transcribe(asr: AsrClient) -> str:
    print("\n=== [2/3] transcribe（合成中文音频）===", file=sys.stderr)
    text_in = "你好，这是一段冒烟测试，今天天气很好。"
    print(f"  input text : {text_in!r}", file=sys.stderr)
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "test.wav"
        audio = synth_chinese_audio(text_in, wav)
        print(f"  audio      : {len(audio)} samples "
              f"({len(audio)/16000:.2f}s)", file=sys.stderr)
        text_out = asr.transcribe(audio)
    print(f"  output text: {text_out!r}", file=sys.stderr)
    assert text_out, "识别返回空文本"
    assert len(text_out) >= 2, f"识别文本太短：{text_out!r}"
    return text_out


def test_injector(text: str) -> None:
    print("\n=== [3/3] MacInjector.inject ===", file=sys.stderr)
    injector = MacInjector()
    injector.inject(text)
    # 等 pbcopy 落盘（实测通常 < 100ms，给 500ms 留余量）
    time.sleep(0.3)
    r = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=2)
    got = r.stdout
    print(f"  写入剪贴板  : {text!r}", file=sys.stderr)
    print(f"  pbpaste 读回: {got!r}", file=sys.stderr)
    assert r.returncode == 0, "pbpaste 失败"
    assert text in got, (
        f"剪贴板内容不匹配：写入 {text!r}, 读回 {got!r}"
    )


def main() -> int:
    cfg = ASRConfig()
    print(f"base_url={cfg.base_url}  model={cfg.model}", file=sys.stderr)

    asr = AsrClient(cfg)

    try:
        test_health(asr)
        text = test_transcribe(asr)
        test_injector(text)
    except AssertionError as e:
        print(f"\n[FAIL] {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"\n[ERROR] {e!r}", file=sys.stderr)
        return 2

    print("\n[PASS] 全部通过 ✓", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
