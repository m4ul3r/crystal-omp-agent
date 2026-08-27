"""Which collectable items exist in the world, and which are still out there.

Written because a full Johto playthrough reached Champion without HM02 FLY:
it had been sitting with Chuck's wife in Cianwood since the Storm Badge
(`maps/CianwoodCity.asm:100  verbosegiveitem HM_FLY`), and nothing in the
harness ever said so. Every journey of that run was on foot, and the
recovery walk cost an hour of wall clock. The same class of silence cost a
refused S.S. Aqua boarding (`maps/OlivinePort.asm:162  checkitem
S_S_TICKET`, given only by Prof. Elm at `maps/ElmsLab.asm:414`) and an
uncollected MASTER BALL.

Three giver forms exist in the scripts and all three matter:

- ``verbosegiveitem <ITEM>`` / ``giveitem <ITEM>`` -- an NPC gift, usually
  guarded by a nearby ``checkevent``/``setevent EVENT_GOT_*`` pair. The
  coordinates are not on the give line: the give sits inside a named
  script, and that script label is the second-to-last field of an
  ``object_event``.
- ``itemball <ITEM>`` -- a ball on the ground, declared as an
  ``OBJECTTYPE_ITEMBALL`` object_event whose LAST field is its event flag.
  **HM07 WATERFALL is one of these** (`maps/IcePath1F.asm:12`), so a
  parser that only understood NPC gifts would miss an HM.
- items behind a ``checkitem`` gate in a DIFFERENT map from their giver
  (the S.S. Ticket pattern). Nothing extra is needed for those: the giver
  itself is a gift site, which is exactly what was missing.

Nothing here is hardcoded game data. Item pockets come from
`data/items/attributes.asm`, HM numbering from the `add_tm`/`add_hm` order
in `constants/item_constants.asm`, event bits from
`constants/event_flags.asm`, and map constants from the same
filename<->const pairing nav already uses.
"""

import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .asmconst import parse_const_defs

# `verbosegiveitem HM_FLY`, `giveitem POKE_BALL, 5`, `itemball HM_WATERFALL`
_GIVE = re.compile(
    r"^\s+(verbosegiveitem|giveitem|itemball)\s+([A-Z][A-Z_0-9]*)\s*(?:,|;|$)")
_TOP_LABEL = re.compile(r"^([A-Za-z_][\w]*):")
_SUB_LABEL = re.compile(r"^\.[\w]+:")
_SETEVENT = re.compile(r"^\s+setevent\s+(EVENT_GOT_\w+)")
_CHECKEVENT = re.compile(r"^\s+checkevent\s+(EVENT_GOT_\w+)")
# object_event x, y, SPRITE, MOVEMENT, r1, r2, -1, -1, PAL, TYPE, n, SCRIPT, EVENT
_OBJECT = re.compile(r"^\s+object_event\s+(.+)$")

# How far from a give line to look for its guarding flag. The window is
# small on purpose: ElmsLab's ProfElmScript hands out three different items
# with three different EVENT_GOT_* flags in one script, so "any setevent in
# this script" would pick the wrong one.
_EVENT_LOOKAHEAD = 8
_EVENT_LOOKBEHIND = 14

_GIVE_KINDS = {"verbosegiveitem": "gift", "giveitem": "gift",
               "itemball": "itemball"}


@dataclass(frozen=True)
class ItemSource:
    """One place in the world an item can be obtained.

    ``x``/``y`` are the giver's walk-cell coordinates, or None when the
    give lives in a script no object_event points at (a map script, a
    coord_event cutscene). Those rows are reported, never guessed at.
    """
    item: str            # item constant, e.g. 'HM_FLY'
    kind: str            # 'gift' | 'itemball'
    map: str             # map constant, e.g. 'CIANWOOD_CITY'
    x: int | None
    y: int | None
    script: str          # the script label the give sits in
    event: str | None    # guarding EVENT_GOT_* flag, when there is one
    source_line: str     # 'maps/CianwoodCity.asm:100'

    def as_dict(self):
        return asdict(self)


