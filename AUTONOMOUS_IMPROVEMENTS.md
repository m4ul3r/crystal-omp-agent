# AUTONOMOUS_IMPROVEMENTS.md — plan for fully autonomous play

Goal: an agent loop that plays Crystal end-to-end — decides objectives,
composes existing primitives, verifies results, checkpoints, and recovers
from failure — without a human issuing per-leg commands.

Design rule that drives everything below:

> **The LLM never touches frames.** One decision = one primitive call =
> thousands of emulator frames executed by deterministic code. The agent
> picks *what* to do; `Driver` handles *how*.

Autonomy layers (who owns which decisions):

| Layer | Owner | Examples |
|---|---|---|
| Frames | emulator | button presses, ticks |
| Mechanics | `trek.Driver` | `goto`, `fight`, `catch`, `mart_buy`, `talk_to`, `heal` |
| Tactics | code policies | best-move from ROM data, auto-POTION <30%, flee hopeless wilds |
| Strategy | **the deciding agent** | objectives, grind targets, spending, team comp, route order, fork/retry calls |

Everything in the Mechanics/Tactics rows is already live-tested
(see PROGRESS.md "Harness state"). The gaps are all in connecting Strategy
to those primitives safely and cheaply.

---

## Phase 1 — Eyes and hands: `observe()` + persistent server

**Problem.** Every `trek.py <leg>` call is a cold process (ROM load per
invocation), and the deciding agent sees only `status_line()` — one line.
A strategy layer needs one cheap, rich snapshot and a warm process to act
in.

**Build.**

