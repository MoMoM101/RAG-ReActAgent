"""Splitter edge case tests — uncovered branches in _find_table_boundary and _choose_cut."""

from rag.splitter import _choose_cut, _find_table_boundary


class TestFindTableBoundary:
    def test_no_table_returns_none(self):
        text = "This is a paragraph.\n\nAnother paragraph with no tables."
        assert _find_table_boundary(text, 50) is None

    def test_cut_above_separator_returns_none(self):
        """cut <= sep_end → returns None (cut is before/at separator line)."""
        text = "Header row\n|---|\nData row 1\nData row 2"
        sep_match_start = text.index("|---|")
        # cut before the separator
        assert _find_table_boundary(text, sep_match_start) is None

    def test_cut_inside_table_body_returns_row_boundary(self):
        """cut inside table body → returns previous newline position."""
        text = "Before\nHeader\n|---|\nRow 1\nRow 2\nAfter"
        # cut inside Row 2
        cut = text.index("Row 2") + 2
        result = _find_table_boundary(text, cut)
        assert result is not None
        # Should be the newline before "Row 2"
        assert result == text.index("\nRow 2")

    def test_single_row_table_no_prev_nl_returns_none(self):
        """When prev_nl is not found between sep_end and cut."""
        text = "|---|\nOnlyRow"  # No newline between separator end and row
        cut = len(text)  # cut at end
        result = _find_table_boundary(text, cut)
        # sep_match finds the |---| separator, but there's no newline between
        # separator end and the row start
        assert result is None

    def test_inside_table_multiple_rows(self):
        """Multi-row table: cut in 3rd row finds boundary before it."""
        text = "Before\n| Col1 | Col2 |\n|---|---|\n| a | b |\n| c | d |\nAfter"
        # Cut in the middle of "| c | d |"
        cut = text.rfind("| c |")
        result = _find_table_boundary(text, cut)
        assert result is not None
        # Should find the newline before "| c | d |"
        assert text[result] == "\n"


class TestChooseCut:
    def test_paragraph_break_priority(self):
        # rfind finds the LAST "\n\n" — must be in second half
        chunk = "prefix " * 20 + "\n\ncontent" * 15
        threshold = len(chunk) // 2
        result = _choose_cut(chunk)
        assert result > threshold
        assert "\n\n" in chunk[result:result + 3]

    def test_markdown_header_priority(self):
        # No paragraph break → should fall to markdown header
        chunk = "prefix line here. " * 15 + "\n## Header\n" + "content " * 10
        threshold = len(chunk) // 2
        result = _choose_cut(chunk)
        assert result > threshold

    def test_chinese_period_priority(self):
        chunk = ("这是第一句话。这是第二句话。" * 5) + "这是结尾部分"
        result = _choose_cut(chunk)
        assert result > 0
        assert "。" in chunk[max(0, result - 1):result + 1]

    def test_english_period_priority(self):
        # ". " must appear after the 50% threshold (rfind finds the LAST occurrence)
        chunk = "no periods here just filler " * 10 + "ending sentence. " + "extra " * 5
        result = _choose_cut(chunk)
        assert result > 0

    def test_english_question_mark_priority(self):
        # "? " in the second half of text (rfind finds the last one)
        chunk = "pad " * 25 + "Is this right? " + "morepad " * 5
        result = _choose_cut(chunk)
        assert result > 0

    def test_fallback_to_single_newline(self):
        """No paragraph/sentence boundary → falls back to single \n."""
        text = "word1\nword2\nword3\nword4\nword5\nword6\nword7\nword8\nword9\nword10"
        result = _choose_cut(text)
        assert result > 0

    def test_return_minus_one_when_no_boundary(self):
        """No suitable boundary found → returns -1."""
        text = "A" * 300  # No newlines, no periods
        result = _choose_cut(text)
        assert result == -1