def _object_events(lines):
    """{script label: (x, y, event or None)} for one map file's objects."""
    out = {}
    for line in lines:
        m = _OBJECT.match(line)
        if not m:
            continue
        fields = [f.strip() for f in m.group(1).split(",")]
        if len(fields) < 13:
            continue
        try:
            x, y = int(fields[0]), int(fields[1])
        except ValueError:
            continue
        script, event = fields[-2], fields[-1]
        out.setdefault(script, (x, y, None if event == "-1" else event))
    return out


def parse_map_objects(path):
    """Every ``object_event`` in one ``maps/<Camel>.asm``, in declaration
    order (which is the map's own object_const_def order):

        [{'x': 3, 'y': 7, 'sprite': 'SPRITE_NURSE',
          'movement': 'SPRITEMOVEDATA_STANDING_DOWN',
          'script': 'IndigoPlateauPokecenter1FNurseScript', 'event': None}]

    Coordinates are walk cells, the same space as `state`'s x,y. This is
    how a helper finds the NPC it needs to talk to instead of assuming a
    layout: heal_pokecenter assumed the Johto counter (3,3) and could not
    heal at INDIGO_PLATEAU_POKECENTER_1F, whose nurse stands at (3,7)
    behind a row-8 counter (FUCK_I_MESSED_UP.md #78)."""
    out = []
    for line in Path(path).read_text(errors="replace").splitlines():
        m = _OBJECT.match(line)
        if not m:
            continue
        fields = [f.strip() for f in m.group(1).split(",")]
        if len(fields) < 13:
            continue
        try:
            x, y = int(fields[0]), int(fields[1])
        except ValueError:
            continue
        out.append({"x": x, "y": y, "sprite": fields[2],
                    "movement": fields[3], "script": fields[-2],
                    "event": None if fields[-1] == "-1" else fields[-1]})
    return out


def _guarding_event(lines, idx, top_label):
    """The EVENT_GOT_* flag guarding the give at `lines[idx]`.

    Forward first (`setevent` right after a successful give is the
    engine's own idiom), then backward (`checkevent ... iftrue .Got`),
    never leaving the give's own top-level script.
    """
    for j in range(idx + 1, min(idx + 1 + _EVENT_LOOKAHEAD, len(lines))):
        if _TOP_LABEL.match(lines[j]):
            break
        m = _SETEVENT.match(lines[j])
        if m:
            return m.group(1)
    for j in range(idx - 1, max(idx - 1 - _EVENT_LOOKBEHIND, -1), -1):
        m = _TOP_LABEL.match(lines[j])
        if m:
            if m.group(1) != top_label:
                break
            continue
        m = _CHECKEVENT.match(lines[j])
        if m:
            return m.group(1)
    return None


_REF = re.compile(r"^\s+(?:iftrue|iffalse|sjump|jump|callasm|call)\s+"
                  r"([A-Za-z_]\w*)\s*$")


def _callers(lines):
    """{label: [top-level scripts that jump to it]}.

    A give often lives in a helper script no object_event points at:
    ElmsLab's `ElmGiveTicketScript` (the ONLY source of the S.S. Ticket)
    is reached by `iftrue` from `ProfElmScript`, which is the object at
    (5,2). One hop of indirection turns "somewhere in ELMS_LAB" into
    "talk to the NPC at (5,2)".
    """
    out, top = {}, ""
    for line in lines:
        m = _TOP_LABEL.match(line)
        if m:
            top = m.group(1)
            continue
        m = _REF.match(line)
        if m and top:
            out.setdefault(m.group(1), []).append(top)
    return out


# How many script hops to follow back toward an object_event. ElmsLab
# needs two for the MASTER BALL (ElmGiveMasterBallScript <- ElmCheckMasterBall
# <- ProfElmScript, the object at (5,2)); the bound stops a cyclic chain.
_CALLER_HOPS = 3


def _object_via_callers(label, callers, objects, hops=_CALLER_HOPS):
    """(x, y, event) of the nearest object_event whose script chain
    reaches `label`, or (None, None, None)."""
    seen, frontier = {label}, [label]
    for _ in range(hops):
        nxt = []
        for name in frontier:
            for caller in sorted(callers.get(name, ())):
                if caller in seen:
                    continue
                if caller in objects:
                    return objects[caller]
                seen.add(caller)
                nxt.append(caller)
        if not nxt:
            break
        frontier = nxt
    return (None, None, None)


