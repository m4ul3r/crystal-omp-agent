#!/usr/bin/env python3
"""Journey driver: reusable primitives for long play sessions, run as legs
in a single persistent process (no per-command emulator reload).

Usage: .venv/bin/python trek.py <leg> [args]   (see main() dispatch)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from crystalagent import paths
from crystalagent.battle import Battle, BattleData, bag_item_index, cancel_pack, goto_pocket
from crystalagent.charmap import Charmap
from crystalagent.emu import Crystal, parse_sequence
from crystalagent.menus import Menus, battle_menu_up
from crystalagent.names import Names
from crystalagent.nav import MapData, STEP, WARPS
from crystalagent.state import game_state, status_line
from crystalagent.symfile import Symbols

DIRS = {"U": "UP", "D": "DOWN", "L": "LEFT", "R": "RIGHT"}


class Driver:
    def __init__(self, state_path=None):
        self.state_path = Path(state_path or paths.DEFAULT_STATE)
        sym = Symbols(paths.SYM)
        cm = Charmap(paths.CHARMAP)
        self.emu = Crystal(paths.ROM, sym, cm, self.state_path)
        # savestates can carry phantom held keys; force-release everything
        for b in ("up", "down", "left", "right", "a", "b", "start", "select"):
            self.emu.py.button_release(b)
        self.emu.tick(10)
        self.names = Names(paths.ROM, sym, cm, paths.MAP_CONSTANTS)
        self.nav = MapData(paths.REPO_ROOT)
        self.menu = Menus(self.emu)
        self.bdata = BattleData(paths.REPO_ROOT, sym, paths.ROM)

    # -- observations ------------------------------------------------------

    def pos(self):
        e = self.emu
        return (e.read_u8("wMapGroup"), e.read_u8("wMapNumber"),
                e.read_u8("wXCoord"), e.read_u8("wYCoord"))

    def map_name(self):
        g, n, _, _ = self.pos()
        return self.names.maps.get((g, n), f"?{g}:{n}")

    def battle(self):
        return self.emu.read_u8("wBattleMode")

    def textbox(self):
        return self.emu.tilemap()[12 * 20] == 0x79

    def lead(self):
        s = game_state(self.emu, self.names)
        return s["party"][0] if s["party"] else None

    def status(self):
        return status_line(game_state(self.emu, self.names))

    def npc_cells(self):
        """Live NPC positions (walk-cell coords) from the object structs.
        Struct map coords are player coords + 4; slot 0 is the player."""
        bank, base = self.emu.sym["wObjectStructs"]
        stride = self.emu.sym.addr("wObject1Struct") - base
        cells = set()
        for i in range(1, 13):
            b = self.emu.read((bank, base + i * stride), 18)
            if b[0]:
                cells.add((b[16] - 4, b[17] - 4))
        return cells

    def settle(self, quiet=3, spacing=20, max_frames=900):
        """Wait until map + position stop changing: door/cutscene warps
        finish asynchronously a beat AFTER the step that triggered them,
        so anything acting on the map right after a step must settle."""
        last, still = None, 0
        f0 = self.emu.frame
        while self.emu.frame - f0 < max_frames:
            if self.battle():
                return
            cur = self.pos()
            if cur == last:
                still += spacing
                if still >= quiet * spacing:
                    return
            else:
                last, still = cur, 0
            self.press(f".:{spacing}")

    # -- actions -----------------------------------------------------------

    def press(self, seq):
        self.emu.run_sequence(parse_sequence(seq))

    def step_dir(self, mv, max_frames=40):
        """Take exactly one step using the engine's own step state
        (wPlayerStepFlags: bit7 = step started, bit6 = step stopped).
        Returns 'moved' | 'blocked' | 'battle' | 'warp'."""
        before = self.pos()
        button = DIRS[mv].lower()
        for _attempt in range(2):  # a turn-in-place consumes the first "step"
            self.emu.py.button_press(button)
            started = False
            for _ in range(max_frames):
                self.emu.tick(1)
                if self.battle():
                    self.emu.py.button_release(button)
                    return "battle"
                if self.emu.read_u8("wPlayerStepFlags") & 0x80:
                    started = True
                    break
            self.emu.py.button_release(button)
            if not started:
                return "battle" if self.battle() else "blocked"
            for _ in range(48):  # ledge hops take longer than plain steps
                self.emu.tick(1)
                if self.emu.read_u8("wPlayerStepFlags") & 0x40:
                    break
            self.emu.tick(2)
            if self.battle():
                return "battle"
            now = self.pos()
            if now[:2] != before[:2]:
                return "warp"
            if now != before:
                return "moved"
        return "blocked"

    def keyboard_open(self):
        s = self.emu.screen_text()
        return any("DEL" in r for r in s) and any("END" in r for r in s)

    def dismiss_keyboard(self):
        """Confirm a naming screen with the minimal name."""
        print("  naming keyboard: confirming", flush=True)
        self.press("START:4 .:20 A:4 .:30")          # jump to END, confirm
        if self.keyboard_open():                      # empty name refused:
            self.press("A:2 .:10 START:4 .:20 A:4 .:30")  # type one letter

    def flush_dialog(self, max_frames=6000, quiet_frames=40):
        """Press A while a textbox is up; return once it's been gone a bit.
        Handles a naming keyboard if one appears."""
        f0, quiet = self.emu.frame, 0
        while self.emu.frame - f0 < max_frames:
            if self.battle():
                return "battle"
            if self.textbox():
                self.press("A:2 .:8")
                quiet = 0
            elif self.keyboard_open():
                self.dismiss_keyboard()
                quiet = 0
            else:
                self.press(".:8")
                quiet += 8
                if quiet >= quiet_frames:
                    return "done"
        return "timeout"

    def fight(self, max_frames=90000, policy=None):
        """Play a battle out with real move selection (best expected
        damage, auto-POTION at low HP, flee hopeless wilds)."""
        if not self.battle():
            return self.lead()
        f0 = self.emu.frame
        b = Battle(self.emu, self.names, self.bdata)
        outcome = b.play(policy=policy, max_frames=max_frames)
        self.flush_dialog(3000)
        lead = self.lead()
        print(f"  battle [{outcome}, {self.emu.frame - f0} frames] -> "
              f"{lead['name']} L{lead['level']} {lead['hp']}/{lead['max_hp']}",
              flush=True)
        return lead

    def catch(self, ball="POKE BALL", max_balls=10):
        """Throw `ball` at the current wild until it connects or the budget
        runs out; flees rather than KO the target once out of balls."""
        thrown = [0]

        def pol(rows, me, enemy):
            dry = bag_item_index(self.emu, self.names, ball, "balls") is None
            if dry or thrown[0] >= max_balls:
                return "flee"
            thrown[0] += 1
            return ("ball", ball)

        return self.fight(policy=pol)

    def use_item(self, item_name, target_slot=0, field=True):
        """Use an item from the pack outside battle (heals/status on party
        member `target_slot`). Returns True if the item was confirmed."""
        e = self.emu
        idx = bag_item_index(e, self.names, item_name, "items")
        if idx is None:
            print(f"  no {item_name} in bag", flush=True)
            return False
        self.press("START:4 .:25")               # open START menu
        if not self.menu.select_label("PACK", max_presses=8):
            self.press("B:4 .:10")
            print("  could not open PACK", flush=True)
            return False
        if not goto_pocket(self.menu, "items"):
            cancel_pack(self.menu)
            return False
        if not self.menu.select_abs(idx):
            cancel_pack(self.menu)
            return False
        # item submenu (USE/GIVE/TOSS/QUIT) pops up after a beat
        if not self.menu.wait_for_label("USE", 300) or \
                not self.menu.select_label("USE", max_presses=4):
            cancel_pack(self.menu)
            print(f"  no USE option for {item_name}", flush=True)
            return False
        used = True
        # healing/status items ask for a target ("Use on which PM?");
        # the party menu swallows the first A during setup, so press
        # until it actually closes
        have_target = self.menu.wait_for(
            lambda r: any("CANCEL" in x for x in r), timeout_frames=400)
        if have_target:
            steps = 0
            while steps < target_slot and \
                    any("CANCEL" in r for r in self.emu.screen_text()):
                self.press("D:6 .:6")
                steps += 1
            f0 = self.emu.frame
            while any("CANCEL" in r for r in self.emu.screen_text()):
                if self.emu.frame - f0 > 1200:
                    return False   # menu refuses to close: something's off
                self.press("A:6 .:18")
            self.flush_dialog(3000)
        else:
            used = False   # submenu confirmed but nothing happened
        # close any leftover UI (pack, stat screens) until the field is back
        def _field_clear(rows):
            bad = ("▶", "▷", "CANCEL", "QUIT", "EXIT", "USE", "TOSS")
            return not any(b in r for r in rows for b in bad)
        f0 = self.emu.frame
        while self.emu.frame - f0 < 900 and not _field_clear(self.emu.screen_text()):
            self.press("B:6 .:14")
        return used

    def walk(self, path, label=""):
        """Walk a path like 'L*12 U*3 D'. Handles battles, NPC dialogs, and
        map transitions along the way; reports blocks instead of looping."""
        if label:
            print(f"[{label}] from {self.map_name()} {self.pos()[2:]}", flush=True)
        for token in path.split():
            d, _, n = token.partition("*")
            d, n = d[0].upper(), int(n or 1)
            done = stuck = 0
            while done < n:
                r = self.step_dir(d)
                if r == "battle":
                    self.fight()
                elif r == "warp":
                    self.settle()
                    print(f"  -> {self.map_name()} {self.pos()[2:]}", flush=True)
                    done += 1
                    stuck = 0
                elif r == "moved":
                    done += 1
                    stuck = 0
                else:
                    if self.textbox():
                        self.flush_dialog()
                        continue
                    stuck += 1
                    if stuck == 2:
                        self.press("B:4 .:10")  # close a stray menu, then retry
                    if stuck >= 4:
                        print(f"  BLOCKED {d} at {self.map_name()} {self.pos()[2:]}",
                              flush=True)
                        return False
        return True

    def goto(self, x, y, label=""):
        """BFS-pathfind to (x,y) on the current map and walk it. Replans
        around NPC bumps; fights encounters on the way."""
        goal = (x, y)
        replans = 0
        if label:
            print(f"[goto {goal}] {label} on {self.map_name()}", flush=True)
        while replans < 20:
            cur = self.pos()[2:]
            if cur == goal:
                return True
            path = self.nav.find_path(self.map_name(), cur, goal, self.npc_cells())
            if not path:
                # distinguish "NPC in the way" from "statically unreachable":
                # if a relaxed (ignore-NPC) route exists, take it and let
                # step_dir handle the bumps -- waiting never moves trainers.
                relaxed = self.nav.find_path(self.map_name(), cur, goal)
                if not relaxed:
                    print(f"  no static path {cur} -> {goal}", flush=True)
                    return False
                self.press(".:40")   # brief beat for genuinely moving NPCs
                replans += 1
                if replans % 5 == 0:
                    print(f"  threading {cur} -> {goal} past NPCs",
                          flush=True)
                path = relaxed
            for mv in path:
                r = self.step_dir(mv)
                if r == "battle":
                    self.fight()
                elif r == "warp":
                    self.settle()
                    print(f"  warped -> {self.map_name()} {self.pos()[2:]}", flush=True)
                    return self.pos()[2:] == goal
                elif r == "blocked":
                    print(f"  blocked {mv} at {self.pos()[2:]}"
                          f"{' [textbox]' if self.textbox() else ''}", flush=True)
                    if self.textbox():
                        self.flush_dialog()
                    else:
                        self.press(".:40")  # let a wandering NPC step aside
                    replans += 1
                    break
            else:
                continue
        print(f"  GAVE UP at {self.pos()[2:]} -> {goal}", flush=True)
        return False

    def grind(self, pace="D U", target_level=13, min_hp=7, max_battles=80):
        """Pace in grass fighting encounters until target level / low HP."""
        battles = 0

        def done():
            lead = self.lead()
            if lead["level"] >= target_level:
                return "leveled"
            if lead["hp"] <= min_hp:
                return "low-hp"
            return None

        while battles < max_battles:
            stop = done()
            if stop:
                return stop
            for token in pace.split():
                d, _, n = token.partition("*")
                moved = 0
                while moved < int(n or 1):
                    r = self.step_dir(d[0].upper())
                    if r == "battle":
                        self.fight()
                        battles += 1
                        stop = done()   # stop mid-pace, don't wander on
                        if stop:
                            return stop
                        break
                    elif r == "moved":
                        moved += 1
        return "max-battles"

    def _approach_cell(self, x, y):
        """Cell to stand on to talk to the NPC at (x,y): an adjacent
        walkable cell if one exists, else (counters!) two cells out along
        a ray whose middle cell is blocked."""
        here = self.pos()[2:]
        name = self.map_name()
        npcs = self.npc_cells()
        grid = self.nav.grid(name)
        wid, hgt = len(grid[0]), len(grid)
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            c = (x + dx, y + dy)
            if self.nav.find_path(name, here, c, npcs) is not None:
                return c
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            mid, far = (x + dx, y + dy), (x + 2 * dx, y + 2 * dy)
            if not (0 <= far[0] < wid and 0 <= far[1] < hgt):
                continue
            if mid in npcs or grid[mid[1]][mid[0]] in WARPS:
                continue   # would need to pass an NPC or a warp
            if self.nav.find_path(name, here, far, npcs) is not None:
                return far
        return None

    def talk_to(self, x, y, label=""):
        """Walk next to the NPC at (x,y) (or across a counter from them),
        face them, and talk. Fights any trainer battle that triggers
        (sight-lines are slow: polls for wBattleMode after the dialog).
        Returns 'battle' | 'talked' | False."""
        if self.battle():
            self.fight()
        self.settle()
        spot = self._approach_cell(x, y)
        if spot is None:
            print(f"  no approach to ({x},{y})", flush=True)
            return False
        if not self.goto(*spot, label or f"approach ({x},{y})"):
            return False
        fdx = (x > spot[0]) - (x < spot[0])
        fdy = (y > spot[1]) - (y < spot[1])
        facing = {(-1, 0): "L", (1, 0): "R", (0, -1): "U", (0, 1): "D"}[
            (fdx, fdy)]
        self.step_dir(facing)          # blocked step = turn toward the NPC
        self.press("A:2 .:20")
        outcome = self.flush_dialog(30000)
        # trainer triggers land slowly; poll before declaring it plain talk
        f0 = self.emu.frame
        while not self.battle() and self.emu.frame - f0 < 2400:
            self.press(".:60")
        if self.battle() or outcome == "battle":
            self.fight()
            return "battle"
        return "talked"

    def save(self, name=None):
        target = Path(paths.SAVES_DIR) / name if name else self.state_path
        self.emu.save(target)
        if name:  # also update the working state
            self.emu.save(self.state_path)
        print(f"[saved {target.name}] {self.status()}", flush=True)


# -- legs -------------------------------------------------------------------

def heal_pokecenter(d):
    """From inside any Pokécenter: talk to the nurse, wait out the jingle."""
    d.goto(3, 3, "nurse counter")
    d.step_dir("U")            # face her (blocked step = turn)
    d.press("A:2 .:20")
    d.flush_dialog()           # "shall we heal?" -> A = yes
    d.press(".:300")           # heal jingle
    d.flush_dialog()           # "we hope to see you again"
    lead = d.lead()
    print(f"  healed: {lead['name']} {lead['hp']}/{lead['max_hp']}", flush=True)


def leg_to_violet(d):
    """Cherrygrove Pokecenter -> Route 30 -> Route 31 -> Violet City."""
    d.goto(3, 7, "pokecenter door")
    d.walk("D", "exit pokecenter")
    d.goto(16, 0, "city north exit")
    d.walk("U", "cross to Route 30")
    d.goto(5, 0, "route 30 north end")     # BFS threads the ledges/trainers
    d.walk("U", "cross to Route 31")
    d.goto(4, 6, "route 31 gate")
    print(f"  now in {d.map_name()} {d.pos()[2:]}", flush=True)


def leg_errand1(d):
    """Route 30 -> Mr. Pokemon's house: receive the Mystery Egg + Pokedex."""
    d.goto(17, 5, "Mr. Pokemon's door")
    d.flush_dialog(2000)
    d.goto(3, 6, "approach Mr. Pokemon")  # he stands at (3,5)
    d.step_dir("U")
    d.press("A:2 .:20")
    d.flush_dialog(30000)                # egg + Oak + Pokedex: very long
    print(f"  done: {d.map_name()} {d.pos()[2:]}", flush=True)


