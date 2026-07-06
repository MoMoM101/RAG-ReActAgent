# Agent 工具化 二期 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 calculator、list_documents、get_document_info 三个 Agent 工具，支持可配置重试策略（指数退避 / 不重试），扩展 intent classifier 关键词覆盖。

**Architecture:** 在现有 `BaseTool` + `ToolRegistry` 模式上增量添加。三个新工具均继承 `BaseTool`，注册到全局 `ToolRegistry`。系统 prompt 和工具列表已动态拼接，无需额外改动。

**Tech Stack:** Python 3.12+, ast (stdlib), SQLAlchemy async, pytest-asyncio

---

## 文件映射

| 文件 | 职责 | 改动类型 |
|------|------|----------|
| `backend/agent/tools.py` | BaseTool + retry_strategy, CalculatorTool, ListDocumentsTool, GetDocumentInfoTool, ToolRegistry retry 逻辑 | 修改 |
| `backend/agent/classifier.py` | 新工具关键词识别 | 修改 |
| `backend/tests/agent/test_tools.py` | 三个新工具的单元测试 + retry 策略测试 | 新建 |

---

### Task 1: BaseTool 增加 retry_strategy 并更新 ToolRegistry 重试逻辑

**Files:**
- Modify: `backend/agent/tools.py`

- [ ] **Step 1: 给 BaseTool 添加 retry_strategy 字段**

修改 `backend/agent/tools.py`，在 `BaseTool` 类中增加 `retry_strategy` 属性：

```python
class BaseTool(ABC):
    name: str
    description: str
    parameters: dict  # JSON Schema
    max_retries: int = 3
    retry_backoff: float = 1.0
    retry_strategy: str = "exponential"  # "exponential" | "none"

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult: ...

    def to_llm_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            }
        }
```

- [ ] **Step 2: 更新 ToolRegistry.execute() 读取 retry_strategy**

修改 `ToolRegistry.execute()` 方法，在重试循环前检查策略：

```python
class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool):
        self._tools[tool.name] = tool

    def get_schemas(self) -> list[dict]:
        return [t.to_llm_schema() for t in self._tools.values()]

    async def execute(self, name: str, **kwargs) -> ToolResult:
        from config import settings

        tool = self._tools[name]
        if tool.retry_strategy == "none":
            try:
                result = await tool.execute(**kwargs)
                result.retries = 0
                return result
            except Exception as e:
                return ToolResult(success=False, error=str(e), retries=0)

        max_retries = min(tool.max_retries, settings.max_tool_retries)
        for attempt in range(max_retries + 1):
            try:
                result = await tool.execute(**kwargs)
                result.retries = attempt
                return result
            except Exception as e:
                if attempt == max_retries:
                    return ToolResult(success=False, error=str(e), retries=attempt)
                await asyncio.sleep(tool.retry_backoff * (2 ** attempt))
        return ToolResult(success=False, error="max retries exceeded")
```

---

### Task 2: 实现 CalculatorTool

**Files:**
- Modify: `backend/agent/tools.py`
- Create: `backend/tests/agent/test_tools.py`

- [ ] **Step 1: 编写 CalculatorTool 测试**

创建 `backend/tests/agent/test_tools.py`：

```python
import pytest
from agent.tools import CalculatorTool, ToolResult


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
```

- [ ] **Step 2: 运行测试确认全部失败**

```bash
cd D:/Python/subject1/RAG_Agent/backend && python -m pytest tests/agent/test_tools.py -v
```
Expected: 全部 8 个测试 FAIL（模块尚未创建 / 类未定义）

- [ ] **Step 3: 实现 CalculatorTool**

在 `backend/agent/tools.py` 文件末尾、`# Global registry` 之前添加：

