from rag.splitter import split_text


def test_split_short_text():
    text = "这是一段简短的测试文本。"
    chunks = split_text(text, chunk_size=100, chunk_overlap=10)
    assert len(chunks) == 1
    assert chunks[0].text == text
    assert chunks[0].chunk_index == 0


def test_split_long_text():
    text = "这是测试。\n" * 200
    chunks = split_text(text, chunk_size=50, chunk_overlap=10)
    assert len(chunks) > 1
    for i, chunk in enumerate(chunks):
        assert chunk.chunk_index == i
        assert len(chunk.text) > 0


def test_overlap():
    text = "第一段内容。第二段内容。第三段内容。" * 50
    chunks = split_text(text, chunk_size=30, chunk_overlap=10)
    # Verify chunks exist and have content
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk.text) > 0
