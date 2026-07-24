# backend/tests/test_metrics.py
import pytest

from metrics import MetricsCollector, export_prometheus


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


class TestLlmUsageMetrics:
    def test_records_actual_and_estimated_usage_separately(self, fresh_collector):
        fresh_collector.record_llm_usage(
            25,
            prompt_tokens=20,
            completion_tokens=5,
            estimated=False,
        )
        fresh_collector.record_llm_usage(7, estimated=True)

        usage = fresh_collector.snapshot()["llm"]
        assert usage["total_tokens"] == 32
        assert usage["prompt_tokens"] == 20
        assert usage["completion_tokens"] == 5
        assert usage["estimated_requests"] == 1
        assert usage["requests"] == 2


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
