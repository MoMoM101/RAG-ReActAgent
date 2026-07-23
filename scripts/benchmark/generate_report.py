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
import json
import subprocess
import sys
from datetime import date
from pathlib import Path


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
        generate_upload_table(upload_results) if upload_results else "_No upload data available._",
        "",
        "## 2. Q&A Concurrency Matrix",
        "",
        generate_qa_table(qa_results) if qa_results else "_No Q&A data available._",
        "",
        "## 3. System Metrics",
        "",
        f"System metrics collected: {len(system_metrics)} samples across "
        f"{len({s['timestamp'][:10] for s in system_metrics if 'timestamp' in s})} time points.",
        "",
        "## 4. Recommendations",
        "",
        "_Fill in after reviewing the data above. "
        "Key decision points: max stable upload batch, max recommended concurrency, "
        "bottleneck ranking (LLM / embedding / SQLite / Qdrant / BM25 / proxy)._",
        "",
        "## 5. Raw Data Index",
        "",
    ]

    for f in sorted(output_dir.glob("raw/*.json")):
        sections.append(f"- `{f.name}`")
    for f in sorted(output_dir.glob("raw/*.csv")):
        sections.append(f"- `{f.name}`")

    return "\n".join(sections)


def write_summary_json(upload_results: list[dict], qa_results: list[dict],
                       output_dir: Path):
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
    print(f"Summary: {summary_path.resolve()}")


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
    print(f"CSV: {uf.resolve()}")

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
    print(f"CSV: {qf.resolve()}")


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

    # Generate markdown report
    md = generate_markdown(upload_results, qa_results, system_metrics, output_dir)
    report_dir = Path("docs")
    report_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    report_path = report_dir / f"CAPACITY_BASELINE_REPORT_{today}.md"
    report_path.write_text(md, encoding="utf-8")
    print(f"Report: {report_path.resolve()}")

    # Write summary JSON
    write_summary_json(upload_results, qa_results, output_dir)

    # Write CSV exports
    write_csv_exports(upload_results, qa_results, raw_dir)


if __name__ == "__main__":
    main()
