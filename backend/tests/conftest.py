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
    os.environ["AUTO_MIGRATE"] = "1"
    _root.mkdir(parents=True, exist_ok=True)
    (_root / "qdrant").mkdir(exist_ok=True)
    (_root / "uploads").mkdir(exist_ok=True)

import pytest
import pytest_asyncio
from sqlalchemy import text as sa_text

# Ensure ORM models are registered with Base.metadata BEFORE init_db()
import models.orm  # noqa: F401
from llm.base import BaseLLM, LLMResponse
from models.database import Base, check_revision_gate, engine, init_db


def pytest_configure(config):
    """Set test environment variables before any application imports."""
    root = Path(tempfile.mkdtemp(prefix="rag_agent_tests_"))
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{root / 'test.db'}"
    os.environ["QDRANT_PATH"] = str(root / "qdrant")
    os.environ["UPLOAD_DIR"] = str(root / "uploads")
    os.environ["AUTO_MIGRATE"] = "1"
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

    async def chat_stream(self, messages=None, tools=None, max_tokens=None):
        if self.call_index >= len(self.queues):
            yield LLMResponse(content="[FakeLLM exhausted]")
            return
        queue = self.queues[self.call_index]
        self.call_index += 1
        for resp in queue:
            yield resp


TEST_ADMIN_TOKEN = "evaluation-admin-token"


@pytest.fixture
def admin_headers() -> dict[str, str]:
    """Return headers with the test admin token for protected routes."""
    return {"X-Admin-Token": TEST_ADMIN_TOKEN}


@pytest.fixture
def enable_admin_token(monkeypatch):
    """Enable admin token auth for the test, so require_admin enforces it."""
    from config import settings
    monkeypatch.setattr(settings, "admin_api_token", TEST_ADMIN_TOKEN)


@pytest.fixture
def mock_embedding(monkeypatch):
    """Replace embedding factory with vectors matching the configured dimension.

    Avoids real API calls for restore/fault injection tests.
    Matching the active configuration keeps collections compatible when a
    local .env overrides the default embedding model or dimension.
    """
    import random as _random
    from unittest.mock import AsyncMock

    from config import settings

    mock = AsyncMock()
    dimension = settings.embedding_dim

    def _random_vec():
        return [_random.uniform(-1, 1) for _ in range(dimension)]

    mock.embed = AsyncMock(return_value=[_random_vec() for _ in range(100)])
    mock.embed_query = AsyncMock(return_value=_random_vec())

    import embedding.factory
    original = embedding.factory.create_embedding
    embedding.factory.create_embedding = lambda: mock
    yield mock
    embedding.factory.create_embedding = original


@pytest.fixture
def make_fake_llm():
    """创建 FakeLLM 并注入到 llm.factory。auto-use setup_db 已调用 reset_llm。"""
    import llm.factory

    def _make(queues: list[list[LLMResponse]]) -> FakeLLM:
        fake = FakeLLM(queues)
        llm.factory._llm_instance = fake
        return fake

    return _make


@pytest_asyncio.fixture
async def setup_db():
    # 重置模块级单例，避免测试间状态污染
    from storage import reset_storage

    from embedding.factory import reset_embedding
    from llm.factory import reset_llm
    from vectordb.qdrant import reset_client_for_test
    reset_embedding()
    reset_llm()
    reset_storage()
    reset_client_for_test()

    await check_revision_gate()
    await init_db()
    # Keep database and storage state isolated across tests.  Recovery tests in
    # particular must not reschedule unfinished rows created by earlier cases.
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())
        await conn.execute(sa_text("DELETE FROM chunks_fts"))
    from storage import get_storage
    await get_storage().clear()
    yield


@pytest_asyncio.fixture
async def bootstrap_admin(setup_db):
    """Create the bootstrap admin user (lifespan doesn't run under ASGITransport)."""
    from main import _bootstrap_user
    await _bootstrap_user()


def pytest_collection_modifyitems(items):
    """Auto-add 'db' marker and setup_db fixture to tests that need database access."""
    db_subdirs = {"api", "agent", "memory", "rag", "textdb", "vectordb", "storage",
                   "llm", "embedding", "reranker", "worker"}
    db_root_files = {"test_e2e_mvp", "test_startup_degraded"}
    for item in items:
        parent = item.path.parent.name
        if parent in db_subdirs or (parent == "tests" and item.path.stem in db_root_files):
            item.add_marker(pytest.mark.db)
            item.fixturenames.append("setup_db")
