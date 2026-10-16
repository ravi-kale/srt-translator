"""Job manager: runs the 3-stage pipeline as resumable steps and handles pause/resume/rerun/edits.

All job state lives in the database, so any server instance can serve any request. A run is a
chain of *steps*. Each step loads the job, advances it until it finishes, pauses, or uses up its
time budget, persists everything, and reports whether more work remains. A Runner decides where
steps execute: LocalRunner loops them in this process (uvicorn), QueueRunner sends each step to
Vercel Queues so it runs in its own short-lived function invocation.

A lease on the job row keeps two steps of the same job from running at once, and a run_id lets a
step notice that its run was paused, restarted or deleted from another instance.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Protocol, Sequence

from ..estimate import estimate_job
from ..llm.base import LLMClient, LLMError, LLMResult, Usage, call_with_retries
from ..llm.factory import ClientFactory
from ..models import (
    FLAG_MISSING,
    RUNNING_STATUSES,
    BibleUpdate,
    Block,
    JobChanges,
    JobDetail,
    JobMeta,
    JobStatus,
    JobSummary,
    RerunRequest,
    RunConfig,
    RunRequest,
)
from ..srt_parser import ParsedSrt, build_srt
from ..store import JobStore, RunControl
from ..text_metrics import refresh_metrics
from .stage1_bible import run_stage1
from .stage2_translate import needs_translation, run_stage2
from .stage3_qa import run_stage3

log = logging.getLogger(__name__)

# A running step renews its lease every watch_interval, so a short TTL is safe; if the step's
# function is killed, the job is free again (and a redelivered step resumes it) within a minute.
LEASE_TTL = 60.0
# Queue mode: no LLM call may run past this point of a step, whatever the provider does
# (OpenRouter keep-alive bytes defeat plain HTTP read timeouts). Below Vercel's 300 s limit.
STEP_HARD_LIMIT = 270.0
MIN_CALL_WINDOW = 15.0
# Queue mode: a running job with no live lease and no write for this long lost its step chain.
STALE_AFTER = 90.0


class JobNotFound(Exception):
    pass


class JobConflict(Exception):
    pass


class LeaseBusy(Exception):
    """Another step of this job is running right now; try again shortly."""


class MissingKeys(Exception):
    def __init__(self, keys: list[str]):
        super().__init__(f"Missing API keys in backend/.env: {', '.join(keys)}")
        self.keys = keys


class LiveJob:
    """A loaded copy of one job, private to the request or step that loaded it."""

    def __init__(self, meta: JobMeta, blocks: list[Block]):
        self.meta = meta
        self.blocks = blocks
        self.by_id = {b.id: b for b in blocks}
        self.lock = asyncio.Lock()
        self.pause_requested = False
        # The run this step belongs to was replaced or the job deleted: stop without writing status.
        self.superseded = False

    @property
    def cps_threshold(self) -> float:
        return self.meta.run_config.cps_threshold if self.meta.run_config else RunConfig().cps_threshold


class RunContext:
    """Everything a stage needs: live state, config, clients, persistence and logging."""

    def __init__(
        self,
        manager: "JobManager",
        live: LiveJob,
        config: RunConfig,
        deadline: float | None = None,
        hard_deadline: float | None = None,
    ):
        self.manager = manager
        self.live = live
        self.config = config
        self.deadline = deadline  # soft: stop starting new work
        self.hard_deadline = hard_deadline  # hard: cut off in-flight calls
        self._clients: dict[int, LLMClient] = {}
        self._logs: list[tuple[float, str, str]] = []

    # -- clients / calls ---------------------------------------------------
    def client(self, stage: int) -> LLMClient:
        if stage not in self._clients:
            self._clients[stage] = self.manager.factory.build(stage, self.config)
        return self._clients[stage]

    async def call(self, stage: int, fn: Callable[[], Awaitable[LLMResult]]) -> LLMResult:
        def on_failed(exc: LLMError, _attempt: int) -> None:
            if exc.usage is not None:
                self.add_usage(stage, exc.usage)

        async def bounded() -> LLMResult:
            if self.hard_deadline is None:
                return await fn()
            remaining = self.hard_deadline - time.monotonic()
            if remaining < MIN_CALL_WINDOW:
                raise LLMError("step time limit reached; continuing in the next step", retryable=True)
            try:
                return await asyncio.wait_for(fn(), remaining)
            except TimeoutError:
                raise LLMError("call cut off at the step time limit; continuing in the next step", retryable=True) from None

        result = await call_with_retries(bounded, on_failed_attempt=on_failed, may_retry=lambda: not self.out_of_time)
        self.add_usage(stage, result.usage)
        return result

    def add_usage(self, stage: int, usage: Usage) -> None:
        su = self.live.meta.usage.stage(stage)
        su.calls += usage.calls
        su.input_tokens += usage.input_tokens
        su.cache_hit_tokens += usage.cache_hit_tokens
        su.cache_write_tokens += usage.cache_write_tokens
        su.output_tokens += usage.output_tokens
        su.cost_usd = round(su.cost_usd + usage.cost_usd, 6)

    @property
    def out_of_time(self) -> bool:
        return self.deadline is not None and time.monotonic() >= self.deadline

    @property
    def pause_requested(self) -> bool:
        # Stages stop taking new work on a real pause *or* when this step's time budget is spent.
        return self.live.pause_requested or self.out_of_time

    # -- persistence -------------------------------------------------------
    async def save_meta(self) -> None:
        await self.manager.save_meta(self.live)
        await self.flush_logs()

    async def save_blocks(self, blocks: Iterable[Block]) -> None:
        await self.manager.store.save_blocks(self.live.meta.id, blocks, keep_user_edits=True)

    async def flush_logs(self) -> None:
        logs, self._logs = self._logs, []
        await self.manager.store.append_logs(self.live.meta.id, logs)

    # -- progress reporting (clients poll the database, so these are no-ops) ----
    def emit_progress(self) -> None:
        pass

    def emit_blocks(self, blocks: Sequence[Block]) -> None:
        pass

    def emit_usage(self) -> None:
        pass

    def log(self, message: str, level: str = "info") -> None:
        getattr(log, "warning" if level == "warning" else "error" if level == "error" else "info")(
            "[job %s] %s", self.live.meta.id, message
        )
        self.live.meta.progress.last_message = message
        self._logs.append((time.time(), level, message))

    # -- state mutation (stage lanes run concurrently, so always under the job lock) ----
    async def apply_drafts(self, targets: Sequence[Block], translations: dict[str, str], missing: Sequence[Block]) -> None:
        missing_ids = {b.id for b in missing}
        async with self.live.lock:
            for b in targets:
                text = translations.get(b.id)
                if text is not None:
                    b.draft_translation = text
                    if not b.user_edited:
                        b.final_translation = text
                    b.flags = [f for f in b.flags if f != FLAG_MISSING]
                elif b.id in missing_ids:
                    b.flags = sorted(set(b.flags) | {FLAG_MISSING})
                refresh_metrics(b, self.config.cps_threshold)
        await self.save_blocks(targets)

    async def apply_fixes(self, window: Sequence[Block], fixes: dict[str, tuple[str, str]]) -> int:
        by_id = {b.id: b for b in window}
        changed: list[Block] = []
        async with self.live.lock:
            for fid, (text, reason) in fixes.items():
                b = by_id.get(fid)
                if b is None or b.user_edited or text == b.current_text:
                    continue
                b.final_translation = text
                b.qa_fixed = True
                b.qa_reason = reason or "QA critic fix"
                refresh_metrics(b, self.config.cps_threshold)
                changed.append(b)
        if changed:
            await self.save_blocks(changed)
        return len(changed)


# ----------------------------------------------------------------------------- runners


class Runner(Protocol):
    def bind(self, manager: "JobManager") -> None: ...
    async def schedule(self, job_id: str, run_id: str) -> None: ...
    def is_running(self, job_id: str, ctl: RunControl, now: float) -> bool: ...
    async def cancel(self, job_id: str) -> None: ...
    async def shutdown(self) -> None: ...


class LocalRunner:
    """Runs each job's steps back to back in an asyncio task of this process."""

    def __init__(self) -> None:
        self.manager: JobManager | None = None
        self.tasks: dict[str, asyncio.Task[None]] = {}

    def bind(self, manager: "JobManager") -> None:
        self.manager = manager

    async def schedule(self, job_id: str, run_id: str) -> None:
        assert self.manager is not None
        self.tasks[job_id] = asyncio.create_task(self._loop(job_id, run_id), name=f"job-{job_id}")

    async def _loop(self, job_id: str, run_id: str) -> None:
        assert self.manager is not None
        try:
            while await self.manager.step(job_id, run_id):
                pass
        finally:
            if self.tasks.get(job_id) is asyncio.current_task():
                self.tasks.pop(job_id, None)

    def is_running(self, job_id: str, ctl: RunControl, now: float) -> bool:
        task = self.tasks.get(job_id)
        return task is not None and not task.done()

    async def wait(self, job_id: str, timeout: float = 10.0) -> None:
        task = self.tasks.get(job_id)
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), timeout)

    async def cancel(self, job_id: str) -> None:
        task = self.tasks.pop(job_id, None)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def shutdown(self) -> None:
        tasks = [t for t in self.tasks.values() if not t.done()]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()


