# Code Review Findings & Improvement Plan — 2026-08-24

Full-repo code review produced by 6 parallel subagent reviews (trek.py halves,
battle/nav/menus, scripts/tests/hygiene, serve/autopilot/watch/core). This file
is the handoff: next session should start at **Work Plan** below and check items
off as they land. Nothing here has been fixed yet.

Scope reviewed: `trek.py`, `watch.py`, `autopilot.py`, `serve.py`,
`crystalagent/*`, `scripts/*`, `tests/*`, top-level small scripts, hygiene.
~11k lines of first-party Python.

P11 folds in three prior field-review docs (`backup/FABLE_FEEDBACK.md`,
`fable_results.md`, `backup/DEEPSEEK_PROGRESS.md`) plus
`backup/AUTONOMOUS_IMPROVEMENTS.md` (roadmap status): their findings were
cross-checked against P0-P10 — live-confirmed duplicates noted, new items
added, already-fixed items recorded so nobody re-files them.

---

## Work Plan (in priority order)

### P0 — Battle AI math is wrong today (two one-line fixes)

- [ ] **battle.py:26-44 — special-type IDs off by 9 vs ROM.**
  `_parse_types` assigns sequential IDs and ignores `const_next 19` in
  `constants/type_constants.asm`, computing FIRE=11…DARK=18 while real
  ROM/WRAM IDs are 20-27. Matchups keyed by parsed IDs but queried with real
  ROM bytes (`mv["type"]`, enemy WRAM types) → every special-type matchup
  lookup misses → defaults to 1.0. Consequence: Water-vs-Fire scores neutral,
  Electric immunity to Ground is missed entirely (AI Thunderbolts Geodude).
  Fix: honor `const_next` when assigning IDs.
  Tests pass only because they round-trip parser values on both sides — add a
  test that feeds REAL ROM bytes (e.g. ICE_PUNCH type byte = 25).
- [ ] **battle.py:64 — accuracy misread as percentage.**
  ROM stores accuracy via `percent` macro = `n * $ff / 100`; current
  `min(rec[4], 100)` clamps everything ≥ ~39% to exactly 100 (DOUBLESLAP 85%
  reads as surefire). Accuracy-weighted move ranking is meaningless.
  Fix: `round(rec[4] * 100 / 255)`.

### P1 — Money leak & shop wedges (mart_buy)

- [ ] **trek.py:2524/2538 — NameError on `bought`** when item not stocked;
  exception escapes BEFORE shop-exit B-loop (menu left open) and before save.
  Init `bought = False`.
- [ ] **trek.py:2497-2498 — substring targeting**: `"POTION"` matches
  SUPER/HYPER POTION rows; wrong item bought, money spent, failure reported
  after the fact. Match full normalized row against exact item.
- [ ] **trek.py:2506-2521 — quantity picker treats parse failure as done**
  (`picker_qty() in (qty, None)` breaks on None and confirms whatever the
  cursor holds). Require actual parsed equality; retry read a few times.
- [ ] Apply the same cleanup-on-every-exit contract used elsewhere: all early
  returns must run the B-only shop exit.

### P2 — Dashboard concurrency & SSE correctness

- [ ] **watch.py:783-787 (also 732-734, 776-778) — select/snapshot race**:
  lock taken separately for `select(save)` and `snapshot()`; two clients on
  different saves interleave → client A gets save B's state labeled as A.
  Fix: single atomic locked select+read (e.g. `viewer.snapshot_for(path)`).
- [ ] **watch.py:392 + 727-747 — SSE duplicate history**: page polls all
  events then opens `/stream` whose server cursor starts at -1 → whole history
  delivered twice. Fix: pass client cursor as `?since=` on `/stream`.
- [ ] **watch.py:583-601 — png() holds global lock ~1800 frames/request**,
  blocking all SSE streams and JSON polls for seconds. Cap work per call or
  use a dedicated emulator mutex guarding only load/read.

### P3 — Autopilot feedback loop lies to the deciding agent

