import pytest

from textdb.sqlite_fts import SQLiteFTS5


@pytest.mark.asyncio
async def test_insert_and_search():
    fts = SQLiteFTS5()
    await fts.insert("chunk_1", "doc_test_1", "项目部署需要配置环境变量和数据库连接")
    await fts.insert("chunk_2", "doc_test_1", "数据库连接需要设置用户名和密码")
    await fts.insert("chunk_3", "doc_test_2", "前端使用 React 和 TypeScript")

    try:
        # trigram: "部署" is 2 chars → LIKE fallback, should hit chunk_1
        results = await fts.search("部署", top_k=5)
        assert len(results) >= 1
        assert results[0].chunk_id == "chunk_1"

        # "数据库连接" is 5 chars → trigram MATCH
        results = await fts.search("数据库连接", top_k=5)
        assert len(results) >= 1

        # English search
        results = await fts.search("React", top_k=5)
        assert len(results) >= 1
        assert results[0].chunk_id == "chunk_3"

        await fts.delete_by_document("doc_test_1")
        count = await fts.count()
        assert count == 1
    finally:
        await fts.delete_by_document("doc_test_1")
        await fts.delete_by_document("doc_test_2")


@pytest.mark.asyncio
async def test_delete_by_chunks():
    fts = SQLiteFTS5()
    await fts.insert("ch_a", "doc_test_x", "text a")
    await fts.insert("ch_b", "doc_test_x", "text b")
    await fts.insert("ch_c", "doc_test_x", "text c")

    try:
        await fts.delete_by_chunks(["ch_a", "ch_c"])
        count = await fts.count()
        assert count == 1
    finally:
        await fts.delete_by_document("doc_test_x")


def test_escape_fts5_no_quotes():
    """_escape_fts5 should NOT wrap output in double quotes."""
    result = SQLiteFTS5._escape_fts5("test query")
    assert '"' not in result
    assert result == "test query"


def test_escape_fts5_strips_operators():
    """_escape_fts5 should strip FTS5 special characters."""
    result = SQLiteFTS5._escape_fts5('test* (group) ^"phrase"')
    assert "*" not in result
    assert '"' not in result
    assert "(" not in result
    assert ")" not in result
    assert "^" not in result


def test_escape_fts5_cjk_passthrough():
    """CJK characters should pass through unchanged (segmentation is separate)."""
    result = SQLiteFTS5._escape_fts5("机器学习")
    assert "机" in result
    assert "器" in result
    assert "学" in result
    assert "习" in result


def test_escape_fts5_handles_hyphen():
    """Hyphens should be replaced with spaces to avoid FTS5 column-filter parsing."""
    result = SQLiteFTS5._escape_fts5("XYZ-9000")
    assert "-" not in result
    assert "XYZ" in result
    assert "9000" in result


@pytest.mark.asyncio
async def test_cjk_multi_char_and_search():
    """Trigram tokenizer correctly handles CJK multi-char queries."""
    fts = SQLiteFTS5()
    await fts.insert("ch_cjk_1", "doc_cjk", "机器学习在人工智能领域有广泛应用")
    await fts.insert("ch_cjk_2", "doc_cjk", "深度学习框架包括TensorFlow和PyTorch")
    try:
        # "机器学习" is 4 chars > 2, uses trigram MATCH
        results = await fts.search("机器学习", top_k=5)
        assert len(results) >= 1
        assert results[0].chunk_id == "ch_cjk_1"

        # Mixed CJK + ASCII: trigram handles uniformly as separate tokens
        results = await fts.search("TensorFlow 深度学习", top_k=5)
        assert len(results) >= 1
    finally:
        await fts.delete_by_document("doc_cjk")


@pytest.mark.asyncio
async def test_trigram_short_query_fallback():
    """Queries of 1-2 characters fall back to LIKE search."""
    fts = SQLiteFTS5()
    await fts.insert("ch_short_1", "doc_short", "AI 是人工智能的缩写")
    await fts.insert("ch_short_2", "doc_short", "Python 是一种编程语言")
    try:
        # "AI" is 2 chars → LIKE fallback
        results = await fts.search("AI", top_k=5)
        assert len(results) >= 1
        assert results[0].chunk_id == "ch_short_1"

        # "语" is 1 char → LIKE fallback
        results = await fts.search("语", top_k=5)
        assert len(results) >= 1
        assert results[0].chunk_id == "ch_short_2"
    finally:
        await fts.delete_by_document("doc_short")


@pytest.mark.asyncio
async def test_trigram_mixed_chinese_english():
    """Trigram handles mixed Chinese/English/numbers uniformly."""
    fts = SQLiteFTS5()
    await fts.insert("ch_mix_1", "doc_mix", "RAG (Retrieval-Augmented Generation) 检索增强生成")
    await fts.insert("ch_mix_2", "doc_mix", "BAAI/bge-reranker-v2-m3 模型用于重排序")
    try:
        results = await fts.search("RAG 检索", top_k=5)
        assert len(results) >= 1
        assert results[0].chunk_id == "ch_mix_1"

        results = await fts.search("bge-reranker-v2-m3", top_k=5)
        assert len(results) >= 1
        assert results[0].chunk_id == "ch_mix_2"
    finally:
        await fts.delete_by_document("doc_mix")
