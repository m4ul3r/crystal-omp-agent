# BATTLE.md — Gen-2 combat mechanics, distilled for a driving agent

Everything here is verified against the pokecrystal disassembly (paths are
relative to `/media/ssd/pokecrystal`) or measured live in this repo. Read this
before deciding a turn; it exists so the same lessons are not re-learned by
losing Pokémon.

Companion: `AGENTS.md` (session protocol, gotchas), `HANDBOOK.md` (how to call
the machine), `PROGRESS.md` (live journal). The code that implements this file
is `crystalagent/tactics.py`.

---

## 1. The instrument

```python
a = d.outlook()                     # None if no battle / pre-init
print(d.tactics.explain(a))         # one auditable line per move
action, why = d.tactics.recommend(a, d.battle_frame())
```

`outlook()` returns, for the mon actually standing there: every one of my
moves with its type multiplier, physical/special category, damage span,
`ko_certain` / `ko_possible`, `hits_to_ko`, `accuracy`, `never_misses`, live
`pp`, and engine `slot`; plus the enemy's moves aimed back at me, and who
moves first.

**Read the damage span, not the multiplier.** A 4× hit from a weak attacker is
weaker than a 1× hit from a strong one — live: Hitmonchan's Ice Punch is ×4 on
a Dragonite but its Sp.Atk is 45, so it dealt 30–36, not the ~120 the
multiplier suggests.

---

## 2. The damage formula (as implemented)

`engine/battle/effect_commands.asm`:

1. **`BattleCommand_DamageCalc` (:2900)** — `((2·level/5 + 2) · power · Atk / Def) / 50`,
   then item boost, then crit (×2), capped at 999, then `+MIN_DAMAGE` (2).
2. **`BattleCommand_Stab` (:1214)** — weather, then **badge boost**, then STAB
   as `d + d/2` (×1.5), then the type-matchup rows one at a time.
3. **`BattleCommand_DamageVariation` (:1496)** — × 85–100% (`BattleRandom` in
   217..255 over 255). This is why every estimate is a *span*.

**Badge boost** (`engine/battle/misc.asm:147`, `DoBadgeTypeBoosts`): on the
**player's turn only**, damage of a type covered by an earned badge gains
`d/8` (min 1). Table: `data/types/badge_type_boosts.asm`. With 8 Johto badges
the boosted attacking types are **FLYING, BUG, NORMAL, GHOST, STEEL, FIGHTING,
ICE, DRAGON**. WATER is *not* boosted without CASCADEBADGE — a Feraligatr's
Surf never gets it in a Johto-only run.

---

## 3. Physical vs special is per **TYPE**, not per move

`constants/type_constants.asm:26` — `DEF SPECIAL EQU const_value`. Types with
id **< 20 are physical** (NORMAL, FIGHTING, FLYING, POISON, GROUND, ROCK, BUG,
GHOST, STEEL); id **≥ 20 are special** (FIRE, WATER, GRASS, ELECTRIC, PSYCHIC,
ICE, DRAGON, DARK).

Consequences that decided real turns:

- **IRON TAIL (Steel) is physical; DRAGONBREATH (Dragon) is special.** Against
  a wall with huge Defense and ordinary Sp.Def, the *lower-power special* move
  wins. Live: chose HYDRO PUMP over STRENGTH into Will's Slowbro.
- Screens: Reflect doubles Defense, Light Screen doubles Sp.Def
  (`effect_commands.asm:2532`), so they blunt exactly one category.
- **Amnesia / Acid Armor only blunt one side too.** Live: Slowbro used Amnesia
  (Sp.Def 81 → 162) and Dragonbreath collapsed 87–103 → 45–54 while the
  physical Wing Attack stayed 89–105. If a defensive buff is coming, attack
  the side it does not protect.

---

## 4. Type ids are the GAME's ids

`const_next 19` (`type_constants.asm:22`) jumps the unused block, so **FIRE is
20 … DARK is 27**. A parser that counts `const` lines from zero shifts every
special type down by 9; because ROM move types and the WRAM type bytes are
real ids, every special-type lookup then misses the chart and reads as a flat
**1.0** — no super-effective, no resistance, no immunity. This bug was live in
this repo and made FAINT ATTACK read ×1 into Exeggutor when Dark is ×2 on
Psychic. Fixed in `crystalagent/battle.py::_parse_types`; regression-tested.

Chart facts I got wrong from memory and had to correct against the file:

| I assumed | Truth (Gen 2) |
|---|---|
| Electric ×2 on Dragon/Flying | **Dragon RESISTS Electric (0.5)** → net ×1 |
| Flying resists Flying | neutral (×1) |
| Water ×4 on Aerodactyl/Charizard | Flying is neutral to Water → **×2** |

