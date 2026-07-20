# Docker E2E Acceptance Automation — Design

> Date: 2026-07-17
> Phase: 1 (per `NEXT_PHASE_OPTIMIZATION_EXECUTION_PLAN_2026-07-17.md`)
> Status: reviewed, ready for implementation planning

## 1. Goal

Replace the manual Docker E2E acceptance steps from the checkpoint document with a single command that orchestrates the full lifecycle: config validation, build, startup, upload, online QA, restart persistence, backup/restore, Qdrant degradation recovery, strict smoke tests, reporting, and optional safe cleanup.

The automation must satisfy four properties:

1. A failed or unreachable service can never be reported as a pass or skip.
2. Every run uses isolated Compose resources and can be diagnosed after failure.
3. Reports are generated on success and failure without leaking credentials or source text.
4. Tests against live E2E volumes are read-only unless the stage explicitly owns the mutation.

## 2. Architecture

### 2.1 Orchestrator

`scripts/docker_e2e_acceptance.ps1` — cross-platform PowerShell 7 script that owns the Docker lifecycle and stage state machine.

Project name per run:

```text
ragagent-e2e-<UTC yyyyMMdd-HHmmss>-<8-char GUID>
```

The timestamp plus GUID prevents project/volume collisions. Host ports are still a shared resource; CI runs are serialized and local runs detect conflicts before startup.

### 2.2 Supporting files

| File | Purpose |
|---|---|
| `docker-compose.yml` | Base application stack |
| `docker-compose.e2e.yml` | E2E health checks, loopback bindings, token and port interpolation |
| `backend/tests/e2e/fixtures/manifest.json` | Exact fixture list, hashes, expected chunks, questions, expected answer terms and source filenames |
| `backend/tests/e2e/test_docker_smoke.py` | Strict live HTTP smoke checks |
| `backend/tests/e2e/test_live_index_consistency.py` | Read-only BM25/Qdrant/active-generation comparison |
| `scripts/docker_e2e_acceptance.ps1` | Lifecycle, HTTP mutations, stage recording and reports |

### 2.3 Separation of concerns

| Layer | Tool | Responsibility |
|---|---|---|
| Docker lifecycle | PowerShell | config, build, up/down, restart, stop/start, logs, status |
| HTTP mutations | PowerShell | upload, backup, clear, restore, SSE capture |
| HTTP assertions | PowerShell + strict pytest | health, auth, proxy, response status and event contracts |
| Live index assertions | dedicated pytest | read-only exact chunk-ID consistency |
| Reports | PowerShell | always write JSON and Markdown from accumulated stage state |

Existing restore/generation integration suites must not run against the live E2E volumes. They use temporary/fake resources and may mutate SQLite or Qdrant when executed with production container configuration.

## 3. Compose parameterization

`docker-compose.e2e.yml` must use environment interpolation rather than hard-coded CLI-visible values:

```yaml
services:
  backend:
    ports: !override
      - "127.0.0.1:${E2E_BACKEND_PORT:-18000}:8000"
    environment:
      ADMIN_API_TOKEN: ${E2E_ADMIN_API_TOKEN:?E2E_ADMIN_API_TOKEN is required}

  frontend:
    ports: !override
      - "127.0.0.1:${E2E_FRONTEND_PORT:-15173}:5173"
```

The script sets these variables before every Compose invocation. It must not assume that PowerShell parameters automatically override Compose YAML.

### 3.1 Frontend health check

The production frontend image is Nginx and does not contain Node.js. Replace the existing `node -e` health check with a command available in the final Nginx Alpine image, for example BusyBox `wget`:

```yaml
healthcheck:
  test: ["CMD", "wget", "-qO-", "http://127.0.0.1:5173/"]
  interval: 5s
  timeout: 4s
  retries: 24
  start_period: 10s
```

Startup acceptance waits for both backend and frontend to become healthy. After that it also requests the frontend root and `/api/health` through Nginx to validate static serving and proxy routing.

