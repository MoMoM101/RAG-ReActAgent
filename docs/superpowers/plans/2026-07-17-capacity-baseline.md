# Capacity & Performance Baseline Tooling — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reusable benchmark toolchain (6 Python scripts) that generates test documents, runs upload and concurrent Q&A benchmarks, collects system metrics, and produces a capacity baseline report.

**Architecture:** Each tool is a standalone Python CLI script under `scripts/benchmark/`. They share a common HTTP client pattern (`httpx`) and output directory structure (`artifacts/bench_<date>/raw/`). The orchestrator (`run_all.py`) runs them sequentially with skip/resume support.

**Tech Stack:** Python 3.12+, httpx, asyncio, psutil (optional), jinja2 (for report templates)

---

### Task 1: Test document generator (`generate_fixtures.py`)

**Files:**
- Create: `scripts/benchmark/generate_fixtures.py`
- Create: `scripts/benchmark/__init__.py` (empty)

- [ ] **Step 1: Create directory and empty __init__.py**

```bash
mkdir -p D:/Python/subject1/RAG_Agent/scripts/benchmark
touch D:/Python/subject1/RAG_Agent/scripts/benchmark/__init__.py
```

- [ ] **Step 2: Write the generator script**

```python
"""Generate deterministic test documents for capacity benchmarks.

Usage:
  python scripts/benchmark/generate_fixtures.py --all --output-dir fixtures_benchmark/
  python scripts/benchmark/generate_fixtures.py --scenario small_batch --output-dir fixtures_benchmark/
"""

import argparse
import csv
import hashlib
import io
import json
import os
import random
import sys
from pathlib import Path

# Fixed seed per scenario for deterministic output
SCENARIO_SEEDS = {
    "small_batch": 1,
    "medium_batch": 2,
    "large_boundary": 3,
    "mixed_formats": 4,
    "partial_invalid": 5,
}

CHINESE_PARAGRAPHS = [
    "星河知识平台是一个面向企业级客户的知识管理与智能问答系统。"
    "它支持多种文档格式的导入、自动解析、向量化索引和基于大语言模型的精准问答。",
    "系统采用混合检索架构，结合语义向量检索和关键词 BM25 检索，"
    "通过倒数排名融合算法对两种检索结果进行加权排序，确保召回率和精确率的平衡。",
    "文档处理管线包括格式解析、文本切分、嵌入向量生成和索引写入四个阶段。"
    "每个阶段都有独立的错误处理和重试机制，确保单文件失败不影响批量处理的其他文件。",
    "平台提供管理控制台和 RESTful API 两种管理方式。"
    "管理员可以通过 Web 界面上传文档、查看处理状态、配置问答策略，"
    "也可以使用 API 集成到现有的企业工作流中。",
    "系统内置了基于引用验证的答案质量评估模块。"
    "每个生成的回答都会经过忠实度、引用精确率和引用完整率三个维度的自动评估，"
    "不满足质量门禁的回答会触发自动修复流程。",
    "数据安全方面，系统支持基于管理令牌的 API 鉴权，"
    "所有上传文档在传输和存储过程中均可配置加密保护。"
    "备份和恢复功能支持全量知识库的快照导出和跨环境迁移。",
]

MD_TEMPLATE = """# {title}

## 概述

{paragraphs}

## 详细说明

{details}

## 配置示例

```yaml
{config}
```

## 注意事项

- {note1}
- {note2}
- {note3}
"""


def _make_txt(target_bytes: int, seed: int) -> str:
    """Generate a TXT file of approximately target_bytes using Chinese text."""
    rng = random.Random(seed)
    parts = []
    total = 0
    while total < target_bytes:
        para = rng.choice(CHINESE_PARAGRAPHS)
        parts.append(para)
        total += len(para.encode("utf-8"))
    return "\n\n".join(parts)


def _make_md(target_bytes: int, seed: int) -> str:
    """Generate a Markdown file of approximately target_bytes."""
    rng = random.Random(seed)
    title = f"文档编号_{seed:04d}"
    paragraphs = "\n\n".join(rng.sample(CHINESE_PARAGRAPHS, min(3, len(CHINESE_PARAGRAPHS))))
    details = "\n\n".join(f"{i}. {rng.choice(CHINESE_PARAGRAPHS)}" for i in range(1, 5))
    config = f"setting_{i}: {rng.randint(1, 1000)}"
    notes = rng.sample(CHINESE_PARAGRAPHS, 3)
    content = MD_TEMPLATE.format(
        title=title, paragraphs=paragraphs, details=details,
        config="\n".join(f"  {config}" for i in range(5)),
        note1=notes[0], note2=notes[1], note3=notes[2],
    )
    while len(content.encode("utf-8")) < target_bytes:
        content += f"\n\n{rng.choice(CHINESE_PARAGRAPHS)}"
    return content


def _make_csv(target_rows: int, seed: int) -> str:
    """Generate a CSV with header + target_rows data rows."""
    rng = random.Random(seed)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "title", "category", "priority", "status", "created_at", "content"])
    for i in range(target_rows):
        writer.writerow([
            i + 1,
            f"事项_{rng.randint(1, 10000):05d}",
            rng.choice(["技术", "产品", "运营", "销售", "人事"]),
            rng.choice(["P0", "P1", "P2", "P3"]),
            rng.choice(["待处理", "进行中", "已完成", "已关闭"]),
            f"2026-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}",
            rng.choice(CHINESE_PARAGRAPHS)[:80],
        ])
    return buf.getvalue()


def _compute_sha256(filepath: Path) -> str:
    return hashlib.sha256(filepath.read_bytes()).hexdigest()


def generate_scenario(scenario: str, output_dir: Path) -> dict:
    seed = SCENARIO_SEEDS[scenario]
    rng = random.Random(seed)
    out = output_dir / scenario
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"scenario": scenario, "documents": []}

    if scenario == "small_batch":
        # 50 files, 10-100 KB each
        for i in range(50):
            size = rng.randint(10_000, 100_000)
            fname = f"doc_{i:03d}.txt"
            fpath = out / fname
            fpath.write_text(_make_txt(size, seed + i), encoding="utf-8")
            manifest["documents"].append({
                "path": fpath.name, "size": fpath.stat().st_size,
                "sha256": _compute_sha256(fpath),
            })

    elif scenario == "medium_batch":
        # 20 files, 5-20 MB each
        for i in range(20):
            size = rng.randint(5_000_000, 20_000_000)
            fname = f"doc_{i:03d}.txt"
            fpath = out / fname
            fpath.write_text(_make_txt(size, seed + i), encoding="utf-8")
            manifest["documents"].append({
                "path": fpath.name, "size": fpath.stat().st_size,
                "sha256": _compute_sha256(fpath),
            })

    elif scenario == "large_boundary":
        # 5 files, 100-200 MB each
        for i in range(5):
            size = rng.randint(100_000_000, 200_000_000)
            fname = f"doc_{i:03d}.txt"
            fpath = out / fname
            fpath.write_text(_make_txt(size, seed + i), encoding="utf-8")
            manifest["documents"].append({
                "path": fpath.name, "size": fpath.stat().st_size,
                "sha256": _compute_sha256(fpath),
            })

    elif scenario == "mixed_formats":
        # 30 files, TXT/MD/CSV, 5 KB-5 MB
        for i in range(30):
            fmt = rng.choice(["txt", "md", "csv"])
            size = rng.randint(5_000, 5_000_000)
            ext = {"txt": ".txt", "md": ".md", "csv": ".csv"}[fmt]
            fname = f"doc_{i:03d}{ext}"
            fpath = out / fname
            if fmt == "txt":
                fpath.write_text(_make_txt(size, seed + i), encoding="utf-8")
            elif fmt == "md":
                fpath.write_text(_make_md(size, seed + i), encoding="utf-8")
            else:
                rows = max(10, size // 200)
                fpath.write_text(_make_csv(rows, seed + i), encoding="utf-8")
            manifest["documents"].append({
                "path": fpath.name, "size": fpath.stat().st_size,
                "sha256": _compute_sha256(fpath),
            })

    elif scenario == "partial_invalid":
        # 10 files: 7 valid TXT + 1 wrong ext + 1 empty + 1 duplicate
        for i in range(7):
            size = rng.randint(5_000, 50_000)
            fname = f"doc_{i:03d}.txt"
            fpath = out / fname
            fpath.write_text(_make_txt(size, seed + i), encoding="utf-8")
            manifest["documents"].append({
                "path": fpath.name, "size": fpath.stat().st_size,
                "sha256": _compute_sha256(fpath),
            })
        # Wrong extension
        fake_exe = out / "readme.exe"
        fake_exe.write_text(_make_txt(10_000, seed + 7), encoding="utf-8")
        manifest["documents"].append({
            "path": "readme.exe", "size": fake_exe.stat().st_size,
            "sha256": _compute_sha256(fake_exe), "note": "wrong_extension",
        })
        # Empty file
        empty = out / "empty.txt"
        empty.write_text("")
        manifest["documents"].append({
            "path": "empty.txt", "size": 0,
            "sha256": _compute_sha256(empty), "note": "empty",
        })
        # Duplicate of first file
        first = out / "doc_000.txt"
        dup = out / "duplicate.txt"
        dup.write_bytes(first.read_bytes())
        manifest["documents"].append({
            "path": "duplicate.txt", "size": dup.stat().st_size,
            "sha256": _compute_sha256(dup), "note": "duplicate_of_doc_000",
        })

    # Write manifest
    manifest_path = out.parent / f"manifest_{scenario}.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    total_size = sum(doc["size"] for doc in manifest["documents"])
    print(f"[{scenario}] {len(manifest['documents'])} files, {total_size / 1024 / 1024:.1f} MB total")
    return manifest


def main():
    parser = argparse.ArgumentParser(description="Generate benchmark test documents")
    parser.add_argument("--scenario", help="Single scenario name")
    parser.add_argument("--all", action="store_true", help="Generate all scenarios")
    parser.add_argument("--output-dir", default="fixtures_benchmark", help="Output root directory")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    scenarios = list(SCENARIO_SEEDS.keys()) if args.all else [args.scenario]
    for scenario in scenarios:
        if scenario not in SCENARIO_SEEDS:
            print(f"Unknown scenario: {scenario}", file=sys.stderr)
            sys.exit(1)
        generate_scenario(scenario, output_dir)

    if args.all:
        print(f"\nAll {len(scenarios)} scenarios generated in {output_dir.resolve()}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Verify generation of all scenarios**

```bash
cd D:/Python/subject1/RAG_Agent
D:/Python/Python/python.exe scripts/benchmark/generate_fixtures.py --all --output-dir fixtures_benchmark/
```

Expected: 5 scenarios generated, each prints file count and total size.

- [ ] **Step 4: Commit**

```bash
git add scripts/benchmark/__init__.py scripts/benchmark/generate_fixtures.py
git commit -m "feat: add benchmark test document generator with 5 scenarios"
```

---

### Task 2: Upload capacity benchmark (`upload_bench.py`)

**Files:**
- Create: `scripts/benchmark/upload_bench.py`

- [ ] **Step 1: Write the upload benchmark script**

```python
"""Upload capacity benchmark — measures batch upload throughput and reliability.

Usage:
  python scripts/benchmark/upload_bench.py \
    --scenario small_batch \
    --fixtures-dir fixtures_benchmark \
    --output artifacts/bench_20260717/raw/upload_small_batch.json \
    --base-url http://127.0.0.1:18000 \
    --admin-token rag-agent-e2e-admin-token \
    --acknowledge-clear
"""

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx


async def clear_all(client: httpx.AsyncClient) -> int:
    r = await client.delete("/api/documents/clear-all")
    r.raise_for_status()
    return r.json()["count"]


async def upload_batch(client: httpx.AsyncClient, file_paths: list[Path]) -> dict:
    files = []
    for fp in file_paths:
        files.append(("files", (fp.name, fp.read_bytes(), "application/octet-stream")))
    t0 = time.monotonic()
    r = await client.post("/api/documents/upload-batch", files=files, timeout=300.0)
    elapsed = time.monotonic() - t0
    r.raise_for_status()
    data = r.json()
    data["client_upload_elapsed_s"] = round(elapsed, 2)
    return data


async def poll_until_terminal(client: httpx.AsyncClient, expected_count: int,
                              timeout_sec: float) -> list[dict]:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        r = await client.get("/api/documents", timeout=10.0)
        r.raise_for_status()
        docs = r.json()
        terminal = all(d["status"] in ("ready", "failed") for d in docs)
        if terminal and len(docs) == expected_count:
            return docs
        await asyncio.sleep(2)
    raise TimeoutError(f"Documents did not reach terminal state within {timeout_sec}s")


async def run_scenario(scenario: str, fixtures_dir: Path, base_url: str,
                       admin_token: str, timeout_sec: float) -> dict:
    headers = {"X-Admin-Token": admin_token}
    results = []
    started_at = datetime.now(UTC).isoformat()

    manifest_path = fixtures_dir / f"manifest_{scenario}.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    scenario_dir = fixtures_dir / scenario
    file_paths = [scenario_dir / doc["path"] for doc in manifest["documents"]]

    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
        cleared = await clear_all(client)
        print(f"  Cleared {cleared} existing documents")

        # Upload
        t0 = time.monotonic()
        upload_result = await upload_batch(client, file_paths)
        upload_elapsed = time.monotonic() - t0
        print(f"  Upload: {upload_result['succeeded']} succeeded, "
              f"{upload_result['failed']} failed in {upload_elapsed:.1f}s")

        # Poll
        docs = await poll_until_terminal(
            client, len(file_paths), timeout_sec,
        )
        total_elapsed = time.monotonic() - t0

        for doc in docs:
            results.append({
                "document_id": doc["id"],
                "filename": doc["filename"],
                "file_size": doc["file_size"],
                "status": doc["status"],
                "chunk_count": doc["chunk_count"],
                "error_message": doc.get("error_message"),
            })

        ready = sum(1 for d in docs if d["status"] == "ready")
        failed = sum(1 for d in docs if d["status"] == "failed")

        return {
            "scenario": scenario,
            "started_at": started_at,
            "upload_elapsed_s": round(upload_elapsed, 1),
            "total_elapsed_s": round(total_elapsed, 1),
            "total_files": len(file_paths),
            "ready": ready,
            "failed": failed,
            "success_rate": round(ready / max(len(file_paths), 1), 4),
            "results": results,
        }


