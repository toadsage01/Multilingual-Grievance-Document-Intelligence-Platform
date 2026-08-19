"""Circuit breaker for the LLM provider chain.

State machine:
- CLOSED: calls go through. On failure, increment failure_count.
- OPEN: after N consecutive failures in the rolling window, refuse
  calls for cooldown_seconds. Bypass by raising CircuitOpen.
- HALF_OPEN: after cooldown, allow ONE call. If it succeeds, close.
  If it fails, reopen.

Thread-safe. In-memory state — fine for a single-process deploy on
Render free tier; for multi-worker setups, swap the dict for a
shared Redis-backed counter.
"""
from __future__ import annotations
import logging
import time
import threading
from dataclasses import dataclass
from typing import Optional

from core.exceptions import CircuitOpen, ProviderUnavailable
from core.interfaces import LLMProvider

log = logging.getLogger(__name__)


@dataclass
class _State:
    failure_count: int = 0
    opened_at: float = 0.0  # epoch seconds; 0 when closed
    last_failure: float = 0.0


class CircuitBreaker:
    def __init__(
        self,
        provider: LLMProvider,
        *,
        failure_threshold: int = 5,
        cooldown_seconds: int = 60,
    ):
        self._provider = provider
        self._failure_threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._lock = threading.Lock()
        self._state = _State()

    @property
    def name(self) -> str:
        return self._provider.name

    @property
    def state(self) -> str:
        """Probe state — 'CLOSED' | 'OPEN' | 'HALF_OPEN'."""
        with self._lock:
            return self._peek_state_locked()

    def _peek_state_locked(self) -> str:
        if self._state.opened_at == 0.0:
            return "CLOSED"
        if time.time() - self._state.opened_at >= self._cooldown:
            return "HALF_OPEN"
        return "OPEN"

    def allow_call(self) -> bool:
        """Should we even try the provider?"""
        with self._lock:
            s = self._peek_state_locked()
            if s == "OPEN":
                return False
            return True

    def record_success(self) -> None:
        with self._lock:
            self._state = _State()  # reset to fully closed

    def record_failure(self) -> None:
        with self._lock:
            self._state.last_failure = time.time()
            self._state.failure_count += 1
            if self._state.failure_count >= self._failure_threshold:
                self._state.opened_at = time.time()
                log.warning(
                    "circuit OPEN provider=%s failures=%d",
                    self._provider.name, self._state.failure_count,
                )


class FallbackChain:
    """Try primary; on failure or open circuit, try fallback.

    The contract from the spec: never silently swallow a provider
    failure — if both are down, raise ProviderUnavailable so the
    chat endpoint can persist the user's message and return a clear
    'service temporarily unavailable, your query has been saved' reply.
    """
    def __init__(self, primary: CircuitBreaker, fallback: CircuitBreaker):
        self._primary = primary
        self._fallback = fallback

    async def stream_completion(self, system_prompt: str, user_prompt: str,
                                context_chunks) -> list[str]:
        """Collect the full streamed response into a list of tokens.

        We collect rather than yield-through because callers (the SSE
        view) wrap each token in an event frame anyway, and gathering
        lets us cleanly retry on the fallback if the primary dies
        mid-stream.
        """
        errors: list[str] = []
        for breaker in (self._primary, self._fallback):
            if not breaker.allow_call():
                errors.append(f"{breaker.name}: circuit open")
                continue
            tokens: list[str] = []
            try:
                async for tok in breaker._provider.stream_completion(
                    system_prompt, user_prompt, context_chunks
                ):
                    tokens.append(tok)
                breaker.record_success()
                if tokens:
                    return tokens
                errors.append(f"{breaker.name}: empty stream")
            except Exception as e:
                breaker.record_failure()
                errors.append(f"{breaker.name}: {e}")
                continue
        raise ProviderUnavailable("; ".join(errors))


# ----------------------------------------------------------------------
# Module-level accessors — wired up from Django settings at first use
# ----------------------------------------------------------------------
_breakers: Optional[tuple[CircuitBreaker, CircuitBreaker]] = None


def get_chain() -> FallbackChain:
    """Build (and memoize) the primary+fallback chain from settings."""
    global _breakers
    if _breakers is not None:
        return FallbackChain(*_breakers)

    from django.conf import settings
    from apps.llm.providers import GroqProvider, GeminiProvider

    primary_name = getattr(settings, "LLM_PRIMARY_PROVIDER", "groq")
    fallback_name = getattr(settings, "LLM_FALLBACK_PROVIDER", "gemini")

    def _factory(name: str) -> LLMProvider:
        if name == "groq":
            return GroqProvider(
                api_key=getattr(settings, "GROQ_API_KEY", ""),
                model=getattr(settings, "GROQ_MODEL", "llama-3.1-8b-instant"),
                base_url=getattr(settings, "GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
            )
        if name == "gemini":
            return GeminiProvider(
                api_key=getattr(settings, "GEMINI_API_KEY", ""),
                model=getattr(settings, "GEMINI_MODEL", "gemini-1.5-flash"),
            )
        raise ValueError(f"unknown LLM provider: {name}")

    primary = CircuitBreaker(
        _factory(primary_name),
        failure_threshold=getattr(settings, "LLM_CIRCUIT_FAILURE_THRESHOLD", 5),
        cooldown_seconds=getattr(settings, "LLM_CIRCUIT_COOLDOWN_SECONDS", 60),
    )
    fallback = CircuitBreaker(
        _factory(fallback_name),
        failure_threshold=getattr(settings, "LLM_CIRCUIT_FAILURE_THRESHOLD", 5),
        cooldown_seconds=getattr(settings, "LLM_CIRCUIT_COOLDOWN_SECONDS", 60),
    )
    _breakers = (primary, fallback)
    return FallbackChain(primary, fallback)
