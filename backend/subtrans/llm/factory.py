"""Builds the LLM client for each pipeline stage from a RunConfig + server-side keys."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from ..config import KEY_ENV_NAMES, Settings, get_settings
from ..models import RunConfig
from .anthropic_client import AnthropicClient
from .base import LLMClient
from .openai_compat import OpenAICompatClient


def _direct_providers(config: RunConfig) -> dict[int, str]:
    if config.mode == "all_deepseek":
        return {1: "deepseek", 2: "deepseek", 3: "deepseek"}
    if config.mode == "all_openrouter":
        return {1: "openrouter", 2: "openrouter", 3: "openrouter"}
    return {1: config.stage1_provider, 2: "deepseek", 3: config.stage3_provider}


def stage_providers(config: RunConfig, keys: Mapping[str, bool] | None = None) -> dict[int, str]:
    """Provider that serves each stage. With `keys`, a stage whose direct key is missing is
    routed through OpenRouter (same model) when an OpenRouter key is available."""
    direct = _direct_providers(config)
    if not keys or not keys.get("openrouter"):
        return direct
    return {s: p if keys.get(p) else "openrouter" for s, p in direct.items()}


# OpenRouter model used for a direct provider's stage when it is routed through OpenRouter.
# Reuses the all_openrouter per-stage IDs, whose defaults are Gemini (1), DeepSeek (2), Claude (3).
def _openrouter_model_for(config: RunConfig, provider: str) -> str:
    return {
        "gemini": config.openrouter_stage1_model,
        "deepseek": config.openrouter_stage2_model,
        "claude": config.openrouter_stage3_model,
    }[provider]


def stage_model(config: RunConfig, stage: int, keys: Mapping[str, bool] | None = None) -> str:
    direct = _direct_providers(config)[stage]
    provider = stage_providers(config, keys)[stage]
    if direct == "openrouter":
        return {
            1: config.openrouter_stage1_model,
            2: config.openrouter_stage2_model,
            3: config.openrouter_stage3_model,
        }[stage]
    if provider == "openrouter":
        return _openrouter_model_for(config, direct)
    return {"deepseek": config.deepseek_model, "gemini": config.gemini_model, "claude": config.claude_model}[provider]


def missing_keys(config: RunConfig, settings: Settings, stages: set[int] | None = None) -> list[str]:
    providers = stage_providers(config, settings.key_status())
    needed = {providers[s] for s in (stages or {1, 2, 3})}
    return sorted(KEY_ENV_NAMES[p] for p in needed if not settings.key_for(p))


def deepseek_extra_body(config: RunConfig) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if config.deepseek_send_thinking_flag:
        body["thinking"] = {"type": "disabled"}  # official api.deepseek.com flag
    if config.deepseek_send_chat_template_flag:
        body["chat_template_kwargs"] = {"enable_thinking": False}  # vLLM / SGLang flag (spec)
    return body


class ClientFactory:
    def __init__(self, settings_provider: Callable[[], Settings] = get_settings):
        self._settings_provider = settings_provider

    def missing_keys(self, config: RunConfig, stages: set[int] | None = None) -> list[str]:
        return missing_keys(config, self._settings_provider(), stages)

    def key_status(self) -> dict[str, bool]:
        return self._settings_provider().key_status()

    def build(self, stage: int, config: RunConfig) -> LLMClient:
        settings = self._settings_provider()
        keys = settings.key_status()
        provider = stage_providers(config, keys)[stage]
        model = stage_model(config, stage, keys)
        timeout = settings.subtrans_request_timeout_sec
        if settings.on_vercel:
            # Keep one call (plus the step's time budget) inside the 300 s function limit. Stage 1
            # starts at the beginning of a step, so it gets the longer allowance.
            timeout = min(timeout, 200.0 if stage == 1 else 110.0)
        key = settings.key_for(provider)
        if not key:
            raise ValueError(f"Missing {KEY_ENV_NAMES[provider]} in backend/.env")

        if provider == "claude":
            return AnthropicClient(
                api_key=key,
                model=model,
                effort=config.qa_effort,
                refusal_fallback=config.claude_refusal_fallback,
                timeout=timeout,
            )
        if provider == "deepseek":
            return OpenAICompatClient(
                provider="deepseek",
                api_key=key,
                base_url=settings.deepseek_base_url,
                model=model,
                json_mode="json_object",
                temperature=config.stage2_temperature if stage == 2 else 0.2,
                extra_body=deepseek_extra_body(config),
                timeout=timeout,
            )
        if provider == "gemini":
            return OpenAICompatClient(
                provider="gemini",
                api_key=key,
                base_url=settings.gemini_base_url,
                model=model,
                json_mode="json_schema",
                reasoning_effort=config.stage1_reasoning_effort if stage == 1 else config.qa_effort,
                timeout=timeout,
            )
        # OpenRouter: reasoning off for the Stage 2 translator and for DeepSeek everywhere; Gemini and
        # Claude get effort-controlled reasoning in Stages 1 & 3 (Claude rejects any reasoning-disable form).
        reasoning: dict[str, Any]
        if stage == 2 or "deepseek" in model.lower():
            # DeepSeek runs with thinking off in every stage, as on the direct DeepSeek API (see
            # deepseek_extra_body): long reasoning made each QA window take minutes.
            reasoning = {"enabled": False}
        elif stage == 1:
            reasoning = {"effort": "low" if config.stage1_reasoning_effort == "minimal" else config.stage1_reasoning_effort}
        else:
            reasoning = {"effort": config.qa_effort}
        return OpenAICompatClient(
            provider="openrouter",
            api_key=key,
            base_url=settings.openrouter_base_url,
            model=model,
            json_mode="json_object" if stage == 2 else "json_schema",
            temperature=config.stage2_temperature if stage == 2 else None,
            extra_body={"reasoning": reasoning, "usage": {"include": True}},
            timeout=timeout,
        )
