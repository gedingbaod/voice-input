# 平台开发指南（给 AI 工具 / 新接手者）

> 本文档目标：让任何 AI 编码工具（Claude Code / Cursor / Copilot 等）或工程师
> 在**不看全部代码**的情况下，理解本项目的架构，并能在 30 分钟内完成
> 一个新平台的实现或某个平台的调试。
>
> 最后更新：2026-09-20（v7 模块化重构后）

---

## 1. 项目一句话

**跨平台语音输入客户端**：按 F9 整段录音 → HTTP 发给局域网里 Mac 上的
omlx 服务（Qwen3-ASR 模型，OpenAI 兼容协议）→ 识别文本自动粘贴到光标。
支持 macOS / Linux(X11) / Windows，三平台共用同一份核心代码。

```
┌─────────────────────────────────────────────────┐
│              任一客户端机器                       │
│  ┌───────────────────────────────────────────┐  │
│  │  client/ （核心，三平台共用）               │  │
│  │  app.py → StreamRecognizer → AsrClient    │  │
│  └──────────────┬────────────────────────────┘  │
│                 │ 只依赖抽象基类                   │
│  ┌──────────────▼────────────────────────────┐  │
│  │  client/platform/ （平台层，按 OS 一个文件）│  │
│  │  macos.py / linux.py / windows.py         │  │
│  └───────────────────────────────────────────┘  │
└───────────────────────┬─────────────────────────┘
                        │ HTTP POST /v1/audio/transcriptions
                        ▼
        omlx 服务（Mac 主机 192.168.31.54:9999）
        模型 Qwen3-ASR-1.7B-8bit（原生带标点）
```

---

## 2. 目录与文件职责（每个文件干什么、改哪里）

```
client/
├── app.py            主入口。装配：config → platform → ASR → 状态机 → 主循环。
│                     ★ 平台无关。除非加全局流程，否则不用动。
├── config.py         全部命令行/环境变量配置。加参数来这里。
├── core.py           StreamRecognizer：录音状态机（whole/vad 两模式）。
│                     ★ 平台无关。线程模型见 §4。
├── asr_client.py     AsrClient：OpenAI 兼容 ASR 客户端（openai SDK）。
│                     音频编码 float32→wav 也在这里。平台无关。
├── punctuator.py     可选的 LLM 标点修复（当前默认关闭，Qwen3-ASR 不需要）。
└── platform/         ★ 平台层 —— 调试/新平台的主战场
    ├── base.py       全部抽象基类 + 跨平台共享实现（见 §3）
    ├── __init__.py   平台注册表 + make_platform() 工厂
    ├── macos.py      macOS 实现（✅ 完成并通过实机验证）
    ├── macos_feedback.py  macOS 菜单栏(rumps)/提示音/通知
    ├── linux.py      Linux X11 实现（✅ 完成，代码迁移自旧版 asr_hotkey.py，
    │                 未在真机回归 —— TODO 见 §6）
    └── windows.py    Windows 实现（🚧 骨架，TODO 清单见文件内 docstring）
scripts/
├── run_mac.sh        Mac 启动脚本
└── smoke_test.py     冒烟测试：合成中文音频→omlx识别→剪贴板验证
docs/ARCHITECTURE.md  架构决策记录（为什么这么设计）
```

---

## 3. 平台层设计（本次重构核心）

### 3.1 五个抽象基类（client/platform/base.py）

| 基类 | 职责 | 关键约束 |
|---|---|---|
| `BaseHotkey` | 全局热键监听 | `on_press` 回调**绝不能阻塞**（核心会在里面起线程，但平台层也别加慢逻辑） |
| `BaseAudioCapture` | 录音 → 30ms float32 帧 | 回调跑在**音频驱动线程**，只许 O(1) 操作（塞队列） |
| `BaseInjector` | 文本 → 光标位置 | 失败**不抛异常**只打日志；空文本直接 return |
| `BaseWindowProbe` | 焦点窗口是否终端 | 探测失败一律返回 `False`（走 GUI 粘贴分支最安全） |
| `BaseFeedback` | 提示音/菜单栏/通知 | 全部方法**非阻塞、不抛异常**；`run_forever()` 可选接管主线程 |

### 3.2 共享实现（能不写就不写）

