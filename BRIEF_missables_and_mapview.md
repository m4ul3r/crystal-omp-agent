# BRIEF — two harness gaps that each cost a whole playthrough

From session claude-wren pt12 (see `PROGRESS.md`). Both are *model-facing
affordance* gaps, not crashes: the harness was correct and silent, and the
driving model paid for it in hours.

Repo: `/media/ssd/pokecrystal/crystal-agent`. Disassembly: `/media/ssd/pokecrystal`.
Run `.venv/bin/python -m pytest tests -q` before and after.

---

## GAP 1 — Nothing ever told me I was missing HM02 FLY

### What happened

I beat the Johto Elite Four and became Champion with **zero** of the 8 badges'
worth of convenience items audited. I had HM01/03/04/05/06/07 but **not HM02
FLY**, which had been sitting with Chuck's wife in Cianwood since I earned the
Storm Badge. Consequence: every journey in the entire run was on foot. In pt12
I walked New Bark → Cherrygrove → Violet → Union Cave → Azalea → (Ilex maze
failed) → back east → Violet → Ecruteak → Olivine → surfed Route 40/41 →
Cianwood just to pick it up. That is an hour of wall clock and hundreds of tool
calls that a one-line warning would have prevented.

Ground truth for the specific item:

```
maps/CianwoodCity.asm:86    checkevent EVENT_GOT_HM02_FLY
maps/CianwoodCity.asm:100   verbosegiveitem HM_FLY
maps/CianwoodCity.asm:102   setevent EVENT_GOT_HM02_FLY
maps/CianwoodCity.asm:415   object_event 10, 46, SPRITE_POKEFAN_F, ... CianwoodCityChucksWife
```

Same class of bug bit me twice more in one session:
- The S.S. Aqua sailor gates on `checkitem S_S_TICKET` (`maps/OlivinePort.asm:162`)
  and **only Prof. Elm** gives it (`maps/ElmsLab.asm:414`), post-Champion. I
  sailed to Olivine, got refused, and had to fly back to New Bark.
- Elm also hands out a MASTER BALL (`maps/ElmsLab.asm:402`) I never collected.

### The scale of the data

```
144  giveitem / verbosegiveitem sites across maps/*.asm
112  distinct items
```

Three distinct giver forms must be handled:
1. `verbosegiveitem <ITEM>` and `giveitem <ITEM>` — NPC gifts, usually guarded
   by a nearby `checkevent EVENT_GOT_...` / `setevent EVENT_GOT_...` pair.
2. `itemball <ITEM>` — ground items declared in `def_object_events`.
   **HM07 WATERFALL is one of these**: `maps/IcePath1F.asm:12  itemball HM_WATERFALL`.
3. TMs/key items behind `checkitem` gates elsewhere (the S.S. Ticket pattern):
   the *consumer* is in one map, the *giver* in another.

### What to build

**`crystalagent/missables.py`** — a parser plus a query surface.

