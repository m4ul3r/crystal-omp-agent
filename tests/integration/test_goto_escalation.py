"""Integration: `goto` escalation on a lying collision grid + the
Pokecenter heal it used to break.

Historical bug: the Indigo Plateau Pokecenter nurse cell renders as wall
in the decoded grid while the approach cell is walkable -- every heal of
a whole session replan-stormed there (20 replans, no progress) until the
leg was hand-driven with step_hold. The fix: `goto` escalates BY ITSELF
to the savestate search (`explore_bfs`, which walks real geometry) when
the failure smells like wrong map data. A fake nav cannot reproduce a
grid that lies against the real ROM; this drives the real room.

Forked savestate: claude_saves/wren-pre-e4.state (boots inside
INDIGO_PLATEAU_POKECENTER_1F at (11, 9)).

Test scaffolding note: the party's HP is poked low THROUGH THE FORK ONLY
(never runtime code) so the heal has observable work to do.
"""

import pytest

pytestmark = pytest.mark.integration


def _damage_party(d):
    """Scaffold: set the first three party mons to 5 HP inside the fork."""
    sym = d.emu.sym
    bank, addr = sym["wPartyMon1HP"]
    stride = sym.offset("wPartyMon2", "wPartyMon1")
    for i in range(3):
        d.emu.write((bank, addr + i * stride), bytes([0x00, 0x05]))


def _finish_nurse_heal(d, max_rounds=14):
    """Answer YES and page the nurse flow through to a clean overworld."""
    d.press("A:6 .:40")                  # answer the YES/NO box
    for _ in range(max_rounds):
        d.emu.tick(60)
        if not d.textbox() and not d.menu_open():
            return True
        if d.menu_open():
            d.press("B:6 .:30")
        else:
            d.press("A:6 .:50")
    return False


def test_goto_nurse_cell_escalates_and_refuses_loudly(fork_driver):
    """goto(3, 8) on the lying grid must NOT storm silently: exactly the
    escalatable failure class fires ONE savestate search and comes back
    loud (last_goto_reason), never a silent False."""
    d = fork_driver("wren-pre-e4")
    calls = []
    orig = d.explore_bfs

    def spy(*a, **k):
        calls.append(a)
        return orig(*a, **k)

    d.explore_bfs = spy
    ok = d.goto(3, 8)
    # The nurse's own cell hosts an NPC behind a counter: standing on it
    # is NOT achievable even for the savestate search, so a loud refusal
    # is the correct outcome. What must never return is the silent storm.
    assert not ok, "goto onto the occupied nurse cell should be refused"
    assert calls, "escalation-to-savestate-search path was NOT exercised"
    assert d.last_goto_reason, "failure must be loud (last_goto_reason)"
    assert "replan-storm" in d.last_goto_reason


def test_heal_completes_at_indigo_plateau_pokecenter(fork_driver):
    """The heal itself completes: talk_to reaches the nurse across the
    same lying grid, YES heals the damaged party to full, and the flow
    ends on a clean interactable overworld (no menu eating input,
    gotcha 7)."""
    d = fork_driver("wren-pre-e4")
    _damage_party(d)
    hp0 = [(m["hp"], m["max_hp"]) for m in d.observe()["party"]]
    assert any(h < mx for h, mx in hp0), "scaffolding damage did not land"
    assert d.talk_to(3, 8), f"nurse unreachable: {d.last_goto_reason}"
    assert _finish_nurse_heal(d), "nurse dialog never returned to field"
    hp1 = [(m["hp"], m["max_hp"]) for m in d.observe()["party"]]
    assert all(h == mx for h, mx in hp1), \
        f"heal did not complete: {hp1}"
    assert not d.menu_open() and not d.textbox(), \
        "open UI left behind after heal (gotcha 7)"
