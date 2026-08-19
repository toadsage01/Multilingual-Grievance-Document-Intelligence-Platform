"""Embedding provider backed by sentence-transformers.

Loaded lazily — the model is ~280MB and we don't want to pull it on
`python manage.py check` or during tests. The first real ingest
triggers the download; subsequent inits hit a local cache.

multilingual-e5-base is the default because it's CPU-friendly and
handles every Indian language in our supported set. Swap by setting
EMBEDDING_MODEL_NAME in env — but remember the embedding column is
pinned to 768 dims in the schema, so a different model means a
schema migration.
"""
import threading
from typing import Optional

from core.interfaces import EmbeddingProvider


class SentenceTransformerEmbedder(EmbeddingProvider):
    """Wraps sentence-transformers SentenceTransformer.

    Thread-safe singleton init — the model load is the expensive part
    and we don't want to do it per-request.
    """
    _lock = threading.Lock()
    _model = None
    _model_name: str

    def __init__(self, model_name: str = "intfloat/multilingual-e5-base"):
        self._model_name = model_name

    @property
    def model(self):
        with self._lock:
            if self._model is None:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(self._model_name)
        return self._model

    def embed(self, texts: list[str], *, normalize: bool = True) -> list[list[float]]:
        """Batch embed. Always batched — the per-chunk loop is ~10x slower."""
        # e5 models require the "query:" / "passage:" prefix convention.
        # for ingestion we use "passage:"; for queries, embed_query uses "query:".
        prefixed = [f"passage: {t}" for t in texts]
        vectors = self.model.encode(
            prefixed, batch_size=32, normalize_embeddings=normalize,
            show_progress_bar=False,
        )
        return [list(v) for v in vectors]

    def embed_query(self, text: str) -> list[float]:
        prefixed = f"query: {text}"
        vec = self.model.encode(
            [prefixed], normalize_embeddings=True, show_progress_bar=False
        )[0]
        return list(vec)


_embedder: Optional[SentenceTransformerEmbedder] = None


def get_embedder() -> SentenceTransformerEmbedder:
    global _embedder
    if _embedder is None:
        from django.conf import settings
        _embedder = SentenceTransformerEmbedder(
            getattr(settings, "EMBEDDING_MODEL_NAME", "intfloat/multilingual-e5-base")
        )
    return _embedder
