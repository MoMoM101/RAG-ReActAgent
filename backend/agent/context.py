"""Context budgeting and atomic conversation trimming."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import Any

import tiktoken

from agent.token_counter import TokenCounter, count_message, count_tools, get_token_counter
from llm.base import ChatMessage

logger = logging.getLogger(__name__)

_template: str | None = None
_legacy_encoder = tiktoken.get_encoding("cl100k_base")
_UNTRUSTED_CLOSE = "\n</UNTRUSTED_RETRIEVED_CONTENT>"
_HISTORY_SUMMARY_MAX_TOKENS = 512


def _counter_from_settings() -> TokenCounter:
    from config import settings

    return get_token_counter(
        settings.llm_model,
        settings.tokenizer_provider,
        settings.tokenizer_model,
        settings.tokenizer_fallback_safety_factor,
    )


def _estimate_tokens(text: str | None) -> int:
    """Backward-compatible text counter used by existing callers and tests."""
    return len(_legacy_encoder.encode(text)) if text else 0


def _load_template() -> str:
    global _template
    if _template is None:
        template_path = Path(__file__).resolve().parent / "prompts" / "system.txt"
        _template = template_path.read_text(encoding="utf-8")
    return _template


class ContextManager:
    def __init__(
        self,
        max_tokens: int = 128000,
        *,
        output_reserve: int = 0,
        reasoning_reserve: int = 0,
        safety_tokens: int = 0,
        tool_result_max_tokens: int = 800,
        counter: TokenCounter | None = None,
    ) -> None:
        self.max_tokens = max_tokens
        self.output_reserve = max(0, output_reserve)
        self.reasoning_reserve = max(0, reasoning_reserve)
        self.safety_tokens = max(0, safety_tokens)
        self.tool_result_max_tokens = max(1, tool_result_max_tokens)
        self.counter = counter or _counter_from_settings()
        self.last_dropped_message_ids: list[str] = []

    @classmethod
    def from_settings(cls, max_tokens: int) -> ContextManager:
        from config import settings

        return cls(
            max_tokens,
            output_reserve=max(
                settings.llm_output_token_reserve,
                settings.rag_generation_max_tokens,
            ),
            reasoning_reserve=settings.llm_reasoning_token_reserve,
            safety_tokens=settings.context_safety_tokens,
            tool_result_max_tokens=settings.context_tool_result_max_tokens,
        )

    def input_budget(self, budget_scale: float = 1.0) -> int:
        fixed_reserve = self.output_reserve + self.reasoning_reserve + self.safety_tokens
        return max(1, int(max(1, self.max_tokens - fixed_reserve) * budget_scale))

    def build_system_prompt(self, intent_hint: str, tools_description: str, profile_text: str = "") -> str:
        hint_section = f"\n[参考] {intent_hint}" if intent_hint else ""
        profile_section = f"\n{profile_text}" if profile_text else ""
        return _load_template().format(
            profile_section=profile_section,
            tools_description=tools_description,
            hint_section=hint_section,
        )

    def _prepared_message(self, message: ChatMessage) -> ChatMessage:
        if message.role != "tool" or not message.content:
            return message
        if message.content.endswith(_UNTRUSTED_CLOSE):
            close_tokens = self.counter.count_text(_UNTRUSTED_CLOSE)
            body = message.content[: -len(_UNTRUSTED_CLOSE)]
            truncated = (
                self.counter.truncate_text(
                    body,
                    max(1, self.tool_result_max_tokens - close_tokens),
                )
                + _UNTRUSTED_CLOSE
            )
        else:
            truncated = self.counter.truncate_text(message.content, self.tool_result_max_tokens)
        return message if truncated == message.content else replace(message, content=truncated)

    def count_request(self, messages: list[ChatMessage], tools: list[dict[str, Any]] | None = None) -> int:
        return sum(count_message(self.counter, message) for message in messages) + count_tools(self.counter, tools)

    def trim_messages(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        *,
        budget_scale: float = 1.0,
    ) -> tuple[list[ChatMessage], str, list[str]]:
        """Keep anchors and newest complete turns within the real request budget."""
        if not messages:
            self.last_dropped_message_ids = []
            return messages, "", []

        budget = self.input_budget(budget_scale)
        prepared = [self._prepared_message(message) for message in messages]
        system_indices = {index for index, message in enumerate(prepared) if message.role == "system"}
        non_system_indices = [index for index in range(len(prepared)) if index not in system_indices]
        last_user_index = next(
            (index for index in reversed(non_system_indices) if prepared[index].role == "user"),
            None,
        )
        selected = set(system_indices)
        if last_user_index is not None:
            selected.add(last_user_index)

        used = sum(count_message(self.counter, prepared[index]) for index in selected)
        used += count_tools(self.counter, tools)
        remaining = budget - used

        index_position = {index: position for position, index in enumerate(non_system_indices)}
        cursor = len(non_system_indices) - 1
        while cursor >= 0 and remaining > 0:
            index = non_system_indices[cursor]
            if index in selected:
                cursor -= 1
                continue

            group = [index]
            message = prepared[index]
            if message.role == "tool":
                start = cursor
                while start > 0 and prepared[non_system_indices[start - 1]].role == "tool":
                    start -= 1
                assistant_position = start - 1
                if assistant_position >= 0:
                    assistant_index = non_system_indices[assistant_position]
                    assistant = prepared[assistant_index]
                    if assistant.role == "assistant" and assistant.tool_calls:
                        start = assistant_position
                group = non_system_indices[start : cursor + 1]

            group = [item for item in group if item not in selected]
            group_tokens = sum(count_message(self.counter, prepared[item]) for item in group)
            if group_tokens > remaining:
                break
            selected.update(group)
            remaining -= group_tokens
            cursor = min(index_position[item] for item in group) - 1

        result = [message for index, message in enumerate(prepared) if index in selected]

        dropped_indices = set(range(len(prepared))) - selected
        dropped_queries: list[str] = []
        dropped_message_ids: list[str] = []
        for index in sorted(dropped_indices):
            message = prepared[index]
            if message.role != "user":
                continue
            if message.content:
                dropped_queries.append(message.content)
            if message.message_id:
                dropped_message_ids.append(message.message_id)
        self.last_dropped_message_ids = dropped_message_ids
        summary = self.counter.truncate_text(
            "；".join(dropped_queries[-10:]),
            _HISTORY_SUMMARY_MAX_TOKENS,
        )
        if dropped_queries:
            logger.info(
                "trim_messages: tokenizer=%s budget=%d used=%d dropped_users=%d kept=%d",
                self.counter.name,
                budget,
                self.count_request(result, tools),
                len(dropped_queries),
                len(result),
            )
        return result, summary, dropped_queries
