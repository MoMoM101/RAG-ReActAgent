# V4 Gray Release Runbook

> Date: 2026-07-15
> Base config: V3.3 quality gates passed on 93 queries
> Feature flags: all default-off, individually toggleable

## Feature Flag Inventory

| Flag | Default | Risk | Rollback |
|---|---|---|---|
| `GROUNDING_DETERMINISTIC_REPAIR_ENABLED` | true | Low | Set to false → back to V3.3 LLM repair |
| `GROUNDING_REPAIR_ENABLED` | true | Low | Set to false → skip repairs entirely |
| `RAG_ANSWER_CACHE_ENABLED` | false | Medium | Set to false → bypass cache |
| `GROUNDING_STREAM_VERIFY_ENABLED` | false | High | Set to false → full buffer before send |
| `RAG_GENERATION_MAX_TOKENS` | 0 (unlimited) | Medium | Set to 0 → no budget |
| `GROUNDING_REPAIR_MODEL` | "" (use primary) | Medium | Set to "" → use primary LLM |

## Phase 1: Shadow (current → +1 day)

**Setup:**
```bash
# In .env or via API
GROUNDING_STREAM_VERIFY_ENABLED=false
RAG_ANSWER_CACHE_ENABLED=false
RAG_GENERATION_MAX_TOKENS=0
```

**Actions:**
1. Run full 93-query evaluation with current V3.3 config → save baseline
2. Enable V4 deterministic repair only (already default)
3. Run full 93-query evaluation with V4 config → compare
4. Verify: repair_trigger_rate drops vs V3.3, quality gates unchanged

**Gate:** All quality metrics within 2pp of V3.3 baseline.

## Phase 2: Internal (day 2–3)

**Setup:**
```bash
GROUNDING_STREAM_VERIFY_ENABLED=true
RAG_ANSWER_CACHE_ENABLED=false
```

**Actions:**
1. Enable for dev-team conversation IDs only
2. Monitor Grafana panels for 8 business hours
3. Collect TTFT P50/P95 from `rag_phase_timing_ms` metric
4. Verify no `answer_chunk` is sent before its unit passes verification

**Gate:** TTFT P50 ≤1000ms, P95 ≤2500ms. Zero unverified chunks sent.

## Phase 3: 5% Traffic (day 4–5)

**Setup:**
```bash
GROUNDING_STREAM_VERIFY_ENABLED=true
RAG_ANSWER_CACHE_ENABLED=true
RAG_ANSWER_CACHE_TTL_SECONDS=300
```

**Actions:**
1. Route 5% of requests to V4 via request-id hash
2. Monitor for 2 business days
3. Check: HTTP 5xx rate, SSE interrupt rate, cache hit rate
4. Check: `rag_answer_cache_total{result="stale"}` stays near 0

**Gate:** No increase in 5xx/SSE interrupt rate. Cache hit rate > 0.

## Phase 4: 25% Traffic (day 6–8)

**Setup:** Same as Phase 3.

**Actions:**
1. Increase to 25% traffic
2. Monitor P95 latency, error rate, user cancel rate
3. Check concurrent citation_id isolation via `rag_stream_unit_total`
4. Run the V4 slow-query regression set against production config

**Gate:** P95 latency ≤5000ms. Cancel rate ≤ baseline. No citation_id cross-talk.

## Phase 5: 50% → 100% (day 9–14)

Same gates, progressively increase traffic share.

## Auto-Rollback Triggers

Any of these triggers immediate feature-flag off:

| Condition | Severity | Action |
|---|---|---|
| Citation precision < 95% | Critical | Disable stream verify + deterministic repair |
| Citation recall < 95% | Critical | Disable stream verify |
| Abstention accuracy < 98% | Critical | Disable stream verify |
| Unverified claim sent to client | Critical | Disable stream verify immediately |
| Cache returns unauthorized source | Critical | Disable cache immediately |
| P95 > 5000ms for 15+ minutes | Warning | Disable stream verify |
| SSE error rate spike > 2x | Warning | Disable stream verify |
| User cancel rate > 2x baseline | Warning | Investigate; disable if sustained |

## Rollback Order

1. `RAG_ANSWER_CACHE_ENABLED=false` (safest, instant)
2. `GROUNDING_STREAM_VERIFY_ENABLED=false` (falls back to full buffer)
3. `GROUNDING_DETERMINISTIC_REPAIR_ENABLED=false` (falls back to V3.3 LLM repair)
4. `GROUNDING_REPAIR_ENABLED=false` (skips all repair, last resort)
