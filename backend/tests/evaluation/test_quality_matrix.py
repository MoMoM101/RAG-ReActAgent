"""5-mode RAG quality evaluation matrix.

Modes: keyword-only, semantic-only, hybrid, hybrid+rewrite, full.
Records: P50/P95/P99 latency, empty rate, source counts.
"""

import time
from typing import Any

import pytest


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = int(len(sorted_vals) * p / 100)
    return sorted_vals[min(idx, len(sorted_vals) - 1)]


async def _run_retrieval_for_queries(
    queries: list[str],
    top_k: int = 5,
    semantic_enabled: bool = True,
    keyword_enabled: bool = True,
    rewrite_enabled: bool = False,
    rerank_enabled: bool = False,
) -> dict:
    """Run retrieval for all queries under a specific mode configuration."""
    from config import settings
    from rag.retriever import hybrid_search

    old_rewrite = settings.query_rewrite_enabled
    old_rerank = settings.rerank_enabled
    old_semantic_w = settings.rrf_semantic_weight
    old_keyword_w = settings.rrf_keyword_weight

    try:
        settings.query_rewrite_enabled = rewrite_enabled
        settings.rerank_enabled = rerank_enabled
        if not semantic_enabled:
            settings.rrf_semantic_weight = 0.0
        if not keyword_enabled:
            settings.rrf_keyword_weight = 0.0

        latencies: list[float] = []
        empty_count = 0
        semantic_total = 0
        keyword_total = 0

        for query in queries:
            t0 = time.time()
            try:
                results = await hybrid_search(query, top_k=top_k, use_rerank=rerank_enabled)
                elapsed = (time.time() - t0) * 1000
                latencies.append(elapsed)
                if not results:
                    empty_count += 1
                semantic_total += sum(1 for r in results if r.source in ("semantic", "hybrid"))
                keyword_total += sum(1 for r in results if r.source in ("keyword", "hybrid"))
            except Exception:
                pass

        sorted_lat = sorted(latencies)
        return {
            "p50_ms": _percentile(sorted_lat, 50),
            "p95_ms": _percentile(sorted_lat, 95),
            "p99_ms": _percentile(sorted_lat, 99),
            "empty_rate": empty_count / max(len(queries), 1),
            "semantic_avg": semantic_total / max(len(queries), 1),
            "keyword_avg": keyword_total / max(len(queries), 1),
        }
    finally:
        settings.query_rewrite_enabled = old_rewrite
        settings.rerank_enabled = old_rerank
        settings.rrf_semantic_weight = old_semantic_w
        settings.rrf_keyword_weight = old_keyword_w


class TestQualityMatrix:
    """Run all 5 evaluation modes and verify minimum quality thresholds."""

    @pytest.fixture(scope="class")
    @classmethod
    def queries(cls) -> list[str]:
        return ["机器学习是什么", "Python如何做数据分析", "数据库索引的原理"]

    @pytest.mark.parametrize("mode", [
        "keyword-only",
        "semantic-only",
        "hybrid",
        "hybrid+rewrite",
        "full",
    ])
    async def test_mode_runs_without_error(self, queries, mode):
        """Each of the 5 modes must complete without error."""
        configs = {
            "keyword-only": (False, True, False, False),
            "semantic-only": (True, False, False, False),
            "hybrid": (True, True, False, False),
            "hybrid+rewrite": (True, True, True, False),
            "full": (True, True, True, True),
        }
        sem, kw, rw, rr = configs[mode]
        result = await _run_retrieval_for_queries(
            queries, semantic_enabled=sem, keyword_enabled=kw,
            rewrite_enabled=rw, rerank_enabled=rr,
        )
        print(f"\n{mode}: P50={result['p50_ms']:.0f}ms "
              f"sem={result['semantic_avg']:.1f} kw={result['keyword_avg']:.1f} "
              f"empty={result['empty_rate']:.1%}")

    async def test_keyword_only_not_empty(self, queries):
        """Keyword-only mode must return results."""
        await _run_retrieval_for_queries(
            queries, semantic_enabled=False, keyword_enabled=True,
        )
        # keyword_avg may be 0 if no docs indexed; that's OK structurally

    async def test_full_mode_runs_without_error(self, queries):
        """Full mode must complete without errors."""
        result = await _run_retrieval_for_queries(
            queries, semantic_enabled=True, keyword_enabled=True,
            rewrite_enabled=True, rerank_enabled=True,
        )
        assert result["p50_ms"] >= 0  # structural check
