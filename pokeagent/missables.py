"""Which collectable items exist in Hoenn, and which are still out there.

The Crystal harness grew this module after a full Johto playthrough reached
Champion without HM02 FLY: it had been sitting with Chuck's wife in Cianwood
since the Storm Badge and nothing in the harness ever said so, so every
journey of that run was on foot. The recovery walk cost an hour of wall
clock and several hundred tool calls that one line of status text would have
prevented.

Sapphire has the identical trap with the identical HM. FLY comes from the
rival on Route 119 (`data/maps/Route119/scripts.inc:150  giveitem
ITEM_HM02_FLY`), five script hops down from a coordinate trigger at (25,31)
that only fires while ``VAR_ROUTE119_STATE`` is 0. Miss the trigger and the
game never offers it again from that source. The same silence covers SURF
(Wally's house), STRENGTH (Rusturf Tunnel), FLASH (Granite Cave 1F),
ROCK SMASH (Mauville), CUT (Rustboro) and DIVE (Steven's house) -- i.e. the
entire overworld toolkit, every one of it behind a chatty NPC nobody has to
talk to.

Four acquisition forms exist in the pokeruby scripts and all four matter:

- ``giveitem <ITEM>`` -- an NPC or cutscene gift
  (`include/macros/event.inc:1547`, which expands to
  ``callstd STD_OBTAIN_ITEM``). Coordinates are not on the give line: the
  give sits in a named script, and that label is the ``script`` field of an
  ``object_event`` / ``coord_event`` / ``bg_event`` in the map's
  ``map.json`` -- often several ``goto``/``call`` hops away.
- ``finditem <ITEM>`` -- the item-ball std (`event.inc:1553`,
  ``callstd STD_FIND_ITEM``). Most live in ``data/item_ball_scripts.inc``,
  a file with no map in its path at all, so the map name has to come from
  whichever ``object_event`` points at the script. **HM07 WATERFALL and
  HM08 DIVE are both item balls**, so a parser that only understood NPC
  gifts would miss two HMs.
- ``bg_hidden_item_event`` -- a hidden item, declared entirely inside
  ``map.json`` as a ``bg_event`` of type ``hidden_item``
  (`include/macros/map.inc:107`). There is no script and no give line; the
  item and its flag are fields.
- ``additem <ITEM>`` -- the raw bag primitive (`event.inc:465`), used by the
  Game Corner TM counter and the Contest Pass. Counted for completeness.

A handful of gives take a *variable* rather than a constant -- the lottery
prize, the Berry Master's daily berry, the Route 113 glass reward. Which
item they hand over is decided at runtime, so :func:`parse_item_sources`
refuses to invent a row for them and :func:`runtime_gives` cites them
instead. That is the whole known blind spot, and it is one call away from a
session instead of being buried.

Nothing here is transcribed game data. Item ids and flag ids come from
``include/constants/{items,flags}.h`` through :mod:`~pokeagent.cconst`,
key-item-ness from ``gItems[].importance`` read out of the ROM through
:meth:`~pokeagent.names.Names.item_data` (`src/item.c:19`), the HM roster
from the ``ITEM_HM<nn>_*`` names in ``items.h``, the move each HM teaches
from the ROM's own ``TMHMMoves`` table (`src/party_menu.c:117-177`), and the
FLASH requirement from each ``map.json``'s ``requires_flash`` key.
"""

import json
import re
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

from . import paths

#: Kinds, and where each one comes from. Note that the *kind* is decided by
#: the map event that owns the script, not by the macro: `finditem` is the
#: ball std but Steven's house uses it for a fallback gift, and `giveitem`
#: is a gift but Route 119's FLY arrives from a coordinate trigger. The
#: engine's own data is the better witness than the macro name.
KIND_BALL = "ball"        # an OBJ_EVENT_GFX_ITEM_BALL object_event
KIND_NPC = "npc"          # any other object_event
KIND_HIDDEN = "hidden"    # a bg_event of type hidden_item
KIND_SCRIPT = "script"    # a coord_event, a map_script table, or unplaced

