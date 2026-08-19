"""Abstract interfaces for the swappable bits: LLM, embeddings, translator.

Each of these has one or more concrete implementations in apps/, but
core/ itself never imports the implementations. This is the seam that
makes the circuit breaker, fallback chain and translation strategy all
unit-testable with fakes.
"""
from abc import ABC, abstractmethod
from typing import AsyncIterator, Iterable
from core.domain.entities import GenerationResult, RetrievedChunk


class EmbeddingProvider(ABC):
    """Turns text into vectors. Local multilingual-e5-base is the
    default impl — see apps.retrieval.embeddings."""

    @abstractmethod
    def embed(self, texts: list[str], *, normalize: bool = True) -> list[list[float]]:
        """Batch embed. Always batch — sentence-transformers supports
        batched encode natively and it's an order of magnitude faster
        than the per-chunk loop."""

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """Single query embed. Convenience wrapper, used by search endpoint."""


class LLMProvider(ABC):
    """Streaming text completion. Implementations live in apps.llm.providers."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def stream_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        context_chunks: Iterable[RetrievedChunk],
    ) -> AsyncIterator[str]:
        """Yield tokens as they arrive. Caller writes them to the SSE stream."""

    @abstractmethod
    def health(self) -> bool:
        """Cheap liveness probe used by the circuit breaker."""


class Translator(ABC):
    """Optional translation hop. Only the response layer should call this —
    queries and retrieval stay in the source language to preserve nuance."""

    @abstractmethod
    def translate(self, text: str, source: str, target: str) -> str: ...

    @abstractmethod
    def supports(self, source: str, target: str) -> bool: ...
