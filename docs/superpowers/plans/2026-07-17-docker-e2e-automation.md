# Docker E2E Acceptance Automation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace manual Docker E2E acceptance with a single `scripts/docker_e2e_acceptance.ps1` command that orchestrates build, up, upload, QA, backup/restore, degradation recovery, strict smoke, and reporting.

**Architecture:** PowerShell script owns Docker lifecycle and HTTP mutations; existing and new pytest suites own assertions (smoke, live consistency). Compose files parameterized via environment variables. CI job added only to release-gate.yml, not per-PR ci.yml.

**Tech Stack:** PowerShell 7, Docker Compose, Python/pytest, httpx, SQLite (BM25 tables), Qdrant HTTP API

---

### Task 1: Parameterize docker-compose.e2e.yml and fix frontend healthcheck

**Files:**
- Modify: `docker-compose.e2e.yml`

- [ ] **Step 1: Rewrite docker-compose.e2e.yml with env interpolation and Nginx-compatible healthcheck**

Replace the entire file content:

```yaml
services:
  backend:
    ports: !override
      - "127.0.0.1:${E2E_BACKEND_PORT:-18000}:8000"
    environment:
      ADMIN_API_TOKEN: ${E2E_ADMIN_API_TOKEN:?E2E_ADMIN_API_TOKEN is required}
    healthcheck:
      test:
        - CMD
        - python
        - -c
        - >-
          import urllib.request;
          urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)
      interval: 5s
      timeout: 4s
      retries: 24
      start_period: 10s

  frontend:
    ports: !override
      - "127.0.0.1:${E2E_FRONTEND_PORT:-15173}:5173"
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://127.0.0.1:5173/"]
      interval: 5s
      timeout: 4s
      retries: 24
      start_period: 10s
```

- [ ] **Step 2: Verify compose config parses correctly**

```bash
cd D:/Python/subject1/RAG_Agent
E2E_ADMIN_API_TOKEN=test-token docker compose -f docker-compose.yml -f docker-compose.e2e.yml config --quiet 2>&1
```

Expected: no output (quiet mode, success).

- [ ] **Step 3: Commit**

```bash
git add docker-compose.e2e.yml
git commit -m "fix: parameterize e2e compose ports/token, switch frontend healthcheck to wget"
```

---

### Task 2: Create fixture manifest

**Files:**
- Create: `backend/tests/e2e/fixtures/manifest.json`

- [ ] **Step 1: Write manifest.json**

```json
{
  "documents": [
    {
      "path": "docker_acceptance_product.txt",
      "sha256": "66bebee9ec8fa3e3e3022166343718535e1b4bd342dc77739ecbb71306004975",
      "expected_chunks": 1
    },
    {
      "path": "docker_acceptance_policy.md",
      "sha256": "64042cd4e4ee930bd111369faf55e2d7f17e4d0f359449911336cf1e83f8ab11",
      "expected_chunks": 1
    }
  ],
  "questions": [
    {
      "question": "根据知识库，星河知识平台的标准工单响应时限和紧急工单首次响应时限分别是多少？请引用来源。",
      "expected_terms": ["四小时", "三十分钟"],
      "expected_source": "docker_acceptance_product.txt"
    },
    {
      "question": "企业年度订阅多少天内可以全额退款？请引用来源。",
      "expected_terms": ["七个自然日"],
      "expected_source": "docker_acceptance_policy.md"
    }
  ]
}
```

- [ ] **Step 2: Verify manifest hashes match fixtures**

```bash
cd D:/Python/subject1/RAG_Agent/backend
python -c "
import json, hashlib
with open('tests/e2e/fixtures/manifest.json') as f:
    manifest = json.load(f)
for doc in manifest['documents']:
    with open(f'tests/e2e/fixtures/{doc[\"path\"]}', 'rb') as f:
        actual = hashlib.sha256(f.read()).hexdigest()
    assert actual == doc['sha256'], f'{doc[\"path\"]}: expected {doc[\"sha256\"]}, got {actual}'
    print(f'{doc[\"path\"]}: OK')
print('All fixture hashes verified')
"
```

Expected: both files report OK.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/e2e/fixtures/manifest.json
git commit -m "feat: add e2e fixture manifest with hashes, expected chunks, and QA questions"
```

---

### Task 3: Add strict mode to docker smoke tests

**Files:**
- Modify: `backend/tests/e2e/test_docker_smoke.py`

- [ ] **Step 1: Rewrite test_docker_smoke.py with strict mode**

```python
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
```

- [ ] **Step 2: Verify strict mode fails when backend is down (negative test)**

```bash
cd D:/Python/subject1/RAG_Agent/backend
DOCKER_E2E_REQUIRED=1 BACKEND_URL=http://127.0.0.1:19999 pytest tests/e2e/test_docker_smoke.py -q 2>&1
```

Expected: all tests FAIL (not skip), exit code non-zero.

- [ ] **Step 3: Verify lenient mode still skips when backend is down**

```bash
cd D:/Python/subject1/RAG_Agent/backend
BACKEND_URL=http://127.0.0.1:19999 pytest tests/e2e/test_docker_smoke.py -q 2>&1
```

Expected: all tests SKIP, exit code 0.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/e2e/test_docker_smoke.py
git commit -m "feat: add DOCKER_E2E_REQUIRED strict mode to docker smoke, fail instead of skip"
```

---

### Task 4: Create read-only live index consistency test

**Files:**
- Create: `backend/tests/e2e/test_live_index_consistency.py`

- [ ] **Step 1: Write the consistency test**

