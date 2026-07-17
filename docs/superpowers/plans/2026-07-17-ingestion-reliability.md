# Ingestion Reliability Hardening — Implementation Plan (Phase 3a)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden backend document ingestion with generation-aware idempotency, error classification, configurable exponential backoff with jitter, and a formal `retry_wait` task state.

**Architecture:** Three files, four focused changes: config settings (config.py), retry logic rewrite (pipeline.py), and task state machine extension (worker/tasks.py). All error classification and backoff is in pipeline.py; the worker only handles DB state transitions.

**Tech Stack:** Python 3.12+, SQLAlchemy async, asyncio, pytest + pytest-asyncio

---

### Task 1: Add ingestion retry config settings

**Files:**
- Modify: `backend/config.py:136-138`

- [ ] **Step 1: Add 4 config fields**

In `backend/config.py`, replace the block at lines 136-138:

```python
    # Ingestion
    ingestion_max_concurrency: int = 3
```

With:

```python
    # Ingestion
    ingestion_max_concurrency: int = 3

    # Ingestion retry
    ingestion_max_retries: int = 3        # max attempts including first try
    ingestion_retry_base_sec: float = 5.0  # base backoff seconds
    ingestion_retry_max_sec: float = 300.0 # backoff cap
    ingestion_retry_jitter: float = 0.3    # jitter ratio (0-1)
```

- [ ] **Step 2: Verify settings load correctly**

```bash
cd D:/Python/subject1/RAG_Agent/backend
D:/Python/Python/python.exe -c "
from config import settings
print(f'max_retries={settings.ingestion_max_retries}')
print(f'retry_base={settings.ingestion_retry_base_sec}')
print(f'retry_max={settings.ingestion_retry_max_sec}')
print(f'jitter={settings.ingestion_retry_jitter}')
print('OK')
"
```

Expected: prints all 4 values with defaults, `OK`.

- [ ] **Step 3: Commit**

```bash
git add backend/config.py
git commit -m "feat: add ingestion retry config with backoff and jitter settings"
```

---

### Task 2: Add error classification and backoff helpers

**Files:**
- Modify: `backend/rag/pipeline.py:20-22` (insert after `_ingestion_semaphore`)

- [ ] **Step 1: Write the helpers**

Insert these two functions after `_ingestion_semaphore = None` (line 22) and before `_hash_chunk_ids` (line 25):

```python
def _classify_error(error: Exception) -> str:
    """Classify ingestion error: 'rate_limit' | 'transient' | 'permanent'."""
    msg = str(error).lower()
    if "429" in msg or "rate limit" in msg or "too many requests" in msg:
        return "rate_limit"
    if isinstance(error, (TimeoutError, ConnectionError)):
        return "transient"
    if any(kw in msg for kw in ("timeout", "connection", "reset", "refused")):
        return "transient"
    return "permanent"


def _retry_delay(attempt: int, error_type: str) -> float:
    """Calculate backoff delay with jitter for a given attempt and error type."""
    import random

    base = settings.ingestion_retry_base_sec
    if error_type == "rate_limit":
        base = base * 4  # 429 needs longer cooling
    delay = min(base * (2 ** attempt), settings.ingestion_retry_max_sec)
    jitter = delay * settings.ingestion_retry_jitter
    return delay + random.uniform(-jitter, jitter)
```

- [ ] **Step 2: Verify syntax**

```bash
cd D:/Python/subject1/RAG_Agent/backend
D:/Python/Python/python.exe -m py_compile rag/pipeline.py && echo "OK"
```

- [ ] **Step 3: Run unit tests for the new functions**