| 类 | 说明 |
|---|---|
| `SharedSoundDeviceAudio` | sounddevice(portaudio) 录音，**三平台通用**（macos.py / linux.py / windows.py 都直接继承它，零代码） |
| `PrintFeedback` | 纯 stderr 文本反馈，所有平台的兜底 |
| `BaseFeedback.run_forever()` | 基类默认的阻塞主循环（无 UI 平台直接用） |

### 3.3 平台聚合类 `BasePlatform`

每个 OS 一个 `XxxPlatform(BasePlatform)`，实现 5 个 `create_*()` 工厂方法，
带惰性缓存的 `.hotkey/.audio/.injector/.window/.feedback` 属性直接可用。
有菜单栏等 UI 主循环的平台 override `ui_loop` 属性返回回调。

### 3.4 注册表（client/platform/__init__.py）

```python
make_platform(name=None, sound=True, notify=True)  # name=None 自动按 sys.platform
detect_platform_name()                              # "macos"|"linux"|"windows"
```

`--platform linux` 可以强制选平台（用于在 Mac 上静态验证 linux 模块的
import/构造路径 —— 系统调用会失败，但能查语法/接口错误）。

### 3.5 新增平台的完整步骤（照抄即可）

1. `client/platform/<name>.py` 写 `XxxPlatform` + 各组件类
   （能继承 `SharedSoundDeviceAudio` / `PrintFeedback` 的直接继承）
2. `client/platform/__init__.py` 的 `_REGISTRY_KEYS` 加一行、`_load_platform_class` 加分支
3. `config.py` 的 `--platform` choices 加名字
4. 跑 `python3 -m client --platform <name> --show-config` 验证装配
5. 完。**core.py / app.py 一行都不用改**

---

## 4. 核心线程模型（改 core.py 前必读）

```
主线程                      pynput/系统热键线程        sounddevice 音频线程
  │                              │                        │
  │ ui_loop()/hotkey.run()       │ on_hotkey()            │ _cb: 30ms/帧
  │ (rumps菜单栏 或 阻塞)         │  ├─ 未录音→ start()     │   ↓ audio_q.put(frame)
  │                              │  └─ 录音中→ 起stop线程   │
  │                              ▼                        ▼
  │                        stop线程                  worker线程(StreamRecognizer)
  │                         stream.stop()            audio_q.get() 循环:
  │                         q.put(None) ──────────→   whole: 无脑攒帧
  │                         join(worker≤120s)          vad: RMS+静音断句
  │                              │                        │
  │                              │                  每句/整段 → 起识别线程
  │                              ▼                        ▼
  │                        "● 停止"              asr线程: transcribe→inject
  │                                                     → feedback.on_result
```

**不变量（违反必出 bug）**：
1. 热键回调不能阻塞 → stop 在独立线程跑（app.py 的 on_hotkey 已处理）
2. 音频回调只许 `queue.put` → 其他全在 worker 做
3. ASR 识别不在 worker 里做 → 每次识别独立线程，不挡下一句/停止
4. whole 模式的识别在 worker 收尾时**同步**完成，`stop()` 用 `join(120s)` 等
   它 —— 所以按下 F9 停止后文字稍后才粘贴是**预期行为**
5. `inject()` 和 feedback 回调都不允许抛异常打断主流程

---

## 5. 配置速查

启动横幅（入口第一件事，`app.py:_print_os_banner`）：
```
[init] 操作系统: macOS (darwin)      ← sys.platform 检测结果
[init] 使用平台实现: macos            ← 据此选择 platform/<os>.py
```
`--platform linux` 强制覆盖时会注明"（自动检测为 macos，被 --platform 覆盖）"。

| 来源 | 变量/参数 | 默认 |
|---|---|---|
| ASR | `--base-url` / `VOICE_INPUT_BASE_URL` | `http://192.168.31.54:9999/v1` |
| ASR | `--api-key` / `VOICE_INPUT_API_KEY` | `123456` |
| ASR | `--model` / `VOICE_INPUT_MODEL` | `Qwen3-ASR-1.7B-8bit` |
| ASR | `--language` / `VOICE_INPUT_LANGUAGE` | `zh` |
| 模式 | `--mode whole\|vad` / `VOICE_INPUT_MODE` | `whole`（F9整段） |
| 热键 | `--hotkey` / `VOICE_INPUT_HOTKEY` | `<f9>`（pynput 语法） |
| VAD | `--sil-ms`、`--energy` | 700、0.005（仅 vad 模式用） |
| 反馈 | `--no-sound`、`--no-notify` | 都开 |
| 标点 | `--punct`、`--punct-model` | 关（Qwen3-ASR 自带） |
| 调试 | `--debug`、`--platform`、`--show-config` | — |

