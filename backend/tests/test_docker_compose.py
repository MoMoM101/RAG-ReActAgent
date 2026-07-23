"""Validate docker-compose.yml structure."""
from pathlib import Path

import yaml

DOCKER_COMPOSE = Path(__file__).resolve().parent.parent.parent / "docker-compose.yml"
BACKEND_DOCKERFILE = Path(__file__).resolve().parent.parent / "Dockerfile"
BACKEND_DOCKERIGNORE = Path(__file__).resolve().parent.parent / ".dockerignore"
E2E_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "docker_e2e_acceptance.ps1"


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


def test_backend_image_migrates_database_before_startup():
    """A fresh Docker volume must be migrated before the revision gate runs."""
    dockerfile = BACKEND_DOCKERFILE.read_text(encoding="utf-8")
    command = next(line for line in dockerfile.splitlines() if line.startswith("CMD "))

    assert "alembic upgrade head" in command
    assert command.index("alembic upgrade head") < command.index("uvicorn main:app")


def test_default_backend_image_excludes_optional_ocr_system_libraries():
    """The default image does not install optional OCR dependencies."""
    dockerfile = BACKEND_DOCKERFILE.read_text(encoding="utf-8")
    assert "libgl1" not in dockerfile
    assert "libglib2.0-0" not in dockerfile


def test_backend_build_context_excludes_tests():
    """Test and evaluation data must not inflate the runtime image."""
    patterns = {
        line.strip()
        for line in BACKEND_DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert "tests" in patterns or "tests/" in patterns


def test_e2e_copies_consistency_check_into_runtime_container():
    """The slim runtime image excludes tests, so E2E injects its read-only probe."""
    script = E2E_SCRIPT.read_text(encoding="utf-8")
    assert "docker cp $ConsistencyScriptPath" in script
    assert 'RAG_AGENT_APP_ROOT=/app python $containerScript' in script