**Check the chart before committing to a matchup.** Mono-type mons store their
type twice; the engine applies each row once (`CheckTypeMatchup`), so Water vs
a mono-Water mon is 0.5, not 0.25.

---

## 5. Accuracy is a 0–255 byte

`macros/data.asm:23` — `DEF percent EQUS "* $ff / 100"`. Iron Tail's 75% is
stored as **191**. Reading it as `min(byte, 100)` reports every move above
~39% as a flat 100%, which silently deletes the entire accuracy dimension.
Real values: WING ATTACK 100, IRON TAIL **75**, DYNAMICPUNCH **50**,
HYDRO PUMP 80, BLIZZARD 70, THUNDER 70.

### The listed number is not the number: accuracy/evasion STAGES

`BattleCommand_CheckHit.StatModifiers`
(`engine/battle/effect_commands.asm:1758`) runs the move's raw accuracy byte
through `AccuracyLevelMultipliers` (`data/battle/accuracy_multipliers.asm`)
**twice**: once with the attacker's accuracy stage, once with
`MAX_STAT_LEVEL + 1 - the target's evasion stage`. Each pass multiplies, then
integer-divides, then floors at 1; the result is capped at `$ff`. Stages run
1..13 with **7 neutral** (`BASE_STAT_LEVEL`/`MAX_STAT_LEVEL`,
`constants/battle_constants.asm:10`), and live in `wPlayerAccLevel` /
`wPlayerEvaLevel` / `wEnemyAccLevel` / `wEnemyEvaLevel`.

`outlook()` reports this as `effective_accuracy` beside the listed
`accuracy`, and `_score`, `recommend()`'s KO tie-break and `explain()` all
rank on it:

```
  KO SURF   WATER  spec x1  219-258 (568.8%) acc  60 (listed 100) STAB
  KO CUT    NORMAL phys x1  120-142 (308.7%) acc  57 (listed 95)
```

That is a live read with the enemy at evasion stage 9 (two MINIMIZEs): a
"100%" move really lands 60% of the time. Two more consequences worth knowing:

- **+2 accuracy does not cancel +2 evasion.** The engine truncates after each
  pass (255 → 423 → 253), so the honest answer is 99%, not 100%.
- **Foresight, X ACCURACY and rain-Thunder are not modelled.** The only
  error is over-reporting evasion against a Foresighted target.

### Never-miss moves

`EFFECT_ALWAYS_HIT` (`data/moves/moves.asm` — SWIFT :145, FAINT ATTACK :201)
skips the accuracy check entirely, so it ignores **evasion** too. This is the
only reliable answer to a MINIMIZE / DOUBLE TEAM stack. Live: Koga's Muk and
Crobat blanked two listed-100% Wing Attacks in a row, while a 15–18 damage
FAINT ATTACK finished each on demand. `outlook()` flags these as
`never_misses`.

---

## 6. Fixed-damage moves are not "powerless"

A power-based move picker throws these away. They are keyed by *effect*:

| effect | behaviour | example |
|---|---|---|
| `EFFECT_STATIC_DAMAGE` | flat damage equal to the move's `power` field | DRAGON RAGE = 40, SONICBOOM = 20 |
| `EFFECT_LEVEL_DAMAGE` | damage = user's level | SEISMIC TOSS, NIGHT SHADE |
| `EFFECT_PSYWAVE` | random, up to ~1.5× level | PSYWAVE |
| `EFFECT_SUPER_FANG` | half the target's CURRENT HP | SUPER FANG |
| `EFFECT_OHKO` | full HP, very low accuracy | FISSURE etc. |

They ignore stats **but not immunity** — Seismic Toss does nothing to a Ghost.
Live payoff: against Lance's L50 Dragonite, RIPTIDE's STAB Surf was resisted
(0.5×, 20–24) so its best move was **Dragon Rage's flat 40**.

---

## 7. Choosing a move: the actual rule

Not "highest damage", and not "prefer accuracy" either.

> **If two moves both certainly KO, the bigger number is worth nothing and the
> miss chance is worth everything → take the reliable one.
> If only the chancier move KOs, take the gamble — compare expected damage
> TAKEN, not dealt.**

Worked examples, both live:

- **Will's Jynx (113 HP).** Iron Tail ×2 = 478–562 at 75%; Wing Attack = 215–253
  at 100%. Both one-shot ⇒ Wing Attack. A 25% miss buys nothing but a free
  Ice Punch.
