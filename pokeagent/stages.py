"""The goal ladder: what the run does after it runs out of game.

The user's framing is an idle game -- something that keeps running in the
background indefinitely -- so the objective is a ladder, each stage strictly
harder than the last, each with real progress:

    1   complete the game            badges, then the Elite Four
    2   living dex                   one of every species held at once
    3a  five-IV dex                  every held species with 5 of 6 IVs at 31
    3b  century dex                  every held species at level 100
    3c  shiny dex                    every held species shiny

3a/3b/3c are siblings rather than a sequence: they are independent grinds over
the same collection, and a run left alone should spread effort across them
rather than starving two to finish one. All three are unbounded in practice,
which is the point.

Progress is always derived from live memory. Nothing here is a counter the
harness increments and could drift.

One honesty note on 3c: Gen-3 shininess is a function of the personality value
and the trainer/secret IDs, so an individual either is or is not shiny at the
moment it is generated -- nothing makes one shiny afterwards. The stage is
"obtain a shiny of each", and it only works as an idle goal because
:mod:`pokeagent.entropy` can break savestate determinism on demand; without
that, reloading and re-fighting the same encounter reproduces the same
non-shiny result forever.
"""

import logging
from dataclasses import dataclass

log = logging.getLogger("pokeagent.stages")

#: IVs are 5-bit, so 31 is perfect (see pokeagent.pokemon).
MAX_IV = 31
#: The user's bar for stage 3a: five of six perfect.
PERFECT_IV_COUNT = 5
MAX_LEVEL = 100

STAGE_GAME = "complete-game"
STAGE_LIVING = "living-dex"
STAGE_IVS = "five-iv-dex"
STAGE_LEVELS = "century-dex"
STAGE_SHINY = "shiny-dex"


@dataclass(slots=True)
class StageProgress:
    key: str
    rank: int
    name: str
    detail: str
    percent: float
    done: bool
    have: int = 0
    need: int = 0
    next_step: str | None = None

    def as_dict(self):
        return {
            "key": self.key, "rank": self.rank, "name": self.name,
            "detail": self.detail, "percent": round(self.percent, 1),
            "done": self.done, "have": self.have, "need": self.need,
            "next_step": self.next_step,
        }


