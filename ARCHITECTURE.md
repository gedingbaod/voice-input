# voice-input 架构设计（v6：客户端 / 服务端分离）

> 目标：把现有 Linux X11 单机脚本改造成 **客户端 + 服务端** 两层结构；
> 客户端跨 Mac / Linux / Windows **同一份代码**；服务端**唯一**，就是
> Mac 上的 omlx 0.6.4（OpenAI 兼容 ASR）。
>
> **变更历史**：
> - **v6**（2026-09-20）：用户反馈"服务端只有一个 = Mac omlx，所有客户端连它"，
>   改为默认 `base_url=http://192.168.31.54:9999/v1`，**Mac 本机客户端也走局域网 IP**；
>   客户端支持 Mac / Linux / **Windows**（之前不做 Windows，现加上）；
>   所有客户端**统一 F9**；**删除 server/ 子项目**（不需要自建服务端）。
>
> **调研已确认的事实（2026-09-20）**：
> 1. Mac 上的 **omlx 0.6.4**（带 UI，浏览器访问 `http://127.0.0.1:9999/`，密码 123456）
>    已经原生支持 **`POST /v1/audio/transcriptions`**（OpenAI Whisper 兼容，Bearer auth），
>    已经加载了 `SenseVoiceSmall`。**不需要任何额外启动操作**。
> 2. 端点实测可用：中文音频 → `{text, language:"zh", duration, segments[]}`。
> 3. **本项目不需要任何服务端代码**。所有客户端（Mac / Linux / Windows）
>    都直接连 Mac 上跑着的 omlx，唯一服务端就是它。
>
> **关键拓扑决策（2026-09-20 确认）**：
> - **唯一服务端**：Mac 上的 omlx，对外地址 `http://192.168.31.54:9999/v1`
> - **客户端**：支持 Mac / Linux / Windows，全部调上面的同一个 omlx
> - **快捷键**：统一 **F9**（所有平台一致）
> - **Mac 本机客户端**：也走 `192.168.31.54:9999`（不搞 localhost 特例，配置统一）
> - **API Key**：默认 `123456`（omlx 当前设置），可配置覆盖

---

## 一、整体拓扑

```
                                          ┌──────────────────────────────┐
                                          │ omlx 0.6.4  (Mac 主机)        │
                                          │   - SenseVoiceSmall (MLX)     │
                                          │   - 监听 0.0.0.0:9999         │
                                          │   - Bearer: 123456            │
                                          └──────────────┬───────────────┘
                                                         │
                              HTTP POST /v1/audio/transcriptions
                              Authorization: Bearer 123456
                                                         │
            ┌────────────────────────────────────────────┼────────────────────────────┐
            │                                            │                            │
            ▼                                            ▼                            ▼
┌────────────────────────┐                ┌────────────────────────┐    ┌────────────────────────┐
│ Mac 客户端              │                │ Linux 客户端            │    │ Windows 客户端          │
│ client_macos.py        │                │ client_linux.py        │    │ client_windows.py      │
│  ┌──────┐ ┌────┐ ┌───┐ │                │  ┌──────┐ ┌────┐ ┌───┐ │    │  ┌──────┐ ┌────┐ ┌───┐ │
│  │录音  │→│ASR │→│注入││                │  │录音  │→│ASR │→│注入││    │  │录音  │→│ASR │→│注入││
│  │+ VAD │ │客户端│ │  │ │                │  │+ VAD │ │客户端│ │  │ │    │  │+ VAD │ │客户端│ │  │ │
│  └──────┘ └────┘ └───┘ │                │  └──────┘ └────┘ └───┘ │    │  └──────┘ └────┘ └───┘ │
│  按 F9 开始/停止        │                │  按 F9 开始/停止        │    │  按 F9 开始/停止        │
└────────────────────────┘                └────────────────────────┘    └────────────────────────┘
```

**关键点**：
- **唯一服务端**：Mac 上的 omlx 0.6.4，对外地址 `http://192.168.31.54:9999/v1`
- **客户端代码完全一致**，只有平台适配层（hotkey / audio / inject / window）不同
- **Mac 本机客户端也连 `192.168.31.54:9999`**（不搞 localhost 特例）
- 所有客户端都是 **F9 快捷键**

---

## 二、项目目录结构

