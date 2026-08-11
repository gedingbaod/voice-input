"""按 F9 说话 → 中文自动粘贴到当前光标位置（整句流式版）。

工作模式：
  - 按第一次 F9：开启麦克风，进入"整句流式"状态
  - 说话时基于能量做 VAD 断句；每检测到一段完整语音（用户停顿 ~700ms），
    立即把该段送给 SenseVoice 识别 → 剪贴板 + Ctrl+V 注入到当前光标
  - 再按 F9：停止录音，把最后一段没识别的也识别 + 注入

用法：
    .venv/bin/python asr_hotkey.py                     # 默认 SenseVoice
    .venv/bin/python asr_hotkey.py --model paraformer  # 切回 Paraformer
    .venv/bin/python asr_hotkey.py --hotkey '<f8>'     # 换热键
    .venv/bin/python asr_hotkey.py --sil-ms 500        # 调静音门限（默认 700ms）

依赖：funasr, sounddevice, pynput；xdotool, xclip。
"""
from __future__ import annotations

import argparse
import os
import queue
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass

import numpy as np
import sounddevice as sd
from funasr import AutoModel
from pynput import keyboard

SAMPLERATE = 16000
FRAME_MS = 30                                # 每帧 30ms
FRAME_SAMPLES = SAMPLERATE * FRAME_MS // 1000
MIN_SPEECH_MS = 300                          # <300ms 认为是噪声，丢弃
HERE = os.path.dirname(os.path.abspath(__file__))
HOTWORDS_PATH = os.path.join(HERE, "hotwords.txt")

_MODELSCOPE_MODELS = os.path.expanduser("~/.cache/modelscope/models")
MODEL_PARAFORMER = os.path.join(
    _MODELSCOPE_MODELS,
    "iic--speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online/snapshots/master",
)
MODEL_VAD = os.path.join(
    _MODELSCOPE_MODELS,
    "iic--speech_fsmn_vad_zh-cn-16k-common-pytorch/snapshots/master",
)
MODEL_PUNC = os.path.join(
    _MODELSCOPE_MODELS,
    "iic--punc_ct-transformer_zh-cn-common-vad_realtime-vocab272727/snapshots/master",
)
MODEL_SENSEVOICE = os.path.join(
    _MODELSCOPE_MODELS,
    "iic--SenseVoiceSmall/snapshots/master",
)

_SENSEVOICE_TAG_RE = re.compile(r"<\|[^|>]*\|>")


@dataclass
class VADConfig:
    """能量 VAD 参数"""
    silence_ms: int = 700                    # 静音超过此值 → 一句结束
    energy_threshold: float = 0.005          # RMS 阈值（float32 音频，经验值）
    hangover_ms: int = 200                   # 语音结束后额外保留的尾巴，防止句尾字被砍
    debug: bool = False                      # 打印每帧 RMS / 每次状态转换


