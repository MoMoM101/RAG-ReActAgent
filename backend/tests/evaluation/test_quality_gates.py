from tests.evaluate_rag import _quality_gate_violations
from tests.qrels_schema import normalize_section_key


def test_section_key_normalization_matches_splitter_contract():
    assert normalize_section_key("意大利经典：Carbonara") == "意大利经典-carbonara"
    assert normalize_section_key("  FastAPI / 部署  ") == "fastapi---部署"


def test_live_quality_gate_accepts_non_regressing_ablation():
    ablation = {
        "keyword-only": {"hit_k5": 0.80},
        "semantic-only": {"ndcg_k5": 0.82},
        "hybrid-no-rerank": {"ndcg_k5": 0.81},
        "hybrid-rerank": {"ndcg_k5": 0.80},
    }

    assert _quality_gate_violations(ablation) == []


def test_live_quality_gate_reports_actionable_regressions():
    ablation = {
        "keyword-only": {"hit_k5": 0.60},
        "semantic-only": {"ndcg_k5": 0.85},
        "hybrid-no-rerank": {"ndcg_k5": 0.80},
        "hybrid-rerank": {"ndcg_k5": 0.77},
    }

    assert _quality_gate_violations(ablation) == [
        "keyword-only Hit@5 is below 75%",
        "hybrid NDCG@5 trails semantic-only by more than 3 points",
        "rerank reduces NDCG@5 by more than 2 points",
    ]
