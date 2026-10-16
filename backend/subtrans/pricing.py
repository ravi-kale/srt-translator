"""Per-model token prices (USD per 1M tokens) and cost calculation.

Sources (checked 2026-10-16):
- DeepSeek pricing page: deepseek-flash $0.003 cache-hit / $0.15 miss / $0.60 out off-peak; peak = 2x
  (01:00-04:00 and 06:00-10:00 UTC, Mon-Fri).
- Gemini 4 Argon: $2 / $10 introductory through 2026-11-30 (cached input 95% off), then $4 / $20.
- Claude: Sonnet 5.5 $2 / $10, cache read $0.20 (cache write billed at 1.25x input).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from .llm.base import Usage

GEMINI_PROMO_END = date(2026, 11, 30)


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float
    cache_write: float | None = None


def deepseek_is_peak(now: datetime) -> bool:
    now = now.astimezone(timezone.utc)
    return now.weekday() < 5 and (1 <= now.hour < 4 or 6 <= now.hour < 10)


def _scaled(p: Price, k: float) -> Price:
    return Price(p.input * k, p.output * k, p.cache_read * k, None if p.cache_write is None else p.cache_write * k)


def price_for(model: str, now: datetime | None = None) -> Price | None:
    now = now or datetime.now(timezone.utc)
    m = model.lower()
    if "deepseek" in m:
        base = Price(0.66, 1.98, 0.022) if "pro" in m else Price(0.15, 0.60, 0.003)
        return _scaled(base, 2.0) if deepseek_is_peak(now) else base
    if "gemini-4-argon" in m or "argon" in m:
        if now.date() <= GEMINI_PROMO_END:
            return Price(2.0, 10.0, 0.10)
        return Price(4.0, 20.0, 0.40)
    if "claude" in m:
        if "fable" in m or "mythos" in m:
            return Price(10.0, 50.0, 1.0, 12.5)
        if "opus-5-5" in m or "opus-5.5" in m:
            return Price(4.0, 20.0, 0.20, 5.0)
        if "opus" in m:
            return Price(5.0, 25.0, 0.50, 6.25)
        if "haiku-5-5" in m or "haiku-5.5" in m:
            return Price(0.10, 0.50, 0.01, 0.125)
        if "haiku" in m:
            return Price(1.0, 5.0, 0.10, 1.25)
        return Price(2.0, 10.0, 0.20, 2.50)  # Sonnet 5.5 / 5
    return None


def cost_usd(model: str, usage: Usage, now: datetime | None = None) -> float:
    price = price_for(model, now)
    if price is None:
        return 0.0
    uncached = max(0, usage.input_tokens - usage.cache_hit_tokens - usage.cache_write_tokens)
    total = (
        uncached * price.input
        + usage.cache_hit_tokens * price.cache_read
        + usage.cache_write_tokens * (price.cache_write if price.cache_write is not None else price.input)
        + usage.output_tokens * price.output
    )
    return round(total / 1_000_000, 6)
