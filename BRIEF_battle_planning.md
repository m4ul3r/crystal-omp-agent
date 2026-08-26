# BRIEF — battle planning: sacrifice lines and trainer heal items

## Context

`crystalagent/tactics.py` now does real Gen-2 damage maths: type multiplier,
per-TYPE physical/special split, STAB, badge boost, 85-100% spread, live PP,
never-miss effects, and (as of the last batch) accuracy/evasion **stages**.
`recommend()` picks a turn from that and states a reason. It is good.

It is also **single-turn greedy**, and that gap cost me the two hardest fights
of the run — I had to drive them by hand.

## Gap 1 — it cannot express a sacrifice line

Twice against Lance's L50 Dragonite ace, the winning play was:

> This mon is dead next turn no matter what I do. So spend its remaining turns
> on damage, let it faint, and the replacement enters **free** — a faint costs
> no switch-in hit, unlike a voluntary switch.

Concretely (live, both clears): RIPTIDE was doomed, chipped the ace 162 → 82
with two DRAGON RAGEs (a *fixed-damage* move, its STAB Surf being resisted),
fainted, and BROOK entered free and one-shot the remainder. `recommend()` cannot
represent any of that: it evaluates only the current turn and has no notion of
"doomed", "free switch on faint", or "who arrives next".

### What to build

Extend the recommender with a **doomed-mon** assessment, derived from data it
already has in the analysis dict:

- `doomed`: the enemy's best move's *minimum* ≥ my current HP, and I cannot
  certainly KO first (respect `faster`; a lethal move I outspeed is not lethal —
  that rule is already in `BATTLE.md` §8 and must not regress).
- When `doomed` is true, prefer **maximum expected damage** over any defensive
  option, and say so in the reason string: the mon's remaining value is the
  damage it deals before fainting, and the replacement's entry is free.
- Expose the successor: which party member would come in on a faint, and
  whether *it* can finish what is left (`hits_to_ko` against the enemy's HP
  after the chip). That is the whole basis of the line above.
- Do **not** silently switch instead. A voluntary switch concedes a free hit;
  the point of the sacrifice line is that a faint does not. `BATTLE.md` §9
  documents this asymmetry — keep the code and the doc agreeing.

Fixed-damage moves must be first-class in this path: DRAGON RAGE's flat 40
(`EFFECT_STATIC_DAMAGE`) was the correct chip precisely because Surf was
resisted. The module already models fixed damage; make sure the "maximise damage
while doomed" ranking uses it rather than falling back to base power.

## Gap 2 — it does not know trainers heal their ace

`data/trainers/attributes.asm` gives each trainer class its items. Verified
earlier this run: Will and Bruno carry MAX POTION; Karen FULL HEAL + MAX POTION;
**Koga and Lance carry FULL HEAL + FULL RESTORE**. The AI uses them **only on
the highest-level mon in its party**.

Observed live: Koga healed his Crobat from 10 → 26 HP mid-fight, exactly as
this rule predicts. I had already written the consequence into `BATTLE.md` §10
("Expect the ace to be healed once, and prefer burst over chip against it") —
but the recommender does not know it, so it will happily chip into a heal.

### What to build

- Parse `data/trainers/attributes.asm` into `{trainer_class: [items]}` with
  file:line provenance, in the same data-driven spirit as the rest of the module
  (no hardcoded lists).
- Surface something like `expects_heal(analysis) -> bool | dict`: true when this
  is a trainer battle, the opponent's class carries a healing item, and the mon
  in front of me is its highest-level mon.
- When it is true, bias the recommendation toward **burst** — prefer a line that
  can remove the mon inside the window before a heal lands, over accumulating
  chip damage that a FULL RESTORE erases. State it in the reason.
- Being unable to identify the trainer class must degrade to today's behaviour,
  never to an exception. This runs inside a live battle loop.

## Non-goals

- No full minimax or multi-turn search. Two specific, well-evidenced patterns:
  the doomed-mon sacrifice line, and heal-aware burst. Depth for its own sake is
  not wanted.
- Do not change the existing move-choice rule. It is documented in `BATTLE.md`
  §7 and verified over two Elite Four clears: *if two moves both certainly KO,
  reliability wins; if only the chancier move KOs, compare expected damage
  **taken**.* Reliability order for a certain kill is
  unmissable > listed 100% > bigger-but-chancier. Any regression there is a
  serious bug, not a refinement.

## Tests

Duck-typed fakes, no emulator boot, in `tests/unit/`. Cite `file:line` against
the real disassembly in docstrings where a claim comes from the source — the
established pattern is `tests/unit/test_tactics.py`.

- A doomed mon with a resisted STAB move and a fixed-damage move picks the
  **fixed-damage** move (the RIPTIDE/Dragon Rage case) and its reason mentions
  the free switch.
- A mon that *looks* doomed but outspeeds and certainly KOs does **not** take
  the sacrifice line (guards the §8 speed rule).
- `expects_heal` is true for a Lance/Koga-class ace and false for the same class
  when the mon in front is not the highest-level one; parse assertions cite
  `data/trainers/attributes.asm` lines.
- Regression: the §7 reliability ordering still holds — reuse/extend the
  existing certain-KO tie-break tests so a refactor cannot quietly invert them.

## File ownership — a sibling agent is working in parallel

**You own:** `crystalagent/tactics.py`, `crystalagent/decide.py`,
`tests/unit/test_tactics.py`, and any new `tests/unit/test_*` you add, plus the
`BATTLE.md` sections you need to keep truthful (§7/§9/§10).

**Do NOT edit:** `trek.py` (6,455 lines, collision hazard),
`tests/integration/**`, `tests/conftest.py`, `pyproject.toml`. A sibling agent
owns the integration lane. If you need a `Driver` method to expose something,
**describe the one-line shim you want in your final message** instead of editing
`trek.py`; it gets applied afterwards.

## Acceptance

- `pytest` green (the default lane; it excludes the emulator).
- The two new behaviours are demonstrated by tests that fail against the current
  recommender.
- Report which claims are disassembly-verified versus fake-only, and state
  plainly anything you could not do.
