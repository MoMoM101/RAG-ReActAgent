"""Unit tests for BM25Search keyword search implementation."""
import contextlib

import pytest

from textdb.bm25_search import BM25Search, tokenize


@pytest.fixture
async def bm25():
    """Create isolated BM25Search per test."""
    import uuid
    suffix = f"_t{uuid.uuid4().hex[:8]}"
    b = BM25Search(table_suffix=suffix)
    yield b
    # Clean up
    with contextlib.suppress(Exception):
        await b._batch_exec([
            f"DROP TABLE IF EXISTS {b._docs}",
            f"DROP TABLE IF EXISTS {b._idx}",
            f"DROP TABLE IF EXISTS {b._stats}",
        ])


@pytest.mark.asyncio
async def test_insert_and_search(bm25):
    await bm25.insert("c1", "d1", "Python machine learning with scikit-learn")
    await bm25.insert("c2", "d1", "Deep learning with TensorFlow and PyTorch")
    await bm25.insert("c3", "d2", "Climate change and renewable energy")

    results = await bm25.search("machine learning", top_k=5)
    assert len(results) >= 1
    # c1 should score highest for "machine learning"
    assert results[0].chunk_id == "c1"
    assert results[0].score > 0


@pytest.mark.asyncio
async def test_search_empty(bm25):
    results = await bm25.search("hello")
    assert results == []


@pytest.mark.asyncio
async def test_search_document_filter(bm25):
    await bm25.insert("c1", "d1", "refund order processing error")
    await bm25.insert("c2", "d2", "refund payment gateway error")

    results = await bm25.search("refund error", top_k=5, document_id="d1")
    assert len(results) >= 1
    for r in results:
        assert r.document_id == "d1"


@pytest.mark.asyncio
async def test_delete_by_document(bm25):
    await bm25.insert("c1", "d1", "test content one")
    await bm25.insert("c2", "d2", "test content two")

    await bm25.delete_by_document("d1")
    count = await bm25.count()
    assert count == 1

    results = await bm25.search("content")
    assert all(r.document_id == "d2" for r in results)


@pytest.mark.asyncio
async def test_delete_by_chunks(bm25):
    await bm25.insert("c1", "d1", "aaa")
    await bm25.insert("c2", "d1", "bbb")
    await bm25.insert("c3", "d2", "ccc")

    await bm25.delete_by_chunks(["c1", "c3"])
    assert await bm25.count() == 1


@pytest.mark.asyncio
async def test_count(bm25):
    assert await bm25.count() == 0
    await bm25.insert("c1", "d1", "hello")
    assert await bm25.count() == 1
    await bm25.insert("c2", "d1", "world")
    assert await bm25.count() == 2


@pytest.mark.asyncio
async def test_bm25_scoring_prefers_relevant(bm25):
    """Highly relevant document scores higher than weakly relevant."""
    await bm25.insert("c1", "d1", "refund refund refund order order")  # high TF
    await bm25.insert("c2", "d1", "process the customer payment order")

    results = await bm25.search("refund order", top_k=5)
    assert results[0].chunk_id == "c1"
    assert results[0].score > results[1].score


@pytest.mark.asyncio
async def test_chinese_tokenization(bm25):
    await bm25.insert("c1", "d1", "Python 机器学习使用 scikit-learn 框架")
    await bm25.insert("c2", "d1", "深度学习使用 TensorFlow 和 PyTorch")

    results = await bm25.search("机器学习", top_k=5)
    assert len(results) >= 1
    assert results[0].chunk_id == "c1"


@pytest.mark.asyncio
async def test_code_token_preserved(bm25):
    """ERR_40003 should be preserved as one token, not split."""
    await bm25.insert("c1", "d1", "Payment error: ERR_40003 amount exceeds limit")
    await bm25.insert("c2", "d1", "Payment error: ERR_40005 status not PAID")

    results = await bm25.search("ERR_40003", top_k=5)
    assert len(results) >= 1
    assert "ERR_40003" in results[0].text


@pytest.mark.asyncio
async def test_mixed_chinese_english(bm25):
    await bm25.insert("c1", "d1", "STM32H743VI MCU 支持 CAN FD 总线接口")
    await bm25.insert("c2", "d1", "ESP32-S3R8 支持 WiFi 和 BLE 5.0")

    results = await bm25.search("STM32H743VI", top_k=5)
    assert len(results) >= 1
    assert "STM32H743VI" in results[0].text


# ── Tokenizer unit tests ──

def test_tokenize_preserves_error_code():
    tokens = tokenize("ERR_40003 amount exceeds limit")
    assert "err_40003" in tokens


def test_tokenize_preserves_sku():
    tokens = tokenize("STM32H743VI MCU specifications")
    assert "stm32h743vi" in tokens


def test_tokenize_chinese_segmented():
    tokens = tokenize("机器学习是什么")
    # jieba should segment this into multiple tokens
    assert len(tokens) >= 2


def test_tokenize_mixed():
    tokens = tokenize("Python 机器学习框架 TensorFlow")
    assert "python" in tokens
    assert "tensorflow" in tokens


def test_tokenize_filters_punctuation():
    tokens = tokenize("hello, world! (test)")
    for t in tokens:
        assert "," not in t
        assert "!" not in t
        assert "(" not in t
