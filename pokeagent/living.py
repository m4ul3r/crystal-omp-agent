"""The LIVING dex: one of every species held at once, not merely registered.

The distinction changes the whole plan. A completed Pokédex only needs each
species to have been *caught once* -- evolving your Torchic into Blaziken
leaves the dex complete. A **living** dex needs one of every species
**simultaneously in the party or the PC**, so an evolution line costs one
individual per stage:

    TORCHIC -> COMBUSKEN -> BLAZIKEN   needs THREE individuals:
      one left as Torchic, one raised to Combusken, one raised to Blaziken

which is why the extra copies have to come from somewhere: breeding for
anything with an egg group, repeat wild catches otherwise.

Facts read rather than assumed:

* **Breedability** is ``eggGroup1 == EGG_GROUP_UNDISCOVERED``
  (include/pokemon.h:8-23, field at :301). Legendaries sit in Undiscovered,
  so a legendary yields only what can be caught.
* **Storage** is ``boxes[14][30]`` plus a party of six
  (include/pokemon.h:323-329) = 426 slots, so a 188-species living dex fits
  with room. The plan reports the number rather than trusting it.
* **Evolution lines** come from the ROM's ``gEvolutionTable`` via
  :mod:`pokeagent.dex`, so branching lines are data, not special cases.

Two real cases handled explicitly rather than silently miscounted:

* **Nincada** evolves to Ninjask *and* leaves a Shedinja behind (given a
  spare ball and party slot), so that line needs one fewer individual than
  it has stages.
* **Branching lines** (Wurmple -> Silcoon/Beautifly or Cascoon/Dustox) split
  on a hidden personality value, so both branches must be obtained
  separately and the branch cannot be chosen.
"""

import logging
from dataclasses import dataclass
from functools import cached_property

log = logging.getLogger("pokeagent.living")

#: include/pokemon.h:23 -- the group meaning "cannot breed".
EGG_GROUP_UNDISCOVERED = 15
#: include/pokemon.h:323-329
BOX_COUNT, BOX_CAPACITY, PARTY_SIZE = 14, 30, 6
STORAGE_SLOTS = BOX_COUNT * BOX_CAPACITY + PARTY_SIZE

#: struct BaseStats offsets for the two egg groups (include/pokemon.h:301-302).
EGG_GROUP1_OFFSET, EGG_GROUP2_OFFSET = 0x14, 0x15


@dataclass(slots=True)
class LineNeed:
    """What one evolution line costs to hold in full."""

    root: int
    root_name: str
    stages: tuple
    stage_names: tuple
    individuals_needed: int
    breedable: bool
    breeding_parent: int | None = None
    breeding_parent_name: str | None = None
    note: str = ""

    def as_dict(self):
        return {
            "root": self.root_name,
            "stages": list(self.stage_names),
            "individuals": self.individuals_needed,
            "breedable": self.breedable,
            "breed_from": self.breeding_parent_name,
            "note": self.note,
        }


@dataclass(slots=True)
class LivingProgress:
    held: int
    target: int
    percent: float
    missing: tuple
    lines_complete: int
    lines_total: int
    storage_used: int
    storage_slots: int = STORAGE_SLOTS
    breeding_needed: tuple = ()

    def as_dict(self):
        return {
            "held": self.held,
            "target": self.target,
            "percent": round(self.percent, 1),
            "remaining": self.target - self.held,
            "lines_complete": self.lines_complete,
            "lines_total": self.lines_total,
            "storage_used": self.storage_used,
            "storage_slots": self.storage_slots,
            "breeding_needed": len(self.breeding_needed),
        }