def main():
    parser = argparse.ArgumentParser(description="Upload capacity benchmark")
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--fixtures-dir", default="fixtures_benchmark")
    parser.add_argument("--output", required=True, help="Output JSON path")
    parser.add_argument("--base-url", default="http://127.0.0.1:18000")
    parser.add_argument("--admin-token", default="rag-agent-e2e-admin-token")
    parser.add_argument("--acknowledge-clear", action="store_true",
                        help="Confirm you want to clear the knowledge base")
    parser.add_argument("--timeout", type=float, default=900.0,
                        help="Per-file poll timeout multiplier (total = files * timeout)")
    args = parser.parse_args()

    if not args.acknowledge_clear:
        print("ERROR: --acknowledge-clear is required (this will DELETE all documents)", file=sys.stderr)
        sys.exit(1)

    fixtures_dir = Path(args.fixtures_dir)
    scenario_dir = fixtures_dir / args.scenario
    if not scenario_dir.is_dir():
        print(f"Scenario directory not found: {scenario_dir}", file=sys.stderr)
        sys.exit(1)

    manifest_path = fixtures_dir / f"manifest_{args.scenario}.json"
    if not manifest_path.exists():
        print(f"Manifest not found: {manifest_path}", file=sys.stderr)
        sys.exit(1)

    file_count = len(list(scenario_dir.iterdir()))
    timeout = file_count * args.timeout if args.timeout else 900.0

    print(f"Upload benchmark: {args.scenario} ({file_count} files, timeout {timeout}s)")
    result = asyncio.run(run_scenario(
        args.scenario, fixtures_dir, args.base_url, args.admin_token, timeout,
    ))

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nResults written to {output_path}")
    print(f"  Success rate: {result['success_rate']:.1%} ({result['ready']}/{result['total_files']})")
    print(f"  Total elapsed: {result['total_elapsed_s']:.1f}s")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Syntax check**

