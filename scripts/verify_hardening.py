#!/usr/bin/env python3
"""Live verification of the hardening work, on a FORK (never a milestone).

Steps 3-5 of the plan: effective accuracy under a poked evasion stage,
paralysis turn-loss, teach_tm's pre-flight refusal, and goto's escalation
on the Indigo Plateau Pokecenter cell whose decoded grid is a lie.

The emu.write calls here are test scaffolding INTO A FORK -- never
harness runtime code. Accuracy/evasion stages are read live by CheckHit,
so poking the byte is faithful (unlike attack/speed stages, which the
engine bakes into the *Stat words when they are applied).
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.basicConfig(level=logging.INFO, format="%(message)s")

from trek import Driver          # noqa: E402


def battle_check():
    d = Driver("claude_saves/acc-check.state")
    print("boot:", d.map_name(), d.pos()[2:], "| lead",
          d.lead()["nickname"], "L%d" % d.lead()["level"])
    print("money:", d.observe()["money"])
    d.travel("ROUTE_39")
    print("on:", d.map_name(), d.pos()[2:])
    # grass cells straight out of the decoded collision grid (0x14 long /
    # 0x18 tall grass) -- Route 39's patch is the (4..5, 20..27) column,
    # well clear of the map seam a random walk otherwise warps through
    grid = d.nav.grid("ROUTE_39")
    grass = [(x, y) for y, row in enumerate(grid)
             for x, c in enumerate(row) if c in (0x14, 0x18)]
    print("grass cells found:", len(grass))
    box = None
    if grass:
        xs = [p[0] for p in grass]
        ys = [p[1] for p in grass]
        box = (min(xs), max(xs), min(ys), max(ys))
        print("box:", box)
        d.goto(grass[0][0], grass[0][1], "into the grass")
        print("standing on:", d.pos()[2:])
    for attempt in range(8):
        res = d.pace(40, box=box)
        print("pace:", res)
        if res["stopped"] == "battle":
            break
    else:
        print("NO BATTLE -- cannot run the live accuracy check")
        return d
    # the encounter hook fires BEFORE the engine fills wBattleMon* (the
    # "Wild X appeared!" page is still up), where outlook() correctly
    # refuses to invent a matchup: page through to the action menu first
    from crystalagent.menus import battle_menu_up
    a = None
    for _ in range(20):
        if battle_menu_up(d.emu.screen_text()):
            a = d.outlook()
            if a:
                break
        d.press("A:4 .:30")
    assert a, "battle mon blocks never populated"
    print("\n-- neutral stages --")
    print(d.tactics.explain(a))
    neutral = {m["move"]: (m["accuracy"], m["effective_accuracy"])
               for m in a["moves"]}
    print("listed vs effective:", neutral)
    assert all(l == e for l, e in neutral.values()), neutral

    print("\n-- enemy at +2 evasion (wEnemyEvaLevel = 9) --")
    d.emu.write("wEnemyEvaLevel", 9)
    a = d.outlook()
    print(d.tactics.explain(a))
    stacked = {m["move"]: (m["accuracy"], m["effective_accuracy"])
               for m in a["moves"]}
    print("listed vs effective:", stacked)
    hundreds = [v for v in stacked.values() if v[0] == 100]
    assert hundreds, stacked
    assert all(v[1] == 60 for v in hundreds), stacked

    print("\n-- my mon paralysed (wBattleMonStatus |= PAR) --")
    d.emu.write("wBattleMonStatus", 0x40)
    a = d.outlook()
    print("my_status:", a["my_status"], "turn_loss:", a["turn_loss"])
    assert a["my_status"] == ["PAR"], a["my_status"]
    assert a["turn_loss"] == 0.25, a["turn_loss"]
    print("recommend:", d.tactics.recommend(a, d.battle_frame()))

    print("\n-- a status cure when nothing lethal is incoming --")
    # nothing this RATTATA-tier wild does is lethal and the KO branch
    # fires first, so ask recommend() about the cure directly
    par = d.tactics.status_bits["PAR"]
    print("cure branch:", d.tactics.recommend(
        {"me": dict(a["me"], hp=a["me"]["max_hp"], status=par),
         "enemy": a["enemy"], "moves": [], "threats": [],
         "their_best": None, "i_can_ko": False, "faster": True,
         "my_status": ["PAR"], "turn_loss": 0.25},
        d.battle_frame()))

    print("\n-- teach_tm --")
    d.emu.write("wBattleMonStatus", 0)
    stock = d.tmhm_stock()
    print("TMs held:", stock, "->",
          {t: d.tmhm_moves()[t] for t in stock})
    lead = d.lead()["nickname"]
    print("lead moves:", [m["name"] for m in d.lead()["moves"]])
    ui_before = d.emu.screen_text()
    print("teach_tm(ZAP CANNON):", d.teach_tm("ZAP CANNON", lead),
          "|", d.last_tm_reason)
    assert d.emu.screen_text() == ui_before, "the UI must not have moved"
    learnset = d.species_tmhm()[d.lead()["name"]]
    nope = next((t for t in stock
                 if d.tmhm_moves()[t] not in learnset), None)
    if nope:
        print(f"teach_tm({nope}):", d.teach_tm(nope, lead),
              "|", d.last_tm_reason)
        assert d.last_tm_reason.startswith("cannot-learn")
        assert d.emu.screen_text() == ui_before, "the UI must not have moved"
    else:
        print("every held TM is learnable by the lead: no cannot-learn "
              "case available on this fork")
    return d


def nav_check():
    d = Driver("claude_saves/nav-check.state")
    print("boot:", d.map_name(), d.pos()[2:])
    d.travel("INDIGO_PLATEAU_POKECENTER_1F")
    print("on:", d.map_name(), d.pos()[2:])
    f0 = d.emu.frame
    ok = d.goto(3, 8)
    print(f"goto(3,8) -> {ok} reason={d.last_goto_reason} "
          f"frames={d.emu.frame - f0} pos={d.pos()[2:]}")
    f1 = d.emu.frame
    ok2 = d.goto(16, 1)
    print(f"goto(16,1) -> {ok2} reason={d.last_goto_reason} "
          f"frames={d.emu.frame - f1} pos={d.pos()[2:]}")
    return d


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("all", "battle"):
        battle_check()
    if which in ("all", "nav"):
        nav_check()
