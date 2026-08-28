"""claude-wren pt5c battle fixes: (1) stable identity in the `me`/`enemy`
dicts -- 'nickname' (wPartyMonNicknames via wCurBattleMon) + 'party_slot'
alongside the species 'name', so policies survive mid-grind EVOLUTION
(the live TOGEPI->TOGETIC break left PEBBLE Struggling to death 5 cycles);
(2) benign level-up pages ('grew to level N!' text / the stat sheet) are
PROGRESS for the wedge detector -- paged with a plain A, never diagnosed --
while genuine freezes still return 'wedged' with capped diagnostics."""
import logging
from types import SimpleNamespace

import pytest

from crystalagent.battle import Battle

pytestmark = pytest.mark.unit


# -- WRAM-level fakes: the real me()/enemy() run against these ---------------

def _sym_table():
    """Arbitrary but self-consistent addresses; me()/enemy() only ever use
    sym[label] and sym.offset(field, base)."""
    return FakeSym({
        "wBattleMon":         (0, 0x100),
        "wBattleMonSpecies":  (0, 0x100),
        "wBattleMonMoves":    (0, 0x102),
        "wBattleMonPP":       (0, 0x106),
        "wBattleMonLevel":    (0, 0x10A),
        "wBattleMonHP":       (0, 0x10B),
        "wBattleMonMaxHP":    (0, 0x10D),
        "wBattleMonType":     (0, 0x10F),
        "wEnemyMon":          (0, 0x200),
        "wEnemyMonSpecies":   (0, 0x200),
        "wEnemyMonLevel":     (0, 0x20A),
        "wEnemyMonHP":        (0, 0x20B),
        "wEnemyMonMaxHP":     (0, 0x20D),
        "wEnemyMonType":      (0, 0x20F),
        "wCurBattleMon":      (0, 0x300),
        "wCurOTMon":          (0, 0x301),
        "wEnemyMonNickname":  (0, 0x310),
        "wPartyMonNicknames": (1, 0x400),
    })


class FakeSym(dict):
    def offset(self, a, b):
        return self[a][1] - self[b][1]


class FakeCharmap:
    """Gen-2-style stand-in for the game charmap: letters live at
    ord(c) + 0x80 and $50 terminates like '@'. (A plain-ASCII fake is a
    trap: 'P' IS 0x50, so 'PEBBLE' would decode empty.)"""

    def decode(self, data, stop_at_terminator=True):
        out = []
        for b in data:
            if stop_at_terminator and b == 0x50:
                break
            out.append(chr(b - 0x80) if b >= 0x80 else "?")
        return "".join(out)


class FakeEmu:
    def __init__(self, sym, rows=()):
        self.sym = sym
        self.charmap = FakeCharmap()
        self.mem = {}
        self.frame = 0
        self.rows = list(rows)

    def _resolve(self, x):
        return x if isinstance(x, tuple) else self.sym[x]

    def poke(self, name_or_addr, data):
        bank, addr = self._resolve(name_or_addr)
        if isinstance(data, int):
            data = bytes([data])
        for i, b in enumerate(data):
            self.mem[(bank, addr + i)] = b

    def read(self, name_or_addr, n=1):
        bank, addr = self._resolve(name_or_addr)
        return bytes(self.mem.get((bank, addr + i), 0) for i in range(n))

    def read_u8(self, name):
        return self.read(name, 1)[0]

    def read_text(self, name, n):
        return self.charmap.decode(self.read(name, n))

    def screen_text(self):
        return list(self.rows)


MON_NAME_LENGTH = 11


def _nick(s):
    return bytes(ord(c) + 0x80 for c in s) + b"\x50"


