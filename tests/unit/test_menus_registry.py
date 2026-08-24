"""Screen-text classifiers and the shared action registry."""
import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

from crystalagent import registry
from crystalagent.menus import (
    Menus, _cursor_x, battle_menu_up, naming_keyboard_up, textbox_up,
)
from trek import Driver

pytestmark = pytest.mark.unit


def row(text, width=20):
    return text.ljust(width)


def test_cursor_finds_both_glyphs():
    assert _cursor_x(row("  ▶FIGHT")) == 2
    assert _cursor_x(row("  ▷SAVE")) == 2
    assert _cursor_x(row("no cursor here")) == -1


def test_cursor_leftmost_of_any_glyph():
    assert _cursor_x(row("▶x▷y")) == 0


# -- Menus.has_label (module-level helper exercised via plain rows) ---------

def test_has_label_prefix_match():
    rows = [row(""), row("  ▶SAVE"), row("  SYSTEM")]
    assert Menus.has_label(None, rows, "SAVE")
    assert not Menus.has_label(None, rows, "SYSTEM")     # cursor sits on SAVE's row


def test_has_label_requires_immediate_prefix():
    # POKéMON starts with "POKé" but not with "POKéDEX"
    rows = [row("  ▶POKéMON")]
    assert not Menus.has_label(None, rows, "POKéDEX")


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

    def observe(self):
        return {"ui": {"battle": self.in_battle}}

    def __getattr__(self, name):
        # record calls for any registered verb
        def fn(**kw):
            self.calls.append((name, kw))
            return f"{name}-ok"
        return fn


REQUIRED_KWARGS = {
    "goto": {"x": 6, "y": 5},
    "walk": {"path": "U*3"},
    "talk_to": {"x": 4, "y": 8},
    "mart_buy": {"x": 4, "y": 8, "item_name": "POTION"},
    "use_item": {"item_name": "POTION"},
    "route": {"dest_map": "VIOLET_CITY"},
    "travel": {"dest_map": "VIOLET_CITY"},
    "step_dir": {"mv": "D"},
    "press": {"seq": "A:4"},
    "use_cut": {"tree_x": 4, "tree_y": 8},
}


@pytest.mark.parametrize("name", sorted(registry.ACTIONS))
def test_every_action_resolves(name, monkeypatch):
    import trek
    monkeypatch.setattr(trek, "heal_pokecenter",
                        lambda d: "healed", raising=False)
    d = FakeDriver(in_battle=(registry.ACTIONS[name].need_battle is True))
    kwargs = dict(REQUIRED_KWARGS.get(name, {}))
    out = registry.resolve(d, name, kwargs)
    if name != "heal":
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
    with pytest.raises(ValueError, match="needs no active battle"):
        registry.check(d, "goto", {"x": 6, "y": 5})
    d2 = FakeDriver(in_battle=False)
    with pytest.raises(ValueError, match="needs an active battle"):
        registry.check(d2, "fight", {})
    # and the happy paths pass
    registry.check(FakeDriver(in_battle=True), "fight", {})
    registry.check(FakeDriver(in_battle=False), "goto", {"x": 6, "y": 5})


def test_registry_methods_exist_on_driver():
    for act in registry.ACTIONS.values():
        if act.method or act.fn is None:
            attr = act.method or act.name
            assert hasattr(Driver, attr), f"Driver.{attr} missing"


def test_leg_table_matches_dispatch_chain():
    """Regression: every dispatched leg must be in the arity spec and vice
    versa. This exact drift made `trek catch/fight/flush/heal/route29`
    unreachable while `mart` was a silent no-op."""
    tree = ast.parse((REPO / "trek.py").read_text(encoding="utf-8"))
    main = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    spec_keys, dispatched = set(), set()
    for node in ast.walk(main):
        if (isinstance(node, (ast.Assign, ast.AnnAssign))
                and isinstance(getattr(node, "value", None), ast.Dict)):
            keys = {k.value for k in node.value.keys
                    if isinstance(k, ast.Constant)}
            targets = (node.targets if isinstance(node, ast.Assign)
                       else [node.target])
            if any(isinstance(t, ast.Name) and t.id == "spec"
                   for t in targets):
                spec_keys = keys
        if (isinstance(node, ast.Compare)
                and isinstance(node.left, ast.Name)
                and node.left.id == "leg"
                and len(node.ops) == 1
                and isinstance(node.ops[0], ast.Eq)):
            for cmp in node.comparators:
                if isinstance(cmp, ast.Constant) and isinstance(
                        cmp.value, str):
                    dispatched.add(cmp.value)
    assert spec_keys, "arity spec not found in trek.main()"
    assert spec_keys == dispatched, (
        f"drift: spec-only={sorted(spec_keys - dispatched)}, "
        f"dispatch-only={sorted(dispatched - spec_keys)}")
