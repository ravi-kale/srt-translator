"""The Vercel step chain, end to end through the SDK's in-process queue service."""

import asyncio

import pytest
import vercel.queue as vq
from vercel.queue.embedded import embedded_queue_service

from subtrans.models import RunConfig, RunRequest
from subtrans.pipeline.orchestrator import QUEUE_TOPIC, JobManager, QueueRunner
from subtrans.worker import handle_step

from .conftest import FakeFactory, parsed

_current: dict[str, JobManager] = {}


@vq.subscribe(topic=QUEUE_TOPIC, consumer_group="subtrans-test")
async def _test_step(message: vq.Message[dict[str, str]]) -> None:
    await handle_step(_current["manager"], message.payload, budget=0.05)


async def test_queue_runner_chains_steps_until_complete(store):
    factory = FakeFactory()
    factory.clients[2].delay = 0.02
    async with embedded_queue_service() as service:
        manager = JobManager(store, factory, QueueRunner(service.get_async_client()), watch_interval=0.02)  # type: ignore[arg-type]
        _current["manager"] = manager
        live = await manager.create("ep.srt", parsed(120), "Spanish", "")
        await manager.start(live.meta.id, RunRequest(config=RunConfig(lanes=1, target_size=10, max_size=12)))
        for _ in range(600):
            meta = await store.get_meta(live.meta.id)
            if meta.status not in {"bible_running", "translating", "qa_running"}:
                break
            await asyncio.sleep(0.05)
    live = await manager.get(live.meta.id)
    assert live.meta.status == "completed", live.meta.error
    assert all(b.final_translation for b in live.blocks)
    ids = [i for c in factory.clients[2].calls for i in c.ids]
    assert sorted(int(i) for i in ids) == list(range(1, 121))
    logs = await store.get_logs(live.meta.id)
    assert any(e.message.startswith("Pipeline complete") for e in logs)
