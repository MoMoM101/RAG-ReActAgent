# Phase 7: Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 4 missing metrics (SSE connections, stream events, queue depth, oldest task age) and expose a standard Prometheus `/metrics` endpoint.

**Architecture:** 3 tasks. Task 1 extends `metrics.py` with new counter methods and `_collect_system_state()`. Task 2 creates the `/metrics` FastAPI route. Task 3 adds SSE instrumentation hooks in `chat.py` and writes tests.

**Tech Stack:** Python, FastAPI, Prometheus text format, SQLite (sync sqlite3 for system state queries)

---

## File Structure

```
backend/
  metrics.py          → Modify: +4 methods, _collect_system_state(), Prometheus export
  api/
    metrics.py        → Create: /metrics FastAPI router
  main.py             → Modify: register metrics router (+3 lines)
  api/
    chat.py           → Modify: SSE hook instrumentation (+15 lines)
  tests/
    test_metrics.py   → Create: Prometheus format + counter tests
```

---

### Task 1: Add New Metrics Methods and System State Collection

**Files:**
- Modify: `backend/metrics.py`

- [ ] **Step 1: Add 4 new record methods to MetricsCollector**

Add after the existing `record_phase_timing` method (line ~203), before the `snapshot()` method:

```python
    # ── SSE ─────────────────────────────────────────────────────

    def record_sse_connection(self, event: str):
        """Record SSE connection lifecycle events: open, done, disconnect."""
        with self._lock:
            self.sse_connections[event] += 1

    def record_stream_event(self, event_type: str):
        """Record SSE stream events by type: answer_chunk, sources, verification, done, error."""
        with self._lock:
            self.stream_events[event_type] += 1
```

- [ ] **Step 2: Add new counters to `__init__`**

Add after the existing `phase_timings` field (line ~81):

```python
        # ── SSE ───────────────────────────────────────────────────
        self.sse_connections: dict[str, int] = defaultdict(int)    # open|done|disconnect → count
        self.stream_events: dict[str, int] = defaultdict(int)      # event_type → count
```

- [ ] **Step 3: Add `_collect_system_state()` function (module-level)**

Add before `export_prometheus()` (line ~321):

```python
def _collect_system_state(db_path: str) -> dict:
    """Query system state for gauge metrics (sync sqlite3, not ORM)."""
    import sqlite3
    import time as _time

    result: dict = {"queue_depth": {}, "oldest_task_age_seconds": 0.0}

    try:
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT status, COUNT(*) FROM task_queue "
                "WHERE status IN ('pending','running','retry_wait') "
                "GROUP BY status"
            ).fetchall()
            result["queue_depth"] = {str(row[0]): row[1] for row in rows}

            row = conn.execute(
                "SELECT created_at FROM task_queue "
                "WHERE status IN ('pending','running','retry_wait') "
                "ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
            if row and row[0]:
                try:
                    created = _time.strptime(str(row[0]), "%Y-%m-%d %H:%M:%S")
                    result["oldest_task_age_seconds"] = _time.time() - _time.mktime(created)
                except (ValueError, OverflowError):
                    pass
        finally:
            conn.close()
    except Exception:
        pass

    return result
```

- [ ] **Step 4: Add new metrics to `snapshot()` dict**

Add inside `snapshot()` return dict, before the closing `}`:

```python
                "sse_connections": dict(self.sse_connections),
                "stream_events": dict(self.stream_events),
```

- [ ] **Step 5: Add Prometheus export for new metrics**

In `export_prometheus()`, add after the V4 Phase timing section (before `return`):

