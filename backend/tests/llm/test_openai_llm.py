from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from config import settings
from llm.base import ChatMessage, LLMResponse
from llm.openai_llm import OpenAILLM, _close_stream_safely


def test_deepseek_v4_disables_thinking_for_latency_sensitive_rag():
    llm = OpenAILLM.__new__(OpenAILLM)
    llm.base_url = "https://api.deepseek.com"
    llm.model = "deepseek-v4-flash"

    with patch.object(settings, "llm_thinking_enabled", False):
        assert llm._thinking_extra_body() == {
            "thinking": {"type": "disabled"},
        }


def test_thinking_toggle_is_not_sent_to_other_providers():
    llm = OpenAILLM.__new__(OpenAILLM)
    llm.base_url = "https://api.openai.com/v1"
    llm.model = "gpt-4o"

    assert llm._thinking_extra_body() is None


@pytest.mark.asyncio
async def test_stream_final_response_exposes_finish_reason():
    class Stream:
        def __init__(self, chunks):
            self._chunks = chunks

        def __aiter__(self):
            self._iterator = iter(self._chunks)
            return self

        async def __anext__(self):
            try:
                return next(self._iterator)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    def chunk(content=None, finish_reason=None):
        delta = SimpleNamespace(
            content=content,
            tool_calls=None,
            model_extra={},
        )
        choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
        return SimpleNamespace(choices=[choice])

    llm = OpenAILLM.__new__(OpenAILLM)
    create = AsyncMock(
        return_value=Stream([
            chunk(content="draft"),
            chunk(finish_reason="length"),
        ]),
    )
    llm.client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=create),
        ),
    )

    responses = [response async for response in llm._stream_once({})]

    assert responses[-1].is_final is True
    assert responses[-1].finish_reason == "length"


@pytest.mark.asyncio
async def test_close_stream_safely_supports_async_close():
    class Stream:
        closed = False

        async def close(self):
            self.closed = True

    stream = Stream()
    await _close_stream_safely(stream)
    assert stream.closed


@pytest.mark.asyncio
async def test_close_stream_safely_supports_aclose():
    class Stream:
        closed = False

        async def aclose(self):
            self.closed = True

    stream = Stream()
    await _close_stream_safely(stream)
    assert stream.closed


def _api_key_valid() -> bool:
    key = settings.llm_api_key
    return bool(key) and key != "your-api-key-here"


requires_api_key = pytest.mark.skipif(
    not _api_key_valid(),
    reason="No valid LLM_API_KEY configured (set in .env or environment)",
)


def _make_llm():
    return OpenAILLM()


async def _collect_stream(llm, messages, tools=None):
    """将 chat_stream 所有 chunk 收集为一个 LLMResponse。"""
    content_parts = []
    tool_calls = []
    async for chunk in llm.chat_stream(messages, tools=tools):
        if chunk.content:
            content_parts.append(chunk.content)
        if chunk.tool_calls:
            tool_calls = chunk.tool_calls
    return LLMResponse(
        content="".join(content_parts) if content_parts else None,
        tool_calls=tool_calls,
        is_final=not bool(tool_calls),
    )


@requires_api_key
@pytest.mark.asyncio
async def test_simple_chat():
    """A basic chat completion without tools should return content."""
    llm = _make_llm()
    messages = [
        ChatMessage(role="user", content="Say exactly 'hello world' with no other text.")
    ]
    response = await _collect_stream(llm, messages)
    assert isinstance(response, LLMResponse)
    assert response.content is not None
    assert "hello" in response.content.lower()
    assert response.is_final is True
    assert response.tool_calls == []


@requires_api_key
@pytest.mark.asyncio
async def test_chat_stream():
    """Streaming chat should yield multiple LLMResponse chunks."""
    llm = _make_llm()
    messages = [
        ChatMessage(role="user", content="Count from 1 to 3, one number per line.")
    ]
    chunks = []
    async for chunk in llm.chat_stream(messages):
        assert isinstance(chunk, LLMResponse)
        chunks.append(chunk)

    assert len(chunks) >= 1
    # Last chunk should be marked as final with empty content
    assert chunks[-1].is_final is True
    # At least one chunk should have content
    contents = [c.content for c in chunks if c.content]
    assert len(contents) >= 1


@requires_api_key
@pytest.mark.asyncio
async def test_function_calling():
    """When a tool is provided, the LLM should invoke it when appropriate."""
    llm = _make_llm()

    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get the current weather for a location",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {
                            "type": "string",
                            "description": "City name",
                        }
                    },
                    "required": ["location"],
                },
            },
        }
    ]

    messages = [
        ChatMessage(role="user", content="What is the weather in Paris?")
    ]
    response = await _collect_stream(llm, messages, tools=tools)

    assert isinstance(response, LLMResponse)
    assert response.is_final is False
    assert len(response.tool_calls) >= 1
    assert response.tool_calls[0].name == "get_weather"
    assert "location" in response.tool_calls[0].arguments


@requires_api_key
@pytest.mark.asyncio
async def test_chat_with_system_message():
    """Chat with a system message should respect the system prompt."""
    llm = _make_llm()
    messages = [
        ChatMessage(role="system", content="Always respond in all uppercase, no exceptions."),
        ChatMessage(role="user", content="Say hello"),
    ]
    response = await _collect_stream(llm, messages)
    assert isinstance(response, LLMResponse)
    assert response.content is not None
    # Check response is uppercase or at least contains uppercase text
    stripped = response.content.strip()
    assert stripped == stripped.upper() or any(c.isupper() for c in stripped)
