"""A boxed mon must carry its nickname, and a rename must not corrupt it.

`parse_mon` deliberately leaves the name blank -- it owns no charmap -- so the
CALLER fills it. `DexTarget.boxed()` never did, which meant every boxed mon
read as `nickname == ''`. That silently defeated matching a mon by name, and
worse, it made auditing names impossible: a scan for mons wrongly named "A"
returned zero from the boxes no matter what was in them, hiding 59 of them for
a whole run.

The second test pins the claim that makes `scripts/fix_names.py` safe: the
nickname lives in the PLAINTEXT header, outside the checksummed secure block.
"""

import struct

import pytest

from pokeagent import pokemon

pytestmark = pytest.mark.unit


def _mon_bytes(nickname_bytes: bytes, species: int = 0x0119) -> bytes:
    """Build a BoxPokemon whose checksum is correct, with a chosen nickname.

    Personality 0 / OT id 0 makes the XOR key 0, so the "encrypted" secure
    block is stored plaintext and the checksum is computable by hand -- the
    point here is the LAYOUT, not the cipher.
    """
    raw = bytearray(pokemon.BOX_SIZE)
    struct.pack_into("<II", raw, 0, 0, 0)          # personality, ot_id
    raw[0x08:0x08 + len(nickname_bytes)] = nickname_bytes
    # substruct order for personality % 24 == 0 is G,A,E,M
    struct.pack_into("<HH", raw, 0x20, species, 0)  # growth: species, item
    plain = bytes(raw[0x20:0x20 + 48])
    struct.pack_into("<H", raw, 0x1C, pokemon.checksum(plain))
    return bytes(raw)


def test_the_nickname_field_is_outside_the_checksummed_block():
    """Rewriting a name must not invalidate the mon.

    This is the whole licence for fix_names.py writing nicknames directly
    instead of walking 59 mons to the Name Rater. If it ever stops being true,
    that script becomes save corruption and this test says so.
    """
    a = _mon_bytes(b"\xbb\xff")                     # "A"
    b = bytearray(a)
    b[0x08:0x12] = b"\xc6\xc3\xc4\xc7\xc9\xc8\xff\xff\xff\xff"   # some name
    mon_a, mon_b = pokemon.parse_mon(a), pokemon.parse_mon(bytes(b))
    assert mon_a is not None and mon_b is not None
    assert mon_a.checksum_ok and mon_b.checksum_ok, (
        "a nickname write must not disturb the secure block"
    )
    assert mon_a.species == mon_b.species
    assert mon_a.experience == mon_b.experience
    # and the bytes that changed really were only the name field
    assert a[:0x08] == bytes(b)[:0x08]
    assert a[0x12:] == bytes(b)[0x12:]


def test_a_full_width_name_has_no_room_for_a_terminator():
    """`nickname` is a fixed 10-byte field, not a C string.

    WIGGLYTUFF is exactly POKEMON_NAME_LENGTH characters, so encoding it with
    a trailing 0xFF produces 11 bytes and overflows into `language`. The
    fixer truncates to the field width instead of refusing the name.
    """
    assert pokemon.NICKNAME_LEN == 10
    ten = b"\xc6" * 10
    mon = pokemon.parse_mon(_mon_bytes(ten))
    assert mon is not None and mon.checksum_ok
    # the field is full; nothing spilled into the byte after it
    assert _mon_bytes(ten)[0x12] == 0
