"""Tool registry for the ReAct agent loop."""

import ast
import asyncio
import logging
from dataclasses import dataclass
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)

from models.database import async_session
from models.orm import Document
from sqlalchemy import select


class RetryableError(Exception):
    """可重试的基础设施错误（网络超时、连接断开等）。
    ToolRegistry 捕获此异常触发指数退避重试。"""
    pass


def _is_retryable_exception(e: Exception) -> bool:
    """判断异常是否为可重试的基础设施错误。"""
    import asyncio

    if isinstance(e, asyncio.TimeoutError):
        return True
    if isinstance(e, (ConnectionError, TimeoutError)):
        return True

    try:
        from sqlalchemy import exc as sa_exc
        if isinstance(e, (sa_exc.OperationalError, sa_exc.InterfaceError,
                          sa_exc.TimeoutError, sa_exc.DBAPIError)):
            return True
    except ImportError:
        pass

    try:
        import httpx
        if isinstance(e, (httpx.TimeoutException, httpx.NetworkError,
                          httpx.ConnectError, httpx.RemoteProtocolError,
                          httpx.HTTPError)):
            return True
    except ImportError:
        pass

    module = type(e).__module__
    if "qdrant" in module.lower() or "grpc" in module.lower():
        return True

    return False


def _raise_if_retryable(e: Exception, tool_name: str) -> None:
    """如果是基础设施错误则 raise RetryableError，否则不做任何事。"""
    if _is_retryable_exception(e):
        raise RetryableError(f"[{tool_name}] {e}") from e


@dataclass
class ToolResult:
    success: bool
    data: Any = None
    error: str | None = None
    retries: int = 0


class BaseTool(ABC):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    max_retries: int = 3
    retry_backoff: float = 1.0
    retry_strategy: str = "exponential"  # "exponential" | "none"

    @abstractmethod
    async def execute(self, **kwargs: Any) -> ToolResult: ...

    def to_llm_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            }
        }


class SearchDocsTool(BaseTool):
    name = "search_docs"
    description = (
        "Search the knowledge base for relevant document chunks. "
        "Use when the user asks about any topic that might be in uploaded documents. "
        "If you already know which document to search in (from list_documents or get_document_info), "
        "pass its document_id to search within that document only."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "top_k": {"type": "integer", "description": "Number of results (optional, uses default if not specified)"},
            "document_id": {"type": "string", "description": "Optional: search within a specific document only"},
        },
        "required": ["query"],
    }

    async def execute(self, query: str, top_k: int = 0, document_id: str = "") -> ToolResult:
        from rag.retriever import hybrid_search
        from reranker.factory import is_reranker_ready
        from config import settings

        try:
            results = await hybrid_search(
                query,
                top_k=top_k or settings.retrieval_top_k,
                document_id=document_id,
                use_rerank=True,
            )
            # Look up filenames for document_ids
            doc_ids = list({r.document_id for r in results})
            filenames: dict[str, str] = {}
            if doc_ids:
                async with async_session() as session:
                    db_result = await session.execute(
                        select(Document.id, Document.filename).where(Document.id.in_(doc_ids))
                    )
                    filenames = {row[0]: row[1] for row in db_result.all()}

            return ToolResult(
                success=True,
                data={
                    "count": len(results),
                    "reranked": is_reranker_ready(),
                    "results": [
                        {
                            "text": r.text,
                            "document_id": r.document_id,
                            "filename": filenames.get(r.document_id, r.document_id[:8]),
                            "score": r.score,
                        }
                        for r in results
                    ],
                },
            )
        except Exception as e:
            _raise_if_retryable(e, "SearchDocs")
            return ToolResult(success=False, error=str(e))


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool):
        self._tools[tool.name] = tool

    def get_schemas(self) -> list[dict]:
        return [t.to_llm_schema() for t in self._tools.values()]

    async def execute(self, name: str, **kwargs) -> ToolResult:
        tool = self._tools[name]

        if tool.retry_strategy == "none":
            try:
                result = await tool.execute(**kwargs)
                result.retries = 0
                return result
            except RetryableError as e:
                return ToolResult(success=False, error=str(e), retries=0)
            except Exception as e:
                return ToolResult(success=False, error=str(e), retries=0)
        elif tool.retry_strategy == "exponential":
            from config import settings

            max_retries = min(tool.max_retries, settings.max_tool_retries)
            for attempt in range(max_retries + 1):
                try:
                    result = await tool.execute(**kwargs)
                    result.retries = attempt
                    return result
                except RetryableError as e:
                    if attempt == max_retries:
                        return ToolResult(
                            success=False, error=str(e), retries=attempt
                        )
                    await asyncio.sleep(tool.retry_backoff * (2 ** attempt))
                except Exception as e:
                    return ToolResult(
                        success=False, error=str(e), retries=attempt
                    )
        else:
            raise ValueError(f"Unknown retry_strategy: {tool.retry_strategy}")
        # Satisfy mypy: all paths above return or raise
        return ToolResult(success=False, error="unreachable")


