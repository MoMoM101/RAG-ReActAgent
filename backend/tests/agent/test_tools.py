from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import delete

from agent.tools import CalculatorTool
from models.database import session_scope
from models.orm import DocStatus, Document


class TestListDocumentsTool:
    @pytest.fixture
    def tool(self):
        from agent.tools import ListDocumentsTool
        return ListDocumentsTool()

    @pytest.mark.asyncio
    async def test_lists_documents(self, tool):
        # Clean up any stale test data
        async with session_scope() as session:
            await session.execute(
                delete(Document).where(Document.id.like("test-doc-%"))
            )
            await session.commit()

        # Seed test data
        async with session_scope() as session:
            for i in range(3):
                doc = Document(
                    id=f"test-doc-{i}",
                    filename=f"test_{i}.pdf",
                    file_hash=f"hash_{i}",
                    file_size=1024,
                    file_type="pdf",
                    status=DocStatus.ready,
                )
                session.add(doc)
            await session.commit()

        result = await tool.execute()
        assert result.success is True
        docs = result.data["documents"]
        assert len(docs) >= 3
        assert any(d["filename"] == "test_0.pdf" for d in docs)
        assert all("id" in d and "filename" in d and "status" in d for d in docs)

        # Clean up seeded data
        async with session_scope() as session:
            await session.execute(
                delete(Document).where(Document.id.like("test-doc-%"))
            )
            await session.commit()

    @pytest.mark.asyncio
    async def test_empty_returns_empty_list(self, tool):
        # Mock empty database
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        with patch('agent.tools.session_scope') as mock_scope:
            mock_cm = AsyncMock()
            mock_cm.__aenter__.return_value = mock_session
            mock_scope.return_value = mock_cm
            result = await tool.execute()

        assert result.success is True
        assert result.data["documents"] == []

    @pytest.mark.asyncio
    async def test_retry_strategy_is_exponential(self, tool):
        assert tool.retry_strategy == "exponential"
        assert tool.max_retries == 2


class TestCalculatorTool:
    @pytest.fixture
    def tool(self):
        return CalculatorTool()

    @pytest.mark.asyncio
    async def test_basic_addition(self, tool):
        result = await tool.execute(expression="1 + 2")
        assert result.success is True
        assert result.data["result"] == 3

    @pytest.mark.asyncio
    async def test_multiplication(self, tool):
        result = await tool.execute(expression="3 * 4")
        assert result.success is True
        assert result.data["result"] == 12

    @pytest.mark.asyncio
    async def test_division(self, tool):
        result = await tool.execute(expression="10 / 3")
        assert result.success is True
        assert abs(result.data["result"] - 3.333) < 0.01

    @pytest.mark.asyncio
    async def test_parentheses(self, tool):
        result = await tool.execute(expression="(1 + 2) * 3")
        assert result.success is True
        assert result.data["result"] == 9

    @pytest.mark.asyncio
    async def test_negative_number(self, tool):
        result = await tool.execute(expression="-5 + 3")
        assert result.success is True
        assert result.data["result"] == -2

    @pytest.mark.asyncio
    async def test_rejects_function_call(self, tool):
        result = await tool.execute(expression="__import__('os').system('dir')")
        assert result.success is False
        assert "不允许" in result.error

    @pytest.mark.asyncio
    async def test_rejects_attribute_access(self, tool):
        result = await tool.execute(expression="obj.attr")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_retry_strategy_is_none(self, tool):
        assert tool.retry_strategy == "none"
        assert tool.max_retries == 0


class TestGetDocumentInfoTool:
    @pytest.fixture
    def tool(self):
        from agent.tools import GetDocumentInfoTool
        return GetDocumentInfoTool()

    @pytest.mark.asyncio
    async def test_returns_document_info(self, tool):
        # Seed a document
        async with session_scope() as session:
            doc = Document(
                id="info-doc-1",
                filename="report.pdf",
                file_hash="hash_info",
                file_size=2048,
                file_type="pdf",
                status=DocStatus.ready,
                chunk_count=15,
                embedding_model="text-embedding-v4",
                embedding_dim=1536,
            )
            session.add(doc)
            await session.commit()

        result = await tool.execute(document_id="info-doc-1")
        assert result.success is True
        data = result.data
        assert data["filename"] == "report.pdf"
        assert data["file_type"] == "pdf"
        assert data["status"] == "ready"
        assert data["chunk_count"] == 15

        # Clean up
        async with session_scope() as session:
            await session.execute(
                delete(Document).where(Document.id == "info-doc-1")
            )
            await session.commit()

    @pytest.mark.asyncio
    async def test_not_found(self, tool):
        result = await tool.execute(document_id="nonexistent")
        assert result.success is False
        assert "未找到" in result.error

    @pytest.mark.asyncio
    async def test_retry_strategy_is_exponential(self, tool):
        assert tool.retry_strategy == "exponential"
        assert tool.max_retries == 2