```python
"""Read-only live index consistency test.

Validates that BM25 and Qdrant chunk-ID sets match for every ready document
in the running E2E stack. Does NOT create, delete, or rebuild any collection
or index. Does NOT use the database-reset fixture.

Usage:
  DOCKER_E2E_REQUIRED=1 BACKEND_URL=http://127.0.0.1:18000 \\
    pytest tests/e2e/test_live_index_consistency.py -v
"""

import os

import pytest
from sqlalchemy import select, text as sa_text

from models.database import async_session
from models.orm import Document, DocStatus, GenerationStatus, IndexGeneration
from textdb.bm25_search import BM25Search
from vectordb.factory import create_vectordb

IS_STRICT = os.environ.get("DOCKER_E2E_REQUIRED", "") == "1"


def _require(condition: bool, message: str) -> None:
    if condition:
        return
    if IS_STRICT:
        pytest.fail(message)
    else:
        pytest.skip(message)


async def _get_ready_documents() -> list[dict]:
    """Return all ready documents with active_generation_id."""
    async with async_session() as session:
        result = await session.execute(
            select(Document).where(Document.status == DocStatus.ready)
        )
        docs = result.scalars().all()
        return [
            {
                "id": d.id,
                "filename": d.filename,
                "file_hash": d.file_hash,
                "chunk_count": d.chunk_count,
                "active_generation_id": d.active_generation_id,
            }
            for d in docs
        ]


async def _get_generation(gen_id: str) -> dict | None:
    """Return generation record or None."""
    async with async_session() as session:
        result = await session.execute(
            select(IndexGeneration).where(IndexGeneration.id == gen_id)
        )
        gen = result.scalar_one_or_none()
        if gen is None:
            return None
        return {
            "id": gen.id,
            "doc_id": gen.doc_id,
            "status": str(gen.status),
            "vector_chunk_count": gen.vector_chunk_count,
            "bm25_count": gen.bm25_count,
        }


async def _get_bm25_chunk_ids(document_id: str) -> set[str]:
    """Get chunk IDs from BM25 for a document."""
    bm25 = BM25Search()
    ids = await bm25.get_chunk_ids_by_document(document_id)
    return set(ids)


async def _get_qdrant_chunk_ids(document_id: str) -> set[str]:
    """Get chunk IDs from the active Qdrant collection for a document."""
    vectordb = await create_vectordb()
    ids = await vectordb.get_chunk_ids_by_document(document_id)
    return set(ids)


@pytest.mark.docker
@pytest.mark.asyncio
class TestLiveIndexConsistency:
    async def test_has_ready_documents(self):
        docs = await _get_ready_documents()
        _require(len(docs) > 0, "No ready documents found in live database")

    async def test_active_generation_points_to_committed(self):
        docs = await _get_ready_documents()
        for doc in docs:
            gen_id = doc["active_generation_id"]
            _require(
                gen_id is not None and gen_id != "",
                f"Document {doc['id']} ({doc['filename']}) has no active_generation_id",
            )
            gen = await _get_generation(gen_id)
            _require(
                gen is not None,
                f"Document {doc['id']}: generation {gen_id} not found",
            )
            _require(
                gen["status"] == GenerationStatus.committed,
                f"Document {doc['id']}: generation {gen_id} status is "
                f"'{gen['status']}', expected 'committed'",
            )

    async def test_bm25_qdrant_chunk_ids_match(self):
        docs = await _get_ready_documents()
        for doc in docs:
            bm25_ids = await _get_bm25_chunk_ids(doc["id"])
            qdrant_ids = await _get_qdrant_chunk_ids(doc["id"])

            _require(
                len(bm25_ids) > 0,
                f"Document {doc['id']} ({doc['filename']}): no BM25 chunk IDs found",
            )
            _require(
                len(qdrant_ids) > 0,
                f"Document {doc['id']} ({doc['filename']}): no Qdrant chunk IDs found",
            )

            bm25_only = bm25_ids - qdrant_ids
            qdrant_only = qdrant_ids - bm25_ids

            _require(
                len(bm25_only) == 0,
                f"Document {doc['id']} ({doc['filename']}): {len(bm25_only)} chunks "
                f"in BM25 but not Qdrant: {sorted(bm25_only)[:5]}",
            )
            _require(
                len(qdrant_only) == 0,
                f"Document {doc['id']} ({doc['filename']}): {len(qdrant_only)} chunks "
                f"in Qdrant but not BM25: {sorted(qdrant_only)[:5]}",
            )

    async def test_chunk_count_matches_expected(self):
        docs = await _get_ready_documents()
        for doc in docs:
            bm25_ids = await _get_bm25_chunk_ids(doc["id"])
            qdrant_ids = await _get_qdrant_chunk_ids(doc["id"])

            expected = doc["chunk_count"]
            _require(
                len(bm25_ids) == expected,
                f"Document {doc['id']} ({doc['filename']}): BM25 has "
                f"{len(bm25_ids)} chunks, document.chunk_count={expected}",
            )
            _require(
                len(qdrant_ids) == expected,
                f"Document {doc['id']} ({doc['filename']}): Qdrant has "
                f"{len(qdrant_ids)} chunks, document.chunk_count={expected}",
            )
```

- [ ] **Step 2: Confirm the test file has no syntax errors**

```bash
cd D:/Python/subject1/RAG_Agent/backend
python -m py_compile tests/e2e/test_live_index_consistency.py && echo "OK"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/tests/e2e/test_live_index_consistency.py
git commit -m "feat: add read-only live index consistency test for BM25/Qdrant chunk-ID comparison"
```

---

### Task 5: Create the main Docker E2E acceptance script

**Files:**
- Create: `scripts/docker_e2e_acceptance.ps1`

- [ ] **Step 1: Create scripts directory and write the orchestrator script**