1. `parse_item_sources(repo) -> list[ItemSource]` where `ItemSource` carries:
   `item` (constant name, e.g. `HM_FLY`), `kind` (`gift` | `itemball`),
   `map` (map const, from the filename via the existing map-const mapping),
   `x`, `y` (from the owning `object_event`, or the itemball's own object),
   `script` (label), `event` (the `EVENT_GOT_*` flag guarding it, when one
   exists), and `source_line` (`maps/Foo.asm:NNN`) so every row is citable.

   Associating a gift with its coordinates: the `verbosegiveitem` sits inside a
   named script; that script label appears as the last field of an
   `object_event` line in the same file. Resolve gift → script label →
   `object_event` → (x, y). Report rows where that resolution fails rather than
   guessing.

2. `Driver.missables(kind="key")` → the un-obtained subset, evaluated live:
   for each source with an `event`, read the flag through the existing
   `_event_flag`; for items without an event flag, fall back to "is it in the
   bag / was it ever obtained". Default `kind="key"` filters to the things that
   change what a player can *do* — HMs, key items, the S.S. Ticket, bicycle,
   Squirtbottle, cards/keys — with `kind="all"` for the full 112.

   Return rows like:
   ```
   {"item": "HM_FLY", "have": False, "map": "CIANWOOD_CITY", "x": 10, "y": 46,
    "event": "EVENT_GOT_HM02_FLY", "source": "maps/CianwoodCity.asm:100"}
   ```

3. `Driver.field_moves()` → the actually-usable overworld toolkit, because
   "HM in bag" is not the same as "I can use it":
   ```
   {"CUT": "GATOR", "SURF": "GATOR", "FLY": None, "STRENGTH": "GATOR", ...}
   ```
   i.e. per HM move: which party member knows it, or `None`. A `None` for FLY
   is the single fact that would have saved the entire pt12 detour.

4. **Make it impossible to miss.** Surface it where a session actually looks:
   - add a compact line to `Driver.status()` (or a `status(verbose=True)`) such
     as `missing: FLY(CIANWOOD_CITY 10,46) SSTICKET(ELMS_LAB)`, capped to a few
     entries;
   - a CLI `crystal missables [--all]` printing the table;
   - and mention it in `AGENTS.md`'s capability table plus the `BATTLE.md`-style
     pre-flight list.

### Tests (duck-typed fakes, no emulator boot)

- The parser finds `HM_FLY` at `CIANWOOD_CITY (10,46)` guarded by
  `EVENT_GOT_HM02_FLY`, and `HM_WATERFALL` as an `itemball` in `ICE_PATH_1F` —
  both asserted against the real disassembly files, with the file:line in the
  test docstring (this is the established pattern in `tests/unit/test_tactics.py`).
- With a fake whose `EVENT_GOT_HM02_FLY` is clear, `missables()` includes FLY;
  with it set, FLY disappears.
- `field_moves()` reports `FLY: None` when no party member knows FLY and the
  nickname when one does.
- All 7 HM constants appear somewhere in the parsed table (regression against a
  parser that silently handles only `verbosegiveitem`).

---

## GAP 2 — the ASCII map view makes the model count characters, and I miscounted

### What happened

`Driver.map_view()` (`trek.py:968`) renders a grid with a 5-character row-label
gutter and a two-line x-axis header (tens digits above units). To answer "what
is at x=15?" the model must count characters in a monospace row. I got this
wrong **at least three times in one session**:

- Ilex Forest: I read row 22 as walkable at x=16,17 and walked into a wall
  repeatedly ("unexplained blocked step" ×20).
- Olivine Port: I computed the pier warp at x=2 when it was at **x=3**.
- Vermilion Port Passage: I hunted for a city exit and only found it by
  grepping `warp_event` — it was at (15,0)/(16,0).

Every one of those was recoverable only by `grep warp_event maps/Foo.asm`,
which means **the disassembly was a better map interface than the harness's own
map renderer**. That is the bug.

### The fix: stop making the model parse art

The art is fine for a human glance. Add a *structured* surface next to it and
document that decisions must come from the structured one.

1. `Driver.tile_at(x, y, map_name=None) -> str` — one cell's terrain word,
   using the same classifier `observe()['tiles']` already uses (`_tile_kind`).

2. `Driver.tiles_in(x0, y0, x1, y1) -> dict[(x, y), str]` — a rectangle as a
   dict keyed by absolute coordinates. No gutters, no headers, no counting.

3. `Driver.find_tiles(kind, map_name=None) -> list[(x, y)]` — **the one I
   reached for constantly and did not have.** `kind` in
   `warp|water|grass|ledge|blocked|floor|buoy|npc`. Every single time I printed
   `map_view()` what I actually wanted was
   `find_tiles("warp") -> [(3,14), (3,2)]`. This alone removes most of the
   character counting from the driving loop.

4. `Driver.exits(map_name=None) -> list[dict]` — the map's warps *and* edge
   connections joined with their destinations, read from the same nav data
   `travel` already uses:
   `[{"x": 15, "y": 0, "to": "VERMILION_CITY"}, {"x": 3, "y": 14, "to": "VERMILION_PORT"}]`.
   This is exactly what I was grepping `warp_event` for by hand.

5. Keep `map_view()` but make it self-describing rather than something to be
   decoded: under the grid, print an annotation block listing the interesting
   cells by absolute coordinate, e.g.
   ```
   warps: (3,2)->VERMILION_PORT_PASSAGE  (15,0)->VERMILION_CITY
   npcs:  (10,46) (17,33)
   water: rows 12-33, x 4-19
   ```
   A model can act on that without counting; a human still gets the picture.

6. Document the rule in `AGENTS.md` next to existing gotcha 11 ("overworld
   screens decode as structure glyphs, not semantics"): **`map_view()` is a
   rendering for humans; `find_tiles`/`exits`/`tiles_in` are the interface for
   decisions.** Gotcha 11 already warns against reading the *screen*; it needs
   the same warning about reading the *map art*.

### Tests

- `find_tiles("warp")` on a synthetic grid returns exactly the warp coords;
  ordering stable (sorted).
- `tile_at` agrees with `observe()['tiles']` for the four neighbours of a known
  position (they must not be two different classifiers).
- `exits()` for a real map matches the `warp_event` lines in its `.asm`
  (e.g. `VERMILION_PORT_PASSAGE` → (15,0) and (16,0) to `VERMILION_CITY`,
  `maps/VermilionPortPassage.asm:23-24`).
- `map_view()`'s annotation block lists every warp `find_tiles("warp")` reports
  — i.e. the art and the data can never disagree.

---

## BONUS (same session, same cost class) — entering warps

Standing **on** a warp tile does not fire it; you must *enter* it. I lost turns
to this at the Ilex/Azalea gate, the Union Cave north mouth, the Olivine pier,
and three ship cabin doors. The working move every time was: step off, then tap
back on (`_step_warp_tap`), or hold the key through the step.

Add `Driver.take_warp(x, y)`: route adjacent, enter with the held/tapped step,
verify the map changed, and report a distinct reason if it did not. Then make
`travel()` use it — `travel` currently fails with
`warp D at (3, 41) -- expected ILEX_FOREST_AZALEA_GATE ... (step result: blocked)`
when the answer is simply "you were already standing on it".

Also: `travel()` cannot cross several map-edge connections. It failed at
Azalea's east edge (39,13) when the real connection row is (39,**14**), and at
Route 32 → Violet (8,0). I hand-wrote a `cross(direction)` helper that slides
along the edge ±6 cells and retries with a held step; that behaviour belongs
inside `travel`.

Finally, two false-positive/UX items observed live:
- The money guard fires on *winnings*: `MONEY +216 ... during goto -- movement
  must never spend money`. It should only warn on a **decrease**.
- FLY is outdoor-only, and a failed indoor attempt leaves the party menu open
  with "Can't use that here", which **blocks all movement** until B'd out
  (gotcha 7). Any field-move helper must clean up its UI on every failure path.