## 4. CLI and configuration

```powershell
./scripts/docker_e2e_acceptance.ps1
  [-Clean]                 # down -v after success; never on failure
  [-SkipBuild]             # reuse existing images
  [-BackendPort 18000]     # loopback host port
  [-FrontendPort 15173]    # loopback host port
  [-HealthTimeoutSec 120]
  [-ReadyTimeoutSec 180]
  [-SseTimeoutSec 120]
  [-RestoreTimeoutSec 180]
```

The admin token is not accepted as a normal string CLI parameter because command arguments can enter shell history and process listings.

Token resolution:

1. Use `E2E_ADMIN_API_TOKEN` when present.
2. Outside CI only, fall back to the fixed synthetic token `rag-agent-e2e-admin-token`.
3. In CI, fail config validation when `E2E_ADMIN_API_TOKEN` is absent.

Online model configuration is supplied through process environment or a caller-provided env file. The script never prints values.

Required online configuration:

- `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`
- `EMBEDDING_PROVIDER`, `EMBEDDING_BASE_URL`, `EMBEDDING_MODEL`, `EMBEDDING_API_KEY`
- `SECRET_KEY` when any injected credential uses the `ENC:` format

Plaintext CI secrets do not require `SECRET_KEY` for decryption, but a non-empty application secret should still be injected when the application configuration requires it.

## 5. Fixture contract

The script must not upload every file discovered under `fixtures/`. Future negative or oversized fixtures would make the acceptance set nondeterministic.

`backend/tests/e2e/fixtures/manifest.json` is authoritative and contains:

```json
{
  "documents": [
    {
      "path": "docker_acceptance_product.txt",
      "sha256": "...",
      "expected_chunks": 1
    },
    {
      "path": "docker_acceptance_policy.md",
      "sha256": "...",
      "expected_chunks": 1
    }
  ],
  "questions": [
    {
      "question": "...",
      "expected_terms": ["四小时", "三十分钟"],
      "expected_source": "docker_acceptance_product.txt"
    },
    {
      "question": "...",
      "expected_terms": ["七个自然日"],
      "expected_source": "docker_acceptance_policy.md"
    }
  ]
}
```

Config validation checks that every declared file exists and its SHA-256 matches before Docker startup.

## 6. Flow (14 stages)

Each stage must pass before the next begins. Stage execution uses a common wrapper that records start time, end time, elapsed time, status and a sanitized error. On failure, later stages become `not_run`; report generation still executes before the script exits non-zero.

```text
1.  config check
    - compose files, manifest and fixture hashes
    - Docker/Compose/Pwsh availability
    - required online model environment
    - E2E token resolution
    - project-name guard and output directory

2.  docker build
    - compose config --quiet
    - build backend and frontend unless -SkipBuild

3.  docker up and health
    - start stack
    - wait backend healthy and frontend healthy
    - request frontend / and frontend /api/health

4.  secrets check
    - /app/.env must be absent
    - build context excludes .env and data directories
    - image config/history must not contain known secret values

5.  auth check
    - documents and metrics return 401/403 without token
    - documents and metrics return 200 with token

6.  batch upload
    - upload only manifest documents through frontend /api proxy
    - assert per-file success and returned document metadata
    - poll until every document reaches ready or failed
    - fail immediately on failed terminal state

7.  live consistency
    - run dedicated read-only test inside backend container
    - compare exact chunk-ID sets per ready document in BM25 and active Qdrant collection
    - verify active_generation_id points to a committed generation
    - compare manifest expected chunk counts

8.  SSE QA
    - ask both manifest questions through frontend /api/chat
    - assert HTTP 200 and events answer_chunk, sources, verification, done
    - assert expected answer terms and expected source filename
    - assert faithfulness, citation_precision and citation_recall are 1.0
    - record rag_total, visible TTFT and client elapsed time

9.  restart persistence
    - restart backend and qdrant
    - wait for health recovery
    - verify exact document IDs, file hashes, statuses and chunk-ID sets survive

10. backup/restore
    - create archive and record size/SHA-256
    - clear all documents
    - assert document count becomes zero
    - restore archive
    - verify original document IDs, filenames, hashes, ready status and chunk-ID sets
    - rerun live consistency check
    - rerun at least one SSE question and require sources, verification and done

11. degradation/recovery
    - stop qdrant
    - dependency health reports qdrant=error within timeout while sqlite remains ok
    - start qdrant
    - health returns ok
    - perform a read-only retrieval/QA probe after recovery

12. final strict smoke
    - run backend/tests/e2e/test_docker_smoke.py
    - set DOCKER_E2E_REQUIRED=1
    - produce JUnit XML
    - require 5 passed and 0 skipped

13. report
    - finalize result.json and report.md
    - include all stages, including not_run

14. optional cleanup
    - only when -Clean and all prior stages passed
    - verify project-name guard, then down -v
```

