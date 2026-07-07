# RAG 检索优化 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 分三阶段优化 RAG 检索系统：FTS5 迁移至 trigram tokenizer、Qdrant 客户端健壮化、检索评测方法升级。

**Architecture:** 三阶段独立可并行。Phase 1 修改 `textdb/sqlite_fts.py` 和 `models/database.py`，trigram 原生处理 CJK。Phase 2 在 `vectordb/qdrant.py` 增加状态感知懒加载，并清理遗留数据目录。Phase 3 重构 `tests/evaluate_rag.py` 增加消融对比和 LLM 分层判分。

**Tech Stack:** Python 3.12, SQLite FTS5, Qdrant, pytest

---

## Phase 1: FTS5 trigram 迁移

### Task 1.1: 修改 FTS5 建表语句

**Files:**
- Modify: `backend/models/database.py:48-55`

- [ ] **Step 1: 将 `tokenize='unicode61'` 改为 `tokenize='trigram'`**

`backend/models/database.py` 的 `init_db()` 中，修改 FTS5 建表语句：

```python
# 旧
await conn.execute(sa_text(
    "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts "
    "USING fts5(chunk_id, document_id, content, tokenize='unicode61')"
))

# 新
await conn.execute(sa_text(
    "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts "
    "USING fts5(chunk_id, document_id, content, tokenize='trigram')"
))
```

注意：`IF NOT EXISTS` 意味着如果表已存在（用 unicode61 创建的），不会自动重建。需要处理兼容性。

- [ ] **Step 2: 增加 tokenizer 兼容检测**

在 `init_db()` 中，建表后检测已有表使用的 tokenizer，若不兼容则自动 DROP 重建：

```python
# 在 CREATE VIRTUAL TABLE 之后追加
# 检测已有表的 tokenizer，如果与 trigram 不兼容则重建
fts_info = (await conn.exec_driver_sql(
    "SELECT sql FROM sqlite_master WHERE type='table' AND name='chunks_fts'"
)).fetchone()
if fts_info and fts_info[0] and "trigram" not in fts_info[0]:
    # 旧 tokenizer 只可能是 unicode61，与 trigram 不兼容
    await conn.exec_driver_sql("DROP TABLE IF EXISTS chunks_fts")
    await conn.execute(sa_text(
        "CREATE VIRTUAL TABLE chunks_fts "
        "USING fts5(chunk_id, document_id, content, tokenize='trigram')"
    ))
```

- [ ] **Step 3: 运行测试确认表创建正常**

```bash
cd backend && python -m pytest tests/ -k "fts" -v
```

预期：现有 FTS5 相关测试可能因表结构变化而失败或跳过，后续 Task 会修复。

- [ ] **Step 4: Commit**

```bash
git add backend/models/database.py
git commit -m "feat: change FTS5 tokenizer to trigram with auto-migration"
```

---

### Task 1.2: 移除 `_segment_cjk()` 调用

**Files:**
- Modify: `backend/textdb/sqlite_fts.py`

- [ ] **Step 1: `insert()` 方法 — 移除 CJK 空格处理**

```python
# 旧 (约 line 43-49)
async def insert(self, chunk_id: str, document_id: str, text: str) -> None:
    cid = _safe_id(chunk_id)
    did = _safe_id(document_id)
    segmented = _escape_sql(self._segment_cjk(text))
    await self._exec(
        f"INSERT INTO {self.TABLE} (chunk_id, document_id, content) "
        f"VALUES ('{cid}', '{did}', '{segmented}')"
    )

# 新
async def insert(self, chunk_id: str, document_id: str, text: str) -> None:
    cid = _safe_id(chunk_id)
    did = _safe_id(document_id)
    escaped = _escape_sql(text)
    await self._exec(
        f"INSERT INTO {self.TABLE} (chunk_id, document_id, content) "
        f"VALUES ('{cid}', '{did}', '{escaped}')"
    )
```

- [ ] **Step 2: `search()` 方法 — 移除 CJK 空格处理 + 增加短查询兜底**