def parse_map_item_sources(path, map_const):
    """Every item source in ONE map file.

    Coordinates come from the `object_event` naming the give's own script;
    failing that, from the object_event of a script that JUMPS to it (see
    `_callers`). When neither resolves, x/y are None -- reported, never
    guessed.
    """
    lines = Path(path).read_text(errors="replace").splitlines()
    objects = _object_events(lines)
    callers = _callers(lines)
    rel = f"maps/{Path(path).name}"
    out, top = [], ""
    for i, line in enumerate(lines):
        m = _TOP_LABEL.match(line)
        if m:
            top = m.group(1)
        elif _SUB_LABEL.match(line):
            pass                      # sub-labels belong to `top`
        m = _GIVE.match(line)
        if not m:
            continue
        verb, item = m.group(1), m.group(2)
        kind = _GIVE_KINDS[verb]
        x, y, obj_event = objects.get(top, (None, None, None))
        if x is None:
            x, y, obj_event = _object_via_callers(top, callers, objects)
        event = obj_event if kind == "itemball" else None
        if event is None:
            event = _guarding_event(lines, i, top)
        out.append(ItemSource(item=item, kind=kind, map=map_const,
                              x=x, y=y, script=top, event=event,
                              source_line=f"{rel}:{i + 1}"))
    return out


_sources_cache = {}


def parse_item_sources(repo, map_const_of=None):
    """Every ``giveitem``/``verbosegiveitem``/``itemball`` site in maps/.

    ``map_const_of(stem)`` maps a ``maps/<Camel>.asm`` stem to its map
    constant; without one the stem is used as-is.
    """
    repo = Path(repo)
    key = str(repo.resolve())
    if key in _sources_cache:
        return _sources_cache[key]
    out = []
    for path in sorted((repo / "maps").glob("*.asm")):
        const = map_const_of(path.stem) if map_const_of else path.stem
        out.extend(parse_map_item_sources(path, const))
    _sources_cache[key] = out
    return out


_POCKET = re.compile(r"^\s+item_attribute\s+(.+)$")
_ITEM_COMMENT = re.compile(r"^;\s*([A-Z][A-Z_0-9]*)\s*$")


def parse_item_pockets(repo):
    """``{ITEM_CONST: pocket}`` from data/items/attributes.asm.

    Pocket is the game's own field: ``ITEM`` / ``KEY_ITEM`` / ``BALL`` /
    ``TM_HM``. Each entry is preceded by a ``; ITEM_NAME`` comment, which
    is how the file names the row it is about.
    """
    path = Path(repo) / "data/items/attributes.asm"
    out, pending = {}, None
    for line in path.read_text().splitlines():
        m = _ITEM_COMMENT.match(line)
        if m:
            pending = m.group(1)
            continue
        m = _POCKET.match(line)
        if m and pending:
            fields = [f.strip() for f in m.group(1).split(",")]
            if len(fields) >= 5:
                out[pending] = fields[4]
            pending = None
    return out


def is_key_item(item, pockets):
    """Does this item change what the player can DO?

    The game's own KEY_ITEM pocket, plus the HMs -- which live in the
    TM_HM pocket but are the entire reason this module exists.
    """
    return item.startswith("HM_") or pockets.get(item) == "KEY_ITEM"


def hm_moves(repo):
    """``{'HM01': 'CUT', ...}`` -- the HM tags and the move each teaches."""
    from .tactics import parse_tmhm_moves
    return {tag: move for tag, move in parse_tmhm_moves(repo).items()
            if tag.startswith("HM")}


def hm_item_tags(repo):
    """``{'HM_FLY': 'HM02'}`` -- item constant to its bag key.

    The bag stores HMs by NUMBER (`_bag()` reads wTMsHMs), while the
    scripts name them `HM_FLY`, so obtaining one cannot be checked
    without this mapping.
    """
    return {f"HM_{move.replace('_', '')}": tag
            for tag, move in hm_moves(repo).items()}


def _norm(text):
    return re.sub(r"[^A-Z0-9]", "", str(text).upper())


