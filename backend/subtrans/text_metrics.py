"""Deterministic QA metrics: characters-per-second and line flags."""

from __future__ import annotations

import re

from .models import FLAG_EMPTY, FLAG_HIGH_CPS, FLAG_IDENTICAL, FLAG_MISSING, Block

TAG_RE = re.compile(r"<[^>]+>|\{\\[^}]*\}")
_WS_RE = re.compile(r"\s+")


def strip_tags(text: str) -> str:
    return TAG_RE.sub("", text)


def visible_length(text: str) -> int:
    """Characters a viewer reads: formatting tags and line breaks are not counted."""
    return len(strip_tags(text).replace("\r", "").replace("\n", ""))


def compute_cps(text: str, duration_sec: float) -> float:
    chars = visible_length(text)
    if chars == 0:
        return 0.0
    return round(chars / max(duration_sec, 0.01), 1)


def _normalize(text: str) -> str:
    return _WS_RE.sub(" ", strip_tags(text)).strip().casefold()


def _has_letters(text: str) -> bool:
    return any(ch.isalpha() for ch in text)


def compute_flags(block: Block, cps_threshold: float) -> list[str]:
    text = block.current_text
    has_source = bool(block.source_text.strip())
    flags: list[str] = []
    if not text.strip():
        if has_source:
            flags.append(FLAG_EMPTY)
            if FLAG_MISSING in block.flags:
                flags.append(FLAG_MISSING)
        return flags
    if compute_cps(text, block.duration_sec) > cps_threshold:
        flags.append(FLAG_HIGH_CPS)
    if has_source and _has_letters(block.source_text) and _normalize(text) == _normalize(block.source_text):
        flags.append(FLAG_IDENTICAL)
    return flags


def refresh_metrics(block: Block, cps_threshold: float) -> None:
    """Recompute cps + flags in place from the block's current translation."""
    block.cps = compute_cps(block.current_text, block.duration_sec)
    block.flags = compute_flags(block, cps_threshold)
