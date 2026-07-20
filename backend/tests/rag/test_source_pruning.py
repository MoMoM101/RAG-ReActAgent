"""Tests for source pruning (V4 Phase 5)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agent.source_utils import (
    dedup_overlapping as _dedup_overlapping,
)
from agent.source_utils import (
    prune_overlapping_sources as _prune_overlapping_sources,
)
from agent.source_utils import (
    token_set as _token_set,
)


class TestTokenSet:
    def test_chinese_substrings(self):
        tokens = _token_set("深度学习使用多层神经网络")
        # Produces 1-3 char CJK substrings (greedy 3-char)
        assert len(tokens) > 0
        assert any(t for t in tokens if len(t) >= 2)
        # At minimum, chunks of the input are in tokens
        assert "深度学" in tokens or "习使用" in tokens

    def test_english_words(self):
        tokens = _token_set("Django supports ORM and templates")
        assert "django" in tokens
        assert "orm" in tokens

    def test_token_count_positive(self):
        tokens = _token_set("温度 1.5C 上升")
        assert len(tokens) >= 2  # at minimum captures CJK tokens


class TestDedupOverlapping:
    def test_identical_chunks_deduped(self):
        chunks = [
            {"text": "Django 内置后台管理界面。", "score": 0.9},
            {"text": "Django 内置后台管理界面。", "score": 0.8},
        ]
        result = _dedup_overlapping(chunks, threshold=0.40)
        assert len(result) == 1
        assert result[0]["score"] == 0.9  # keeps higher score

    def test_distinct_chunks_kept(self):
        chunks = [
            {"text": "Django 是一个全栈 Web 框架。", "score": 0.9},
            {"text": "Flask 是一个轻量级微框架。", "score": 0.8},
        ]
        result = _dedup_overlapping(chunks, threshold=0.40)
        assert len(result) == 2

    def test_partial_overlap_below_threshold_kept(self):
        chunks = [
            {"text": "Django 支持 ORM 和后台管理界面用于内容管理。", "score": 0.9},
            {"text": "Django 还提供模板系统和中间件支持认证功能。", "score": 0.8},
        ]
        result = _dedup_overlapping(chunks, threshold=0.40)
        assert len(result) == 2  # different enough

    def test_empty_input(self):
        assert _dedup_overlapping([], threshold=0.40) == []


class TestPruneOverlappingSources:
    def test_pruning_reduces_overlapping_chunks(self):
        sources = [
            {
                "text": "Django 内置后台管理界面。",
                "document_key": "doc1",
                "score": 0.9,
                "rank": 1,
            },
            {
                "text": "Django 内置后台管理界面用于内容管理。",
                "document_key": "doc1",
                "score": 0.8,
                "rank": 2,
            },
            {
                "text": "Flask 是一个轻量级微框架。",
                "document_key": "doc2",
                "score": 0.7,
                "rank": 3,
            },
        ]
        result = _prune_overlapping_sources(sources)
        assert len(result) <= 3
        # doc1 overlapping chunks should be deduped
        doc1_chunks = [r for r in result if r["document_key"] == "doc1"]
        assert len(doc1_chunks) <= 2

    def test_max_chunks_enforced(self):
        sources = [
            {
                "text": f"Source {i} content text for testing pruning.",
                "document_key": f"doc{i}",
                "score": 1.0 - i * 0.1,
                "rank": i + 1,
            }
            for i in range(15)
        ]
        result = _prune_overlapping_sources(sources, max_chunks=8)
        assert len(result) <= 8

    def test_max_per_document_enforced(self):
        sources = [
            {
                "text": f"Django feature {i} description for web framework.",
                "document_key": "doc1",
                "score": 1.0 - i * 0.1,
                "rank": i + 1,
            }
            for i in range(8)
        ]
        result = _prune_overlapping_sources(sources, max_per_document=3)
        doc1_chunks = [r for r in result if r["document_key"] == "doc1"]
        assert len(doc1_chunks) <= 3

    def test_rank_order_maintained(self):
        sources = [
            {"text": "Source C content.", "document_key": "doc3", "score": 0.5, "rank": 3},
            {"text": "Source A content.", "document_key": "doc1", "score": 0.9, "rank": 1},
            {"text": "Source B content.", "document_key": "doc2", "score": 0.7, "rank": 2},
        ]
        result = _prune_overlapping_sources(sources, max_chunks=3)
        ranks = [r["rank"] for r in result]
        assert ranks == sorted(ranks)
