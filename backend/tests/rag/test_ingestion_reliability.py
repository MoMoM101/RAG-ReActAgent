"""Phase 3a: ingestion idempotency, error classification, and retry behavior."""


import pytest

from config import settings
from rag.pipeline import _classify_error, _retry_delay


class TestErrorClassification:
    def test_rate_limit_429(self):
        assert _classify_error(Exception("HTTP 429 Too Many Requests")) == "rate_limit"

    def test_rate_limit_message(self):
        assert _classify_error(Exception("rate limit exceeded")) == "rate_limit"
        assert _classify_error(Exception("too many requests")) == "rate_limit"

    def test_transient_timeout(self):
        assert _classify_error(TimeoutError()) == "transient"

    def test_transient_connection(self):
        assert _classify_error(ConnectionError()) == "transient"
        assert _classify_error(ConnectionRefusedError()) == "transient"

    def test_transient_keywords(self):
        assert _classify_error(Exception("connection reset by peer")) == "transient"
        assert _classify_error(Exception("request timeout")) == "transient"
        assert _classify_error(Exception("connection refused")) == "transient"
        assert _classify_error(Exception("500 (Internal Server Error)")) == "transient"
        assert _classify_error(Exception("Failed to apply operation to Active replica")) == "transient"

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
        assert d_transient > 0 and d_rate > 0

    def test_delay_capped(self):
        d = _retry_delay(100, "transient")
        assert d <= settings.ingestion_retry_max_sec * 1.3

    def test_jitter_produces_variation(self):
        delays = [_retry_delay(1, "transient") for _ in range(20)]
        unique = len(set(round(d, 1) for d in delays))
        assert unique > 1, "Jitter should produce varied delays"


@pytest.mark.asyncio
class TestConfigDefaults:
    async def test_retry_defaults_sensible(self):
        assert settings.ingestion_max_retries >= 2
        assert settings.ingestion_retry_base_sec > 0
        assert settings.ingestion_retry_max_sec > settings.ingestion_retry_base_sec
        assert 0 < settings.ingestion_retry_jitter < 1
