"""Pure field decoders in state.py: DVs, shininess, status, badges."""
import pytest

from crystalagent.state import EGG, JOHTO_BADGES, _badges, _dvs, _shiny, _status

pytestmark = pytest.mark.unit


def test_dvs_nibble_order():
    # high nibble first within each byte: atk|def then spd|spc
    assert _dvs(bytes([0xAB, 0xCD])) == [0xA, 0xB, 0xC, 0xD]


def test_shiny_requires_all_four_conditions():
    assert _shiny([0b0010, 10, 10, 10])          # atk bit1 set, rest 10
    assert not _shiny([0b0000, 10, 10, 10])      # atk bit1 clear
    assert not _shiny([0b0010, 9, 10, 10])       # def off by one
    assert not _shiny([0b0010, 10, 11, 10])      # spd off
    assert not _shiny([0b0010, 10, 10, 15])      # spc off


def test_shiny_rejects_near_miss_high_values():
    # 11 != 10 even though it "looks close"; engine demands exactly 10
    assert not _shiny([0b1010, 10, 10, 11])


def test_status_single_bits():
    assert _status(0x08) == ["PSN"]
    assert _status(0x10) == ["BRN"]
    assert _status(0x20) == ["FRZ"]
    assert _status(0x40) == ["PAR"]


def test_status_combined_and_sleep_counter():
    got = _status(0x48)                          # PSN | PAR
    assert got == ["PSN", "PAR"]
    assert _status(0x07) == ["SLP:7"]
    assert _status(0x03) == ["SLP:3"]
    assert _status(0x00) == []


def test_badge_bit_order():
    # MINERAL is bit 4, STORM bit 5 per ram_constants -- do not reorder
    assert _badges(0b0000_0101, JOHTO_BADGES) == ["ZEPHYR", "PLAIN"]
    assert _badges(0b0011_0000, JOHTO_BADGES) == ["MINERAL", "STORM"]
    assert _badges(0, JOHTO_BADGES) == []
    assert _badges(0xFF, JOHTO_BADGES) == JOHTO_BADGES


def test_egg_sentinel():
    assert EGG == 0xFD
