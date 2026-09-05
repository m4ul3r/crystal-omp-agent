"""wOptions text-delay bit packing for Driver.set_text_speed."""
import pytest

from crystalagent.driver import Driver

pytestmark = pytest.mark.unit


def test_fast_preserves_upper_option_bits():
    # scroll/noise/other options live above the 3-bit TEXT_DELAY field
    assert Driver._text_speed_byte(0b11010101, "FAST") == 0b11010001


def test_modes_map_to_delay_values():
    assert Driver._text_speed_byte(0b11111111, "FAST") & 0b111 == 0b001
    assert Driver._text_speed_byte(0b11111000, "MED") & 0b111 == 0b011
    assert Driver._text_speed_byte(0b11111000, "SLOW") & 0b111 == 0b101


def test_unknown_mode_raises():
    with pytest.raises(KeyError):
        Driver._text_speed_byte(0, "INSTANT")
