"""Chunking with paragraph-aware boundaries + overlap.

The naive fixed-character-count chunker destroys procedural context
(you split mid-sentence of a circular). This one walks the paragraph
list, accumulates until ~target_tokens, and overlaps by a small
number of paragraphs from the previous chunk so a clause that
straddles a boundary is still retrievable from either side.

Token estimate is rough — whitespace-split length. Good enough for
budgeting; the embedding model is the real arbiter.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ChunkSpec:
    text: str
    index: int


def _approx_tokens(text: str) -> int:
    """Whitespace-split length, with a small correction for CJK.

    The CJK fallback matters because government circulars often mix
    English and Devanagari in the same paragraph; CJK-script chars
    don't split on whitespace.
    """
    base = len(text.split())
    # add a fudge for non-space-separated scripts
    non_ascii = sum(1 for c in text if ord(c) > 0x900)
    return base + non_ascii // 4


def chunk_paragraphs(
    paragraphs: list[str],
    *,
    target_tokens: int = 500,
    overlap_tokens: int = 50,
) -> list[ChunkSpec]:
    """Walk paragraphs, accumulate to target, overlap by overlap_tokens.

    Empty input -> empty output. Single-paragraph input -> single chunk.
    """
    if not paragraphs:
        return []

    chunks: list[ChunkSpec] = []
    buf: list[str] = []
    buf_tokens = 0
    idx = 0
    for para in paragraphs:
        n = _approx_tokens(para)
        if buf and buf_tokens + n > target_tokens:
            text = "\n\n".join(buf)
            chunks.append(ChunkSpec(text=text, index=idx))
            idx += 1
            # overlap: keep tail paragraphs whose tokens sum <= overlap
            overlap: list[str] = []
            overlap_tok = 0
            for p in reversed(buf):
                pt = _approx_tokens(p)
                if overlap_tok + pt > overlap_tokens:
                    break
                overlap.insert(0, p)
                overlap_tok += pt
            buf = overlap
            buf_tokens = sum(_approx_tokens(p) for p in buf)
        buf.append(para)
        buf_tokens += n

    if buf:
        chunks.append(ChunkSpec(text="\n\n".join(buf), index=idx))
    return chunks
