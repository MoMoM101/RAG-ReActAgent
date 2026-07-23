import re
from dataclasses import dataclass

import tiktoken


@dataclass
class Chunk:
    text: str
    chunk_index: int
    page_num: int | None = None
    section_key: str = ""  # stable section identifier from nearest markdown header


def _normalize_section_key(title: str) -> str:
    key = re.sub(r"[^a-zA-Z0-9一-鿿_-]", "-", title.strip()).strip("-")
    return key[:30].lower()


def _markdown_sections(text: str) -> list[tuple[str, str]]:
    """Split Markdown while carrying ancestor headings into child sections.

    Child chunks need their document/parent topic for long natural-language
    queries. The inherited context is plain text so recursive splitting does
    not treat it as another Markdown section boundary.
    """
    headers = list(re.finditer(r"^#{1,6}\s+(.+)$", text, re.MULTILINE))
    if len(headers) <= 1:
        return [(text, _normalize_section_key(headers[0].group(1)) if headers else "")]

    sections: list[tuple[str, str]] = []
    if headers[0].start() > 0 and text[:headers[0].start()].strip():
        sections.append((text[:headers[0].start()].strip(), ""))
    ancestors: list[tuple[int, str]] = []
    for index, header in enumerate(headers):
        level = len(header.group(0)) - len(header.group(0).lstrip("#"))
        ancestors = [(depth, title) for depth, title in ancestors if depth < level]
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        section_text = text[header.start():end].strip()
        # A bare heading is metadata, not retrievable evidence. Keeping a
        # document-title-only chunk can rank it above the child section that
        # actually answers the query. Children still inherit the heading.
        heading_only = section_text == header.group(0).strip()
        if section_text and not heading_only:
            if ancestors:
                path = " > ".join(title for _, title in ancestors)
                section_text = f"文档上下文：{path}\n\n{section_text}"
            sections.append((section_text, _normalize_section_key(header.group(1))))
        ancestors.append((level, header.group(1).strip()))
    return sections


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


def _section_key_for_range(text: str, start: int, end: int) -> str:
    """Return the most specific heading governing a chunk range.

    Overlap means a chunk often starts in the previous section and then
    contains a new heading plus most of the next section. In that case the
    last heading inside the chunk is a better stable identifier than the
    heading immediately preceding its start.
    """
    headers = list(re.finditer(r"^#{1,6}\s+(.+)$", text, re.MULTILINE))
    selected = None
    for header in headers:
        if header.start() >= end:
            break
        if header.start() <= start or start < header.start() < end:
            selected = header
    if selected is None:
        return ""
    return _normalize_section_key(selected.group(1))


def split_text(
    text: str,
    chunk_size: int = 200,
    chunk_overlap: int = 40,
    encoding_name: str = "cl100k_base",
) -> list[Chunk]:
    sections = _markdown_sections(text)
    if len(sections) > 1:
        section_chunks: list[Chunk] = []
        for section_text, section_key in sections:
            current_chunks = split_text(
                section_text,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                encoding_name=encoding_name,
            )
            for chunk in current_chunks:
                chunk.chunk_index = len(section_chunks)
                chunk.section_key = section_key
                section_chunks.append(chunk)
        return section_chunks

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

        # Convert token offsets to character offsets before assigning stable
        # section metadata; CJK characters can span multiple tokens.
        start_char = len(enc.decode(tokens[:start]))
        end_char = len(enc.decode(tokens[:end]))
        sk = _section_key_for_range(text, start_char, end_char)
        chunks.append(Chunk(text=chunk_text.strip(), chunk_index=idx, section_key=sk))
        idx += 1
        start = end - chunk_overlap if end < len(tokens) else end

    return chunks
