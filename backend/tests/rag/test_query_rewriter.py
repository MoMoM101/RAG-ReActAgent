from unittest.mock import patch

import pytest

from llm.base import LLMResponse
from rag.query_rewriter import rewrite


class StubLLM:
    def __init__(self, chunks):
        self.chunks = chunks
        self.messages = None

    async def chat_stream(self, messages):
        self.messages = messages
        for chunk in self.chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield LLMResponse(content=chunk)


@pytest.mark.asyncio
async def test_rewrite_collects_filters_and_caps_variants():
    llm = StubLLM(["部署流程\n", "A\n上线步骤\n额外版本"])

    with patch("rag.query_rewriter.create_llm", return_value=llm):
        variants = await rewrite("如何部署", n_variants=3)

    assert variants == ["部署流程", "上线步骤"]
    assert llm.messages[0].role == "system"
    assert "如何部署" in llm.messages[1].content


@pytest.mark.asyncio
async def test_rewrite_non_positive_count_skips_llm_creation():
    with patch("rag.query_rewriter.create_llm") as create:
        assert await rewrite("query", n_variants=0) == []
    create.assert_not_called()


@pytest.mark.asyncio
async def test_rewrite_returns_empty_when_factory_fails():
    with patch("rag.query_rewriter.create_llm", side_effect=RuntimeError("offline")):
        assert await rewrite("query") == []


@pytest.mark.asyncio
async def test_rewrite_returns_empty_when_stream_fails():
    llm = StubLLM([RuntimeError("stream failed")])
    with patch("rag.query_rewriter.create_llm", return_value=llm):
        assert await rewrite("query") == []