_GIVE = re.compile(
    r"^\s+(giveitem|finditem|additem)\s+(ITEM_[A-Z0-9_]+)\s*(?:,|@|$)"
)
#: The same macros handed a variable instead of a constant. See
#: :func:`runtime_gives` -- the item is chosen at runtime.
_RUNTIME_GIVE = re.compile(
    r"^\s+(giveitem|finditem|additem)\s+(VAR_[A-Za-z0-9_]+)\s*(?:,|@|$)"
)
#: `Route102_EventScript_1B1439:: @ 81B1439` -- pokeruby labels are exported
#: (`::`) and carry the original ROM address as a comment.
_LABEL = re.compile(r"^([A-Za-z_]\w*)::?\s*(?:@.*)?$")
_SETFLAG = re.compile(r"^\s+setflag\s+(FLAG_[A-Z0-9_]+)")
#: The guard that makes a gift one-shot, read when no setflag follows.
_FLAG_GUARD = re.compile(
    r"^\s+(?:goto|call)_if_(?:set|unset)\s+(FLAG_[A-Z0-9_]+)"
)
#: Every op that can transfer control to another label. The destination is
#: always the last comma-separated argument.
_CONTROL = re.compile(
    r"^\s+(goto|call|case|switch|goto_if\w*|call_if\w*|map_script\w*|"
    r"trainerbattle\w*|special\w*|setstepcallback)\b(.*)$"
)
#: Script labels are CamelCase-with-underscores; every constant is SHOUTING.
#: A lowercase letter is therefore an exact discriminator between
#: `Route119_EventScript_151352` and `FLAG_RECEIVED_HM02` / `MSGBOX_DEFAULT`.
_LABEL_ARG = re.compile(r"^[A-Za-z_]\w*$")
#: The per-map script tables (`include/macros/map.inc` `map_script`). A
#: scene entered from one of these has no coordinates at all -- OnFrame
#: fires wherever the player is standing -- so naming the table is a real
#: answer where "could not resolve" would read like a parser bug.
_MAP_SCRIPT_TABLE = re.compile(r"(_MapScripts|_On[A-Z]\w*)$")

#: How far back up the caller chain to look for an object. Route 119's FLY
#: needs five hops (151352 <- 1512BD <- 15128D <- 15125E <- 1511DB <-
#: 1511C5, the coord_event at (25,31)); the bound stops a cyclic chain and
#: keeps a resolution from wandering into an unrelated scene.
_CALLER_HOPS = 8

#: Flag names shaped like an acquisition. Used only to *rank* candidates
#: inside a give's own script block -- Steven sets FLAG_RECEIVED_HM08 and
#: FLAG_OMIT_DIVE_FROM_STEVEN_LETTER back to back
#: (`data/maps/MossdeepCity_StevensHouse/scripts.inc:42-43`) and only the
#: first gates the item.
_ACQUISITION = re.compile(r"FLAG_(RECEIVED|GOT|ITEM|HIDDEN_ITEM)_")

#: `data/scripts/debug.inc` is the developer menu: 250 `additem` lines that
#: hand out 99 of everything. It is unreachable in a retail cartridge, so
#: counting it would bury every real source under a shopping list.
_SKIP_PARTS = ("debug", "text")
#: The same exclusion by label, for the developer setup scripts that live
#: inside an ordinary map file:
#: `LilycoveCity_ContestLobby_EventScript_SetDebug`
#: (`data/maps/LilycoveCity_ContestLobby/scripts.inc:581`) hands over a
#: CONTEST PASS that no player can ever reach.
_DEBUG_LABEL = re.compile(r"debug", re.I)

#: The item-ball object sprite. Its presence on the object_event is how the
#: engine itself distinguishes a ball from a person.
_ITEM_BALL_GFX = "ITEM_BALL"


