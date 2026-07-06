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
