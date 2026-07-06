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
        t0 = time.time()

        response = await call_next(request)

        elapsed_ms = int((time.time() - t0) * 1000)
        _log_request(request_id, request.method, request.url.path, response.status_code, elapsed_ms)
        response.headers["X-Request-ID"] = request_id
        return response


def _log_request(request_id: str, method: str, path: str, status: int, elapsed_ms: int):
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "rid": request_id,
        "method": method,
        "path": path,
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
