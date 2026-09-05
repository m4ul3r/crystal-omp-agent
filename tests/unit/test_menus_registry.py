"""Screen-text classifiers and the shared action registry."""
import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

from crystalagent import registry
from crystalagent.menus import (
    Menus, _cursor_x, battle_menu_up, naming_keyboard_up, textbox_up,
)
from crystalagent.driver import Driver

pytestmark = pytest.mark.unit


def row(text, width=20):
    return text.ljust(width)


def test_cursor_finds_both_glyphs():
    assert _cursor_x(row("  ▶FIGHT")) == 2
    assert _cursor_x(row("  ▷SAVE")) == 2
    assert _cursor_x(row("no cursor here")) == -1


def test_cursor_leftmost_of_any_glyph():
    assert _cursor_x(row("▶x▷y")) == 0


# -- Menus.has_label (a staticmethod: no instance, just rows) ---------------

def test_has_label_prefix_match():
    rows = [row(""), row("  ▶SAVE"), row("  SYSTEM")]
    assert Menus.has_label(rows, "SAVE")
    assert not Menus.has_label(rows, "SYSTEM")     # cursor sits on SAVE's row


def test_has_label_requires_immediate_prefix():
    # POKéMON starts with "POKé" but not with "POKéDEX"
    rows = [row("  ▶POKéMON")]
    assert not Menus.has_label(rows, "POKéDEX")


def test_battle_menu_classifier():
    assert battle_menu_up([row("FIGHT"), row("PKMN"), row("PACK"),
                           row("RUN")])
    assert not battle_menu_up([row("FIGHT")])          # no RUN -> not the grid


def test_naming_keyboard_needs_del_and_end():
    kb = [row("") for _ in range(18)]
    kb[5] = row("A B C DEL")
    kb[6] = row("D E F END")
    assert naming_keyboard_up(kb)
    assert not naming_keyboard_up([row("") for _ in range(18)])


def _blank_rows():
    return [" " * 20 for _ in range(18)]


def test_textbox_up_with_text_in_bottom_rows():
    rows = _blank_rows()
    rows[14] = row("┌────────────────┐")
    rows[15] = row("│ OAK: hello!    │")
    rows[16] = row("│                │")
    rows[17] = row("└────────────────┘")
    assert textbox_up(rows)


def test_textbox_up_false_for_empty_box():
    rows = _blank_rows()
    for i in range(13, 18):
        rows[i] = row("─" * 20)
    assert not textbox_up(rows)


def test_textbox_up_ignores_top_screen_text():
    # a top-anchored menu with an empty bottom box is NOT a textbox
    # (documents current bottom-only detection)
    rows = _blank_rows()
    rows[1] = row("MENU TITLE")
    assert not textbox_up(rows)


# -- registry ---------------------------------------------------------------

class FakeDriver:
    def __init__(self, in_battle=False):
        self.in_battle = in_battle
        self.calls = []

    def battle(self):
        return self.in_battle

    def observe(self):
        raise AssertionError("registry.check must not build an observation")

    def __getattr__(self, name):
        # record calls for any registered verb
        def fn(**kw):
            self.calls.append((name, kw))
            return f"{name}-ok"
        return fn


ACTION_CONTRACT = {
    "goto": (("x", "y"), ("label", "map_name"), False),
    "walk": (("path",), ("label",), False),
    "fight": ((), ("max_frames", "policy", "require_decision"), True),
    "catch": ((), ("ball", "max_balls", "nickname"), True),
    "heal": ((), ("tries",), None),
    "talk_to": (("x", "y"), ("label", "facing"), False),
    "mart_buy": (("x", "y", "item_name"), ("qty", "label"), False),
    "use_item": (("item_name",), ("target_slot", "mon", "field"), False),
    "heal_party": ((), ("items", "max_items_per_mon"), False),
    "settle": ((), ("quiet", "spacing", "max_frames"), None),
    "drain_scene": ((), ("max_frames",), None),
    "catch_up": (
        (),
        ("nickname", "ball", "max_balls", "max_encounters", "label"),
        False,
    ),
    "resolve_choice": ((), ("choice",), None),
    "who_fights": ((), (), True),
    "gym_scout": (("map",), (), None),
    "travel": (("dest_map",), ("label",), False),
    "name_prompt": (("name",), (), False),
    "step_dir": (("mv",), ("max_frames",), False),
    "press": (("seq",), (), None),
    "use_cut": (("tree_x", "tree_y"), ("label", "forget_move"), False),
    "deposit": (("mon",), (), False),
    "withdraw": (("mon",), (), False),
    "box_list": ((), (), False),
    "use_field_move": (("move",), ("facing",), False),
    "teach_tm": (("tm", "mon"), ("forget",), False),
}

