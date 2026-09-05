#!/usr/bin/env python
"""Fight the Elite Four and the Champion, from the League's front door.

The gauntlet is eleven maps, alternating room and corridor, and every warp is
taken by ENTERING its tile -- standing on one does nothing (the arrival cell of
every door is itself a warp, which is what makes `step_off` necessary).

Two facts about this building that the rest of the run does not have:

* `EverGrandeCity_PokemonLeague` has a **NURSE at (3,2)** and a mart clerk at
  (16,2). That matters more than it sounds: Ever Grande's other Pokemon Center
  is at (27,48) on the LOWER plateau, which cannot be reached from up here
  (row 37 of the city is solid wall), so without this nurse the party arrives
  from Victory Road however the dungeon left it. It arrived twice with the
  level-100 lead at 0 HP.
* There is no healing BETWEEN the five battles. Whatever the party has when it
  leaves this hall is what it fights Steven with.

The lead's real constraint is PP, not HP. Crossing Victory Road with
`on_battle="fight"` spends Surf on wild Golbats and then Struggles the level
100 to death, so the crossing flees wilds and saves its moves for the League.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokeagent.trek import Driver, TravelInterrupted  # noqa: E402
from pokeagent.live import LiveFeed  # noqa: E402
from pokeagent.watchdog import StallWatch  # noqa: E402
# `step_off` is used at the room exit but was never imported: every pass
# crashed with `NameError: name 'step_off' is not defined` one line after
# walking through a door, so the gauntlet advanced exactly ONE room per run
# and exited 1. Its own module docstring already says the function is what
# makes these doors safe -- "every door is itself a warp, which is what makes
# `step_off` necessary" -- it just was not reachable from here.
from league_chain import step_off  # noqa: E402

log = logging.getLogger("e4")

#: (map, trainer cell or None, exit warp). The exit of each room is the door
#: at its top; corridors just pass through.
GAUNTLET = [
    ("EverGrandeCity_PokemonLeague", None, (9, 1)),
    ("EverGrandeCity_Corridor5", None, (5, 2)),
    ("EverGrandeCity_SidneysRoom", (6, 5), (6, 2)),
    ("EverGrandeCity_Corridor1", None, (5, 2)),
    ("EverGrandeCity_PhoebesRoom", (6, 5), (6, 2)),
    ("EverGrandeCity_Corridor2", None, (5, 2)),
    ("EverGrandeCity_GlaciasRoom", (6, 5), (6, 2)),
    ("EverGrandeCity_Corridor3", None, (5, 2)),
    ("EverGrandeCity_DrakesRoom", (6, 5), (6, 2)),
    ("EverGrandeCity_Corridor4", None, (5, 2)),
    ("EverGrandeCity_ChampionsRoom", (6, 5), (6, 2)),
]



def laggard_slot(d, ceiling: int = 95):
    """The living party member furthest behind, ignoring the L100 lead."""
    best, idx = None, None
    for i, m in enumerate(d.state.party()):
        if (m.hp or 0) <= 0 or m.level >= ceiling:
            continue
        if best is None or m.level < best.level:
            best, idx = m, i
    return idx, best


def training_policy(d, inner):
    """Front the laggard on turn one, then hand over to `inner`.

    A level-100 lead earns NOTHING from a kill, so every Elite Four mon it
    knocks out is experience thrown away -- and the five members that cannot
    survive the gauntlet are exactly the ones that need it. Gen 3 splits
    experience between PARTICIPANTS, so sending the laggard out first and
    letting the lead finish pays BOTH.

    Worth more than it sounds: an Elite Four mon is L46-57, against a bench of
    L48-54. Wild grinding on Victory Road 1F measured about one level per
    seven minutes; a single gauntlet lap is five trainers deep.

    The switched flag is per BATTLE (rebuilt by the caller each room), not per
    run -- holding it across battles switches once and never again.
    """
    done = {"switched": False, "returned": False}

    def policy(frame):
        me = (frame or {}).get("me") or {}
        active = me.get("nickname") or me.get("name")
        idx, mon = laggard_slot(d)
        # IN FOR ONE TURN, THEN OUT. Participation is all Gen 3 asks for, so
        # the laggard only needs to be on the field when the KO lands -- it
        # does NOT need to survive the fight. Leaving it in got it killed
        # immediately and ended the lap two battles deep, which cost more
        # experience than fronting it earned.
        if mon is not None and not done["switched"] and active != mon.nickname:
            done["switched"] = True
            return ("switch", idx)
        if done["switched"] and not done["returned"]:
            done["returned"] = True
            lead = d.state.party()[0] if d.state.party() else None
            if lead is not None and (lead.hp or 0) > 0 \
                    and active != lead.nickname:
                return ("switch", 0)
        return inner(frame)

    return policy


def protect_bench_policy(d, inner):
    """`inner`, but it may never switch. Healing and items still pass.

    The EXP. SHARE pays a benched holder half of every knockout without it
    ever being sent out -- but `tactics.recommend` ranks "switch to a mon that
    RESISTS what is incoming" above damage, and a benched MARILL resists
    Water. So against Sidney's SHARPEDO the harness fronted the L25 holder
    itself: `T3 attack:2 ROLLOUT#2 | me 75->0`. A fainted mon is skipped by
    both the participant count and the exp loop
    (`battle_script_commands.c:3361-3364`, `:3436`), so that one switch threw
    away the entire lap's experience.

    Vetoing the switch and substituting the best available attack keeps the
    holder on the bench where it earns, and leaves the healing and item
    branches of the recommendation untouched.
    """

    def best_attack(frame):
        best, score = 0, -1.0
        for i, mv in enumerate((frame or {}).get("moves") or []):
            if not mv or not mv.get("pp"):
                continue
            s = (mv.get("power") or 0) * (mv.get("effect_mult") or 1.0)
            if s > score:
                best, score = i, s
        return ("attack", best)

    def policy(frame):
        action = inner(frame)
        if isinstance(action, tuple) and action and action[0] == "switch":
            log.info("[bench] vetoed %s -- the holder stays benched", action)
            return best_attack(frame)
        return action

    return policy


def lead_out_policy(d, inner):
    """LEAD with the mon that needs experience, then switch it straight out.

    This is the opposite of `training_policy` and strictly better for a
    low-level target. Both rely on the same ROM rule -- exp goes to every mon
    in `sentIn` that is still alive, and a level-100 earns nothing
    (`battle_script_commands.c:3414-3441`) -- but they differ in WHO eats the
    incoming attack:

      * `training_policy` switches the laggard IN, so the opponent's attack
        that turn lands on the laggard. Against Sidney's L46-55 a L25 target
        is knocked out, and `viaSentIn` skips fainted mons
        (`:3361-3364`), so it earns NOTHING. Measured tonight: the bench went
        `me 58->0` and the gauntlet ended two rooms deep.
      * Leading with the target and switching OUT on turn one means the
        opponent hits the INCOMING escort instead. The target is already in
        `sentIn` because it started the battle, it never takes a hit, and it
        stays alive -- so it banks a full participant share of every KO.

    Exp per KO is `expYield * level / 7 / viaSentIn` (`:3381-3396`), so with
    the target plus one escort alive that is half of every Elite Four mon.
    The target also leads the NEXT battle automatically, because the engine
    sends out the first unfainted party member.
    """

    def policy(frame):
        # ASK THE ENGINE WHO IS OUT, NOT THE FRAME. The first policy call of a
        # battle happens before `state.battle_ready()` populates the battle mon
        # block, so `frame["me"]["nickname"]` is empty on exactly the turn that
        # matters. Comparing it to the target's nickname therefore never
        # matched, the switch never fired, and Sidney's L46 MIGHTYENA knocked
        # out the L25 MARILL on turn one -- the log opens at T2 with
        # "replacement slot 1" and there is no T1 at all.
        #
        # `gBattlerPartyIndexes[0]` is the party slot of my active battler and
        # is valid immediately (pokeagent/battle.py:429-430).
        party = d.state.party()
        if not party:
            return inner(frame)
        try:
            active_slot = d.emu.u16(("gBattlerPartyIndexes", 0))
        except Exception as exc:  # noqa: BLE001
            log.info("[lead-out] cannot read gBattlerPartyIndexes: %s",
                     str(exc)[:60])
            return inner(frame)
        log.info("[lead-out] consulted: active_slot=%s hp0=%s",
                 active_slot, party[0].hp if party else "?")
        if active_slot != 0:
            return inner(frame)          # the escort is out; fight normally
        target = party[0]
        if (target.level or 0) >= 95 or (target.hp or 0) <= 0:
            return inner(frame)          # nothing worth protecting
        escort, best = None, -1
        for i, m in enumerate(party):
            if i == 0 or (m.hp or 0) <= 0 or m.is_egg:
                continue
            if (m.level or 0) > best:
                escort, best = i, (m.level or 0)
        if escort is None:
            return inner(frame)
        log.info("[lead-out] slot0 %s L%s is out -- switching to slot %d",
                 target.nickname, target.level, escort)
        return ("switch", escort)

    return policy


def champion_policy(d):
    """The harness's own tactics, but healing earlier.

    A hand-rolled policy was tried here and it was strictly worse: it read PP
    off `frame["moves"]`, which does not carry the live counts, so it kept
    asking for HYDRO PUMP (5 PP) after it was spent. The harness fell back to
    SPIT UP -- a no-damage move without Stockpile -- and the level-100 lead
    lost 158 HP to a Walrein it never scratched, over ten turns.

    `tactics.recommend` already ranks a certain KO first, then healing, then a
    status cure, then a resist-switch, then best expected damage, and it reads
    PP and the live accuracy/evasion stages properly. The only thing worth
    changing for a five-battle gauntlet with no healing between rooms is WHEN
    it heals: the default 0.35 is one turn from dead against a level-55
    dragon, and an item costs exactly the turn a faint would.
    """

    def policy(frame):
        try:
            analysis = d.outlook()
            if analysis is None:
                return None
            action, why = d.tactics.recommend(analysis, heal_at=0.60)
            return action
        except Exception:  # noqa: BLE001 - never lose a battle to the policy
            return None

    return policy



def walk_door(d, cell, mv_pref=("U", "D", "L", "R")) -> bool:
    """Enter the warp at `cell` by stepping onto it from a walkable neighbour.

    Standing on a warp never fires it; only the step that ENTERS it does.
    take_warp's own routing stalls inside these rooms, so the last step is
    driven by hand.
    """
    name = d.map_name()
    order = {"U": (0, 1), "D": (0, -1), "L": (1, 0), "R": (-1, 0)}
    for mv in mv_pref:
        dx, dy = order[mv]
        stand = (cell[0] + dx, cell[1] + dy)
        c = d.nav.cell(name, *stand)
        if c is None or c.collision:
            continue
        guard(d, d.goto, stand[0], stand[1], on_battle="fight")
        settle(d)
        if d.pos() != stand:
            continue
        guard(d, d.step_dir, mv)
        settle(d)
        if d.map_name() != name:
            return True
    return False


def resupply(d, out) -> bool:
    """Walk back to the hall, spend the prize money, and return.

    The economy only looks broken until you watch the money: each leader pays
    as they fall, so the run reaches Drake holding ~16,000 -- and the mart is
    four maps behind it. Then the whiteout takes half. Six Hyper Potions
    bought before Sidney are gone by Glacia, and Drake is fought with nothing.

    Beaten rooms stay open (`call_if_set FLAG_DEFEATED_ELITE_4_*` reopens both
    doors) and the leaders do NOT reset while we stay inside the building, so
    the trip down and back is free. That turns the money into about thirteen
    Hyper Potions at the moment they are needed.
    """
    from pokeagent.mart import Mart

    money = d.state.money()
    if money < 1200:
        return False
    log.info("  resupply: walking back to the hall with %d", money)
    for _ in range(12):
        name = d.map_name()
        if name == "EverGrandeCity_PokemonLeague":
            break
        warps = [(w.x, w.y) for w in d.nav.info(name).warps]
        if not warps or not walk_door(d, max(warps, key=lambda c: c[1]),
                                      ("U", "L", "R", "D")):
            log.info("  resupply: stuck in %s at %s", name, d.pos())
            return False
    if d.map_name() != "EverGrandeCity_PokemonLeague":
        return False
    m = Mart(d)
    guard(d, d.talk_to, 16, 2)
    for _ in range(4):
        d.advance_scene(20000)
        if m.is_open():
            break
    if m.is_open():
        n = d.state.money() // 1200
        if n and m.buy("HYPER POTION", min(n, 60)):
            log.info("  resupply: bought %d Hyper Potions", min(n, 60))
        m.leave()
    settle(d)
    guard(d, d.talk_to, *NURSE)
    for _ in range(4):
        d.advance_scene(60000)
        d.close_menus()
    d.save(out)
    log.info("  resupply: %s, party %s", (d.state.bag() or {}).get("items"),
             hp_line(d))
    return True


def patch_up(d, out) -> None:
    """Revive and heal the party before the door that starts the Champion.

    `ChampionsRoom_EventScript_EnterRoom` runs `goto ..._EventScript_Steven`,
    so the Champion fight begins the INSTANT the room is entered -- there is
    no corridor, no pause, and no chance to open the bag once inside. Walking
    in with five fainted and a single mon on 133 HP is a loss before a button
    is pressed, and that is exactly how the last two completed gauntlets ended:
    Sidney, Phoebe, Glacia and Drake all beaten, then a whiteout back to
    (18,6) inside the same step.

    The bag has been carrying four REVIVES through every one of those runs
    unused, because the in-battle policy only ever reaches for potions.
    """
    from pokeagent.teaching import Teacher

    t = Teacher(d)
    bag = (d.state.bag() or {}).get("items") or {}
    fainted = [m for m in d.state.party() if (m.hp or 0) <= 0]
    revives = bag.get("REVIVE", 0)
    if fainted and revives:
        log.info("  patching up: %d fainted, %d REVIVE", len(fainted), revives)
        for m in fainted[:revives]:
            who = m.nickname
            ok = t.use_on_mon("REVIVE", who)
            log.info("    revive %-10s -> %s", who, ok)
    # Then top everyone up with whatever healing is left.
    bag = (d.state.bag() or {}).get("items") or {}
    for name in ("HYPER POTION", "MAX POTION", "SUPER POTION"):
        have = bag.get(name, 0)
        if not have:
            continue
        for m in d.state.party():
            if have <= 0:
                break
            hp, mx = (m.hp or 0), (m.max_hp or 1)
            if hp <= 0 or hp >= mx:
                continue
            if t.use_on_mon(name, m.nickname):
                have -= 1
    log.info("  patched up: %s", hp_line(d))
    d.save(out)

def settle(d) -> None:
    for _ in range(10):
        if d.in_battle():
            d.fight()
            d.advance_scene(60000)
        elif d.scene_active():
            d.advance_scene(60000)
            d.close_menus()
        else:
            return


def guard(d, fn, *a, **k):
    for _ in range(4):
        try:
            return fn(*a, **k)
        except TravelInterrupted:
            settle(d)
    return None


def hp_line(d) -> str:
    return " ".join("%s %d/%d" % (m.nickname, m.hp, m.max_hp)
                    for m in d.state.party())


def alive(d) -> int:
    return sum(1 for m in d.state.party() if m.hp > 0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    # PUBLISH WHERE THE WIDGET IS LOOKING. A Driver left to itself publishes
    # to a feed named after its STATE FILE, so a run on saves/gauntlet2.state
    # wrote live/gauntlet2.png while the desktop widget watched live/default.*
    # and showed a frame from 102 minutes earlier -- reported, correctly, as
    # "last frame was over 6000 seconds ago" while the game was running fine.
    ap.add_argument("--feed", default="default")
    ap.add_argument("--train", action="store_true",
                    help="front the laggard each battle so the bench levels "
                         "off the Elite Four instead of the L100 wasting it")
    ap.add_argument("--protect-bench", action="store_true",
                    help="never switch: keeps a benched EXP. SHARE holder out "
                         "of the ring so it survives to collect")
    ap.add_argument("--front-lead", action="store_true",
                    help="lead with party slot 0 and switch it out turn one, "
                         "so a low-level target banks participation exp "
                         "without ever taking a hit")
    ap.add_argument("--out")
    ap.add_argument("--minutes", type=float, default=90.0)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    out = a.out or a.state
    stop = time.time() + a.minutes * 60.0

    d = Driver(a.state)
    if a.feed:
        # The Driver auto-attaches a feed named after its STATE FILE, and the
        # emulator allows exactly one tick observer -- so publishing where the
        # widget is looking means REPLACING that feed, not adding to it.
        if getattr(d.emu, "observer", None) is not None:
            d.emu.observer = None
        LiveFeed(a.feed).attach(d)
    base_policy = champion_policy(d)
    if a.protect_bench:
        log.info("protect-bench mode: switching vetoed, holder stays benched")
        d.battle_policy = protect_bench_policy(d, base_policy)
    elif a.front_lead:
        log.info("lead-out mode: slot 0 fronts for participation exp")
        d.battle_policy = lead_out_policy(d, base_policy)
    elif a.train:
        d.battle_policy = training_policy(d, base_policy)
    else:
        d.battle_policy = base_policy
    watch = StallWatch(feed_name=a.feed or "default", log=log,
                       idle_s=300.0).start()
    log.info("START %s %s", d.map_name(), d.pos())
    log.info("party %s", hp_line(d))

    # HEAL FIRST. This nurse is the only one reachable from the plateau.
    # `d.heal()` does not find her -- it looks the nurse up through the map's
    # sprite table and answers False here -- but she is an ordinary NPC at
    # (3,2) and talking to her restores HP *and* PP for the whole party.
    if d.map_name() == "EverGrandeCity_PokemonLeague":
        log.info("healing at the League nurse -> %s",
                 guard(d, d.talk_to, 3, 2))
        for _ in range(4):
            d.advance_scene(60000)
            d.close_menus()
        log.info("party %s", hp_line(d))
        d.save(out)
        # THE GUARDS STAND ON THE DOORWAY. (9,2) and (10,2) are occupied by
        # the two badge-checkers, so the door at (9,1) is unreachable until
        # one of them is spoken to; they then step aside to (8,2)/(11,2).
        log.info("badge check -> %s", guard(d, d.talk_to, 9, 2))
        d.advance_scene(60000)
        d.close_menus()
        d.save(out)

    for want, trainer, exit_cell in GAUNTLET:
        if time.time() > stop:
            log.info("out of time at %s", d.map_name())
            break
        if d.map_name() != want:
            log.info("SKIP %s -- standing on %s", want, d.map_name())
            continue
        log.info("=== %s ===", want)
        if want == "EverGrandeCity_Corridor4":
            # THE LAST CORRIDOR. Steven's fight starts on room entry, so this
            # is the final chance to open the bag.
            patch_up(d, out)
        if watch.stalled:
            log.info("  %s -- giving up this attempt", watch.detail)
            d.save(out)
            return 1
        if trainer is not None:
            if a.train:
                # Fresh per room: the switched flag must not survive a battle.
                d.battle_policy = training_policy(d, base_policy)
                idx, who = laggard_slot(d)
                if who is not None:
                    log.info("  training %s L%d (slot %d)", who.nickname,
                             who.level, idx)
            log.info("  engaging the trainer at %s", trainer)
            guard(d, d.talk_to, *trainer)
            settle(d)
            # STEP OFF THE TRAINER'S FACE. An NPC you are still facing
            # re-enters dialogue on the next A, so every later press -- the
            # ones meant to page the exit warp -- re-opened Glacia's victory
            # speech instead. Measured: pinned at (6,6) facing U for half an
            # hour with the frame counter climbing, one room from Drake.
            for mv in ("D", "L", "R"):
                if guard(d, d.step_dir, mv):
                    break
            settle(d)
            log.info("  after the battle: %s", hp_line(d))
            d.save(out)
            # NO MID-GAUNTLET RESUPPLY IS POSSIBLE, and the reason is in the
            # ROM rather than in our routing. `elite_four.inc:24-29`:
            #
            #     setmetatile 5,12 / 6,12 / 7,12  EntryDoor_ClosedTop,    1
            #     setmetatile 5,13 / 6,13 / 7,13  EntryDoor_ClosedBottom, 1
            #
            # Walking into a leader's room turns all six tiles of the way back
            # IMPASSABLE at runtime. The static grid still reads them walkable,
            # which is precisely why every attempt to walk out stopped at
            # (7,11) -- one row short of a door that no longer exists. The
            # money the leaders pay (about 16,000 by Drake) therefore cannot
            # be spent until the run is over.
            #
            # Sync the live grid so nav stops planning through those tiles and
            # wasting a goto budget on them.
            try:
                drift = d.sync_grid()
                if drift:
                    log.info("  synced %d changed cells (the door closed "
                             "behind us)", drift)
            except Exception:  # noqa: BLE001 - a debug aid, never fatal
                pass
            if alive(d) == 0:
                log.info("WHITED OUT at %s", d.map_name())
                d.save(out)
                return 1
        # LEAVE BY THE DOOR AT THE TOP -- AND ONLY THAT DOOR.
        #
        # `take_warp` was catastrophic here. Every leg arrives STANDING ON the
        # door it came through, and take_warp steps off and re-enters the
        # nearest warp -- which is the one underfoot. So after beating Drake
        # the run took the (5,33) door straight back into his room, wandered
        # out of the building, and RESET the whole gauntlet: leaving the
        # League clears VAR_ELITE_4_STATE, so four won fights were thrown away
        # one corridor from the Champion. Twice.
        #
        # walk_door goes to a walkable neighbour of the intended door and
        # steps onto it, which is also the only thing that fires a warp.
        for attempt in range(4):
            before = d.map_name()
            if walk_door(d, exit_cell, ("U", "L", "R", "D")):
                log.info("  -> %s %s", d.map_name(), d.pos())
                step_off(d, exit_cell)
                break
            log.info("  exit attempt %d still on %s %s", attempt, before,
                     d.pos())
        d.save(out)

    log.info("FINAL %s %s", d.map_name(), d.pos())
    log.info("party %s", hp_line(d))
    d.save(out)
    if d.map_name() == "EverGrandeCity_HallOfFame":
        log.info("*** HALL OF FAME ***")
        return 0
    return 0 if d.map_name() == "EverGrandeCity_ChampionsRoom" else 1


if __name__ == "__main__":
    raise SystemExit(main())
