import asyncio
import difflib
import logging
import time
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from config import settings
from embedding.factory import create_embedding
from models.database import async_session
from models.orm import Document
from textdb.base import TextSearchResult
from textdb.bm25_search import BM25Search
from vectordb.base import VectorSearchResult
from vectordb.factory import create_vectordb

logger = logging.getLogger(__name__)

RRF_CANDIDATE_MULTIPLIER = 2


@dataclass
class RetrievalResult:
    chunk_id: str
    document_id: str
    text: str
    score: float
    source: str  # "semantic" | "keyword" | "hybrid"
    document_key: str = ""
    section_key: str = ""


def _rrf_fusion(
    vector_results: list[VectorSearchResult],
    text_results: list[TextSearchResult],
    k: int = 5,
    rrf_k: int | None = None,
    semantic_weight: float | None = None,
    keyword_weight: float | None = None,
) -> list[RetrievalResult]:
    """Reciprocal Rank Fusion with source-specific weights."""
    if rrf_k is None:
        rrf_k = settings.rrf_k
    if semantic_weight is None:
        semantic_weight = settings.rrf_semantic_weight
    if keyword_weight is None:
        keyword_weight = settings.rrf_keyword_weight
    # chunk_id -> (score, doc_id, text, source, document_key, section_key)
    scores: dict[str, tuple[float, str, str, str, str, str]] = {}

    # Semantic scores
    for rank, r in enumerate(vector_results):
        rrf_score = semantic_weight / (rrf_k + rank + 1)
        if r.chunk_id in scores:
            existing = scores[r.chunk_id]
            scores[r.chunk_id] = (existing[0] + rrf_score, r.document_id, r.text, "hybrid",
                                  r.document_key or existing[4], r.section_key or existing[5])
        else:
            scores[r.chunk_id] = (rrf_score, r.document_id, r.text, "semantic",
                                  r.document_key, r.section_key)

    # Keyword scores
    for rank, tr in enumerate(text_results):
        rrf_score = keyword_weight / (rrf_k + rank + 1)
        if tr.chunk_id in scores:
            existing = scores[tr.chunk_id]
            scores[tr.chunk_id] = (existing[0] + rrf_score, tr.document_id, tr.text, "hybrid",
                                   tr.document_key or existing[4], tr.section_key or existing[5])
        else:
            scores[tr.chunk_id] = (rrf_score, tr.document_id, tr.text, "keyword",
                                   tr.document_key, tr.section_key)

    # Sort by fused score descending
    sorted_items = sorted(scores.items(), key=lambda x: x[1][0], reverse=True)

    return [
        RetrievalResult(
            chunk_id=chunk_id,
            document_id=info[1],
            text=info[2],
            score=info[0],
            source=info[3],
            document_key=info[4],
            section_key=info[5],
        )
        for chunk_id, info in sorted_items[:k]
    ]


def _quality_prefilter(
    results: list[VectorSearchResult | TextSearchResult],
) -> list:
    """Filter out low-quality chunks before RRF fusion."""
    if not settings.rrf_quality_prefilter_enabled:
        return results
    return [r for r in results if _chunk_quality_score(r.text) >= 0.5]


async def _dedup_results(results: list[RetrievalResult]) -> list[RetrievalResult]:
    """Remove near-duplicate chunks from different documents, keeping newest doc."""
    if len(results) <= 1:
        return results

    # Load document created_at for comparison
    doc_ids = list({r.document_id for r in results})
    async with async_session() as session:
        result = await session.execute(
            select(Document.id, Document.created_at).where(Document.id.in_(doc_ids))
        )
        doc_times: dict[str, datetime] = {row[0]: row[1] for row in result.all()}

    threshold = settings.dedup_similarity_threshold
    kept: list[RetrievalResult] = []

    for r in results:
        replaced = False
        for i, existing in enumerate(kept):
            if r.document_id != existing.document_id:
                ratio = difflib.SequenceMatcher(None, r.text, existing.text).ratio()
                if ratio >= threshold:
                    r_time = doc_times.get(r.document_id)
                    e_time = doc_times.get(existing.document_id)
                    if r_time and e_time and r_time > e_time:
                        kept[i] = r  # Replace with newer doc's chunk
                    replaced = True
                    break
        if not replaced:
            kept.append(r)

    return kept


