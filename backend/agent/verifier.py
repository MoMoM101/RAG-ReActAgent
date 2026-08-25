"""Deterministic claim-to-evidence verification for knowledge-base answers."""

from __future__ import annotations

import ast
import logging
import math
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
    is_conditional_decision_query,
)

logger = logging.getLogger(__name__)

_MIN_CLAIM_LENGTH = 6
_SUPPORT_THRESHOLD = 0.28
_CITATION_ID_PATTERN = r"(?:WS|S)\d+"
_CITATION_RE = re.compile(
    rf"\[({_CITATION_ID_PATTERN}(?:\s*[,，]\s*{_CITATION_ID_PATTERN})*)\]",
    re.IGNORECASE,
)
_POST_SENTENCE_CITATION_RE = re.compile(
    rf"([。！？!?；;])\s*(\[{_CITATION_ID_PATTERN}(?:\s*[,，]\s*{_CITATION_ID_PATTERN})*\])",
    re.IGNORECASE,
)
_ORPHAN_CITATION_TAIL_RE = re.compile(
    rf"(?<=[。！？!?；;])\s*(?:\[{_CITATION_ID_PATTERN}(?:\s*[,，]\s*{_CITATION_ID_PATTERN})*\]\s*){{2,}}(?=$|\n)",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9])(?<![A-Za-z]-)\d+(?:\.\d+)*"
    r"(?:\s*(?:%|℃|°C|ms|s|MB|GB|mg|V))?",
    re.IGNORECASE,
)
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
    "最后，我们来计算",
)
_CALCULATION_META_RE = re.compile(
    r"^(?:根据)?(?:知识库|资料).{0,24}计算器.{0,12}计算结果.{0,24}(?:可以|可)得出"
)
_CALCULATION_SUMMARY_LEAD_RE = re.compile(
    r"^(?:根据知识库中的信息[，,]?)?以下是计算.+(?:步骤|结果)"
)
_NON_FACTUAL_TRANSITION_RE = re.compile(
    r"^(?:根据(?:知识库|资料)中的信息[，,]?\s*)?(?:我们)?"
    r"(?:找到|找到了|获取|获取了)(?:相关|以下|对应)?(?:的)?(?:信息|资料|定价详情)[。.]?$|"
    r"^下面是根据.+(?:来计算|进行计算).+$|"
    r"^现在(?:我们)?可以(?:开始)?计算"
)
_NON_FACTUAL_REQUEST_RE = re.compile(
    r"^请(?:客户|用户|您)?(?:提供|补充|上传|联系|咨询|确认|核实|查阅|参考)"
)
_NON_FACTUAL_ADVICE_RE = re.compile(
    r"^(?:对于.{0,32}[，,]\s*)?建议(?:您|客户|用户)?"
    r"(?:联系|查阅|咨询|提供|补充|上传|确认|核实|参考)"
)
_RUNTIME_FALLBACK_RE = re.compile(
    r"^(?:本轮检索已完成，但计算器步骤未完整执行|"
    r"已获得\s*\d+\s*个可信计算结果|请重试本问题)"
)
_ANAPHORIC_EVIDENCE_RE = re.compile(
    r"^(?:这些|上述|相关)(?:信息|内容|资料).{0,32}"
    r"(?:未|没有|无法).{0,16}(?:提供|找到|确认)"
)
_SOURCE_ATTRIBUTION_SUFFIX_RE = re.compile(
    r"(?:以上|上述|这些|相关)?信息(?:均|主要)?来源于"
    r".{1,48}(?:文档|文件|资料|知识库)[。.]?\s*$"
)
_SOURCE_SECTION_HEADING_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*{1,2}|_{1,2})?\s*"
    r"(?:(?:参考|引用|资料|信息)?来源(?:列表)?|sources?|references?)"
    r"\s*[:：]?\s*(?:\*{1,2}|_{1,2})?\s*$",
    re.IGNORECASE,
)
_SOURCE_DESCRIPTOR_RE = re.compile(
    r"(?:https?://|www\.|`[^`]+`|\[[^]]+\]\([^)]+\)|"
    r"\.(?:md|txt|pdf|docx?|xlsx?|pptx?|csv|html?|json|ya?ml)\b|"
    r"文件|文档|资料|来源|链接|知识库|网页|页面|章节|内容|file|document|source|link)",
    re.IGNORECASE,
)
_LIMITATION_RE = re.compile(
    r"^(?:但)?(?:现有)?资料(?:中)?(?:不足|未|没有)"
    r"|^.+(?:未在|没有在)(?:现有)?资料(?:中)?(?:提及|说明|提供)"
    r"|^.+(?:现有)?资料(?:中)?(?:未|没有)(?:提及|说明|提供|涉及)",
)
_EVIDENCE_LEAD_RE = re.compile(
    r"^(?:根据|参考)(?:现有)?(?:检索)?"
    r"(?:来源|资料|内容|知识库(?:中的)?信息)"
    rf"(?:\s*\[{_CITATION_ID_PATTERN}(?:\s*[,，]\s*{_CITATION_ID_PATTERN})*\])?[，,:：]?\s*",
    re.IGNORECASE,
)
_CONFIRMED_LEAD_RE = re.compile(r"^已确认[：:]\s*")
_LIMITATION_ANYWHERE_RE = re.compile(
    r"(?:现有)?资料(?:中)?不足以|无法(?:直接)?(?:确认|确定|比较|回答)|"
    r"(?:知识库|资料|文档|检索结果).{0,16}(?:未|没有|并未)(?:提供|说明|提及|包含|找到)|"
    r"(?:未|没有|并未)(?:在.{0,12})?(?:提供|说明|提及|包含)(?:该|这些|相关|具体)?信息"
)
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
_CONDITIONAL_CONCLUSION_RE = re.compile(
    r"(?:结论[：:]\s*)?(?:是|否|不是|不必然|并非必然|不一定|不会自动|不能仅凭|取决于)|"
    r"(?:只有|除非).{0,32}才|(?:满足|不满足).{0,24}(?:计入|排除|算作)"
)
_IDENTIFIER_REQUEST_RE = re.compile(
    r"哪一级|(?:证书|备案|登记|许可|认证|报告|文档)?编号|备案号|分别是什么|是什么[？?]?$"
)
_MISSING_INFORMATION_EVIDENCE_RE = re.compile(
    r"不包含|未提供|没有提供|未给出|缺少资料|资料缺失|无法提供"
)
_MISSING_INFORMATION_ANSWER_RE = re.compile(
    r"无法确认|无法提供|未能找到|未找到|未提供|没有提供|未给出|不包含|缺少资料|资料缺失"
)
_REQUIRED_CONDITION_CUE_RE = re.compile(r"(?:必须|需要)同时满足(?:以下)?条件|同时满足以下条件")
_NUMBERED_CONDITION_RE = re.compile(
    r"(?:^|[；;\n])\s*\d+[）)]\s*(.+?)(?=(?:[；;\n]\s*\d+[）)])|$)",
    re.DOTALL,
)


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


