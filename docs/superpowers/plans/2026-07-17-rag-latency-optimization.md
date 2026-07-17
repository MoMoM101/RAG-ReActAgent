# RAG Tail Latency Optimization — Implementation Plan (Phase 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce RAG P95/P99 latency by wiring per-phase timeout budgets, enabling existing optimizations (cache, stream verify), and building an A/B comparison tool for the 93-sample grounded-answer evaluation.

**Architecture:** Three changes to production code (config, agent loop), one extension to existing eval runner, one new A/B comparison script. All optimization toggles remain feature-flagged.

**Tech Stack:** Python 3.12+, asyncio, pytest

---

### Task 1: Per-phase timeout budgets

**Files:**
- Modify: `backend/config.py` (add 5 settings)
- Modify: `backend/agent/loop.py` (wire `asyncio.wait_for` at 4 phase boundaries)

- [ ] **Step 1: Add timeout config settings**

In `backend/config.py`, after the existing performance gates section (~line 125), add:

```python
    # Phase-level timeout budgets (seconds, 0 = unlimited)
    rag_timeout_intent: float = 5.0       # intent classification
    rag_timeout_retrieval: float = 10.0   # retrieval (semantic + BM25 + rerank)
    rag_timeout_generation: float = 60.0  # LLM draft generation
    rag_timeout_verification: float = 5.0 # verification
    rag_timeout_repair: float = 10.0      # LLM repair (reuses grounding_repair_timeout)
```

- [ ] **Step 2: Wire timeout at intent phase**

In `backend/agent/loop.py`, around line 79, wrap the LLM classification:

```python
    # Existing code:
    hint = classify_intent(user_message, conversation_history)
    if hint.intent == "_llm_needed":
        hint = await llm_classify(user_message, conversation_history)
    _record_phase("rag_intent")
```

Add timeout wrapper:

```python
    hint = classify_intent(user_message, conversation_history)
    if hint.intent == "_llm_needed":
        try:
            hint = await asyncio.wait_for(
                llm_classify(user_message, conversation_history),
                timeout=settings.rag_timeout_intent,
            )
        except asyncio.TimeoutError:
            logger.warning("intent classification timed out, defaulting to knowledge_qa")
            hint = classify_intent(user_message, conversation_history)
            hint.intent = "knowledge_qa"
    _record_phase("rag_intent")
```

- [ ] **Step 3: Wire timeout at retrieval phase**

Find `hybrid_search` call in loop.py. Wrap the search + re-rank calls with `asyncio.wait_for()`. The retrieval is called via `search_docs` tool or directly. The key call site is inside the tool execution. Add timeout in the `search_docs` tool or in the agent loop at the point where retrieval is dispatched.

In `backend/agent/tools.py`, find the `search_docs` handler and add:

```python
        try:
            results = await asyncio.wait_for(
                hybrid_search(query, top_k=settings.retrieval_top_k),
                timeout=settings.rag_timeout_retrieval,
            )
        except asyncio.TimeoutError:
            logger.warning("retrieval timed out for query: %s", query[:100])
            results = []
```

- [ ] **Step 4: Wire timeout at generation phase**

Find the main LLM generation call in loop.py. In the primary generation path (not cache hit), wrap the `llm.chat_stream()` call:

```python
    try:
        async for chunk in asyncio.wait_for(
            llm.chat_stream(messages=chat_messages, tools=tools, max_tokens=max_tok),
            timeout=settings.rag_timeout_generation,
        ):
            # existing chunk processing
            ...
    except asyncio.TimeoutError:
        logger.warning("generation timed out, yielding partial content")
        # yield any accumulated content + fallback
```

Note: `asyncio.wait_for` on an async generator may need a helper pattern. Use a sentinel-based approach:

```python
    async def _generation_with_timeout():
        async for chunk in llm.chat_stream(...):
            yield chunk

    gen = _generation_with_timeout()
    while True:
        try:
            chunk = await asyncio.wait_for(gen.__anext__(), timeout=settings.rag_timeout_generation)
            # process chunk
        except StopAsyncIteration:
            break
        except asyncio.TimeoutError:
            # partial content fallback
            break
```

