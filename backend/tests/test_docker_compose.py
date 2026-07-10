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


def test_frontend_api_url_is_localhost():
    """Frontend container VITE_API_BASE_URL must use localhost (browser-accessible)."""
    doc = yaml.safe_load(DOCKER_COMPOSE.read_text(encoding="utf-8"))
    frontend = doc["services"]["frontend"]
    env = frontend.get("environment", [])
    for item in env:
        if isinstance(item, str) and "VITE_API_BASE_URL" in item:
            assert "localhost" in item, (
                f"VITE_API_BASE_URL must use localhost (browser-accessible), got: {item}"
            )
            return
    assert False, "VITE_API_BASE_URL not found in frontend environment"
