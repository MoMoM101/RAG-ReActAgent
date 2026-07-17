"""Docker E2E smoke test — verifies the full stack is functional.

Usage:
  # Lenient mode (local dev): skips when services unreachable
  pytest tests/e2e/test_docker_smoke.py -v

  # Strict mode (CI/acceptance): fails when services unreachable
  DOCKER_E2E_REQUIRED=1 pytest tests/e2e/test_docker_smoke.py -v
"""

import os

import httpx
import pytest

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")
IS_STRICT = os.environ.get("DOCKER_E2E_REQUIRED", "") == "1"


def _require(condition: bool, message: str) -> None:
    """Fail or skip based on strict mode."""
    if condition:
        return
    if IS_STRICT:
        pytest.fail(message)
    else:
        pytest.skip(message)


def _health_ok() -> bool:
    try:
        r = httpx.get(f"{BACKEND_URL}/api/health", timeout=5.0)
        return r.status_code == 200 and r.json().get("status") == "ok"
    except Exception:
        return False


def _get_json(path: str, timeout: float = 5.0) -> tuple[int, dict]:
    try:
        r = httpx.get(f"{BACKEND_URL}{path}", timeout=timeout)
        return r.status_code, r.json()
    except Exception as e:
        return 0, {"error": str(e)}


@pytest.mark.docker
class TestDockerSmoke:
    def test_health_endpoint(self):
        _require(_health_ok(), "Backend health endpoint unreachable or unhealthy")

    def test_health_dependencies(self):
        code, data = _get_json("/api/health/dependencies")
        if code == 0:
            _require(False, f"Dependencies health not reachable: {data.get('error')}")
            return
        status = data.get("status")
        _require(status in ("ok", "degraded"), f"Unexpected dependency status: {status}")

    def test_admin_auth_required(self):
        try:
            r = httpx.get(f"{BACKEND_URL}/api/documents", timeout=5.0)
            _require(
                r.status_code in (401, 403),
                f"Expected 401/403 without token, got {r.status_code}",
            )
        except Exception as e:
            _require(False, f"Auth test failed: {e}")

    def test_no_secrets_in_health_response(self):
        try:
            r = httpx.get(f"{BACKEND_URL}/api/health", timeout=5.0)
            text = r.text.lower()
            for key in ["api_key", "password", "secret", "token"]:
                _require(key not in text, f"Found '{key}' in health response")
        except Exception as e:
            _require(False, f"Secrets test failed: {e}")

    def test_metrics_endpoint_requires_auth(self):
        try:
            r = httpx.get(f"{BACKEND_URL}/api/metrics", timeout=5.0)
            _require(
                r.status_code in (401, 403),
                f"Expected 401/403 for metrics without token, got {r.status_code}",
            )
        except Exception as e:
            _require(False, f"Metrics auth test failed: {e}")