class TestToolRegistryRetry:
    """Verify that RetryableError triggers exponential backoff retry in ToolRegistry."""

    @pytest.mark.asyncio
    async def test_retryable_triggers_retry(self):
        from agent.tools import BaseTool, RetryableError, ToolRegistry, ToolResult

        call_count = [0]

        class FlakyTool(BaseTool):
            name = "flaky"
            description = "test"
            parameters = {"type": "object", "properties": {}, "required": []}
            max_retries = 2
            retry_strategy = "exponential"

            async def execute(self, **kwargs):
                call_count[0] += 1
                if call_count[0] < 3:
                    raise RetryableError("transient failure")
                return ToolResult(success=True, data={"ok": True})

        r = ToolRegistry()
        r.register(FlakyTool())
        result = await r.execute("flaky")
        assert result.success is True
        assert result.retries == 2
        assert call_count[0] == 3

    @pytest.mark.asyncio
    async def test_business_error_no_retry(self):
        """ToolResult(success=False) should NOT trigger retry."""
        from agent.tools import BaseTool, ToolRegistry, ToolResult

        call_count = [0]

        class FailTool(BaseTool):
            name = "f"
            description = "test"
            parameters = {"type": "object", "properties": {}, "required": []}
            max_retries = 3
            retry_strategy = "exponential"

            async def execute(self, **kwargs):
                call_count[0] += 1
                return ToolResult(success=False, error="business error")

        r = ToolRegistry()
        r.register(FailTool())
        result = await r.execute("f")
        assert result.success is False
        assert call_count[0] == 1

    @pytest.mark.asyncio
    async def test_max_retries_exhausted(self):
        """Continuous RetryableError -> fail after max_retries+1 attempts."""
        from agent.tools import BaseTool, RetryableError, ToolRegistry

        call_count = [0]

        class AlwaysFail(BaseTool):
            name = "af"
            description = "test"
            parameters = {"type": "object", "properties": {}, "required": []}
            max_retries = 2
            retry_strategy = "exponential"

            async def execute(self, **kwargs):
                call_count[0] += 1
                raise RetryableError("always failing")

        r = ToolRegistry()
        r.register(AlwaysFail())
        result = await r.execute("af")
        assert result.success is False
        assert result.retries == 2
        assert call_count[0] == 3

    @pytest.mark.asyncio
    async def test_none_strategy_no_retry_on_retryable(self):
        """retry_strategy='none' should NOT retry even on RetryableError."""
        from agent.tools import BaseTool, RetryableError, ToolRegistry

        call_count = [0]

        class NoRetryTool(BaseTool):
            name = "nr"
            description = "test"
            parameters = {"type": "object", "properties": {}, "required": []}
            max_retries = 0
            retry_strategy = "none"

            async def execute(self, **kwargs):
                call_count[0] += 1
                raise RetryableError("should not retry")

        r = ToolRegistry()
        r.register(NoRetryTool())
        result = await r.execute("nr")
        assert result.success is False
        assert result.retries == 0
        assert call_count[0] == 1


class TestIsRetryableException:
    def test_asyncio_timeout_error(self):

        from agent.tools import _is_retryable_exception
        assert _is_retryable_exception(TimeoutError()) is True

    def test_builtin_connection_error(self):
        from agent.tools import _is_retryable_exception
        assert _is_retryable_exception(ConnectionError()) is True

    def test_builtin_timeout_error(self):
        from agent.tools import _is_retryable_exception
        assert _is_retryable_exception(TimeoutError()) is True

    def test_sqlalchemy_errors(self):
        from agent.tools import _is_retryable_exception

        try:
            from sqlalchemy import exc as sa_exc
            assert _is_retryable_exception(sa_exc.OperationalError("", "", "")) is True
            assert _is_retryable_exception(sa_exc.InterfaceError("", "", "")) is True
            assert _is_retryable_exception(sa_exc.TimeoutError("", "", "")) is True
            assert _is_retryable_exception(sa_exc.DBAPIError("", "", "")) is True
        except ImportError:
            pass

    def test_httpx_errors(self):
        from agent.tools import _is_retryable_exception

        try:
            import httpx
            assert _is_retryable_exception(httpx.TimeoutException("")) is True
            assert _is_retryable_exception(httpx.NetworkError("")) is True
            assert _is_retryable_exception(httpx.ConnectError("")) is True
            assert _is_retryable_exception(httpx.RemoteProtocolError("")) is True
            assert _is_retryable_exception(httpx.HTTPError("")) is True
        except ImportError:
            pass

    def test_qdrant_grpc_exception_by_module_name(self):
        from agent.tools import _is_retryable_exception

        class FakeQdrantError(Exception):
            pass

        FakeQdrantError.__module__ = "qdrant_client.grpc"
        assert _is_retryable_exception(FakeQdrantError()) is True

        class FakeGrpcError(Exception):
            pass

        FakeGrpcError.__module__ = "grpc.aio"
        assert _is_retryable_exception(FakeGrpcError()) is True

    def test_business_error_not_retryable(self):
        from agent.tools import _is_retryable_exception
        assert _is_retryable_exception(ValueError("bad value")) is False
        assert _is_retryable_exception(KeyError("missing")) is False
        assert _is_retryable_exception(Exception("generic")) is False

    def test_raise_if_retryable_raises(self):

        import pytest as _pytest

        from agent.tools import RetryableError, _raise_if_retryable

        with _pytest.raises(RetryableError):
            _raise_if_retryable(TimeoutError(), "test_tool")

    def test_raise_if_retryable_noop(self):
        from agent.tools import _raise_if_retryable
        _raise_if_retryable(ValueError("not retryable"), "test_tool")

    def test_retryable_error_wraps_original(self):
        import asyncio

        from agent.tools import RetryableError

        original = TimeoutError("timed out")
        try:
            raise RetryableError(f"[test_tool] {original}") from original
        except RetryableError as e:
            assert isinstance(e.__cause__, asyncio.TimeoutError)
            assert "test_tool" in str(e)


