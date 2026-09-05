"""Gen-3 Pokemon structures: the encrypted, shuffled substructures.

This has no Crystal analog at all. In Gen 2 a party mon was a flat struct you
could just read. In Gen 3 the 48 interesting bytes are XOR-encrypted and the
four 12-byte substructures are permuted by the mon's personality value, so
"read the species" is a real algorithm:

1. ``key = personality ^ otId``; XOR it into each of the twelve little-endian
   u32 words at ``+0x20`` (src/pokemon_2.c:179-197). XOR is involutive, so
   encrypt and decrypt are the same pass.
2. The four decrypted 12-byte slots hold Growth / Attacks / EVs / Misc in an
   order given by ``personality % 24`` (src/pokemon_2.c:217-276).
3. A u16 sum of all 24 decrypted halfwords must equal the plaintext checksum
   at ``+0x1C``, or the game itself treats the mon as a bad egg
   (src/pokemon_1.c:1669-1692, src/pokemon_2.c:315-329).

Everything before ``+0x20`` -- personality, OT id, nickname, egg bits -- and
everything after the box data in a party mon -- level, HP, computed stats --
is plaintext, so HP and level cost no crypto at all.

We only ever decrypt into a scratch copy. Writing decrypted bytes back into
emulator memory would corrupt the save.
"""

import struct
from dataclasses import dataclass, field

BOX_SIZE = 0x50
MON_SIZE = 0x64
PARTY_SIZE = 6
NICKNAME_LEN = 10
OT_NAME_LEN = 7

#: slot index holding substruct type G/A/E/M, indexed by personality % 24.
#: Transcribed from the SUBSTRUCT_CASE table at src/pokemon_2.c:247-274.
SUBSTRUCT_ORDER = (
    (0, 1, 2, 3), (0, 1, 3, 2), (0, 2, 1, 3), (0, 3, 1, 2),
    (0, 2, 3, 1), (0, 3, 2, 1), (1, 0, 2, 3), (1, 0, 3, 2),
    (2, 0, 1, 3), (3, 0, 1, 2), (2, 0, 3, 1), (3, 0, 2, 1),
    (1, 2, 0, 3), (1, 3, 0, 2), (2, 1, 0, 3), (3, 1, 0, 2),
    (2, 3, 0, 1), (3, 2, 0, 1), (1, 2, 3, 0), (1, 3, 2, 0),
    (2, 1, 3, 0), (3, 1, 2, 0), (2, 3, 1, 0), (3, 2, 1, 0),
)

#: include/pokemon.h:26-50 -- nature is personality % 25.
NATURES = (
    "HARDY", "LONELY", "BRAVE", "ADAMANT", "NAUGHTY",
    "BOLD", "DOCILE", "RELAXED", "IMPISH", "LAX",
    "TIMID", "HASTY", "SERIOUS", "JOLLY", "NAIVE",
    "MODEST", "MILD", "QUIET", "BASHFUL", "RASH",
    "CALM", "GENTLE", "SASSY", "CAREFUL", "QUIRKY",
)

#: include/constants/battle.h -- the non-volatile status bits in Pokemon.status.
STATUS_BITS = (
    (0x07, "SLP"),  # low 3 bits are the remaining sleep turns
    (0x08, "PSN"),
    (0x10, "BRN"),
    (0x20, "FRZ"),
    (0x40, "PAR"),
    (0x80, "TOX"),
)


def decrypt_secure(raw: bytes) -> bytes:
    """The 48 plaintext bytes of a BoxPokemon's secure block."""
    personality, ot_id = struct.unpack_from("<II", raw, 0)
    key = personality ^ ot_id
    words = struct.unpack_from("<12I", raw, 0x20)
    return struct.pack("<12I", *(w ^ key for w in words))


def checksum(plain: bytes) -> int:
    """u16 sum of the 24 decrypted halfwords (src/pokemon_1.c:1669-1692)."""
    return sum(struct.unpack("<24H", plain)) & 0xFFFF


def status_name(status: int) -> str | None:
    if not status:
        return None
    for mask, name in STATUS_BITS:
        if status & mask:
            return name
    return None


