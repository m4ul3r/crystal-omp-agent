# RETROSPECTIVE — crystal-agent harness, sessions claude-wren pt1–pt11

Written for an agent tasked with improving this harness. It is organised as:
what the harness is for, what demonstrably works (keep and extend), what is
broken or weak (fix), the **recurring failure patterns** that matter more than
any individual bug, and a prioritised backlog with acceptance criteria.

Repo: `/media/ssd/pokecrystal/crystal-agent`. Disassembly: the parent dir
`/media/ssd/pokecrystal`. Live journal: `PROGRESS.md` (newest section first).
Combat doctrine: `BATTLE.md`. Session protocol and gotchas: `AGENTS.md`.

Current state: **479 unit tests, ~16 s, all green.** `trek.py` is 5,538 lines;
`crystalagent/` is ~4,000 across 16 modules.

---

## 0. What was accomplished (so the baseline is clear)

A full playthrough driven by a model: fresh boot → starter → 8 badges →
Champion, then **two further Elite Four clears where the model chose every
single action, turn by turn**, with the overlevelled Feraligatr benched
(verifiably: it ended L78 293/293, never in a battle).

Along the way the harness gained: decision-first battles, a real Gen-2
damage/type engine, savestate-search navigation, live-sprite navigation,
clamped grinding, out-of-battle item use, and a battle-mechanics doctrine file.

Five harness bugs found were **silently wrong answers**, not crashes. That is
the single most important finding in this document; see §3.

---

## 1. What works well — keep and extend

### 1.1 Everything is derived from the disassembly, nothing hardcoded

Type chart, type ids, move power/type/accuracy/effect, effect constants, badge
boost table, species types, map warps, trainer parties — all parsed from
`/media/ssd/pokecrystal` or read from the ROM via `pokecrystal.sym`. This is
why bugs were *findable*: when a number looked wrong, there was a file to check
it against. **Do not introduce hardcoded game data.**

### 1.2 `crystalagent/tactics.py` (514 lines) — the model's instrument

```python
a = d.outlook()                                  # live analysis or None
print(d.tactics.explain(a))                      # one auditable line per move
action, why = d.tactics.recommend(a, d.battle_frame())
```

`outlook()` gives, per move: type multiplier, physical/special category (which
in Gen 2 is per **TYPE**), damage span, `ko_certain`/`ko_possible`,
`hits_to_ko`, real accuracy, `never_misses`, live PP, engine slot — plus the
enemy's moves aimed back and who moves first. This turned battles from
"pick highest power" into decisions with stated reasons, and it is what exposed
three separate data bugs within minutes of first use.

Extend this, don't replace it. Gaps listed in §2.6.

### 1.3 Model-in-the-loop turn control

`d.decide_all = True` + `d.fight(policy=..., require_decision=True)` raising
`trek.DecisionRequired`, resumed by queueing one action per call. Clean,
resumable, and the battle state survives the exception. This is the mechanism
that made "the model decides" real rather than aspirational.

**Architectural constraint worth documenting loudly:** battles cannot be
delegated to a subagent. The emulator lives in the deciding process's Python
kernel; subagents get independent kernels and would have to boot a fresh
`Driver` from a savestate, which is impossible mid-battle.

### 1.4 Savestate discipline

Forking (`claude_saves/<agent>-pre-<thing>.state` + `.meta`) with pyboy/ROM
stamps that refuse mismatched loads. Determinism (same state + same inputs =
identical result, RNG included) made every bug reproducible. Used constantly;
never once let us down.

### 1.5 Savestate-search navigation: `explore_bfs` / `reach`

Several floors' decoded collision grids are simply wrong (Victory Road, the
Rocket base, Ice Path, the Indigo Plateau north corridor). `d.reach(x, y)`
(goto, then BFS over settled directional moves using in-memory savestates as
nodes) is the only thing that reliably crosses them. Genuinely excellent
engineering; it rescued multiple stuck sessions.

### 1.6 Auditability features that paid for themselves

