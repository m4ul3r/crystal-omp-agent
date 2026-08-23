"""Grind TOGEPI to a target level in Victory Road on the claude-lex2 timeline.

Mechanic: TOGEPI is party slot 1, so it is sent out at the start of every
wild battle and banks participation XP; the policy immediately switches to
the L60 TYPHLOSION (slot 3), which does the killing. TOGEPI eats exactly
one hit per battle for that -- cheap once it is out of the one-shot range,
genuinely risky at L10, so the loop heals it, revives it, and refuses to
keep pacing while it is fainted.

Saves after every resolved battle, so re-running resumes where it stopped.

usage: python scripts/grind_togepi.py [target_level] [battle_budget]
"""
import sys

sys.path.insert(0, ".")

from trek import Driver
from crystalagent.battle import bag_quantity
from crystalagent.state import game_state

STATE = "saves/togepi-grind.state"
# 175 TOGEPI, 176 TOGETIC -- it evolves by happiness mid-grind, and a
# species check that only knows 175 crashes the loop the moment it does.
TOGEPI = 175
TOGEPI_LINE = (175, 176)
TYPHLOSION_SLOT = 2          # 0-based party index for Battle.switch_to
PACE_AXIS = ("D", "U")


def party(d):
    return game_state(d.emu, d.names)["party"]


def togepi_slot(d):
    for i, m in enumerate(party(d)):
        if m["species"] in TOGEPI_LINE:
            return i
    return None


def report(d, tag):
    rows = [(m["name"], m["level"], f"{m['hp']}/{m['max_hp']}") for m in party(d)]
    print(f"[{tag}] {d.map_name()} {d.pos()[2:]} {rows}", flush=True)


def drain(d, limit=20000):
    """Advance any script/textbox that owns the player."""
    f0 = d.emu.frame
    while d.emu.frame - f0 < limit:
        if d.battle():
            return
        if d.textbox():
            d.press("A:2 .:20")
            continue
        if d.emu.read_u8("wScriptMode") == 0:
            return
        d.press(".:40")


def raw(d, seq):
    """Walk a fixed direction sequence, verifying each step by coordinate.
    step_dir misreports in tall grass and after surf mounts, so this is the
    only movement primitive the grind trusts."""
    for mv in seq:
        for _ in range(10):
            p0 = (d.map_name(), d.pos()[2:])
            d.press(f"{mv}:16 .:40")
            if d.battle():
                return "battle"
            if d.textbox() or d.emu.read_u8("wScriptMode"):
                drain(d)
                continue
            d.settle(max_frames=200)
            if (d.map_name(), d.pos()[2:]) != p0:
                break
        else:
            return f"stuck {mv}"
    return "ok"


def make_policy(d):
    """Switch TOGEPI out on its first turn, then fight normally. EMBER is
    forced against Ghosts: TYPHLOSION's other three moves are Normal and
    cannot touch them (that wedged a whole Karen attempt)."""
    def policy(rows, me, enemy):
        if me["species"] in TOGEPI_LINE and d.emu.read_u8("wBattleMode"):
            others = [m for m in party(d)
                      if m["species"] not in TOGEPI_LINE and m["hp"] > 0]
            if others:
                return ("switch", TYPHLOSION_SLOT)
            # nothing left to switch to: TOGETIC's GROWL/CHARM/METRONOME
            # cannot finish a Victory Road wild, so the battle would run to
            # the frame cap. Bail out and let the loop revive the killer.
            if (bag_quantity(d.emu, d.names, "REVIVE") or 0):
                return ("item", "REVIVE")
            return "flee"
        st = d.emu.read_u8("wBattleMonStatus")
        if me["hp"] > 0:
            if st & 0x47 and (bag_quantity(d.emu, d.names, "FULL HEAL") or 0):
                return ("item", "FULL HEAL")
            if me["hp"] * 3 < me["max_hp"] and \
                    (bag_quantity(d.emu, d.names, "MAX POTION") or 0):
                return ("item", "MAX POTION")
        if 26 in enemy.get("types", []):        # GHOST: only EMBER connects
            return ("attack", 3)
        return None
    return policy


def to_victory_road(d):
    if "VICTORY_ROAD" in d.map_name():
        return True
    # warp carpets need two presses: one to step ON, one to step OFF
    if "POKECENTER" in d.map_name():
        d.goto(5, 12, "PC south door")
        d.settle()
        raw(d, ["D", "D"])
        drain(d)
    if "ROUTE_23" in d.map_name():
        d.goto(9, 12, "R23 south warp")
        d.settle()
        raw(d, ["D", "D"])
        drain(d)
    return "VICTORY_ROAD" in d.map_name()


