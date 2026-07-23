"""Model-aware token counting with safe, dependency-light fallbacks."""

from __future__ import annotations

import json
import logging
import math
from functools import lru_cache
from typing import Any, Protocol

import tiktoken

from llm.base import ChatMessage

logger = logging.getLogger(__name__)


class TokenCounter(Protocol):
    name: str

    def count_text(self, text: str | None) -> int: ...

    def truncate_text(self, text: str, max_tokens: int) -> str: ...


class TiktokenCounter:
    def __init__(self, model: str, safety_factor: float = 1.0) -> None:
        try:
            encoding_name = tiktoken.encoding_name_for_model(model)
        except KeyError:
            encoding_name = "cl100k_base"
        # o200k_base can trigger an implicit network download when its BPE file
        # is not cached. Token counting must never make startup network-bound.
        offline_fallback = encoding_name != "cl100k_base"
        self.encoder = tiktoken.get_encoding("cl100k_base")
        self.name = "tiktoken:cl100k_base:fallback" if offline_fallback else "tiktoken:cl100k_base"
        if offline_fallback:
            safety_factor = max(safety_factor, 1.10)
        self.safety_factor = max(1.0, safety_factor)

    def count_text(self, text: str | None) -> int:
        if not text:
            return 0
        return math.ceil(len(self.encoder.encode(text)) * self.safety_factor)

    def truncate_text(self, text: str, max_tokens: int) -> str:
        if max_tokens <= 0:
            return ""
        encoded = self.encoder.encode(text)
        capacity = max(1, int(max_tokens / self.safety_factor))
        if len(encoded) <= capacity:
            return text
        suffix = "…[截断]"
        suffix_tokens = len(self.encoder.encode(suffix))
        raw_limit = max(0, capacity - suffix_tokens)
        return self.encoder.decode(encoded[:raw_limit]) + suffix


class HuggingFaceCounter:
    def __init__(self, model: str) -> None:
        from transformers import AutoTokenizer

        self.tokenizer = AutoTokenizer.from_pretrained(model, local_files_only=True)
        self.name = f"huggingface:{model}"

    def count_text(self, text: str | None) -> int:
        if not text:
            return 0
        return len(self.tokenizer.encode(text, add_special_tokens=False))

    def truncate_text(self, text: str, max_tokens: int) -> str:
        tokens = self.tokenizer.encode(text, add_special_tokens=False)
        if len(tokens) <= max_tokens:
            return text
        suffix = "…[截断]"
        suffix_tokens = self.tokenizer.encode(suffix, add_special_tokens=False)
        content_limit = max(0, max_tokens - len(suffix_tokens))
        decoded = self.tokenizer.decode(tokens[:content_limit], skip_special_tokens=True)
        text = decoded if isinstance(decoded, str) else "".join(decoded)
        return text + suffix


@lru_cache(maxsize=16)
def get_token_counter(
    model: str,
    provider: str = "auto",
    tokenizer_model: str = "",
    fallback_safety_factor: float = 1.15,
) -> TokenCounter:
    normalized = provider.strip().lower()
    if normalized == "huggingface" or tokenizer_model:
        try:
            return HuggingFaceCounter(tokenizer_model or model)
        except (ImportError, OSError, ValueError) as exc:
            logger.warning("tokenizer unavailable for %s, using tiktoken fallback: %s", model, exc)
    safety_factor = 1.0 if normalized == "tiktoken" or model.startswith(("gpt-", "o1", "o3", "o4")) else fallback_safety_factor
    return TiktokenCounter(model, safety_factor=safety_factor)


def count_message(counter: TokenCounter, message: ChatMessage) -> int:
    """Count content plus OpenAI-compatible message protocol fields."""
    total = 4  # conservative role/message framing overhead
    total += counter.count_text(message.role)
    total += counter.count_text(message.content)
    total += counter.count_text(message.tool_call_id)
    total += counter.count_text(message.tool_name)
    if message.tool_calls:
        payload = [
            {"id": call.id, "name": call.name, "arguments": call.arguments}
            for call in message.tool_calls
        ]
        total += counter.count_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return total


def count_tools(counter: TokenCounter, tools: list[dict[str, Any]] | None) -> int:
    if not tools:
        return 0
    return counter.count_text(json.dumps(tools, ensure_ascii=False, sort_keys=True)) + 4