- **Lance's Aerodactyl (139 HP).** Iron Tail ×2 = 297–350 at 75% is the *only*
  one-shot; Dragonbreath needs two guaranteed turns. Expected damage taken
  ≈ 0.25 × 55 ≈ **18** (gamble) vs ≈ **55** (safe) ⇒ take the 75%.

Reliability order for a certain kill: **unmissable > listed 100% > bigger but
chancier.** Implemented in `Tactics._score` / `recommend()`.

**When a whiff has a tail risk, weight accuracy harder.** Karen's Gengar holds
DESTINY BOND: missing hands it the turn it needs to trade for my killer, so
the 75% move is strictly wrong even though it hits harder.

---

## 8. Speed decides whether a threat exists at all

`outlook()['faster']`. If I outspeed and my move certainly KOs, the enemy's
scariest move is irrelevant — it never happens. Live: Machamp's Rock Slide was
×2 for 42–50 against BROOK's 52 HP (lethal), and it simply never moved.

Corollary: a lethal incoming move only matters when it will actually resolve —
check `faster` before spending a turn on healing or switching.

---

## 9. Switching

- `battle_frame()['can_switch']` lists **legal** targets. It is empty while the
  active mon is trapped.
- **Trapped mons cannot be recalled.** `TryPlayerSwitch .check_trapped`
  (`engine/battle/core.asm`) answers a confirmed SWITCH with
  `BattleText_MonCantBeRecalled` and jumps back to the party list with **no
  turn consumed**. Triggers: `wPlayerWrapCount != 0` (BIND / WRAP / FIRE SPIN /
  CLAMP / WHIRLPOOL) or `wEnemySubStatus5 & (1<<SUBSTATUS_CANT_RUN)`
  (MEAN LOOK / SPIDER WEB). Same shape blocks RUN.
