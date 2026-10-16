"""Process-wide store + job manager, shared by the API app and the Vercel queue worker."""

from __future__ import annotations

import asyncio

from .config import get_settings
from .llm.factory import ClientFactory
from .pipeline.orchestrator import JobManager, LocalRunner, QueueRunner
from .store import JobStore

_managers: dict[int, JobManager] = {}


def build_manager(store: JobStore) -> JobManager:
    runner = QueueRunner() if get_settings().runner_mode == "queue" else LocalRunner()
    return JobManager(store, ClientFactory(), runner)


async def get_manager() -> JobManager:
    """Connect once per event loop. A warm serverless instance reuses its pool across invocations;
    a runtime that starts a fresh loop gets a fresh pool (pools are bound to their loop)."""
    loop_id = id(asyncio.get_running_loop())
    manager = _managers.get(loop_id)
    if manager is None:
        store = JobStore(get_settings().database_target)
        await store.init()
        manager = _managers.setdefault(loop_id, build_manager(store))
    return manager
