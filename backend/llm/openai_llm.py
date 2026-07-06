import json
from openai import AsyncOpenAI
from config import settings
from .base import BaseLLM, LLMResponse, ToolCall, ChatMessage


class OpenAILLM(BaseLLM):
    def __init__(self, api_key: str | None = None, base_url: str | None = None, model: str | None = None):
        self.client = AsyncOpenAI(
            api_key=api_key or settings.llm_api_key,
            base_url=base_url or settings.llm_base_url,
        )
        self.model = model or settings.llm_model

    def _build_messages(self, messages: list[ChatMessage]) -> list[dict]:
        result = []
        for m in messages:
            msg = {"role": m.role}
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

    async def chat_stream(self, messages: list[ChatMessage], tools: list[dict] | None = None):
        kwargs = {"model": self.model, "messages": self._build_messages(messages), "stream": True}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        stream = await self.client.chat.completions.create(**kwargs)
        tool_call_buf: dict[int, dict] = {}  # index → {id, name, args_str}
        has_tool_calls = False

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
