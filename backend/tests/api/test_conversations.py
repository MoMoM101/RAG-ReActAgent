"""Conversation-history response tests."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.conversations import get_conversation_context, get_messages


@pytest.mark.asyncio
async def test_get_messages_includes_structured_tool_result_summary():
    message = SimpleNamespace(
        id="message-1",
        role="tool",
        content="Success: 0 results",
        tool_name="list_documents",
        tool_call_id="call-1",
        tool_args="{}",
        tool_result_json=(
            '{"count": 2, "documents": '
            '[{"filename": "one.docx"}, {"filename": "two.pdf"}]}'
        ),
        sources=None,
        verification=None,
        created_at=datetime(2026, 7, 22, tzinfo=UTC),
    )
    scalar_result = MagicMock()
    scalar_result.scalars.return_value.all.return_value = [message]
    db = AsyncMock()
    db.execute.return_value = scalar_result

    response = await get_messages("conversation-1", db)

    assert response[0]["tool_result_summary"] == {
        "kind": "documents",
        "count": 2,
    }


@pytest.mark.asyncio
async def test_context_status_reports_exact_counts_without_source_ids():
    conversation = SimpleNamespace(
        id="conversation-1",
        context_summary=(
            '{"recent_context":["old question"],'
            '"source_message_ids":["message-1","message-2"]}'
        ),
        context_summary_through_id="message-2",
        context_summary_token_count=31,
        context_summary_version=2,
        context_summary_updated_at=datetime(2026, 7, 22, tzinfo=UTC),
    )
    scalar_result = MagicMock()
    scalar_result.scalar_one_or_none.return_value = conversation
    db = AsyncMock()
    db.execute.return_value = scalar_result

    response = await get_conversation_context("conversation-1", db)

    assert response["source_message_count"] == 2
    assert response["token_count"] == 31
    assert response["version"] == 2
    assert "source_message_ids" not in response["summary"]
