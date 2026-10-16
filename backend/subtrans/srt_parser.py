"""Deterministic .srt parser and exporter.

Timestamps are parsed into seconds for batching/CPS math, but the original timing line is kept
verbatim and written back unchanged on export, so timings are 100% locked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from charset_normalizer import from_bytes

from .models import Block

TIMING_RE = re.compile(
    r"^\s*(\d+):(\d{1,2}):(\d{1,2})[,.:](\d{1,3})\s*-->\s*(\d+):(\d{1,2}):(\d{1,2})[,.:](\d{1,3})(.*)$"
)


@dataclass
class ParsedSrt:
    blocks: list[Block]
    line_ending: str
    encoding: str
    warnings: list[str] = field(default_factory=list)


def _to_seconds(h: str, m: str, s: str, ms: str) -> float:
    # "5" after the separator means 500 ms (fractional part), not 5 ms.
    millis = int(ms.ljust(3, "0")[:3])
    return int(h) * 3600 + int(m) * 60 + int(s) + millis / 1000.0


# Single-byte Latin code pages that statistical detection confuses on short subtitle files;
# Windows-1252 is by far the most common legacy .srt encoding among them.
_WESTERN_CONFUSABLE = {"cp1250", "cp1252", "iso8859_2", "latin_1", "iso8859_15", "mac_roman", "cp850", "cp437"}


def decode_srt_bytes(data: bytes) -> tuple[str, str]:
    """Return (text, encoding). Tries UTF-8 (with/without BOM), UTF-16 BOMs, detection, cp1252."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8-sig"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16"), "utf-16"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    best = from_bytes(data).best()
    if best is None or best.encoding in _WESTERN_CONFUSABLE:
        try:
            return data.decode("cp1252"), "cp1252"
        except UnicodeDecodeError:
            pass
    if best is not None and best.encoding:
        return str(best), best.encoding
    return data.decode("latin-1"), "latin-1"


def parse_srt(text: str, encoding: str = "utf-8") -> ParsedSrt:
    line_ending = "\r\n" if "\r\n" in text else "\n"
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    lines = normalized.split("\n")
    warnings: list[str] = []

    timing_positions = [i for i, line in enumerate(lines) if TIMING_RE.match(line)]
    blocks: list[Block] = []
    missing_index = 0

    for k, ti in enumerate(timing_positions):
        index_line = ti - 1 if ti - 1 >= 0 and lines[ti - 1].strip().isdigit() else None
        if k + 1 < len(timing_positions):
            nxt = timing_positions[k + 1]
            end = nxt - 1 if nxt - 1 > ti and lines[nxt - 1].strip().isdigit() else nxt
        else:
            end = len(lines)

        # A blank line inside a block would terminate it in any player, so drop blank lines.
        text_lines = [ln.rstrip() for ln in lines[ti + 1 : end] if ln.strip()]

        m = TIMING_RE.match(lines[ti])
        assert m is not None
        start = _to_seconds(*m.group(1, 2, 3, 4))
        stop = _to_seconds(*m.group(5, 6, 7, 8))
        if stop < start:
            warnings.append(f"Block {len(blocks) + 1}: end time precedes start time")

        position = len(blocks) + 1
        if index_line is None:
            missing_index += 1
            index = position
        else:
            index = int(lines[index_line].strip())

        blocks.append(
            Block(
                id=str(position),
                index=index,
                timestamp=lines[ti].strip(),
                start_sec=round(start, 3),
                end_sec=round(stop, 3),
                duration_sec=round(max(0.0, stop - start), 3),
                source_text="\n".join(text_lines),
            )
        )

    if missing_index:
        warnings.append(f"{missing_index} block(s) had no index line; position was used instead")
    return ParsedSrt(blocks=blocks, line_ending=line_ending, encoding=encoding, warnings=warnings)


def parse_srt_bytes(data: bytes) -> ParsedSrt:
    text, encoding = decode_srt_bytes(data)
    return parse_srt(text, encoding)


def export_text(block: Block) -> str:
    """Text written on export: final -> draft -> source."""
    return block.final_translation or block.draft_translation or block.source_text


def build_srt(blocks: list[Block], line_ending: str = "\n") -> str:
    out: list[str] = []
    for block in blocks:
        text_lines = [ln for ln in export_text(block).split("\n") if ln.strip()]
        out.append(line_ending.join([str(block.index), block.timestamp, *text_lines]))
    if not out:
        return ""
    return (line_ending * 2).join(out) + line_ending
