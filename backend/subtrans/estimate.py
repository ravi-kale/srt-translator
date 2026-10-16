"""Pre-run token & cost estimator (character-based token approximation)."""

from __future__ import annotations

import math
import unicodedata
from typing import Any, Mapping, Sequence

from . import pricing
from .batching import create_smart_batches
from .llm.base import Usage
from .llm.factory import stage_model, stage_providers
from .models import Bible, Block, RunConfig
from .pipeline.prompts import render_bible

# Static instruction overhead per system prompt (tokens), measured from prompts.py templates.
STAGE1_SYSTEM_TOKENS = 600
STAGE2_SYSTEM_TOKENS = 650
STAGE3_SYSTEM_TOKENS = 650
DEFAULT_BIBLE_TOKENS = 1500
LINE_OVERHEAD_TOKENS = 12  # IDs, durations, JSON punctuation per line

# Target-language token inflation vs. English source (tokenizers are less efficient on these scripts).
_DENSE_LANGS = ("hindi", "bengali", "tamil", "telugu", "marathi", "gujarati", "kannada", "malayalam",
                "punjabi", "urdu", "thai", "arabic", "persian", "farsi", "hebrew", "greek", "burmese", "khmer")
_CJK_LANGS = ("chinese", "mandarin", "cantonese", "japanese", "korean")


def estimate_tokens(text: str) -> int:
    cjk = 0
    other = 0
    for ch in text:
        if ch.isspace():
            other += 1
            continue
        name = unicodedata.name(ch, "")
        if name.startswith(("CJK", "HIRAGANA", "KATAKANA", "HANGUL")):
            cjk += 1
        else:
            other += 1
    return int(cjk * 1.0 + other / 3.8) + 1


def target_factor(target_language: str) -> float:
    lang = target_language.lower()
    if any(k in lang for k in _DENSE_LANGS):
        return 2.2
    if any(k in lang for k in _CJK_LANGS):
        return 1.1
    return 1.25


def _stage(
    stage: int, config: RunConfig, usage: Usage, extra: dict[str, Any], keys: Mapping[str, bool] | None = None
) -> dict[str, Any]:
    model = stage_model(config, stage, keys)
    usage.cost_usd = pricing.cost_usd(model, usage)
    return {
        "stage": stage,
        "provider": stage_providers(config, keys)[stage],
        "model": model,
        "input_tokens": usage.input_tokens,
        "cached_tokens": usage.cache_hit_tokens,
        "output_tokens": usage.output_tokens,
        "cost_usd": round(usage.cost_usd, 4),
        "priced": pricing.price_for(model) is not None,
        **extra,
    }


def estimate_job(
    blocks: Sequence[Block],
    bible: Bible | None,
    config: RunConfig,
    target_language: str,
    keys: Mapping[str, bool] | None = None,
) -> dict[str, Any]:
    with_text = [b for b in blocks if b.source_text.strip()]
    src_tokens = {b.id: estimate_tokens(b.source_text) for b in blocks}
    factor = target_factor(target_language or "")
    trg_tokens = {k: int(v * factor) for k, v in src_tokens.items()}
    bible_tokens = estimate_tokens(render_bible(bible)) if bible else DEFAULT_BIBLE_TOKENS

    stages: list[dict[str, Any]] = []

    # Stage 1 — one request over up to bible_max_lines lines (skipped if a Bible already exists).
    if bible is None:
        sample = with_text[: config.bible_max_lines]
        s1_in = STAGE1_SYSTEM_TOKENS + sum(src_tokens[b.id] + 4 for b in sample)
        stages.append(_stage(1, config, Usage(input_tokens=s1_in, output_tokens=int(bible_tokens * factor)), {"calls": 1}, keys))
    else:
        stages.append(_stage(1, config, Usage(input_tokens=0, output_tokens=0), {"calls": 0, "skipped": True}, keys))

    # Stage 2 — cached system prefix + per-batch sliding window payload.
    batches = create_smart_batches(blocks, config.target_size, config.max_size, config.gap_sec)
    system2 = STAGE2_SYSTEM_TOKENS + int(bible_tokens * factor)
    ctx_lines = config.context_window
    s2 = Usage(input_tokens=0, output_tokens=0)
    avg_src = (sum(src_tokens.values()) / max(1, len(blocks))) if blocks else 0
    avg_trg = avg_src * factor
    for i, batch in enumerate(batches):
        payload = sum(src_tokens[b.id] + LINE_OVERHEAD_TOKENS for b in batch)
        context = int(ctx_lines * (avg_src + avg_trg + LINE_OVERHEAD_TOKENS) + ctx_lines * (avg_src + LINE_OVERHEAD_TOKENS))
        s2.input_tokens += system2 + payload + context
        if i > 0:
            s2.cache_hit_tokens += system2
        s2.output_tokens += sum(trg_tokens[b.id] + 6 for b in batch)
    stages.append(_stage(2, config, s2, {"calls": len(batches)}, keys))

    # Stage 3 — windows of qa_window lines; system prefix cached after the first window.
    windows = math.ceil(len(blocks) / config.qa_window) if blocks else 0
    system3 = STAGE3_SYSTEM_TOKENS + int(bible_tokens * factor)
    window_body = sum(src_tokens[b.id] + trg_tokens[b.id] + LINE_OVERHEAD_TOKENS for b in blocks)
    s3 = Usage(input_tokens=system3 * windows + window_body, output_tokens=0)
    if stage_providers(config, keys)[3] == "claude" and windows:
        s3.cache_write_tokens = system3
        s3.cache_hit_tokens = system3 * (windows - 1)
    elif windows:
        s3.cache_hit_tokens = system3 * (windows - 1)
    # Sparse patches: assume ~8% of lines fixed, ~25 tokens of reason each, plus thinking overhead.
    fixed = int(len(with_text) * 0.08)
    s3.output_tokens = sum(int(avg_trg) + 35 for _ in range(fixed)) + windows * 600
    stages.append(_stage(3, config, s3, {"calls": windows}, keys))

    total = round(sum(s["cost_usd"] for s in stages), 4)
    return {
        "stages": stages,
        "total_cost_usd": total,
        "total_input_tokens": sum(s["input_tokens"] for s in stages),
        "total_output_tokens": sum(s["output_tokens"] for s in stages),
        "batches": len(batches),
        "qa_windows": windows,
        "lines": len(blocks),
    }
