"""Structured metrics collection for RAG Agent observability.

Collects per-request timing, tool execution stats, and system health
without exposing raw user queries, document content, or API keys.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from threading import RLock


@dataclass
class RequestTimer:
    """Tracks a single HTTP request lifecycle."""
    start_ms: float = field(default_factory=lambda: time.time() * 1000)
    label: str = ""

    def stop(self) -> float:
        return time.time() * 1000 - self.start_ms


class MetricsCollector:
    """Thread-safe collector for request, tool, and system metrics."""

    def __init__(self):
        self._lock = RLock()

        # HTTP
        self.http_requests: dict[str, int] = defaultdict(int)      # method:path → count
        self.http_errors: dict[str, int] = defaultdict(int)        # 4xx/5xx → count
        self.http_latencies: list[float] = []                      # last N latencies ms

        # Agent
        self.agent_iterations: list[int] = []                      # loop count per request
        self.agent_timeouts: int = 0
        self.agent_loop_limits: int = 0

        # Tools
        self.tool_calls: dict[str, int] = defaultdict(int)         # tool name → calls
        self.tool_successes: dict[str, int] = defaultdict(int)     # tool name → successes
        self.tool_retries: dict[str, int] = defaultdict(int)       # tool name → retries
        self.tool_latencies: dict[str, list[float]] = defaultdict(list)  # tool name → latencies ms

        # Ingestion
        self.ingestion_total: int = 0
        self.ingestion_failures: int = 0
        self.ingestion_latencies: list[float] = []

        # LLM
        self.llm_tokens_total: int = 0
        self.llm_requests: int = 0

        # Embedding
        self.embedding_tokens_total: int = 0
        self.embedding_requests: int = 0

        self._max_samples = 1000  # cap latency samples to bound memory

    # ── HTTP ──────────────────────────────────────────────────

    def record_request(self, method: str, path: str):
        key = f"{method}:{path}"
        with self._lock:
            self.http_requests[key] += 1

    def record_error(self, status: int):
        with self._lock:
            self.http_errors[str(status)] += 1

    def record_latency(self, ms: float):
        with self._lock:
            self.http_latencies.append(ms)
            if len(self.http_latencies) > self._max_samples:
                self.http_latencies = self.http_latencies[-self._max_samples:]

    # ── Agent ─────────────────────────────────────────────────

    def record_agent_run(self, iterations: int, timed_out: bool, loop_limit: bool):
        with self._lock:
            self.agent_iterations.append(iterations)
            if timed_out:
                self.agent_timeouts += 1
            if loop_limit:
                self.agent_loop_limits += 1

    # ── Tools ─────────────────────────────────────────────────

    def record_tool_call(self, name: str, success: bool, retries: int, latency_ms: float):
        with self._lock:
            self.tool_calls[name] += 1
            if success:
                self.tool_successes[name] += 1
            self.tool_retries[name] += retries
            self.tool_latencies[name].append(latency_ms)
            if len(self.tool_latencies[name]) > self._max_samples:
                self.tool_latencies[name] = self.tool_latencies[name][-self._max_samples:]

    # ── Ingestion ─────────────────────────────────────────────

    def record_ingestion(self, success: bool, latency_ms: float):
        with self._lock:
            self.ingestion_total += 1
            if not success:
                self.ingestion_failures += 1
            self.ingestion_latencies.append(latency_ms)
            if len(self.ingestion_latencies) > self._max_samples:
                self.ingestion_latencies = self.ingestion_latencies[-self._max_samples:]

    # ── LLM / Embedding ───────────────────────────────────────

    def record_llm_usage(self, tokens: int):
        with self._lock:
            self.llm_tokens_total += tokens
            self.llm_requests += 1

    def record_embedding_usage(self, tokens: int):
        with self._lock:
            self.embedding_tokens_total += tokens
            self.embedding_requests += 1

    # ── Snapshot ──────────────────────────────────────────────

    def _percentile(self, samples: list[float], p: float) -> float:
        if not samples:
            return 0.0
        sorted_samples = sorted(samples)
        idx = int(len(sorted_samples) * p / 100)
        return sorted_samples[min(idx, len(sorted_samples) - 1)]

    def snapshot(self) -> dict:
        """Return current metrics snapshot (safe for API exposure)."""
        with self._lock:
            return {
                "http": {
                    "total_requests": sum(self.http_requests.values()),
                    "requests_by_path": dict(self.http_requests),
                    "errors_by_status": dict(self.http_errors),
                    "latency_ms": {
                        "p50": self._percentile(self.http_latencies, 50),
                        "p95": self._percentile(self.http_latencies, 95),
                        "p99": self._percentile(self.http_latencies, 99),
                        "samples": len(self.http_latencies),
                    },
                },
                "agent": {
                    "iterations": {
                        "avg": (
                            sum(self.agent_iterations) / max(len(self.agent_iterations), 1)
                        ),
                        "max": max(self.agent_iterations) if self.agent_iterations else 0,
                        "samples": len(self.agent_iterations),
                    },
                    "timeouts": self.agent_timeouts,
                    "loop_limits": self.agent_loop_limits,
                },
                "tools": {
                    name: {
                        "calls": self.tool_calls[name],
                        "success_rate": (
                            self.tool_successes[name] / max(self.tool_calls[name], 1)
                        ),
                        "avg_retries": (
                            self.tool_retries[name] / max(self.tool_calls[name], 1)
                        ),
                        "latency_ms": {
                            "p50": self._percentile(self.tool_latencies[name], 50),
                            "p95": self._percentile(self.tool_latencies[name], 95),
                        },
                    }
                    for name in self.tool_calls
                },
                "ingestion": {
                    "total": self.ingestion_total,
                    "failures": self.ingestion_failures,
                    "latency_ms": {
                        "p50": self._percentile(self.ingestion_latencies, 50),
                        "p95": self._percentile(self.ingestion_latencies, 95),
                    },
                },
                "llm": {
                    "total_tokens": self.llm_tokens_total,
                    "requests": self.llm_requests,
                },
                "embedding": {
                    "total_estimated_tokens": self.embedding_tokens_total,
                    "requests": self.embedding_requests,
                },
            }


# Global singleton
_collector: MetricsCollector | None = None


def get_metrics() -> MetricsCollector:
    global _collector
    if _collector is None:
        _collector = MetricsCollector()
    return _collector