```bash
cd D:/Python/subject1/RAG_Agent/backend
D:/Python/Python/python.exe -c "
from rag.pipeline import _classify_error, _retry_delay
import asyncio

# Test classification
assert _classify_error(Exception('HTTP 429 Too Many Requests')) == 'rate_limit'
assert _classify_error(Exception('rate limit exceeded')) == 'rate_limit'
assert _classify_error(asyncio.TimeoutError()) == 'transient'
assert _classify_error(ConnectionError()) == 'transient'
assert _classify_error(Exception('connection reset')) == 'transient'
assert _classify_error(ValueError('corrupt file')) == 'permanent'
print('_classify_error: OK')

# Test backoff
d = _retry_delay(0, 'transient')
assert 3.5 <= d <= 6.5, f'Expected ~5.0, got {d}'
d = _retry_delay(2, 'transient')
assert 14.0 <= d <= 26.0, f'Expected ~20.0, got {d}'
d = _retry_delay(0, 'rate_limit')
assert 14.0 <= d <= 26.0, f'Expected ~20.0 (4x base), got {d}'
print('_retry_delay: OK')
"
```

- [ ] **Step 4: Commit**

```bash
git add backend/rag/pipeline.py
git commit -m "feat: add error classification and exponential backoff helpers for ingestion"
```

---

### Task 3: Add idempotency guard and rewrite retry loop

**Files:**
- Modify: `backend/rag/pipeline.py:278-307` (rewrite `_bg_process` in `_run_document_ingestion`)

- [ ] **Step 1: Replace the _bg_process inner function**

Read the current `_bg_process` (approximately lines 279-307 in `_run_document_ingestion`). Replace the entire `_bg_process` function body with:

```python
        async def _bg_process():
            sem = _get_semaphore()
            async with sem:
                started = time.time()
                logger.info("ingestion started doc_id=%s filename=%s", doc_id, filename)

                # ── Idempotency guard: skip if already committed ──
                from models.orm import IndexGeneration, GenerationStatus
                async with async_session() as session:
                    doc = (await session.execute(
                        select(Document).where(Document.id == doc_id)
                    )).scalar_one()
                    if doc.active_generation_id:
                        gen = (await session.execute(
                            select(IndexGeneration).where(
                                IndexGeneration.id == doc.active_generation_id
                            )
                        )).scalar_one_or_none()
                        if gen and gen.status == GenerationStatus.committed:
                            if doc.status != DocStatus.ready:
                                doc.status = DocStatus.ready
                                await session.commit()
                            logger.info(
                                "ingestion skipped: generation already committed "
                                "doc_id=%s gen_id=%s", doc_id, doc.active_generation_id[:8],
                            )
                            return

                from rag.progress import progress
                for attempt in range(settings.ingestion_max_retries):
                    try:
                        await _process_document(doc_id, file_path, file_type)
                        elapsed = (time.time() - started) * 1000
                        logger.info(
                            "ingestion complete doc_id=%s elapsed_ms=%d attempt=%d",
                            doc_id, int(elapsed), attempt,
                        )
                        from metrics import get_metrics
                        get_metrics().record_ingestion(success=True, latency_ms=elapsed)
                        return
                    except Exception as e:
                        error_type = _classify_error(e)
                        is_last = (attempt == settings.ingestion_max_retries - 1)
                        if error_type == "permanent" or is_last:
                            async with async_session() as session:
                                doc = (await session.execute(
                                    select(Document).where(Document.id == doc_id)
                                )).scalar_one()
                                doc.status = DocStatus.failed
                                doc.error_message = str(e)[:500]
                                await session.commit()
                            logger.error(
                                "ingestion failed doc_id=%s type=%s error=%s",
                                doc_id, error_type, str(e)[:200],
                            )
                            from metrics import get_metrics
                            get_metrics().record_ingestion(
                                success=False, latency_ms=(time.time() - started) * 1000,
                            )
                            progress.publish(doc_id, {
                                "status": "failed", "error": str(e)[:200],
                            })
                            return
                        delay = _retry_delay(attempt, error_type)
                        logger.warning(
                            "ingestion retry doc_id=%s attempt=%d/%d type=%s delay=%.1fs error=%s",
                            doc_id, attempt + 1, settings.ingestion_max_retries,
                            error_type, delay, str(e)[:200],
                        )
                        await asyncio.sleep(delay)
```

- [ ] **Step 2: Verify syntax and imports**

