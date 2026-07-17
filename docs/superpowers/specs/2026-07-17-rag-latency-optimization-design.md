# RAG Tail Latency Optimization — Design (Phase 4)

> Date: 2026-07-17
> Phase: 4 (per `NEXT_PHASE_OPTIMIZATION_EXECUTION_PLAN_2026-07-17.md`)
> Status: approved, ready for implementation planning

## 1. Goal

Reduce RAG P95/P99 latency and user-visible TTFT by wiring per-phase timeout budgets, enabling already-built optimizations (answer cache, stream verify, query rewrite), and running a controlled A/B comparison across 4 config profiles against the 93-sample grounded-answer evaluation dataset.

**Hard constraint**: Quality gate must pass for every profile. No disabling the verifier, hiding citation gaps, or lowering support thresholds.

## 2. Scope — Three Changes

### 2.1 Per-phase timeout budget (new code)

Add 5 config settings and wire `asyncio.wait_for()` around each RAG phase in the agent loop.

Config (`config.py`):
```python
rag_timeout_intent: float = 5.0
rag_timeout_retrieval: float = 10.0
rag_timeout_generation: float = 60.0
rag_timeout_verification: float = 5.0
rag_timeout_repair: float = 10.0
```

Wiring (`agent/loop.py`): wrap intent classification, retrieval, generation, verification, and repair calls with `asyncio.wait_for()`. On timeout, fall back to safe defaults (knowledge_qa intent, empty retrieval, partial answer without repair) and record a degradation metric.

### 2.2 Enable answer cache (config change)

Change `rag_answer_cache_enabled` default from `false` to `true`. The cache implementation is complete and production-ready. Cache invalidation on document changes is already wired. Cache key includes normalized query, retrieval hash, collection version, model, prompt version, context hash, and grounding policy version.

### 2.3 A/B latency comparison tool (new script)

New script `backend/tests/run_latency_ab.py` that:
1. Runs the 93-sample grounded-answer eval under 4 config profiles
2. Records per-sample TTFT, rag_total, phase timings, and quality scores
3. Computes P50/P95/P99 latency and quality metrics per profile
4. Outputs comparison JSON and markdown report

Four profiles:

| Profile | Config |
|---|---|
| `control` | Current defaults (cache off, stream off, rewrite off, deterministic repair on) |
| `cache_only` | `rag_answer_cache_enabled=true` |
| `cache_stream_rewrite` | cache + `grounding_stream_verify_enabled=true` + `query_rewrite_enabled=true` |
| `combined` | All above + per-phase timeout budgets |

The eval runner needs a small extension: `--env-override KEY=VALUE` flag so profiles can be switched without restarting the server.

## 3. Files

| File | Change |
|---|---|
| `backend/config.py` | Add 5 timeout settings; change cache default to true |
| `backend/agent/loop.py` | Wire `asyncio.wait_for()` at 5 phase boundaries |
| `backend/tests/run_grounded_answer_eval.py` | Add `--env-override` flag |
| `backend/tests/run_latency_ab.py` | New: A/B comparison runner |

## 4. Quality Gates (Non-Negotiable)

| Metric | Minimum |
|---|---|
| Faithfulness | ≥ 98% |
| Citation precision | ≥ 95% |
| Citation recall | ≥ 95% |
| P95 TTFT (combined profile) | ≤ 2.5s or ≥ 20% improvement vs control |
| P95 rag_total (combined profile) | ≤ 4.0s or ≥ 20% improvement vs control |

## 5. Acceptance Criteria

- All 4 profiles complete the 93-sample eval
- Every profile passes quality gates (faithfulness, citation precision, citation recall)
- Comparison report shows P50/P95/P99 for each profile with clear improvement/regression
- At least one profile achieves P95 TTFT ≤ 2.5s or 20% reduction from control
- Combined profile's slowest 10 requests have identifiable root causes (not "network fluctuation")
- Per-phase timeouts fire gracefully (no crash, no hang) under intentional slow-LLM injection

## 6. Out of Scope

- Replacing the 93-sample eval with a larger dataset
- Production rollout or traffic splitting
- Grafana dashboard updates
- Prompt tuning (already optimized in the existing eval)
- Model switching (gpt-4o remains the baseline)
