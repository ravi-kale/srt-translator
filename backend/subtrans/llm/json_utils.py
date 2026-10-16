"""Tolerant JSON extraction for model output (code fences, leading prose)."""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json(text: str | None) -> dict[str, Any]:
    if text is None or not text.strip():
        raise ValueError("empty response")
    candidates = [text.strip()]
    candidates += [m.group(1).strip() for m in _FENCE_RE.finditer(text)]
    first, last = text.find("{"), text.rfind("}")
    if first != -1 and last > first:
        candidates.append(text[first : last + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("response is not a JSON object")
