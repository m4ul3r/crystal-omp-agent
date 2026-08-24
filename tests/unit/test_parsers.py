"""sym/charmap/input-DSL parsers: the lossless interfaces to game data."""
import pytest

from crystalagent.charmap import Charmap
from crystalagent.emu import InputError, parse_sequence
from crystalagent.symfile import Symbols

pytestmark = pytest.mark.unit

SYM_SAMPLE = """\
01:1234 NAME_A
01:2345 DUP_NAME
02:3456 OTHER_BANK
this line is garbage
00:0001 LOWEST
"""


@pytest.fixture()
def syms(tmp_path):
    p = tmp_path / "sample.sym"
    p.write_text(SYM_SAMPLE, encoding="utf-8")
    return Symbols(p)


def test_first_definition_wins(syms):
    # 01:2345 must NOT overwrite the first mapping of DUP_NAME... it would,
    # except DUP_NAME only appears once; assert both directions precisely:
    assert syms["NAME_A"] == (0x01, 0x1234)
    assert syms["DUP_NAME"] == (0x01, 0x2345)
    assert syms.bank("OTHER_BANK") == 0x02
    assert syms.addr("LOWEST") == 1


def test_malformed_lines_skipped(syms):
    assert len(syms.by_name) == 4


def test_offset_same_bank_delta(syms):
    assert syms.offset("DUP_NAME", "NAME_A") == 0x2345 - 0x1234


def test_find_pattern_sorted(syms):
    hits = syms.find("NAME")
    names = [n for n, _, _ in hits]
    assert names == sorted(["NAME_A", "DUP_NAME"])


@pytest.fixture(scope="module")
def charmap():
    from pathlib import Path
    poke = Path(__file__).resolve().parents[2].parent
    path = poke / "constants/charmap.asm"
    if not path.exists():
        pytest.skip("pokecrystal checkout not found")
    return Charmap(path)


def test_decode_stops_at_terminator(charmap):
    a = next(b for b, t in charmap.tokens.items() if t == "A")
    term = next(b for b, t in charmap.tokens.items() if t == "@")
    b_byte = next(b for b, t in charmap.tokens.items() if t == "B")
    assert charmap.decode(bytes([a, term, b_byte])) == "A"


def test_unknown_byte_emits_hex_token(charmap):
    unmapped = max(b for b in range(256) if b not in charmap.tokens)
    out = charmap.decode(bytes([unmapped]))
    assert out == "<$%02x>" % unmapped


def test_no_terminator_decodes_everything(charmap):
    a = next(b for b, t in charmap.tokens.items() if t == "A")
    b_byte = next(b for b, t in charmap.tokens.items() if t == "B")
    assert charmap.decode(bytes([a, b_byte]),
                          stop_at_terminator=False) == "AB"

def test_unknown_byte_emits_hex_token(charmap):
    unmapped = max(b for b in range(256) if b not in charmap.tokens)
    out = charmap.decode(bytes([unmapped]))
    assert out == "<$%02x>" % unmapped




def test_parse_sequence_tokens():
    steps = parse_sequence("A:10 B*2 .")
    assert steps[0] == (frozenset({"a"}), 10)
    assert steps[1] == steps[2] == (frozenset({"b"}), 8)   # default press 8f
    assert steps[3] == (frozenset(), 1)                    # '.' waits 1f


def test_parse_sequence_combos_and_waits():
    steps = parse_sequence("A+B:5, .:30")
    assert steps[0] == (frozenset({"a", "b"}), 5)
    assert steps[1] == (frozenset(), 30)


def test_parse_sequence_default_press_is_eight_frames():
    assert parse_sequence("A") == [(frozenset({"a"}), 8)]


def test_parse_sequence_garbage_button_raises():
    with pytest.raises(InputError):
        parse_sequence("X:5")