```
voice-input/
├── ARCHITECTURE.md            ← 本文档
├── CLAUDE.md                  ← 项目速览（已有，更新指向新结构）
├── README.md                  ← 用户文档（更新）
│
├── client/                    ← 跨平台客户端（Mac/Linux/Windows 同一份代码）
│   ├── __init__.py
│   ├── app.py                 ← 入口（选平台 + 装平台 adapter + 跑主循环）
│   ├── core.py                ← VAD + 整句流式 状态机（移植自 asr_hotkey.py）
│   ├── asr_client.py          ← OpenAI 协议 ASR 客户端（直连 omlx）
│   ├── config.py              ← 配置加载（CLI + 环境变量 + 默认值）
│   │
│   ├── platform/              ← 平台抽象（每个文件 = 一个 OS 的实现）
│   │   ├── __init__.py        ← detect() 工厂（按 sys.platform 自动选）
│   │   ├── base.py            ← 抽象接口：Hotkey / Audio / Injector / WindowProbe
│   │   ├── macos.py           ← Mac 实现：osascript / pbcopy / sounddevice
│   │   ├── linux.py           ← Linux 实现：xdotool / xclip / sounddevice
│   │   └── windows.py         ← Windows 实现：ctypes SendInput / pyperclip / sounddevice
│   │
│   └── pyproject.toml         ← 客户端依赖：sounddevice, pynput, numpy, openai
│
├── scripts/
│   ├── run_mac.sh             ← Mac 启动脚本（设置麦克风/辅助功能提示）
│   ├── run_linux.sh           ← Linux 启动脚本（设置 DISPLAY/XAUTHORITY）
│   └── run_windows.bat / .ps1 ← Windows 启动脚本
│
├── deploy/
│   ├── voice-input.plist      ← macOS launchd 配置（登录自启）
│   └── voice-input.service    ← systemd --user unit（Linux）
│
├── hotwords.txt               ← 保留（omlx SenseVoice 不支持，提示忽略；未来若换服务端再用）
├── asr_hotkey.py              ← 旧入口，改为薄壳：调 client/app.py 向后兼容
├── asr_hotkey_paraformer.py   ← 旧备份，保留
├── asr_design.md              ← 旧设计文档，保留为历史
├── notes.md                   ← 旧笔记，保留
└── run.sh                     ← 旧启动脚本，保留（内部改为调用 client）
```

---

## 三、分层与职责

### 3.1 `client/core.py` —— VAD 状态机（平台无关）

直接移植现有 `asr_hotkey.py` 的 `StreamRecognizer` 类，**唯一变化**：
- `recognize()` 调用从 `funasr_local_recognize(...)` 改为 `asr_client.transcribe(audio)`
- 其余线程模型、RMS 算法、`hangover`、`<300ms` 丢弃逻辑、debug 输出全部保留

```python
class StreamRecognizer:
    def __init__(self, asr: AsrClient, vad_cfg: VADConfig, injector: Injector):
        self.asr = asr
        self.cfg = vad_cfg
        self.injector = injector
        # ... 同原代码 ...
```

### 3.2 `client/asr_client.py` —— OpenAI 协议客户端

```python
class AsrClient:
    """OpenAI 兼容 ASR 客户端，omlx 和 funasr 服务端都吃这个接口"""
    def __init__(self, base_url: str, api_key: str, model: str,
                 timeout: float = 30.0):
        self._client = openai.OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        self.model = model

    def transcribe(self, audio: np.ndarray, sr: int = 16000) -> str:
        # float32 → 16kHz mono wav bytes
        buf = audio_to_wav_bytes(audio, sr)
        f = ("audio.wav", buf, "audio/wav")
        resp = self._client.audio.transcriptions.create(
            model=self.model, file=f, response_format="json",
        )
        return (resp.text or "").strip()
```

**为什么用 openai SDK 而不直接 requests**：
- omlx 完全按 OpenAI schema 实现，SDK 自动处理 multipart / 鉴权 / JSON 解析
- 后续想换任何 OpenAI 兼容服务（如本地 whisper.cpp、自建服务）零改动
- 错误处理（401/429/5xx）SDK 内置

### 3.3 `client/platform/base.py` —— 平台接口

