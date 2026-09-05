"""Battle scenarios against a real wild fight.

The checkpoint is the Poochyena that jumps Birch on Route 101: MUDKIP L5
21/21 with TACKLE and GROWL versus POOCHYENA L2 13/13.
"""

import pytest

pytestmark = pytest.mark.integration


def test_outlook_matches_the_engines_own_maths(fork):
    """The damage span must bracket what the engine actually deals.

    This is the claim the whole tactics layer rests on: if the formula is
    off, every recommendation is confidently wrong -- the predecessor's worst
    defect class.
    """
    d = fork("first-battle")
    analysis = d.outlook()
    assert analysis is not None, d.tactics.last_outlook_reason
    tackle = next(m for m in analysis["moves"] if m["name"] == "SCRATCH")
    lo, hi = tackle["damage_min"], tackle["damage_max"]
    assert 0 < lo <= hi

    before = d.state.battle().mons[1]["hp"]
    assert d.attack(tackle["slot"])
    d.battle._settle_turn() if hasattr(d.battle, "_settle_turn") else d.emu.tick(600)
    after = d.state.battle().mons[1]["hp"] if d.in_battle() else 0
    dealt = before - after
    assert lo <= dealt <= hi, f"predicted {lo}-{hi}, engine dealt {dealt}"


def test_moves_carry_their_engine_slot(fork):
    """A move row's slot is the ENGINE slot, never the list position.

    The predecessor sorted its move list and then used the index as a slot,
    so it picked LEER as its 'best move' (its journal #22).
    """
    d = fork("first-battle")
    analysis = d.outlook()
    by_slot = {m["slot"]: m["name"] for m in analysis["moves"]}
    assert by_slot[0] == "SCRATCH" and by_slot[1] == "GROWL"

    # Selecting slot 1 must use GROWL: no damage, enemy attack stage drops.
    enemy_hp = d.state.battle().mons[1]["hp"]
    assert d.attack(1)
    d.emu.tick(600)
    if d.in_battle():
        assert d.state.battle().mons[1]["hp"] == enemy_hp, "GROWL must not damage"


def test_status_moves_are_not_recommended_as_damage(fork):
    d = fork("first-battle")
    action, why = d.recommend()
    assert action[0] == "attack" and action[1] == 0, f"expected SCRATCH, got {action}"
    assert why and "SCRATCH" in why, "a harness choice must explain itself"


def test_play_wins_and_logs_honestly(fork):
    """A full battle, with a turn log that can be audited afterwards."""
    d = fork("first-battle")
    result = d.fight()
    assert result["outcome"] == "B_OUTCOME_WON", result
    assert not d.in_battle()
    assert result["turns"], "a won battle must have turns"
    for turn in result["turns"]:
        assert turn.my_mon and turn.their_mon
        assert turn.why, "every turn records why it was chosen"
    # The enemy ended at zero and we did not.
    last = result["turns"][-1]
    assert last.their_hp_after == 0
    assert d.state.party()[0].hp > 0


def test_harness_choices_are_never_silent(fork):
    """With no policy the harness decides, and must say so on every turn."""
    d = fork("first-battle")
    result = d.fight()
    assert all("tactics" in (t.note or "") for t in result["turns"])


def test_policy_overrides_and_a_raising_policy_is_distinct(fork):
    """A policy that raises must be recorded differently from one that
    declines -- conflating them is how the predecessor silently fell back to
    slot 0 and picked a status move (its journal #21)."""
    d = fork("first-battle")

    def boom(frame):
        raise KeyError("deliberate")

    result = d.fight(policy=boom)
    notes = " ".join(t.note or "" for t in result["turns"])
    assert "raised" in notes or "KeyError" in notes, notes


def test_registry_exposes_the_battle_verbs(fork):
    from pokeagent.registry import resolve

    d = fork("first-battle")
    frame = resolve(d, "battle_frame", {})
    assert frame["active"] and frame["moves"]
    analysis = resolve(d, "outlook", {})
    assert analysis["moves"]
