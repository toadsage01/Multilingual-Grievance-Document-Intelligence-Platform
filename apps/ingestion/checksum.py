"""Checksum-based idempotent ingestion.

The actual claim this system has to make — "we can re-ingest 100k+
documents without re-embedding all of them" — lives here. The
contract: if (department, source_url) exists with the same sha256 of
normalized raw text, skip entirely. If the checksum differs, we
insert a new document row and supersede the old one rather than
deleting it, because past conversations may have cited the old text.
"""
import hashlib


def checksum(raw_text: str) -> str:
    """sha256 of utf-8 encoded raw text. Used as the dedup key."""
    return hashlib.sha256(raw_text.encode("utf-8")).hexdigest()


class ChecksumDelta:
    """Returns the per-document decision: SKIP / INSERT / SUPERSEDE.

    Pure-Python, no Django imports, so it's unit-testable against
    any list of (department_id, source_url, checksum) tuples.
    """

    def __init__(self, existing: list[tuple[str, str, str]]):
        # existing: rows already in the documents table for this tenant
        # {(source_url, checksum): True} for fast lookup
        self._seen = {(url, cs) for _, url, cs in existing}

    def decide(self, source_url: str, new_checksum: str) -> str:
        if (source_url, new_checksum) in self._seen:
            return "SKIP"
        return "INSERT"
