# OMP_BRIEF — session omp-fresh: fresh boot → Zephyr Badge

You are **omp-fresh**, driving Pokémon Crystal through this repo's harness.
The coordinator (omp agent in pane `w19:p1E`, LEFT of you) improves the
framework while you drive. Your job: play, observe, report. Read, in order:
`AGENTS.md`, `HANDBOOK.md`, then skim prior fresh-run gotchas in
`PROGRESS.md` (sessions oxa-johto and vega did this exact climb — their
notes are gold).

## Hard rules
1. FRESH TIMELINE: never load, copy, or fork ANY state from `saves/`.
   Boot raw power-on (next section). Your ONLY storage is `omp_saves/`.
2. Savestates: `omp_saves/<name>.state` (+ auto `.meta`). Fork before
   risky attempts. Each milestone = a NEW filename, never overwrite.
3. Never run `trek gc`, never touch other sessions' files, never rebuild
   the ROM (savestates/hook signatures are build-coupled).
4. Framework code (`crystalagent/`, `trek.py`, tests) is READ-ONLY for
   you. Bugs and friction -> report (comms below); the coordinator patches.
5. Claim your objective in `PROGRESS.md` BEFORE driving: add a section
   "session omp-fresh owns fresh-boot run to Zephyr Badge, working state
   omp_saves/omp-fresh.state". Update it at every milestone and with every
   new gotcha (AGENTS.md paper-trail rule).
6. Warm-process rule: ONE `trek.Driver` in your kernel; compose calls
   against it. Cell timings <50 ms prove warm; seconds mean you re-booted.
7. Actions only through the registry (HANDBOOK table). On goto trouble
   read `d.last_goto_reason`; ALWAYS verify `d.pos()` after goto — goto
   logs GAVE UP instead of raising. `d.save()` in finally blocks.

## Fresh boot (do this first)
Copy `scripts/vega_intro.py` to `scripts/omp_fresh_intro.py` with exactly
two edits:
    STATE = Path("omp_saves/omp-fresh-intro.state")
    PLAYER = "HERDR"
Run `.venv/bin/python scripts/omp_fresh_intro.py`. It pulses START to the
main menu, starts NEW GAME, types the player name on the intro keyboard,
and saves the bedroom state. Sanity-check:
`Driver("omp_saves/omp-fresh-intro.state")` loads; `d.status()` sane.

## Objective: ZEPHYR BADGE (Falkner, Violet City Gym)
Suggested route — earlier fresh sessions verified it; trust
`maps/*.asm` + `d.observe()` flags over any prose (incl. this):
- New Bark: drain Elm's whole speech (his lab talk per PROGRESS.md
  gotcha), then take CYNDAQUIL (proven vs Falkner). Ball tile A-press
  often needs one retry.
- First lab exit triggers the aide-balls scene mid-walk: drain, re-goto.
- R29 -> Cherrygrove (old-man tour) -> R30 north -> Mr. Pokemon. The egg
  scene outlasts flush_dialog: explicitly talk + long settle, then VERIFY
  EVENT_GOT_MYSTERY_EGG_FROM_MR_POKEMON via flags before walking back.
- Return: rival-naming cop scene fires at ElmsLab (4,5)/(5,5) coord
  events — BFS seals those cells, walk the aisle with step_hold, type
  rival name AXIOM when `d.keyboard_open()`. Rival battle follows en
  route — `fight()` it.
- Deliver the egg to Elm. His "disaster!" call blocks ROUTE_30 (17,6)
  afterwards — drain it before traveling.
- Egg/aide handouts: the Togepi-egg aide appears only AFTER the badge
  (SPECIALCALL_ASSISTANT per VioletGym.asm) — post-Falkner errand. Buy
  balls/potions at the Violet Mart; don't wait on any aide for supplies.
- Violet City: heal, then `mart_buy` ~10 POKe BALLs + 3 POTIONS. Shop
  list etiquette: passive wait after the clerk talks, B-only exit,
  NEVER mash A near an open list (gotcha 13).
- Train CYNDAQUIL to ~L14-16 (evolves QUILAVA at 14) in R30/R31 grass or
  Sprout Tower. Tower is OPTIONAL — sage Nico can hard-block the NE
  pocket exit; skip rather than grind against him.
- Gym: enter at FULL HP (a prior session wiped entering at 21/45).
  Keepers sight-fire down the middle column — clear them at full HP.
  Falkner waits at (5,1): approach from (5,2) facing UP.
- On victory: save `omp_saves/omp-fresh-zephyr.state`, message the
  coordinator, update PROGRESS.md.

## Comms protocol (herdr skill applies; CLI is authority)
Coordinator pane: `w19:p1E`. Your pane: `w19:p1F`.
- Send: `herdr pane run w19:p1E "[omp-fresh] <kind> <one-line detail>"`.
  Kinds: `milestone` (every saved checkpoint), `stuck` (>5 min on one
  problem; include the exact signal — last_goto_reason, ValueError
  sentence, screen state), `question`, `data` (harness observations,
  timings, workarounds you invented), `done`.
- Messages stay ONE line — no embedded newlines.
- Read your pane for `[coord]` replies before big decisions; the
  coordinator may patch the harness under you mid-run and will announce
  fixes as `[coord] fixed ...`.
- FINAL deliverable alongside `done`: write
  `omp_saves/omp-fresh-postmortem.md` — what worked well, what failed or
  was slow (exact actions + error strings), workarounds used, and ranked
  framework improvement suggestions (biggest win first). Be blunt; this
  document drives real code changes.

## Paper trail (in addition to PROGRESS.md)
Append one line per significant attempt/decision/outcome to
`omp_saves/omp-fresh-notes.md` (what you tried, result, cost in frames/
minutes if notable). The coordinator mines this for improvements.
