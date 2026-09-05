"""What the run is trying to do right now, and the autosave rail.

Two of the user's requirements meet here:

* the objective is **badges until the Elite Four is beaten, then the
  Pokédex** -- "after the elite 4, the objective becomes to complete the
  pokedex to the fullest extent possible, excluding trade evolutions and
  version exclusives";
* **save often.**

Both are policy, and policy belongs somewhere a session can read and audit
rather than scattered through scripts. The objective is derived from live
game state on every call -- never cached, never advanced by hand -- so it
cannot drift out of step with what has actually been achieved. That is the
same discipline the nav layer applies to blocked cells.
"""

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("pokeagent.objective")

#: Gen-3 Hoenn: the flag that means the Hall of Fame has been entered.
CHAMPION_FLAGS = ("FLAG_SYS_GAME_CLEAR",)
#: Gen 2's equivalent, for when a Johto run is driven.
CHAMPION_FLAGS_GEN2 = ("EVENT_BEAT_CHAMPION_LANCE",)

BADGES = "badges"
CHAMPION = "champion"
DEX = "dex"


@dataclass(slots=True)
class Objective:
    name: str
    detail: str
    percent: float
    #: The next concrete thing to do, when the engine can name one.
    next_step: str | None = None

    def as_dict(self):
        return {
            "name": self.name,
            "detail": self.detail,
            "percent": round(self.percent, 1),
            "next_step": self.next_step,
        }


class ObjectiveEngine:
    """Derives the current objective from live state."""

    def __init__(self, driver):
        self.d = driver

    def champion(self) -> bool:
        """Has the Elite Four been beaten?

        Read from the game's own flag, not from a badge count -- eight badges
        means the League is open, not that it has been cleared.
        """
        flags = CHAMPION_FLAGS if self.d.spec.generation >= 3 else CHAMPION_FLAGS_GEN2
        for name in flags:
            try:
                if self.d.state.flag(name):
                    return True
            except (KeyError, AttributeError):
                continue
        return False

    def current(self) -> Objective:
        badges = len(self.d.state.badges())
        if not self.champion():
            pct = 100.0 * badges / 8
            return Objective(
                name=BADGES,
                detail=f"{badges}/8 badges, then the Elite Four",
                percent=pct,
                next_step=self._next_gym_hint(badges),
            )

        # Post-game: the dex, to the fullest extent a solo run allows.
        try:
            from pokeagent import dex as dexmod

            progress = dexmod.progress(self.d.state, self.d.spec)
        except Exception as exc:  # noqa: BLE001 - the dex data is optional
            log.debug("dex progress unavailable: %s", exc)
            return Objective(
                name=DEX,
                detail="Pokedex completion (dex data unavailable)",
                percent=0.0,
            )
        return Objective(
            name=DEX,
            detail=(
                f"{progress['caught']}/{progress['achievable']} achievable "
                f"(trade evolutions and {self.d.spec.paired_with or 'paired'}-"
                f"exclusives are out of reach solo)"
            ),
            percent=progress["percent"],
            next_step=progress.get("next_step"),
        )

    def _next_gym_hint(self, badges):
        # Deliberately not a hardcoded gym order: the run's own route planner
        # owns that. Saying "the next badge" is honest; naming a city we have
        # not verified reachable would not be.
        return f"badge {badges + 1}" if badges < 8 else "the Elite Four"


class Autosave:
    """The save-often rail.

    Three triggers, because "often" without a definition becomes "never" the
    moment a session gets busy:

    * **periodic** -- every `every_frames` of emulated time;
    * **milestone** -- a badge, a level-up, a catch, a map change, a battle
      ending: the transitions worth being able to return to;
    * **explicit** -- `checkpoint(kind)` from a caller.

    A milestone save gets its own filename so it is never overwritten, which
    is the rule the predecessor project learned by overwriting one. Periodic
    saves rotate through a small ring so they cannot fill a disk.
    """

    def __init__(self, driver, session="run", saves_dir=None, every_frames=30000,
                 ring=6):
        from pokeagent import paths

        self.d = driver
        self.session = session
        self.dir = Path(saves_dir or paths.SAVES_DIR)
        self.every = every_frames
        self.ring = ring
        self._last_frame = driver.emu.frame
        self._ring_next = 0
        self._seen = None
        self.saved = 0
        self.milestones = []

    # ---- triggers -----------------------------------------------------

    def _signature(self):
        st = self.d.state
        party = st.party()
        return {
            "map": st.location().map_name,
            "badges": len(st.badges()),
            "party": len(party),
            "levels": tuple(m.level or 0 for m in party),
            "battle": self.d.in_battle(),
        }

    def milestones_since(self):
        """Which meaningful transitions happened since the last check."""
        now = self._signature()
        if self._seen is None:
            self._seen = now
            return []
        was, out = self._seen, []
        if now["badges"] > was["badges"]:
            out.append("badge")
        if now["party"] > was["party"]:
            out.append("caught")
        if now["levels"] != was["levels"] and sum(now["levels"]) > sum(was["levels"]):
            out.append("level-up")
        if now["map"] != was["map"]:
            out.append("map")
        if was["battle"] and not now["battle"]:
            out.append("battle-end")
        self._seen = now
        return out

    def tick(self):
        """Call this after any action. Saves when a trigger fires.

        Returns the list of checkpoint paths written, so a caller can report
        them rather than guessing whether anything happened.
        """
        written = []
        for kind in self.milestones_since():
            # A badge is worth its own permanent file; the noisier
            # transitions ride the periodic ring so they cannot flood.
            if kind in ("badge", "caught"):
                written.append(self.checkpoint(kind))
            else:
                written.extend(self._periodic(force=(kind == "battle-end")))
        written.extend(self._periodic())
        return [w for w in written if w]

    def _periodic(self, force=False):
        frame = self.d.emu.frame
        if not force and frame - self._last_frame < self.every:
            return []
        self._last_frame = frame
        path = self.dir / f"{self.session}-auto{self._ring_next}.state"
        self._ring_next = (self._ring_next + 1) % self.ring
        return [self._write(path, "periodic")]

    def checkpoint(self, kind):
        """A permanent, uniquely-named checkpoint."""
        n = sum(1 for m in self.milestones if m[0] == kind) + 1
        path = self.dir / f"{self.session}-{kind}{n}.state"
        self.milestones.append((kind, str(path)))
        return self._write(path, kind)

    def _write(self, path, kind):
        try:
            self.d.emu.save_state(path)
        except Exception as exc:  # noqa: BLE001 - a failed save must not end a run
            log.warning("autosave (%s) failed: %s", kind, exc)
            return None
        self.saved += 1
        self.d.state_path = self.d.state_path or path
        log.info("saved %s (%s) @%d", path.name, kind, self.d.emu.frame)
        return str(path)

    def stats(self):
        return {
            "saves": self.saved,
            "milestones": len(self.milestones),
            "every_frames": self.every,
        }
