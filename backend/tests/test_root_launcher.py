"""Regression tests for the repository-level service launcher."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_launcher():
    launcher_path = Path(__file__).resolve().parents[2] / "main.py"
    spec = importlib.util.spec_from_file_location("rag_agent_root_launcher", launcher_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_startup_timeout_allows_slow_model_initialization():
    launcher = _load_launcher()

    args = launcher.parse_args([])

    assert args.startup_timeout == 180.0


def test_launcher_explains_optional_models_are_not_bound_to_core_timeout(monkeypatch):
    launcher = _load_launcher()
    lines: list[str] = []
    monkeypatch.setattr(launcher, "print_status", lambda service, status, color="green": lines.append(status))
    monkeypatch.setattr(launcher, "release_port", lambda _port: None)
    monkeypatch.setattr(launcher, "_spawn", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("stop")))

    assert launcher.start_backend(8000, 180) is False
    assert any("不受此时限影响" in line for line in lines)


def test_startup_summary_keeps_frontend_url_as_last_line(monkeypatch):
    launcher = _load_launcher()
    lines: list[str] = []
    monkeypatch.setattr(launcher, "_print", lambda message="": lines.append(message))
    monkeypatch.setattr(launcher, "terminal_link", lambda url, label=None: label or url)

    launcher.print_startup_summary("http://localhost:5173", "http://localhost:8000")

    assert lines[-1] == "  打开网页：http://localhost:5173"
