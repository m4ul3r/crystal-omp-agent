"""Shared TALLOW driver setup: warm Driver + persona policies."""
import logging
import trek

log = logging.getLogger("tallow")

STEP_OF = {"U": (0, -1), "D": (0, 1), "L": (-1, 0), "R": (1, 0)}


SUICIDE = {"SELFDESTRUCT", "EXPLOSION"}


def tactics_policy(d):
    """d.tactics.recommend with the self-KO moves struck from the table: a
    'certain KO' by SELFDESTRUCT forfeits the exp (CRUST fainted 240 wild
    battles in a row while the anchor levelled)."""
    def policy(frame):
        a = d.outlook()
        if a is None:
            return ("attack", 0)
        a = dict(a)
        a["moves"] = [m for m in a["moves"] if (m.get("move") or "").upper() not in SUICIDE]
        act, why = d.tactics.recommend(a, frame)
        log.info("  [tallow] %s -- %s", act, why)
        return act
    return policy


# persona core plan: species -> kitchen nickname. A wild of a planned species
# whose nickname is not yet owned gets AT MOST two balls, then we flee.
WANT = {"GEODUDE": "CRUST", "POLIWAG": "BRINE", "RATTATA": "CRUMB", "MAGNEMITE": "LADLE"}
MAX_BALLS = 2


def owned_nicks(d):
    return {m.get("nick") for m in d.observe()["party"]}


def encounter_policy(d):
    def policy(frame):
        sp = (frame.get("enemy") or {}).get("name") if isinstance(frame, dict) else None
        if sp is None:
            sp = d.observe().get("enemy", {}).get("name")
        nick = WANT.get(sp)
        if nick and nick not in owned_nicks(d) and d._bag().get("POKEBALL", 0) > 0 \
                and len(d.observe()["party"]) < 6:
            d._pending_nickname = nick
            return "catch"
        return "ko"
    return policy


def boot(state):
    d = trek.Driver(state)
    d.encounter_policy = encounter_policy(d)
    def _learn(mon, new_move, current, _d=d):
        # never learn a self-KO move (EXPLOSION replaced CRUST's EARTHQUAKE once)
        if str(new_move).upper() in SUICIDE:
            return "DECLINE"
        return _d.default_learn_policy(mon, new_move, current)
    d.learn_policy = _learn
    d.default_policy = tactics_policy(d)
    orig = d._ball_policy
    d._ball_policy = lambda ball="POKE BALL", max_balls=MAX_BALLS: orig(ball, min(max_balls, MAX_BALLS))
    take = d._take_pending_nickname

    def take_pending():
        # flush_dialog's keyboard hook: the harness declines gift/hatch names
        # unless one is armed, and fight() disarms after every battle. An egg
        # in the party means this keyboard is the hatch -> persona name.
        if d._pending_nickname is None and any(m.get("egg") for m in d.observe()["party"]):
            d._pending_nickname = HATCH_NAME
        return take()
    d._take_pending_nickname = take_pending
    try:
        d.enable_surf()
    except RuntimeError:
        pass
    return d


def trainee_policy(d, trainee, anchor, margin=3, hp_floor=0.4):
    """Battle policy: `trainee` (nickname) fights alone while the enemy is
    within `margin` levels and its HP is above `hp_floor`; otherwise the
    `anchor` takes over. Keeps the anchor's exp (and level) down --
    persona ceiling ace+3 -- while the bench earns full exp."""
    smart = tactics_policy(d)

    def policy(frame):
        party = d.observe()["party"]
        idx = {m.get("nick"): i for i, m in enumerate(party)}
        me = frame.get("me", {}) if isinstance(frame, dict) else {}
        enemy = frame.get("enemy", {}) if isinstance(frame, dict) else {}
        cur = d.emu.read_u8("wCurBattleMon")
        active = party[cur] if cur < len(party) else {}
        if active.get("nick") == trainee:
            weak = active["hp"] < active["max_hp"] * hp_floor
            outlvl = enemy.get("level", 0) > active["level"] + margin
            if (weak or outlvl) and anchor in idx and party[idx[anchor]]["hp"] > 0 \
                    and frame.get("can_switch", True):
                log.info("  [trainee] %s steps back (%s); %s in",
                         trainee, "low hp" if weak else "outleveled", anchor)
                return ("switch", idx[anchor])
        return smart(frame)
    return policy



