"""Validate sanitized local Docker E2E evidence for release tags."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EVIDENCE = (
    Path(__file__).resolve().parent
    / "tests"
    / "e2e"
    / "docker_e2e_release_evidence.json"
)
EVIDENCE_PATH = "backend/tests/e2e/docker_e2e_release_evidence.json"
REQUIRED_STAGES = (
    "config_check",
    "build",
    "health",
    "secrets_check",
    "auth_check",
    "upload",
    "consistency",
    "sse_qa",
    "restart_persistence",
    "backup_restore",
    "degradation",
    "smoke",
)
RUN_ID_PATTERN = re.compile(r"^ragagent-e2e-\d{8}-\d{6}-[0-9a-f]{8}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
TOP_LEVEL_FIELDS = {
    "evidence_schema_version",
    "run_id",
    "timestamp",
    "git_commit",
    "source_digest_sha256",
    "overall",
    "failed_stage",
    "stages",
    "config_snapshot",
}
STAGE_FIELDS = {"status", "started", "elapsed_s", "error"}
CONFIG_FIELDS = {
    "llm_provider",
    "llm_model_sha256",
    "embedding_provider",
    "embedding_model_sha256",
    "secret_key",
}


def source_digest(repo_root: Path = REPO_ROOT) -> str:
    """Hash the tracked Git tree, excluding the generated evidence itself."""
    result = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-s", "-z"],
        check=True,
        capture_output=True,
    )
    digest = hashlib.sha256()
    for entry in result.stdout.split(b"\0"):
        if not entry:
            continue
        metadata, path_bytes = entry.split(b"\t", 1)
        path = path_bytes.decode("utf-8", errors="strict").replace("\\", "/")
        if path == EVIDENCE_PATH:
            continue
        mode, object_id, stage = metadata.split()
        if stage != b"0":
            raise ValueError(f"unmerged Git index entry: {path}")
        digest.update(mode)
        digest.update(b"\0")
        digest.update(object_id)
        digest.update(b"\0")
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def tracked_worktree_is_clean(repo_root: Path = REPO_ROOT) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "diff", "--quiet", "HEAD", "--"],
        check=False,
    )
    return result.returncode == 0


def commit_is_ancestor(commit: str, repo_root: Path = REPO_ROOT) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "merge-base", "--is-ancestor", commit, "HEAD"],
        check=False,
        capture_output=True,
    )
    return result.returncode == 0


def validate_evidence(
    evidence: dict[str, Any],
    expected_source_digest: str,
    *,
    max_age_days: int = 7,
) -> list[str]:
    """Return release blockers for a sanitized Docker E2E report."""
    blockers: list[str] = []
    unexpected_top_level = sorted(set(evidence) - TOP_LEVEL_FIELDS)
    if unexpected_top_level:
        blockers.append(f"unexpected Docker E2E evidence fields: {unexpected_top_level}")
    if evidence.get("evidence_schema_version") != "1.0":
        blockers.append("unsupported or missing evidence_schema_version")
    if evidence.get("source_digest_sha256") != expected_source_digest:
        blockers.append("Docker E2E evidence does not match the tagged source tree")
    if evidence.get("overall") != "passed" or evidence.get("failed_stage") not in (None, ""):
        blockers.append("Docker E2E evidence is not a clean passing run")
    if not RUN_ID_PATTERN.fullmatch(str(evidence.get("run_id", ""))):
        blockers.append("invalid Docker E2E run_id")
    if not COMMIT_PATTERN.fullmatch(str(evidence.get("git_commit", ""))):
        blockers.append("Docker E2E evidence must record a full Git commit")

    stages = evidence.get("stages")
    if not isinstance(stages, dict):
        blockers.append("missing Docker E2E stage results")
    else:
        missing = sorted(set(REQUIRED_STAGES) - set(stages))
        unexpected = sorted(set(stages) - set(REQUIRED_STAGES))
        if missing:
            blockers.append(f"missing Docker E2E stages: {missing}")
        if unexpected:
            blockers.append(f"unexpected Docker E2E stages: {unexpected}")
        for stage_name in REQUIRED_STAGES:
            stage = stages.get(stage_name)
            if not isinstance(stage, dict) or stage.get("status") != "passed":
                blockers.append(f"Docker E2E stage did not pass: {stage_name}")
            elif stage.get("error") not in (None, ""):
                blockers.append(f"Docker E2E stage contains an error: {stage_name}")
            if isinstance(stage, dict):
                unexpected_fields = sorted(set(stage) - STAGE_FIELDS)
                if unexpected_fields:
                    blockers.append(
                        f"unexpected fields in Docker E2E stage {stage_name}: "
                        f"{unexpected_fields}",
                    )

    config_snapshot = evidence.get("config_snapshot")
    if not isinstance(config_snapshot, dict):
        blockers.append("missing Docker E2E config snapshot")
    else:
        unexpected_config = sorted(set(config_snapshot) - CONFIG_FIELDS)
        if unexpected_config:
            blockers.append(f"unexpected Docker E2E config fields: {unexpected_config}")

    timestamp = evidence.get("timestamp")
    try:
        generated_at = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=UTC)
        age = datetime.now(UTC) - generated_at.astimezone(UTC)
        if age.total_seconds() < -300:
            blockers.append("Docker E2E evidence timestamp is in the future")
        elif age.days > max_age_days:
            blockers.append(
                f"Docker E2E evidence is stale: age={age.days} days, max={max_age_days}",
            )
    except (TypeError, ValueError):
        blockers.append("invalid or missing Docker E2E timestamp")
    return blockers


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    digest_parser = subparsers.add_parser(
        "digest",
        help="print the current sanitized source digest",
    )
    digest_parser.add_argument("--require-clean", action="store_true")
    validate = subparsers.add_parser("validate", help="validate checked-in E2E evidence")
    validate.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    validate.add_argument("--max-age-days", type=int, default=7)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.command == "digest":
        if args.require_clean and not tracked_worktree_is_clean():
            print("tracked worktree is not clean")
            return 1
        digest = source_digest()
        print(digest)
        return 0
    if not args.evidence.is_file():
        print(json.dumps({"passed": False, "blockers": [f"evidence not found: {args.evidence}"]}))
        return 1
    evidence = json.loads(args.evidence.read_text(encoding="utf-8-sig"))
    blockers = validate_evidence(
        evidence,
        source_digest(),
        max_age_days=args.max_age_days,
    )
    commit = str(evidence.get("git_commit", ""))
    if COMMIT_PATTERN.fullmatch(commit) and not commit_is_ancestor(commit):
        blockers.append("Docker E2E evidence commit is not an ancestor of the tag")
    print(json.dumps({"passed": not blockers, "blockers": blockers}, ensure_ascii=False, indent=2))
    return 0 if not blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