def pc_heal_trip(d):
    """Walk back to the Indigo Plateau center for a free full heal, then
    return to the pacing corridor. Cheaper than burning MAX POTIONs, and
    TYPHLOSION has to stay healthy -- it does all the killing."""
    print("  [heal trip] -> Pokecenter", flush=True)
    # The VR exit and the R23 doors are warp carpets: standing on one is
    # not entering it. Press toward the exit until the MAP changes rather
    # than trusting a fixed step count (raw() gives up on a no-move press
    # while sitting on the carpet).
    def push_until_map_change(mv, tries=16):
        m0 = d.map_name()
        for _ in range(tries):
            d.press(f"{mv}:16 .:50")
            if d.battle():
                d.fight(max_frames=150000, policy=make_policy(d))
                d.settle()
                continue
            drain(d)
            d.settle(max_frames=200)
            if d.map_name() != m0:
                return True
        return False

    if "VICTORY_ROAD" in d.map_name():
        d.goto(13, 6, "VR north exit")
        d.settle()
        push_until_map_change("U")
    if "ROUTE_23" in d.map_name():
        # the PC door is well north of the VR warp; walk the whole way
        push_until_map_change("U", tries=20)
    if "POKECENTER" not in d.map_name():
        print(f"  [heal trip] lost the way on {d.map_name()}", flush=True)
        return False
    d.goto(3, 8, "nurse")
    d.settle()
    d.talk_to(3, 7, "nurse")
    drain(d)
    d.settle()
    ok = all(m["hp"] == m["max_hp"] for m in party(d))
    print(f"  [heal trip] healed={ok}", flush=True)
    return to_victory_road(d)


def heal_up(d):
    """Between fights: revive anything that fainted and top up whoever is
    badly hurt. The KILLER matters as much as TOGEPI -- a fainted
    TYPHLOSION leaves TOGETIC unable to end a battle at all."""
    for slot, m in enumerate(party(d)):
        if m["species"] == 60:                     # POLIWAG: never battles
            continue
        if m["hp"] == 0:
            if not (bag_quantity(d.emu, d.names, "REVIVE") or 0):
                print(f"  {m['name']} fainted and no REVIVE left", flush=True)
                return False
            d.close_menus()
            if not d.use_item("REVIVE", target_slot=slot):
                return False
        cur = party(d)[slot]
        if cur["hp"] * 2 < cur["max_hp"] and \
                (bag_quantity(d.emu, d.names, "MAX POTION") or 0):
            d.close_menus()
            d.use_item("MAX POTION", target_slot=slot)
    return all(m["hp"] > 0 for m in party(d) if m["species"] != 60)


def main():
    target = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    budget = int(sys.argv[2]) if len(sys.argv) > 2 else 40

    d = Driver(STATE)
    policy = make_policy(d)
    report(d, "start")

    # a queued phone call owns the player and silently eats START
    d.press(".:120")
    drain(d)
    d.settle()

    slot = togepi_slot(d)
    if slot != 0:
        print(f"TOGEPI is in slot {slot + 1}; moving it to the lead", flush=True)
        if not d.party_swap(1, slot + 1):
            print("could not put TOGEPI in the lead -- stopping", flush=True)
            return
        d.save(STATE)

    if not to_victory_road(d):
        print(f"not in Victory Road (on {d.map_name()}) -- stopping", flush=True)
        return
    d.save(STATE)

    axis = list(PACE_AXIS)
    fights = 0
    stuck = 0
    while fights < budget:
        lvl = party(d)[togepi_slot(d)]["level"]
        if lvl >= target:
            print(f"TOGEPI reached L{lvl}", flush=True)
            break

        p0 = d.pos()[2:]
        r = raw(d, [axis[0]])
        if r == "battle":
            d.fight(max_frames=60000, policy=policy)
            d.settle()
            drain(d)
            fights += 1
            report(d, f"fight {fights}")
            if d.emu.read_u8("wScriptMode") == 0 and not d.battle():
                d.save(STATE)
            if not heal_up(d):
                break
            typh = next((m for m in party(d) if m["species"] == 157), None)
            if typh and typh["hp"] * 2 < typh["max_hp"]:
                if not pc_heal_trip(d):
                    break
                d.save(STATE)
            continue
        if r != "ok" or d.pos()[2:] == p0:
            stuck += 1
            axis.reverse()
            if stuck > 8:
                print(f"corridor wedged at {d.pos()[2:]} -- stopping", flush=True)
                break
        else:
            stuck = 0

    report(d, "done")
    if d.emu.read_u8("wScriptMode") == 0 and not d.battle():
        d.save(STATE)


if __name__ == "__main__":
    main()
