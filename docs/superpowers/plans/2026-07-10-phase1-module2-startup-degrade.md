# Phase 1 Module 1.2: 启动流程降级设计

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 后端服务启动不强依赖外部 LLM / embedding API。无 key 时服务仍可启动，基础 API 可用。

**Architecture:** 将 main.py lifespan 中的 eager embedding 维度检测改为懒加载——启动时只读配置值，首次 embed 调用时才检测真实维度。新增 `/api/health/dependencies` 端点报告各组件状态。修改 start 确保服务不因缺 key 而崩溃。

**Tech Stack:** FastAPI, pytest, httpx, AsyncOpenAI

---

### Task 1: 写测试——启动降级 + 懒加载维度检测 + health/dependencies（TDD 红）

**Files:**
- Create: `backend/tests/test_startup_degraded.py`

- [ ] **Step 1: 写测试**

```python
"""Test that the service starts and reports status without API keys."""
import os
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


# ── Test: /api/health works without embedding API ──

@pytest.mark.asyncio
async def test_health_works_without_api_keys():
    """Service should start and /api/health should return 200 without API keys."""
    import embedding.openai_embed

    with patch.object(embedding.openai_embed.AsyncOpenAI, "__init__", return_value=None):
        with patch.object(embedding.openai_embed.OpenAIEmbedding, "embed", AsyncMock(
            return_value=[[0.1] * 1536]
        )):
            from main import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/api/health")
                assert response.status_code == 200
                assert response.json()["status"] == "ok"


# ── Tests: /api/health/dependencies ──

@pytest.mark.asyncio
async def test_health_dependencies_returns_all_components():
    """/api/health/dependencies should return status for each dependency."""
    import embedding.openai_embed

    with patch.object(embedding.openai_embed.AsyncOpenAI, "__init__", return_value=None):
        with patch.object(embedding.openai_embed.OpenAIEmbedding, "embed", AsyncMock(
            return_value=[[0.1] * 1536]
        )):
            from main import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/api/health/dependencies")
                assert response.status_code == 200
                data = response.json()
                for key in ("sqlite", "qdrant", "embedding", "llm"):
                    assert key in data, f"Missing key: {key}"


@pytest.mark.asyncio
async def test_health_dependencies_detects_missing_api_key():
    """When API key is empty, embedding and llm should report missing_api_key."""
    from config import settings

    # Save originals
    orig_llm_key = settings.llm_api_key
    orig_emb_key = settings.embedding_api_key
    settings.llm_api_key = ""
    settings.embedding_api_key = ""
    try:
        import embedding.openai_embed
        with patch.object(embedding.openai_embed.AsyncOpenAI, "__init__", return_value=None):
            with patch.object(embedding.openai_embed.OpenAIEmbedding, "embed", AsyncMock(
                return_value=[[0.1] * 1536]
            )):
                from main import app
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.get("/api/health/dependencies")
                    assert response.status_code == 200
                    data = response.json()
                    assert data["embedding"] == "missing_api_key"
                    assert data["llm"] == "missing_api_key"
    finally:
        settings.llm_api_key = orig_llm_key
        settings.embedding_api_key = orig_emb_key


# ── Test: Lazy dimension detection ──

@pytest.mark.asyncio
async def test_lazy_dimension_detection_on_first_embed():
    """First embed call should detect and cache the dimension."""
    from config import settings

    from embedding.factory import create_embedding

    # Reset embedding singleton to trigger fresh init
    from embedding.factory import reset_embedding
    reset_embedding()

    try:
        embedding = create_embedding()
        # Embed something — this should detect dim from API response
        vec = await embedding.embed_query("test dimension check")
        assert len(vec) > 0
        # After first call, settings.embedding_dim should be updated
        assert settings.embedding_dim == len(vec)
    except Exception:
        pytest.skip("API key not available, skipping integration test")
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd backend && python -m pytest tests/test_startup_degraded.py -v 2>&1
```
Expected: `test_health_dependencies_*` FAIL (endpoint doesn't exist yet, 404)

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_startup_degraded.py
git commit -m "test: add startup degraded mode and health/dependencies tests (red)"
```

---

### Task 2: 移除 main.py lifespan 中的 eager embedding 检测（TDD 绿 step 1）

**Files:**
- Modify: `backend/main.py` (lifespan 中去掉 embedding 维度检测)

- [ ] **Step 1: 修改 lifespan**

In `backend/main.py`, remove lines 68-79 and 81-87 (the embedding dimension detection block):

```python
# REMOVE these lines from lifespan:
#     # Auto-detect actual embedding dimension from API
#     from embedding.factory import create_embedding
#     emb = create_embedding()
#     test_vec = await emb.embed_query("dim check")
#     detected_dim = len(test_vec)
#     settings.embedding_dim = detected_dim
#     logger.info("embedding dim detected: %d", detected_dim)
#     # Warn if existing Qdrant collection dimension doesn't match
#     try:
#         from vectordb.qdrant import QdrantVectorDB
#         chunks_db = QdrantVectorDB()
#         if await chunks_db.collection_exists():
#             col_dim = await chunks_db.get_collection_dim()
#             if col_dim is not None and col_dim != detected_dim:
#                 logger.warning(
#                     "embedding dim mismatch: API=%d Qdrant=%d. Use /api/settings/rebuild-collections to migrate",
#                     detected_dim, col_dim,
#                 )
#     except Exception:
#         logger.warning("Qdrant dimension check skipped, service unavailable", exc_info=True)
```

Keep the rest of the lifespan intact. After the change, the lifespan block containing dimension detection should be replaced with just a log statement:

```python
    logger.info("embedding dim (from config): %d", settings.embedding_dim)
```

- [ ] **Step 2: 按 Patch 方式修改 main.py 的 lifespan 函数**

The lifespan function currently has these steps in order:
1. setup_logging() and mkdir
2. init_db()
3. Restore active collection pointers
4. **Eager embedding dim detection + mismatch check (REMOVE)**
5. Clean up stuck documents
6. Preload reranker + OCR
7. Rebuild profile index

Step 4 must be replaced with a simple log line.

- [ ] **Step 3: Commit** (after Step 4 below — we need the /api/health/dependencies endpoint too)

---

### Task 3: 添加 /api/health/dependencies 端点 + 在主 health router 注册

**Files:**
- Modify: `backend/main.py` (add health dependencies router)

- [ ] **Step 1: 在 main.py 中添加 `/api/health/dependencies` 端点**

Add a new endpoint directly in `main.py` after the existing `/api/health` route:

```python
@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/health/dependencies")
async def health_dependencies():
    """Report health status of each dependency without blocking startup."""
    deps = {
        "sqlite": "ok",
        "qdrant": "ok",
        "embedding": "ok",
        "llm": "ok",
    }

    # Check SQLite
    try:
        from sqlalchemy import text as sa_text
        from models.database import engine
        async with engine.begin() as conn:
            await conn.execute(sa_text("SELECT 1"))
    except Exception:
        deps["sqlite"] = "error"

    # Check Qdrant
    try:
        from vectordb.qdrant import QdrantVectorDB
        vdb = QdrantVectorDB()
        await vdb.collection_exists()
    except Exception:
        deps["qdrant"] = "error"

    # Check embedding (just check if key is configured)
    if not settings.embedding_api_key and not settings.llm_api_key:
        deps["embedding"] = "missing_api_key"

    # Check LLM (just check if key is configured)
    if not settings.llm_api_key:
        deps["llm"] = "missing_api_key"

    # Aggregate status
    has_error = any(v == "error" for v in deps.values())
    has_missing = any(v == "missing_api_key" for v in deps.values())

    if has_error:
        status = "error"
    elif has_missing:
        status = "degraded"
    else:
        status = "ok"

    return {"status": status, "dependencies": deps}
```

- [ ] **Step 2: Run tests**

```bash
cd backend && python -m pytest tests/test_startup_degraded.py -v 2>&1
```
Expected: All tests PASS (no more 404, endpoint now exists)

- [ ] **Step 3: Run full test suite**

```bash
cd backend && python -m pytest tests/ -v 2>&1 | tail -15
```
Expected: All pass, no regressions.

- [ ] **Step 4: Commit**

```bash
git add backend/main.py backend/tests/test_startup_degraded.py
git commit -m "feat: defer embedding dim detection to lazy, add /api/health/dependencies"
```
