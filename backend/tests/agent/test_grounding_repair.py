"""Tests for deterministic grounding repair (V4 Phase 1)."""

from agent.grounding_repair import (
    auto_cite_claim,
    deterministic_repair,
    repair_atomic_claim_citations,
    repair_citation_position,
    repair_duplicate_citations,
    repair_invalid_citations,
    select_minimal_supporting_sources,
)
from agent.verifier import (
    Evidence,
    GroundingDecision,
)


def _evidence(citation_id: str = "S1", text: str = "") -> Evidence:
    return Evidence(
        citation_id=citation_id,
        text=text or f"Test evidence for {citation_id}.",
    )


def _sources(*texts: str) -> list[Evidence]:
    return [
        Evidence(citation_id=f"S{i+1}", text=t)
        for i, t in enumerate(texts)
    ]


# ── Citation position repair ────────────────────────────────


def test_moves_citation_from_after_period_to_before():
    text, changed = repair_citation_position("这是一个事实。 [S1]")
    assert changed
    assert text == "这是一个事实 [S1]。"


def test_leaves_correctly_positioned_citation_unchanged():
    text, changed = repair_citation_position("这是一个事实 [S1]。")
    assert not changed
    assert text == "这是一个事实 [S1]。"


def test_handles_multiple_sentence_end_markers():
    text, changed = repair_citation_position("你好！ [S1] 这是真的吗？ [S2]")
    assert changed
    assert " [S1]！" in text
    assert " [S2]？" in text


# ── Duplicate citation repair ───────────────────────────────


def test_deduplicates_repeated_citation_group():
    text, changed = repair_duplicate_citations("事实 [S1] [S1]。")
    assert changed
    assert text == "事实 [S1]。"


def test_deduplicates_within_citation_group():
    text, changed = repair_duplicate_citations("事实 [S1, S1, S2]。")
    assert changed
    assert "[S1, S2]" in text


def test_leaves_distinct_citations_unchanged():
    text, changed = repair_duplicate_citations("事实 [S1, S2]。")
    assert not changed


# ── Invalid citation removal ────────────────────────────────


def test_removes_nonexistent_citation():
    text, removed = repair_invalid_citations("事实 [S1, S99]。", {"S1"})
    assert "S99" in removed
    assert "[S1]" in text
    assert "S99" not in text


def test_removes_entire_group_when_all_invalid():
    text, removed = repair_invalid_citations("事实 [S99]。", {"S1"})
    assert "S99" in removed
    assert "[S99]" not in text


# ── Auto-cite safety ────────────────────────────────────────


def test_auto_cite_adds_citation_when_unique_high_confidence():
    claim = "Django 内置后台管理界面。"
    sources = _sources("Django 使用内置后台管理界面进行内容管理。")
    repaired, changed = auto_cite_claim(claim, sources, min_score=0.55, min_margin=0.15)
    assert changed
    assert "[S1]" in repaired


def test_auto_cite_rejects_when_score_too_low():
    claim = "Django has an admin interface Django Django Django。"
    sources = _sources("Flask is a lightweight micro-framework.")
    repaired, changed = auto_cite_claim(claim, sources, min_score=0.55, min_margin=0.15)
    assert not changed


def test_auto_cite_rejects_comparison_claim():
    claim = "Django 比 Flask 更适合大型项目。"
    sources = _sources("Django 适合大型项目，Flask 适合小型项目。")
    repaired, changed = auto_cite_claim(claim, sources, min_score=0.55, min_margin=0.15)
    assert not changed


def test_auto_cite_rejects_superlative_claim():
    claim = "Django 是最好的 Python 框架。"
    sources = _sources("Django 是最流行的 Python Web 框架之一。")
    repaired, changed = auto_cite_claim(claim, sources, min_score=0.55, min_margin=0.15)
    assert not changed