def matchup_policy(d, table, fallback=None):
    """Battle policy: `table` = {ENEMY_SPECIES: preferred nickname}. When the
    active mon is not the preferred one for what is out there (and the
    preferred one can still fight), switch; otherwise defer to `fallback`
    (default: the tactics policy)."""
    smart = fallback or tactics_policy(d)

    def policy(frame):
        if not isinstance(frame, dict):
            return smart(frame)
        enemy = (frame.get("enemy") or {}).get("name")
        want = table.get(enemy)
        party = d.observe()["party"]
        cur = d.emu.read_u8("wCurBattleMon")
        active = party[cur].get("nick") if cur < len(party) else None
        if want and active != want and frame.get("can_switch", True):
            for i, m in enumerate(party):
                if m.get("nick") == want and m["hp"] > 0:
                    log.info("  [matchup] %s vs %s: switching %s -> %s", active, enemy, active, want)
                    return ("switch", i)
        return smart(frame)
    return policy

def set_lead(d, nick, second=None):
    """Put `nick` in slot 1 (and `second` in slot 2, so a fainting trainee
    hands the fight to the anchor, not to whoever happens to be next)."""
    party = [m.get("nick") for m in d.observe()["party"]]
    if party and party[0] != nick:
        ok = d.party_swap(1, party.index(nick) + 1)
        log.info("set_lead %s: %s -> %s", nick, ok, [m.get("nick") for m in d.observe()["party"]])
        party = [m.get("nick") for m in d.observe()["party"]]
    if second and second in party and party[1] != second:
        ok = d.party_swap(2, party.index(second) + 1)
        log.info("second %s: %s -> %s", second, ok, [m.get("nick") for m in d.observe()["party"]])
    return d.observe()["party"][0].get("nick") == nick


HATCH_NAME = "SUGAR"      # persona: the Mystery Egg's Togepi


def settle_dialog(d, rounds=30, choice="YES"):
    """Page dialog; answer choice boxes; name a hatch; stop when no textbox remains."""
    for _ in range(rounds):
        if d.keyboard_open():
            log.info("naming keyboard -> %s", HATCH_NAME)
            d.name_prompt(HATCH_NAME)
            continue
        r = d.flush_dialog()
        if d.keyboard_open():
            continue
        if r == "menu" or d.menu_open():
            d.resolve_choice(choice)
            continue
        if not d.textbox():
            break


def travel(d, dest_map, attempts=4):
    from crystalagent.driver.navigation import TravelError
    dest = d._resolve_map(dest_map)
    for attempt in range(attempts):
        if d.map_name() == dest:
            return True
        try:
            d.travel(dest)
            d.settle()
        except TravelError as ex:
            log.info("[leg %s] attempt %d: %s", dest, attempt, ex)
            settle_dialog(d)
            d.drain_scene()
            d.settle()
            if "no path" in str(ex) and d.field_moves().get("CUT"):
                # a CUT tree is a wall to nav and regrows on every map load
                here = d.pos()[2:]
                for tx, ty in sorted(d.find_tiles("cut-tree"),
                                     key=lambda c: abs(c[0]-here[0]) + abs(c[1]-here[1])):
                    if d.cut(tx, ty):
                        log.info("[leg %s] cut tree at (%d,%d)", dest, tx, ty)
                        break
                    log.info("[leg %s] cut (%d,%d) refused: %s", dest, tx, ty, d.last_field_reason)
    return d.map_name() == dest


def save_clean(d, name=None):
    for _ in range(20):
        if d.battle():
            d.fight()
        settle_dialog(d)
        d.drain_scene()
        d.settle()
        if d.emu.read_u8("wScriptMode") == 0 and not d.textbox():
            break
        d.press("A:4 .:40")
    d.save(name) if name else d.save()


def heal_at(d, center_map):
    """Walk into `center_map` (a Pokecenter) and run the nurse cycle."""
    if not travel(d, center_map):
        raise RuntimeError(f"could not reach {center_map}: {d.map_name()}")
    d.heal()
    lead = d.lead()
    log.info("healed at %s: lead L%d %d/%d", center_map, lead["level"],
             lead["hp"], lead["max_hp"])


