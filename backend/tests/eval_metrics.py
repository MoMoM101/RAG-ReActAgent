"""Corrected IR evaluation metrics using stable qrels identifiers.

All ratio metrics are guaranteed to be in [0, 1]. Uses document_key
and section_key for stable relevance matching instead of text Jaccard.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass
class RetrievedItem:
    """A single retrieved result with stable identifiers."""
    document_key: str
    section_key: str
    score: float
    chunk_id: str


@dataclass
class QrelItem:
    """A single relevance judgment with grade."""
    document_key: str
    section_key: str
    grade: int  # 1-3


def _match_key(doc_key: str, section_key: str, qrel: QrelItem) -> bool:
    """Check if a retrieved item matches a qrel item by stable keys."""
    if doc_key != qrel.document_key:
        return False
    if not qrel.section_key or not section_key:
        return doc_key == qrel.document_key
    return section_key == qrel.section_key


def _is_relevant(doc_key: str, section_key: str, qrels: list[QrelItem]) -> bool:
    """Check if a retrieved item is relevant to any qrel entry."""
    return any(_match_key(doc_key, section_key, q) for q in qrels)


def _unique_relevant_count(
    retrieved: list[RetrievedItem], qrels: list[QrelItem]
) -> int:
    """Count unique qrel items matched by the retrieved list.

    Each qrel item is counted at most once, preventing double-counting
    from overlapping chunks that hit the same section.
    """
    matched = set()
    for item in retrieved:
        for q in qrels:
            if _match_key(item.document_key, item.section_key, q):
                matched.add((q.document_key, q.section_key))
                break
    return len(matched)


def precision_at_k(
    retrieved: list[RetrievedItem], qrels: list[QrelItem], k: int
) -> float:
    """Precision@k: fraction of top-k results that are relevant."""
    if k <= 0 or not retrieved:
        return 0.0
    top_k = retrieved[:k]
    relevant = sum(1 for r in top_k if _is_relevant(r.document_key, r.section_key, qrels))
    return relevant / k


def recall_at_k(
    retrieved: list[RetrievedItem], qrels: list[QrelItem], k: int
) -> float:
    """Recall@k: unique relevant items found / total relevant items."""
    if k <= 0 or not retrieved or not qrels:
        return 0.0
    total_relevant = len({(q.document_key, q.section_key) for q in qrels})
    if total_relevant == 0:
        return 0.0
    found = _unique_relevant_count(retrieved[:k], qrels)
    return min(found / total_relevant, 1.0)


def mrr(retrieved: list[RetrievedItem], qrels: list[QrelItem]) -> float:
    """Mean Reciprocal Rank: 1 / rank of first relevant item."""
    for rank, item in enumerate(retrieved, 1):
        if _is_relevant(item.document_key, item.section_key, qrels):
            return 1.0 / rank
    return 0.0


def ndcg_at_k(
    retrieved: list[RetrievedItem], qrels: list[QrelItem], k: int
) -> float:
    """NDCG@k using qrels grades for ideal DCG.

    DCG = sum(rel_i / log2(i+2)) for top-k retrieved items.
    IDCG = best possible DCG from qrels grades sorted descending.
    """
    if k <= 0 or not retrieved or not qrels:
        return 0.0

    # Build grade lookup (empty section_key = document-level match, any section_key)
    grade_map: dict[tuple[str, str], int] = {}
    doc_grades: dict[str, int] = {}  # explicit document-level fallback grades
    for q in qrels:
        if q.section_key:
            key = (q.document_key, q.section_key)
            if q.grade > grade_map.get(key, 0):
                grade_map[key] = q.grade
        elif q.grade > doc_grades.get(q.document_key, 0):
            doc_grades[q.document_key] = q.grade

    # DCG from retrieved
    dcg = 0.0
    for i, item in enumerate(retrieved[:k]):
        rel = 0
        key = (item.document_key, item.section_key)
        if key in grade_map:
            rel = grade_map[key]
        elif item.document_key in doc_grades:
            rel = doc_grades[item.document_key]  # document-level fallback
        dcg += rel / math.log2(i + 2)

    # IDCG includes every explicitly judged section/document exactly once.
    ideal_grades = sorted(
        [*grade_map.values(), *doc_grades.values()], reverse=True
    )[:k]
    idcg = sum(g / math.log2(i + 2) for i, g in enumerate(ideal_grades))

    if idcg == 0:
        return 0.0
    return min(dcg / idcg, 1.0)


def hit_at_k(
    retrieved: list[RetrievedItem], qrels: list[QrelItem], k: int
) -> int:
    """Hit@k: 1 if at least one relevant item in top-k, else 0."""
    top_k = retrieved[:k]
    return 1 if any(_is_relevant(r.document_key, r.section_key, qrels) for r in top_k) else 0


def compute_metrics_v2(
    retrieved: list[RetrievedItem],
    qrels: list[QrelItem],
    k_values: tuple[int, ...] = (3, 5, 10),
) -> dict[str, Any]:
    """Compute all IR metrics with stable-key matching.

    Returns a dict with precision, recall, mrr, ndcg, hit for each k.
    All ratio values are in [0, 1].
    """
    result: dict[str, Any] = {
        "precision": {},
        "recall": {},
        "ndcg": {},
        "hit": {},
        "mrr": 0.0,
    }

    if not qrels:
        for k in k_values:
            result["precision"][k] = 0.0
            result["recall"][k] = 0.0
            result["ndcg"][k] = 0.0
            result["hit"][k] = 0
        return result

    if not retrieved:
        for k in k_values:
            result["precision"][k] = 0.0
            result["recall"][k] = 0.0
            result["ndcg"][k] = 0.0
            result["hit"][k] = 0
        return result

    result["mrr"] = mrr(retrieved, qrels)

    for k in k_values:
        result["precision"][k] = precision_at_k(retrieved, qrels, k)
        result["recall"][k] = recall_at_k(retrieved, qrels, k)
        result["ndcg"][k] = ndcg_at_k(retrieved, qrels, k)
        result["hit"][k] = hit_at_k(retrieved, qrels, k)

    return result


def aggregate_metrics(
    per_query_metrics: list[dict[str, Any]],
    k_values: tuple[int, ...] = (3, 5, 10),
) -> dict[str, Any]:
    """Average metrics across multiple queries."""
    n = max(len(per_query_metrics), 1)
    agg: dict[str, Any] = {
        "precision": {}, "recall": {}, "ndcg": {}, "hit": {}, "mrr": 0.0,
    }
    for k in k_values:
        agg["precision"][k] = sum(m["precision"][k] for m in per_query_metrics) / n
        agg["recall"][k] = sum(m["recall"][k] for m in per_query_metrics) / n
        agg["ndcg"][k] = sum(m["ndcg"][k] for m in per_query_metrics) / n
        agg["hit"][k] = sum(m["hit"][k] for m in per_query_metrics) / n
    agg["mrr"] = sum(m["mrr"] for m in per_query_metrics) / n
    return agg


def validate_metrics_range(metrics: dict[str, Any]) -> list[str]:
    """Return list of violations if any metric is outside [0, 1]."""
    violations = []
    for key in ("precision", "recall", "ndcg", "hit"):
        for k, v in metrics.get(key, {}).items():
            if not (0.0 <= v <= 1.0):
                violations.append(f"{key}@{k}={v} out of [0,1]")
    mrr_val = metrics.get("mrr", 0)
    if not (0.0 <= mrr_val <= 1.0):
        violations.append(f"mrr={mrr_val} out of [0,1]")
    return violations
