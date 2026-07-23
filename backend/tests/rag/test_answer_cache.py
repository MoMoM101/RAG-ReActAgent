"""Tests for version-aware answer cache (V4 Phase 4)."""

import time

import pytest

from rag.answer_cache import AnswerCache, CacheEntry


@pytest.fixture
def cache():
    return AnswerCache(ttl=2, max_entries=10)


def make_entry(
    answer: str = "test answer",
    sources: list | None = None,
    cv: int = 0,
) -> CacheEntry:
    return CacheEntry(
        answer=answer,
        sources=sources or [],
        verification=None,
        collection_version=cv,
    )


class TestCacheKey:
    def test_same_inputs_produce_same_key(self):
        k1 = AnswerCache.compute_key("query", "hash1", 1, "gpt-4o", "v1")
        k2 = AnswerCache.compute_key("query", "hash1", 1, "gpt-4o", "v1")
        assert k1 == k2

    def test_different_query_produces_different_key(self):
        k1 = AnswerCache.compute_key("query A", "hash1", 1)
        k2 = AnswerCache.compute_key("query B", "hash1", 1)
        assert k1 != k2

    def test_different_collection_version_produces_different_key(self):
        k1 = AnswerCache.compute_key("query", "hash1", 1)
        k2 = AnswerCache.compute_key("query", "hash1", 2)
        assert k1 != k2

    def test_query_normalization_is_case_insensitive(self):
        k1 = AnswerCache.compute_key("Hello World", "hash1", 1)
        k2 = AnswerCache.compute_key("hello world", "hash1", 1)
        assert k1 == k2

    @staticmethod
    def test_retrieval_hash_is_stable():
        sources = [
            {"chunk_id": "abc", "document_key": "doc1", "section_key": "s1"},
            {"chunk_id": "def", "document_key": "doc2", "section_key": "s2"},
        ]
        h1 = AnswerCache.retrieval_hash(sources)
        h2 = AnswerCache.retrieval_hash(sources)
        assert h1 == h2

    @staticmethod
    def test_retrieval_hash_ignores_score():
        s1 = [{"chunk_id": "abc", "score": 0.9}]
        s2 = [{"chunk_id": "abc", "score": 0.3}]
        assert AnswerCache.retrieval_hash(s1) == AnswerCache.retrieval_hash(s2)

    @staticmethod
    def test_retrieval_hash_distinguishes_legacy_sources_without_chunk_keys():
        first = [{"document_id": "doc-1", "text": "first source"}]
        second = [{"document_id": "doc-2", "text": "second source"}]
        assert AnswerCache.retrieval_hash(first) != AnswerCache.retrieval_hash(second)


class TestCacheOperations:
    def test_put_and_get(self, cache):
        key = AnswerCache.compute_key("q", "h", 1)
        cache.put(key, make_entry(cv=1))
        entry = cache.get(key)
        assert entry is not None
        assert entry.answer == "test answer"

    def test_miss_on_nonexistent_key(self, cache):
        assert cache.get("nonexistent") is None

    def test_ttl_expiry(self, cache):
        key = AnswerCache.compute_key("q", "h", 1)
        cache.put(key, make_entry(cv=1))
        time.sleep(2.1)  # ttl=2
        assert cache.get(key) is None

    def test_bump_collection_version_clears_all(self, cache):
        for i in range(3):
            key = AnswerCache.compute_key(f"q{i}", f"h{i}", 0)
            cache.put(key, make_entry(cv=0))
        assert cache.stats["entries"] == 3
        cache.bump_collection_version()
        assert cache.stats["entries"] == 0
        assert cache.collection_version == 1

    def test_eviction_when_full(self, cache):
        small = AnswerCache(ttl=300, max_entries=3)
        for i in range(5):
            key = AnswerCache.compute_key(f"q{i}", f"h{i}", 0)
            small.put(key, make_entry(cv=0))
        assert small.stats["entries"] <= 3

    def test_stats_accuracy(self, cache):
        key = AnswerCache.compute_key("q", "h", 1)
        cache.put(key, make_entry(cv=1))
        cache.get(key)  # hit
        cache.get("nonexistent")  # miss
        stats = cache.stats
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["entries"] == 1


class TestInvalidation:
    def test_invalidate_removes_stale_entries(self, cache):
        key1 = AnswerCache.compute_key("q1", "h1", 1)
        key2 = AnswerCache.compute_key("q2", "h2", 2)
        cache.put(key1, make_entry(cv=1))
        cache.put(key2, make_entry(cv=2))
        removed = cache.invalidate_for_collection(2)
        assert removed == 1  # key1 evicted
        assert cache.get(key1) is None
        assert cache.get(key2) is not None