- **A switch costs a free hit; a faint does not.** When a mon faints, the
  replacement enters *without* being hit. Use this: if a mon is dead anyway,
  spend its last turns on damage and let the next mon arrive free. Live:
  RIPTIDE chipped Lance's ace 162 → 82 with two Dragon Rages, fainted, and
  BROOK entered free and one-shot it.
  `recommend()` now enforces this as the **sacrifice line**: when the
  enemy's best move kills my active mon on its *minimum* roll and I cannot
  certainly KO first (respecting `faster` -- §8), it attacks for maximum
  expected damage (fixed-damage moves compete on their flat number, so
  Dragon Rage beat a resisted STAB Surf) and NEVER voluntarily switches --
  a switch would concede exactly the hit the faint avoids. `outlook()`
  consumers get `tactics.sacrifice_line(analysis, frame)`:
  `{pick, chip_min, enemy_hp_after_chip, successor, successor_finishes}`
  where the successor is the top `switch_options` entry and
  `successor_finishes` is its best move's `hits_to_ko` against the chipped
  HP (needs the frame roster's moves/stats, which `read_party` provides).
- **Switching resets a badly-poisoned mon to ordinary poison** (Toxic's
  escalating counter clears), which is sometimes reason enough to rotate.
- Switch *to* the mon that **resists** what is incoming, not merely the
  healthiest one. Nothing in my roster resisted Psychic, so against Will the
  right answer was to attack and say so, not to shuffle.

---

## 10. Status and buffs worth pre-empting

| enemy move | why it matters | answer |
|---|---|---|
| MINIMIZE / DOUBLE TEAM | evasion; "100%" moves start whiffing | never-miss move, or kill before it stacks |
| TOXIC | escalating damage, persists after the battle | kill fast; switch to downgrade it; cure in battle |
| AMNESIA / ACID ARMOR / REFLECT | blunt exactly one damage category | attack the other category |
| DESTINY BOND | takes my killer with it | do not miss; kill it before it can set up |
| SPIDER WEB / MEAN LOOK | trap; switching becomes illegal | kill it first, or accept being locked in |
| WHIRLWIND / ROAR | forces me out | kill it first |
| EXPLOSION / SELFDESTRUCT | huge, halves my Defense in the calc | price it (it is often survivable), or let it kill a mon I can spare |
| PROTECT | blanks one turn | just swing again; consecutive Protects fail more |
| HYPER BEAM | 150 power then a recharge turn | survive it and get a free turn |
| SAND ATTACK / SMOKESCREEN | lowers MY accuracy, so a listed 100% move starts missing | same bucket as evasion: kill it fast, or use a never-miss move |
| THUNDER WAVE | halves my speed and costs ~25% of my turns outright — it flips turn order, and §8 says turn order decides whether threats exist at all | cure it in the CHEAPEST window (while a weak enemy is out), not mid-crisis. Live: Lance's L47 Dragonites paralysed both BROOK (148→37) and RIPTIDE (118→29); curing with a FULL HEAL during Charizard bought back the speed to out-run the L50's Outrage |

Trainer AI carries items: Will/Bruno MAX POTION, Karen FULL HEAL + MAX POTION,
Koga and Lance FULL HEAL + FULL RESTORE (`data/trainers/attributes.asm`), used
only on their **highest-level** mon. Expect the ace to be healed once, and
prefer burst over chip against it.
Both halves are now disassembly-verified and implemented:
`AI_TryItem` refuses every enemy item unless the active mon is the
highest-level one (`.IsHighestLevel`, `engine/battle/ai/items.asm:167`
and :242), and HP heals fire once that mon drops to half HP (`.HealItem`,
ibid.:346, via `AICheckEnemyHalfHP`). `tactics.parse_trainer_items()` parses
the class table with `file:line` provenance, `outlook()['trainer']` carries
the live class + OT-party levels, and `tactics.expects_heal(analysis)` is
true exactly for a healer-class ace -- `recommend()` then prefers the move
with the fewest hits-to-KO over bigger expected chip ("burst over chip"),
because a FULL RESTORE erases accumulated damage. Unknown class or levels
degrade to no bias.

### Status costs TURNS, and the harness now says how many

`outlook()` carries `my_status` / `their_status` (`_status` names -- `PAR`,
`SLP:3`, ...), `my_confused` / `their_confused` (SUBSTATUS_CONFUSED, bit 7 of
`wPlayerSubStatus3`), and `turn_loss`: the share of my turns the status is
expected to eat.

| state | turn_loss | source |
|---|---|---|
| PAR | 0.25 | `cp 25 percent` (`effect_commands.asm:323`) |
| confusion | 0.5 | self-hit roll (`effect_commands.asm:494`) |
| PAR + confusion | 0.625 | compounded |
| SLP / FRZ | 1.0 | the engine returns before the move runs |
| PSN / BRN | 0 | they cost HP, not turns |

`recommend()` spends the cheapest ROM-priced cure (PARLYZ HEAL ¥200 before
FULL HEAL ¥600) on PAR/SLP/FRZ when nothing lethal is incoming and no KO is
available. PSN/BRN are deliberately left to the potion branch and
`heal_party()`.

**Do not halve speed for paralysis yourself.** `ApplyPrzEffectOnSpeed`
(`engine/battle/core.asm:6585`) writes the halved value straight into
`wBattleMonSpeed`/`wEnemyMonSpeed` when the paralysis lands, so
`outlook()['faster']` -- a raw compare of those words -- already accounts for
it. Halving again double-counts.

---

## 11. Items, mine

- **In battle**: `('item', 'FULL RESTORE')` works and is the reliable path.
  Verified live: cured poison and healed 49 → 202 in one turn. Item use
  resolves at the start of the turn, before the enemy's move.
- **Out of battle**: `d.use_item(name, mon="BROOK")` / `target_slot=N`, and
  `d.heal_party()` to top everyone up with the cheapest sufficient item.
- A full-HP, unstatused mon genuinely cannot be healed — that is a real no-op,
  not a harness failure.

---

## 12. Turn loop for a model-driven battle

Battles **cannot** be delegated to a subagent: the emulator lives in the
deciding process's kernel, and subagents get independent kernels, so they would
have to boot a fresh `Driver` from a savestate — impossible mid-battle.

```python
d.default_policy = None
d.decide_all = True                 # every turn comes back to me
PENDING = []
def manual(frame):                  # one queued action per call
    return PENDING.pop(0) if PENDING else None

def play(action):
    PENDING[:] = [action]
    try:
        d.fight(policy=manual, require_decision=True)
        return None                 # battle finished
    except trek.DecisionRequired:
        pass
    return d.outlook()              # next turn's numbers
```

Per turn: read `outlook()`, apply §7 (KO first, reliability among kills), §8
(does the threat even resolve?), §9 (is switching legal and does anything
actually resist?), then queue exactly one action.

---

## 13. Pre-flight checklist

1. `d.tactics` badge types match the save's badges (damage is +1/8 per boosted type).
2. Accuracies are real (`IRON TAIL` must read 75, not 100) — and read
   `effective_accuracy`, not `accuracy`, once anything has used MINIMIZE,
   DOUBLE TEAM, SAND-ATTACK or SMOKESCREEN.
3. `DARK -> PSYCHIC` is 2.0 (proves the type ids are the game's).
4. Party healed and de-statused; `heal_party()` then verify HP/status.
5. Fork the save before a gauntlet: `claude_saves/<agent>-pre-<thing>.state` + `.meta`.
