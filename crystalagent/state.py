"""Structured game state, read entirely through named symbols.

Party/enemy struct offsets are derived from the labels the disassembly
gives every field (wPartyMon1HP - wPartyMon1, etc.) -- no magic numbers.
"""

MON_NAME_LENGTH = 11

JOHTO_BADGES = ["ZEPHYR", "HIVE", "PLAIN", "FOG", "STORM", "MINERAL", "GLACIER", "RISING"]
KANTO_BADGES = ["BOULDER", "CASCADE", "THUNDER", "RAINBOW", "SOUL", "MARSH", "VOLCANO", "EARTH"]

_STATUS_BITS = [(0x08, "PSN"), (0x10, "BRN"), (0x20, "FRZ"), (0x40, "PAR")]


def _status(byte):
    out = [name for bit, name in _STATUS_BITS if byte & bit]
    if byte & 0x07:
        out.append(f"SLP:{byte & 0x07}")
    return out


def _badges(byte, names):
    return [n for i, n in enumerate(names) if byte & (1 << i)]


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
    party = []
    for i in range(count):
        base = party_base + i * stride
        rd = lambda f, n=1: emu.read((party_bank, base + off(f)), n)
        species = rd("Species")[0]
        party.append({
            "species": species,
            "name": names.species.get(species, "?"),
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
        s["battle"] = {
            "mode": {1: "wild", 2: "trainer"}.get(mode, mode),
            "enemy": {
                "species": species,
                "name": names.species.get(species, "?"),
                "level": emu.read_u8("wEnemyMonLevel"),
                "hp": emu.read_be("wEnemyMonHP", 2),
                "max_hp": emu.read_be("wEnemyMonMaxHP", 2),
            },
        }
    else:
        s["battle"] = None

    if include_screen:
        s["screen"] = emu.screen_text()
    return s


def status_line(state):
    loc = state["location"]
    line = f"frame={state['frame']} map={loc['map']} pos=({loc['x']},{loc['y']})"
    if state["party"]:
        lead = state["party"][0]
        line += f" lead={lead['name']} L{lead['level']} {lead['hp']}/{lead['max_hp']}"
    if state["battle"]:
        e = state["battle"]["enemy"]
        line += f" BATTLE[{state['battle']['mode']}] vs {e['name']} L{e['level']} {e['hp']}/{e['max_hp']}"
    return line
