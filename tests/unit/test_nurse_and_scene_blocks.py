"""Two "the map already says so" fixes, both from session claude pt12.

FUCK_I_MESSED_UP.md #78: heal_pokecenter routed to (3,3) -- the Johto town
counter -- so INDIGO_PLATEAU_POKECENTER_1F, whose counter is on row 8 with
the nurse behind (3,7), was unhealable: no path, then "party not fully
healed". The map declares where she stands.

Grievance 6 (PROGRESS pt12): nav.blocked marks at (16,4)/(17,4) severed the
only corridor to the League door and came back after every failed goto. The
cells carry coord_events whose scene token is the map's ONLY scene, and the
post-battle script sets the scene to that same id -- so the token matched
forever. The scripts' own guard chain says whether they still have teeth.
"""
from pathlib import Path

import pytest

import crystalagent.driver.inventory as inventory_driver
from crystalagent.driver import Driver
from crystalagent.nav import script_guards
from crystalagent import missables, paths

pytestmark = pytest.mark.unit

MAPS = Path(paths.REPO_ROOT, "maps")


# -- object_events name the nurse (#78) -------------------------------------

def test_parse_map_objects_finds_the_indigo_nurse_off_the_map_source():
    objs = missables.parse_map_objects(MAPS / "IndigoPlateauPokecenter1F.asm")
    nurse = [o for o in objs if o["sprite"] == "SPRITE_NURSE"]
    assert len(nurse) == 1
    assert (nurse[0]["x"], nurse[0]["y"]) == (3, 7)      # NOT (3, 3)
    assert nurse[0]["script"] == "IndigoPlateauPokecenter1FNurseScript"


def test_parse_map_objects_still_finds_a_johto_nurse_at_the_old_coords():
    objs = missables.parse_map_objects(MAPS / "VioletPokecenter1F.asm")
    nurse = next(o for o in objs if o["sprite"] == "SPRITE_NURSE")
    assert (nurse["x"], nurse["y"]) == (3, 1)


def test_parse_map_objects_keeps_declaration_order_and_events():
    objs = missables.parse_map_objects(MAPS / "IndigoPlateauPokecenter1F.asm")
    assert [o["sprite"] for o in objs][:2] == ["SPRITE_NURSE", "SPRITE_CLERK"]
    assert all(o["event"] is None or o["event"].startswith("EVENT_")
               for o in objs)


class NurseDriver:
    """Duck-typed d for heal_pokecenter with a nurse away from (3,3)."""

    def __init__(self, cell=(3, 7)):
        self.cell = cell
        self.talked = []
        self.gotos = []
        self.steps = []
        self.healed = False
        self.emu = _NurseEmu()
        self.names = None

        class M:
            def wait_for(self, pred, timeout_frames=600, quiet=False):
                return False

            def select_label(self, label, **kw):
                return True
        self.menu = M()

    def map_name(self):
        return "INDIGO_PLATEAU_POKECENTER_1F"

    def sprite_cell(self, sprite, map_name=None):
        return self.cell if sprite == "SPRITE_NURSE" else None

    def talk_to(self, x, y, label=""):
        self.talked.append((x, y))
        self.healed = True
        return "talked"

    def goto(self, x, y, label=""):
        self.gotos.append((x, y))
        return True

    def step_dir(self, mv):
        self.steps.append(mv)
        return "moved" if mv == "D" else "blocked"

    def facing(self):
        return "U"

    def press(self, seq):
        self.emu.tick(5)

    def flush_dialog(self, *a, **k):
        return "done"

    def settle(self, **kw):
        pass

    def textbox(self):
        return False

    def lead(self):
        return {"name": "TYPHLOSION", "hp": 198, "max_hp": 198}

    def party(self):
        hp = 198 if self.healed else 12
        return [{"species": "TYPHLOSION", "hp": hp, "max_hp": 198}]


class _NurseEmu:
    def __init__(self):
        self.frame = 0

    def tick(self, n=1):
        self.frame += n

    def read_u8(self, name):
        return 0


def test_heal_talks_to_the_nurse_the_map_declares(monkeypatch):
    d = NurseDriver(cell=(3, 7))
    monkeypatch.setattr(inventory_driver, "game_state",
                        lambda emu, names: {"party": d.party()})
    inventory_driver.heal_pokecenter(d)
    assert d.talked == [(3, 7)]
    assert (3, 3) not in d.gotos          # never routes to the Johto counter


def test_heal_falls_back_to_the_old_counter_when_no_nurse_is_declared(
        monkeypatch):
    d = NurseDriver(cell=None)
    monkeypatch.setattr(inventory_driver, "game_state",
                        lambda emu, names: {"party": d.party()})
    d.healed = True                       # the (3,3) path heals in this fake
    inventory_driver.heal_pokecenter(d)
    assert d.gotos and d.gotos[0] == (3, 3)