def leg_errand2(d):
    """Back south to Cherrygrove; rival fight triggers heading east."""
    d.goto(2, 7, "house exit")
    d.walk("D", "leave house")
    d.goto(6, 53, "route 30 south end")
    d.walk("D", "into Cherrygrove")
    d.save("pre-rival.state")
    d.goto(39, 6, "east exit (rival ambush en route)")
    d.walk("R*2", "cross to Route 29")


def leg_errand3(d):
    """Route 29 east, into New Bark, deliver the egg at Elm's lab."""
    d.goto(59, 8, "route 29 east end")
    d.walk("R", "into New Bark")
    d.goto(6, 3, "Elm's lab door")
    d.flush_dialog(8000)                 # officer scene (includes naming)
    d.goto(5, 4, "walk up to Elm")
    d.step_dir("U")
    d.press("A:2 .:20")
    d.flush_dialog(30000)                # egg handover, gate clears here
    d.save("egg-delivered.state")


def leg_errand4(d):
    """Leave the lab (aide gives Poke Balls), trek back west to Route 30."""
    d.goto(4, 11, "lab exit")            # aide scene fires on the way
    d.walk("D", "leave lab")
    d.goto(0, 8, "town west exit")
    d.walk("L", "onto Route 29")
    d.goto(0, 6, "route 29 west end")    # catch tutorial fires at x=53
    d.walk("L*2", "into Cherrygrove")
    d.goto(16, 0, "city north exit")
    d.walk("U", "onto Route 30")


