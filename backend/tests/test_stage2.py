from subtrans.models import FLAG_MISSING, RunConfig, RunRequest
from subtrans.pipeline.prompts import build_stage2_user
from subtrans.pipeline.stage2_translate import needs_translation, normalize_translations

from .conftest import blocks_from, make_srt, parsed, wait_done


def test_user_payload_has_context_and_no_timestamps():
    blocks = blocks_from(make_srt(30))
    for b in blocks[:10]:
        b.draft_translation = b.final_translation = f"[ES] {b.source_text}"
    msg = build_stage2_user(blocks, blocks[10:20], window=6)
    assert "-->" not in msg and "00:00:" not in msg
    assert "PREVIOUS CONTEXT (Read-only, already translated)" in msg
    assert "[5] (2.0s) SRC" in msg and "[ES] Line number 10." in msg
    assert "UPCOMING CONTEXT (Read-only)" in msg and "[26] (2.0s)" in msg and "[27]" not in msg
    assert '"duration_sec": 2.0' in msg


def test_lane_seam_context_falls_back_to_source():
    blocks = blocks_from(make_srt(20))
    msg = build_stage2_user(blocks, blocks[10:15], window=6)
    assert "TRG: (not yet translated)" in msg


def test_normalize_translations_shapes():
    assert normalize_translations({"translations": {"#1": " Hola ", "2": {"text": "Adiós"}, "3": ""}}) == {
        "1": "Hola",
        "2": "Adiós",
    }
    assert normalize_translations({"4": "a\\nb"}) == {"4": "a\nb"}


async def test_full_stage2_translates_every_line_with_stable_system_prompt(manager, factory):
    live = await manager.create("ep1.srt", parsed(200), "Spanish", "")
    await manager.start(live.meta.id, RunRequest(config=RunConfig(lanes=4)))
    live = await wait_done(manager, live.meta.id)
    assert live.meta.status == "completed"
    assert all(b.draft_translation == f"[ES] {b.source_text}" for b in live.blocks)
    calls = factory.clients[2].calls
    assert len({c.system for c in calls}) == 1, "system prompt must be byte-identical for prefix caching"
    assert all(len(c.ids) <= 42 for c in calls)
    assert sorted(int(i) for c in calls for i in c.ids) == list(range(1, 201))


async def test_missing_id_triggers_one_retry_in_small_chunks(manager, factory):
    factory.clients[2].drop_ids_once = {"5"}
    live = await manager.create("ep1.srt", parsed(60), "Spanish", "")
    await manager.start(live.meta.id, RunRequest(config=RunConfig(lanes=1)))
    live = await wait_done(manager, live.meta.id)
    calls = factory.clients[2].calls
    first = calls[0]
    assert "5" in first.ids and len(first.ids) == 30
    retries = [c for c in calls[1:] if set(c.ids) <= set(first.ids)]
    assert retries and all(len(c.ids) <= 21 for c in retries)
    assert {i for c in retries for i in c.ids} == set(first.ids)
    assert live.by_id["5"].draft_translation == "[ES] Line number 5."
    assert not any(FLAG_MISSING in b.flags for b in live.blocks)


async def test_persistently_missing_id_is_flagged_and_qa_fills_it(manager, factory):
    factory.clients[2].drop_ids_always = {"7"}
    factory.clients[3].fix_ids = {"7"}
    live = await manager.create("ep1.srt", parsed(40), "Spanish", "")
    await manager.start(live.meta.id, RunRequest(config=RunConfig(lanes=1)))
    live = await wait_done(manager, live.meta.id)
    b = live.by_id["7"]
    assert b.draft_translation == ""
    assert b.final_translation == "relleno" and b.qa_fixed
    assert "MISSING" in factory.clients[3].calls[0].user


async def test_user_edit_during_run_is_never_overwritten(manager, factory):
    factory.clients[2].delay = 0.05
    live = await manager.create("ep1.srt", parsed(90), "Spanish", "")
    await manager.edit_block(live.meta.id, "50", "Mi versión")
    live = await manager.get(live.meta.id)
    assert not needs_translation(live.by_id["50"])
    factory.clients[3].fix_ids = {"50"}
    await manager.start(live.meta.id, RunRequest(config=RunConfig(lanes=2)))
    live = await wait_done(manager, live.meta.id)
    b = live.by_id["50"]
    assert b.final_translation == "Mi versión" and b.user_edited and not b.qa_fixed
    assert "LOCKED" in "".join(c.user for c in factory.clients[3].calls)
