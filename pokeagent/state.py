"""Structured game state: the Sapphire analog of ``crystalagent/state.py``.

Everything here is addressed through the symbol table and laid out by offsets
parsed from the decomp's headers, so no address or field offset is written
down in this file.

Two Gen-3 specifics worth knowing before reading:

* **The live party is not the saved party.** ``gPlayerParty`` (IWRAM) is what
  the game plays with; ``gSaveBlock1.playerParty`` is a mirror synced only at
  save/load (src/load_save.c:64-82). Reading the save block gives you stale
  data after any battle.
* **Player coordinates carry no border offset.** ``gSaveBlock1.pos`` is a
  plain map-local tile; the famous ``+7`` applies only when indexing the
  padded runtime grid (src/overworld.c:749). Map JSON event coordinates are in
  the same unpadded space, so they compare directly.
"""

from dataclasses import dataclass, field

from . import cconst, cstruct, pokemon

#: include/task.h:4
NUM_TASKS = 16

#: include/fieldmap.h:19 -- pad applied to the runtime grid, NOT to pos.
MAP_OFFSET = 7

#: src/event_data.c:113-120 -- var ids below this are not vars at all.
VARS_START = 0x4000
SPECIAL_VARS_START = 0x8000
#: src/event_data.c:145-154 -- flag ids at/above this live in the temp buffer.
TEMP_FLAGS_START = 0x4000

#: include/global.h -- struct ItemSlot is {u16 itemId; u16 quantity}.
ITEM_SLOT_SIZE = 4

#: Bag pockets, in the order the bag UI shows them.
POCKETS = (
    ("items", "bagPocket_Items"),
    ("key_items", "bagPocket_KeyItems"),
    ("poke_balls", "bagPocket_PokeBalls"),
    ("tms_hms", "bagPocket_TMHM"),
    ("berries", "bagPocket_Berries"),
)

#: include/constants/battle.h:47-62
BATTLE_TYPE = {
    "double": 0x0001,
    "link": 0x0002,
    "wild": 0x0004,
    "trainer": 0x0008,
    "first_battle": 0x0010,
    "multi": 0x0040,
    "safari": 0x0080,
    "battle_tower": 0x0100,
    "roamer": 0x0400,
    "legendary": 0x2000,
}

#: enum Direction, include/global.fieldmap.h
FACING = {0: "none", 1: "D", 2: "U", 3: "L", 4: "R"}


@dataclass(slots=True)
class Location:
    map_group: int
    map_num: int
    map_name: str
    x: int
    y: int

    def __str__(self):
        return f"{self.map_name} ({self.x},{self.y})"


@dataclass(slots=True)
class Battle:
    active: bool
    type_flags: int = 0
    kinds: tuple = ()
    battler_count: int = 0
    outcome: int = 0
    mons: list = field(default_factory=list)

    @property
    def wild(self):
        return self.active and "trainer" not in self.kinds

    @property
    def trainer(self):
        return "trainer" in self.kinds