def make_emu(rows=()):
    """A battle in progress: slot 1 (PEBBLE, TOGEPI) is out; party
    nicknames GATOR / PEBBLE / REED; enemy TAUROS."""
    emu = FakeEmu(_sym_table(), rows)
    nb, na = emu.sym["wPartyMonNicknames"]
    for i, name in enumerate(("GATOR", "PEBBLE", "REED")):
        emu.poke((nb, na + i * MON_NAME_LENGTH), _nick(name))
    emu.poke("wCurBattleMon", 1)
    emu.poke("wBattleMonSpecies", 175)          # TOGEPI
    emu.poke("wBattleMonMoves", bytes([33, 45, 0, 0]))
    emu.poke("wBattleMonPP", bytes([10, 8, 0, 0]))
    emu.poke("wBattleMonLevel", 20)
    emu.poke("wBattleMonHP", (30).to_bytes(2, "big"))
    emu.poke("wBattleMonMaxHP", (32).to_bytes(2, "big"))
    emu.poke("wBattleMonType", bytes([0, 0]))
    emu.poke("wCurOTMon", 0)
    emu.poke("wEnemyMonNickname", _nick("TAUROS"))
    emu.poke("wEnemyMonSpecies", 128)           # TAUROS
    emu.poke("wEnemyMonLevel", 15)
    emu.poke("wEnemyMonHP", (40).to_bytes(2, "big"))
    emu.poke("wEnemyMonMaxHP", (44).to_bytes(2, "big"))
    emu.poke("wEnemyMonType", bytes([0, 0]))
    return emu


SPECIES = {128: "TAUROS", 159: "CROCONAW", 175: "TOGEPI", 176: "TOGETIC"}


def make_battle(emu):
    return Battle(emu, SimpleNamespace(moves={}, species=SPECIES), None)


# -- (a) me()/enemy() carry the stable identity -------------------------------

def test_me_carries_nickname_and_party_slot():
    b = make_battle(make_emu())
    me = b.me()
    assert me["name"] == "TOGEPI"           # species name: compat unchanged
    assert me["nickname"] == "PEBBLE"
    assert me["party_slot"] == 1
    assert me["moves"] == [(33, 10), (45, 8)]
    assert (me["level"], me["hp"], me["max_hp"]) == (20, 30, 32)


def test_evolution_changes_name_but_not_identity():
    """The live pt5c break: TOGEPI -> TOGETIC mid-grind flips me['name'];
    nickname/party_slot are the stable handles."""
    emu = make_emu()
    b = make_battle(emu)
    before = b.me()
    emu.poke("wBattleMonSpecies", 176)      # evolved to TOGETIC
    after = b.me()
    assert before["name"] == "TOGEPI" and after["name"] == "TOGETIC"
    assert before["nickname"] == after["nickname"] == "PEBBLE"
    assert before["party_slot"] == after["party_slot"] == 1


def test_me_identity_tracks_the_active_slot_after_switch():
    emu = make_emu()
    b = make_battle(emu)
    emu.poke("wCurBattleMon", 2)            # engine switched to slot 2
    me = b.me()
    assert me["party_slot"] == 2
    assert me["nickname"] == "REED"


def test_enemy_carries_nickname_and_party_slot():
    emu = make_emu()
    emu.poke("wCurOTMon", 3)
    e = make_battle(emu).enemy()
    assert e["name"] == "TAUROS"
    assert e["nickname"] == "TAUROS"
    assert e["party_slot"] == 3


# -- (b) play() hands the identity fields to the policy -----------------------

MENU_ROWS = ["PEBBLE L20", "", "FIGHT  PKMN", "PACK   RUN"]


class FakeMenu:
    def __init__(self, emu):
        self.emu = emu
        self.presses = []

    def press(self, seq):
        self.presses.append(seq)
        self.emu.frame += 10

    def wait_for(self, predicate, timeout_frames=600, quiet=False):
        start = self.emu.frame
        while self.emu.frame - start < timeout_frames:
            if predicate(self.emu.screen_text()):
                return True
            self.emu.frame += 10
        return False


class PlayHarness(Battle):
    """play() with the REAL me()/enemy() over fake WRAM; menu/action
    primitives faked. switch_to updates wCurBattleMon like the engine."""

    def __init__(self, turns=2):
        self.emu = make_emu(MENU_ROWS)
        self.menu = FakeMenu(self.emu)
        self.names = SimpleNamespace(moves={}, species=SPECIES)
        self.data = None
        self.turns_left = turns
        self.executed = []

    def active(self):
        return self.turns_left > 0

    def party_alive(self):
        return True

    def _party_count(self):
        return 3

    def _alive_slots(self):
        return [0, 1, 2]

    def _egg_slots(self):
        return set()

    def bag_item_index(self, name, pocket="items"):
        return None

    def _default_policy(self, me, enemy, potion_frac):
        return "attack"

    def _turn(self, record):
        self.executed.append(record)
        self.turns_left -= 1
        hp = int.from_bytes(self.emu.read("wEnemyMonHP", 2), "big")
        self.emu.poke("wEnemyMonHP", (hp - 1).to_bytes(2, "big"))
        return True

    def attack(self, move_idx=None):
        return self._turn(("attack", move_idx))

    def switch_to(self, i):
        self.emu.poke("wCurBattleMon", i)
        return self._turn(("switch", i))


