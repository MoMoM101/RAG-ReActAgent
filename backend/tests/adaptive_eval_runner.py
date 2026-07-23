"""Run business eval with adaptive RRF enabled."""
import asyncio
import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from config import settings

settings.rrf_adaptive_enabled = True
settings.rrf_k = 30
settings.rerank_top_n = 24
settings.chunk_quality_filter_enabled = True
settings.dedup_enabled = True

DATA_DIR = Path(__file__).resolve().parent / "eval_data"
from evaluate_rag import TestDoc


def load_docs():
    docs = []
    for fname in ["paygate_api_v3.txt", "mcu_catalog_2024q3.txt",
                  "drug_reference_cardio.txt", "data_compliance_checklist.txt"]:
        content = (DATA_DIR / fname).read_text(encoding="utf-8")
        docs.append(TestDoc(filename=fname, content=content))
    return docs

import evaluate_rag
from business_eval_runner import _make_queries

evaluate_rag.TEST_DOCS = load_docs()
evaluate_rag.QUERY_CASES = _make_queries()

async def main():
    try:
        await evaluate_rag.run_evaluation()
    finally:
        await evaluate_rag.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