- `d.last_battle` — `.rows()`, `.summary()`, `.free_hits()`. The turn log is
  how we learned a switch policy had handed Koga ~10 free hits.
- The loud `auto: attack slot 0 (SURF) -- the HARNESS is choosing` line, so
  auto-pilot can never be silent again.
- `d.tactics.explain()` — the per-move table.
- `d.last_goto_reason` — navigation failures name themselves.

### 1.7 Test strategy

479 tests in ~16 s, duck-typed fakes, **no emulator boots**. Fakes are built by
writing real WRAM layout arithmetic (see `tests/unit/test_decide_frame.py`'s
`FakeEmu`/`FakeSym`), so they test real decoding rather than mocks agreeing with
themselves. Several tests parse the *actual* disassembly files
(`tests/unit/test_tactics.py` uses `_parse_types`/`_parse_matchups` on the real
sources), which is why the type-id regression is now impossible to reintroduce.

### 1.8 `PROGRESS.md` journalling

Newest-section-first, with live evidence, exact coordinates, and failure
signatures. This is the reason an 11-session arc stayed coherent. Keep writing
it; keep it evidence-first rather than narrative.

### 1.9 Subagent delegation for self-contained fixes

Worked well when the task was: one bug, live-verifiable outside a battle,
explicit acceptance criteria, own savestate fork, "run only your own tests".
Both `LockedTurnFixer` and `ItemFixer` root-caused things correctly — and
**both disproved the coordinator's stated hypothesis**, which is the point of
asking for verification before coding.

---

## 2. What does not work well — fix these

Ordered by how much damage each caused.

### 2.1 Silent wrong answers in data parsing (WORST CATEGORY)

Three separate instances, all "the harness confidently returned a wrong number
for a long time":

| bug | effect | status |
|---|---|---|
| `_parse_types` ignored `const_next 19` (`constants/type_constants.asm:22`) | every SPECIAL type id off by 9 → **every special-type matchup lookup missed the chart and returned a flat 1.0**: no super-effective, no resistance, no immunity, for Fire/Water/Grass/Electric/Psychic/Ice/Dragon/Dark | fixed + regression test |
| accuracy read as `min(byte, 100)` when accuracy is a 0–255 scale (`macros/data.asm:23`, `percent EQUS "* $ff / 100"`) | **every move above ~39% reported as 100% accurate**; Iron Tail (75) and Dynamicpunch (50) looked perfectly reliable while whiffing | fixed + test |
| `battle_frame()['can_switch']` listed party indexes without checking legality | the frame *promised* legal targets and lied while trapped, so policies proposed illegal switches | fixed |

**Action for the improving agent:** audit every other parser in
`crystalagent/` the same way — cross-check a parsed table against the
disassembly *by value*, not by "it loads without error". Candidates:
`names.py`, `nav.py` (warps/connections), `charmap.py`, base-stat parsing in
`tactics.py`, item lists in `battle.py`. Add value-asserting tests
(e.g. "Dark→Psychic is 2.0", "Iron Tail accuracy is 75") for each.

### 2.2 Silent no-ops: functions that return False without saying why

`use_item` out of battle returned `False` **with no log line at all** for two
whole sessions, because `Menus.select_label('PACK')` reported success from the
cursor glyph alone without verifying the pack opened; the swallowed A left the
START menu open, which then ate the *next* caller's input. Identical calls
alternated between working and failing, which is maximally confusing.

`teach_tm` — actually not in the harness at all. I hand-rolled it in my kernel
and it returned `'cursor-miss'`; I drove the TM pocket manually instead.

**Action:** every menu-driving primitive must (a) verify the *state it
intended to reach*, not the keypress, and (b) log a distinct reason on every
failure path. A silent `return False` should be considered a bug. Consider a
shared `_expect_state(pred, what)` helper so the pattern is uniform.

### 2.3 Menu driving is fragile in three specific ways