class StreamRecognizer:
    """录音 + VAD 断句 + 识别 + 注入 的整体状态机"""

    def __init__(self, model_name: str, model, hotwords: str, vad_cfg: VADConfig):
        self.model_name = model_name
        self.model = model
        self.hotwords = hotwords
        self.cfg = vad_cfg

        self.stream: sd.InputStream | None = None
        self.running = False

        # 每帧塞入的队列（sd callback 线程 → worker 线程）
        self.audio_q: "queue.Queue[np.ndarray | None]" = queue.Queue()
        self.worker: threading.Thread | None = None

    # ---------- 主线程接口 ----------
    def start(self):
        if self.running:
            return
        self.running = True
        self.stream = sd.InputStream(
            samplerate=SAMPLERATE,
            channels=1,
            dtype="float32",
            blocksize=FRAME_SAMPLES,
            callback=self._sd_cb,
        )
        self.stream.start()
        self.worker = threading.Thread(target=self._worker, daemon=True)
        self.worker.start()
        print("● 录音中… 再按热键停止", file=sys.stderr, flush=True)

    def stop(self):
        if not self.running:
            return
        self.running = False
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            finally:
                self.stream = None
        # 通知 worker 停止
        self.audio_q.put(None)
        if self.worker is not None:
            self.worker.join(timeout=15)
            self.worker = None
        print("● 停止", file=sys.stderr, flush=True)

    # ---------- 音频回调（sounddevice 线程）----------
    def _sd_cb(self, indata, frames, t, status):
        if status:
            print(f"[audio] {status}", file=sys.stderr)
        if self.running:
            # indata: (N,1) float32
            self.audio_q.put(indata.copy().reshape(-1))

    # ---------- worker：VAD + 识别 + 注入 ----------
    def _worker(self):
        speech_buf: list[np.ndarray] = []       # 当前正在累积的一句
        in_speech = False
        silence_frames = 0
        hangover_frames = 0
        silence_limit = self.cfg.silence_ms // FRAME_MS
        hangover_limit = self.cfg.hangover_ms // FRAME_MS
        min_speech_frames = MIN_SPEECH_MS // FRAME_MS
        dbg_counter = 0
        peak_rms = 0.0

        while True:
            frame = self.audio_q.get()
            if frame is None:
                # 收尾：把剩余的最后一句识别掉
                if in_speech and len(speech_buf) >= min_speech_frames:
                    self._recognize_and_inject(np.concatenate(speech_buf))
                break

            rms = float(np.sqrt(np.mean(frame * frame)))
            peak_rms = max(peak_rms, rms)
            is_voice = rms > self.cfg.energy_threshold

            if self.cfg.debug:
                dbg_counter += 1
                # 每 15 帧 (~450ms) 打印一次当前 RMS + 状态
                if dbg_counter % 15 == 0:
                    print(
                        f"[vad] rms={rms:.4f} peak={peak_rms:.4f} "
                        f"thr={self.cfg.energy_threshold:.4f} "
                        f"state={'SPEECH' if in_speech else 'sil'} "
                        f"sil_frames={silence_frames}/{silence_limit}",
                        file=sys.stderr,
                    )

            if is_voice:
                if not in_speech:
                    in_speech = True
                    silence_frames = 0
                    if self.cfg.debug:
                        print(f"[vad] → SPEECH (rms={rms:.4f})", file=sys.stderr)
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
                                    print(
                                        f"[vad] → SIL, 触发识别 ({dur:.1f}s)",
                                        file=sys.stderr,
                                    )
                                audio = np.concatenate(speech_buf)
                                threading.Thread(
                                    target=self._recognize_and_inject,
                                    args=(audio,),
                                    daemon=True,
                                ).start()
                            speech_buf = []
                            in_speech = False
                            silence_frames = 0
                            hangover_frames = 0

    def _recognize_and_inject(self, audio: np.ndarray):
        if audio.size < SAMPLERATE // 3:     # <300ms 直接丢
            return
        t0 = time.time()
        try:
            text = recognize(self.model_name, self.model, audio, self.hotwords)
        except Exception as e:
            print(f"[asr error] {e}", file=sys.stderr)
            return
        dt = time.time() - t0
        dur = audio.size / SAMPLERATE
        print(f"→ ({dur:.1f}s → {dt:.2f}s) {text!r}", file=sys.stderr)
        if text:
            inject(text)


def load_hotwords(path: str) -> str:
    if not os.path.exists(path):
        return ""
    words = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                words.append(line)
    joined = " ".join(words)
    if joined:
        print(f"loaded {len(words)} hotwords", file=sys.stderr)
    return joined


def build_model(name: str):
    if name == "paraformer":
        for p in (MODEL_PARAFORMER, MODEL_VAD, MODEL_PUNC):
            if not os.path.isdir(p):
                sys.exit(f"模型目录不存在: {p}")
        return AutoModel(
            model=MODEL_PARAFORMER,
            vad_model=MODEL_VAD,
            punc_model=MODEL_PUNC,
            device="cpu",
            disable_update=True,
        )
    if name == "sensevoice":
        if not os.path.isdir(MODEL_SENSEVOICE):
            sys.exit(f"模型目录不存在: {MODEL_SENSEVOICE}")
        return AutoModel(
            model=MODEL_SENSEVOICE,
            device="cpu",
            disable_update=True,
        )
    sys.exit(f"unknown model: {name}")


def recognize(model_name: str, model, audio: np.ndarray, hotwords: str) -> str:
    if audio.size < SAMPLERATE // 3:
        return ""
    if model_name == "sensevoice":
        res = model.generate(
            input=audio,
            cache={},
            language="auto",
            use_itn=True,
            batch_size_s=60,
        )
    else:
        res = model.generate(
            input=audio,
            batch_size_s=60,
            hotword=hotwords or None,
        )
    if not res:
        return ""
    text = "".join(r.get("text", "") for r in res)
    if model_name == "sensevoice":
        text = _SENSEVOICE_TAG_RE.sub("", text)
    return text.strip()


