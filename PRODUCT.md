# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

(Quickshell/QML bar plugin inside the Omarchy desktop shell. It is not a
browser surface, but it is a screen surface with the shell's own design
tokens, fonts and components; no native-OS design language applies.)

## Users

The person running the agent, on the same machine, glancing at the popup for
a few seconds several times an hour while doing other work. One user, one
screen (2880x1800 at 2x, 11px base font in the shell), the popup hanging off
a top-bar widget.

## Product Purpose

`poke.run` is an Omarchy status-bar widget that shows a Pokémon agent's run:
the bar slot carries the lead Pokémon's HP; the popup answers "is it alive
and how is it doing" — live or stale, the console frame, where it is, what it
is fighting, what it is trying to do — with the run's totals secondary.

## Positioning

Read-only view of a feed the driver publishes (`live/<feed>.json/.png/.jsonl`);
the widget never touches the emulator, so nothing a viewer does can perturb a
run. Any Gen 1–3 game; which cartridge is booted, and every number derived
from it, comes from the feed, never from the widget.

## Operating Context

- The agent is started from a terminal (`scripts/play.py --feed default`) and
  can run for hours unattended; it re-saves the state on autosave ticks.
- The widget polls the feed at ~0.5 Hz closed, 2.5 Hz open; frames arrive at
  ~12 fps while a run is live.
- Sprites and the Torchic mark are generated locally from the decomp
  (`widget/make_sprites.py`, `widget/make_logo.py`); nothing from the
  cartridge is committed.
- Installed by `widget/install.sh` into `~/.config/omarchy/plugins/poke.run/`;
  the shell hot-reloads files but may need `omarchy restart shell` after a
  whole-directory swap.

## Capabilities and Constraints

- Feed keys (all optional past the first group): `t live frame status error`,
  `party[] in_battle message`, `map pos`, `badges money play_time`, `game`,
  `objective`, `dex`, `stages[]`, `team`, `enemy`, `counters`, `projection`;
  narration lines from the `.jsonl`.
- An unreported key renders nothing — never a zero or a placeholder.
- The framebuffer is 240x160 (GBA) or 160x144 (Game Boy); it must be drawn at
  a whole number of device pixels per source pixel.
- Nothing above the frame may change height within a session (it moves the
  picture); everything below may reflow at the rate its content changes.
- Repeaters bind to lengths, not arrays (delegate churn is visible pop-in).
- The popup may be wide: two columns are acceptable; it must fit above the
  fold on a 1440x900-logical screen with a top bar.
- Undecided: whether the game's message-buffer text is shown in the popup
  (the 3x frame already shows the text box).

## Brand Commitments

The Torchic mark (animated while live, still and dimmed when not) and the
shell's own palette, monospace font and `qs.Ui` components. Section labels
are the shell's `PanelSectionHeader`.

## Evidence on Hand

- Live feed from a real run: `~/Documents/crystal-omp-agent/live/default.*`.
- 384 party sprites and the mark under `widget/poke.run/`.
- Screenshots of the current popup at device pixels in `/tmp/p8.png` (session
  only).

## Product Principles

- Truth from the feed only; absence is rendered as absence.
- Glanceable: the answer to "alive? how?" in the first second, totals after.
- The frame is the object; chrome never competes with it.
- One vocabulary: the shell's own components and tokens, so the popup reads
  as part of Omarchy, not as a foreign dashboard.
- Stability over reactivity: layout never jitters at game speed.