def catch_species(d, want, route, center, heal_via=(), rounds=30, max_balls=2):
    """Pace the grass belt nearest the current cell on `route` until one of
    the `want` {SPECIES: NICK} species is caught (persona: max 2 balls per
    wild, then flee). Heals at `center` (walking back through `heal_via`)
    when the lead drops under 40%. Returns the nickname caught or None."""
    nicks = set(want.values())
    grass = d.find_tiles("grass") or d.find_tiles("floor")   # caves: every floor cell
    here = d.pos()[2:]
    near = sorted(grass, key=lambda c: abs(c[0]-here[0]) + abs(c[1]-here[1]))[:12]
    bx = (min(c[0] for c in near), max(c[0] for c in near),
          min(c[1] for c in near), max(c[1] for c in near))
    log.info("catch_species %s on %s box=%s", want, route, bx)
    d.goto(*near[0])
    for _ in range(rounds):
        r = d.pace(40, box=bx)
        if r["stopped"] != "battle":
            log.info("pace stopped: %s", r)
            if r["stopped"] in ("warp", "whiteout"):
                return None
            d.goto(*near[0])
            continue
        enemy = d.observe().get("enemy", {})
        sp = enemy.get("name")
        have = {m.get("nick") for m in d.observe()["party"]}
        if sp in want and want[sp] not in have:
            log.info("wild %s L%s -> catch as %s", sp, enemy.get("level"), want[sp])
            d.catch(nickname=want[sp], max_balls=max_balls)
            have = {m.get("nick") for m in d.observe()["party"]}
            if want[sp] in have:
                log.info("caught %s; party %s", want[sp], have)
                return want[sp]
            if d.battle():
                d.fight(policy=lambda frame: "flee")
        else:
            d.fight()
        lead = d.lead()
        if lead["hp"] < lead["max_hp"] * 0.4:
            log.info("lead low (%d/%d); healing at %s", lead["hp"], lead["max_hp"], center)
            heal_at(d, center)
            for m in list(heal_via) + [route]:
                assert travel(d, m), (m, d.map_name())
            d.goto(*near[0])
    return None


ICE_COLL, PIT_COLL, LEDGE_COLL = 0x23, 0x60, 0xA3


def slide_to(d, goal, live=True, avoid=(), max_steps=40):
    """BFS over ice slides (0x23) on the current map and execute it. Uses the
    LIVE block map (the Ice Path B2F decoded grid is wrong -- RUSTY) plus the
    live NPC/boulder cells as walls. Returns True when standing on `goal`."""
    from collections import deque
    from crystalagent.nav import WALKABLE, WARPS
    grid = d.live_grid() if live else d.nav.grid(d.map_name())
    H, W = len(grid), len(grid[0])
    walls = set(d.npc_cells()) | set(d.boulder_cells()) | set(avoid)

    def enterable(x, y):
        if not (0 <= x < W and 0 <= y < H) or (x, y) in walls:
            return False
        c = grid[y][x]
        return c == ICE_COLL or c in WALKABLE or c in WARPS or c == PIT_COLL

    def slide(pos, mv):
        dx, dy = STEP_OF[mv]
        x, y = pos
        if not enterable(x + dx, y + dy):
            return pos
        x, y = x + dx, y + dy
        while grid[y][x] == ICE_COLL and enterable(x + dx, y + dy):
            x, y = x + dx, y + dy
        return (x, y)

    start = d.pos()[2:]
    prev = {start: None}
    q = deque([start])
    while q and goal not in prev:
        cur = q.popleft()
        for mv in "UDLR":
            nxt = slide(cur, mv)
            if nxt != cur and nxt not in prev:
                prev[nxt] = (cur, mv)
                q.append(nxt)
    if goal not in prev:
        log.info("slide_to: no slide path %s -> %s on %s", start, goal, d.map_name())
        return False
    path, c = [], goal
    while prev[c]:
        c, mv = prev[c]
        path.append(mv)
    path.reverse()
    log.info("slide_to %s -> %s: %s", start, goal, "".join(path))
    for mv in path[:max_steps]:
        before = d.pos()[2:]
        d.step_dir(mv)
        d.settle(); settle_dialog(d)
        if d.battle():
            d.fight()
        log.info("  %s: %s -> %s", mv, before, d.pos()[2:])
    return d.pos()[2:] == goal

from collections import deque
LEARNED_WALLS = {}


