import asyncio
import inspect
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any
from urllib.parse import urlparse

import httpx
from openai import APIStatusError, AsyncOpenAI

from config import settings

from .base import BaseLLM, ChatMessage, LLMResponse, ToolCall

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = {429, 408, 500, 502, 503, 504}


class OpenAILLM(BaseLLM):
    def __init__(self, api_key: str | None = None, base_url: str | None = None, model: str | None = None):
        timeout = httpx.Timeout(
            connect=settings.llm_connect_timeout,
            read=settings.llm_read_timeout,
            write=30.0,
            pool=10.0,
        )
        http_client = httpx.AsyncClient(proxy=None, trust_env=False, timeout=timeout)
        self.base_url = base_url or settings.llm_base_url
        self.client = AsyncOpenAI(
            api_key=api_key or settings.llm_api_key,
            base_url=self.base_url,
            http_client=http_client,
            max_retries=0,  # we handle retries ourselves for streaming
        )
        self.model = model or settings.llm_model

    def _thinking_extra_body(self) -> dict[str, Any] | None:
        """Return DeepSeek V4's explicit thinking-mode switch when applicable."""
        host = (urlparse(self.base_url).hostname or "").lower()
        if host != "api.deepseek.com" or not self.model.lower().startswith("deepseek-v4"):
            return None
        return {
            "thinking": {
                "type": "enabled" if settings.llm_thinking_enabled else "disabled",
            },
        }

    def _build_messages(self, messages: list[ChatMessage]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for m in messages:
            msg: dict[str, Any] = {"role": m.role}
            if m.content is not None:
                msg["content"] = m.content
            if m.tool_call_id:
                msg["tool_call_id"] = m.tool_call_id
            if m.tool_name:
                msg["name"] = m.tool_name
            if m.tool_calls:
                msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        },
                    }
                    for tc in m.tool_calls
                ]
            result.append(msg)
        return result

    async def chat_stream(
        self, messages: list[ChatMessage], tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[LLMResponse, None]:
        kwargs: dict[str, Any] = {"model": self.model, "messages": self._build_messages(messages), "stream": True}
        extra_body = self._thinking_extra_body()
        if extra_body is not None:
            kwargs["extra_body"] = extra_body
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if settings.llm_stream_usage_enabled:
            kwargs["stream_options"] = {"include_usage": True}

        last_exception: Exception | None = None
        for attempt in range(settings.llm_max_retries):
            try:
                while True:
                    try:
                        async for response in self._stream_once(kwargs):
                            yield response
                        return
                    except (APIStatusError, httpx.HTTPStatusError) as e:
                        status_code = getattr(e, "status_code", 0)
                        if not status_code:
                            http_response = getattr(e, "response", None)
                            status_code = http_response.status_code if http_response else 0
                        if status_code == 400 and "stream_options" in kwargs:
                            logger.info("provider rejected stream usage; retrying without stream_options")
                            kwargs.pop("stream_options", None)
                            continue
                        raise
            except APIStatusError as e:
                last_exception = e
                status_code = e.status_code
                if status_code not in _RETRYABLE_STATUS:
                    raise
            except (httpx.HTTPStatusError, httpx.RemoteProtocolError) as e:
                last_exception = e
                status = getattr(e, "response", None)
                status_code = status.status_code if status else 0
                if status_code not in _RETRYABLE_STATUS and status_code != 0:
                    raise
            except (httpx.TimeoutException, httpx.ConnectError,
                    httpx.NetworkError, httpx.ReadError) as e:
                last_exception = e
            except TimeoutError as e:
                last_exception = e

            if attempt < settings.llm_max_retries - 1:
                delay = settings.llm_retry_backoff * (2 ** attempt)
                logger.warning(
                    "llm retry attempt=%d/%d delay=%.1fs error=%s",
                    attempt + 1, settings.llm_max_retries, delay, str(last_exception)[:200],
                )
                await asyncio.sleep(delay)

        raise last_exception  # type: ignore[misc]

    async def _stream_once(self, kwargs: dict[str, Any]) -> AsyncGenerator[LLMResponse, None]:
        stream = await self.client.chat.completions.create(**kwargs)
        tool_call_buf: dict[int, dict] = {}  # index → {id, name, args_str}
        has_tool_calls = False
        token_count = 0
        usage_prompt_tokens = 0
        usage_completion_tokens = 0
        usage_total_tokens = 0
        finish_reason: str | None = None

        try:
            async for chunk in stream:
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    usage_prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
                    usage_completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
                    usage_total_tokens = int(getattr(usage, "total_tokens", 0) or 0)
                choice = chunk.choices[0] if chunk.choices and chunk.choices[0] else None
                if choice is not None and choice.finish_reason:
                    finish_reason = str(choice.finish_reason)
                delta = choice.delta if choice is not None else None
                if delta is None:
                    continue

                # Reasoning content → yield immediately (DeepSeek R1 et al.)
                reasoning = delta.model_extra.get("reasoning_content") if delta.model_extra else None
                if reasoning:
                    yield LLMResponse(reasoning_content=reasoning, is_final=False)

                # Content delta → yield immediately
                if delta.content:
                    token_count += len(delta.content) // 3  # rough estimate
                    yield LLMResponse(content=delta.content, is_final=False)

                # Tool call deltas → accumulate by index
                if delta.tool_calls:
                    has_tool_calls = True
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_call_buf:
                            tool_call_buf[idx] = {"id": "", "name": "", "args_str": ""}
                        if tc.id:
                            tool_call_buf[idx]["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                tool_call_buf[idx]["name"] = tc.function.name
                            if tc.function.arguments:
                                tool_call_buf[idx]["args_str"] += tc.function.arguments
        except asyncio.CancelledError:
            logger.info("llm stream cancelled, closing stream")
            await _close_stream_safely(stream)
            raise

        # Stream ended — yield final with aggregated tool_calls (if any)
        if has_tool_calls:
            tool_calls = []
            for idx in sorted(tool_call_buf.keys()):
                buf = tool_call_buf[idx]
                try:
                    args = json.loads(buf["args_str"])
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(ToolCall(
                    id=buf["id"],
                    name=buf["name"],
                    arguments=args,
                ))
            yield LLMResponse(
                tool_calls=tool_calls,
                is_final=True,
                finish_reason=finish_reason,
            )
        else:
            yield LLMResponse(
                content="",
                is_final=True,
                finish_reason=finish_reason,
            )

        try:
            from metrics import get_metrics
            get_metrics().record_llm_usage(
                usage_total_tokens or max(token_count, 1),
                prompt_tokens=usage_prompt_tokens,
                completion_tokens=usage_completion_tokens,
                estimated=usage_total_tokens == 0,
            )
        except Exception:
            pass


async def _close_stream_safely(stream: Any) -> None:
    """Close OpenAI-compatible streams across SDK/provider variants."""
    close = getattr(stream, "aclose", None) or getattr(stream, "close", None)
    if close is None:
        logger.debug("llm stream exposes no close method")
        return
    try:
        result = close()
        if inspect.isawaitable(result):
            await result
    except Exception:
        logger.warning("failed to close cancelled llm stream", exc_info=True)
