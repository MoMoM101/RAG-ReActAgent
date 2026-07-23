"""Unit tests for corrected IR evaluation metrics.

Verifies: [0,1] bounds, duplicate chunk handling, cross-document
multi-answer, empty inputs, edge cases.
"""

import pytest

from tests.eval_metrics import (
    QrelItem,
    RetrievedItem,
    aggregate_metrics,
    compute_metrics_v2,
    hit_at_k,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    validate_metrics_range,
)

# ── Fixtures ──────────────────────────────────────────────────

@pytest.fixture
def basic_qrels() -> list[QrelItem]:
    return [
        QrelItem(document_key="ml-guide", section_key="deep-learning", grade=3),
        QrelItem(document_key="ml-guide", section_key="preprocessing", grade=2),
        QrelItem(document_key="climate-report", section_key="temperature", grade=3),
    ]


@pytest.fixture
def perfect_retrieved() -> list[RetrievedItem]:
    return [
        RetrievedItem("ml-guide", "deep-learning", 0.95, "c1"),
        RetrievedItem("climate-report", "temperature", 0.90, "c2"),
        RetrievedItem("ml-guide", "preprocessing", 0.85, "c3"),
    ]


@pytest.fixture
def half_retrieved() -> list[RetrievedItem]:
    return [
        RetrievedItem("ml-guide", "deep-learning", 0.95, "c1"),
        RetrievedItem("unknown", "unknown", 0.90, "c2"),
        RetrievedItem("wrong", "section", 0.85, "c3"),
    ]


# ── Precision ─────────────────────────────────────────────────

class TestPrecision:
    def test_perfect(self, perfect_retrieved, basic_qrels):
        assert precision_at_k(perfect_retrieved, basic_qrels, 3) == 1.0

    def test_half(self, half_retrieved, basic_qrels):
        assert precision_at_k(half_retrieved, basic_qrels, 3) == pytest.approx(1 / 3)

    def test_k_larger_than_list(self, perfect_retrieved, basic_qrels):
        # P@100 with only 3 items = 3/100 (k denominator, not list length)
        assert precision_at_k(perfect_retrieved, basic_qrels, 100) == pytest.approx(3 / 100)

    def test_empty_retrieved(self, basic_qrels):
        assert precision_at_k([], basic_qrels, 5) == 0.0

    def test_empty_qrels(self, perfect_retrieved):
        assert precision_at_k(perfect_retrieved, [], 5) == 0.0

    def test_zero_k(self, perfect_retrieved, basic_qrels):
        assert precision_at_k(perfect_retrieved, basic_qrels, 0) == 0.0


# ── Recall ────────────────────────────────────────────────────

class TestRecall:
    def test_perfect(self, perfect_retrieved, basic_qrels):
        assert recall_at_k(perfect_retrieved, basic_qrels, 3) == 1.0

    def test_half(self, half_retrieved, basic_qrels):
        assert recall_at_k(half_retrieved, basic_qrels, 3) == pytest.approx(1 / 3)

    def test_empty_retrieved(self, basic_qrels):
        assert recall_at_k([], basic_qrels, 5) == 0.0

    def test_empty_qrels(self, perfect_retrieved):
        assert recall_at_k(perfect_retrieved, [], 5) == 0.0

    def test_duplicate_chunks_same_section(self, basic_qrels):
        """Multiple chunks hitting the same relevant section count as 1."""
        dup_retrieved = [
            RetrievedItem("ml-guide", "deep-learning", 0.95, "c1"),
            RetrievedItem("ml-guide", "deep-learning", 0.90, "c2"),
            RetrievedItem("ml-guide", "deep-learning", 0.85, "c3"),
            RetrievedItem("ml-guide", "deep-learning", 0.80, "c4"),
        ]
        assert recall_at_k(dup_retrieved, basic_qrels, 10) == pytest.approx(1 / 3)

    def test_never_exceeds_one(self, basic_qrels):
        """Recall must not exceed 1.0 even with many overlapping chunks."""
        many = [
            RetrievedItem("ml-guide", "deep-learning", 0.95, f"c{i}")
            for i in range(100)
        ]
        r = recall_at_k(many, basic_qrels, 100)
        assert r <= 1.0


# ── MRR ───────────────────────────────────────────────────────

class TestMRR:
    def test_first_relevant(self, basic_qrels):
        retrieved = [
            RetrievedItem("other", "x", 0.5, "c0"),
            RetrievedItem("ml-guide", "deep-learning", 0.9, "c1"),
            RetrievedItem("climate-report", "temperature", 0.8, "c2"),
        ]
        assert mrr(retrieved, basic_qrels) == pytest.approx(1 / 2)

    def test_no_relevant(self, basic_qrels):
        retrieved = [
            RetrievedItem("other", "x", 0.5, "c0"),
            RetrievedItem("other", "y", 0.4, "c1"),
        ]
        assert mrr(retrieved, basic_qrels) == 0.0

    def test_empty(self, basic_qrels):
        assert mrr([], basic_qrels) == 0.0

    def test_first_position(self, basic_qrels):
        retrieved = [RetrievedItem("ml-guide", "deep-learning", 1.0, "c1")]
        assert mrr(retrieved, basic_qrels) == 1.0


# ── NDCG ──────────────────────────────────────────────────────

