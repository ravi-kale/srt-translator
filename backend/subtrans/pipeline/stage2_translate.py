"""Stage 2: bidirectional sliding-window translation with a strict 1-to-1 ID lock."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Sequence

from ..batching import create_smart_batches, split_into_lanes
from ..llm.base import LLMClient, LLMError
from ..models import Block
from .prompts import build_stage2_system, build_stage2_user

if TYPE_CHECKING:
    from .orchestrator import RunContext


def needs_translation(b: Block) -> bool:
    return (
        bool(b.source_text.strip())
        and not b.user_edited
        and not b.draft_translation.strip()
        and not b.final_translation.strip()
    )


def normalize_translations(data: dict[str, Any]) -> dict[str, str]:
    payload = data.get("translations", data) if isinstance(data, dict) else {}
    if not isinstance(payload, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in payload.items():
        if isinstance(value, dict):
            value = value.get("text") or value.get("translation") or ""
        if not isinstance(value, str):
            continue
        text = value.replace("\\n", "\n").strip()
        if text:
            out[str(key).strip().lstrip("#").strip()] = text
    return out


def _max_tokens(targets: Sequence[Block]) -> int:
    return min(8192, max(2048, 160 * len(targets)))


async def _attempt(ctx: "RunContext", client: LLMClient, system: str, targets: Sequence[Block]) -> dict[str, str] | None:
    """One request (with transport retries). None means the call failed non-fatally."""
    user = build_stage2_user(ctx.live.blocks, targets, ctx.config.context_window)
    try:
        result = await ctx.call(
            2,
            lambda: client.complete_json(
                system=system, user=user, schema=None, schema_name="translations", max_tokens=_max_tokens(targets)
            ),
        )
    except LLMError as exc:
        if exc.fatal:
            raise
        ctx.log(f"Batch {targets[0].id}–{targets[-1].id} failed: {exc}", "warning")
        return None
    return normalize_translations(result.data)


async def translate_batch(ctx: "RunContext", client: LLMClient, system: str, targets: list[Block]) -> None:
    cfg = ctx.config
    ids = [b.id for b in targets]
    translations = await _attempt(ctx, client, system, targets)
    if translations is not None and set(translations) == set(ids):
        await ctx.apply_drafts(targets, translations, missing=())
        return
    if getattr(ctx, "out_of_time", False):
        return  # this step's time budget is spent: leave the batch untouched for the next step

    matched = len(set(translations or {}) & set(ids))
    ctx.log(
        f"ID lock mismatch in batch {ids[0]}–{ids[-1]} ({matched}/{len(ids)} IDs returned); "
        f"retrying once in chunks of {cfg.retry_chunk_size}",
        "warning",
    )
    results: dict[str, str] = {}
    for sub in create_smart_batches(targets, cfg.retry_chunk_size, cfg.retry_chunk_size + 6, cfg.gap_sec):
        sub_ids = {b.id for b in sub}
        retried = await _attempt(ctx, client, system, sub)
        if retried:
            results.update({k: v for k, v in retried.items() if k in sub_ids})
    missing = [b for b in targets if b.id not in results]
    if missing:
        ctx.log(
            f"{len(missing)} line(s) still missing after retry ({', '.join(b.id for b in missing[:10])}"
            f"{'…' if len(missing) > 10 else ''}); flagged MISSING for the QA critic",
            "warning",
        )
    await ctx.apply_drafts(targets, results, missing=missing)


async def run_stage2(ctx: "RunContext") -> None:
    live, cfg = ctx.live, ctx.config
    progress = live.meta.progress
    batches = create_smart_batches(live.blocks, cfg.target_size, cfg.max_size, cfg.gap_sec)
    lanes = split_into_lanes(batches, cfg.lanes)
    progress.stage = 2
    progress.batches_total = len(batches)
    progress.batches_done = sum(1 for batch in batches if not any(needs_translation(b) for b in batch))
    await ctx.save_meta()
    ctx.emit_progress()
    if progress.batches_done == progress.batches_total:
        return

    client = ctx.client(2)
    system = build_stage2_system(live.meta.target_language, live.meta.context_hints, live.meta.bible, cfg.cps_threshold)
    ctx.log(
        f"Stage 2: {progress.batches_total - progress.batches_done} of {progress.batches_total} batches to translate "
        f"with {client.model} across {len(lanes)} lane(s)"
    )

    async def lane_worker(lane: list[list[Block]]) -> None:
        for batch in lane:
            if ctx.pause_requested:
                return
            targets = [b for b in batch if needs_translation(b)]
            if not targets:
                continue
            await translate_batch(ctx, client, system, targets)
            progress.batches_done += 1
            await ctx.save_meta()
            ctx.emit_progress()

    try:
        async with asyncio.TaskGroup() as tg:
            for lane in lanes:
                tg.create_task(lane_worker(lane))
    except ExceptionGroup as group:
        raise group.exceptions[0] from None