## 7. Strict smoke behavior

The current smoke suite skips when services are unreachable. Implementation must add a strict mode:

```text
DOCKER_E2E_REQUIRED=1
```

In strict mode:

- connection errors call `pytest.fail`, never `pytest.skip`;
- malformed JSON fails;
- dependency status must satisfy the E2E expectation;
- JUnit skipped count must equal zero.

Optional local discovery behavior may continue to skip only when strict mode is not enabled. The acceptance script and CI always enable strict mode.

## 8. Live consistency test

Create `backend/tests/e2e/test_live_index_consistency.py` as a read-only test. It must not use restore integration helpers, clear data, create collections, rebuild indexes or receive the generic database-reset fixture.

For each ready document it checks:

1. `documents.active_generation_id` is non-empty.
2. The referenced generation has status `committed`.
3. BM25 chunk IDs for the document match Qdrant chunk IDs exactly.
4. The exact set size matches `documents.chunk_count` and the manifest expectation.

The test reads the active Qdrant collection pointer and reports document ID plus counts on mismatch, but does not print source text.

## 9. Failure handling and reports

The output directory and initial result object are created before Stage 1. A top-level `try/catch/finally` guarantees report generation.

Failure behavior:

1. Record the failing stage and sanitized exception.
2. Capture `docker compose ps -a` and bounded, sanitized service log tails.
3. Mark later functional stages `not_run`.
4. Write `result.json` and `report.md` in `finally`.
5. Preserve containers and volumes even when `-Clean` was supplied.
6. Print exact inspect and teardown commands.
7. Exit non-zero only after reports have been flushed.

Report sanitization removes known secret values, Authorization headers, API keys, query-string credentials and full source text.

## 10. Safety protections

- **Project name guard:** `down -v` is permitted only when the exact project name matches `^ragagent-e2e-[0-9]{8}-[0-9]{6}-[0-9a-f]{8}$`.
- **No broad cleanup:** never enumerate and delete projects, networks or volumes by partial name.
- **Zero credential leak:** reports record only configured/missing status and SHA-256 of non-secret model identifiers.
- **No token CLI argument:** CI injects the token through environment.
- **Timeout on every wait:** all waits accept configurable finite timeouts and print the last known state.
- **Failure preserves state:** no automatic down on failure.
- **Port conflict detection:** check both requested ports before `up` and fail with a clear message.
- **Binary backup handling:** use an explicit output file API and verify non-zero size plus SHA-256.
- **Bounded logs:** capture only a configured tail and sanitize before writing artifacts.

## 11. CI integration

