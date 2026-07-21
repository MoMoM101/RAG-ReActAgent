"""Deterministic claim-to-evidence verification for knowledge-base answers."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from agent.query_semantics import (
    COMPARISON_QUERY_RE,
    COVERAGE_QUERY_RE,
    UNRESOLVED_REFERENCE_RE,
    extract_comparison_entities,
    is_comparison_query,
    is_underspecified_query,
)

logger = logging.getLogger(__name__)

_MIN_CLAIM_LENGTH = 6
_SUPPORT_THRESHOLD = 0.28
_CITATION_RE = re.compile(r"\[(S\d+(?:\s*[,，]\s*S\d+)*)\]", re.IGNORECASE)
_POST_SENTENCE_CITATION_RE = re.compile(
    r"([。！？!?；;])\s*(\[S\d+(?:\s*[,，]\s*S\d+)*\])",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)*(?:%|℃|°C|ms|s|MB|GB|mg|V)?", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.+-]*|\d+(?:\.\d+)*|[\u4e00-\u9fff]+")
_META_PREFIXES = (
    "以下是",
    "总结",
    "注意",
    "抱歉",
    "无法从",
    "未找到",
    "现有资料",
    "无法确认",
    "如需",
)
_LIMITATION_RE = re.compile(
    r"^(?:但)?(?:现有)?资料(?:中)?(?:不足|未|没有)"
    r"|^.+(?:未在|没有在)(?:现有)?资料(?:中)?(?:提及|说明|提供)"
    r"|^.+(?:现有)?资料(?:中)?(?:未|没有)(?:提及|说明|提供|涉及)",
)
_EVIDENCE_LEAD_RE = re.compile(
    r"^(?:根据|参考)(?:现有)?(?:检索)?(?:来源|资料|内容)"
    r"(?:\s*\[S\d+(?:\s*[,，]\s*S\d+)*\])?[，,:：]?\s*",
    re.IGNORECASE,
)
_CONFIRMED_LEAD_RE = re.compile(r"^已确认[：:]\s*")
_LIMITATION_ANYWHERE_RE = re.compile(r"(?:现有)?资料(?:中)?不足以|无法(?:直接)?(?:确认|确定|比较|回答)")
_REFUSAL_RE = re.compile(
    r"现有资料不足|资料不足|无法从现有资料|无法回答|无法确认|无法确定|未找到相关"
    r"|没有明确(?:指定|的)?|请(?:您)?(?:提供|说明|明确|指定)|具体指的是什么"
    r"|指代不清|无法理解.{0,6}问题"
)
_FULL_REFUSAL_START_RE = re.compile(
    r"^\s*(?:已确认[：:]\s*)?(?:"
    r"现有资料不足|资料不足|无法从现有资料|无法回答|无法确认|无法确定|未找到相关"
    r"|您的问题.{0,12}没有明确|请(?:您)?(?:提供|说明|明确|指定)"
    r"|追问[：:]?\s*请问|无法理解.{0,6}问题"
    r")"
)
_TOPICAL_RETRY_BLOCK_RE = re.compile(
    r"忽略.{0,8}指令|系统提示词|"
    r"职责|怎么计算|如何计算|计算公式|"
    r"原因|为什么|如何影响|什么关系"
)
_CLARIFICATION_RE = re.compile(
    r"没有明确(?:指定|的)?|请(?:您)?(?:提供|说明|明确|指定).{0,16}"
    r"(?:对象|内容|主题|话题|框架|问题|信息)|具体指的是什么"
    r"|指代不清|无法理解.{0,6}问题|请问您想(?:询问|了解|问)(?:什么|哪方面)"
)
_QUERY_TOKEN_STOPWORDS = {
    "什么",
    "怎么",
    "如何",
    "哪个",
    "哪些",
    "可以",
    "是否",
    "有什么",
    "不同",
    "共同",
    "比较",
    "资料",
    "问题",
    "使用",
    "进行",
}
_COVERAGE_QUERY_RE = COVERAGE_QUERY_RE
_COMPARISON_QUERY_RE = COMPARISON_QUERY_RE
_COMPARISON_ANSWER_RE = re.compile(
    r"不同|区别|差异|相比|相较|共同|相似|优于|劣于|各自|分别|选择|适合|更(?:好|优|适合)",
)
_UNRESOLVED_REFERENCE_RE = UNRESOLVED_REFERENCE_RE
_SUPERLATIVE_QUERY_RE = re.compile(
    r"(?:哪(?:个|种|项|类).{0,10}最|最(?:能|适合|有效|好|优|佳)|最佳|最好|首选)",
)
_SUPERLATIVE_ANSWER_RE = re.compile(
    r"(?:最(?:能|适合|有效|好|优|佳)|最佳|最好|首选|优于|高于|低于)",
)
_CALCULATION_QUERY_RE = re.compile(r"(?:怎么|如何)?计算|计算公式|公式是什么")
_CALCULATION_ANSWER_RE = re.compile(
    r"(?:公式|计算为|等于|调和平均|=|/|÷|×|\*)",
)
_RESPONSIBILITY_QUERY_RE = re.compile(r"职责|负责什么|作用分别|每层.{0,8}(?:做什么|作用)")
_RESPONSIBILITY_ANSWER_RE = re.compile(r"负责|用于|作用是|处理|管理|呈现|渲染|控制")
_CAUSAL_QUERY_RE = re.compile(r"原因|为什么|为何")
_CAUSAL_ANSWER_RE = re.compile(r"因为|原因|由于|源于|导致|使得|所以")
_IMPACT_QUERY_RE = re.compile(r"如何影响|有什么影响")
_IMPACT_ANSWER_RE = re.compile(r"影响|导致|使得|从而|增加|降低|提升|减少")
_RELATION_QUERY_RE = re.compile(r"什么关系|有何关系")
_RELATION_ANSWER_RE = re.compile(r"关系|属于|基于|依赖|连接|组成|包含")


def comparison_answer_complete(query: str, answer: str) -> bool:
    """Require an explicit relation and both named sides when extractable."""
    if not is_comparison_query(query):
        return True
    if _REFUSAL_RE.search(answer):
        return True
    if not _COMPARISON_ANSWER_RE.search(answer):
        return False
    entities = extract_comparison_entities(query)
    if entities is None:
        return True
    normalized_answer = re.sub(r"\s+", "", answer).casefold()
    return all(re.sub(r"\s+", "", entity).casefold() in normalized_answer for entity in entities)


@dataclass(frozen=True)
class Evidence:
    """Normalized source evidence used by the verifier."""

    citation_id: str
    text: str
    document_key: str = ""
    section_key: str = ""
    filename: str = ""


EvidenceSource = str | Mapping[str, Any] | Evidence


@dataclass
class ClaimVerification:
    """Verification details for one factual claim."""

    claim: str
    citations: list[str] = field(default_factory=list)
    supported: bool = False
    support_score: float = 0.0
    supporting_citations: list[str] = field(default_factory=list)
    missing_numbers: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class GroundingDecision:
    """Structured grounding repair decision (V4).

    Replaces the previous boolean return from needs_grounding_repair.
    action is one of: accept, deterministic_repair, llm_repair, refuse.
    """

    action: str = "accept"
    reasons: list[str] = field(default_factory=list)
    verification: VerificationResult | None = None

    @property
    def needs_repair(self) -> bool:
        """Backward-compatible boolean: True when repair action is required."""
        return self.action in ("deterministic_repair", "llm_repair")

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reasons": self.reasons,
            "verification": self.verification.to_dict() if self.verification else None,
        }


@dataclass
class VerificationResult:
    """Aggregate and explainable grounded-answer verification result."""

    facts_found: int = 0
    facts_supported: int = 0
    coverage: float = 0.0
    sources_used: int = 0
    status: str = "not_verified"
    citation_precision: float = 0.0
    citation_recall: float = 0.0
    claims: list[ClaimVerification] = field(default_factory=list)

    @property
    def faithfulness(self) -> float:
        return self.coverage

    @property
    def unsupported_claims(self) -> list[str]:
        return [claim.claim for claim in self.claims if not claim.supported]

    def to_dict(self, *, include_claims: bool = False) -> dict[str, Any]:
        unsupported_claims = self.unsupported_claims
        if unsupported_claims:
            display_status = "warning"
        elif self.status == "verified":
            display_status = "verified"
        else:
            # Content may be supported while citation markers are incomplete.
            # Keep collecting the metric without showing a misleading warning.
            display_status = "hidden"
        data: dict[str, Any] = {
            "status": self.status,
            "claim_count": self.facts_found,
            "supported_claims": self.facts_supported,
            "faithfulness": round(self.faithfulness, 4),
            "citation_precision": round(self.citation_precision, 4),
            "citation_recall": round(self.citation_recall, 4),
            "sources_used": self.sources_used,
            "unsupported_claims": unsupported_claims,
            "display_status": display_status,
            "citation_status": (
                "complete" if self.citation_recall >= 1.0 else "partial" if self.citation_recall > 0.0 else "missing"
            ),
        }
        if include_claims:
            data["claims"] = [asdict(claim) for claim in self.claims]
        return data


def _extract_facts(text: str) -> list[str]:
    """Extract factual claims from prose and Markdown list items."""
    normalized = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    normalized = re.sub(r"^\s{0,3}#{1,6}\s+.*$", " ", normalized, flags=re.MULTILINE)
    normalized = re.sub(
        r"^\s*\|.*\|\s*\n\s*\|\s*:?-{3,}.*\|\s*$",
        " ",
        normalized,
        flags=re.MULTILINE,
    )
    # Models sometimes emit "事实。 [S1]" despite the requested "事实 [S1]。".
    # Move that citation before sentence splitting so it remains claim-bound.
    normalized = _POST_SENTENCE_CITATION_RE.sub(r" \2\1", normalized)
    parts = re.split(r"(?<=[。！？!?；;])\s*|\n+", normalized)
    claims: list[str] = []
    for part in parts:
        claim = re.sub(r"^\s*(?:[-*+]\s+|\d+[.)、]\s*)", "", part).strip()
        if claim.startswith("|") and claim.endswith("|"):
            claim = "；".join(cell.strip() for cell in claim.strip("|").split("|") if cell.strip())
        # “已确认：”是回答结构标签，不是一个需要引用的事实。去掉标签，
        # 但保留同一行后面的真实声明供正常校验。
        claim = _CONFIRMED_LEAD_RE.sub("", claim).strip()
        # An evidence lead-in introduces a real claim. Strip the lead-in rather
        # than dropping the entire sentence, while preserving its citations.
        lead_match = _EVIDENCE_LEAD_RE.match(claim)
        if lead_match:
            lead = lead_match.group(0)
            lead_citations = " ".join(f"[{group}]" for group in _CITATION_RE.findall(lead))
            claim = f"{claim[lead_match.end() :].strip()} {lead_citations}".strip()
        plain = _CITATION_RE.sub("", claim)
        plain = re.sub(r"[*_`]+", "", plain).strip(" ：:。；;，,")
        if len(plain) < _MIN_CLAIM_LENGTH or plain.endswith(("?", "？")):
            continue
        if (
            plain.startswith(_META_PREFIXES)
            or plain.endswith(("资料事实", "已确认", "无法确认"))
            or _LIMITATION_RE.search(plain)
            or _LIMITATION_ANYWHERE_RE.search(plain)
        ):
            continue
        claims.append(claim)
    return claims


def _content_tokens(text: str) -> set[str]:
    """Tokenize mixed Chinese/Latin text without requiring an external model."""
    tokens: set[str] = set()
    for raw in _TOKEN_RE.findall(text.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", raw):
            if len(raw) == 1:
                tokens.add(raw)
            else:
                tokens.update(raw[i : i + 2] for i in range(len(raw) - 1))
        else:
            tokens.add(raw)
    return tokens


def _support_score(claim: str, source: str) -> float:
    claim_text = _CITATION_RE.sub("", claim)
    # Definitional glue is frequently introduced by paraphrasing but carries
    # no evidence-bearing meaning. Removing it avoids false negatives such as
    # “socarrat 是指……锅底焦香的部分” against evidence saying
    # “形成底部焦香的 socarrat”, without lowering the global support floor.
    claim_text = re.sub(r"是指|指的是|所指的是|的部分", "", claim_text)
    claim_tokens = _content_tokens(claim_text)
    source_tokens = _content_tokens(source)
    if not claim_tokens or not source_tokens:
        return 0.0
    return len(claim_tokens & source_tokens) / len(claim_tokens)


def _numbers(text: str) -> set[str]:
    return {match.group(0).lower() for match in _NUMBER_RE.finditer(text)}


def _normalize_evidence(sources: Sequence[EvidenceSource]) -> list[Evidence]:
    evidence: list[Evidence] = []
    for index, source in enumerate(sources, 1):
        if isinstance(source, Evidence):
            evidence.append(source)
            continue
        if isinstance(source, str):
            evidence.append(Evidence(citation_id=f"S{index}", text=source))
            continue
        citation_id = str(source.get("citation_id") or f"S{index}").upper()
        evidence.append(
            Evidence(
                citation_id=citation_id,
                text=str(source.get("text", "")),
                document_key=str(source.get("document_key", "")),
                section_key=str(source.get("section_key", "")),
                filename=str(source.get("filename", "")),
            )
        )
    return evidence


def _claim_citations(claim: str) -> list[str]:
    citations: list[str] = []
    for group in _CITATION_RE.findall(claim):
        for citation in re.split(r"\s*[,，]\s*", group.upper()):
            if citation and citation not in citations:
                citations.append(citation)
    return citations


def verify_answer(
    answer: str,
    sources: Sequence[EvidenceSource],
    *,
    min_coverage: float = 0.70,
) -> VerificationResult:
    """Verify answer claims against their cited source chunks.

    The function is deliberately deterministic and offline. A cited claim passes
    only when at least one cited source has sufficient token coverage and contains
    every numeric value present in the claim.
    """
    evidence = _normalize_evidence(sources)
    facts = _extract_facts(answer)
    if not evidence:
        return VerificationResult(facts_found=len(facts), status="no_sources")
    if not facts:
        return VerificationResult(sources_used=len(evidence), status="unverified")

    evidence_by_id = {item.citation_id: item for item in evidence}
    claim_results: list[ClaimVerification] = []
    cited_claims = 0
    valid_citation_count = 0
    supporting_citation_count = 0

    for fact in facts:
        citations = _claim_citations(fact)
        if citations:
            cited_claims += 1
        candidates = [evidence_by_id[c] for c in citations if c in evidence_by_id]
        valid_citation_count += len(candidates)
        if not citations:
            candidates = evidence

        best_score = 0.0
        supporting: list[str] = []
        missing_numbers: set[str] = set()
        fact_numbers = _numbers(_CITATION_RE.sub("", fact))
        for candidate in candidates:
            score = _support_score(fact, candidate.text)
            best_score = max(best_score, score)
            missing = fact_numbers - _numbers(candidate.text)
            if score >= _SUPPORT_THRESHOLD and not missing:
                supporting.append(candidate.citation_id)
                if citations:
                    supporting_citation_count += 1
            elif score == best_score:
                missing_numbers = missing

        # A synthesis claim can legitimately require several cited chunks.
        # Evaluate their union only after no individual source was sufficient;
        # uncited claims never receive this broader allowance.
        if citations and len(candidates) > 1 and not supporting:
            union_text = "\n".join(candidate.text for candidate in candidates)
            union_score = _support_score(fact, union_text)
            union_missing = fact_numbers - _numbers(union_text)
            best_score = max(best_score, union_score)
            if union_score >= _SUPPORT_THRESHOLD and not union_missing:
                fact_tokens = _content_tokens(_CITATION_RE.sub("", fact))
                supporting = [
                    candidate.citation_id
                    for candidate in candidates
                    if fact_tokens & _content_tokens(candidate.text) or fact_numbers & _numbers(candidate.text)
                ]
                supporting_citation_count += len(supporting)
            missing_numbers = union_missing

        supported = bool(supporting)
        if citations and not candidates:
            reason = "引用不存在"
        elif missing_numbers:
            reason = "证据缺少声明中的数字"
        elif not supported:
            reason = "证据与声明的内容覆盖不足"
        elif not citations:
            reason = "内容有证据支持，但声明缺少引用"
        else:
            reason = "已由引用证据支持"
        claim_results.append(
            ClaimVerification(
                claim=fact,
                citations=citations,
                supported=supported,
                support_score=round(best_score, 4),
                supporting_citations=supporting,
                missing_numbers=sorted(missing_numbers),
                reason=reason,
            )
        )

    supported_count = sum(1 for claim in claim_results if claim.supported)
    coverage = supported_count / len(claim_results)
    citation_recall = cited_claims / len(claim_results)
    citation_precision = supporting_citation_count / valid_citation_count if valid_citation_count else 0.0
    if coverage >= min_coverage and citation_recall >= min_coverage:
        status = "verified"
    elif supported_count:
        status = "partial"
    else:
        status = "unverified"

    result = VerificationResult(
        facts_found=len(claim_results),
        facts_supported=supported_count,
        coverage=coverage,
        sources_used=len(evidence),
        status=status,
        citation_precision=citation_precision,
        citation_recall=citation_recall,
        claims=claim_results,
    )
    logger.info(
        "answer verification status=%s faithfulness=%.0f%% citation_recall=%.0f%% claims=%d/%d",
        status,
        coverage * 100,
        citation_recall * 100,
        supported_count,
        len(claim_results),
    )
    return result


def _has_topical_evidence(query: str, sources: Sequence[EvidenceSource]) -> bool:
    """Conservatively detect whether retrieved evidence is on the query topic."""
    evidence_text = "\n".join(item.text for item in _normalize_evidence(sources))
    query_tokens = {token for token in _content_tokens(query) if token not in _QUERY_TOKEN_STOPWORDS and len(token) >= 2}
    if not query_tokens:
        return False
    overlap = query_tokens & _content_tokens(evidence_text)
    latin_overlap = {token for token in overlap if re.search(r"[a-z]", token)}
    return len(latin_overlap) >= 1 or len(overlap) >= 3


def _should_retry_topical_refusal(
    query: str,
    sources: Sequence[EvidenceSource],
) -> bool:
    """Retry only direct, evidence-backed questions—not synthesis requests.

    Derivation, formula, causality, responsibility, and prompt-injection
    requests often share topic words with retrieved chunks while the requested
    relation is absent. Comparison requests are allowed one retry because the
    safe response can still enumerate directly supported facts for each side
    before declaring the comparison dimension unavailable.
    """
    return bool(query and not _TOPICAL_RETRY_BLOCK_RE.search(query) and _has_topical_evidence(query, sources))


def apply_query_safety_guard(
    query: str,
    answer: str,
    *,
    has_context: bool = False,
) -> str:
    """Turn relation-missing answers into explicit, deterministic abstentions.

    A topically supported statement is not necessarily an answer to an
    unresolved follow-up or a superlative question.  This guard only acts when
    the generated answer failed to resolve the requested relation; it does not
    replace a model answer that already contains an explicit supported choice.
    """
    query_chars = [char.casefold() for char in query if char.isalnum()]
    is_low_information = len(query_chars) <= 1 or (len(query_chars) >= 8 and len(set(query_chars)) / len(query_chars) <= 0.25)
    if _REFUSAL_RE.search(answer):
        return answer
    if is_low_information:
        return "无法确认：问题缺少可识别的有效信息，请提供具体问题后再提问。"
    if _UNRESOLVED_REFERENCE_RE.search(query) or is_underspecified_query(query):
        return "无法确认：问题中的指代对象不明确，请说明具体对象后再提问。"
    if _SUPERLATIVE_QUERY_RE.search(query) and not _SUPERLATIVE_ANSWER_RE.search(answer):
        return "无法确认：现有资料没有给出所问对象之间的最高级比较结论。"
    if _CALCULATION_QUERY_RE.search(query) and not _CALCULATION_ANSWER_RE.search(answer):
        return "无法确认：现有资料没有给出该指标的计算公式或计算方法。"
    if not comparison_answer_complete(query, answer):
        return "无法确认：现有资料没有直接给出问题所要求的比较结论。"
    if _RESPONSIBILITY_QUERY_RE.search(query) and not _RESPONSIBILITY_ANSWER_RE.search(answer):
        return "无法确认：现有资料没有说明所问对象各自承担的职责。"
    if _CAUSAL_QUERY_RE.search(query) and not _CAUSAL_ANSWER_RE.search(answer):
        return "无法确认：现有资料没有直接给出问题所要求的原因。"
    if _IMPACT_QUERY_RE.search(query) and not _IMPACT_ANSWER_RE.search(answer):
        return "无法确认：现有资料没有直接说明所问影响。"
    if _RELATION_QUERY_RE.search(query) and not _RELATION_ANSWER_RE.search(answer):
        return "无法确认：现有资料没有直接说明所问关系。"
    return answer


def apply_zero_support_guard(
    answer: str,
    sources: Sequence[EvidenceSource],
) -> str:
    """Refuse instead of emitting a factual answer with zero supported claims."""
    verification = verify_answer(answer, sources)
    if verification.facts_found and verification.facts_supported == 0:
        return "无法确认：现有资料没有直接支持问题所要求的事实。"
    return answer


def build_partial_comparison_fallback(
    query: str,
    sources: Sequence[EvidenceSource],
) -> str | None:
    """Build conservative source-extractive facts for a refused comparison.

    The fallback copies at most one complete sentence from each of the two best
    matching sources and adds claim-level citations. It never invents the
    unavailable comparison relation.
    """
    if not query or not _COMPARISON_QUERY_RE.search(query):
        return None

    evidence = _normalize_evidence(sources)
    query_tokens = {token for token in _content_tokens(query) if token not in _QUERY_TOKEN_STOPWORDS and len(token) >= 2}
    if not query_tokens:
        return None

    candidates: list[tuple[int, int, int, str, Evidence]] = []
    for source_rank, item in enumerate(evidence):
        source_best: tuple[int, int, int, str, Evidence] | None = None
        section_tokens = _content_tokens(item.section_key)
        section_on_topic = bool(query_tokens & section_tokens)
        raw_units = re.split(r"(?<=[。！？!?；;])\s*|\n+", item.text)
        for unit_rank, raw_unit in enumerate(raw_units):
            sentence = re.sub(r"^\s*(?:#{1,6}\s*|[-*+]\s+)", "", raw_unit).strip()
            sentence = re.sub(r"^文档上下文[：:]\s*", "", sentence).strip()
            sentence = sentence.strip(" ：:。；;，,")
            if not (6 <= len(sentence) <= 220):
                continue
            if sentence == item.section_key or sentence.startswith("文档上下文"):
                continue
            if re.search(r"(?:指南|报告|手册|文档|概览)$", sentence):
                continue

            display_sentence = sentence
            sentence_tokens = _content_tokens(sentence)
            if section_on_topic and not (query_tokens & sentence_tokens & section_tokens):
                display_sentence = f"{item.section_key}：{sentence}"
                sentence_tokens |= section_tokens

            overlap = query_tokens & sentence_tokens
            latin_overlap = {token for token in overlap if re.search(r"[a-z]", token)}
            chinese_overlap = overlap - latin_overlap
            if not latin_overlap and len(chinese_overlap) < 2:
                continue
            score = len(latin_overlap) * 3 + len(chinese_overlap)
            candidate = (score, -source_rank, -unit_rank, display_sentence, item)
            if source_best is None or candidate[:3] > source_best[:3]:
                source_best = candidate
        if source_best is not None:
            candidates.append(source_best)

    if not candidates:
        return None

    selected = sorted(candidates, key=lambda item: item[:3], reverse=True)[:2]
    fact_lines = "\n".join(f"- {sentence} [{source.citation_id}]。" for _, _, _, sentence, source in selected)
    fallback = f"已确认：\n{fact_lines}\n无法确认：现有资料没有直接给出问题所要求的比较结论。"
    verification = verify_answer(fallback, evidence)
    if (
        verification.facts_supported < 1
        or verification.faithfulness < 1.0
        or verification.citation_precision < 1.0
        or verification.citation_recall < 1.0
    ):
        return None
    return fallback


def needs_grounding_repair(
    answer: str,
    sources: Sequence[EvidenceSource],
    *,
    query: str = "",
    coverage_recheck: bool = True,
) -> GroundingDecision:
    """Analyze grounding quality and return a structured repair decision.

    Returns GroundingDecision with action: accept, deterministic_repair, llm_repair, or refuse.
    deterministic_repair actions can be handled without an LLM call (citation format fixes).
    llm_repair actions require a second model generation.
    """
    if not sources:
        return GroundingDecision(action="accept", reasons=["no_sources"])

    verification = verify_answer(answer, sources)

    if _CLARIFICATION_RE.search(answer) and verification.facts_supported == 0:
        return GroundingDecision(
            action="accept",
            reasons=["clarification_refusal"],
            verification=verification,
        )

    if _FULL_REFUSAL_START_RE.search(answer):
        if not _CLARIFICATION_RE.search(answer) and _should_retry_topical_refusal(query, sources):
            return GroundingDecision(
                action="llm_repair",
                reasons=["topical_false_refusal"],
                verification=verification,
            )
        if query and _has_topical_evidence(query, sources):
            return GroundingDecision(
                action="accept",
                reasons=["topical_false_refusal"],
                verification=verification,
            )
        return GroundingDecision(action="accept", verification=verification)

    if verification.facts_found:
        reasons: list[str] = []

        # Citation format issues (deterministic fix possible)
        if verification.citation_recall < 1.0:
            reasons.append("missing_citation")
        if verification.citation_precision < 0.95:
            # Inspect claims for invalid/redundant citations
            for c in verification.claims:
                if c.reason == "引用不存在":
                    reasons.append("invalid_citation")
                    break
            else:
                reasons.append("redundant_citation")

        # Content grounding issues (may need LLM)
        if verification.faithfulness < 1.0:
            for c in verification.claims:
                if not c.supported:
                    if c.missing_numbers:
                        reasons.append("missing_number")
                    elif c.reason == "证据与声明的内容覆盖不足":
                        reasons.append("unsupported_claim")
                    else:
                        reasons.append("unsupported_claim")

        # Coverage recheck: fully-grounded one-line answers may still
        # omit directly relevant categories/examples in substantive evidence.
        # Let the LLM try to expand, but acceptance is decided by verifier.
        evidence_length = sum(len(item.text) for item in _normalize_evidence(sources))
        if (
            coverage_recheck
            and query
            and _COVERAGE_QUERY_RE.search(query.strip())
            and verification.facts_supported < 2
            and evidence_length >= 100
        ):
            reasons.append("coverage_recheck")

        # Classify: format-only vs. content problems
        format_reasons = {"missing_citation", "invalid_citation", "redundant_citation"}
        content_reasons = {"unsupported_claim", "missing_number"}
        coverage_reasons = {"coverage_recheck"}

        has_content_issue = bool(set(reasons) & content_reasons)
        has_coverage_issue = bool(set(reasons) & coverage_reasons)
        only_format_issues = set(reasons).issubset(format_reasons)

        if not reasons:
            return GroundingDecision(action="accept", verification=verification)
        elif only_format_issues:
            return GroundingDecision(
                action="deterministic_repair",
                reasons=reasons,
                verification=verification,
            )
        elif has_content_issue or has_coverage_issue:
            return GroundingDecision(
                action="llm_repair",
                reasons=reasons,
                verification=verification,
            )
        else:
            return GroundingDecision(action="accept", verification=verification)

    # Non-leading refusal language remains diagnostic only. It may be a valid
    # limitation following supported facts and must not force regeneration.
    if _REFUSAL_RE.search(answer) and query and _has_topical_evidence(query, sources):
        return GroundingDecision(
            action="accept",
            reasons=["topical_false_refusal"],
            verification=verification,
        )

    return GroundingDecision(action="accept", verification=verification)


def grounding_repair_instruction(answer: str) -> str:
    """Build the single-pass correction request used by eval and production."""
    return (
        "上一个回答草稿未通过知识库声明级校验。请仅依据已有检索内容重新输出完整最终答案，"
        "不要调用工具。若任何来源能直接支持问题的一部分，必须先输出单独一行“已确认：”，"
        "再用可直接渲染的 Markdown 逐条回答，不能整体拒答；不要把答案包在代码围栏中。"
        "比较题使用三级标题加列表；如果使用表格，表格行前禁止添加列表标记。"
        "其余部分最后写“无法确认：……”。每个列表项只写一个"
        "原子事实，只用一个完整支持该事实的最小来源编号，并把引用放在句号前。不要使用分号"
        "连接多个事实，也不要在引用后另起一行放句号。若确实没有任何可直接回答的事实，保持"
        "整体拒答。删除所有无法由所引来源直接找到的内容。\n\n待纠正草稿：\n"
        f"{answer}"
    )


def select_better_grounded_answer(
    original: str,
    repaired: str,
    sources: Sequence[EvidenceSource],
) -> str:
    """Keep a safe repair without needlessly collapsing supported coverage."""
    if not repaired.strip():
        return original

    original_result = verify_answer(original, sources)
    repaired_result = verify_answer(repaired, sources)

    if original_result.facts_supported >= 2 and repaired_result.facts_supported < original_result.facts_supported:
        supported_lines: list[str] = []
        for claim in original_result.claims:
            if not claim.supported or not claim.supporting_citations:
                continue
            plain = _CITATION_RE.sub("", claim.claim).strip(" ：:。；;，,")
            if not plain:
                continue
            citations = ", ".join(claim.supporting_citations)
            supported_lines.append(f"- {plain} [{citations}]。")
        if len(supported_lines) >= 2:
            supported_answer = "已确认：\n" + "\n".join(supported_lines)
            supported_result = verify_answer(supported_answer, sources)
            if (
                supported_result.facts_supported > repaired_result.facts_supported
                and supported_result.faithfulness == 1.0
                and supported_result.citation_recall == 1.0
                and supported_result.citation_precision == 1.0
            ):
                return supported_answer

    def quality(result: VerificationResult) -> tuple[float, float, float, int]:
        return (
            result.faithfulness,
            result.citation_recall,
            result.citation_precision,
            result.facts_supported,
        )

    return repaired if quality(repaired_result) > quality(original_result) else original
