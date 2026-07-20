# 检索超时降级与 Reranker 预热实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** rerank 超时时降级返回 RRF 排序结果(而非空),配合候选数削减与模型预热,消除重启后首次检索必失败的问题。

**Architecture:** 在 `hybrid_search` 的 rerank 调用外包 `asyncio.wait_for`(预算 `rag_timeout_rerank=5s`),超时走既有 `fallback_reason` 降级机制标记 `rerank_timeout`;`rerank_top_n` 16→8 使 CPU 常态耗时落入预算;`CrossEncoderReranker._load` 加载后先做一次 dummy 推理再置 ready;`SearchDocsTool` 的 `reranked` 标志改为从本次结果的降级标记推导。

**Tech Stack:** Python 3.12 / FastAPI / asyncio / sentence-transformers CrossEncoder / pytest + pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-07-18-retrieval-timeout-degradation-design.md`

**运行测试的方式(Windows,项目 venv):**
```bash
cd D:/Python/subject1/RAG_Agent/backend
D:/Python/subject1/RAG_Agent/.venv/Scripts/python.exe -m pytest <测试路径> -v
```

---

### Task 1: rerank 超时降级(retriever.py)+ 新配置

**Files:**
- Modify: `backend/config.py`(约 78-80 行 `rerank_top_n`;约 129 行 `rag_timeout_retrieval` 附近加新配置)
- Modify: `backend/rag/retriever.py:539-546`(rerank 调用块)
- Test: `backend/tests/rag/test_retriever.py`(文件末尾追加)

- [ ] **Step 1: 写失败测试**

在 `backend/tests/rag/test_retriever.py` 文件末尾追加(文件已有 `import asyncio` 吗?没有——需在文件顶部现有 import 区加 `import asyncio`):

```python
@pytest.mark.asyncio
async def test_hybrid_search_rerank_timeout_falls_back_to_rrf(monkeypatch):
    """rerank 超预算时必须降级返回 RRF 排序结果,标记 rerank_timeout,而不是抛错或返回空。"""
    _disable_optional_retrieval_features(monkeypatch)
    embedding = MagicMock()
    embedding.embed_query = AsyncMock(return_value=[0.1, 0.2])
    vector = VectorSearchResult(
        chunk_id="vec", document_id="doc", text="semantic result", score=0.9
    )
    keyword = TextSearchResult(
        chunk_id="kw", document_id="doc", text="keyword result", score=3.0
    )
    vectordb = MagicMock()
    vectordb.search = AsyncMock(return_value=[vector])
    fts = MagicMock()
    fts.search = AsyncMock(return_value=[keyword])

    monkeypatch.setattr(retriever, "create_embedding", lambda: embedding)
    monkeypatch.setattr(retriever, "create_vectordb", AsyncMock(return_value=vectordb))
    monkeypatch.setattr(retriever, "BM25Search", lambda: fts)
    monkeypatch.setattr(retriever.settings, "rerank_enabled", True)
    monkeypatch.setattr(retriever.settings, "rag_timeout_rerank", 0.05)

    async def slow_rerank(query, results, top_k):
        await asyncio.sleep(1.0)
        return results

    monkeypatch.setattr(retriever, "_rerank_results", slow_rerank)

    results = await hybrid_search("query", top_k=3, use_rerank=True)

    assert len(results) == 2  # RRF 结果保留,不为空
    assert all("rerank_timeout" in r.fallback_reason for r in results)


@pytest.mark.asyncio
async def test_hybrid_search_rerank_within_budget_no_fallback(monkeypatch):
    """rerank 在预算内完成时正常精排,无降级标记。"""
    _disable_optional_retrieval_features(monkeypatch)
    embedding = MagicMock()
    embedding.embed_query = AsyncMock(return_value=[0.1, 0.2])
    vector = VectorSearchResult(
        chunk_id="vec", document_id="doc", text="semantic result", score=0.9
    )
    vectordb = MagicMock()
    vectordb.search = AsyncMock(return_value=[vector])
    fts = MagicMock()
    fts.search = AsyncMock(return_value=[])

    monkeypatch.setattr(retriever, "create_embedding", lambda: embedding)
    monkeypatch.setattr(retriever, "create_vectordb", AsyncMock(return_value=vectordb))
    monkeypatch.setattr(retriever, "BM25Search", lambda: fts)
    monkeypatch.setattr(retriever.settings, "rerank_enabled", True)
    monkeypatch.setattr(retriever.settings, "rag_timeout_rerank", 5.0)

    async def fast_rerank(query, results, top_k):
        return results[:top_k]

    monkeypatch.setattr(retriever, "_rerank_results", fast_rerank)

    results = await hybrid_search("query", top_k=3, use_rerank=True)

    assert len(results) == 1
    assert "rerank" not in results[0].fallback_reason
```

注意:第一个测试同时 mock 了向量与关键词两路都成功,避免 `keyword_only_fallback`/`semantic_only_fallback` 混入断言。

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/subject1/RAG_Agent/.venv/Scripts/python.exe -m pytest tests/rag/test_retriever.py::test_hybrid_search_rerank_timeout_falls_back_to_rrf -v`
Expected: FAIL — `AttributeError: ... settings ... has no attribute 'rag_timeout_rerank'`(monkeypatch 对不存在的属性报错)

