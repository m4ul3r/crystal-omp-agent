#!/usr/bin/env python
"""BELDUM (Steven's house) and LILEEP (root fossil) -- the two gift dex entries.

BELDUM
------
`pret/data/maps/MossdeepCity_StevensHouse/events.inc:9` puts object 2, an
`OBJ_EVENT_GFX_ITEM_BALL`, at **(4, 3)** behind
`FLAG_HIDE_BELDUM_BALL_STEVENS_HOUSE`, wired to
`MossdeepCity_StevensHouse_EventScript_BeldumPokeball`. That script
(`scripts.inc:71-93`) is:

    lockall
    msgbox ...Text_TakeBallContainingBeldum, MSGBOX_YESNO
    compare VAR_RESULT, NO / goto_if_eq ...EventScript_LeaveBeldum
    getpartysize
    compare VAR_RESULT, 6
    goto_if_eq ...EventScript_GiveBeldum      @ the "no space" branch
    ...
    givemon SPECIES_BELDUM, 5, ITEM_NONE
    msgbox gText_NicknameReceivedPokemon, MSGBOX_YESNO
    setflag FLAG_HIDE_BELDUM_BALL_STEVENS_HOUSE
    setflag FLAG_RECEIVED_BELDUM

Three consequences this script is built around:

* The ball is only there after the Hall of Fame. `OnLoad` (`scripts.inc:7-13`)
  hides Steven's note while `FLAG_SYS_GAME_CLEAR` is clear, and the ball's own
  hide flag is cleared by the game-clear script.
* There are TWO yes/no boxes and they want OPPOSITE answers: YES to take the
  ball, NO to the nickname. In RSE B advances a text box *and* answers a
  yes/no with NO, so the sequence is: one deliberate `resolve_choice("YES")`,
  then B for absolutely everything after it. A blind A-loop lands on the
  naming keyboard, which is the failure `scripts/errands.py:365-385` records
  as having cost two dex entries.
* `getpartysize == 6` refuses the gift outright, so a slot is freed first.

LILEEP -- not obtainable on a save that has already taken a fossil
-----------------------------------------------------------------
`grep -rn ROOT_FOSSIL pret/data/maps` finds exactly three maps: Route111
(the item), Route114_FossilManiacsTunnel (dialogue only) and
RustboroCity_DevonCorp_2F (the regenerator). Sapphire has **no Mirage Tower
and no Desert Underpass** -- `ls pret/data/maps | grep -i 'mirage\\|underpass'`
is empty; both are Emerald additions. The two fossils simply lie on the
Route 111 desert floor:

    Route111/map.json:465-490
      OBJ_EVENT_GFX_FOSSIL (32,38) script ..._150023  FLAG_HIDE_ROOT_FOSSIL
      OBJ_EVENT_GFX_FOSSIL (33,38) script ..._150069  FLAG_HIDE_CLAW_FOSSIL

and `Route111/scripts.inc:50-66` shows taking EITHER sets BOTH hide flags and
`removeobject`s both. `grep -rn 'clearflag FLAG_HIDE_.*FOSSIL' pret` returns
nothing, so the choice is permanent: one cartridge, one fossil line.

So this script probes the flags before walking anywhere, and takes the ROOT
fossil at **(32,38)** -- note `scripts/errands.py:285` aims at (33,38), the
CLAW, which is what makes the check mandatory rather than polite.
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.dex import DexTarget  # noqa: E402
from pokeagent.menus import Menus  # noqa: E402
from pokeagent.naming import NamingScreen  # noqa: E402
from pokeagent.trek import Driver, TravelInterrupted  # noqa: E402

log = logging.getLogger("fossil_beldum")

#: MossdeepCity_StevensHouse/events.inc:9 -- object 2, the BELDUM ball.
BELDUM_BALL = (4, 3)
STEVENS_HOUSE = "MossdeepCity_StevensHouse"
#: MossdeepCity_StevensHouse/events.inc:14-15 -- the warps back out, and so
#: (3,6)/(4,6) on MossdeepCity is the doorway in.
STEVENS_DOOR = (3, 7)

#: Route111/map.json:465-477 -- object 34, FLAG_HIDE_ROOT_FOSSIL.
ROOT_FOSSIL = (32, 38)
#: RustboroCity_DevonCorp_2F/events.inc:12 -- object 5, the fossil scientist.
FOSSIL_SCIENTIST = (14, 8)
#: The floor's other four scientists; any of them runs `SetFossilReady` and
#: flips VAR_FOSSIL_RESURRECTION_STATE 1 -> 2 (scripts.inc:14-60).
COLLEAGUES = ((6, 5), (1, 5), (2, 6), (10, 5))

#: Species ids off `constants/species.h` are resolved live; these are the
#: NATIONAL dex numbers the dex bitfield is indexed by (dex.py:1342-1343).
NATDEX = {"LILEEP": 345, "CRADILY": 346, "ANORITH": 347, "ARMALDO": 348,
          "BELDUM": 374, "METANG": 375, "METAGROSS": 376}

FLY_HINT = {
    STEVENS_HOUSE: "MossdeepCity",
    "MossdeepCity": "MossdeepCity",
    "MossdeepCity_PokemonCenter_1F": "MossdeepCity",
    "Route111": "MauvilleCity",
    "RustboroCity_DevonCorp_2F": "RustboroCity",
    "RustboroCity_DevonCorp_1F": "RustboroCity",
    "RustboroCity": "RustboroCity",
    "RustboroCity_PokemonCenter_1F": "RustboroCity",
}


# ---- reading the world -------------------------------------------------

def caught_natdex(d, t):
    """The caught set, which is keyed by NATIONAL DEX NUMBER, not species id.

    Testing a species NAME or a species id against it is silently always
    False -- that is how a first pass here concluded ANORITH had never been
    received when it is sitting in box 4 as an ARMALDO.
    """
    return t.dex_flags(d.state)[0]


def has(d, t, name):
    return NATDEX[name] in caught_natdex(d, t)


def bag_has(d, name):
    for pocket in d.state.bag().values():
        if name in pocket:
            return True
    return False


def party_species(d):
    return [d.names.species(m.species) for m in d.state.party()]


def probe(d, t):
    """Everything that decides whether either gift is still available."""
    caught, seen = t.dex_flags(d.state)
    info = {
        "map": d.map_name(),
        "pos": d.pos(),
        "party": party_species(d),
        "party_size": len(d.state.party()),
        "dex_caught": len(caught),
        "game_clear": d.state.flag("FLAG_SYS_GAME_CLEAR"),
        "received_beldum": d.state.flag("FLAG_RECEIVED_BELDUM"),
        "beldum_ball_hidden":
            d.state.flag("FLAG_HIDE_BELDUM_BALL_STEVENS_HOUSE"),
        "received_fossil_mon": d.state.flag("FLAG_RECEIVED_FOSSIL_MON"),
        "root_fossil_hidden": d.state.flag("FLAG_HIDE_ROOT_FOSSIL"),
        "claw_fossil_hidden": d.state.flag("FLAG_HIDE_CLAW_FOSSIL"),
        "resurrection_state": d.state.var("VAR_FOSSIL_RESURRECTION_STATE"),
        "which_fossil_revived": d.state.var("VAR_WHICH_FOSSIL_REVIVED"),
        "bag_root_fossil": bag_has(d, "ROOT FOSSIL"),
        "bag_claw_fossil": bag_has(d, "CLAW FOSSIL"),
        "key_items": sorted(d.state.bag().get("key_items", {})),
    }
    for name, num in NATDEX.items():
        info[f"{name.lower()}_caught"] = num in caught
        info[f"{name.lower()}_seen"] = num in seen
    for k, v in info.items():
        log.info("  %-22s %s", k, v)
    return info


# ---- navigation --------------------------------------------------------

def goto_map(d, dest):
    """travel(), with a FLY first when the destination is across Hoenn."""
    if d.map_name() == dest:
        return True
    hint = FLY_HINT.get(dest)
    if hint and d.map_name() != hint:
        try:
            d.fly_to(hint)
            d.advance_scene(40_000)
        except Exception as exc:  # noqa: BLE001
            log.info("  fly_to(%s): %s", hint, str(exc)[:120])
    if d.map_name() == dest:
        return True
    for _ in range(3):
        try:
            if d.travel(dest, on_battle="fight"):
                return True
        except TravelInterrupted:
            d.fight()
            d.advance_scene(20_000)
            continue
        except Exception as exc:  # noqa: BLE001
            log.info("  travel(%s): %s", dest, str(exc)[:160])
            break
    return d.map_name() == dest


#: Never deposit the lead (index 0) and never the boat-critical mon.
KEEP = ("SEA BIRD",)


def make_room(d):
    """Free a party slot; both gift scripts refuse at six."""
    if len(d.state.party()) < 6:
        return True
    from pokeagent.storage import Storage

    here = d.map_name()
    centre = ("MossdeepCity_PokemonCenter_1F"
              if "Mossdeep" in here or "Steven" in here
              else "RustboroCity_PokemonCenter_1F")
    if not goto_map(d, centre) and not goto_map(
            d, "RustboroCity_PokemonCenter_1F"):
        log.info("  make_room: no Pokemon Centre reachable from %s", here)
        return False
    st = Storage(d)
    names = st.party_names()
    idx = next((i for i in range(len(names) - 1, 0, -1)
                if names[i] not in KEEP), None)
    if idx is None:
        log.info("  make_room: nothing in the party is depositable")
        return False
    if not st.deposit(idx):
        log.info("  make_room: deposit(%d) failed: %s", idx, st.last_reason)
        return False
    log.info("  make_room: deposited slot %d (%s); party is now %d",
             idx, names[idx], len(d.state.party()))
    return True


# ---- the gift dialog shape ---------------------------------------------

def yes_then_b(d, label, yes_boxes=1, presses=24):
    """Answer the first `yes_boxes` yes/no boxes YES, then press B forever.

    The script must already be running. `Menus.bounds() == (0,1)` is the
    live yes/no list (src/menu.c: index 0 = YES); reading it means the YES is
    a cursor-verified selection rather than a hopeful A. After that only B
    is sent, because B both advances a text box and answers the nickname
    yes/no with NO -- and a YES there opens the naming keyboard, which a
    savestate cannot be resumed out of.
    """
    menus = Menus(d.emu, d.state)
    ns = NamingScreen(d.emu, d.state)
    answered = 0
    for _ in range(presses):
        if ns.is_open():
            # Recovery only: complete the keyboard rather than fight it.
            log.info("  %s: keyboard slipped through; accepting %r",
                     label, ns.accept())
            d.advance_scene(40_000)
            continue
        if not d.scene_active() and not d.dialog_open():
            break
        if answered < yes_boxes:
            try:
                if menus.bounds() == (0, 1):
                    log.info("  %s: yes/no box up; taking YES", label)
                    if menus.resolve_choice("YES"):
                        answered += 1
                        d.settle(400)
                        continue
                    log.info("  %s: resolve_choice: %s", label,
                             menus.last_reason)
            except Exception as exc:  # noqa: BLE001
                log.info("  %s: bounds: %s", label, str(exc)[:80])
            # Not the box yet -- keep the text moving with B, which cannot
            # answer YES to anything.
            d.emu.run_sequence("B:6 .:70")
            d.settle(300)
            continue
        d.emu.run_sequence("B:6 .:70")
        d.settle(300)
    d.advance_scene(40_000)
    if ns.is_open():
        ns.accept()
        d.advance_scene(40_000)
    return answered >= yes_boxes


def take_beldum(d, t) -> bool:
    if has(d, t, "BELDUM"):
        log.info("beldum: already registered")
        return True
    if not d.state.flag("FLAG_SYS_GAME_CLEAR"):
        log.info("beldum: FLAG_SYS_GAME_CLEAR clear -- the ball is not there")
        return False
    if d.state.flag("FLAG_HIDE_BELDUM_BALL_STEVENS_HOUSE"):
        log.info("beldum: FLAG_HIDE_BELDUM_BALL_STEVENS_HOUSE is SET -- the "
                 "ball has already been taken on this line")
        return False
    if not make_room(d):
        return False
    if not goto_map(d, STEVENS_HOUSE):
        log.info("beldum: could not reach %s (at %s %s)",
                 STEVENS_HOUSE, d.map_name(), d.pos())
        return False
    before = caught_natdex(d, t)
    ok = False
    # `render_map` on this layout: (4,4) and (5,4) are the TABLE (wall), so
    # the ball at (4,3) is only reachable from (4,2) facing down or (3,3)
    # facing right. (5,3) is the HM08 DIVE ball -- approaching from the north
    # cannot hit it by accident.
    for stand, face in (((4, 2), "D"), ((3, 3), "R")):
        if d.pos() != stand and not d.goto(*stand, map_name=STEVENS_HOUSE):
            log.info("  beldum: cannot stand on %s", (stand,))
            continue
        d.emu.run_sequence(f"{face}:8 .:30")
        d.settle(200)
        log.info("  beldum: pressing A at %s facing %s at the ball %s",
                 stand, face, BELDUM_BALL)
        d.emu.run_sequence("A:6 .:60")
        d.settle(400)
        if not (d.scene_active() or d.dialog_open()):
            log.info("  beldum: no script answered from %s", stand)
            continue
        ok = yes_then_b(d, "beldum", yes_boxes=1)
        break
    now = caught_natdex(d, t)
    log.info("beldum: yes_taken=%s flag=%s new_natdex=%s party=%s",
             ok, d.state.flag("FLAG_RECEIVED_BELDUM"),
             sorted(now - before), party_species(d))
    return NATDEX["BELDUM"] in now


def take_root_fossil(d, t) -> bool:
    """Pick the ROOT FOSSIL -- (32,38), NOT the claw at (33,38)."""
    if bag_has(d, "ROOT FOSSIL"):
        log.info("fossil: already in the bag")
        return True
    if d.state.flag("FLAG_HIDE_ROOT_FOSSIL"):
        log.info("fossil: BLOCKED -- FLAG_HIDE_ROOT_FOSSIL is SET. "
                 "Route111/scripts.inc:55-58 sets BOTH fossil hide flags when "
                 "either is taken; no clearflag exists in the Sapphire data "
                 "and there is no Mirage Tower / Desert Underpass map. "
                 "VAR_WHICH_FOSSIL_REVIVED=%d (2 = claw/ANORITH).",
                 d.state.var("VAR_WHICH_FOSSIL_REVIVED"))
        return False
    if not bag_has(d, "GO-GOGGLES"):
        log.info("fossil: no GO-GOGGLES; the desert gate walks you back")
        return False
    if not goto_map(d, "Route111"):
        log.info("fossil: could not reach Route111 (at %s %s)",
                 d.map_name(), d.pos())
        return False
    d.nav.surfing = True
    # nav.blocked carries every coord_event; Route 111's are the desert gates,
    # whose only guard is `checkitem ITEM_GO_GOGGLES` (scripts.inc:162-181).
    # Holding the goggles they do nothing, and blocking them severs the map at
    # y=61 -- same clearing scripts/errands.py:275-290 does.
    if d.nav.blocked.get("Route111"):
        log.info("fossil: clearing %d Go-Goggles gate cells",
                 len(d.nav.blocked["Route111"]))
        d.nav.blocked["Route111"] = set()
        try:
            d.nav._reach_cache.clear()
        except Exception:  # noqa: BLE001
            pass
    for stand, face in (((31, 38), "R"), ((32, 37), "D"), ((32, 39), "U")):
        try:
            if not d.goto(*stand, on_battle="fight"):
                continue
        except TravelInterrupted:
            d.fight()
            d.advance_scene(20_000)
            continue
        except Exception as exc:  # noqa: BLE001
            log.info("  goto%s: %s", stand, str(exc)[:100])
            continue
        d.emu.run_sequence(f"{face}:8 .:30")
        d.settle(200)
        log.info("  fossil: pressing A at %s facing %s at %s",
                 stand, face, ROOT_FOSSIL)
        d.emu.run_sequence("A:6 .:60")
        d.settle(400)
        yes_then_b(d, "root fossil", yes_boxes=1)
        if bag_has(d, "ROOT FOSSIL"):
            break
    got = bag_has(d, "ROOT FOSSIL")
    log.info("fossil: root=%s claw=%s", got, bag_has(d, "CLAW FOSSIL"))
    return got


def revive_lileep(d, t) -> bool:
    if has(d, t, "LILEEP"):
        log.info("revive: LILEEP already registered")
        return True
    state = d.state.var("VAR_FOSSIL_RESURRECTION_STATE")
    which = d.state.var("VAR_WHICH_FOSSIL_REVIVED")
    if state == 0 and not bag_has(d, "ROOT FOSSIL"):
        log.info("revive: BLOCKED -- no ROOT FOSSIL in the bag and the "
                 "regenerator is empty (state=0, which=%d)", which)
        return False
    if state and which == 2:
        log.info("revive: the regenerator holds the CLAW fossil (ANORITH), "
                 "not the root")
        return False
    if not make_room(d):
        return False
    if not goto_map(d, "RustboroCity_DevonCorp_2F"):
        log.info("revive: could not reach Devon 2F (at %s %s)",
                 d.map_name(), d.pos())
        return False
    before = caught_natdex(d, t)
    if d.state.var("VAR_FOSSIL_RESURRECTION_STATE") == 0:
        if d.talk_to(*FOSSIL_SCIENTIST):
            yes_then_b(d, "hand over", yes_boxes=1)
        log.info("revive: state=%d which=%d",
                 d.state.var("VAR_FOSSIL_RESURRECTION_STATE"),
                 d.state.var("VAR_WHICH_FOSSIL_REVIVED"))
    if d.state.var("VAR_FOSSIL_RESURRECTION_STATE") == 1:
        for cell in COLLEAGUES:
            try:
                if not d.talk_to(*cell):
                    continue
                yes_then_b(d, "colleague", yes_boxes=0, presses=8)
            except Exception:  # noqa: BLE001
                continue
            if d.state.var("VAR_FOSSIL_RESURRECTION_STATE") == 2:
                break
    log.info("revive: resurrection state is now %d",
             d.state.var("VAR_FOSSIL_RESURRECTION_STATE"))
    # Collecting has NO yes box, only the nickname one -- so B for everything.
    if d.talk_to(*FOSSIL_SCIENTIST):
        yes_then_b(d, "collect", yes_boxes=0, presses=20)
    now = caught_natdex(d, t)
    log.info("revive: new_natdex=%s party=%s",
             sorted(now - before), party_species(d))
    return NATDEX["LILEEP"] in now


LEGS = {
    "probe": lambda d, t: bool(probe(d, t)),
    "beldum": take_beldum,
    "fossil": take_root_fossil,
    "revive": revive_lileep,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out", default=None, help="where to bank progress")
    ap.add_argument("--legs", default="probe,beldum,fossil,revive")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if "line3" in a.state or "milestone" in a.state:
        raise SystemExit("refusing to drive a canonical save: fork it first")

    out = a.out or a.state
    d = Driver(a.state)
    if d.at_title():
        log.info("boot: title screen; resuming")
        d.resume_from_title()
    d.advance_scene(40_000)
    d.nav.surfing = True
    if hasattr(d.nav, "waterfall"):
        d.nav.waterfall = True
    t = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    log.info("start %s %s | %s", d.map_name(), d.pos(), t.summary(d.state))

    for name in [s.strip() for s in a.legs.split(",") if s.strip()]:
        fn = LEGS.get(name)
        if fn is None:
            log.info("no such leg: %s", name)
            continue
        log.info("=== %s ===", name)
        try:
            log.info("=== %s -> %s ===", name, fn(d, t))
        except Exception as exc:  # noqa: BLE001
            log.info("%s raised %s: %s", name, type(exc).__name__,
                     str(exc)[:250])
        if name == "probe":
            continue
        if d.scene_active() or d.dialog_open():
            log.info("   NOT banking %s -- a scene still owns input (%s)",
                     name, d.state.tasks())
        else:
            d.save(out)
            log.info("   banked %s | %s", out, t.summary(d.state))
    log.info("done | %s | %s", t.summary(d.state), party_species(d))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
