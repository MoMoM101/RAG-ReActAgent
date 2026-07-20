"""Validate the canonical grounded-answer evaluation before release.

This command never calls an external model. It verifies that a checked-in
full evaluation was produced by the current dataset, verifier, evaluator and
optimized prompt, then recomputes quality/performance gates from the aggregate.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tests.qrels_schema import QrelDataset
from tests.run_grounded_answer_eval import (
    EVALUATION_SCOPE,
    SCORING_VERSION,
    _evaluation_provenance,
    evaluate_performance_gate,
    evaluate_quality_gate,
)

DEFAULT_REPORT = (
    Path(__file__).parent
    / "tests"
    / "grounded_answer_eval_final_full_rescored.json"
)
DEFAULT_DATASET = Path(__file__).parent / "tests" / "qrels_data_v2.json"


def validate_release_report(
    report: dict[str, Any],
    dataset: QrelDataset,
    *,
    max_age_days: int = 30,
    require_performance: bool = True,
) -> list[str]:
    """Return actionable release blockers for a grounded-answer report."""
    blockers: list[str] = []
    if report.get("schema_version") != "1.0":
        blockers.append("unsupported or missing schema_version")
    if report.get("scoring_version") != SCORING_VERSION:
        blockers.append(
            f"stale scoring_version={report.get('scoring_version')!r}; "
            f"expected {SCORING_VERSION!r}",
        )
    if report.get("evaluation_scope") != EVALUATION_SCOPE:
        blockers.append(
            f"invalid evaluation_scope={report.get('evaluation_scope')!r}; "
            f"expected {EVALUATION_SCOPE!r}",
        )

    expected_provenance = _evaluation_provenance()
    actual_provenance = report.get("provenance") or {}
    for key, expected in expected_provenance.items():
        actual = actual_provenance.get(key)
        if actual != expected:
            blockers.append(f"stale provenance {key}: expected {expected}, got {actual}")

    expected_ids = {query.query_id for query in dataset.queries}
    records = report.get("records") or []
    for mode in ("control", "optimized"):
        mode_records = [record for record in records if record.get("mode") == mode]
        actual_ids = {str(record.get("query_id")) for record in mode_records}
        missing = sorted(expected_ids - actual_ids)
        unexpected = sorted(actual_ids - expected_ids)
        duplicates = len(mode_records) - len(actual_ids)
        if missing:
            blockers.append(f"{mode} is missing {len(missing)} query ids: {missing[:5]}")
        if unexpected:
            blockers.append(f"{mode} has unexpected query ids: {unexpected[:5]}")
        if duplicates:
            blockers.append(f"{mode} has {duplicates} duplicate records")
        errors = [record for record in mode_records if record.get("error")]
        if errors:
            blockers.append(f"{mode} has {len(errors)} generation errors")

    aggregate = report.get("aggregate")
    if not isinstance(aggregate, dict) or not {"control", "optimized"} <= set(aggregate):
        blockers.append("missing control/optimized aggregate")
    else:
        quality_gate = evaluate_quality_gate(aggregate)
        blockers.extend(f"quality: {item}" for item in quality_gate["violations"])
        if require_performance:
            performance_gate = evaluate_performance_gate(aggregate)
            blockers.extend(f"performance: {item}" for item in performance_gate["violations"])

    timestamp = report.get("timestamp")
    try:
        generated_at = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=UTC)
        age_days = (datetime.now(UTC) - generated_at.astimezone(UTC)).days
        if age_days > max_age_days:
            blockers.append(f"evaluation is stale: age={age_days} days, max={max_age_days}")
    except (TypeError, ValueError):
        blockers.append("invalid or missing evaluation timestamp")
    return blockers


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--max-age-days", type=int, default=30)
    parser.add_argument(
        "--quality-only",
        action="store_true",
        help="do not block on latency/performance targets",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not args.report.is_file():
        print(json.dumps({"passed": False, "blockers": [f"report not found: {args.report}"]}, ensure_ascii=False))
        return 1
    report = json.loads(args.report.read_text(encoding="utf-8"))
    dataset = QrelDataset.load(str(args.dataset))
    blockers = validate_release_report(
        report,
        dataset,
        max_age_days=args.max_age_days,
        require_performance=not args.quality_only,
    )
    print(json.dumps({"passed": not blockers, "blockers": blockers}, ensure_ascii=False, indent=2))
    return 0 if not blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