- [ ] **autopilot.py:78-88, 362-367 — stuck-digest missing bag/money/PP**:
  successful `mart_buy` = zero-delta digest → journaled/replied as
  `ok:false "stuck"` with default stuck_limit=1. Add bag/money to digest or a
  no-op-action exemption list.
- [ ] **autopilot.py:380 — record validated AFTER action executed**: schema
  drift raises inside shell → "decision failed" reported though action
  succeeded, skipping milestone checkpoints/whiteout recovery (382-391).
  Validate in try/except that journals failure but still returns reply.
- [ ] **autopilot.py:216-221, 385-388 — whiteout recovery reloads wiped state**:
  milestone classification saves a fresh checkpoint of the blacked-out party
  before the wipe check, and recovery picks latest_checkpoint(). Record last
  checkpoint taken at healthy party, or skip checkpointing when party wiped.

### P4 — trek.py driver robustness

- [ ] **trek.py:1322-1328 — unbudgeted wedge in train()**: modal menus B can't
  close (documented Togepi naming-keyboard case) spin forever. Count failed
  close_menus() attempts; abort with diagnostics like the 400-dry-step guard.
- [ ] **trek.py:1792-1809 — walk() spins forever on re-firing textboxes**
  (no iteration cap; textbox branch increments nothing). Bound like goto.
- [ ] **trek.py:1767-1769 — use_item timeout leaves menus wedged open** →
  stray START menu baked into next save (gotcha 7). Clean up on this path too.
- [ ] **trek.py:2397-2400 — talk_to KeyErrors when diagonal from NPC**
  (warp drift / NPC bump). Clamp to dominant axis or re-goto.
- [ ] **trek.py:2865-2867 — CLI legs exit 0 on failure**, save runs
  unconditionally. Propagate nonzero exit; try/finally around the leg.
- [ ] **trek.py:2442-2444 — milestone save(name=…) writes working state
  bypassing rollback guard.** Guard both writes.
- [ ] **trek.py:932-943 — kb_cursor dereferences naming-screen pointer with no
  validity check** → navigation against garbage cursor mistypes names.
- [ ] **trek.py:814-821 — _mount_surf fires up to 10 blind A presses**;
  verify enterable water first, bail after 2-3 presses (gotcha-13 class).

### P5 — Systemic: silent fallbacks hide failures

Replace every "swallow and continue" with a logged warning (once) or distinct
cached value:

- [ ] trek.py:743 — observe() `except Exception: pass` on tiles → agent gets
  empty terrain grid silently.
- [ ] trek.py:363-368 — unknown script label caches as not-disruptive,
  indistinguishable from verified-pure-dialog → push-back storms return.
- [ ] trek.py:313-321 — `_file_const` silently falls back to filename stem.
- [ ] battle.py:324-328 — attack rejection sniffed via literal screen text
  ("NO PP"/"DISABLED"); enemy Disable announcements count as our rejection and
  can silently abort mid-catch policies. Prefer WRAM-side check (turn counter /
  PP delta).
- [ ] trek.py:656-669 — _bag caps at arbitrary 26 instead of engine pocket
  capacity 20 → corrupt bytes fabricate phantom inventory driving shop/catch.

### P6 — Game data: derive from repo, don't freeze snapshots

- [ ] trek.py:1233 — `_HEAL_CENTERS` hardcodes six early-Johto centers; past
  Ecruteak training raises "no routable Pokécenter". Derive from
  `names.maps` keys containing POKECENTER.
- [ ] nav.py:18 — diagonal ledges $a4-$a7 missing from HOPS → BFS treats them
  as walls, fails routes hopping down-right/down-left ledges.
- [ ] nav.py:167/371 — ice inconsistently modeled: BFS can't traverse ice but
  region map counts ICE passable → planned legs find_path can't execute.
- [ ] Dedupe tall-grass bytes {0x14,0x18} (trek.py:493 vs 720s); move
  cut-tree/textbox/move-length literals to named constants sourced from
  data/tilesets collision tables.
- [ ] trek.py:473-489 — event-flag parser ignores const_def offsets and
  const_skip args; raise on unexpected forms before it silently shifts bits.