def test_policy_receives_identity_and_sees_switch():
    """The policy's `me` argument carries nickname/party_slot, and after a
    ('switch', 2) turn the next call reflects the new active slot."""
    h = PlayHarness(turns=2)
    seen = []

    def policy(rows, me, enemy):
        seen.append((me["nickname"], me["party_slot"], enemy["nickname"]))
        return ("switch", 2) if len(seen) == 1 else "attack"

    assert h.play(policy=policy) == "won"
    assert h.executed == [("switch", 2), ("attack", None)]
    assert seen == [("PEBBLE", 1, "TAUROS"), ("REED", 2, "TAUROS")]


# -- (c)/(d) level-up pages vs genuine freezes --------------------------------

LEVELUP_ROWS = ["", "", "", "", "SNAG grew to", "level 21!"]
STAT_PAGE_ROWS = ["SNAG", " ATTACK    52", " DEFENSE   40",
                  " SPCL.ATK  38", " SPCL.DEF  35", " SPEED     47"]
FROZEN_ROWS = ["SNAG", "", "...blinking cursor that will never advance"]


class ScreenEmu:
    def __init__(self, rows):
        self.frame = 0
        self.rows = list(rows)

    def screen_text(self):
        return list(self.rows)


class PagingMenu(FakeMenu):
    """Counts play()'s generic text-advance presses as page turns."""

    def __init__(self, emu, harness):
        super().__init__(emu)
        self.harness = harness

    def press(self, seq):
        super().press(seq)
        if seq == "A:2 .:8":
            self.harness.pages_left -= 1


class ScreenHarness(Battle):
    """Screen-driven play(): the battle ends after `pages` text-advance
    presses (a real level-up sheet dismisses on A); a frozen screen never
    ends on its own."""

    def __init__(self, rows, pages=6):
        self.emu = ScreenEmu(rows)
        self.menu = PagingMenu(self.emu, self)
        self.names = SimpleNamespace(moves={}, species={})
        self.data = None
        self.pages_left = pages
        self._me = {"species": 20, "name": "FURRET", "nickname": "SNAG",
                    "party_slot": 0, "level": 21, "hp": 50, "max_hp": 55,
                    "types": [0], "moves": [(33, 10)]}
        self._enemy = {"species": 128, "name": "TAUROS", "nickname": "TAUROS",
                       "party_slot": 0, "level": 15, "hp": 0, "max_hp": 44,
                       "types": [0]}

    def active(self):
        return self.pages_left > 0

    def party_alive(self):
        return True

    def me(self):
        return dict(self._me)

    def enemy(self):
        return dict(self._enemy)


@pytest.mark.parametrize("rows", [LEVELUP_ROWS, STAT_PAGE_ROWS],
                         ids=["grew-to-text", "stat-page-layout"])
def test_levelup_pages_never_trip_the_wedge_diagnostic(rows, caplog):
    """A static 'grew to level N!' page (and the stat sheet) is identical
    text+vitals for many passes -- exactly the freeze fingerprint -- but it
    is paged with the normal A advance: no diagnostic, no 'wedged', no
    recovery B press; the battle completes."""
    h = ScreenHarness(rows)
    with caplog.at_level(logging.WARNING, logger="trek"):
        outcome = h.play()
    assert outcome == "won"
    assert not any("frozen screen" in r.getMessage() for r in caplog.records)
    assert "B:4 .:12" not in h.menu.presses          # no wedge recovery fired
    assert h.menu.presses.count("A:2 .:8") == 6      # paged, page by page


def test_genuine_freeze_still_returns_wedged_with_capped_diag(caplog):
    """A frozen NON-levelup screen keeps the pre-fix behavior: one full
    diagnostic, one recovery attempt, one suppression line, 'wedged'."""
    h = ScreenHarness(FROZEN_ROWS, pages=999)
    with caplog.at_level(logging.WARNING, logger="trek"):
        outcome = h.play()
    assert outcome == "wedged"
    msgs = [r.getMessage() for r in caplog.records]
    assert len([m for m in msgs if "frozen screen" in m]) == 1
    assert len([m for m in msgs if "suppressing further identical" in m]) == 1
    assert h.menu.presses.count("B:4 .:12") == 1