1. `Driver.observe()` → structured JSON dict:
   - map name + `(x,y)`, facing direction
   - party: species, level, HP/max, moves + PP, status, exp-to-next
   - bag: items + quantities; money; badges
   - story flags read from `wEventFlags` (currently progress is inferred
     from position — fragile)
   - live NPC/trainer cells (`npc_cells()` exists), textbox open?, battle?
   - last event deltas (reuse watch.py's snapshot-diff logic)
2. `trek serve`: long-lived process (launch under `hub start`) reading
   line commands on stdin (`observe`, `run <primitive> <args...>`,
   `save <file>`, `load <file>`) and replying with JSON. ROM loads once;
   a decision cycle becomes two stdin writes instead of two Python
   cold-starts.
3. CLI shim for humans/debugging: `trek-cmd observe`, `trek-cmd run ...`
   talks to the same server.

**Acceptance.**
- `observe` returns full party/bag/flags JSON in <100ms on a warm server.
- An external script can drive 50 consecutive primitive calls through the
  server with no ROM reload.
- watch.py keeps working unchanged (read-only side).

---

## Phase 2 — Knowing the world: cross-map pathing

**Problem.** `nav.MapData` ignores map-edge CONNECTIONS
(`maps/attributes.asm`) — known gap from the visibility run. BFS cannot
leave a town; every exit is hand-scripted (`leg_to_violet` hardcodes the
Cherrygrove north exit, the finicky Route 31 gate, etc.). This is the
single most fragile part of long journeys and the reason legs like
`to_azalea` don't exist yet.

**Build.**

1. Offline map-graph builder: parse every `maps/*.asm` for
   `warp_event`, `map_event` (connections), and object events → emit
   `mapgraph.json`: nodes = maps, edges = warps/connections with entry
   cells and required heading (the Route 31 gate fires sideways from
   (4,6)/(4,7) — encode exactly such quirks as edge attributes).
2. `Driver.route_to(map_name)` → list of legs across maps using BFS over
   the graph, each leg executed with existing `goto`/`walk`/`step_hold`
   mechanics (warp-hold behavior already automatic).
3. Validate against known-good history: replaying Violet arrival and
   Pokecenter approaches must match the coordinates recorded in
   PROGRESS.md route notes.

**Acceptance.**
- `route_to("VIOLET_CITY")` from Route 31 grass succeeds via the gate,
  no hardcoded leg.
- Every warp edge in `mapgraph.json` spot-checked against its
  `maps/*.asm` source (sample of ~20).
- New journey legs (`to_route32`, `to_azalea`) become data + composition,
  not new Python.

---

## Phase 3 — The decide-loop

**Problem.** Nothing executes strategy. Legs are authored by a human at
writing time; an autonomous session has nothing between `observe()` and
button frames.

**Build.** `autopilot.py` — the outer loop:

```
loop:
  obs    = server.observe()                     # ~200 tokens after compaction
  action = llm.decide(obs, journal, skills)     # ONE structured decision:
                                                # {goal, tool, args,
                                                #  success_condition, risky}
  result = server.run(action)                   # bounded frames budget
  journal.append(obs, action, result)           # append-only JSONL
  if milestone: checkpoint + PROGRESS.md entry
```

Supporting pieces:

1. **Decision schema** — strict JSON: goal id, one primitive call or one
   sub-goal, success postcondition (e.g. `map == UNION_CAVE_B1F`),
   `risky` flag, fallback hint. Reject malformed decisions and re-ask
   with the schema error (cheap self-correction).
2. **Journal** — `journal/<session>.jsonl`: observation digest + chosen
   action + verified outcome per cycle. This is the context re-injection
   surface: on replan, the decider reads the last N entries, not full
   history.
3. **Milestones are automatic** — watch.py already diffs snapshots into
   events (map entry, battle end, level-up, money delta). On a milestone
   event: `save saves/<name>.state` (new filename, never overwrite —
   AGENTS.md rule) and append to PROGRESS.md.
4. **Context economy** — decider prompt = compact observe() + last ~20
   journal lines + skills doc + current goal stack. Everything else stays
   on disk. Context windows kill agents before Falkner does.

**Acceptance.**
- Unattended overnight run: reaches Route 32 Pokecenter from
  `two-mon.state` — walking, wilds, healing when low, checkpointing —
  with zero human input.
- Journal shows a complete decision trail for every battle/heal/save.
- A killed mid-run session resumes from its own journal + newest
  checkpoint without human triage.

---

## Phase 4 — Rails: failing safely

**Problem.** Autonomy fails differently than supervised play: hung dialogs
burn frames silently, whiteouts cascade, bad plans repeat forever. All
rails are deterministic code — the LLM is only escalated *to*, never
trusted *with* safety.

**Build.**

1. **Stuck detector**: no positional / flag / battle-state delta within N
   frames of expected progress → halt action, escalate one replan call
   with extra context (screen text, NPC cells). Known traps it must catch:
   stray START menu eating movement (gotcha 7), async warps (gotcha 12).
2. **Whiteout recovery** (codified once): on blackout → load newest
   checkpoint → heal → grind lead to expected level − 1 → retry objective.
   The Zephyr loss at L13/17HP is the template case.
3. **Fork-before-risky**: any decision tagged `risky` copies working
   state (+ `.meta` sidecar) first. Savestate determinism (gotcha 9)
   makes retries free; the loop should exploit that mechanically.
4. **Frame budget**: every action carries a max-frames cap; exceeding it
   counts as failure and triggers the stuck path. No dialog can burn an
   hour.
5. **Escalation ladder**: heuristic recovery (B press, settle) → replan
   with more context → load checkpoint → stop and write a PROGRESS.md
   blocker note. Each step strictly less frequent than the last.

**Acceptance.**
- Injected faults pass: hang the game in a dialog → stuck detector fires
  ≤2× budget; force a whiteout → recovery completes without LLM
  intervention; tag a doomed action risky → fork exists before first
  frame of the attempt.

---

## Phase 5 — Growing the skill library

**Problem.** Today new situations require a human authoring a new leg.
The system should convert novel solved situations into reusable,
verified skills — the Voyager pattern applied here.

**Build.**

1. **Skill format**: `skills/<name>.py` — a function over `Driver` with
   declared preconditions (map/party/bag requirements) and a
   postcondition check, plus a header comment linking the journal entries
   where it was first proven.
2. **Promotion path**: decider hits a situation with no matching skill →
   composes primitives ad hoc → on verified success, writes the sequence
   into a candidate skill → runs it once on a disposable fork to verify →
   registers it in `skills/index.json`.
3. **Skill selection** becomes part of the decision schema: prefer an
   indexed skill whose preconditions match before composing ad hoc.
4. First candidates expected from the current route: Union Cave B1F
   navigation, Rock Smash-free Ilex Forest passage, headbutt-tree grinding.

**Acceptance.**
- A skill written by the loop passes its fork-verification unattended.
- Re-running the same situation uses the skill instead of recomposing.

---

## Deliberately out of scope

- **LLM battle tactics.** Type-chart math parsed from
  `data/types/type_matchups.asm` and ROM move data beats model guessing;
  `fight()` keeps this. Escalate only on policy-detector trouble (bad
  matchup, global PP starvation).
- **Frame-level play.** Never. Cost per decision makes it strictly worse
  than primitive composition.
- **Multi-agent parallel forks** (explore two routes simultaneously,
  merge findings via savestate determinism). Powerful but deferred until
  single-loop autonomy is boring.

## Build order and dependencies

Phase 1 → everything (no eyes/hands without it).
Phase 2 before Phase 3's overnight target (Route 32+ requires crossing
map edges reliably). Phases 4 and 5 can interleave with 3; rails should
land *before* the first truly unattended overnight attempt.

| Phase | Deliverable | Unblocks |
|---|---|---|
| 1 | `observe()` + `trek serve` | all decision work |
| 2 | map graph + `route_to` | long journeys, kills scripted legs |
| 3 | `autopilot.py` decide-loop + journal | supervised→unattended transition |
| 4 | stuck/whiteout/fork/budget rails | trustworthy unattended runs |
| 5 | skill library + promotion | open-ended coverage growth |

Immediate next step: Phase 1. It is independently useful in supervised
sessions (one-call rich snapshots today's Route 32 push would use), and
every later phase consumes it.
