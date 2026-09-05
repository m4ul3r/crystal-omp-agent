# poke.run — Omarchy bar widget

A live view of a Pokemon agent run, in the Omarchy status bar. The harness
drives any Gen 1–3 game, so this widget names none of them: which cartridge is
booted, and every number derived from it, comes from the feed.

The bar slot shows the Torchic mark and the lead Pokemon's HP. Clicking it opens
a popup with the objective the agent is pursuing, the console framebuffer it is
actually running, the party with an HP bar on the lead, Pokedex and team
progress, where the player is standing, the opponent when a battle is on, run
totals, session counters, and the agent's own log lines as narration.

Nothing here drives the emulator. The widget only ever reads three files that
`pokeagent.live.LiveFeed` writes; the driving process is the sole writer.
A viewer crash, a second monitor, or ten open popups cannot perturb a run.

```
<feedDir>/<feed>.json     compact state snapshot, rewritten ~4x/second
<feedDir>/<feed>.png      current framebuffer, rewritten ~12x/second
<feedDir>/<feed>.jsonl    narration, one JSON object per line, appended
```

Every write is `write-to-temp` + `os.replace`, so a poll never catches half a
frame.

## Install

```bash
widget/install.sh
```

That copies the plugin to `~/.config/omarchy/plugins/poke.run/`, validates the
manifest with `omarchy plugin validate`, tells the running shell to rescan,
enables the widget on the bar, and pins `feedDir` to this checkout's live
directory. It never writes to `/usr/share/omarchy/` (package-owned, read-only)
and re-running it is a no-op beyond refreshing the files.

It also **retires this widget's previous id, `sapphire.run`**, if it finds it:
the new widget is enabled first, placed where the old one sat, and only then is
the old one disabled (which is what removes its `shell.json` bar entry) and its
folder moved to a dot-prefixed backup the shell's plugin scan ignores. Both
halves are conditional, so a second run does nothing.

Options:

| Flag | Effect |
|---|---|
| `--section left\|center\|right` | where to place the widget when enabling it |
| `--feed NAME` | pin a feed name other than `default` |
| `--no-enable` | copy the files only; print the enable commands |

If the shell is not running (installing from a TTY or over SSH), the files are
still copied and the remaining commands are printed.

### By hand

```bash
cp -r widget/poke.run ~/.config/omarchy/plugins/
omarchy-shell shell rescanPlugins
omarchy plugin enable poke.run center
omarchy bar set poke.run feedDir /path/to/sapphire-omarchy-widget/live
omarchy plugin remove sapphire.run --yes   # if you had the old id installed
```

Note that the shell hot-reloads plugin code on **every** write under
`~/.config/omarchy/plugins/`, so `cp -r` makes it load a half-copied plugin once
per file. `install.sh` stages the whole directory outside the watched tree and
swaps it in with a single rename for exactly that reason.

## Publish to it

```python
from trek import Driver
from pokeagent.live import LiveFeed

d = Driver(state_path="saves/littleroot.state")
feed = LiveFeed("default").attach(d)      # every d.goto()/d.fight() now streams
...
feed.detach()                             # leaves a final frame + live: false
```

`LiveFeed` also bridges the `trek` / `pokeagent` / `serve` / `newgame`
loggers into the narration file, so the popup reports what the agent decided
without any call site knowing the feed exists.

## What the popup reads

Everything below the first group is **optional**. A key the publisher does not
write is *not reported*: the section it would have filled is not rendered at
all. There is no placeholder, no zero, and no labelled blank — an older feed, or
a generation whose adapter cannot compute a number, must not make the widget
claim the run is at 0%.

| Key | Shown as |
|---|---|
| `t`, `live`, `frame`, `status`, `error` | staleness, the bar tooltip, the error banner |
| `party[]`, `in_battle`, `message` | party rows with an HP bar on the lead; the urgent colour in the bar |
| `map`, `pos {x, y, facing}` | header fallback, and the `MAP` / `POS` / `FACING` cells |
| `badges`, `money`, `play_time` | the `RUN` grid |
| `game {id, name, generation, region}` | the popup header: **which of Gen 1–3 is running** |
| `objective {name, detail, percent}` | the `OBJECTIVE` block, with a progress bar |
| `dex {caught, achievable, percent}` | the `POKEDEX` bar; `percent` is derived from the counts if absent |
| `team {min_level, max_level, spread, coverage_gaps[]}` | the `TEAM` block; an empty `coverage_gaps` reads as "none" |
| `enemy {species, level, hp, max_hp}` | the `OPPONENT` block, with the foe's HP bar |
| `counters {battles_won, caught, faints, saves, steps, frames}` | the `COUNTERS` grid |

