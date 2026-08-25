# Harness Improvement Writeup — through Badge 6 (session claude-wren)

State at writing: 6/8 badges (Mineral just won), team FERALIGATR L52 /
SUDOWOODO L38 / TOGETIC L37 / PIDGEOT L37, full suite 138 green,
fixer batch committed at 319bbe6.

## What we improved (landed, tested, verified in live play)

| Batch | Fixes | Live proof |
|---|---|---|
| Sprout Tower era | `_drain_scene` textbox auto-drain; verb retries (heal/mart/use_item); `_held_warp_entry` door glide; region-aware mapgraph + (map,region) route nodes | travel climbs Sprout Tower end-to-end; no more 20-replan textbox storms |
| Whitney era | cursor-glyph menu guard; WRAM `_pocket_select`; heal step-away; `save()` dirty-screen guard; `Driver.default_policy`; battle policy-action validation; frozen-screen wedge cap ('wedged' outcome) | Surf PP exhaustion degraded gracefully mid-ocean; zero battle wedges for two badges |
| Morty era | in-battle item executor rewrite; `norm_item`; LEARN transparency (`move_changes` + log lines); learn ACCEPT/REPLACE policy documented | the Bite→Scary Face bug class became visible, then preventable |
| Grind era | `Driver.learn_policy` (model decides move replacement); `set_text_speed` adopted; grinding recipe in HANDBOOK | SNAG 20→37 in 133s; REED forgot SAND-ATTACK for WING ATTACK *by policy* |
| pt5c batch (319bbe6) | mid-battle learn DECLINE fix (scroll-fragment parsing); `me`/`enemy` carry nickname+party_slot; level-up pages exempt from wedge diagnostics; move_changes `source` field | committed this session |

## What still needs improving (prioritized)

1. **`goto` silently no-ops on unreachable targets** — returns without moving
   or raising. Cost real minutes every leg. Should raise or set
   `last_goto_reason` unconditionally.
2. **Interior mapgraph coverage** — lighthouse warp expectations are wrong
   (travel unusable inside); Cianwood Gym boulders, hole-fall topology, and
   elevator floors are all outside the graph. Either model one-way
   warps/holes/pushables or mark maps "manual-only" so travel fails fast.
3. **Party-menu automation** — reorder needed screen-text targeting because
   the submenu lists FIELD MOVES above SWITCH for HM-carriers and the
   second-phase cursor is invisible to glyph scraping. A `Driver.reorder(nick)`
   using WRAM (like `_party_target`) would end this class.
4. **Battle throughput** — even at FAST text a wild battle costs ~8-12s.
   Biggest remaining lever for grinding is a turbo/frame-skip during
   `fight()` (pyboy supports speedup), gated to battles only.
5. **Policy ergonomics** — policies must re-read `game_state` every call
   (expensive) and there is no library of reusable policies (solo-trainee,
   bank-and-switch, gym-leader-with-items are rewritten each session).
   Ship them in trek as named policies.
6. **NPC-body pathing** — beaten trainers and wanderers park on choke cells;
   nav treats them as permanent walls ("npc on target cell" storms). Model
   wanderers as transient (wait-and-retry in goto) and beaten trainers as
   static obstacles in the live grid.
7. **Diagnostics budget** — trek-side `[fight diagnostic]` (the caller-side
   one) still spams identical dumps; give it the same 2-print cap
   battle.py's got.

## The meta-lesson

Every multi-whiteout incident in this run — Whitney, Morty, the grind wedge —
had the same anatomy: an invisible decision (auto-forget, default policy,
species-keyed identity) made by the harness on the model's behalf, silently.
The fixes that worked all moved the decision to the model (learn_policy,
default_policy) or made the harness's choice loud (move_changes, wedge
outcomes, source tags). The remaining backlog above continues that direction.