def item_in_bag(item, bag, repo):
    """Is item constant `item` in a `Driver._bag()`-shaped dict?

    Bag keys are normalised DISPLAY names ('SSTICKET' for 'S.S.TICKET')
    plus 'HM02'-style TM/HM tags, so both spellings are tried.
    """
    tag = hm_item_tags(repo).get(item)
    if tag:
        return bool(bag.get(tag))
    want = _norm(item)
    return any(_norm(k) == want and v for k, v in bag.items())


def missing_items(sources, *, have_event, bag, repo, kind="key",
                  pockets=None):
    """The un-obtained subset of `sources`, HMs first.

    ``have_event(flag) -> bool | None`` answers "is this event flag set?"
    (None = unknown flag). An item that is demonstrably IN THE BAG counts
    as obtained whatever the flag says -- the question a session asks is
    "do I have FLY?", and nagging about something already held would train
    the reader to ignore the line. Items with no guarding flag are judged
    by the bag alone, which is why a consumable bought and used again can
    reappear; ``kind='key'`` (the default) filters to the things that
    change what a player can DO, where that cannot happen.
    """
    pockets = pockets if pockets is not None else parse_item_pockets(repo)
    rows, seen = [], set()
    for s in sources:
        if kind == "key" and not is_key_item(s.item, pockets):
            continue
        dedup = (s.item, s.map, s.x, s.y)
        if dedup in seen:
            continue
        seen.add(dedup)
        if item_in_bag(s.item, bag, repo):
            continue
        if s.event and have_event(s.event):
            continue
        row = s.as_dict()
        row["have"] = False
        row["source"] = row.pop("source_line")
        rows.append(row)
    rows.sort(key=lambda r: (not r["item"].startswith("HM_"), r["item"],
                             r["map"]))
    return rows


def status_fragment(rows, limit=3):
    """``missing: FLY(CIANWOOD_CITY 10,46) SSTICKET(ELMS_LAB) +7 more``.

    Deliberately short: this rides on `Driver.status()`, which is printed
    after almost every command, and the whole point is that a session
    cannot avoid seeing it.
    """
    if not rows:
        return ""
    parts = []
    for r in rows[:limit]:
        name = r["item"].removeprefix("HM_").replace("_", "")
        where = r["map"]
        if r["x"] is not None:
            where += f" {r['x']},{r['y']}"
        parts.append(f"{name}({where})")
    if len(rows) > limit:
        parts.append(f"+{len(rows) - limit} more")
    return "missing: " + " ".join(parts)


def event_bits(repo):
    """``{EVENT_NAME: bit index into wEventFlags}``."""
    return parse_const_defs(Path(repo) / "constants/event_flags.asm")


# -- which maps are pitch dark (the FLASH gate) -------------------------
#
# `data/maps/maps.asm` carries one `map` line per map:
#     map <Name>, <TILESET_*>, <ENV>, <LANDMARK_*>, <MUSIC_*>, <phone>,
#         <PALETTE_*>, <FISHGROUP_*>
#
# The FLASH requirement tracks the PALETTE, not the tileset. Checked
# against the source: RockTunnel1F is TILESET_DARK_CAVE *and*
# PALETTE_DARK, but MountMortar1FInside is TILESET_DARK_CAVE with
# PALETTE_NITE and needs no FLASH, and the whole IcePath is
# TILESET_ICE_PATH/PALETTE_NITE. Keying on the tileset would invent
# requirements that the game does not have.
DARK_PALETTE = "PALETTE_DARK"

_map_flag_cache = {}


def parse_map_flags(repo):
    """``{CamelCaseMapName: {"tileset", "environment", "palette"}}`` from
    data/maps/maps.asm."""
    key = str(repo)
    if key in _map_flag_cache:
        return _map_flag_cache[key]
    out = {}
    path = Path(repo) / "data/maps/maps.asm"
    for line in path.read_text().splitlines():
        m = re.match(r"\s*map\s+(\w+),\s*(\w+),\s*(\w+),\s*\w+,\s*\w+,"
                     r"\s*\w+,\s*(\w+)", line)
        if m:
            out[m.group(1)] = {"tileset": m.group(2),
                               "environment": m.group(3),
                               "palette": m.group(4)}
    _map_flag_cache[key] = out
    return out


def dark_map_names(repo):
    """CamelCase names of every map that is pitch dark without FLASH."""
    return {name for name, f in parse_map_flags(repo).items()
            if f["palette"] == DARK_PALETTE}
