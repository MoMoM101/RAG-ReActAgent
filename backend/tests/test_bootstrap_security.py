"""First-run administration bootstrap regression tests."""

from security import generate_admin_token

from config import settings
from main import _bootstrap_admin_token


def test_generate_admin_token_is_random_and_url_safe():
    first = generate_admin_token()
    second = generate_admin_token()

    assert first != second
    assert len(first) >= 32
    assert all(char.isalnum() or char in "-_" for char in first)


def test_bootstrap_admin_token_persists_to_isolated_env(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    monkeypatch.setitem(settings.model_config, "env_file", str(env_path))
    monkeypatch.setattr(settings, "admin_api_token", "")

    _bootstrap_admin_token()

    token = settings.admin_api_token
    assert token
    assert env_path.read_text(encoding="utf-8") == f"ADMIN_API_TOKEN={token}\n"


def test_bootstrap_admin_token_does_not_overwrite_existing(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("ADMIN_API_TOKEN=existing-token\n", encoding="utf-8")
    monkeypatch.setitem(settings.model_config, "env_file", str(env_path))
    monkeypatch.setattr(settings, "admin_api_token", "")

    _bootstrap_admin_token()

    assert settings.admin_api_token == "existing-token"
    assert env_path.read_text(encoding="utf-8") == "ADMIN_API_TOKEN=existing-token\n"
