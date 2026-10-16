"""DeepSeek / Gemini / OpenRouter through the OpenAI-compatible Chat Completions API."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

import openai
from openai import AsyncOpenAI

from .. import pricing
from .base import LLMError, LLMResult, Usage
from .json_utils import extract_json

log = logging.getLogger(__name__)

Provider = Literal["deepseek", "gemini", "openrouter"]


def _field(obj: Any, name: str) -> Any:
    """Read a provider-specific usage field that the SDK keeps as an 'extra' attribute."""
    if obj is None:
        return None
    value = getattr(obj, name, None)
    if value is None:
        extra = getattr(obj, "model_extra", None) or {}
        value = extra.get(name)
    return value


def _parse_usage(provider: str, model: str, raw: Any) -> Usage:
    if raw is None:
        return Usage()
    prompt = int(_field(raw, "prompt_tokens") or 0)
    output = int(_field(raw, "completion_tokens") or 0)
    if provider == "deepseek":
        hit = int(_field(raw, "prompt_cache_hit_tokens") or 0)
    else:
        details = _field(raw, "prompt_tokens_details")
        hit = int(_field(details, "cached_tokens") or 0)
    usage = Usage(input_tokens=prompt, cache_hit_tokens=hit, output_tokens=output)
    reported_cost = _field(raw, "cost") if provider == "openrouter" else None
    usage.cost_usd = float(reported_cost) if reported_cost is not None else pricing.cost_usd(model, usage)
    return usage


class OpenAICompatClient:
    def __init__(
        self,
        *,
        provider: Provider,
        api_key: str,
        base_url: str,
        model: str,
        json_mode: Literal["json_object", "json_schema"],
        temperature: float | None = None,
        reasoning_effort: str | None = None,
        extra_body: dict[str, Any] | None = None,
        timeout: float = 240.0,
    ):
        self.provider = provider
        self.model = model
        self.json_mode = json_mode
        self.structured = json_mode == "json_schema"
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort
        self.extra_body = extra_body or {}
        # Retries are handled by call_with_retries so there is a single retry policy.
        self.timeout = timeout
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0)

    def _response_format(self, schema: dict[str, Any] | None, schema_name: str) -> dict[str, Any]:
        if self.structured and schema is not None:
            return {"type": "json_schema", "json_schema": {"name": schema_name, "schema": schema, "strict": True}}
        return {"type": "json_object"}

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any] | None,
        schema_name: str,
        max_tokens: int,
    ) -> LLMResult:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max_tokens,
            "response_format": self._response_format(schema, schema_name),
        }
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.reasoning_effort is not None:
            kwargs["reasoning_effort"] = self.reasoning_effort
        if self.extra_body:
            kwargs["extra_body"] = self.extra_body

        try:
            # wait_for bounds the whole call: the HTTP read timeout alone restarts on every keep-alive byte.
            resp = await asyncio.wait_for(self._client.chat.completions.create(**kwargs), self.timeout)
        except TimeoutError:
            raise LLMError(f"{self.provider}: no complete response within {self.timeout:.0f}s", retryable=True) from None
        except (openai.APITimeoutError, openai.APIConnectionError) as exc:
            raise LLMError(f"{self.provider}: connection error: {exc}", retryable=True) from exc
        except openai.RateLimitError as exc:
            raise LLMError(f"{self.provider}: rate limited (429)", retryable=True) from exc
        except openai.APIStatusError as exc:
            retryable = exc.status_code in (408, 409) or exc.status_code >= 500
            raise LLMError(
                f"{self.provider} HTTP {exc.status_code} for model '{self.model}': {exc.message}", retryable=retryable,
                fatal=not retryable,
            ) from exc

        usage = _parse_usage(self.provider, self.model, resp.usage)
        choice = resp.choices[0] if resp.choices else None
        content = choice.message.content if choice is not None else None
        finish = choice.finish_reason if choice is not None else None
        if not content or not content.strip():
            # DeepSeek JSON mode documents occasional empty content; retrying usually fixes it.
            raise LLMError(f"{self.provider}: empty content (finish_reason={finish})", retryable=True, usage=usage)
        try:
            data = extract_json(content)
        except ValueError as exc:
            truncated = finish == "length"
            raise LLMError(
                f"{self.provider}: invalid JSON ({'truncated at max_tokens' if truncated else exc})",
                retryable=not truncated,
                usage=usage,
            ) from exc
        return LLMResult(data=data, usage=usage, model=resp.model or self.model, raw_text=content)
