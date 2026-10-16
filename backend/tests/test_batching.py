from subtrans.batching import create_smart_batches, ends_sentence, is_boundary, split_into_lanes

from .conftest import blocks_from, make_srt


def _assert_cuts_at_boundaries(blocks, batches, gap=1.5):
    pos = 0
    for batch in batches[:-1]:
        pos += len(batch)
        assert is_boundary(blocks, pos - 1, gap), f"cut after block {pos} is mid-sentence"


def test_every_line_terminal_gives_target_size_batches():
    blocks = blocks_from(make_srt(100))
    batches = create_smart_batches(blocks, 30, 42)
    assert [len(b) for b in batches] == [30, 30, 40]  # last 40 <= max -> kept together
    assert sum(len(b) for b in batches) == 100


def test_cuts_wait_for_sentence_end_up_to_max():
    blocks = blocks_from(make_srt(120, terminal_every=37))  # periods at 37, 74, 111
    batches = create_smart_batches(blocks, 30, 42)
    assert [len(b) for b in batches] == [37, 37, 37, 9]
    _assert_cuts_at_boundaries(blocks, batches)


def test_backtracks_to_earlier_boundary_instead_of_slicing_sentence():
    # periods only at 20 and 70: no boundary in 30..42 -> backtrack to 20
    text = make_srt(100, terminal_every=1000)
    text = text.replace("Line number 20,", "Line number 20.").replace("Line number 70,", "Line number 70.")
    blocks = blocks_from(text)
    batches = create_smart_batches(blocks, 30, 42)
    assert len(batches[0]) == 20
    assert all(len(b) <= 42 for b in batches)


def test_hard_cut_only_when_no_boundary_exists():
    blocks = blocks_from(make_srt(100, terminal_every=1000))
    batches = create_smart_batches(blocks, 30, 42)
    assert len(batches[0]) == 42


def test_silence_gap_is_a_boundary():
    blocks = blocks_from(make_srt(80, terminal_every=1000, gap_after={33}))
    batches = create_smart_batches(blocks, 30, 42)
    assert len(batches[0]) == 33


def test_ellipsis_continuation_is_not_a_boundary():
    assert not ends_sentence("I was going to...", "...the store")
    assert not ends_sentence("I was going to…", "the store")
    assert ends_sentence("Wait...", "What?")
    assert ends_sentence('He said "go."', None)
    assert ends_sentence("<i>Done!</i>", None)
    assert not ends_sentence("and then", None)


def test_lanes_are_contiguous_and_balanced():
    blocks = blocks_from(make_srt(300))
    batches = create_smart_batches(blocks, 30, 42)
    lanes = split_into_lanes(batches, 4)
    assert len(lanes) == 4
    flat = [b for lane in lanes for batch in lane for b in batch]
    assert [b.id for b in flat] == [b.id for b in blocks]
    sizes = [sum(len(x) for x in lane) for lane in lanes]
    assert max(sizes) - min(sizes) <= 42
    assert split_into_lanes(batches, 1) == [batches]
    assert len(split_into_lanes(batches[:2], 8)) == 2