- [ ] **Step 3: 加配置**

`backend/config.py`,`rag_timeout_retrieval: float = 10.0` 一行(约 129 行)后追加:

```python
    rag_timeout_rerank: float = 5.0       # rerank budget within retrieval; on timeout fall back to RRF order
```

同文件 `rerank_top_n: int = 16`(约 80 行)改为:

```python
    rerank_top_n: int = 8
```

- [ ] **Step 4: 实现超时降级**

`backend/rag/retriever.py` 约 539-546 行,现状:

```python
    reranked = use_rerank and settings.rerank_enabled
    if reranked:
        try:
            results = await _rerank_results(query, results, top_k)
        except Exception as e:
            logger.warning("rerank failed, using RRF order: %s", e)
            results = results[:top_k]
            fallback_reason = _merge_fallback(fallback_reason, "rerank_fallback")
```

改为(注意 `except TimeoutError` 必须在 `except Exception` 之前,Python 3.11+ `asyncio.TimeoutError` 即内置 `TimeoutError`,是 `Exception` 子类):

```python
    reranked = use_rerank and settings.rerank_enabled
    if reranked:
        try:
            results = await asyncio.wait_for(
                _rerank_results(query, results, top_k),
                timeout=settings.rag_timeout_rerank,
            )
        except TimeoutError:
            logger.warning(
                "rerank timed out after %.1fs, using RRF order",
                settings.rag_timeout_rerank,
            )
            results = results[:top_k]
            fallback_reason = _merge_fallback(fallback_reason, "rerank_timeout")
        except Exception as e:
            logger.warning("rerank failed, using RRF order: %s", e)
            results = results[:top_k]
            fallback_reason = _merge_fallback(fallback_reason, "rerank_fallback")
```

`retriever.py` 第 1 行已有 `import asyncio`,无需新增。

- [ ] **Step 5: 运行确认通过**

Run: `D:/Python/subject1/RAG_Agent/.venv/Scripts/python.exe -m pytest tests/rag/test_retriever.py -v`
Expected: 全部 PASS(含两个新测试与既有测试)

- [ ] **Step 6: Commit**

```bash
cd D:/Python/subject1/RAG_Agent
git add backend/config.py backend/rag/retriever.py backend/tests/rag/test_retriever.py
git commit -m "feat: add rerank timeout degradation with RRF fallback (rag_timeout_rerank=5s, top_n 16->8)"
```

---

### Task 2: Reranker 加载后预热(cross_encoder.py)

**Files:**
- Modify: `backend/reranker/cross_encoder.py:60-64`(`_load` 内)
- Test: `backend/tests/reranker/test_reranker.py`(文件末尾追加)

- [ ] **Step 1: 写失败测试**

在 `backend/tests/reranker/test_reranker.py` 末尾追加。测试用假 `sentence_transformers` 模块注入,`predict` 阻塞在 `threading.Event` 上,验证 ready 只在预热推理完成后才为 True:

```python
class TestRerankerWarmup:
    def test_ready_only_after_warmup_inference(self, monkeypatch):
        """加载完成后必须先跑一次 dummy 推理(预热),再置 ready。"""
        import sys
        import threading
        import time as _time
        import types

        release = threading.Event()
        calls: list = []

        class FakeCrossEncoder:
            def __init__(self, model_name):
                pass

            def predict(self, pairs):
                calls.append(pairs)
                release.wait(timeout=5)
                return [0.5] * len(pairs)

        fake_module = types.ModuleType("sentence_transformers")
        fake_module.CrossEncoder = FakeCrossEncoder
        monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

        from reranker.cross_encoder import CrossEncoderReranker

        r = CrossEncoderReranker("fake-model-for-warmup-test")
        r.preload_async()

        # 等到预热 predict 被调用(模型"加载"是瞬时的)
        deadline = _time.time() + 2
        while not calls and _time.time() < deadline:
            _time.sleep(0.01)
        assert calls, "warmup predict was never called"
        assert calls[0] == [["warmup", "warmup"]]

        # 预热尚未完成(predict 阻塞中)→ 不得 ready
        assert r.ready is False

        # 放行预热 → ready
        release.set()
        deadline = _time.time() + 2
        while not r.ready and _time.time() < deadline:
            _time.sleep(0.01)
        assert r.ready is True
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/subject1/RAG_Agent/.venv/Scripts/python.exe -m pytest tests/reranker/test_reranker.py::TestRerankerWarmup -v`
Expected: FAIL — `assert calls, "warmup predict was never called"`(当前代码加载后从不调用 predict;或 `assert r.ready is False` 失败,因为当前加载即 ready)

- [ ] **Step 3: 实现预热**

`backend/reranker/cross_encoder.py` `_load()` 内,现状(约 60-64 行):

```python
                start = time.time()
                self._model = CrossEncoder(self._model_name)
                elapsed = time.time() - start
                self._ready = True
                logger.info("reranker ready model=%s elapsed=%.0fs", self._model_name, elapsed)
```