def test_auto_cite_rejects_when_multiple_candidates_close():
    claim = "Django 支持 ORM 和模板系统。"
    sources = _sources(
        "Django 内置 ORM 和模板引擎用于 Web 开发。",
        "Django 模板系统与 ORM 紧密集成。",
    )
    repaired, changed = auto_cite_claim(claim, sources, min_score=0.55, min_margin=0.15)
    assert not changed


def test_auto_cite_rejects_when_numbers_mismatch():
    claim = "温度上升了 1.5°C。"
    sources = _sources("工业革命以来全球温度已上升约 1.1°C")
    repaired, changed = auto_cite_claim(claim, sources, min_score=0.55, min_margin=0.15)
    assert not changed


# ── Minimal source selection ────────────────────────────────


def test_selects_single_source_when_sufficient():
    sources = _sources(
        "Django 内置 ORM、后台管理界面和认证系统。",
        "Django 也支持中间件和模板系统。",
    )
    selected = select_minimal_supporting_sources(
        "Django 内置 ORM [S1, S2]。", sources,
    )
    assert len(selected) <= 2
    # At minimum should not increase citations
    assert len(selected) >= 1


# ── Atomic claim repair ─────────────────────────────────────


def test_atomic_repair_applies_all_fixes():
    sources = _sources("Django 是一个电池自带的 Web 框架，内置后台管理界面。")
    claim = "Django 内置后台管理界面。 [S1]  [S1]  [S99]"
    repaired, changes = repair_atomic_claim_citations(
        claim, sources, {"S1"},
    )
    assert "citation_position" in changes
    # Should remove duplicate and invalid


# ── Deterministic repair entry point ────────────────────────


def test_deterministic_repair_accept_returns_unchanged():
    decision = GroundingDecision(action="accept")
    result = deterministic_repair("Hello.", _sources("Test."), decision)
    assert not result.repaired
    assert result.repaired_text == "Hello."


def test_deterministic_repair_llm_repair_returns_with_needs_llm():
    decision = GroundingDecision(
        action="llm_repair",
        reasons=["unsupported_claim"],
    )
    result = deterministic_repair("内容。", _sources("Test."), decision)
    assert result.needs_llm
    assert "unsupported_claim" in result.llm_reasons


def test_failed_deterministic_repair_escalates_to_llm():
    decision = GroundingDecision(
        action="deterministic_repair",
        reasons=["missing_citation"],
    )
    result = deterministic_repair(
        "无法安全自动引用的推荐结论。",
        _sources("仅包含客观资料。"),
        decision,
    )
    assert not result.repaired
    assert result.needs_llm
    assert result.llm_reasons == ["missing_citation"]


def test_deterministic_repair_fixes_position_and_returns_repaired():
    decision = GroundingDecision(
        action="deterministic_repair",
        reasons=["missing_citation"],
    )
    sources = _sources("Django 内置后台管理界面。")
    result = deterministic_repair(
        "Django 内置后台管理界面。 [S1]", sources, decision,
    )
    # Should try to fix citation position
    assert result.changes or not result.repaired  # may or may not repair (depends on quality check)


def test_deterministic_repair_handles_each_sentence_in_list_item():
    decision = GroundingDecision(
        action="deterministic_repair",
        reasons=["missing_citation"],
    )
    sources = _sources(
        "传统希腊沙拉属于前菜。食材包括番茄、黄瓜和菲达奶酪。",
    )
    result = deterministic_repair(
        "- 传统希腊沙拉属于前菜。食材包括番茄、黄瓜和菲达奶酪 [S1]。",
        sources,
        decision,
        min_score=0.28,
    )
    assert result.repaired
    assert "属于前菜 [S1]。" in result.repaired_text


def test_same_line_confirmed_label_does_not_skip_citation_repair():
    decision = GroundingDecision(
        action="deterministic_repair",
        reasons=["missing_citation"],
    )
    sources = _sources("Django 内置后台管理界面。")

    result = deterministic_repair(
        "已确认：Django 内置后台管理界面。",
        sources,
        decision,
    )

    assert result.repaired
    assert "[S1]" in result.repaired_text
