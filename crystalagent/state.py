"""Structured game state, read entirely through named symbols.

Party/enemy struct offsets are derived from the labels the disassembly
gives every field (wPartyMon1HP - wPartyMon1, etc.) -- no magic numbers.
"""
from . import paths
from .asmconst import parse_const_defs, parse_defs
from .schemas import validate_game_state

MON_NAME_LENGTH = 11

# bit order per constants/ram_constants.asm: MINERAL is bit 4, STORM bit 5
# (display order on the trainer card differs -- don't "fix" this back)
JOHTO_BADGES = ["ZEPHYR", "HIVE", "PLAIN", "FOG", "MINERAL", "STORM", "GLACIER", "RISING"]
KANTO_BADGES = ["BOULDER", "CASCADE", "THUNDER", "RAINBOW", "SOUL", "MARSH", "VOLCANO", "EARTH"]

# Status bits and the sleep-counter mask come from the game's own
# constants (constants/battle_constants.asm:162): SLP is the low 3 bits
# as a turn counter, then PSN/BRN/FRZ/PAR at bits 3-6. Everything that
# reasons about status -- game_state, battle_frame, the mid-battle cure
# in tactics.recommend -- reads the same numbers as the engine.
_BATTLE_CONSTANTS = paths.REPO_ROOT / "constants" / "battle_constants.asm"
_STATUS_BITS = [(1 << parse_const_defs(_BATTLE_CONSTANTS)[n], n)
                for n in ("PSN", "BRN", "FRZ", "PAR")]
SLP_MASK = parse_defs(_BATTLE_CONSTANTS)["SLP_MASK"]
EGG = 0xFD  # wPartySpecies entry for an egg (the mon struct holds the real species)


def _dvs(b):
    """Two packed DV bytes -> [atk, def, spd, spc]."""
    return [b[0] >> 4, b[0] & 0xF, b[1] >> 4, b[1] & 0xF]


def _shiny(dv):
    """engine/gfx/color.asm CheckShininess: atk DV has bit 1 set, and
    def/spd/spc DVs are all 10."""
    return bool(dv[0] & 0b0010) and dv[1] == 10 and dv[2] == 10 and dv[3] == 10