```python
class Hotkey(Protocol):
    def register(self, key: str, on_press: Callable[[], None]) -> None: ...
    def run(self) -> None: ...   # 阻塞，直到 KeyboardInterrupt

class AudioCapture(Protocol):
    def start(self, on_frame: Callable[[np.ndarray], None]) -> None: ...
    def stop(self) -> None: ...

class Injector(Protocol):
    """把文本送到当前光标位置"""
    def inject(self, text: str) -> None: ...

class WindowProbe(Protocol):
    """返回当前焦点窗口是不是终端（决定注入方式）"""
    def is_terminal(self) -> bool: ...
```

### 3.4 `client/platform/macos.py` —— Mac 实现要点

| 能力 | 实现 |
|---|---|
| 热键 | `pynput.keyboard.GlobalHotKeys` —— **需用户在 系统设置 → 隐私与安全 → 辅助功能 中授权 Python/Terminal** |
| 录音 | `sounddevice.InputStream(samplerate=16000, channels=1, dtype="float32", blocksize=480)` —— 首次运行会弹麦克风权限请求 |
| 剪贴板 | `pbcopy` （无 PRIMARY，Mac 没有这个概念）|
| 注入（GUI） | `osascript -e 'tell application "System Events" to keystroke "v" using command down'` |
| 注入（终端） | Mac 终端（Terminal.app / iTerm2 / Warp / Alacritty 等）**都支持 Cmd+V**；所以 Mac 上 GUI/终端都是 Cmd+V，不需要 is_terminal 分支——但保留接口以便未来扩展 |
| 焦点窗口探测 | `osascript -e 'tell application "System Events" to get name of first application process whose frontmost is true'` |

**Mac 注入的核心脚本**（写成函数而非 subprocess 多行）：

```python
def _cmd_v():
    subprocess.run(["osascript", "-e",
        'tell application "System Events" to keystroke "v" using command down'],
        check=False, timeout=2)
```

### 3.5 `client/platform/linux.py` —— Linux 实现（基本复用现有逻辑）

| 能力 | 实现 |
|---|---|
| 热键 | `pynput.keyboard.GlobalHotKeys`（同 Mac，但需要 X11 权限） |
| 录音 | `sounddevice`（同 Mac） |
| 剪贴板 | `xclip -selection clipboard` + `xclip -selection primary`（保留 PRIMARY，X11 习惯） |
| 注入（GUI） | `xdotool key --clearmodifiers ctrl+v` |
| 注入（终端） | `xdotool key --clearmodifiers shift+Insert` |
| 焦点窗口探测 | `xdotool getactivewindow` + `xprop WM_CLASS` + 关键词匹配 |

直接迁移 `asr_hotkey.py` 里现有的 `_target_is_terminal()` 和 `inject()`。

### 3.5b `client/platform/windows.py` —— Windows 实现

| 能力 | 实现 |
|---|---|
| 热键 | `pynput.keyboard.GlobalHotKeys`（Win 下也支持全局监听，但首次运行需以管理员权限启动 Python，或用 `pythonw.exe` 注册成服务避免权限问题） |
| 录音 | `sounddevice`（Win 自带 portaudio，无需额外装） |
| 剪贴板 | `pyperclip.copy(text)` |
| 注入 | `ctypes.windll.user32.keybd_event(VK_CONTROL, 0, 0, 0)` + `keybd_event(VK_V, 0, 0, 0)` 发 Ctrl+V；**统一 Ctrl+V**（cmd/PowerShell/WSL 都支持）|
| 焦点窗口探测 | `ctypes.windll.user32.GetForegroundWindow()` + `GetWindowTextW()` 拿窗口标题，关键词匹配（Terminal / Windows Terminal / cmd / PowerShell / WezTerm / Alacritty / Tabby 等）|

**注意**：pynput 在 Windows 上需要"管理员权限"或把 Python 注册成全局钩子服务。本次范围先用普通权限启动（GUI 应用已足够）；如遇焦点问题，可后续改用 `keyboard` 库（Windows 原生支持更好）。

### 3.6 `client/platform/__init__.py` —— 平台自动选择

