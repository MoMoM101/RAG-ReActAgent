# Capacity & Performance Baseline Tooling — Design

> Date: 2026-07-17
> Phase: 2 (per `NEXT_PHASE_OPTIMIZATION_EXECUTION_PLAN_2026-07-17.md`)
> Status: approved, ready for implementation planning

## 1. Goal

Build a reusable benchmark toolchain that answers three questions with concrete numbers:

1. Can 50 files be uploaded stably, and at what scale does reliability degrade?
2. How do latency and error rates change under concurrent Q&A load?
3. Where is the bottleneck — LLM, embedding, SQLite, Qdrant, BM25, or the frontend proxy?

All tools run from the host against `http://127.0.0.1:18000`, targeting the same Docker stack used in Phase 1 E2E acceptance.

## 2. Architecture

```
scripts/benchmark/
├── generate_fixtures.py    # Test document generator
├── upload_bench.py          # Upload capacity benchmarks (5 scenarios)
├── qa_bench.py              # Concurrent Q&A benchmarks (5 levels)
├── collect_metrics.py       # System metrics collector (background)
├── generate_report.py       # Report generator → markdown + JSON
└── run_all.py               # Orchestrator: run all benchmarks
```

Data flow:

```
generate_fixtures → fixtures_benchmark/<scenario>/
                 → fixtures_benchmark/manifest_<scenario>.json

upload_bench → artifacts/bench_<date>/raw/upload_<scenario>.json

qa_bench     → artifacts/bench_<date>/raw/qa_concurrency_<N>.json

collect_metrics (background) → artifacts/bench_<date>/raw/system.jsonl

generate_report → docs/CAPACITY_BASELINE_REPORT_<date>.md
                artifacts/bench_<date>/summary.json
                artifacts/bench_<date>/raw/*.csv
```

All tools use `httpx` (already in project deps). No new Python dependencies beyond `psutil` (optional, for host resource metrics).

## 3. Test Documents Generator (`generate_fixtures.py`)

### 3.1 Scenarios

| Scenario | Files | Size per file | Formats | Directory |
|---|---|---|---|---|
| `small_batch` | 50 | 10–100 KB | TXT | `fixtures_benchmark/small_batch/` |
| `medium_batch` | 20 | 5–20 MB | TXT | `fixtures_benchmark/medium_batch/` |
| `large_boundary` | 5 | 100–200 MB | TXT | `fixtures_benchmark/large_boundary/` |
| `mixed_formats` | 30 | 5 KB–5 MB | TXT, MD, CSV | `fixtures_benchmark/mixed_formats/` |
| `partial_invalid` | 10 | 5–50 KB | TXT + `.exe` + empty + dup | `fixtures_benchmark/partial_invalid/` |

### 3.2 Generation logic

- **TXT**: Chinese template text repeated to target byte size, varying paragraph lengths
- **MD**: Headers, bullet lists, fenced code blocks, tables — realistic documentation structure
- **CSV**: Column headers + randomized data rows to target row count
- **Invalid files**: wrong extension claim (`foo.exe` containing text), zero-byte file, SHA-256 duplicate of another file in the same batch

### 3.3 Repeatability

Each scenario produces a `manifest_<scenario>.json` with filename, SHA-256, expected chunk count estimate, and file size. Uses a fixed random seed per scenario so generation is deterministic across runs.

```bash
python scripts/benchmark/generate_fixtures.py --all --output-dir fixtures_benchmark/
```

## 4. Upload Benchmarks (`upload_bench.py`)

### 4.1 Per-scenario flow

```
1. clear-all knowledge base
2. Start system metrics collector (background)
3. Upload all files via /api/documents/upload-batch
4. Poll /api/documents until all ready or timeout (files × 30s)
5. Stop collector, write results
```

### 4.2 Per-file metrics

| Metric | Source |
|---|---|
| Upload round-trip time (client) | Script timer |
| Processing time (uploaded → ready) | Document status transition timestamps |
| Final status (ready / failed) | `/api/documents` response |
| chunk_count | `/api/documents` response |
| BM25 chunk count | Direct `BM25Search.get_chunk_ids_by_document()` call |
| Qdrant chunk count | Direct `QdrantVectorDB.get_chunk_ids_by_document()` call |

### 4.3 Aggregate metrics

- Success rate (ready / total)
- P50 / P95 / P99 processing time
- Failure reason distribution
- Embedding API call count and rate (from `/api/metrics`)
- Index consistency rate (BM25 == Qdrant chunk count)

### 4.4 Output

`artifacts/bench_<date>/raw/upload_<scenario>.json` — per-file records + aggregate summary.

## 5. Concurrent Q&A Benchmarks (`qa_bench.py`)

### 5.1 Concurrency levels

| Level | Concurrency | Duration | Purpose |
|---|---|---|---|
| 1 | 1 | 10 min | Single-user baseline |
| 2 | 5 | 15 min | Small team |
| 3 | 10 | 20 min | Department pilot |
| 4 | 25 | 20 min | Single-instance stress boundary |
| 5 | 50 | 10 min | Overload and protection |

### 5.2 Per-level flow

```
1. Reset metrics collector snapshot
2. Prepare question pool (10 unique questions, cycled)
3. Start N async coroutines, each:
   a. Pick next question from pool
   b. POST to /api/chat (SSE), record client-side timing
   c. Parse SSE events: answer_chunk, sources, verification, done, timing
   d. Extract: TTFT, rag_total, faithfulness, SSE completion, HTTP status
   e. On completion or error, record result, pick next question
4. Run for configured duration
5. Collect /api/metrics snapshot
6. Collect docker stats summary
```

