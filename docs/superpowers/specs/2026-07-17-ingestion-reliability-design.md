# Ingestion Reliability Hardening — Design (Phase 3a)

> Date: 2026-07-17
> Phase: 3a (per `NEXT_PHASE_OPTIMIZATION_EXECUTION_PLAN_2026-07-17.md`)
> Status: approved, ready for implementation planning

## 1. Goal

Harden backend document ingestion so that batch uploads survive transient failures (embedding 429, network timeouts) with configurable exponential backoff and jitter, while permanently failing fast on business errors (corrupt files, unsupported formats). Ensure idempotency by skipping re-processing when a committed generation already exists.

## 2. Scope

Backend-only. Four focused changes:

1. **Generation-aware idempotency** — skip ingestion when document already has a committed generation
2. **Error classification** — rate limit (429), transient (network/timeout), permanent (business error)
3. **Configurable retry with backoff + jitter** — replace hardcoded `range(2)` + `sleep(10)` with config-driven exponential backoff
4. **Task state machine** — formalize `retry_wait` state with `next_run_at`, integrate with existing stale-task recovery

## 3. Files

| File | Change |
|---|---|
| `backend/config.py` | Add 4 ingestion retry settings |
| `backend/rag/pipeline.py` | Idempotency check, error classification, backoff retry loop |
| `backend/worker/tasks.py` | `retry_wait` status support in `_persist_update`, `recover_stale_tasks` |

## 4. Config (config.py)

```python
ingestion_max_retries: int = 3        # max attempts including first try
ingestion_retry_base_sec: float = 5.0  # base backoff seconds
ingestion_retry_max_sec: float = 300.0 # backoff cap
ingestion_retry_jitter: float = 0.3    # jitter ratio (0-1)
```

## 5. Idempotency Guard (pipeline.py)

In `_run_document_ingestion._bg_process`, after acquiring the semaphore and before calling `_process_document`:

1. Load the document record
2. If `active_generation_id` is set, load the generation
3. If generation status is `committed`, ensure document status is `ready`, log, and return immediately

This prevents re-processing a document that was already successfully indexed by a previous attempt or a concurrent worker.

## 6. Error Classification (pipeline.py)

```python
def _classify_error(error: Exception) -> str:
    # Returns: "rate_limit" | "transient" | "permanent"
```

- `rate_limit`: HTTP 429 or rate-limit keywords in error text
- `transient`: TimeoutError, ConnectionError, or timeout/connection/reset keywords
- `permanent`: everything else (corrupt files, unsupported formats, validation errors)

## 7. Backoff Retry (pipeline.py)

```python
def _retry_delay(attempt: int, error_type: str) -> float:
```

- Rate-limit errors: base delay × 4, capped at `ingestion_retry_max_sec`
- Transient errors: standard exponential backoff `base × 2^attempt`, capped
- Both: random jitter ± `ingestion_retry_jitter` ratio

The `_bg_process` retry loop uses `settings.ingestion_max_retries` instead of hardcoded `range(2)`:
- `rate_limit` or `transient` + not last attempt → sleep `_retry_delay()` and retry
- `permanent` or last attempt → mark document `failed`, record error, stop

## 8. Task State Machine (worker/tasks.py)

Six formal states using existing `task_queue` columns:

```
pending → running → done
pending → running → retry_wait → pending  (next_run_at expired, auto-recovered)
pending → running → failed                 (permanent error)
pending → running → dead_letter            (attempt >= max_attempts)
```

Changes:

**`_persist_update`**: Add `retry_wait` branch that sets `next_run_at = datetime('now', '+<delay_sec> seconds')`.

**`recover_stale_tasks`**: Add query that transitions `retry_wait` tasks with `next_run_at <= now` back to `pending` (only if `attempt < max_attempts`).

The in-memory retry loop in `_pipeline._bg_process` remains the primary retry driver. The DB `retry_wait` state is written only when the in-memory retries are exhausted, so that worker restart recovery can pick up pending retries.

## 9. Test Plan

1. **Unit: error classification** — parameterized: 429 response → `rate_limit`, TimeoutError → `transient`, ValueError → `permanent`
2. **Unit: backoff calculation** — verify exponential growth, cap, jitter range
3. **Integration: idempotency guard** — upload same file twice, verify second attempt skips with "already committed" log
4. **Integration: retry with mock failure** — inject transient failure twice, verify document eventually reaches `ready`
5. **Integration: permanent failure** — inject permanent error, verify document reaches `failed` on first attempt
6. **Integration: rate-limit backoff** — verify 429 errors get 4× base delay

## 10. Acceptance Criteria

- 50-file batch upload with 3 artificially injected transient failures per file: ≥ 95% ready, 0 stuck in non-terminal state
- Duplicate upload of ready document: second attempt skips silently, document stays ready
- Corrupt file: marked `failed` on first attempt, no retry
- `retry_wait` tasks recovered after restart via `recover_stale_tasks`
- All existing ingestion tests pass (zero regression)

## 11. Out of Scope

- Frontend retry button / dead-letter UI (Phase 3b)
- SSE disconnect recovery (Phase 3b)
- Worker-level task polling loop (current on-demand model is sufficient for single-instance)
- PostgreSQL migration (Phase 6)