@dataclass(frozen=True, slots=True)
class ItemSource:
    """One place in the world an item can be obtained.

    ``x``/``y`` are ``None`` when no event in any ``map.json`` could be
    traced back to the script within :data:`_CALLER_HOPS`; ``unresolved``
    then says so in words rather than the row quietly carrying a zero.
    """

    item: str
    kind: str
    map: str
    x: int | None
    y: int | None
    script: str
    flag: str | None
    source_line: str
    #: The macro that actually hands the item over: giveitem / finditem /
    #: additem, or "" for a hidden item, which has no script at all.
    macro: str = ""
    #: Why ``map``/``x``/``y``/``flag`` are incomplete, or None when whole.
    unresolved: str | None = None
    #: Caller hops walked to reach the event that gave the coordinates.
    hops: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


# ---- the script text -------------------------------------------------


def _script_files(root: Path) -> list[Path]:
    """Every assembled script file, in a stable sorted order.

    Scanned recursively because the map a script belongs to is *not* implied
    by its path: `data/item_ball_scripts.inc` and
    `data/scripts/players_house.inc` (the S.S. TICKET) are included globally
    from `data/event_scripts.s` and hold items for a dozen maps.
    """
    files = [p for p in sorted(root.rglob("*.inc")) if p.is_file()]
    files += [p for p in sorted(root.glob("*.s")) if p.is_file()]
    return [
        p
        for p in files
        if not any(part.lower().startswith(_SKIP_PARTS) for part in p.parts)
    ]


def _blocks(lines: list[str]) -> list[tuple[str, int, int]]:
    """``[(label, first_body_line_index, end_index_exclusive), ...]``."""
    starts = [
        (m.group(1), i) for i, line in enumerate(lines) if (m := _LABEL.match(line))
    ]
    return [
        (label, idx + 1, starts[n + 1][1] if n + 1 < len(starts) else len(lines))
        for n, (label, idx) in enumerate(starts)
    ]


def _refs(lines: list[str], blocks) -> dict[str, set[str]]:
    """``{destination label: {labels that can jump to it}}``.

    The destination is the last comma-separated argument of a control-flow
    op. Text and movement labels get edges too (harmless: nothing ever asks
    for their callers), but a `msgbox` destination is never a script, so no
    false hop can be introduced by them.
    """
    out: dict[str, set[str]] = {}
    for label, start, end in blocks:
        for line in lines[start:end]:
            m = _CONTROL.match(line)
            if not m:
                continue
            arg = m.group(2).split("@")[0].split(",")[-1].strip()
            if _LABEL_ARG.match(arg) and any(c.islower() for c in arg):
                out.setdefault(arg, set()).add(label)
    return out


def _guarding_flag(lines: list[str], idx: int, start: int, end: int):
    """The ``FLAG_*`` gating the give at ``lines[idx]``, or ``(None, why)``.

    Forward first, because the engine's own order is give-then-setflag
    (`RustboroCity_CuttersHouse/scripts.inc:9-10`), capped at the next give
    in the same block so a two-item script never steals its neighbour's
    flag. The S.S. TICKET's setflag is fifteen lines downstream
    (`data/scripts/players_house.inc:379,394`), which is why this scans to
    the end of the block instead of a fixed lookahead window.
    """
    forward = []
    for line in lines[idx + 1 : end]:
        if _GIVE.match(line):
            break
        if m := _SETFLAG.match(line):
            forward.append(m.group(1))
    if forward:
        ranked = [f for f in forward if _ACQUISITION.search(f)]
        return (ranked or forward)[0], None
    for line in reversed(lines[start:idx]):
        if m := _FLAG_GUARD.match(line):
            return m.group(1), None
    return None, "no setflag or flag guard in the enclosing script"


# ---- the event index -------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Event:
    map: str
    x: int
    y: int
    kind: str
    flag: str | None


