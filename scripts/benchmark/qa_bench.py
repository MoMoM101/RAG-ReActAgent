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


def parse_sse_events(raw: str) -> dict[str, list[str]]:
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

    qf = Path(args.questions_file)
    data = json.loads(qf.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "questions" in data:
        questions = [q["question"] for q in data["questions"]]
    elif isinstance(data, list):
        questions = data
    else:
        print("Questions file must be a manifest with 'questions' key or a JSON array",
              file=sys.stderr)
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
