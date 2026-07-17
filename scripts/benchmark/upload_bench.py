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

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=30.0) as client:
        cleared = await clear_all(client)
        print(f"  Cleared {cleared} existing documents")

        t0 = time.monotonic()
        upload_result = await upload_batch(client, file_paths)
        upload_elapsed = time.monotonic() - t0
        print(f"  Upload: {upload_result['succeeded']} succeeded, "
              f"{upload_result['failed']} failed in {upload_elapsed:.1f}s")

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
    parser.add_argument("--timeout", type=float, default=30.0,
                        help="Per-file poll timeout (total = files * timeout seconds)")
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

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    file_count = len(manifest.get("documents", []))
    if file_count == 0:
        print("Manifest has no documents", file=sys.stderr)
        sys.exit(1)
    timeout = file_count * args.timeout

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