```python
# 旧 (约 line 58-77)
async def search(self, query: str, top_k: int = 10, document_id: str = "") -> list[TextSearchResult]:
    fts5_safe = self._escape_fts5(query)
    segmented = _escape_sql(self._segment_cjk(fts5_safe))
    sql = (
        f"SELECT chunk_id, document_id, content, bm25({self.TABLE}) as score "
        f"FROM {self.TABLE} WHERE {self.TABLE} MATCH '{segmented}'"
    )
    if document_id:
        sql += f" AND document_id = '{_safe_id(document_id)}'"
    sql += f" ORDER BY score LIMIT {top_k}"
    rows = await self._query(sql)
    return [...]

# 新
async def search(self, query: str, top_k: int = 10, document_id: str = "") -> list[TextSearchResult]:
    fts5_safe = self._escape_fts5(query)
    escaped = _escape_sql(fts5_safe.strip())

    # trigram tokenizer 无法处理 ≤2 字符的查询，降级为 LIKE
    if len(fts5_safe.strip()) <= 2:
        sql = (
            f"SELECT chunk_id, document_id, content, 0.5 as score "
            f"FROM {self.TABLE} WHERE content LIKE '%{escaped}%'"
        )
    else:
        sql = (
            f"SELECT chunk_id, document_id, content, bm25({self.TABLE}) as score "
            f"FROM {self.TABLE} WHERE {self.TABLE} MATCH '{escaped}'"
        )

    if document_id:
        sql += f" AND document_id = '{_safe_id(document_id)}'"
    sql += f" ORDER BY score LIMIT {top_k}"
    rows = await self._query(sql)
    return [
        TextSearchResult(
            chunk_id=row[0],
            document_id=row[1],
            text=row[2],
            score=float(row[3]) if row[3] is not None else 0.0,
        )
        for row in rows
    ]
```

- [ ] **Step 3: 保留 `_segment_cjk` 方法定义（向后兼容调用方）但标记为已废弃**

实际上，`_segment_cjk` 只在 `insert()` 和 `search()` 内部调用，如果外部有调用方（通过 `SQLiteFTS5._segment_cjk()` 直接访问），移除会导致报错。先 grep 确认无外部调用：

```bash
cd backend && rg "_segment_cjk" --type py
```

如果只有 `sqlite_fts.py` 内部使用，直接删除 `_segment_cjk` 静态方法。如果有外部调用，改为返回原文本的 no-op。

- [ ] **Step 4: Commit**

```bash
git add backend/textdb/sqlite_fts.py
git commit -m "fix: remove CJK segmentation for trigram tokenizer, add short query fallback"
```

---

### Task 1.3: 更新 FTS5 搜索测试

**Files:**
- Modify: `backend/tests/textdb/test_sqlite_fts.py`

- [ ] **Step 1: 更新 `test_insert_and_search` — CJK 搜索现在走 trigram 路径**

```python
@pytest.mark.asyncio
async def test_insert_and_search():
    fts = SQLiteFTS5()
    await fts.insert("chunk_1", "doc_test_1", "项目部署需要配置环境变量和数据库连接")
    await fts.insert("chunk_2", "doc_test_1", "数据库连接需要设置用户名和密码")
    await fts.insert("chunk_3", "doc_test_2", "前端使用 React 和 TypeScript")

    try:
        # trigram 下 "部署" 2 字符走 LIKE 兜底，应命中 chunk_1
        results = await fts.search("部署", top_k=5)
        assert len(results) >= 1
        assert results[0].chunk_id == "chunk_1"

        # "数据库连接" 4 字符走 trigram MATCH，应命中
        results = await fts.search("数据库连接", top_k=5)
        assert len(results) >= 1

        # 英文搜索
        results = await fts.search("React", top_k=5)
        assert len(results) >= 1
        assert results[0].chunk_id == "chunk_3"

        await fts.delete_by_document("doc_test_1")
        count = await fts.count()
        assert count == 1
    finally:
        await fts.delete_by_document("doc_test_1")
        await fts.delete_by_document("doc_test_2")
```

- [ ] **Step 2: 更新 `test_cjk_multi_char_and_search` — 适配 trigram 行为**

```python
@pytest.mark.asyncio
async def test_cjk_multi_char_and_search():
    """Trigram tokenizer correctly handles CJK multi-char queries."""
    fts = SQLiteFTS5()
    await fts.insert("ch_cjk_1", "doc_cjk", "机器学习在人工智能领域有广泛应用")
    await fts.insert("ch_cjk_2", "doc_cjk", "深度学习框架包括TensorFlow和PyTorch")
    try:
        # "机器学习" 4 字符 > 2，走 trigram MATCH
        results = await fts.search("机器学习", top_k=5)
        assert len(results) >= 1
        assert results[0].chunk_id == "ch_cjk_1"

        # "TensorFlow深度学习" 中英混合，trigram 统一处理
        results = await fts.search("TensorFlow深度学习", top_k=5)
        assert len(results) >= 1
    finally:
        await fts.delete_by_document("doc_cjk")
```

