"""Deterministic citation repair for grounded answers (V4 Phase 1).

Fixes citation formatting issues without calling an LLM:

1. Move citations from after sentence-end punctuation to before it.
2. Deduplicate repeated citations within the same atomic claim.
3. Remove citations to non-existent source IDs.
4. Select the minimal set of sources that still fully supports a claim.
5. Auto-cite unsupported claims when a unique high-confidence source exists.

Safety rules (auto-cite MUST satisfy ALL conditions):
  - The claim is directly supported by the candidate evidence.
  - All numbers/dates/versions in the claim appear in the candidate source.
  - Best candidate support score >= auto_cite_min_score (default 0.55).
  - Best candidate score margin over 2nd-best >= auto_cite_min_margin (default 0.15),
    OR only one candidate reaches the threshold.
  - The claim does NOT involve comparisons, causality, rankings, or derivations.
  - Only citation markers and punctuation are modified — claim text is never changed.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from agent.verifier import (
    _CITATION_RE,
    _SUPPORT_THRESHOLD,
    Evidence,
    GroundingDecision,
    _claim_citations,
    _content_tokens,
    _numbers,
    verify_answer,
)

logger = logging.getLogger(__name__)

# Patterns for deterministic repair
_POST_SENTENCE_PAT = re.compile(
    r"([。！？!?；;])\s*(\[S\d+(?:\s*[,，]\s*S\d+)*\])",
    re.IGNORECASE,
)
_DUPLICATE_CITATION_PAT = re.compile(
    r"(\[S\d+(?:\s*[,，]\s*S\d+)*\])(?:\s*\1)+",
    re.IGNORECASE,
)

# Claims that should never receive auto-citations
_DERIVATION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"因此|所以|由此可见|综上",
        r"相比于?|比.*更|优于|好于|超过|低于|不如",
        r"导致|引起|造成|促使|触发",
        r"最高|最低|最大|最小|最好|最差|最.*的",
        r"建议|推荐|应该|必须|需要",
        r"适用[于场景]|合适|适合",
    ]
]


@dataclass
class GroundingRepairResult:
    """Result of a deterministic grounding repair attempt."""

    repaired_text: str
    changes: list[str] = field(default_factory=list)
    repaired: bool = False
    needs_llm: bool = False
    llm_reasons: list[str] = field(default_factory=list)


def _get_valid_citation_ids(sources: list[Evidence]) -> set[str]:
    return {s.citation_id.upper() for s in sources}


def _best_supporting_source(
    claim: str, sources: list[Evidence], min_score: float, min_margin: float,
) -> Evidence | None:
    """Find the best source that supports a claim, respecting safety rules."""
    claim_text = _CITATION_RE.sub("", claim)
    claim_nums = _numbers(claim_text)

    # Check for derivation patterns — auto-cite forbidden
    for pat in _DERIVATION_PATTERNS:
        if pat.search(claim_text):
            return None

    scored: list[tuple[float, Evidence]] = []
    for src in sources:
        score = _source_coverage(claim_text, src.text)
        if score >= min_score:
            missing = claim_nums - _numbers(src.text)
            if not missing:
                scored.append((score, src))

    if not scored:
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_src = scored[0]
    if len(scored) == 1:
        return best_src
    second_best = scored[1][0]
    if best_score - second_best >= min_margin:
        return best_src
    return None


def _source_coverage(claim_text: str, source_text: str) -> float:
    """Token-level Jaccard coverage of claim tokens in source."""
    claim_tokens = _content_tokens(claim_text)
    source_tokens = _content_tokens(source_text)
    if not claim_tokens:
        return 0.0
    return len(claim_tokens & source_tokens) / len(claim_tokens)


# ── Repair functions ──────────────────────────────────────────────


def repair_citation_position(text: str) -> tuple[str, bool]:
    """Move citations from after sentence-end punctuation to before it.

    "事实。 [S1]" → "事实 [S1]。"
    """
    repaired = _POST_SENTENCE_PAT.sub(r" \2\1", text)
    return repaired, repaired != text


def repair_duplicate_citations(text: str) -> tuple[str, bool]:
    """Remove duplicate citation groups within the same sentence.

    "事实 [S1] [S1]。" → "事实 [S1]。"
    """

    def _dedup_in_citation_group(m: re.Match) -> str:
        group = m.group(1)  # content inside brackets, e.g. "S1, S2"
        ids = [cid.strip() for cid in re.split(r"\s*[,，]\s*", group)]
        seen: set[str] = set()
        unique: list[str] = []
        for cid in ids:
            if cid.upper() not in seen:
                seen.add(cid.upper())
                unique.append(cid)
        return "[" + ", ".join(unique) + "]"

    # First dedup within each citation group
    repaired = _CITATION_RE.sub(_dedup_in_citation_group, text)
    # Then remove adjacent identical groups
    repaired = _DUPLICATE_CITATION_PAT.sub(r"\1", repaired)
    return repaired, repaired != text


def repair_invalid_citations(
    text: str, valid_ids: set[str],
) -> tuple[str, list[str]]:
    """Remove citations that reference non-existent source IDs.

    Returns (repaired_text, removed_ids).
    """

    def _filter_citation(m: re.Match) -> str:
        group = m.group(0)
        ids = [cid.strip() for cid in re.split(r"\s*[,，]\s*", group[1:-1])]
        valid = [cid for cid in ids if cid.upper() in valid_ids]
        if not valid:
            return ""
        return "[" + ", ".join(valid) + "]"

    removed: list[str] = []
    for m in _CITATION_RE.finditer(text):
        ids = [cid.strip() for cid in re.split(r"\s*[,，]\s*", m.group(0)[1:-1])]
        removed.extend(cid for cid in ids if cid.upper() not in valid_ids)

    repaired = _CITATION_RE.sub(_filter_citation, text)
    # Clean up leftover whitespace from removed citations
    repaired = re.sub(r"\s{2,}", " ", repaired)
    repaired = re.sub(r"\s([。！？!?；;，,])", r"\1", repaired)
    return repaired.strip(), removed


def select_minimal_supporting_sources(
    claim: str, sources: list[Evidence],
) -> list[str]:
    """Return the minimal set of source IDs whose texts collectively support the claim.

    If a single source fully supports the claim, return just that one.
    Falls back to the original citations if no single source is sufficient.
    """
    original_citations = _claim_citations(claim)
    claim_text = _CITATION_RE.sub("", claim)
    claim_nums = _numbers(claim_text)
    evidence_by_id = {s.citation_id.upper(): s for s in sources}

    # Check each cited source individually
    for cid in original_citations:
        src = evidence_by_id.get(cid.upper())
        if src is None:
            continue
        score = _source_coverage(claim_text, src.text)
        missing = claim_nums - _numbers(src.text)
        if score >= _SUPPORT_THRESHOLD and not missing:
            return [cid]  # single source is sufficient

    # No single source is sufficient — keep original citations
    return original_citations


def auto_cite_claim(
    claim: str, sources: list[Evidence], min_score: float, min_margin: float,
) -> tuple[str, bool]:
    """Attempt to auto-cite an uncited claim with a high-confidence source.

    Returns (updated_claim, was_repaired). Only adds a citation when the
    safety conditions are strictly met.
    """
    # Only attempt for claims without existing citations
    if _claim_citations(claim):
        return claim, False

    best = _best_supporting_source(claim, sources, min_score, min_margin)
    if best is None:
        return claim, False

    # Add citation before sentence-end punctuation
    cid = best.citation_id.upper()
    end_punct = re.search(r"[。！？!?；;]$", claim)
    if end_punct:
        return claim[: end_punct.start()] + f" [{cid}]" + claim[end_punct.start():], True
    else:
        return f"{claim} [{cid}]", True


def repair_atomic_claim_citations(
    claim: str, sources: list[Evidence], valid_ids: set[str], *,
    min_score: float = 0.55, min_margin: float = 0.15,
) -> tuple[str, list[str]]:
    """Apply all deterministic repairs to a single atomic claim.

    Returns (repaired_claim, change_descriptions).
    """
    changes: list[str] = []
    current = claim

    # 1. Repair citation position
    fixed, changed = repair_citation_position(current)
    if changed:
        changes.append("citation_position")
        current = fixed

    # 2. Deduplicate citations
    fixed, changed = repair_duplicate_citations(current)
    if changed:
        changes.append("dedup_citations")
        current = fixed

    # 3. Remove invalid citations
    fixed, removed = repair_invalid_citations(current, valid_ids)
    if removed:
        changes.append(f"removed_invalid:{','.join(removed)}")
        current = fixed

    # 4. Select minimal supporting sources (only if claim has multiple citations)
    citations = _claim_citations(current)
    if len(citations) > 1:
        minimal = select_minimal_supporting_sources(current, sources)
        if len(minimal) < len(citations):
            for cid in citations:
                if cid.upper() not in [m.upper() for m in minimal]:
                    changes.append(f"removed_redundant:{cid}")
            # Rebuild citation group
            new_claim = _CITATION_RE.sub("", current).rstrip()
            new_claim = f"{new_claim} [{', '.join(minimal)}]"
            current = new_claim

    # 5. Auto-cite unsupported claims
    if not _claim_citations(current):
        fixed, changed = auto_cite_claim(current, sources, min_score, min_margin)
        if changed:
            changes.append("auto_cited")
            current = fixed

    return current, changes


def classify_grounding_failure(
    answer: str, sources: list[Evidence], query: str = "",
) -> GroundingDecision:
    """Classify the grounding failure reason for monitoring (V4 counter support).

    Used after a repair was triggered to record the dominant reason.
    """
    from agent.verifier import needs_grounding_repair

    # needs_grounding_repair now returns GroundingDecision (V4)
    decision = needs_grounding_repair(answer, sources, query=query)
    return decision


def deterministic_repair(
    answer: str,
    sources: list[Evidence],
    decision: GroundingDecision,
    *,
    min_score: float = 0.55,
    min_margin: float = 0.15,
    re_verify: bool = True,
) -> GroundingRepairResult:
    """Apply deterministic repairs when decision allows it.

    Only performs repairs when decision.action == "deterministic_repair".
    For "llm_repair" decisions, returns unchanged with needs_llm=True.
    For "accept" decisions, returns unchanged.
    """
    if decision.action == "accept":
        return GroundingRepairResult(repaired_text=answer)

    if decision.action == "llm_repair":
        format_reasons = {
            "missing_citation", "invalid_citation", "redundant_citation",
        }
        content_reasons = set(decision.reasons) - format_reasons
        # Still try format fixes even if LLM repair is needed
        if set(decision.reasons) & format_reasons:
            return GroundingRepairResult(
                repaired_text=answer,
                needs_llm=True,
                llm_reasons=sorted(content_reasons),
            )
        return GroundingRepairResult(
            repaired_text=answer,
            needs_llm=True,
            llm_reasons=decision.reasons,
        )

    # decision.action == "deterministic_repair"
    evidence = sources
    valid_ids = _get_valid_citation_ids(evidence)
    all_changes: list[str] = []

    # Split answer into paragraphs, then sentence-level claims. A paragraph can
    # contain multiple independently verified facts with only a trailing cite;
    # repairing the whole paragraph cannot fill those missing claim citations.
    paragraphs = answer.split("\n")
    repaired_paragraphs: list[str] = []
    for para in paragraphs:
        stripped = para.strip()
        if not stripped:
            repaired_paragraphs.append(para)
            continue

        # Skip structural lines (headings, labels)
        if (
            re.match(r"^(#{1,6}\s|[-*+]\s+\*\*.*\*\*|总结)", stripped)
            or re.fullmatch(r"(?:已确认|无法确认|注意)[：:]?", stripped)
        ):
            repaired_paragraphs.append(para)
            continue

        prefix_match = re.match(r"^(\s*(?:[-*+]\s+|\d+[.)、]\s*))", para)
        prefix = prefix_match.group(1) if prefix_match else ""
        content = para[len(prefix):] if prefix else para
        content, position_changed = repair_citation_position(content)
        if position_changed:
            all_changes.append("citation_position")
        units = [
            unit for unit in re.split(r"(?<=[。！？!?；;])\s*", content)
            if unit.strip()
        ]
        repaired_units: list[str] = []
        for unit in units:
            repaired, changes = repair_atomic_claim_citations(
                unit.strip(), evidence, valid_ids,
                min_score=min_score, min_margin=min_margin,
            )
            all_changes.extend(changes)
            repaired_units.append(repaired)
        repaired_paragraphs.append(prefix + "".join(repaired_units))

    repaired_text = "\n".join(repaired_paragraphs)

    # Re-verify to confirm quality improved
    if re_verify and all_changes:
        original_q = _quality(answer, evidence)
        repaired_q = _quality(repaired_text, evidence)
        if repaired_q <= original_q:
            logger.info(
                "deterministic repair rejected: quality did not improve "
                "(orig=%.3f repaired=%.3f)",
                original_q, repaired_q,
            )
            return GroundingRepairResult(
                repaired_text=answer,
                changes=all_changes,
                repaired=False,
                needs_llm=True,
                llm_reasons=decision.reasons,
            )

    return GroundingRepairResult(
        repaired_text=repaired_text,
        changes=all_changes,
        repaired=bool(all_changes),
        needs_llm=not all_changes,
        llm_reasons=decision.reasons if not all_changes else [],
    )


def _quality(text: str, sources: list[Evidence]) -> float:
    """Composite quality score for comparing original vs. repaired answers."""
    try:
        result = verify_answer(text, [{
            "citation_id": s.citation_id,
            "text": s.text,
            "document_key": s.document_key,
            "section_key": s.section_key,
            "filename": s.filename,
        } for s in sources])
        return (
            result.faithfulness * 0.40
            + result.citation_recall * 0.35
            + result.citation_precision * 0.25
        )
    except Exception:
        return 0.0