- [ ] **Step 5: Wire timeout at verification phase**

Wrap `verify_answer()` call (around line 900-910 in loop.py):

```python
    try:
        verification = await asyncio.wait_for(
            verify_answer(...),
            timeout=settings.rag_timeout_verification,
        )
        _timing["rag_verification"] = (time.perf_counter() - _verification_started) * 1000
    except asyncio.TimeoutError:
        logger.warning("verification timed out")
        verification = {"status": "unverified", "error": "timeout"}
        _timing["rag_verification"] = settings.rag_timeout_verification * 1000
```

- [ ] **Step 6: Verify syntax and existing tests**

```bash
cd D:/Python/subject1/RAG_Agent/backend
D:/Python/Python/python.exe -m py_compile config.py && echo "config OK"
D:/Python/Python/python.exe -m py_compile agent/loop.py && echo "loop OK"
D:/Python/Python/python.exe -m py_compile agent/tools.py && echo "tools OK"
```

- [ ] **Step 7: Commit**

```bash
git add backend/config.py backend/agent/loop.py backend/agent/tools.py
git commit -m "feat: add per-phase timeout budgets for intent, retrieval, generation, verification"
```

---

### Task 2: Enable answer cache by default

**Files:**
- Modify: `backend/config.py:113`

- [ ] **Step 1: Change default**

Change line 113 from:
```python
    rag_answer_cache_enabled: bool = False
```
To:
```python
    rag_answer_cache_enabled: bool = True
```

- [ ] **Step 2: Verify**

```bash
cd D:/Python/subject1/RAG_Agent/backend
D:/Python/Python/python.exe -c "from config import settings; assert settings.rag_answer_cache_enabled; print('cache enabled:', settings.rag_answer_cache_enabled)"
```

- [ ] **Step 3: Commit**

```bash
git add backend/config.py
git commit -m "feat: enable answer cache by default (TTL=300s, max=1000 entries)"
```

---

### Task 3: Add --env-override to evaluation runner

**Files:**
- Modify: `backend/tests/run_grounded_answer_eval.py`

- [ ] **Step 1: Add --env-override argument**

In the argparse section, add:

```python
    parser.add_argument("--env-override", action="append", default=[],
                        help="Override config via env var, e.g. KEY=VALUE")
```

- [ ] **Step 2: Apply overrides before config import**

At script startup, before importing config (around line 12-20):

```python
    # Parse args early for env overrides
    import argparse as _ap
    _pre_parser = _ap.ArgumentParser(add_help=False)
    _pre_parser.add_argument("--env-override", action="append", default=[])
    _pre_args, _ = _pre_parser.parse_known_args()
    for override in _pre_args.env_override:
        if "=" in override:
            key, value = override.split("=", 1)
            os.environ[key] = value
```

- [ ] **Step 3: Verify**

```bash
cd D:/Python/subject1/RAG_Agent/backend
D:/Python/Python/python.exe -c "
import os, sys
sys.argv = ['test', '--env-override', 'RAG_ANSWER_CACHE_ENABLED=true']
# re-run the pre-parser logic
import argparse as _ap
_pre_parser = _ap.ArgumentParser(add_help=False)
_pre_parser.add_argument('--env-override', action='append', default=[])
_pre_args, _ = _pre_parser.parse_known_args()
for override in _pre_args.env_override:
    if '=' in override:
        key, value = override.split('=', 1)
        os.environ[key] = value
print('RAG_ANSWER_CACHE_ENABLED =', os.environ.get('RAG_ANSWER_CACHE_ENABLED'))
assert os.environ['RAG_ANSWER_CACHE_ENABLED'] == 'true'
print('OK')
"
```

- [ ] **Step 4: Commit**

```bash
git add backend/tests/run_grounded_answer_eval.py
git commit -m "feat: add --env-override flag to evaluation runner for A/B profile testing"
```

---

### Task 4: A/B latency comparison runner

