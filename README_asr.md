# 中文流式 ASR — 按 F9 说话，中文自动粘贴到光标位置

## 首次准备

```bash
# venv 已存在：~/work/test/.venv
# 依赖已经装好：funasr / sounddevice / pynput（uv 装的）
# 系统命令（已装）：xdotool、xclip、libportaudio2
```

首次运行时会自动从 ModelScope 拉取 3 个模型到 `~/.cache/modelscope/hub/`，共约 400 MB：

| 模型 ID | 用途 | 大小 |
|---|---|---|
| `iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online` | 流式 ASR | ~220 MB |
| `iic/speech_fsmn_vad_zh-cn-16k-common-pytorch` | VAD 断句 | ~30 MB |
| `iic/punc_ct-transformer_zh-cn-common-vad_realtime-vocab272727` | 实时标点 | ~150 MB |

> 注：`paraformer-zh-streaming` / `fsmn-vad` / `ct-punc` 是 funasr 在 `AutoModel(model=...)` 里用的**短名**，
> ModelScope 网站上搜不到；下载时必须用上面那一列的完整 `iic/...` 真实 model_id。

## 手动下载模型（推荐提前拉，避免首次启动等）

funasr 会在 `~/.cache/modelscope/hub/<model_id>/` 下查模型。下面三条命令任选其一。

### 方式 A：用 `modelscope` Python SDK（最稳）

```bash
cd ~/work/test
.venv/bin/python - <<'PY'
from modelscope import snapshot_download
for mid in [
    "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online",
    "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
    "iic/punc_ct-transformer_zh-cn-common-vad_realtime-vocab272727",
]:
    path = snapshot_download(mid)
    print(mid, "->", path)
PY
```

如果提示 `No module named 'modelscope'`：`.venv/bin/pip install modelscope`（funasr 依赖里通常已带）。

### 方式 B：ModelScope CLI

```bash
.venv/bin/pip install modelscope  # 若未安装
.venv/bin/modelscope download --model iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online
.venv/bin/modelscope download --model iic/speech_fsmn_vad_zh-cn-16k-common-pytorch
.venv/bin/modelscope download --model iic/punc_ct-transformer_zh-cn-common-vad_realtime-vocab272727
```

### 方式 C：git clone（国内网络稳，但要装 git-lfs）

```bash
sudo apt install git-lfs && git lfs install
mkdir -p ~/.cache/modelscope/hub/iic && cd ~/.cache/modelscope/hub/iic
git clone https://www.modelscope.cn/iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online.git
git clone https://www.modelscope.cn/iic/speech_fsmn_vad_zh-cn-16k-common-pytorch.git
git clone https://www.modelscope.cn/iic/punc_ct-transformer_zh-cn-common-vad_realtime-vocab272727.git
```

### 验证下载成功

```bash
ls -la ~/.cache/modelscope/hub/iic/
# 应看到三个目录，每个里面都有 config.yaml / model.pt 等文件
```

### 让主程序直接加载（不再联网）

```bash
cd ~/work/test
.venv/bin/python -c "
from funasr import AutoModel
m = AutoModel(model='paraformer-zh-streaming',
              vad_model='fsmn-vad',
              punc_model='ct-punc',
              device='cpu', disable_update=True)
print('loaded OK')
"
```
看到 `loaded OK` 就万事俱备。

## 用法

```bash
.venv/bin/python asr_hotkey.py
```

- 光标点进任意可输入位置（文本框 / 聊天窗口 / 终端…）
- 按一次 **F9** → "● 录音中…"
- 说话，中英文混合都行
- 再按一次 **F9** → 识别 → 自动粘贴到光标

按 Ctrl+C 退出程序。

## 热词自定义

编辑 `hotwords.txt`，每行一个词；`#` 开头的行是注释。加你常说的专业术语可以显著提升识别率。

## 故障排查

- **F9 无响应**：桌面环境可能占用了 F9（GNOME/KDE 有时会），改成 F8：把 `asr_hotkey.py` 里
  `<f9>` 改为 `<f8>`。
- **麦克风采不到声音**：`.venv/bin/python -c "import sounddevice as sd; print(sd.query_devices())"`
  看看是否有输入设备；也可以 `pactl list sources short`。
- **`xdotool` 或 `xclip` 未装**：`sudo apt install xdotool xclip`。
- **`XDG_SESSION_TYPE` 不是 x11**：本方案基于 X11，Wayland 下 xdotool/pynput 有限制，
  换用 `wtype` + `ydotool` 需要额外配置。
- **首次识别很慢**：冷启加载模型 15~30 秒，之后每次识别通常 <1s。
- **粘贴时把当前剪贴板覆盖了**：本次实现未做剪贴板备份/恢复。

## 参数微调

- 觉得识别延迟大：`asr_hotkey.py` 里 `chunk_size=[0,10,5]` → 改成 `[0,8,4]`，会更快但边界字容易碎。
- 想更省 CPU：模型加载参数里加 `ncpu=2`（默认全用）。
