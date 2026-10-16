"""Vercel Queues consumer logic: run one job step, then chain the next one."""

from __future__ import annotations

import logging

from .config import get_settings
from .pipeline.orchestrator import JobManager, LeaseBusy

log = logging.getLogger(__name__)

LEASE_BUSY_RETRY_SEC = 20


async def handle_step(manager: JobManager, payload: dict[str, str], budget: float | None = None) -> None:
    from vercel.queue import RetryAfter

    job_id, run_id = payload["job_id"], payload["run_id"]
    try:
        more = await manager.step(job_id, run_id, budget=budget or min(get_settings().subtrans_step_budget_sec, 170.0))
    except LeaseBusy:
        # Another step of this job is still running (e.g. a redelivery overlapping it): come back later.
        raise RetryAfter(LEASE_BUSY_RETRY_SEC) from None
    if more:
        await manager.runner.schedule(job_id, run_id)
