# voice_input — F9 语音输入法（SenseVoice + 整句流式）

按 **F9** 说话 → 中文自动粘贴到当前光标位置。基于 funasr + SenseVoice-Small，
CPU 可跑，识别精度好，中英混支持，同时兼容 **GUI 窗口和终端**。

## 效果特点

- **整句流式**：说完一句自动出字，不用等整段结束（VAD 检测停顿，默认 400ms 换气即切句）
- **同时支持文本框和终端**：自动探测目标窗口类型，GUI 用 `Ctrl+V`，终端用 `Shift+Insert`
- **开机自启**：systemd --user 集成，登录后自动跑
- **模型本地加载**：不联网，从 `~/.cache/modelscope/models/` 直接读

## 目录内容

| 文件 | 说明 |
|---|---|
| `asr_hotkey.py` | 主程序 |
| `asr_hotkey_paraformer.py` | Paraformer 版本备份，仅留档 |
| `hotwords.txt` | 热词表（仅 Paraformer 生效；SenseVoice 不支持热词）|
| `run.sh` | 服务启动脚本（自动补 DISPLAY / XAUTHORITY，调用 venv 里的 python） |
| `voice-input.service` | systemd --user unit（软链在 `~/.config/systemd/user/`）|
| `README.md` | 本文档 |
| `README_asr.md` | 早期使用说明（模型下载三种方式、故障排查等留档）|
| `asr_design.md` | 设计文档（技术选型、陷阱清单、验证方案）|

## 依赖

- **venv**：`~/work/test/.venv`（funasr / sounddevice / pynput 已装）
- **系统命令**：`xdotool`、`xclip`、`xprop`
- **X11 会话**（`echo $XDG_SESSION_TYPE` 显示 `x11`）
- **模型**：三个都在 `~/.cache/modelscope/models/` 下
  - `iic--SenseVoiceSmall`（默认使用，~940 MB）
  - `iic--speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online`（备用）
  - `iic--speech_fsmn_vad_zh-cn-16k-common-pytorch`（Paraformer 才需要）
  - `iic--punc_ct-transformer_zh-cn-common-vad_realtime-vocab272727`（Paraformer 才需要）

## 用法

### 交互流程

1. 按 **F9** → 出现 `● 录音中… 再按热键停止`
2. **连续说话**，中间自然停顿即可 —— 每停顿 ~400ms 就自动识别 + 粘贴一段到光标
3. 再按 **F9** → 停止录音；剩余未识别的最后一段也会补识别一次

日志示例：
```
→ (2.3s → 0.6s) '今天沪深300 ETF 涨了三个点。'
→ (1.8s → 0.5s) 'docker 容器重启一下。'
```
括号里是 "音频长度 → 识别耗时"。

### 光标注入的机制

X11 有两套剪贴板 —— GUI 程序用 CLIPBOARD（Ctrl+V），终端普遍用 PRIMARY（Shift+Insert）。
本程序会：

1. **同时写两份**：把识别文本同时写入 CLIPBOARD 和 PRIMARY
2. **探测目标窗口**：`xprop WM_CLASS` 拿到窗口类型
3. **按窗口类型选热键**：
   - 检出 `kitty / alacritty / konsole / gnome-terminal / xterm / rxvt / wezterm / tilix / terminator / foot / hyper / tabby` 等 → **`Shift+Insert`**
   - 其他 → **`Ctrl+V`**

这就是为什么在 gedit / VSCode / 浏览器 / 聊天软件、以及各种终端里都能生效。

## 手动运行

```bash
cd ~/work/voice_input
./run.sh                                            # 走服务同款默认参数
# 或直接：
~/work/test/.venv/bin/python asr_hotkey.py         # 默认 SenseVoice + 400ms 断句
~/work/test/.venv/bin/python asr_hotkey.py --hotkey '<f8>'
~/work/test/.venv/bin/python asr_hotkey.py --model paraformer  # 换模型对比
~/work/test/.venv/bin/python asr_hotkey.py --debug             # 看 VAD 实时状态
```

## 开机自启（systemd --user）

已 `enable`，登录到 graphical session 后自动启动。

