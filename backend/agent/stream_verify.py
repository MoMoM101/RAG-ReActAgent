"""Atomic unit buffer and streaming verification state machine (V4 Phase 3).

Buffers LLM output tokens until a complete atomic unit boundary is detected,
then verifies the unit against retrieved sources before sending it to the
client.  Unsupported units pause the stream and trigger a bounded repair pass
that only generates the remaining content.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum, auto

from agent.verifier import (
    _CITATION_RE,
    Evidence,
    _claim_citations,
    _content_tokens,
    _number_subset_of,
    _numbers,
)

logger = logging.getLogger(__name__)

# ── Atomic unit boundary patterns ────────────────────────────────────

# Sentence-ending punctuation (Chinese + ASCII)
_SENTENCE_END = re.compile(r"[。！？!?；;]")

# Markdown list items
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)、]\s+)", re.MULTILINE)

# Citation group anywhere
_CITATION_GROUP = re.compile(r"\[S\d+(?:\s*[,，]\s*S\d+)*\]", re.IGNORECASE)

# Structural labels that should not be treated as standalone facts
_STRUCTURAL_LINE = re.compile(
    r"^(#{1,6}\s|已确认[：:]|无法确认[：:]|注意[：:]|总结|以下是|现有资料|"
    r"\*\*.*\*\*|> )",
)

# Blank line / paragraph break
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")

# Minimum characters for a sendable unit (excludes citations)
_MIN_UNIT_CHARS = 4


class UnitVerdict(Enum):
    VERIFIED = auto()
    FORMAT_ONLY = auto()
    UNSUPPORTED = auto()
    INCOMPLETE = auto()


@dataclass
class AtomicUnit:
    text: str
    boundary: str = ""  # the punctuation/separator that ended this unit
    citations: list[str] = field(default_factory=list)


@dataclass
class UnitResult:
    unit: AtomicUnit
    verdict: UnitVerdict
    repaired_text: str = ""
    missing_citations: list[str] = field(default_factory=list)
    reason: str = ""


class AtomicUnitBuffer:
    """Accumulates streaming tokens and emits complete atomic units."""

    def __init__(self):
        self._buf: list[str] = []
        self._committed: list[AtomicUnit] = []

    @property
    def committed_text(self) -> str:
        return "".join(u.text + u.boundary for u in self._committed)

    @property
    def pending_text(self) -> str:
        return "".join(self._buf)

    def feed(self, token: str) -> AtomicUnit | None:
        """Feed a token; return a complete AtomicUnit if a boundary is found."""
        self._buf.append(token)
        return self._extract_unit()

    def extract_next(self) -> AtomicUnit | None:
        """Try extracting another unit from the buffer (after a previous extraction)."""
        return self._extract_unit()

    def flush_remainder(self) -> AtomicUnit | None:
        """Return whatever is left in the buffer as a unit, if non-empty."""
        text = "".join(self._buf).strip()
        self._buf.clear()
        if len(text) >= _MIN_UNIT_CHARS:
            return AtomicUnit(text=text)
        return None

    def commit(self, unit: AtomicUnit) -> None:
        self._committed.append(unit)

    def reset_for_repair(self) -> None:
        """Clear buffer for repair generation; committed units are kept."""
        self._buf.clear()

    def _extract_unit(self) -> AtomicUnit | None:
        """Try to find a complete atomic unit at the end of the buffer."""
        text = "".join(self._buf)

        # Don't split mid-citation
        if _is_mid_citation(text):
            return None

        # Try sentence boundaries first
        m = _SENTENCE_END.search(text)
        if m:
            end = m.end()
            # Check if a citation follows the punctuation
            after_raw = text[end:]  # includes whitespace before citation
            after_stripped = after_raw.lstrip()
            cite_m = _CITATION_GROUP.match(after_stripped)
            if cite_m:
                # Extend end past whitespace + citation group
                ws_len = len(after_raw) - len(after_stripped)
                end += ws_len + len(cite_m.group(0))

            unit_text = text[:end].strip()
            # Sentence punctuation is already part of unit_text.  The text
            # after ``end`` belongs to the next unit and must never be emitted
            # as this unit's boundary, otherwise it is sent twice.
            boundary = ""
            remaining = text[end:].lstrip()

            if len(_CITATION_RE.sub("", unit_text).strip()) < _MIN_UNIT_CHARS:
                return None  # too short to be meaningful

            # Don't split structural lines alone
            if _STRUCTURAL_LINE.match(unit_text) and not _SENTENCE_END.search(_CITATION_RE.sub("", unit_text)):
                return None

            self._buf = [remaining] if remaining else []
            citations = _claim_citations(unit_text)
            return AtomicUnit(text=unit_text, boundary=boundary, citations=citations)

        # Try paragraph breaks
        pm = _PARAGRAPH_BREAK.search(text)
        if pm:
            unit_text = text[: pm.start()].strip()
            remaining = text[pm.end() :].lstrip()

            if len(_CITATION_RE.sub("", unit_text).strip()) < _MIN_UNIT_CHARS:
                return None

            if _STRUCTURAL_LINE.match(unit_text) and not _SENTENCE_END.search(_CITATION_RE.sub("", unit_text)):
                return None

            self._buf = [remaining] if remaining else []
            citations = _claim_citations(unit_text)
            return AtomicUnit(text=unit_text, boundary="\n\n", citations=citations)

        return None  # still incomplete


def _is_mid_citation(text: str) -> bool:
    """Check if the buffer ends mid-citation like '[S1' or '[S1, S'."""
    # Case-insensitive search — LLM may emit lowercase [s1
    text_lower = text.lower()
    last_bracket = text_lower.rfind("[s")
    if last_bracket == -1:
        return False
    after = text[last_bracket:]
    # Complete citation group: [S1], [S1, S2], etc.
    if _CITATION_GROUP.fullmatch(after.strip()):
        return False
    # If we have [S but no closing ], we're mid-citation
    return "]" not in after


# ── Unit-level verification ─────────────────────────────────────


def verify_unit(
    unit: AtomicUnit,
    evidence: list[Evidence],
    *,
    min_support_score: float = 0.28,
) -> UnitResult:
    """Check whether a single atomic unit is grounded in the evidence.

    Returns a UnitResult with the verdict and repair instructions.
    """
    plain = _CITATION_RE.sub("", unit.text).strip()

    # Skip structural/structural-only lines
    if _STRUCTURAL_LINE.match(plain) and not _SENTENCE_END.search(plain):
        return UnitResult(unit=unit, verdict=UnitVerdict.VERIFIED)

    # No facts to verify
    if len(plain) < _MIN_UNIT_CHARS:
        return UnitResult(unit=unit, verdict=UnitVerdict.VERIFIED)

    unit_nums = _numbers(plain)
    unit_tokens = _content_tokens(plain)

    if not unit_tokens:
        return UnitResult(unit=unit, verdict=UnitVerdict.VERIFIED)

    cited_ids = {c.upper() for c in unit.citations}
    evidence_by_id = {e.citation_id.upper(): e for e in evidence}

    # Check each cited source
    best_score = 0.0
    best_src_id = ""
    missing_numbers: set[str] = set()

    cited_evidence: list[Evidence] = []
    if cited_ids:
        for cid in cited_ids:
            src = evidence_by_id.get(cid)
            if src is None:
                continue
            cited_evidence.append(src)
            score = _source_cov(unit_tokens, _content_tokens(src.text))
            if score > best_score:
                best_score = score
                best_src_id = cid
            missing = _number_subset_of(unit_nums, _numbers(src.text))
            if score >= min_support_score and not missing:
                return UnitResult(unit=unit, verdict=UnitVerdict.VERIFIED)
            if not missing:
                missing_numbers = set()
            elif score == best_score:
                missing_numbers = missing
        # Comparison and synthesis claims are often supported jointly by
        # multiple explicitly cited chunks. Keep single-source checks strict,
        # then allow the cited evidence union to satisfy the same threshold.
        if len(cited_evidence) > 1:
            union_tokens: set[str] = set()
            union_numbers: set[str] = set()
            for src in cited_evidence:
                union_tokens.update(_content_tokens(src.text))
                union_numbers.update(_numbers(src.text))
            union_score = _source_cov(unit_tokens, union_tokens)
            union_missing = _number_subset_of(unit_nums, union_numbers)
            best_score = max(best_score, union_score)
            if union_score >= min_support_score and not union_missing:
                return UnitResult(unit=unit, verdict=UnitVerdict.VERIFIED)
            missing_numbers = union_missing
    if not cited_ids or not cited_evidence:
        # No citations or all phantom — check all evidence for potential support.
        # Phantom citations (e.g. [S99] with only 8 sources) are treated as
        # uncited content eligible for deterministic auto-cite repair.
        if not cited_ids:
            pass  # original uncited case
        else:
            cited_ids = set()  # clear phantom citations for FORMAT_ONLY path below
        for src in evidence:
            score = _source_cov(unit_tokens, _content_tokens(src.text))
            if score > best_score:
                best_score = score
                best_src_id = src.citation_id
            missing = _number_subset_of(unit_nums, _numbers(src.text))
            if score >= min_support_score and not missing:
                # Uncited but supported → format issue
                return UnitResult(
                    unit=unit,
                    verdict=UnitVerdict.FORMAT_ONLY,
                    missing_citations=[src.citation_id],
                    reason="missing_citation",
                )
            if not missing:
                missing_numbers = set()
            elif score == best_score:
                missing_numbers = missing

    # Determine verdict
    if best_score >= min_support_score and not missing_numbers:
        if not cited_ids:
            return UnitResult(
                unit=unit,
                verdict=UnitVerdict.FORMAT_ONLY,
                missing_citations=[best_src_id],
                reason="missing_citation",
            )
        return UnitResult(unit=unit, verdict=UnitVerdict.VERIFIED)

    if best_score < min_support_score:
        return UnitResult(
            unit=unit,
            verdict=UnitVerdict.UNSUPPORTED,
            reason="unsupported_claim" if not cited_ids else "insufficient_evidence",
        )

    if missing_numbers:
        return UnitResult(
            unit=unit,
            verdict=UnitVerdict.UNSUPPORTED,
            reason="missing_number",
        )

    return UnitResult(unit=unit, verdict=UnitVerdict.VERIFIED)


def _source_cov(claim_tokens: set[str], source_tokens: set[str]) -> float:
    if not claim_tokens:
        return 0.0
    return len(claim_tokens & source_tokens) / len(claim_tokens)


def build_repair_prompt(
    query: str,
    sources: list[dict],
    committed_units: list[AtomicUnit],
    remaining_draft: str,
) -> str:
    """Build the repair prompt that asks the LLM to only generate remaining content.

    The prompt includes already-committed facts so the model doesn't repeat them.
    """
    committed_text = "\n".join(u.text for u in committed_units) if committed_units else ""

    source_list = []
    for s in sources[:8]:
        cid = s.get("citation_id", "?")
        text = s.get("text", "")[:200]
        source_list.append(f"[{cid}] {text}")

    return (
        "以下回答的前半部分已通过校验并发送给用户，不可修改和重复。\n\n"
        f"用户问题：{query}\n\n"
        "可用的唯一来源：\n"
        + "\n".join(source_list)
        + "\n\n已发送的已验证内容：\n"
        + (committed_text or "（无）")
        + "\n\n未完成草稿（请基于来源重新生成后半部分，每个事实一句，"
        "引用放在句号前）：\n" + remaining_draft + "\n\n请只输出尚未发送的后半部分内容。不要重复已发送的内容。"
        "不要调用工具。使用可直接渲染的 Markdown；每个列表项只写一个原子事实，"
        "并引用唯一支持该事实的来源。不要把答案包在代码围栏中，"
        "也不要在 Markdown 表格行前添加列表标记。"
    )
