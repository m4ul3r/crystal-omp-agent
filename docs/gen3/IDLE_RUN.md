# The idle run — power-on to unattended play at hardware speed

`scripts/idle_run.py` resets the cartridge and lets the harness play the game
to itself at the GBA's real 59.7275 Hz, publishing every frame to the widget.
No savestate tour, no tool-assisted speedrun: the title screen is sat through,
a new game is named, and the existing story chain takes it from there.

```
env -u DISPLAY -u WAYLAND_DISPLAY LD_LIBRARY_PATH=vendor/lib \
  .venv/bin/python scripts/idle_run.py --minutes 45 --fps hardware --feed idle
```

Useful flags: `--fps off` (flat out, ~17x faster, for testing the chain),
`--prefix NAME` (checkpoint stem), `--resume` (pick up from the newest
checkpoint of that prefix), `--name`/`--girl`/`--starter`/`--nickname`,
`--brain` (let the local model break ties — off by default because an
unreachable ollama stalls a paced run).

## The chain

Three legs, all of them code that already existed:

| leg | what it does | reused from |
| --- | --- | --- |
| `boot` | attract sequence → NEW GAME → gender → naming keyboard → control | `scripts/newgame.py` (`drive_until`, `GENDER_TASK`) |
| `intro` | truck → clock → rival → Route 101 → starter → first fight | `to_starter.drive_intro` |
| `play` | everything after that: objective engine, catching, training, saving | `play.Session` |

Legs 1 and 2 share one emulator. Leg 3 is a second one, because `play.Session`
builds its own `Driver`; the feed is detached and re-attached across the seam
so exactly one publisher exists at a time.

**Pacing needed no de-TASsing.** `advance_scene(N)` was always a frame *budget*
that returns when the scene settles, so the game was already being stepped one
frame at a time — headless mGBA was simply running those frames at ~1300 fps.
`Sapphire._pace` (`pokeagent/emu.py:303`) throttles inside `tick`, which every
frame in the harness goes through. The rate is set through `SAPPHIRE_FPS`
rather than a constructor argument because the adapter builds the emulator
(`pokeagent/adapters/gen3.py:61`) and leg 3 builds a second one; the
environment variable is the one lever both see.

## Evidence: the paced run

```
scripts/idle_run.py --minutes 30 --fps hardware --feed idle --prefix idle
```

```
emulator paced at 59.7275 fps (SAPPHIRE_FPS=hardware)
ok   boot       2:07      7600 frames    59.7 fps  InsideOfTruck (2, 2)  player 'AGENT' has control
ok   intro     10:44     29274 frames    45.4 fps  LittlerootTown_ProfessorBirchsLab (6, 5)  party is not empty
ok   play      21:07     42231 frames    33.3 fps  Route103 (10, 3)  budget spent
total 34:01  79105 frames  38.8 fps average
stopped in Route103 (10, 3): budget spent
```

### Wall-clock per milestone at 59.7 fps

Cumulative from power-on, as logged by the `Chronicle` observer:

| wall | frame | milestone | where |
| --- | --- | --- | --- |
| 0:56 | 3270 | title screen | — |
| 1:37 | 5744 | gender chosen | — |
| 1:44 | 6200 | named `AGENT` | — |
| **2:08** | **7600** | **player control** | **InsideOfTruck (2, 2)** |
| 2:34 | 9143 | stepped outside | LittlerootTown (3, 10) |
| **3:30** | **12505** | **the bedroom** | **LittlerootTown_BrendansHouse_2F (7, 2)** |
| 6:27 | 20641 | clock set, rival's bedroom | LittlerootTown_MaysHouse_2F (1, 2) |
| 9:31 | 27135 | north gate open | Route101 (10, 19) |
| 11:18 | 32568 | Birch's lab | LittlerootTown_ProfessorBirchsLab (6, 5) |
| 12:53 | 36874 | intro complete: starter, Poochyena fight, nickname | same |
| 14:41 | 39987 | first new town | OldaleTown (8, 18) |
| 15:44 | 42144 | Route 103 | Route103 (8, 19) |
| 27:08 | 64184 | healed | OldaleTown_PokemonCenter_1F (7, 8) |
| 34:01 | 79105 | rival beaten on Route 103 (Pokédex gate cleared) | Route103 (10, 3) |

Sapphire does **not** hand over control in a bedroom: the first frame the
player owns is inside the moving truck. The bedroom is the clock, 82 seconds
later.

### Measured frame rate, and what the shortfall means

The `boot` leg measured **59.7 fps** — the target exactly — because it barely
thinks between ticks. The other two legs came in *under* the target: 45.4 and
33.3 fps. That is not the pacer overshooting; the pacer can only ever make the
emulator slower. The deficit is harness think-time — route BFS, party decrypt,
feed encode — during which the emulator is stopped:

| leg | measured | fraction of real time | time not spent emulating |
| --- | --- | --- | --- |
| boot | 59.7 fps | 100% | ~0% |
| intro | 45.4 fps | 76% | 24% |
| play | 33.3 fps | 56% | 44% |

So a hardware-paced run is *at most* real-time and in the play loop runs at
about 0.56x. 79,105 frames is 22 minutes of in-game time bought with 34
minutes of wall clock. Anyone budgeting a demo should use **~2,000 frames per
wall-clock minute**, not 3,584.

