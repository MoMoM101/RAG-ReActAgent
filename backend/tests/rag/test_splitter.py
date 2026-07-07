from rag.splitter import _choose_cut, _find_table_boundary, split_text


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
    header_found = any("## 模型评估" in c.text for c in chunks)
    assert header_found, "Markdown header should appear at start of a chunk"


def test_paragraph_break_priority():
    """Paragraph breaks (\\n\\n) should be preferred when past 50% threshold."""
    # Paragraph break at position 55, text goes to ~80 — well past 50%
    prefix = "填充文字。" * 10
    suffix = "\n\n第二段落不同主题的开始。更多内容填充到足够长以触发切割逻辑。"
    text = prefix + suffix
    chunks = split_text(text, chunk_size=25, chunk_overlap=5)
    assert len(chunks) >= 1
    if len(chunks) >= 2:
        assert "第二段落" not in chunks[0].text


def test_table_boundary_protection():
    """Chunks should keep table rows together when possible."""
    intro = "表格数据如下：\n\n"
    table = (
        "| 名称 | 数值 | 说明 |\n"
        "|---|------|------|\n"
        "| 项目A | 100 | 测试数据 |\n"
        "| 项目B | 200 | 更多数据 |\n"
        "| 项目C | 300 | 补充信息 |\n"
    )
    text = intro + table
    chunks = split_text(text, chunk_size=60, chunk_overlap=10)
    table_chunks = [c for c in chunks if "项目" in c.text]
    assert len(table_chunks) >= 1
    for tc in table_chunks:
        if "项目A" in tc.text and "项目B" in tc.text:
            assert "项目C" in tc.text, "Table rows should stay together"


def test_choose_cut_paragraph_first():
    """_choose_cut returns paragraph break when it falls past 50% of text."""
    prefix = "填充文字。" * 4  # ~16 chars
    text = prefix + "\n\n第二段。"
    cut = _choose_cut(text)
    para_pos = text.find("\n\n")
    assert para_pos > len(text) // 2, "Test setup fail: para must be past 50%"
    assert cut == para_pos


def test_choose_cut_falls_back_to_period():
    """When no paragraph break past 50%, falls back to period."""
    text = "短前言。继续写一些内容，让文本变长到超过阈值线。最后是句号。"
    cut = _choose_cut(text)
    last_period = text.rfind("。")
    assert cut == last_period


def test_find_table_boundary_inside_table():
    """_find_table_boundary detects cut inside table and returns row boundary."""
    table = (
        "| 名称 | 数值 |\n"
        "|---|------|\n"
        "| 项目A | 100 |\n"
        "| 项目B | 200 |\n"
    )
    cut_inside = table.find("项目B")
    result = _find_table_boundary(table, cut_inside)
    assert result is not None
    assert result < cut_inside


def test_find_table_boundary_no_table():
    """_find_table_boundary returns None when no table present."""
    text = "普通文本，没有表格。\n只有一些换行。\n"
    result = _find_table_boundary(text, len(text) - 1)
    assert result is None
