# voice_input 项目笔记

> 整理自 Claude 会话（DolphinDB 迁移任务）中涉及本项目的全部观察记录。
> 观察时间点：2026-08-11 深夜 ~ 2026-08-12 凌晨（迁移期间）、2026-09-20 晚（排查期间）。

## 一、项目是什么

- **F9 按键说话 + SenseVoice 语音识别（ASR）的语音输入工具**，systemd 用户服务方式常驻运行，非 Claude 会话产物、无人正在开发。
- 服务描述：`Voice input (F9 push-to-talk, SenseVoice ASR)`
- 代码目录：`/home/zzfoo02/work/voice_input/`

### 目录内容（2026-09-20 观察）

| 文件 | 大小 | 说明 |
|---|---:|---|
| `asr_hotkey.py` | 13,894B | 主程序（当前在跑的就是它） |
| `asr_hotkey_paraformer.py` | 5,597B | Paraformer 版变体 |
| `asr_design.md` | 10,035B | 设计文档 |
| `README.md` / `README_asr.md` | 6,955B / 4,419B | 说明文档 |
| `run.sh` | 833B | 启动脚本 |
| `voice-input.service` | 676B | 服务文件（部署副本） |
| `hotwords.txt` | 249B | 热词表 |

## 二、运行方式

- systemd **用户级服务**：`systemctl --user` 管理，**enabled（开机自启）**
- 服务文件：`/home/zzfoo02/.config/systemd/user/voice-input.service`
- 实际命令（2026-09-20 20:34 观察，Main PID 663673）：

```
/home/zzfoo02/work/test/.venv/bin/python -u asr_hotkey.py --model sensevoice --sil-ms 400
```

- **注意：用的是 `work/test` 项目的 venv**（不是独立虚拟环境）。旁证：`work/test/__pycache__/asr_hotkey.cpython-312.pyc`（2026-08-11 生成）。

## 三、稳定性问题（重要）

### 1. 反复被 OOM 杀掉，已崩溃重启 12 万次

2026-09-20 20:34 `systemctl --user status` 显示：

```
Active: active (running) since ... 1s ago
restart counter is at 120943        ← 崩溃重启计数
```

即该服务长期处于「崩溃 → systemd 自动拉起 → 再崩溃」循环。

### 2. OOM 事件原始记录（2026-08-12 00:18:45，journalctl）

当晚机器（zzfoo02-aero，11GiB 内存、无 swap、当时 9.6GiB 已用）发生整机 OOM，受害者即本服务：

```
oom-kill: ... task_memcg=/user.slice/user-1000.slice/user@1000.service/app.slice/voice-input.service,
          task=python, pid=103043, uid=1000
Out of memory: Killed process 103043 (python)
  total-vm:4474688kB, anon-rss:2533560kB, oom_score_adj:200
```

要点：
- 当时该 python 进程 **RSS 高达 ~2.53GB**（total-vm ~4.47GB）
- `oom_score_adj=200` —— 被标记为 OOM 时的优先牺牲目标（可能来自 slice/服务 OOM 策略配置），所以每次内存紧张它先死
- 正常刚启动时内存仅 ~112MB（status 显示 Memory 111.9M），说明 **2.5GB 是运行中逐渐膨胀/推理峰值**，内存泄漏或模型推理累积的嫌疑很大

### 3. 对其他任务的影响

2026-08-11/12 夜间 DolphinDB 数据迁移期间，该进程的 2.5GB 占用是机器内存紧张的主要因素之一，间接导致迁移脚本进程被连带 OOM。它每次被杀后 systemd 立即重启并再次膨胀，形成持续的内存压力源。

## 四、git 状态

最后 3 次提交均为 2026-08-11、作者 gedingbaod（本人手工提交，无 Claude 会话参与）：

```
61dfd59  08-11 22:10  second
5ba5d49  08-11 21:35  first
71ab136  08-11 21:56  Create aaa
```

（`.git` 目录时间戳 2026-09-20 20:25 有变动，但无对应新提交，推测为 git 内务操作。）

## 五、结论与建议

> **2026-09-20 更新**：排查发现 12 万重启计数实为两个阶段累计：
> ① 8月12 前 OOM 内存膨胀（见第三节）；② 8月18 11:22 起 `~/.cache/modelscope/models/` 被清空（所有模型删除），
> 服务因「模型目录不存在」exit 1 无限重启。已于 2026-09-20 20:47 执行 `systemctl --user disable --now voice-input` 停用止血，
> 用户目录软链被 systemd 一并移除（项目内 voice-input.service 源文件完好）。恢复方法见 CLAUDE.md。

1. **没有 Claude session 在开发此项目**；它是 8月11 由用户本人创建并部署为服务的工具。
2. 核心待解决的问题是**内存膨胀 + 崩溃循环**（12 万次重启）：
   - 排查 `asr_hotkey.py` 是否有推理结果/音频缓冲累积泄漏（正常 112MB → 峰值 2.5GB 不合理）；
   - 或给服务加内存上限：`systemctl --user edit voice-input` 增加 `[Service] MemoryMax=1G`，让它到量自杀重启，避免拖垮整机。
3. 若近期不用 F9 语音输入，可直接停用：
   ```bash
   systemctl --user stop voice-input.service      # 立即释放内存
   systemctl --user disable voice-input.service   # 不再开机自启
   ```
