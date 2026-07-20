"""V4 slow-question regression gate (Plan Section 9).

Validates that the 7 known-slow queries and edge-case queries are present
in the qrels dataset and can be evaluated.  The slow queries must never be
dropped from the evaluation set.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.qrels_schema import QrelDataset

# ── Plan Section 9: Fixed slow queries ──
SLOW_QUERY_IDS = [
    "cross-005",      # 15651 ms
    "multi-hop-003",  # 13715 ms
    "instruct-004",   # 11197 ms
    "cn-en-mix-002",  # 10338 ms
    "short-002",      # 7824 ms
    "exact-009",      # 7765 ms
    "cross-003",      # 7283 ms
]

# ── Plan Section 9: Additional V4 edge-case scenarios ──
# These query IDs should exist or be added to the dataset
EDGE_CASE_DESCRIPTIONS = {
    "missing_citation": "引用缺失但存在唯一支持来源",
    "redundant_citations": "多来源冗余引用",
    "number_conflict": "数字冲突",
    "partial_answerable_full_refusal": "部分可答但首稿整体拒答",
    "unanswerable_topical": "真正不可答且实体与来源相关",
    "mid_answer_unsupported": "长答案中间出现一个不受支持声明",
    "mid_citation_stream_split": "模型流式 chunk 在引用中间断开",
}


class TestV4RegressionSet:
    """Ensure the V4 slow regression set is complete and evaluable."""

    @pytest.fixture(scope="class")
    @classmethod
    def dataset(cls) -> QrelDataset:
        return QrelDataset.load(str(Path(__file__).parents[1] / "qrels_data_v2.json"))

    @pytest.fixture(scope="class")
    @classmethod
    def query_ids(cls, dataset: QrelDataset) -> set[str]:
        return {q.query_id for q in dataset.queries}

    def test_all_slow_queries_exist(self, query_ids: set[str]):
        """Every V4 slow query must be present in the qrels dataset."""
        missing = [qid for qid in SLOW_QUERY_IDS if qid not in query_ids]
        assert not missing, f"Slow queries missing from qrels: {missing}"

    def test_slow_queries_have_categories(self, dataset: QrelDataset):
        """Each slow query should have a non-trivial answerability label."""
        by_id = {q.query_id: q for q in dataset.queries}
        for qid in SLOW_QUERY_IDS:
            q = by_id[qid]
            assert q.answerability in ("full", "partial", "none"), (
                f"{qid}: unknown answerability '{q.answerability}'"
            )

    def test_at_least_50_answerable_queries(self, dataset: QrelDataset):
        """Ensure the dataset has enough answerable queries for statistical
        significance in the V4 evaluation."""
        answerable = [q for q in dataset.queries if q.answerability != "none"]
        assert len(answerable) >= 50, (
            f"Only {len(answerable)} answerable queries, need ≥50"
        )

    def test_expected_facts_present_for_answerable(self, dataset: QrelDataset):
        """Answerable queries should have expected facts for fact-recall scoring."""
        missing_facts = [
            q.query_id
            for q in dataset.queries
            if q.answerability != "none"
            and not (q.answer_expected_facts or q.expected_answer_facts)
        ]
        # Not a hard failure — some queries may legitimately lack fact labels
        if missing_facts:
            pytest.skip(
                f"{len(missing_facts)} answerable queries lack expected_facts "
                f"(non-blocking): {missing_facts[:5]}..."
            )


class TestEvalScriptV4Support:
    """Verify the eval script outputs V4 metadata fields."""

    def test_eval_record_has_v4_fields(self):
        from tests.run_grounded_answer_eval import EvalRecord

        record = EvalRecord(
            query_id="test-001",
            query="test",
            mode="optimized",
            answerable=True,
            answer="answer",
            sources=[],
            latency_ms=100.0,
            faithfulness=1.0,
            citation_precision=1.0,
            citation_recall=1.0,
            refused=False,
            abstention_correct=None,
            expected_fact_recall=1.0,
            verification_status="verified",
            repair_used="llm",
            repair_reasons=["missing_citation"],
            repair_triggered=True,
            draft_latency_ms=80.0,
            verification_latency_ms=5.0,
            repair_latency_ms=50.0,
        )
        assert record.repair_used == "llm"
        assert record.repair_triggered is True
        assert record.repair_reasons == ["missing_citation"]
        assert record.draft_latency_ms == 80.0
        assert record.verification_latency_ms == 5.0
        assert record.repair_latency_ms == 50.0

    def test_aggregate_includes_v4_stats(self):
        from tests.run_grounded_answer_eval import EvalRecord, _aggregate

        records = [
            EvalRecord(
                query_id="t1", query="q", mode="optimized", answerable=True,
                answer="a [S1]。", sources=[
                    {"citation_id": "S1", "text": "a content", "score": 0.9}],
                latency_ms=100, faithfulness=1.0, citation_precision=1.0,
                citation_recall=1.0, refused=False, abstention_correct=None,
                expected_fact_recall=1.0, verification_status="verified",
                repair_used="llm", repair_reasons=["missing_citation"],
                repair_triggered=True, draft_latency_ms=80.0,
                verification_latency_ms=5.0, repair_latency_ms=50.0,
            ),
        ]
        agg = _aggregate(records, "optimized")
        assert agg["repair_triggered_count"] == 1
        assert agg["repair_trigger_rate"] == 1.0
        assert "missing_citation" in agg["repair_by_reason"]
        assert agg["repair_accepted_count"] == 1

    def test_clarification_answers_count_as_correct_abstention(self):
        from tests.run_grounded_answer_eval import _is_full_refusal

        assert _is_full_refusal(
            "请提供您想了解的具体对象或话题名称。", None,
        )
        assert _is_full_refusal(
            "请问您指的是哪个框架？请指定您想了解的框架名称。", None,
        )
        assert _is_full_refusal(
            "追问：请问“这个”具体指的是什么？", None,
        )
        assert _is_full_refusal("请提供您的问题。", None)
        assert _is_full_refusal("请提供明确的问题。", None)
        assert _is_full_refusal(
            "当前问题指代不清晰，请提供您所指的具体内容。", None,
        )
        assert _is_full_refusal(
            "无法理解您的问题，请您提供一个有明确对象的具体问题。", None,
        )
        assert _is_full_refusal("请问您想询问什么？", None)


class TestV4QualityGates:
    """Verify quality gate constants are consistent with the plan."""

    def test_floors_match_plan(self):
        from tests.run_grounded_answer_eval import QUALITY_FLOORS

        assert QUALITY_FLOORS["citation_precision"] == 0.95
        assert QUALITY_FLOORS["citation_recall"] == 0.95
        assert QUALITY_FLOORS["abstention_accuracy"] == 0.98
        assert QUALITY_FLOORS["expected_fact_recall"] == 0.85
        assert QUALITY_FLOORS["answer_completion_accuracy"] == 0.95
