"""Prompt templates. System prompts are static per job so provider prefix caches hit on every call."""

from __future__ import annotations

import json
from typing import Any, Sequence

from ..models import FLAG_EMPTY, FLAG_HIGH_CPS, FLAG_IDENTICAL, FLAG_MISSING, Bible, Block

# ---------------------------------------------------------------------------
# JSON schemas (strict-mode compatible: every property required, no extra keys)
# ---------------------------------------------------------------------------

BIBLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "synopsis_and_tone": {"type": "string"},
        "speaker_pronoun_rules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"speakers": {"type": "string"}, "rule": {"type": "string"}},
                "required": ["speakers", "rule"],
                "additionalProperties": False,
            },
        },
        "glossary": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_term": {"type": "string"},
                    "target_term": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["source_term", "target_term", "notes"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["synopsis_and_tone", "speaker_pronoun_rules", "glossary"],
    "additionalProperties": False,
}

CRITIC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "fixes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "corrected_text": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "corrected_text", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["fixes"],
    "additionalProperties": False,
}


def _q(text: str) -> str:
    """Quote subtitle text unambiguously (keeps \\n visible as an escape)."""
    return json.dumps(text, ensure_ascii=False)


def render_bible(bible: Bible | None) -> str:
    """Deterministic rendering: identical Bible -> byte-identical text -> stable cache prefix."""
    if bible is None:
        return "## TRANSLATION BIBLE\n(none provided)"
    out = ["## TRANSLATION BIBLE", "### Synopsis, genre & tone", bible.synopsis_and_tone.strip() or "(not specified)"]
    out.append("### Speaker, pronoun & formality rules")
    if bible.speaker_pronoun_rules:
        out += [f"- {r.speakers.strip()}: {r.rule.strip()}" for r in bible.speaker_pronoun_rules]
    else:
        out.append("- (none)")
    out.append("### Glossary (use these target terms consistently)")
    if bible.glossary:
        for g in bible.glossary:
            note = f"  ({g.notes.strip()})" if g.notes.strip() else ""
            out.append(f"- {_q(g.source_term.strip())} -> {_q(g.target_term.strip())}{note}")
    else:
        out.append("- (none)")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Stage 1 — Translation Bible
# ---------------------------------------------------------------------------

_BIBLE_EXAMPLE = {
    "synopsis_and_tone": "Gritty crime drama ... Register: casual, profane street talk; terse lines.",
    "speaker_pronoun_rules": [
        {"speakers": "Raj -> his mother", "rule": "Always respectful 'Aap'; never 'Tu'."},
    ],
    "glossary": [
        {"source_term": "Detective Hale", "target_term": "Detective Hale", "notes": "Name: never translate"},
    ],
}


def build_stage1_system(target_language: str, context_hints: str, structured: bool) -> str:
    contract = (
        "Respond with the JSON object defined by the response schema."
        if structured
        else "Respond with json only, exactly in this shape:\n" + json.dumps(_BIBLE_EXAMPLE, ensure_ascii=False, indent=2)
    )
    return f"""You are a senior film & TV subtitle localization lead preparing a "Translation Bible" for translators who will localize this subtitle file into {target_language}.

Study the transcript (subtitle IDs + source text only) and produce:
1. synopsis_and_tone: genre, setting, plot gist, emotional register, humour/profanity level, and concrete style notes for {target_language} subtitles (sentence length, slang policy, how to render catch-phrases).
2. speaker_pronoun_rules: one rule per speaker or relationship covering formality and address forms in {target_language} (e.g. Tu/Tum/Aap, Tú/Usted, Tu/Vous, du/Sie, keigo levels), plus gender agreement for characters whose gender is evident. Infer relationships from the dialogue.
3. glossary: character and place names, titles, technical or invented terms, brand names and recurring idioms/catch-phrases, each with the exact {target_language} rendering to use and short notes (e.g. "keep in English", "idiom: means ..."). Prefer 15-60 high-value entries; skip trivial words.

User context hints: {context_hints.strip() or "none"}

{contract}"""


def build_stage1_user(blocks: Sequence[Block], total_lines: int) -> str:
    transcript = "\n".join(f"{b.id}: {b.source_text.replace(chr(10), ' / ')}" for b in blocks)
    return (
        f"SUBTITLE TRANSCRIPT — {len(blocks)} of {total_lines} lines (ID: text):\n{transcript}\n\n"
        "Produce the Translation Bible as json."
    )


# ---------------------------------------------------------------------------
# Stage 2 — sliding-window translation
# ---------------------------------------------------------------------------


def build_stage2_system(target_language: str, context_hints: str, bible: Bible | None, cps_threshold: float) -> str:
    return f"""You are an elite subtitle translator localizing a film/series into {target_language}.

CORE RULES
1. Translate conversational meaning, intent and idioms naturally — never word-for-word. Write dialogue a native {target_language} viewer would find natural and idiomatic.
2. A spoken sentence often spans 2-3 consecutive IDs. Mentally merge those IDs, translate the complete thought naturally, then split the {target_language} text back across those SAME IDs in proportion to each ID's duration_sec, so each subtitle reads naturally at its own moment.
3. Respect reading speed: aim for at most {cps_threshold:g} characters per second (characters / duration_sec). Lines shorter than 1.5 s must be very concise. Condense filler rather than exceed reading speed.
4. At most 2 lines per subtitle and roughly 42 characters per line; use "\\n" for a line break.
5. Preserve formatting tags such as <i>...</i>, leading dialogue dashes ("- "), and sound/music cue brackets (translate their contents briefly).
6. Follow the Translation Bible: use glossary target terms exactly and apply the speaker/pronoun/formality rules consistently.
7. STRICT 1-TO-1 ID LOCK: return exactly one translation for every ID in TARGET BATCH — never skip, merge, add or renumber IDs, and never return an empty string for a non-empty source. The PREVIOUS and UPCOMING CONTEXT sections are read-only: never translate or return them.
8. Output json only, exactly in this shape: {{"translations": {{"<id>": "<{target_language} text>", ...}}}}

TARGET LANGUAGE: {target_language}
USER CONTEXT HINTS: {context_hints.strip() or "none"}

{render_bible(bible)}"""