Add one job to `.github/workflows/release-gate.yml`. It runs only on `workflow_dispatch` and `v*` tags already declared by that workflow; it is not added to per-PR `ci.yml` because the existing Compose smoke remains the fast PR-level check.

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
    LLM_PROVIDER: ${{ vars.LLM_PROVIDER }}
    LLM_BASE_URL: ${{ vars.LLM_BASE_URL }}
    LLM_MODEL: ${{ vars.LLM_MODEL }}
    LLM_API_KEY: ${{ secrets.LLM_API_KEY }}
    EMBEDDING_PROVIDER: ${{ vars.EMBEDDING_PROVIDER }}
    EMBEDDING_BASE_URL: ${{ vars.EMBEDDING_BASE_URL }}
    EMBEDDING_MODEL: ${{ vars.EMBEDDING_MODEL }}
    EMBEDDING_API_KEY: ${{ secrets.EMBEDDING_API_KEY }}
    SECRET_KEY: ${{ secrets.E2E_SECRET_KEY }}
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

Repository configuration must document every required variable/secret before enabling the job. GitHub masks known secrets, but the script still performs its own sanitization.

## 12. Reports

Output directory:

```text
artifacts/docker-e2e/<run-id>/
```

### 12.1 result.json

```json
{
  "run_id": "ragagent-e2e-20260717-143022-a1b2c3d4",
  "timestamp": "2026-07-17T14:30:22Z",
  "git_commit": "9afa9cc",
  "overall": "failed",
  "failed_stage": "backup_restore",
  "stages": {
    "config_check": {"status": "passed", "elapsed_s": 0.1},
    "build": {"status": "passed", "elapsed_s": 45.2},
    "health": {"status": "passed", "elapsed_s": 18.3},
    "secrets_check": {"status": "passed", "elapsed_s": 0.5},
    "auth_check": {"status": "passed", "elapsed_s": 0.3},
    "upload": {"status": "passed", "elapsed_s": 22.1, "files": 2, "chunks": 2},
    "consistency": {"status": "passed", "elapsed_s": 2.1},
    "sse_qa": {
      "status": "passed",
      "elapsed_s": 8.4,
      "faithfulness": 1.0,
      "citation_precision": 1.0,
      "citation_recall": 1.0,
      "rag_total_ms": 3648,
      "client_elapsed_ms": 4250
    },
    "restart_persistence": {"status": "passed", "elapsed_s": 11.2},
    "backup_restore": {"status": "failed", "elapsed_s": 8.2, "error": "sanitized error"},
    "degradation": {"status": "not_run"},
    "smoke": {"status": "not_run"}
  },
  "config_snapshot": {
    "llm_provider": "configured",
    "llm_model_sha256": "abc123...",
    "embedding_provider": "configured",
    "embedding_model_sha256": "def456..."
  }
}
```

### 12.2 report.md

Human-readable summary containing:

- overall status, failed stage, run ID and git commit;
- stage table with elapsed times;
- upload, consistency, RAG quality and latency metrics;
- sanitized failure details and bounded log references;
- retained resource names;
- exact inspect and teardown commands.

## 13. Acceptance criteria

- Same commit runs 3 consecutive times and all pass.
- Backend and Nginx frontend both become healthy.
- Structured JSON, Markdown and JUnit reports are generated on success and failure.
- Any functional stage failure returns non-zero after reports are written.
- Strict smoke produces exactly 5 passed and 0 skipped.
- Live consistency compares exact BM25/Qdrant chunk-ID sets without mutating E2E data.
- Restore recovers original IDs, hashes, statuses and exact chunk sets, then passes post-restore SSE QA.
- CI does not depend on a checked-in `.env` and declares all required variables/secrets.
- Failure preserves containers and volumes; success plus `-Clean` removes only the guarded E2E project.
- Project-name guard prevents `down -v` on non-E2E stacks.
- Reports and logs contain no credential values or full retrieved source text.

## 14. Out of scope

- Load testing 50-file batches or concurrent chat traffic; that belongs to the capacity-baseline phase.
- PostgreSQL, multi-tenant or distributed-worker migration.
- Production deployment rollout or external infrastructure provisioning.
- Replacing the 93-sample Grounded Answer release evaluation; Docker E2E is an additional deployment gate, not a substitute.