```python
import sys

def make_platform() -> tuple[Hotkey, AudioCapture, Injector, WindowProbe]:
    if sys.platform == "darwin":
        from .macos import MacHotkey, MacAudio, MacInjector, MacWindowProbe
        return MacHotkey(), MacAudio(), MacInjector(), MacWindowProbe()
    if sys.platform.startswith("linux"):
        from .linux import LinuxHotkey, LinuxAudio, LinuxInjector, LinuxWindowProbe
        return LinuxHotkey(), LinuxAudio(), LinuxInjector(), LinuxWindowProbe()
    if sys.platform == "win32":
        from .windows import WinHotkey, WinAudio, WinInjector, WinWindowProbe
        return WinHotkey(), WinAudio(), WinInjector(), WinWindowProbe()
    raise RuntimeError(f"unsupported platform: {sys.platform}")
```

启动时打印当前平台，方便排查（"platform=darwin, asr=http://192.168.31.54:9999/v1"）。

### 3.7 `client/app.py` —— 主入口

```python
def main():
    cfg = load_config()                # CLI + env + defaults
    hotkey, audio, injector, probe = make_platform()
    asr = AsrClient(cfg.asr_base_url, cfg.asr_api_key, cfg.asr_model)
    recognizer = StreamRecognizer(asr, cfg.vad, injector)

    def on_hotkey():
        if not recognizer.running:
            recognizer.start()
        else:
            threading.Thread(target=recognizer.stop, daemon=True).start()

    print(f"就绪，按 {cfg.hotkey} 开始/停止 (Ctrl+C 退出)", file=sys.stderr)
    print(f"ASR 后端：{cfg.asr_base_url}  model={cfg.asr_model}", file=sys.stderr)
    hotkey.register(cfg.hotkey, on_hotkey)
    hotkey.run()       # 阻塞
```

### 3.8 `client/config.py` —— 配置

```python
@dataclass
class Config:
    # ASR 服务端 —— 默认指向 Mac 上的 omlx
    asr_base_url: str = "http://192.168.31.54:9999/v1"
    asr_api_key: str = "123456"
    asr_model: str = "SenseVoiceSmall"
    # VAD
    sil_ms: int = 400
    energy: float = 0.005
    debug: bool = False
    # 热键（所有平台统一 F9）
    hotkey: str = "<f9>"
```

加载优先级（高 → 低）：
1. 命令行参数（`--base-url`、`--api-key`、`--model`、`--hotkey`、`--sil-ms`、`--energy`、`--debug`）
2. 环境变量（`VOICE_INPUT_BASE_URL` 等）
3. 默认值（指向 Mac 局域网 omlx）

**所有平台默认值统一为**：`http://192.168.31.54:9999/v1`（Mac 本机客户端也走这个，不搞 localhost 特例）

---

## 四、服务端

**本项目没有服务端代码。**

- **唯一服务端**：Mac 上的 omlx 0.6.4（已运行、已加载 SenseVoiceSmall）
- **地址**：`http://192.168.31.54:9999/v1`
- **客户端直连**：通过 OpenAI 兼容协议 `POST /v1/audio/transcriptions`
- **不需要在本项目里写任何 ASR 推理代码**

如果未来 omlx 退役、需要自建服务端，再单独建 `server/` 子项目（funasr 或 vLLM-ASR 包装）。

---

## 五、依赖矩阵

所有客户端共用一份 `pyproject.toml`：

```
sounddevice     # 录音（portaudio 后端；Win/Linux/Mac 都支持）
numpy
pynput          # 全局热键（Win/Linux/Mac 都支持，需各自授权）
openai>=1.0     # ASR 客户端（用 OpenAI SDK 调 omlx）
pyperclip       # 跨平台剪贴板（Windows 必需；Mac/Linux 用原生命令）
```

平台特定的**系统依赖**：

| 平台 | 系统包 |
|---|---|
| Mac | 系统自带 `osascript`、`pbcopy`、`pbpaste`；首次运行需手动授予"辅助功能"和"麦克风"权限 |
| Linux | `xdotool`、`xclip`、`xprop`（已有）|
| Windows | 系统自带 `SendInput`（通过 ctypes）；无需额外系统包 |

---

## 六、迁移路径（如何从旧代码过渡）

1. **第一步**：新增 `client/` 目录，按上面结构写文件
2. **第二步**：让 `asr_hotkey.py` 变成薄壳（兼容旧启动方式）：
   ```python
   # asr_hotkey.py
   from client.app import main
   if __name__ == "__main__":
       main()
   ```
