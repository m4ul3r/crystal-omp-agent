"""Crystal, actually running.

The registry calls Gen 2 `live`, and until now that claim rested on the data
layer alone: symbols parsed, maps decoded, BFS working. Those are real but they
are all static -- none of them proves the emulator is being driven.

These scenarios drive the ROM from power-on through its own menus, so "live"
means observed. What is still ABSENT is a Gen-2 battle driver: there is no
`pokeagent/gen2/battle.py`, and `Gen2Adapter.CAPABILITIES` deliberately omits
`battle`. That absence is asserted here too, so the day someone adds one this
test fails and the claim gets updated with it.
"""

import pytest

from pokeagent import gamespec
from pokeagent.adapters import base

pytestmark = pytest.mark.integration


def _main_menu(emu, rounds: int = 8) -> list[str]:
    """Boot to the NEW GAME / OPTION menu, returning the decoded screen."""
    while emu.frame < 3000:
        emu.tick(200)
    for _ in range(rounds):
        for button in ("start", "a"):
            emu.run_sequence([([button], 4), ([], 40)])
            rows = emu.screen_text()
            if any("NEW GAME" in r for r in rows):
                return rows
    return emu.screen_text()


# Function-scoped on purpose: these tests DRIVE the emulator, so a shared
# instance makes them order-dependent -- selecting NEW GAME in one test left
# the next one looking for a menu that was no longer on screen. A fresh boot
# costs about a second and buys independence.
@pytest.fixture()
def crystal():
    spec = gamespec.get("crystal")
    try:
        return base.resolve(spec).open()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"pokecrystal artifacts unavailable: {exc}")


def test_crystal_boots_through_the_adapter(crystal):
    assert crystal.spec.core == "pyboy", "Gen 2 is a Game Boy, not a GBA"
    crystal.emu.tick(200)
    assert crystal.emu.frame >= 200


def test_the_screen_really_decodes_to_text(crystal):
    """`decodable_screen` is a claimed capability. Here it is, on the ROM's own
    output: the main menu's labels and its box-drawing border, which are real
    charmap entries rather than an OCR guess."""
    assert "decodable_screen" in crystal.capabilities
    rows = _main_menu(crystal.emu)
    joined = " ".join(rows)
    assert "NEW GAME" in joined
    assert "OPTION" in joined
    assert any("\u250c" in r or "\u2502" in r for r in rows), "box border"


def test_the_menu_cursor_is_found_and_followed(crystal):
    """The vendored Gen-2 `Menus` was never driven in this checkout. It is
    now: the cursor glyph is located and the labelled row is selected."""
    from pokeagent.gen2.menus import Menus

    rows = _main_menu(crystal.emu)
    assert any("NEW GAME" in r for r in rows)

    menus = Menus(crystal.emu)
    found = menus.cursor_row()
    assert found is not None, "no cursor glyph on a menu that has one"
    index, text = found
    assert "NEW GAME" in text

    assert menus.select_label("NEW GAME") is True
    crystal.emu.run_sequence([(["a"], 4), ([], 60)])
    after = " ".join(crystal.emu.screen_text())
    assert "NEW GAME" not in after, "selecting NEW GAME must leave the menu"


def test_gen2_does_not_claim_a_battle_driver_it_does_not_have(crystal):
    """Honesty check. Gen 3 drives battles; Gen 2 has no battle module here,
    so the capability set must not advertise one."""
    import importlib

    assert "battle" not in crystal.capabilities
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("pokeagent.gen2.battle")


def test_both_generations_load_in_one_process(crystal):
    """The multi-game claim in one assertion: two ROMs, two cores, one run."""
    sapphire = base.resolve(gamespec.get("sapphire")).open()
    assert sapphire.spec.core == "mgba"
    assert crystal.spec.core == "pyboy"
    assert sapphire.spec.generation == 3
    assert crystal.spec.generation == 2


# ---- Gen 1 (pokered) -----------------------------------------------------


@pytest.fixture()
def red():
    spec = gamespec.get("red")
    try:
        return base.resolve(spec).open()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"pokered artifacts unavailable: {exc}")


def test_red_boots_and_decodes_its_own_menu(red):
    """Kanto, exercised rather than declared. pokered builds with the same
    vendored rgbds as pokecrystal, so the symbol, charmap and emulator layers
    port unchanged -- and this proves it on the ROM's own output."""
    rows = _main_menu(red.emu)
    joined = " ".join(rows)
    assert "NEW GAME" in joined
    assert "OPTION" in joined


def test_reds_cursor_is_found_despite_a_duplicate_charmap_entry(red):
    """pokered maps $ed TWICE -- to the town-map up arrow at charmap.asm:85
    and to the menu cursor at :177 -- and the parser keeps the first, so Red's
    ordinary NEW GAME cursor decodes as an up arrow. Same byte, different
    alias. The menu driver matches all three glyphs so it need not know which
    game it is looking at."""
    from pokeagent.gen2.menus import Menus

    _main_menu(red.emu)
    menus = Menus(red.emu)
    found = menus.cursor_row()
    assert found is not None, "cursor glyph not recognised in Gen 1"
    assert "NEW GAME" in found[1]
    assert menus.select_label("NEW GAME") is True


def test_red_refuses_the_layers_it_did_not_port(red):
    """The unported readers must raise by NAME. A Gen-1 party read through
    Gen-2 offsets parses cleanly and every stat is wrong."""
    with pytest.raises(NotImplementedError, match="not ported"):
        red.state.party()
    with pytest.raises(NotImplementedError, match="not ported"):
        red.nav.grid("PalletTown")


def test_red_claims_only_what_it_can_do(red):
    assert "decodable_screen" in red.capabilities
    assert "banked_memory" in red.capabilities
    assert "flat_party" not in red.capabilities, "nothing reads Gen 1's party"
    assert "battle" not in red.capabilities


def test_three_generations_load_in_one_process(crystal, red):
    """mGBA and two PyBoy cartridges, side by side."""
    sapphire = base.resolve(gamespec.get("sapphire")).open()
    gens = {sapphire.spec.generation, crystal.spec.generation,
            red.spec.generation}
    assert gens == {1, 2, 3}