def build_stage2_user(all_blocks: Sequence[Block], targets: Sequence[Block], window: int) -> str:
    first, last = targets[0].pos, targets[-1].pos
    previous = all_blocks[max(0, first - window) : first]
    upcoming = all_blocks[last + 1 : last + 1 + window]

    prev_lines: list[str] = []
    for b in previous:
        prev_lines.append(f"[{b.id}] ({b.duration_sec:.1f}s) SRC: {_q(b.source_text)}")
        translated = b.current_text
        prev_lines.append(f"      TRG: {_q(translated)}" if translated else "      TRG: (not yet translated)")
    up_lines = [f"[{b.id}] ({b.duration_sec:.1f}s) SRC: {_q(b.source_text)}" for b in upcoming]

    batch = {b.id: {"duration_sec": round(b.duration_sec, 2), "text": b.source_text} for b in targets}
    ids = ", ".join(b.id for b in targets)
    return "\n".join(
        [
            "PREVIOUS CONTEXT (Read-only, already translated):",
            "\n".join(prev_lines) if prev_lines else "(start of file)",
            "",
            "TARGET BATCH TO TRANSLATE:",
            json.dumps(batch, ensure_ascii=False, indent=1),
            "",
            "UPCOMING CONTEXT (Read-only):",
            "\n".join(up_lines) if up_lines else "(end of file)",
            "",
            f'Return json {{"translations": {{...}}}} containing exactly these {len(targets)} IDs: {ids}',
        ]
    )


# ---------------------------------------------------------------------------
# Stage 3 — QA critic
# ---------------------------------------------------------------------------

FLAG_LABELS = {
    FLAG_HIGH_CPS: "⚠️ HIGH CPS",
    FLAG_EMPTY: "EMPTY",
    FLAG_MISSING: "MISSING",
    FLAG_IDENTICAL: "IDENTICAL TO SOURCE",
}


def build_stage3_system(
    target_language: str, context_hints: str, bible: Bible | None, cps_threshold: float, structured: bool
) -> str:
    if structured:
        contract = (
            'Respond with the JSON object defined by the response schema: {"fixes": [{"id": "<id>", '
            '"corrected_text": "...", "reason": "..."}]}. Use an empty array when nothing needs fixing.'
        )
    else:
        contract = (
            'Output json only, exactly in this shape: {"fixes": {"<id>": {"corrected_text": "...", '
            '"reason": "..."}}}. Use {"fixes": {}} when nothing needs fixing.'
        )
    return f"""You are the final QA critic for {target_language} subtitles drafted by a fast machine-translation model. You receive windows of consecutive subtitles as `ID | duration | flags | SRC | TRG`.

Fix ONLY these five error classes:
1. Awkward or broken grammar across split consecutive IDs (agreement, word order, dangling or duplicated clauses where a sentence was split).
2. Stiff or literal translations of idioms, slang or jokes.
3. Gender, pronoun-formality or glossary inconsistencies against the Translation Bible.
4. Untranslated source-language leaks (including lines flagged IDENTICAL TO SOURCE that should be translated; names and universal interjections may legitimately stay).
5. Bloated phrasing on [⚠️ HIGH CPS] lines: condense to at most {cps_threshold:g} characters per second without losing meaning.
Lines flagged EMPTY or MISSING have no translation: supply one.

Rules:
- Return a SPARSE patch: only IDs you actually changed. Leave acceptable lines untouched — never rewrite for personal style.
- Never change lines flagged LOCKED (human-edited); use them only as reference.
- Keep line breaks as "\\n" (max 2 lines), formatting tags and dialogue dashes.
- corrected_text is the complete replacement subtitle text for that ID.
- reason: one short sentence naming the error class, e.g. "Fixed split-sentence verb agreement with #143".
- {contract}

TARGET LANGUAGE: {target_language}
USER CONTEXT HINTS: {context_hints.strip() or "none"}

{render_bible(bible)}"""


def format_qa_line(b: Block) -> str:
    flags = [FLAG_LABELS[f] for f in b.flags if f in FLAG_LABELS]
    if b.user_edited:
        flags.append("LOCKED")
    flag_text = ", ".join(flags) if flags else "-"
    return f"{b.id} | {b.duration_sec:.1f}s | {flag_text} | SRC: {_q(b.source_text)} | TRG: {_q(b.current_text)}"


def build_stage3_user(window: Sequence[Block], window_no: int, window_total: int) -> str:
    lines = "\n".join(format_qa_line(b) for b in window)
    return (
        f"WINDOW {window_no}/{window_total} — IDs {window[0].id} to {window[-1].id}\n"
        f"ID | duration | flags | SRC | TRG\n{lines}\n\nReturn the sparse json patch."
    )
