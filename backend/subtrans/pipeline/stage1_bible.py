"""Stage 1: full-file Translation Bible extraction."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..models import Bible, GlossaryEntry, PronounRule
from .prompts import BIBLE_SCHEMA, build_stage1_system, build_stage1_user

if TYPE_CHECKING:
    from .orchestrator import RunContext


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return "\n".join(f"{k}: {_text(v)}" for k, v in value.items())
    if isinstance(value, list):
        return "\n".join(_text(v) for v in value)
    return str(value)


def coerce_bible(data: dict[str, Any]) -> Bible:
    """Accept the strict schema shape plus common near-misses from json_object providers."""
    rules_raw = data.get("speaker_pronoun_rules") or data.get("pronoun_rules") or []
    if isinstance(rules_raw, dict):
        rules_raw = [{"speakers": k, "rule": v} for k, v in rules_raw.items()]
    rules: list[PronounRule] = []
    for item in rules_raw if isinstance(rules_raw, list) else []:
        if isinstance(item, str) and item.strip():
            rules.append(PronounRule(speakers="General", rule=item.strip()))
        elif isinstance(item, dict):
            speakers = _text(item.get("speakers") or item.get("speaker") or item.get("relationship")) or "General"
            rule = _text(item.get("rule") or item.get("rules") or item.get("formality"))
            if rule:
                rules.append(PronounRule(speakers=speakers, rule=rule))

    glossary_raw = data.get("glossary") or []
    if isinstance(glossary_raw, dict):
        glossary_raw = [{"source_term": k, "target_term": v} for k, v in glossary_raw.items()]
    glossary: list[GlossaryEntry] = []
    for item in glossary_raw if isinstance(glossary_raw, list) else []:
        if not isinstance(item, dict):
            continue
        source = _text(item.get("source_term") or item.get("source") or item.get("term"))
        target = _text(item.get("target_term") or item.get("target") or item.get("translation"))
        if source and target:
            glossary.append(GlossaryEntry(source_term=source, target_term=target, notes=_text(item.get("notes"))))

    return Bible(
        synopsis_and_tone=_text(data.get("synopsis_and_tone") or data.get("synopsis") or data.get("tone")),
        speaker_pronoun_rules=rules,
        glossary=glossary,
    )


async def run_stage1(ctx: "RunContext") -> Bible:
    live, cfg = ctx.live, ctx.config
    client = ctx.client(1)
    with_text = [b for b in live.blocks if b.source_text.strip()]
    sample = with_text[: cfg.bible_max_lines]
    system = build_stage1_system(live.meta.target_language, live.meta.context_hints, client.structured)
    user = build_stage1_user(sample, len(with_text))
    ctx.log(f"Stage 1: extracting Translation Bible from {len(sample)} lines with {client.model}")

    result = await ctx.call(
        1,
        lambda: client.complete_json(
            system=system,
            user=user,
            schema=BIBLE_SCHEMA if client.structured else None,
            schema_name="translation_bible",
            max_tokens=16000,
        ),
    )
    bible = coerce_bible(result.data)
    ctx.log(
        f"Stage 1 done: {len(bible.glossary)} glossary terms, "
        f"{len(bible.speaker_pronoun_rules)} pronoun/formality rules"
    )
    return bible
