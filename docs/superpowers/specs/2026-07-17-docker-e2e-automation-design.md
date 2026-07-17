# Docker E2E Acceptance Automation — Design

> Date: 2026-07-17
> Phase: 1 (per NEXT_PHASE_OPTIMIZATION_EXECUTION_PLAN_2026-07-17.md)
> Status: approved, ready for implementation

## 1. Goal

Replace the manual Docker E2E acceptance steps from the checkpoint document with a single command that orchestrates the full lifecycle: build, up, upload, QA, backup/restore, Qdrant degradation recovery, and reporting.

## 2. Architecture

### 2.1 Script

`scripts/docker_e2e_acceptance.ps1` — PowerShell script that orchestrates Docker lifecycle. Calls existing pytest smoke tests for detailed assertions instead of reimplementing them in shell.

Project name per run: `ragagent-e2e-<yyyyMMdd-HHmmss>`. Each run is isolated from others and from any persistent dev stack.

### 2.2 Separation of concerns

| Layer | Tool | Responsibility |
|---|---|---|
| Docker lifecycle | PowerShell | build, up, down, restart, stop/start containers |
| HTTP checks | PowerShell (`Invoke-RestMethod`) | Health, auth gate, upload, SSE event capture, backup, restore |
| Assertions | pytest (existing) | Smoke suite, BM25/Qdrant consistency, integration tests |

### 2.3 CLI

```powershell
scripts/docker_e2e_acceptance.ps1
  [-Clean]              # down -v after success (never on failure)
  [-SkipBuild]          # reuse existing images, only up + test
  [-Token <string>]     # admin token (default: from env or e2e fixed token)
  [-BackendPort 18000]  # backend port
  [-FrontendPort 15173] # frontend port
```

## 3. Flow (13 stages)

Each stage must pass before the next begins. On any failure, exit non-zero, skip teardown, and print the retention command.

```
1.  config check     — compose files present, .env readable, fixtures exist
2.  docker build     — build backend and frontend images
3.  docker up -d     — start stack, wait for backend healthy (timeout 120s)
4.  secrets check    — verify /app/.env absent inside backend container
5.  auth check       — 401 without token, 200 with token
6.  batch upload     — upload all files from fixtures/, wait all ready
7.  consistency      — BM25/Qdrant chunk count match (pytest)
8.  SSE QA           — two fixed questions via /api/chat, assert events: answer_chunk, sources, verification, done; verify faithfulness/citation_precision/citation_recall = 1.0
9.  restart persistence — restart backend+qdrant, verify documents survive
10. backup/restore   — backup → clear-all → restore → verify document count
11. degradation      — stop qdrant → health shows error → start qdrant → health recovers
12. final smoke      — pytest backend/tests/e2e/test_docker_smoke.py (5 tests)
13. report           — write result.json + report.md
14. cleanup          — only if -Clean and all passed: docker compose down -v
```

## 4. Safety protections

- **Project name guard**: `down -v` only if project name matches `ragagent-e2e-*`. Refuse and error otherwise.
- **Zero credential leak**: Report records provider/model/endpoint presence only. Model names are SHA-256 hashed. No API keys in logs or reports.
- **Token handling**: `-Token` parameter accepts plain string; recommendation in help text to use environment variable. Not logged.
- **Timeout on every wait**: health (120s), ready (180s), SSE response (120s), restore (60s). On timeout, print current state then exit.
- **Failure preserves state**: Non-zero exit skips `down`, even with `-Clean`. Script prints the exact compose commands to inspect or tear down manually.
- **Port conflict detection**: Before `up`, check if 18000 or 15173 are bound. If occupied, report and exit.

## 5. CI integration

Add one job to `.github/workflows/release-gate.yml`:

```yaml
docker-e2e-acceptance:
  runs-on: ubuntu-latest
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
```

Trigger: `workflow_dispatch` and `v*` tags (inherits from release-gate.yml). Not added to `ci.yml` — existing `compose-smoke` already covers per-PR Docker regression.

## 6. Reports

Output directory: `artifacts/docker-e2e/<run-id>/`

### result.json

```json
{
  "run_id": "ragagent-e2e-20260717-143022",
  "timestamp": "2026-07-17T14:30:22Z",
  "git_commit": "9afa9cc",
  "overall": "passed",
  "stages": {
    "config_check": {"status": "passed", "elapsed_s": 0.1},
    "build": {"status": "passed", "elapsed_s": 45.2},
    "health": {"status": "passed", "elapsed_s": 18.3},
    "secrets_check": {"status": "passed", "elapsed_s": 0.5},
    "auth_check": {"status": "passed", "elapsed_s": 0.3},
    "upload": {"status": "passed", "elapsed_s": 22.1, "files": 2, "chunks": 2},
    "consistency": {"status": "passed", "elapsed_s": 2.1},
    "sse_qa": {"status": "passed", "elapsed_s": 12.4, "faithfulness": 1.0, "citation_precision": 1.0, "citation_recall": 1.0},
    "restart_persistence": {"status": "passed", "elapsed_s": 11.2},
    "backup_restore": {"status": "passed", "elapsed_s": 8.2, "documents_restored": 2},
    "degradation": {"status": "passed", "elapsed_s": 15.3},
    "smoke": {"status": "passed", "elapsed_s": 2.0}
  },
  "config_snapshot": {
    "llm_provider": "configured",
    "llm_model_sha256": "abc123...",
    "embedding_provider": "configured",
    "embedding_model_sha256": "def456..."
  }
}
```

### report.md

Human-readable summary: stage table, key metrics, failure details (if any), git commit, run ID, and teardown instructions.

## 7. Acceptance criteria

- Same commit runs 3 consecutive times, all pass
- Structured JSON and Markdown reports generated each run
- Non-zero exit on any stage failure, with stage name in output
- CI job cannot skip past unreachable services (no `pytest.skip` gating)
- Failure preserves containers and volumes; success + `-Clean` tears down completely
- Project-name guard prevents accidental `down -v` on non-e2e stacks
