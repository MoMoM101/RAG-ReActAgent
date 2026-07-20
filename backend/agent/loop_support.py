"""Grounding verification and answer-cache helpers for the agent loop."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from agent.verifier import Evidence
from config import settings
from llm.base import ChatMessage

if TYPE_CHECKING:
    from agent.stream_verify import AtomicUnit, UnitResult


def repair_single_unit(
    unit: AtomicUnit,
    result: UnitResult,
    evidence: list,
) -> tuple[str, bool]:
    """Apply deterministic citation repair to a single unit."""
    from agent.grounding_repair import (
        _get_valid_citation_ids,
        repair_atomic_claim_citations,
    )
    from agent.stream_verify import UnitVerdict

    if result.verdict != UnitVerdict.FORMAT_ONLY:
        return unit.text, False
    valid_ids = _get_valid_citation_ids(evidence)
    repaired, changes = repair_atomic_claim_citations(unit.text, evidence, valid_ids)
    return repaired, bool(changes)


def verify_stream_unit(
    unit: AtomicUnit,
    evidence: list[Evidence],
    query: str,
) -> UnitResult:
    """Apply the final-answer selective refusal policy to a stream unit."""
    from agent.stream_verify import UnitResult, UnitVerdict, verify_unit
    from agent.verifier import (
        build_partial_comparison_fallback,
        needs_grounding_repair,
    )

    decision = needs_grounding_repair(
        unit.text,
        [
            {
                "citation_id": item.citation_id,
                "text": item.text,
                "document_key": item.document_key,
                "section_key": item.section_key,
                "filename": item.filename,
            }
            for item in evidence
        ],
        query=query,
        coverage_recheck=False,
    )
    if "topical_false_refusal" in decision.reasons:
        if decision.action == "llm_repair":
            if build_partial_comparison_fallback(query, evidence):
                return UnitResult(unit=unit, verdict=UnitVerdict.VERIFIED)
            return UnitResult(
                unit=unit,
                verdict=UnitVerdict.UNSUPPORTED,
                reason="topical_false_refusal",
            )
        return UnitResult(unit=unit, verdict=UnitVerdict.VERIFIED)
    return verify_unit(unit, evidence)


def cache_sources_accessible(sources: list[dict]) -> bool:
    """Check that a cached result still carries document-backed sources."""
    if not sources:
        return False
    return any(source.get("document_id") or source.get("chunk_id") for source in sources)


def build_answer_cache_key(
    user_message: str,
    conversation_history: list[ChatMessage],
    sources: list[dict],
    profile_text: str,
) -> str:
    """Build the cache key from retrieval, history, profile, model, and policy."""
    from rag.answer_cache import AnswerCache, get_answer_cache

    context_hash = AnswerCache.context_hash(conversation_history)
    profile_hash = hashlib.sha256(profile_text.encode("utf-8")).hexdigest()[:12] if profile_text else ""
    scoped_context = ":".join(part for part in (context_hash, profile_hash) if part)
    return AnswerCache.compute_key(
        normalized_query=user_message,
        retrieval_hash=AnswerCache.retrieval_hash(sources),
        collection_version=get_answer_cache().collection_version,
        model_name=settings.llm_model,
        prompt_version="v5-comparison-complete",
        context_hash=scoped_context,
        grounding_policy_version=AnswerCache.grounding_policy_version(),
    )