```bash
# 查看状态
systemctl --user status voice-input

# 手动启动/停止/重启
systemctl --user start voice-input
systemctl --user stop voice-input
systemctl --user restart voice-input

# 看日志
journalctl --user -u voice-input -f                 # 实时
journalctl --user -u voice-input -n 100 --no-pager  # 最近 100 行

# 禁用/重新启用开机启动
systemctl --user disable voice-input
systemctl --user enable voice-input
```

### 修改默认参数

编辑 `voice-input.service`，`[Service]` 下加或改：

```ini
Environment=VOICE_INPUT_ARGS=--model sensevoice --sil-ms 500 --hotkey <f8>
```

改完执行：

```bash
systemctl --user daemon-reload
systemctl --user restart voice-input
```

## 命令行参数速查

| 参数 | 默认 | 说明 |
|---|---|---|
| `--model` | `sensevoice` | `sensevoice`（精度好、无热词）或 `paraformer`（支持热词）|
| `--hotkey` | `<f9>` | 全局热键；如 `<f8>`、`<pause>`、`<scroll_lock>` |
| `--sil-ms` | `400` | 静音门限；连续 N ms 静音算一句结束（换气触发） |
| `--energy` | `0.005` | 能量阈值；RMS < 此值算静音 |
| `--debug` | — | 打印实时 RMS、VAD 状态转换、每段识别信息 |

## 调优速查

| 现象 | 试这个 |
|---|---|
| 说话中间一句被切成两半 | `--sil-ms 600` 或更大 |
| 说完停很久才出字 | `--sil-ms 300` 或更小 |
| 一直在录、始终不断句 | 环境噪声大，`--energy 0.01` 加大门限；或先 `--debug` 看 RMS |
| 我说话小声、录不到 | `--energy 0.002` 降低门限 |
| F9 被其他程序占用 | `--hotkey '<f8>'` 之类 |
| 想诊断 | `--debug` |

## 故障排查

- **F9 无响应**
  - 桌面环境（GNOME / KDE）可能占用了 F9，改热键
  - 或 X 权限问题：`xhost +si:localuser:$(whoami)`
- **某终端里粘不上**
  - 极少数终端 `Shift+Insert` 不是粘贴，可以在源码里把 `key = "shift+Insert"` 改成 `key = "ctrl+shift+v"`（多数终端也认这个）
  - 如果是我们没识别到的终端类型，`xprop -id "$(xdotool getactivewindow)" WM_CLASS` 拿到类名，把它加进 `asr_hotkey.py` 的 `_TERMINAL_CLASS_KEYWORDS`
- **服务起不来**：`journalctl --user -u voice-input -n 50 --no-pager`
- **登录后没自动起**
  - `systemctl --user is-enabled voice-input` 应为 `enabled`
  - `systemctl --user status graphical-session.target` 应为 active
- **`inject` 提示 xdotool/xclip 缺失**：`sudo apt install xdotool xclip x11-utils`
- **模型加载失败**：检查 `~/.cache/modelscope/models/iic--SenseVoiceSmall/snapshots/master/` 有 `model.pt` 等文件

## 卸载

```bash
systemctl --user disable --now voice-input
rm ~/.config/systemd/user/voice-input.service
systemctl --user daemon-reload
# 目录里的源文件按需保留或删除
```

## 版本历史

| 阶段 | 关键变更 |
|---|---|
| v0 | 最初尝试 Qwen3-TTS → 发现方向反了（TTS 非 ASR），换 funasr |
| v1 | Paraformer-online + FSMN-VAD + CT-Punc，push-to-talk（按 F9 停止后一次识别）|
| v2 | 换 SenseVoice-Small，精度明显提升；加 `--model` 切换保留 Paraformer 备份 |
| v3 | 改成整句流式：说完一句立即出字（能量 VAD 断句 + 后台识别线程）|
| v4 | 补自动区分 GUI/终端：探测窗口 WM_CLASS，终端用 Shift+Insert；同时双写 CLIPBOARD+PRIMARY |
| v5 | systemd --user 集成，开机自启；目录整体搬到 `~/work/voice_input/` |