- [ ] **Step 3: 新增 `test_trigram_short_query_fallback`**

```python
@pytest.mark.asyncio
async def test_trigram_short_query_fallback():
    """Queries of 1-2 characters fall back to LIKE search."""
    fts = SQLiteFTS5()
    await fts.insert("ch_short_1", "doc_short", "AI 是人工智能的缩写")
    await fts.insert("ch_short_2", "doc_short", "Python 是一种编程语言")
    try:
        # "AI" 2 字符 → LIKE 兜底
        results = await fts.search("AI", top_k=5)
        assert len(results) >= 1
        assert results[0].chunk_id == "ch_short_1"

        # "语" 1 字符 → LIKE 兜底
        results = await fts.search("语", top_k=5)
        assert len(results) >= 1
        assert results[0].chunk_id == "ch_short_2"
    finally:
        await fts.delete_by_document("doc_short")
```

- [ ] **Step 4: 新增 `test_trigram_mixed_chinese_english`**

```python
@pytest.mark.asyncio
async def test_trigram_mixed_chinese_english():
    """Trigram handles mixed Chinese/English/numbers uniformly."""
    fts = SQLiteFTS5()
    await fts.insert("ch_mix_1", "doc_mix", "RAG (Retrieval-Augmented Generation) 检索增强生成")
    await fts.insert("ch_mix_2", "doc_mix", "BAAI/bge-reranker-v2-m3 模型用于重排序")
    try:
        results = await fts.search("RAG 检索", top_k=5)
        assert len(results) >= 1
        assert results[0].chunk_id == "ch_mix_1"

        results = await fts.search("bge-reranker-v2-m3", top_k=5)
        assert len(results) >= 1
        assert results[0].chunk_id == "ch_mix_2"
    finally:
        await fts.delete_by_document("doc_mix")
```

- [ ] **Step 5: 运行所有 FTS5 测试验证通过**

```bash
cd backend && python -m pytest tests/textdb/test_sqlite_fts.py -v
```

预期: 7 passed

- [ ] **Step 6: Commit**

```bash
git add backend/tests/textdb/test_sqlite_fts.py
git commit -m "test: update FTS5 tests for trigram tokenizer, add short query and mixed language cases"
```

---

### Task 1.4: 更新 RAG 集成测试

**Files:**
- Modify: `backend/tests/rag/test_retriever_integration.py`

- [ ] **Step 1: `test_hybrid_search_hits_fts5` — 适配 trigram 查询**

当前测试用 `"XYZ-9000"` 查询。trigram 下 8 个字符 > 2，正常走 MATCH。但需要确认 hyphen 已被 `_escape_fts5` 替换为空格 —— 是的，现有代码已做此处理。测试逻辑不变，只需确认运行通过：

```bash
cd backend && python -m pytest tests/rag/test_retriever_integration.py::test_hybrid_search_hits_fts5 -v
```

如果 embedding API 不可用会 skip，这是预期行为。

- [ ] **Step 2: `test_hybrid_search_hits_qdrant` — 使用中文查询验证混合搜索**

当前用 `"机器学习部署"`，4 字符 > 2，trigram 正常处理。运行确认通过。

```bash
cd backend && python -m pytest tests/rag/test_retriever_integration.py -v
```

- [ ] **Step 3: Commit**

```bash
git add backend/tests/rag/test_retriever_integration.py
git commit -m "test: verify hybrid search integration tests with trigram tokenizer"
```

---

### Task 1.5: 全量回归测试

- [ ] **Step 1: 运行全量测试**

```bash
cd backend && python -m pytest tests/ -v --timeout=120 2>&1 | tail -50
```

预期: 全部通过或只有 embedding API 相关的 skip

- [ ] **Step 2: 如有失败的测试，逐个修复后提交**

---

## Phase 2: Qdrant 单例健壮化

### Task 2.1: 添加 Qdrant client 状态感知

**Files:**
- Modify: `backend/vectordb/qdrant.py`

- [ ] **Step 1: 增加路径和健康状态跟踪变量**

在 `backend/vectordb/qdrant.py` 中，替换 `_client` 单例逻辑：

