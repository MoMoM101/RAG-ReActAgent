"""Application logging setup — console (text) + rotating file (JSON lines)."""

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config import settings

_MANAGED_HANDLER_ATTR = "_rag_agent_managed"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        import traceback
        obj = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "module": record.module,
            "func": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info and record.exc_info[1]:
            obj["exc"] = "".join(traceback.format_exception(*record.exc_info))
        return json.dumps(obj, ensure_ascii=False, default=str)


def setup_logging() -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    log_dir = Path(settings.upload_dir).parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    # Lifespan can run more than once in tests or embedded deployments. Replace
    # only handlers owned by this module so third-party logging stays intact.
    for handler in list(root.handlers):
        if getattr(handler, _MANAGED_HANDLER_ATTR, False):
            root.removeHandler(handler)
            handler.close()

    # Console — human readable
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    ))
    setattr(console, _MANAGED_HANDLER_ATTR, True)
    root.addHandler(console)

    # File — JSON lines with rotation (10 MB × 5)
    file_handler = RotatingFileHandler(
        log_dir / "app.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(JsonFormatter())
    setattr(file_handler, _MANAGED_HANDLER_ATTR, True)
    root.addHandler(file_handler)

    logging.getLogger(__name__).info(
        "logging configured level=%s dir=%s", settings.log_level.upper(), str(log_dir)
    )