class CalculatorTool(BaseTool):
    name = "calculator"
    description = (
        "Evaluate a mathematical expression. "
        "Use for arithmetic calculations like addition, subtraction, "
        "multiplication, and division."
    )
    parameters = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "Mathematical expression with numbers and +-*/() operators only",
            },
        },
        "required": ["expression"],
    }
    max_retries = 0
    retry_strategy = "none"

    async def execute(self, expression: str) -> ToolResult:
        try:
            tree = ast.parse(expression.strip(), mode="eval")
        except SyntaxError as e:
            return ToolResult(success=False, error=f"表达式语法错误: {e.msg}")

        if not self._is_allowed(tree.body):
            return ToolResult(success=False, error="不允许的操作: 表达式只允许数字和 + - * / ( ) 运算符")

        try:
            value = self._eval_node(tree.body)
            return ToolResult(success=True, data={"expression": expression, "result": value})
        except ZeroDivisionError:
            return ToolResult(success=False, error="除数不能为零")
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    def _is_allowed(self, node):
        if isinstance(node, ast.Constant):
            return isinstance(node.value, (int, float))
        if isinstance(node, ast.UnaryOp):
            return isinstance(node.op, ast.USub) and self._is_allowed(node.operand)
        if isinstance(node, ast.BinOp):
            return (
                isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div))
                and self._is_allowed(node.left)
                and self._is_allowed(node.right)
            )
        return False

    def _eval_node(self, node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.UnaryOp):
            return -self._eval_node(node.operand)
        if isinstance(node, ast.BinOp):
            left = self._eval_node(node.left)
            right = self._eval_node(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            elif isinstance(node.op, ast.Sub):
                return left - right
            elif isinstance(node.op, ast.Mult):
                return left * right
            elif isinstance(node.op, ast.Div):
                return left / right
        raise ValueError(f"Unexpected node: {type(node)}")


class ListDocumentsTool(BaseTool):
    name = "list_documents"
    description = (
        "List all documents in the knowledge base. "
        "Use when the user asks what documents are available."
    )
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
    }
    max_retries = 2
    retry_strategy = "exponential"

    async def execute(self) -> ToolResult:
        try:
            async with async_session() as session:
                result = await session.execute(
                    select(Document).order_by(Document.created_at.desc())
                )
                docs = result.scalars().all()
                return ToolResult(
                    success=True,
                    data={
                        "count": len(docs),
                        "documents": [
                            {
                                "id": d.id,
                                "filename": d.filename,
                                "file_type": d.file_type,
                                "status": d.status.value,
                                "created_at": d.created_at.isoformat(),
                            }
                            for d in docs
                        ],
                    },
                )
        except Exception as e:
            _raise_if_retryable(e, "ListDocuments")
            return ToolResult(success=False, error=str(e))


