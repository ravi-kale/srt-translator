"""Vercel Queues subscriber: each message advances one translation job by one step."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

import vercel.queue as vq  # noqa: E402

from subtrans.pipeline.orchestrator import QUEUE_TOPIC  # noqa: E402
from subtrans.runtime import get_manager  # noqa: E402
from subtrans.worker import handle_step  # noqa: E402


@vq.subscribe(topic=QUEUE_TOPIC, retry_after=20)
async def run_job_step(message: vq.Message[dict[str, str]]) -> None:
    await handle_step(await get_manager(), message.payload)
