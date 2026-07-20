# Phase 1 Module 1.1: 配置与依赖治理

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让新用户从干净环境按 README 一次性启动，补齐 .env.example、拆分依赖、WebSearch 降级。

**Architecture:** 配置与依赖层面的改动，不涉及业务逻辑。新增 `.env.example` 作为配置模板，将 `requirements.txt` 中的开发依赖移到 `requirements-dev.txt`，新增 OCR/rerank 可选依赖文件，WebSearch 默认关闭。

**Tech Stack:** Python 3.12, pydantic-settings

---

### Task 1: 写 .env.example 同步测试（TDD 红阶段）

**Files:**
- Create: `backend/tests/test_env_example_sync.py`

- [ ] **Step 1: 写测试**

```python
"""Test that .env.example covers all Settings fields in config.py."""
from pathlib import Path


def _parse_settings_fields() -> set[str]:
    """Extract all field names from config.Settings class (UPPERCASED)."""
    from config import Settings

    fields = set()
    for name in Settings.model_fields:
        if name == "model_config":
            continue
        fields.add(name.upper())
    return fields


def _parse_env_example_keys() -> set[str]:
    """Extract all key names from .env.example."""
    env_example = Path(__file__).resolve().parent.parent / ".env.example"
    if not env_example.exists():
        return set()

    keys = set()
    for line in env_example.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            keys.add(key.upper())
    return keys


def test_env_example_covers_all_settings():
    """.env.example should have an entry for every config.Settings field."""
    settings_fields = _parse_settings_fields()
    env_keys = _parse_env_example_keys()

    missing = settings_fields - env_keys
    extra = env_keys - settings_fields

    assert not missing, (
        f".env.example 缺少配置项: {sorted(missing)}\n"
        f"请检查 config.py 中 Settings 类与 .env.example 的对应关系"
    )
    assert not extra, (
        f".env.example 包含不在 config.py 中的额外键: {sorted(extra)}\n"
        f"请更新 config.py 或从 .env.example 移除多余字段"
    )
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd backend && python -m pytest tests/test_env_example_sync.py -v
```
Expected: FAIL — `.env.example` 不存在，返回空集合，assert 触发

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_env_example_sync.py
git commit -m "test: add .env.example vs config.py field coverage test (red)"
```

---

### Task 2: 新增 backend/.env.example（TDD 绿阶段）

**Files:**
- Create: `backend/.env.example`

- [ ] **Step 1: 创建 .env.example**

```env
# ── LLM ────────────────────────────────────────────
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=
LLM_MAX_CONTEXT=0

# ── Embedding ──────────────────────────────────────
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_BASE_URL=https://api.openai.com/v1
EMBEDDING_API_KEY=
EMBEDDING_DIM=1536

# ── Vector Database (Qdrant) ──────────────────────
QDRANT_HOST=
QDRANT_PORT=6333
QDRANT_PATH=./data/qdrant2
QDRANT_COLLECTION=rag_chunks
QDRANT_ACTIVE_COLLECTION=

# ── SQLite ─────────────────────────────────────────
DATABASE_URL=sqlite+aiosqlite:///./data/rag_agent.db

# ── Storage ────────────────────────────────────────
UPLOAD_DIR=./data/uploads

# ── Agent ──────────────────────────────────────────
MAX_LOOP_ITERATIONS=10
MAX_TOOL_RETRIES=3
MAX_TOTAL_TIME=120

# ── Chunking ───────────────────────────────────────
CHUNK_SIZE=200
CHUNK_OVERLAP=40
RETRIEVAL_TOP_K=8

# ── Retrieval Dedup ────────────────────────────────
DEDUP_ENABLED=true
DEDUP_SIMILARITY_THRESHOLD=0.85

# ── Reranker (optional, requires pip install -r requirements-rerank.txt) ──
RERANK_ENABLED=false
RERANK_MODEL=BAAI/bge-reranker-v2-m3
RERANK_TOP_N=16
RRF_K=60
RRF_SEMANTIC_WEIGHT=2.0
RRF_KEYWORD_WEIGHT=1.0
RRF_QUALITY_PREFILTER_ENABLED=false
RRF_ADAPTIVE_ENABLED=false
HF_ENDPOINT=
CHUNK_QUALITY_FILTER_ENABLED=true
CHUNK_QUALITY_LLM_ENABLED=false
QUERY_REWRITE_ENABLED=false

