"""Structured logging with request_id tracking."""

import json
import time
import uuid
from datetime import UTC, datetime

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])
        request.state.request_id = request_id
        from tracing import set_request_id, span
        set_request_id(request_id)
        t0 = time.time()

        with span(f"http.{request.method}", path=request.url.path):
            response = await call_next(request)

        elapsed_ms = int((time.time() - t0) * 1000)
        _log_request(request_id, request.method, request.url.path, response.status_code, elapsed_ms)
        response.headers["X-Request-ID"] = request_id

        # Metrics
        from metrics import get_metrics
        get_metrics().record_request(request.method, request.url.path)
        get_metrics().record_latency(float(elapsed_ms))
        if response.status_code >= 400:
            get_metrics().record_error(response.status_code)

        return response


def _sanitize_path(path: str) -> str:
    """Mask sensitive query parameter values in access logs."""
    import re
    for key in ("token", "secret", "key", "api_key", "password"):
        path = re.sub(rf"({key}=)[^&\s]+", r"\1***", path, flags=re.IGNORECASE)
    return path


def _log_request(request_id: str, method: str, path: str, status: int, elapsed_ms: int):
    from tracing import peek_request_id
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "rid": peek_request_id() or request_id,
        "method": method,
        "path": _sanitize_path(path),
        "status": status,
        "elapsed_ms": elapsed_ms,
    }
    # Write one JSON line per request to a log file
    try:
        from pathlib import Path

        from config import settings
        log_dir = Path(settings.upload_dir).parent / "logs"
        log_dir.mkdir(exist_ok=True)
        log_file = log_dir / "access.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass  # Don't break the app if logging fails