def _chunk_quality_score(text: str) -> float:
    """Score chunk quality 0.0-1.0. Low score = low quality, should be demoted."""
    if not settings.chunk_quality_filter_enabled:
        return 1.0
    stripped = text.strip()
    if len(stripped) < 30:
        return 0.1  # Too short to be useful

    lines = [ln.strip() for ln in stripped.split("\n") if ln.strip()]
    if not lines:
        return 0.2

    # 1. Content density: chars per line. Headers have very low density.
    avg_line_len = sum(len(ln) for ln in lines) / len(lines)
    if avg_line_len < 20:
        return 0.3  # Very sparse — likely TOC, headers, or lists

    # 2. Newline explosion: too many short lines = low content density
    newline_ratio = len(lines) / max(len(stripped), 1)
    if newline_ratio > 0.04:  # >1 newline per 25 chars
        return 0.4

    # 3. Number/symbol dominance: page numbers, separators
    symbol_count = sum(1 for c in stripped if c in "0123456789-/|….…·●◆■□▪▫")
    if symbol_count > len(stripped) * 0.2:
        return 0.5

    # 4. Repeated character patterns (like "======" or "──────")
    import re
    repeated_len = sum(len(m.group()) for m in re.finditer(r'(.)\1{4,}', stripped))
    if repeated_len > 4:
        return 0.4

    return 1.0  # Good quality content


async def _llm_chunk_quality_batch(texts: list[str]) -> list[float]:
    """Batch-evaluate chunk quality with LLM. Returns scores 0.0-1.0."""
    if not texts:
        return []

    from llm.base import ChatMessage
    from llm.factory import create_llm

    prompt = (
        "Rate each text chunk below on content quality from 0 (junk) to 10 (highly informative). "
        "Consider: content density, factual substance, and whether it contains actual information "
        "vs just headers/separators/TOC entries. Output exactly one integer per chunk, one per line:\n\n"
    )
    for i, text in enumerate(texts):
        prompt += f"[{i}] {text[:300]}\n"

    prompt += "\nScores (one integer per line, 0-10):"

    try:
        llm = create_llm()
        content_parts: list[str] = []
        async for chunk in llm.chat_stream([
            ChatMessage(role="system", content="You are a text quality evaluator. Output only numbers."),
            ChatMessage(role="user", content=prompt),
        ]):
            if chunk.content:
                content_parts.append(chunk.content)

        raw = "".join(content_parts)
        scores: list[float] = []
        for line in raw.strip().split("\n"):
            try:
                score = int(line.strip()) / 10.0
                scores.append(max(0.0, min(1.0, score)))
            except (ValueError, IndexError):
                scores.append(0.5)  # Default mid-score on parse failure

        # Pad or truncate to match input
        if len(scores) < len(texts):
            scores.extend([0.5] * (len(texts) - len(scores)))
        return scores[:len(texts)]

    except Exception:
        logger.warning("LLM chunk quality batch failed, falling back to regex scores", exc_info=True)
        return [0.5] * len(texts)


async def _rerank_results(
    query: str, results: list[RetrievalResult], top_k: int
) -> list[RetrievalResult]:
    """Re-rank results using Cross-Encoder. TOC chunks are deprioritized."""
    from reranker.factory import create_reranker

    reranker = create_reranker()
    if reranker is None or len(results) <= 1:
        return results

    # Score chunk quality, push low-quality chunks to end
    scored = [(r, _chunk_quality_score(r.text)) for r in results]

    # LLM batch evaluation for borderline chunks
    if settings.chunk_quality_llm_enabled:
        low_indices = [
            (i, r.text) for i, (r, s) in enumerate(scored)
            if 0.0 < s < 1.0
        ]
        if low_indices:
            llm_scores = await _llm_chunk_quality_batch([t for _, t in low_indices])
            for (i, _), llm_s in zip(low_indices, llm_scores, strict=False):
                scored[i] = (scored[i][0], llm_s)

    # Instead of full quality sort (which can push good-but-short chunks down):
    # keep RRF order, only demote very-low-quality chunks (score < 0.3) to the end
    good = [r for r, s in scored if s >= 0.3]
    bad = [r for r, s in scored if s < 0.3]
    scored_results = good + bad
    candidates = scored_results[: settings.rerank_top_n]

    texts = [r.text for r in candidates]
    ranked = await reranker.rerank(query, texts)

    reranked = [candidates[i] for i, _ in ranked[:top_k]]
    if len(results) > settings.rerank_top_n:
        reranked.extend(scored_results[settings.rerank_top_n:])
    return reranked


