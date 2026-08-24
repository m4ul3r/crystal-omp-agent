# DESIGN — decision boundary doctrine

How this driver splits work between **deterministic code** (mechanics) and
**the deciding LLM / human operator** (judgment). Distilled from
ai-plays-pokemon's docs/philosophy.md, adapted to a sym-derived Crystal
driver driven conversationally.

## The spectrum

```
pure mechanics ←————————————————————→ pure judgment
  BFS pathfinding                        when to train, where to go next
  menu navigation                        which mon to catch/keep
  battle best-move scoring               whether to flee or push
  savestate forks/checkpoints            goal setting, risk appetite
  input DSL, settle loops                interpreting an ambiguous screen
```

Code owns everything left of the boundary because it is *cheaper, faster,
reproducible and verifiable*. Judgment owns everything right of it because
it is *context-sensitive and cheap for a model, expensive to hand-code*.

## Rules

1. **Model decides WHAT, code executes HOW.** A decision names one action +
   arguments (`goto x y`, `fight`, `catch`); the driver walks menus, waits
   out animations, verifies arrival. Never ask the model for frame-level
   input sequences; never let code pick strategic targets.

2. **One decision, one call.** Every action goes through the shared
   registry (`crystalagent/registry.py`) — validated kwargs, preconditions
   against live game state (`ui.battle`), one reply. No side doors: three
   drifting whitelists is how `serve.py cmd_run` shipped a NameError and
   `trek mart` became a silent no-op.

3. **Validate against LIVE state, not the plan.** Preconditions are checked
   against a fresh `observe()` at execution time ("needs an active battle"),
   not against what the decider believed two cycles ago.

4. **Bounded hard problem → algorithm.** Sokoban-style puzzles, ice slides,
   cross-map routing are algorithms over ground-truth grids, never model
   guesswork. Our grids come from the disassembly (`nav.py`), not runtime
   discovery — prefer compile-time truth.

5. **Verify steps, don't trust inputs.** Every movement primitive confirms
   its effect (`settle()`, position checks, step-hold through warps). With
   hooks live (`crystalagent/hookevents.py`) text pages and menus are events,
   not guesses; with hooks off, bounded polling heuristics apply.

6. **Fail loud at boundaries, soft in play.** Corrupt provenance stamps
   refuse a load (`emu.py`); a summarizer failure logs and plays on
   (`rolling.py`). Corruption must never silently fork a timeline; a lost
   convenience feature must never end a run.

7. **Evidence ranking for decisions.** Structured state > map view >
   rolling memory > raw screen text > screenshot. When sources disagree,
   the structured read wins; screens lie during transitions.

8. **Checkpoints before risk, journals always.** Fork before risky moves
   (savestate determinism makes retries free); journal every cycle with
   wall-clock time + frame spend so any session can be audited later.

9. **Never assert on prompts/screens in tests.** Unit tests cover pure
   logic (battle math, routing, parsers, classifiers); the emulator-in-loop
   harness (`scripts/trek_selftest.py`) covers integration. Prompt/screen
   assertions rot instantly.
