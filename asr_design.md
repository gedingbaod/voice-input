# 语音输入法设计文档：按 F9 说话 → 中文自动粘贴到光标位置

- **项目路径**：`~/work/test/`
- **主程序**：`asr_hotkey.py`
- **热词表**：`hotwords.txt`
- **使用说明**：`README_asr.md`
- **文档日期**：2026-08-11

---

## 1. 背景

用户希望在 Linux 桌面上做一个**类输入法**的工具：按热键 → 说话 → 抬手后中文自动出现在**当前 focus 的输入位置**（文本框、聊天窗口、终端命令行都行）。

最初尝试用本地已下载的 `Qwen3-TTS-12Hz-0.6B-Base`，但那个模型是 **TTS（Text-to-Speech，文字→语音）**，用不了。想要的能力是 **ASR（Automatic Speech Recognition，语音→文字）**，方向反了，改用 funasr。

## 2. 需求

| 类别 | 需求 |
|---|---|
| 输入 | 麦克风实时录音 |
| 输出 | 汉字（中英混合） |
| 交互 | 全局热键（F9）开始/停止 |
| 注入 | 识别结果自动出现在**当前 focus 窗口的光标位置**（不限文本框 / 终端） |
| 流式 | 边说边处理，减小延迟 |
| 场景 | 中英混合 + 金融术语 + 代码/技术术语（docker、TypeScript、K 线、主力合约等） |
| 平台 | Linux 本机运行 |

## 3. 硬件

`nvidia-smi` 实测：

| 项 | 值 | 影响 |
|---|---|---|
| GPU | GTX 965M（笔记本 Maxwell） | 老卡 |
| 显存 | 2 GB（≈1987 MiB 空闲） | 装不下大模型 |
| Compute Capability | **5.2** | ⚠️ 不支持 bf16 / flash-attention；较新 PyTorch 轮子可能已放弃 CC<6.0 支持 |
| 桌面 | `XDG_SESSION_TYPE=x11`，`DISPLAY=:1` | 可直接用 X11 系工具链（xdotool / xclip） |

**结论**：全流程走 CPU，避开 GPU 陷阱。

## 4. 技术选型

### 4.1 ASR 模型：funasr Paraformer-zh-streaming

从 4 个候选中选出，决策矩阵：

| 模型 | 中文精度 | 流式 | 中英混 | CPU 可行 | 热词支持 |
|---|---|---|---|---|---|
| **funasr Paraformer-zh-streaming** ✅ | 高 | ✅ 真流式（chunk-conformer） | 中等 | ✅ | ✅ 原生 `hotword` |
| SenseVoice-Small | 更高（多语种） | ❌ 只能"伪流式"（VAD 切段） | 强 | ✅ | ❌ |
| Whisper large-v3 | 高（通用） | ❌ 整段模型 | 强 | 慢 | ❌ |
| Qwen3-TTS（用户最初选的） | — | — | — | — | **方向错误：TTS 非 ASR** |

流式是硬需求（用户明确要求"边说边出字"），所以选 Paraformer-zh-streaming。

### 4.2 配套模型

funasr 的经典三件套：

| 模型 ID | 作用 | 大小 |
|---|---|---|
| `iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online` | 流式 ASR 主模型 | ~220 MB |
| `iic/speech_fsmn_vad_zh-cn-16k-common-pytorch` | VAD 断句 | ~30 MB |
| `iic/punc_ct-transformer_zh-cn-common-vad_realtime-vocab272727` | 实时标点 | ~150 MB |

> funasr 里 `AutoModel(model="paraformer-zh-streaming", vad_model="fsmn-vad", punc_model="ct-punc")`
> 用的是**短名**，它内部会解析成上面三个真实 model_id 去 ModelScope 拉。手动下载必须用真名。

首次运行 funasr 会自动从 ModelScope 拉到 `~/.cache/modelscope/hub/iic/` 下。

### 4.3 音频采集：`sounddevice`

- 依赖 `libportaudio2`（系统包，已装）
- 采样：16 kHz, mono, float32（Paraformer 的强制要求）
- 优先级：`sounddevice` > `pyaudio`（pyaudio 编译更麻烦）

### 4.4 全局热键：`pynput`

- 在 X11 会话下 `pynput.keyboard.GlobalHotKeys({'<f9>': ...})` 可直接用
- 若热键被桌面环境占用，改成 `<f8>` 等（README 已说明）
- Wayland 下会受限，本设计限定 X11

### 4.5 光标注入：**剪贴板 + xdotool Ctrl+V**

两个方案对比：

| 方案 | 中英混 | Emoji | 稳定性 | 备注 |
|---|---|---|---|---|
| **`xclip -selection clipboard` + `xdotool key ctrl+v`** ✅ | ✅ | ✅ | 高 | 会覆盖当前剪贴板 |
| `xdotool type` 直接敲键 | 依赖 IME | ❌ | 中 | 中文不稳 |

选剪贴板方案。

## 5. 交互流程

```
┌───────────────────┐
│  程序启动          │
│  1) 加载 3 个模型  │  ← 首次 ~3-5 min 从 ModelScope 下载
│  2) 加载 hotwords │
│  3) 监听 F9        │
└─────────┬─────────┘
          │
    ┌─────▼──────┐
    │  等待 F9    │◄────────────────────────┐
    └─────┬──────┘                         │
          │ 按下                            │
    ┌─────▼──────────────┐                 │
    │  开麦，"● 录音中…" │                 │
    └─────┬──────────────┘                 │
          │ 再按 F9                         │
    ┌─────▼──────────────┐                 │
    │  停麦，取音频      │                 │
    ├────────────────────┤                 │
    │  流式喂给 model    │                 │
    │  最后一 chunk       │                 │
    │  is_final=True     │                 │
    ├────────────────────┤                 │
    │  拿到文本 (含标点) │                 │
    ├────────────────────┤                 │
    │  xclip 写剪贴板    │                 │
    │  sleep 100ms       │  ← 让焦点回来   │
    │  xdotool ctrl+v    │                 │
    └─────┬──────────────┘                 │
          │                                │
          └────────────────────────────────┘
```

