"""Controlled online A/B evaluation for grounded knowledge-base answers.

The retrieval corpus and qrels are checked into the repository. Retrieval uses
the production splitter and BM25 implementation in an isolated SQLite database;
only the answer generation step calls the configured external LLM.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tests.qrels_schema import QrelDataset, QrelQuery, document_key_from_filename


def _load_isolated_verifier() -> Any:
    """Load the dependency-free verifier without importing agent package side effects."""
    path = Path(__file__).parents[1] / "agent" / "verifier.py"
    spec = importlib.util.spec_from_file_location("grounded_eval_verifier", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load verifier from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_verifier = _load_isolated_verifier()
verify_answer = _verifier.verify_answer
needs_grounding_repair = _verifier.needs_grounding_repair
grounding_repair_instruction = _verifier.grounding_repair_instruction
select_better_grounded_answer = _verifier.select_better_grounded_answer
build_partial_comparison_fallback = _verifier.build_partial_comparison_fallback
apply_query_safety_guard = _verifier.apply_query_safety_guard
apply_zero_support_guard = _verifier.apply_zero_support_guard

CONTROL_PROMPT = """你是知识库问答助手。只能根据给出的检索资料回答用户问题，不要使用外部知识。
可以用自己的话概括。资料不足时说明无法从现有资料确定。回答简洁，不要求添加来源编号。"""

OPTIMIZED_PROMPT = (
    "你是知识库问答助手，只能使用给出的检索资料，不得补充外部知识。\n"
    "有任一可答事实时，第一行只写“已确认：”，随后逐条回答；不可答部分最后写“无法确认：……”。"
    "仅当全部不可答时才整体拒答。问题缺少对象时简短追问。\n"
    "每个列表项只写一个能由同一来源完整支持的原子事实，并在句号前引用最少的真实编号，"
    "例如“成本下降 90% [S1]。”禁止用分号连接事实、用段尾引用覆盖多句或写“根据检索资料”。\n"
    "比较题必须分别列出“A 的资料事实”“B 的资料事实”“无法确认的比较维度”；"
    "资料未明确给出时，不推导差异、因果、优劣、最高级、适用场景或步骤。\n"
    "覆盖问题直接要求的定义、类别、例子、数字和限制，删除重复与无关内容；"
    "用户只输入一个术语或实体名称时，将其视为概览请求，检查所有直接相关片段，在六项以内覆盖定义、核心组成、典型用途和明确限制，不要只回答一句定义；"
    "简洁不等于省略关键事实，同一相关片段明确列出多个核心属性时，应在字数上限内完整覆盖后再结束回答；"
    "先完整覆盖排名靠前且标题直接匹配问题的片段，再考虑较低排名的旁支内容；来源给出专有名词、英文名或缩写时保留这些名称，不要只改写成泛称；"
    "一般不超过 500 个中文字符或 6 项。输出前删除任何无法在所引来源中直接找到的陈述。"
)

REFUSAL_MARKERS = (
    "现有资料不足",
    "资料不足",
    "无法从现有资料",
    "无法回答",
    "无法确认",
    "请指明",
    "请问您想问",
    "请问您想询问",
    "请提供具体",
    "请补充",
    "缺少明确的指代对象",
    "无法提供",
    "无法确定",
    "不能提供",
    "没有相关信息",
    "未找到",
    "没有提到",
    "不能确定",
    "不足以回答",
    "请明确",
    "请说明",
    "请问具体想了解",
    "请提供您想了解的具体",
    "请问您指的是哪个",
    "请指定您想了解",
    "具体指的是什么",
    "请提供您的问题",
    "请提供明确的问题",
    "指代不清晰",
    "请提供您所指的具体",
    "无法理解您的问题",
    "无法识别您的问题",
    "请您提供一个有明确对象",
)

DEV_QUERY_IDS = {
    "exact-002", "exact-007", "numeric-004", "comparison-001",
    "comparison-004", "cross-001", "cross-005", "detail-001",
    "detail-003", "instruct-001", "instruct-003", "long-001",
    "long-002", "multi-hop-001", "multi-hop-004", "paraphrase-003",
    "negation-002", "typo-001", "followup-002", "unanswerable-001",
    "unanswerable-003", "prompt-inject-003",
}

V3_DEV_QUERY_IDS = {
    "cn-en-mix-001", "cn-en-mix-005", "multi-hop-003", "multi-hop-004",
    "comparison-002", "comparison-003", "comparison-005", "detail-002",
    "detail-005", "exact-003", "exact-007", "exact-009", "short-003",
    "long-002", "long-004", "ambiguous-001", "unanswerable-001",
    "prompt-inject-003",
}

V31_DEV_QUERY_IDS = {
    "comparison-003", "comparison-005", "multi-hop-003", "multi-hop-004",
    "long-002", "long-004", "cn-en-mix-005", "short-003", "exact-003",
    "comparison-002", "ambiguous-001", "unanswerable-001",
}

QUALITY_FLOORS = {
    "citation_precision": 0.95,
    "citation_recall": 0.95,
    "abstention_accuracy": 0.98,
    "expected_fact_recall": 0.85,
    "answer_completion_accuracy": 0.95,
    "max_faithfulness_regression": 0.0,
    "max_fact_recall_regression": 0.02,
}

SCORING_VERSION = "claim-evidence-v4-conditional-definition"
EVALUATION_SCOPE = "production-like-controlled-online"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _evaluation_provenance() -> dict[str, str]:
    tests_dir = Path(__file__).resolve().parent
    return {
        "dataset_sha256": _sha256_file(tests_dir / "qrels_data_v2.json"),
        "verifier_sha256": _sha256_file(tests_dir.parent / "agent" / "verifier.py"),
        "evaluator_sha256": _sha256_file(Path(__file__).resolve()),
        "optimized_prompt_sha256": hashlib.sha256(
            OPTIMIZED_PROMPT.encode("utf-8"),
        ).hexdigest(),
    }

PERFORMANCE_TARGETS = {
    "ttft_p50_ms": 1000.0,
    "ttft_p95_ms": 2500.0,
    "latency_p50_ms": 2000.0,
    "latency_p95_ms": 5000.0,
    "latency_p99_ms": 10000.0,
    "llm_repair_rate": 0.10,
    "llm_repair_accept_rate": 0.40,
}


@dataclass
class EvalRecord:
    query_id: str
    query: str
    mode: str
    answerable: bool
    answer: str
    sources: list[dict[str, Any]]
    latency_ms: float
    faithfulness: float | None
    citation_precision: float | None
    citation_recall: float | None
    refused: bool
    abstention_correct: bool | None
    expected_fact_recall: float | None
    verification_status: str | None
    answerability: str = "full"
    answer_completion_correct: bool | None = None
    error: str | None = None
    # ── V4 phased timing & repair metadata ──
    repair_used: str = "none"
    repair_reasons: list[str] | None = None
    repair_triggered: bool = False
    draft_latency_ms: float | None = None
    verification_latency_ms: float | None = None
    repair_latency_ms: float | None = None
    ttft_ms: float | None = None


# Parse --env-override early before any config import
_pre_parser = argparse.ArgumentParser(add_help=False)
_pre_parser.add_argument("--env-override", action="append", default=[])
_pre_args, _ = _pre_parser.parse_known_args()
for _override in _pre_args.env_override:
    if "=" in _override:
        _key, _value = _override.split("=", 1)
        os.environ[_key] = _value


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="grounded_answer_eval_results.json")
    parser.add_argument("--limit", type=int, default=0, help="0 evaluates all queries")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--dev", action="store_true", help="run the fixed failure-focused dev split")
    parser.add_argument("--v3-dev", action="store_true", help="run the V3 coverage/refusal split")
    parser.add_argument("--v31-dev", action="store_true", help="run the V3.1 synthesis rollback split")
    parser.add_argument("--query-id", action="append", default=[], help="evaluate only this query id")
    parser.add_argument(
        "--rescore", action="store_true", help="rescore an existing output without LLM calls"
    )
    parser.add_argument(
        "--enforce-gate",
        action="store_true",
        help="return a non-zero exit code when quality or performance gates fail",
    )
    parser.add_argument(
        "--env-override", action="append", default=[],
        help="Override config via env var, e.g. KEY=VALUE",
    )
    return parser.parse_args()


async def _build_index() -> Any:
    from rag.splitter import split_text
    from tests.evaluate_rag import TEST_DOCS
    from textdb.bm25_search import BM25Search

    bm25 = BM25Search(table_suffix="_grounded_eval")
    entries: list[tuple[str, str, str, str, int, str]] = []
    for doc_index, doc in enumerate(TEST_DOCS):
        document_key = document_key_from_filename(doc.filename)
        chunks = split_text(doc.content, chunk_size=200, chunk_overlap=40)
        entries.extend(
            (
                f"grounded-{doc_index}-{chunk.chunk_index}",
                f"grounded-doc-{doc_index}",
                document_key,
                chunk.section_key,
                chunk.chunk_index,
                chunk.text,
            )
            for chunk in chunks
        )
    await bm25.insert_batch(entries)
    return bm25


def _source_payload(results: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "citation_id": f"S{index}",
            "chunk_id": result.chunk_id,
            "document_id": result.document_id,
            "document_key": result.document_key,
            "section_key": result.section_key,
            "text": result.text,
            "score": result.score,
            "rank": index,
        }
        for index, result in enumerate(results, 1)
    ]


def _context(sources: list[dict[str, Any]]) -> str:
    if not sources:
        return "（没有检索到资料）"
    return "\n\n".join(
        f"[{source['citation_id']}] document={source['document_key']} section={source['section_key']}\n{source['text']}"
        for source in sources
    )


async def _generate(system_prompt: str, query: str, sources: list[dict[str, Any]]) -> dict:
    """Generate answer with V4 metadata: timing phases, repair tracking."""
    from llm.base import ChatMessage
    from llm.factory import create_llm

    llm = create_llm()
    messages = [
        ChatMessage(role="system", content=system_prompt),
        ChatMessage(
            role="user",
            content=(f"<UNTRUSTED_RETRIEVED_CONTENT>\n{_context(sources)}\n</UNTRUSTED_RETRIEVED_CONTENT>\n\n用户问题：{query}"),
        ),
    ]
    async def collect(call_messages: list[ChatMessage]) -> tuple[str, float | None]:
        chunks: list[str] = []
        started = time.perf_counter()
        ttft_ms: float | None = None
        async for response in llm.chat_stream(call_messages, tools=None):
            if response.content:
                if ttft_ms is None:
                    ttft_ms = (time.perf_counter() - started) * 1000
                chunks.append(response.content)
        return "".join(chunks).strip(), ttft_ms

    t_draft_start = time.perf_counter()
    answer, ttft_ms = await collect(messages)
    if not answer:
        # Treat a one-off empty stream as a transient provider response. Keep
        # the retry bounded so evaluation latency still reflects production.
        answer, retry_ttft_ms = await collect(messages)
        if ttft_ms is None:
            ttft_ms = retry_ttft_ms
    draft_ms = (time.perf_counter() - t_draft_start) * 1000
    if not answer:
        raise RuntimeError("LLM returned an empty answer")

    repair_used = "none"
    repair_reasons: list[str] | None = None
    repair_triggered = False
    verification_ms = 0.0
    repair_ms = 0.0

    if system_prompt == OPTIMIZED_PROMPT:
        answer = apply_query_safety_guard(query, answer, has_context=False)
        t_verify = time.perf_counter()
        decision = needs_grounding_repair(
            answer,
            sources,
            query=query,
            # Online latency policy: coverage expansion never blocks output.
            coverage_recheck=False,
        )
        verification_ms = (time.perf_counter() - t_verify) * 1000
        repair_triggered = decision.needs_repair
        repair_reasons = list(decision.reasons) or None

        if repair_triggered:
            if "topical_false_refusal" in decision.reasons:
                partial_fallback = build_partial_comparison_fallback(query, sources)
                if partial_fallback:
                    answer = partial_fallback
                    repair_used = "deterministic_partial"
                    decision = _verifier.GroundingDecision(action="accept")

            if decision.action == "deterministic_repair":
                from agent.grounding_repair import deterministic_repair

                t_repair = time.perf_counter()
                result = deterministic_repair(
                    answer,
                    _verifier._normalize_evidence(sources),
                    decision,
                )
                repair_ms = (time.perf_counter() - t_repair) * 1000
                if result.repaired:
                    answer = result.repaired_text
                    repair_used = "deterministic"
                if result.needs_llm:
                    decision = _verifier.GroundingDecision(
                        action="llm_repair",
                        reasons=result.llm_reasons,
                    )

            if decision.action == "llm_repair":
                t_repair = time.perf_counter()
                from config import settings

                try:
                    async with asyncio.timeout(settings.grounding_repair_timeout):
                        repaired, _ = await collect(messages + [
                            ChatMessage(role="assistant", content=answer),
                            ChatMessage(
                                role="user",
                                content=grounding_repair_instruction(answer),
                            ),
                        ])
                except TimeoutError:
                    repaired = ""
                    repair_used = "llm_timeout"
                    repair_reasons = list(repair_reasons or []) + [
                        "llm_repair_timeout",
                    ]
                repair_ms += (time.perf_counter() - t_repair) * 1000
                if repaired:
                    better = select_better_grounded_answer(answer, repaired, sources)
                    if better != answer:
                        answer = better
                        repair_used = "llm"
                    else:
                        repair_used = "llm_rejected"
                elif repair_used != "llm_timeout":
                    repair_used = "llm_empty"

        guarded_answer = apply_zero_support_guard(answer, sources)
        if guarded_answer != answer:
            answer = guarded_answer
            repair_used = "safe_refusal"
            repair_reasons = list(repair_reasons or []) + [
                "zero_supported_claims",
            ]

    return {
        "answer": answer,
        "repair_used": repair_used,
        "repair_reasons": repair_reasons,
        "repair_triggered": repair_triggered,
        "draft_latency_ms": draft_ms,
        "verification_latency_ms": verification_ms,
        "repair_latency_ms": repair_ms,
        "ttft_ms": ttft_ms,
    }


def _normalize_fact_text(value: str) -> str:
    """Normalize formatting variants without weakening lexical fact matching."""
    return re.sub(r"[\s,，_]", "", value.lower())


def _fact_recall(answer: str, query: QrelQuery) -> float | None:
    facts = query.answer_expected_facts or query.expected_answer_facts
    if query.answerability == "none" or not facts:
        return None
    normalized_answer = _normalize_fact_text(answer)
    return sum(
        any(
            _normalize_fact_text(alternative) in normalized_answer
            for alternative in fact.split("|")
        )
        for fact in facts
    ) / len(facts)


def _is_full_refusal(answer: str, verification: Any | None) -> bool:
    """Distinguish a whole-answer refusal from a limitation after supported facts."""
    has_marker = any(marker in answer for marker in REFUSAL_MARKERS)
    if not has_marker:
        return False
    return verification is None or verification.facts_supported == 0


def _is_safe_abstention(answer: str) -> bool:
    """Recognize an explicit limitation even after useful supporting context."""
    return any(marker in answer for marker in REFUSAL_MARKERS)


def _score(
    query: QrelQuery,
    mode: str,
    answer: str,
    sources: list[dict[str, Any]],
    latency_ms: float,
) -> EvalRecord:
    answerable = query.answerability != "none"
    verification = verify_answer(answer, sources) if answerable else None
    refused = _is_full_refusal(answer, verification)
    if query.answerability == "none":
        completion_correct = _is_safe_abstention(answer)
    elif query.answerability == "partial":
        completion_correct = bool(verification and verification.facts_supported)
    else:
        completion_correct = bool(
            verification and verification.facts_supported and not refused
        )
    return EvalRecord(
        query_id=query.query_id,
        query=query.query,
        mode=mode,
        answerable=answerable,
        answer=answer,
        sources=sources,
        latency_ms=latency_ms,
        faithfulness=verification.faithfulness if verification else None,
        citation_precision=verification.citation_precision if verification else None,
        citation_recall=verification.citation_recall if verification else None,
        refused=refused,
        abstention_correct=_is_safe_abstention(answer) if not answerable else None,
        expected_fact_recall=_fact_recall(answer, query) if answerable else None,
        verification_status=verification.status if verification else None,
        answerability=query.answerability,
        answer_completion_correct=completion_correct,
    )


def _aggregate(records: list[EvalRecord], mode: str) -> dict[str, Any]:
    selected = [record for record in records if record.mode == mode and not record.error]
    answerable = [record for record in selected if record.answerability != "none"]
    unanswerable = [record for record in selected if record.answerability == "none"]
    partial = [record for record in selected if record.answerability == "partial"]

    def average(field: str, items: list[EvalRecord]) -> float | None:
        values = [getattr(item, field) for item in items if getattr(item, field) is not None]
        return sum(values) / len(values) if values else None

    def percentile(field: str, q: float) -> float | None:
        values = sorted(
            float(value)
            for item in selected
            if (value := getattr(item, field)) is not None
        )
        if not values:
            return None
        index = (len(values) - 1) * q
        lower = int(index)
        upper = min(lower + 1, len(values) - 1)
        weight = index - lower
        return values[lower] * (1 - weight) + values[upper] * weight

    # ── V4: Repair stats ──
    repair_triggered = [r for r in selected if r.repair_triggered]
    llm_repair_triggered = [
        r for r in selected
        if r.repair_used in {
            "llm", "llm_rejected", "llm_empty", "llm_timeout", "llm_error",
        }
    ]
    repair_by_reason: dict[str, int] = {}
    for r in repair_triggered:
        for reason in (r.repair_reasons or []):
            repair_by_reason[reason] = repair_by_reason.get(reason, 0) + 1
    diagnostic_by_reason: dict[str, int] = {}
    for r in selected:
        for reason in (r.repair_reasons or []):
            diagnostic_by_reason[reason] = diagnostic_by_reason.get(reason, 0) + 1

    return {
        "queries_completed": len(selected),
        "answerable_queries": len(answerable),
        "unanswerable_queries": len(unanswerable),
        "partially_answerable_queries": len(partial),
        "faithfulness": average("faithfulness", answerable),
        "citation_precision": average("citation_precision", answerable),
        "citation_recall": average("citation_recall", answerable),
        "abstention_accuracy": average("abstention_correct", unanswerable),
        "expected_fact_recall": average("expected_fact_recall", answerable),
        "answer_completion_accuracy": average("answer_completion_correct", selected),
        "avg_latency_ms": average("latency_ms", selected),
        "latency_p50_ms": percentile("latency_ms", 0.50),
        "latency_p95_ms": percentile("latency_ms", 0.95),
        "latency_p99_ms": percentile("latency_ms", 0.99),
        "ttft_p50_ms": percentile("ttft_ms", 0.50),
        "ttft_p95_ms": percentile("ttft_ms", 0.95),
        "avg_draft_ms": average("draft_latency_ms", selected),
        "avg_verification_ms": average("verification_latency_ms", selected),
        "avg_repair_ms": average("repair_latency_ms", [r for r in selected if r.repair_triggered]),
        "errors": len([record for record in records if record.mode == mode and record.error]),
        # ── V4 repair counters ──
        "repair_triggered_count": len(repair_triggered),
        "repair_trigger_rate": len(repair_triggered) / max(len(selected), 1),
        "repair_by_reason": repair_by_reason,
        "diagnostic_by_reason": diagnostic_by_reason,
        "repair_accepted_count": len([r for r in selected if r.repair_used == "llm"]),
        "repair_accept_rate": (
            len([r for r in selected if r.repair_used == "llm"])
            / max(len(repair_triggered), 1)
        ),
        "llm_repair_triggered_count": len(llm_repair_triggered),
        "llm_repair_rate": len(llm_repair_triggered) / max(len(selected), 1),
        "llm_repair_accept_rate": (
            len([r for r in llm_repair_triggered if r.repair_used == "llm"])
            / max(len(llm_repair_triggered), 1)
        ),
    }


def evaluate_quality_gate(aggregate: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Evaluate release gates for a completed control/optimized A/B run."""
    control = aggregate["control"]
    optimized = aggregate["optimized"]
    violations: list[str] = []
    if control["queries_completed"] != optimized["queries_completed"]:
        violations.append("control and optimized query counts differ")
    if control["errors"] or optimized["errors"]:
        violations.append("one or more generation calls failed")

    def below(metric: str, floor: float) -> None:
        value = optimized.get(metric)
        if value is None or value < floor:
            violations.append(f"{metric}={value} below {floor}")

    below("citation_precision", QUALITY_FLOORS["citation_precision"])
    below("citation_recall", QUALITY_FLOORS["citation_recall"])
    below("abstention_accuracy", QUALITY_FLOORS["abstention_accuracy"])
    below("expected_fact_recall", QUALITY_FLOORS["expected_fact_recall"])
    below("answer_completion_accuracy", QUALITY_FLOORS["answer_completion_accuracy"])

    faith_delta = (optimized.get("faithfulness") or 0.0) - (
        control.get("faithfulness") or 0.0
    )
    if faith_delta < -QUALITY_FLOORS["max_faithfulness_regression"]:
        violations.append(f"faithfulness regression={faith_delta:.4f}")
    fact_delta = (optimized.get("expected_fact_recall") or 0.0) - (
        control.get("expected_fact_recall") or 0.0
    )
    if fact_delta < -QUALITY_FLOORS["max_fact_recall_regression"]:
        violations.append(f"expected_fact_recall regression={fact_delta:.4f}")
    return {"passed": not violations, "violations": violations, "floors": QUALITY_FLOORS}


