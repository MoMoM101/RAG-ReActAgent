import json
from pathlib import Path

import pytest

from rag.retriever import RetrievalResult
from rag.splitter import split_text
from tests.complex_eval_runner import QRELS_PATH, QUERY_CASES, validate_complex_qrels
from tests.eval_metrics import (
    QrelItem,
    RetrievedItem,
    aggregate_metrics,
    compute_metrics_v2,
)
from tests.evaluate_rag import (
    AblationStrategy,
    QueryCase,
    _reviewed_qrels_by_query,
    save_results,
)
from tests.qrels_schema import QrelDataset, QrelQuery
from textdb.bm25_search import BM25Search


def test_complex_qrels_cover_every_query_and_reference_real_sections():
    dataset = validate_complex_qrels()

    assert len(dataset.queries) == len(QUERY_CASES) == 29
    assert {item.query for item in dataset.queries} == {
        case.query for case in QUERY_CASES
    }


def test_complex_qrels_use_stable_keys_instead_of_legacy_chunk_indices():
    dataset = validate_complex_qrels()

    assert all(item.relevant for item in dataset.queries)
    assert all(
        relevant.document_key and relevant.section_key
        for item in dataset.queries
        for relevant in item.relevant
    )


def test_strict_qrels_coverage_rejects_missing_queries():
    dataset = QrelDataset(
        name="incomplete",
        queries=[QrelQuery(query_id="q1", query="covered")],
    )
    cases = [
        QueryCase(query="covered", relevant_chunk_indices=[0]),
        QueryCase(query="missing", relevant_chunk_indices=[0]),
    ]

    with pytest.raises(ValueError, match="Strict qrels coverage failed"):
        _reviewed_qrels_by_query(
            dataset,
            cases,
            Path("incomplete.json"),
            allow_fallback=False,
        )


def test_complex_qrels_path_is_dedicated_dataset():
    assert QRELS_PATH.name == "qrels_complex_v2.json"


def test_saved_results_include_ranked_diagnostics(tmp_path):
    qrel = QrelItem("api-paygate", "post--orders--id--refund", 3)
    retrieved = RetrievalResult(
        chunk_id="chunk-1",
        document_id="doc-1",
        text="ERR_40005 status not PAID",
        score=0.95,
        source="keyword",
        document_key="api-paygate",
        section_key="post--orders--id--refund",
    )
    metrics = compute_metrics_v2(
        [],
        [qrel],
    )
    output = tmp_path / "complex-result.json"
    results = {
        "query_cases": [QueryCase("ERR_40005", [1])],
        "qrels_per_query": [[qrel]],
        "per_query": [metrics],
        "strategy_results": {
            AblationStrategy.KEYWORD_ONLY: {
                "metrics": [metrics],
                "latencies": [1],
                "results": [[retrieved]],
            }
        },
    }

    save_results(results, str(output))

    payload = json.loads(output.read_text(encoding="utf-8"))
    diagnostic = payload["diagnostics"][0]
    ranked = diagnostic["strategies"]["keyword-only"][0]
    assert diagnostic["expected"] == [
        "api-paygate#post--orders--id--refund"
    ]
    assert ranked["matched"] is True
    assert ranked["rank"] == 1


@pytest.mark.asyncio
async def test_complex_keyword_retrieval_offline_baseline(setup_db):
    dataset = validate_complex_qrels()
    bm25 = BM25Search()

    from tests.complex_eval_runner import load_docs

    for doc_index, document in enumerate(load_docs()):
        document_key = dataset.document_keys[document.filename]
        chunks = split_text(document.content, chunk_size=200, chunk_overlap=40)
        await bm25.insert_batch([
            (
                f"complex-{doc_index}-{chunk.chunk_index}",
                f"complex-doc-{doc_index}",
                document_key,
                chunk.section_key,
                chunk.chunk_index,
                chunk.text,
            )
            for chunk in chunks
        ])

    per_query = []
    misses_at_5: list[str] = []
    for query in dataset.queries:
        results = await bm25.search(query.query, top_k=10)
        retrieved = [
            RetrievedItem(
                result.document_key,
                result.section_key,
                result.score,
                result.chunk_id,
            )
            for result in results
        ]
        qrels = [
            QrelItem(item.document_key, item.section_key, item.grade)
            for item in query.relevant
        ]
        metrics = compute_metrics_v2(retrieved, qrels, (3, 5, 10))
        per_query.append(metrics)
        if not metrics["hit"][5]:
            misses_at_5.append(query.query_id)

    aggregate = aggregate_metrics(per_query, (3, 5, 10))
    diagnostic = {"aggregate": aggregate, "misses_at_5": misses_at_5}
    assert aggregate["hit"][5] >= 0.75, diagnostic
    assert aggregate["mrr"] >= 0.65, diagnostic
    assert aggregate["ndcg"][5] >= 0.65, diagnostic