def _event_index(maps_dir: Path):
    """``({script label: [_Event]}, [ItemSource] for hidden items)``.

    ``map.json`` is the only place coordinates exist. Object events carry
    the ball/NPC distinction in ``graphics_id`` and the real gate in
    ``flag``; coord events are cutscene triggers (Route 119's FLY);
    ``bg_event`` hidden items are complete sources on their own.
    """
    index: dict[str, list[_Event]] = {}
    hidden: list[ItemSource] = []
    for path in sorted(maps_dir.glob("*/map.json")):
        text = path.read_text()
        j = json.loads(text)
        name = j.get("name") or path.parent.name
        rel = f"{path.relative_to(paths.PRET)}"
        # Hidden items get a real line citation: the flag is unique per
        # event, so its own line in the JSON is the honest reference.
        flag_lines = {}
        for n, line in enumerate(text.splitlines(), 1):
            if m := re.search(r'"flag":\s*"(FLAG_\w+)"', line):
                flag_lines.setdefault(m.group(1), n)
        for obj in j.get("object_events") or []:
            gfx = obj.get("graphics_id", "")
            kind = KIND_BALL if _ITEM_BALL_GFX in gfx else KIND_NPC
            flag = obj.get("flag")
            index.setdefault(obj.get("script") or "", []).append(
                _Event(name, obj["x"], obj["y"], kind,
                       flag if flag and flag != "0" else None)
            )
        for trig in j.get("coord_events") or []:
            if trig.get("script"):
                index.setdefault(trig["script"], []).append(
                    _Event(name, trig["x"], trig["y"], KIND_SCRIPT, None)
                )
        for bg in j.get("bg_events") or []:
            if bg.get("type") == "hidden_item":
                flag = bg.get("flag")
                line = flag_lines.get(flag)
                hidden.append(
                    ItemSource(
                        item=bg["item"],
                        kind=KIND_HIDDEN,
                        map=name,
                        x=bg["x"],
                        y=bg["y"],
                        script="",
                        flag=flag,
                        source_line=f"{rel}:{line}" if line else rel,
                    )
                )
            elif bg.get("script"):
                index.setdefault(bg["script"], []).append(
                    _Event(name, bg["x"], bg["y"], KIND_SCRIPT, None)
                )
    index.pop("", None)
    return index, hidden


def _locate(label: str, refs, index, hops: int = _CALLER_HOPS):
    """``(_Event or None, hops, visited labels in BFS order)``.

    Breadth-first up the caller chain so the shallowest -- i.e. the most
    directly responsible -- event wins. The visited list is returned even on
    failure, because it is what lets the caller name the map for a scene
    that has coordinates nowhere: Steven's DIVE gift hangs off
    ``map_script_2 VAR_STEVENS_HOUSE_STATE, 0, ...``
    (`data/maps/MossdeepCity_StevensHouse/scripts.inc:26`), an OnFrame
    table entry that fires wherever the player happens to be standing.
    """
    seen = {label}
    visited = [label]
    frontier = [label]
    depth = 0
    for depth in range(hops + 1):
        found = [e for lbl in frontier for e in index.get(lbl, ())]
        if found:
            # Deterministic pick: balls and NPCs before triggers, then by
            # position, so two callers never flip the answer between runs.
            order = {"ball": 0, "npc": 1, "script": 2}
            best = min(found, key=lambda e: (order[e.kind], e.map, e.y, e.x))
            return best, depth, visited
        nxt = sorted(
            {c for lbl in frontier for c in refs.get(lbl, ()) if c not in seen}
        )
        seen.update(nxt)
        visited.extend(nxt)
        frontier = nxt
        if not frontier:
            break
    return None, depth, visited


# ---- the parse -------------------------------------------------------


def _map_from_path(rel: str) -> str | None:
    """``data/maps/Route102/scripts.inc`` -> ``Route102``. Returns None for
    the globally-included files, whose map is genuinely not in their path."""
    parts = Path(rel).parts
    return parts[2] if len(parts) > 3 and parts[1] == "maps" else None


