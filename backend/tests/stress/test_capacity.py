"""Capacity benchmarks: measure performance under concurrent load."""

import asyncio
import time

import pytest


class TestConcurrencyScaling:
    @pytest.mark.parametrize("concurrency", [1, 5, 10])
    async def test_concurrent_retrieval(self, concurrency):
        from rag.retriever import hybrid_search

        async def single_search():
            t0 = time.time()
            results = await hybrid_search("测试查询", top_k=3)
            return (time.time() - t0) * 1000, len(results)

        tasks = [single_search() for _ in range(concurrency)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        errors = [r for r in results if isinstance(r, Exception)]
        successes = [r for r in results if not isinstance(r, Exception)]

        if successes:
            latencies = [s[0] for s in successes]
            print(f"\nConcurrency={concurrency}: {len(successes)}/{len(results)} ok, "
                  f"P50={sorted(latencies)[len(latencies)//2]:.0f}ms, "
                  f"errors={len(errors)}")

        assert len(errors) == 0, f"{len(errors)} requests failed: {errors[:3]}"

    async def test_batch_retrieval_stability(self):
        from rag.retriever import hybrid_search

        latencies: list[float] = []
        for _ in range(20):
            t0 = time.time()
            await hybrid_search("测试查询", top_k=3)
            latencies.append((time.time() - t0) * 1000)

        sorted_lat = sorted(latencies)
        p50 = sorted_lat[len(sorted_lat) // 2]
        p99 = sorted_lat[int(len(sorted_lat) * 0.99)]

        print(f"\nBatch 20: P50={p50:.0f}ms P99={p99:.0f}ms")
        if p50 > 0:
            assert p99 / p50 < 30, f"P99/P50 ratio too high: {p99/p50:.1f}"