### 5.3 Per-request metrics

| Metric | Source |
|---|---|
| Client elapsed (total wall time) | Script timer |
| TTFT (first answer_chunk) | SSE parsing |
| rag_total, rag_intent, rag_retrieval, etc. | SSE timing event |
| HTTP status code | httpx response |
| SSE events completeness (all 4 events present) | SSE parsing |
| Faithfulness, citation_precision, citation_recall | SSE verification event |
| Exception / timeout / disconnect | Script error tracking |

### 5.4 Aggregate metrics (server-side, from /api/metrics)

- HTTP latencies P50 / P95 / P99
- RAG phase timing P50 / P95 / P99 per phase
- LLM / embedding request count, token usage
- Retrieval fallbacks, empty results
- Cache hit / miss rates
- Tool call success rates

### 5.5 Output

`artifacts/bench_<date>/raw/qa_concurrency_<N>.json` — per-request records + aggregate summary.

## 6. System Metrics Collector (`collect_metrics.py`)

Runs as a background process during benchmarks. Two data sources:

### Source A: `/api/metrics` (Prometheus text format)

- Poll every 5 seconds
- Parse Prometheus format into structured dict
- Write one JSON line per timestamp to `system.jsonl`

### Source B: `docker stats`

- Poll every 5 seconds (or same interval, alternating)
- Record per container: CPU%, memory usage/limit, network RX/TX, block I/O
- Parse container name from output
- Merge with Source A data, write same jsonl stream

### Usage

```bash
python scripts/benchmark/collect_metrics.py \
  --duration 600 \
  --output artifacts/bench_20260717/raw/system.jsonl \
  --base-url http://127.0.0.1:18000
```

Stops automatically after `--duration` seconds or on SIGTERM.

## 7. Report Generator (`generate_report.py`)

Reads all raw data from `artifacts/bench_<date>/raw/` and produces:

### 7.1 `docs/CAPACITY_BASELINE_REPORT_<date>.md`

Sections:
- **Environment**: git commit, Docker image IDs, container resource limits, model config (provider + hashed model name)
- **Upload Matrix**: 5-scenario table with success rate, P50/P95/P99, embedding calls, failure reasons
- **Q&A Concurrency Matrix**: 5-level table with TTFT P50/P95, rag_total P50/P95/P99, error rate, SSE interruption rate
- **Bottleneck Analysis**: phase timing breakdown, external API 429/5xx count, SQLite busy metrics, Qdrant query latency
- **Resource Profile**: CPU/memory/network per container at each concurrency level
- **Recommendations**: max stable upload batch, max recommended concurrency, trigger points for scaling, bottleneck ranking
- **Raw Data Index**: paths to JSON and CSV files

### 7.2 `artifacts/bench_<date>/summary.json`

Structured summary of all aggregate metrics, suitable for CI comparison or automated gating.

### 7.3 CSV exports

Per-scenario and per-concurrency-level CSV files for spreadsheet analysis.

## 8. Orchestrator (`run_all.py`)

```bash
python scripts/benchmark/run_all.py
  [--scenarios small_batch medium_batch mixed_formats partial_invalid]
  [--concurrency 1 5 10]
  [--output-dir artifacts/bench_20260717]
  [--base-url http://127.0.0.1:18000]
  [--admin-token rag-agent-e2e-admin-token]
  [--skip-generate]
  [--skip-upload]
  [--skip-qa]
```

Flow:
1. `generate_fixtures.py --all` (unless `--skip-generate`)
2. For each scenario: `upload_bench.py --scenario <name>` (unless `--skip-upload`)
3. For each concurrency level: `qa_bench.py --concurrency <N>` (unless `--skip-qa`)
4. `generate_report.py --input-dir <output-dir>`
5. Print report path and key findings summary to stdout

Partial runs are supported: if a previous run was interrupted, re-running with the same `--output-dir` skips already-completed benchmarks (detected by output file existence).

## 9. Safety

- **Never commits generated fixtures**: `fixtures_benchmark/` is in `.gitignore`
- **Clear-all gate**: upload_bench requires explicit `--acknowledge-clear` flag before calling clear-all
- **Token**: read from `E2E_ADMIN_API_TOKEN` env var or CLI flag; never logged
- **Online API costs**: each full run estimates total embedding + LLM tokens before starting; user confirms
- **Resource limits**: large_boundary scenario warns if disk space < 2 GB; concurrency 50 warns about rate limits
- **Report sanitization**: model names are SHA-256 hashed; no API keys or source text in reports

## 10. Acceptance Criteria

- 50-file small batch: success rate ≥ 99%, zero lost files
- All 5 upload scenarios complete with structured per-file results
- At least 3 concurrency levels complete with per-request TTFT/rag_total data
- System metrics collected for all benchmark runs (both `/api/metrics` and `docker stats`)
- Capacity report answers all three core questions with specific numbers and timestamps
- All tools can be re-run independently (skip phases, resume partial runs)

## 11. Out of Scope

- Fixing performance issues found by benchmarks (that belongs to Phase 4)
- Production-grade load testing with distributed agents
- Grafana dashboard updates (existing `grafana_v4_rag_dashboard.json` is consumed as-is)
- Multi-instance or PostgreSQL benchmarks (Phase 6)