```bash
cd D:/Python/subject1/RAG_Agent
D:/Python/Python/python.exe -m py_compile scripts/benchmark/upload_bench.py && echo "OK"
```

- [ ] **Step 3: Commit**

```bash
git add scripts/benchmark/upload_bench.py
git commit -m "feat: add upload capacity benchmark with per-file tracking"
```

---

### Task 3: Concurrent Q&A benchmark (`qa_bench.py`)

**Files:**
- Create: `scripts/benchmark/qa_bench.py`

- [ ] **Step 1: Write the Q&A benchmark script**

```python
"""Concurrent Q&A benchmark — measures RAG latency and quality under load.

Usage:
  python scripts/benchmark/qa_bench.py \
    --concurrency 5 --duration 900 \
    --questions-file backend/tests/e2e/fixtures/manifest.json \
    --output artifacts/bench_20260717/raw/qa_concurrency_5.json \
    --base-url http://127.0.0.1:18000
"""

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx


def parse_sse_events(raw: str) -> dict:
    """Parse SSE text into {event_name: [data_strings]}."""
    events: dict[str, list[str]] = {}
    current_event = ""
    for line in raw.replace("\r\n", "\n").split("\n"):
        m = re.match(r"^event:\s*(.+)$", line)
        if m:
            current_event = m.group(1).strip()
            if current_event not in events:
                events[current_event] = []
            continue
        m = re.match(r"^data:\s*(.+)$", line)
        if m and current_event:
            events[current_event].append(m.group(1).strip())
    return events


async def one_qa(client: httpx.AsyncClient, question: str, sse_timeout: float) -> dict:
    body = {"message": question}
    t0 = time.monotonic()
    try:
        async with client.stream(
            "POST", "/api/chat", json=body, timeout=sse_timeout,
        ) as response:
            raw_body = ""
            first_chunk_ts = None
            async for line in response.aiter_lines():
                raw_body += line + "\n"
                if first_chunk_ts is None and "answer_chunk" in line:
                    first_chunk_ts = time.monotonic()
            elapsed = time.monotonic() - t0
            events = parse_sse_events(raw_body)

            timing = {}
            if "timing" in events and events["timing"]:
                try:
                    timing = json.loads(events["timing"][-1])
                except json.JSONDecodeError:
                    pass

            verification = {}
            if "verification" in events and events["verification"]:
                try:
                    verification = json.loads(events["verification"][-1])
                except json.JSONDecodeError:
                    pass

            return {
                "status": response.status_code,
                "elapsed_s": round(elapsed, 3),
                "ttft_s": round((first_chunk_ts - t0) if first_chunk_ts else elapsed, 3),
                "has_answer_chunk": "answer_chunk" in events,
                "has_sources": "sources" in events,
                "has_verification": "verification" in events,
                "has_done": "done" in events,
                "faithfulness": verification.get("faithfulness"),
                "citation_precision": verification.get("citation_precision"),
                "citation_recall": verification.get("citation_recall"),
                "rag_total_ms": timing.get("rag_total"),
                "rag_ttft_ms": timing.get("rag_visible_ttft"),
                "error": None,
            }
    except Exception as e:
        elapsed = time.monotonic() - t0
        return {
            "status": 0,
            "elapsed_s": round(elapsed, 3),
            "ttft_s": None,
            "has_answer_chunk": False,
            "has_sources": False,
            "has_verification": False,
            "has_done": False,
            "faithfulness": None,
            "citation_precision": None,
            "citation_recall": None,
            "rag_total_ms": None,
            "rag_ttft_ms": None,
            "error": f"{type(e).__name__}: {e}",
        }


async def worker(client: httpx.AsyncClient, questions: list[str], sse_timeout: float,
                 results: list, stats: dict):
    """Continuously send questions, cycling through the pool."""
    idx = 0
    while stats["running"]:
        q = questions[idx % len(questions)]
        idx += 1
        r = await one_qa(client, q, sse_timeout)
        results.append(r)
        with stats["lock"]:
            stats["completed"] += 1
            if r["status"] == 200 and r["has_done"]:
                stats["success"] += 1
            elif r["error"]:
                stats["errors"] += 1


async def run_concurrency_level(concurrency: int, duration_sec: float,
                                 questions: list[str], base_url: str,
                                 admin_token: str, sse_timeout: float) -> dict:
    headers = {"X-Admin-Token": admin_token, "Content-Type": "application/json"}
    started_at = datetime.now(UTC).isoformat()

    results: list[dict] = []
    stats = {
        "running": True, "completed": 0, "success": 0, "errors": 0,
        "lock": asyncio.Lock(),
    }

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=30.0) as client:
        tasks = [
            asyncio.create_task(worker(client, questions, sse_timeout, results, stats))
            for _ in range(concurrency)
        ]
        print(f"  Running {concurrency} concurrent workers for {duration_sec}s...")
        await asyncio.sleep(duration_sec)
        stats["running"] = False
        await asyncio.gather(*tasks, return_exceptions=True)

    elapsed_times = [r["elapsed_s"] for r in results]
    elapsed_times.sort()
    ttfts = [r["ttft_s"] for r in results if r["ttft_s"] is not None]
    ttfts.sort()

    def _p(arr, pct):
        if not arr:
            return None
        return arr[min(int(len(arr) * pct / 100), len(arr) - 1)]

    return {
        "concurrency": concurrency,
        "duration_s": duration_sec,
        "started_at": started_at,
        "total_requests": len(results),
        "success": stats["success"],
        "errors": stats["errors"],
        "error_rate": round(stats["errors"] / max(len(results), 1), 4),
        "elapsed_p50": _p(elapsed_times, 50),
        "elapsed_p95": _p(elapsed_times, 95),
        "elapsed_p99": _p(elapsed_times, 99),
        "ttft_p50": _p(ttfts, 50),
        "ttft_p95": _p(ttfts, 95),
        "events_complete_rate": round(
            sum(1 for r in results
                if r["has_answer_chunk"] and r["has_sources"]
                and r["has_verification"] and r["has_done"])
            / max(len(results), 1), 4,
        ),
        "avg_faithfulness": round(
            sum(r["faithfulness"] for r in results if r["faithfulness"] is not None)
            / max(sum(1 for r in results if r["faithfulness"] is not None), 1), 4,
        ),
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(description="Concurrent Q&A benchmark")
    parser.add_argument("--concurrency", type=int, required=True)
    parser.add_argument("--duration", type=float, required=True,
                        help="Duration in seconds")
    parser.add_argument("--questions-file", required=True,
                        help="Path to manifest.json or JSON array of questions")
    parser.add_argument("--output", required=True, help="Output JSON path")
    parser.add_argument("--base-url", default="http://127.0.0.1:18000")
    parser.add_argument("--admin-token", default="rag-agent-e2e-admin-token")
    parser.add_argument("--sse-timeout", type=float, default=120.0)
    args = parser.parse_args()

    # Load questions
    qf = Path(args.questions_file)
    data = json.loads(qf.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "questions" in data:
        questions = [q["question"] for q in data["questions"]]
    elif isinstance(data, list):
        questions = data
    else:
        print("Questions file must be a manifest with 'questions' key or a JSON array", file=sys.stderr)
        sys.exit(1)
    print(f"Loaded {len(questions)} questions")

    print(f"QA benchmark: concurrency={args.concurrency}, duration={args.duration}s")
    result = asyncio.run(run_concurrency_level(
        args.concurrency, args.duration, questions,
        args.base_url, args.admin_token, args.sse_timeout,
    ))

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nResults written to {output_path}")
    print(f"  Requests: {result['total_requests']}")
    print(f"  Error rate: {result['error_rate']:.1%}")
    print(f"  TTFT P50: {result['ttft_p50']}s, P95: {result['ttft_p95']}s")
    print(f"  Elapsed P50: {result['elapsed_p50']}s, P95: {result['elapsed_p95']}s")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Syntax check**

```bash
cd D:/Python/subject1/RAG_Agent
D:/Python/Python/python.exe -m py_compile scripts/benchmark/qa_bench.py && echo "OK"
```

- [ ] **Step 3: Commit**

```bash
git add scripts/benchmark/qa_bench.py
git commit -m "feat: add concurrent Q&A benchmark with SSE event parsing and latency percentiles"
```

---

### Task 4: System metrics collector (`collect_metrics.py`)

**Files:**
- Create: `scripts/benchmark/collect_metrics.py`

- [ ] **Step 1: Write the metrics collector script**

```python
"""System metrics collector — polls /api/metrics and docker stats during benchmarks.

Usage:
  python scripts/benchmark/collect_metrics.py \
    --duration 600 \
    --output artifacts/bench_20260717/raw/system.jsonl \
    --base-url http://127.0.0.1:18000
"""

