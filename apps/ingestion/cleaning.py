"""Text cleaning — the unglamorous part of ingestion.

Operates on raw text dumps (PDFs already extracted, or HTML already
parsed). Three goals:
1. Strip the boilerplate headers / footers that pollute every
   government circular.
2. Normalize whitespace and unicode quirks without changing meaning.
3. Detect language so the right embedding model can be selected if
   we ever swap to a per-language model.
"""
import re
import unicodedata
from typing import Optional

import pandas as pd

# boilerplate patterns we strip from every circular. kept conservative
# — false positives here would silently drop real text.
_BOILERPLATE = [
    r"^\s*Government of India\s*$",
    r"^\s*Ministry of [A-Za-z ]+\s*$",
    r"^\s*Page \d+ of \d+\s*$",
    r"^\s*\d+\s*$",  # lone page numbers
    r"^\s*\[.*?\]\s*$",  # citation footnotes like [1], [2]
    r"^\s*Downloaded from .*$",
    r"^\s*www\..*$",
]
_BOILERPLATE_RE = re.compile("|".join(_BOILERPLATE), re.MULTILINE)


def normalize(text: str) -> str:
    """NFKC normalize + collapse whitespace. Idempotent."""
    text = unicodedata.normalize("NFKC", text)
    text = _BOILERPLATE_RE.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def language_detect(text: str) -> Optional[str]:
    """Cheap language detection — good enough for routing.

    Heavyweight NLP libs would be overkill at ingestion time; this
    uses a Unicode-script heuristic that correctly identifies the
    major Indian language families on a single-line sample.
    """
    if not text:
        return None
    sample = text[:500]
    counts: dict[str, int] = {}
    for ch in sample:
        try:
            name = unicodedata.name(ch)
        except ValueError:
            continue
        if "DEVANAGARI" in name:
            counts["hi"] = counts.get("hi", 0) + 1
        elif "BENGALI" in name:
            counts["bn"] = counts.get("bn", 0) + 1
        elif "TAMIL" in name:
            counts["ta"] = counts.get("ta", 0) + 1
        elif "TELUGU" in name:
            counts["te"] = counts.get("te", 0) + 1
        elif "GUJARATI" in name:
            counts["gu"] = counts.get("gu", 0) + 1
        elif "KANNADA" in name:
            counts["kn"] = counts.get("kn", 0) + 1
        elif "MALAYALAM" in name:
            counts["ml"] = counts.get("ml", 0) + 1
        elif "GURMUKHI" in name:
            counts["pa"] = counts.get("pa", 0) + 1
        elif "LATIN" in name:
            counts["en"] = counts.get("en", 0) + 1
    if not counts:
        return "en"
    # require >30% of detected chars to actually be the dominant script
    total = sum(counts.values())
    winner, n = max(counts.items(), key=lambda kv: kv[1])
    return winner if n / total > 0.3 else "en"


def clean_to_dataframe(raw_text: str) -> pd.DataFrame:
    """Return a one-column DataFrame of normalized, non-empty paragraphs.

    Useful for the chunker — pandas groupby makes paragraph-boundary
    chunking a one-liner.
    """
    text = normalize(raw_text)
    if not text:
        return pd.DataFrame(columns=["paragraph"])
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    return pd.DataFrame({"paragraph": paragraphs})