async def _multi_search(
    queries,
    vectordb,
    fts,
    embedding,
    top_k: int,
    document_id: str = "",
):
    """Run semantic + keyword search across multiple query variants, merge results.

    Each source (semantic/keyword) is flattened from all query variants,
    deduplicated by chunk_id keeping the highest score.
    """
    # Embed all queries in parallel
    all_vectors = await asyncio.gather(*(embedding.embed_query(q) for q in queries))

    # Parallel: all semantic searches + all keyword searches
    sem_tasks = [vectordb.search(v, top_k=top_k * RRF_CANDIDATE_MULTIPLIER)
                 for v in all_vectors]
    kw_tasks = [fts.search(q, top_k=top_k * RRF_CANDIDATE_MULTIPLIER, document_id=document_id)
                for q in queries]
    all_sem, all_kw = await asyncio.gather(
        asyncio.gather(*sem_tasks),
        asyncio.gather(*kw_tasks),
    )

    # Flatten + dedup per source by chunk_id, keeping highest score
    def _merge(results_lists):
        merged: dict = {}
        for rlist in results_lists:
            for r in rlist:
                if r.chunk_id not in merged or r.score > merged[r.chunk_id][0]:
                    merged[r.chunk_id] = (r.score, r)
        return [item[1] for item in sorted(merged.values(), key=lambda x: x[0], reverse=True)]

    vector_results = _merge(all_sem)
    text_results = _merge(all_kw)
    return vector_results, text_results


async def hybrid_search(query: str, top_k: int | None = None, document_id: str = "",
                       use_rerank: bool = False) -> list[RetrievalResult]:
    if top_k is None:
        top_k = settings.retrieval_top_k

    # Determine RRF weights: adaptive (query-typed) or static
    if settings.rrf_adaptive_enabled:
        from rag.query_classifier import get_profile
        profile = get_profile(query)
        semantic_w = profile.semantic_weight
        keyword_w = profile.keyword_weight
    else:
        semantic_w = settings.rrf_semantic_weight
        keyword_w = settings.rrf_keyword_weight

    t0 = time.time()

    embedding = create_embedding()
    vectordb = await create_vectordb()
    fts = BM25Search()

    # Multi-query rewrite: run LLM rewrite in parallel with first embedding
    queries = [query]
    query_rewritten = False
    if settings.query_rewrite_enabled:
        from rag.query_rewriter import rewrite
        variants = await rewrite(query, n_variants=2)
        if variants:
            queries = [query] + variants
            query_rewritten = True

    if query_rewritten:
        vector_results, text_results = await _multi_search(
            queries, vectordb, fts, embedding, top_k, document_id,
        )
    else:
        query_vector = await embedding.embed_query(query)
        vector_results, text_results = await asyncio.gather(
            vectordb.search(query_vector, top_k=top_k * RRF_CANDIDATE_MULTIPLIER),
            fts.search(query, top_k=top_k * RRF_CANDIDATE_MULTIPLIER, document_id=document_id),
        )
        vector_results = list(vector_results)
        text_results = list(text_results)

    # Filter by document_id if specified
    if document_id:
        vector_results = [r for r in vector_results if r.document_id == document_id]

    # Quality pre-filter: remove low-quality chunks before RRF fusion
    if settings.rrf_quality_prefilter_enabled:
        vector_results = _quality_prefilter(vector_results)
        text_results = _quality_prefilter(text_results)

    # RRF fusion — ensure enough candidates so reranker sees full pool
    rrf_count = max(settings.rerank_top_n, top_k * 3)
    results = _rrf_fusion(vector_results, text_results, k=rrf_count,
                          semantic_weight=semantic_w, keyword_weight=keyword_w)
    n_fused = len(results)

    # Dedup: remove near-duplicate chunks from different docs, keep newest
    if settings.dedup_enabled:
        results = await _dedup_results(results)

    # Reranker: Cross-Encoder re-scoring
    reranked = use_rerank and settings.rerank_enabled
    if reranked:
        results = await _rerank_results(query, results, top_k)

    final = results[:top_k]
    elapsed = int((time.time() - t0) * 1000)
    logger.info(
        "search queries=%d semantic=%d keyword=%d fused=%d dedup=%d rerank=%s final=%d elapsed_ms=%d",
        len(queries), len(vector_results), len(text_results), n_fused, len(results),
        str(reranked).lower(), len(final), elapsed,
    )
    return final
