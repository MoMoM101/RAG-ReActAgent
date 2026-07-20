"""SSE grounded-answer verification and persistence contract tests."""

import json
from unittest.mock import AsyncMock

import pytest

from api import chat as chat_api


async def _grounded_events(*_args, **_kwargs):
    yield {"event": "answer_chunk", "data": {"delta": "Python 3.10 is required. [S1]"}}
    yield {"event": "sources", "data": [{
        "citation_id": "S1",
        "document_id": "doc-1",
        "document_key": "deploy-guide",
        "section_key": "python-version",
        "filename": "deployment.md",
        "text": "Python 3.10 is required.",
        "score": 0.9,
        "rank": 1,
    }]}
    yield {"event": "done", "data": {}}


@pytest.mark.asyncio
async def test_verification_event_precedes_done_and_is_persisted(monkeypatch):
    save = AsyncMock()
    monkeypatch.setattr(chat_api, "run_agent_loop", _grounded_events)
    monkeypatch.setattr(chat_api, "_save_messages", save)
    monkeypatch.setattr(chat_api.settings, "grounding_verification_enabled", True)
    monkeypatch.setattr(chat_api.settings, "grounding_enforcement", "report")

    chunks = [chunk async for chunk in chat_api.sse_generator("query", [], "conv-1")]
    event_names = [chunk.split("\n", 1)[0].removeprefix("event: ") for chunk in chunks]

    assert event_names[-2:] == ["verification", "done"]
    verification = json.loads(chunks[-2].split("data: ", 1)[1])
    assert verification["status"] == "verified"
    assert verification["citation_precision"] == 1.0
    assert save.await_args.kwargs["verification"]["status"] == "verified"


@pytest.mark.asyncio
async def test_strict_mode_appends_warning_for_unsupported_answer(monkeypatch):
    async def unsupported_events(*_args, **_kwargs):
        yield {"event": "answer_chunk", "data": {"delta": "Python 2.7 is required. [S1]"}}
        yield {"event": "sources", "data": [{
            "citation_id": "S1", "document_id": "doc-1",
            "text": "Python 3.10 is required.",
        }]}
        yield {"event": "done", "data": {}}

    save = AsyncMock()
    monkeypatch.setattr(chat_api, "run_agent_loop", unsupported_events)
    monkeypatch.setattr(chat_api, "_save_messages", save)
    monkeypatch.setattr(chat_api.settings, "grounding_verification_enabled", True)
    monkeypatch.setattr(chat_api.settings, "grounding_enforcement", "strict")

    chunks = [chunk async for chunk in chat_api.sse_generator("query", [], "conv-1")]

    assert any("部分内容未获得检索来源" in chunk for chunk in chunks)
    assert "部分内容未获得检索来源" in save.await_args.args[1]


@pytest.mark.asyncio
async def test_system_fallback_is_not_scored_as_grounded_answer(monkeypatch):
    async def fallback_events(*_args, **_kwargs):
        yield {
            "event": "answer_chunk",
            "data": {"delta": "抱歉，本次生成的内容未能通过来源校验，请重新提问。"},
        }
        yield {
            "event": "timing",
            "data": {
                "repair_used": "stream_verify_empty_fallback",
                "repair_reasons": ["empty_after_stream_verification"],
            },
        }
        yield {"event": "sources", "data": [{
            "citation_id": "S1", "document_id": "doc-1",
            "text": "Python 3.10 is required.",
        }]}
        yield {"event": "done", "data": {}}

    save = AsyncMock()
    monkeypatch.setattr(chat_api, "run_agent_loop", fallback_events)
    monkeypatch.setattr(chat_api, "_save_messages", save)
    monkeypatch.setattr(chat_api.settings, "grounding_verification_enabled", True)
    monkeypatch.setattr(chat_api.settings, "grounding_enforcement", "strict")

    chunks = [chunk async for chunk in chat_api.sse_generator("query", [], "conv-1")]
    event_names = [
        chunk.split("\n", 1)[0].removeprefix("event: ")
        for chunk in chunks
    ]

    assert "verification" not in event_names
    assert not any("部分内容未获得检索来源" in chunk for chunk in chunks)
    assert save.await_args.kwargs["verification"] is None