**Files:**
- Create: `backend/tests/run_latency_ab.py`

- [ ] **Step 1: Write the A/B comparison runner**

```python
"""A/B latency comparison across config profiles using grounded-answer eval.

Runs the 93-sample evaluation under 4 config profiles, records per-sample
timing and quality data, and produces comparison JSON + markdown report.

Usage:
  python tests/run_latency_ab.py --profiles control cache_only combined
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROFILES: dict[str, dict[str, str]] = {
    "control": {},
    "cache_only": {
        "RAG_ANSWER_CACHE_ENABLED": "true",
    },
    "cache_stream_rewrite": {
        "RAG_ANSWER_CACHE_ENABLED": "true",
        "GROUNDING_STREAM_VERIFY_ENABLED": "true",
        "QUERY_REWRITE_ENABLED": "true",
    },
    "combined": {
        "RAG_ANSWER_CACHE_ENABLED": "true",
        "GROUNDING_STREAM_VERIFY_ENABLED": "true",
        "QUERY_REWRITE_ENABLED": "true",
        "RAG_TIMEOUT_INTENT": "5.0",
        "RAG_TIMEOUT_RETRIEVAL": "10.0",
        "RAG_TIMEOUT_GENERATION": "60.0",
        "RAG_TIMEOUT_VERIFICATION": "5.0",
        "RAG_TIMEOUT_REPAIR": "10.0",
    },
}

QUALITY_GATES: dict[str, tuple[float, str]] = {
    "avg_faithfulness": (0.98, ">="),
    "avg_citation_precision": (0.95, ">="),
    "avg_citation_recall": (0.95, ">="),
}


def _p(arr: list[float], pct: float) -> float:
    if not arr:
        return 0.0
    s = sorted(arr)
    return s[min(int(len(s) * pct / 100), len(s) - 1)]


def run_profile(profile_name: str, env_overrides: dict[str, str],
                output_dir: Path,
                eval_script: Path) -> dict[str, Any]:
    print(f"\n{'=' * 60}")
    print(f"  Profile: {profile_name}")
    print(f"{'=' * 60}")

    env_args = []
    for k, v in env_overrides.items():
        env_args.extend(["--env-override", f"{k}={v}"])

    output_file = output_dir / f"{profile_name}.json"
    cmd = [
        sys.executable, str(eval_script),
        "--output", str(output_file),
    ] + env_args

    t0 = time.monotonic()
    result = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.monotonic() - t0

    if result.returncode != 0:
        print(f"[FAIL] {profile_name} (exit {result.returncode})")
        print(result.stderr[-2000:])
        return {"profile": profile_name, "error": result.stderr[-2000:], "elapsed_s": elapsed}

    print(f"[OK] {profile_name} ({elapsed:.0f}s)")

    data = json.loads(output_file.read_text(encoding="utf-8"))
    return {
        "profile": profile_name,
        "elapsed_s": round(elapsed, 1),
        "total_samples": len(data.get("results", [])),
        "data": data,
    }


def compute_metrics(profile_result: dict) -> dict:
    results = profile_result.get("data", {}).get("results", [])
    if not results:
        return {"profile": profile_result["profile"], "error": "no results"}

    ttf_ts = [r.get("visible_ttft_ms", 0) for r in results if r.get("visible_ttft_ms")]
    totals = [r.get("rag_total_ms", 0) for r in results if r.get("rag_total_ms")]
    faiths = [r.get("faithfulness", 0) for r in results if r.get("faithfulness") is not None]
    cprec = [r.get("citation_precision", 0) for r in results if r.get("citation_precision") is not None]
    crec = [r.get("citation_recall", 0) for r in results if r.get("citation_recall") is not None]

    return {
        "profile": profile_result["profile"],
        "samples": len(results),
        "ttft_p50": round(_p(ttf_ts, 50) / 1000, 2),
        "ttft_p95": round(_p(ttf_ts, 95) / 1000, 2),
        "ttft_p99": round(_p(ttf_ts, 99) / 1000, 2),
        "rag_total_p50": round(_p(totals, 50) / 1000, 2),
        "rag_total_p95": round(_p(totals, 95) / 1000, 2),
        "rag_total_p99": round(_p(totals, 99) / 1000, 2),
        "avg_faithfulness": round(sum(faiths) / max(len(faiths), 1), 4),
        "avg_citation_precision": round(sum(cprec) / max(len(cprec), 1), 4),
        "avg_citation_recall": round(sum(crec) / max(len(crec), 1), 4),
    }


def check_quality_gates(metrics: dict) -> list[str]:
    failures = []
    for key, (threshold, op) in QUALITY_GATES.items():
        value = metrics.get(key, 0)
        if op == ">=" and value < threshold:
            failures.append(f"{key}={value:.4f} < {threshold}")
    return failures


def generate_report(all_metrics: list[dict], output_dir: Path, date_str: str):
    output_dir.mkdir(parents=True, exist_ok=True)

    # Write comparison.json
    comp_path = output_dir / "comparison.json"
    comp_path.write_text(json.dumps(all_metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    # Find control
    control = next((m for m in all_metrics if m["profile"] == "control"), None)

    # Write report.md
    lines = [
        f"# RAG Latency A/B Comparison — {date_str}",
        "",
        "## Latency Summary (seconds)",
        "",
        "| Profile | Samples | TTFT P50 | TTFT P95 | TTFT P99 | Total P50 | Total P95 | Total P99 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for m in all_metrics:
        lines.append(
            f"| {m['profile']} | {m.get('samples', 0)} | "
            f"{m.get('ttft_p50', '-')} | {m.get('ttft_p95', '-')} | {m.get('ttft_p99', '-')} | "
            f"{m.get('rag_total_p50', '-')} | {m.get('rag_total_p95', '-')} | {m.get('rag_total_p99', '-')} |"
        )

    lines += [
        "",
        "## Quality Summary",
        "",
        "| Profile | Faithfulness | Citation Precision | Citation Recall | Quality Gate |",
        "|---|---:|---:|---:|---:|",
    ]
    for m in all_metrics:
        failures = check_quality_gates(m)
        gate = "PASS" if not failures else f"FAIL: {'; '.join(failures)}"
        lines.append(
            f"| {m['profile']} | {m.get('avg_faithfulness', '-')} | "
            f"{m.get('avg_citation_precision', '-')} | "
            f"{m.get('avg_citation_recall', '-')} | {gate} |"
        )

    if control:
        lines += [
            "",
            "## Improvement vs Control",
            "",
            "| Profile | TTFT P95 Δ | Total P95 Δ | Quality Δ |",
            "|---|---:|---:|---:|",
        ]
        for m in all_metrics:
            if m["profile"] == "control":
                continue
            ttft_delta = (m.get("ttft_p95", 0) - control.get("ttft_p95", 0)) / max(control.get("ttft_p95", 0.001), 0.001) * 100
            total_delta = (m.get("rag_total_p95", 0) - control.get("rag_total_p95", 0)) / max(control.get("rag_total_p95", 0.001), 0.001) * 100
            faith_delta = (m.get("avg_faithfulness", 0) - control.get("avg_faithfulness", 0)) * 100
            lines.append(
                f"| {m['profile']} | {ttft_delta:+.1f}% | {total_delta:+.1f}% | {faith_delta:+.2f}pp |"
            )

    report_path = output_dir / "report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport: {report_path.resolve()}")
    print(f"Comparison: {comp_path.resolve()}")


def main():
    parser = argparse.ArgumentParser(description="RAG latency A/B comparison")
    parser.add_argument("--profiles", nargs="*",
                        default=["control", "cache_only", "cache_stream_rewrite", "combined"],
                        help="Profiles to run")
    parser.add_argument("--output-dir", default="artifacts/latency_ab")
    parser.add_argument("--eval-script",
                        default="tests/run_grounded_answer_eval.py",
                        help="Path to evaluation script")
    args = parser.parse_args()

    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    output_dir = Path(args.output_dir) / date_str
    output_dir.mkdir(parents=True, exist_ok=True)
    eval_script = Path(args.eval_script)

    if not eval_script.exists():
        print(f"Eval script not found: {eval_script}", file=sys.stderr)
        sys.exit(1)

    all_metrics = []
    for profile_name in args.profiles:
        if profile_name not in PROFILES:
            print(f"Unknown profile: {profile_name}", file=sys.stderr)
            continue
        env_overrides = PROFILES[profile_name]
        result = run_profile(profile_name, env_overrides, output_dir, eval_script)
        metrics = compute_metrics(result)
        all_metrics.append(metrics)

        failures = check_quality_gates(metrics)
        if failures:
            print(f"  Quality gate FAIL for {profile_name}: {'; '.join(failures)}")
        else:
            print(f"  Quality gate PASS for {profile_name}")

    if all_metrics:
        generate_report(all_metrics, output_dir, date_str)

    # Exit non-zero if any quality gate failed
    any_failed = any(check_quality_gates(m) for m in all_metrics)
    if any_failed:
        print("\nSome quality gates failed!", file=sys.stderr)
        sys.exit(1)
    print("\nAll profiles passed quality gates.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify syntax**

```bash
cd D:/Python/subject1/RAG_Agent/backend
D:/Python/Python/python.exe -m py_compile tests/run_latency_ab.py && echo "OK"
```

- [ ] **Step 3: Commit**

```bash
git add backend/tests/run_latency_ab.py
git commit -m "feat: add A/B latency comparison runner with 4 config profiles and quality gates"
```

---

### Task 5: Unit tests for timeout behavior

**Files:**
- Create: `backend/tests/agent/test_timeout_budget.py`

- [ ] **Step 1: Write timeout tests**

```python
"""Phase 4: per-phase timeout budget unit tests."""

