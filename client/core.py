"""录音 + 能量 VAD 断句 + ASR 识别 + 文本注入 的状态机。

平台无关。线程模型（保持与原 asr_hotkey.py 一致）：

    sounddevice 录音回调线程          worker 线程                    每句独立线程
      │ 30ms 帧 put 进 audio_q  →     │ 能量 VAD 断句          →     │ _recognize_and_inject:
      │                               │ (RMS 阈值 + 静音计时)         │ transcribe() → inject()

设计约束：
  - 热键回调不能阻塞 → start/stop 都另起线程
  - 识别调用不能挡住 VAD 累积 → 每句独立线程
  - audio_q 必须无界或足够大：录音 30ms 一帧，10s 缓冲也才 333 帧，正常不会积压
"""
from __future__ import annotations

import logging
import queue
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from .asr_client import ASRError, AsrClient
from .platform.base import AudioCapture, Injector

log = logging.getLogger("voice-input.core")

SAMPLERATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = SAMPLERATE * FRAME_MS // 1000  # 480
MIN_SPEECH_MS = 300  # < 300ms 段丢弃


@dataclass
class VADConfig:
    """能量 VAD 参数（仅 mode='vad' 时生效）。"""

    silence_ms: int = 700          # 连续静音此值算一句结束（400 对慢语速太敏感）
    energy_threshold: float = 0.005  # RMS > 此值算语音
    hangover_ms: int = 200         # 语音结束后多保留一段尾巴防句尾被砍
    min_speech_ms: int = MIN_SPEECH_MS
    debug: bool = False


@dataclass
class SegmentEvent:
    """识别完一段后抛出的事件（供 UI / 日志订阅；当前未启用，仅打印日志）。"""

    audio: np.ndarray
    duration_s: float
    text: str
    elapsed_s: float


