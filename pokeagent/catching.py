"""Deciding what to catch, and playing the turn that catches it.

A run that never catches anything is not building a team. Measured before this
existed: 604 battles, 2,382 steps, one Pokemon in the party, nine uncovered
types. The harness had every piece -- `Team.recommend_catch` scores a candidate
against the party's gaps, `BattleSession.throw_ball` throws -- and nothing
joined them, so the loop KO'd every wild it met.

Two decisions live here, and they are different:

* **Is this one worth a ball?** Type coverage the party lacks, a species the
  dex has never recorded, and level parity -- all of which `Team` and `DexTarget`
  already answer. This module only decides WHETHER to ask them.
* **Is this the turn to throw?** Catch rate rises as HP falls and with status,
  so throwing at full HP wastes balls. But a wild that faints is gone, so the
  turn before a certain KO is the last safe moment to switch to a ball.

Neither is a model decision: both are arithmetic over state the game gives us.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from . import team as teammod

log = logging.getLogger(__name__)

#: Throw once the target is at or below this fraction of its HP. Gen 3's catch
#: formula scales with (3*max - 2*current)/(3*max), so a third of HP is roughly
#: 2.3x the full-HP rate -- most of the benefit, without the extra turns of
#: risk that chasing 1 HP costs.
THROW_BELOW = 0.34

#: Never keep whittling below this: the next hit is likely to kill the thing we
#: are trying to catch.
DANGER_HP = 0.12

#: Leave at least this many balls for the story (a mandatory catch tutorial, a
#: legendary) rather than spending the bag on route filler.
BALL_RESERVE = 3

#: Party slots to keep free so a catch is never refused for want of room.
MAX_PARTY = 6

#: Below this many party members, judge a candidate on COVERAGE alone and
#: ignore the level penalty.
#:
#: `Team.recommend_catch` charges -0.25 per level a catch owes the training
#: floor, which is right for a settled team and wrong for an empty one: with a
#: L24 lead on Route 102 every wild is L2-L3, so every candidate scored around
#: -5 and the run refused to catch anything at all -- 604 battles, one Pokemon,
#: nine uncovered types. Training fixes a level gap and nothing fixes an empty
#: slot, so until the core exists the parity term is dropped. From the fourth
#: member on, the full parity-aware score applies again: by then a new catch
#: has to be worth the training it will cost.
CORE_TEAM_SIZE = 4


@dataclass(frozen=True)
class CatchPlan:
    """Why we are (or are not) catching this one."""

    wanted: bool
    reason: str
    species: str = ""
    score: float = 0.0

    def __bool__(self) -> bool:
        return self.wanted


class Catcher:
    """Decides catch-or-KO for a wild encounter, and drives the ball turn."""

    def __init__(self, driver, team, dex=None):
        self.d = driver
        self.team = team
        self.dex = dex
        self.last_reason = ""
        self.thrown = 0
        self.caught = 0
        #: One GO NEAR per Safari battle; reset when a policy is built.
        self._approached = False
        #: The dex model, built lazily by `_dex_target` (False once it has
        #: failed, so a broken ROM read is not retried every turn).
        self._dex = None

    # ---- the "is it worth a ball" decision -------------------------------

    def in_safari(self) -> bool:
        """Is this a Safari battle? Read from the live battle type."""
        try:
            return "safari" in (self.d.state.battle().kinds or ())
        except Exception:  # noqa: BLE001
            return False

    def balls_available(self) -> int:
        """How many balls we can actually throw right now.

        Inside the Safari Zone that is `gNumSafariBalls`, not the ball pocket.
        Counting the pocket there measured a pool the game will not let you
        throw from, so the BALL_RESERVE guard could refuse a catch while thirty
        Safari Balls sat unused -- or, worse, allow one when the zone had run
        dry (pret/src/safari_zone.c:28,62).
        """
        try:
            if self.d.state.in_safari():
                return int(self.d.state.safari_balls())
        except Exception:  # noqa: BLE001
            pass
        try:
            balls = self.d.state.bag().get("poke_balls") or {}
        except Exception as err:  # noqa: BLE001
            log.debug("bag unreadable: %s", err)
            return 0
        return sum(int(v) for v in balls.values() if isinstance(v, int))

    def party_has_room(self) -> bool:
        try:
            return len([m for m in self.d.state.party()]) < MAX_PARTY
        except Exception:  # noqa: BLE001
            return False

    def storage_has_room(self) -> bool:
        """Can a caught mon go ANYWHERE -- party or a box?

        This is the gate that matters, and using party room instead is why the
        run caught nothing for hours. `GiveMonToPlayer` fills the first empty
        party slot and, when all six are taken, calls `SendMonToPC`
        (src/pokemon_2.c:964-983): a full party is not a refusal, it is a
        redirect. The run has had six mons since Petalburg, so every single
        encounter was declined with "party is full" while the game would have
        happily boxed the catch.
        """
        if self.party_has_room():
            return True
        try:
            target = self._dex_target()
            if target is None:
                return True
            # Occupancy, not identity: `owned_species` collapses duplicates,
            # so count slots straight out of gPokemonStorage.
            return target.box_free_slots() > 0
        except Exception:  # noqa: BLE001 - unreadable storage: let the throw
            # decide. A refused catch costs one ball; a refused RUN costs the
            # whole objective.
            return True

    def dex_caught(self, species: str) -> bool:
        """Has this species' CAUGHT flag been set in the live Pokedex?

        Seen is not caught: the run has 77 seen against 14 caught, and every
        one of those 63 is a ball that was never thrown.
        """
        try:
            target = self._dex_target()
            if target is None:
                return True
            caught, _seen = target.dex_flags(self.d.state)

            # HONOUR THE ARGUMENT. This used to ignore `species` entirely and
            # answer about `_enemy_species_id()` instead. In `plan()` the two
            # agree -- the caller passes the enemy's own name -- so it was
            # right by luck rather than by construction, and any other caller
            # got a confident answer about the wrong Pokemon. Asked about
            # DODUO and PIKACHU outside a battle it returned True for both,
            # which is the "unknown, fall through" default wearing a species
            # name.
            want = str(species).strip().upper()
            nat = next(
                (e.natdex for e in target.entries
                 if e.name.strip().upper() == want and e.natdex),
                None,
            )
            if nat is None:
                sid = self._enemy_species_id()
                if not sid:
                    return True      # genuinely unknown: keep the old default
                nat = target.evolutions.natdex(sid)
            return bool(nat) and nat in caught
        except Exception as err:  # noqa: BLE001
            log.debug("dex_caught failed: %s", err)
            return True

    def _dex_target(self):
        """The dex model, built once and kept.

        Constructing it parses the ROM's own tables, so it must not happen per
        turn -- but the FLAGS are read live every time, because they change the
        moment a ball lands.
        """
        if self._dex is None:
            try:
                from pokeagent import dex as dexmod

                self._dex = dexmod.DexTarget(
                    self.d.emu, self.d.names, self.d.consts, self.d.nav,
                    spec=self.d.spec,
                )
            except Exception as err:  # noqa: BLE001
                log.debug("no dex target for catching: %s", err)
                self._dex = False
        return self._dex or None

    def already_own(self, species: str) -> bool:
        """True when this species is already in the party.

        Deliberately NOT "already in the dex": the dex objective wants one of
        every species HELD, and a species seen fifty routes ago but never kept
        is not progress toward that.
        """
        try:
            names = {
                self.d.names.species(m.species)
                for m in self.d.state.party()
                if not m.is_egg
            }
        except Exception:  # noqa: BLE001
            return False
        return species in names

    def plan(self, frame) -> CatchPlan:
        """Should we spend a ball on the wild in this frame?"""
        if not frame.get("wild"):
            return self._no("trainer battle")
        enemy = frame.get("enemy") or {}
        species = enemy.get("species") or ""
        if not species:
            return self._no("no species read")
        if not self.storage_has_room():
            return self._no("party and every box are full")
        balls = self.balls_available()
        # THE DEX COMES FIRST -- AHEAD OF THE RESERVE TOO. Team merit decides
        # which mon to raise; it must not decide whether a species enters the
        # Pokedex at all. A species the dex has never recorded as CAUGHT is
        # worth a ball on sight, because that registration is the objective and
        # no later encounter is guaranteed -- some of these tables are 5% slots
        # on one map.
        #
        # The reserve used to be checked FIRST, which contradicted that in the
        # worst way: holding exactly BALL_RESERVE balls made `balls <= RESERVE`
        # true and declined EVERY catch, dex-new included. The run sat at 3
        # NET BALLs and 38/114 caught, visiting maps with five new species each
        # and refusing all of them, while the reserve it was protecting had
        # nothing left to protect for. A reserve is for choosing between
        # catches, never for refusing the only kind that closes the objective.
        if not self.dex_caught(species):
            if balls < 1:
                return self._no("no balls at all")
            note = f"{species} is new to the Pokedex"
            self.last_reason = note
            return CatchPlan(True, note, species, 999.0)
        if balls <= BALL_RESERVE:
            return self._no(f"only {balls} balls left (reserve {BALL_RESERVE})")
        if self.already_own(species):
            return self._no(f"already have a {species}")

        species_id = self._enemy_species_id()
        if not species_id:
            return self._no(f"no species id for {species}")
        party = self._party_rows()
        try:
            ranked = self.team.recommend_catch(
                [(species_id, enemy.get("level") or 1)], party
            )
        except Exception as err:  # noqa: BLE001 - never lose a battle to this
            log.debug("recommend_catch failed: %s", err)
            return self._no(f"cannot score {species}")
        if not ranked:
            return self._no(f"{species} scored nothing")
        top = ranked[0]
        score = float(getattr(top, "score", 0.0))
        why = getattr(top, "why", "") or ""

        # Coverage merit is the score with the level penalty taken back out.
        # W_PARITY is negative, so subtracting its contribution removes it.
        parity_cost = int(getattr(top, "parity_cost", 0) or 0)
        merit = score - (teammod.W_PARITY * parity_cost)

        building = len(party) < CORE_TEAM_SIZE
        judged, label = (merit, "coverage") if building else (score, "score")
        if judged <= 0:
            return CatchPlan(
                False, f"{species} adds nothing ({why})", species, judged
            )
        note = (
            f"{species} {label} {judged:.1f}"
            + (f" (parity cost {parity_cost} ignored while the core is "
               f"{len(party)}/{CORE_TEAM_SIZE})" if building else "")
            + f": {why}"
        )
        self.last_reason = note
        return CatchPlan(True, note, species, judged)

    def _enemy_species_id(self) -> int:
        """The wild's species as an ID.

        `recommend_catch` scores by species id, but the battle frame reports
        the NAME (it is built for humans and logs). The live `Combatant`
        carries both, so read it there rather than reverse-mapping a string.
        """
        try:
            return int(self.d.battle.battler(1).species)
        except Exception as err:  # noqa: BLE001
            log.debug("no enemy species id: %s", err)
            return 0

    def _party_rows(self):
        try:
            return self.d.state.party()
        except Exception:  # noqa: BLE001
            return []

    def _no(self, reason: str) -> CatchPlan:
        self.last_reason = reason
        return CatchPlan(False, reason)

    # ---- the "is this the turn" decision ---------------------------------

    def policy(self, plan: CatchPlan, inner=None):
        """A battle policy that throws a ball at the right moment.

        Wraps another policy (the training policy, normally) so that
        everything except the catch decision keeps working: the wrapped policy
        still picks the move that weakens without killing.
        """

        self._approached = False

        def decide(frame):
            enemy = frame.get("enemy") or {}
            hp, mx = enemy.get("hp") or 0, enemy.get("max_hp") or 0
            frac = (hp / mx) if mx else 1.0
            if hp <= 0:
                return inner(frame) if inner else None

            # THE SAFARI HAS NO WEAKENING PHASE. There is no player mon on the
            # field at all -- the engine zeroes it (battle_main.c:3711-3715) --
            # so `frame["moves"]` is empty, the target sits at full HP forever,
            # and every HP-based throw trigger below is unreachable. Waiting
            # for a weakened target there is waiting for something that cannot
            # happen while the 15%-per-turn flee roll runs
            # (battle_ai_script_commands.c:1668-1674). Throw on turn one, and
            # never hand the turn to a training policy that has no move to
            # pick.
            if self.in_safari():
                # ONE approach first, then throw. GO NEAR is the only thing
                # that raises the odds (there is no weakening phase), and the
                # ROM's own tables make exactly the first one worthwhile: the
                # catch-factor bonus falls 4,3,2,1 while the flee-rate penalty
                # stays a flat 4 (pret/data/btl_attrs.s:380-391). Taking a
                # second is paying more flee risk for less catch chance.
                if not self._approached:
                    self._approached = True
                    return "go_near"
                self.thrown += 1
                return ("ball", "SAFARI BALL")

            ball = self._pick_ball()
            if ball is None:
                return inner(frame) if inner else None
            if frac <= THROW_BELOW or frac <= DANGER_HP:
                self.thrown += 1
                return ("ball", ball)
            # Still healthy: if our best move would KO it, throw NOW rather
            # than lose the catch to our own damage.
            if self._would_ko(frame, hp):
                self.thrown += 1
                return ("ball", ball)
            return inner(frame) if inner else None

        return decide

    def _pick_ball(self):
        """The cheapest ball in the bag, by the ROM's own price."""
        try:
            balls = self.d.state.bag().get("poke_balls") or {}
        except Exception:  # noqa: BLE001
            return None
        have = [n for n, q in balls.items() if isinstance(q, int) and q > 0]
        if not have:
            return None

        def price(name):
            # item_data() is keyed by ID; the bag reports names.
            try:
                item_id = self.d.battle.tactics.item_id(name)
                return self.d.names.item_data(item_id).price
            except Exception:  # noqa: BLE001
                return 10_000

        return min(have, key=price)

    def _would_ko(self, frame, enemy_hp) -> bool:
        """Would our own best move finish it this turn?

        Uses the same outlook the tactics layer uses, so the estimate is the
        game's damage formula rather than a guess.
        """
        try:
            analysis = self.d.outlook()
        except Exception:  # noqa: BLE001
            return False
        if not analysis:
            return False
        for mv in analysis.get("moves") or ():
            if (mv.get("damage_max") or 0) >= enemy_hp:
                return True
        return False

    def stats(self) -> dict:
        return {"thrown": self.thrown, "caught": self.caught}