def live_walk(d, goal, passable=None, hops=True):
    """BFS on the LIVE block map (static grids lie on Dragon's Den, Ice Path,
    Victory Road, Route 27): floor/grass/ice/water/warp cells, one-way ledge
    hops (entering a ledge cell in ITS direction lands two cells on), NPC cells
    and previously-blocked cells as walls. One step_dir per cell; battles fought."""
    from crystalagent.nav import WALKABLE, WATER, WARPS, HOPS, ICE
    ok = WALKABLE | WATER | ICE | {0x01, 0x24}   # warps are NOT passable (stepping on one fires it)
    passable = passable or (lambda c: c in ok)
    g = d.live_grid(); H, W = len(g), len(g[0])
    walls = set(d.npc_cells()) | LEARNED_WALLS.setdefault(d.map_name(), set())
    start = d.pos()[2:]
    prev = {start: None}; q = deque([start])
    while q and goal not in prev:
        x, y = q.popleft()
        for mv, (dx, dy) in STEP_OF.items():
            n = (x + dx, y + dy)
            if not (0 <= n[0] < W and 0 <= n[1] < H) or n in walls:
                continue
            c = g[n[1]][n[0]]
            if hops and c in HOPS:
                if HOPS[c] != mv:
                    continue
                n = (n[0] + dx, n[1] + dy)  # landing is NOT collision-checked by the engine
                if not (0 <= n[0] < W and 0 <= n[1] < H) or n in walls:
                    continue
            elif not passable(c) and not (n == goal and c in WARPS):
                continue
            if n not in prev:
                prev[n] = ((x, y), mv); q.append(n)
    assert goal in prev, f"live_walk: {start} -> {goal} unreachable"
    path, c = [], goal
    while prev[c]:
        c, mv = prev[c]; path.append(mv)
    for mv in path[::-1]:
        before = d.pos()[2:]
        r = d._step(mv)
        d.settle(); settle_dialog(d)
        if d.battle():
            d.fight()
        tries = 0
        while d.pos()[2:] == before and r not in ("moved", "warp") and tries < 3:
            tries += 1
            d.settle(); settle_dialog(d); d.close_menus(); d.emu.tick(30)
            r = d._step(mv); d.settle()
            if d.battle():
                d.fight()
        if d.pos()[2:] == before and r not in ("moved", "warp"):
            dx, dy = STEP_OF[mv]
            LEARNED_WALLS[d.map_name()].add((before[0] + dx, before[1] + dy))
            log.info("  step %s from %s: %s -- learned wall %s", mv, before, r, (before[0] + dx, before[1] + dy))
            return False
    return d.pos()[2:] == goal




LANDMARK = {"NEW_BARK_TOWN": 0x01, "CHERRYGROVE_CITY": 0x03, "VIOLET_CITY": 0x06, "AZALEA_TOWN": 0x0c,
            "GOLDENROD_CITY": 0x10, "ECRUTEAK_CITY": 0x16, "OLIVINE_CITY": 0x1b, "CIANWOOD_CITY": 0x21,
            "MAHOGANY_TOWN": 0x24, "LAKE_OF_RAGE": 0x26, "BLACKTHORN_CITY": 0x29}


def fly(d, town, knower="FLOUR"):
    """START -> POKeMON -> <knower> -> FLY -> cycle the fly map until
    wTownMapCursorLandmark is the town's landmark -> A. True when the map changed to `town`."""
    target = LANDMARK[town]
    idx = [m["nick"] for m in d.observe()["party"]].index(knower) + 1
    for _ in range(3):
        d.press("START:4 .:45")
        if d.menu_open():
            break
    for _ in range(10):
        row = d.menu.cursor_row()
        if row and "MON" in row[1].upper() and "DEX" not in row[1].upper():
            d.press("A:4 .:25"); break
        d.press("D:6 .:12")
    d.press(".:25")
    if not d._party_cursor_to(idx):
        d.close_menus(); log.info("fly: no party row %s", idx); return False
    d.press("A:4 .:25")
    if not d.select_menu_row("FLY", max_presses=8):
        d.close_menus(); log.info("fly: FLY entry not found"); return False
    d.press(".:60")
    want = town.replace("_", " ")
    for _ in range(24):
        if want in d.emu.screen_text()[1].upper():
            break
        d.press("D:6 .:20")
    else:
        d.close_menus(); log.info("fly: %s never selected", want); return False
    d.press("A:4 .:40")
    for _ in range(60):
        d.emu.tick(30)
        if d.map_name() == town and d.emu.read_u8("wScriptMode") == 0:
            break
    d.settle(); settle_dialog(d)
    log.info("fly -> %s: %s %s", town, d.map_name(), d.pos()[2:])
    return d.map_name() == town
