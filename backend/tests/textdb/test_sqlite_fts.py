import pytest

from textdb.sqlite_fts import SQLiteFTS5


@pytest.mark.asyncio
async def test_insert_and_search():
    fts = SQLiteFTS5()
    await fts.insert("chunk_1", "doc_test_1", "项目部署需要配置环境变量和数据库连接")
    await fts.insert("chunk_2", "doc_test_1", "数据库连接需要设置用户名和密码")
    await fts.insert("chunk_3", "doc_test_2", "前端使用 React 和 TypeScript")

    try:
        results = await fts.search("部署", top_k=5)
        assert len(results) >= 1
        assert results[0].chunk_id == "chunk_1"

        results = await fts.search("数据库连接", top_k=5)
        assert len(results) >= 1

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
    """CJK multi-char query should use AND matching, not require contiguous phrase."""
    fts = SQLiteFTS5()
    await fts.insert("ch_cjk_1", "doc_cjk", "机器学习在人工智能领域有广泛应用")
    await fts.insert("ch_cjk_2", "doc_cjk", "深度学习框架包括TensorFlow和PyTorch")
    try:
        results = await fts.search("机器学习", top_k=5)
        assert len(results) >= 1
        # chunk_1 has all four chars, should rank higher
        assert results[0].chunk_id == "ch_cjk_1"

        # Mixed CJK + ASCII: words don't need to be contiguous
        results = await fts.search("TensorFlow深度学习", top_k=5)
        assert len(results) >= 1
    finally:
        await fts.delete_by_document("doc_cjk")