@dataclass(slots=True)
class Mon:
    """One party or box Pokemon, fully decoded."""

    personality: int
    ot_id: int
    nickname: str
    ot_name: str
    language: int
    is_bad_egg: bool
    is_egg: bool
    checksum_ok: bool

    species: int = 0
    held_item: int = 0
    experience: int = 0
    friendship: int = 0
    pp_bonuses: int = 0
    moves: tuple = ()
    pp: tuple = ()
    evs: dict = field(default_factory=dict)
    ivs: dict = field(default_factory=dict)
    met_level: int = 0
    met_location: int = 0
    pokeball: int = 0
    alt_ability: int = 0
    pokerus: int = 0

    # Party-only tail; absent (None) for a box mon.
    status: int | None = None
    level: int | None = None
    hp: int | None = None
    max_hp: int | None = None
    stats: dict = field(default_factory=dict)

    @property
    def nature(self):
        return NATURES[self.personality % 25]

    @property
    def shiny(self):
        # (otId_hi ^ otId_lo ^ pid_hi ^ pid_lo) < 8
        p, o = self.personality, self.ot_id
        return (
            (o >> 16) ^ (o & 0xFFFF) ^ (p >> 16) ^ (p & 0xFFFF)
        ) < 8

    @property
    def status_name(self):
        return status_name(self.status or 0)

    @property
    def fainted(self):
        # An egg reads 0 HP and is NOT a fainted mon -- Crystal's train() rail
        # looped forever on exactly this (its journal #20).
        return not self.is_egg and self.hp == 0

    @property
    def gender_value(self):
        return self.personality & 0xFF


def parse_mon(raw: bytes) -> Mon | None:
    """Decode a ``struct Pokemon`` (100 bytes) or ``struct BoxPokemon`` (80).

    Returns None for an empty slot (species 0 after decryption).
    """
    personality, ot_id = struct.unpack_from("<II", raw, 0)
    if personality == 0 and ot_id == 0 and not any(raw[:BOX_SIZE]):
        return None

    flags = raw[0x13]
    plain = decrypt_secure(raw)
    stored_checksum = struct.unpack_from("<H", raw, 0x1C)[0]
    ok = checksum(plain) == stored_checksum

    g, a, e, m = SUBSTRUCT_ORDER[personality % 24]
    growth = plain[g * 12 : g * 12 + 12]
    attacks = plain[a * 12 : a * 12 + 12]
    evblock = plain[e * 12 : e * 12 + 12]
    misc = plain[m * 12 : m * 12 + 12]

    species, held_item = struct.unpack_from("<HH", growth, 0)
    experience = struct.unpack_from("<I", growth, 4)[0]
    pp_bonuses, friendship = growth[8], growth[9]

    moves = struct.unpack_from("<4H", attacks, 0)
    pp = tuple(attacks[8:12])

    ev_names = ("hp", "attack", "defense", "speed", "sp_attack", "sp_defense")
    evs = dict(zip(ev_names, evblock[0:6]))

    iv32 = struct.unpack_from("<I", misc, 4)[0]
    ivs = {
        "hp": iv32 & 0x1F,
        "attack": (iv32 >> 5) & 0x1F,
        "defense": (iv32 >> 10) & 0x1F,
        "speed": (iv32 >> 15) & 0x1F,
        "sp_attack": (iv32 >> 20) & 0x1F,
        "sp_defense": (iv32 >> 25) & 0x1F,
    }
    egg_from_misc = bool((iv32 >> 30) & 1)
    met = struct.unpack_from("<H", misc, 2)[0]

    mon = Mon(
        personality=personality,
        ot_id=ot_id,
        nickname="",  # filled by the caller, which owns the charmap
        ot_name="",
        language=raw[0x12],
        is_bad_egg=bool(flags & 1),
        is_egg=bool(flags & 4) or egg_from_misc,
        checksum_ok=ok,
        species=species if ok else 0,
        held_item=held_item,
        experience=experience,
        friendship=friendship,
        pp_bonuses=pp_bonuses,
        moves=tuple(mv for mv in moves),
        pp=pp,
        evs=evs,
        ivs=ivs,
        met_level=met & 0x7F,
        met_location=misc[1],
        pokeball=(met >> 11) & 0xF,
        alt_ability=(iv32 >> 31) & 1,
        pokerus=misc[0],
    )

    if len(raw) >= MON_SIZE:
        (status, level, _mail, hp, max_hp, atk, dfn, spe, spa, spd) = struct.unpack_from(
            "<IBBHHHHHHH", raw, 0x50
        )
        mon.status = status
        mon.level = level
        mon.hp = hp
        mon.max_hp = max_hp
        mon.stats = {
            "attack": atk,
            "defense": dfn,
            "speed": spe,
            "sp_attack": spa,
            "sp_defense": spd,
        }
    return mon
