import os

# Must set BEFORE any application imports — use test database to avoid destroying production data
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./data/test_rag_agent.db"
os.environ["QDRANT_PATH"] = "./data/test_qdrant"
os.environ["UPLOAD_DIR"] = "./data/test_uploads"

import pytest
import pytest_asyncio
from models.database import init_db, engine
from sqlalchemy import text as sa_text
from llm.base import BaseLLM, LLMResponse, ToolCall


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
    reset_embedding()
    reset_llm()

    await init_db()
    # Clean FTS data before each test
    async with engine.begin() as conn:
        await conn.execute(sa_text("DELETE FROM chunks_fts"))
    yield
