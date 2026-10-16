from subtrans.models import Block
from subtrans.srt_parser import build_srt, decode_srt_bytes, parse_srt, parse_srt_bytes

from .conftest import make_srt


def test_roundtrip_is_byte_identical_lf_and_crlf():
    for ending in ("\n", "\r\n"):
        text = make_srt(25, line_ending=ending)
        p = parse_srt(text)
        assert p.line_ending == ending
        assert len(p.blocks) == 25
        assert build_srt(p.blocks, p.line_ending) == text


def test_timing_lines_are_preserved_verbatim_including_position_cues():
    text = "7\n00:01:02.5 --> 00:01:04,250  X1:100 X2:200\nHi\n"
    b = parse_srt(text).blocks[0]
    assert b.timestamp == "00:01:02.5 --> 00:01:04,250  X1:100 X2:200"
    assert b.index == 7
    assert b.start_sec == 62.5 and b.end_sec == 64.25
    assert abs(b.duration_sec - 1.75) < 1e-9
    b.final_translation = "Hola"
    assert build_srt([b]) == "7\n00:01:02.5 --> 00:01:04,250  X1:100 X2:200\nHola\n"


def test_bom_multiline_missing_index_and_blank_lines():
    raw = (
        "﻿1\n00:00:01,000 --> 00:00:02,000\n<i>First line</i>\nsecond line\n\n\n\n"
        "00:00:03,000 --> 00:00:04,000\nNo index here\n\n"
        "3\n00:00:05,000 --> 00:00:06,000\nA\n\nB\n"
    )
    p = parse_srt(raw)
    assert [b.id for b in p.blocks] == ["1", "2", "3"]
    assert p.blocks[0].source_text == "<i>First line</i>\nsecond line"
    assert p.blocks[1].index == 2  # missing index -> position
    assert p.blocks[2].source_text == "A\nB"  # blank line inside a block is dropped
    assert any("no index" in w for w in p.warnings)


def test_decoding_utf8_bom_cp1252_and_utf16():
    srt = "1\n00:00:01,000 --> 00:00:02,000\nCafé – déjà vu\n"
    assert decode_srt_bytes(("﻿" + srt).encode("utf-8"))[1] == "utf-8-sig"
    assert parse_srt_bytes(srt.encode("cp1252")).blocks[0].source_text == "Café – déjà vu"
    assert parse_srt_bytes(srt.encode("utf-16")).blocks[0].source_text == "Café – déjà vu"


def test_export_falls_back_final_then_draft_then_source():
    base = dict(index=1, timestamp="00:00:01,000 --> 00:00:02,000", start_sec=1, end_sec=2, duration_sec=1)
    b1 = Block(id="1", source_text="src", draft_translation="draft", final_translation="final", **base)
    b2 = Block(id="2", source_text="src", draft_translation="draft", **base)
    b3 = Block(id="3", source_text="src", **base)
    out = build_srt([b1, b2, b3])
    assert out.split("\n\n") == [
        "1\n00:00:01,000 --> 00:00:02,000\nfinal",
        "1\n00:00:01,000 --> 00:00:02,000\ndraft",
        "1\n00:00:01,000 --> 00:00:02,000\nsrc\n",
    ]


def test_utf8_output_for_non_latin_text():
    p = parse_srt("1\n00:00:01,000 --> 00:00:02,000\nHello\n")
    p.blocks[0].final_translation = "नमस्ते दुनिया"
    assert build_srt(p.blocks).encode("utf-8").decode("utf-8").endswith("नमस्ते दुनिया\n")
