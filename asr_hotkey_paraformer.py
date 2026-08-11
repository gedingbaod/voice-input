"""按 F9 说话 → 中文自动粘贴到当前光标位置。

依赖：funasr, sounddevice, pynput（venv 已装）；xdotool, xclip（系统命令）。
"""
import os
import subprocess
import sys
import threading
import time

import numpy as np
import sounddevice as sd
from funasr import AutoModel
from pynput import keyboard

SAMPLERATE = 16000
CHUNK_STRIDE_MS = 600
CHUNK_SAMPLES = int(SAMPLERATE * CHUNK_STRIDE_MS / 1000)
HERE = os.path.dirname(os.path.abspath(__file__))
HOTWORDS_PATH = os.path.join(HERE, "hotwords.txt")

# 直接指向已下载的模型 snapshot，避免联网检查
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


class Recorder:
    def __init__(self):
        self.frames: list[np.ndarray] = []
        self.recording = False
        self.stream: sd.InputStream | None = None
        self._lock = threading.Lock()

    def start(self):
        with self._lock:
            if self.recording:
                return
            self.frames = []
            self.recording = True
            self.stream = sd.InputStream(
                samplerate=SAMPLERATE,
                channels=1,
                dtype="float32",
                callback=self._cb,
            )
            self.stream.start()
        print("● 录音中… 再按 F9 停止", file=sys.stderr, flush=True)

    def _cb(self, indata, frames, t, status):
        if status:
            print(f"[audio] {status}", file=sys.stderr)
        if self.recording:
            self.frames.append(indata.copy().reshape(-1))

    def stop(self) -> np.ndarray:
        with self._lock:
            if not self.recording:
                return np.zeros(0, np.float32)
            self.recording = False
            if self.stream:
                self.stream.stop()
                self.stream.close()
                self.stream = None
        if not self.frames:
            return np.zeros(0, np.float32)
        return np.concatenate(self.frames).astype(np.float32)


def load_hotwords(path: str) -> str:
    if not os.path.exists(path):
        return ""
    words = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            words.append(line)
    joined = " ".join(words)
    if joined:
        print(f"loaded {len(words)} hotwords", file=sys.stderr)
    return joined


def recognize(model, audio: np.ndarray, hotwords: str) -> str:
    """一次性喂整段音频，交给内置 VAD 切段 + ASR + Punc。

    push-to-talk 场景下用户已经手动控制起止，直接整段识别更简单也更准。
    真流式（cache + is_final）只能在不启用 vad_model 的前提下用。
    """
    if audio.size < SAMPLERATE // 2:
        return ""
    res = model.generate(
        input=audio,
        batch_size_s=60,
        hotword=hotwords or None,
    )
    if not res:
        return ""
    # res 是 list[dict]，每个 dict 一个 VAD 段
    return "".join(r.get("text", "") for r in res).strip()


def inject(text: str):
    if not text:
        return
    try:
        subprocess.run(
            ["xclip", "-selection", "clipboard"],
            input=text.encode("utf-8"),
            check=True,
        )
        subprocess.run(
            ["xdotool", "key", "--clearmodifiers", "ctrl+v"],
            check=True,
        )
    except FileNotFoundError as e:
        print(f"[inject] missing tool: {e}. install xdotool + xclip", file=sys.stderr)
    except subprocess.CalledProcessError as e:
        print(f"[inject] failed: {e}", file=sys.stderr)


def main():
    print("加载模型…（首次冷启约 45s）", file=sys.stderr)
    for p in (MODEL_PARAFORMER, MODEL_VAD, MODEL_PUNC):
        if not os.path.isdir(p):
            sys.exit(f"模型目录不存在: {p}")
    model = AutoModel(
        model=MODEL_PARAFORMER,
        vad_model=MODEL_VAD,
        punc_model=MODEL_PUNC,
        device="cpu",
        disable_update=True,
    )
    hotwords = load_hotwords(HOTWORDS_PATH)
    print("就绪。按 F9 开始/停止录音（Ctrl+C 退出）", file=sys.stderr)

    rec = Recorder()
    busy = threading.Lock()

    def process_and_inject():
        with busy:
            audio = rec.stop()
            if audio.size == 0:
                print("(empty audio)", file=sys.stderr)
                return
            print(f"识别中… ({audio.size / SAMPLERATE:.1f}s)", file=sys.stderr)
            t0 = time.time()
            text = recognize(model, audio, hotwords)
            dt = time.time() - t0
            print(f"→ {text!r}  ({dt:.2f}s)", file=sys.stderr)
            if text:
                # 稍等 100ms 让用户按完 F9 焦点回到目标输入框
                time.sleep(0.1)
                inject(text)

    def on_f9():
        if not rec.recording:
            rec.start()
        else:
            threading.Thread(target=process_and_inject, daemon=True).start()

    try:
        with keyboard.GlobalHotKeys({"<f9>": on_f9}) as h:
            h.join()
    except KeyboardInterrupt:
        print("bye", file=sys.stderr)


if __name__ == "__main__":
    main()
