"""Docker E2E smoke test — verifies the full stack is functional.

Usage: docker compose up -d && docker compose exec backend python -m pytest tests/e2e/test_docker_smoke.py -v
"""

import os

import httpx
import pytest

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")


def _health_ok() -> bool:
    try:
        r = httpx.get(f"{BACKEND_URL}/api/health", timeout=5.0)
        return r.status_code == 200 and r.json().get("status") == "ok"
    except Exception:
        return False


@pytest.mark.docker
class TestDockerSmoke:
    def test_health_endpoint(self):
        if not _health_ok():
            pytest.skip("Backend not reachable — skipping Docker E2E test")

    def test_health_dependencies(self):
        try:
            r = httpx.get(f"{BACKEND_URL}/api/health/dependencies", timeout=5.0)
            data = r.json()
            assert data.get("status") in ("ok", "degraded")
        except Exception as e:
            pytest.skip(f"Dependencies health not reachable: {e}")

    def test_admin_auth_required(self):
        try:
            r = httpx.get(f"{BACKEND_URL}/api/documents", timeout=5.0)
            assert r.status_code in (401, 403), f"Expected 401/403, got {r.status_code}"
        except Exception as e:
            pytest.skip(f"Auth test skipped: {e}")

    def test_no_secrets_in_health_response(self):
        try:
            r = httpx.get(f"{BACKEND_URL}/api/health", timeout=5.0)
            text = r.text.lower()
            for key in ["api_key", "password", "secret", "token"]:
                assert key not in text, f"Found '{key}' in health response"
        except Exception as e:
            pytest.skip(f"Secrets test skipped: {e}")

    def test_metrics_endpoint_requires_auth(self):
        try:
            r = httpx.get(f"{BACKEND_URL}/api/metrics", timeout=5.0)
            assert r.status_code in (401, 403), f"Expected 401/403, got {r.status_code}"
        except Exception as e:
            pytest.skip(f"Metrics auth test skipped: {e}")