import argparse
import asyncio
import json
import re
import subprocess
import sys
import time
from datetime import UTC, datetime

import httpx


def parse_prometheus(text: str) -> dict:
    """Parse Prometheus text format into a flat dict of metric_name -> value or labels dict."""
    metrics: dict = {}
    for line in text.strip().split("\n"):
        if line.startswith("#") or not line.strip():
            continue
        m = re.match(r"^(\w+)\{([^}]*)\}\s+(\S+)", line)
        if m:
            name = m.group(1)
            labels_str = m.group(2)
            value = float(m.group(3)) if "." in m.group(3) or "e" in m.group(3).lower() else int(m.group(3))
            labels = {}
            for pair in labels_str.split(","):
                k, v = pair.split("=", 1)
                labels[k.strip()] = v.strip().strip('"')
            if name not in metrics:
                metrics[name] = []
            metrics[name].append({"labels": labels, "value": value})
            continue
        m = re.match(r"^(\w+)\s+(\S+)", line)
        if m:
            metrics[m.group(1)] = float(m.group(2)) if "." in m.group(2) else int(m.group(2))
    return metrics


def docker_stats_snapshot() -> dict:
    """Get one snapshot of docker stats for e2e containers."""
    try:
        result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}"],
            capture_output=True, text=True, timeout=10,
        )
        containers = []
        for line in result.stdout.strip().split("\n"):
            if line.strip():
                try:
                    containers.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return {"containers": containers, "error": None}
    except Exception as e:
        return {"containers": [], "error": str(e)}


async def collect_loop(duration_sec: float, output_path: str, base_url: str,
                        admin_token: str, interval: float = 5.0):
    headers = {"X-Admin-Token": admin_token}
    deadline = time.monotonic() + duration_sec
    samples = 0

    with open(output_path, "w", encoding="utf-8") as f:
        async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
            while time.monotonic() < deadline:
                ts = datetime.now(UTC).isoformat()
                record = {"timestamp": ts}

                # API metrics
                try:
                    r = await client.get("/api/metrics", headers=headers)
                    if r.status_code == 200:
                        record["api_metrics"] = parse_prometheus(r.text)
                    else:
                        record["api_metrics_error"] = f"HTTP {r.status_code}"
                except Exception as e:
                    record["api_metrics_error"] = str(e)

                # Docker stats
                record["docker_stats"] = docker_stats_snapshot()

                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                f.flush()
                samples += 1

                await asyncio.sleep(interval)

    print(f"Collected {samples} samples over {duration_sec}s → {output_path}")


