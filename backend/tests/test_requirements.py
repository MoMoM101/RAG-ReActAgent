"""Test requirements files are well-formed."""
import re
from pathlib import Path

REQUIREMENTS_DIR = Path(__file__).resolve().parent.parent


def _parse_requirements(filename: str) -> dict[str, str]:
    """Return {package_name: version_spec} from a requirements file."""
    path = REQUIREMENTS_DIR / filename
    if not path.exists():
        return {}
    pkgs = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([a-zA-Z0-9_-]+)(?:\[[^\]]*\])?\s*([<>=!~].*)?$", line)
        if match:
            name = match.group(1).lower()
            pkgs[name] = line
    return pkgs


def test_runtime_requirements_contains_core():
    """requirements.txt must contain core runtime deps."""
    pkgs = _parse_requirements("requirements.txt")
    assert pkgs, "requirements.txt 不应为空"

    required = [
        "fastapi", "uvicorn", "qdrant-client", "pymupdf",
        "python-docx", "openpyxl", "tiktoken", "openai",
        "sqlalchemy", "aiosqlite", "sse-starlette", "slowapi",
        "jieba", "cryptography",
    ]
    missing = [p for p in required if p not in pkgs]
    assert not missing, f"requirements.txt 缺少核心依赖: {missing}"


def test_runtime_requirements_excludes_pandas():
    """Tabular loaders use csv/openpyxl and must not pull pandas/numpy into runtime."""
    pkgs = _parse_requirements("requirements.txt")
    assert "pandas" not in pkgs


def test_runtime_requirements_excludes_dev_deps():
    """requirements.txt should NOT contain pytest, mypy, ruff, httpx."""
    pkgs = _parse_requirements("requirements.txt")
    dev = ["pytest", "pytest-asyncio", "pytest-cov", "mypy", "ruff", "httpx"]
    found = [p for p in dev if p in pkgs]
    assert not found, (
        f"requirements.txt 包含开发依赖: {found}。"
        f"请将它们移到 requirements-dev.txt"
    )


def test_dev_requirements_exists():
    """requirements-dev.txt must exist and contain dev deps."""
    pkgs = _parse_requirements("requirements-dev.txt")
    assert pkgs, "requirements-dev.txt 不应为空"

    required = ["pytest", "pytest-asyncio", "pytest-cov", "mypy", "ruff", "httpx", "pyyaml"]
    missing = [p for p in required if p not in pkgs]
    assert not missing, f"requirements-dev.txt 缺少: {missing}"


def test_ocr_requirements_exists():
    """requirements-ocr.txt must exist."""
    path = REQUIREMENTS_DIR / "requirements-ocr.txt"
    assert path.exists(), "requirements-ocr.txt 不存在"


def test_rerank_requirements_exists():
    """requirements-rerank.txt must exist."""
    path = REQUIREMENTS_DIR / "requirements-rerank.txt"
    assert path.exists(), "requirements-rerank.txt 不存在"


def test_web_search_deps_in_runtime():
    """Web search deps (bs4 + ddgs) should be in runtime requirements.txt."""
    pkgs = _parse_requirements("requirements.txt")
    assert "beautifulsoup4" in pkgs, "requirements.txt 缺少 beautifulsoup4"
    has_ddg = any(d in pkgs for d in ["duckduckgo-search", "duckduckgo_search"])
    assert has_ddg, "requirements.txt 缺少 duckduckgo_search"
