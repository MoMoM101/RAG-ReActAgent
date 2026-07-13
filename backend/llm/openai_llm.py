import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

import httpx
from openai import AsyncOpenAI

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
        self.client = AsyncOpenAI(
            api_key=api_key or settings.llm_api_key,
            base_url=base_url or settings.llm_base_url,
            http_client=http_client,
            max_retries=0,  # we handle retries ourselves for streaming
        )
        self.model = model or settings.llm_model

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
        self, messages: list[ChatMessage], tools: list[dict[str, Any]] | None = None
    ) -> AsyncGenerator[LLMResponse, None]:
        kwargs: dict[str, Any] = {"model": self.model, "messages": self._build_messages(messages), "stream": True}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        last_exception: Exception | None = None
        for attempt in range(settings.llm_max_retries):
            try:
                async for response in self._stream_once(kwargs):
                    yield response
                return
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

        try:
            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices and chunk.choices[0] else None
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
            await stream.aclose()
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
            yield LLMResponse(tool_calls=tool_calls, is_final=True)
        else:
            yield LLMResponse(content="", is_final=True)

        try:
            from metrics import get_metrics
            get_metrics().record_llm_usage(max(token_count, 1))
        except Exception:
            pass