import asyncio

import pytest

from config import settings


class TestTimeoutConfig:
    def test_timeout_defaults_positive(self):
        assert settings.rag_timeout_intent > 0
        assert settings.rag_timeout_retrieval > 0
        assert settings.rag_timeout_generation > 0
        assert settings.rag_timeout_verification > 0
        assert settings.rag_timeout_repair > 0

    def test_cache_enabled_by_default(self):
        assert settings.rag_answer_cache_enabled is True

    def test_retrieval_timeout_exceeds_intent(self):
        """Retrieval should have more budget than intent classification."""
        assert settings.rag_timeout_retrieval >= settings.rag_timeout_intent

    def test_generation_timeout_is_largest(self):
        """Generation should have the most budget."""
        assert settings.rag_timeout_generation >= settings.rag_timeout_retrieval
        assert settings.rag_timeout_generation >= settings.rag_timeout_verification


class TestAsyncTimeout:
    @pytest.mark.asyncio
    async def test_wait_for_returns_value_on_success(self):
        async def fast_op():
            await asyncio.sleep(0.01)
            return "result"

        result = await asyncio.wait_for(fast_op(), timeout=1.0)
        assert result == "result"

    @pytest.mark.asyncio
    async def test_wait_for_raises_timeout_error(self):
        async def slow_op():
            await asyncio.sleep(10)

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(slow_op(), timeout=0.05)

    @pytest.mark.asyncio
    async def test_wait_for_timeout_pattern(self):
        """The pattern used in agent loop: catch TimeoutError, return fallback."""
        async def maybe_slow():
            await asyncio.sleep(0.02)
            return "ok"

        try:
            result = await asyncio.wait_for(maybe_slow(), timeout=0.005)
        except asyncio.TimeoutError:
            result = "timeout_fallback"

        assert result == "timeout_fallback"
```

- [ ] **Step 2: Run tests**

```bash
cd D:/Python/subject1/RAG_Agent/backend
D:/Python/Python/python.exe -m pytest tests/agent/test_timeout_budget.py -v --tb=short 2>&1
```

Expected: 7 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/agent/test_timeout_budget.py
git commit -m "test: add per-phase timeout budget and cache config unit tests"
```
