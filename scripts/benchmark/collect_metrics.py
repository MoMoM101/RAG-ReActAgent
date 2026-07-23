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
import os
import re
import subprocess
import time
from contextlib import suppress
from datetime import UTC, datetime

import httpx
from jwt_auth import login_access_token


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
                kv = pair.split("=", 1)
                if len(kv) == 2:
                    labels[kv[0].strip()] = kv[1].strip().strip('"')
            if name not in metrics:
                metrics[name] = []
            metrics[name].append({"labels": labels, "value": value})
            continue
        m = re.match(r"^(\w+)\s+(\S+)", line)
        if m:
            v_str = m.group(2)
            metrics[m.group(1)] = float(v_str) if "." in v_str else int(v_str)
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
                with suppress(json.JSONDecodeError):
                    containers.append(json.loads(line))
        return {"containers": containers, "error": None}
    except Exception as e:
        return {"containers": [], "error": str(e)}


async def collect_loop(duration_sec: float, output_path: str, base_url: str,
                        username: str, password: str, interval: float = 5.0):
    access_token = await login_access_token(base_url, username, password)
    headers = {"Authorization": f"Bearer {access_token}"}
    deadline = time.monotonic() + duration_sec
    samples = 0

    with open(output_path, "w", encoding="utf-8") as f:
        async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=10.0) as client:
            while time.monotonic() < deadline:
                ts = datetime.now(UTC).isoformat()
                record = {"timestamp": ts}

                try:
                    r = await client.get("/api/metrics")
                    if r.status_code == 200:
                        record["api_metrics"] = parse_prometheus(r.text)
                    else:
                        record["api_metrics_error"] = f"HTTP {r.status_code}"
                except Exception as e:
                    record["api_metrics_error"] = str(e)

                record["docker_stats"] = docker_stats_snapshot()

                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                f.flush()
                samples += 1

                await asyncio.sleep(interval)

    print(f"Collected {samples} samples over {duration_sec}s -> {output_path}")


def main():
    parser = argparse.ArgumentParser(description="System metrics collector")
    parser.add_argument("--duration", type=float, required=True, help="Seconds to collect")
    parser.add_argument("--output", required=True, help="Output JSONL path")
    parser.add_argument("--base-url", default="http://127.0.0.1:18000")
    parser.add_argument("--username", default=os.environ.get("E2E_ADMIN_USERNAME", "admin"))
    parser.add_argument("--password", default=os.environ.get("E2E_ADMIN_PASSWORD"))
    parser.add_argument("--interval", type=float, default=5.0, help="Poll interval in seconds")
    args = parser.parse_args()

    if not args.password:
        parser.error("--password or E2E_ADMIN_PASSWORD is required")

    print(f"Collecting metrics for {args.duration}s (interval={args.interval}s)")
    asyncio.run(collect_loop(
        args.duration, args.output, args.base_url, args.username, args.password, args.interval,
    ))


if __name__ == "__main__":
    main()
