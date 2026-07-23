"""Answer-level RAG evaluation: faithfulness, citations, and abstention.

Goes beyond IR metrics to evaluate the final answer quality.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AnswerEvalResult:
    """Per-query answer evaluation results."""
    query_id: str
    answer_correctness: float = 0.0     # [0,1] fraction of expected facts covered
    faithfulness: float = 0.0            # [0,1] answer facts supported by context
    citation_precision: float = 0.0      # [0,1] cited sources support claims
    citation_recall: float = 0.0         # [0,1] claims needing citation have one
    abstention_correct: bool | None = None  # True=correctly refused, False=hallucinated, None=N/A
    task_success: bool = False           # overall success
    reranker_status: str = "unavailable"


@dataclass
class AnswerEvalAggregate:
    """Aggregated answer evaluation metrics."""
    num_queries: int = 0
    avg_correctness: float = 0.0
    avg_faithfulness: float = 0.0
    avg_citation_precision: float = 0.0
    avg_citation_recall: float = 0.0
    abstention_accuracy: float | None = None
    task_success_rate: float = 0.0
    reranker_status: str = "unavailable"


def evaluate_answer_facts(answer: str, expected_facts: list[str]) -> float:
    """Deterministic fact coverage: fraction of expected facts found in answer.

    Uses case-insensitive substring matching.
    """
    if not expected_facts:
        return 1.0
    answer_lower = answer.lower()
    found = sum(1 for f in expected_facts if f.lower() in answer_lower)
    return found / len(expected_facts)


def check_citations(answer: str, must_cite: list[str]) -> dict[str, float]:
    """Check citation presence in the answer.

    Returns citation_precision and citation_recall. Uses simple
    heuristic: looks for document_key#section_key patterns or
    document_key mentions in the answer text.
    """
    if not must_cite:
        return {"citation_precision": 1.0, "citation_recall": 1.0}

    answer_lower = answer.lower()
    cited_count = 0
    for cite_ref in must_cite:
        doc_key = cite_ref.split("#")[0].lower()
        section = cite_ref.split("#")[1].lower() if "#" in cite_ref else ""
        if doc_key in answer_lower or section in answer_lower:
            cited_count += 1

    citation_recall = cited_count / len(must_cite) if must_cite else 1.0
    # Citation precision: assume all citations found are valid for now
    citation_precision = 1.0 if cited_count > 0 else 0.0

    return {
        "citation_precision": min(citation_precision, 1.0),
        "citation_recall": min(citation_recall, 1.0),
    }


def check_abstention(
    answer: str, expected_facts: list[str], is_unanswerable: bool
) -> bool | None:
    """Check if abstention is correct.

    Returns:
        True  = correctly refused to answer / said "don't know"
        False = should have refused but hallucinated / should have answered but refused
        None  = not an unanswerable question
    """
    if not is_unanswerable:
        return None

    refusal_markers = [
        "不知道", "没有相关信息", "无法回答", "未找到",
        "i don't know", "no information", "cannot answer",
        "not found", "unable to", "不在", "没有提到",
    ]
    answer_lower = answer.lower()
    is_refusing = any(m in answer_lower for m in refusal_markers)

    if is_unanswerable:
        return is_refusing

    return None


def evaluate_answer(
    query_id: str,
    answer: str,
    expected_facts: list[str],
    must_cite: list[str],
    is_unanswerable: bool = False,
    reranker_available: bool = False,
) -> AnswerEvalResult:
    """Full per-query answer evaluation."""
    result = AnswerEvalResult(query_id=query_id)

    result.answer_correctness = evaluate_answer_facts(answer, expected_facts)

    citation = check_citations(answer, must_cite)
    result.citation_precision = citation["citation_precision"]
    result.citation_recall = citation["citation_recall"]

    abstention = check_abstention(answer, expected_facts, is_unanswerable)
    result.abstention_correct = abstention

    # Heuristic faithfulness: if answer has citations and facts match, assume faithful
    has_answer = len(answer.strip()) > 20
    if not has_answer:
        result.faithfulness = 1.0 if is_unanswerable and abstention else 0.0
    else:
        # Simple heuristic: faithfulness = min(correctness, citation_recall)
        result.faithfulness = min(result.answer_correctness, result.citation_recall)

    # Task success: faithfully answered with decent correctness
    result.task_success = (
        result.faithfulness >= 0.5 and result.answer_correctness >= 0.5
    )

    result.reranker_status = "available" if reranker_available else "unavailable"

    return result


def aggregate_answer_results(
    results: list[AnswerEvalResult],
) -> AnswerEvalAggregate:
    """Aggregate per-query answer evaluation results."""
    n = max(len(results), 1)
    agg = AnswerEvalAggregate(num_queries=n)

    agg.avg_correctness = sum(r.answer_correctness for r in results) / n
    agg.avg_faithfulness = sum(r.faithfulness for r in results) / n
    agg.avg_citation_precision = sum(r.citation_precision for r in results) / n
    agg.avg_citation_recall = sum(r.citation_recall for r in results) / n

    abstention_results = [r for r in results if r.abstention_correct is not None]
    if abstention_results:
        agg.abstention_accuracy = sum(
            1 for r in abstention_results if r.abstention_correct
        ) / len(abstention_results)

    agg.task_success_rate = sum(1 for r in results if r.task_success) / n

    reranker_statuses = {r.reranker_status for r in results}
    agg.reranker_status = (
        "available" if reranker_statuses == {"available"}
        else "unavailable" if reranker_statuses == {"unavailable"}
        else "mixed"
    )

    return agg