3. **第三步**：保留 `run.sh` 不动（内部调用入口仍兼容）
4. **第四步**：现有 Linux systemd 服务不用改 unit，只需重启即可；
   `base_url` 会从原 funasr 本地切换到 `http://192.168.31.54:9999/v1`
   （需要 Mac omlx 在局域网可达；客户端启动前会 GET /health 探活）
5. **Mac 端新部署**：用 `scripts/run_mac.sh`，调 `client/app.py`
6. **Mac 端服务化（可选）**：用 `deploy/voice-input.plist`，launchd 登录自启

---

## 七、决策点 / 风险

| 决策 | 选择 | 理由 |
|---|---|---|
| 协议 | OpenAI `/v1/audio/transcriptions` | omlx 原生支持，生态成熟 |
| ASR 客户端库 | `openai` SDK | 比裸 requests 简洁，自带重试/超时 |
| 服务端 | **只 omlx，不写 server 包** | 唯一服务端就是 Mac omlx，避免重复造轮子 |
| 平台抽象粒度 | 4 个 Protocol（Hotkey/Audio/Injector/WindowProbe） | 录音是平台无关的（sounddevice 都行），但注入和窗口探测必须分开 |
| Mac 终端粘贴 | **统一 Cmd+V** | Mac 终端 100% 支持 Cmd+V，不存在 X11 PRIMARY 那种历史包袱 |
| Windows 粘贴 | **统一 Ctrl+V**（pyautogui 或 ctypes SendInput）| Win 终端（cmd/PowerShell/WSL/ConEmu）都支持 Ctrl+V |
| 默认 base_url | `http://192.168.31.54:9999/v1`（所有平台统一）| 用户确认 Mac 本机客户端也走局域网 IP |
| 默认 API key | `123456`（omlx 当前设置） | 与 omlx 一致；可命令行/环境变量覆盖 |
| 热键 | 统一 F9（所有平台） | 用户明确指定 |
| hotwords.txt | 保留文件但客户端不读（omlx SenseVoice 不支持热词） | 与现有行为一致；未来切换服务端可复用 |

---

## 八、交付时序

1. **第一批（核心，Mac 能跑通）**：
   - `client/asr_client.py`（OpenAI 客户端）
   - `client/platform/base.py` + `__init__.py`（接口 + 工厂）
   - `client/platform/macos.py`（pbcopy + osascript Cmd+V）
   - `client/core.py`（VAD 状态机）
   - `client/app.py`（主入口）
   - `client/config.py`
   - `client/pyproject.toml` + `scripts/run_mac.sh`
   - **冒烟测试脚本**：合成一段中文音频 → client → omlx → 文本进剪贴板 全链路验证

2. **第二批（Linux 兼容）**：
   - `client/platform/linux.py`（xdotool + xclip + xprop）
   - `asr_hotkey.py` 改成薄壳（保持向后兼容）
   - `run.sh` 改内部调用入口（外部不变）

3. **第三批（Windows 兼容）**：
   - `client/platform/windows.py`（pyperclip + ctypes SendInput）
   - `scripts/run_windows.ps1`

4. **第四批（部署与文档）**：
   - `deploy/voice-input.plist`（macOS launchd）
   - 更新 `README.md`、`CLAUDE.md`

按这个顺序，第一批交付完 Mac 端就能用了；后续批次不阻塞。

---

## 九、未在此版本解决的问题

- **进程保活**：Mac 用 launchd plist（登录自启）；Linux 已有 systemd，不变；Windows 用"任务计划程序"开机启动
- **权限申请**：Mac 首次运行需手动授予"辅助功能"和"麦克风"权限，会在 README 写明；Windows 首次可能被防火墙拦
- **omlx 离线/宕机**：客户端启动时 GET /health 探活，失败给清晰报错（"无法连接 192.168.31.54:9999，请确认 Mac omlx 在跑"）
- **网络抖动**：识别超时（默认 30s）+ 重试 1 次；客户端日志记录每次识别耗时
- **日志/监控**：保持 stderr，systemd journal / launchd log / Windows event log 自然收集
- **音频格式**：客户端固定发 16kHz mono wav，简化两端逻辑
- **hotwords**：omlx SenseVoice 不支持 hotwords；hotwords.txt 保留文件但客户端不读；未来若换支持热词的服务端（如 Whisper / Paraformer），可复用