class StreamRecognizer:
    """F9 触发后启动；worker 线程做断句/累积 + 后台识别注入。

    mode:
      - "whole"（默认）: 整段录音。按 F9 开始，再按 F9 停止后把整段
        音频一次性送识别 —— 模型拿到完整上下文，准确率最高，无中途切句。
      - "vad": 能量 VAD 流式断句。说话停顿 silence_ms 即出一段字。
    """

    def __init__(
        self,
        asr: AsrClient,
        audio: AudioCapture,
        injector: Injector,
        vad_cfg: VADConfig,
        on_segment: Optional[Callable[[SegmentEvent], None]] = None,
        punctuator=None,  # Optional[Punctuator]；None = 不做标点修复
        mode: str = "whole",
        feedback=None,    # Optional[Feedback]；提示音/菜单栏/通知
    ):
        self.asr = asr
        self.audio = audio
        self.injector = injector
        self.cfg = vad_cfg
        self.on_segment = on_segment
        self.punctuator = punctuator
        if mode not in ("whole", "vad"):
            raise ValueError(f"unknown mode: {mode!r}")
        self.mode = mode
        self.feedback = feedback

        self.running = False
        self._audio_q: "queue.Queue[Optional[np.ndarray]]" = queue.Queue()
        self._worker: Optional[threading.Thread] = None

    # ---------- 主线程接口（热键回调里调用）----------

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.audio.start(self._on_frame)
        self._worker = threading.Thread(target=self._worker_loop, daemon=True,
                                        name="vad-worker")
        self._worker.start()
        print("● 录音中… 再按 F9 停止", file=sys.stderr, flush=True)
        if self.feedback is not None:
            try:
                self.feedback.on_recording_start()
            except Exception:  # noqa: BLE001
                pass

    def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        try:
            self.audio.stop()
        finally:
            # 通知 worker 退出
            self._audio_q.put(None)
            if self._worker is not None:
                # whole 模式下 worker 退出前要完成整段识别，长音频可能耗时
                self._worker.join(timeout=120)
                self._worker = None
        print("● 停止", file=sys.stderr, flush=True)
        if self.feedback is not None:
            try:
                self.feedback.on_recording_stop()
            except Exception:  # noqa: BLE001
                pass

    # ---------- 录音回调线程 ----------

    def _on_frame(self, frame: np.ndarray) -> None:
        # 这里就是 sounddevice 回调线程，每 30ms 调一次
        if self.running:
            self._audio_q.put(frame)

    # ---------- worker：VAD + 识别 + 注入 ----------

    def _worker_loop(self) -> None:
        if self.mode == "whole":
            self._worker_whole()
        else:
            self._worker_vad()

    def _worker_whole(self) -> None:
        """整段模式：无脑累积所有帧，收到 None 后一次性识别整段。"""
        frames: list[np.ndarray] = []
        while True:
            frame = self._audio_q.get()
            if frame is None:
                break
            frames.append(frame)
        if not frames:
            return
        audio = np.concatenate(frames)
        dur = audio.size / SAMPLERATE
        print(f"● 整段 {dur:.1f}s，识别中…", file=sys.stderr, flush=True)
        # 直接在 stop 线程里识别（stop 本身在独立线程跑，不挡热键）
        self._recognize_and_inject(audio)

    def _worker_vad(self) -> None:
        speech_buf: list[np.ndarray] = []
        in_speech = False
        silence_frames = 0
        hangover_frames = 0
        silence_limit = max(1, self.cfg.silence_ms // FRAME_MS)
        hangover_limit = max(0, self.cfg.hangover_ms // FRAME_MS)
        min_speech_frames = max(1, self.cfg.min_speech_ms // FRAME_MS)
        dbg_counter = 0
        peak_rms = 0.0

        while True:
            frame = self._audio_q.get()
            if frame is None:
                # 收尾：把剩余的最后一句也识别掉
                if in_speech and len(speech_buf) >= min_speech_frames:
                    audio = np.concatenate(speech_buf)
                    threading.Thread(
                        target=self._recognize_and_inject,
                        args=(audio,),
                        daemon=True,
                        name="asr-final",
                    ).start()
                break

            rms = float(np.sqrt(np.mean(frame * frame)))
            peak_rms = max(peak_rms, rms)
            is_voice = rms > self.cfg.energy_threshold

            if self.cfg.debug:
                dbg_counter += 1
                if dbg_counter % 15 == 0:  # ~450ms 一次
                    print(
                        f"[vad] rms={rms:.4f} peak={peak_rms:.4f} "
                        f"thr={self.cfg.energy_threshold:.4f} "
                        f"state={'SPEECH' if in_speech else 'sil'} "
                        f"sil={silence_frames}/{silence_limit}",
                        file=sys.stderr,
                    )

            if is_voice:
                if not in_speech:
                    in_speech = True
                    silence_frames = 0
                    if self.cfg.debug:
                        print(f"[vad] → SPEECH (rms={rms:.4f})",
                              file=sys.stderr)
                speech_buf.append(frame)
                hangover_frames = hangover_limit
                silence_frames = 0
            else:
                if in_speech:
                    if hangover_frames > 0:
                        speech_buf.append(frame)
                        hangover_frames -= 1
                    else:
                        silence_frames += 1
                        if silence_frames >= silence_limit:
                            if len(speech_buf) >= min_speech_frames:
                                dur = len(speech_buf) * FRAME_MS / 1000
                                if self.cfg.debug:
                                    print(f"[vad] → SIL, 触发识别 ({dur:.1f}s)",
                                          file=sys.stderr)
                                audio = np.concatenate(speech_buf)
                                threading.Thread(
                                    target=self._recognize_and_inject,
                                    args=(audio,),
                                    daemon=True,
                                    name="asr",
                                ).start()
                            speech_buf = []
                            in_speech = False
                            silence_frames = 0
                            hangover_frames = 0

    # ---------- 每句独立线程 ----------

    def _recognize_and_inject(self, audio: np.ndarray) -> None:
        if audio.size < SAMPLERATE // 3:  # < 300ms 直接丢
            return
        t0 = time.time()
        try:
            text = self.asr.transcribe(audio)
        except ASRError as e:
            print(f"[asr error] {e}", file=sys.stderr)
            self._fb_error(str(e))
            return
        except Exception as e:  # noqa: BLE001
            print(f"[asr unexpected] {e!r}", file=sys.stderr)
            self._fb_error(repr(e))
            return
        dt = time.time() - t0
        dur = audio.size / SAMPLERATE
        print(f"→ ({dur:.1f}s → {dt:.2f}s) {text!r}", file=sys.stderr,
              flush=True)
        if text:
            # 标点修复（可选；fail-open，内部出错返回原文）
            if self.punctuator is not None:
                t1 = time.time()
                text = self.punctuator.punctuate(text)
                dtp = time.time() - t1
                if dtp > 0.05:
                    print(f"  (punct {dtp:.2f}s) {text!r}", file=sys.stderr,
                          flush=True)
            try:
                self.injector.inject(text)
            except Exception as e:  # noqa: BLE001
                print(f"[inject error] {e!r}", file=sys.stderr)
            if self.feedback is not None:
                try:
                    self.feedback.on_result(text)
                except Exception:  # noqa: BLE001
                    pass

        if self.on_segment is not None:
            try:
                self.on_segment(SegmentEvent(
                    audio=audio, duration_s=dur, text=text, elapsed_s=dt,
                ))
            except Exception:  # noqa: BLE001
                pass

    def _fb_error(self, msg: str) -> None:
        if self.feedback is not None:
            try:
                self.feedback.on_error(msg)
            except Exception:  # noqa: BLE001
                pass