class GetDocumentInfoTool(BaseTool):
    name = "get_document_info"
    description = (
        "Get detailed information about a specific document by its ID. "
        "Use when the user asks about a specific document's details, "
        "such as how many chunks it has or when it was uploaded."
    )
    parameters = {
        "type": "object",
        "properties": {
            "document_id": {
                "type": "string",
                "description": "The document ID to look up",
            },
        },
        "required": ["document_id"],
    }
    max_retries = 2
    retry_strategy = "exponential"

    async def execute(self, document_id: str) -> ToolResult:
        try:
            async with async_session() as session:
                result = await session.execute(
                    select(Document).where(Document.id == document_id)
                )
                doc = result.scalar_one_or_none()
                if not doc:
                    return ToolResult(
                        success=False, error=f"未找到文档: {document_id}"
                    )
                return ToolResult(
                    success=True,
                    data={
                        "id": doc.id,
                        "filename": doc.filename,
                        "file_type": doc.file_type,
                        "file_size": doc.file_size,
                        "status": doc.status.value,
                        "chunk_count": doc.chunk_count,
                        "embedding_model": doc.embedding_model,
                        "embedding_dim": doc.embedding_dim,
                        "error_message": doc.error_message,
                        "created_at": doc.created_at.isoformat(),
                        "updated_at": doc.updated_at.isoformat(),
                    },
                )
        except Exception as e:
            _raise_if_retryable(e, "GetDocumentInfo")
            return ToolResult(success=False, error=str(e))


