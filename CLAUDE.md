# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目性质

Linux X11 桌面语音输入工具：按 F9 说话 → SenseVoice ASR 识别 → 自动粘贴到当前光标位置（GUI 用 Ctrl+V，终端用 Shift+Insert）。

**这是已部署的生产工具，不是开发中的项目**。venv 借用外部项目 `~/work/test/.venv`（Python 3.12，funasr 1.4.1），本目录无独立环境、无依赖管理文件、无测试框架。

**当前状态（2026-09-20）：服务已恢复运行（enabled）**。当日事件：模型缓存 8月18 被清空导致崩溃循环（计数 12 万+）→ 手动重下 SenseVoiceSmall 后恢复；期间修复了 unit 文件 `StartLimitIntervalSec/StartLimitBurst` 误放 `[Service]` 段（被 systemd 忽略）的问题，已移至 `[Unit]`。若再次失效：`~/work/test/.venv/bin/modelscope download --model iic/SenseVoiceSmall`（约 894MB）→ 确认软链存在 → `systemctl --user daemon-reload && systemctl --user enable --now voice-input`。

## 常用命令

```bash
# 语法检查（唯一的自动化验证手段，无测试）
~/work/test/.venv/bin/python -m py_compile asr_hotkey.py

# 手动前台运行（调试用，先 systemctl --user stop voice-input 避免热键冲突）
./run.sh
~/work/test/.venv/bin/python asr_hotkey.py --debug    # 看 RMS / VAD 状态
~/work/test/.venv/bin/python asr_hotkey.py --model paraformer  # 换模型对比

# 服务管理（服务文件软链在 ~/.config/systemd/user/，直接编辑本目录的即生效）
systemctl --user restart voice-input
systemctl --user status voice-input
journalctl --user -u voice-input -f                    # 实时日志
# 改 voice-input.service 后需要：
systemctl --user daemon-reload && systemctl --user restart voice-input
```

端到端验证只能手动：光标点入文本框 → 按 F9 说话 → 再按 F9 → 确认文本粘贴且断句正常。

## 架构（asr_hotkey.py，单文件 ~390 行）

三线程流水线，理解数据流是改动的前提：

```
sounddevice 录音回调线程          worker 线程                    每句独立线程
  │ 30ms 帧 put 进 audio_q  →     │ 能量 VAD 断句          →     │ _recognize_and_inject:
  │                               │ (RMS 阈值 + 静音计时)         │ recognize() → inject()
```

- **能量 VAD**（非模型 VAD）：RMS > `--energy` 算语音；连续静音 `--sil-ms`（默认 400ms）判定一句结束；`hangover_ms` 保留句尾防止砍字；短于 300ms 的段丢弃。
- **热键回调必须不阻塞**：pynput 回调里 stop 走独立线程；worker 里每句识别注入也是独立线程（识别耗时不能挡住下一句的 VAD 累积）。
- **`recognize()` 双分支**：`sensevoice`（默认，无热词，输出需用正则剥 `<|xx|>` 标签）vs `paraformer`（支持 hotwords.txt，需额外加载 VAD/Punc 两个模型）。两分支互不通用。
- **`inject()` 的终端适配**：文本双写 CLIPBOARD + PRIMARY；`xprop WM_CLASS` 探测激活窗口是否终端（`_TERMINAL_CLASS_KEYWORDS` 列表），终端发 `Shift+Insert`，否则 `Ctrl+V`。新增终端类型 = 往该列表加关键词。
- **模型加载**：直接读本地 `~/.cache/modelscope/models/*/snapshots/master`，启动时检查目录、缺失即 exit，不联网。

## 硬约束

- **仅 X11**（xdotool/xclip/pynput 依赖），Wayland 下不工作。
- **仅 CPU**：GPU 是 GTX 965M（CC 5.2，2GB），funasr 强制 `device="cpu"`，别动。
- 音频必须 16kHz mono float32（SAMPLERATE 常量），Paraformer 的硬性要求。

## 已知问题（见 notes.md）

服务存在**内存膨胀 + OOM 崩溃循环**（RSS 从 ~112MB 涨到 ~2.5GB，历史重启计数超 12 万次，`oom_score_adj=200` 优先被杀）。改动涉及音频缓冲、推理调用、线程生命周期时，注意排查累积泄漏；临时缓解可给服务加 `MemoryMax=1G`。机器仅 11GiB 内存且无 swap，本服务的内存压力曾连带拖垮其他任务。
