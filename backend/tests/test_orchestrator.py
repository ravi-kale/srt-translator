import asyncio

from subtrans.models import Bible, BibleUpdate, GlossaryEntry, RerunRequest, RunConfig, RunRequest
from subtrans.pipeline.orchestrator import JobConflict, JobManager, LeaseBusy
from subtrans.store import JobStore

from .conftest import FakeFactory, parsed, wait_done


async def test_pause_after_bible_review_edit_then_continue(manager, factory):
    live = await manager.create("ep.srt", parsed(40), "Spanish", "")
    await manager.start(live.meta.id, RunRequest(config=RunConfig(pause_after_bible=True)))
    live = await wait_done(manager, live.meta.id)
    assert live.meta.status == "awaiting_review"
    assert live.meta.bible is not None and not live.meta.bible_approved
    assert factory.clients[2].calls == []

    edited = Bible(
        synopsis_and_tone="Edited tone",
        glossary=[GlossaryEntry(source_term="Widget", target_term="Artilugio", notes="")],
    )
    await manager.update_bible(live.meta.id, BibleUpdate(bible=edited, approve=True))
    await manager.start(live.meta.id, RunRequest(config=RunConfig(pause_after_bible=True)))
    live = await wait_done(manager, live.meta.id)
    assert live.meta.status == "completed"
    assert len(factory.clients[1].calls) == 1  # Stage 1 not repeated
    assert '"Widget" -> "Artilugio"' in factory.clients[2].calls[0].system
    assert "Edited tone" in factory.clients[3].calls[0].system


async def _translated(mgr: JobManager, job_id: str) -> int:
    return sum(1 for b in await mgr.store.get_blocks(job_id) if b.draft_translation)


async def test_interrupted_job_resumes_only_remaining_batches(tmp_path):
    db = tmp_path / "jobs.db"
    store = JobStore(db)
    await store.init()
    factory = FakeFactory()
    factory.clients[2].delay = 0.05
    mgr = JobManager(store, factory, watch_interval=0.02)  # type: ignore[arg-type]
    live = await mgr.create("long.srt", parsed(300), "French", "")
    job_id = live.meta.id
    await mgr.start(job_id, RunRequest(config=RunConfig(lanes=1)))
    while await _translated(mgr, job_id) < 90:
        await asyncio.sleep(0.01)
    await mgr.shutdown()  # simulates the server going down mid-Stage-2
    assert (await store.get_meta(job_id)).status == "interrupted"
    done_before = await _translated(mgr, job_id)
    await store.close()

    # "Restart": fresh store + manager on the same database file.
    store2 = JobStore(db)
    await store2.init()
    factory2 = FakeFactory()
    mgr2 = JobManager(store2, factory2, watch_interval=0.02)  # type: ignore[arg-type]
    await mgr2.startup()
    live2 = await mgr2.get(job_id)
    assert live2.meta.status == "interrupted"
    assert sum(1 for b in live2.blocks if b.draft_translation) == done_before
    await mgr2.start(job_id, RunRequest(config=RunConfig(lanes=1)))
    live2 = await wait_done(mgr2, job_id)
    assert live2.meta.status == "completed"
    translated_again = {i for c in factory2.clients[2].calls for i in c.ids}
    assert len(translated_again) == 300 - done_before
    assert factory2.clients[1].calls == []  # Bible reused from the database
    assert all(b.draft_translation for b in live2.blocks)
    await mgr2.shutdown()
    await store2.close()


async def test_pause_and_resume(manager, factory):
    factory.clients[2].delay = 0.03
    live = await manager.create("ep.srt", parsed(300), "German", "")
    await manager.start(live.meta.id, RunRequest(config=RunConfig(lanes=1)))
    while not await _translated(manager, live.meta.id):
        await asyncio.sleep(0.01)
    await manager.pause(live.meta.id)
    live = await wait_done(manager, live.meta.id)
    assert live.meta.status == "paused"
    assert any(not b.draft_translation for b in live.blocks)
    await manager.start(live.meta.id, RunRequest(config=RunConfig(lanes=1)))
    live = await wait_done(manager, live.meta.id)
    assert live.meta.status == "completed"
    assert all(b.draft_translation for b in live.blocks)


async def test_step_budget_splits_a_run_into_resumable_steps(manager, factory):
    """Queue mode: each step stops starting work once its budget is spent; the next step resumes."""
    factory.clients[2].delay = 0.02
    live = await manager.create("ep.srt", parsed(200), "Spanish", "")
    await manager.store.save_meta(live.meta)
    req = RunRequest(config=RunConfig(lanes=1, target_size=10, max_size=12))
    # Drive steps by hand instead of through the local runner.
    manager.runner.schedule = _noop_schedule  # type: ignore[method-assign]
    await manager.start(live.meta.id, req)
    run_id = (await manager.store.get_control(live.meta.id)).run_id
    steps = 0
    while await manager.step(live.meta.id, run_id, budget=0.05):
        steps += 1
        assert steps < 200
    live = await manager.get(live.meta.id)
    assert steps > 3
    assert live.meta.status == "completed"
    assert all(b.draft_translation == f"[ES] {b.source_text}" for b in live.blocks)
    ids = [i for c in factory.clients[2].calls for i in c.ids]
    assert sorted(int(i) for i in ids) == list(range(1, 201)), "every line translated exactly once"
    assert len(factory.clients[1].calls) == 1