def _unown_letter(b):
    """engine/gfx/load_pics.asm GetUnownLetter: middle two bits of each DV,
    concatenated atk|def|spd|spc, divided by 10 -> 'a'..'z'."""
    v = ((b[0] & 0x60) << 1) | ((b[0] & 0x06) << 3) | ((b[1] & 0x60) >> 3) | ((b[1] & 0x06) >> 1)
    return chr(ord("a") + v // 10)


def _status(byte):
    out = [name for bit, name in _STATUS_BITS if byte & bit]
    if byte & SLP_MASK:
        out.append(f"SLP:{byte & SLP_MASK}")
    return out


def _badges(byte, names):
    return [n for i, n in enumerate(names) if byte & (1 << i)]


# -- live overworld sprites (wObjectStructs) ---------------------------------
# Struct layout per constants/map_object_constants.asm (OBJECT_* offsets),
# cross-checked against the sym's field labels: wObject1Struct -
# wObjectStructs == 0x28 (OBJECT_LENGTH), wPlayerMovementType == +3,
# wPlayerMapX/Y == +0x10/+0x11. OBJECT_MAP_X/Y are the standing-tile map
# coords (walk cell + 4); mid-step they already hold the tile being stepped
# INTO -- the tile that collides. wMapObjects is the STATIC map definitions
# and never moves (journaled confusion: pushed boulders "reset" in the npcs
# list); live positions live here.
OBJECT_LENGTH = 0x28
NUM_OBJECT_STRUCTS = 13          # player slot 0 + 12 NPC slots
_OBJ_SPRITE = 0x00               # OBJECT_SPRITE; 0 = empty slot
_OBJ_MOVEMENT_TYPE = 0x03        # OBJECT_MOVEMENT_TYPE (SPRITEMOVEDATA_*)
_OBJ_MAP_X = 0x10                # OBJECT_MAP_X
_OBJ_MAP_Y = 0x11                # OBJECT_MAP_Y

# SPRITEMOVEDATA_* movement types whose owner vacates its tile on its own
# (wander/spin/pace): worth WAITING for. Everything else stands still until
# scripted -- waiting on those is pure frame burn.
SPRITE_WANDERERS = frozenset((
    0x02,   # SPRITEMOVEDATA_WANDER
    0x03,   # SPRITEMOVEDATA_SPINRANDOM_SLOW
    0x04,   # SPRITEMOVEDATA_WALK_UP_DOWN
    0x05,   # SPRITEMOVEDATA_WALK_LEFT_RIGHT
    0x0A,   # SPRITEMOVEDATA_SPINRANDOM_FAST
    0x1E,   # SPRITEMOVEDATA_SPINCOUNTERCLOCKWISE
    0x1F,   # SPRITEMOVEDATA_SPINCLOCKWISE
    0x24,   # SPRITEMOVEDATA_SWIM_WANDER
))


def decode_object_structs(buf):
    """wObjectStructs bytes -> [{slot, map_x, map_y, movement}] for every
    LIVE slot (OBJECT_SPRITE != 0), player included as slot 0. Coords are
    walk-cell coords (the struct stores cell + 4)."""
    sprites = []
    for slot in range(NUM_OBJECT_STRUCTS):
        b = buf[slot * OBJECT_LENGTH:(slot + 1) * OBJECT_LENGTH]
        if len(b) < OBJECT_LENGTH or not b[_OBJ_SPRITE]:
            continue
        sprites.append({
            "slot": slot,
            "map_x": b[_OBJ_MAP_X] - 4,
            "map_y": b[_OBJ_MAP_Y] - 4,
            "movement": b[_OBJ_MOVEMENT_TYPE],
        })
    return sprites


def live_sprites(emu):
    """Live sprite positions read straight from wObjectStructs."""
    return decode_object_structs(
        emu.read("wObjectStructs", NUM_OBJECT_STRUCTS * OBJECT_LENGTH))


def game_state(emu, names, include_screen=False):
    sym = emu.sym
    s = {}

    s["frame"] = emu.frame
    hours = emu.read_be("wGameTimeHours", 2)
    s["play_time"] = "%d:%02d:%02d" % (hours, emu.read_u8("wGameTimeMinutes"),
                                       emu.read_u8("wGameTimeSeconds"))

    group, num = emu.read_u8("wMapGroup"), emu.read_u8("wMapNumber")
    s["location"] = {
        "map_group": group,
        "map_number": num,
        "map": names.maps.get((group, num), "?"),
        "x": emu.read_u8("wXCoord"),
        "y": emu.read_u8("wYCoord"),
    }

    s["player"] = {
        "name": emu.read_text("wPlayerName", MON_NAME_LENGTH),
        "rival": emu.read_text("wRivalName", MON_NAME_LENGTH),
        "money": emu.read_be("wMoney", 3),
        "johto_badges": _badges(emu.read_u8("wJohtoBadges"), JOHTO_BADGES),
        "kanto_badges": _badges(emu.read_u8("wKantoBadges"), KANTO_BADGES),
    }

    stride = sym.offset("wPartyMon2", "wPartyMon1")
    off = lambda f: sym.offset("wPartyMon1" + f, "wPartyMon1")
    party_bank, party_base = sym["wPartyMon1"]
    nick_bank, nick_base = sym["wPartyMonNicknames"]
    count = min(emu.read_u8("wPartyCount"), 6)
    slots = emu.read("wPartySpecies", count) if count else b""
    party = []
    for i in range(count):
        base = party_base + i * stride
        rd = lambda f, n=1: emu.read((party_bank, base + off(f)), n)
        species = rd("Species")[0]
        dvb = rd("DVs", 2)
        party.append({
            "species": species,
            "name": names.species.get(species, "?"),
            "egg": slots[i] == EGG,
            "dvs": _dvs(dvb),
            "shiny": _shiny(_dvs(dvb)),
            "form": _unown_letter(dvb) if species == 201 else None,
            "nickname": emu.charmap.decode(
                emu.read((nick_bank, nick_base + i * MON_NAME_LENGTH), MON_NAME_LENGTH)),
            "level": rd("Level")[0],
            "hp": int.from_bytes(rd("HP", 2), "big"),
            "max_hp": int.from_bytes(rd("MaxHP", 2), "big"),
            "status": _status(rd("Status")[0]),
            "item": names.items.get(rd("Item")[0]),
            "moves": [
                {"name": names.moves.get(m, "?"), "pp": pp}
                for m, pp in zip(rd("Moves", 4), rd("PP", 4)) if m
            ],
        })
    s["party"] = party

    mode = emu.read_u8("wBattleMode")
    if mode:
        species = emu.read_u8("wEnemyMonSpecies")
        dvb = emu.read("wEnemyMonDVs", 2)
        s["battle"] = {
            "mode": {1: "wild", 2: "trainer"}.get(mode, mode),
            "enemy": {
                "species": species,
                "name": names.species.get(species, "?"),
                "shiny": _shiny(_dvs(dvb)),
                "form": _unown_letter(dvb) if species == 201 else None,
                "level": emu.read_u8("wEnemyMonLevel"),
                "hp": emu.read_be("wEnemyMonHP", 2),
                "max_hp": emu.read_be("wEnemyMonMaxHP", 2),
            },
        }
    else:
        s["battle"] = None

    if include_screen:
        s["screen"] = emu.screen_text()
    return validate_game_state(s, include_screen)


# -- PC boxes ---------------------------------------------------------------
# The box the PC is looking at is the CURRENT box, `sBox` in SRAM bank 1;
# boxes 1-14 are copies in SRAM banks 2-3 that CHANGE BOX swaps in
# (engine/pokemon/bills_pc.asm GetBoxPointer). One banked read therefore
# answers "what is in the box a deposit lands in" without touching the
# screen -- the deposit list's own selection cursor is an OAM sprite, so
# the tilemap cannot be trusted for box contents at all.
MONS_PER_BOX = 20


def box_mons(emu, names):
    """The current PC box's contents, read from SRAM through named
    symbols: ``[{species, name, nickname, level}, ...]`` in box order
    (which is the order the WITHDRAW list paints them in)."""
    sym = emu.sym
    stride = sym.offset("sBoxMon2Species", "sBoxMon1Species")
    level_off = sym.offset("sBoxMon1Level", "sBoxMon1Species")
    mon_bank, mon_base = sym["sBoxMon1Species"]
    nick_bank, nick_base = sym["sBoxMonNicknames"]
    count = min(emu.read_u8("sBoxCount"), MONS_PER_BOX)
    mons = []
    for i in range(count):
        base = mon_base + i * stride
        species = emu.read((mon_bank, base), 1)[0]
        mons.append({
            "species": species,
            "name": names.species.get(species, "?"),
            "nickname": emu.charmap.decode(
                emu.read((nick_bank, nick_base + i * MON_NAME_LENGTH),
                         MON_NAME_LENGTH)),
            "level": emu.read((mon_bank, base + level_off), 1)[0],
        })
    return mons


def box_state(emu, names):
    """``{'box': n, 'count': k, 'capacity': 20, 'mons': [...]}`` for the
    current box. `box` is 1-based, decoded the way the engine does it
    (`wCurBox & $f`, then +1 -- engine/pokemon/bills_pc.asm:_MovePKMNWithoutMail)."""
    mons = box_mons(emu, names)
    return {
        "box": (emu.read_u8("wCurBox") & 0x0F) + 1,
        "count": len(mons),
        "capacity": MONS_PER_BOX,
        "mons": mons,
    }


def status_line(state):
    loc = state["location"]
    line = f"frame={state['frame']} map={loc['map']} pos=({loc['x']},{loc['y']})"
    j, k = state["player"]["johto_badges"], state["player"]["kanto_badges"]
    line += f" badges={len(j)}/8+{len(k)}/8"
    if j or k:
        line += " (" + " ".join(j + k) + ")"
    if state["party"]:
        lead = state["party"][0]
        line += f" lead={lead['name']} L{lead['level']} {lead['hp']}/{lead['max_hp']}"
    if state["battle"]:
        e = state["battle"]["enemy"]
        line += f" BATTLE[{state['battle']['mode']}] vs {e['name']} L{e['level']} {e['hp']}/{e['max_hp']}"
    return line
