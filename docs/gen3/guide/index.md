# Route guide

Generated for **Pokémon Sapphire** (gen 3, Hoenn) by `scripts/build_guide.py`.

This is route context for whichever agent is playing. It is generated from the pret decompilation and the vendored regional-dex-buddy dataset rather than copied from a wiki, because an agent needs exact coordinates, flag names and level ranges -- not prose. Bulbapedia appears here as links, which is the right use of it.

| File | What it answers |
|------|-----------------|
| `gyms.md` | Badge order, leaders, towns, types |
| `trainers.json` | Every trainer's party: species, levels, moves |
| `gates.md` / `.json` | Which FLAG/VAR opens which map |
| `encounters.md` / `.json` | Per-map wild species, levels, rates |
| `items.json` | Key items and HMs with coordinates |

Live equivalents, preferred when a driver is running, because they are evaluated against the actual save:

- `Driver.missables()` -- uncollected key items and HMs
- `Driver.field_moves()` -- which party member knows each HM
- `pokeagent.dex.DexTarget.plan()` -- what to catch next, and where
- `pokeagent.living.LivingDex.plan()` -- what to breed next
- `pokeagent.stages.Ladder.current()` -- the active goal