def _required_condition_clauses(sources: Sequence[EvidenceSource]) -> list[str]:
    """Extract an explicit all-required numbered condition set from evidence."""
    for item in _normalize_evidence(sources):
        cue = _REQUIRED_CONDITION_CUE_RE.search(item.text)
        if not cue:
            continue
        clauses = [
            match.group(1).strip(" ：:。；;，,\n")
            for match in _NUMBERED_CONDITION_RE.finditer(item.text[cue.end() :])
        ]
        clauses = [clause for clause in clauses if len(clause) >= 6]
        if len(clauses) >= 2:
            return clauses
    return []


def conditional_answer_complete(
    query: str,
    answer: str,
    sources: Sequence[EvidenceSource] = (),
) -> bool:
    """Require an explicit verdict and every stated all-required condition."""
    if not is_conditional_decision_query(query) or _REFUSAL_RE.search(answer):
        return True
    if not _CONDITIONAL_CONCLUSION_RE.search(answer):
        return False
    plain_answer = _CITATION_RE.sub("", answer)
    return all(_support_score(clause, plain_answer) >= 0.28 for clause in _required_condition_clauses(sources))


def missing_information_answer_complete(
    query: str,
    answer: str,
    sources: Sequence[EvidenceSource] = (),
) -> bool:
    """Preserve an explicit absence relation when evidence is a boundary note."""
    if not _has_relevant_missing_information_boundary(query, sources):
        return True
    return bool(_MISSING_INFORMATION_ANSWER_RE.search(answer))


def _has_relevant_missing_information_boundary(
    query: str,
    sources: Sequence[EvidenceSource],
) -> bool:
    """Return whether evidence explicitly says requested identifiers are absent."""
    return bool(relevant_missing_information_boundaries(query, sources))


def relevant_missing_information_boundaries(
    query: str,
    sources: Sequence[EvidenceSource],
) -> list[Evidence]:
    """Return evidence that explicitly defines a boundary for requested identifiers."""
    if not query or not _IDENTIFIER_REQUEST_RE.search(query):
        return []
    return [
        item
        for item in _normalize_evidence(sources)
        if (
        _MISSING_INFORMATION_EVIDENCE_RE.search(item.text)
        and _support_score(query, item.text) >= 0.28
        )
    ]


def is_missing_information_statement(text: str) -> bool:
    """Return whether a claim explicitly states that requested information is absent."""
    return bool(_MISSING_INFORMATION_ANSWER_RE.search(_CITATION_RE.sub("", text)))


def is_missing_information_evidence(text: str) -> bool:
    """Return whether evidence itself explicitly states an information boundary."""
    return bool(_MISSING_INFORMATION_EVIDENCE_RE.search(text))


