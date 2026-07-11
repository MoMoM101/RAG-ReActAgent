"""Pytest configuration — isolated temp directories for all tests.

Environment variables are set in pytest_configure (before any application
imports) so that config.Settings picks up the test values.
"""

import os
import shutil
import tempfile
from pathlib import Path

# --- Early env setup (module level, before any local imports) ---
# pytest_configure runs even earlier; this is the fallback for IDE runners.
if "DATABASE_URL" not in os.environ:
    _root = Path(tempfile.mkdtemp(prefix="rag_agent_tests_"))
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_root / 'test.db'}"
    os.environ["QDRANT_PATH"] = str(_root / "qdrant")
    os.environ["UPLOAD_DIR"] = str(_root / "uploads")
    _root.mkdir(parents=True, exist_ok=True)
    (_root / "qdrant").mkdir(exist_ok=True)
    (_root / "uploads").mkdir(exist_ok=True)

import pytest
import pytest_asyncio
from sqlalchemy import text as sa_text

# Ensure ORM models are registered with Base.metadata BEFORE init_db()
import models.orm  # noqa: F401
from llm.base import BaseLLM, LLMResponse
from models.database import engine, init_db


def pytest_configure(config):
    """Set test environment variables before any application imports."""
    root = Path(tempfile.mkdtemp(prefix="rag_agent_tests_"))
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{root / 'test.db'}"
    os.environ["QDRANT_PATH"] = str(root / "qdrant")
    os.environ["UPLOAD_DIR"] = str(root / "uploads")
    (root / "qdrant").mkdir(parents=True, exist_ok=True)
    (root / "uploads").mkdir(parents=True, exist_ok=True)
    # Store for cleanup
    config._rag_test_root = root


def pytest_unconfigure(config):
    """Clean up temporary test directories."""
    root = getattr(config, "_rag_test_root", None)
    if root and root.exists():
        shutil.rmtree(root, ignore_errors=True)


class FakeLLM(BaseLLM):
    """可注入预定响应的 LLM。每次 chat_stream 消费一个响应队列。"""

    def __init__(self, response_queues: list[list[LLMResponse]]):
        self.queues = response_queues
        self.call_index = 0

    async def chat_stream(self, messages=None, tools=None):
        if self.call_index >= len(self.queues):
            yield LLMResponse(content="[FakeLLM exhausted]")
            return
        queue = self.queues[self.call_index]
        self.call_index += 1
        for resp in queue:
            yield resp


@pytest.fixture
def make_fake_llm():
    """创建 FakeLLM 并注入到 llm.factory。auto-use setup_db 已调用 reset_llm。"""
    import llm.factory

    def _make(queues: list[list[LLMResponse]]) -> FakeLLM:
        fake = FakeLLM(queues)
        llm.factory._llm_instance = fake
        return fake

    return _make


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    # 重置模块级单例，避免测试间状态污染
    from embedding.factory import reset_embedding
    from llm.factory import reset_llm
    from vectordb.qdrant import reset_client_for_test
    reset_embedding()
    reset_llm()
    reset_client_for_test()

    await init_db()
    # Clean FTS data before each test
    async with engine.begin() as conn:
        await conn.execute(sa_text("DELETE FROM chunks_fts"))
    yield
