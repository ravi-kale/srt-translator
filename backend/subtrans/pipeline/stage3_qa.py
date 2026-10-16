"""Stage 3: deterministic pre-check + flagship LLM critic returning sparse patches."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from ..llm.base import LLMError, LLMRefusalError
from ..text_metrics import refresh_metrics
from .prompts import CRITIC_SCHEMA, build_stage3_system, build_stage3_user

if TYPE_CHECKING:
    from .orchestrator import RunContext


def normalize_fixes(data: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """Accept both {"fixes": [{id, corrected_text, reason}]} and {"fixes": {"<id>": {...}}}."""
    raw = data.get("fixes", {}) if isinstance(data, dict) else {}
    items: list[tuple[Any, Any, Any]] = []
    if isinstance(raw, list):
        items = [(f.get("id"), f.get("corrected_text"), f.get("reason")) for f in raw if isinstance(f, dict)]
    elif isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(value, dict):
                items.append((key, value.get("corrected_text"), value.get("reason")))
            elif isinstance(value, str):
                items.append((key, value, ""))
    out: dict[str, tuple[str, str]] = {}
    for fid, text, reason in items:
        if fid is None or not isinstance(text, str):
            continue
        text = text.replace("\\n", "\n").strip()
        if text:
            out[str(fid).strip().lstrip("#").strip()] = (text, str(reason or "").strip())
    return out


async def run_precheck(ctx: "RunContext") -> None:
    """Recompute CPS + flags for every block; emit only the blocks whose metrics changed."""
    changed = []
    async with ctx.live.lock:
        for b in ctx.live.blocks:
            before = (b.cps, tuple(b.flags))
            refresh_metrics(b, ctx.config.cps_threshold)
            if (b.cps, tuple(b.flags)) != before:
                changed.append(b)
    if changed:
        await ctx.save_blocks(changed)
        ctx.emit_blocks(changed)


async def run_stage3(ctx: "RunContext") -> list[str]:
    """Returns error messages for windows that failed (they stay pending for a resume)."""
    live, cfg = ctx.live, ctx.config
    progress = live.meta.progress
    await run_precheck(ctx)

    blocks = live.blocks
    windows = [blocks[i : i + cfg.qa_window] for i in range(0, len(blocks), cfg.qa_window)]
    if progress.qa_window_size != cfg.qa_window:
        progress.qa_windows_done = []
        progress.qa_window_size = cfg.qa_window
    progress.stage = 3
    progress.qa_windows_total = len(windows)
    progress.qa_fixes = sum(1 for b in blocks if b.qa_fixed)
    done = set(progress.qa_windows_done)
    pending = [i for i in range(len(windows)) if i not in done]
    await ctx.save_meta()
    ctx.emit_progress()
    if not pending:
        return []

    client = ctx.client(3)
    system = build_stage3_system(
        live.meta.target_language, live.meta.context_hints, live.meta.bible, cfg.cps_threshold, client.structured
    )
    ctx.log(f"Stage 3: QA critic on {len(pending)} window(s) of {cfg.qa_window} lines with {client.model}")
    semaphore = asyncio.Semaphore(cfg.qa_concurrency)
    errors: list[str] = []

    async def mark_done(i: int) -> None:
        progress.qa_windows_done.append(i)
        progress.qa_fixes = sum(1 for b in blocks if b.qa_fixed)
        await ctx.save_meta()
        ctx.emit_progress()

    async def worker(i: int) -> None:
        async with semaphore:
            if ctx.pause_requested:
                return
            window = windows[i]
            if not any(b.source_text.strip() for b in window):
                await mark_done(i)
                return
            user = build_stage3_user(window, i + 1, len(windows))
            try:
                result = await ctx.call(
                    3,
                    lambda: client.complete_json(
                        system=system,
                        user=user,
                        schema=CRITIC_SCHEMA if client.structured else None,
                        schema_name="qa_patch",
                        max_tokens=16000,
                    ),
                )
            except LLMRefusalError as exc:
                ctx.log(f"QA window {i + 1}: {exc}; drafts kept as-is", "warning")
                await mark_done(i)
                return
            except LLMError as exc:
                if exc.fatal:
                    raise
                if getattr(ctx, "out_of_time", False):
                    return  # window stays pending for the next step
                errors.append(f"window {i + 1}: {exc}")
                ctx.log(f"QA window {i + 1} failed after retries: {exc}", "error")
                return
            applied = await ctx.apply_fixes(window, normalize_fixes(result.data))
            if applied:
                ctx.log(f"QA window {i + 1}/{len(windows)}: {applied} fix(es)")
            await mark_done(i)

    try:
        async with asyncio.TaskGroup() as tg:
            for i in pending:
                tg.create_task(worker(i))
    except ExceptionGroup as group:
        raise group.exceptions[0] from None
    return errors