`percent` is 0–100 and clamped. Anything non-numeric counts as not reported.

The framebuffer's **aspect is read off the published PNG**, not assumed: a GBA
frame is 240×160 and a Game Boy / Color frame is 160×144, so a Gen 1–2 run
renders correctly with no extra feed key and no table of consoles in the widget.

## The mark

`widget/make_logo.py` regenerates the logo from the decomp's own graphics:

```bash
.venv/bin/python widget/make_logo.py              # torchic
.venv/bin/python widget/make_logo.py --species mudkip
```

It keys out the background by **palette index 0** — the entry the GBA never
draws, which pret fills with a loud unused colour — rather than by colour, so it
is safe on the green starters too. Outputs land in `docs/logo/` and are copied
into `widget/poke.run/` so the installed plugin is self-contained:

```
docs/logo/torchic-64.png   64x64 still, from the battle sprite
docs/logo/torchic-32.png   32x32 still
docs/logo/torchic.gif      98x98, 8 frames, a one-pixel idle hop
```

Torchic breathes while a run is live and stands still, dimmed, when it is not —
the same fact the bar's dim/lit treatment carries, in a channel the eye catches
without being pointed at it. If the GIF cannot be loaded the still is used, and
if neither asset is present the widget falls back to a gamepad glyph rather than
leaving a hole in the bar.

## Settings

Settings live inline on the widget's entry in `~/.config/omarchy/shell.json`
(the shell has no separate per-plugin config file). Set them with
`omarchy bar set poke.run <key> <value>`, or through
Setup > Plugins in the Omarchy menu.

| Key | Default | Meaning |
|---|---|---|
| `feedDir` | *(unset)* | Directory the driver publishes into. Unset falls back to `$SAPPHIRE_LIVE_DIR` **as seen by the shell** (the same variable `pokeagent/paths.py` honours), then reports "not configured". |
| `feed` | `default` | The name passed to `LiveFeed(name=...)`. |
| `staleAfterSec` | `6` | How long without a new snapshot before the feed counts as dead. |
| `showFrame` | `true` | Show the framebuffer in the popup. |

`allowMultiple` is on, so two runs can be watched side by side:

```bash
omarchy plugin enable poke.run right
omarchy bar set poke.run feed second-run right
```

## Interactions

| Input | Effect |
|---|---|
| left click | open/close the popup |
| middle click | force a re-read of the feed files |
| right click | send the status line as a notification |
| `r` (popup focused) | force a re-read |
| `Esc` | close |

## States it can show

The widget stays visible when nothing is playing, because "the agent is not
running" is information, and a widget that disappears is indistinguishable from
a widget that is broken. In order of severity, the popup reports:

- **no feed directory configured** — with the `omarchy bar set` line to fix it
- **waiting for a run** — the exact path being watched, and the `LiveFeed(...)`
  call that would fill it
- **feed reports an error** — the publisher could not read the game (a boot or
  intro screen has no save block yet); the message is the publisher's own
- **run ended** — the last `detach()` wrote `live: false`, so this is a finished
  run rather than a broken feed
- **feed stale** — snapshots stopped arriving without a `detach()`: the driver
  crashed, was killed, or is sitting in a debugger

## Cost

Closed, the widget reads one small JSON file every 2 seconds. The framebuffer
PNG and the narration log are only read while the popup is open (2.5 Hz), so an
installed-but-unwatched widget costs a `stat` and a few hundred bytes. The mark's
animation is 8 frames of a 98×98 sprite and only plays while a run is live.

## Removing it

```bash
omarchy plugin disable poke.run
rm -rf ~/.config/omarchy/plugins/poke.run
```

## Files

```
widget/install.sh                  installer (idempotent, user-config only)
widget/make_logo.py                regenerates the mark from the decomp
widget/README.md                   this file
widget/poke.run/manifest.json      plugin manifest: id, kinds, settings schema
widget/poke.run/Panel.qml          bar slot + popup (the barWidget entry point)
widget/poke.run/Feed.qml           read-only feed reader (FileView + poll timer)
widget/poke.run/Model.js           pure parsing/formatting functions
widget/poke.run/torchic*.png|gif   the mark, copied from docs/logo/
```
