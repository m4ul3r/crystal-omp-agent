# Who decides what

There is no language model in the play loop.

That surprises people, so it is worth stating plainly before the detail: when
`scripts/play.py` is running the game unattended, essentially every choice is
made by deterministic code reading the ROM's own data. A local 4B model is
attached, but it is only allowed to speak when the arithmetic has already
declared two options equal. The large model -- the agent you are talking to --
is not in that loop at all.

## The four layers

```
                                        who decides         how often
1  the game's own maths      tactics.py    damage formula     ~every battle turn
2  fixed policies            quest.py,     stated rules       every overworld step
                             team.py,
                             battle.default_learn
3  the local model           brain.py via  gemma4:e4b         ties only
                             smallchoices.py
4  the driving agent         serve.py /    an LLM or a human  only when driving
                             trek.Driver                       manually
```

### 1. The maths owns battle turns

`Tactics.recommend` scores every move with the game's own damage formula,
ported from `src/calculate_base_damage.c`: type chart, the Gen-3 per-type
physical/special split, STAB, badge boosts, the 85-100% roll, and effective
accuracy after live evasion stages. Then it ranks: a certain KO first (ties
broken by accuracy), then healing, then curing a turn-eating status, then a
resist switch, else best expected damage.

Every turn is logged with its reason, so the decision is auditable:

```
[battle] harness chose ('attack', 0): SCRATCH (slot 0) KOs now: 7-9 vs 5 HP, x1, 100% acc
[battle] T2 attack:0 SCRATCH#0 | me 20->20 | POOCHYENA 5->0  (chosen by tactics)
```

The `source` field on each turn records who chose: `policy`, `tactics`, or
`hook`. Nothing is anonymous.

### 2. Fixed policies own everything else

* **What to do next** -- `quest.py`. Reads the badge count, answers train /
  heal / travel / challenge. The level target is the *leader's own ace*, read
  from the decompilation's trainer data, so it cannot drift from the game.
* **Who fights** -- `team.training_policy`. Laggards must be the sole
  participant and land the KO, because Gen 3 splits experience.
* **Which move to forget** -- `battle.default_learn`. Never an HM, never trade
  a damaging move for a status one.
* **When to heal** -- a fraction of max HP, checked every step.

These are rules, not intelligence. They are in code because they are cheap,
reproducible and testable; that is the whole doctrine in `DESIGN.md`.

### 3. The local model gets the ties, and only the ties

`brain.py` talks to `gemma4:e4b` on the Ollama host. `smallchoices.py` is the
boundary, and the boundary was drawn by *measuring* the model rather than
guessing at it:

* 5/5 on clean single-hop type questions ("which type is strongest against
  water?");
* wrong on multi-hop ones (it answered `THUNDERBOLT` beats Water when the
  question was about a move it did not have) and on numeric judgment.

So it is asked only things that cannot be wrong in a costly way: a nickname, a
tie between two moves the damage maths scored *identically*, which of several
equally-good catch targets to prefer. `tied_move` refuses to consult it at all
when one move is a certain KO -- the arithmetic wins and `last_reason` says
`arithmetic`.

In practice it is asked very little. A run reports its own counts:

```
small choices: {'consulted': 0, 'declined': 1, 'enabled': True}
```

`declined: 1` there means the model was available, a tie was offered, and the
maths turned out to separate the moves after all.

When the host is unreachable the fallback is the deterministic answer and the
run does not notice -- measured at 0.001s, versus 1.4-3.3s for a live call.

### 4. The driving agent is not in the loop

The harness was built for an LLM to drive it turn by turn, and that still
works: `serve.py` speaks NDJSON, `registry.py` validates 25 verbs, and
`Driver.fight(require_decision=True)` raises `DecisionRequired` rather than
letting the harness guess. That is the mode used when a model is genuinely
playing.

`scripts/play.py` is the *unattended* mode -- the idle-game loop. It uses
layers 1-3 only. If you want the big model deciding, you drive through
`serve.py` or a warm `Driver`; the loop is what runs when nobody is watching.

## Why this split

From `DESIGN.md`: code owns what is cheap, reproducible and verifiable;
judgment owns what is context-sensitive. The predecessor project's own
retrospective lists "the harness decided for the model" as the cause of every
one of its multi-whiteout runs -- which is why movement never silently
auto-fights, why a naming keyboard and a move-learn prompt both stop the scene
runner instead of taking a blind A, and why every automatic choice carries a
reason string you can read afterwards.

The inverse failure is just as real: handing a 4B model a decision the damage
formula already answers exactly. Hence the measured boundary rather than an
enthusiastic one.