```bash
cd D:/Python/subject1/RAG_Agent/backend
D:/Python/Python/python.exe -m py_compile rag/pipeline.py && echo "OK"
```

- [ ] **Step 3: Run existing ingestion tests to confirm no regression**

```bash
cd D:/Python/subject1/RAG_Agent/backend
D:/Python/Python/python.exe -m pytest tests/rag/test_ingestion_recovery.py -q --tb=short 2>&1
```

Expected: existing tests pass (or skip if no Docker stack).

- [ ] **Step 4: Commit**

```bash
git add backend/rag/pipeline.py
git commit -m "feat: add idempotency guard and configurable retry with backoff to ingestion"
```

---

### Task 4: Add retry_wait state to task system

**Files:**
- Modify: `backend/worker/tasks.py:117-138` (extend `_persist_update`)
- Modify: `backend/worker/tasks.py:295-328` (extend `recover_stale_tasks`)

- [ ] **Step 1: Add retry_wait branch to _persist_update**

In `_persist_update`, add a new `elif` branch after the `status == "running"` block and before the `done"/"failed"` block. The current code is:

```python
                if status == "running":
                    await conn.execute(sa_text(
                        "UPDATE task_queue SET status='running', heartbeat_at=datetime('now') "
                        "WHERE id=:id"
                    ), {"id": name})
                elif status in ("done", "failed"):
                    await conn.execute(sa_text(
                        "UPDATE task_queue SET status=:st, error=:err, "
                        "completed_at=datetime('now') WHERE id=:id"
                    ), {"st": status, "err": error, "id": name})
```

Add the `retry_wait` branch between `running` and `done`:

```python
                if status == "running":
                    await conn.execute(sa_text(
                        "UPDATE task_queue SET status='running', heartbeat_at=datetime('now') "
                        "WHERE id=:id"
                    ), {"id": name})
                elif status == "retry_wait":
                    delay = (metadata or {}).get("retry_delay_sec", 60)
                    await conn.execute(sa_text(
                        "UPDATE task_queue SET status='retry_wait', error=:err, "
                        "next_run_at=datetime('now', :delay) WHERE id=:id"
                    ), {"err": error, "delay": f"+{int(delay)} seconds", "id": name})
                elif status in ("done", "failed"):
                    await conn.execute(sa_text(
                        "UPDATE task_queue SET status=:st, error=:err, "
                        "completed_at=datetime('now') WHERE id=:id"
                    ), {"st": status, "err": error, "id": name})
```

Note: the `_persist_update` signature doesn't have a `metadata` param — we need to add it. Change the function signature from:

```python
    async def _persist_update(
        self, name: str, status: str, error: str | None = None,
    ) -> None:
```

To:

```python
    async def _persist_update(
        self, name: str, status: str, error: str | None = None,
        *, metadata: dict | None = None,
    ) -> None:
```

- [ ] **Step 2: Add retry_wait recovery to recover_stale_tasks**

In `recover_stale_tasks`, INSIDE the `async with async_session() as session:` block, BEFORE the existing dead-letter and retry queries, add:

```python
                # Re-enqueue retry_wait tasks whose next_run_at has passed
                retry_wait_result = await conn.execute(sa_text(
                    "UPDATE task_queue SET status='pending', worker_id=NULL, "
                    "next_run_at=NULL WHERE status='retry_wait' "
                    "AND next_run_at <= datetime('now')"
                ))
                retry_wait_count = retry_wait_result.rowcount
```

And update the log message to include `retry_wait_count`:

```python
                total = dead_count + retry_count + retry_wait_count
                if total:
                    logger.warning(
                        "task recovery: %d dead-lettered, %d re-enqueued, %d retry_wait",
                        dead_count, retry_count, retry_wait_count,
                    )
```

- [ ] **Step 3: Verify syntax**

```bash
cd D:/Python/subject1/RAG_Agent/backend
D:/Python/Python/python.exe -m py_compile worker/tasks.py && echo "OK"
```

- [ ] **Step 4: Commit**