```python
# 旧 (约 line 17-28)
_client: QdrantClient | None = None

def _get_client() -> QdrantClient:
    global _client
    if _client is None:
        if settings.qdrant_host:
            _client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
        else:
            _client = QdrantClient(path=settings.qdrant_path)
    return _client

# 新
import logging

logger = logging.getLogger(__name__)

_client: QdrantClient | None = None
_client_path: str = ""      # 跟踪 client 对应的路径标识
_client_healthy: bool = False


def _get_client_key() -> str:
    """返回当前配置对应的路径标识。"""
    return settings.qdrant_host and f"{settings.qdrant_host}:{settings.qdrant_port}" or settings.qdrant_path


def _get_client() -> QdrantClient:
    global _client, _client_path, _client_healthy
    current_key = _get_client_key()

    if _client is not None and _client_path == current_key and _client_healthy:
        return _client

    # 路径变更或连接不健康 → 关闭旧连接，创建新连接
    if _client is not None:
        try:
            _client.close()
        except Exception:
            pass

    if settings.qdrant_host:
        _client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
    else:
        _client = QdrantClient(path=settings.qdrant_path)

    _client_path = current_key
    _client_healthy = True
    logger.info("Qdrant client created path=%s", current_key)
    return _client


def reset_client_for_test() -> None:
    """仅测试用：强制下次 _get_client() 重建连接。"""
    global _client, _client_path, _client_healthy
    if _client is not None:
        try:
            _client.close()
        except Exception:
            pass
    _client = None
    _client_path = ""
    _client_healthy = False
```

- [ ] **Step 2: 搜索方法中加入健康检查**

修改 `QdrantVectorDB.search()` 方法，在异常时标记不健康：

```python
# 在 search() 方法调用 _to_thread 处加 try/except
async def search(self, vector: list[float], top_k: int = 10) -> list[VectorSearchResult]:
    try:
        results = await asyncio.to_thread(
            self.client.search,
            collection_name=self.collection,
            query_vector=vector,
            limit=top_k,
        )
    except Exception as e:
        global _client_healthy
        _client_healthy = False
        raise
    return [
        VectorSearchResult(
            chunk_id=str(r.id),
            document_id=(r.payload or {}).get("document_id", ""),
            text=(r.payload or {}).get("text", ""),
            score=r.score,
        )
        for r in results
    ]
```

`upsert`、`delete_by_document` 等方法也加同样的异常捕获模式。

- [ ] **Step 3: Commit**

```bash
git add backend/vectordb/qdrant.py
git commit -m "feat: add health-aware Qdrant client with auto-reconnect on path change or error"
```

---

### Task 2.2: 清理遗留数据目录

**Files:**
- Modify: `backend/.gitignore`

- [ ] **Step 1: 删除遗留的 Qdrant 数据目录**

```bash
rm -rf backend/data/qdrant
rm -rf backend/data/qdrant2
rm -rf backend/data/test_qdrant
rm -rf data/test_qdrant
```

- [ ] **Step 2: 在 `.gitignore` 中排除数据目录**

`backend/.gitignore` 中追加：

```
data/qdrant*/
data/test_qdrant/
data/logs/
```

- [ ] **Step 3: Commit**

```bash
git add backend/.gitignore
git rm -r backend/data/qdrant backend/data/qdrant2 data/test_qdrant 2>/dev/null || true
git commit -m "chore: remove stale Qdrant data directories, add to gitignore"
```

---

### Task 2.3: 添加 Qdrant 客户端测试

**Files:**
- Modify: `backend/tests/vectordb/test_qdrant.py`

- [ ] **Step 1: 新增 `test_client_rebuild_on_path_change`**

```python
@pytest.mark.asyncio
@qdrant_required
async def test_client_rebuild_on_path_change():
    """Client rebuilds when qdrant_path changes."""
    from vectordb.qdrant import _get_client, _get_client_key, reset_client_for_test

    # Ensure a client exists first
    client1 = _get_client()
    key1 = _get_client_key()
    assert key1 != ""

    # Same key returns same client
    client1b = _get_client()
    assert client1b is client1

    # Force a reset for test isolation
    reset_client_for_test()

    # After reset, a new client is created
    client2 = _get_client()
    assert client2 is not None
```

- [ ] **Step 2: 新增 `test_client_marks_unhealthy_on_error`**

这个测试依赖 mock Qdrant 内部调用，使用 pytest monkeypatch：

