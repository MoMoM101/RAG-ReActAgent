# Phase 1.3 — Embedding/Qdrant 实例复用设计

**日期**: 2026-07-01 | **优先级**: 高 | **预计改动范围**: 4 文件

---

## 问题描述

当前 embedding 和 Qdrant 实例在每次操作时重新创建：

**Embedding** (`embedding/factory.py`):
```python
def create_embedding():
    # 每次调用都新建 OpenAIEmbedding 实例
    return OpenAIEmbedding(
        model=settings.embedding_model,
        api_key=settings.embedding_api_key,
        base_url=settings.embedding_base_url,
    )
```

**Qdrant** (`vectordb/qdrant.py`):
```python
class QdrantVectorDB:
    def __init__(self, collection_name=...):
        # 每次实例化都创建新的 QdrantClient
        self.client = QdrantClient(...)
```

**LLM** (`llm/factory.py`):
```python
def create_llm():
    # 每次调用都新建 OpenAILLM 实例
    return OpenAILLM(...)
```

虽然 httpx/httpx 内部有连接池，但 OpenAI client 和 QdrantClient 的重复创建仍有开销。更关键的是 embedding 调用（`loop.py:52`、`profile.py:47`、`retriever.py:169`、`search_profile`）每次都可能创建新实例。

---

## 设计方案

### 核心思路：模块级单例缓存

在每个 factory 模块中使用 `_instance` 变量缓存实例，后续调用返回缓存值。

### 改动 1：Embedding 单例

`embedding/factory.py`:

```python
_embedding_instance = None

def create_embedding():
    global _embedding_instance
    if _embedding_instance is None:
        _embedding_instance = _build_embedding()
    return _embedding_instance

def _build_embedding():
    # 原有的 create_embedding 逻辑
    ...

def reset_embedding():
    """仅测试用：重置单例"""
    global _embedding_instance
    _embedding_instance = None
```

### 改动 2：LLM 单例

`llm/factory.py`:

```python
_llm_instance = None

def create_llm():
    global _llm_instance
    if _llm_instance is None:
        _llm_instance = _build_llm()
    return _llm_instance

def reset_llm():
    """仅测试用"""
    global _llm_instance
    _llm_instance = None
```

### 改动 3：Qdrant 连接复用

`vectordb/qdrant.py` 中为常用 collection 缓存 client：

```python
_clients: dict[str, QdrantVectorDB] = {}

async def get_qdrant(collection_name: str = "rag_chunks") -> QdrantVectorDB:
    if collection_name not in _clients:
        db = QdrantVectorDB(collection_name=collection_name)
        _clients[collection_name] = db
    return _clients[collection_name]

def reset_qdrant(collection_name: str | None = None):
    """仅测试用"""
    global _clients
    if collection_name:
        _clients.pop(collection_name, None)
    else:
        _clients.clear()
```

### 不改的部分

- Reranker (Cross-Encoder) 模型本身已由 HuggingFace 缓存，不需要额外处理
- OCR (PaddleOCR) 模型加载策略不变（首次加载慢，后续由 PaddleOCR 自身管理）
- 各 factory 的 `reset_*` 函数仅在测试中调用，生产代码不调用

### 线程安全考量

当前项目使用 `async` 单线程模型 (FastAPI + asyncio)，不存在多线程竞争。全局变量在 asyncio 中是安全的。如果将来引入多 worker，再考虑加锁或使用 `contextvars`。

---

## 影响范围

| 文件 | 改动 |
|------|------|
| `backend/embedding/factory.py` | 添加模块级单例 + reset 函数 |
| `backend/llm/factory.py` | 添加模块级单例 + reset 函数 |
| `backend/vectordb/qdrant.py` | 添加按 collection 缓存的 get_qdrant |
| `backend/vectordb/factory.py` | 改为调用 get_qdrant |

### 调用方适配

需要将 `QdrantVectorDB(collection_name=...)` 的直接实例化替换为 `await get_qdrant(collection_name)`。涉及文件：

| 文件 | 当前调用方式 | 改动 |
|------|-------------|------|
| `profile.py:88` | `QdrantVectorDB(collection_name=PROFILE_COLLECTION)` | → `await get_qdrant(PROFILE_COLLECTION)` |
| `profile.py:207` | `QdrantVectorDB(collection_name=PROFILE_COLLECTION)` | → `await get_qdrant(PROFILE_COLLECTION)` |
| `retriever.py:165` | `await create_vectordb()` | 无需改动（走 factory） |

API 无变更，前端无变更。

---

## 验证方式

1. 在 `create_embedding()` 和 `create_llm()` 中添加 `print` 语句，验证仅首次调用时创建实例
2. 发送两条连续聊天消息，确认第二条复用已创建的实例
3. 运行现有测试：`pytest backend/tests/ -v`（确保 reset 函数在测试 teardown 中调用）
4. 测试中需要确保每个 test 前后重置单例，避免测试间状态污染
