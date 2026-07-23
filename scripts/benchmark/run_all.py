"""Benchmark orchestrator — runs all benchmarks sequentially with skip/resume support.

Usage:
  python scripts/benchmark/run_all.py \
    --scenarios small_batch medium_batch mixed_formats partial_invalid \
    --concurrency 1 5 \
    --output-dir artifacts/bench_20260717 \
    --base-url http://127.0.0.1:18000 \
    --acknowledge-clear
"""

import argparse
import os
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


def run_step(cmd: list[str], label: str, env: dict[str, str] | None = None) -> bool:
    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}")
    t0 = time.monotonic()
    result = subprocess.run(cmd, env=env)
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
    parser.add_argument("--username", default=os.environ.get("E2E_ADMIN_USERNAME", "admin"))
    parser.add_argument("--password", default=os.environ.get("E2E_ADMIN_PASSWORD"))
    parser.add_argument("--acknowledge-clear", action="store_true")
    parser.add_argument("--skip-generate", action="store_true")
    parser.add_argument("--skip-upload", action="store_true")
    parser.add_argument("--skip-qa", action="store_true")
    parser.add_argument("--fixtures-dir", default="fixtures_benchmark")
    parser.add_argument("--collector-interval", type=float, default=5.0)
    args = parser.parse_args()

    if not args.password:
        parser.error("--password or E2E_ADMIN_PASSWORD is required")

    child_env = os.environ.copy()
    child_env["E2E_ADMIN_USERNAME"] = args.username
    child_env["E2E_ADMIN_PASSWORD"] = args.password

    output_dir = Path(args.output_dir)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    fixtures_dir = Path(args.fixtures_dir)

    script_dir = Path(__file__).resolve().parent
    python = sys.executable

    failed = False

    # Step 1: Generate fixtures
    if not args.skip_generate and not run_step(
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
                 "--acknowledge-clear"],
                f"Upload benchmark: {scenario}", child_env,
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
            collector = subprocess.Popen(
                [python, str(script_dir / "collect_metrics.py"),
                 "--duration", str(duration),
                 "--output", str(sys_metrics_file),
                 "--base-url", args.base_url,
                 "--interval", str(args.collector_interval)],
                env=child_env,
            )
            qa_ok = run_step(
                [python, str(script_dir / "qa_bench.py"),
                 "--concurrency", str(level),
                 "--duration", str(duration),
                 "--questions-file", QUESTIONS_FILE,
                 "--output", str(output_file),
                 "--base-url", args.base_url],
                f"QA benchmark: concurrency={level}, duration={duration}s", child_env,
            )
            collector.wait()
            if not qa_ok:
                failed = True
                break

    # Step 4: Generate report (always, even on partial data)
    run_step(
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
