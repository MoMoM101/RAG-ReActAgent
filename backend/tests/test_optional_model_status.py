"""Optional models must never turn a slow download into a core startup failure."""

import time


def test_ocr_status_after_notice_keeps_background_download_alive(monkeypatch):
    import ocr.factory as factory

    monkeypatch.setattr(factory.settings, "ocr_enabled", True)
    monkeypatch.setattr(factory.settings, "optional_model_notice_seconds", 180.0)
    monkeypatch.setattr(factory, "_status", factory.ComponentStatus.DOWNLOADING)
    monkeypatch.setattr(factory, "_started_at", time.monotonic() - 181)
    monkeypatch.setattr(factory, "_cached", False)

    status = factory.get_ocr_status()

    assert status["slow"] is True
    assert status["continuing_in_background"] is True
    assert "不会关闭服务" in status["message"]


def test_reranker_failure_is_optional_and_has_manual_command(monkeypatch):
    import reranker.factory as factory

    monkeypatch.setattr(factory.settings, "rerank_enabled", True)
    monkeypatch.setattr(factory, "_status", factory.ComponentStatus.FAILED)
    monkeypatch.setattr(factory, "_last_error", "network unavailable")

    status = factory.get_reranker_status()

    assert status["optional"] is True
    assert status["continuing_in_background"] is False
    assert "RRF" in status["message"]
    assert status["manual_command"].endswith("--reranker")