def test_heal_steps_away_from_whatever_it_faces(monkeypatch):
    d = NurseDriver()
    monkeypatch.setattr(inventory_driver, "game_state",
                        lambda emu, names: {"party": d.party()})
    inventory_driver.heal_pokecenter(d)
    assert d.steps[-1] == "D"             # faced UP -> steps DOWN
    assert d.steps.count("D") == 1


# -- scene blocks expire when the script's own guards say so ----------------

def test_script_guards_reads_the_rival_guard_chain():
    guards = script_guards(paths.REPO_ROOT, "IndigoPlateauPokecenter1F",
                                "PlateauRivalBattle1")
    assert guards[:2] == [
        ("checkevent", "EVENT_BEAT_RIVAL_IN_MT_MOON", "iffalse",
         "PlateauRivalScriptDone"),
        ("checkflag", "ENGINE_INDIGO_PLATEAU_RIVAL_FIGHT", "iftrue",
         "PlateauRivalScriptDone")]


def test_script_guards_stops_at_the_first_non_guard():
    """readvar/ifequal weekday tests are not check/jump pairs, so the chain
    ends there -- anything past it is not provably guard-only."""
    guards = script_guards(paths.REPO_ROOT, "IndigoPlateauPokecenter1F",
                                "PlateauRivalBattle1")
    assert len(guards) == 2
    assert all(g[0] in ("checkevent", "checkflag") for g in guards)


def test_a_guardless_scene_reports_no_guards():
    """Route 32's push-back cutscene -- the case nav.blocked exists for --
    has no leading guard chain, so nothing can call it spent."""
    assert script_guards(paths.REPO_ROOT, "Route32",
                              "Route32CooltrainerMStopsYouScene") == []


def guard_driver(events=(), flags=()):
    d = Driver.__new__(Driver)
    d.nav = _FakeNav()
    d._event_flag = lambda name: name in events
    d.engine_flag = lambda name: name in flags
    return d


class _FakeNav:
    _repo = paths.REPO_ROOT


def test_scene_spent_when_the_gating_event_is_not_set_yet():
    """CLAUDE never went to Kanto, so EVENT_BEAT_RIVAL_IN_MT_MOON is clear,
    `iffalse` jumps to a script that does nothing, and the corridor is
    walkable. This is the live state of the champion file."""
    d = guard_driver(events=(), flags=())
    assert d._scene_spent("IndigoPlateauPokecenter1F",
                          "PlateauRivalBattle1") is True


def test_scene_spent_when_the_fight_already_happened():
    d = guard_driver(events=("EVENT_BEAT_RIVAL_IN_MT_MOON",),
                     flags=("ENGINE_INDIGO_PLATEAU_RIVAL_FIGHT",))
    assert d._scene_spent("IndigoPlateauPokecenter1F",
                          "PlateauRivalBattle1") is True


def test_scene_still_armed_keeps_its_cells_blocked():
    """Event set, daily flag clear: the ambush can fire, so nav must keep
    refusing to plan through it."""
    d = guard_driver(events=("EVENT_BEAT_RIVAL_IN_MT_MOON",), flags=())
    assert d._scene_spent("IndigoPlateauPokecenter1F",
                          "PlateauRivalBattle1") is False


def test_a_guardless_disruptive_scene_is_never_called_spent():
    d = guard_driver()
    assert d._scene_spent("Route32",
                          "Route32CooltrainerMStopsYouScene") is False


def test_unreadable_flags_assume_the_scene_is_armed():
    d = Driver.__new__(Driver)
    d.nav = _FakeNav()

    def boom(name):
        raise ValueError("unknown flag")
    d._event_flag = boom
    d.engine_flag = boom
    assert d._scene_spent("IndigoPlateauPokecenter1F",
                          "PlateauRivalBattle1") is False


def test_heal_answers_a_residual_nurse_prompt(monkeypatch):
    """A live heal returned with "Shall we heal your POKéMON?" still on
    screen, and an open choice box blocks every later step: the next
    travel reported "blocked by choice menu" from inside the Pokécenter
    and the gym leg never started."""
    d = NurseDriver()
    monkeypatch.setattr(inventory_driver, "game_state",
                        lambda emu, names: {"party": d.party()})
    rows = ["", "", "┌────┐", "│▶YES│", "│ NO │", "└────┘"]
    d._choice_box = lambda r: {"cursor": 3, "options": [(3, "YES"), (4, "NO")],
                               "span": (0, 5)}
    d.emu.screen_text = lambda: list(rows)
    answered = []

    def resolve(choice="YES"):
        answered.append(choice)
        d._choice_box = lambda r: None
        return {"answered": True, "chose": choice, "options": ["YES", "NO"]}

    d.resolve_choice = resolve
    d.close_menus = lambda: answered.append("close_menus")
    inventory_driver.heal_pokecenter(d)
    assert answered == ["NO"], answered