def leg_violet(d):
    """Route 30 north (gate now clear) -> Route 31 -> gate -> Violet City."""
    d.goto(5, 0, "route 30 north end")
    d.walk("U", "cross to Route 31")
    d.goto(4, 6, "route 31 gate door")
    d.flush_dialog(1500)
    if d.map_name() != "ROUTE_31_VIOLET_GATE":
        d.goto(4, 7, "gate door (south half)")
    d.goto(0, 4, "gate west door")
    d.flush_dialog(1500)
    print(f"  now in {d.map_name()} {d.pos()[2:]}", flush=True)


def leg_route29(d):
    # From Route 29 grass (44,10) west to Cherrygrove. Path along y=8-10.
    d.walk("U*2", "back to path")           # out of grass to y=8
    d.walk("L*18", "route 29 west")         # long straight, trees at gaps
    print(d.status())


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        sys.exit("usage: trek.py <leg> [<state>] [args...]\n"
             "legs: walk PATH | goto X Y | talk X Y | grind [PACE] [LEVEL] | "
             "catch | fight |\n"
             "      flush | heal | route29 | to_violet |\n"
             "      errand1 errand2 errand3 errand4 violet\n"
             "<state>: savestate path ('' or omitted = saves/default.state)")
    leg, rest = argv[0], list(argv[1:])
    spec = {
        "walk": (1, 1), "goto": (2, 2), "talk": (2, 2),
        "grind": (0, 2), "catch": (0, 0),
        "fight": (0, 0), "flush": (0, 0), "route29": (0, 0), "heal": (0, 0),
        "to_violet": (0, 0), "errand1": (0, 0), "errand2": (0, 0),
        "errand3": (0, 0), "errand4": (0, 0), "violet": (0, 0),
    }
    arity = spec.get(leg)
    if arity is None:
        sys.exit(f"unknown leg {leg!r}; legs: {', '.join(sorted(spec))}")
    lo, hi = arity
    # state path comes right after the leg: '' = default, or a *.state file;
    # anything else is the leg's first real argument
    state_arg = None
    if rest and (rest[0] == "" or rest[0].endswith(".state")):
        state_arg = rest.pop(0) or None
    if not lo <= len(rest) <= hi:
        usage = {"walk": "PATH", "goto": "X Y", "talk": "X Y",
                 "grind": "[PACE] [LEVEL]"}.get(leg, "")
        sys.exit(f"usage: trek.py {leg} [<state>] {usage}".rstrip())
    try:
        d = Driver(state_arg)
    except FileNotFoundError as e:
        sys.exit(f"no such state file: {e.filename}")
    print(f"[start] {d.status()}", flush=True)
    if leg == "walk":
        d.walk(rest[0])
    elif leg == "goto":
        d.goto(int(rest[0]), int(rest[1]))
    elif leg == "talk":
        print(d.talk_to(int(rest[0]), int(rest[1])), flush=True)
    elif leg == "grind":
        gargs = [rest[0], int(rest[1])] if len(rest) > 1 else rest
        print(d.grind(*gargs), flush=True)
    elif leg == "catch":
        d.catch()
    elif leg == "fight":
        d.fight()
    elif leg == "flush":
        d.flush_dialog()
    elif leg == "route29":
        leg_route29(d)
    elif leg == "heal":
        heal_pokecenter(d)
    elif leg == "to_violet":
        leg_to_violet(d)
    elif leg == "errand1":
        leg_errand1(d)
    elif leg == "errand2":
        leg_errand2(d)
    elif leg == "errand3":
        leg_errand3(d)
    elif leg == "errand4":
        leg_errand4(d)
    elif leg == "violet":
        leg_violet(d)
    d.save()
    print(f"[end] {d.status()}", flush=True)


if __name__ == "__main__":
    main()