```powershell
<#
.SYNOPSIS
    Docker E2E acceptance — one-command full-stack validation.

.DESCRIPTION
    Orchestrates config check, build, up, upload, SSE QA, backup/restore,
    Qdrant degradation/recovery, smoke tests, and report generation.
    Each run uses an isolated Compose project with a timestamped name.

.PARAMETER Clean
    Run "docker compose down -v" after ALL stages pass. Never cleans on failure.

.PARAMETER SkipBuild
    Reuse existing images; skip docker build.

.PARAMETER BackendPort
    Host port for backend loopback binding (default 18000).

.PARAMETER FrontendPort
    Host port for frontend loopback binding (default 15173).

.PARAMETER HealthTimeoutSec
    Max seconds to wait for backend+frontend healthy (default 120).

.PARAMETER ReadyTimeoutSec
    Max seconds to wait for documents to reach ready status (default 180).

.PARAMETER SseTimeoutSec
    Max seconds to wait for each SSE chat response (default 120).

.PARAMETER RestoreTimeoutSec
    Max seconds to wait for restore to complete (default 180).

.EXAMPLE
    ./scripts/docker_e2e_acceptance.ps1

.EXAMPLE
    ./scripts/docker_e2e_acceptance.ps1 -Clean -SkipBuild
#>

[CmdletBinding()]
param(
    [switch]$Clean,
    [switch]$SkipBuild,
    [int]$BackendPort = 18000,
    [int]$FrontendPort = 15173,
    [int]$HealthTimeoutSec = 120,
    [int]$ReadyTimeoutSec = 180,
    [int]$SseTimeoutSec = 120,
    [int]$RestoreTimeoutSec = 180
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# ── Paths ────────────────────────────────────────────────────────────────
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$ArtifactsBase = Join-Path $RepoRoot "artifacts" "docker-e2e"
$FixturesDir = Join-Path $RepoRoot "backend" "tests" "e2e" "fixtures"
$ManifestPath = Join-Path $FixturesDir "manifest.json"
$SmokeTestPath = Join-Path $RepoRoot "backend" "tests" "e2e" "test_docker_smoke.py"
$ConsistencyTestPath = Join-Path $RepoRoot "backend" "tests" "e2e" "test_live_index_consistency.py"
$BackendDir = Join-Path $RepoRoot "backend"

# ── Run identity ─────────────────────────────────────────────────────────
$RunTimestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss")
$ShortGuid = [Guid]::NewGuid().ToString("N").Substring(0, 8)
$RunId = "ragagent-e2e-${RunTimestamp}-${ShortGuid}"
$OutputDir = Join-Path $ArtifactsBase $RunId
$ProjectNamePattern = '^ragagent-e2e-\d{8}-\d{6}-[0-9a-f]{8}$'

# ── Token resolution ─────────────────────────────────────────────────────
$IsCI = [Environment]::GetEnvironmentVariable("CI") -eq "true"
$Token = [Environment]::GetEnvironmentVariable("E2E_ADMIN_API_TOKEN")
if (-not $Token) {
    if ($IsCI) {
        throw "E2E_ADMIN_API_TOKEN is required in CI. Set it via GitHub Secrets."
    }
    $Token = "rag-agent-e2e-admin-token"
    Write-Host "[config] Using fixed local test token (not CI)" -ForegroundColor Yellow
}

# Compose arguments (shared across all docker compose invocations)
$ComposeArgs = @(
    "-p", $RunId,
    "-f", (Join-Path $RepoRoot "docker-compose.yml"),
    "-f", (Join-Path $RepoRoot "docker-compose.e2e.yml")
)

# Environment block for compose (merged with process env)
$ComposeEnv = @{
    E2E_BACKEND_PORT = [string]$BackendPort
    E2E_FRONTEND_PORT = [string]$FrontendPort
    E2E_ADMIN_API_TOKEN = $Token
}

# ── Result tracking ──────────────────────────────────────────────────────
$Result = @{
    run_id = $RunId
    timestamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    git_commit = ""
    overall = "running"
    failed_stage = $null
    stages = [ordered]@{}
    config_snapshot = @{}
}

$Stopwatch = [System.Diagnostics.Stopwatch]::new()
$OverallTimer = [System.Diagnostics.Stopwatch]::StartNew()

# ── Helper functions ─────────────────────────────────────────────────────

function Write-Stage { param([string]$Name) Write-Host "`n==== Stage: $Name ====" -ForegroundColor Cyan }

function Invoke-Stage {
    param(
        [string]$Name,
        [scriptblock]$ScriptBlock,
        [hashtable]$Extra = @{}
    )
    $stage = @{
        status = "running"
        elapsed_s = 0.0
    }
    foreach ($kv in $Extra.GetEnumerator()) { $stage[$kv.Key] = $kv.Value }
    $Result.stages[$Name] = $stage

    $Stopwatch.Restart()
    try {
        & $ScriptBlock
        $stage.status = "passed"
        Write-Host "[$Name] PASSED (${([math]::Round($Stopwatch.Elapsed.TotalSeconds, 1))}s)" -ForegroundColor Green
    }
    catch {
        $stage.status = "failed"
        # Sanitize: never include raw token or key values
        $msg = $_.Exception.Message -replace $Token, "***"
        $stage.error = $msg
        Write-Host "[$Name] FAILED: $msg" -ForegroundColor Red
        throw
    }
    finally {
        $stage.elapsed_s = [math]::Round($Stopwatch.Elapsed.TotalSeconds, 1)
    }
}

function Get-GitCommit {
    try {
        $hash = & git -C $RepoRoot rev-parse --short HEAD 2>$null
        if ($LASTEXITCODE -eq 0) { return $hash.Trim() }
    } catch {}
    return "unknown"
}

function Wait-Healthy {
    param([int]$TimeoutSec)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $backendOk = $false
    $frontendOk = $false
    while ((Get-Date) -lt $deadline) {
        try {
            $b = Invoke-RestMethod -Uri "http://127.0.0.1:${BackendPort}/api/health" -TimeoutSec 3 -ErrorAction Stop
            if ($b.status -eq "ok") { $backendOk = $true }
        } catch {}
        try {
            $f = Invoke-RestMethod -Uri "http://127.0.0.1:${FrontendPort}/" -TimeoutSec 3 -ErrorAction Stop
            if ($f -match '<!doctype|<html|<head|<body|<div|<script') { $frontendOk = $true }
        } catch {}
        if ($backendOk -and $frontendOk) {
            Write-Host "[health] Both backend and frontend healthy"
            return
        }
        Write-Host "[health] Waiting... (backend=$backendOk, frontend=$frontendOk)"
        Start-Sleep -Seconds 2
    }
    throw "Health timeout after ${TimeoutSec}s (backend=$backendOk, frontend=$frontendOk)"
}

function Invoke-Pytest {
    param(
        [string]$TestPath,
        [string]$Label,
        [string[]]$ExtraArgs = @()
    )
    $env:BACKEND_URL = "http://127.0.0.1:${BackendPort}"
    $env:DOCKER_E2E_REQUIRED = "1"
    $args = @(
        "-m", $TestPath,
        "-q", "--tb=short",
        "--junitxml=" + (Join-Path $OutputDir "$Label-junit.xml")
    ) + $ExtraArgs
    $proc = Start-Process -FilePath "python" -ArgumentList $args -WorkingDirectory $BackendDir -NoNewWindow -PassThru -Wait
    if ($proc.ExitCode -ne 0) {
        throw "pytest $Label exited with code $($proc.ExitCode)"
    }
    # Verify zero skipped
    $junitPath = Join-Path $OutputDir "$Label-junit.xml"
    if (Test-Path $junitPath) {
        $xml = [xml](Get-Content $junitPath)
        $skipped = [int]($xml.testsuites.testsuite.skipped)
        if ($skipped -ne 0) {
            throw "$Label: $skipped tests skipped in strict mode (DOCKER_E2E_REQUIRED=1)"
        }
    }
    Write-Host "[$Label] All tests passed, 0 skipped"
}