```python
import ast
import operator


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

    _ALLOWED_NODES = (ast.Expression, ast.Constant, ast.UnaryOp, ast.BinOp)

    _OPERATORS = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.USub: operator.neg,
    }

    async def execute(self, expression: str) -> ToolResult:
        try:
            tree = ast.parse(expression.strip(), mode="eval")
        except SyntaxError as e:
            return ToolResult(success=False, error=f"表达式语法错误: {e.msg}")

        if not self._is_allowed(tree.body):
            return ToolResult(success=False, error="表达式只允许数字和 + - * / ( ) 运算符")

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
        if isinstance(node, ast.Expression):
            return self._eval_node(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.UnaryOp):
            return self._OPERATORS[type(node.op)](self._eval_node(node.operand))
        if isinstance(node, ast.BinOp):
            return self._OPERATORS[type(node.op)](
                self._eval_node(node.left), self._eval_node(node.right)
            )
        raise ValueError(f"Unexpected node: {type(node)}")
```

- [ ] **Step 4: 注册 CalculatorTool**

在 `backend/agent/tools.py` 全局 registry 部分，`registry.register(SearchDocsTool())` 之后添加：

```python
registry.register(CalculatorTool())
```

- [ ] **Step 5: 运行测试确认全部通过**

```bash
cd D:/Python/subject1/RAG_Agent/backend && python -m pytest tests/agent/test_tools.py::TestCalculatorTool -v
```
Expected: 全部 PASS

---

### Task 3: 实现 ListDocumentsTool

**Files:**
- Modify: `backend/agent/tools.py`
- Modify: `backend/tests/agent/test_tools.py`

- [ ] **Step 1: 编写 ListDocumentsTool 测试**

在 `backend/tests/agent/test_tools.py` 文件末尾追加：

```python
from models.database import async_session
from models.schemas import Document, DocStatus
from datetime import datetime, timezone


class TestListDocumentsTool:
    @pytest.fixture
    def tool(self):
        from agent.tools import ListDocumentsTool
        return ListDocumentsTool()

    @pytest.mark.asyncio
    async def test_lists_documents(self, tool):
        # Seed test data
        async with async_session() as session:
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

    @pytest.mark.asyncio
    async def test_empty_returns_empty_list(self, tool):
        result = await tool.execute()
        assert result.success is True
        assert result.data["documents"] == []

    @pytest.mark.asyncio
    async def test_retry_strategy_is_exponential(self, tool):
        assert tool.retry_strategy == "exponential"
        assert tool.max_retries == 2
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd D:/Python/subject1/RAG_Agent/backend && python -m pytest tests/agent/test_tools.py::TestListDocumentsTool -v
```
Expected: FAIL（`ListDocumentsTool` 未定义）

- [ ] **Step 3: 实现 ListDocumentsTool**

在 `backend/agent/tools.py` 中 `CalculatorTool` 之后添加：

```python
from models.database import async_session
from models.schemas import Document
from sqlalchemy import select


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
            return ToolResult(success=False, error=str(e))
```

- [ ] **Step 4: 注册 ListDocumentsTool**

在全局 registry 部分添加：

```python
registry.register(ListDocumentsTool())
```

- [ ] **Step 5: 运行测试确认通过**

```bash
cd D:/Python/subject1/RAG_Agent/backend && python -m pytest tests/agent/test_tools.py::TestListDocumentsTool -v
```
Expected: 全部 PASS

---

### Task 4: 实现 GetDocumentInfoTool

**Files:**
- Modify: `backend/agent/tools.py`
- Modify: `backend/tests/agent/test_tools.py`

- [ ] **Step 1: 编写 GetDocumentInfoTool 测试**

在 `backend/tests/agent/test_tools.py` 文件末尾追加：

```python
class TestGetDocumentInfoTool:
    @pytest.fixture
    def tool(self):
        from agent.tools import GetDocumentInfoTool
        return GetDocumentInfoTool()

    @pytest.mark.asyncio
    async def test_returns_document_info(self, tool):
        # Seed a document
        async with async_session() as session:
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

    @pytest.mark.asyncio
    async def test_not_found(self, tool):
        result = await tool.execute(document_id="nonexistent")
        assert result.success is False
        assert "未找到" in result.error or "not found" in result.error.lower()

    @pytest.mark.asyncio
    async def test_retry_strategy_is_exponential(self, tool):
        assert tool.retry_strategy == "exponential"
        assert tool.max_retries == 2
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd D:/Python/subject1/RAG_Agent/backend && python -m pytest tests/agent/test_tools.py::TestGetDocumentInfoTool -v
```
Expected: FAIL

