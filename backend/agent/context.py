"""Context manager for system prompt and message trimming."""

import hashlib
import logging
from pathlib import Path

import tiktoken

from llm.base import ChatMessage

logger = logging.getLogger(__name__)

MAX_TOOL_RESULT_CHARS = 2000
BUDGET_RATIO = 0.8

_encoder: tiktoken.Encoding | None = None
_template: str | None = None


def _get_encoder() -> tiktoken.Encoding:
    global _encoder
    if _encoder is None:
        _encoder = tiktoken.get_encoding("cl100k_base")
    return _encoder


def _load_template() -> str:
    global _template
    if _template is None:
        template_path = Path(__file__).resolve().parent / "prompts" / "system.txt"
        _template = template_path.read_text(encoding="utf-8")
    return _template


def _estimate_tokens(text: str | None) -> int:
    if not text:
        return 0
    return len(_get_encoder().encode(text))


class ContextManager:
    def __init__(self, max_tokens: int = 128000):
        self.max_tokens = max_tokens

    def build_system_prompt(self, intent_hint: str, tools_description: str, profile_text: str = "") -> str:
        hint_section = f"\n[参考] {intent_hint}" if intent_hint else ""
        profile_section = f"\n{profile_text}" if profile_text else ""
        return _load_template().format(
            profile_section=profile_section,
            tools_description=tools_description,
            hint_section=hint_section,
        )

    def trim_messages(self, messages: list[ChatMessage]) -> tuple[list[ChatMessage], str, list[str]]:
        """滑动窗口裁剪。返回 (裁剪后消息列表, 摘要文本, 被丢弃的用户消息列表)。"""
        if not messages:
            return messages, "", []

        budget = int(self.max_tokens * BUDGET_RATIO)

        system_msgs = [m for m in messages if m.role == "system"]
        others = [m for m in messages if m.role != "system"]

        if not others:
            return system_msgs, "", []

        last_user_idx = None
        for i in range(len(others) - 1, -1, -1):
            if others[i].role == "user":
                last_user_idx = i
                break

        anchor_tokens = sum(_estimate_tokens(m.content) for m in system_msgs)
        if last_user_idx is not None:
            anchor_tokens += _estimate_tokens(others[last_user_idx].content)

        if anchor_tokens > budget:
            return system_msgs + ([others[last_user_idx]] if last_user_idx is not None else []), "", []

        remaining = budget - anchor_tokens
        kept: list[ChatMessage] = []
        dropped_summary_parts: list[str] = []

        i = len(others) - 1
        while i >= 0 and remaining > 0:
            msg = others[i]

            content = msg.content
            if msg.role == "tool" and content and len(content) > MAX_TOOL_RESULT_CHARS:
                content = content[:MAX_TOOL_RESULT_CHARS] + "…[截断]"

            tok = _estimate_tokens(content)

            if msg.role == "tool":
                pair_msgs = [msg]
                pair_tokens = tok
                j = i - 1
                while j >= 0 and others[j].role == "tool":
                    pair_msgs.insert(0, others[j])
                    pair_tokens += _estimate_tokens(others[j].content)
                    j -= 1
                if j >= 0 and others[j].role == "assistant" and others[j].tool_calls:
                    pair_msgs.insert(0, others[j])
                    pair_tokens += _estimate_tokens(others[j].content)
                    i = j

                if pair_tokens <= remaining:
                    kept = pair_msgs + kept
                    remaining -= pair_tokens
                else:
                    remaining = 0
            else:
                if tok <= remaining:
                    kept.insert(0, msg)
                    remaining -= tok
                else:
                    remaining = 0

            i -= 1

        # 收集被丢弃的用户消息（基于内容 hash 而非对象 id 去重）
        def _msg_hash(msg: ChatMessage) -> str:
            return hashlib.md5(f"{msg.role}:{msg.content}".encode()).hexdigest()

        kept_ids = {_msg_hash(m) for m in kept}
        dropped_queries: list[str] = []
        for m in others:
            if m.role == "user" and _msg_hash(m) not in kept_ids and m.content:
                dropped_queries.append(m.content)
                dropped_summary_parts.append(m.content)

        result = system_msgs + kept

        if last_user_idx is not None:
            last_user = others[last_user_idx]
            if last_user not in kept:
                result.append(last_user)

        summary = "；".join(dropped_summary_parts[-10:]) if dropped_summary_parts else ""
        if dropped_queries:
            logger.info(
                "trim_messages: dropped %d messages (%d chars), kept %d",
                len(dropped_queries),
                sum(len(q) for q in dropped_queries),
                len(kept),
            )
        return result, summary, dropped_queries