class Ladder:
    """Every stage's progress, and which one the run should work on."""

    def __init__(self, driver, target=None, living=None):
        self.d = driver
        self._target = target
        self._living = living

    @property
    def target(self):
        if self._target is None:
            from pokeagent import dex

            self._target = dex.DexTarget(
                self.d.emu, self.d.names, self.d.consts, self.d.nav,
                spec=self.d.spec,
            )
        return self._target

    @property
    def living(self):
        if self._living is None:
            from pokeagent.living import LivingDex

            self._living = LivingDex(self.target)
        return self._living

    # ---- the collection ----------------------------------------------------

    def held_individuals(self):
        """Every individual held, party and boxes.

        Stages 3a-3c are about individuals, not species, so they need the
        decoded mons rather than a species set.
        """
        out = list(self.d.state.party())
        boxes = getattr(self.target, "box_mons", None)
        if callable(boxes):
            try:
                out.extend(boxes(self.d.state))
            except Exception as exc:  # noqa: BLE001 - boxes are optional
                log.debug("box read unavailable: %s", exc)
        return [m for m in out
                if m is not None and not getattr(m, "is_egg", False)]

    # ---- stages ------------------------------------------------------------

    def stage_game(self) -> StageProgress:
        from pokeagent.objective import ObjectiveEngine

        badges = len(self.d.state.badges())
        champion = ObjectiveEngine(self.d).champion()
        return StageProgress(
            key=STAGE_GAME, rank=1, name="Complete the game",
            detail=("Hall of Fame registered" if champion
                    else f"{badges}/8 badges, then the Elite Four"),
            percent=100.0 if champion else 100.0 * badges / 8,
            done=champion, have=badges, need=8,
            next_step=None if champion else f"badge {badges + 1}",
        )

    def stage_living(self) -> StageProgress:
        p = self.living.progress(self.d.state)
        plan = self.living.plan(self.d.state, limit=1)
        return StageProgress(
            key=STAGE_LIVING, rank=2, name="Living dex",
            detail=(f"{p.held}/{p.target} species held at once, "
                    f"{p.lines_complete}/{p.lines_total} lines complete"),
            percent=p.percent, done=p.held >= p.target,
            have=p.held, need=p.target,
            next_step=(plan[0]["detail"] if plan else None),
        )

    @staticmethod
    def _perfect_ivs(mon):
        return sum(1 for v in (getattr(mon, "ivs", None) or {}).values()
                   if v == MAX_IV)

    def stage_ivs(self) -> StageProgress:
        mons = self.held_individuals()
        target = self.living.progress(self.d.state).target
        good = {m.species for m in mons
                if self._perfect_ivs(m) >= PERFECT_IV_COUNT}
        best = max((self._perfect_ivs(m) for m in mons), default=0)
        return StageProgress(
            key=STAGE_IVS, rank=3, name=f"{PERFECT_IV_COUNT}-IV dex",
            detail=(f"{len(good)}/{target} species held with "
                    f"{PERFECT_IV_COUNT}+ perfect IVs (best held: {best}/6)"),
            percent=100.0 * len(good) / max(1, target),
            done=len(good) >= target, have=len(good), need=target,
            next_step="breed for IVs: keep the better parent, re-breed",
        )

    def stage_levels(self) -> StageProgress:
        mons = self.held_individuals()
        target = self.living.progress(self.d.state).target
        maxed = {m.species for m in mons if (m.level or 0) >= MAX_LEVEL}
        banked = sum(m.level or 0 for m in mons)
        possible = MAX_LEVEL * max(1, target)
        return StageProgress(
            key=STAGE_LEVELS, rank=3, name="Century dex",
            detail=(f"{len(maxed)}/{target} species at level {MAX_LEVEL}; "
                    f"{banked}/{possible} levels banked"),
            # Levels banked, not species maxed: 0/188 would read as no progress
            # through hundreds of hours of legitimate training.
            percent=100.0 * banked / possible,
            done=len(maxed) >= target, have=len(maxed), need=target,
            next_step="train the lowest-level held species",
        )

    def stage_shiny(self) -> StageProgress:
        mons = self.held_individuals()
        target = self.living.progress(self.d.state).target
        shinies = {m.species for m in mons if getattr(m, "shiny", False)}
        return StageProgress(
            key=STAGE_SHINY, rank=3, name="Shiny dex",
            detail=(f"{len(shinies)}/{target} species held shiny -- needs "
                    f"entropy injection, since a plain savestate retry "
                    f"reproduces the same non-shiny result"),
            percent=100.0 * len(shinies) / max(1, target),
            done=len(shinies) >= target, have=len(shinies), need=target,
            next_step="fork at an encounter, reseed the RNG, retry",
        )

    # ---- the ladder --------------------------------------------------------

    def all_stages(self) -> list:
        return [self.stage_game(), self.stage_living(), self.stage_ivs(),
                self.stage_levels(), self.stage_shiny()]

    def current(self) -> StageProgress:
        """The lowest-rank unfinished stage; among the rank-3 siblings, the
        least complete, so effort spreads instead of starving two of three."""
        stages = self.all_stages()
        unfinished = [s for s in stages if not s.done]
        if not unfinished:
            return stages[-1]
        lowest = min(s.rank for s in unfinished)
        return min((s for s in unfinished if s.rank == lowest),
                   key=lambda s: s.percent)

    def summary(self) -> str:
        c = self.current()
        return f"stage {c.rank} {c.name}: {c.detail} ({c.percent:.1f}%)"

    def as_dict(self) -> dict:
        """The ladder as the feed publishes it.

        Each row carries `current`, because which rung is being worked on is a
        property of the LADDER (least-complete sibling), not of the stage --
        a consumer cannot recompute it from a single row.
        """
        cur = self.current()
        rows = []
        for stage in self.all_stages():
            row = stage.as_dict()
            row["current"] = stage.key == cur.key
            rows.append(row)
        return {"current": cur.as_dict(), "stages": rows}
