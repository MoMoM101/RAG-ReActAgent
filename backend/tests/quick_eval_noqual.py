"""Quick test: complex eval with chunk_quality_filter_enabled=False."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings

settings.chunk_quality_filter_enabled = True  # Test new demote-only logic
import evaluate_rag
from complex_eval_runner import QUERY_CASES, load_docs

evaluate_rag.TEST_DOCS = load_docs()
evaluate_rag.QUERY_CASES = QUERY_CASES

async def main():
    try:
        await evaluate_rag.run_evaluation()
    finally:
        await evaluate_rag.cleanup()

asyncio.run(main())