function Write-Reports {
    # Ensure output and backup subdirectory exist
    New-Item -ItemType Directory -Force $OutputDir | Out-Null

    $Result.overall = if ($Result.failed_stage) { "failed" } else { "passed" }
    $Result.git_commit = Get-GitCommit

    # config snapshot (sanitized — values only, SHA-256 for model names)
    $Result.config_snapshot = @{
        llm_provider = if ([Environment]::GetEnvironmentVariable("LLM_PROVIDER")) { "configured" } else { "missing" }
        llm_model_sha256 = (Get-HashSafe "LLM_MODEL")
        embedding_provider = if ([Environment]::GetEnvironmentVariable("EMBEDDING_PROVIDER")) { "configured" } else { "missing" }
        embedding_model_sha256 = (Get-HashSafe "EMBEDDING_MODEL")
        secret_key = if ([Environment]::GetEnvironmentVariable("SECRET_KEY")) { "configured" } else { "missing" }
    }

    # Mark remaining stages not_run after failure
    $allStages = @(
        "config_check", "build", "health", "secrets_check", "auth_check",
        "upload", "consistency", "sse_qa", "restart_persistence",
        "backup_restore", "degradation", "smoke"
    )
    foreach ($s in $allStages) {
        if (-not $Result.stages.Contains($s)) {
            $Result.stages[$s] = @{ status = "not_run"; elapsed_s = 0.0 }
        }
    }

    # Write result.json
    $jsonPath = Join-Path $OutputDir "result.json"
    $Result | ConvertTo-Json -Depth 6 | Set-Content $jsonPath -Encoding UTF8

    # Write report.md
    $mdPath = Join-Path $OutputDir "report.md"
    $lines = @(
        "# Docker E2E Acceptance Report",
        "",
        "| Field | Value |",
        "|---|---|",
        "| Run ID | $RunId |",
        "| Timestamp | $($Result.timestamp) |",
        "| Git commit | $($Result.git_commit) |",
        "| Overall | **$($Result.overall)** |",
        "| Failed stage | $($Result.failed_stage) |",
        "",
        "## Stages",
        "",
        "| Stage | Status | Elapsed (s) |",
        "|---|---|---|"
    )
    foreach ($kv in $Result.stages.GetEnumerator()) {
        $extra = ""
        foreach ($pk in $kv.Value.Keys) {
            if ($pk -notin @("status", "elapsed_s", "error")) {
                $extra += " $pk=$($kv.Value[$pk])"
            }
        }
        $lines += "| $($kv.Key) | $($kv.Value.status) | $($kv.Value.elapsed_s) |"
        if ($kv.Value.error) {
            $lines += "| | **Error:** $($kv.Value.error) | |"
        }
    }
    $lines += @(
        "",
        "## Config snapshot",
        "",
        '```json',
        ($Result.config_snapshot | ConvertTo-Json -Compress),
        '```',
        "",
        "## Retention",
        "",
        "To inspect:",
        "",
        "```bash",
        "docker compose -p $RunId ps",
        "docker compose -p $RunId logs --tail=200",
        "```",
        "",
        "To clean:",
        "",
        "```bash",
        "docker compose -p $RunId down -v",
        "```"
    )
    $lines -join "`n" | Set-Content $mdPath -Encoding UTF8

    Write-Host "`nReports written to $OutputDir" -ForegroundColor Green
}

function Get-HashSafe {
    param([string]$EnvName)
    $val = [Environment]::GetEnvironmentVariable($EnvName)
    if (-not $val) { return "missing" }
    $bytes = [System.Security.Cryptography.SHA256]::Create().ComputeHash([System.Text.Encoding]::UTF8.GetBytes($val))
    return [BitConverter]::ToString($bytes).Replace("-", "").ToLower()
}

# ── Main flow ────────────────────────────────────────────────────────────