1. **Two cursor glyphs.** `▶` (U+25B6) for battle/scrolling menus, `▷`
   (U+25B7) for static ones — and both appear **simultaneously** (battle party
   list + its SWITCH submenu), and a list flips from one to the other when a
   textbox overlays it. A single-glyph reader goes blind. Cost me a mid-shop
   failure and contributed to the battle-switch wedge. `menus._cursor_xs()`
   now returns all positions; make sure every reader uses it.
2. **Input swallowed on the frame a menu is drawn** (`AGENTS.md` gotcha 2).
   Pervasive root cause; see 2.2.
3. **Scrolling lists re-index as items are consumed.** The TM pocket shifted
   after a TM was used, so an index-based pick landed on the wrong item — I
   started teaching **Attract to my Dragonite** and caught it only at the
   "Delete an older move?" prompt. Always locate by reading the live cursor
   row, never by remembered index.

### 2.4 Navigation: chronic `goto` replan-storms

`trek.py:4280` — `replan-storm (20 replans)`. This fires constantly and
harmlessly-looking, e.g. `INDIGO_PLATEAU_POKECENTER_1F (3,9) -> (3,8)` on
essentially every heal attempt, and it is the single noisiest thing in the
logs. Two distinct causes are conflated: (a) genuinely wrong static collision
data, and (b) a live NPC standing on the only path. `reach()` fixes (a) in
practice, so **`goto` should escalate to the savestate search automatically
instead of giving up 20 replans later**, and the "NPC in the way" case should
wait/route rather than storm.

Also: `travel()` refused the Plateau→Will corridor entirely
("no path from (16,5) to (16,1)") where a plain `step_hold("U")` walks it.

### 2.5 Wedges: spinning on an unchanged screen

The worst single time sink of the whole arc (~9 minutes of wall clock in one
session, 60 "fights" with zero exp). Cause: a switch requested against a
**trapped** mon; the engine refuses with `<MON> can't be recalled!`
(`TryPlayerSwitch .check_trapped`, no turn consumed), the harness left the
party menu open, the screen stopped changing, `fight()` logged
`frozen screen` 20–30× and returned `'timeout'` **with the battle still
live** — so the next `pace()` walked straight back in.

Now fixed (unchanged-screen breaker, forced-turn waits, diagnostic capped at 3,
`UNRESOLVED ... battle is STILL LIVE` reporting). **The generalisable rule:
never re-send an action that changed nothing; never report an unresolved battle
as finished; cap repeated diagnostics.** Apply that rule to every other
input-driving loop in the harness, not just `fight()`.

### 2.6 `tactics.py` gaps (the engine is good; these are missing)

- **No accuracy/evasion stage modelling.** `outlook()` reports listed accuracy;
  it cannot see that Minimize/Double Team or Sand Attack/Smokescreen have moved
  the stages, so a "100%" move silently becomes ~75% or worse. The stage bytes
  are at `wPlayerStatLevels` / `wEnemyStatLevels` (accuracy and evasion are the
  last two of the seven). **This is the highest-value tactics improvement.**
- **No status/volatile awareness in scoring.** Paralysis (halved speed + ~25%
  lost turns) and confusion are invisible to `recommend()`; `faster` is read
  from raw speed. Live cost: Lance's Thunder Wave paralysed both my mons and
  the fight went 12 → 17 turns.
- **No multi-turn planning.** `recommend()` is single-turn greedy. It cannot
  reason "chip with a doomed mon, then take the free switch on the faint",
  which was the winning line against Lance's ace twice.
- **No trainer-item modelling.** The E4 heal their highest-level mon once
  (`data/trainers/attributes.asm`); the recommender doesn't know, so it can
  chip into a heal.