# 终端窗口的 WM_CLASS 关键词（小写匹配）
_TERMINAL_CLASS_KEYWORDS = (
    "terminal", "term", "konsole", "alacritty", "kitty",
    "wezterm", "tilix", "terminator", "xterm", "rxvt",
    "urxvt", "st-256color", "foot", "hyper", "tabby",
)


def _target_is_terminal() -> bool:
    """探测当前 focus 窗口是不是终端。失败则视为非终端。

    xdotool 3.x 有 getwindowclassname 子命令，旧版没有；用 xprop 兜底最稳。
    """
    try:
        wid = subprocess.run(
            ["xdotool", "getactivewindow"],
            capture_output=True, text=True, timeout=1.5, check=True,
        ).stdout.strip()
        if not wid:
            return False
        r = subprocess.run(
            ["xprop", "-id", wid, "WM_CLASS"],
            capture_output=True, text=True, timeout=1.5, check=True,
        )
        # 输出形如：WM_CLASS(STRING) = "kitty", "kitty"
        cls = r.stdout.lower()
        return any(k in cls for k in _TERMINAL_CLASS_KEYWORDS)
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def inject(text: str):
    if not text:
        return
    try:
        # 双写：CLIPBOARD 用于 Ctrl+V，PRIMARY 用于 Shift+Insert（终端）
        subprocess.run(
            ["xclip", "-selection", "clipboard"],
            input=text.encode("utf-8"),
            check=True,
        )
        subprocess.run(
            ["xclip", "-selection", "primary"],
            input=text.encode("utf-8"),
            check=True,
        )
        # 终端类窗口用 Shift+Insert（多数终端支持），其他用 Ctrl+V
        if _target_is_terminal():
            key = "shift+Insert"
        else:
            key = "ctrl+v"
        subprocess.run(
            ["xdotool", "key", "--clearmodifiers", key],
            check=True,
        )
    except FileNotFoundError as e:
        print(f"[inject] missing tool: {e}", file=sys.stderr)
    except subprocess.CalledProcessError as e:
        print(f"[inject] failed: {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=["sensevoice", "paraformer"],
                    default="sensevoice")
    ap.add_argument("--hotkey", default="<f9>")
    ap.add_argument("--sil-ms", type=int, default=400,
                    help="静音门限：连续多少 ms 静音算一句结束（默认 400；说话不停顿也会因为换气触发）")
    ap.add_argument("--energy", type=float, default=0.005,
                    help="能量阈值，越大越难触发录音（默认 0.005）")
    ap.add_argument("--debug", action="store_true",
                    help="打印实时 RMS 和 VAD 状态，用来调参")
    args = ap.parse_args()

    print(f"加载 {args.model} 模型…（首次冷启约 30-60s）", file=sys.stderr)
    model = build_model(args.model)
    hotwords = load_hotwords(HOTWORDS_PATH) if args.model == "paraformer" else ""
    if args.model == "sensevoice" and os.path.exists(HOTWORDS_PATH):
        print("(SenseVoice 不支持 hotwords，已忽略)", file=sys.stderr)
    print(
        f"就绪。按 {args.hotkey.upper()} 开始/停止录音（Ctrl+C 退出）",
        file=sys.stderr,
    )
    print(
        f"配置：VAD 静音门限 {args.sil_ms}ms | 能量阈值 {args.energy}",
        file=sys.stderr,
    )

    vad_cfg = VADConfig(
        silence_ms=args.sil_ms,
        energy_threshold=args.energy,
        debug=args.debug,
    )
    stream_rec = StreamRecognizer(args.model, model, hotwords, vad_cfg)

    def on_hotkey():
        if not stream_rec.running:
            stream_rec.start()
        else:
            # 停止时另起线程，避免热键回调阻塞
            threading.Thread(target=stream_rec.stop, daemon=True).start()

    try:
        with keyboard.GlobalHotKeys({args.hotkey: on_hotkey}) as h:
            h.join()
    except KeyboardInterrupt:
        print("bye", file=sys.stderr)
        stream_rec.stop()


if __name__ == "__main__":
    main()
