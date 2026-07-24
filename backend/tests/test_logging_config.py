import json
import logging
import sys

from config import settings
from logging_config import JsonFormatter, setup_logging


def _managed_handlers():
    return [
        handler
        for handler in logging.getLogger().handlers
        if getattr(handler, "_rag_agent_managed", False)
    ]


def test_setup_logging_is_idempotent(tmp_path, monkeypatch):
    root = logging.getLogger()
    original_level = root.level
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "log_level", "warning")

    try:
        setup_logging()
        setup_logging()

        handlers = _managed_handlers()
        assert len(handlers) == 2
        assert root.level == logging.WARNING
        assert (tmp_path / "logs" / "app.log").exists()
    finally:
        for handler in _managed_handlers():
            root.removeHandler(handler)
            handler.close()
        root.setLevel(original_level)


def test_json_formatter_includes_exception():
    try:
        raise ValueError("bad value")
    except ValueError:
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="test.logger",
        level=logging.ERROR,
        pathname=__file__,
        lineno=42,
        msg="failed %s",
        args=("operation",),
        exc_info=exc_info,
    )
    payload = json.loads(JsonFormatter().format(record))

    assert payload["level"] == "ERROR"
    assert payload["msg"] == "failed operation"
    assert "ValueError: bad value" in payload["exc"]