def evaluate_performance_gate(aggregate: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Evaluate V4 latency targets independently from the quality hard gate."""
    optimized = aggregate["optimized"]
    violations: list[str] = []
    for metric in (
        "ttft_p50_ms", "ttft_p95_ms", "latency_p50_ms",
        "latency_p95_ms", "latency_p99_ms", "llm_repair_rate",
    ):
        value = optimized.get(metric)
        target = PERFORMANCE_TARGETS[metric]
        if value is None or value > target:
            violations.append(f"{metric}={value} above {target}")
    accept_rate = optimized.get("llm_repair_accept_rate")
    if optimized.get("llm_repair_triggered_count", 0) and (
        accept_rate is None
        or accept_rate < PERFORMANCE_TARGETS["llm_repair_accept_rate"]
    ):
        violations.append(
            "llm_repair_accept_rate="
            f"{accept_rate} below {PERFORMANCE_TARGETS['llm_repair_accept_rate']}"
        )
    return {
        "passed": not violations,
        "violations": violations,
        "targets": PERFORMANCE_TARGETS,
    }


def _write_report(path: Path, records: list[EvalRecord], dataset: QrelDataset) -> None:
    aggregate = {
        "control": _aggregate(records, "control"),
        "optimized": _aggregate(records, "optimized"),
    }
    report = {
        "schema_version": "1.0",
        "scoring_version": SCORING_VERSION,
        "evaluation_scope": EVALUATION_SCOPE,
        "timestamp": datetime.now(UTC).isoformat(),
        "provenance": _evaluation_provenance(),
        "dataset": {"name": dataset.name, "version": dataset.version},
        "methodology": {
            "control": "legacy source-only prompt without mandatory citations",
            "optimized": (
                "V4 selective false-refusal retry, deterministic claim-level "
                "citation repair, and one timeout-bounded LLM repair"
            ),
            "retrieval": "same production splitter + BM25 top-5 sources for both groups",
            "scoring": (
                "deterministic claim-to-evidence verifier; answerability is labeled "
                "independently from retrieval relevance"
            ),
            "latency": "timer starts after acquiring the local concurrency slot",
        },
        "aggregate": aggregate,
        "quality_gate": evaluate_quality_gate(aggregate),
        "performance_gate": evaluate_performance_gate(aggregate),
        "records": [asdict(record) for record in records],
    }
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _rescore_report(path: Path, dataset: QrelDataset) -> None:
    report = json.loads(path.read_text(encoding="utf-8"))
    queries = {query.query_id: query for query in dataset.queries}
    records: list[EvalRecord] = []
    for raw in report.get("records", []):
        record = EvalRecord(**raw)
        query = queries[record.query_id]
        record.answerability = query.answerability
        record.answerable = query.answerability != "none"
        if record.mode == "optimized" and not record.error:
            record.answer = apply_query_safety_guard(
                query.query,
                record.answer,
                has_context=False,
            )
            record.answer = apply_zero_support_guard(record.answer, record.sources)
        if record.answerable and not record.error:
            verification = verify_answer(record.answer, record.sources)
            record.refused = _is_full_refusal(record.answer, verification)
            record.faithfulness = verification.faithfulness
            record.citation_precision = verification.citation_precision
            record.citation_recall = verification.citation_recall
            record.verification_status = verification.status
            record.answer_completion_correct = (
                bool(verification.facts_supported)
                if query.answerability == "partial"
                else bool(verification.facts_supported and not record.refused)
            )
            record.expected_fact_recall = _fact_recall(record.answer, query)
        elif query.answerability == "none":
            record.refused = _is_full_refusal(record.answer, None)
            record.faithfulness = None
            record.citation_precision = None
            record.citation_recall = None
            record.expected_fact_recall = None
            record.verification_status = None
            record.answer_completion_correct = _is_safe_abstention(record.answer)
        record.abstention_correct = (
            _is_safe_abstention(record.answer)
            if query.answerability == "none" else None
        )
        records.append(record)

    report["rescored_at"] = datetime.now(UTC).isoformat()
    report["scoring_version"] = SCORING_VERSION
    report["rescored_provenance"] = {
        "dataset_sha256": _evaluation_provenance()["dataset_sha256"],
        "verifier_sha256": _evaluation_provenance()["verifier_sha256"],
    }
    report["aggregate"] = {
        "control": _aggregate(records, "control"),
        "optimized": _aggregate(records, "optimized"),
    }
    report["quality_gate"] = evaluate_quality_gate(report["aggregate"])
    report["performance_gate"] = evaluate_performance_gate(report["aggregate"])
    report["records"] = [asdict(record) for record in records]
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["aggregate"], ensure_ascii=False, indent=2))


async def _main() -> int:
    args = _parse_args()
    output = Path(args.output).resolve()
    dataset = QrelDataset.load(str(Path(__file__).with_name("qrels_data_v2.json")))
    if args.rescore:
        _rescore_report(output, dataset)
        if not args.enforce_gate:
            return 0
        rescored = json.loads(output.read_text(encoding="utf-8"))
        return 0 if (
            rescored["quality_gate"]["passed"]
            and rescored["performance_gate"]["passed"]
        ) else 1
    queries = dataset.queries
    if args.dev:
        queries = [query for query in queries if query.query_id in DEV_QUERY_IDS]
    if args.v3_dev:
        queries = [query for query in queries if query.query_id in V3_DEV_QUERY_IDS]
    if args.v31_dev:
        queries = [query for query in queries if query.query_id in V31_DEV_QUERY_IDS]
    if args.query_id:
        selected_ids = set(args.query_id)
        queries = [query for query in queries if query.query_id in selected_ids]
    queries = queries[: args.limit or None]
    bm25 = await _build_index()
    retrieved = {query.query_id: _source_payload(await bm25.search(query.query, top_k=args.top_k)) for query in queries}
    if args.dry_run:
        retrieval_hits = {
            query.query_id: any(
                source["document_key"] == relevant.document_key
                and source["section_key"] == relevant.section_key
                for source in retrieved[query.query_id]
                for relevant in query.relevant
            )
            for query in queries
            if query.relevant
        }
        answerable_retrieval_hits = {
            query_id: hit
            for query_id, hit in retrieval_hits.items()
            if next(query for query in queries if query.query_id == query_id).answerability
            != "none"
        }
        print(
            json.dumps(
                {
                    "queries": len(queries),
                    "answerable": sum(query.answerability != "none" for query in queries),
                    "unanswerable": sum(query.answerability == "none" for query in queries),
                    "queries_with_sources": sum(bool(retrieved[query.query_id]) for query in queries),
                    "retrieval_hit_at_k": (
                        sum(retrieval_hits.values()) / len(retrieval_hits)
                        if retrieval_hits else None
                    ),
                    "retrieval_misses": [
                        query_id for query_id, hit in retrieval_hits.items() if not hit
                    ],
                    "answerable_retrieval_hit_at_k": (
                        sum(answerable_retrieval_hits.values())
                        / len(answerable_retrieval_hits)
                        if answerable_retrieval_hits else None
                    ),
                    "answerable_retrieval_misses": [
                        query_id
                        for query_id, hit in answerable_retrieval_hits.items()
                        if not hit
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    records: list[EvalRecord] = []
    lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(max(args.concurrency, 1))

    async def run_query(query: QrelQuery) -> None:
        sources = retrieved[query.query_id]
        for mode, prompt in (("control", CONTROL_PROMPT), ("optimized", OPTIMIZED_PROMPT)):
            async with semaphore:
                # Measure external model service time, not local semaphore queueing.
                started = time.perf_counter()
                try:
                    gen_result = await _generate(prompt, query.query, sources)
                    answer = gen_result["answer"]
                    record = _score(
                        query, mode, answer, sources,
                        (time.perf_counter() - started) * 1000,
                    )
                    # ── V4: Attach repair & timing metadata ──
                    record.repair_used = gen_result.get("repair_used", "none")
                    record.repair_reasons = gen_result.get("repair_reasons")
                    record.repair_triggered = gen_result.get("repair_triggered", False)
                    record.draft_latency_ms = gen_result.get("draft_latency_ms")
                    record.verification_latency_ms = gen_result.get("verification_latency_ms")
                    record.repair_latency_ms = gen_result.get("repair_latency_ms")
                    record.ttft_ms = gen_result.get("ttft_ms")
                except Exception as exc:
                    record = EvalRecord(
                        query_id=query.query_id,
                        query=query.query,
                        mode=mode,
                        answerable=query.answerability != "none",
                        answer="",
                        sources=sources,
                        latency_ms=(time.perf_counter() - started) * 1000,
                        faithfulness=None,
                        citation_precision=None,
                        citation_recall=None,
                        refused=False,
                        abstention_correct=None,
                        expected_fact_recall=None,
                        verification_status=None,
                        answerability=query.answerability,
                        error=f"{type(exc).__name__}: {str(exc)[:300]}",
                    )
            async with lock:
                records.append(record)
                _write_report(output, records, dataset)
                print(
                    f"[{len(records)}/{len(queries) * 2}] {query.query_id} {mode} "
                    f"error={bool(record.error)} latency={record.latency_ms:.0f}ms",
                    flush=True,
                )

    await asyncio.gather(*(run_query(query) for query in queries))
    _write_report(output, records, dataset)
    quality_gate = evaluate_quality_gate({
        "control": _aggregate(records, "control"),
        "optimized": _aggregate(records, "optimized"),
    })
    performance_gate = evaluate_performance_gate({
        "control": _aggregate(records, "control"),
        "optimized": _aggregate(records, "optimized"),
    })
    print(
        json.dumps(
            {
                "output": str(output),
                "control": _aggregate(records, "control"),
                "optimized": _aggregate(records, "optimized"),
                "quality_gate": quality_gate,
                "performance_gate": performance_gate,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if args.enforce_gate and not (
        quality_gate["passed"] and performance_gate["passed"]
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
