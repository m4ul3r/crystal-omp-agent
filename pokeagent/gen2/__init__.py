"""Gen 1/2 data layer, vendored from the Crystal harness.

These modules are the crystal-omp-agent originals (`crystalagent/symfile.py`,
`charmap.py`, `asmconst.py`, `names.py`, `emu.py`, `state.py`, `nav.py`,
`menus.py`), brought in rather than rewritten. The user's requirement was not
to blow away the Crystal controls, and the most faithful reading of that is to
carry the working code across instead of reimplementing it from memory.

Two adjustments were needed and both are mechanical:

* `crystalagent.paths` derived every location from the checkout's PARENT
  directory, which is why that project broke the moment it was cloned on its
  own. Here the decompilation root is passed in explicitly.
* Imports are relative to this subpackage.

What this gives you today is the Gen-2 **data layer**: the rgbds symbol table,
the charmap, the asm constant parsers, the ROM name tables, banked memory
reads, structured party/position state, and `.blk` map grids with BFS. That is
enough to read and navigate a Crystal game.

What it does NOT give you is the Gen-2 battle and menu stack driven end to end.
Those modules are present but unexercised here, so the Gen-2 adapter advertises
capabilities honestly and the registry's `status` reflects reality.
"""