class TestNDCG:
    def test_perfect_order(self, basic_qrels):
        retrieved = [
            RetrievedItem("ml-guide", "deep-learning", 0.95, "c1"),    # grade 3
            RetrievedItem("climate-report", "temperature", 0.90, "c2"), # grade 3
            RetrievedItem("ml-guide", "preprocessing", 0.85, "c3"),     # grade 2
        ]
        ndcg = ndcg_at_k(retrieved, basic_qrels, 3)
        assert ndcg <= 1.0
        assert ndcg > 0.8  # nearly perfect ordering

    def test_wrong_order(self, basic_qrels):
        retrieved = [
            RetrievedItem("ml-guide", "preprocessing", 0.95, "c1"),     # grade 2
            RetrievedItem("ml-guide", "deep-learning", 0.90, "c2"),     # grade 3
            RetrievedItem("climate-report", "temperature", 0.85, "c3"), # grade 3
        ]
        ndcg = ndcg_at_k(retrieved, basic_qrels, 3)
        assert ndcg < 1.0

    def test_empty(self, basic_qrels):
        assert ndcg_at_k([], basic_qrels, 5) == 0.0

    def test_no_qrels(self, perfect_retrieved):
        assert ndcg_at_k(perfect_retrieved, [], 5) == 0.0


# ── Hit ────────────────────────────────────────────────────────

class TestHit:
    def test_hit(self, basic_qrels):
        retrieved = [
            RetrievedItem("other", "x", 0.5, "c0"),
            RetrievedItem("ml-guide", "deep-learning", 0.9, "c1"),
        ]
        assert hit_at_k(retrieved, basic_qrels, 2) == 1

    def test_miss(self, basic_qrels):
        retrieved = [RetrievedItem("other", "x", 0.5, "c0")]
        assert hit_at_k(retrieved, basic_qrels, 2) == 0

    def test_empty(self, basic_qrels):
        assert hit_at_k([], basic_qrels, 5) == 0


# ── compute_metrics_v2 ────────────────────────────────────────

class TestComputeMetricsV2:
    def test_all_keys_present(self, perfect_retrieved, basic_qrels):
        result = compute_metrics_v2(perfect_retrieved, basic_qrels)
        for key in ("precision", "recall", "ndcg", "hit", "mrr"):
            assert key in result
        for k in (3, 5, 10):
            assert k in result["precision"]

    def test_no_violations(self, perfect_retrieved, basic_qrels):
        result = compute_metrics_v2(perfect_retrieved, basic_qrels)
        violations = validate_metrics_range(result)
        assert not violations, f"Violations: {violations}"

    def test_empty_preserves_structure(self, basic_qrels):
        result = compute_metrics_v2([], basic_qrels)
        assert result["mrr"] == 0.0
        assert result["precision"][5] == 0.0

    def test_zero_qrels_preserves_structure(self, perfect_retrieved):
        result = compute_metrics_v2(perfect_retrieved, [])
        assert result["mrr"] == 0.0
        assert result["recall"][5] == 0.0


# ── Aggregate ─────────────────────────────────────────────────

class TestAggregate:
    def test_single_query(self, perfect_retrieved, basic_qrels):
        m1 = compute_metrics_v2(perfect_retrieved, basic_qrels)
        agg = aggregate_metrics([m1])
        assert agg["mrr"] == m1["mrr"]

    def test_two_queries(self, perfect_retrieved, basic_qrels):
        m1 = compute_metrics_v2(perfect_retrieved, basic_qrels)
        m2 = compute_metrics_v2([], basic_qrels)
        agg = aggregate_metrics([m1, m2])
        assert agg["mrr"] == m1["mrr"] / 2

    def test_all_in_range(self, perfect_retrieved, basic_qrels):
        m1 = compute_metrics_v2(perfect_retrieved, basic_qrels)
        m2 = compute_metrics_v2([], basic_qrels)
        agg = aggregate_metrics([m1, m2])
        violations = validate_metrics_range(agg)
        assert not violations, f"Violations: {violations}"


# ── Cross-document multi-answer ───────────────────────────────

class TestCrossDocument:
    def test_recall_multi_doc(self):
        qrels = [
            QrelItem("doc-a", "section-1", 3),
            QrelItem("doc-b", "section-2", 3),
        ]
        retrieved = [
            RetrievedItem("doc-a", "section-1", 0.9, "c1"),
            RetrievedItem("other", "other", 0.8, "c2"),
        ]
        assert recall_at_k(retrieved, qrels, 5) == 0.5

    def test_precision_multi_doc(self):
        qrels = [
            QrelItem("doc-a", "section-1", 3),
            QrelItem("doc-b", "section-2", 3),
        ]
        retrieved = [
            RetrievedItem("doc-a", "section-1", 0.9, "c1"),
            RetrievedItem("doc-b", "section-2", 0.8, "c2"),
            RetrievedItem("irrelevant", "x", 0.7, "c3"),
        ]
        assert precision_at_k(retrieved, qrels, 3) == pytest.approx(2 / 3)


# ── Section-less matching ─────────────────────────────────────

class TestSectionLessMatch:
    def test_match_without_section(self):
        qrels = [QrelItem("doc-a", "", 3)]
        retrieved = [RetrievedItem("doc-a", "any-section", 0.9, "c1")]
        assert precision_at_k(retrieved, qrels, 1) == 1.0

    def test_no_match_wrong_doc(self):
        qrels = [QrelItem("doc-a", "", 3)]
        retrieved = [RetrievedItem("doc-b", "any-section", 0.9, "c1")]
        assert recall_at_k(retrieved, qrels, 1) == 0.0
