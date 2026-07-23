"""A/B latency comparison across config profiles using grounded-answer eval.

Runs the 93-sample evaluation under 4 config profiles, records per-sample
timing and quality data, and produces comparison JSON + markdown report.

Usage:
  python tests/run_latency_ab.py --profiles control cache_only combined
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROFILES: dict[str, dict[str, str]] = {
    "control": {},
    "cache_only": {
        "RAG_ANSWER_CACHE_ENABLED": "true",
    },
    "cache_stream_rewrite": {
        "RAG_ANSWER_CACHE_ENABLED": "true",
        "GROUNDING_STREAM_VERIFY_ENABLED": "true",
        "QUERY_REWRITE_ENABLED": "true",
    },
    "combined": {
        "RAG_ANSWER_CACHE_ENABLED": "true",
        "GROUNDING_STREAM_VERIFY_ENABLED": "true",
        "QUERY_REWRITE_ENABLED": "true",
        "RAG_TIMEOUT_INTENT": "5.0",
        "RAG_TIMEOUT_RETRIEVAL": "10.0",
        "RAG_TIMEOUT_GENERATION": "60.0",
        "RAG_TIMEOUT_VERIFICATION": "5.0",
        "RAG_TIMEOUT_REPAIR": "10.0",
    },
}

QUALITY_GATES: dict[str, tuple[float, str]] = {
    "avg_faithfulness": (0.98, ">="),
    "avg_citation_precision": (0.95, ">="),
    "avg_citation_recall": (0.95, ">="),
}


def _p(arr: list[float], pct: float) -> float:
    if not arr:
        return 0.0
    s = sorted(arr)
    return s[min(int(len(s) * pct / 100), len(s) - 1)]


def run_profile(profile_name: str, env_overrides: dict[str, str],
                output_dir: Path,
                eval_script: Path) -> dict[str, Any]:
    print(f"\n{'=' * 60}")
    print(f"  Profile: {profile_name}")
    print(f"{'=' * 60}")

    env_args = []
    for k, v in env_overrides.items():
        env_args.extend(["--env-override", f"{k}={v}"])

    output_file = output_dir / f"{profile_name}.json"
    cmd = [
        sys.executable, str(eval_script),
        "--output", str(output_file),
    ] + env_args

    t0 = time.monotonic()
    result = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.monotonic() - t0

    if result.returncode != 0:
        print(f"[FAIL] {profile_name} (exit {result.returncode})")
        print(result.stderr[-2000:])
        return {"profile": profile_name, "error": result.stderr[-2000:], "elapsed_s": elapsed}

    print(f"[OK] {profile_name} ({elapsed:.0f}s)")

    data = json.loads(output_file.read_text(encoding="utf-8"))
    return {
        "profile": profile_name,
        "elapsed_s": round(elapsed, 1),
        "total_samples": len(data.get("results", [])),
        "data": data,
    }


def compute_metrics(profile_result: dict) -> dict:
    results = profile_result.get("data", {}).get("results", [])
    if not results:
        return {"profile": profile_result["profile"], "error": "no results"}

    ttf_ts = [r.get("visible_ttft_ms", 0) for r in results if r.get("visible_ttft_ms")]
    totals = [r.get("rag_total_ms", 0) for r in results if r.get("rag_total_ms")]
    faiths = [r.get("faithfulness", 0) for r in results if r.get("faithfulness") is not None]
    cprec = [r.get("citation_precision", 0) for r in results if r.get("citation_precision") is not None]
    crec = [r.get("citation_recall", 0) for r in results if r.get("citation_recall") is not None]

    return {
        "profile": profile_result["profile"],
        "samples": len(results),
        "ttft_p50": round(_p(ttf_ts, 50) / 1000, 2),
        "ttft_p95": round(_p(ttf_ts, 95) / 1000, 2),
        "ttft_p99": round(_p(ttf_ts, 99) / 1000, 2),
        "rag_total_p50": round(_p(totals, 50) / 1000, 2),
        "rag_total_p95": round(_p(totals, 95) / 1000, 2),
        "rag_total_p99": round(_p(totals, 99) / 1000, 2),
        "avg_faithfulness": round(sum(faiths) / max(len(faiths), 1), 4),
        "avg_citation_precision": round(sum(cprec) / max(len(cprec), 1), 4),
        "avg_citation_recall": round(sum(crec) / max(len(crec), 1), 4),
    }


def check_quality_gates(metrics: dict) -> list[str]:
    failures = []
    for key, (threshold, op) in QUALITY_GATES.items():
        value = metrics.get(key, 0)
        if op == ">=" and value < threshold:
            failures.append(f"{key}={value:.4f} < {threshold}")
    return failures


def generate_report(all_metrics: list[dict], output_dir: Path, date_str: str):
    output_dir.mkdir(parents=True, exist_ok=True)

    comp_path = output_dir / "comparison.json"
    comp_path.write_text(json.dumps(all_metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    control = next((m for m in all_metrics if m["profile"] == "control"), None)

    lines = [
        f"# RAG Latency A/B Comparison — {date_str}",
        "",
        "## Latency Summary (seconds)",
        "",
        "| Profile | Samples | TTFT P50 | TTFT P95 | TTFT P99 | Total P50 | Total P95 | Total P99 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for m in all_metrics:
        lines.append(
            f"| {m['profile']} | {m.get('samples', 0)} | "
            f"{m.get('ttft_p50', '-')} | {m.get('ttft_p95', '-')} | {m.get('ttft_p99', '-')} | "
            f"{m.get('rag_total_p50', '-')} | {m.get('rag_total_p95', '-')} | {m.get('rag_total_p99', '-')} |"
        )

    lines += [
        "",
        "## Quality Summary",
        "",
        "| Profile | Faithfulness | Citation Precision | Citation Recall | Quality Gate |",
        "|---|---:|---:|---:|---:|",
    ]
    for m in all_metrics:
        failures = check_quality_gates(m)
        gate = "PASS" if not failures else f"FAIL: {'; '.join(failures)}"
        lines.append(
            f"| {m['profile']} | {m.get('avg_faithfulness', '-')} | "
            f"{m.get('avg_citation_precision', '-')} | "
            f"{m.get('avg_citation_recall', '-')} | {gate} |"
        )

    if control:
        lines += [
            "",
            "## Improvement vs Control",
            "",
            "| Profile | TTFT P95 Δ | Total P95 Δ | Quality Δ |",
            "|---|---:|---:|---:|",
        ]
        for m in all_metrics:
            if m["profile"] == "control":
                continue
            ttft_delta = (
                (m.get("ttft_p95", 0) - control.get("ttft_p95", 0))
                / max(control.get("ttft_p95", 0.001), 0.001)
                * 100
            )
            total_delta = (
                (m.get("rag_total_p95", 0) - control.get("rag_total_p95", 0))
                / max(control.get("rag_total_p95", 0.001), 0.001)
                * 100
            )
            faith_delta = (m.get("avg_faithfulness", 0) - control.get("avg_faithfulness", 0)) * 100
            lines.append(
                f"| {m['profile']} | {ttft_delta:+.1f}% | {total_delta:+.1f}% | {faith_delta:+.2f}pp |"
            )

    report_path = output_dir / "report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport: {report_path.resolve()}")
    print(f"Comparison: {comp_path.resolve()}")


def main():
    parser = argparse.ArgumentParser(description="RAG latency A/B comparison")
    parser.add_argument("--profiles", nargs="*",
                        default=["control", "cache_only", "cache_stream_rewrite", "combined"],
                        help="Profiles to run")
    parser.add_argument("--output-dir", default="artifacts/latency_ab")
    parser.add_argument("--eval-script",
                        default="tests/run_grounded_answer_eval.py",
                        help="Path to evaluation script")
    args = parser.parse_args()

    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    output_dir = Path(args.output_dir) / date_str
    output_dir.mkdir(parents=True, exist_ok=True)
    eval_script = Path(args.eval_script)

    if not eval_script.exists():
        print(f"Eval script not found: {eval_script}", file=sys.stderr)
        sys.exit(1)

    all_metrics = []
    for profile_name in args.profiles:
        if profile_name not in PROFILES:
            print(f"Unknown profile: {profile_name}", file=sys.stderr)
            continue
        env_overrides = PROFILES[profile_name]
        result = run_profile(profile_name, env_overrides, output_dir, eval_script)
        metrics = compute_metrics(result)
        all_metrics.append(metrics)

        failures = check_quality_gates(metrics)
        if failures:
            print(f"  Quality gate FAIL for {profile_name}: {'; '.join(failures)}")
        else:
            print(f"  Quality gate PASS for {profile_name}")

    if all_metrics:
        generate_report(all_metrics, output_dir, date_str)

    any_failed = any(check_quality_gates(m) for m in all_metrics)
    if any_failed:
        print("\nSome quality gates failed!", file=sys.stderr)
        sys.exit(1)
    print("\nAll profiles passed quality gates.")


if __name__ == "__main__":
    main()
