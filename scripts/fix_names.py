#!/usr/bin/env python
"""Clear junk nicknames (the legacy "A"s) from the party and the PC boxes.

WHY THIS IS A WRITE AND NOT A WALK TO THE NAME RATER
----------------------------------------------------
The Name Rater renames ONE party member per visit, so 59 boxed mons is 59
withdraw / fly / rename / deposit cycles -- hours of emulator time to undo a
harness bug that took one line to fix. A nickname is also the one field we can
legitimately poke: a ``BoxPokemon``'s secure block starts at ``0x20`` and the
checksum covers only that block (``decrypt_secure`` unpacks from ``0x20``,
pokeagent/pokemon.py:65-70), while the nickname lives at ``0x08`` in the
PLAINTEXT header. So rewriting a name cannot invalidate the checksum, cannot
touch species / EXP / IVs / moves, and is verified here by reading the mon
back through the ordinary parser rather than trusting the write.

WHERE THE "A"s CAME FROM
------------------------
``naming.py accept()`` used to send ``START`` then ``A`` to take the
pre-filled species name off the keyboard. START is swallowed during menu setup
(AGENTS.md gotcha 2), so the ``A`` typed the letter instead. Every catch made
before that fix is branded. The count is now frozen -- 59 across every
milestone from dex 105 to dex 132, with 17 catches made after the fix adding
none -- which is the evidence the bug is actually dead and this is cleanup, not
a workaround.

They were invisible because ``DexTarget.boxed()`` never filled the nickname
(``parse_mon`` leaves it to the caller), so every audit of the boxes returned
"no bad names" no matter what was in them.

    python scripts/fix_names.py --state saves/<fork>.state [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent import dex as dexmod                              # noqa: E402
from pokeagent import pokemon                                     # noqa: E402
from pokeagent.trek import Driver                                 # noqa: E402

log = logging.getLogger("fix_names")

# A name is junk if it is one of these. Anything a human or a policy chose
# deliberately -- SEA BIRD, EMBER, ROCKY -- is left exactly alone.
JUNK = {"A", "AA", "AAA", "AAAA", "AAAAAAAAAA", ""}

NICK_OFF = 0x08          # BoxPokemon.nickname, plaintext header


def junk(name: str | None) -> bool:
    return (name or "").strip().upper() in JUNK


def encode_name(d, text: str) -> bytes:
    """Charmap-encode a nickname to exactly NICKNAME_LEN bytes.

    ``nickname`` is a FIXED-WIDTH 10-byte field, not a C string: a name that
    is exactly 10 characters fills it with no room for the ``0xFF``
    terminator, which is why WIGGLYTUFF (10 chars -> 11 bytes with the
    terminator) has to be written unterminated. Shorter names are padded with
    ``0xFF`` so no tail of the previous name survives.
    """
    raw = d.emu.charmap.encode(text[:pokemon.NICKNAME_LEN])
    if len(raw) > pokemon.NICKNAME_LEN:
        raw = raw[:pokemon.NICKNAME_LEN]
    raw = raw + b"\xff" * (pokemon.NICKNAME_LEN - len(raw))
    if len(raw) != pokemon.NICKNAME_LEN:
        raise ValueError(
            f"encoded {text!r} to {len(raw)} bytes, need {pokemon.NICKNAME_LEN}"
        )
    return raw


def box_base(d, target) -> int:
    return d.emu.resolve("gPokemonStorage") + target._storage["boxes"]


def party_nick_addr(d, index: int) -> int:
    return (d.emu.resolve("gPlayerParty") + index * d.state._mon_size
            + d.state.mon["box"] + NICK_OFF)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out", default=None,
                    help="defaults to --state (in place)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    target = dexmod.DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)

    plan: list[tuple[str, int, str, str]] = []
    for i, mon in enumerate(d.state.party()):
        if mon.is_egg:
            continue
        if junk(mon.nickname):
            plan.append(("party", i, mon.nickname or "",
                         d.names.species(mon.species)))
    for slot, mon in target.boxed():
        if junk(mon.nickname):
            plan.append(("box", slot, mon.nickname or "",
                         d.names.species(mon.species)))

    log.info("%d junk nickname(s) to clear", len(plan))
    for where, idx, was, now in plan[:8]:
        log.info("  %-5s %-4d %-11r -> %s", where, idx, was, now)
    if len(plan) > 8:
        log.info("  ... and %d more", len(plan) - 8)
    if not plan:
        return 0
    if a.dry_run:
        log.info("dry run; nothing written")
        return 0

    base = box_base(d, target)
    for where, idx, _was, now in plan:
        addr = (party_nick_addr(d, idx) if where == "party"
                else base + idx * pokemon.BOX_SIZE + NICK_OFF)
        d.emu.write(addr, encode_name(d, now))

    # VERIFY BY RE-READING, not by trusting the write: species and checksum
    # have to survive, because the whole claim of this script is that a
    # nickname sits outside the secure block.
    after = dexmod.DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    left = [(s, m.nickname) for s, m in after.boxed() if junk(m.nickname)]
    left += [("party%d" % i, m.nickname) for i, m in enumerate(d.state.party())
             if not m.is_egg and junk(m.nickname)]
    boxed_now = after.boxed()
    if len(boxed_now) != len(target.boxed()):
        log.error("FAIL: box population changed %d -> %d",
                  len(target.boxed()), len(boxed_now))
        return 1
    if left:
        log.error("FAIL: %d junk name(s) survived: %s", len(left), left[:5])
        return 1

    dex_before = len(target.dex_flags(d.state)[0])
    dex_after = len(after.dex_flags(d.state)[0])
    if dex_before != dex_after:
        log.error("FAIL: dex moved %d -> %d", dex_before, dex_after)
        return 1

    sample = [(s, d.names.species(m.species), m.nickname)
              for s, m in boxed_now[:5]]
    log.info("verified: 0 junk names left, %d boxed mons intact, dex %d",
             len(boxed_now), dex_after)
    log.info("sample: %s", sample)

    out = a.out or a.state
    d.save(out)
    log.info("saved %s", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
