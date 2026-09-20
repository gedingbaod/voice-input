"""标点修复：用 omlx 上的小 LLM 给无标点的 ASR 输出补标点。

背景：omlx 的 mlx-audio 后端跑 SenseVoiceSmall 不输出标点（funasr 版靠
use_itn=True 生成标点，mlx-audio 未实现 ITN）。用同服务器上的
Qwen3-30B-A3B（MoE，激活参数 3B，热态 ~1s/句）做后处理。

设计原则：
  - fail-open：标点服务挂了/超时 → 返回原文，绝不阻塞注入
  - 纯中文短句才需要处理；已含标点或纯英文/数字直接跳过
  - 输出做长度校验，防止 LLM 瞎改写（长度差 >30% 视为不可信，回退原文）
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("voice-input.punct")

# 默认用 omlx 上常驻的 Qwen3-30B-A3B-Instruct-2507-4bit（MoE，热态快）
DEFAULT_PUNCT_MODEL = "Qwen3-30B-A3B-Instruct-2507-4bit"

_SYSTEM_PROMPT = (
    "你给中文口语转写加标点。只输出加好标点的原句，"
    "不增删字、不改写、不翻译、不解释、不加引号。"
)

# 中文标点 + 英文标点（有任意一个就认为"已有标点"，跳过）
_HAS_PUNCT_RE = re.compile(r"[，。！？；：、,.!?;:…—]")
# 含至少一个 CJK 字符才处理
_HAS_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

# LLM 输出与原文长度差超过此比例视为不可信（防改写）
_MAX_LEN_DRIFT = 0.35


class Punctuator:
    """调用同服务器 chat/completions 给文本补标点。fail-open。"""

    def __init__(self, client, model: str = DEFAULT_PUNCT_MODEL,
                 timeout: float = 8.0):
        self._client = client      # openai.OpenAI 实例（与 ASR 共用）
        self.model = model
        self.timeout = timeout

    def needs_punctuation(self, text: str) -> bool:
        """仅中文、且当前没有标点的文本才需要处理。"""
        if not text or not _HAS_CJK_RE.search(text):
            return False
        return not _HAS_PUNCT_RE.search(text)

    def punctuate(self, text: str) -> str:
        """补标点；任何失败返回原文。"""
        if not self.needs_punctuation(text):
            return text
        try:
            # 继承 timeout（openai SDK 支持 with_options 覆盖单次请求超时）
            resp = self._client.with_options(timeout=self.timeout) \
                .chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": text},
                    ],
                    max_tokens=max(32, len(text) * 3),
                    temperature=0.0,
                )
            out = (resp.choices[0].message.content or "").strip()
        except Exception as e:  # noqa: BLE001
            log.warning("punctuate failed (fallback to raw): %s", e)
            return text

        if not out:
            return text
        # 可信度校验：长度不能差太多（去掉空白后比较）
        a = _strip_ws(text)
        b = _strip_ws(out)
        if not b:
            return text
        drift = abs(len(b) - len(a)) / max(len(a), 1)
        if drift > _MAX_LEN_DRIFT:
            log.warning("punctuate output drifted %.0f%%, discard: %r",
                        drift * 100, out)
            return text
        return out


def _strip_ws(s: str) -> str:
    return re.sub(r"\s+", "", s)