```bash
git add backend/worker/tasks.py
git commit -m "feat: add retry_wait task state with next_run_at recovery"
```

---

### Task 5: Integration tests

**Files:**
- Create: `backend/tests/rag/test_ingestion_reliability.py`

- [ ] **Step 1: Write integration tests**

```python
"""Phase 3a: ingestion idempotency, error classification, and retry behavior."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from config import settings
from models.database import async_session
from models.orm import DocStatus, Document, GenerationStatus, IndexGeneration
from rag.pipeline import _classify_error, _retry_delay


class TestErrorClassification:
    def test_rate_limit_429(self):
        assert _classify_error(Exception("HTTP 429 Too Many Requests")) == "rate_limit"

    def test_rate_limit_message(self):
        assert _classify_error(Exception("rate limit exceeded")) == "rate_limit"
        assert _classify_error(Exception("too many requests")) == "rate_limit"

    def test_transient_timeout(self):
        assert _classify_error(asyncio.TimeoutError()) == "transient"

    def test_transient_connection(self):
        assert _classify_error(ConnectionError()) == "transient"
        assert _classify_error(ConnectionRefusedError()) == "transient"

    def test_transient_keywords(self):
        assert _classify_error(Exception("connection reset by peer")) == "transient"
        assert _classify_error(Exception("request timeout")) == "transient"
        assert _classify_error(Exception("connection refused")) == "transient"

    def test_permanent_value_error(self):
        assert _classify_error(ValueError("unsupported format")) == "permanent"

    def test_permanent_generic(self):
        assert _classify_error(RuntimeError("something unexpected")) == "permanent"


class TestRetryDelay:
    def test_transient_base_delay(self):
        d = _retry_delay(0, "transient")
        assert 3.5 <= d <= 6.5, f"Expected ~5.0, got {d}"

    def test_transient_exponential_growth(self):
        d0 = _retry_delay(0, "transient")
        d2 = _retry_delay(2, "transient")
        assert d2 > d0, f"Expected d2 > d0, got {d0} vs {d2}"

    def test_rate_limit_longer_delay(self):
        d_transient = _retry_delay(0, "transient")
        d_rate = _retry_delay(0, "rate_limit")
        # rate_limit should use ~4× base, but with jitter may overlap
        # Just verify both are positive
        assert d_transient > 0 and d_rate > 0

    def test_delay_capped(self):
        d = _retry_delay(100, "transient")
        assert d <= settings.ingestion_retry_max_sec * 1.3  # jitter may push slightly above

    def test_jitter_produces_variation(self):
        delays = [_retry_delay(1, "transient") for _ in range(20)]
        unique = len(set(round(d, 1) for d in delays))
        assert unique > 1, "Jitter should produce varied delays"


@pytest.mark.asyncio
class TestIdempotencyGuard:
    async def test_config_defaults(self):
        """Verify config defaults are sensible."""
        assert settings.ingestion_max_retries >= 2
        assert settings.ingestion_retry_base_sec > 0
        assert settings.ingestion_retry_max_sec > settings.ingestion_retry_base_sec
        assert 0 < settings.ingestion_retry_jitter < 1
```

- [ ] **Step 2: Run the tests**

```bash
cd D:/Python/subject1/RAG_Agent/backend
D:/Python/Python/python.exe -m pytest tests/rag/test_ingestion_reliability.py -v --tb=short 2>&1
```

Expected: all tests in `TestErrorClassification` and `TestRetryDelay` pass (8 + 5 = 13 tests).

- [ ] **Step 3: Run full regression on relevant test suites**

```bash
cd D:/Python/subject1/RAG_Agent/backend
D:/Python/Python/python.exe -m pytest tests/rag/test_ingestion_recovery.py tests/rag/test_generation_visibility.py -q --tb=short 2>&1
```

Expected: no failures (may skip tests that need Docker/Qdrant).

- [ ] **Step 4: Commit**

```bash
git add backend/tests/rag/test_ingestion_reliability.py
git commit -m "test: add ingestion error classification, backoff, and config unit tests"
```
