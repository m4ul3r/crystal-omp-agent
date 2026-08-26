"""Integration: trapped-switch refusal must never wedge the battle.

Historical bug (the 535-second one): on Victory Road an ONIX had BOUND
the active mon; the policy kept asking to switch, TryPlayerSwitch
answered BattleText_MonCantBeRecalled and `jp BattleMenuPKMN_Loop` --
party menu left open, nothing on screen changing, 60 'fights' / 535 s /
0 exp. The hardened path: the trap is detected BEFORE driving menus
(wPlayerWrapCount / wEnemySubStatus5 SUBSTATUS_CANT_RUN), the impossible
switch is refused pre-flight with a named reason, and the battle still
resolves through the substituted action.

Scaffolding caveat (scripts/verify_hardening.py convention): the wrap
counter is poked INTO THE FORK at decision time. That poke is faithful
-- unlike attack/speed stat stages, which the engine bakes into the
*Stat words when applied, TryPlayerSwitch reads wPlayerWrapCount live on
every attempt -- so the refusal exercised here is byte-for-byte the one
a real Bind produces.

Forked savestate: claude_saves/wren-zephyr-badge.state (Falkner beaten;
one gate from Route 31's grass patch).
"""

import pytest

pytestmark = pytest.mark.integration


def test_trapped_switch_refusal_does_not_wedge(fork_driver,
                                               route31_wild_battle,
                                               caplog):
    """A policy that asks to switch while wPlayerWrapCount != 0 must be
    refused pre-flight ('trapped: can't be recalled'), the turn resolved
    anyway, and the battle finished -- never a wedge to the frame cap."""
    d = fork_driver("wren-zephyr-badge")
    route31_wild_battle(d)

    def pol(rows, me, enemy):
        # scaffolding: re-poke AT DECISION TIME so TryPlayerSwitch sees
        # a non-zero counter when the engine checks recall legality
        d.emu.write("wPlayerWrapCount", 5)
        return ("switch", 1)
    f0 = d.emu.frame
    d.fight(max_frames=40000, policy=pol)
    ev = d.encounter_events[-1]
    frames = d.emu.frame - f0
    # the battle RESOLVED: no wedge, no timeout, nothing still live
    assert ev["outcome"] not in ("wedged", "timeout", "stuck", "stalled",
                                 "wipe"), f"wedged/lost: {ev}"
    assert ev["battle_live"] is False, \
        f"fight() reported an UNRESOLVED battle: {ev}"
    assert frames < 40000, f"burned {frames} frames -- the wedge is back"
    # the refusal IS the contract (like the money guard's polarity): the
    # impossible switch was named and substituted, never driven into menus
    assert "impossible" in caplog.text and "trapped" in caplog.text, \
        f"switch refusal not surfaced: {caplog.text[-400:]}"