def _safe_arithmetic_value(expression: str) -> float | None:
    """Evaluate the calculator's arithmetic subset without executing code."""
    try:
        node = ast.parse(expression, mode="eval").body
    except SyntaxError:
        return None

    def evaluate(item: ast.expr) -> float:
        if isinstance(item, ast.Constant) and isinstance(item.value, int | float) and not isinstance(item.value, bool):
            return float(item.value)
        if isinstance(item, ast.UnaryOp) and isinstance(item.op, ast.USub):
            return -evaluate(item.operand)
        if isinstance(item, ast.BinOp) and isinstance(item.op, ast.Add | ast.Sub | ast.Mult | ast.Div):
            left = evaluate(item.left)
            right = evaluate(item.right)
            if isinstance(item.op, ast.Add):
                return left + right
            if isinstance(item.op, ast.Sub):
                return left - right
            if isinstance(item.op, ast.Mult):
                return left * right
            return left / right
        raise ValueError

    try:
        return evaluate(node)
    except (ValueError, ZeroDivisionError, OverflowError):
        return None


def _arithmetic_expression(text: str) -> str:
    normalized = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", text)
    normalized = re.sub(
        r"(\d+(?:\.\d+)?)\s*%",
        lambda match: f"({match.group(1)}/100)",
        normalized,
    )
    normalized = normalized.replace("×", "*").replace("÷", "/")
    # Only parse the trailing arithmetic expression before ``=``.  Removing
    # prose from the whole segment would concatenate unrelated numbers from a
    # label (for example "1 年订阅 ... 2540 * 12") into the expression.
    normalized = normalized.replace("**", "").replace("__", "")
    match = re.search(r"[\d().+\-*/\s]+$", normalized)
    if not match:
        return ""
    return "".join(re.findall(r"\d+(?:\.\d+)?|[+\-*/()]", match.group(0)))


def _valid_calculation_results(text: str) -> set[float]:
    """Collect results that are demonstrated by a valid visible equation."""
    results: set[float] = set()
    normalized = text.replace("＝", "=")
    for line in re.split(r"[。！？!?\n]+", normalized):
        segments = line.split("=")
        if len(segments) < 2:
            continue
        for index, segment in enumerate(segments):
            expression = _arithmetic_expression(segment)
            if not expression or not re.search(r"[+\-*/]", expression):
                continue
            value = _safe_arithmetic_value(expression)
            if value is None:
                continue
            if index + 1 < len(segments):
                right_numbers = _numbers(segments[index + 1].replace(",", ""))
                matches_value = any(
                    abs(float(number.rstrip("%")) - value) < 1e-6
                    for number in right_numbers
                    if re.fullmatch(r"\d+(?:\.\d+)?", number)
                )
                if not matches_value:
                    continue
            results.add(value)
    return results


def _valid_calculation_numbers(text: str) -> set[str]:
    """Return operands/results that belong to a demonstrated valid equation."""
    numbers: set[str] = set()
    normalized = text.replace("＝", "=")
    for line in re.split(r"[。！？!?\n]+", normalized):
        segments = line.split("=")
        if len(segments) < 2:
            continue
        for index, segment in enumerate(segments[:-1]):
            expression = _arithmetic_expression(segment)
            if not expression or not re.search(r"[+\-*/]", expression):
                continue
            value = _safe_arithmetic_value(expression)
            if value is None:
                continue
            right_numbers = _numbers(segments[index + 1].replace(",", ""))
            if not any(
                abs(float(number.rstrip("%")) - value) < 1e-6
                for number in right_numbers
                if re.fullmatch(r"\d+(?:\.\d+)?", number)
            ):
                continue
            numbers.update(_numbers(expression))
            numbers.update(right_numbers)
    return numbers


def _is_derived_calculation_claim(claim: str, results: set[float]) -> bool:
    if not results:
        return False

    plain = _CITATION_RE.sub("", claim).replace("`", "").strip(" ：:。；;，,")
    claim_numbers = {
        float(number.replace(",", ""))
        for number in re.findall(r"\d[\d,]*(?:\.\d+)?", plain)
    }

    # Markdown renderers may place an inline arithmetic expression and its
    # explanatory "结果是 ..." text on separate lines. Both are calculator
    # provenance, not independent knowledge-base claims.
    arithmetic_only = plain.replace("×", "*").replace("÷", "/")
    if re.fullmatch(r"[\d\s,.()+\-*/]+", arithmetic_only):
        value = _safe_arithmetic_value(
            _arithmetic_expression(arithmetic_only),
        )
        if value is not None and any(
            math.isclose(value, result, rel_tol=1e-9, abs_tol=1e-6)
            for result in results
        ):
            return True
    if re.match(r"^(?:该)?(?:表达式)?的?结果(?:是|为)", plain) and any(
        math.isclose(number, result, rel_tol=1e-9, abs_tol=1e-6)
        for number in claim_numbers
        for result in results
    ):
        return True

    if not re.search(
        r"=|计算|合计|总额|总预算|原价|年费|年度费用|"
        r"(?:实际|折后|年度|平台)订阅费|折后|折扣|小计",
        claim,
    ):
        return False
    return any(
        math.isclose(number, result, rel_tol=1e-9, abs_tol=1e-6)
        for number in claim_numbers
        for result in results
    )


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
        if unsupported_claims or (
            self.facts_found > 0
            and self.citation_recall > 0.0
            and self.citation_precision < 0.95
        ):
            display_status = "warning"
        elif self.status == "verified" or (
            self.facts_found > 0
            and self.faithfulness >= 1.0
            and self.citation_precision >= 1.0
            and self.citation_recall > 0.0
        ):
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


