---
version: 1
slug: "widget-poke-run-panel-qml"
primary_target: "widget/poke.run/Panel.qml"
related_targets: []
---

# Surface: poke.run popup (widget/poke.run/Panel.qml)

Scope: the bar widget's popup. Visitor mode: Operate (glance: alive? how?).
Audience: the one user, mid-work, a few seconds at a time.

## Direction contract

THESIS: A cockpit — the screen dead centre, instruments either side — instead
of the category's tall labelled list. Nothing scrolls above the fold.

OWN-WORLD: The shell's own palette, mono face and qs.Ui components; the only
colour is the game's, the sprites' and the HP greens. Frame in a dark bezel at
an integer device scale.

STORY: Glance: mark lit, frame moving, lead bar green — alive. Next second:
where, what it fights, what it is trying to do. Totals last.

FIRST VIEWPORT: ~760 logical wide. Hero row. Left column: six party slots
stacked (sprite, name, level, bar), lead top. Centre: 3x frame, matchup band
under it (reserved). Right column: place, HUD facts, badge pips, objective,
progress ladder. Footer: team | counters | pace | narration.

FORM: Cockpit, #3 of 7 ranked; dealt by seed 34104124 (indices 7,2,3), user
locked #3.

RISK: Density on an 11px base; columns must not elide the objective text.