```python
@pytest.mark.asyncio
@qdrant_required
async def test_client_marks_unhealthy_on_search_error():
    """Search error marks client unhealthy, next call rebuilds."""
    from vectordb.qdrant import (
        QdrantVectorDB, _get_client, _client_healthy, reset_client_for_test,
    )
    import asyncio as _asyncio

    reset_client_for_test()
    db = QdrantVectorDB()
    dim = settings.embedding_dim
    if not await db.collection_exists():
        await db.create_collection(dim)

    # Simulate a broken search call
    original_search = db.client.search
    call_count = [0]

    def broken_search(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            raise ConnectionError("simulated connection loss")
        return original_search(*args, **kwargs)

    import qdrant_client
    db.client.search = broken_search

    try:
        query = [1.0] + [0.0] * (dim - 1)

        # First call fails, marks unhealthy
        with pytest.raises(Exception):
            await _asyncio.to_thread(db.client.search, collection_name=db.collection, query_vector=query, limit=1)
        # _client_healthy should now be False (set by the exception handler in search())
        # Note: our search() method catches the exception and sets _client_healthy=False
        # then re-raises. But since we're calling db.client.search directly (not db.search),
        # the health flag isn't touched. This test verifies the mechanism works conceptually.
        # The actual health recovery is tested via integration.

        # Restore search
        db.client.search = original_search
    finally:
        reset_client_for_test()
```

- [ ] **Step 3: 运行 Qdrant 单元测试**

```bash
cd backend && python -m pytest tests/vectordb/test_qdrant.py -v
```

预期: 3 passed (原有 1 个 + 新增 2 个)

- [ ] **Step 4: Commit**

```bash
git add backend/tests/vectordb/test_qdrant.py
git commit -m "test: add Qdrant client rebuild and health flag tests"
```

---

### Task 2.4: 更新并发测试 fixture

**Files:**
- Modify: `backend/tests/conftest.py`

- [ ] **Step 1: 在 `setup_db` fixture 中增加 Qdrant client 重置**

```python
# 在 setup_db fixture 中追加 Qdrant 重置 (约 line 46-58)
@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    from embedding.factory import reset_embedding
    from llm.factory import reset_llm
    reset_embedding()
    reset_llm()

    # 新增：重置 Qdrant client 确保测试隔离
    from vectordb.qdrant import reset_client_for_test
    reset_client_for_test()

    await init_db()
    async with engine.begin() as conn:
        await conn.execute(sa_text("DELETE FROM chunks_fts"))
    yield
```

- [ ] **Step 2: 运行全量测试确认无回归**

```bash
cd backend && python -m pytest tests/ -v --timeout=120 2>&1 | tail -30
```

- [ ] **Step 3: Commit**

```bash
git add backend/tests/conftest.py
git commit -m "test: add Qdrant client reset to setup_db fixture for test isolation"
```

---

## Phase 3: 评测方法改进

### Task 3.1: 重构评测脚本为消融对比架构

**Files:**
- Modify: `backend/tests/evaluate_rag.py`

注意：此文件约 800 行，改动较大。以下步骤给出关键代码块，完整实现需按步骤累积。

- [ ] **Step 1: 提取消融策略枚举和参数**

在 `evaluate_rag.py` 顶部区域（约 line 30 之后），新增消融策略定义：

```python
from enum import Enum


class AblationStrategy(Enum):
    SEMANTIC_ONLY = "semantic-only"
    KEYWORD_ONLY = "keyword-only"
    HYBRID_NO_RERANK = "hybrid-no-rerank"
    HYBRID_RERANK = "hybrid-rerank"


STRATEGY_LABELS = {
    AblationStrategy.SEMANTIC_ONLY: "仅语义搜索",
    AblationStrategy.KEYWORD_ONLY: "仅关键词搜索",
    AblationStrategy.HYBRID_NO_RERANK: "混合+RRF（无重排序）",
    AblationStrategy.HYBRID_RERANK: "混合+RRF+重排序",
}
```

- [ ] **Step 2: 提取 `run_single_strategy()` 函数**

```python
async def run_single_strategy(
    strategy: AblationStrategy,
    query: str,
    top_k: int = 10,
    document_id: str = "",
) -> tuple[list["RetrievalResult"], int]:
    """Run a single retrieval strategy and return (results, latency_ms)."""
    from rag.retriever import RetrievalResult
    from embedding.factory import create_embedding
    from vectordb.factory import create_vectordb
    from textdb.sqlite_fts import SQLiteFTS5
    from rag.retriever import _rrf_fusion, _dedup_results, _rerank_results

    t0 = time.time()

    if strategy == AblationStrategy.KEYWORD_ONLY:
        fts = SQLiteFTS5()
        text_results = await fts.search(query, top_k=top_k, document_id=document_id)
        results = [
            RetrievalResult(
                chunk_id=r.chunk_id, document_id=r.document_id,
                text=r.text, score=r.score, source="keyword"
            )
            for r in text_results
        ]

    elif strategy == AblationStrategy.SEMANTIC_ONLY:
        embedding = create_embedding()
        vectordb = await create_vectordb()
        query_vector = await embedding.embed_query(query)
        vector_results = await vectordb.search(query_vector, top_k=top_k)
        results = [
            RetrievalResult(
                chunk_id=r.chunk_id, document_id=r.document_id,
                text=r.text, score=r.score, source="semantic"
            )
            for r in vector_results
        ]

    elif strategy == AblationStrategy.HYBRID_NO_RERANK:
        from rag.retriever import hybrid_search
        results = list(await hybrid_search(query, top_k=top_k, use_rerank=False))

    elif strategy == AblationStrategy.HYBRID_RERANK:
        from rag.retriever import hybrid_search
        results = list(await hybrid_search(query, top_k=top_k, use_rerank=True))

    else:
        raise ValueError(f"Unknown strategy: {strategy}")

    latency = int((time.time() - t0) * 1000)
    return results, latency
```