def _source_display_line(line: str, *, explicit_section: bool) -> bool:
    """Return whether a line is a citation legend entry, not answer prose."""
    candidate = re.sub(r"^\s*(?:>\s*)?(?:[-*+]\s+|\d+[.)、]\s*)", "", line).strip()
    if candidate.startswith("|") and candidate.endswith("|"):
        candidate = candidate.strip("|").strip()
    citation = _CITATION_RE.match(candidate)
    if not citation:
        return False
    remainder = candidate[citation.end() :].strip(" \t|:：-–—")
    if not remainder:
        return True
    if _SOURCE_DESCRIPTOR_RE.search(remainder):
        return True
    # Explicit sections may use a plain document title. Accept only a compact
    # title-shaped value; numbers and sentence punctuation make it answer prose
    # that must remain visible to the verifier.
    return (
        explicit_section
        and len(remainder) <= 80
        and not _NUMBER_RE.search(remainder)
        and not remainder.endswith(("。", ".", "！", "!", "？", "?", "；", ";"))
    )


def _strip_trailing_source_display(text: str) -> str:
    """Separate answer prose from an optional trailing source-display area.

    Source cards are already carried in structured response data. Models may
    repeat them as Markdown headings, citation legends, or a provenance outro.
    Remove that presentation-only suffix only when cited answer prose precedes
    it. This keeps standalone answers about filenames and documents verifiable.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")

    for index in range(len(lines) - 1, -1, -1):
        if not _SOURCE_SECTION_HEADING_RE.fullmatch(lines[index]):
            continue
        body = "\n".join(lines[:index]).rstrip()
        source_lines = [line for line in lines[index + 1 :] if line.strip()]
        if (
            body
            and _CITATION_RE.search(body)
            and source_lines
            and all(_source_display_line(line, explicit_section=True) for line in source_lines)
        ):
            normalized = body
        break

    # Also accept an unheaded, blank-line-separated legend. Requiring both a
    # cited body and file/link-shaped entries makes this intentionally stricter
    # than an explicit Sources section.
    paragraphs = re.split(r"\n\s*\n", normalized.rstrip())
    if len(paragraphs) > 1:
        body = "\n\n".join(paragraphs[:-1]).rstrip()
        source_lines = [line for line in paragraphs[-1].split("\n") if line.strip()]
        if (
            _CITATION_RE.search(body)
            and source_lines
            and all(_source_display_line(line, explicit_section=False) for line in source_lines)
        ):
            normalized = body

    # A final provenance sentence is display metadata only if cited factual
    # content comes before it. The same sentence remains a claim when it is the
    # answer itself (for example, to "which document contains this?").
    outro = _SOURCE_ATTRIBUTION_SUFFIX_RE.search(normalized)
    if outro:
        body = normalized[: outro.start()].rstrip()
        if _CITATION_RE.search(body):
            normalized = body

    return normalized


def _extract_facts(
    text: str,
    calculation_results: set[float] | None = None,
    *,
    score_missing_information: bool = False,
) -> list[str]:
    """Extract factual claims from prose and Markdown list items."""
    normalized = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    normalized = _strip_trailing_source_display(normalized)
    normalized = re.sub(r"^\s{0,3}#{1,6}\s+.*$", " ", normalized, flags=re.MULTILINE)
    normalized = re.sub(
        r"^\s*\|.*\|\s*\n\s*\|\s*:?-{3,}.*\|\s*$",
        " ",
        normalized,
        flags=re.MULTILINE,
    )
    # A citation-only dump after a completed sentence is not claim-level
    # evidence. Do not bind it to the preceding claim or let it inflate recall.
    normalized = _ORPHAN_CITATION_TAIL_RE.sub("", normalized)
    # Models sometimes emit "事实。 [S1]" despite the requested "事实 [S1]。".
    # Move that citation before sentence splitting so it remains claim-bound.
    normalized = _POST_SENTENCE_CITATION_RE.sub(r" \2\1", normalized)
    parts = re.split(r"(?<=[。！？!?；;])\s*|\n+", normalized)
    claims: list[str] = []
    in_limitation_section = False
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
        # Bold list labels such as ``**平台订阅**:`` describe answer
        # structure; they are not independently checkable factual claims.
        if (
            not _claim_citations(claim)
            and not _numbers(claim)
            and re.fullmatch(r"\*{1,2}[^*]+\*{1,2}\s*[:：]?", claim)
        ):
            continue
        plain = _CITATION_RE.sub("", claim)
        plain = re.sub(r"[*_`]+", "", plain).strip(" ：:。；;，,")
        # Comparison answers often use a structural heading followed by one
        # or more bullets that describe what the sources cannot establish.
        # Those bullets are limitations, not factual claims to ground. Keep
        # the section state until another confirmed-facts heading begins.
        if not _claim_citations(claim) and plain.endswith("资料事实"):
            in_limitation_section = False
            continue
        if plain.startswith(("无法确认的", "无法确认：", "无法确认:")):
            in_limitation_section = True
            continue
        if in_limitation_section:
            continue
        # Bind a citation-bearing anaphoric sentence back to the immediately
        # preceding concrete claim. Example: "未找到 A、B、C。这些信息未被
        # 提供 [S7]。" The second sentence carries evidence for the first but
        # has too little lexical content to verify independently.
        if (
            claims
            and _claim_citations(claim)
            and _ANAPHORIC_EVIDENCE_RE.search(plain)
            and not _claim_citations(claims[-1])
        ):
            previous = claims[-1].rstrip()
            punctuation = "。" if previous.endswith("。") else ""
            previous = previous.rstrip("。！？!?；;")
            citation_markers = " ".join(
                f"[{group}]" for group in _CITATION_RE.findall(claim)
            )
            claims[-1] = f"{previous} {citation_markers}{punctuation}".strip()
            continue
        if len(plain) < _MIN_CLAIM_LENGTH or plain.endswith(("?", "？")):
            continue
        if (
            plain.startswith(_META_PREFIXES)
            or _CALCULATION_META_RE.search(plain)
            or _CALCULATION_SUMMARY_LEAD_RE.search(plain)
            or _NON_FACTUAL_TRANSITION_RE.search(plain)
            or _NON_FACTUAL_REQUEST_RE.search(plain)
            or _NON_FACTUAL_ADVICE_RE.search(plain)
            or _RUNTIME_FALLBACK_RE.search(plain)
            or plain.endswith(("资料事实", "已确认", "无法确认"))
            or (
                (_LIMITATION_RE.search(plain) or _LIMITATION_ANYWHERE_RE.search(plain))
                and not (
                    score_missing_information
                    and is_missing_information_statement(plain)
                )
            )
        ):
            continue
        if _is_derived_calculation_claim(claim, calculation_results or set()):
            continue
        claims.append(claim)

    # A compact summary often repeats a sourced input already cited above.
    # Requiring the same inline citation a second time makes citation recall
    # look worse without finding a real grounding problem.
    cited_claims = [claim for claim in claims if _claim_citations(claim)]
    deduplicated: list[str] = []
    for claim in claims:
        if not _claim_citations(claim):
            claim_numbers = _numbers(claim)
            if claim_numbers and any(
                claim_numbers == _numbers(cited)
                and _support_score(claim, cited) >= 0.45
                for cited in cited_claims
            ):
                continue
        deduplicated.append(claim)
    return deduplicated


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


def _number_subset_of(claim_nums: set[str], source_nums: set[str]) -> set[str]:
    """Return claim numbers NOT found in source, with version prefix matching.

    A claim number like "3.12" is considered present if the source contains
    "3.12.0" or any number starting with "3.12." (version-like patterns).
    """
    missing: set[str] = set()
    for cn in claim_nums:
        if cn in source_nums:
            continue
        # Version prefix: "3.12" matches "3.12.0", "3.12.1", etc.
        if "." in cn and any(sn.startswith(cn + ".") for sn in source_nums):
            continue
        # Percentage and decimal forms are equivalent: 10% == 0.1.
        try:
            claim_is_percent = cn.endswith("%")
            claim_value = float(cn.rstrip("%")) / (100 if claim_is_percent else 1)
            if any(
                abs(
                    claim_value
                    - float(sn.rstrip("%")) / (100 if sn.endswith("%") else 1)
                )
                < 1e-9
                for sn in source_nums
            ):
                continue
        except ValueError:
            pass
        missing.add(cn)
    return missing


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


def _evidence_support_text(item: Evidence) -> str:
    """Combine trustworthy source metadata and body text for verification.

    Document inventory answers often mention a filename. Source cards expose
    that filename and section, so they must participate in grounding checks.
    Otherwise numeric prefixes such as ``01_`` look like invented numbers.
    """
    return "\n".join(
        value
        for value in (
            item.filename,
            item.document_key,
            item.section_key,
            item.text,
        )
        if value
    )


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
    query: str = "",
    calculation_results: Sequence[int | float] = (),
) -> VerificationResult:
    """Verify answer claims against their cited source chunks.

    The function is deliberately deterministic and offline. A cited claim passes
    only when at least one cited source has sufficient token coverage and contains
    every numeric value present in the claim.
    """
    evidence = _normalize_evidence(sources)
    trusted_results = _valid_calculation_results(answer) | {
        float(value)
        for value in calculation_results
        if isinstance(value, int | float) and not isinstance(value, bool)
    }
    facts = _extract_facts(
        answer,
        trusted_results,
        score_missing_information=_has_relevant_missing_information_boundary(
            query,
            evidence,
        ),
    )
    if not missing_information_answer_complete(query, answer, evidence):
        facts.append("回答缺少来源要求保留的‘未提供或无法确认’结论。")
    trusted_numbers = _numbers(query) | _valid_calculation_numbers(answer)
    trusted_numbers.update(
        _numbers(
            " ".join(
                str(value)
                for value in calculation_results
                if isinstance(value, int | float) and not isinstance(value, bool)
            )
        )
    )
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
        missing_information_claim = is_missing_information_statement(fact)
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
            candidate_text = _evidence_support_text(candidate)
            support_text = f"{candidate_text}\n{query}" if query else candidate_text
            score = _support_score(fact, support_text)
            # Merely mentioning the same product or field does not prove that
            # the requested value is absent. The cited evidence must state the
            # information boundary explicitly.
            if (
                missing_information_claim
                and not is_missing_information_evidence(candidate_text)
            ):
                score = 0.0
            best_score = max(best_score, score)
            missing = _number_subset_of(
                fact_numbers,
                _numbers(candidate_text) | trusted_numbers,
            )
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
            union_text = "\n".join(_evidence_support_text(candidate) for candidate in candidates)
            union_score = _support_score(fact, union_text)
            union_missing = _number_subset_of(fact_numbers, _numbers(union_text))
            best_score = max(best_score, union_score)
            if union_score >= _SUPPORT_THRESHOLD and not union_missing:
                fact_tokens = _content_tokens(_CITATION_RE.sub("", fact))
                supporting = [
                    candidate.citation_id
                    for candidate in candidates
                    if fact_tokens & _content_tokens(_evidence_support_text(candidate))
                    or fact_numbers & _numbers(_evidence_support_text(candidate))
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
    return bool(
        query
        and not _TOPICAL_RETRY_BLOCK_RE.search(query)
        and not _SUPERLATIVE_QUERY_RE.search(query)
        and _has_topical_evidence(query, sources)
    )


def apply_query_safety_guard(
    query: str,
    answer: str,
    *,
    has_context: bool = False,
) -> str:
    """Return answer unchanged. Relation checks are now handled by the model's
    own ReAct reasoning and post-hoc grounding verification.

    Previously this function replaced answers with fixed refusal templates when
    the answer lacked expected semantic patterns. Now it trusts the model to
    self-correct based on grounding repair feedback.
    """
    return answer


def apply_zero_support_guard(
    answer: str,
    sources: Sequence[EvidenceSource],
    *,
    query: str = "",
    calculation_results: Sequence[int | float] = (),
) -> str:
    """Refuse instead of emitting a factual answer with zero supported claims."""
    verification = verify_answer(
        answer,
        sources,
        query=query,
        calculation_results=calculation_results,
    )
    if verification.facts_found and verification.facts_supported == 0:
        return "无法确认：现有资料没有直接支持问题所要求的事实。"
    return answer


def build_partial_comparison_fallback(
    query: str,
    sources: Sequence[EvidenceSource],
) -> str | None:
    """Build conservative source-extractive facts for a refused comparison.

    The fallback copies up to four strongly matching complete source sentences
    and adds claim-level citations. This lets each side retain both defining
    facts and a directly stated procedure while never inventing the unavailable
    comparison relation.
    """
    if not query or not _COMPARISON_QUERY_RE.search(query):
        return None

    evidence = _normalize_evidence(sources)
    query_tokens = {token for token in _content_tokens(query) if token not in _QUERY_TOKEN_STOPWORDS and len(token) >= 2}
    if not query_tokens:
        return None

    candidates: list[tuple[int, int, int, str, Evidence]] = []
    for source_rank, item in enumerate(evidence):
        section_tokens = _content_tokens(item.section_key)
        section_on_topic = bool(query_tokens & section_tokens)
        normalized_text = re.sub(
            r"(?<![。！？!?；;])\n(?=\S)",
            " ",
            item.text,
        )
        raw_units = re.split(r"(?<=[。！？!?；;])\s*|\n+", normalized_text)
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
            detail_bonus = (
                5
                if re.search(r"(?:关键|步骤|工具|形成|方式|流程)", sentence)
                else 0
            )
            score = (
                len(latin_overlap) * 4
                + len(chinese_overlap) * 2
                + len(query_tokens & section_tokens) * 5
                + detail_bonus
            )
            candidate = (score, -source_rank, -unit_rank, display_sentence, item)
            candidates.append(candidate)

    if not candidates:
        return None

    ranked = sorted(candidates, key=lambda item: item[:3], reverse=True)
    by_section: dict[str, list[tuple[int, int, int, str, Evidence]]] = {}
    for candidate in ranked:
        source = candidate[4]
        section_group = source.section_key.casefold() or source.citation_id.casefold()
        by_section.setdefault(section_group, []).append(candidate)
    best_sections = sorted(
        by_section.values(),
        key=lambda items: items[0][:3],
        reverse=True,
    )[:2]
    section_selections: list[tuple[int, int, int, str, Evidence]] = []
    for section in best_sections:
        chosen = [section[0]]
        first_citation = section[0][4].citation_id
        distinct_source = next(
            (
                candidate
                for candidate in section[1:]
                if candidate[4].citation_id != first_citation
            ),
            None,
        )
        if distinct_source is not None:
            chosen.append(distinct_source)
        elif len(section) > 1:
            chosen.append(section[1])
        section_selections.extend(chosen)
    selected = sorted(
        section_selections,
        key=lambda item: item[:3],
        reverse=True,
    )
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


def build_topical_evidence_fallback(
    query: str,
    sources: Sequence[EvidenceSource],
) -> str | None:
    """Extract conservative supported facts after a false topical refusal.

    This counterpart to the comparison fallback is limited to direct
    definition queries and explicit structured requests (lists, steps,
    workflows and summaries). It copies complete source sentences, cites
    them, and explicitly leaves unsupported conclusions unanswered.
    """
    if not _should_retry_topical_refusal(query, sources):
        return None

    normalized_query = re.sub(r"\s+", " ", query).strip(" \t\r\n。！？?!")
    is_direct_definition = bool(
        re.fullmatch(
            r"(?:什么是\s*.+|.+?(?:是什么|是指什么|的定义是什么|什么意思))",
            normalized_query,
            flags=re.IGNORECASE,
        )
        or re.fullmatch(
            r"(?:what\s+(?:is|are)|define)\s+[^,;:?!]{1,64}",
            normalized_query,
            flags=re.IGNORECASE,
        )
    )
    is_structured_request = bool(
        re.search(
            r"(?:列出|罗列|步骤|流程|汇总|总结|概括|介绍)",
            normalized_query,
        )
    )
    is_workflow_request = bool(
        re.search(r"(?:流程|从.+到)", normalized_query)
    )
    if (
        (not is_direct_definition and not is_structured_request)
        or len(normalized_query) > 100
    ):
        return None

    evidence = _normalize_evidence(sources)
    query_tokens = {
        token
        for token in _content_tokens(query)
        if token not in _QUERY_TOKEN_STOPWORDS and len(token) >= 2
    }
    if not query_tokens:
        return None

    candidates: list[tuple[int, int, int, str, Evidence]] = []
    for source_rank, item in enumerate(evidence):
        source_best: tuple[int, int, int, str, Evidence] | None = None
        section_tokens = _content_tokens(item.section_key)
        section_on_topic = bool(query_tokens & section_tokens)
        normalized_text = re.sub(
            r"(?<![。！？!?；;])\n(?=\S)",
            " ",
            item.text,
        )
        raw_units = re.split(r"(?<=[。！？!?；;])\s*|\n+", normalized_text)
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
            if section_on_topic and (
                is_structured_request
                or not (query_tokens & sentence_tokens & section_tokens)
            ):
                display_sentence = f"{item.section_key}：{sentence}"
                sentence_tokens |= section_tokens

            overlap = query_tokens & sentence_tokens
            latin_overlap = {token for token in overlap if re.search(r"[a-z]", token)}
            chinese_overlap = overlap - latin_overlap
            if not latin_overlap and len(chinese_overlap) < 2:
                continue
            section_overlap = query_tokens & section_tokens
            structure_bonus = 0
            if is_structured_request:
                if re.search(r"(?:包括|包含|分为|常见|分别|有[:：])", sentence):
                    structure_bonus += 3 if is_workflow_request else 12
                if re.search(r"(?:步骤|流程)", sentence):
                    structure_bonus += 3
                if re.search(r"(?:训练模型|模型训练|测试集|泛化能力|数据预处理|评估)", sentence):
                    structure_bonus += 8 if is_workflow_request else 3
            score = (
                len(latin_overlap) * 4
                + len(chinese_overlap) * 2
                + len(section_overlap) * 5
                + structure_bonus
            )
            if is_structured_request and re.search(
                r"(?:指南|报告|手册|文档|概览)$",
                item.section_key,
            ):
                score -= 15
            candidate = (score, -source_rank, -unit_rank, display_sentence, item)
            if source_best is None or candidate[:3] > source_best[:3]:
                source_best = candidate
        if source_best is not None:
            candidates.append(source_best)

    if not candidates:
        return None

    selection_limit = 3 if is_structured_request else 2
    ranked = sorted(candidates, key=lambda item: item[:3], reverse=True)
    minimum_score = ranked[0][0] * 0.55 if is_structured_request else 0
    selected = [item for item in ranked if item[0] >= minimum_score][
        :selection_limit
    ]
    fact_lines = "\n".join(
        f"- {sentence} [{source.citation_id}]。"
        for _, _, _, sentence, source in selected
    )
    fallback = (
        f"已确认：\n{fact_lines}\n"
        "无法确认：现有资料没有直接给出问题所要求的完整解释。"
    )
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
    calculation_results: Sequence[int | float] = (),
    coverage_recheck: bool = True,
) -> GroundingDecision:
    """Analyze grounding quality and return a structured repair decision.

    Returns GroundingDecision with action: accept, deterministic_repair, llm_repair, or refuse.
    deterministic_repair actions can be handled without an LLM call (citation format fixes).
    llm_repair actions require a second model generation.
    """
    if not sources:
        return GroundingDecision(action="accept", reasons=["no_sources"])

    verification = verify_answer(
        answer,
        sources,
        query=query,
        calculation_results=calculation_results,
    )
    conditional_incomplete = not conditional_answer_complete(query, answer, sources)
    missing_information_incomplete = not missing_information_answer_complete(
        query,
        answer,
        sources,
    )

    if _CLARIFICATION_RE.search(answer) and verification.facts_supported == 0:
        return GroundingDecision(
            action="accept",
            reasons=["clarification_refusal"],
            verification=verification,
        )

    if (
        _FULL_REFUSAL_START_RE.search(answer)
        and not _has_relevant_missing_information_boundary(query, sources)
    ):
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

        if conditional_incomplete:
            reasons.append("conditional_incomplete")
        if missing_information_incomplete:
            reasons.append("missing_information_relation")

        # Citation format issues (deterministic fix possible)
        if verification.citation_recall < 1.0:
            reasons.append("missing_citation")
        if verification.citation_precision < 0.95 and any(
            claim.citations for claim in verification.claims
        ):
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
        content_reasons = {
            "unsupported_claim",
            "missing_number",
            "conditional_incomplete",
            "missing_information_relation",
        }
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


def grounding_repair_instruction(answer: str, query: str = "") -> str:
    """Build the single-pass correction request used by eval and production."""
    return (
        "上一个回答草稿未通过知识库声明级校验。请仅依据已有检索内容重新输出完整最终答案，"
        "不要调用工具。若任何来源能直接支持问题的一部分，必须先输出单独一行“已确认：”，"
        "再用可直接渲染的 Markdown 逐条回答，不能整体拒答；不要把答案包在代码围栏中。"
        "比较题使用三级标题加列表；如果使用表格，表格行前禁止添加列表标记。"
        "其余部分最后写“无法确认：……”。每个列表项只写一个"
        "原子事实，只用一个完整支持该事实的最小来源编号，并把引用放在句号前。不要使用分号"
        "连接多个事实，也不要在引用后另起一行放句号。若确实没有任何可直接回答的事实，保持"
        "整体拒答。删除所有无法由所引来源直接找到的内容。"
        "若原问题是条件判断，必须先明确回答是否必然/自动成立，再完整列出来源中的全部必要条件，"
        "并说明条件满足与不满足时的结论；若同时询问操作步骤和规则判断，分节回答且保持步骤顺序。"
        "若来源明确说明所问信息不包含、未提供或缺少资料，最终答案必须保留这一否定关系；"
        "不得把回答压缩成只有字段名称的列表。"
        f"\n\n原问题：\n{query}\n\n待纠正草稿：\n"
        f"{answer}"
    )


def select_better_grounded_answer(
    original: str,
    repaired: str,
    sources: Sequence[EvidenceSource],
    *,
    query: str = "",
    calculation_results: Sequence[int | float] = (),
) -> str:
    """Keep a safe repair without needlessly collapsing supported coverage."""
    if not repaired.strip():
        return original

    original_result = verify_answer(
        original,
        sources,
        query=query,
        calculation_results=calculation_results,
    )
    repaired_result = verify_answer(
        repaired,
        sources,
        query=query,
        calculation_results=calculation_results,
    )

    def semantically_complete(text: str) -> bool:
        return conditional_answer_complete(query, text, sources) and missing_information_answer_complete(
            query,
            text,
            sources,
        )

    repaired_is_semantically_better = not semantically_complete(original) and semantically_complete(repaired)
    if (
        not repaired_is_semantically_better
        and original_result.facts_supported >= 2
        and repaired_result.facts_supported < original_result.facts_supported
    ):
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

    def quality(text: str, result: VerificationResult) -> tuple[int, float, float, float, int]:
        return (
            int(semantically_complete(text)),
            result.faithfulness,
            result.citation_recall,
            result.citation_precision,
            result.facts_supported,
        )

    return repaired if quality(repaired, repaired_result) > quality(original, original_result) else original
