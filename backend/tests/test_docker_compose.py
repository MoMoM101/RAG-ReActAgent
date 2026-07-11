"""Validate docker-compose.yml structure."""
from pathlib import Path

import yaml

DOCKER_COMPOSE = Path(__file__).resolve().parent.parent.parent / "docker-compose.yml"


def test_docker_compose_exists():
    """docker-compose.yml must exist."""
    assert DOCKER_COMPOSE.exists(), "docker-compose.yml not found"


def test_docker_compose_has_three_services():
    """docker-compose must define qdrant, backend, frontend."""
    doc = yaml.safe_load(DOCKER_COMPOSE.read_text(encoding="utf-8"))
    services = doc.get("services", {})
    assert "qdrant" in services
    assert "backend" in services
    assert "frontend" in services


def test_frontend_proxy_target_is_backend():
    """Frontend container VITE_API_PROXY_TARGET must point to backend service."""
    doc = yaml.safe_load(DOCKER_COMPOSE.read_text(encoding="utf-8"))
    frontend = doc["services"]["frontend"]
    env = frontend.get("environment", [])
    for item in env:
        if isinstance(item, str) and "VITE_API_PROXY_TARGET" in item:
            assert "backend:8000" in item, (
                f"VITE_API_PROXY_TARGET must use backend:8000, got: {item}"
            )
            return
    raise AssertionError("VITE_API_PROXY_TARGET not found in frontend environment")


def test_backend_listens_on_all_interfaces():
    """Backend container must listen on 0.0.0.0 for inter-service networking."""
    doc = yaml.safe_load(DOCKER_COMPOSE.read_text(encoding="utf-8"))
    backend = doc["services"]["backend"]
    env = backend.get("environment", [])
    for item in env:
        if isinstance(item, str) and "SERVER_HOST" in item:
            assert "0.0.0.0" in item, (
                f"SERVER_HOST must be 0.0.0.0 for container networking, got: {item}"
            )
            return
    raise AssertionError("SERVER_HOST not found in backend environment")