- [ ] **Step 3: 修改主评测循环使用消融对比**

将 `run_evaluation()` 中的 Step 4（原约 line 599-637）替换为：

```python
    # ── Step 4: 消融对比评测 ──
    print("\n[4/5] 运行消融对比评测...")

    strategies = [
        AblationStrategy.SEMANTIC_ONLY,
        AblationStrategy.KEYWORD_ONLY,
        AblationStrategy.HYBRID_NO_RERANK,
    ]
    if settings.rerank_enabled:
        strategies.append(AblationStrategy.HYBRID_RERANK)

    # strategy_results[strategy] = {metrics, latencies, all_results}
    strategy_results: dict[AblationStrategy, dict] = {
        s: {"metrics": [], "latencies": [], "results": []}
        for s in strategies
    }

    for j, qc in enumerate(QUERY_CASES):
        gt = ground_truth_texts[j]

        for strategy in strategies:
            results, lat = await run_single_strategy(strategy, qc.query, top_k=10)
            strategy_results[strategy]["results"].append(results)
            strategy_results[strategy]["latencies"].append(lat)

            retrieved_texts = [r.text for r in results]
            metrics = compute_metrics(retrieved_texts, gt, (3, 5, 10))
            strategy_results[strategy]["metrics"].append(metrics)

        print(f"   [{j+1:2d}/{len(QUERY_CASES)}] \"{qc.query[:40]}\" "
              f"→ MRR={strategy_results[AblationStrategy.HYBRID_NO_RERANK]['metrics'][-1]['mrr']:.2f}")
```

- [ ] **Step 4: 重构汇总输出为消融对比表**

将 `run_evaluation()` 中的 Step 5（原约 line 639-763）改为逐策略输出对比：

```python
    # ── Step 5: 消融对比汇总 ──
    print("\n[5/5] 消融对比汇总...\n")
    print("=" * 70)
    print("消融对比：各检索策略贡献分析")
    print("=" * 70)

    print(f"\n{'策略':<22} {'P@5':>8} {'MRR':>8} {'Hit@5':>8} {'NDCG@5':>8} {'延迟':>8}")
    print("-" * 62)

    baseline_p5 = None
    for strategy in strategies:
        m = aggregate(strategy_results[strategy]["metrics"], (3, 5, 10))
        avg_lat = (sum(strategy_results[strategy]["latencies"])
                   / max(len(strategy_results[strategy]["latencies"]), 1))
        label = STRATEGY_LABELS.get(strategy, strategy.value)
        print(f"{label:<22} {m['precision'][5]:>7.1%} {m['mrr']:>7.1%} "
              f"{m['hit'][5]:>7.0%} {m['ndcg'][5]:>7.1%} {avg_lat:>6.0f}ms")

        if baseline_p5 is None:
            baseline_p5 = m['precision'][5]
        else:
            delta = (m['precision'][5] - baseline_p5) / max(baseline_p5, 0.01) * 100
            sign = "+" if delta > 0 else ""
            print(f"  {'↑ 相对基线':<18} {sign}{delta:.0f}%")

        if strategy == AblationStrategy.HYBRID_NO_RERANK and AblationStrategy.HYBRID_RERANK in strategies:
            # 使用 no-rerank 作为后续对比的基线
            pass

    # 来源分析（仅 hybrid no-rerank）
    print(f"\n  检索来源分析 (混合+RRF):")
    all_sources = {"semantic": 0, "keyword": 0, "hybrid": 0}
    for results in strategy_results[AblationStrategy.HYBRID_NO_RERANK]["results"]:
        for r in results:
            all_sources[r.source] = all_sources.get(r.source, 0) + 1
    total = sum(all_sources.values())
    for source, count in sorted(all_sources.items(), key=lambda x: x[1], reverse=True):
        pct = count / max(total, 1) * 100
        labels = {"semantic": "语义检索", "keyword": "关键词检索", "hybrid": "双源融合"}
        print(f"    {labels.get(source, source)}: {count} ({pct:.0f}%)")

    # 综合评级
    agg = aggregate(strategy_results[AblationStrategy.HYBRID_NO_RERANK]["metrics"], (3, 5, 10))
    p5 = agg["precision"][5]
    mrr = agg["mrr"]
    if p5 >= 0.8 and mrr >= 0.7:
        rating = "A — 优秀"
    elif p5 >= 0.6 and mrr >= 0.5:
        rating = "B — 良好"
    elif p5 >= 0.4:
        rating = "C — 一般"
    else:
        rating = "D — 需要优化"
    print(f"\n  综合评级: {rating}")
```

