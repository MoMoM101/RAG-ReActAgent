"""ReAct Agent Loop with SSE event yielding."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from functools import partial
from typing import TYPE_CHECKING

from agent.answer_format import normalize_answer_markdown
from agent.context import ContextManager
from agent.context_window import get_window, is_context_error
from agent.loop_setup import (
    apply_memory_context,
    classify_turn,
)
from agent.loop_support import (
    build_answer_cache_key as _build_answer_cache_key,
)
from agent.loop_support import (
    cache_sources_accessible as _cache_sources_accessible,
)
from agent.loop_support import (
    repair_single_unit as _repair_single_unit,
)
from agent.loop_support import (
    verify_stream_unit as _verify_stream_unit,
)
from agent.loop_tools import ToolTurnState, execute_tool_turn
from agent.query_semantics import (
    requires_whole_answer_validation,
    resolve_followup_query,
    sanitize_conversation_history,
)
from agent.tools import registry
from agent.verifier import Evidence, verify_answer
from config import settings
from llm.base import ChatMessage
from llm.factory import create_llm

if TYPE_CHECKING:
    from agent.context_state import WorkingContextState
    from agent.stream_verify import AtomicUnit

logger = logging.getLogger(__name__)
_CONTEXT_RETRY_SCALES = (0.85, 0.70, 0.50)


def _pending_dropped_messages(
    queries: list[str],
    message_ids: list[str],
    context_state: WorkingContextState | None,
) -> tuple[list[str], list[str]]:
    """Return only dropped messages newer than the persisted compaction watermark."""
    if context_state is None or len(message_ids) != len(queries):
        return queries, message_ids
    through_id = context_state.through_message_id
    if through_id and through_id in message_ids:
        boundary = message_ids.index(through_id) + 1
        return queries[boundary:], message_ids[boundary:]
    known_ids = set(context_state.summary.get("source_message_ids", []))
    pending_pairs = [
        (query, message_id)
        for query, message_id in zip(queries, message_ids, strict=True)
        if message_id not in known_ids
    ]
    return (
        [query for query, _message_id in pending_pairs],
        [message_id for _query, message_id in pending_pairs],
    )


async def run_agent_loop(
    user_message: str,
    conversation_history: list[ChatMessage],
    cancelled: asyncio.Event | None = None,
    conversation_id: str | None = None,
    user_message_id: str | None = None,
):
    """Async generator yielding SSE events as dicts.

    If `cancelled` is set, the loop stops at the next safe boundary.
    """
    llm = create_llm()
    # ── V4: Optional fast repair model ──
    _repair_llm = None
    if settings.grounding_repair_model:
        from llm.openai_llm import OpenAILLM

        _repair_llm = OpenAILLM(model=settings.grounding_repair_model)

    def _get_repair_llm():
        return _repair_llm if _repair_llm is not None else llm

    context_window = get_window()
    ctx_manager = ContextManager.from_settings(context_window)
    start_time = time.time()
    turn_sources: list[dict] = []
    citation_by_source: dict[str, str] = {}
    search_groups_by_source: dict[str, set[str]] = {}
    iteration = 0
    grounding_guard_enabled = settings.grounding_verification_enabled and settings.grounding_enforcement != "off"
    # ── V4 timing instrumentation ──
    _timing: dict[str, float] = {}
    _phase_start = time.time()

    def _record_phase(name: str) -> None:
        nonlocal _phase_start
        now = time.time()
        _timing[name] = (now - _phase_start) * 1000
        _phase_start = now

    def _record_elapsed(name: str) -> None:
        """Record request elapsed time for end-to-end milestones."""
        _timing[name] = (time.time() - start_time) * 1000

    def _is_cancelled() -> bool:
        return cancelled is not None and cancelled.is_set()

    prepared_history = sanitize_conversation_history(conversation_history)

    # 1. Intent classification: 规则优先 + LLM 兜底
    hint = await classify_turn(user_message, prepared_history)
    grounding_query = resolve_followup_query(user_message, prepared_history)
    if grounding_query != user_message:
        hint.hint_text += f"\n[系统] 已将当前追问解析为：{grounding_query}"
    _record_phase("rag_intent")
    yield {"event": "status", "data": {"message": "正在分析问题..."}}

    # 1.5. 记忆前置拦截 — 正则提取 + 批量 LLM 确认
    hint, saved = await apply_memory_context(user_message, hint)

    # 2. Build messages
    tools = registry.get_schemas()
    tools_desc = "\n".join(f"- {t['function']['name']}: {t['function']['description']}" for t in tools)
    from memory.profile import format_profile
    from memory.profile import get_profile as get_profile_data

    profile_data = await get_profile_data()
    profile_text = format_profile(profile_data)
    working_context = ""
    context_state = None
    if conversation_id:
        try:
            from agent.context_state import format_working_context, load_working_context

            context_state = await load_working_context(conversation_id)
            working_context = format_working_context(
                context_state.summary if context_state else None
            )
        except Exception:
            logger.warning(
                "failed to load working context conv_id=%s",
                conversation_id,
                exc_info=True,
            )
    system_msg = ChatMessage(
        role="system",
        content=ctx_manager.build_system_prompt(hint.hint_text, tools_desc, profile_text),
    )
    if working_context:
        system_msg.content = (
            (system_msg.content or "")
            + "\n\n## 工作上下文（早期对话的有界摘要）\n"
            + working_context
        )
    base_system_content = system_msg.content

    messages = [system_msg] + prepared_history
    messages.append(ChatMessage(
        role="user", content=user_message, message_id=user_message_id,
    ))

    # 3. ReAct Loop
    loop_exhausted = True  # False if break due to timeout
    trimmed: list[ChatMessage] = []
    scheduled_drop_fingerprints: set[str] = set()
    _verification_retries = 0
    _tool_call_history: list[tuple[str, str]] = []  # (tool_name, query_arg) for loop detection
    _loop_force_converge = False  # set when loop detected → disable tools
    while iteration < settings.max_loop_iterations:
        if _is_cancelled():
            from tracing import peek_request_id

            logger.info("agent loop cancelled by client disconnect rid=%s", peek_request_id())
            yield {"event": "error", "data": {"code": "CANCELLED", "message": "客户端已断开连接"}}
            from metrics import get_metrics

            get_metrics().record_agent_run(iteration, timed_out=False, loop_limit=False)
            return

        if time.time() - start_time > settings.max_total_time:
            loop_exhausted = False
            from metrics import get_metrics

            get_metrics().record_agent_run(iteration, timed_out=True, loop_limit=False)
            yield {"event": "error", "data": {"code": "TIME_LIMIT", "message": "请求超时"}}
            break

        # Context error retry loop
        # Always rebuild from the immutable base. Otherwise every ReAct
        # iteration appends another copy of the temporary history summary.
        _orig_system = base_system_content
        _first_attempt = True
        _empty_response_retries = 0
        _empty_generation_reason = ""
        _recovery_trigger = ""
        ctx_manager = ContextManager.from_settings(context_window)
        context_retry_index = 0
        context_budget_scale = 1.0
        while True:
            system_msg.content = _orig_system
            trimmed, history_summary, dropped_queries = ctx_manager.trim_messages(
                messages,
                tools,
                budget_scale=context_budget_scale,
            )
            dropped_ids = list(ctx_manager.last_dropped_message_ids)
            pending_queries, pending_ids = _pending_dropped_messages(
                dropped_queries,
                dropped_ids,
                context_state,
            )
            if context_state and len(dropped_ids) == len(dropped_queries):
                history_summary = ctx_manager.counter.truncate_text(
                    "；".join(pending_queries[-10:]),
                    settings.context_summary_max_tokens,
                )
            if history_summary:
                system_msg.content = (_orig_system or "") + (f"\n[历史摘要] 早期对话要点: {history_summary}")
                trimmed, _, _ = ctx_manager.trim_messages(
                    messages,
                    tools,
                    budget_scale=context_budget_scale,
                )
            if pending_queries:
                from worker.tasks import get_task_manager

                source_material = "\x1f".join(pending_ids or pending_queries)
                fingerprint = hashlib.sha256(source_material.encode()).hexdigest()
                if fingerprint not in scheduled_drop_fingerprints:
                    scheduled_drop_fingerprints.add(fingerprint)
                    task_manager = get_task_manager()
                    if conversation_id:
                        from agent.context_state import compact_working_context

                        task_manager.create(
                            partial(
                                compact_working_context,
                                conversation_id,
                                pending_queries,
                                pending_ids,
                            ),
                            "compact_context",
                            metadata={
                                "conv_id": conversation_id,
                                "dropped_count": len(pending_queries),
                            },
                            idempotency_key=(
                                f"context-compact:v1:{conversation_id}:{fingerprint}"
                            ),
                            task_type="compact_context",
                            payload={
                                "conversation_id": conversation_id,
                                "queries": pending_queries,
                                "message_ids": pending_ids,
                            },
                        )
                    task_manager.create(
                        partial(_process_dropped, pending_queries),
                        "process_dropped",
                        metadata={
                            "conv_id": conversation_id,
                            "dropped_count": len(pending_queries),
                        },
                        idempotency_key=(
                            f"memory-dropped:v1:{conversation_id or 'anonymous'}:{fingerprint}"
                        ),
                        task_type="process_dropped_memories",
                        payload={"queries": pending_queries},
                    )
            _first_attempt = False

            # V4 answer-cache fast path: retrieval has completed and the exact
            # source set is known, but final answer generation has not started.
            if turn_sources and settings.rag_answer_cache_enabled and not saved:
                try:
                    from rag.answer_cache import get_answer_cache

                    cache_key = _build_answer_cache_key(
                        user_message,
                        prepared_history,
                        turn_sources,
                        profile_text,
                    )
                    cached = get_answer_cache().get(cache_key)
                    if cached and _cache_sources_accessible(cached.sources):
                        from metrics import get_metrics as _cache_metrics

                        _cache_metrics().record_answer_cache("hit")
                        _record_elapsed("rag_visible_ttft")
                        cached_answer = normalize_answer_markdown(cached.answer)
                        yield {
                            "event": "answer_chunk",
                            "data": {"delta": cached_answer},
                        }
                        yield {"event": "sources", "data": cached.sources}
                        _record_elapsed("rag_total")
                        _cache_metrics().record_phase_timing(
                            "rag_visible_ttft",
                            _timing["rag_visible_ttft"],
                        )
                        _cache_metrics().record_phase_timing(
                            "rag_total",
                            _timing["rag_total"],
                        )
                        yield {
                            "event": "timing",
                            "data": {
                                "repair_used": "cache_hit",
                                "repair_reasons": [],
                                "cache_hit": True,
                                "rag_visible_ttft": round(
                                    _timing["rag_visible_ttft"],
                                    1,
                                ),
                                "rag_total": round(_timing["rag_total"], 1),
                            },
                        }
                        yield {"event": "done", "data": {}}
                        _cache_metrics().record_agent_run(
                            iteration,
                            timed_out=False,
                            loop_limit=False,
                        )
                        return
                    from metrics import get_metrics as _cache_metrics

                    _cache_metrics().record_answer_cache(
                        "stale" if cached else "miss",
                    )
                except Exception:
                    logger.warning("cache fast-path lookup failed", exc_info=True)
                    from metrics import get_metrics as _cache_metrics

                    _cache_metrics().record_answer_cache("miss")

            yield {"event": "status", "data": {"message": "思考中..."}}

            tool_calls_acc = []
            assistant_content = ""
            _last_finish_reason: str | None = None
            _reasoning_seen = False
            _first_token_recorded = False
            # ── V4 Phase 3: Stream verify state ──
            _stream_verify_active = (
                turn_sources
                and grounding_guard_enabled
                and settings.grounding_stream_verify_enabled
                and not requires_whole_answer_validation(grounding_query)
            )
            _unit_buffer: AtomicUnitBuffer | None = None
            _committed_units: list[AtomicUnit] = []
            _stream_emitted_parts: list[str] = []
            _stream_needs_repair = False
            _stream_repair_reasons: list[str] = []
            if _stream_verify_active:
                from agent.stream_verify import (
                    AtomicUnitBuffer,
                    UnitVerdict,
                    build_repair_prompt,
                )

                _unit_buffer = AtomicUnitBuffer()
                # Build evidence list once for unit-level verification
                _stream_evidence = [
                    Evidence(
                        citation_id=s["citation_id"],
                        text=s.get("text", ""),
                        document_key=s.get("document_key", ""),
                        section_key=s.get("section_key", ""),
                        filename=s.get("filename", ""),
                    )
                    for s in turn_sources
                ]
            try:
                # Per-call deadline from remaining total time
                remaining = max(1.0, settings.max_total_time - (time.time() - start_time))
                llm_deadline = min(remaining, settings.llm_read_timeout)
                _llm_started = time.time()
                _rag_final_generation = bool(turn_sources)
                _generation_start = time.time()
                from tracing import span as _span

                with _span("agent.llm_call", model=getattr(llm, "model", "unknown"), iteration=iteration):
                    async with asyncio.timeout(llm_deadline):
                        _gen_max_tokens = (
                            settings.rag_generation_max_tokens
                            or settings.llm_output_token_reserve
                            or None
                        )
                        _call_messages = trimmed
                        _call_tools: list[dict] | None = tools
                        if _loop_force_converge:
                            _call_tools = None  # disable further tool calls
                        if _empty_response_retries:
                            recovery_instruction = ChatMessage(
                                role="user",
                                content=(
                                    "上一次生成没有产生最终正文。现在只输出给用户看的最终答案，"
                                    "不要描述思考过程，不要调用工具。答案保持简洁，并确保每个事实"
                                    "都由现有检索结果支持且带有 [S数字] 引用。"
                                ),
                            )
                            _call_messages = [*trimmed, recovery_instruction]
                            _call_tools = None
                            _gen_max_tokens = (
                                max(
                                    _gen_max_tokens or 0,
                                    settings.rag_truncation_recovery_max_tokens,
                                )
                                or None
                            )
                        elif _verification_retries:
                            verify_retry_instruction = ChatMessage(
                                role="user",
                                content=(
                                    "上一次回答未能通过来源校验。请根据反馈自行调整："
                                    "检查你的回答中哪些陈述缺少 [S数字] 引用、哪些数字"
                                    "在来源中找不到对应、哪些内容超出了来源范围。"
                                    "如果来源确实不包含相关信息，可以尝试用其他工具"
                                    "（如 web_search 联网搜索）补充后再回答，"
                                    "或诚实说明知识库的局限。"
                                ),
                            )
                            _call_messages = [*trimmed, verify_retry_instruction]
                            _call_tools = tools  # allow tool calls so agent can escalate to web_search
                        async for chunk in llm.chat_stream(
                            _call_messages,
                            tools=_call_tools,
                            max_tokens=_gen_max_tokens,
                        ):
                            if chunk.is_final and chunk.finish_reason:
                                _last_finish_reason = chunk.finish_reason
                            if _rag_final_generation and time.time() - _generation_start > settings.rag_timeout_generation:
                                logger.warning("generation timeout reached, stopping generation")
                                break
                            if not _first_token_recorded and (chunk.reasoning_content or chunk.content):
                                _first_token_recorded = True
                                if _rag_final_generation:
                                    _timing["rag_llm_ttft"] = (time.time() - _llm_started) * 1000
                            if chunk.reasoning_content:
                                _reasoning_seen = True
                                yield {"event": "thought", "data": {"delta": chunk.reasoning_content}}
                            if chunk.content:
                                assistant_content += chunk.content
                                if _stream_verify_active and _unit_buffer is not None:
                                    # Feed token to unit buffer, check for complete units
                                    unit = _unit_buffer.feed(chunk.content)
                                    while unit is not None:
                                        result = _verify_stream_unit(
                                            unit,
                                            _stream_evidence,
                                            grounding_query,
                                        )
                                        if result.verdict in (UnitVerdict.VERIFIED, UnitVerdict.FORMAT_ONLY):
                                            emit_text = unit.text
                                            if result.verdict == UnitVerdict.FORMAT_ONLY:
                                                # Try deterministic repair on this unit
                                                emit_text, was_repaired = _repair_single_unit(
                                                    unit,
                                                    result,
                                                    _stream_evidence,
                                                )
                                                if not was_repaired:
                                                    _stream_needs_repair = True
                                                    _stream_repair_reasons.append(
                                                        result.reason or "missing_citation",
                                                    )
                                                    from metrics import get_metrics as _sm

                                                    _sm().record_stream_unit("held")
                                                    unit = _unit_buffer.extract_next()
                                                    continue
                                                from metrics import get_metrics as _sm

                                                _sm().record_stream_unit("repaired")
                                            else:
                                                from metrics import get_metrics as _sm

                                                _sm().record_stream_unit("verified")
                                            # Emit the verified unit immediately
                                            if not _timing.get("rag_visible_ttft"):
                                                _record_elapsed("rag_visible_ttft")
                                            emitted = emit_text + unit.boundary
                                            _stream_emitted_parts.append(emitted)
                                            yield {"event": "answer_chunk", "data": {"delta": emitted}}
                                            _committed_units.append(unit)
                                            _unit_buffer.commit(unit)
                                        elif result.verdict == UnitVerdict.UNSUPPORTED:
                                            _stream_needs_repair = True
                                            _stream_repair_reasons.append(result.reason)
                                            from metrics import get_metrics as _sm

                                            _sm().record_stream_unit("held")
                                            unit = _unit_buffer.extract_next()
                                            continue
                                        else:
                                            # INCOMPLETE — shouldn't happen here
                                            break
                                        # Try next unit from buffer
                                        unit = _unit_buffer.extract_next()
                                elif not (turn_sources and grounding_guard_enabled):
                                    yield {"event": "answer_chunk", "data": {"delta": chunk.content}}
                            if chunk.tool_calls:
                                tool_calls_acc = chunk.tool_calls
                if (
                    turn_sources
                    and not tool_calls_acc
                    and not assistant_content.strip()
                    and _empty_response_retries < 1
                    and settings.rag_truncation_recovery_enabled
                ):
                    _empty_response_retries += 1
                    _recovery_trigger = (
                        "token_limit"
                        if _last_finish_reason == "length"
                        else "reasoning_without_final"
                        if _reasoning_seen
                        else "empty_final_answer"
                    )
                    from metrics import get_metrics as _generation_metrics

                    _generation_metrics().record_generation_status(
                        f"recovery_triggered:{_recovery_trigger}",
                    )
                    logger.warning(
                        "empty final RAG answer; retrying once reason=%s finish_reason=%s",
                        _recovery_trigger,
                        _last_finish_reason,
                    )
                    continue
                if turn_sources and not tool_calls_acc and not assistant_content.strip():
                    _empty_generation_reason = (
                        "generation_truncated"
                        if _last_finish_reason == "length" or _recovery_trigger == "token_limit"
                        else "reasoning_without_final"
                        if _reasoning_seen or _recovery_trigger == "reasoning_without_final"
                        else "empty_final_answer"
                    )
                    from metrics import get_metrics as _generation_metrics

                    _generation_metrics().record_generation_status(
                        f"recovery_failed:{_empty_generation_reason}",
                    )
                elif _empty_response_retries:
                    from metrics import get_metrics as _generation_metrics

                    _generation_metrics().record_generation_status(
                        f"recovery_succeeded:{_recovery_trigger}",
                    )
                break  # LLM call succeeded
            except TimeoutError:
                logger.warning("llm call timed out after %.1fs", llm_deadline)
                from metrics import get_metrics

                get_metrics().record_agent_run(iteration, timed_out=True, loop_limit=False)
                yield {"event": "error", "data": {"code": "LLM_TIMEOUT", "message": "模型响应超时"}}
                return
            except Exception as e:
                if is_context_error(e) and context_retry_index < len(_CONTEXT_RETRY_SCALES):
                    context_budget_scale = _CONTEXT_RETRY_SCALES[context_retry_index]
                    context_retry_index += 1
                    logger.warning(
                        "context overflow detected, retry=%d input_budget_scale=%.2f",
                        context_retry_index,
                        context_budget_scale,
                    )
                    yield {"event": "status", "data": {"message": "上下文过长，自动调整中..."}}
                    continue
                elif is_context_error(e):
                    yield {
                        "event": "error",
                        "data": {
                            "code": "CONTEXT_ERROR",
                            "message": (
                                f"当前模型 {settings.llm_model} 在三级渐进裁剪后仍然超出上下文窗口，"
                                "请更换模型或在 .env 中设置正确的 LLM_MAX_CONTEXT"
                            ),
                        },
                    }
                    return
                raise

        # Stream ended — branch on tool_calls or final answer
        if tool_calls_acc:
            tool_state = ToolTurnState(
                messages=messages,
                sources=turn_sources,
                citation_by_source=citation_by_source,
                search_groups_by_source=search_groups_by_source,
                timing=_timing,
            )
            outcome = await execute_tool_turn(
                tool_calls_acc,
                assistant_content,
                tool_state,
                ctx_manager,
                registry,
            )
            messages = outcome.messages
            for event in outcome.events:
                yield event

            # Loop detection: track tool calls, force convergence on repeats
            for tc in tool_calls_acc:
                query_arg = str(tc.arguments.get("query", ""))
                _tool_call_history.append((tc.name, query_arg))
            # Check for repeated calls to same tool with similar queries
            if not _loop_force_converge:
                same_tool_calls = [t for t in _tool_call_history if t[0] == tool_calls_acc[0].name]
                if len(same_tool_calls) >= 3:
                    _loop_force_converge = True
                    logger.warning(
                        "loop detected: %s called %d times; forcing convergence",
                        tool_calls_acc[0].name, len(same_tool_calls),
                    )

            # After tool execution, check cancellation before next iteration
            if _is_cancelled():
                from tracing import peek_request_id

                logger.info("agent loop cancelled after tool execution rid=%s", peek_request_id())
                yield {"event": "error", "data": {"code": "CANCELLED", "message": "客户端已断开连接"}}
                from metrics import get_metrics

                get_metrics().record_agent_run(iteration, timed_out=False, loop_limit=False)
                return

            iteration += 1
            continue

        # No tool calls — final answer.
        sources = turn_sources
        if sources:
            _timing["rag_draft_generation"] = (time.time() - _llm_started) * 1000

        # ── V4 Phase 3: Stream verify post-generation handling ──
        if _stream_verify_active and _unit_buffer is not None:
            # Flush remaining units from buffer (normal completion without repair)
            if _unit_buffer.pending_text:
                remaining_unit = _unit_buffer.flush_remainder()
                if remaining_unit:
                    result = _verify_stream_unit(
                        remaining_unit,
                        _stream_evidence,
                        grounding_query,
                    )
                    emit_text = remaining_unit.text
                    sendable = result.verdict == UnitVerdict.VERIFIED
                    if result.verdict == UnitVerdict.FORMAT_ONLY:
                        emit_text, sendable = _repair_single_unit(
                            remaining_unit,
                            result,
                            _stream_evidence,
                        )
                    if sendable:
                        if not _timing.get("rag_visible_ttft"):
                            _record_elapsed("rag_visible_ttft")
                        _stream_emitted_parts.append(emit_text)
                        yield {"event": "answer_chunk", "data": {"delta": emit_text}}
                        _committed_units.append(remaining_unit)
                    elif result.verdict != UnitVerdict.VERIFIED:
                        _stream_needs_repair = True
                        _stream_repair_reasons.append(
                            result.reason or "unsupported_claim",
                        )

            # Stream repair: a unit was unsupported, re-generate remaining content
            _repair_attempted = False
            if _stream_needs_repair and settings.grounding_repair_enabled:
                remaining_ms = (settings.max_total_time - (time.time() - start_time)) * 1000
                if remaining_ms >= 1500:
                    _repair_attempted = True
                    yield {"event": "status", "data": {"message": "正在校验来源并修正..."}}
                    # Build repair prompt for the remaining content
                    remaining_draft = assistant_content
                    if _committed_units:
                        committed_text = "".join(u.text + u.boundary for u in _committed_units)
                        # Extract remaining uncommitted draft
                        if committed_text and assistant_content.startswith(committed_text):
                            remaining_draft = assistant_content[len(committed_text) :]
                    repair_prompt = build_repair_prompt(
                        grounding_query,
                        sources,
                        _committed_units,
                        remaining_draft,
                    )
                    repair_messages = trimmed + [
                        ChatMessage(role="assistant", content=assistant_content),
                        ChatMessage(role="user", content=repair_prompt),
                    ]
                    _unit_buffer.reset_for_repair()
                    repaired_chunks: list[str] = []
                    _stream_repair_started = time.perf_counter()
                    try:
                        repair_timeout = min(
                            settings.grounding_repair_timeout,
                            remaining_ms / 1000,
                        )
                        from tracing import span as _span3

                        with _span3("agent.repair", model=getattr(_get_repair_llm(), "model", "unknown")):
                            async with asyncio.timeout(repair_timeout):
                                async for chunk in _get_repair_llm().chat_stream(
                                    repair_messages,
                                    tools=None,
                                    max_tokens=settings.grounding_repair_max_tokens,
                                ):
                                    if chunk.content:
                                        repaired_chunks.append(chunk.content)
                                        unit = _unit_buffer.feed(chunk.content)
                                        while unit is not None:
                                            result = _verify_stream_unit(
                                                unit,
                                                _stream_evidence,
                                                grounding_query,
                                            )
                                            emit_text = unit.text
                                            sendable = result.verdict == UnitVerdict.VERIFIED
                                            if result.verdict == UnitVerdict.FORMAT_ONLY:
                                                emit_text, sendable = _repair_single_unit(
                                                    unit,
                                                    result,
                                                    _stream_evidence,
                                                )
                                            if sendable:
                                                if not _timing.get("rag_visible_ttft"):
                                                    _record_elapsed("rag_visible_ttft")
                                                emitted = emit_text + unit.boundary
                                                _stream_emitted_parts.append(emitted)
                                                yield {"event": "answer_chunk", "data": {"delta": emitted}}
                                                _committed_units.append(unit)
                                                _unit_buffer.commit(unit)
                                            else:
                                                from metrics import get_metrics as _sm

                                                _sm().record_stream_unit("dropped")
                                            unit = _unit_buffer.extract_next()
                        # Flush remaining
                        rem = _unit_buffer.flush_remainder()
                        if rem:
                            result = _verify_stream_unit(
                                rem,
                                _stream_evidence,
                                grounding_query,
                            )
                            emit_text = rem.text
                            sendable = result.verdict == UnitVerdict.VERIFIED
                            if result.verdict == UnitVerdict.FORMAT_ONLY:
                                emit_text, sendable = _repair_single_unit(
                                    rem,
                                    result,
                                    _stream_evidence,
                                )
                            if sendable:
                                _stream_emitted_parts.append(emit_text)
                                yield {"event": "answer_chunk", "data": {"delta": emit_text}}
                                _committed_units.append(rem)
                        repair_used = "llm_stream"
                        repair_reasons = _stream_repair_reasons
                        _timing["rag_llm_repair"] = (time.perf_counter() - _stream_repair_started) * 1000
                    except TimeoutError:
                        logger.warning("stream repair timed out")
                        repair_used = "llm_stream_timeout"
                        repair_reasons = _stream_repair_reasons + ["llm_repair_timeout"]
                        _timing["rag_llm_repair"] = (time.perf_counter() - _stream_repair_started) * 1000
                    except Exception:
                        logger.warning("stream repair failed", exc_info=True)
                        repair_used = "llm_stream_error"
                        repair_reasons = _stream_repair_reasons + ["llm_repair_error"]
                        _timing["rag_llm_repair"] = (time.perf_counter() - _stream_repair_started) * 1000
                else:
                    logger.info("skipping stream repair: insufficient budget")
                    repair_used = "stream_repair_skipped"
                    repair_reasons = _stream_repair_reasons + ["insufficient_budget"]
                    # Flush safe units from buffer
                    rem = _unit_buffer.flush_remainder()
                    if rem:
                        result = _verify_stream_unit(
                            rem,
                            _stream_evidence,
                            grounding_query,
                        )
                        emit_text = rem.text
                        sendable = result.verdict == UnitVerdict.VERIFIED
                        if result.verdict == UnitVerdict.FORMAT_ONLY:
                            emit_text, sendable = _repair_single_unit(
                                rem,
                                result,
                                _stream_evidence,
                            )
                        if sendable:
                            _stream_emitted_parts.append(emit_text)
                            yield {"event": "answer_chunk", "data": {"delta": emit_text}}
                            _committed_units.append(rem)
            elif _stream_needs_repair:
                # Repair is disabled by config
                repair_used = "stream_repair_disabled"
                repair_reasons = _stream_repair_reasons + ["llm_repair_disabled_by_config"]
                # Flush safe units from buffer
                rem = _unit_buffer.flush_remainder()
                if rem:
                    result = _verify_stream_unit(
                        rem,
                        _stream_evidence,
                        grounding_query,
                    )
                    emit_text = rem.text
                    sendable = result.verdict == UnitVerdict.VERIFIED
                    if result.verdict == UnitVerdict.FORMAT_ONLY:
                        emit_text, sendable = _repair_single_unit(
                            rem,
                            result,
                            _stream_evidence,
                        )
                    if sendable:
                        _stream_emitted_parts.append(emit_text)
                        yield {"event": "answer_chunk", "data": {"delta": emit_text}}
                        _committed_units.append(rem)

            # Yield timing metrics for stream verify path
            _record_elapsed("rag_total")
            if not _repair_attempted and not _stream_needs_repair:
                repair_used = "stream_verify"
                repair_reasons = []

            # Never complete a turn with sources/thoughts but no visible answer.
            # Do not expose the rejected draft; retry once or emit a safe fallback.
            if _stream_verify_active and not "".join(_stream_emitted_parts).strip():
                if _empty_generation_reason:
                    empty_fallback = "抱歉，模型本次未能生成完整的最终答案，已自动重试但仍未恢复。请重新发送问题。"
                    empty_reason = _empty_generation_reason
                elif _verification_retries < 1:
                    _verification_retries += 1
                    logger.warning(
                        "stream verification dropped all units; retrying with strict prompt"
                    )
                    _unit_buffer = AtomicUnitBuffer()
                    _committed_units = []
                    _stream_emitted_parts = []
                    _stream_needs_repair = False
                    _stream_repair_reasons = []
                    assistant_content = ""
                    _last_finish_reason = None
                    continue
                else:
                    empty_fallback = (
                        "抱歉，知识库中的资料暂无法回答您的问题。"
                        "建议上传相关资料后重试，或换个问题提问。"
                    )
                    empty_reason = "empty_after_stream_verification_retried"
                _stream_emitted_parts.append(empty_fallback)
                if not _timing.get("rag_visible_ttft"):
                    _record_elapsed("rag_visible_ttft")
                yield {
                    "event": "answer_chunk",
                    "data": {"delta": empty_fallback},
                }
                repair_reasons = list(
                    dict.fromkeys(
                        [
                            *repair_reasons,
                            empty_reason,
                        ]
                    )
                )
                if repair_used == "stream_verify":
                    repair_used = "stream_verify_empty_fallback"

            # Cache exactly what was emitted, never the unverified draft.
            if settings.rag_answer_cache_enabled and _stream_emitted_parts:
                try:
                    from rag.answer_cache import CacheEntry, get_answer_cache

                    emitted_answer = "".join(_stream_emitted_parts)
                    cache = get_answer_cache()
                    cache.put(
                        _build_answer_cache_key(
                            user_message,
                            prepared_history,
                            sources,
                            profile_text,
                        ),
                        CacheEntry(
                            answer=emitted_answer,
                            sources=sources,
                            verification=verify_answer(
                                emitted_answer,
                                sources,
                            ).to_dict(),
                            collection_version=cache.collection_version,
                        ),
                    )
                except Exception:
                    logger.warning("stream cache store failed", exc_info=True)
            timing_payload = {
                "repair_used": repair_used,
                "repair_reasons": (
                    repair_reasons if _repair_attempted or _stream_needs_repair or _empty_generation_reason else []
                ),
                "stream_units_committed": len(_committed_units),
            }
            for phase in (
                "rag_intent",
                "rag_llm_ttft",
                "rag_rerank",
                "rag_retrieval",
                "rag_draft_generation",
                "rag_visible_ttft",
                "rag_llm_repair",
                "rag_total",
            ):
                if phase in _timing:
                    timing_payload[phase] = round(_timing[phase], 1)
                    from metrics import get_metrics as _stream_metrics

                    _stream_metrics().record_phase_timing(
                        phase,
                        _timing[phase],
                    )
            yield {"event": "timing", "data": timing_payload}

            if sources:
                yield {"event": "sources", "data": sources}
            yield {"event": "done", "data": {}}
            from metrics import get_metrics

            get_metrics().record_agent_run(iteration, timed_out=False, loop_limit=False)
            return

        if sources and grounding_guard_enabled and assistant_content:
            from agent.verifier import (
                GroundingDecision,
                apply_query_safety_guard,
                apply_zero_support_guard,
                build_partial_comparison_fallback,
                grounding_repair_instruction,
                needs_grounding_repair,
                select_better_grounded_answer,
            )

            normalized_draft = normalize_answer_markdown(assistant_content)
            final_content = apply_query_safety_guard(
                grounding_query,
                normalized_draft,
                has_context=bool(prepared_history),
            )
            # ── V4: Structured grounding decision ──
            from tracing import span as _span2

            _verification_started = time.perf_counter()
            with _span2("agent.verify"):
                try:
                    decision = await asyncio.wait_for(
                        asyncio.to_thread(
                            needs_grounding_repair,
                            final_content,
                            sources,
                            query=grounding_query,
                            coverage_recheck=settings.grounding_coverage_recheck_enabled,
                        ),
                        timeout=settings.rag_timeout_verification,
                    )
                except TimeoutError:
                    logger.warning("verification timed out — running safe repairs only")
                    decision = GroundingDecision(
                        action="deterministic_repair", reasons=["verification_timeout"],
                    )
            _timing["rag_verification"] = (time.perf_counter() - _verification_started) * 1000
            repair_used = "none"
            repair_reasons = []

            if "topical_false_refusal" in decision.reasons:
                from metrics import get_metrics as _diagnostic_metrics

                _diagnostic_metrics().record_full_refusal("detected")
                repair_reasons = list(decision.reasons)

            if decision.needs_repair:
                # ── Metrics: record repair trigger ──
                from metrics import get_metrics as _get_m

                for r in decision.reasons:
                    _get_m().record_repair_trigger(r)
                if "topical_false_refusal" in decision.reasons:
                    partial_fallback = build_partial_comparison_fallback(
                        grounding_query,
                        sources,
                    )
                    if partial_fallback:
                        final_content = partial_fallback
                        repair_used = "deterministic_partial"
                        repair_reasons = decision.reasons
                        decision = GroundingDecision(action="accept")
                        _get_m().record_deterministic_repair(
                            "partial_comparison_extract",
                        )
                        _get_m().record_full_refusal("converted_partial")
                # ── Phase 1: Deterministic repair first ──
                if settings.grounding_deterministic_repair_enabled and decision.action == "deterministic_repair":
                    from agent.grounding_repair import deterministic_repair

                    evidence = [
                        Evidence(
                            citation_id=s["citation_id"],
                            text=s.get("text", ""),
                            document_key=s.get("document_key", ""),
                            section_key=s.get("section_key", ""),
                            filename=s.get("filename", ""),
                        )
                        for s in sources
                    ]
                    _deterministic_started = time.perf_counter()
                    repair_result = deterministic_repair(
                        final_content,
                        evidence,
                        decision,
                        min_score=settings.grounding_auto_cite_min_score,
                        min_margin=settings.grounding_auto_cite_min_margin,
                    )
                    _timing["rag_deterministic_repair"] = (time.perf_counter() - _deterministic_started) * 1000
                    if repair_result.repaired:
                        final_content = repair_result.repaired_text
                        repair_used = "deterministic"
                        repair_reasons = decision.reasons
                        for change in repair_result.changes:
                            _get_m().record_deterministic_repair(change)
                        logger.info(
                            "deterministic repair applied: changes=%s",
                            repair_result.changes,
                        )
                    if repair_result.needs_llm:
                        # Deterministic repair wasn't enough — escalate to LLM
                        decision = GroundingDecision(
                            action="llm_repair",
                            reasons=repair_result.llm_reasons,
                        )
                elif decision.action == "deterministic_repair":
                    # Disabling deterministic repair must not silently accept
                    # an answer that failed the citation contract.
                    decision = GroundingDecision(
                        action="llm_repair",
                        reasons=decision.reasons,
                        verification=decision.verification,
                    )

                # ── Phase 2: Bounded LLM repair ──
                if settings.grounding_repair_enabled and decision.action == "llm_repair":
                    remaining_ms = (settings.max_total_time - (time.time() - start_time)) * 1000
                    # Budget check: need at least 1.5s for a repair
                    _llm_repair_started = time.perf_counter()
                    if remaining_ms >= 1500:
                        yield {"event": "status", "data": {"message": "正在校验来源并修正引用..."}}
                        repair_messages = trimmed + [
                            ChatMessage(role="assistant", content=final_content),
                            ChatMessage(
                                role="user",
                                content=grounding_repair_instruction(final_content),
                            ),
                        ]
                        repaired_chunks = []
                        try:
                            repair_timeout = min(
                                settings.grounding_repair_timeout,
                                remaining_ms / 1000,
                            )
                            from tracing import span as _span3

                            with _span3("agent.repair", model=getattr(_get_repair_llm(), "model", "unknown")):
                                async with asyncio.timeout(repair_timeout):
                                    async for chunk in _get_repair_llm().chat_stream(
                                        repair_messages,
                                        tools=None,
                                        max_tokens=settings.grounding_repair_max_tokens,
                                    ):
                                        if chunk.content:
                                            repaired_chunks.append(chunk.content)
                            repaired = "".join(repaired_chunks).strip()
                            if repaired:
                                selected = select_better_grounded_answer(
                                    final_content,
                                    repaired,
                                    sources,
                                )
                                repair_accepted = selected != final_content
                                final_content = selected
                                repair_used = "llm" if repair_accepted else "llm_rejected"
                                repair_reasons = decision.reasons
                                if repair_accepted:
                                    for r in decision.reasons:
                                        _get_m().record_repair_accept(r)
                                    if "topical_false_refusal" in decision.reasons:
                                        _get_m().record_full_refusal(
                                            "converted_partial",
                                        )
                        except TimeoutError:
                            logger.warning(
                                "grounding repair timed out after %.1fs",
                                repair_timeout,
                            )
                            repair_reasons = decision.reasons + ["llm_repair_timeout"]
                        except Exception:
                            logger.warning(
                                "grounding repair failed; using original draft",
                                exc_info=True,
                            )
                            repair_reasons = decision.reasons + ["llm_repair_error"]
                    else:
                        logger.info(
                            "skipping llm repair: only %.0fms remaining",
                            remaining_ms,
                        )
                        repair_reasons = decision.reasons + ["insufficient_budget"]
                    _timing["rag_llm_repair"] = (time.perf_counter() - _llm_repair_started) * 1000
                elif decision.action == "llm_repair":
                    repair_reasons = decision.reasons + [
                        "llm_repair_disabled_by_config",
                    ]

            query_guarded_content = apply_query_safety_guard(
                grounding_query,
                final_content,
                has_context=bool(prepared_history),
            )
            if query_guarded_content != final_content:
                comparison_fallback = build_partial_comparison_fallback(
                    grounding_query,
                    sources,
                )
                final_content = comparison_fallback or query_guarded_content
                repair_used = "deterministic_partial" if comparison_fallback else "safe_refusal"
                repair_reasons = list(repair_reasons) + ["incomplete_query_relation"]

            guarded_content = apply_zero_support_guard(final_content, sources)
            if guarded_content != final_content:
                final_content = guarded_content
                repair_used = "safe_refusal"
                repair_reasons = list(repair_reasons) + ["zero_supported_claims"]

            final_content = normalize_answer_markdown(final_content)

            # ── Yield final answer ──
            if final_content:
                # Store in answer cache for future identical queries
                if settings.rag_answer_cache_enabled and repair_used != "llm":
                    try:
                        from rag.answer_cache import CacheEntry, get_answer_cache

                        cv = get_answer_cache().collection_version
                        cache_key = _build_answer_cache_key(
                            user_message,
                            prepared_history,
                            sources,
                            profile_text,
                        )
                        get_answer_cache().put(
                            cache_key,
                            CacheEntry(
                                answer=final_content,
                                sources=sources,
                                verification=verify_answer(
                                    final_content,
                                    sources,
                                ).to_dict(),
                                collection_version=cv,
                            ),
                        )
                    except Exception:
                        logger.warning("cache store failed", exc_info=True)

                # visible_ttft: first moment user sees answer content
                if not _timing.get("rag_visible_ttft"):
                    _record_elapsed("rag_visible_ttft")
                yield {"event": "answer_chunk", "data": {"delta": final_content}}

            # ── Yield timing metrics ──
            _record_elapsed("rag_total")
            timing_payload = {
                "repair_used": repair_used,
                "repair_reasons": repair_reasons,
                "decision": decision.to_dict() if decision else None,
            }
            for phase in (
                "rag_intent",
                "rag_llm_ttft",
                "rag_rerank",
                "rag_retrieval",
                "rag_draft_generation",
                "rag_verification",
                "rag_deterministic_repair",
                "rag_llm_repair",
                "rag_visible_ttft",
                "rag_total",
            ):
                if phase in _timing:
                    timing_payload[phase] = round(_timing[phase], 1)
                    from metrics import get_metrics as _get_m4

                    _get_m4().record_phase_timing(phase, _timing[phase])
            yield {"event": "timing", "data": timing_payload}

        elif sources and assistant_content:
            # Grounding may be disabled in controlled rollouts, but answer
            # caching and end-to-end timing must still work on the RAG path.
            assistant_content = normalize_answer_markdown(assistant_content)
            if settings.rag_answer_cache_enabled:
                try:
                    from rag.answer_cache import CacheEntry, get_answer_cache

                    cache = get_answer_cache()
                    cache.put(
                        _build_answer_cache_key(
                            user_message,
                            prepared_history,
                            sources,
                            profile_text,
                        ),
                        CacheEntry(
                            answer=assistant_content,
                            sources=sources,
                            verification=verify_answer(
                                assistant_content,
                                sources,
                            ).to_dict(),
                            collection_version=cache.collection_version,
                        ),
                    )
                except Exception:
                    logger.warning("cache store failed", exc_info=True)
            if not _timing.get("rag_visible_ttft"):
                _record_elapsed("rag_visible_ttft")
            _record_elapsed("rag_total")
            timing_payload = {
                "repair_used": "none",
                "repair_reasons": [],
            }
            for phase in (
                "rag_intent",
                "rag_llm_ttft",
                "rag_rerank",
                "rag_retrieval",
                "rag_draft_generation",
                "rag_visible_ttft",
                "rag_total",
            ):
                if phase in _timing:
                    timing_payload[phase] = round(_timing[phase], 1)
                    from metrics import get_metrics as _plain_metrics

                    _plain_metrics().record_phase_timing(
                        phase,
                        _timing[phase],
                    )
            yield {"event": "timing", "data": timing_payload}

        elif sources and not assistant_content.strip():
            fallback = "抱歉，模型本次未能生成完整的最终答案，已自动重试但仍未恢复。请重新发送问题。"
            if not _timing.get("rag_visible_ttft"):
                _record_elapsed("rag_visible_ttft")
            _record_elapsed("rag_total")
            yield {"event": "answer_chunk", "data": {"delta": fallback}}
            yield {
                "event": "timing",
                "data": {
                    "repair_used": "generation_recovery_failed",
                    "repair_reasons": [
                        _empty_generation_reason or "empty_final_answer",
                    ],
                    "rag_visible_ttft": round(_timing["rag_visible_ttft"], 1),
                    "rag_total": round(_timing["rag_total"], 1),
                },
            }

        if sources:
            yield {"event": "sources", "data": sources}
        yield {"event": "done", "data": {}}
        from metrics import get_metrics

        get_metrics().record_agent_run(iteration, timed_out=False, loop_limit=False)
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

    sources = turn_sources
    if sources:
        yield {"event": "sources", "data": sources}
    if summary_failed:
        yield {"event": "status", "data": {"message": "注意：思考轮次已达上限且总结失败，以上为兜底回复"}}
    else:
        yield {"event": "status", "data": {"message": "注意：思考轮次已达上限，以上为自动总结"}}
    yield {"event": "done", "data": {}}
    from metrics import get_metrics

    get_metrics().record_agent_run(iteration, timed_out=False, loop_limit=True)


async def _process_dropped(queries: list[str]):
    """后台处理被裁剪丢弃的用户消息，提取画像信息。"""
    try:
        from agent.intercept import confirm_candidates_batch, extract_memory_candidates
        from agent.session_extract import _extract_with_llm
        from memory.profile import handle_intercept

        # 1. 正则提取
        candidates = []
        for q in queries:
            for c, t in extract_memory_candidates(q):
                if (c, t) not in candidates:
                    candidates.append((c, t))

        # 2. 正则未覆盖的，批量 LLM 提取
        llm_input = "\n".join(queries)
        llm_extracted = await _extract_with_llm(llm_input)
        for item in llm_extracted or []:
            c = item.get("content", "")
            t = item.get("memory_type", "fact")
            if c and (c, t) not in candidates:
                candidates.append((c, t))

        # 3. 批量确认 + 写入画像
        confirmed = await confirm_candidates_batch(candidates)
        for content, mem_type in confirmed:
            await handle_intercept(content, mem_type)
    except Exception:
        logger.warning("_process_dropped failed for %d queries, memory extraction skipped", len(queries), exc_info=True)