async def test_stale_step_message_is_ignored(manager, factory):
    manager.runner.schedule = _noop_schedule  # type: ignore[method-assign]
    live = await manager.create("ep.srt", parsed(20), "Spanish", "")
    await manager.start(live.meta.id, RunRequest())
    assert await manager.step(live.meta.id, "some-old-run-id") is False
    assert factory.clients[1].calls == []


async def test_concurrent_step_is_rejected_by_lease(manager, factory):
    manager.runner.schedule = _noop_schedule  # type: ignore[method-assign]
    factory.clients[1].delay = 0.2
    live = await manager.create("ep.srt", parsed(20), "Spanish", "")
    await manager.start(live.meta.id, RunRequest())
    run_id = (await manager.store.get_control(live.meta.id)).run_id
    first = asyncio.create_task(manager.step(live.meta.id, run_id))
    await asyncio.sleep(0.05)
    try:
        await manager.step(live.meta.id, run_id)
    except LeaseBusy:
        pass
    else:
        raise AssertionError("expected LeaseBusy")
    assert await first is False
    assert (await manager.get(live.meta.id)).meta.status == "completed"


async def _noop_schedule(job_id: str, run_id: str) -> None:
    return None


async def test_fatal_error_fails_job_with_message(manager, factory):
    factory.clients[2].fatal_error = "deepseek HTTP 401 for model 'deepseek-flash': invalid api key"
    live = await manager.create("ep.srt", parsed(40), "Spanish", "")
    await manager.start(live.meta.id, RunRequest())
    live = await wait_done(manager, live.meta.id)
    assert live.meta.status == "failed"
    assert "401" in (live.meta.error or "")


async def test_target_language_locks_after_translation_starts(manager):
    live = await manager.create("ep.srt", parsed(10), "Spanish", "")
    await manager.start(live.meta.id, RunRequest())
    live = await wait_done(manager, live.meta.id)
    try:
        await manager.start(live.meta.id, RunRequest(target_language="Hindi"))
    except JobConflict as exc:
        assert "locked" in str(exc)
    else:
        raise AssertionError("expected JobConflict")


async def test_usage_and_estimate(manager, factory):
    live = await manager.create("ep.srt", parsed(100), "Hindi", "")
    est = await manager.estimate(live.meta.id, RunRequest())
    assert est["batches"] == 3 and est["qa_windows"] == 1
    assert [s["stage"] for s in est["stages"]] == [1, 2, 3]
    assert est["stages"][1]["cached_tokens"] > 0
    assert est["total_cost_usd"] > 0
    await manager.start(live.meta.id, RunRequest())
    live = await wait_done(manager, live.meta.id)
    assert live.meta.usage.stage2.calls == 3
    assert live.meta.usage.total_cost_usd > 0


async def test_hard_step_limit_cuts_off_a_hung_call_and_resumes_next_step(manager, factory, monkeypatch):
    """A call that never returns (e.g. a provider streaming keep-alives) must not outlive the step."""
    import subtrans.pipeline.orchestrator as orch

    monkeypatch.setattr(orch, "STEP_HARD_LIMIT", 0.3)
    monkeypatch.setattr(orch, "MIN_CALL_WINDOW", 0.05)
    manager.runner.schedule = _noop_schedule  # type: ignore[method-assign]
    live = await manager.create("ep.srt", parsed(30), "Spanish", "")
    factory.clients[1].delay = 5.0  # hangs far past the hard limit
    await manager.start(live.meta.id, RunRequest())
    run_id = (await manager.store.get_control(live.meta.id)).run_id
    started = asyncio.get_running_loop().time()
    assert await manager.step(live.meta.id, run_id, budget=0.1) is True  # cut off, more to do
    assert asyncio.get_running_loop().time() - started < 1.5
    assert (await manager.get(live.meta.id)).meta.status == "bible_running"
    factory.clients[1].delay = 0.0
    while await manager.step(live.meta.id, run_id, budget=5):
        pass
    assert (await manager.get(live.meta.id)).meta.status == "completed"


async def test_qa_critic_can_be_turned_off(manager, factory):
    live = await manager.create("ep.srt", parsed(60), "Spanish", "")
    est = await manager.estimate(live.meta.id, RunRequest(config=RunConfig(qa_enabled=False)))
    assert est["stages"][2]["skipped"] and est["qa_windows"] == 0
    factory.missing = []
    await manager.start(live.meta.id, RunRequest(config=RunConfig(qa_enabled=False)))
    live = await wait_done(manager, live.meta.id)
    assert live.meta.status == "completed" and live.meta.progress.stage == 4
    assert factory.clients[3].calls == []
    assert all(b.final_translation == b.draft_translation != "" for b in live.blocks)
    # The critic can still be run later on demand, which turns it on for that run.
    factory.clients[3].fix_ids = {"2"}
    await manager.rerun(live.meta.id, RerunRequest(stage=3, config=RunConfig(qa_enabled=False)))
    live = await wait_done(manager, live.meta.id)
    assert live.meta.status == "completed" and live.by_id["2"].qa_fixed
    assert len(factory.clients[3].calls) == 1


async def test_qa_off_does_not_require_the_critic_key(manager, factory):
    live = await manager.create("ep.srt", parsed(10), "Spanish", "")
    seen: list[set[int]] = []
    factory.missing_keys = lambda config, stages=None: seen.append(set(stages or ())) or []  # type: ignore[method-assign]
    await manager.start(live.meta.id, RunRequest(config=RunConfig(qa_enabled=False)))
    await wait_done(manager, live.meta.id)
    assert 3 not in seen[0]