QUEUE_TOPIC = "subtrans-steps"


class QueueRunner:
    """Sends each step to Vercel Queues; the subscriber in worker.py runs it and chains the next."""

    def __init__(self, client: Any = None) -> None:
        self.manager: JobManager | None = None
        self.client = client  # a vercel.queue QueueClient; None = the SDK's default client

    def bind(self, manager: "JobManager") -> None:
        self.manager = manager

    async def schedule(self, job_id: str, run_id: str) -> None:
        payload = {"job_id": job_id, "run_id": run_id}
        if self.client is not None:
            await self.client.send(QUEUE_TOPIC, payload)
            return
        from vercel.queue import send  # only needed on Vercel

        await send(QUEUE_TOPIC, payload)

    def is_running(self, job_id: str, ctl: RunControl, now: float) -> bool:
        if ctl.status not in RUNNING_STATUSES or not ctl.run_id:
            return False
        return ctl.lease_active(now) or now - ctl.updated_at < STALE_AFTER

    async def cancel(self, job_id: str) -> None:
        pass  # clearing run_id (done by the manager) makes any pending or running step stand down

    async def shutdown(self) -> None:
        pass


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower() or "translated"


# ----------------------------------------------------------------------------- manager


class JobManager:
    def __init__(self, store: JobStore, factory: ClientFactory, runner: Runner | None = None, *, watch_interval: float = 2.0):
        self.store = store
        self.factory = factory
        self.runner: Runner = runner if runner is not None else LocalRunner()
        self.runner.bind(self)
        self.watch_interval = watch_interval
        # Steps running in this process, so a pause served by this process takes effect at once.
        self._active: dict[str, LiveJob] = {}

    # -- lifecycle ---------------------------------------------------------
    async def startup(self) -> int:
        if isinstance(self.runner, LocalRunner):
            return await self.store.mark_interrupted()
        return 0

    async def shutdown(self) -> None:
        await self.runner.shutdown()

    # -- loading / reading -------------------------------------------------
    async def get(self, job_id: str) -> LiveJob:
        meta = await self.store.get_meta(job_id)
        if meta is None:
            raise JobNotFound(job_id)
        return LiveJob(meta, await self.store.get_blocks(job_id))

    async def list_jobs(self) -> list[JobSummary]:
        return await self.store.list_jobs()

    async def detail(self, live: LiveJob) -> JobDetail:
        meta = live.meta
        return JobDetail(
            job=JobSummary.from_meta(meta),
            run_config=meta.run_config,
            bible=meta.bible,
            usage=meta.usage,
            total_cost_usd=meta.usage.total_cost_usd,
            blocks=live.blocks,
            rev=await self.store.get_rev(meta.id),
            logs=await self.store.get_logs(meta.id),
        )

    async def detail_by_id(self, job_id: str) -> JobDetail:
        # Read the cursor before the blocks so nothing written in between is skipped by the next poll.
        rev = await self.store.get_rev(job_id)
        live = await self.get(job_id)
        detail = await self.detail(live)
        detail.rev = rev
        return detail

    async def changes(self, job_id: str, since_rev: int, after_log: int) -> JobChanges:
        rev = await self.store.get_rev(job_id)
        meta = await self.store.get_meta(job_id)
        if meta is None:
            raise JobNotFound(job_id)
        blocks = await self.store.get_blocks_since(job_id, since_rev) if rev > since_rev else []
        await self._heal(job_id)
        return JobChanges(
            job=JobSummary.from_meta(meta),
            run_config=meta.run_config,
            bible=meta.bible,
            usage=meta.usage,
            total_cost_usd=meta.usage.total_cost_usd,
            blocks=blocks,
            rev=rev,
            logs=await self.store.get_logs(job_id, after_log),
        )

    async def _heal(self, job_id: str) -> None:
        """Queue mode: restart a run whose step chain was lost (e.g. a step killed at the time limit
        after its retries ran out). The lease keeps this from ever running two steps at once."""
        if isinstance(self.runner, LocalRunner):
            return
        ctl = await self.store.get_control(job_id)
        now = time.time()
        if ctl is None or ctl.status not in RUNNING_STATUSES or not ctl.run_id or self.runner.is_running(job_id, ctl, now):
            return
        log.warning("[job %s] step chain stalled; rescheduling", job_id)
        await self.store.db.execute("UPDATE jobs SET updated_at=? WHERE id=?", (now, job_id))
        await self.runner.schedule(job_id, ctl.run_id)

    # -- persistence helpers -----------------------------------------------
    async def save_meta(self, live: LiveJob) -> None:
        live.meta.updated_at = time.time()
        await self.store.save_meta(live.meta)

    async def _set_status(self, live: LiveJob, status: JobStatus, error: str | None = None) -> None:
        live.meta.status = status
        live.meta.error = error
        await self.save_meta(live)

    # -- create ------------------------------------------------------------
    async def create(self, filename: str, parsed: ParsedSrt, target_language: str, context_hints: str) -> LiveJob:
        now = time.time()
        meta = JobMeta(
            id=uuid.uuid4().hex[:12],
            filename=filename,
            created_at=now,
            updated_at=now,
            status="ready",
            target_language=target_language.strip(),
            context_hints=context_hints.strip(),
            line_ending=parsed.line_ending,
            source_encoding=parsed.encoding,
            block_count=len(parsed.blocks),
        )
        await self.store.create_job(meta, parsed.blocks)
        return LiveJob(meta, parsed.blocks)

    # -- run control ---------------------------------------------------------
    def _pending_stages(self, live: LiveJob, config: RunConfig) -> set[int]:
        stages: set[int] = set()
        if live.meta.bible is None:
            stages.add(1)
        if any(needs_translation(b) for b in live.blocks):
            stages.add(2)
        progress = live.meta.progress
        windows = -(-len(live.blocks) // config.qa_window) if live.blocks else 0
        if config.qa_enabled and (
            progress.qa_window_size != config.qa_window or len(set(progress.qa_windows_done)) < windows
        ):
            stages.add(3)
        return stages

    def _apply_request(self, live: LiveJob, req: RunRequest) -> None:
        meta = live.meta
        if req.target_language is not None and req.target_language.strip() != meta.target_language:
            if meta.bible is not None or any(b.draft_translation or b.final_translation for b in live.blocks):
                raise JobConflict("Target language is locked once translation has started; upload the file again for another language.")
            meta.target_language = req.target_language.strip()
        if req.context_hints is not None:
            meta.context_hints = req.context_hints.strip()
        if not meta.target_language:
            raise JobConflict("Choose a target language first.")

    async def _running(self, job_id: str) -> bool:
        ctl = await self.store.get_control(job_id)
        return ctl is not None and self.runner.is_running(job_id, ctl, time.time())

    async def start(self, job_id: str, req: RunRequest) -> LiveJob:
        live = await self.get(job_id)
        if await self._running(job_id):
            raise JobConflict("This job is already running.")
        self._apply_request(live, req)
        missing = self.factory.missing_keys(req.config, self._pending_stages(live, req.config))
        if missing:
            raise MissingKeys(missing)
        meta = live.meta
        if meta.status == "awaiting_review":
            meta.bible_approved = True  # starting from review == approving the Bible
        meta.run_config = req.config
        meta.error = None
        meta.progress.started_at = time.time()
        meta.progress.finished_at = None
        meta.progress.stage = 1 if meta.bible is None else 2
        meta.status = "bible_running" if meta.bible is None else "translating"
        run_id = uuid.uuid4().hex
        await self.save_meta(live)
        await self.store.set_run(job_id, run_id)
        await self.runner.schedule(job_id, run_id)
        return live

    async def pause(self, job_id: str) -> LiveJob:
        live = await self.get(job_id)
        ctl = await self.store.get_control(job_id)
        now = time.time()
        if ctl is None or not self.runner.is_running(job_id, ctl, now):
            raise JobConflict("This job is not running.")
        active = self._active.get(job_id)
        if active is not None:
            active.pause_requested = True
        if not isinstance(self.runner, LocalRunner) and not ctl.lease_active(now):
            # No step is executing (one is only queued): pause right here; the queued step stands down.
            await self.store.set_run(job_id, None)
            live.meta.progress.last_message = "Paused. Resume continues where it stopped."
            await self._set_status(live, "paused")
            return live
        await self.store.request_pause(job_id)
        await self.store.append_logs(job_id, [(time.time(), "info", "Pause requested — finishing in-flight requests…")])
        return live

    async def rerun(self, job_id: str, req: RerunRequest) -> LiveJob:
        live = await self.get(job_id)
        if await self._running(job_id):
            raise JobConflict("Pause the job before re-running a stage.")
        if live.meta.bible is None:
            raise JobConflict("Run Stage 1 first.")
        if req.stage == 3 and not req.config.qa_enabled:
            req = req.model_copy(update={"config": req.config.model_copy(update={"qa_enabled": True})})
        self._apply_request(live, req)
        stages = ({2, 3} if req.config.qa_enabled else {2}) if req.stage == 2 else {3}
        missing = self.factory.missing_keys(req.config, stages)
        if missing:
            raise MissingKeys(missing)
        threshold = req.config.cps_threshold
        for b in live.blocks:
            if req.stage == 2 and not b.user_edited:
                b.draft_translation = ""
                b.final_translation = ""
            elif req.stage == 3 and not b.user_edited:
                b.final_translation = b.draft_translation
            b.qa_fixed = False
            b.qa_reason = ""
            b.flags = []
            refresh_metrics(b, threshold)
        progress = live.meta.progress
        if req.stage == 2:
            progress.batches_done = 0
        progress.qa_windows_done = []
        progress.qa_fixes = 0
        progress.stage = req.stage
        await self.store.save_blocks(job_id, live.blocks)
        live.meta.bible_approved = True
        await self.save_meta(live)
        return await self.start(job_id, req)

    # -- step execution ------------------------------------------------------
    async def step(self, job_id: str, run_id: str, budget: float | None = None) -> bool:
        """Advance one job. Returns True when more steps are needed. Raises LeaseBusy if another
        step of the same job is running."""
        ctl = await self.store.get_control(job_id)
        if ctl is None or ctl.run_id != run_id or ctl.status not in RUNNING_STATUSES:
            return False  # stale message: the run was paused, restarted, finished or deleted
        owner = uuid.uuid4().hex
        if not await self.store.acquire_lease(job_id, owner, LEASE_TTL):
            raise LeaseBusy(job_id)
        try:
            live = await self.get(job_id)
            live.pause_requested = ctl.pause_requested
            config = live.meta.run_config or RunConfig()
            now = time.monotonic()
            ctx = RunContext(
                self,
                live,
                config,
                deadline=now + budget if budget else None,
                hard_deadline=now + max(STEP_HARD_LIMIT, budget + MIN_CALL_WINDOW) if budget else None,
            )
            self._active[job_id] = live
            watcher = asyncio.create_task(self._watch(live, owner, run_id))
            try:
                return await self._advance(live, ctx)
            finally:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
                if self._active.get(job_id) is live:
                    self._active.pop(job_id, None)
                with contextlib.suppress(Exception):
                    await ctx.flush_logs()
        finally:
            with contextlib.suppress(Exception):
                await self.store.release_lease(job_id, owner)

    async def _watch(self, live: LiveJob, owner: str, run_id: str) -> None:
        """Renew the lease and pick up pause / delete / restart requests made by other instances."""
        while True:
            await asyncio.sleep(self.watch_interval)
            try:
                ctl = await self.store.get_control(live.meta.id)
                if ctl is None or ctl.run_id != run_id:
                    live.superseded = True
                    live.pause_requested = True
                    return
                if ctl.pause_requested:
                    live.pause_requested = True
                await self.store.acquire_lease(live.meta.id, owner, LEASE_TTL)
            except Exception:  # a transient DB error must not kill the step
                log.exception("[job %s] lease watcher error", live.meta.id)

    async def _pause(self, live: LiveJob, ctx: RunContext, message: str) -> bool:
        if live.superseded:
            return False
        ctx.log(message)
        await self.store.set_run(live.meta.id, None)
        await self._set_status(live, "paused")
        return False

    async def _advance(self, live: LiveJob, ctx: RunContext) -> bool:
        meta, config = live.meta, ctx.config
        try:
            if live.pause_requested:
                return await self._pause(live, ctx, "Paused. Resume continues where it stopped.")
            if meta.bible is None:
                meta.progress.stage = 1
                await self._set_status(live, "bible_running")
                meta.bible = await run_stage1(ctx)
                meta.bible_approved = not config.pause_after_bible
                await ctx.save_meta()
            if not meta.bible_approved:
                if config.pause_after_bible:
                    ctx.log("Translation Bible ready — review and edit it, then approve to start Stage 2.")
                    await self.store.set_run(meta.id, None)
                    await self._set_status(live, "awaiting_review")
                    return False
                meta.bible_approved = True

            if meta.progress.stage <= 2:
                if meta.status != "translating":
                    await self._set_status(live, "translating")
                await run_stage2(ctx)
                if live.pause_requested:
                    return await self._pause(live, ctx, "Paused during Stage 2. Resume continues with the remaining batches.")
                # Lines never attempted (not merely flagged MISSING after a retry) mean the step ran out of time.
                if any(needs_translation(b) and FLAG_MISSING not in b.flags for b in live.blocks):
                    await ctx.save_meta()
                    return True
                meta.progress.stage = 3
                if ctx.out_of_time:
                    await ctx.save_meta()
                    return True

            if not config.qa_enabled:
                await self.store.set_run(meta.id, None)
                meta.progress.stage = 4
                meta.progress.finished_at = time.time()
                ctx.log(f"Pipeline complete — QA critic skipped (off in Settings), total cost ${meta.usage.total_cost_usd:.4f}")
                await self._set_status(live, "completed")
                return False

            if meta.status != "qa_running":
                await self._set_status(live, "qa_running")
            errors = await run_stage3(ctx)
            if live.pause_requested:
                return await self._pause(live, ctx, "Paused during Stage 3. Resume continues with the remaining QA windows.")
            if len(set(meta.progress.qa_windows_done)) < meta.progress.qa_windows_total and ctx.out_of_time:
                await ctx.save_meta()
                return True
            await self.store.set_run(meta.id, None)
            if errors:
                await self._set_status(
                    live, "failed", f"{len(errors)} QA window(s) failed ({errors[0]}). Resume to retry them."
                )
                return False

            meta.progress.stage = 4
            meta.progress.finished_at = time.time()
            ctx.log(f"Pipeline complete — {meta.progress.qa_fixes} QA fix(es), total cost ${meta.usage.total_cost_usd:.4f}")
            await self._set_status(live, "completed")
            return False
        except asyncio.CancelledError:
            meta.status = "interrupted"
            try:
                await self.store.set_run(meta.id, None)
                await self.save_meta(live)
            except Exception:  # store may already be closing during shutdown
                log.exception("Could not persist interrupted state for job %s", meta.id)
            raise
        except LLMError as exc:
            if exc.retryable and ctx.out_of_time:
                ctx.log(f"{exc} — retrying in the next step", "warning")
                await ctx.save_meta()
                return True
            ctx.log(str(exc), "error")
            await self.store.set_run(meta.id, None)
            await self._set_status(live, "failed", str(exc))
            return False
        except Exception as exc:  # surface unexpected errors to the UI instead of a dead run
            log.exception("Job %s crashed", meta.id)
            ctx.log(f"Unexpected error: {exc}", "error")
            await self.store.set_run(meta.id, None)
            await self._set_status(live, "failed", f"Unexpected error: {exc}")
            return False

    # -- Bible / block edits -------------------------------------------------
    async def update_bible(self, job_id: str, update: BibleUpdate) -> LiveJob:
        live = await self.get(job_id)
        if await self._running(job_id):
            raise JobConflict("Pause the job before editing the Translation Bible.")
        live.meta.bible = update.bible
        if update.approve:
            live.meta.bible_approved = True
        await self.save_meta(live)
        return live

    async def _load_block(self, job_id: str, block_id: str) -> tuple[JobMeta, Block]:
        meta = await self.store.get_meta(job_id)
        block = await self.store.get_block(job_id, block_id) if meta else None
        if meta is None or block is None:
            raise JobNotFound(f"{job_id}/{block_id}")
        return meta, block

    @staticmethod
    def _threshold(meta: JobMeta) -> float:
        return meta.run_config.cps_threshold if meta.run_config else RunConfig().cps_threshold

    async def edit_block(self, job_id: str, block_id: str, text: str) -> Block:
        meta, block = await self._load_block(job_id, block_id)
        block.final_translation = text.replace("\r\n", "\n").strip()
        block.user_edited = True
        refresh_metrics(block, self._threshold(meta))
        await self.store.save_blocks(job_id, [block])
        return block

    async def revert_block(self, job_id: str, block_id: str) -> Block:
        meta, block = await self._load_block(job_id, block_id)
        block.final_translation = block.draft_translation
        block.qa_fixed = False
        block.qa_reason = ""
        block.user_edited = False
        refresh_metrics(block, self._threshold(meta))
        await self.store.save_blocks(job_id, [block])
        return block

    # -- misc ----------------------------------------------------------------
    async def delete(self, job_id: str) -> None:
        if await self.store.get_meta(job_id) is None:
            raise JobNotFound(job_id)
        await self.store.set_run(job_id, None)
        await self.runner.cancel(job_id)
        await self.store.delete_job(job_id)

    async def export(self, job_id: str) -> tuple[str, str, int]:
        live = await self.get(job_id)
        content = build_srt(live.blocks, live.meta.line_ending)
        stem = Path(live.meta.filename).stem or "subtitles"
        filename = f"{stem}.{_slug(live.meta.target_language)}.srt"
        untranslated = sum(1 for b in live.blocks if b.source_text.strip() and not b.current_text.strip())
        return filename, content, untranslated

    async def estimate(self, job_id: str, req: RunRequest) -> dict[str, Any]:
        live = await self.get(job_id)
        language = (req.target_language or live.meta.target_language or "").strip()
        key_status = getattr(self.factory, "key_status", None)
        result = estimate_job(live.blocks, live.meta.bible, req.config, language, key_status() if key_status else None)
        result["spent_usd"] = live.meta.usage.total_cost_usd
        return result
