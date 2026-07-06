import asyncio
import difflib
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from embedding.factory import create_embedding
from vectordb.factory import create_vectordb
from vectordb.base import VectorSearchResult
from textdb.sqlite_fts import SQLiteFTS5
from textdb.base import TextSearchResult
from models.database import async_session
from models.orm import Document
from sqlalchemy import select
from config import settings

logger = logging.getLogger(__name__)

RRF_CANDIDATE_MULTIPLIER = 2


@dataclass
class RetrievalResult:
    chunk_id: str
    document_id: str
    text: str
    score: float
    source: str  # "semantic" | "keyword" | "hybrid"


def _rrf_fusion(
    vector_results: list[VectorSearchResult],
    text_results: list[TextSearchResult],
    k: int = 5,
    rrf_k: int = 60,
) -> list[RetrievalResult]:
    """Reciprocal Rank Fusion."""
    scores: dict[str, tuple[float, str, str, str]] = {}  # chunk_id -> (score, doc_id, text, source)

    # Semantic scores
    for rank, r in enumerate(vector_results):
        rrf_score = 1.0 / (rrf_k + rank + 1)
        if r.chunk_id in scores:
            scores[r.chunk_id] = (scores[r.chunk_id][0] + rrf_score, r.document_id, r.text, "hybrid")
        else:
            scores[r.chunk_id] = (rrf_score, r.document_id, r.text, "semantic")

    # Keyword scores
    for rank, tr in enumerate(text_results):
        rrf_score = 1.0 / (rrf_k + rank + 1)
        if tr.chunk_id in scores:
            scores[tr.chunk_id] = (scores[tr.chunk_id][0] + rrf_score, tr.document_id, tr.text, "hybrid")
        else:
            scores[tr.chunk_id] = (rrf_score, tr.document_id, tr.text, "keyword")

    # Sort by fused score descending
    sorted_items = sorted(scores.items(), key=lambda x: x[1][0], reverse=True)

    return [
        RetrievalResult(
            chunk_id=chunk_id,
            document_id=info[1],
            text=info[2],
            score=info[0],
            source=info[3],
        )
        for chunk_id, info in sorted_items[:k]
    ]


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

    lines = [l.strip() for l in stripped.split("\n") if l.strip()]
    if not lines:
        return 0.2

    # 1. Content density: chars per line. Headers have very low density.
    avg_line_len = sum(len(l) for l in lines) / len(lines)
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
    scored.sort(key=lambda x: x[1], reverse=True)  # High quality first
    scored_results = [r for r, _ in scored]
    candidates = scored_results[: settings.rerank_top_n]

    texts = [r.text for r in candidates]
    ranked = await reranker.rerank(query, texts)

    reranked = [candidates[i] for i, _ in ranked[:top_k]]
    if len(results) > settings.rerank_top_n:
        reranked.extend(scored_results[settings.rerank_top_n:])
    return reranked


async def hybrid_search(query: str, top_k: int | None = None, document_id: str = "",
                       use_rerank: bool = False) -> list[RetrievalResult]:
    if top_k is None:
        top_k = settings.retrieval_top_k

    t0 = time.time()

    embedding = create_embedding()
    vectordb = await create_vectordb()
    fts = SQLiteFTS5()

    # Parallel: semantic + keyword
    query_vector = await embedding.embed_query(query)
    vector_results, text_results = await asyncio.gather(
        vectordb.search(query_vector, top_k=top_k * RRF_CANDIDATE_MULTIPLIER),
        fts.search(query, top_k=top_k * RRF_CANDIDATE_MULTIPLIER, document_id=document_id),
    )

    # Filter by document_id if specified
    if document_id:
        vector_results = [r for r in vector_results if r.document_id == document_id]

    # RRF fusion — get extra candidates for downstream filtering
    results = _rrf_fusion(vector_results, text_results, k=top_k * 3)
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
        "search semantic=%d keyword=%d fused=%d dedup=%d rerank=%s final=%d elapsed_ms=%d",
        len(vector_results), len(text_results), n_fused, len(results),
        str(reranked).lower(), len(final), elapsed,
    )
    return final