---

## 6. 各平台状态与调试要点

### macOS（✅ 完成度 100%，参考实现）

- 权限：**辅助功能**（pynput 热键 + osascript 按键）、**麦克风**（sounddevice）。
  排查：系统设置→隐私与安全；osascript 报错 `1002` = 辅助功能没给。
- 粘贴：`pbcopy` + `osascript keystroke "v" using command down`。终端/GUI 统一 Cmd+V。
- 菜单栏：rumps（可选依赖，缺了自动降级）。`ui_loop` 返回 `run_forever`。
- 麦克风名单：终端里跑的 python 需要终端 App 有麦克风权限。

### Linux X11（✅ 代码完成，⚠️ 未真机回归）

- 代码 100% 从旧版 `asr_hotkey.py` 迁移（生产验证过的逻辑），但**新架构下
  没在真机跑过**。在 Linux 机器上首次部署时的验收清单：
  1. `python3 -m client --show-config` → platform=linux
  2. 装依赖：`pip install sounddevice numpy pynput openai` + `sudo apt install xdotool xclip x11-utils`
  3. `python3 -m client --debug`，按 F9：确认 `[vad]` 日志出现、`● 录音中`
  4. 说一段话，停止后确认文字粘贴（GUI 是 Ctrl+V，终端是 Shift+Insert）
  5. systemd 用户服务：参考根目录 `voice-input.service`（老版可用，
     新版改 ExecStart 为 `python -u -m client` 即可）
- 已知坑：systemd 环境要 `DISPLAY`/`XAUTHORITY`（run.sh 已处理）；
  Wayland 不工作（xdotool/pynput 依赖 X11）。
- 提示音依赖 `canberra-gtk-play` 或 `paplay`，都没有则纯文本反馈（可接受）。

### Windows（🚧 骨架，TODO 都写在 windows.py 的 docstring 里）

- **热键/录音已完成**（pynput + SharedSoundDeviceAudio，理论可用）
- 待实现：`WindowsInjector.inject()`（ctypes 剪贴板+keybd_event，
  **原型代码已写在 docstring 里**，照抄+微调即可）、
  `WindowsWindowProbe.is_terminal()`（原型也在 docstring）、
  `WindowsFeedback` 的 MessageBeep。
- 每个 TODO 都带验收标准。补完后跑 `--show-config` + notepad 实测。

---

## 7. 常用验证命令（改完代码跑什么）

```bash
cd <项目根>

# 1. 语法
python3 -m py_compile client/*.py client/platform/*.py

# 2. 三平台装配（本机是 mac，但能静态加载三平台类）
for p in macos linux windows; do python3 -c "
from client.platform import make_platform
make_platform('$p'); print('$p ok')"; done

# 3. ASR 链路（需 omlx 在跑；合成音频，不用麦克风）
python3 scripts/smoke_test.py

# 4. 整机启动（Ctrl+C / 菜单退出）
python3 -m client --debug
```

---

## 8. 给 AI 工具的操作建议

- **改平台层**：直接改对应 `platform/<os>.py`，不要动 `core.py`/`app.py`。
  接口签名以 `platform/base.py` 的 abstractmethod 为准。
- **加平台功能**（如新的反馈通道）：在 `BaseFeedback` 加方法（带默认空实现，
  向后兼容），平台层按需 override。
- **改识别/断句**：只动 `core.py` + `asr_client.py`，保持平台对象只通过
  构造注入的引用被调用。
- **验证**：至少跑 §7 的 1、2、4 步；动了 ASR 相关就跑 3。
- **不要做**：平台模块里 import core（会循环依赖，方向只能是 core→base）；
  在热键/音频回调里做耗时操作；让 inject 抛异常。