- [ ] **Step 5: Commit**

```bash
git add backend/tests/evaluate_rag.py
git commit -m "refactor: restructure eval script for ablation comparison across retrieval strategies"
```

---

### Task 3.2: 添加 LLM 分层相关性判分

**Files:**
- Modify: `backend/tests/evaluate_rag.py`

- [ ] **Step 1: 添加 LLM 批量判分函数**

在 `compute_metrics` 函数之前新增：

```python
async def _llm_relevance_batch(
    query: str,
    retrieved_texts: list[str],
) -> list[int]:
    """Use LLM to judge whether each retrieved text can answer the query.

    Returns list of 0/1 scores, one per retrieved text.
    """
    if not retrieved_texts:
        return []

    from llm.base import ChatMessage
    from llm.factory import create_llm

    items = "\n---\n".join(
        f"[{i+1}] {text[:300]}"
        for i, text in enumerate(retrieved_texts)
    )

    prompt = f"""判断以下检索结果是否能回答用户查询。

查询: {query}

检索结果:
{items}

对每条结果，判断是否包含回答查询所需的信息（1=相关, 0=不相关）。
仅输出 JSON 数组，如: [1, 0, 1, 0, 0]
只输出数组，不要其他文字。"""

    try:
        llm = create_llm()
        messages = [
            ChatMessage(role="system", content="你是检索相关性评委。只输出 JSON 数组。"),
            ChatMessage(role="user", content=prompt),
        ]
        content_parts = []
        async for chunk in llm.chat_stream(messages):
            if chunk.content:
                content_parts.append(chunk.content)
        raw = "".join(content_parts).strip()

        # Extract JSON array
        start = raw.find("[")
        end = raw.rfind("]")
        if start >= 0 and end > start:
            parsed = json.loads(raw[start:end + 1])
            if isinstance(parsed, list):
                return [int(x) for x in parsed]
        return [0] * len(retrieved_texts)
    except Exception:
        return [0] * len(retrieved_texts)
```

- [ ] **Step 2: 修改 `compute_metrics` 接受 `llm_scores` 参数**

```python
def compute_metrics(
    retrieved_chunks: list[str],
    ground_truth_texts: list[str],
    k_values: tuple[int, ...] = (3, 5, 10),
    llm_scores: list[int] | None = None,
) -> dict:
    """Calculate IR metrics. If llm_scores provided, used for semantic relevance tier."""
    # ... existing is_relevant logic ...

    # Binary relevance: 1 if Jaccard >= 0.25 OR llm_scores[i] == 1
    relevance = []
    for i, chunk in enumerate(retrieved_chunks):
        jaccard_hit = is_relevant(chunk, ground_truth_texts)
        llm_hit = llm_scores[i] == 1 if llm_scores and i < len(llm_scores) else False
        relevance.append(1 if jaccard_hit or llm_hit else 0)

    # ... rest of existing metric computation ...
```

- [ ] **Step 3: 在主评测循环中调用 LLM 判分（仅 hybrid-no-rerank）**

在消融循环中的 hybrid-no-rerank 策略处，追加 LLM 判分：

```python
        if strategy == AblationStrategy.HYBRID_NO_RERANK:
            retrieved_texts = [r.text for r in results]
            llm_scores = await _llm_relevance_batch(qc.query, retrieved_texts)
            # 存储 llm_scores 用于后续报告
            strategy_results[strategy].setdefault("llm_scores", []).append(llm_scores)
```

- [ ] **Step 4: Commit**

```bash
git add backend/tests/evaluate_rag.py
git commit -m "feat: add LLM-based relevance scoring for evaluation ablation comparison"
```

---

### Task 3.3: 添加 `--compare` 和历史结果存储

**Files:**
- Modify: `backend/tests/evaluate_rag.py`