@lru_cache(maxsize=1)
def parse_item_sources() -> tuple[ItemSource, ...]:
    """Every place an item can be obtained, sorted by citation.

    Cached: the parse walks every script file and every map JSON once.
    """
    index, hidden = _event_index(paths.MAPS)

    texts = {p: p.read_text().splitlines() for p in _script_files(paths.DATA)}
    blocks = {p: _blocks(lines) for p, lines in texts.items()}
    refs: dict[str, set[str]] = {}
    #: {label: owning map} for every label DEFINED under data/maps/<Map>/.
    #: This is the second-chance map lookup for the globally-included files:
    #: the S.S. TICKET give lives in `data/scripts/players_house.inc`, but
    #: its caller `..._MapScripts` is defined in
    #: `data/maps/LittlerootTown_BrendansHouse_1F/scripts.inc:57`, and the
    #: directory of that definition is the map.
    home: dict[str, str] = {}
    for path, lines in texts.items():
        rel = f"{path.relative_to(paths.PRET)}"
        owner = _map_from_path(rel)
        for dest, callers in _refs(lines, blocks[path]).items():
            refs.setdefault(dest, set()).update(callers)
        if owner:
            for label, _, _ in blocks[path]:
                home.setdefault(label, owner)

    out: list[ItemSource] = list(hidden)
    for path, lines in texts.items():
        rel = f"{path.relative_to(paths.PRET)}"
        for label, start, end in blocks[path]:
            if _DEBUG_LABEL.search(label):
                continue
            for idx in range(start, end):
                m = _GIVE.match(lines[idx])
                if not m:
                    continue
                macro, item = m.group(1), m.group(2)
                flag, why = _guarding_flag(lines, idx, start, end)
                event, hops, visited = _locate(label, refs, index)
                kind = KIND_SCRIPT
                map_name = _map_from_path(rel) or ""
                x = y = None
                if event is not None:
                    map_name = event.map
                    x, y = event.x, event.y
                    # The object's own `flag` field is what the engine reads
                    # to decide whether to draw the ball at all, so it
                    # outranks anything the script says.
                    flag = event.flag or flag
                    kind = event.kind
                    why = None if flag else why
                else:
                    hops = 0
                    table = next(
                        (l for l in visited if _MAP_SCRIPT_TABLE.search(l)), None
                    )
                    map_name = map_name or next(
                        (home[l] for l in visited if l in home), ""
                    )
                    why = "; ".join(
                        filter(
                            None,
                            [
                                why,
                                f"reached from {table}, a map_script table, so "
                                f"the scene has no fixed coordinates"
                                if table
                                else f"no map.json event reaches {label} within "
                                f"{_CALLER_HOPS} caller hops",
                            ],
                        )
                    )
                out.append(
                    ItemSource(
                        item=item,
                        kind=kind,
                        map=map_name,
                        x=x,
                        y=y,
                        script=label,
                        flag=flag,
                        source_line=f"{rel}:{idx + 1}",
                        macro=macro,
                        unresolved=why,
                        hops=hops,
                    )
                )
    out.sort(key=_citation_key)
    return tuple(out)


def _citation_key(src: ItemSource):
    path, _, line = src.source_line.rpartition(":")
    return (path or src.source_line, int(line) if line.isdigit() else 0, src.item)


def unresolved_sources() -> tuple[ItemSource, ...]:
    """The rows the parser could not fully place. Exposed rather than
    swallowed: a silently-dropped source is exactly the defect this module
    exists to fix."""
    return tuple(s for s in parse_item_sources() if s.unresolved)


#: `Std_ObtainItem` is the body `giveitem` expands into
#: (`data/scripts/obtain_item.inc:1-5`), so its own `additem VAR_0x8000` is
#: every give site at once rather than a site of its own.
_STD_ITEM_SCRIPTS = ("data/scripts/obtain_item.inc",)


@lru_cache(maxsize=1)
def runtime_gives() -> tuple[str, ...]:
    """``('data/maps/SootopolisCity/scripts.inc:94  giveitem VAR_RESULT', ...)``

    Some gives take a *variable*, not a constant: the lottery prize
    (`data/maps/LilycoveCity_DepartmentStore_1F/scripts.inc:113`), the Berry
    Master's daily berry, the Route 113 glass reward, the department store
    rooftop vending machine. Which item those hand over is decided at
    runtime, so no parse can name it and :func:`parse_item_sources`
    deliberately does not invent a row. Listing the citations here is the
    honest alternative to dropping them silently.
    """
    out = []
    for path in _script_files(paths.DATA):
        rel = f"{path.relative_to(paths.PRET)}"
        if rel in _STD_ITEM_SCRIPTS:
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if m := _RUNTIME_GIVE.match(line):
                out.append(f"{rel}:{n}  {m.group(1)} {m.group(2)}")
    return tuple(out)


