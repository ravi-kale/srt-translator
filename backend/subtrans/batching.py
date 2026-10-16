"""Smart scene batcher: cut batches only at sentence ends or long silences."""

from __future__ import annotations

import logging
from typing import Sequence

from .models import Block
from .text_metrics import strip_tags

log = logging.getLogger(__name__)

# Spec terminators (. ? ! ") plus typographic / CJK equivalents and the music cue.
TERMINAL_CHARS = frozenset('.?!"”…。？！」』♪')
ELLIPSES = ("...", "…")


def _tail(text: str) -> str:
    return strip_tags(text).rstrip()


def _head(text: str) -> str:
    return strip_tags(text).lstrip().lstrip("-–—").lstrip()


def ends_sentence(text: str, next_text: str | None = None) -> bool:
    tail = _tail(text)
    if not tail:
        return True  # an empty cue is a natural break
    if tail.endswith(ELLIPSES) and next_text is not None:
        head = _head(next_text)
        # "I was going to..." / "...the store" -> the sentence continues across the cut.
        if head.startswith(ELLIPSES) or (head[:1].isalpha() and head[:1].islower()):
            return False
    return tail[-1] in TERMINAL_CHARS


def is_boundary(blocks: Sequence[Block], i: int, gap_sec: float) -> bool:
    """True if a batch may end after blocks[i]."""
    if i >= len(blocks) - 1:
        return True
    curr, nxt = blocks[i], blocks[i + 1]
    if nxt.start_sec - curr.end_sec >= gap_sec:
        return True
    return ends_sentence(curr.source_text, nxt.source_text)


def create_smart_batches(
    blocks: Sequence[Block],
    target_size: int = 30,
    max_size: int = 42,
    gap_sec: float = 1.5,
) -> list[list[Block]]:
    """Group contiguous blocks into batches of ~target_size (hard cap max_size).

    A batch closes at the first boundary at or after target_size. If none appears before
    max_size, it backtracks to the latest boundary below target_size. Only when the whole
    window has no boundary at all is it hard-cut at max_size.
    """
    if target_size < 1 or max_size < target_size:
        raise ValueError("require 1 <= target_size <= max_size")
    batches: list[list[Block]] = []
    n = len(blocks)
    start = 0
    while start < n:
        if n - start <= max_size:
            batches.append(list(blocks[start:]))
            break
        cut: int | None = None
        for size in range(target_size, max_size + 1):
            if is_boundary(blocks, start + size - 1, gap_sec):
                cut = size
                break
        if cut is None:
            for size in range(target_size - 1, 0, -1):
                if is_boundary(blocks, start + size - 1, gap_sec):
                    cut = size
                    break
        if cut is None:
            cut = max_size
            log.warning("No sentence boundary in blocks %s..%s; hard cut at %s", start + 1, start + max_size, max_size)
        batches.append(list(blocks[start : start + cut]))
        start += cut
    return batches


def split_into_lanes(batches: list[list[Block]], lanes: int) -> list[list[list[Block]]]:
    """Split batches into up to `lanes` contiguous groups with roughly equal block counts."""
    if not batches:
        return []
    lanes = max(1, min(lanes, len(batches)))
    if lanes == 1:
        return [batches]
    total = sum(len(b) for b in batches)
    result: list[list[list[Block]]] = []
    current: list[list[Block]] = []
    cumulative = 0
    for batch in batches:
        if current and cumulative >= total * (len(result) + 1) / lanes and len(result) < lanes - 1:
            result.append(current)
            current = []
        current.append(batch)
        cumulative += len(batch)
    result.append(current)
    return result
