import pytest

from rag.loaders import load_csv, load_document, load_md, load_txt


def test_load_txt(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("Hello World\nLine 2", encoding="utf-8")
    text = load_txt(str(f))
    assert "Hello World" in text

def test_load_md(tmp_path):
    f = tmp_path / "test.md"
    f.write_text("# Title\nContent here", encoding="utf-8")
    text = load_md(str(f))
    assert "# Title" in text

def test_load_csv(tmp_path):
    f = tmp_path / "test.csv"
    f.write_text("name,age\nAlice,30\nBob,25", encoding="utf-8")
    text = load_csv(str(f))
    assert "Alice" in text
    assert "30" in text

def test_load_document_routing(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("test content", encoding="utf-8")
    text = load_document(str(f), ".txt")
    assert text == "test content"

def test_unsupported_type():
    with pytest.raises(ValueError, match="Unsupported"):
        load_document("test.xyz", ".xyz")