# ---- the HM roster ---------------------------------------------------


def hm_items(consts) -> dict[str, int]:
    """``{'ITEM_HM01_CUT': 339, ...}`` in HM number order.

    Derived from the ``ITEM_HM<nn>_<MOVE>`` names in
    ``include/constants/items.h``, so adding or renumbering an HM in the
    decomp changes this table without an edit here.
    """
    found = {
        name: value
        for name, value in consts.items.items()
        if re.match(r"^ITEM_HM\d\d_", name)
    }
    if not found:
        raise ValueError(
            "no ITEM_HM<nn>_* constants in constants/items.h -- the HM "
            "roster cannot be derived, refusing to guess it"
        )
    return dict(sorted(found.items(), key=lambda kv: kv[1]))


def hm_moves(emu, names, consts) -> dict[str, int]:
    """``{'CUT': MOVE_CUT, 'FLY': MOVE_FLY, ...}``, HM order.

    ``TMHMMoves`` (`src/party_menu.c:117-177`) is one u16 per machine,
    indexed by ``itemId - ITEM_TM01_FOCUS_PUNCH`` -- the same arithmetic
    ``ItemIdToBattleMoveId`` uses (`src/party_menu.c:3197`). Reading it
    means the move names are the game's own strings, spaces and all
    ("ROCK SMASH").
    """
    first = consts.items["ITEM_TM01_FOCUS_PUNCH"]
    count = (emu.sym.size("TMHMMoves") or 0) // 2
    if not count:
        raise ValueError("TMHMMoves has no size in the symbol table")
    out = {}
    for item_name, item_id in hm_items(consts).items():
        slot = item_id - first
        if not 0 <= slot < count:
            raise ValueError(
                f"{item_name} maps to TMHMMoves[{slot}], outside the "
                f"table's {count} entries"
            )
        move_id = emu.u16(("TMHMMoves", slot * 2))
        out[names.move(move_id)] = move_id
    return out


def field_moves(state, names=None) -> dict[str, str | None]:
    """``{'CUT': 'MUDKIP', 'FLY': None, ...}`` -- who can actually use each HM.

    "HM in the bag" is not "I can use it": the machine has to have been
    taught to a party member, and that member has to still be in the party.
    A ``None`` for FLY is the single fact that would have saved the Crystal
    run's hour-long walk.
    """
    names = names or state.names
    wanted = hm_moves(state.emu, names, state.consts)
    out: dict[str, str | None] = {move: None for move in wanted}
    by_id = {move_id: move for move, move_id in wanted.items()}
    for mon in state.party():
        for move_id in mon.moves:
            move = by_id.get(move_id)
            if move and out[move] is None:
                out[move] = mon.nickname or names.species(mon.species)
    return out


# ---- live evaluation -------------------------------------------------

KINDS = ("key", "hm", "all")


def is_key_item(item_id: int, names) -> bool:
    """Does this item change what the player can DO?

    ``gItems[].importance`` is the engine's own answer (`src/item.c:19`):
    non-zero means the item cannot be tossed or sold, which is exactly the
    HM / bike / ticket / key set. Reading it beats transcribing a list --
    the Crystal module's hand-written pocket check is what let the
    Devon Scope class of item slip through there.
    """
    return names.item_data(item_id).importance != 0


def _matches(kind: str, item_id: int, names, hm_ids) -> bool:
    if kind == "all":
        return True
    if kind == "hm":
        return item_id in hm_ids
    return item_id in hm_ids or is_key_item(item_id, names)