@pytest.mark.asyncio
async def test_unknown_tool_returns_structured_error():
    """Unknown tool name should return structured error, not KeyError."""
    from agent.tools import registry
    result = await registry.execute("nonexistent_tool", arg1="val")
    assert result.success is False
    assert "nonexistent_tool" in result.error
    assert "未知工具" in result.error


@pytest.mark.asyncio
async def test_tool_param_validation_rejects_invalid():
    """Calculator should reject missing required parameter."""
    from agent.tools import registry
    # Calculator requires 'expression' but we pass none
    result = await registry.execute("calculator")
    assert result.success is False
    assert "参数校验失败" in result.error


@pytest.mark.asyncio
async def test_tool_param_validation_allows_valid():
    """Calculator should accept valid parameters."""
    from agent.tools import registry
    result = await registry.execute("calculator", expression="2+2")
    assert result.success is True
    assert result.data["result"] == 4


def test_tool_param_validation_degrades_safely_without_jsonschema(monkeypatch):
    """A missing optional validator must not crash an active SSE tool call."""
    import builtins

    from agent.tools import _validate_tool_params, registry

    real_import = builtins.__import__

    def import_without_jsonschema(name, *args, **kwargs):
        if name == "jsonschema":
            raise ImportError("simulated missing jsonschema")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_jsonschema)
    assert _validate_tool_params(registry._tools["calculator"], {}) == ""


class TestSearchDocsRerankedFlag:
    @pytest.mark.asyncio
    async def test_reranked_false_when_results_degraded(self, monkeypatch):
        """结果带 rerank 降级标记时,reranked 必须为 False,即使 reranker 全局 ready。"""
        from agent.tools import SearchDocsTool
        from rag.retriever import RetrievalResult

        fake_results = [
            RetrievalResult(
                chunk_id="c1", document_id="d1", text="t1", score=0.9,
                source="hybrid", fallback_reason="rerank_timeout",
            ),
            RetrievalResult(
                chunk_id="c2", document_id="d1", text="t2", score=0.8,
                source="hybrid", fallback_reason="rerank_timeout",
            ),
        ]

        async def fake_search(query, top_k=0, document_id="", use_rerank=False):
            return fake_results

        import rag.retriever
        monkeypatch.setattr(rag.retriever, "hybrid_search", fake_search)
        import reranker.factory
        monkeypatch.setattr(reranker.factory, "is_reranker_ready", lambda: True)

        result = await SearchDocsTool().execute("测试查询")

        assert result.success is True
        assert result.data["count"] == 2
        assert result.data["reranked"] is False

    @pytest.mark.asyncio
    async def test_reranked_true_when_no_degradation(self, monkeypatch):
        """结果无降级标记且 reranker ready 时,reranked 为 True。"""
        from agent.tools import SearchDocsTool
        from rag.retriever import RetrievalResult

        fake_results = [
            RetrievalResult(
                chunk_id="c1", document_id="d1", text="t1", score=0.9,
                source="hybrid",
            ),
        ]

        async def fake_search(query, top_k=0, document_id="", use_rerank=False):
            return fake_results

        import rag.retriever
        monkeypatch.setattr(rag.retriever, "hybrid_search", fake_search)
        import reranker.factory
        monkeypatch.setattr(reranker.factory, "is_reranker_ready", lambda: True)

        result = await SearchDocsTool().execute("测试查询")

        assert result.data["reranked"] is True