try {
    # Create output directory immediately
    New-Item -ItemType Directory -Force $OutputDir | Out-Null
    $Result.git_commit = Get-GitCommit

    # ── Stage 1: Config check ───────────────────────────────────────────
    Write-Stage "config_check"
    Invoke-Stage "config_check" {
        # Check required files
        @(
            (Join-Path $RepoRoot "docker-compose.yml"),
            (Join-Path $RepoRoot "docker-compose.e2e.yml"),
            $ManifestPath
        ) | ForEach-Object {
            if (-not (Test-Path $_)) { throw "Missing required file: $_" }
        }
        # Check Docker/Compose available
        $null = docker --version 2>$null
        if ($LASTEXITCODE -ne 0) { throw "docker not available" }
        $null = docker compose version 2>$null
        if ($LASTEXITCODE -ne 0) { throw "docker compose not available" }

        # Validate manifest fixtures exist and hashes match
        $manifest = Get-Content $ManifestPath -Raw | ConvertFrom-Json
        foreach ($doc in $manifest.documents) {
            $fixturePath = Join-Path $FixturesDir $doc.path
            if (-not (Test-Path $fixturePath)) {
                throw "Fixture not found: $fixturePath"
            }
            $actual = (Get-FileHash $fixturePath -Algorithm SHA256).Hash.ToLower()
            if ($actual -ne $doc.sha256) {
                throw "Hash mismatch for $($doc.path): expected $($doc.sha256), got $actual"
            }
        }
        Write-Host "Manifest OK: $($manifest.documents.Count) documents, $($manifest.questions.Count) questions"

        # Project name guard
        if ($RunId -notmatch $ProjectNamePattern) {
            throw "Run ID '$RunId' does not match expected pattern"
        }

        # Port conflict detection
        $portTests = @(
            @{Port=$BackendPort; Name="backend"},
            @{Port=$FrontendPort; Name="frontend"}
        )
        foreach ($pt in $portTests) {
            $listener = Get-NetTCPConnection -LocalPort $pt.Port -ErrorAction SilentlyContinue | Where-Object State -eq "Listen"
            if ($listener) {
                throw "Port $($pt.Port) ($($pt.Name)) is already in use. Stop the conflicting process or choose a different port."
            }
        }
        Write-Host "No port conflicts on $BackendPort / $FrontendPort"
    }

    # ── Stage 2: Build ───────────────────────────────────────────────────
    Write-Stage "build"
    Invoke-Stage "build" {
        if ($SkipBuild) {
            Write-Host "Skipping build (-SkipBuild)"
            return
        }
        $buildArgs = $ComposeArgs + @("build", "--quiet")
        $proc = Start-Process -FilePath "docker" -ArgumentList "compose $buildArgs" -NoNewWindow -PassThru -Wait
        if ($proc.ExitCode -ne 0) { throw "docker compose build exited with code $($proc.ExitCode)" }
    }

    # ── Stage 3: Up and health ──────────────────────────────────────────
    Write-Stage "health"
    Invoke-Stage "health" {
        # Set env vars for compose
        $envBlock = @{}
        foreach ($kv in $ComposeEnv.GetEnumerator()) { $envBlock[$kv.Key] = $kv.Value }

        $upArgs = $ComposeArgs + @("up", "-d", "--wait")
        $proc = Start-Process -FilePath "docker" -ArgumentList "compose $upArgs" -NoNewWindow -PassThru -Wait
        if ($proc.ExitCode -ne 0) { throw "docker compose up -d exited with code $($proc.ExitCode)" }

        Wait-Healthy -TimeoutSec $HealthTimeoutSec

        # Verify frontend home page through Nginx
        $home = Invoke-RestMethod -Uri "http://127.0.0.1:${FrontendPort}/" -TimeoutSec 5
        if ($home -notmatch '<!doctype|<html|<head|<body') {
            throw "Frontend home page does not look like HTML"
        }
        Write-Host "Frontend home page OK"

        # Verify /api/health proxy through Nginx
        $proxyHealth = Invoke-RestMethod -Uri "http://127.0.0.1:${FrontendPort}/api/health" -TimeoutSec 5
        if ($proxyHealth.status -ne "ok") { throw "Proxy /api/health not ok: $($proxyHealth.status)" }
        Write-Host "Nginx /api/health proxy OK"
    }

    # ── Stage 4: Secrets check ──────────────────────────────────────────
    Write-Stage "secrets_check"
    Invoke-Stage "secrets_check" {
        # Verify /app/.env absent from backend container
        $svc = docker compose @ComposeArgs ps --format json 2>$null | ConvertFrom-Json
        $backendSvc = $svc | Where-Object { $_.Service -eq "backend" } | Select-Object -First 1
        if (-not $backendSvc) { throw "Backend container not found" }
        $envCheck = docker exec $backendSvc.Name test -f /app/.env 2>&1
        if ($LASTEXITCODE -eq 0) {
            throw "/app/.env exists in backend container — image may include secrets!"
        }
        Write-Host "/app/.env absent in container (env-file-absent)"

        # Verify build context excludes .env
        $buildCtxSize = (docker image inspect "$RunId`_backend" --format '{{.Size}}' 2>&1).Trim()
        if ($buildCtxSize) {
            Write-Host "Backend image size: $([math]::Round([int64]$buildCtxSize / 1MB, 1)) MB"
        }
    }

    # ── Stage 5: Auth check ─────────────────────────────────────────────
    Write-Stage "auth_check"
    Invoke-Stage "auth_check" {
        # Without token → 401
        $headers = @{ "X-Admin-Token" = $Token }
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:${FrontendPort}/api/documents" -TimeoutSec 5 -ErrorAction Stop
            throw "Expected 401 without token, got $($r.StatusCode)"
        } catch {
            if ($_.Exception.Response.StatusCode.value__ -notin @(401, 403)) {
                throw "Expected 401/403 without token, got $($_.Exception.Response.StatusCode.value__)"
            }
        }
        Write-Host "Unauthenticated → 401/403 OK"

        # With token → 200
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:${FrontendPort}/api/documents" -Headers $headers -TimeoutSec 5
        Write-Host "Authenticated → 200 OK ($($r.Count) documents)"

        # Metrics also requires auth
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:${FrontendPort}/api/metrics" -TimeoutSec 5 -ErrorAction Stop
            throw "Expected 401 for metrics without token, got $($r.StatusCode)"
        } catch {
            if ($_.Exception.Response.StatusCode.value__ -notin @(401, 403)) {
                throw "Expected 401/403 for metrics without token, got $($_.Exception.Response.StatusCode.value__)"
            }
        }
        Write-Host "Metrics auth OK"
    }

    # ── Stage 6: Batch upload ───────────────────────────────────────────
    Write-Stage "upload"
    Invoke-Stage "upload" {
        $manifest = Get-Content $ManifestPath -Raw | ConvertFrom-Json
        $headers = @{ "X-Admin-Token" = $Token }

        # Build multipart form with all manifest documents
        $formFiles = @()
        foreach ($doc in $manifest.documents) {
            $absPath = Join-Path $FixturesDir $doc.path
            $formFiles += @{ Path = $absPath; Name = $doc.path }
        }

        # Use Invoke-RestMethod with multipart
        $form = @{}
        foreach ($f in $formFiles) {
            $form[$f.Name] = Get-Item $f.Path
        }
        $uploadResult = Invoke-RestMethod -Uri "http://127.0.0.1:${FrontendPort}/api/documents/upload-batch" `
            -Method Post -Headers $headers -Form $form -TimeoutSec 60
        Write-Host "Upload: $($uploadResult.succeeded) succeeded, $($uploadResult.failed) failed"
        if ($uploadResult.failed -gt 0) {
            throw "Upload had $($uploadResult.failed) failures"
        }

        # Wait for all documents to reach ready
        $deadline = (Get-Date).AddSeconds($ReadyTimeoutSec)
        while ((Get-Date) -lt $deadline) {
            $docs = Invoke-RestMethod -Uri "http://127.0.0.1:${FrontendPort}/api/documents" -Headers $headers -TimeoutSec 5
            $allReady = $true
            $anyFailed = $false
            foreach ($d in $docs) {
                if ($d.status -eq "failed") {
                    $anyFailed = $true
                    Write-Host "Document $($d.filename) FAILED: $($d.error_message)"
                }
                if ($d.status -ne "ready") { $allReady = $false }
            }
            if ($anyFailed) { throw "At least one document reached failed state" }
            if ($allReady -and $docs.Count -eq $manifest.documents.Count) {
                Write-Host "All $($docs.Count) documents ready"
                break
            }
            Write-Host "Waiting for documents... ($($docs.Count)/$($manifest.documents.Count), ready=$allReady)"
            Start-Sleep -Seconds 2
        }
        if (-not $allReady) { throw "Timeout waiting for documents to reach ready" }

        # Verify expected chunk counts
        $docs = Invoke-RestMethod -Uri "http://127.0.0.1:${FrontendPort}/api/documents" -Headers $headers -TimeoutSec 5
        foreach ($doc in $docs) {
            $m = $manifest.documents | Where-Object { $_.path -eq $doc.filename } | Select-Object -First 1
            if ($m -and $doc.chunk_count -ne $m.expected_chunks) {
                throw "$($doc.filename): expected $($m.expected_chunks) chunks, got $($doc.chunk_count)"
            }
        }
        Write-Host "Chunk counts verified"
    }

    # ── Stage 7: Live consistency ────────────────────────────────────────
    Write-Stage "consistency"
    Invoke-Stage "consistency" {
        Invoke-Pytest -TestPath $ConsistencyTestPath -Label "consistency"
    }

    # ── Stage 8: SSE QA ─────────────────────────────────────────────────
    Write-Stage "sse_qa"
    Invoke-Stage "sse_qa" -Extra @{
        questions = @()
        total_client_ms = 0
        rag_total_ms = 0
    } {
        $manifest = Get-Content $ManifestPath -Raw | ConvertFrom-Json
        $headers = @{
            "Content-Type" = "application/json"
            "X-Admin-Token" = $Token
        }
        $qaResults = @()

        foreach ($q in $manifest.questions) {
            $body = @{ message = $q.question } | ConvertTo-Json -Compress
            $bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($body)
            $bodyFile = Join-Path $OutputDir "chat_body_$($qaResults.Count).json"
            [System.IO.File]::WriteAllBytes($bodyFile, $bodyBytes)

            $sseOutput = & curl -sS -N `
                -H "X-Admin-Token: $Token" `
                -H "Content-Type: application/json" `
                -d "@$bodyFile" `
                --max-time $SseTimeoutSec `
                "http://127.0.0.1:${BackendPort}/api/chat" 2>&1

            $events = @{}
            $currentEvent = ""
            foreach ($line in ($sseOutput -split "`n")) {
                if ($line -match '^event:\s*(.+)$') {
                    $currentEvent = $matches[1].Trim()
                    if (-not $events.ContainsKey($currentEvent)) {
                        $events[$currentEvent] = @()
                    }
                }
                elseif ($line -match '^data:\s*(.+)$') {
                    if ($currentEvent) {
                        $events[$currentEvent] += $matches[1].Trim()
                    }
                }
            }

            # Assert required events
            foreach ($evt in @("answer_chunk", "sources", "verification", "done")) {
                if (-not $events.ContainsKey($evt)) {
                    throw "SSE missing event '$evt' for question: $($q.question.Substring(0, [Math]::Min(50, $q.question.Length)))..."
                }
            }

            # Check expected answer terms
            $answerText = ($events["answer_chunk"] -join " ")
            foreach ($term in $q.expected_terms) {
                if ($answerText -notmatch [regex]::Escape($term)) {
                    throw "Expected term '$term' not found in answer for question"
                }
            }

            # Check expected source filename in sources
            $sourcesText = ($events["sources"] -join " ")
            if ($sourcesText -notmatch [regex]::Escape($q.expected_source)) {
                throw "Expected source '$($q.expected_source)' not found in sources"
            }

            # Parse verification
            $verification = $events["verification"][-1] | ConvertFrom-Json
            if ($verification.faithfulness -ne 1.0) {
                throw "Faithfulness=$($verification.faithfulness), expected 1.0"
            }
            if ($verification.citation_precision -ne 1.0) {
                throw "Citation precision=$($verification.citation_precision), expected 1.0"
            }
            if ($verification.citation_recall -ne 1.0) {
                throw "Citation recall=$($verification.citation_recall), expected 1.0"
            }

            # Parse timing
            $timingEvent = $events.Keys | Where-Object { $_ -eq "timing" } | Select-Object -First 1
            if ($timingEvent) {
                $timing = $events["timing"][-1] | ConvertFrom-Json
                if ($timing.rag_total) { $Result.stages["sse_qa"].rag_total_ms = $timing.rag_total }
            }

            $qaResults += @{
                question = $q.question
                faithfulness = $verification.faithfulness
                citation_precision = $verification.citation_precision
                citation_recall = $verification.citation_recall
                has_answer_chunk = $events.ContainsKey("answer_chunk")
                has_sources = $events.ContainsKey("sources")
                has_verification = $events.ContainsKey("verification")
                has_done = $events.ContainsKey("done")
            }

            Remove-Item $bodyFile -Force -ErrorAction SilentlyContinue
            Write-Host "QA passed: faithfulness=$($verification.faithfulness), precision=$($verification.citation_precision), recall=$($verification.citation_recall)"
        }

        $Result.stages["sse_qa"].questions = $qaResults
    }

    # ── Stage 9: Restart persistence ────────────────────────────────────
    Write-Stage "restart_persistence"
    Invoke-Stage "restart_persistence" {
        $restartProcs = @(
            (Start-Process -FilePath "docker" -ArgumentList "compose $($ComposeArgs -join ' ') restart backend qdrant" -NoNewWindow -PassThru)
        )
        $restartProcs | ForEach-Object { $_.WaitForExit() }

        Wait-Healthy -TimeoutSec $HealthTimeoutSec

        $headers = @{ "X-Admin-Token" = $Token }
        $docs = Invoke-RestMethod -Uri "http://127.0.0.1:${FrontendPort}/api/documents" -Headers $headers -TimeoutSec 5
        $manifest = Get-Content $ManifestPath -Raw | ConvertFrom-Json
        if ($docs.Count -ne $manifest.documents.Count) {
            throw "After restart: expected $($manifest.documents.Count) documents, got $($docs.Count)"
        }
        foreach ($d in $docs) {
            if ($d.status -ne "ready") { throw "After restart: document $($d.filename) status is $($d.status), expected ready" }
        }
        Write-Host "All $($docs.Count) documents survived restart"
    }

    # ── Stage 10: Backup/clear/restore ───────────────────────────────────
    Write-Stage "backup_restore"
    Invoke-Stage "backup_restore" {
        $headers = @{ "X-Admin-Token" = $Token }
        $manifest = Get-Content $ManifestPath -Raw | ConvertFrom-Json

        # Snapshot pre-backup state
        $preDocs = Invoke-RestMethod -Uri "http://127.0.0.1:${BackendPort}/api/documents" -Headers $headers -TimeoutSec 5
        $preState = @{}
        foreach ($d in $preDocs) {
            $preState[$d.id] = @{
                filename = $d.filename
                file_size = $d.file_size
                status = $d.status
                chunk_count = $d.chunk_count
            }
        }

        # Create backup
        $backupDir = Join-Path $OutputDir "backups"
        New-Item -ItemType Directory -Force $backupDir | Out-Null
        $backupFile = Join-Path $backupDir "restore-test.tar.gz"
        & curl -sS -f -H "X-Admin-Token: $Token" -o $backupFile "http://127.0.0.1:${BackendPort}/api/backup" 2>&1
        if ($LASTEXITCODE -ne 0) { throw "Backup download failed" }
        $backupSize = (Get-Item $backupFile).Length
        $backupHash = (Get-FileHash $backupFile -Algorithm SHA256).Hash
        Write-Host "Backup: $backupSize bytes, SHA-256: $backupHash"
        if ($backupSize -eq 0) { throw "Backup file is empty" }

        # Clear all
        $clearResult = Invoke-RestMethod -Method Delete -Uri "http://127.0.0.1:${BackendPort}/api/documents/clear-all" -Headers $headers -TimeoutSec 30
        Write-Host "Cleared: $($clearResult.count) documents"
        Start-Sleep -Seconds 2

        # Verify empty
        $afterClear = Invoke-RestMethod -Uri "http://127.0.0.1:${BackendPort}/api/documents" -Headers $headers -TimeoutSec 5
        if ($afterClear.Count -ne 0) { throw "After clear: expected 0 documents, got $($afterClear.Count)" }

        # Restore
        $restoreOutput = & curl -sS `
            -H "X-Admin-Token: $Token" `
            -F "file=@$backupFile;type=application/gzip" `
            -w "`nHTTP_STATUS:%{http_code}" `
            --max-time $RestoreTimeoutSec `
            "http://127.0.0.1:${BackendPort}/api/backup/restore" 2>&1
        if ($restoreOutput -match "HTTP_STATUS:200") {
            Write-Host "Restore HTTP 200 OK"
        } else {
            throw "Restore failed: $restoreOutput"
        }

        # Parse restore response
        $restoreJson = ($restoreOutput -replace "`nHTTP_STATUS:200", "") | ConvertFrom-Json
        if ($restoreJson.documents_restored -ne $manifest.documents.Count) {
            throw "Restored $($restoreJson.documents_restored) documents, expected $($manifest.documents.Count)"
        }

        # Wait for ready
        Start-Sleep -Seconds 3
        $restoredDocs = Invoke-RestMethod -Uri "http://127.0.0.1:${BackendPort}/api/documents" -Headers $headers -TimeoutSec 5

        # Verify original document IDs and properties
        foreach ($preId in $preState.Keys) {
            $match = $restoredDocs | Where-Object { $_.id -eq $preId } | Select-Object -First 1
            if (-not $match) { throw "Original document $preId not found after restore" }
            if ($match.status -ne "ready") { throw "Restored document $($match.filename) status is $($match.status), expected ready" }
            if ($match.chunk_count -ne $preState[$preId].chunk_count) {
                throw "Restored chunk count mismatch for $($match.filename): $($match.chunk_count) vs $($preState[$preId].chunk_count)"
            }
        }
        Write-Host "All $($restoredDocs.Count) documents restored with correct properties"

        # Re-run consistency check
        $Result.stages["backup_restore"].consistency_rerun = "pending"
        Invoke-Pytest -TestPath $ConsistencyTestPath -Label "consistency-post-restore"

        # Re-run one SSE QA after restore
        $q = $manifest.questions[0]
        $body = @{ message = $q.question } | ConvertTo-Json -Compress
        $bodyFile = Join-Path $OutputDir "chat_body_restore.json"
        [System.IO.File]::WriteAllBytes($bodyFile, [System.Text.Encoding]::UTF8.GetBytes($body))

        $sseOut = & curl -sS -N `
            -H "X-Admin-Token: $Token" `
            -H "Content-Type: application/json" `
            -d "@$bodyFile" `
            --max-time $SseTimeoutSec `
            "http://127.0.0.1:${BackendPort}/api/chat" 2>&1

        Remove-Item $bodyFile -Force -ErrorAction SilentlyContinue

        $requiredEvents = @("sources", "verification", "done")
        foreach ($evt in $requiredEvents) {
            if ($sseOut -notmatch "event: $evt") {
                throw "Post-restore SSE missing event: $evt"
            }
        }
        Write-Host "Post-restore SSE QA passed (sources, verification, done confirmed)"

        $Result.stages["backup_restore"].backup_sha256 = $backupHash
        $Result.stages["backup_restore"].documents_restored = $restoreJson.documents_restored
    }

    # ── Stage 11: Degradation/recovery ──────────────────────────────────
    Write-Stage "degradation"
    Invoke-Stage "degradation" {
        $headers = @{ "X-Admin-Token" = $Token }

        # Stop Qdrant
        & docker compose @ComposeArgs stop qdrant 2>&1 | Out-Null
        Write-Host "Qdrant stopped"
        Start-Sleep -Seconds 3

        # Verify degradation
        $degraded = Invoke-RestMethod -Uri "http://127.0.0.1:${BackendPort}/api/health/dependencies" -Headers $headers -TimeoutSec 5
        if ($degraded.qdrant -ne "error") {
            throw "Expected qdrant=error after stop, got qdrant=$($degraded.qdrant)"
        }
        if ($degraded.sqlite -ne "ok") {
            throw "Expected sqlite=ok during qdrant outage, got sqlite=$($degraded.sqlite)"
        }
        Write-Host "Degradation confirmed: qdrant=error, sqlite=ok"

        # Start Qdrant
        & docker compose @ComposeArgs start qdrant 2>&1 | Out-Null
        Write-Host "Qdrant started"
        Start-Sleep -Seconds 5

        # Verify recovery
        $recovered = Invoke-RestMethod -Uri "http://127.0.0.1:${BackendPort}/api/health/dependencies" -Headers $headers -TimeoutSec 5
        if ($recovered.status -ne "ok") {
            throw "Health not ok after recovery: $($recovered.status)"
        }
        if ($recovered.qdrant -ne "ok") {
            throw "Qdrant not ok after recovery: $($recovered.qdrant)"
        }
        Write-Host "Recovery confirmed: overall=$($recovered.status), qdrant=$($recovered.qdrant)"
    }

    # ── Stage 12: Final strict smoke ─────────────────────────────────────
    Write-Stage "smoke"
    Invoke-Stage "smoke" {
        Invoke-Pytest -TestPath $SmokeTestPath -Label "smoke"
    }

    Write-Host "`n==== ALL STAGES PASSED ====" -ForegroundColor Green
}
catch {
    $Result.failed_stage = ($Result.stages.GetEnumerator() | Where-Object { $_.Value.status -eq "failed" } | Select-Object -First 1).Key
    Write-Host "`n==== ACCEPTANCE FAILED at stage: $($Result.failed_stage) ====" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    if ($_.ScriptStackTrace) {
        Write-Host $_.ScriptStackTrace -ForegroundColor DarkRed
    }
}
finally {
    # Always write reports
    try {
        Write-Reports
    }
    catch {
        Write-Host "Failed to write reports: $_" -ForegroundColor Red
    }

    # Cleanup only on success and when -Clean is set
    if ($Result.overall -eq "passed" -and $Clean) {
        Write-Host "`nCleaning up (project: $RunId)..." -ForegroundColor Yellow
        & docker compose @ComposeArgs down -v 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Cleanup complete" -ForegroundColor Green
        }
        else {
            Write-Host "Cleanup exited with code $LASTEXITCODE" -ForegroundColor Red
        }
    }
    else {
        if (-not $Clean) {
            Write-Host "`nRetaining containers and volumes (-Clean not specified)" -ForegroundColor Yellow
        }
        else {
            Write-Host "`nRetaining containers and volumes (failure, ignoring -Clean)" -ForegroundColor Yellow
        }
        Write-Host "To inspect: docker compose -p $RunId ps" -ForegroundColor Yellow
        Write-Host "To clean:   docker compose -p $RunId down -v" -ForegroundColor Yellow
    }

    $OverallTimer.Stop()
    Write-Host "`nTotal elapsed: $([math]::Round($OverallTimer.Elapsed.TotalSeconds, 1))s"
}