- **`recommend()`'s switch rule is narrow**: it only switches to a mon that
  *resists* the incoming move. It never switches for a *resisted-attacker*
  reason (e.g. bring in the mon whose move is 2× even if it doesn't resist).

### 2.7 Observability gaps

- `use_item`'s silent failure (2.2).
- Field poison ticks between rooms with no notice; I walked into Bruno at
  71/215 without being told.
- A stray `A` during `goto` near a shop clerk **bought an Ultra Ball**
  (¥1219 → ¥19). Gotcha 13 says "never blind-A near a shop list" but the
  navigation code can still emit presses there. Money changes should be
  logged, and ideally navigation should refuse to press A near a known clerk.
- `d.save(force=True)` happily persists an open START menu; after reload,
  movement is dead (gotcha 7) and `move_settled` reports `blocked` on four
  floor tiles. `save()` should warn (or refuse) when UI is open.

### 2.8 Structural

`trek.py` is **5,538 lines** in one `Driver` class. It works, and the docstrings
are unusually good (they cite the live failure each guard exists for — keep
that habit). But it is now the main obstacle to safe change: the item code,
battle code, navigation code and menu code all live together, and two
concurrent subagents editing it is a merge hazard we hit once already
(three fixers died mid-write in pt6, leaving half-built APIs). Consider
extracting cohesive seams (`items.py`, `navigation.py`) with the public
`Driver` methods delegating, so ownership can be split.

---

## 3. Recurring failure patterns (read this section twice)

These generalise beyond the individual bugs.

1. **The harness lied more often than it crashed.** 3 of the 5 significant
   bugs were confidently-wrong numbers (type ids, accuracy, `can_switch`). A
   crash gets fixed in minutes; a wrong number shapes decisions for sessions.
   *Mitigation: value-assert parsed data against the source in tests.*

2. **Failures were silent.** `return False` with no log; `'timeout'` for a
   battle that was still live; `goto` no-ops. Every one cost far more than the
   underlying bug. *Mitigation: no unexplained falsy return; distinct reasons.*

3. **"Verify the state, not the keypress."** Every menu bug reduces to
   confirming an input was *sent* rather than that the intended state was
   *reached*.

4. **The model's memory of game mechanics is unreliable; the files are not.**
   I was wrong from memory three times in one session (Electric is resisted by
   Dragon; Flying is neutral to Flying; Water is 2× not 4× on
   Aerodactyl/Charizard) and correct every time I checked the chart. The
   harness's job is to make checking cheap — it now does.

5. **Coordinator hypotheses were wrong twice, and that was fine.** Both
   delegated fixes disproved my stated root cause via live instrumentation.
   *Keep demanding "verify before coding" and keep stating the hypothesis so it
   can be falsified.*

6. **Overlevelling hides harness quality.** A L78 Feraligatr made the first
   Champion run prove nothing about decision quality. The honest tests were the
   two runs with it benched. *Any future "does the harness play well" claim
   needs a level-matched or handicapped configuration.*

7. **Fix-forward beats working around.** Manual TM driving, `fight_guarded`
   wrappers, and hand-rolled learn policies all lived in my kernel where the
   next session loses them. Anything used twice belongs in the harness with a
   test.

---

## 4. Prioritised backlog with acceptance criteria

### P0 — correctness of what the model is told

1. **Accuracy/evasion stages in `outlook()`.** Read `wPlayerStatLevels` /
   `wEnemyStatLevels`; expose `effective_accuracy` alongside listed accuracy,
   and make `_score`/`recommend()` use it. *Accept:* a fake with the enemy at
   +2 evasion shows a listed-100% move below 100 and ranks a `never_misses`
   move above it; live check against Koga's Muk after it Minimizes.
2. **Audit-by-value of every parser** (§2.1). *Accept:* each parsed table has
   at least one test asserting a specific value cross-checked against the
   disassembly, with the file:line in the test docstring.
3. **Status in the decision surface.** Surface paralysis/sleep/freeze/confusion
   and make `faster` account for paralysis' halved speed. *Accept:* a paralysed
   fake reports `faster=False` against a slower enemy, and `recommend()`
   prefers curing when a cheap cure exists and the enemy is weak.

### P1 — stop the silent failures

4. **No unexplained falsy returns.** Sweep `trek.py`/`crystalagent` for
   `return False`/`return None` on failure paths; attach a reason and log it.
   *Accept:* a test asserts each menu primitive's failure returns/records a
   distinct non-empty reason.
5. **`_expect_state` helper** and migrate menu primitives to verify reached
   state (§2.2/2.3). *Accept:* `select_label` cannot report success unless the
   target screen/WRAM state is observed; a fake that swallows the A returns
   failure with a reason.
6. **`save()` refuses/warns on open UI.** *Accept:* saving with a drawn START
   menu either errors or logs loudly; a test covers it.

### P2 — navigation ergonomics

7. **`goto` auto-escalates to `reach`** instead of storming 20 replans, and
   distinguishes "static grid wrong" from "NPC in the way". *Accept:* the
   Plateau nurse route `(3,9) -> (3,8)` succeeds without a replan-storm line;
   the corridor `(16,5) -> (16,1)` resolves without manual `step_hold`.
8. **Navigation must not press A near shop clerks / known script NPCs.**
   *Accept:* a regression test proving no A is emitted while adjacent to a
   clerk object; money-delta logging on any purchase.

### P3 — capability gaps worth closing

9. **A real `teach_tm(tm, mon, forget)`** in the harness, cursor-row driven,
   handling pocket re-indexing and the two-glyph cursor. *Accept:* teaches a TM
   over a chosen move on a fake and refuses cleanly when the mon can't learn it.
10. **`learn_policy` default should not trade damage for status.** The default
    accepted every level-up move and cost a Gyarados its Hydro Pump for Rain
    Dance. Ship the rule (never replace a damaging move with a status move when
    ≤2 damaging moves remain) as the default. *Accept:* test with a fake
    level-up offering a 0-power move.
11. **Multi-turn/sacrifice planning in `recommend()`** (§2.6) — at minimum,
    recognise "this mon is dead next turn regardless; maximise damage now, the
    replacement enters free".
12. **Trainer-item awareness** for the E4 aces.

### P4 — structure

13. Extract `items.py` / `navigation.py` seams out of `trek.py` so concurrent
    work stops colliding. *Accept:* `Driver` public API unchanged, full suite
    green, and the two new modules have no imports back into `trek`.

---

## 5. Explicit anti-goals

- **Do not add hardcoded game data** to "fix" a parser. Fix the parser.
- **Do not make the harness decide battles again.** Movement must never
  auto-fight; `encounter_policy`/`require_decision`/`DecisionRequired` exist
  because silent auto-play fed all the exp to one mon and whited out a party
  while a log line claimed `fights=0`.
- **Do not "fix" the full-HP heal no-op.** A full-HP unstatused mon genuinely
  cannot be healed; it must stay a distinct `no-effect` result, not be
  conflated with a mechanical failure.
- **Do not delegate battles to subagents** (kernel locality, §1.3).
- **Do not silence diagnostics by deleting them** — cap them (3 per battle is
  the established pattern) and keep the state dump.
- **Do not touch `saves/`, `omp_saves/`, `journal/`, or the ROM/sym files.**
  Every fork lives in `claude_saves/` and must copy the `.meta` sidecar.

---

## 6. Fast orientation for the next agent

1. Read `AGENTS.md` (protocol + 14 gotchas, all learned empirically), then
   `BATTLE.md` (combat doctrine, disassembly-cited), then the newest two
   sections of `PROGRESS.md`.
2. Boot cost is ~1 s; hold ONE `Driver` in a warm kernel and compose against
   it. Cell timings under ~50 ms prove you are warm.
3. Reproduce before believing: fork a state, instrument the WRAM, and confirm
   the mechanism in the disassembly. Both delegated fixes overturned a
   plausible-sounding hypothesis this way.
4. Run `.venv/bin/python -m pytest tests -q` (479 tests, ~16 s) before and
   after. Keep it green; it is fast enough that there is no excuse.
5. Useful live checks that catch the historic bugs in one line each:
   `d.bdata.types['DARK'] == 27`, Iron Tail accuracy `== 75`,
   `d.bdata.effectiveness(27, [24]) == 2.0`.