按 Ctrl+C 退出主程序。

## 6. 关键参数

```python
SAMPLERATE       = 16000       # Paraformer 只吃 16 kHz mono
CHUNK_STRIDE_MS  = 600         # 每次喂给模型的音频长度
chunk_size       = [0, 10, 5]  # 官方推荐 lookback（≈600ms）
encoder_chunk_look_back = 4
decoder_chunk_look_back = 1
device           = "cpu"       # GTX 965M 太老，强制 CPU
disable_update   = True        # 关闭版本检查降噪
```

- 若延迟大：`chunk_size` 降为 `[0, 8, 4]`（快，边界字可能碎）
- 若 CPU 满载：加 `ncpu=2`（默认全用）

## 7. 代码结构（`asr_hotkey.py`，~180 行）

```
Recorder              # 麦克风采集，thread-safe start/stop
  ├── start()         # sd.InputStream + callback 累计帧
  └── stop() -> np.ndarray

load_hotwords(path)   # 读 hotwords.txt，返回空格分隔的字符串

recognize(model, audio, hotwords) -> str
  # 按 CHUNK_SAMPLES 分块喂给 model.generate
  # 最后一 chunk 传 is_final=True
  # 拼接每 chunk 输出

inject(text)          # xclip 写剪贴板 → xdotool key ctrl+v

main()
  # 1) AutoModel(paraformer + vad + punc, device="cpu")
  # 2) 加载热词
  # 3) GlobalHotKeys({<f9>: on_f9})
  # 4) F9 状态机：not recording → start；recording → 新线程做 stop+识别+注入
```

## 8. 热词表设计

`hotwords.txt`，每行一个词，`#` 开头为注释。初始预置 30 条：

**金融**：K线 / 均线 / 多头 / 空头 / 主力合约 / 期货 / 期权 / 涨停 / 跌停 / 注册制 / ETF / 沪深300 / 创业板 / 科创板 / 量化 / 回测 / 因子

**代码/技术**：docker / kubernetes / TypeScript / Python / async / await / Redis / PostgreSQL / DolphinDB / Claude / API / SDK

用户可以直接编辑扩充。funasr 的 `hotword` 参数会显著提升这些词的识别率。

## 9. 依赖清单

### Python（`~/work/test/.venv`，通过 `uv pip install --python .venv/bin/python`）

- `funasr==1.4.1`
- `sounddevice==0.5.5`
- `pynput==1.8.2`
- `numpy` 2.4.6（uv 会自动升级）
- 隐式依赖：torch, torchaudio, modelscope, evdev, python-xlib 等

### 系统（apt）

- `libportaudio2`（sounddevice 需要）— 已装
- `xdotool` — 已装
- `xclip` — 已装

## 10. 关键陷阱

1. **模型方向**：`Qwen3-TTS` 是 TTS 不是 ASR，选错就废
2. **CC 5.2 老卡**：不装 flash-attn，强制 `device="cpu"`
3. **音频参数**：Paraformer 只吃 16k mono float32，别喂错
4. **`is_final=True`**：不在最后一 chunk 传，decoder 不吐尾字
5. **`GlobalHotKeys` 需要 X 权限**：Wayland 不支持；X11 通常直接可用
6. **F9 按完延迟 100ms 再粘贴**：给焦点回到目标窗口留时间
7. **剪贴板会被覆盖**：本次实现未做备份/恢复（可作为后续迭代）
8. **首次下载 ~400MB**：无网络会静默卡住，需在 README 里明确提示

## 11. 验证方案

| 步骤 | 命令 / 操作 | 预期 |
|---|---|---|
| 装包 | `uv pip install --python .venv/bin/python funasr sounddevice pynput` | 无报错，funasr 1.4.1 |
| 语法 | `.venv/bin/python -m py_compile asr_hotkey.py` | `compile OK` |
| 麦克风 | `.venv/bin/python -c "import sounddevice as sd; print(sd.query_devices())"` | 至少 1 个输入设备 |
| 剪贴板 | `echo hi \| xclip -selection clipboard && xclip -o -selection clipboard` | 输出 `hi` |
| 模型下载 | `.venv/bin/python -c "from funasr import AutoModel; AutoModel(model='paraformer-zh-streaming', vad_model='fsmn-vad', punc_model='ct-punc', device='cpu', disable_update=True)"` | 首次 3-5 min，出现 "loaded" 日志 |
| 端到端 | 光标点入文本框 → 跑主程序 → 按 F9 说"今天沪深300 ETF 涨了三个点，docker 容器重启" → 再按 F9 | 文本框粘贴出接近原意的中文 |

## 12. 后续迭代（不在本次范围）

- 自定义热键（当前硬编码 F9）
- 剪贴板备份/恢复
- 支持从命令行传入音频文件做非实时识别（`AutoModel` 走 non-streaming 分支）
- 识别结果先在悬浮窗预览，Enter 后再注入（防误粘）
- 打包 `systemd --user` 服务开机自启
- Wayland 兼容（改 `wtype` + `ydotool`，需 `/dev/uinput` 权限）
