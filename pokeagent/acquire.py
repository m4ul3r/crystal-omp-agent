"""How do I actually get this Pokemon?

The dex objective needs a real answer per species, not "it exists". Roughly a
third of the Hoenn dex has no encounter data at all -- Sceptile, Blaziken,
Gardevoir, Ludicolo -- because you do not find them, you make them. Another
handful are one-off fights the game hides behind a puzzle.

Three sources, in order of authority:

* **The ROM's own evolution table** (``gEvolutionTable``, five
  ``struct Evolution`` per species: method, param, target). This is where the
  stones live -- EVO_ITEM with the item id in ``param`` -- along with the
  level thresholds, the trade requirements, the friendship ones, and Wurmple's
  two personality-value branches. Reading it means the answer cannot drift
  from the cartridge.
* **The catch database** (regional-dex-buddy) for wild encounters, gifts and
  static fights, with area names.
* **A short table below** for the static legendaries, because "REGIROCK is at
  Desert Ruins" is in the dataset but "you cannot open the door without
  Relicanth and Wailord and a Braille puzzle" is a fact about the GAME that no
  encounter table records.

The output is deliberately a chain, not a sentence: SCEPTILE is
"evolve GROVYLE at 36", and GROVYLE is "evolve TREECKO at 16", and TREECKO is
"gift at Route 101". A planner wants the step, not the prose.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

#: Five evolutions per species (include/pokemon.h:387-390). The BYTE stride is
#: derived from the symbol's own size, never transcribed: the header says
#: `struct Evolution` is three u16s = 6 bytes, but gEvolutionTable is 16480
#: bytes over 412 species = 40 each = 8 per entry, because the array is padded.
#: Transcribing the 6 read every field from the wrong offset and produced
#: "Shedinja: evolve REGIROCK". This project has been bitten by exactly this
#: before -- struct BattleMove's literal 9-byte header against a real 12-byte
#: stride gave every move 0 power (journal, port-01).
EVO_PER_SPECIES = 5

EVO_NONE = 0
EVO_FRIENDSHIP = 1
EVO_FRIENDSHIP_DAY = 2
EVO_FRIENDSHIP_NIGHT = 3
EVO_LEVEL = 4
EVO_TRADE = 5
EVO_TRADE_ITEM = 6
EVO_ITEM = 7
EVO_LEVEL_ATK_GT_DEF = 8
EVO_LEVEL_ATK_EQ_DEF = 9
EVO_LEVEL_ATK_LT_DEF = 10
EVO_LEVEL_SILCOON = 11
EVO_LEVEL_CASCOON = 12
EVO_LEVEL_NINJASK = 13
EVO_LEVEL_SHEDINJA = 14
EVO_BEAUTY = 15

#: Which methods a solo run can actually perform. Trades need a second
#: cartridge and a second human, so they are reported and excluded rather than
#: quietly planned for.
SOLO_IMPOSSIBLE = {EVO_TRADE, EVO_TRADE_ITEM}

#: What the encounter tables cannot tell you: the conditions on the one-off
#: fights. Kept short and cited, not a walkthrough.
STATIC_NOTES = {
    "Regirock": "Desert Ruins. Needs the Sealed Chamber opened first "
                "(Relicanth and Wailord in the party, Braille puzzle, DIVE).",
    "Regice": "Island Cave, same Sealed Chamber prerequisite.",
    "Registeel": "Ancient Tomb, same Sealed Chamber prerequisite.",
    "Rayquaza": "Sky Pillar summit. Needs MACH BIKE for the cracked floors.",
    "Kyogre": "Cave of Origin / Marine Cave, after the story climax.",
    "Latias": "Roams Hoenn after the Elite Four; needs a way to trap a roamer.",
    "Latios": "Roams Hoenn after the Elite Four; needs a way to trap a roamer.",
    "Jirachi": "Event distribution only. Not obtainable in a normal cartridge.",
    "Deoxys": "Event distribution only. Not obtainable in a normal cartridge.",
}


@dataclass
class Step:
    """One actionable way to obtain a species."""

    kind: str                 # wild | gift | static | evolve | breed | trade
    detail: str
    where: str = ""
    from_species: str = ""
    item: str = ""
    level: int = 0
    solo: bool = True         # can one player, one cartridge, do this?

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v not in ("", 0)}


@dataclass
class Acquisition:
    species: str
    steps: list = field(default_factory=list)
    note: str = ""

    @property
    def solo(self) -> bool:
        return any(s.solo for s in self.steps)

    def best(self):
        """The step a solo planner should take: a wild catch beats making one."""
        order = {"wild": 0, "gift": 1, "static": 2, "evolve": 3, "breed": 4,
                 "trade": 9}
        usable = [s for s in self.steps if s.solo] or self.steps
        return min(usable, key=lambda s: order.get(s.kind, 5)) if usable else None

    def summary(self) -> str:
        step = self.best()
        if step is None:
            return f"{self.species}: no known method"
        bits = [step.detail]
        if self.note:
            bits.append(self.note)
        return f"{self.species}: " + " -- ".join(bits)

    def as_dict(self) -> dict:
        return {
            "species": self.species,
            "solo": self.solo,
            "note": self.note,
            "best": self.best().as_dict() if self.best() else None,
            "steps": [s.as_dict() for s in self.steps],
        }


class Acquisitions:
    """Answers "how do I get this" for every species in the dex."""

    def __init__(self, emu, names, dex_target=None):
        self.emu = emu
        self.names = names
        self.dex = dex_target
        self._pre = None

    # ---- the ROM's evolution table --------------------------------------

    @property
    def _strides(self) -> tuple[int, int]:
        """(bytes per species, bytes per entry), derived from the symbol."""
        cached = getattr(self, "_stride_cache", None)
        if cached:
            return cached
        count = getattr(self.names, "species_count", 412) or 412
        try:
            total = self.emu.sym.size("gEvolutionTable")
        except Exception:  # noqa: BLE001
            total = 0
        per_species = (total // count) if total else 40
        entry = per_species // EVO_PER_SPECIES or 8
        self._stride_cache = (per_species, entry)
        return self._stride_cache

    def evolutions_of(self, species_id: int) -> list[tuple[int, int, int]]:
        """``(method, param, target)`` for one species, empty entries dropped."""
        try:
            base = self.emu.resolve("gEvolutionTable")
        except Exception:  # noqa: BLE001
            return []
        stride, entry_size = self._strides
        try:
            raw = self.emu.read(base + species_id * stride, stride)
        except Exception:  # noqa: BLE001
            return []
        out = []
        for i in range(EVO_PER_SPECIES):
            off = i * entry_size
            method = int.from_bytes(raw[off:off + 2], "little")
            if method == EVO_NONE:
                continue
            out.append((
                method,
                int.from_bytes(raw[off + 2:off + 4], "little"),
                int.from_bytes(raw[off + 4:off + 6], "little"),
            ))
        return out

    def pre_evolutions(self) -> dict[int, list[tuple[int, int, int]]]:
        """target -> [(method, param, from_species)], built once.

        The table is stored forwards (what does X become) and the dex question
        is backwards (what becomes X), so it is inverted here.
        """
        if self._pre is not None:
            return self._pre
        out: dict[int, list] = {}
        count = getattr(self.names, "species_count", 412)
        for sid in range(1, count):
            for method, param, target in self.evolutions_of(sid):
                if target:
                    out.setdefault(target, []).append((method, param, sid))
        self._pre = out
        return out

    def _evolution_step(self, method, param, from_id) -> Step:
        who = self.names.species(from_id)
        if method == EVO_ITEM:
            item = self.names.item(param)
            return Step("evolve", f"use {item} on {who}", from_species=who,
                        item=item)
        if method in (EVO_LEVEL, EVO_LEVEL_ATK_GT_DEF, EVO_LEVEL_ATK_EQ_DEF,
                      EVO_LEVEL_ATK_LT_DEF, EVO_LEVEL_NINJASK):
            extra = {
                EVO_LEVEL_ATK_GT_DEF: " with Attack > Defense",
                EVO_LEVEL_ATK_EQ_DEF: " with Attack = Defense",
                EVO_LEVEL_ATK_LT_DEF: " with Attack < Defense",
            }.get(method, "")
            return Step("evolve", f"raise {who} to level {param}{extra}",
                        from_species=who, level=param)
        if method == EVO_LEVEL_SHEDINJA:
            return Step(
                "evolve",
                f"evolve {who} at level {param} with a spare ball and party "
                f"slot -- Shedinja is left behind, it is not the evolution",
                from_species=who, level=param)
        if method in (EVO_LEVEL_SILCOON, EVO_LEVEL_CASCOON):
            branch = "Silcoon" if method == EVO_LEVEL_SILCOON else "Cascoon"
            return Step(
                "evolve",
                f"raise {who} to level {param} -- the {branch} branch is a "
                f"hidden personality value, so both must be caught separately",
                from_species=who, level=param)
        if method in (EVO_FRIENDSHIP, EVO_FRIENDSHIP_DAY, EVO_FRIENDSHIP_NIGHT):
            when = {EVO_FRIENDSHIP_DAY: " during the day",
                    EVO_FRIENDSHIP_NIGHT: " at night"}.get(method, "")
            return Step("evolve",
                        f"raise {who}'s friendship to 220 and level up{when}",
                        from_species=who)
        if method == EVO_BEAUTY:
            return Step("evolve",
                        f"raise {who}'s beauty to {param} with Pokeblocks, "
                        f"then level up", from_species=who)
        if method == EVO_TRADE:
            return Step("trade", f"trade {who} to another cartridge",
                        from_species=who, solo=False)
        if method == EVO_TRADE_ITEM:
            item = self.names.item(param)
            return Step("trade",
                        f"trade {who} holding {item}", from_species=who,
                        item=item, solo=False)
        return Step("evolve", f"evolve {who} (method {method})",
                    from_species=who)

    # ---- the whole answer ------------------------------------------------

    def for_entry(self, entry) -> Acquisition:
        """Every way to obtain one dex entry."""
        steps: list[Step] = []

        for enc in getattr(entry, "encounters", ()) or ():
            method = (enc.method or "").lower()
            kind = ("gift" if method == "gift"
                    else "static" if method in ("static", "event")
                    else "trade" if "trade" in method
                    else "wild")
            where = enc.area or ""
            level = f" (L{enc.min_level}-{enc.max_level})" if enc.min_level else ""
            steps.append(Step(
                kind,
                f"{enc.method_label or method} at {where}{level}".strip(),
                where=where, level=enc.min_level or 0,
                solo=kind != "trade",
            ))

        species_id = getattr(entry, "species", 0)
        for method, param, from_id in self.pre_evolutions().get(species_id, []):
            steps.append(self._evolution_step(method, param, from_id))

        # Babies hatch; they do not evolve into existence and mostly are not
        # catchable. The inverted evolution table finds nothing for them
        # because the arrow points the other way, so the answer is to breed
        # the ADULT -- which is also the only reason a living dex needs the
        # adult at all.
        if not steps:
            grown = self._baby_parent(species_id)
            if grown:
                steps.append(Step(
                    "breed",
                    f"breed {grown} at the Day Care -- {entry.name} hatches "
                    f"from the egg and cannot be caught",
                    from_species=grown,
                ))

        note = STATIC_NOTES.get(entry.name, "")
        if getattr(entry, "needs_trade_partner", False) and not any(
            st.kind == "trade" for st in steps
        ):
            # The dataset flags "hard to obtain" species this way too, so it is
            # only repeated when an actual trade step exists.
            note = note or ""
        return Acquisition(entry.name, steps, note)

    def _baby_parent(self, species_id: int) -> str:
        """The adult whose egg is this species, from the forward table."""
        for target_id, rows in self.pre_evolutions().items():
            del target_id
        for adult in range(1, getattr(self.names, "species_count", 412)):
            for method, param, target in self.evolutions_of(adult):
                del method, param
                if target == species_id:
                    return self.names.species(adult)
        # A baby is the START of its chain: find who it becomes, then who
        # breeds into it is that same line's adult.
        becomes = self.evolutions_of(species_id)
        if becomes:
            return self.names.species(becomes[0][2])
        return ""

    def plan(self, entries=None) -> list[Acquisition]:
        entries = entries if entries is not None else (
            self.dex.achievable if self.dex else ()
        )
        return [self.for_entry(e) for e in entries]

    def unexplained(self, entries=None) -> list[str]:
        """Species we still cannot say how to get. The honest gap list."""
        return [a.species for a in self.plan(entries) if not a.steps]