# ── Web Search (optional) ──────────────────────────
WEB_SEARCH_ENABLED=false
WEB_SEARCH_MAX_RESULTS=5
WEB_SEARCH_PROXY=

# ── OCR (optional, requires pip install -r requirements-ocr.txt) ──
OCR_ENABLED=true
OCR_MIN_TEXT_LENGTH=50

# ── Memory ─────────────────────────────────────────
MEMORY_ENABLED=true
MEMORY_MAX_COUNT=100

# ── Ingestion ──────────────────────────────────────
INGESTION_MAX_CONCURRENCY=3

# ── Logging ────────────────────────────────────────
LOG_LEVEL=INFO

# ── Server ─────────────────────────────────────────
SECRET_KEY=
```

- [ ] **Step 2: 运行测试确认通过**

```bash
cd backend && python -m pytest tests/test_env_example_sync.py -v
```
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add backend/.env.example
git commit -m "feat: add .env.example covering all config.Settings fields"
```

---

### Task 3: 拆分 requirements.txt（TDD 红→绿→重构）

**Files:**
- Create: `backend/tests/test_requirements.py`
- Modify: `backend/requirements.txt`
- Create: `backend/requirements-dev.txt`
- Create: `backend/requirements-ocr.txt`
- Create: `backend/requirements-rerank.txt`

- [ ] **Step 1: 写测试（红阶段）**

```python
"""Test requirements files are well-formed."""
from pathlib import Path
import re


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
        "python-docx", "openpyxl", "pandas", "tiktoken", "openai",
        "sqlalchemy", "aiosqlite", "sse-starlette", "slowapi",
        "jieba", "cryptography",
    ]
    missing = [p for p in required if p not in pkgs]
    assert not missing, f"requirements.txt 缺少核心依赖: {missing}"


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

    required = ["pytest", "pytest-asyncio", "pytest-cov", "mypy", "ruff", "httpx"]
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
    # ddgs can be from duckduckgo-search or duckduckgo_search
    has_ddg = any(d in pkgs for d in ["duckduckgo-search", "duckduckgo_search"])
    assert has_ddg, "requirements.txt 缺少 duckduckgo_search"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd backend && python -m pytest tests/test_requirements.py -v
```
Expected: test_runtime_requirements_excludes_dev_deps FAIL, test_dev_requirements_exists FAIL, test_ocr_requirements_exists FAIL, test_rerank_requirements_exists FAIL, test_web_search_deps_in_runtime FAIL

- [ ] **Step 3: 写入新的 requirements.txt（仅运行时依赖）**

```
fastapi==0.115.6
uvicorn[standard]==0.34.0
python-multipart==0.0.20
qdrant-client==1.13.0
pymupdf==1.25.3
python-docx==1.1.2
openpyxl==3.1.5
pandas==2.2.3
tiktoken==0.8.0
openai==1.70.0
pydantic==2.10.5
pydantic-settings==2.7.1
python-dotenv==1.0.1
sqlalchemy==2.0.36
aiosqlite==0.20.0
sse-starlette==2.2.1
slowapi>=0.1.9
jieba>=0.42
cryptography>=41.0
beautifulsoup4>=4.12
duckduckgo_search>=7.0
```

- [ ] **Step 4: 创建 requirements-dev.txt**

```
pytest>=9.0
pytest-asyncio>=1.0
pytest-cov>=6.0
mypy>=1.15
ruff>=0.10
httpx>=0.28
```

- [ ] **Step 5: 创建 requirements-ocr.txt**

```
# 安装 OCR 可选依赖:
# pip install -r requirements-ocr.txt
# paddlepaddle 需根据 CUDA 版本选择，详见:
# https://www.paddlepaddle.org.cn/install/quick

paddlepaddle>=3.0.0
paddleocr>=2.9
```

- [ ] **Step 6: 创建 requirements-rerank.txt**

```
# 安装重排序可选依赖 (需 torch):
# pip install -r requirements-rerank.txt

sentence-transformers>=3.0
torch>=2.0
```

- [ ] **Step 7: 运行测试确认通过（绿阶段）**

