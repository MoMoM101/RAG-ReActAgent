"""ReAct Agent Loop with SSE event yielding."""

import json
import logging
import time
from llm.base import ChatMessage, ToolCall
from llm.factory import create_llm
from agent.tools import registry, ToolResult
from agent.context import ContextManager
from agent.context_window import get_window, is_context_error
from config import settings

logger = logging.getLogger(__name__)
_MIN_CONTEXT_WINDOW = 16000


async def run_agent_loop(
    user_message: str,
    conversation_history: list[ChatMessage],
):
    """Async generator yielding SSE events as dicts."""
    llm = create_llm()
    context_window = get_window()
    ctx_manager = ContextManager(max_tokens=context_window)
    start_time = time.time()
    iteration = 0

    # 1. Intent classification: 规则优先 + LLM 兜底
    from agent.classifier import classify_intent, llm_classify
    hint = classify_intent(user_message, conversation_history)
    if hint.intent == "_llm_needed":
        hint = await llm_classify(user_message, conversation_history)
    yield {"event": "status", "data": {"message": "正在分析问题..."}}

    # 1.5. 记忆前置拦截 — 正则提取 + 批量 LLM 确认
    from agent.intercept import extract_memory_candidates, confirm_candidates_batch
    from memory.profile import handle_intercept
    candidates = extract_memory_candidates(user_message)
    # LLM 分类器提取的 save_to_profile
    for item in (hint.save_to_profile or []):
        c = item.get("content", ""); t = item.get("type", "fact")
        if c and (c, t) not in candidates:
            candidates.append((c, t))
    saved = []
    if candidates:
        confirmed = await confirm_candidates_batch(candidates)
        for candidate, mem_type in confirmed:
            await handle_intercept(candidate, mem_type)
            saved.append(candidate)
    if saved:
        hint.hint_text = (
            f"[系统] 已记录: {'; '.join(saved)}\n" + hint.hint_text
        )

    # 1.6. 仅 personal_memory 意图时预加载记忆到系统提示
    if hint.intent == "personal_memory" and any(
        t in hint.suggested_tools for t in ("recall_memory",)
    ):
        from memory.profile import search_profile
        recalled = await search_profile(user_message, top_k=5)
        if recalled:
            recall_text = "\n".join(
                f"- {r['text']}" for r in recalled
            )
            hint.hint_text = (
                f"[系统] 记忆检索结果:\n{recall_text}\n" + hint.hint_text
            )

    # 2. Build messages
    tools = registry.get_schemas()
    tools_desc = "\n".join(
        f"- {t['function']['name']}: {t['function']['description']}"
        for t in tools
    )
    from memory.profile import get_profile as get_profile_data, format_profile
    profile_data = await get_profile_data()
    profile_text = format_profile(profile_data)
    system_msg = ChatMessage(
        role="system",
        content=ctx_manager.build_system_prompt(hint.hint_text, tools_desc, profile_text),
    )

    messages = [system_msg] + list(conversation_history)
    messages.append(ChatMessage(role="user", content=user_message))

    # 3. ReAct Loop
    loop_exhausted = True  # False if break due to timeout
    while iteration < settings.max_loop_iterations:
        if time.time() - start_time > settings.max_total_time:
            loop_exhausted = False
            yield {"event": "error", "data": {"code": "TIME_LIMIT", "message": "请求超时"}}
            break

        # Context error retry loop
        _orig_system = system_msg.content
        _first_attempt = True
        ctx_manager = ContextManager(max_tokens=context_window)
        while True:
            system_msg.content = _orig_system
            trimmed, history_summary, dropped_queries = ctx_manager.trim_messages(messages)
            if history_summary and _first_attempt:
                system_msg.content = (_orig_system or "") + (
                    f"\n[历史摘要] 早期对话要点: {history_summary}"
                )
            if dropped_queries and _first_attempt:
                from agent.session_extract import _extract_with_llm as _batch_extract
                from memory.profile import handle_session_extract
                import asyncio as _asyncio
                _asyncio.create_task(_process_dropped(dropped_queries))
            _first_attempt = False
            yield {"event": "status", "data": {"message": "思考中..."}}

            tool_calls_acc = []
            assistant_content = ""
            try:
                async for chunk in llm.chat_stream(trimmed, tools=tools):
                    if chunk.reasoning_content:
                        yield {"event": "thought", "data": {"delta": chunk.reasoning_content}}
                    if chunk.content:
                        assistant_content += chunk.content
                        yield {"event": "answer_chunk", "data": {"delta": chunk.content}}
                    if chunk.tool_calls:
                        tool_calls_acc = chunk.tool_calls
                break  # LLM call succeeded
            except Exception as e:
                if is_context_error(e) and context_window // 2 >= _MIN_CONTEXT_WINDOW:
                    context_window //= 2
                    ctx_manager = ContextManager(max_tokens=context_window)
                    logger.warning(
                        "context overflow detected, reducing window to %d", context_window,
                    )
                    yield {"event": "status", "data": {"message": "上下文过长，自动调整中..."}}
                    continue
                elif is_context_error(e):
                    yield {
                        "event": "error",
                        "data": {
                            "code": "CONTEXT_ERROR",
                            "message": (
                                f"当前模型 {settings.llm_model} 上下文窗口过小（探测至 "
                                f"{_MIN_CONTEXT_WINDOW} 仍然失败），请更换模型或在 .env "
                                f"中设置 LLM_MAX_CONTEXT"
                            ),
                        },
                    }
                    return
                raise

        # Stream ended — branch on tool_calls or final answer
        if tool_calls_acc:
            messages.append(ChatMessage(
                role="assistant",
                content=assistant_content or None,
                tool_calls=tool_calls_acc,
            ))
            for tc in tool_calls_acc:
                yield {
                    "event": "tool_call",
                    "data": {"tool": tc.name, "args": tc.arguments, "call_id": tc.id},
                }

                try:
                    result = await registry.execute(tc.name, **tc.arguments)
                except Exception as e:
                    result = ToolResult(success=False, error=str(e))

                result_count = 0
                if result.data:
                    for key in ("results", "documents"):
                        items = result.data.get(key, [])
                        if isinstance(items, list):
                            result_count = len(items)
                            break
                    if result_count == 0:
                        result_count = result.data.get("count", 0)

                yield {
                    "event": "tool_result",
                    "data": {
                        "tool": tc.name,
                        "success": result.success,
                        "result_count": result_count,
                        "reranked": result.data.get("reranked", False) if result.data else False,
                        "error": result.error,
                    },
                }

                result_text = (
                    json.dumps(result.data, ensure_ascii=False)
                    if result.success
                    else f"Error: {result.error}"
                )
                if tc.name == "search_docs" and result.success:
                    result_text = (
                        "【以下是你唯一可以使用的回答来源。只能引用这些内容回答用户，"
                        "禁止使用你自己的知识或训练数据中的信息。"
                        "如果以下内容不足以回答问题，如实告知用户。】\n"
                        + result_text
                    )
                messages.append(ChatMessage(
                    role="tool",
                    content=result_text,
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                ))

            # Trim after appending tool results to prevent token accumulation
            messages, _, _ = ctx_manager.trim_messages(messages)
            iteration += 1
            continue

        # No tool calls — final answer (content already streamed)
        sources = _extract_sources(messages)
        if sources:
            yield {"event": "sources", "data": sources}
        yield {"event": "done", "data": {}}
        return

    # Loop limit reached — force final synthesis
    if loop_exhausted:
        yield {
            "event": "error",
            "data": {"code": "LOOP_LIMIT", "message": f"思考轮次已达上限（{settings.max_loop_iterations} 轮）"},
        }
    yield {"event": "status", "data": {"message": "已达到最大思考轮次，正在整理答案..."}}

    force_prompt = ChatMessage(
        role="user",
        content=(
            "你已达到最大工具调用次数。现在请根据上述所有工具调用的结果，"
            "直接回答用户最初的问题。不要调用任何工具，只给出最终答案。"
            "如果信息不足以完整回答，请基于已有信息给出最佳答案，并说明局限性。"
        ),
    )
    trimmed, _, _ = ctx_manager.trim_messages(messages + [force_prompt])

    summary_failed = False
    try:
        async for chunk in llm.chat_stream(trimmed, tools=None):
            if chunk.content:
                yield {"event": "answer_chunk", "data": {"delta": chunk.content}}
    except Exception:
        summary_failed = True
        logger.warning("force final answer failed after loop limit", exc_info=True)
        yield {
            "event": "answer_chunk",
            "data": {"delta": "抱歉，思考轮次已达上限，且自动总结失败。请尝试简化问题或提供更具体的指引。"},
        }

    sources = _extract_sources(messages)
    if sources:
        yield {"event": "sources", "data": sources}
    if summary_failed:
        yield {"event": "status", "data": {"message": "注意：思考轮次已达上限且总结失败，以上为兜底回复"}}
    else:
        yield {"event": "status", "data": {"message": "注意：思考轮次已达上限，以上为自动总结"}}
    yield {"event": "done", "data": {}}