class LivingDex:
    """The living-dex target and its progress, layered over a DexTarget."""

    def __init__(self, target):
        self.t = target                      # pokeagent.dex.DexTarget
        self.names = target.names
        self.last_reason = None

    # ---- species facts ---------------------------------------------------

    def egg_groups(self, species) -> tuple[int, int]:
        raw = self.names.emu.read(
            ("gBaseStats", species * self.names.base_stats_stride),
            self.names.base_stats_stride,
        )
        return raw[EGG_GROUP1_OFFSET], raw[EGG_GROUP2_OFFSET]

    def breedable(self, species) -> bool:
        """False for the Undiscovered group: legendaries, Unown and BABIES.

        Baby Pokemon sit in Undiscovered because they cannot breed
        themselves -- you get a Pichu by breeding a Pikachu, not a Pichu. So
        "is this species breedable" is not the same question as "can this
        LINE produce more individuals"; see :meth:`breeding_parent`.
        """
        return self.egg_groups(species)[0] != EGG_GROUP_UNDISCOVERED

    def breeding_parent(self, root, stages) -> int | None:
        """Which member of a line can actually lay the eggs.

        For a normal line that is the root. For a baby line (Pichu, Igglybuff,
        Azurill, Wynaut) the root cannot breed and the EVOLVED form can, and
        breeding it produces the baby -- so the parent is the first stage in
        the line whose egg group is not Undiscovered. None when nothing in the
        line can breed at all, which is the legendary case.
        """
        for species in stages:
            if self.breedable(species):
                return species
        return None

    def _name(self, species):
        try:
            return self.names.species(species).strip().upper()
        except Exception:  # noqa: BLE001
            return f"SPECIES_{species}"

    # ---- evolution lines --------------------------------------------------

    @cached_property
    def lines(self) -> tuple:
        """One LineNeed per achievable evolution line, roots outward."""
        achievable = {e.species for e in self.t.achievable}
        seen, out = set(), []
        for entry in self.t.achievable:
            roots = self.t.evolutions.roots(entry.species) or (entry.species,)
            root = roots[0]
            if root in seen:
                continue
            seen.add(root)
            stages = tuple(
                s for s in self.t.evolutions.chain(root) if s in achievable
            )
            if not stages:
                continue
            names = tuple(self._name(s) for s in stages)
            need, note = len(stages), ""
            if {"NINJASK", "SHEDINJA"} <= set(names):
                need -= 1
                note = ("Nincada's evolution leaves a Shedinja behind, so this "
                        "line needs one fewer individual than it has stages "
                        "(requires a spare ball and party slot at the time)")
            if len(self.t.evolutions.evolutions(root)) > 1:
                note = (note + "; " if note else "") + (
                    "branches on a hidden personality value, so each branch "
                    "must be obtained separately"
                )
            parent = self.breeding_parent(root, stages)
            if parent is not None and parent != root:
                note = (note + "; " if note else "") + (
                    f"{self._name(root)} cannot breed itself (baby); breed "
                    f"{self._name(parent)} to produce more"
                )
            out.append(LineNeed(
                root=root,
                root_name=self._name(root),
                stages=stages,
                stage_names=names,
                individuals_needed=need,
                breedable=parent is not None,
                breeding_parent=parent,
                breeding_parent_name=self._name(parent) if parent else None,
                note=note,
            ))
        return tuple(out)

    # ---- progress ---------------------------------------------------------

    def held_species(self, state) -> frozenset:
        """Every species held right now, party plus all 420 box slots.

        The whole point of a living dex: a species caught and then evolved
        away no longer counts.
        """
        return self.t.owned_species(state)

    def progress(self, state) -> LivingProgress:
        held = self.held_species(state)
        wanted = {e.species for e in self.t.achievable}
        have = wanted & held
        complete, breeding = 0, []
        for line in self.lines:
            missing = [s for s in line.stages if s not in held]
            if not missing:
                complete += 1
                continue
            # A new individual is needed whenever some stage of the line is
            # already held: evolving that one would empty its slot.
            if any(s in held for s in line.stages):
                breeding.append(line.root_name)
        return LivingProgress(
            held=len(have),
            target=len(wanted),
            percent=100.0 * len(have) / max(1, len(wanted)),
            missing=tuple(sorted(self._name(s) for s in wanted - held)),
            lines_complete=complete,
            lines_total=len(self.lines),
            storage_used=len(held),
            breeding_needed=tuple(sorted(set(breeding))),
        )

    def plan(self, state, limit=None):
        """What to do next, with the living-dex constraint applied.

        A stage whose pre-evolution is already held becomes "breed another
        root, then raise it", because evolving the one on the shelf would
        lose the stage already held. That is the difference between this and
        the plain dex plan.
        """
        held = self.held_species(state)
        rows = []
        for line in self.lines:
            missing = [s for s in line.stages if s not in held]
            if not missing:
                continue
            line_has_something = any(s in held for s in line.stages)
            for species in missing:
                if line_has_something:
                    # Breed the member of the line that CAN lay eggs, which
                    # is not the root for a baby line.
                    how = (
                        f"breed another {line.breeding_parent_name}"
                        if line.breedable
                        else f"catch another {line.root_name}"
                    )
                    rows.append({
                        "species": self._name(species),
                        "route": "breed" if line.breedable else "wild",
                        "detail": (
                            f"{how} and raise it -- evolving the "
                            f"{line.root_name} you hold would empty that slot"
                        ),
                        "line": line.root_name,
                        "blocked": None if line.breedable else (
                            f"nothing in the {line.root_name} line can breed "
                            f"(Undiscovered egg group); needs a repeat catch"
                        ),
                    })
                else:
                    rows.append({
                        "species": self._name(species),
                        "route": "acquire",
                        "detail": f"obtain {self._name(species)} (see dex plan)",
                        "line": line.root_name,
                        "blocked": None,
                    })
        rows.sort(key=lambda r: (r["blocked"] is not None, r["species"]))
        return rows[:limit] if limit else rows

    def summary(self, state) -> str:
        p = self.progress(state)
        return (
            f"living dex {p.held}/{p.target} ({p.percent:.1f}%), "
            f"{p.lines_complete}/{p.lines_total} lines complete, "
            f"{p.storage_used}/{p.storage_slots} slots used"
        )