- [ ] **Step 3: 实现 GetDocumentInfoTool**

在 `backend/agent/tools.py` 中 `ListDocumentsTool` 之后添加：

```python
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
            return ToolResult(success=False, error=str(e))
```

- [ ] **Step 4: 注册 GetDocumentInfoTool**

在全局 registry 部分添加：

```python
registry.register(GetDocumentInfoTool())
```

- [ ] **Step 5: 运行测试确认通过**

```bash
cd D:/Python/subject1/RAG_Agent/backend && python -m pytest tests/agent/test_tools.py::TestGetDocumentInfoTool -v
```
Expected: 全部 PASS

---

### Task 5: 扩展 Intent Classifier 关键词识别

**Files:**
- Modify: `backend/agent/classifier.py`

- [ ] **Step 1: 添加 calculator 和文档管理工具的关键词**

修改 `backend/agent/classifier.py` 的 `classify_intent` 函数，在关键词匹配部分添加新工具的关键词组：

在 `retrieval_keywords` 定义之后、return 之前，插入以下逻辑：

```python
    # Calculator keywords
    calc_keywords = {"计算", "算", "等于", "加", "减", "乘", "除", "多少", "+", "-", "*", "/"}
    if any(kw in query for kw in calc_keywords):
        # Only trigger calculator intent if query looks like math
        # (contains digits and operators, not just a general question with "多少")
        import re
        if re.search(r"[\d+\-*/]", query):
            return IntentHint(
                intent="calculation",
                confidence=0.7,
                suggested_tools=["calculator"],
                hint_text="用户可能在询问数学计算，建议使用 calculator 进行计算",
            )

    # Document listing keywords
    list_keywords = {"有哪些文档", "文档列表", "所有文档", "什么文档", "哪些文件", "文件列表", "列出文档"}
    if any(kw in query for kw in list_keywords):
        return IntentHint(
            intent="document_listing",
            confidence=0.7,
            suggested_tools=["list_documents"],
            hint_text="用户想查看知识库中的文档列表，建议使用 list_documents",
        )

    # Document info keywords
    info_keywords = {"文档详情", "文档信息", "多少个切片", "切片数", "什么时候上传"}
    if any(kw in query for kw in info_keywords):
        return IntentHint(
            intent="document_info",
            confidence=0.6,
            suggested_tools=["get_document_info"],
            hint_text="用户想查看某个文档的详细信息，先调用 list_documents 获取文档ID，再用 get_document_info 查详情",
        )
```

- [ ] **Step 2: 验证导入正确**

```bash
cd D:/Python/subject1/RAG_Agent/backend && python -c "from agent.classifier import classify_intent; print(classify_intent('计算 1+2'))"
```
Expected: 打印出 `IntentHint(intent='calculation', ...suggested_tools=['calculator']...)`

---

### Task 6: 全量回归验证

**Files:**
- 全部

- [ ] **Step 1: 运行全部工具单元测试**

```bash
cd D:/Python/subject1/RAG_Agent/backend && python -m pytest tests/agent/test_tools.py -v
```
Expected: 全部 PASS（约 14 个测试）

- [ ] **Step 2: 运行现有回归测试**

```bash
cd D:/Python/subject1/RAG_Agent/backend && python -m pytest tests/ -v --timeout=30
```
Expected: 所有已有测试仍 PASS

- [ ] **Step 3: 验证 Agent 工具 Schema 完整性**

```bash
cd D:/Python/subject1/RAG_Agent/backend && python -c "
from agent.tools import registry
schemas = registry.get_schemas()
names = [s['function']['name'] for s in schemas]
print('Registered tools:', names)
assert 'search_docs' in names
assert 'calculator' in names
assert 'list_documents' in names
assert 'get_document_info' in names
print('All 4 tools registered OK')
"
```
Expected: 打印 "All 4 tools registered OK"

- [ ] **Step 4: 验证 Agent Loop 导入正常**

```bash
cd D:/Python/subject1/RAG_Agent/backend && python -c "from agent.loop import run_agent_loop; from agent.tools import registry; print('Agent loop + registry OK')"
```
Expected: 打印 "Agent loop + registry OK"
