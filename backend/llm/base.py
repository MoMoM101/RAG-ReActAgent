from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    is_final: bool = True
    finish_reason: str | None = None


@dataclass
class ChatMessage:
    role: str  # system / user / assistant / tool
    content: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_calls: list[ToolCall] | None = None  # assistant messages with tool calls
    message_id: str | None = None


class BaseLLM(ABC):
    @abstractmethod
    def chat_stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[LLMResponse, None]:
        """Async generator yielding LLMResponse chunks for streaming"""
        raise NotImplementedError
