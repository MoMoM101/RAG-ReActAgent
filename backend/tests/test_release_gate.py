"""Release evaluation artifact contract tests."""

from datetime import UTC, datetime, timedelta

from release_gate import DEFAULT_REPORT, validate_release_report
from tests.qrels_schema import QrelDataset, QrelQuery
from tests.run_grounded_answer_eval import EVALUATION_SCOPE, SCORING_VERSION


def _dataset() -> QrelDataset:
    return QrelDataset(
        name="release-gate-test",
        version="1.0",
        queries=[QrelQuery(query_id="q1", query="question")],
    )


def _passing_aggregate() -> dict:
    return {
        "control": {
            "queries_completed": 1,
            "errors": 0,
            "faithfulness": 0.90,
            "expected_fact_recall": 0.80,
        },
        "optimized": {
            "queries_completed": 1,
            "errors": 0,
            "faithfulness": 0.96,
            "citation_precision": 0.96,
            "citation_recall": 0.96,
            "abstention_accuracy": 1.0,
            "expected_fact_recall": 0.90,
            "answer_completion_accuracy": 0.96,
            "ttft_p50_ms": 500.0,
            "ttft_p95_ms": 1200.0,
            "latency_p50_ms": 1000.0,
            "latency_p95_ms": 3000.0,
            "latency_p99_ms": 6000.0,
            "llm_repair_rate": 0.05,
            "llm_repair_triggered_count": 0,
            "llm_repair_accept_rate": None,
        },
    }


def _report(provenance: dict[str, str]) -> dict:
    return {
        "schema_version": "1.0",
        "scoring_version": SCORING_VERSION,
        "evaluation_scope": EVALUATION_SCOPE,
        "timestamp": datetime.now(UTC).isoformat(),
        "provenance": provenance,
        "aggregate": _passing_aggregate(),
        "records": [
            {"query_id": "q1", "mode": "control", "error": None},
            {"query_id": "q1", "mode": "optimized", "error": None},
        ],
    }


def test_release_gate_accepts_fresh_complete_passing_report(monkeypatch):
    provenance = {
        "dataset_sha256": "dataset",
        "verifier_sha256": "verifier",
        "evaluator_sha256": "evaluator",
        "optimized_prompt_sha256": "prompt",
    }
    monkeypatch.setattr("release_gate._evaluation_provenance", lambda: provenance)

    assert validate_release_report(_report(provenance), _dataset()) == []


def test_release_gate_rejects_stale_code_provenance(monkeypatch):
    current = {
        "dataset_sha256": "dataset-new",
        "verifier_sha256": "verifier-new",
        "evaluator_sha256": "evaluator-new",
        "optimized_prompt_sha256": "prompt-new",
    }
    monkeypatch.setattr("release_gate._evaluation_provenance", lambda: current)
    blockers = validate_release_report(
        _report({key: "old" for key in current}),
        _dataset(),
    )

    assert len([item for item in blockers if "stale provenance" in item]) == 4


def test_release_gate_rejects_incomplete_and_failed_quality_report(monkeypatch):
    provenance = {"dataset_sha256": "same"}
    monkeypatch.setattr("release_gate._evaluation_provenance", lambda: provenance)
    report = _report(provenance)
    report["records"] = [report["records"][0]]
    report["aggregate"]["optimized"]["citation_precision"] = 0.80

    blockers = validate_release_report(report, _dataset())

    assert any("optimized is missing" in item for item in blockers)
    assert any("quality: citation_precision" in item for item in blockers)


def test_release_gate_rejects_expired_report(monkeypatch):
    provenance = {"dataset_sha256": "same"}
    monkeypatch.setattr("release_gate._evaluation_provenance", lambda: provenance)
    report = _report(provenance)
    report["timestamp"] = (datetime.now(UTC) - timedelta(days=31)).isoformat()

    blockers = validate_release_report(report, _dataset(), max_age_days=30)

    assert any("evaluation is stale" in item for item in blockers)


def test_default_report_is_the_canonical_rescored_full_evaluation():
    assert DEFAULT_REPORT.name == "grounded_answer_eval_final_full_rescored.json"