async def _process_dropped(queries: list[str]):
    """后台处理被裁剪丢弃的用户消息，提取画像信息。"""
    try:
        from agent.intercept import extract_memory_candidates, confirm_candidates_batch
        from memory.profile import handle_intercept
        from agent.session_extract import _extract_with_llm

        # 1. 正则提取
        candidates = []
        for q in queries:
            for c, t in extract_memory_candidates(q):
                if (c, t) not in candidates:
                    candidates.append((c, t))

        # 2. 正则未覆盖的，批量 LLM 提取
        llm_input = "\n".join(queries)
        llm_extracted = await _extract_with_llm(llm_input)
        for item in (llm_extracted or []):
            c = item.get("content", "")
            t = item.get("memory_type", "fact")
            if c and (c, t) not in candidates:
                candidates.append((c, t))

        # 3. 批量确认 + 写入画像
        confirmed = await confirm_candidates_batch(candidates)
        for content, mem_type in confirmed:
            await handle_intercept(content, mem_type)
    except Exception:
        logger.warning(
            "_process_dropped failed for %d queries, memory extraction skipped",
            len(queries), exc_info=True
        )


def _extract_sources(messages: list[ChatMessage]) -> list[dict]:
    """Extract sources from the last search_docs tool result."""
    for msg in reversed(messages):
        if msg.role == "tool" and msg.content and msg.tool_name == "search_docs":
            try:
                content = msg.content
                if content.startswith("【"):
                    idx = content.find("】\n")
                    if idx > 0:
                        content = content[idx + 2:]
                data = json.loads(content)
                if "results" in data:
                    sources: list[dict] = []
                    for i, r in enumerate(data["results"]):
                        sources.append({
                            "document_id": r.get("document_id", ""),
                            "filename": r.get("filename", r.get("document_id", "")[:8]),
                            "text": r.get("text", "")[:200],
                            "score": r.get("score", 0),
                            "rank": i + 1,
                        })
                    return sources
            except json.JSONDecodeError:
                pass
    return []