def main():
    parser = argparse.ArgumentParser(description="System metrics collector")
    parser.add_argument("--duration", type=float, required=True, help="Seconds to collect")
    parser.add_argument("--output", required=True, help="Output JSONL path")
    parser.add_argument("--base-url", default="http://127.0.0.1:18000")
    parser.add_argument("--admin-token", default="rag-agent-e2e-admin-token")
    parser.add_argument("--interval", type=float, default=5.0, help="Poll interval in seconds")
    args = parser.parse_args()

    print(f"Collecting metrics for {args.duration}s (interval={args.interval}s)")
    asyncio.run(collect_loop(
        args.duration, args.output, args.base_url, args.admin_token, args.interval,
    ))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Syntax check**

```bash
cd D:/Python/subject1/RAG_Agent
D:/Python/Python/python.exe -m py_compile scripts/benchmark/collect_metrics.py && echo "OK"
```

- [ ] **Step 3: Commit**

```bash
git add scripts/benchmark/collect_metrics.py
git commit -m "feat: add system metrics collector for Prometheus API and docker stats"
```

---

### Task 5: Report generator (`generate_report.py`)

**Files:**
- Create: `scripts/benchmark/generate_report.py`

- [ ] **Step 1: Write the report generator**

```python
"""Capacity baseline report generator.

Reads raw benchmark data and produces:
  - docs/CAPACITY_BASELINE_REPORT_<date>.md
  - artifacts/bench_<date>/summary.json
  - artifacts/bench_<date>/raw/*.csv (summary CSVs)

Usage:
  python scripts/benchmark/generate_report.py \
    --input-dir artifacts/bench_20260717/raw/ \
    --output-dir artifacts/bench_20260717/
"""

import argparse
import csv
import io
import json
import os
import subprocess
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any


def _p(arr: list[float], pct: float) -> float | None:
    if not arr:
        return None
    s = sorted(arr)
    return s[min(int(len(s) * pct / 100), len(s) - 1)]


def load_upload_results(raw_dir: Path) -> list[dict]:
    results = []
    for f in sorted(raw_dir.glob("upload_*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        results.append(data)
    return results


def load_qa_results(raw_dir: Path) -> list[dict]:
    results = []
    for f in sorted(raw_dir.glob("qa_concurrency_*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        results.append(data)
    return results


def load_system_metrics(raw_dir: Path) -> list[dict]:
    metrics = []
    sys_file = raw_dir / "system.jsonl"
    if sys_file.exists():
        for line in sys_file.read_text(encoding="utf-8").strip().split("\n"):
            if line.strip():
                metrics.append(json.loads(line))
    return metrics


def get_git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True,
        ).strip()
    except Exception:
        return "unknown"


def generate_upload_table(results: list[dict]) -> str:
    rows = [
        "| Scenario | Files | Ready | Failed | Success Rate | Total Time |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        rows.append(
            f"| {r['scenario']} | {r['total_files']} | {r['ready']} | "
            f"{r['failed']} | {r['success_rate']:.1%} | {r['total_elapsed_s']:.0f}s |"
        )
    return "\n".join(rows)


def generate_qa_table(results: list[dict]) -> str:
    rows = [
        "| Concurrency | Requests | Error Rate | TTFT P50 | TTFT P95 | "
        "Elapsed P50 | Elapsed P95 | SSE Complete | Avg Faithfulness |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sorted(results, key=lambda x: x["concurrency"]):
        rows.append(
            f"| {r['concurrency']} | {r['total_requests']} | "
            f"{r['error_rate']:.1%} | "
            f"{r['ttft_p50']}s | {r['ttft_p95']}s | "
            f"{r['elapsed_p50']}s | {r['elapsed_p95']}s | "
            f"{r['events_complete_rate']:.1%} | {r['avg_faithfulness']:.2f} |"
        )
    return "\n".join(rows)


def analyze_bottleneck(qa_results: list[dict]) -> str:
    """Identify dominant RAG phase from timing data."""
    phase_totals: dict[str, float] = defaultdict(float)
    phase_count = 0
    for r in qa_results:
        for req in r.get("results", []):
            if req.get("rag_total_ms"):
                phase_count += 1
    if phase_count == 0:
        return "_No phase timing data available._"

    return (
        "Bottleneck analysis requires per-phase timing data from the SSE timing event. "
        "Check individual request records in the raw JSON output for "
        "`rag_total_ms`, `rag_ttft_ms`, and other phase timings."
    )


def generate_markdown(upload_results: list[dict], qa_results: list[dict],
                      system_metrics: list[dict], output_dir: Path) -> str:
    commit = get_git_commit()
    today = date.today().isoformat()

    sections = [
        f"# Capacity & Performance Baseline Report — {today}",
        "",
        f"> Git commit: `{commit}`  ",
        f"> Date: {today}  ",
        f"> Raw data: `{output_dir.resolve()}/raw/`",
        "",
        "## 1. Upload Matrix",
        "",
        generate_upload_table(upload_results),
        "",
        "## 2. Q&A Concurrency Matrix",
        "",
        generate_qa_table(qa_results),
        "",
        "## 3. Bottleneck Analysis",
        "",
        analyze_bottleneck(qa_results),
        "",
        "## 4. System Metrics",
        "",
        f"System metrics collected: {len(system_metrics)} samples.",
        "",
        "## 5. Recommendations",
        "",
        "_Fill in after reviewing the data above. "
        "Key decision points: max stable upload batch, max recommended concurrency, "
        "bottleneck ranking (LLM / embedding / SQLite / Qdrant / BM25 / proxy)._",
        "",
        "## 6. Raw Data Index",
        "",
    ]

    for f in sorted(output_dir.glob("raw/*.json")):
        sections.append(f"- `{f.name}`")
    for f in sorted(output_dir.glob("raw/*.csv")):
        sections.append(f"- `{f.name}`")

    return "\n".join(sections)


def write_summary_json(upload_results: list[dict], qa_results: list[dict],
                       output_dir: Path) -> dict:
    summary = {
        "generated_at": date.today().isoformat(),
        "git_commit": get_git_commit(),
        "upload_scenarios": {},
        "qa_levels": {},
    }
    for r in upload_results:
        summary["upload_scenarios"][r["scenario"]] = {
            "files": r["total_files"],
            "ready": r["ready"],
            "failed": r["failed"],
            "success_rate": r["success_rate"],
        }
    for r in qa_results:
        summary["qa_levels"][str(r["concurrency"])] = {
            "requests": r["total_requests"],
            "error_rate": r["error_rate"],
            "ttft_p50": r["ttft_p50"],
            "ttft_p95": r["ttft_p95"],
            "elapsed_p50": r["elapsed_p50"],
            "elapsed_p95": r["elapsed_p95"],
            "events_complete_rate": r["events_complete_rate"],
        }
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def write_csv_exports(upload_results: list[dict], qa_results: list[dict],
                      raw_dir: Path):
    raw_dir.mkdir(parents=True, exist_ok=True)
    # Upload CSV
    uf = raw_dir / "upload_summary.csv"
    with open(uf, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["scenario", "files", "ready", "failed", "success_rate", "total_elapsed_s"])
        for r in upload_results:
            w.writerow([r["scenario"], r["total_files"], r["ready"], r["failed"],
                        r["success_rate"], r["total_elapsed_s"]])
    # QA CSV
    qf = raw_dir / "qa_summary.csv"
    with open(qf, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["concurrency", "requests", "error_rate", "ttft_p50", "ttft_p95",
                     "elapsed_p50", "elapsed_p95", "events_complete_rate", "avg_faithfulness"])
        for r in qa_results:
            w.writerow([r["concurrency"], r["total_requests"], r["error_rate"],
                        r["ttft_p50"], r["ttft_p95"], r["elapsed_p50"], r["elapsed_p95"],
                        r["events_complete_rate"], r["avg_faithfulness"]])


def main():
    parser = argparse.ArgumentParser(description="Capacity baseline report generator")
    parser.add_argument("--input-dir", required=True, help="Directory containing raw/ subdirectory")
    parser.add_argument("--output-dir", required=True, help="Output directory for report")
    args = parser.parse_args()

    raw_dir = Path(args.input_dir) / "raw"
    if not raw_dir.is_dir():
        print(f"Raw data directory not found: {raw_dir}", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(args.output_dir)
    upload_results = load_upload_results(raw_dir)
    qa_results = load_qa_results(raw_dir)
    system_metrics = load_system_metrics(raw_dir)

    print(f"Loaded: {len(upload_results)} upload scenarios, "
          f"{len(qa_results)} concurrency levels, "
          f"{len(system_metrics)} system metric samples")

    # Generate report
    md = generate_markdown(upload_results, qa_results, system_metrics, output_dir)
    report_dir = Path("docs")
    report_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    report_path = report_dir / f"CAPACITY_BASELINE_REPORT_{today}.md"
    report_path.write_text(md, encoding="utf-8")
    print(f"Report: {report_path.resolve()}")

    # Write summary
    write_summary_json(upload_results, qa_results, output_dir)
    print(f"Summary: {(output_dir / 'summary.json').resolve()}")

    # Write CSVs
    write_csv_exports(upload_results, qa_results, raw_dir)
    print(f"CSVs: {raw_dir.resolve()}/")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Syntax check**

```bash
cd D:/Python/subject1/RAG_Agent
D:/Python/Python/python.exe -m py_compile scripts/benchmark/generate_report.py && echo "OK"
```

- [ ] **Step 3: Commit**

```bash
git add scripts/benchmark/generate_report.py
git commit -m "feat: add capacity baseline report generator with markdown, JSON, and CSV output"
```

---

### Task 6: Orchestrator (`run_all.py`)

**Files:**
- Create: `scripts/benchmark/run_all.py`

- [ ] **Step 1: Write the orchestrator**

```python
"""Benchmark orchestrator — runs all benchmarks sequentially with skip/resume support.

Usage:
  python scripts/benchmark/run_all.py \
    --scenarios small_batch medium_batch mixed_formats partial_invalid \
    --concurrency 1 5 \
    --output-dir artifacts/bench_20260717 \
    --base-url http://127.0.0.1:18000 \
    --admin-token rag-agent-e2e-admin-token \
    --acknowledge-clear
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

CONCURRENCY_DURATIONS = {
    1: 600,   # 10 min
    5: 900,   # 15 min
    10: 1200,  # 20 min
    25: 1200,  # 20 min
    50: 600,   # 10 min
}

QUESTIONS_FILE = "backend/tests/e2e/fixtures/manifest.json"


def run_step(cmd: list[str], label: str) -> bool:
    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}")
    t0 = time.monotonic()
    result = subprocess.run(cmd)
    elapsed = time.monotonic() - t0
    if result.returncode == 0:
        print(f"[OK] {label} ({elapsed:.0f}s)")
        return True
    else:
        print(f"[FAIL] {label} (exit {result.returncode}, {elapsed:.0f}s)", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="Run all capacity benchmarks")
    parser.add_argument("--scenarios", nargs="*",
                        default=["small_batch", "medium_batch", "mixed_formats", "partial_invalid"],
                        help="Upload scenarios to run")
    parser.add_argument("--concurrency", type=int, nargs="*",
                        default=[1, 5],
                        help="Concurrency levels to run")
    parser.add_argument("--output-dir", default="artifacts/bench_20260717")
    parser.add_argument("--base-url", default="http://127.0.0.1:18000")
    parser.add_argument("--admin-token", default="rag-agent-e2e-admin-token")
    parser.add_argument("--acknowledge-clear", action="store_true")
    parser.add_argument("--skip-generate", action="store_true")
    parser.add_argument("--skip-upload", action="store_true")
    parser.add_argument("--skip-qa", action="store_true")
    parser.add_argument("--fixtures-dir", default="fixtures_benchmark")
    parser.add_argument("--collector-interval", type=float, default=5.0)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    fixtures_dir = Path(args.fixtures_dir)

    script_dir = Path(__file__).resolve().parent
    python = sys.executable

    failed = False

    # Step 1: Generate fixtures
    if not args.skip_generate:
        if not run_step(
            [python, str(script_dir / "generate_fixtures.py"),
             "--all", "--output-dir", str(fixtures_dir)],
            "Generate test fixtures",
        ):
            failed = True

    # Step 2: Upload benchmarks
    if not args.skip_upload and not failed:
        for scenario in args.scenarios:
            output_file = raw_dir / f"upload_{scenario}.json"
            if output_file.exists():
                print(f"  Skipping {scenario} (output exists: {output_file})")
                continue
            if not run_step(
                [python, str(script_dir / "upload_bench.py"),
                 "--scenario", scenario,
                 "--fixtures-dir", str(fixtures_dir),
                 "--output", str(output_file),
                 "--base-url", args.base_url,
                 "--admin-token", args.admin_token,
                 "--acknowledge-clear"],
                f"Upload benchmark: {scenario}",
            ):
                failed = True
                break

    # Step 3: QA benchmarks (with system metrics collector)
    if not args.skip_qa and not failed:
        for level in args.concurrency:
            output_file = raw_dir / f"qa_concurrency_{level}.json"
            if output_file.exists():
                print(f"  Skipping concurrency={level} (output exists: {output_file})")
                continue
            duration = CONCURRENCY_DURATIONS.get(level, 600)
            sys_metrics_file = raw_dir / "system.jsonl"
            # Start collector in background
            collector = subprocess.Popen(
                [python, str(script_dir / "collect_metrics.py"),
                 "--duration", str(duration),
                 "--output", str(sys_metrics_file),
                 "--base-url", args.base_url,
                 "--admin-token", args.admin_token,
                 "--interval", str(args.collector_interval)],
            )
            qa_ok = run_step(
                [python, str(script_dir / "qa_bench.py"),
                 "--concurrency", str(level),
                 "--duration", str(duration),
                 "--questions-file", QUESTIONS_FILE,
                 "--output", str(output_file),
                 "--base-url", args.base_url,
                 "--admin-token", args.admin_token],
                f"QA benchmark: concurrency={level}, duration={duration}s",
            )
            collector.wait()
            if not qa_ok:
                failed = True
                break

    # Step 4: Generate report (always, even on partial data)
    report_ok = run_step(
        [python, str(script_dir / "generate_report.py"),
         "--input-dir", str(output_dir),
         "--output-dir", str(output_dir)],
        "Generate capacity baseline report",
    )

    if failed:
        print("\nSome benchmarks failed. Report generated from partial data.", file=sys.stderr)
        sys.exit(1)
    print("\nAll benchmarks complete.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Syntax check**

```bash
cd D:/Python/subject1/RAG_Agent
D:/Python/Python/python.exe -m py_compile scripts/benchmark/run_all.py && echo "OK"
```

- [ ] **Step 3: Commit**

```bash
git add scripts/benchmark/run_all.py
git commit -m "feat: add benchmark orchestrator with skip/resume and collector integration"
```

---

### Task 7: Add fixtures_benchmark/ to .gitignore and final verification

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Add fixtures_benchmark to .gitignore**

Read `.gitignore`, append `fixtures_benchmark/` on a new line.

- [ ] **Step 2: Verify all scripts compile**

```bash
cd D:/Python/subject1/RAG_Agent
for f in scripts/benchmark/*.py; do
    D:/Python/Python/python.exe -m py_compile "$f" && echo "$f: OK" || echo "$f: FAIL"
done
```

Expected: all 6 files compile OK.

- [ ] **Step 3: Verify help text for all tools**

```bash
cd D:/Python/subject1/RAG_Agent
D:/Python/Python/python.exe scripts/benchmark/generate_fixtures.py --help
D:/Python/Python/python.exe scripts/benchmark/upload_bench.py --help
D:/Python/Python/python.exe scripts/benchmark/qa_bench.py --help
D:/Python/Python/python.exe scripts/benchmark/collect_metrics.py --help
D:/Python/Python/python.exe scripts/benchmark/generate_report.py --help
D:/Python/Python/python.exe scripts/benchmark/run_all.py --help
```

Expected: each prints usage without errors.

- [ ] **Step 4: Commit**

```bash
git add .gitignore
git commit -m "chore: add fixtures_benchmark/ to .gitignore, verify all benchmark tools compile"
```
