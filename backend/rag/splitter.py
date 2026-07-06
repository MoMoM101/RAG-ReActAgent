import tiktoken
from dataclasses import dataclass


@dataclass
class Chunk:
    text: str
    chunk_index: int
    page_num: int | None = None


def split_text(
    text: str,
    chunk_size: int = 512,
    chunk_overlap: int = 50,
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

        # Try to break at natural boundaries (Chinese/English sentence end + newline)
        if end < len(tokens):
            last_period = chunk_text.rfind("。")
            last_newline = chunk_text.rfind("\n")
            last_en = max(chunk_text.rfind(". "), chunk_text.rfind("? "), chunk_text.rfind("! "))
            cut = max(last_period, last_newline, last_en)
            if cut > len(chunk_text) * 0.5:
                chunk_text = chunk_text[:cut + 1]
                # Re-encode trimmed text to get actual token count so
                # end and overlap stay correct
                actual_tokens = len(enc.encode(chunk_text))
                trimmed = len(chunk_tokens) - actual_tokens
                end = end - trimmed

        chunks.append(Chunk(text=chunk_text.strip(), chunk_index=idx))
        idx += 1
        start = end - chunk_overlap if end < len(tokens) else end

    return chunks
