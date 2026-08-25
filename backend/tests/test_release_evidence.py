"""Release Docker E2E evidence contract tests."""

from datetime import UTC, datetime, timedelta

from release_evidence import REQUIRED_STAGES, validate_evidence


def _passing_evidence(digest: str = "source") -> dict:
    return {
        "evidence_schema_version": "1.0",
        "source_digest_sha256": digest,
        "run_id": "ragagent-e2e-20260825-120000-1234abcd",
        "timestamp": datetime.now(UTC).isoformat(),
        "git_commit": "a" * 40,
        "overall": "passed",
        "failed_stage": None,
        "stages": {
            stage: {"status": "passed", "elapsed_s": 1.0, "error": ""}
            for stage in REQUIRED_STAGES
        },
        "config_snapshot": {},
    }


def test_accepts_fresh_matching_complete_evidence():
    assert validate_evidence(_passing_evidence(), "source") == []


def test_rejects_stale_or_mismatched_evidence():
    evidence = _passing_evidence("old-source")
    evidence["timestamp"] = (datetime.now(UTC) - timedelta(days=8)).isoformat()

    blockers = validate_evidence(evidence, "current-source")

    assert any("does not match" in blocker for blocker in blockers)
    assert any("stale" in blocker for blocker in blockers)


def test_rejects_incomplete_or_failed_stage():
    evidence = _passing_evidence()
    evidence["stages"].pop("smoke")
    evidence["stages"]["sse_qa"]["status"] = "failed"

    blockers = validate_evidence(evidence, "source")

    assert any("missing Docker E2E stages" in blocker for blocker in blockers)
    assert any("sse_qa" in blocker for blocker in blockers)


def test_rejects_unexpected_fields_that_could_carry_secrets():
    evidence = _passing_evidence()
    evidence["api_key"] = "must-not-be-committed"

    blockers = validate_evidence(evidence, "source")

    assert any("unexpected Docker E2E evidence fields" in blocker for blocker in blockers)
