"""Version-aware answer cache (V4 Phase 4).

Caches final answers keyed by query semantics + retrieval results + document
collection version.  Avoids redundant LLM generation for repeated or similar
queries against the same document set.

Safety rules:
  - Personal-memory and conversation-context queries are never cached.
  - Cache invalidation on every document add/delete/reprocess.
  - Permissions are re-checked on cache hit via document visibility.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from threading import RLock
from typing import Any

from config import settings

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """A cached answer with its retrieval context."""

    answer: str
    sources: list[dict]
    verification: dict | None
    collection_version: int
    created_at: float = field(default_factory=time.time)
    hit_count: int = 0


class AnswerCache:
    """In-memory answer cache with TTL and collection-version-based invalidation."""

    def __init__(self, ttl: int = 300, max_entries: int = 1000):
        self._ttl = ttl
        self._max_entries = max_entries
        self._store: dict[str, CacheEntry] = {}
        self._collection_version: int = 0
        self._lock = RLock()
        self._hits: int = 0
        self._misses: int = 0
        self._stale_evictions: int = 0

    # ── Collection version ───────────────────────────────────────

    @property
    def collection_version(self) -> int:
        with self._lock:
            return self._collection_version

    def bump_collection_version(self) -> int:
        """Invalidate all cached entries when documents change.

        Called from the ingestion pipeline after document add/delete/reprocess.
        """
        with self._lock:
            self._collection_version += 1
            self._stale_evictions += len(self._store)
            self._store.clear()
            logger.info(
                "answer cache invalidated: collection_version=%d, evicted=%d",
                self._collection_version, self._stale_evictions,
            )
            return self._collection_version

    # ── Cache key ─────────────────────────────────────────────────

    @staticmethod
    def compute_key(
        normalized_query: str,
        retrieval_hash: str,
        collection_version: int,
        model_name: str = "",
        prompt_version: str = "",
        *,
        context_hash: str = "",
        grounding_policy_version: str = "",
    ) -> str:
        """Compute a stable cache key from the retrieval context.

        Includes conversation context hash to prevent cross-conversation
        leaks, and grounding policy version so config changes invalidate.
        """
        components = [
            normalized_query.strip().lower(),
            retrieval_hash,
            str(collection_version),
            model_name,
            prompt_version,
        ]
        if context_hash:
            components.append(context_hash)
        if grounding_policy_version:
            components.append(grounding_policy_version)
        joined = "|".join(components)
        return hashlib.sha256(joined.encode()).hexdigest()

    @staticmethod
    def retrieval_hash(sources: list[dict]) -> str:
        """Compute a deterministic hash of the retrieval results.

        Only uses stable identifiers (chunk_id or document_key+section_key),
        not scores or text content, so minor score changes don't invalidate.
        """
        ids = []
        for s in sources[:8]:  # top-8 only
            cid = s.get("chunk_id")
            if not cid:
                document_key = s.get("document_key") or s.get("document_id") or ""
                section_key = s.get("section_key") or ""
                # Legacy payloads may omit both stable keys. Include a content
                # digest so unrelated sources never collapse to the same "#".
                text_digest = hashlib.sha256(
                    str(s.get("text", "")).encode("utf-8"),
                ).hexdigest()[:12]
                cid = f"{document_key}#{section_key}#{text_digest}"
            ids.append(cid)
        return hashlib.md5("|".join(sorted(ids)).encode()).hexdigest()[:12]

    @staticmethod
    def context_hash(history: list) -> str:
        """Compute a short hash of the conversation context for cache isolation.

        Prevents cache sharing across conversations with different contexts
        that might influence the answer even for the same query.
        """
        if not history:
            return ""
        # Hash the last 3 user/assistant message contents
        snippets = []
        for msg in history[-6:]:  # last 3 exchanges
            role = getattr(msg, "role", "")
            content = getattr(msg, "content", "") or ""
            if role in ("user", "assistant") and content:
                snippets.append(f"{role}:{content[:120]}")
        if not snippets:
            return ""
        return hashlib.md5("|".join(snippets).encode()).hexdigest()[:8]

    @staticmethod
    def grounding_policy_version() -> str:
        """Derive a version string from grounding config so policy changes
        invalidate cached answers.
        """
        from config import settings
        parts = [
            str(settings.grounding_min_coverage),
            settings.grounding_enforcement,
            str(settings.grounding_auto_cite_min_score),
            str(settings.grounding_auto_cite_min_margin),
        ]
        return hashlib.md5("|".join(parts).encode()).hexdigest()[:6]

    # ── Cache operations ──────────────────────────────────────────

    def get(self, key: str) -> CacheEntry | None:
        """Retrieve a cached entry if not expired."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            if time.time() - entry.created_at > self._ttl:
                del self._store[key]
                self._stale_evictions += 1
                self._misses += 1
                return None
            entry.hit_count += 1
            self._hits += 1
            return entry

    def put(self, key: str, entry: CacheEntry) -> None:
        """Store a cache entry, evicting oldest if at capacity."""
        with self._lock:
            if len(self._store) >= self._max_entries:
                # Evict oldest entry by creation time
                oldest_key = min(
                    self._store, key=lambda k: self._store[k].created_at,
                )
                del self._store[oldest_key]
            self._store[key] = entry

    def clear(self) -> int:
        """Clear all entries. Returns count of entries removed."""
        with self._lock:
            count = len(self._store)
            self._store.clear()
            return count

    def invalidate_for_collection(self, collection_version: int) -> int:
        """Remove entries from an older collection version."""
        with self._lock:
            stale = [
                k for k, v in self._store.items()
                if v.collection_version < collection_version
            ]
            for k in stale:
                del self._store[k]
            self._stale_evictions += len(stale)
            return len(stale)

    # ── Stats ─────────────────────────────────────────────────────

    @property
    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = self._hits + self._misses
            return {
                "entries": len(self._store),
                "max_entries": self._max_entries,
                "ttl_seconds": self._ttl,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": self._hits / max(total, 1),
                "stale_evictions": self._stale_evictions,
                "collection_version": self._collection_version,
            }


# ── Global singleton ───────────────────────────────────────────

_cache: AnswerCache | None = None


def get_answer_cache() -> AnswerCache:
    global _cache
    if _cache is None:
        _cache = AnswerCache(
            ttl=settings.rag_answer_cache_ttl_seconds,
            max_entries=settings.rag_answer_cache_max_entries,
        )
    return _cache


def bump_collection_version() -> int:
    """Invalidate the answer cache (called from ingestion pipeline)."""
    if not settings.rag_answer_cache_enabled:
        return -1
    return get_answer_cache().bump_collection_version()
