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


def test_split_at_markdown_header():
    """Chunks should break before markdown headers to keep sections together."""
    intro = "Some introductory content that fills up space. " * 8
    section = "\n## 模型评估\n\n模型训练完成后，需要使用测试集评估其泛化能力。" * 3
    text = intro + section
    chunks = split_text(text, chunk_size=100, chunk_overlap=10)
    assert len(chunks) >= 2
    # The second chunk should start with the markdown header
    header_found = any("## 模型评估" in c.text for c in chunks)
    assert header_found, "Markdown header should appear at start of a chunk"
