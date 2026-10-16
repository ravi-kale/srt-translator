"""Mock OpenAI-compatible LLM server for local UI demos and end-to-end tests without API keys.

It answers /v1/chat/completions for all three SubTrans stages with deterministic pseudo-translations,
occasionally drops an ID (to exercise the ID-lock retry), produces some over-long lines (HIGH CPS),
and returns sparse critic patches. Usage includes DeepSeek-style cache-hit fields.

Run:   python backend/tests/mock_llm_server.py            (listens on 127.0.0.1:8099)
Then start the backend with:
    DEEPSEEK_API_KEY=mock GEMINI_API_KEY=mock \
    DEEPSEEK_BASE_URL=http://127.0.0.1:8099/v1 GEMINI_BASE_URL=http://127.0.0.1:8099/v1 \
    uvicorn main:app --app-dir backend --port 8000
and choose "Gemini 4 Argon" for Stage 3 in Settings (Claude has no mock).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

import uvicorn
from fastapi import FastAPI, Request

log = logging.getLogger("mock-llm")
app = FastAPI(title="SubTrans mock LLM")

_BATCH_RE = re.compile(r"TARGET BATCH TO TRANSLATE:\n(.*?)\n\nUPCOMING CONTEXT", re.DOTALL)
_QA_LINE_RE = re.compile(r"^(\d+) \| ([\d.]+)s \| (.*?) \| SRC: (.*) \| TRG: (.*)$", re.MULTILINE)
_dropped_once: set[str] = set()

WORDS = {
    "the": "el", "you": "tú", "your": "tu", "is": "es", "was": "era", "and": "y", "i": "yo", "to": "a",
    "a": "un", "my": "mi", "this": "esto", "that": "eso", "we": "nosotros", "it": "lo", "not": "no",
    "what": "qué", "yes": "sí", "no": "no", "okay": "vale", "look": "mira", "listen": "escucha",
    "wait": "espera", "come": "ven", "on": "en", "well": "bueno", "so": "así que", "plan": "plan",
    "money": "dinero", "car": "coche", "brother": "hermano", "captain": "capitán", "night": "noche",
}


def pseudo_translate(text: str) -> str:
    def word(m: re.Match[str]) -> str:
        w = m.group(0)
        t = WORDS.get(w.lower())
        if t is None:
            return w
        return t.capitalize() if w[0].isupper() else t

    # Leave formatting tags such as <i>…</i> untouched, like a real translator would.
    parts = re.split(r"(<[^>]+>)", text)
    return "".join(p if p.startswith("<") else re.sub(r"[A-Za-z']+", word, p) for p in parts)


def usage(system: str, user: str, output: str) -> dict[str, Any]:
    prompt = (len(system) + len(user)) // 4
    return {
        "prompt_tokens": prompt,
        "completion_tokens": max(1, len(output) // 4),
        "total_tokens": prompt + len(output) // 4,
        "prompt_cache_hit_tokens": len(system) // 4,
        "prompt_cache_miss_tokens": len(user) // 4,
    }


def stage1() -> dict[str, Any]:
    return {
        "synopsis_and_tone": (
            "Police procedural / crime drama. Tense, clipped dialogue with dry humour between partners. "
            "Use natural Latin-American Spanish street register; keep lines short and punchy."
        ),
        "speaker_pronoun_rules": [
            {"speakers": "Maya ↔ Hale (partners)", "rule": "Informal 'tú' — close colleagues who tease each other."},
            {"speakers": "Detectives → Captain Reyes", "rule": "Formal 'usted' and 'señor/capitán'."},
        ],
        "glossary": [
            {"source_term": "Night Shift", "target_term": "Turno de noche", "notes": "Show title"},
            {"source_term": "Kessler crew", "target_term": "la banda de Kessler", "notes": "Criminal gang"},
            {"source_term": "Pier 9", "target_term": "Muelle 9", "notes": "Location"},
            {"source_term": "piece of cake", "target_term": "pan comido", "notes": "Idiom: easy"},
            {"source_term": "spill the beans", "target_term": "soltar la sopa", "notes": "Idiom: reveal"},
        ],
    }


def stage2(user: str) -> dict[str, Any]:
    match = _BATCH_RE.search(user)
    batch: dict[str, Any] = json.loads(match.group(1)) if match else {}
    out: dict[str, str] = {}
    for bid, item in batch.items():
        if int(bid) % 97 == 0 and bid not in _dropped_once:  # exercise the ID-lock retry once
            _dropped_once.add(bid)
            continue
        text = pseudo_translate(item["text"])
        if int(bid) % 23 == 0:
            text += " — y de verdad que no hay ninguna otra manera de decirlo"  # forces HIGH CPS
        out[bid] = text
    return {"translations": out}


def stage3(user: str, structured: bool) -> dict[str, Any]:
    fixes: list[dict[str, str]] = []
    for bid, _dur, flags, _src, trg in _QA_LINE_RE.findall(user):
        current = json.loads(trg)
        if "LOCKED" in flags:
            continue
        if "HIGH CPS" in flags and " — " in current:
            fixes.append({"id": bid, "corrected_text": current.split(" — ")[0], "reason": "Condensed bloated phrasing on a HIGH CPS line"})
        elif "EMPTY" in flags or "MISSING" in flags:
            fixes.append({"id": bid, "corrected_text": "(traducción añadida)", "reason": "Filled a line the translator skipped"})
        elif int(bid) % 17 == 0 and current:
            fixes.append({"id": bid, "corrected_text": current.rstrip(".!?") + ", ¿vale?", "reason": f"Fixed split-sentence agreement with #{int(bid) + 1}"})
    if structured:
        return {"fixes": fixes}
    return {"fixes": {f["id"]: {"corrected_text": f["corrected_text"], "reason": f["reason"]} for f in fixes}}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> dict[str, Any]:
    body = await request.json()
    messages = body.get("messages", [])
    system = next((m["content"] for m in messages if m["role"] == "system"), "")
    user = next((m["content"] for m in messages if m["role"] == "user"), "")
    structured = (body.get("response_format") or {}).get("type") == "json_schema"
    extras = {k: body[k] for k in ("thinking", "chat_template_kwargs", "reasoning_effort", "temperature") if k in body}

    if "TARGET BATCH TO TRANSLATE" in user:
        kind, data = "stage2", stage2(user)
        await asyncio.sleep(0.6)
    elif "ID | duration | flags | SRC | TRG" in user:
        kind, data = "stage3", stage3(user, structured)
        await asyncio.sleep(1.0)
    else:
        kind, data = "stage1", stage1()
        await asyncio.sleep(1.2)
    log.info("%s model=%s extras=%s", kind, body.get("model"), extras)

    content = json.dumps(data, ensure_ascii=False)
    return {
        "id": f"mock-{time.time_ns()}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.get("model", "mock"),
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
        "usage": usage(system, user, content),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    uvicorn.run(app, host="127.0.0.1", port=8099, log_level="warning")
