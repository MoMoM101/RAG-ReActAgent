"""Bounded, rebuildable working-context snapshots for conversations."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update

from agent.token_counter import TokenCounter, get_token_counter
from config import settings
from models.database import session_scope
from models.orm import Conversation

_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class WorkingContextState:
    summary: dict[str, Any]
    through_message_id: str | None
    source_hash: str | None
    token_count: int
    version: int


def _counter() -> TokenCounter:
    return get_token_counter(
        settings.llm_model,
        settings.tokenizer_provider,
        settings.tokenizer_model,
        settings.tokenizer_fallback_safety_factor,
    )


def _canonical(text: str) -> str:
    return _SPACE_RE.sub(" ", text.strip()).casefold()


def empty_summary() -> dict[str, Any]:
    return {
        "recent_context": [],
        "constraints": [],
        "decisions": [],
        "open_questions": [],
        "source_message_ids": [],
    }


def _append_unique(items: list[str], value: str, limit: int) -> None:
    normalized = _canonical(value)
    if not normalized or any(_canonical(item) == normalized for item in items):
        return
    items.append(value.strip())
    if len(items) > limit:
        del items[: len(items) - limit]


def merge_summary(
    previous: dict[str, Any] | None,
    queries: list[str],
    message_ids: list[str],
    *,
    counter: TokenCounter | None = None,
    max_tokens: int | None = None,
    max_items: int | None = None,
) -> tuple[dict[str, Any], int]:
    """Deterministically merge new user context into a bounded snapshot."""
    token_counter = counter or _counter()
    token_limit = max(1, max_tokens or settings.context_summary_max_tokens)
    item_limit = max(1, max_items or settings.context_summary_max_items)
    merged = empty_summary()
    if isinstance(previous, dict):
        for key in merged:
            values = previous.get(key, [])
            if isinstance(values, list):
                merged[key] = [str(value) for value in values[-item_limit:] if value]

    for query in queries:
        clean = query.strip()
        if not clean:
            continue
        _append_unique(merged["recent_context"], clean, item_limit)
        if any(marker in clean for marker in ("必须", "需要", "不要", "不能", "允许", "限制")):
            _append_unique(merged["constraints"], clean, item_limit)
        if any(marker in clean for marker in ("决定", "采用", "选择", "先用", "确定")):
            _append_unique(merged["decisions"], clean, item_limit)
        if clean.endswith(("?", "？")):
            _append_unique(merged["open_questions"], clean, item_limit)
    for message_id in message_ids:
        _append_unique(merged["source_message_ids"], message_id, item_limit)

    eviction_order = (
        "recent_context",
        "open_questions",
        "constraints",
        "decisions",
        "source_message_ids",
    )
    serialized = json.dumps(merged, ensure_ascii=False, sort_keys=True)
    while token_counter.count_text(serialized) > token_limit:
        evicted = False
        for key in eviction_order:
            if merged[key]:
                merged[key].pop(0)
                evicted = True
                break
        if not evicted:
            break
        serialized = json.dumps(merged, ensure_ascii=False, sort_keys=True)
    return merged, token_counter.count_text(serialized)


def format_working_context(summary: dict[str, Any] | None) -> str:
    if not summary:
        return ""
    labels = {
        "recent_context": "早期上下文",
        "constraints": "约束",
        "decisions": "已定事项",
        "open_questions": "待解决问题",
    }
    sections = []
    for key, label in labels.items():
        values = summary.get(key, [])
        if isinstance(values, list) and values:
            sections.append(f"{label}: " + "；".join(str(value) for value in values))
    return "\n".join(sections)


async def load_working_context(conversation_id: str) -> WorkingContextState | None:
    async with session_scope() as session:
        result = await session.execute(
            select(
                Conversation.context_summary,
                Conversation.context_summary_through_id,
                Conversation.context_summary_source_hash,
                Conversation.context_summary_token_count,
                Conversation.context_summary_version,
            ).where(Conversation.id == conversation_id)
        )
        row = result.one_or_none()
    if row is None:
        return None
    try:
        summary = json.loads(row[0]) if row[0] else empty_summary()
    except (json.JSONDecodeError, TypeError):
        summary = empty_summary()
    return WorkingContextState(summary, row[1], row[2], row[3] or 0, row[4] or 0)


async def compact_working_context(
    conversation_id: str,
    queries: list[str],
    message_ids: list[str],
) -> WorkingContextState | None:
    """Merge and save with optimistic locking; bounded retries prevent stale overwrite."""
    if not conversation_id or not queries:
        return await load_working_context(conversation_id) if conversation_id else None
    source_material = "\x1f".join(message_ids or queries)
    source_hash = hashlib.sha256(source_material.encode()).hexdigest()
    for _attempt in range(3):
        current = await load_working_context(conversation_id)
        if current is None:
            return None
        if current.source_hash == source_hash:
            return current
        summary, token_count = merge_summary(current.summary, queries, message_ids)
        serialized = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        through_id = message_ids[-1] if message_ids else current.through_message_id
        async with session_scope() as session:
            result = await session.execute(
                update(Conversation)
                .where(
                    Conversation.id == conversation_id,
                    Conversation.context_summary_version == current.version,
                )
                .values(
                    context_summary=serialized,
                    context_summary_through_id=through_id,
                    context_summary_source_hash=source_hash,
                    context_summary_token_count=token_count,
                    context_summary_version=current.version + 1,
                    context_summary_updated_at=datetime.now(UTC),
                )
            )
            await session.commit()
        if result.rowcount == 1:
            from metrics import get_metrics

            get_metrics().record_context_compaction(len(queries))
            return WorkingContextState(summary, through_id, source_hash, token_count, current.version + 1)
    raise RuntimeError("working context update conflicted after 3 attempts")