class GameState:
    """Reads the live game. Construct once per Driver; call :meth:`snapshot`
    as often as you like -- every read goes straight to emulator memory."""

    def __init__(self, emu, names, consts):
        self.emu = emu
        self.names = names
        self.consts = consts
        self.sb1 = cstruct.layout("SaveBlock1")
        self.sb2 = cstruct.layout("SaveBlock2")
        self.mon = cstruct.layout("Pokemon", "pokemon.h")
        self.battle_mon = cstruct.layout("BattlePokemon", "pokemon.h")
        self.object_event = cstruct.layout("ObjectEvent", "global.fieldmap.h")
        self._mon_size = pokemon.MON_SIZE
        self._battle_mon_size = self._derive_battle_mon_size()

    def _derive_battle_mon_size(self):
        size = self.emu.sym.size("gBattleMons")
        if size:
            # include/constants/battle.h:26 -- MAX_BATTLERS_COUNT is 4.
            return size // 4
        return 0x58

    # ---- addressing --------------------------------------------------

    def _sb1(self, field_name, extra=0):
        return self.emu.resolve("gSaveBlock1") + self.sb1[field_name] + extra

    def _sb2(self, field_name, extra=0):
        return self.emu.resolve("gSaveBlock2") + self.sb2[field_name] + extra

    # ---- player ------------------------------------------------------

    def location(self) -> Location:
        raw = self.emu.read(self._sb1("pos"), 6)
        x = int.from_bytes(raw[0:2], "little", signed=True)
        y = int.from_bytes(raw[2:4], "little", signed=True)
        group, num = raw[4], raw[5]
        return Location(group, num, self.names.map_name(group, num), x, y)

    def facing(self) -> str:
        """Player facing, from object event 0. SaveBlock1 does not carry it."""
        base = self.emu.resolve("gObjectEvents")
        byte = self.emu.u8(base + self.object_event["facingDirection"])
        return FACING.get(byte & 0xF, "?")

    def player_name(self) -> str:
        return self.emu.charmap.decode(self.emu.read(self._sb2("playerName"), 8))

    def money(self) -> int:
        return self.emu.u32(self._sb1("money"))

    def coins(self) -> int:
        return self.emu.u16(self._sb1("coins"))

    def play_time(self) -> str:
        h = self.emu.u16(self._sb2("playTimeHours"))
        m = self.emu.u8(self._sb2("playTimeMinutes"))
        s = self.emu.u8(self._sb2("playTimeSeconds"))
        return f"{h}:{m:02}:{s:02}"

    def trainer_id(self) -> int:
        return self.emu.u16(self._sb2("playerTrainerId"))

    def gender(self) -> str:
        return "female" if self.emu.u8(self._sb2("playerGender")) else "male"

    # ---- flags and vars ----------------------------------------------

    def flag(self, flag) -> bool:
        """Read an event flag by ``FLAG_*`` name or numeric id.

        Mirrors ``FlagGet`` (src/event_data.c:171-181), including the split
        between the save block's array and the temp buffer at 0x4000.
        """
        fid = self.consts.flags[flag] if isinstance(flag, str) else flag
        if fid == 0:
            return False
        if fid < TEMP_FLAGS_START:
            addr = self._sb1("flags", fid // 8)
        else:
            addr = self.emu.resolve("gUnknown_0202E8E2") + (fid - TEMP_FLAGS_START) // 8
        return bool(self.emu.u8(addr) >> (fid & 7) & 1)

    # ---- engine introspection ------------------------------------------

    def callback_name(self) -> str:
        """Which engine callback owns the screen right now.

        ``gMain.callback2`` is a function pointer, and the symbol table turns
        it into a name -- so "am I on the title screen / in the overworld /
        on the naming keyboard" is an exact question. The Crystal harness had
        to answer the same question by pattern-matching decoded screen text,
        which is still an open bug there (its journal #6: `keyboard_open()`
        is `"DEL" in screen and "END" in screen`).
        """
        main = cstruct.layout("Main", "main.h")
        ptr = self.emu.u32(self.emu.resolve("gMain") + main["callback2"]) & ~1
        sym = self.emu.sym.at(ptr)
        return sym.name if sym else f"{ptr:#010x}"

    def task_data(self, task_name) -> list[int] | None:
        """``gTasks[i].data[0..15]`` for a running task, by name.

        Cutscene and picker state lives in these s16 slots -- the starter
        chooser keeps its selection in ``data[0]``
        (src/starter_choose.c:255), so a picker can be driven to an exact
        index instead of by counting presses.
        """
        task = cstruct.layout("Task", "task.h")
        base = self.emu.resolve("gTasks")
        size = self.emu.sym.size("gTasks") // NUM_TASKS or 0x28
        for i in range(NUM_TASKS):
            raw = self.emu.read(base + i * size, size)
            if not raw[task["isActive"]]:
                continue
            ptr = int.from_bytes(raw[task["func"] : task["func"] + 4], "little") & ~1
            sym = self.emu.sym.at(ptr)
            if sym and sym.name == task_name:
                off = task["data"]
                return [
                    int.from_bytes(raw[off + n * 2 : off + n * 2 + 2], "little", signed=True)
                    for n in range(16)
                ]
        return None

    def tasks(self) -> list[str]:
        """Names of the active entries in ``gTasks``.

        Cutscenes and menus are task-driven, so this says what the engine is
        actually doing (``Task_NewGameSpeech12``, ``Task_MainMenuDraw``) when
        the callback alone is too coarse.
        """
        task = cstruct.layout("Task", "task.h")
        base = self.emu.resolve("gTasks")
        size = self.emu.sym.size("gTasks") // NUM_TASKS or 0x28
        out = []
        for i in range(NUM_TASKS):
            raw = self.emu.read(base + i * size, size)
            if not raw[task["isActive"]]:
                continue
            ptr = int.from_bytes(raw[task["func"] : task["func"] + 4], "little") & ~1
            sym = self.emu.sym.at(ptr)
            out.append(sym.name if sym else f"{ptr:#010x}")
        return out

    def var(self, name_or_id) -> int:
        """Read a script var. ``VarGet`` returns the id itself for anything
        outside the var range (src/event_data.c:122-128) -- that is what makes
        ``compare VAR, 5`` work on literals, so we reproduce it."""
        vid = self.consts.vars[name_or_id] if isinstance(name_or_id, str) else name_or_id
        if vid < VARS_START or vid >= SPECIAL_VARS_START:
            return vid
        return self.emu.u16(self._sb1("vars", (vid - VARS_START) * 2))

    def badges(self) -> list[str]:
        return [
            f"BADGE{i:02}"
            for i in range(1, 9)
            if self.flag(f"FLAG_BADGE{i:02}_GET")
        ]

    # ---- party and bag -----------------------------------------------

    def party_count(self) -> int:
        return self.emu.u8("gPlayerPartyCount")

    def _read_party(self, symbol, count):
        base = self.emu.resolve(symbol)
        out = []
        for i in range(count):
            raw = self.emu.read(base + i * self._mon_size, self._mon_size)
            mon = pokemon.parse_mon(raw)
            if mon is None:
                continue
            mon.nickname = self.emu.charmap.decode(
                raw[self.mon["box"] + 0x08 : self.mon["box"] + 0x08 + pokemon.NICKNAME_LEN]
            )
            mon.ot_name = self.emu.charmap.decode(
                raw[self.mon["box"] + 0x14 : self.mon["box"] + 0x14 + pokemon.OT_NAME_LEN]
            )
            out.append(mon)
        return out

    def party(self):
        return self._read_party("gPlayerParty", self.party_count())

    def enemy_party(self):
        """The foe's party, with the count DERIVED rather than read.

        `gEnemyPartyCount` is only ever written by `CalculateEnemyPartyCount`
        (pret/src/pokemon_2.c:1025-1030) and the WILD encounter path never
        calls it, so in a wild battle the variable holds whatever the last
        trainer battle left -- frequently 0. This method therefore returned
        `[]` for every wild mon in the game, silently: a catch policy that
        asked "what am I facing?" got nothing, fell through to attacking, and
        killed a NINCADA that was new to the dex.

        So count the way the engine's own function counts -- scan slots until
        the first empty species -- instead of trusting a cache the wild path
        does not maintain. Six is `PARTY_SIZE`.
        """
        count = 0
        base = self.emu.resolve("gEnemyParty")
        while count < 6:
            raw = self.emu.read(base + count * self._mon_size, self._mon_size)
            mon = pokemon.parse_mon(raw)
            if mon is None or not mon.species:
                break
            count += 1
        return self._read_party("gEnemyParty", count)

    def bag(self) -> dict:
        """``{pocket: {item_name: quantity}}``, stopping at the first empty
        slot the way the bag UI does."""
        out = {}
        for label, field_name in POCKETS:
            base = self._sb1(field_name)
            size = self.sb1_pocket_size(field_name)
            pocket = {}
            raw = self.emu.read(base, size * ITEM_SLOT_SIZE)
            for i in range(size):
                item = int.from_bytes(raw[i * 4 : i * 4 + 2], "little")
                qty = int.from_bytes(raw[i * 4 + 2 : i * 4 + 4], "little")
                if item == 0:
                    continue
                pocket[self.names.item(item)] = qty
            out[label] = pocket
        return out

    def sb1_pocket_size(self, field_name) -> int:
        """Slots in a bag pocket, derived from the gap to the next field."""
        offsets = sorted(self.sb1.values())
        start = self.sb1[field_name]
        nxt = next(o for o in offsets if o > start)
        return (nxt - start) // ITEM_SLOT_SIZE

    # ---- battle -------------------------------------------------------

    def in_battle(self) -> bool:
        """``gMain.inBattle`` is bit 1 of the byte holding the bitfield at
        include/main.h:41-43. Corroborated by gBattleTypeFlags, because the
        bit lingers for a few frames around transitions."""
        main = cstruct.layout("Main", "main.h")
        byte = self.emu.u8(self.emu.resolve("gMain") + main["oamLoadDisabled"])
        return bool(byte >> 1 & 1) and self.emu.u16("gBattleTypeFlags") != 0

    def in_safari(self) -> bool:
        """Is a Safari Zone visit in progress?

        The counters below are only meaningful during one, and `EnterSafariMode`
        sets them together with the flag (pret/src/safari_zone.c:57-64).
        """
        return self.safari_steps() > 0 or self.safari_balls() > 0

    def safari_balls(self) -> int:
        """`gNumSafariBalls` -- EWRAM, 30 on entry, 0 on exit.

        NOT the bag. `Catcher.balls_available()` reads the ball POCKET, which
        inside the zone is the wrong pool entirely: the reserve guard was
        measuring Poke Balls the game will not let you throw while the thirty
        balls that actually exist went uncounted
        (pret/src/safari_zone.c:28,62).
        """
        try:
            return self.emu.u8("gNumSafariBalls")
        except Exception:  # noqa: BLE001 - absent on a non-Sapphire build
            return 0

    def safari_steps(self) -> int:
        """`gSafariZoneStepCounter` -- 500 on entry, and the run is ejected at 0.

        Reading it is the difference between retiring on purpose and being
        thrown out mid-sweep (pret/src/safari_zone.c:29,63,74-86).
        """
        try:
            return self.emu.u16("gSafariZoneStepCounter")
        except Exception:  # noqa: BLE001
            return 0

    def battle_ready(self) -> bool:
        """True once the battle's mon blocks are actually readable.

        ``in_battle()`` goes true at the start of the transition animation,
        roughly 60 frames before ``gBattleMons`` is populated -- read too
        early and every species, level and HP is zero. This is the Gen-3
        shape of Crystal's gotcha 4: the battle HUD being on screen does not
        mean the battle is interactive.
        """
        if not self.in_battle():
            return False
        base = self.emu.resolve("gBattleMons")
        b = self.battle_mon
        # IN THE SAFARI ZONE THE PLAYER HAS NO BATTLE MON, BY DESIGN. The
        # engine memsets the player-side `gBattleMons` entry to zero on every
        # controller pass (pret/src/battle_main.c:3711-3715):
        #
        #     if ((gBattleTypeFlags & BATTLE_TYPE_SAFARI)
        #      && GetBattlerSide(gActiveBattler) == 0)
        #         MEMSET_ALT(&gBattleMons[gActiveBattler], 0, 0x58, i, ptr);
        #
        # so demanding a species from every battler could NEVER come true
        # there. That is why a Safari encounter logged "no battle frame
        # (battle_ready never came true)" and the catch decision was never
        # consulted at all -- 16 species in one venue, behind a readiness check
        # waiting for a mon the engine deliberately erases. In singles the
        # player is battler 0 and the foe is battler 1, and side is index
        # parity, so on the Safari only the ODD battlers are expected to exist.
        safari = bool(self.emu.u16("gBattleTypeFlags") & BATTLE_TYPE["safari"])
        for i in range(max(1, self.emu.u8("gBattlersCount"))):
            if safari and i % 2 == 0:
                continue
            raw = self.emu.read(base + i * self._battle_mon_size, self._battle_mon_size)
            species = int.from_bytes(raw[b["species"] : b["species"] + 2], "little")
            if species == 0 or raw[b["level"]] == 0:
                return False
        # NON-ZERO IS NOT FRESH. `gBattleMons` is not cleared between battles,
        # so the foe's slot still holds the LAST battle's mon through the whole
        # intro -- and it passes the species/level test above perfectly. Two
        # separate hunts read the wrong species off the first frame after this
        # returned True: a cast that produced a FEEBAS was logged as a WINGULL
        # KO'd two encounters earlier, and a hooked WAILMER read as "ZIGZAGOON
        # L3". That is not merely a bad log line: `battle_policy` is asked ONCE
        # per wild, so a decision made off a stale frame can FLEE a species
        # that is new to the dex.
        #
        # For a WILD battle the encounter generator has already written the
        # real mon to gEnemyParty[0] before the intro copies it into
        # gBattleMons, so the two disagreeing means the copy has not happened
        # yet. Scoped to wild battles on purpose: in a TRAINER battle
        # gEnemyParty[0] is only the lead, and the active foe legitimately
        # differs after a switch.
        flags = self.emu.u16("gBattleTypeFlags")
        if (flags & BATTLE_TYPE["wild"]) and not (flags & BATTLE_TYPE["trainer"]):
            foe = self.emu.read(base + self._battle_mon_size, self._battle_mon_size)
            live = int.from_bytes(foe[b["species"] : b["species"] + 2], "little")
            try:
                party = self._read_party("gEnemyParty", 1)
            except Exception:                       # noqa: BLE001
                return True                         # cannot cross-check; trust it
            if party and party[0].species and party[0].species != live:
                return False
        return True

    def battle(self) -> Battle:
        if not self.in_battle():
            return Battle(active=False)
        flags = self.emu.u16("gBattleTypeFlags")
        kinds = tuple(k for k, bit in BATTLE_TYPE.items() if flags & bit)
        count = self.emu.u8("gBattlersCount")
        mons = []
        base = self.emu.resolve("gBattleMons")
        for i in range(min(count, 4)):
            raw = self.emu.read(base + i * self._battle_mon_size, self._battle_mon_size)
            mons.append(self._parse_battle_mon(raw))
        return Battle(
            active=True,
            type_flags=flags,
            kinds=kinds,
            battler_count=count,
            outcome=self.emu.u8("gBattleOutcome"),
            mons=mons,
        )

    def _parse_battle_mon(self, raw) -> dict:
        """``gBattleMons`` is plaintext -- no substructure crypto in battle."""
        b = self.battle_mon
        u16 = lambda off: int.from_bytes(raw[off : off + 2], "little")
        species = u16(b["species"])
        stages = list(raw[b["statStages"] : b["statStages"] + 8])
        return {
            "species": species,
            "name": self.names.species(species) if species else "-",
            "nickname": self.emu.charmap.decode(raw[b["nickname"] : b["nickname"] + 11]),
            "level": raw[b["level"]],
            "hp": u16(b["hp"]),
            "max_hp": u16(b["maxHP"]),
            "moves": [u16(b["moves"] + i * 2) for i in range(4)],
            "pp": list(raw[b["pp"] : b["pp"] + 4]),
            "types": (raw[b["type1"]], raw[b["type2"]]),
            "ability": raw[b["ability"]],
            "item": u16(b["item"]),
            "stat_stages": stages,
            "status": int.from_bytes(raw[b["status1"] : b["status1"] + 4], "little"),
            "stats": {
                "attack": u16(b["attack"]),
                "defense": u16(b["defense"]),
                "speed": u16(b["speed"]),
                "sp_attack": u16(b["spAttack"]),
                "sp_defense": u16(b["spDefense"]),
            },
        }

    # ---- text ----------------------------------------------------------

    def message(self) -> str:
        """The expanded message the game is currently showing.

        Sapphire has no flat text layer to decode the way Crystal's tilemap
        did; the engine builds strings into these buffers and the window code
        renders them. ``gStringVar4`` is the post-placeholder-expansion one.
        """
        buf = "gDisplayedStringBattle" if self.in_battle() else "gStringVar4"
        return self.emu.charmap.decode(self.emu.read(buf, 200)).strip()

    # ---- the snapshot --------------------------------------------------

    def snapshot(self, include_party=True) -> dict:
        loc = self.location()
        battle = self.battle()
        snap = {
            "frame": self.emu.frame,
            "location": {
                "map": loc.map_name,
                "group": loc.map_group,
                "num": loc.map_num,
                "x": loc.x,
                "y": loc.y,
                "facing": self.facing(),
            },
            "player": {
                "name": self.player_name(),
                "gender": self.gender(),
                "trainer_id": self.trainer_id(),
                "money": self.money(),
                "coins": self.coins(),
                "play_time": self.play_time(),
                "badges": self.badges(),
            },
            "ui": {"battle": battle.active, "message": self.message()},
        }
        if battle.active:
            snap["battle"] = {
                "kinds": list(battle.kinds),
                "battlers": battle.battler_count,
                "mons": battle.mons,
            }
        if include_party:
            snap["party"] = [
                {
                    "nickname": m.nickname,
                    "species": self.names.species(m.species) if m.species else "EGG",
                    "level": m.level,
                    "hp": m.hp,
                    "max_hp": m.max_hp,
                    "status": m.status_name,
                    "egg": m.is_egg,
                    "shiny": m.shiny,
                    "nature": m.nature,
                    "moves": [
                        {"name": self.names.move(mv), "pp": pp}
                        for mv, pp in zip(m.moves, m.pp)
                        if mv
                    ],
                }
                for m in self.party()
            ]
            snap["bag"] = self.bag()
        return snap

    def status_line(self) -> str:
        loc = self.location()
        party = self.party()
        lead = next((m for m in party if not m.is_egg), None)
        bits = [f"frame={self.emu.frame}", f"map={loc.map_name}", f"pos=({loc.x},{loc.y})"]
        if lead:
            bits.append(
                f"lead={lead.nickname or self.names.species(lead.species)}"
                f" L{lead.level} {lead.hp}/{lead.max_hp}"
            )
        bits.append(f"money={self.money()}")
        badges = self.badges()
        bits.append(f"badges={len(badges)}/8")
        if self.in_battle():
            bits.append("BATTLE")
        return " ".join(bits)


#: `gSaveBlock1.gameStats` offset (include/global.h:703) and the indices worth
#: surfacing (include/constants/game_stat.h). The game keeps these itself, so
#: they are the honest numbers: they count what the PLAYER did, they survive a
#: restart, and they do not drift when the harness grows a new way to move.
GAME_STATS_OFFSET = 0x1540
GAME_STATS = {
    "steps": 5,
    "battles": 7,
    "wild_battles": 8,
    "trainer_battles": 9,
    "captures": 11,
    "fishing_captures": 12,
    "hatched_eggs": 13,
    "evolutions": 14,
    "pokecenter_visits": 15,
    "saves": 0,
}


def game_stats(emu) -> dict:
    """The cartridge's own tally.

    The harness used to count its own steps and reported 0 across a run that
    had walked fifty thousand, because the counter only incremented on the
    grass-grinding path and every goto/travel step missed it. Rather than fix
    a parallel tally in three more places, read the one the game already
    maintains.
    """
    import struct

    base = emu.resolve("gSaveBlock1") + GAME_STATS_OFFSET
    top = max(GAME_STATS.values()) + 1
    raw = bytes(emu.read(base, 4 * top))
    values = struct.unpack(f"<{top}I", raw)
    return {name: values[index] for name, index in GAME_STATS.items()}