class WebSearchTool(BaseTool):
    name = "web_search"
    description = (
        "Search the internet for information. "
        "Use when the knowledge base does not have enough information to answer the user's question."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "max_results": {"type": "integer", "description": "Max results (optional, uses config default if not specified)"},
        },
        "required": ["query"],
    }
    max_retries = 2
    retry_strategy = "exponential"

    async def execute(self, query: str, max_results: int = 0) -> ToolResult:
        from config import settings

        if not settings.web_search_enabled:
            return ToolResult(success=False, error="联网搜索未启用")

        max_r = max_results or settings.web_search_max_results
        bing_retryable = False
        bing_error = ""

        # Primary: Bing (fast from China ~500ms)
        try:
            result = await self._search_bing(query, max_r)
        except RetryableError as e:
            bing_retryable = True
            bing_error = str(e)
        except Exception as e:
            bing_error = str(e)[:200]
        else:
            if result.success and result.data and result.data.get("count", 0) > 0:
                return result
            bing_error = result.error or "Bing 返回 0 条结果"

        # Fallback: DuckDuckGo via ddgs library
        try:
            ddg_result = await self._search_ddgs(query, max_r)
        except RetryableError as e:
            if bing_retryable:
                raise RetryableError(
                    f"Bing: {bing_error}; DDG: {e}"
                ) from e
            raise
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Bing: {bing_error}; DDG: {str(e)[:200]}",
            )

        if ddg_result.success:
            return ddg_result

        return ToolResult(
            success=False,
            error=f"Bing: {bing_error}; DDG: {ddg_result.error}",
        )

    async def _search_bing(self, query: str, max_results: int) -> ToolResult:
        """Search via cn.bing.com — fast and accessible from China."""
        import httpx
        from bs4 import BeautifulSoup

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(10, connect=5),
                follow_redirects=True,
            ) as client:
                resp = await asyncio.wait_for(
                    client.get(
                        "https://cn.bing.com/search",
                        params={"q": query, "count": max_results},
                        headers={
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Safari/537.36",
                            "Accept-Language": "zh-CN,zh;q=0.9",
                        },
                    ),
                    timeout=12,
                )
        except asyncio.TimeoutError:
            raise RetryableError("Bing 搜索超时")
        except Exception as e:
            raise RetryableError(f"Bing 网络错误: {e}") from e

        if resp.status_code != 200:
            logger.warning("Bing HTTP %s, query=%r", resp.status_code, query)
            return ToolResult(success=False, error=f"Bing HTTP {resp.status_code}")

        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        # Multiple selector chains — Bing may change CSS classes
        selectors = [
            ("li.b_algo", "h2 a", ".b_caption p"),
            ("li.b_algo", "h2 a", ".b_lineclamp2"),
            ("ol#b_results li.b_algo", "h2 a", "div.b_caption p"),
        ]
        tried = 0
        for item_sel, link_sel, snippet_sel in selectors:
            items = soup.select(item_sel)
            if items:
                tried += 1
                for li in items:
                    a = li.select_one(link_sel)
                    if not a:
                        continue
                    title = a.get_text(strip=True)
                    url = a.get("href", "")
                    snippet_el = li.select_one(snippet_sel)
                    snippet = snippet_el.get_text(strip=True) if snippet_el else ""
                    if title and url:
                        results.append({"title": title, "snippet": snippet, "url": url})
                if results:
                    break

        if not results:
            logger.warning(
                "Bing HTML parsing returned 0 results, query=%r, "
                "selectors_tried=%d, html_snippet=%s",
                query, tried, soup.get_text()[:200],
            )

        return ToolResult(
            success=True,
            data={"count": len(results), "results": results[:max_results]},
        )

    async def _search_ddgs(self, query: str, max_results: int) -> ToolResult:
        """Fallback: DuckDuckGo via ddgs library."""
        from config import settings

        DDGS = None
        for module_name in ("ddgs", "duckduckgo_search"):
            try:
                mod = __import__(module_name, fromlist=["DDGS"])
                DDGS = getattr(mod, "DDGS")
                break
            except ImportError:
                continue
        if DDGS is None:
            return ToolResult(success=False, error="ddgs 未安装")

        proxy = settings.web_search_proxy or None

        def _do_search():
            ddgs = DDGS(proxies=proxy) if proxy else DDGS()
            results = []
            with ddgs:
                for r in ddgs.text(query, max_results=max_results):
                    results.append({
                        "title": r.get("title", ""),
                        "snippet": r.get("body", ""),
                        "url": r.get("href", ""),
                    })
            return results

        try:
            results = await asyncio.wait_for(
                asyncio.to_thread(_do_search), timeout=10
            )
            return ToolResult(
                success=True,
                data={"count": len(results), "results": results},
            )
        except asyncio.TimeoutError:
            raise RetryableError("DDG 搜索超时")
        except Exception as e:
            _raise_if_retryable(e, "WebSearch-DDG")
            return ToolResult(success=False, error=str(e)[:200])


class RecallMemoryTool(BaseTool):
    name = "recall_memory"
    description = (
        "Search the user's long-term memory for previously saved facts, preferences, and decisions. "
        "Use when the user asks about something they mentioned before (e.g. '我之前说过...', '你还记得...')."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query for memory"},
            "top_k": {"type": "integer", "description": "Number of memories to recall (optional)"},
        },
        "required": ["query"],
    }
    max_retries = 1
    retry_strategy = "exponential"

    async def execute(self, query: str, top_k: int = 0) -> ToolResult:
        try:
            from memory.profile import search_profile
            results = await search_profile(query, top_k=top_k or 5)
            return ToolResult(
                success=True,
                data={
                    "count": len(results),
                    "results": [{"text": r["text"], "score": r["score"]} for r in results],
                },
            )
        except Exception as e:
            _raise_if_retryable(e, "RecallMemory")
            return ToolResult(success=False, error=str(e))


# Global registry
registry = ToolRegistry()
registry.register(SearchDocsTool())
registry.register(CalculatorTool())
registry.register(ListDocumentsTool())
registry.register(GetDocumentInfoTool())
registry.register(WebSearchTool())
registry.register(RecallMemoryTool())