```python
    # SSE
    for event, count in snap.get("sse_connections", {}).items():
        lines.append(f'rag_sse_connections_total{{event="{event}"}} {count}')
    for event_type, count in snap.get("stream_events", {}).items():
        lines.append(f'rag_stream_events_total{{type="{event_type}"}} {count}')

    # System state gauges (query DB for live values)
    from config import settings
    db_url = settings.database_url
    if db_url.startswith("sqlite+aiosqlite:///"):
        db_path = db_url[len("sqlite+aiosqlite:///"):]
        if db_path.startswith("./"):
            from pathlib import Path as _Path
            db_path = str((_Path(__file__).resolve().parent / db_path).resolve())
    else:
        db_path = db_url

    state = _collect_system_state(db_path)
    for status, count in state["queue_depth"].items():
        lines.append(f'rag_ingestion_queue_depth{{status="{status}"}} {count}')
    lines.append(f'rag_oldest_task_age_seconds {state["oldest_task_age_seconds"]:.1f}')
```

- [ ] **Step 6: Verify metrics.py imports cleanly**

```bash
cd backend && python -c "from metrics import get_metrics, export_prometheus; print('import OK')"
```

- [ ] **Step 7: Commit**

```bash
git add backend/metrics.py
git commit -m "feat: add SSE, queue depth, oldest task age metrics with system state collection"
```

---

### Task 2: Create /metrics Endpoint

**Files:**
- Create: `backend/api/metrics.py`
- Modify: `backend/main.py`

- [ ] **Step 1: Read main.py to find router registration pattern**

Read `backend/main.py` around lines 340-380 to see how existing routers are registered.

- [ ] **Step 2: Create api/metrics.py**

```python
# backend/api/metrics.py
"""Prometheus /metrics endpoint."""
from fastapi import APIRouter, Response

from metrics import export_prometheus

router = APIRouter(tags=["metrics"])


@router.get("/metrics", include_in_schema=False)
async def metrics():
    """Return all metrics in Prometheus text format."""
    return Response(content=export_prometheus(), media_type="text/plain")
```

- [ ] **Step 3: Register router in main.py**

Add at the end of the router registration section (after the last `app.include_router(...)`):

```python
# ── Metrics ──────────────────────────────────────────────
from api.metrics import router as metrics_router
app.include_router(metrics_router)
```

