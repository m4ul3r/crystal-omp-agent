# BRIEF — build the emulator-in-the-loop test lane (`tests/integration/`)

## Why this exists

593 unit tests are green and **not one of them boots the emulator**. Every
significant bug of this project's history was found by a model playing the game
for an hour, never by a test:

| bug | how it was found |
|---|---|
| `_parse_types` ignored `const_next 19` → every SPECIAL type id off by 9, so **all** special-type matchups silently read 1.0 | noticed `FAINT ATTACK x1` into a PSYCHIC mon mid-battle |
| accuracy read as `min(byte,100)` → **every** move reported 100% | noticed IRON TAIL "100%" whiffing repeatedly |
| out-of-battle `use_item` returned False with no log, leaving the START menu open | four failed heals in a row |
| trapped-switch wedge (`can't be recalled`) burned 60 "fights" / 535 s / 0 exp | dumped the frozen screen by hand |
| HM02 FLY never collected in a whole playthrough | realised it after becoming Champion |

Unit tests with duck-typed fakes are excellent at *locking in* a fix. They
structurally cannot *find* these, because the thing they fake is the thing that
lies.

The harness already has the property that makes integration testing cheap and
reliable: **same savestate + same inputs ⇒ byte-identical result, RNG included**
(`AGENTS.md` gotcha 9). That determinism is currently unused for testing.

## The lane already has a designated home

`pyproject.toml` declares:

```toml
markers = [
    "unit: pure logic, no emulator",
    "integration: drives the emulator",
]
```

and `tests/test_markers.py` enforces "every test function carries a marker
matching its parent directory". So the target is **`tests/integration/`** with
`pytestmark = pytest.mark.integration`. Nobody ever populated it. Do that.

## Requirements

1. **Excluded from the default run.** Change `addopts` to `-q -m "not integration"`
   so `pytest` stays the fast unit lane. Document the explicit invocation
   (`pytest -m integration`) in `AGENTS.md` next to the existing
   "keep `pytest tests` green" line.

2. **Never touch a milestone savestate.** Every scenario forks
   `claude_saves/<milestone>.state` **plus its `.meta` sidecar** into a temp path,
   boots there, and deletes the fork in teardown even on failure. A test that
   mutates a milestone is a worse bug than the one it catches. Assert in a
   fixture that the milestone's mtime/hash is unchanged after the run.

3. **Boot cost is ~1 s.** Share a booted `Driver` per scenario module where the
   scenario allows it; otherwise accept the cost. Report total wall time.

4. **Assert observable game state, not log text.** Positions, map names, HP,
   bag quantities, event flags. Log strings are allowed only when the log *is*
   the contract (e.g. the money guard's polarity).

## Seed scenarios — each one is a bug that actually happened

Draw the forks from `claude_saves/`. `wren-kanto.state` (post-Fly, in Kanto),
`wren-champion.state` (8 badges, **no Fly**), `wren-pre-e4.state`,
`wren-fly.state`, `wren-ssaqua.state` exist. Pick per scenario and say which.

1. **`_slide_edge` / map-edge connection.** `travel` used to fail crossing
   Azalea Town's east edge at `(39,13)` when the real connection row is
   `(39,14)`, and Route 32 → Violet at `(8,0)`. This is the one piece of the
   last batch that is unit-tested only, precisely because a fake cannot
   reproduce an off-by-one edge band. Assert the map actually changes.

2. **`take_warp` on a warp you are standing on.** Standing *on* a warp does not
   fire it; you must enter it. Cost real turns at the Ilex/Azalea gate, the
   Union Cave north mouth `(17,3)`, the Olivine pier, and three ship cabin
   doors. Assert: called from two cells away, called while already on the tile,
   and called with stale coordinates (must refuse with a distinct reason, not
   wander).

3. **`goto` escalation on a lying collision grid.** The Indigo Plateau
   Pokecenter nurse cell `(3,9)→(3,8)` replan-stormed on every heal all session.
   Assert the heal completes and that the escalation-to-savestate-search path is
   exercised.

4. **Trapped-switch refusal.** In a battle where the active mon is trapped
   (`wPlayerWrapCount != 0`), a policy that asks to switch must not wedge: the
   refusal is dismissed, the menu closed, and the battle still resolves. This is
   the 535-second bug. Use a fork mid-battle if you can make one, otherwise
   drive into a Bind user and poke the wrap counter **in the fork only** (that
   is test scaffolding, never runtime code — see `scripts/verify_hardening.py`
   for the established convention and its caveat about which stages are
   faithful when poked).

5. **`missables()` both directions.** On `wren-champion.state` (flag clear),
   assert the FLY row appears with `map=CIANWOOD_CITY, x=10, y=46,
   event=EVENT_GOT_HM02_FLY, source=maps/CianwoodCity.asm:100` and that
   `status()` contains `FLY(CIANWOOD_CITY 10,46)`; on `wren-kanto.state`
   (flag set) assert FLY is absent and `field_moves()['FLY'] == 'REED'`.
   This is the regression that guards the "walked the whole game" class of miss.

6. **The map interface agrees with the disassembly.** `exits("VERMILION_PORT_PASSAGE")`
   contains `(15,0)` and `(16,0)` → `VERMILION_CITY` (grep `warp_event` in
   `maps/VermilionPortPassage.asm:23-24` to source the expectation), and
   `tile_at` agrees with `observe()['tiles']` for all four neighbours of the
   booted position. Two classifiers that can drift apart is the bug this pins.

Add a 7th of your own choosing if you find a cheap high-value one while working.

## Also promote the existing one-off

`scripts/verify_hardening.py` is already ~80% of this pattern (live checks on a
fork). Fold what it proves into the lane so it becomes repeatable rather than a
script someone remembers to run. Keep or delete the script as you judge, but do
not leave two divergent copies of the same check.

## File ownership — a sibling agent is working in parallel

**You own:** `tests/integration/**`, `tests/conftest.py`, `pyproject.toml`
(`addopts` only), `scripts/verify_hardening.py`, and the `AGENTS.md` line about
running tests.

**Do NOT edit:** `trek.py`, `crystalagent/tactics.py`, `crystalagent/decide.py`.
A sibling agent owns the battle modules right now, and `trek.py` is a 6,455-line
collision hazard. If a scenario exposes a real harness bug, **report it in your
final message and leave a failing/xfail test** rather than fixing it — the fix
gets serialized afterwards.

## Acceptance

- `pytest` (default) stays green and still excludes the emulator lane.
- `pytest -m integration` passes, and you report its wall time and which
  savestate each scenario forked.
- No milestone `.state` or `.meta` modified (prove it).
- Each scenario's docstring names the historical bug it guards.
