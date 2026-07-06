from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    is_final: bool = True


@dataclass
class ChatMessage:
    role: str  # system / user / assistant / tool
    content: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_calls: list[ToolCall] | None = None  # assistant messages with tool calls


class BaseLLM(ABC):
    @abstractmethod
    async def chat_stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None = None,
    ):
        """Async generator yielding LLMResponse chunks for streaming"""
        ...