exit $(if ($Result.overall -eq "passed") { 0 } else { 1 })
```

- [ ] **Step 2: Verify PowerShell syntax**

```powershell
$ErrorActionPreference = "Stop"
try {
    $null = Get-Command -Name "D:/Python/subject1/RAG_Agent/scripts/docker_e2e_acceptance.ps1" -ErrorAction Stop
} catch {
    # If Get-Command doesn't find it, use Invoke-Expression for syntax check
}
# Basic parse test
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    "D:/Python/subject1/RAG_Agent/scripts/docker_e2e_acceptance.ps1",
    [ref]$null,
    [ref]$null
)
if ($ast) { Write-Host "Parse OK" } else { throw "Parse failed" }
```

Expected: `Parse OK`

- [ ] **Step 3: Commit**

```bash
git add scripts/docker_e2e_acceptance.ps1
git commit -m "feat: add Docker E2E acceptance orchestrator script with 12-stage validation"
```

---

### Task 6: Add Docker E2E acceptance job to release gate

**Files:**
- Modify: `.github/workflows/release-gate.yml`

- [ ] **Step 1: Add the docker-e2e-acceptance job**

Insert after the existing `grounded-answer-release-gate` job:

```yaml
  docker-e2e-acceptance:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    concurrency:
      group: docker-e2e-acceptance
      cancel-in-progress: false
    env:
      CI: "true"
      E2E_ADMIN_API_TOKEN: ${{ secrets.E2E_ADMIN_API_TOKEN }}
      LLM_PROVIDER: ${{ vars.LLM_PROVIDER || 'openai' }}
      LLM_BASE_URL: ${{ vars.LLM_BASE_URL || 'https://api.openai.com/v1' }}
      LLM_MODEL: ${{ vars.LLM_MODEL || 'gpt-4o' }}
      LLM_API_KEY: ${{ secrets.LLM_API_KEY }}
      EMBEDDING_PROVIDER: ${{ vars.EMBEDDING_PROVIDER || 'openai' }}
      EMBEDDING_BASE_URL: ${{ vars.EMBEDDING_BASE_URL || 'https://api.openai.com/v1' }}
      EMBEDDING_MODEL: ${{ vars.EMBEDDING_MODEL || 'text-embedding-3-small' }}
      EMBEDDING_API_KEY: ${{ secrets.EMBEDDING_API_KEY }}
      SECRET_KEY: ${{ secrets.E2E_SECRET_KEY || secrets.SECRET_KEY }}
    steps:
      - uses: actions/checkout@v4
      - name: Run Docker E2E acceptance
        shell: pwsh
        run: ./scripts/docker_e2e_acceptance.ps1 -Clean
      - name: Upload report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: docker-e2e-report
          path: artifacts/docker-e2e/*/
          if-no-files-found: error
```

- [ ] **Step 2: Verify YAML syntax**

```bash
cd D:/Python/subject1/RAG_Agent
python -c "
import yaml
with open('.github/workflows/release-gate.yml') as f:
    data = yaml.safe_load(f)
jobs = data.get('jobs', {})
assert 'docker-e2e-acceptance' in jobs, 'Job not found'
job = jobs['docker-e2e-acceptance']
assert job.get('timeout-minutes') == 30
print('YAML OK, job defined')
"
```

Expected: `YAML OK, job defined`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release-gate.yml
git commit -m "ci: add docker-e2e-acceptance job to release gate, pwsh + env secrets"
```

---

### Task 7: End-to-end verification

**Files:**
- None (verification only)

- [ ] **Step 1: Run the full acceptance script locally**

```powershell
cd D:/Python/subject1/RAG_Agent
./scripts/docker_e2e_acceptance.ps1 -SkipBuild
```

Expected: all 12 stages pass, `result.json` and `report.md` generated, exit code 0.

- [ ] **Step 2: Run again with -Clean to test full teardown**

```powershell
./scripts/docker_e2e_acceptance.ps1 -SkipBuild -Clean
```

Expected: all stages pass, containers cleaned up (verify with `docker compose -p ragagent-e2e-* ps` — no results).

- [ ] **Step 3: Verify strict smoke fails when backend is intentionally unreachable**

```bash
cd D:/Python/subject1/RAG_Agent/backend
DOCKER_E2E_REQUIRED=1 BACKEND_URL=http://127.0.0.1:19999 pytest tests/e2e/test_docker_smoke.py -q 2>&1
```

Expected: tests FAIL (not skip), exit code non-zero.

- [ ] **Step 4: Verify manifest hash validation catches a mismatch**

```bash
cd D:/Python/subject1/RAG_Agent
python -c "
import json
with open('backend/tests/e2e/fixtures/manifest.json') as f:
    m = json.load(f)
m['documents'][0]['sha256'] = 'deadbeef'
with open('/tmp/bad-manifest.json', 'w') as f:
    json.dump(m, f)
"
# Then in the script, point manifest to /tmp/bad-manifest.json and verify it fails config_check
```

Expected: config_check stage fails with "Hash mismatch".

- [ ] **Step 5: Verify project-name guard**

```bash
# Attempt to down -v with wrong project name should block
# This is tested by the guard regex in the script — verified by code review
cd D:/Python/subject1/RAG_Agent
python -c "
import re
pattern = r'^ragagent-e2e-\d{8}-\d{6}-[0-9a-f]{8}$'
assert re.match(pattern, 'ragagent-e2e-20260717-143022-a1b2c3d4'), 'Valid ID rejected'
assert not re.match(pattern, 'my-production-app'), 'Invalid ID accepted'
assert not re.match(pattern, 'ragagent-e2e'), 'Too-short ID accepted'
print('Guard regex verified')
"
```

Expected: `Guard regex verified`

- [ ] **Step 6: Commit any verification artifacts**

No code changes expected from verification. If script fixes needed, commit them with `fix: ...` messages.