## How far it got unattended

At hardware speed 30 minutes only reaches the Pokédex gate, so the same chain
was resumed unthrottled to find the actual wall:

```
scripts/idle_run.py --resume --minutes 40 --fps off --feed idle-far --prefix idle-far
```

```
ok   play      43:07    499330 frames   193.0 fps  PetalburgCity_PokemonCenter_1F (7, 8)  budget spent
metrics: {'session': 'idle-far-play', 'hours': 0.72, 'frames': 499330,
          'play_time': '2:33:44', 'badges': 0, 'species_caught': 3, 'events': 4}
```

Two and a half hours of game time, entirely unattended, no human input at any
point. In that window it: beat the rival on Route 103, collected the Pokédex
from Birch (`story advanced` at 0:55), crossed Route 102, caught three new
species, reached Petalburg City at 14:12 — and then **stopped progressing**.
From roughly the 15-minute mark to the end, every status line reads
`PetalburgCity_PokemonCenter_1F(7, 8)` or `OldaleTown_PokemonCenter_1F(7, 8)`.
Zero badges.

(Every mon it caught is nicknamed `A` in these logs. That was
`pokeagent/naming.py`'s `accept()` typing a character instead of confirming —
diagnosed and fixed in main while this run was in flight, so a fresh idle run
will not reproduce it.)

## Where it needed a human, and why

**First and only hard wall: the Pokémon Centre heal cycle.** `could not heal:
the nurse was talked to but the party is still spent` appears **17 times** in
the 43-minute run. Each failure sends the loop back round — heal, fail, train,
heal — and the story step that would actually advance the game (*see Norman in
the Petalburg gym, which opens Route 104*, `pokeagent/quest.py:111-115`) is
starved of attempts: it was tried twice in 43 minutes and lost both times to
`could not reach PetalburgCity_Gym`.

It is **flaky, not deterministic**, which is what makes it expensive:

* loading the exact failing savestate and calling `d.heal()` succeeds —
  `EMBER 5/31 → 31/31`, `A 5/14 → 14/14`, player ends in front of the nurse at
  (7, 4);
* `d.heal_at_nearest_center()` on the same state succeeds too, with
  `talk_to(7,2) -> True`;
* a 3-minute `play.py` run from that same state healed on the first attempt
  and went on to *arrive at PetalburgCity_Gym*.

So the nurse conversation intermittently fails to complete, and at hardware
pacing each retry costs 30–60 seconds of wall clock rather than 2 seconds.
That is the difference between the paced run reaching the Pokédex gate and the
unthrottled run reaching Petalburg.

**The failure reason is unreliable, which is why it took three probes to pin
down.** `Driver.heal` sets a specific reason when the nurse cannot be reached
(`pokeagent/trek.py:2907`) but then `continue`s, and the generic
"the nurse was talked to but the party is still spent" at
`pokeagent/trek.py:2924` overwrites it unconditionally on the way out. The log
therefore cannot distinguish *never reached the nurse* from *healed and it
didn't take*. Fixing that overwrite is the cheapest next step for anyone
attacking this.

Two smaller time sinks, both of which the loop recovers from on its own:

* `the clerk did not open a shop` — Oldale Mart, three round trips before the
  loop gave up on restocking. Pre-Pokédex the clerk is not a shop.
* `no walkable route from Route103 to Route102` ×8, then the same for
  Route 110 — genuinely closed gates before the Pokédex, retried eight times
  each because `TRAVEL_ATTEMPTS = 8` (`scripts/play.py:56`). Unthrottled that
  is seconds; paced it was about four minutes.

**`--minutes` is a soft budget.** The 30-minute run took 34:01. `Session`
checks `out_of_time()` between steps, and one paced step — a travel with a
45-second emulated budget, or a battle — can own several wall-clock minutes.
Budget 10% slack.

## Checkpoints

Written under `saves/<prefix>-…`, never to `saves/line3.state` (the script
refuses by path comparison):

| file | when |
| --- | --- |
| `<prefix>-boot.state` | player has control, before the intro leg |
| `<prefix>-starter.state` | party exists, before the play loop |
| `<prefix>-play.state` | the loop's working copy — it adopts and refreshes whatever file it is handed (`scripts/play.py:382`), so the two milestones above stay pristine |
| `<prefix>-final.state` | the loop's budget ran out |

`--resume` picks the newest of `-play`, `-starter`, `-boot` in that order, so
re-running the same command continues the same timeline instead of restarting
the game — which is what an idle game should do after a machine reboot.

## The live feed

One publisher per emulator. The `Driver` auto-attaches a feed named after its
state file (`pokeagent/trek.py:1840`), so `--feed` *displaces* that publisher
rather than adding a second — the same move `scripts/elite_four.py:431` makes.
The observer slot is then taken by `Chronicle`, which forwards to the feed and
timestamps every new map on the way past; that is where the milestone table
above comes from, and it costs one guarded location read twice a second.

Widget watchers: `--feed default` puts it where the bar looks. The name
defaults to `idle` so an unattended demo cannot silently stomp a canonical run
that is already publishing.
