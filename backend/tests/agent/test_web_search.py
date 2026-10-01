"""Web search tool tests — _search_bing and _search_ddgs with mocked HTTP."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.tools import RetryableError, ToolResult, WebSearchTool


def _mock_httpx_client(mock_get_return=None, mock_get_side_effect=None):
    """Create a mock AsyncClient context manager for patching httpx.AsyncClient."""
    mock_client = MagicMock()
    mock_get = AsyncMock(return_value=mock_get_return, side_effect=mock_get_side_effect)
    mock_client.get = mock_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


class TestSearchBing:
    @pytest.fixture
    def tool(self):
        return WebSearchTool()

    @pytest.mark.asyncio
    async def test_bing_timeout_raises_retryable(self, tool):
        """asyncio.TimeoutError → RetryableError."""
        mock_client = _mock_httpx_client(
            mock_get_side_effect=TimeoutError("timed out")
        )
        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            pytest.raises(RetryableError, match="Bing"),
        ):
            await tool._search_bing("test query", 3)

    @pytest.mark.asyncio
    async def test_bing_network_error_raises_retryable(self, tool):
        """httpx.ConnectError → RetryableError (caught by generic except)."""
        import httpx

        mock_client = _mock_httpx_client(
            mock_get_side_effect=httpx.ConnectError("connection refused")
        )
        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            pytest.raises(RetryableError, match="Bing"),
        ):
            await tool._search_bing("test query", 3)

    @pytest.mark.asyncio
    async def test_bing_200_parses_results(self, tool):
        """HTTP 200 with b_algo list items → ToolResult(success=True)."""
        html = """<html><body>
        <ol id="b_results">
        <li class="b_algo">
            <h2><a href="https://example.com/page1">Title 1</a></h2>
            <div class="b_caption"><p>Snippet 1</p></div>
        </li>
        <li class="b_algo">
            <h2><a href="https://example.com/page2">Title 2</a></h2>
            <div class="b_caption"><p>Snippet 2</p></div>
        </li>
        </ol>
        </body></html>"""

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = html

        mock_client = _mock_httpx_client(mock_get_return=mock_response)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool._search_bing("test query", 3)

        assert result.success is True
        assert result.data["count"] >= 1
        assert result.data["results"][0]["title"] == "Title 1"
        assert "example.com" in result.data["results"][0]["url"]

    @pytest.mark.asyncio
    async def test_bing_non_200_returns_toolresult(self, tool):
        """HTTP non-200 → ToolResult(success=False)."""
        mock_response = MagicMock()
        mock_response.status_code = 403

        mock_client = _mock_httpx_client(mock_get_return=mock_response)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool._search_bing("test query", 3)

        assert result.success is False
        assert "403" in result.error

    @pytest.mark.asyncio
    async def test_bing_empty_parse_returns_success_zero(self, tool):
        """HTTP 200 but no matching selectors → count=0."""
        html = "<html><body><p>No results found</p></body></html>"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = html

        mock_client = _mock_httpx_client(mock_get_return=mock_response)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool._search_bing("test query", 3)

        assert result.success is True
        assert result.data["count"] == 0


class TestSearchDDGS:
    @pytest.fixture
    def tool(self):
        return WebSearchTool()

    @pytest.mark.asyncio
    async def test_ddgs_library_missing(self, tool):
        """Neither ddgs nor duckduckgo_search available → ToolResult(success=False)."""
        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name in ("ddgs", "duckduckgo_search"):
                raise ImportError(f"No module named '{name}'")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            result = await tool._search_ddgs("test query", 3)

        assert result.success is False
        assert "未安装" in result.error


class TestWebSearchFallback:
    """Bing → DDG fallback chain tests."""

    @pytest.fixture
    def tool(self):
        return WebSearchTool()

    @pytest.mark.asyncio
    async def test_bing_fails_ddg_succeeds(self, tool):
        """Bing timeout → DDG returns results → ToolResult(success=True)."""
        from config import settings
        settings.web_search_enabled = True

        bing_error = RetryableError("Bing 搜索超时")
        ddg_result = ToolResult(
            success=True,
            data={"count": 2, "results": [
                {"title": "DDG R1", "snippet": "S1", "url": "https://a.com"},
                {"title": "DDG R2", "snippet": "S2", "url": "https://b.com"},
            ]},
        )
        with (
            patch.object(tool, "_search_bing", AsyncMock(side_effect=bing_error)),
            patch.object(tool, "_search_ddgs", AsyncMock(return_value=ddg_result)),
        ):
            result = await tool.execute("test query")
            assert result.success is True
            assert result.data["count"] == 2
            assert result.data["results"][0]["title"] == "DDG R1"

    @pytest.mark.asyncio
    async def test_bing_and_ddg_both_fail(self, tool):
        """Bing returns empty, DDG raises → combined error message."""
        from config import settings
        settings.web_search_enabled = True

        bing_empty = ToolResult(success=True, data={"count": 0, "results": []})
        ddg_error = RuntimeError("DDG network unreachable")
        with (
            patch.object(tool, "_search_bing", AsyncMock(return_value=bing_empty)),
            patch.object(tool, "_search_ddgs", AsyncMock(side_effect=ddg_error)),
        ):
            result = await tool.execute("test query")
            assert result.success is False
            assert "Bing" in result.error
            assert "DDG" in result.error

    @pytest.mark.asyncio
    async def test_bing_retryable_ddg_retryable_raises_combined(self, tool):
        """Both Bing and DDG raise RetryableError → combined RetryableError raised."""
        from config import settings
        settings.web_search_enabled = True

        bing_err = RetryableError("Bing 搜索超时")
        ddg_err = RetryableError("DDG 搜索超时")
        with (
            patch.object(tool, "_search_bing", AsyncMock(side_effect=bing_err)),
            patch.object(tool, "_search_ddgs", AsyncMock(side_effect=ddg_err)),
            pytest.raises(RetryableError, match="Bing.*DDG"),
        ):
            await tool.execute("test query")


class TestWebSearchMain:
    @pytest.fixture
    def tool(self):
        return WebSearchTool()

    @pytest.mark.asyncio
    async def test_web_search_disabled(self, tool):
        """settings.web_search_enabled=False → ToolResult(success=False)."""
        from config import settings
        original = settings.web_search_enabled
        settings.web_search_enabled = False
        try:
            result = await tool.execute("test query")
            assert result.success is False
            assert "未启用" in result.error
        finally:
            settings.web_search_enabled = original

    @pytest.mark.asyncio
    async def test_bing_success_skips_ddg(self, tool):
        """When Bing returns results, DDG is not called."""
        from config import settings
        settings.web_search_enabled = True

        with patch.object(tool, "_search_bing") as mock_bing:
            mock_bing.return_value = ToolResult(
                success=True, data={"count": 3, "results": [
                    {"title": "R1", "snippet": "S1", "url": "http://a.com"},
                ]},
            )
            with patch.object(tool, "_search_ddgs") as mock_ddg:
                result = await tool.execute("test query")
                assert result.success is True
                assert result.data["count"] == 3
                mock_ddg.assert_not_called()


def _youcom_web_payload():
    import json

    return json.dumps({
        "results": {
            "web": [
                {
                    "title": "You R1",
                    "description": "D1",
                    "url": "https://you.example/1",
                },
                {
                    "title": "You R2",
                    "snippets": ["S2-first"],
                    "url": "https://you.example/2",
                },
            ],
        },
    })


class TestSearchYouCom:
    """_search_youcom with mocked HTTP — MCP JSON-RPC over httpx."""

    @pytest.fixture
    def tool(self):
        return WebSearchTool()

    @pytest.mark.asyncio
    async def test_youcom_success_parses_results(self, tool):
        """Plain-JSON initialize + tools/call responses → ToolResult(success=True)."""
        import json

        init_resp = MagicMock()
        init_resp.headers = {"content-type": "application/json", "mcp-session-id": "sess-1"}
        init_resp.text = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}})

        search_resp = MagicMock()
        search_resp.headers = {"content-type": "application/json"}
        search_resp.text = json.dumps({
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"content": [{"type": "text", "text": _youcom_web_payload()}]},
        })

        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=[init_resp, search_resp])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool._search_youcom("test query", 5)

        assert result.success is True
        assert result.data["count"] == 2
        assert result.data["results"][0]["title"] == "You R1"
        assert result.data["results"][0]["snippet"] == "D1"
        assert result.data["results"][1]["snippet"] == "S2-first"
        # Session id from the initialize response is passed on the call
        second_call = mock_client.post.await_args_list[1]
        assert second_call.kwargs["headers"]["Mcp-Session-Id"] == "sess-1"
        assert second_call.kwargs["json"]["params"]["name"] == "you-search"

    @pytest.mark.asyncio
    async def test_youcom_sse_response_parses_results(self, tool):
        """SSE tools/call response → last data: message parsed."""
        import json

        init_resp = MagicMock()
        init_resp.headers = {"content-type": "application/json"}
        init_resp.text = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}})

        search_resp = MagicMock()
        search_resp.headers = {"content-type": "text/event-stream"}
        message = json.dumps({
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"content": [{"type": "text", "text": _youcom_web_payload()}]},
        })
        search_resp.text = f"event: message\ndata: {message}\n\n"

        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=[init_resp, search_resp])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool._search_youcom("test query", 5)

        assert result.success is True
        assert result.data["count"] == 2

    @pytest.mark.asyncio
    async def test_youcom_timeout_raises_retryable(self, tool):
        """asyncio.TimeoutError → RetryableError."""
        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=TimeoutError("timed out"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            pytest.raises(RetryableError, match="You.com"),
        ):
            await tool._search_youcom("test query", 3)

    @pytest.mark.asyncio
    async def test_youcom_jsonrpc_error_returns_toolresult(self, tool):
        """JSON-RPC error in the tools/call response → ToolResult(success=False)."""
        import json

        init_resp = MagicMock()
        init_resp.headers = {"content-type": "application/json"}
        init_resp.text = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}})

        search_resp = MagicMock()
        search_resp.headers = {"content-type": "application/json"}
        search_resp.text = json.dumps({
            "jsonrpc": "2.0",
            "id": 2,
            "error": {"code": -32000, "message": "rate limited"},
        })

        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=[init_resp, search_resp])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool._search_youcom("test query", 3)

        assert result.success is False
        assert "rate limited" in result.error


class TestWebSearchYouComFallback:
    """Opt-in You.com source tests at the execute() level."""

    @pytest.fixture
    def tool(self):
        return WebSearchTool()

    @pytest.mark.asyncio
    async def test_youcom_success_skips_bing(self, tool):
        """You.com enabled and returns results → Bing/DDG not called."""
        from config import settings
        settings.web_search_enabled = True
        settings.web_search_youcom_enabled = True

        youcom_result = ToolResult(
            success=True,
            data={"count": 1, "results": [
                {"title": "You R1", "snippet": "D1", "url": "https://you.example/1"},
            ]},
        )
        with (
            patch.object(tool, "_search_youcom", AsyncMock(return_value=youcom_result)),
            patch.object(tool, "_search_bing") as mock_bing,
            patch.object(tool, "_search_ddgs") as mock_ddg,
        ):
            result = await tool.execute("test query")
            assert result.success is True
            assert result.data["results"][0]["title"] == "You R1"
            mock_bing.assert_not_called()
            mock_ddg.assert_not_called()

    @pytest.mark.asyncio
    async def test_youcom_failure_falls_back_to_bing(self, tool):
        """You.com returns empty → chain falls back to Bing."""
        from config import settings
        settings.web_search_enabled = True
        settings.web_search_youcom_enabled = True

        youcom_empty = ToolResult(success=True, data={"count": 0, "results": []})
        bing_result = ToolResult(
            success=True,
            data={"count": 2, "results": [
                {"title": "Bing R1", "snippet": "S1", "url": "https://a.com"},
                {"title": "Bing R2", "snippet": "S2", "url": "https://b.com"},
            ]},
        )
        with (
            patch.object(tool, "_search_youcom", AsyncMock(return_value=youcom_empty)),
            patch.object(tool, "_search_bing", AsyncMock(return_value=bing_result)),
            patch.object(tool, "_search_ddgs") as mock_ddg,
        ):
            result = await tool.execute("test query")
            assert result.success is True
            assert result.data["results"][0]["title"] == "Bing R1"
            mock_ddg.assert_not_called()

    @pytest.mark.asyncio
    async def test_youcom_disabled_bing_still_used(self, tool):
        """You.com disabled (default) → existing Bing→DDG behavior unchanged."""
        from config import settings
        settings.web_search_enabled = True
        settings.web_search_youcom_enabled = False

        bing_result = ToolResult(
            success=True,
            data={"count": 1, "results": [
                {"title": "Bing R1", "snippet": "S1", "url": "https://a.com"},
            ]},
        )
        with (
            patch.object(tool, "_search_youcom") as mock_youcom,
            patch.object(tool, "_search_bing", AsyncMock(return_value=bing_result)),
        ):
            result = await tool.execute("test query")
            assert result.success is True
            assert result.data["results"][0]["title"] == "Bing R1"
            mock_youcom.assert_not_called()
