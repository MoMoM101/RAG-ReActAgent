"""Web search tool tests — _search_bing and _search_ddgs with mocked HTTP."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.tools import RetryableError, ToolResult, WebSearchTool


class TestSearchBing:
    @pytest.fixture
    def tool(self):
        return WebSearchTool()

    @pytest.mark.asyncio
    async def test_bing_timeout_raises_retryable(self, tool):
        """asyncio.TimeoutError → RetryableError."""

        with patch("asyncio.wait_for", AsyncMock(side_effect=TimeoutError("timed out"))):
            with pytest.raises(RetryableError, match="Bing"):
                await tool._search_bing("test query", 3)

    @pytest.mark.asyncio
    async def test_bing_network_error_raises_retryable(self, tool):
        """httpx.ConnectError → RetryableError (caught by generic except)."""
        import httpx

        with patch("asyncio.wait_for", AsyncMock(
            side_effect=httpx.ConnectError("connection refused")
        )), pytest.raises(RetryableError, match="Bing"):
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

        with patch("asyncio.wait_for", AsyncMock(return_value=mock_response)):
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

        with patch("asyncio.wait_for", AsyncMock(return_value=mock_response)):
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

        with patch("asyncio.wait_for", AsyncMock(return_value=mock_response)):
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
