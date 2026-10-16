"""Claude critic client (AsyncAnthropic, structured outputs via output_config.format)."""

from __future__ import annotations

from typing import Any

import anthropic
from anthropic import AsyncAnthropic

from .. import pricing
from .base import LLMError, LLMRefusalError, LLMResult, Usage
from .json_utils import extract_json

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicClient:
    provider = "claude"
    structured = True

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        effort: str = "medium",
        refusal_fallback: bool = True,
        timeout: float = 240.0,
    ):
        self.model = model
        self.effort = effort
        self.refusal_fallback = refusal_fallback
        self._client = AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=0)

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any] | None,
        schema_name: str,
        max_tokens: int,
    ) -> LLMResult:
        output_config: dict[str, Any] = {"effort": self.effort}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            # The static instructions + Translation Bible form a cacheable prefix across windows.
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
            "output_config": output_config,
        }
        if self.refusal_fallback:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"

        try:
            resp = await self._client.beta.messages.create(**kwargs)
        except (anthropic.APITimeoutError, anthropic.APIConnectionError) as exc:
            raise LLMError(f"claude: connection error: {exc}", retryable=True) from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("claude: rate limited (429)", retryable=True) from exc
        except anthropic.APIStatusError as exc:
            retryable = exc.status_code in (408, 409) or exc.status_code >= 500
            raise LLMError(
                f"claude HTTP {exc.status_code} for model '{self.model}': {exc.message}", retryable=retryable,
                fatal=not retryable,
            ) from exc

        u = resp.usage
        cache_read = u.cache_read_input_tokens or 0
        cache_write = u.cache_creation_input_tokens or 0
        usage = Usage(
            input_tokens=u.input_tokens + cache_read + cache_write,
            cache_hit_tokens=cache_read,
            cache_write_tokens=cache_write,
            output_tokens=u.output_tokens,
        )
        served_by = resp.model or self.model
        usage.cost_usd = pricing.cost_usd(served_by, usage)

        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            category = getattr(details, "category", None) if details is not None else None
            raise LLMRefusalError(f"claude declined this window (category: {category or 'unspecified'})", usage=usage)

        text = "".join(block.text for block in resp.content if block.type == "text")
        if resp.stop_reason == "max_tokens":
            raise LLMError("claude: output truncated at max_tokens", retryable=False, usage=usage)
        try:
            data = extract_json(text)
        except ValueError as exc:
            raise LLMError(f"claude: invalid JSON ({exc})", retryable=True, usage=usage) from exc
        return LLMResult(data=data, usage=usage, model=served_by, raw_text=text)
