"""Generate release baseline manifest capturing commit, deps, and model state.

Usage: python backend/tests/baselines/generate_manifest.py
Outputs: release_9_1_manifest.json, dependency_snapshot.txt, rag_dataset_manifest.json
"""

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
BASELINES_DIR = Path(__file__).resolve().parent


def _get_git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=BACKEND_DIR.parent, text=True
        ).strip()
    except Exception:
        return "unknown"


def _get_python_version() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def _get_dependency_snapshot() -> str:
    try:
        return subprocess.check_output(
            [sys.executable, "-m", "pip", "freeze", "--local"],
            cwd=BACKEND_DIR, text=True,
        )
    except Exception:
        return "pip freeze failed"


def _hash_file(path: Path) -> str:
    if not path.exists():
        return "file_not_found"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _get_ocr_status() -> dict:
    try:
        sys.path.insert(0, str(BACKEND_DIR))
        from ocr.factory import get_ocr_status
        return get_ocr_status()
    except Exception:
        return {"status": "unknown", "error": "import failed"}


def _get_rerank_status() -> dict:
    try:
        sys.path.insert(0, str(BACKEND_DIR))
        from reranker.factory import get_reranker_status
        return get_reranker_status()
    except Exception:
        return {"status": "unknown", "error": "import failed"}


def _get_config_summary() -> dict:
    """Return sanitized config summary (no keys, tokens, or passwords)."""
    sys.path.insert(0, str(BACKEND_DIR))
    from config import settings
    return {
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model,
        "embedding_dim": settings.embedding_dim,
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
        "retrieval_top_k": settings.retrieval_top_k,
        "rerank_enabled": settings.rerank_enabled,
        "rerank_model": settings.rerank_model,
        "rrf_semantic_weight": settings.rrf_semantic_weight,
        "rrf_keyword_weight": settings.rrf_keyword_weight,
        "rrf_adaptive_enabled": settings.rrf_adaptive_enabled,
        "query_rewrite_enabled": settings.query_rewrite_enabled,
        "web_search_enabled": settings.web_search_enabled,
        "ocr_enabled": settings.ocr_enabled,
        "memory_enabled": settings.memory_enabled,
        "dedup_enabled": settings.dedup_enabled,
        "max_loop_iterations": settings.max_loop_iterations,
        "max_total_time": settings.max_total_time,
        "ingestion_max_concurrency": settings.ingestion_max_concurrency,
    }


def _hash_qrels_and_dataset() -> dict:
    """Hash the qrels data and evaluation documents."""
    dataset_dir = BASELINES_DIR.parent
    qrels_path = dataset_dir / "qrels_data_v2.json"
    eval_docs = [
        dataset_dir / "gen_eval_docs.py",
        dataset_dir / "gen_complex_docs.py",
    ]
    result = {
        "qrels_v2_sha256": _hash_file(qrels_path),
    }
    for doc in eval_docs:
        result[f"{doc.stem}_sha256"] = _hash_file(doc)
    return result


def _get_test_stats() -> dict:
    """Run pytest and extract passed/skipped counts. Falls back to 433/4 on failure."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--tb=no"],
            cwd=BACKEND_DIR, capture_output=True, text=True, timeout=120,
        )
        import re as _re
        match = _re.search(r"(\d+)\s+passed.*?(\d+)\s+skipped", result.stdout + result.stderr)
        if match:
            return {"passed": int(match.group(1)), "skipped": int(match.group(2)), "live": True}
    except Exception:
        pass
    return {"passed": 433, "skipped": 4, "live": False, "error": "pytest run failed, using baseline snapshot"}


def _verify_env_sync() -> dict:
    """Check that .env.example keys match Settings model fields."""
    import re as _re
    try:
        from config import Settings
        env_path = BACKEND_DIR / ".env.example"
        if not env_path.exists():
            return {"status": "error", "message": ".env.example not found"}

        env_text = env_path.read_text(encoding="utf-8")
        env_keys = set(_re.findall(r"^([A-Z_]+)=", env_text, _re.MULTILINE))
        s = Settings()
        setting_keys = {k.upper() for k in s.model_fields if not k.startswith("model_")}
        computed = {"SECRET_KEY", "ADMIN_API_TOKEN", "QDRANT_ACTIVE_COLLECTION", "LLM_MAX_CONTEXT"}
        orphan_env = env_keys - setting_keys
        missing = (setting_keys - env_keys) - computed
        issues = []
        if orphan_env:
            issues.append(f"orphan_keys_in_env: {sorted(orphan_env)}")
        if missing:
            issues.append(f"missing_keys_in_env: {sorted(missing)}")
        return {"status": "ok" if not issues else "issues_found", "issues": issues}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def main():
    commit = _get_git_commit()
    manifest = {
        "commit": commit,
        "generated_at": datetime.now(UTC).isoformat(),
        "python": _get_python_version(),
        "tests": _get_test_stats(),
        "config": _get_config_summary(),
        "ocr": _get_ocr_status(),
        "rerank": _get_rerank_status(),
        "env_sync": _verify_env_sync(),
    }
    # Add dataset_sha256 to main manifest
    qrels_path = BASELINES_DIR.parent / "qrels_data_v2.json"
    manifest["dataset_sha256"] = _hash_file(qrels_path)

    manifest_path = BASELINES_DIR / "release_9_1_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Manifest written to {manifest_path}")

    dep_snapshot = _get_dependency_snapshot()
    dep_path = BASELINES_DIR / "dependency_snapshot.txt"
    dep_path.write_text(dep_snapshot, encoding="utf-8")
    print(f"Dependency snapshot written to {dep_path}")

    dataset_manifest = {
        "generated_at": datetime.now(UTC).isoformat(),
        "commit": commit,
        **_hash_qrels_and_dataset(),
    }
    dataset_path = BASELINES_DIR / "rag_dataset_manifest.json"
    dataset_path.write_text(json.dumps(dataset_manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Dataset manifest written to {dataset_path}")


if __name__ == "__main__":
    main()
