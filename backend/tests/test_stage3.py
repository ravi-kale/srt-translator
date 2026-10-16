from subtrans.models import Block, RerunRequest, RunConfig, RunRequest
from subtrans.pipeline.stage3_qa import normalize_fixes
from subtrans.text_metrics import compute_cps, compute_flags

from .conftest import parsed, wait_done


def _block(src: str, trg: str, dur: float = 2.0) -> Block:
    return Block(
        id="1", index=1, timestamp="t", start_sec=0, end_sec=dur, duration_sec=dur,
        source_text=src, draft_translation=trg, final_translation=trg,
    )


def test_cps_ignores_tags_and_line_breaks():
    assert compute_cps("<i>abcd</i>\nefgh", 2.0) == 4.0
    assert compute_cps("", 2.0) == 0.0


def test_deterministic_flags():
    assert compute_flags(_block("Hello", "x" * 50, 2.0), 20) == ["HIGH_CPS"]
    assert compute_flags(_block("Hello", ""), 20) == ["EMPTY"]
    assert compute_flags(_block("Hello there", " hello  THERE "), 20) == ["IDENTICAL"]
    assert compute_flags(_block("♪♪", "♪♪"), 20) == []  # no letters -> not a leak
    assert compute_flags(_block("", ""), 20) == []


def test_normalize_fixes_accepts_both_shapes():
    spec_shape = {"fixes": {"#12": {"corrected_text": "Hola\\nmundo", "reason": "r"}}}
    array_shape = {"fixes": [{"id": "13", "corrected_text": "Adiós", "reason": "r2"}, {"id": "14", "corrected_text": ""}]}
    assert normalize_fixes(spec_shape) == {"12": ("Hola\nmundo", "r")}
    assert normalize_fixes(array_shape) == {"13": ("Adiós", "r2")}


async def test_critic_patch_is_sparse_and_keeps_draft_for_diff(manager, factory):
    factory.clients[3].fix_ids = {"3", "130"}
    live = await manager.create("ep.srt", parsed(150), "Spanish", "")
    await manager.start(live.meta.id, RunRequest(config=RunConfig(qa_window=120)))
    live = await wait_done(manager, live.meta.id)
    calls = factory.clients[3].calls
    assert [len(c.ids) for c in calls] == [120, 30]
    fixed = [b for b in live.blocks if b.qa_fixed]
    assert {b.id for b in fixed} == {"3", "130"}
    b = live.by_id["3"]
    assert b.draft_translation == "[ES] Line number 3."
    assert b.final_translation == "[ES] Line number 3. (fixed)"
    assert b.qa_reason == "Fixed #3"
    assert live.meta.progress.qa_fixes == 2
    assert live.meta.usage.stage3.calls == 2


async def test_refusal_keeps_drafts_and_completes(manager, factory):
    factory.clients[3].refuse = True
    live = await manager.create("ep.srt", parsed(30), "Spanish", "")
    await manager.start(live.meta.id, RunRequest())
    live = await wait_done(manager, live.meta.id)
    assert live.meta.status == "completed"
    assert not any(b.qa_fixed for b in live.blocks)


async def test_rerun_stage3_resets_fixes(manager, factory):
    factory.clients[3].fix_ids = {"2"}
    live = await manager.create("ep.srt", parsed(30), "Spanish", "")
    await manager.start(live.meta.id, RunRequest())
    live = await wait_done(manager, live.meta.id)
    assert live.by_id["2"].qa_fixed
    factory.clients[3].fix_ids = {"4"}
    await manager.rerun(live.meta.id, RerunRequest(stage=3))
    live = await wait_done(manager, live.meta.id)
    assert not live.by_id["2"].qa_fixed and live.by_id["2"].final_translation == live.by_id["2"].draft_translation
    assert live.by_id["4"].qa_fixed
    assert len(factory.clients[2].calls) == 1  # Stage 2 not re-run


async def test_revert_to_draft(manager, factory):
    factory.clients[3].fix_ids = {"2"}
    live = await manager.create("ep.srt", parsed(10), "Spanish", "")
    await manager.start(live.meta.id, RunRequest())
    live = await wait_done(manager, live.meta.id)
    b = await manager.revert_block(live.meta.id, "2")
    assert b.final_translation == b.draft_translation and not b.qa_fixed and b.qa_reason == ""
