"""Unit tests for BM25Search keyword search implementation."""
import contextlib

import pytest

from textdb.bm25_search import BM25Search, _closest_fuzzy_term, tokenize


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
async def test_search_corrects_one_character_latin_typo(bm25):
    await bm25.insert("c1", "d1", "Carbonara 意面使用 guanciale")
    await bm25.insert("c2", "d2", "Paella 使用 Bomba 米")

    results = await bm25.search("cabonara 意面", top_k=5)

    assert results
    assert results[0].chunk_id == "c1"


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
async def test_insert_batch_document(bm25):
    """insert_batch should work for multi-chunk document."""
    entries = [
        ("c1", "d1", "doc-key", "s1", 0, "python is great for data science"),
        ("c2", "d1", "doc-key", "s2", 1, "python supports machine learning"),
        ("c3", "d1", "doc-key", "s3", 2, "python runs on many platforms"),
    ]
    await bm25.insert_batch(entries)

    assert await bm25.count() == 3
    results = await bm25.search("python", top_k=5)
    assert len(results) == 3


@pytest.mark.asyncio
async def test_insert_batch_df_counts_chunks(bm25):
    """df should count the number of chunks containing a term, not +1 per batch."""
    entries = [
        ("c1", "d1", "doc-key", "s1", 0, "python is great for data science"),
        ("c2", "d1", "doc-key", "s2", 1, "python supports machine learning"),
        ("c3", "d1", "doc-key", "s3", 2, "python runs on many platforms"),
    ]
    await bm25.insert_batch(entries)

    rows = await bm25._query(
        f"SELECT term, df FROM {bm25._stats} WHERE term = 'python'"
    )
    assert len(rows) == 1
    assert rows[0][1] == 3, f"Expected df=3 (3 chunks contain 'python'), got df={rows[0][1]}"


@pytest.mark.asyncio
async def test_insert_batch_df_shared_term(bm25):
    """When only some chunks share a term, df reflects actual count."""
    entries = [
        ("c1", "d1", "doc-key", "s1", 0, "python is great for scripting"),
        ("c2", "d1", "doc-key", "s2", 1, "python for data analysis"),
        ("c3", "d1", "doc-key", "s3", 2, "only machine learning and deep learning"),
    ]
    await bm25.insert_batch(entries)

    rows = await bm25._query(
        f"SELECT term, df FROM {bm25._stats} WHERE term = 'python'"
    )
    assert len(rows) == 1
    assert rows[0][1] == 2, f"Expected df=2 (2 chunks contain 'python'), got df={rows[0][1]}"


@pytest.mark.asyncio
async def test_delete_document_decrements_df(bm25):
    """After delete_by_document, df should decrease for terms in deleted chunks."""
    entries = [
        ("c1", "d1", "doc-key", "s1", 0, "python sklearn"),
        ("c2", "d1", "doc-key", "s2", 1, "python tensorflow"),
        ("c3", "d2", "doc-key2", "s1", 0, "java spring"),
    ]
    await bm25.insert_batch(entries)

    # Verify initial df
    rows = await bm25._query(
        f"SELECT term, df FROM {bm25._stats} WHERE term = 'python'"
    )
    assert rows[0][1] == 2

    await bm25.delete_by_document("d1")

    # After deleting d1, df for 'python' should be 0 (and row removed)
    rows_after = await bm25._query(
        f"SELECT term, df FROM {bm25._stats} WHERE term = 'python'"
    )
    assert len(rows_after) == 0, "df should be 0 and row deleted after removing both chunks"


@pytest.mark.asyncio
async def test_insert_batch_df_no_inflation_on_reinsert(bm25):
    """Re-inserting same chunk_ids must not inflate df (correct upsert)."""
    entries = [
        ("c1", "d1", "doc-key", "s1", 0, "hello world"),
        ("c2", "d1", "doc-key", "s2", 1, "hello again"),
    ]
    await bm25.insert_batch(entries)

    rows1 = await bm25._query(
        f"SELECT term, df FROM {bm25._stats} WHERE term = 'hello'"
    )
    df1 = rows1[0][1] if rows1 else 0
    assert df1 == 2, f"Initial df should be 2, got {df1}"

    # Re-insert same chunks — df must NOT inflate
    await bm25.insert_batch(entries)

    rows2 = await bm25._query(
        f"SELECT term, df FROM {bm25._stats} WHERE term = 'hello'"
    )
    df2 = rows2[0][1] if rows2 else 0
    assert df2 == 2, f"Re-insert must not inflate df, expected 2 got {df2}"


@pytest.mark.asyncio
async def test_insert_reinsert_no_df_inflation(bm25):
    """Single insert re-insert must not inflate df."""
    await bm25.insert("c1", "d1", "hello world", document_key="dk", section_key="s1")
    rows = await bm25._query(
        f"SELECT term, df FROM {bm25._stats} WHERE term = 'hello'"
    )
    assert rows[0][1] == 1

    # Re-insert same chunk with different text
    await bm25.insert("c1", "d1", "hello universe", document_key="dk", section_key="s1")
    rows = await bm25._query(
        f"SELECT term, df FROM {bm25._stats} WHERE term = 'hello'"
    )
    assert rows[0][1] == 1, "df must not inflate on re-insert"

    # Old term 'world' should be gone
    world_rows = await bm25._query(
        f"SELECT term, df FROM {bm25._stats} WHERE term = 'world'"
    )
    assert len(world_rows) == 0, "old term 'world' should be removed"


@pytest.mark.asyncio
async def test_text_changed_old_term_not_found(bm25):
    """After text change, old terms must not match in search."""
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


def test_fuzzy_correction_is_conservative():
    assert _closest_fuzzy_term("cabonara", ["carbonara", "climate"]) == "carbonara"
    assert _closest_fuzzy_term("api", ["app"]) is None
    assert _closest_fuzzy_term("err40003", ["err40005"]) is None


@pytest.mark.asyncio
async def test_search_returns_document_key_and_section_key(bm25):
    """Keyword results must carry document_key and section_key (Gate 8)."""
    await bm25.insert("c1", "d1", "Python 机器学习入门", document_key="ml-guide", section_key="intro")
    results = await bm25.search("Python 机器学习", top_k=5)
    assert len(results) >= 1
    assert results[0].document_key == "ml-guide"
    assert results[0].section_key == "intro"


@pytest.mark.asyncio
async def test_insert_batch_returns_document_key(bm25):
    """Batch-inserted keyword results must carry stable keys."""
    entries = [
        ("c1", "d1", "ml-guide", "s1", 0, "Python machine learning"),
        ("c2", "d1", "ml-guide", "s2", 1, "deep learning"),
    ]
    await bm25.insert_batch(entries)
    results = await bm25.search("machine learning", top_k=5)
    assert len(results) >= 1
    assert results[0].document_key == "ml-guide"