def missing_items(state, kind: str = "key", names=None) -> list[dict]:
    """Every source whose guarding flag is still clear, HMs first.

    ``kind`` is ``'key'`` (HMs plus everything ``gItems`` marks important),
    ``'hm'``, or ``'all'``. Rows are deduplicated on (item, flag) because
    HM08 DIVE genuinely has two sources behind one flag -- Steven's gift and
    a fallback item ball
    (`data/maps/MossdeepCity_StevensHouse/scripts.inc:41,126`) -- and
    listing it twice would only make the status line lie about how much is
    left.
    """
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, not {kind!r}")
    names = names or state.names
    item_ids = state.consts.items
    hm_ids = set(hm_items(state.consts).values())
    flags = state.consts.flags
    bag = {n for pocket in state.bag().values() for n in pocket}

    rows: list[dict] = []
    seen: set[tuple] = set()
    for src in parse_item_sources():
        item_id = item_ids.get(src.item)
        if item_id is None or not _matches(kind, item_id, names, hm_ids):
            continue
        if src.flag and src.flag in flags and state.flag(src.flag):
            continue
        item_name = names.item(item_id)
        # HOLDING IT IS THE ANSWER. A guarding flag is a proxy for "have you
        # been to the place"; the bag is ground truth for "do you have the
        # thing". HM08 DIVE was reported missing for this ENTIRE run while
        # sitting in the bag: it has two sources behind one flag (Steven's
        # gift and a fallback ball), Steven handed it over directly, so the
        # ball's flag never got set and the row never cleared. I planned
        # around that phantom for hours, and a scout independently proved the
        # cited coordinate is a FULL HEAL anyway
        # (data/maps/VictoryRoad_B2F/map.json:66-78 ->
        # item_ball_scripts.inc:532-534).
        #
        # Only UNIQUE items get this treatment. Under kind='all' a single
        # POTION in the bag must not hide every other potion on the map.
        if (item_id in hm_ids or is_key_item(item_id, names)) and item_name in bag:
            continue
        key = (src.item, src.flag)
        if key in seen:
            continue
        seen.add(key)
        why = src.unresolved
        if src.flag and src.flag not in flags:
            why = "; ".join(
                filter(None, [why, f"{src.flag} is not defined in constants/flags.h"])
            )
        rows.append(
            {
                "item": item_name,
                "const": src.item,
                "kind": src.kind,
                "map": src.map,
                "x": src.x,
                "y": src.y,
                "flag": src.flag,
                "hm": item_id in hm_ids,
                "source": src.source_line,
                "unresolved": why,
            }
        )
    rows.sort(key=lambda r: (not r["hm"], r["item"], r["source"]))
    return rows


def status_fragment(state, kind: str = "key", limit: int = 3, names=None) -> str:
    """``missing: HM02(Route119 25,31) HM03(PetalburgCity_WallysHouse 4,3) +9 more``.

    Short enough to append to :meth:`trek.Driver.status` unconditionally,
    which is the whole point: the fact has to appear where a session already
    looks, not behind a command nobody runs.
    """
    rows = missing_items(state, kind=kind, names=names)
    if not rows:
        return ""
    parts = []
    for row in rows[:limit]:
        where = row["map"] or "?"
        if row["x"] is not None:
            where += f" {row['x']},{row['y']}"
        parts.append(f"{row['item'].replace(' ', '')}({where})")
    if len(rows) > limit:
        parts.append(f"+{len(rows) - limit} more")
    return "missing: " + " ".join(parts)


# ---- the FLASH gate --------------------------------------------------


@lru_cache(maxsize=1)
def dark_maps() -> frozenset[str]:
    """Maps that are pitch dark without FLASH.

    ``requires_flash`` is a first-class boolean in every ``map.json`` and is
    read by ``mapdata_from_json`` into the map header, so unlike Crystal --
    where the requirement had to be inferred from a palette id and keying on
    the tileset invented requirements the game did not have -- there is
    nothing to deduce here.
    """
    out = set()
    for path in sorted(paths.MAPS.glob("*/map.json")):
        j = json.loads(path.read_text())
        if j.get("requires_flash"):
            out.add(j.get("name") or path.parent.name)
    return frozenset(out)


def needs_flash(map_name: str) -> bool:
    """Does this map need FLASH? Raises on an unknown map rather than
    answering False, which reads identically to "it is lit"."""
    if not (paths.MAPS / map_name / "map.json").exists():
        raise KeyError(f"no map named {map_name!r} under data/maps/")
    return map_name in dark_maps()
