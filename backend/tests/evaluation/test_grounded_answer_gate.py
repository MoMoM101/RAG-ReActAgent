"""Release gate tests for grounded-answer online evaluation."""

import asyncio
from types import SimpleNamespace

import pytest

from tests.qrels_schema import QrelQuery
from tests.run_grounded_answer_eval import (
    ModelCallBudget,
    ModelCallBudgetExceededError,
    _fact_recall,
    _is_full_refusal,
    _is_safe_abstention,
    evaluate_quality_gate,
)


def test_model_call_budget_refuses_call_past_hard_limit():
    async def exercise() -> ModelCallBudget:
        budget = ModelCallBudget(2)
        await budget.reserve()
        await budget.reserve()
        with pytest.raises(ModelCallBudgetExceededError):
            await budget.reserve()
        return budget

    budget = asyncio.run(exercise())

    assert budget.used == 2


def test_model_call_budget_is_concurrency_safe():
    async def exercise() -> tuple[ModelCallBudget, list[object]]:
        budget = ModelCallBudget(3)
        results = await asyncio.gather(
            *(budget.reserve() for _ in range(8)),
            return_exceptions=True,
        )
        return budget, results

    budget, results = asyncio.run(exercise())

    assert budget.used == 3
    assert sum(result is None for result in results) == 3
    assert sum(isinstance(result, ModelCallBudgetExceededError) for result in results) == 5


def _aggregate(**optimized_overrides):
    control = {
        "queries_completed": 20,
        "errors": 0,
        "faithfulness": 0.70,
        "expected_fact_recall": 0.74,
    }
    optimized = {
        "queries_completed": 20,
        "errors": 0,
        "faithfulness": 0.71,
        "citation_precision": 0.97,
        "citation_recall": 0.97,
        "abstention_accuracy": 1.0,
        "expected_fact_recall": 0.86,
        "answer_completion_accuracy": 0.97,
        **optimized_overrides,
    }
    return {"control": control, "optimized": optimized}


def test_grounded_answer_gate_passes_balanced_improvement():
    result = evaluate_quality_gate(_aggregate())

    assert result["passed"] is True
    assert result["violations"] == []


def test_grounded_answer_gate_rejects_fact_coverage_regression():
    result = evaluate_quality_gate(_aggregate(expected_fact_recall=0.68))

    assert result["passed"] is False
    assert any("expected_fact_recall" in item for item in result["violations"])


def test_grounded_answer_gate_rejects_weak_citations():
    result = evaluate_quality_gate(_aggregate(citation_precision=0.80, citation_recall=0.85))

    assert result["passed"] is False
    assert any("citation_precision" in item for item in result["violations"])
    assert any("citation_recall" in item for item in result["violations"])


def test_grounded_answer_gate_rejects_low_completion_accuracy():
    result = evaluate_quality_gate(_aggregate(answer_completion_accuracy=0.90))

    assert result["passed"] is False
    assert any("answer_completion_accuracy" in item for item in result["violations"])


def test_qrel_answerability_is_independent_from_retrieval_relevance():
    query = QrelQuery.from_dict(
        {
            "query_id": "relation-missing",
            "query": "两个实体有什么区别",
            "relevant": [
                {
                    "document_key": "doc",
                    "section_key": "entities",
                    "grade": 2,
                }
            ],
            "answerability": "none",
            "answerability_rationale": "实体出现，但所问关系缺失。",
        }
    )

    assert query.relevant
    assert query.answerability == "none"


def test_legacy_qrel_answerability_is_inferred():
    answerable = QrelQuery.from_dict(
        {
            "query_id": "old-positive",
            "query": "q",
            "relevant": [{"document_key": "doc", "section_key": "s"}],
        }
    )
    unanswerable = QrelQuery.from_dict(
        {
            "query_id": "old-negative",
            "query": "q",
            "relevant": [],
        }
    )

    assert answerable.answerability == "full"
    assert unanswerable.answerability == "none"


def test_supported_partial_answer_is_not_misclassified_as_full_refusal():
    verification = SimpleNamespace(facts_supported=1)

    assert (
        _is_full_refusal(
            "光伏成本下降了 90% [S1]。现有资料不足以比较风电成本。",
            verification,
        )
        is False
    )
    assert (
        _is_full_refusal(
            "现有资料不足以回答该问题。",
            SimpleNamespace(facts_supported=0),
        )
        is True
    )
    assert (
        _is_full_refusal(
            "无法确认两者存在相似之处。",
            SimpleNamespace(facts_supported=0),
        )
        is True
    )


def test_supported_context_with_explicit_relation_limit_is_safe_abstention():
    answer = "One-Hot 和 Label Encoding 都属于类别变量编码 [S1]。两者的区别无法从现有资料确认。"

    assert _is_full_refusal(answer, SimpleNamespace(facts_supported=1)) is False
    assert _is_safe_abstention(answer) is True


def test_unrecognized_question_phrase_is_safe_abstention():
    assert _is_safe_abstention("抱歉，我无法识别您的问题，请提供具体问题。")


def test_fact_recall_ignores_spacing_and_thousands_separators():
    query = QrelQuery(
        query_id="formatting",
        query="q",
        expected_answer_facts=["200万年", "3,000 GW"],
    )

    assert _fact_recall("距今 200 万年，容量达到 3000 GW。", query) == 1.0


def test_fact_recall_accepts_human_annotated_expression_alternatives():
    query = QrelQuery(
        query_id="alternatives",
        query="q",
        expected_answer_facts=[
            "成本下降|成本在过去十年下降",
            "Carbonara|西班牙海鲜饭|Paella",
        ],
    )

    answer = "光伏发电成本在过去十年下降了 90%，主菜是西班牙海鲜饭。"
    assert _fact_recall(answer, query) == 1.0


def test_fact_recall_treats_domain_aliases_as_one_annotated_fact():
    query = QrelQuery(
        query_id="domain-aliases",
        query="q",
        expected_answer_facts=[
            "SQLAlchemy|ORM",
            "WTForms|表单验证",
            "泛化能力|模型性能",
        ],
    )

    assert _fact_recall("需要 ORM 和表单验证，可稳定估计模型性能。", query) == 1.0


def test_clarification_request_counts_as_safe_abstention():
    assert _is_full_refusal("请指明您说的是 Django、Flask 还是 FastAPI。", None)
    assert _is_full_refusal("请提供具体的问题内容。", None)
    assert _is_full_refusal("问题缺少明确的指代对象，请补充上下文。", None)