```bash
cd backend && python -m pytest tests/test_requirements.py -v
```
Expected: 全部 PASS

- [ ] **Step 8: Commit**

```bash
# .gitignore should already ignore .env but double-check
git add backend/requirements.txt backend/requirements-dev.txt backend/requirements-ocr.txt backend/requirements-rerank.txt backend/tests/test_requirements.py
git commit -m "refactor: split requirements into runtime/dev/ocr/rerank, add bs4 and ddgs"
```

---

### Task 4: WebSearch 默认关闭，修复已有测试

**Files:**
- Modify: `backend/config.py:67` (web_search_enabled: True → False)
- Modify: `backend/tests/agent/test_web_search.py` (有3个测试依赖于默认 enabled 行为)
- Modify: `backend/.env` (当前项目 .env 保持 web_search_enabled=true)

- [ ] **Step 1: 修改 config.py 默认值**

In `backend/config.py`, line 67:
```python
# Before:
web_search_enabled: bool = True
# After:
web_search_enabled: bool = False
```

- [ ] **Step 2: 运行已有 web search 测试，观察哪些失败**

```bash
cd backend && python -m pytest tests/agent/test_web_search.py -v 2>&1 | head -80
```
Expected: 以下测试将 FAIL（它们依赖默认 enabled）：
- `test_bing_fails_ddg_succeeds` — `tool.execute()` 第一行 `if not settings.web_search_enabled: return error`
- `test_bing_and_ddg_both_fail` — 同上
- `test_bing_retryable_ddg_retryable_raises_combined` — 同上
- `test_bing_success_skips_ddg` — 同上

- [ ] **Step 3: 在受影响测试中添加 `settings.web_search_enabled = True`**

In `backend/tests/agent/test_web_search.py`, update 4 tests in classes `TestWebSearchFallback` and `TestWebSearchMain`:

For `TestWebSearchFallback` — add at the start of each test method:
```python
from config import settings
settings.web_search_enabled = True
```

`test_bing_fails_ddg_succeeds` (line 122):
```python
async def test_bing_fails_ddg_succeeds(self, tool):
    """Bing timeout → DDG returns results → ToolResult(success=True)."""
    from config import settings
    settings.web_search_enabled = True

    bing_error = RetryableError("Bing 搜索超时")
    # ... rest unchanged
```

`test_bing_and_ddg_both_fail` (line 142):
```python
async def test_bing_and_ddg_both_fail(self, tool):
    """Bing returns empty, DDG raises → combined error message."""
    from config import settings
    settings.web_search_enabled = True

    bing_empty = ToolResult(success=True, data={"count": 0, "results": []})
    # ... rest unchanged
```

`test_bing_retryable_ddg_retryable_raises_combined` (line 155):
```python
async def test_bing_retryable_ddg_retryable_raises_combined(self, tool):
    """Both Bing and DDG raise RetryableError → combined RetryableError raised."""
    from config import settings
    settings.web_search_enabled = True

    bing_err = RetryableError("Bing 搜索超时")
    # ... rest unchanged
```

For `TestWebSearchMain.test_bing_success_skips_ddg` (line 187):
```python
async def test_bing_success_skips_ddg(self, tool):
    """When Bing returns results, DDG is not called."""
    from config import settings
    settings.web_search_enabled = True

    with patch.object(tool, "_search_bing") as mock_bing:
        # ... rest unchanged
```

- [ ] **Step 4: 运行测试确认全部 PASS**

```bash
cd backend && python -m pytest tests/agent/test_web_search.py -v
```
Expected: 全部 PASS (14 tests)

- [ ] **Step 5: 更新项目 .env 确保用户环境不受影响**

当前 `backend/.env` 中有 `WEB_SEARCH_ENABLED=true`，.env 文件中的值会覆盖默认值，所以用户现有环境不受影响。无需修改。

- [ ] **Step 6: 运行全部测试确认无回归**

```bash
cd backend && python -m pytest tests/ -v --timeout=120 2>&1 | tail -30
```
Expected: 全部 PASS

- [ ] **Step 7: Commit**

```bash
git add backend/config.py backend/tests/agent/test_web_search.py
git commit -m "fix: default web_search_enabled to false for safer first-run experience"
```
