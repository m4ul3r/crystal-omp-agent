"""Integration: outlook() against the LIVE engine, under poked stages.

Promotes scripts/verify_hardening.py into the repeatable lane. Two
historical bugs are pinned here, both found by a model watching battles,
neither findable by a fake:

- accuracy read as `min(byte, 100)` -> EVERY move reported 100%; noticed
  because IRON TAIL kept whiffing. Under a stacked enemy evasion stage a
  listed-100 move must read its real effective accuracy.
- (same live path) status maths: a paralysed attacker must report
  my_status=['PAR'] and turn_loss=0.25 -- the share of turns PAR eats.

Scaffolding caveat (verify_hardening convention): accuracy/evasion
STAGES are read live by CheckHit every turn, so poking wEnemyEvaLevel /
wBattleMonStatus is faithful; attack/speed stages are NOT (the engine
bakes those into the *Stat words when applied). All pokes go INTO THE
FORK only.

Forked savestate: claude_saves/wren-zephyr-badge.state (one gate from
Route 31's grass patch).
"""

import pytest

pytestmark = pytest.mark.integration


def _outlook_at_action_menu(d):
    """outlook() refuses to invent a matchup before wBattleMon* fills;
    page to the action menu first."""
    a = None
    for _ in range(20):
        a = d.outlook()
        if a:
            return a
        d.press("A:4 .:30")
    raise AssertionError("battle mon blocks never populated")


def test_listed_accuracy_equals_effective_at_neutral_stages(
        fork_driver, route31_wild_battle):
    """The min(byte,100) bug read every move as 100%. At neutral stages
    listed and effective accuracy must agree exactly."""
    d = fork_driver("wren-zephyr-badge")
    route31_wild_battle(d)
    a = _outlook_at_action_menu(d)
    pairs = {m["move"]: (m["accuracy"], m["effective_accuracy"])
             for m in a["moves"]}
    assert all(l == e for l, e in pairs.values()), \
        f"listed != effective at neutral stages: {pairs}"
    assert any(l == 100 for l, _ in pairs.values()), \
        f"no listed-100 move on this moveset: {pairs}"


def test_enemy_evasion_stage_moves_effective_accuracy(
        fork_driver, route31_wild_battle):
    """wEnemyEvaLevel = 9 (+2 evasion) must drag a listed-100 move's
    effective accuracy down to 60% -- '100%' that stays 100% is exactly
    the lie IRON TAIL exposed."""
    d = fork_driver("wren-zephyr-badge")
    route31_wild_battle(d)
    a = _outlook_at_action_menu(d)
    assert any(m["accuracy"] == 100 for m in a["moves"])
    d.emu.write("wEnemyEvaLevel", 9)          # scaffolding: fork only
    a = d.outlook()
    stacked = {m["move"]: (m["accuracy"], m["effective_accuracy"])
               for m in a["moves"]}
    hundreds = [v for v in stacked.values() if v[0] == 100]
    assert hundreds, stacked
    assert all(e == 60 for _, e in hundreds), stacked


def test_paralysis_reports_status_and_turn_loss(
        fork_driver, route31_wild_battle):
    """Poking wBattleMonStatus |= PAR must surface in outlook() as
    my_status ['PAR'] with turn_loss 0.25 (a quarter of turns eaten)."""
    d = fork_driver("wren-zephyr-badge")
    route31_wild_battle(d)
    a = _outlook_at_action_menu(d)
    assert a["my_status"] != ["PAR"]          # clean before the poke
    d.emu.write("wBattleMonStatus", 0x40)     # scaffolding: fork only
    a = d.outlook()
    assert a["my_status"] == ["PAR"]
    assert a["turn_loss"] == 0.25


def test_battle_still_resolves_after_pokes(fork_driver, route31_wild_battle):
    """The scaffolding must not poison the fight itself: after both pokes
    the battle plays out to a resolved outcome and a clean overworld."""
    d = fork_driver("wren-zephyr-badge")
    route31_wild_battle(d)
    _outlook_at_action_menu(d)
    d.emu.write("wEnemyEvaLevel", 9)
    d.emu.write("wBattleMonStatus", 0x40)
    d.fight(max_frames=40000)
    ev = d.encounter_events[-1]
    assert ev["outcome"] not in ("wedged", "timeout", "stuck", "stalled"), ev
    assert ev["battle_live"] is False, ev
    assert not d.battle()