- [ ] trek.py:230-255 — `_reach(surf=…)` ignores surf param; map view
  disagrees with router about water/side-walls.

### P7 — Extract shared logic (mechanical refactors)

- [ ] Savestate load-and-verify exists 3×: emu.py:102-114 (verified),
  serve.py:81-90 (skips provenance checks!), autopilot.py:225-238. Extract
  `Crystal.load(path)`; fixes hot-loading incompatible savestates mid-session.
- [ ] `drain()` copy-pasted in 8 vega_* scripts with diverging semantics
  (most answer naming keyboards with A; vega_to_r32 declines with B). One
  shared helper with an explicit flag.
- [ ] Fight-logging Driver.fight monkeypatch duplicated ×3 (e4_chain, e4_lose,
  karen_debug) + e4.py carries its own drifted e4_policy → extend
  e4_helpers.make_policy.
- [ ] vega_intro.py / omp_fresh_intro.py near-verbatim clones, both
  monkeypatch Crystal.__init__ at import time. Merge; make raw-boot explicit.
- [ ] ~17 scripts hardcode shared working states (omp_speed_run.state ×8,
  vega.state ×9) with no CLI override — defeats fork-first discipline. Accept
  `<state>` argv/env like trek does.

### P8 — Hygiene commit (one focused commit)

- [ ] .gitignore: add `backup/`, `claude_saves/`, `omp_saves/`, `.pytest_cache/`
  (~3.5 MB savestates polluting status); consider root `*.png`.
- [ ] Commit untracked `tests/unit/test_save_paths.py`; reconcile deleted docs
  (AUTONOMOUS_IMPROVEMENTS.md etc.) and modified files (PROGRESS.md, trek.py,
  vega_intro.py) — uncommitted drift is dangerous in a multi-agent repo.
- [ ] pyproject.toml: pydantic is under dev group but imported at runtime by
  serve.py/schemas.py → move to `[project].dependencies`.
- [ ] Remove tracked artifacts: gym_entry.png.
- [ ] data/mapgraph.json: add generated-at/source-hash meta so staleness is
  detectable.

### P9 — Test gaps

- [ ] trek.py (2,900 lines) has ZERO pytest coverage; only env-coupled
  scripts/trek_selftest.py (depends on one specific savestate, outside CI).
  Port pure logic (path stitching, landing verification, drift replanning)
  into tests/unit with fake nav/emulator objects.
- [ ] tests/unit/test_parsers.py:66&71 — duplicate test def, second shadows
  first. Delete one.
- [ ] trek_selftest.py:203 — precedence bug makes returncode check ineffective
  whenever "ok" appears anywhere; parenthesize.
- [ ] Add regression tests feeding REAL ROM bytes for type chart + accuracy
  (would have caught both P0 bugs).

### P10 — Low priority backlog

- Dead code: duplicate `_coord_event_cache` decl (trek.py:303/372);
  `_party_knows_cut` dupes `_party_knows("CUT")` (1397); duplicate OPPOSITE
  dict (build_mapgraph.py:147/177); unused WindowEvent (clean.py:8); dead
  parent-map build (explore.py:45); stale observe fallback comment
  (serve.py:53); unused WALKABLE import (watch.py:41); unused fork(tag) param +
  full-journal rescan per decision (autopilot.py:195); duplicate import
  (autopilot.py:66).
- Always-False comparison: vega_gym.py:36 `d.pos()[2] == (18,17)` int vs tuple.
- Zero-frame wait tokens (.:0) make max_frames loops unreachable (emu.py:66,
  cli.py:84/111); negative frame counts accepted (emu.py:57).
- watch.py: unsynchronized streams counter (793-800); state/meta two-file write
  race vs mtime watchers (613 vs emu.py:214); unbounded per-save caches;
  status_line computed twice in snapshot().
- rolling.py docstring claims nothing lost but _merge_level truncates merged
  blocks to 3000 chars.
- names.py:46-53 unbounded $50 scan over item table; hookevents.py:77 /
  names.py:21 unclosed file handles.
