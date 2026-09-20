# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) — 及其他 AI 编码工具 — when working with code in this repository.

## 项目性质

**跨平台语音输入客户端**（macOS / Linux X11 / Windows）：按 F9 整段录音 →
发送到局域网 Mac 上的 omlx 服务（Qwen3-ASR-1.7B，OpenAI 兼容协议）→
识别文本自动粘贴到光标。**详细设计文档：`docs/PLATFORM_GUIDE.md`（必读）。**

- **唯一服务端**：Mac 主机 omlx 0.6.4，`http://192.168.31.54:9999/v1`，Bearer `123456`
- **模型**：`Qwen3-ASR-1.7B-8bit`（原生标点+术语规范，2.46GB；替代过 SenseVoice+LLM补标点方案）
- **录音模式**：`whole` 整段（F9 开始→F9 停止→整段识别），VAD 流式仅作可选
- 历史背景：从 Linux 单机脚本 `asr_hotkey.py`（funasr 本地推理）演进而来，
  旧文件保留在根目录仅作参考，**当前入口是 `client/` 包**

## 常用命令

```bash
# 运行（Mac）
python3 -m client
python3 -m client --debug              # VAD/RMS 日志
python3 -m client --show-config        # 打印生效配置后退出

# 语法检查（最低验证）
python3 -m py_compile client/*.py client/platform/*.py

# 三平台装配检查（本机 Mac 也能加载三平台类）
for p in macos linux windows; do python3 -c "from client.platform import make_platform; make_platform('$p'); print('$p ok')"; done

# 冒烟测试（合成中文音频→omlx→剪贴板，不需要麦克风）
python3 scripts/smoke_test.py

# 强制平台（调试 linux/windows 代码的 import 路径）
python3 -m client --platform linux --show-config
```

## 架构速览（详见 docs/PLATFORM_GUIDE.md）

```
client/
├── app.py / config.py / core.py / asr_client.py   ← 平台无关核心
└── platform/
    ├── base.py        ← ABC 基类 + 共享实现（SharedSoundDeviceAudio 等）
    ├── macos.py       ← ✅ 完成（参考实现）
    ├── linux.py       ← ✅ 代码完成（迁移自旧版，未真机回归）
    └── windows.py     ← 🚧 骨架（TODO+原型代码在 docstring）
```

**铁律**：core/app 只依赖 `platform/base.py` 的基类；平台模块不得 import core
（方向只能 core→base）。新增平台 = 实现一个 BasePlatform 子类 + 注册表加一行。

## 硬约束

- 音频必须 16kHz mono float32（`platform/base.py` 顶部常量，全项目唯一出处）
- 热键回调 / 音频回调绝不阻塞（线程模型见 PLATFORM_GUIDE §4）
- `inject()` 和 Feedback 回调不得抛异常
- Linux 仅 X11（xdotool/xclip）；Wayland 不支持
- ASR 是 HTTP 调用 omlx，本地不加载任何模型

## 已知问题

- Linux 平台代码 100% 迁移自旧版生产代码，但新架构下**未真机回归**——
  部署清单见 docs/PLATFORM_GUIDE.md §6
- Windows 的 Injector/WindowProbe/Feedback 未实现（骨架 docstring 里有
  原型代码和验收标准）
- macOS 首次运行需手动授予"辅助功能"+"麦克风"权限（osascript 报错 1002
  = 辅助功能未授权）
