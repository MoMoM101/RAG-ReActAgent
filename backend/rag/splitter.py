from dataclasses import dataclass
import re

import tiktoken


@dataclass
class Chunk:
    text: str
    chunk_index: int
    page_num: int | None = None


def _find_table_boundary(text: str, cut: int) -> int | None:
    """If `cut` falls inside a markdown table, return the row boundary above it.
    A table is defined by a `|---|` separator line followed by data rows.
    Returns None if not inside a table or no safe boundary found."""
    sep_match = re.search(r"\n\|[-\s|]+\|\s*\n", text)
    if not sep_match:
        return None
    sep_end = sep_match.end()
    if cut <= sep_end:
        return None  # cut is above or at the table separator, not inside the body
    # Find the row boundary just above the cut point
    prev_nl = text.rfind("\n", sep_end, cut)
    if prev_nl > sep_end:
        return prev_nl
    return None


def _choose_cut(chunk_text: str) -> int:
    """Find the best natural boundary to cut, prioritizing paragraph breaks.
    Returns the position of the highest-priority boundary past the 50% mark."""
    threshold = len(chunk_text) // 2

    # Priority order: paragraph break > markdown header > sentence end > single newline
    paragraph = chunk_text.rfind("\n\n")
    if paragraph > threshold:
        return paragraph

    md = max((m.start() for m in re.finditer(r"\n#{1,6}\s", chunk_text)), default=-1)
    if md > threshold:
        return md

    period_cn = chunk_text.rfind("。")
    if period_cn > threshold:
        return period_cn

    period_en = max(chunk_text.rfind(". "), chunk_text.rfind("? "), chunk_text.rfind("! "))
    if period_en > threshold:
        return period_en

    newline = chunk_text.rfind("\n")
    if newline > threshold:
        return newline

    return -1


def split_text(
    text: str,
    chunk_size: int = 200,
    chunk_overlap: int = 40,
    encoding_name: str = "cl100k_base",
) -> list[Chunk]:
    enc = tiktoken.get_encoding(encoding_name)
    tokens = enc.encode(text)

    chunks: list[Chunk] = []
    start = 0
    idx = 0

    while start < len(tokens):
        end = min(start + chunk_size, len(tokens))
        chunk_tokens = tokens[start:end]
        chunk_text = enc.decode(chunk_tokens)

        if end < len(tokens):
            cut = _choose_cut(chunk_text)
            table_cut = _find_table_boundary(chunk_text, cut)
            if table_cut is not None:
                cut = table_cut
            if cut > len(chunk_text) * 0.5:
                chunk_text = chunk_text[:cut + 1]
                actual_tokens = len(enc.encode(chunk_text))
                trimmed = len(chunk_tokens) - actual_tokens
                end = end - trimmed

        chunks.append(Chunk(text=chunk_text.strip(), chunk_index=idx))
        idx += 1
        start = end - chunk_overlap if end < len(tokens) else end

    return chunks