- trek.py logging cosmetics: success logged at WARNING; stray trailing-comma
  log.info(f"…",) artifacts (1584, 1814, 1903, 2257, 2571).
- conftest.py puts pokecrystal parent dir on sys.path (import-shadow risk).
- menus.py: two border-glyph allowlists invite drift (132 vs 150); DOWN-only
  select_label times out on non-wrapping menus.
- registry.check + autopilot do 3 full observes per decision where 1 would do.

---

## P11 — Folded field reviews (`backup/FABLE_FEEDBACK.md`, `fable_results.md`, `backup/DEEPSEEK_PROGRESS.md`, `backup/AUTONOMOUS_IMPROVEMENTS.md)

Three prior agent sessions wrote post-mortem/review docs. Cross-checked each
claim against current code and against this plan's P0-P10. Result: several
findings are **live-confirmed duplicates** of existing plan items (strong
evidence they're real), a handful are **new**, and several were **already
fixed** by the post-run subagent batches recorded in `fable_results.md` and
the git log.

### 11a. Live-confirmed duplicates (raise confidence, no new work beyond existing items)

| Existing item | Field confirmation |
|---|---|
| **P1 mart_buy `bought` NameError** | DEEPSEEK session hit it live ("harness mart_buy CRASHES on the shop-open path (UnboundLocalError 'bought') - buy manually"). Real money/time cost, not theoretical. |
| **P5 battle.py:324 attack-rejection sniffing** | FABLE_FEEDBACK open gap #1 says the same thing with a better fix sketch: policies should track PP via `me['moves']` until play() verifies a turn actually elapsed (watch `wBattleTurnCounter` or enemy HP/PP deltas) instead of screen text alone. Use their approach for the P5 fix. |
| **P6 ice modeling** | FABLE_FEEDBACK gap #4 confirms ice is only crossable via savestate BFS today ("a slide is a deterministic function of (grid, entry cell, direction) — cheap to precompute"); git log shows a precompute landed but the nav.py reviewer found BFS still can't route through ice floors. Close the loop end-to-end. |

### 11b. NEW findings (not covered by P0-P10)

- [ ] **fight()'s auto-save writes working state during battles**
  (FABLE_FEEDBACK gap #6). `emu.save` mid-leg (for watch.py) combined with
  crash-before-save patterns bakes mid-battle states into forks. Fix: save to
  a scratch sidecar during legs, promote on leg completion — or suppress
  auto-save while `wBattleMode != 0`.
- [ ] **use_battle_item can consume/toss the WRONG item when select_abs
  desyncs** (FABLE_FEEDBACK gap #2) — once burned ~9 potions in a wedge.
  Verify the highlighted row text is the intended item before confirming,
  and re-check bag counts after (same verify-after-act pattern as P1 shop
  fixes).
- [ ] **Unverified success claims: talk_to returns 'talked' when it faced an
  empty tile** (FABLE_FEEDBACK failure pattern #2 — wandering NPCs like
  Floria). General rule the repo keeps relearning: verify by reading WRAM
  (event flags set, badge bits, bag delta, HP), never by "the flow
  completed". Audit talk_to/catch/use_item return contracts for false
  positives; note whiteout HEALS the party so `hp > 0` proves nothing about
  winning.
- [ ] **Assert-before-save rolls back timelines** (FABLE_FEEDBACK failure
  pattern #1): scripts that assert between an achievement and `d.save()` lose
  the achievement on crash (lighthouse climb lost twice, pharmacy trip).
  Convention + lint-worthy rule: save immediately after every VERIFIED
  transition; asserts go AFTER the save. Related: P4's CLI finding (save runs
  unconditionally) is the mirror image — both need the same discipline:
  persist first, validate second, report third.
- [ ] **Driver.save lacks a state-poison guard** (FABLE_FEEDBACK failure
  pattern #3): saving while `wBattleMode != 0` or mid-fade repaint poisons
  the state file (two poisoned saves required milestone rollbacks;
  close_menus' fade-awareness landed, but the save side has no guard). Add a
  precondition check in save(): refuse/warn when battle mode or a fade is
  active.
- [ ] **use_item unsafe after warps** (FABLE_FEEDBACK gap #5): blind presses
  WALKED the player onto a ladder mid-flow ("could not open PACK" then stray
  inputs move the player). Needs the same wait-for-menu verification the
  battle pack flow got — verify PACK actually opened before any further
  presses, and abort without movement inputs otherwise.
- [ ] **Nav grid staleness vs live ROM blocks** (DEEPSEEK, repeated):
  repo `.blk` data can disagree with the game (Route 34 (9,29) wall-vs-floor,
  Ilex BFS emitting tree cells as walkable). Precedent exists for overwriting
  the per-map grid from `wMapBlocksPointer` + tileset collision. Add a
  fallback path (and/or a validation warning) when static grid and live
  blocks diverge; at minimum document which maps are known-stale.
- [ ] **mapgraph surf/water edges + impossible border connections**
  (FABLE_FEEDBACK gap #7, partially addressed by the region-aware MapgraphFixer):
  verify the rebuilt mapgraph doesn't price decorative border cells
  (Goldenrod→R35 "north edge", Route 37→Ecruteak only crosses at x=8) as
  crossings, and decide whether SURF edges belong in the graph now that
  enable_surf exists. Route-level knowledge still accrues only in
  PROGRESS.md.
- [ ] **Cianwood stuck-NPC workaround undocumented in code** (FABLE_FEEDBACK):
  zeroing a `wObjectStructs` slot to unstick Lung is precedented-but-hacky;
  either encode it as a documented `unstick_npc()` helper or leave a comment
  where NPC-on-choke geometry bites, so the next session doesn't rediscover it.
- [ ] **Persona gates as machine-checkable assertions** (fable_results
  reflection): purchases verified by bag deltas, names read back after
  keyboards, full-HP-before-landmark checks. The bag-delta and name-readback
  halves overlap P1/P4 fixes; consider exposing them as reusable
  postcondition helpers rather than per-script ad hoc checks.

### 11c. Already fixed (verified — do NOT re-file)

- Sprout Tower mapgraph wrong → MapgraphFixer region-aware graph
  (per-map connected components, per-edge from/to regions); travel reaches
  SPROUT_TOWER_3F end-to-end (fable_results post-run batch, suite green).
- travel/goto replan-storms on scene textboxes → `_drain_scene` auto-drain;
  its blank-textbox choice-menu misdetection was itself fixed later
  (cursor-glyph requirement — see PROGRESS.md LegTwoFixer entry).
- First-call races in use_item/heal_pokecenter/mart_buy → one
  settle-drain-retry inside each (TrekFixer).
- Tower double-door ping-pong → `_held_warp_entry`.
- Dormant warp_events sealing corridors (Burned Tower B1F) → landed
  (git: "dormant warp_events walkable").
- Live collision divergence (changeblock doors/boulders) → `set_cell`/
  `clear_overrides` patches landed; residual staleness handled in 11b above.
- Nav side-wall directions inverted → caught and reverted by DEEPSEEK session;
  matches home/map.asm today.
- AUTONOMOUS_IMPROVEMENTS.md phases 1-4 (observe/serve, mapgraph+route,
  autopilot decide-loop, rails) — all built; Phase 5 (skill library +
  fork-verified promotion) remains unbuilt and is a design direction, not a
  defect. Park it unless autonomous coverage growth becomes the goal.

---

## Overall assessment

Thoughtfully engineered under fire — atomic savestate writes, provenance-stamped
metas, signature-validated ROM hooks, frame-bounded retries, drift-aware
replanning, and a genuinely high-quality (small) test suite with meta-tests.
Systemic weaknesses: (1) silent fallbacks convert bugs into confusing
agent-level failures; (2) frozen snapshots of game data turn "works in early
Johto" into hard fails later; (3) shopping never got the verify-after-act rigor
the rest has; (4) concurrency around the shared emulator in watch.py. P0-P4 are
all surgical fixes worth doing before more gameplay sessions rely on them.
