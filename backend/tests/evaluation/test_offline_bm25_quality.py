"""Deterministic offline quality gate for the keyword retrieval path.

Unlike the live evaluation this suite never calls an embedding or LLM API. It
indexes the checked-in corpus with the production splitter and BM25 backend,
then scores it against the stable qrels v2 judgments.
"""

from pathlib import Path

import pytest

from rag.splitter import split_text
from tests.eval_metrics import QrelItem, RetrievedItem, aggregate_metrics, compute_metrics_v2
from tests.evaluate_rag import TEST_DOCS
from tests.qrels_schema import QrelDataset, document_key_from_filename
from textdb.bm25_search import BM25Search


@pytest.mark.asyncio
async def test_keyword_retrieval_meets_offline_quality_floor(setup_db):
    bm25 = BM25Search()

    for doc_index, doc in enumerate(TEST_DOCS):
        document_key = document_key_from_filename(doc.filename)
        chunks = split_text(doc.content, chunk_size=200, chunk_overlap=40)
        await bm25.insert_batch([
            (
                f"offline-{doc_index}-{chunk.chunk_index}",
                f"offline-doc-{doc_index}",
                document_key,
                chunk.section_key,
                chunk.chunk_index,
                chunk.text,
            )
            for chunk in chunks
        ])

    dataset = QrelDataset.load(str(Path(__file__).parents[1] / "qrels_data_v2.json"))
    per_query = []
    misses_at_5: list[str] = []
    for query in dataset.queries:
        # Empty-qrels cases measure refusal/abstention quality at the answer
        # layer; treating them as retrieval misses would depress every IR
        # metric regardless of ranking quality.
        if not query.relevant:
            continue
        results = await bm25.search(query.query, top_k=10)
        retrieved = [
            RetrievedItem(
                document_key=result.document_key,
                section_key=result.section_key,
                score=result.score,
                chunk_id=result.chunk_id,
            )
            for result in results
        ]
        qrels = [
            QrelItem(
                document_key=item.document_key,
                section_key=item.section_key,
                grade=item.grade,
            )
            for item in query.relevant
        ]
        metrics = compute_metrics_v2(retrieved, qrels, (3, 5, 10))
        per_query.append(metrics)
        if not metrics["hit"][5]:
            misses_at_5.append(query.query_id)

    aggregate = aggregate_metrics(per_query, (3, 5, 10))

    # These are deliberately conservative regression floors, not aspirational
    # benchmark targets. Tightening them requires a reviewed qrels update.
    diagnostic = {"aggregate": aggregate, "misses_at_5": misses_at_5}
    assert aggregate["hit"][5] >= 0.75, diagnostic
    assert aggregate["mrr"] >= 0.70, diagnostic
    assert aggregate["ndcg"][5] >= 0.70, diagnostic