REQUIRED_KWARGS = {
    "goto": {"x": 6, "y": 5},
    "walk": {"path": "U*3"},
    "talk_to": {"x": 4, "y": 8},
    "mart_buy": {"x": 4, "y": 8, "item_name": "POTION"},
    "use_item": {"item_name": "POTION"},
    "travel": {"dest_map": "VIOLET_CITY"},
    "step_dir": {"mv": "D"},
    "press": {"seq": "A:4"},
    "use_cut": {"tree_x": 4, "tree_y": 8},
    "gym_scout": {"map": "VIOLET_GYM"},
    "name_prompt": {"name": "BUD"},
    "who_fights": {},
    "resolve_choice": {"choice": "YES"},
    "drain_scene": {},
    "catch_up": {},
    "deposit": {"mon": "TOGEPI"},
    "withdraw": {"mon": "PANIC"},
    "box_list": {},
    "use_field_move": {"move": "WATERFALL"},
    "teach_tm": {"tm": "HM07", "mon": "GOLDEEN"},
}


def test_action_contract_is_exact():
    assert {
        name: (act.required, act.optional, act.need_battle)
        for name, act in registry.ACTIONS.items()
    } == ACTION_CONTRACT


def test_action_expect_change_contract_is_exact():
    assert {
        name for name, action in registry.ACTIONS.items()
        if not action.expect_change
    } == {
        "goto", "walk", "heal", "talk_to", "heal_party", "settle",
        "drain_scene", "resolve_choice", "who_fights", "gym_scout",
        "travel", "box_list",
    }


@pytest.mark.parametrize("name", sorted(registry.ACTIONS))
def test_every_action_resolves(name):
    d = FakeDriver(in_battle=(registry.ACTIONS[name].need_battle is True))
    kwargs = dict(REQUIRED_KWARGS.get(name, {}))
    registry.resolve(d, name, kwargs)
    assert d.calls, f"{name} never invoked its driver method"


def test_unknown_action_names_allowed_set():
    d = FakeDriver()
    with pytest.raises(ValueError, match="unknown action"):
        registry.check(d, "teleport", {})


def test_missing_required_argument_rejected():
    d = FakeDriver()
    with pytest.raises(ValueError, match="missing required"):
        registry.check(d, "goto", {"x": 6})


def test_unknown_kwarg_rejected():
    d = FakeDriver()
    with pytest.raises(ValueError, match="unknown argument"):
        registry.check(d, "settle", {"bogus": 1})


def test_preconditions_against_live_state():
    d = FakeDriver(in_battle=True)
    with pytest.raises(ValueError) as outside:
        registry.check(d, "goto", {"x": 6, "y": 5})
    assert str(outside.value) == "goto: needs no active battle (ui.battle=True)"
    d2 = FakeDriver(in_battle=False)
    with pytest.raises(ValueError) as inside:
        registry.check(d2, "fight", {})
    assert str(inside.value) == "fight: needs an active battle (ui.battle=False)"
    registry.check(FakeDriver(in_battle=True), "fight", {})
    registry.check(FakeDriver(in_battle=False), "goto", {"x": 6, "y": 5})


def test_registry_methods_exist_on_driver():
    for act in registry.ACTIONS.values():
        if act.method or act.fn is None:
            attr = act.method or act.name
            assert hasattr(Driver, attr), f"Driver.{attr} missing"


CLI_ARITIES = {
    "walk": (1, 1),
    "goto": (2, 3),
    "talk": (2, 2),
    "route": (1, 1),
    "travel": (1, 1),
    "mart": (4, 4),
    "verify": (1, 10),
    "states": (0, 0),
    "train": (1, 1),
    "gc": (0, 2),
    "map": (0, 1),
    "catch": (0, 1),
    "fight": (0, 0),
    "flush": (0, 0),
    "heal": (0, 0),
    "route29": (0, 0),
    "to_violet": (0, 0),
    "errand1": (0, 0),
    "errand2": (0, 0),
    "errand3": (0, 0),
    "errand4": (0, 0),
    "violet": (0, 0),
}


def test_leg_table_matches_dispatch_chain():
    """The exact public CLI names and positional arities stay stable."""
    tree = ast.parse((REPO / "trek.py").read_text(encoding="utf-8"))
    main = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    spec = None
    dispatched = set()
    for node in ast.walk(main):
        if (isinstance(node, (ast.Assign, ast.AnnAssign))
                and isinstance(getattr(node, "value", None), ast.Dict)):
            targets = (node.targets if isinstance(node, ast.Assign)
                       else [node.target])
            if any(isinstance(t, ast.Name) and t.id == "spec"
                   for t in targets):
                spec = ast.literal_eval(node.value)
        if (isinstance(node, ast.Compare)
                and isinstance(node.left, ast.Name)
                and node.left.id == "leg"
                and len(node.ops) == 1
                and isinstance(node.ops[0], ast.Eq)):
            dispatched.update(
                cmp.value for cmp in node.comparators
                if isinstance(cmp, ast.Constant) and isinstance(cmp.value, str)
            )
    assert spec == CLI_ARITIES
    assert set(spec) == dispatched