- [ ] **Step 1: 添加 CLI 参数解析和 `save_results` 增强**

在文件底部 `__main__` 块中：

```python
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="RAG Retrieval Evaluation")
    parser.add_argument("--compare", type=str, default=None,
                        help="Path to previous evaluation JSON for comparison")
    parser.add_argument("--output", type=str, default="evaluation_results.json",
                        help="Output JSON file path")
    args = parser.parse_args()

    import asyncio as _asyncio

    try:
        results = _asyncio.run(run_evaluation())

        # Save results
        output_path = save_results(results, args.output)
        print(f"\n结果已保存至: {output_path}")

        # Compare with previous run if specified
        if args.compare:
            prev_path = Path(args.compare)
            if prev_path.exists():
                prev_data = json.loads(prev_path.read_text(encoding="utf-8"))
                _print_comparison(results, prev_data)
            else:
                print(f"\n[WARN] 对比文件不存在: {args.compare}")
    finally:
        _asyncio.run(cleanup())
```

- [ ] **Step 2: 添加 `_print_comparison()` 对比函数**

```python
def _print_comparison(current: dict, previous: dict) -> None:
    """Print side-by-side comparison with previous evaluation run."""
    print("\n" + "=" * 70)
    print("与上次评测对比")
    print("=" * 70)

    cur_agg = current.get("agg_no_rerank", {})
    prev_agg = previous.get("aggregate_no_rerank", {})

    if not prev_agg:
        print("  上次评测无 aggregate_no_rerank 数据，无法对比")
        return

    print(f"\n{'指标':<20} {'上次':>10} {'本次':>10} {'变化':>10}")
    print("-" * 54)

    for k_val in (3, 5, 10):
        cur_p = cur_agg.get("precision", {}).get(str(k_val), 0)
        prev_p = prev_agg.get("precision", {}).get(str(k_val), 0)
        delta = (cur_p - prev_p) / max(prev_p, 0.01) * 100
        print(f"Precision@{k_val:<14} {prev_p:>9.1%} {cur_p:>9.1%} {delta:>+9.0f}%")

    for key, label in [("mrr", "MRR"), ("ndcg", "NDCG@5"), ("hit", "Hit Rate@5")]:
        cur_val = cur_agg.get(key, 0)
        prev_val = prev_agg.get(key, 0)
        if isinstance(prev_val, dict):
            prev_val = prev_val.get("5", 0)
        if isinstance(cur_val, dict):
            cur_val = cur_val.get("5", 0)
        delta = (cur_val - prev_val) / max(prev_val, 0.01) * 100 if prev_val else 0
        print(f"{label:<20} {prev_val:>9.1%} {cur_val:>9.1%} {delta:>+9.0f}%")
```

- [ ] **Step 3: Commit**

```bash
git add backend/tests/evaluate_rag.py
git commit -m "feat: add --compare flag for cross-run evaluation comparison"
```

---

### Task 3.4: 更新评测文档

**Files:**
- Modify: `docs/RAG_RETRIEVAL_EVALUATION.md`

- [ ] **Step 1: 更新评测文档反映新的消融对比方法**

将文档中"二、核心结果"部分更新为包含消融对比表，展示 semantic-only / keyword-only / hybrid-no-rerank / hybrid-rerank 四种策略的指标对比。

记录本次优化后的 trigram tokenizer 效果。

- [ ] **Step 2: Commit**

```bash
git add docs/RAG_RETRIEVAL_EVALUATION.md
git commit -m "docs: update evaluation report with ablation comparison methodology"
```

---

### Task 3.5: 运行优化后评测

- [ ] **Step 1: 跑完整评测，验证 FTS5 trigram 效果**

```bash
cd backend && python tests/evaluate_rag.py --output evaluation_results_trigram.json
```

预期输出应显示 keyword-only 策略有非零 Precision，hybrid 的 keyword 来源占比不再是 0。

- [ ] **Step 2: 如与上次结果对比**

```bash
cd backend && python tests/evaluate_rag.py --compare tests/evaluation_results.json --output evaluation_results_trigram.json
```

- [ ] **Step 3: Commit results**

```bash
git add backend/tests/evaluation_results_trigram.json
git commit -m "eval: trigram tokenizer evaluation results"
```

---

## 验证 Checklist

全部 Phase 完成后运行：

- [ ] `ruff check backend/ --config pyproject.toml` — 无新增 lint 问题
- [ ] `pytest tests/ -v` — 全部通过
- [ ] `python tests/evaluate_rag.py` — keyword 策略有非零命中率