改为:

```python
                start = time.time()
                self._model = CrossEncoder(self._model_name)
                # Warmup: torch 首次推理有数秒惰性初始化,在后台线程消化掉,
                # 避免算进第一个真实请求导致检索超时
                self._model.predict([["warmup", "warmup"]])
                elapsed = time.time() - start
                self._ready = True
                logger.info("reranker ready model=%s elapsed=%.0fs", self._model_name, elapsed)
```

预热异常会被 `_load` 既有的 `except Exception` 捕获并走 `set_reranker_failed`,无需新增处理。

- [ ] **Step 4: 运行确认通过**

Run: `D:/Python/subject1/RAG_Agent/.venv/Scripts/python.exe -m pytest tests/reranker/ -v`
Expected: 全部 PASS(`test_real_reranker` 标记 slow,默认跳过或较慢,允许 skip)

- [ ] **Step 5: Commit**

```bash
cd D:/Python/subject1/RAG_Agent
git add backend/reranker/cross_encoder.py backend/tests/reranker/test_reranker.py
git commit -m "feat: warm up reranker with dummy inference before marking ready"
```

---

### Task 3: `reranked` 标志从本次结果推导(tools.py)

**Files:**
- Modify: `backend/agent/tools.py:167-177`(`SearchDocsTool.execute` 的 `ToolResult` 构造处)
- Test: `backend/tests/agent/test_tools.py`(文件末尾追加)

- [ ] **Step 1: 写失败测试**

在 `backend/tests/agent/test_tools.py` 末尾追加:

```python
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
```

说明:`SearchDocsTool.execute` 内部是函数级 `from rag.retriever import hybrid_search` / `from reranker.factory import is_reranker_ready`,在源模块上 monkeypatch 即可生效。fake 的 document_id 不在测试库中,filename 回退为 `document_id[:8]`,不影响断言。

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/subject1/RAG_Agent/.venv/Scripts/python.exe -m pytest tests/agent/test_tools.py::TestSearchDocsRerankedFlag -v`
Expected: `test_reranked_false_when_results_degraded` FAIL — `assert True is False`(当前 `reranked` 直接取 `is_reranker_ready()`);`test_reranked_true_when_no_degradation` PASS(既有行为恰好正确,保留作回归锚点)

- [ ] **Step 3: 实现标志推导**

`backend/agent/tools.py` `SearchDocsTool.execute` 中,`ToolResult` 构造前(拿到 `results` 与 `filenames` 之后)加:

```python
            degraded = any(
                "rerank_timeout" in r.fallback_reason or "rerank_fallback" in r.fallback_reason
                for r in results
            )
            reranked_flag = bool(results) and is_reranker_ready() and not degraded
```

并把 data 中的:

```python
                    "reranked": is_reranker_ready(),
```

改为:

```python
                    "reranked": reranked_flag,
```

同函数 `except asyncio.TimeoutError` 分支中的 `"reranked": False` 保持不变。

- [ ] **Step 4: 运行确认通过**

Run: `D:/Python/subject1/RAG_Agent/.venv/Scripts/python.exe -m pytest tests/agent/test_tools.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
cd D:/Python/subject1/RAG_Agent
git add backend/agent/tools.py backend/tests/agent/test_tools.py
git commit -m "fix: derive reranked flag from per-result fallback state instead of global readiness"
```

---

### Task 4: 全量回归 + 真机验证

**Files:** 无新改动

- [ ] **Step 1: 相关目录全量回归**

Run: `D:/Python/subject1/RAG_Agent/.venv/Scripts/python.exe -m pytest tests/rag/ tests/agent/ tests/reranker/ tests/vectordb/ -q`
Expected: 全部 PASS(slow 标记项允许 skip)

- [ ] **Step 2: 真机冷启动验证**

重启后端(杀掉 8000 端口旧进程后 `python -m uvicorn main:app --port 8000`,或 `python main.py`),启动完成后**立即**发一条指向文档的问题(不等 reranker 预热)。先生成 UTF-8 请求体(Git Bash 直接 `-d` 传中文会乱码导致 400):

```bash
cd D:/Python/subject1/RAG_Agent
.venv/Scripts/python.exe -c "import json; open('chat_req.json','wb').write(json.dumps({'message':'根据知识库文档,总结 agent skill 的主要内容'}, ensure_ascii=False).encode('utf-8'))"
curl -s -N -X POST http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json; charset=utf-8" \
  -H "X-Admin-Token: $(grep -oP '^ADMIN_API_TOKEN=\K.*' backend/.env)" \
  --data-binary @chat_req.json
```

Expected:`tool_result` 事件 `result_count > 0`(预热未完成时走中性分数路径,或超时降级返回 RRF 结果,均不为空);日志无 `retrieval timed out`,可能出现 `rerank timed out ... using RRF order`(属预期降级)。

- [ ] **Step 3: 热态验证**

再发一次同样问题。Expected:`result_count > 0` 且 `reranked: true`,检索总耗时明显低于 10s(rerank 候选 8 个,约 2~3s)。
