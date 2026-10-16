"""Provider-neutral LLM interface used by every pipeline stage."""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class Usage:
    input_tokens: int = 0  # all prompt tokens, including cached ones
    cache_hit_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 1


@dataclass
class LLMResult:
    data: dict[str, Any]
    usage: Usage
    model: str
    raw_text: str = field(repr=False, default="")


class LLMError(Exception):
    def __init__(self, message: str, *, retryable: bool, fatal: bool = False, usage: Usage | None = None):
        super().__init__(message)
        # retryable: worth repeating the identical request (429, 5xx, timeouts, empty/invalid output).
        self.retryable = retryable
        # fatal: the request itself is rejected (auth, bad params, unknown model) — stop the job.
        self.fatal = fatal
        # Tokens billed for a failed call (e.g. unparsable output) still count toward cost.
        self.usage = usage


class LLMRefusalError(LLMError):
    def __init__(self, message: str, usage: Usage | None = None):
        super().__init__(message, retryable=False, usage=usage)


class LLMClient(Protocol):
    provider: str
    model: str
    # True when the client enforces a JSON schema server-side (prompts adapt their output contract).
    structured: bool

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any] | None,
        schema_name: str,
        max_tokens: int,
    ) -> LLMResult: ...


async def call_with_retries(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 2.0,
    on_failed_attempt: Callable[[LLMError, int], None] | None = None,
    may_retry: Callable[[], bool] | None = None,
) -> T:
    """Retry retryable LLMErrors with exponential backoff + jitter; re-raise the last error.
    may_retry() returning False (e.g. a serverless step out of time) stops further attempts."""
    last: LLMError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await fn()
        except LLMError as exc:
            last = exc
            if on_failed_attempt is not None:
                on_failed_attempt(exc, attempt)
            if not exc.retryable or attempt == attempts or (may_retry is not None and not may_retry()):
                raise
            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
            log.warning("LLM call failed (attempt %s/%s): %s; retrying in %.1fs", attempt, attempts, exc, delay)
            await asyncio.sleep(delay)
    assert last is not None
    raise last