Note: metrics endpoint does NOT require authentication (it's read-only system status, and Prometheus needs to scrape it).

- [ ] **Step 4: Test /metrics endpoint**

```bash
cd backend && AUTO_MIGRATE=1 python -c "
import asyncio
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)
# Need to trigger lifespan to initialize DB
with client:
    # Record some events first
    from metrics import get_metrics
    get_metrics().record_sse_connection('open')
    get_metrics().record_sse_connection('done')
    get_metrics().record_stream_event('answer_chunk')
    get_metrics().record_stream_event('done')

    r = client.get('/metrics')
    assert r.status_code == 200
    assert 'text/plain' in r.headers['content-type']
    body = r.text
    assert 'rag_sse_connections_total{event=\"open\"} 1' in body
    assert 'rag_sse_connections_total{event=\"done\"} 1' in body
    assert 'rag_stream_events_total{type=\"answer_chunk\"} 1' in body
    assert 'rag_ingestion_queue_depth' in body
    assert 'rag_oldest_task_age_seconds' in body
    assert 'http_requests_total' in body
    print('ALL /metrics assertions passed')
"
- [ ] **Step 5: Commit**

```bash
git add backend/api/metrics.py backend/main.py
git commit -m "feat: add /metrics Prometheus endpoint"
```

---

### Task 3: SSE Instrumentation and Tests

**Files:**
- Modify: `backend/api/chat.py`
- Create: `backend/tests/test_metrics.py`

- [ ] **Step 1: Add SSE hooks to chat.py**

Read `backend/api/chat.py` to find the `sse_generator` function (around line 125). Add instrumentation at the start and in the event loop.

At the top of `sse_generator`, after `cancelled = asyncio.Event()`:

```python
    from metrics import get_metrics
    get_metrics().record_sse_connection("open")
```

After the yield line `yield f"event: {event_type}\ndata: {data}\n\n"` (around line 186), add counting:

```python
        # After yield: count the event type
        get_metrics().record_stream_event(event_type)
```

In the `if event_type == "done":` block, after `_save_messages` succeeds, add:

```python
            get_metrics().record_sse_connection("done")
```

- [ ] **Step 2: Write metrics tests**

Create `backend/tests/test_metrics.py`:

```python
# backend/tests/test_metrics.py
import pytest
from metrics import get_metrics, export_prometheus, MetricsCollector


@pytest.fixture
def fresh_collector():
    """Return a clean MetricsCollector for isolated testing."""
    return MetricsCollector()


class TestSseMetrics:
    def test_record_sse_connection_increments(self, fresh_collector):
        fresh_collector.record_sse_connection("open")
        fresh_collector.record_sse_connection("open")
        fresh_collector.record_sse_connection("done")
        fresh_collector.record_sse_connection("disconnect")

        snap = fresh_collector.snapshot()
        assert snap["sse_connections"]["open"] == 2
        assert snap["sse_connections"]["done"] == 1
        assert snap["sse_connections"]["disconnect"] == 1

    def test_record_stream_event_increments(self, fresh_collector):
        fresh_collector.record_stream_event("answer_chunk")
        fresh_collector.record_stream_event("answer_chunk")
        fresh_collector.record_stream_event("sources")
        fresh_collector.record_stream_event("done")

        snap = fresh_collector.snapshot()
        assert snap["stream_events"]["answer_chunk"] == 2
        assert snap["stream_events"]["sources"] == 1
        assert snap["stream_events"]["done"] == 1


class TestPrometheusExport:
    def test_export_contains_sse_metrics(self, fresh_collector):
        import metrics as _m
        old = _m._collector
        _m._collector = fresh_collector
        try:
            fresh_collector.record_sse_connection("open")
            fresh_collector.record_stream_event("answer_chunk")

            output = export_prometheus()
            assert 'rag_sse_connections_total{event="open"} 1' in output
            assert 'rag_stream_events_total{type="answer_chunk"}' in output
        finally:
            _m._collector = old

    def test_export_contains_http_metrics(self, fresh_collector):
        import metrics as _m
        old = _m._collector
        _m._collector = fresh_collector
        try:
            fresh_collector.record_request("GET", "/api/chat")
            fresh_collector.record_latency(100.0)

            output = export_prometheus()
            assert 'http_requests_total' in output
            assert 'http_latency_ms' in output
        finally:
            _m._collector = old

    def test_export_contains_system_state_gauges(self, fresh_collector):
        import metrics as _m
        old = _m._collector
        _m._collector = fresh_collector
        try:
            output = export_prometheus()
            assert 'rag_ingestion_queue_depth' in output
            assert 'rag_oldest_task_age_seconds' in output
        finally:
            _m._collector = old

    def test_export_is_valid_prometheus_format(self, fresh_collector):
        import metrics as _m
        old = _m._collector
        _m._collector = fresh_collector
        try:
            fresh_collector.record_request("GET", "/test")
            output = export_prometheus()
            for line in output.strip().split("\n"):
                if line and not line.startswith("#"):
                    assert " " in line or "\t" in line, f"Invalid line: {line}"
        finally:
            _m._collector = old
```

- [ ] **Step 3: Run tests**

```bash
cd backend && python -m pytest tests/test_metrics.py -v
```

Expected: 7/7 PASS

- [ ] **Step 4: Run full regression**

```bash
cd backend && python -m pytest -q \
  --ignore=tests/api/test_restore_integration.py \
  --ignore=tests/api/test_auth.py \
  --ignore=tests/api/test_users.py \
  --ignore=tests/test_e2e_mvp.py \
  --ignore=tests/reranker/
```

Expected: 665+ passed (658 existing + 7 new).

- [ ] **Step 5: Commit**

```bash
git add backend/api/chat.py backend/tests/test_metrics.py
git commit -m "feat: add SSE instrumentation hooks and metrics tests"
```
