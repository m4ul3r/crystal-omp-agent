"""Team composition policy: type coverage, level parity, and training order.

The objective this module serves is "the team is well-rounded and all Pokemon
are around the same level". Those are two different questions and this module
keeps them apart:

* **Well-rounded** is a *type* question. It is answered against the ROM's own
  effectiveness table (``gTypeEffectiveness``, data/type_effectiveness.inc:1-6,
  read by :attr:`pokeagent.names.Names.type_chart`), the ROM's own species
  types (``gBaseStats``) and the moves the party *actually knows*. Nothing here
  transcribes a type chart, a base stat or a learnset: every number is read out
  of the cartridge at runtime. The predecessor project shipped a hand-typed
  chart with two wrong rows and fought a whole gym with a resisted move.
* **Around the same level** is an *arithmetic* question over the party, and the
  policy it implies is a level FLOOR: raise the laggards up, never let one
  overlevelled lead carry the run. That distinction changes the battle policy
  completely, because of the Gen-3 experience rule below.

**The Gen-3 experience rule that dictates the training policy.**
``getexp`` computes ``exp = expYield * enemyLevel / 7`` and then divides it by
``viaSentIn`` -- the number of the player's Pokemon that *participated* in the
fight (src/battle_script_commands.c:3379-3396). With an EXP.SHARE in the party
the participants' share is halved again (ibid.:3381-3389). So:

* a laggard that fights the whole battle alone gets 100% of the exp;
* a laggard switched in after the lead already took a turn gets 50%, because
  the lead is counted as a participant for the rest of the battle;
* a laggard sitting on the bench gets nothing at all.

Therefore :meth:`Team.training_policy` leads with a laggard when it is safe to
and says, in its ``why``, whether the exp will be split. It never switches a
laggard into a fight it cannot survive -- fainting a laggard trades exp for a
trip to the Pokemon Centre, which is a net loss of levels per wall-clock
minute.

Every recommendation this module makes carries a ``why`` string, and every
fallible call leaves a ``last_*_reason``. A team policy the operator cannot
audit is indistinguishable from a random one.
"""

import logging
from dataclasses import dataclass

from . import paths
from .names import TYPE_MUL_NORMAL

log = logging.getLogger("pokeagent.team")

#: Levels a member may sit below the party maximum before it counts as a
#: laggard. "Around the same level" is a band, not an equality: three levels is
#: roughly one Route-101 encounter's worth of exp at the levels where parity
#: actually matters, so a tighter band would flag the whole party after every
#: single battle.
DEFAULT_TOLERANCE = 3

#: A laggard is only worth leading with above this HP fraction. Below it the
#: policy anchors on a healthy mon instead: a fainted laggard earns no exp and
#: costs a Centre trip.
DEFAULT_SAFE_HP_FRAC = 0.5

#: Levels the enemy may be ABOVE a laggard before leading with the laggard is
#: refused. A laggard four levels down loses the speed tie and the damage race
#: at the same time.
DEFAULT_ENEMY_GAP = 3

#: :meth:`Team.recommend_catch` weights. Stated here rather than buried in the
#: function so the ranking can be argued with.
W_OFFENSE_NOW = 3.0        # a gap type the candidate can hit SE with a move it has
W_OFFENSE_POTENTIAL = 1.0  # a gap type its own types can hit SE, given a TM later
W_DEFENSE = 2.0            # a defensive hole the candidate resists
W_REDUNDANT = -2.0         # per type the party already fields
W_PARITY = -0.25           # per level the catch would owe the training floor

#: ``GiveBoxMonInitialMoveset`` fills four slots (src/pokemon_1.c:1863-1878).
MOVE_SLOTS = 4


@dataclass(slots=True)
class Member:
    """One party slot, normalised so coverage math does not care whether it
    came from :meth:`pokeagent.state.GameState.party` (a
    :class:`pokeagent.pokemon.Mon`) or from
    :meth:`pokeagent.battle.BattleSession.frame` (a dict)."""

    index: int
    nickname: str
    species: int
    level: int
    hp: int
    max_hp: int
    egg: bool
    moves: tuple = ()
    types: tuple = ()
    #: ``[(move_id, move_name, move_type)]`` for the moves with non-zero power.
    attacks: tuple = ()

    @property
    def fights(self) -> bool:
        """Can this slot ever take a turn? An egg cannot, and neither can an
        empty slot. An egg reads 0 HP and 0 level, so treating it as a member
        is how the predecessor's train() rail built an infinite heal loop
        (pokemon.py:140-142 carries the same warning for ``fainted``)."""
        return not self.egg and bool(self.species)

    @property
    def alive(self) -> bool:
        return self.fights and self.hp > 0

    @property
    def hp_frac(self) -> float:
        return self.hp / self.max_hp if self.max_hp else 0.0

    @property
    def label(self) -> str:
        return self.nickname or f"#{self.index}"


@dataclass(slots=True)
class TypeFact:
    """What the party can do to one type, and what that type can do to it.

    Deliberately species-agnostic: the catch planner consumes *types*, so it
    can score a candidate it has never seen without knowing anything about the
    party's roster.
    """

    type_id: int
    type_name: str
    #: Best multiplier the party achieves attacking INTO this type. ``None``
    #: when the party knows no damaging move at all -- which is not the same
    #: as "immune", and conflating the two is how a GROWL-only party looks
    #: fine on paper.
    best_offense: float | None
    #: ``[(member label, move name, multiplier)]`` for the super-effective hits.
    hitters: tuple
    #: Worst multiplier this type achieves against any party member.
    worst_incoming: float
    #: Labels of members that resist (or are immune to) this type.
    resisted_by: tuple
    #: Labels of members this type hits super-effectively.
    hits: tuple
    offense_gap: bool
    no_effect: bool
    defense_hole: bool
    why: str


@dataclass(slots=True)
class Coverage:
    """Type analysis of a whole party, plus the :attr:`roundness` score.

    ``roundness`` is on 0-100 and is defined as::

        offense = |types the party can hit super-effectively| / |real types|
        defense = 1 - |types that hit the party SE with nothing resisting|
                        / |real types|
        roundness = 100 * (offense + defense) / 2

    ``|real types|`` is derived, not written down: it is every ``TYPE_*`` in
    include/constants/pokemon.h:101-118 except ``TYPE_MYSTERY``, which is the
    table separator rather than a type (17 of them in Gen 3).

    The two halves are weighted equally on purpose. An all-offense team sweeps
    until it meets something faster; an all-defense team stalls out on PP. The
    score is a summary, never the decision -- :attr:`offense_gaps` and
    :attr:`defense_holes` are what a planner should act on.
    """

    types: tuple
    covered: tuple
    offense_gaps: tuple
    no_effect: tuple
    defense_holes: tuple
    roundness: float
    offense_fraction: float
    defense_fraction: float
    members: tuple
    why: str

    def fact(self, type_id) -> TypeFact | None:
        return next((f for f in self.types if f.type_id == type_id), None)

    def gap_type_ids(self) -> tuple:
        """Type ids the party cannot hit super-effectively -- the offensive
        shopping list, as ids so a planner can feed them straight to the type
        chart."""
        return tuple(f.type_id for f in self.types if f.offense_gap)

    def hole_type_ids(self) -> tuple:
        """Type ids that hit the party super-effectively with nothing
        resisting."""
        return tuple(f.type_id for f in self.types if f.defense_hole)

    def as_dict(self) -> dict:
        return {
            "roundness": self.roundness,
            "offense_fraction": round(self.offense_fraction, 3),
            "defense_fraction": round(self.defense_fraction, 3),
            "covered": list(self.covered),
            "offense_gaps": list(self.offense_gaps),
            "no_effect": list(self.no_effect),
            "defense_holes": list(self.defense_holes),
            "members": [m.label for m in self.members],
            "why": self.why,
        }


@dataclass(slots=True)
class CatchRec:
    """One ranked catch candidate, with the arithmetic that ranked it."""

    species: int
    species_name: str
    level: int
    types: tuple
    score: float
    offense_now: tuple
    offense_potential: tuple
    defense_fill: tuple
    redundant_types: tuple
    parity_cost: int
    moveset: tuple
    why: str

    def as_dict(self) -> dict:
        return {
            "species": self.species,
            "name": self.species_name,
            "level": self.level,
            "types": list(self.types),
            "score": self.score,
            "fills_offense": list(self.offense_now),
            "could_fill_offense": list(self.offense_potential),
            "fills_defense": list(self.defense_fill),
            "redundant_types": list(self.redundant_types),
            "parity_cost": self.parity_cost,
            "moveset": list(self.moveset),
            "why": self.why,
        }


@dataclass(slots=True)
class Decision:
    """One turn's worth of policy output, kept so a battle can be audited
    after the fact the way :meth:`pokeagent.battle.BattleSession.summary_text`
    is."""

    turn: int
    action: tuple | str | None
    why: str


class TrainingPolicy:
    """A :meth:`pokeagent.battle.BattleSession.play` policy that farms exp for
    the party's laggards.

    Shape, per ``play``'s contract: ``policy(frame)`` returns
    ``("attack", slot)``, ``("switch", party_index)``, ``("item", name)``,
    ``"flee"`` or ``None``, and ``None`` defers to
    :meth:`pokeagent.tactics.Tactics.recommend`.

    This policy only ever returns a switch or ``None``. Picking the *move* is
    a damage-math question that ``tactics`` already answers from the ROM's own
    formula; duplicating it here would be a second, weaker source of truth.
    What ``tactics`` cannot know is *which* Pokemon should be the one getting
    the exp, and that is the whole job here.

    The Gen-3 rule it exists to exploit: exp is divided by the number of
    participants (src/battle_script_commands.c:3379-3396), so a laggard must be
    the sole participant AND land the KO to get the full amount. Switching in
    after the lead has already taken a turn halves it, and the ``why`` says so
    when that happens.
    """

    __slots__ = ("team", "party", "tolerance", "safe_hp_frac", "enemy_gap",
                 "log", "last_why")

    def __init__(self, team, party, *, tolerance=DEFAULT_TOLERANCE,
                 safe_hp_frac=DEFAULT_SAFE_HP_FRAC,
                 enemy_gap=DEFAULT_ENEMY_GAP):
        self.team = team
        #: The party as it looked when the policy was built. Only a fallback:
        #: a live frame carries levels that have already changed this battle.
        self.party = tuple(team.members(party)) if party is not None else ()
        self.tolerance = tolerance
        self.safe_hp_frac = safe_hp_frac
        self.enemy_gap = enemy_gap
        self.log: list[Decision] = []
        self.last_why = "not called yet"

    def _decide(self, action, why, turn):
        self.last_why = why
        self.log.append(Decision(turn=turn, action=action, why=why))
        log.info("[team] turn %s: %r -- %s", turn, action, why)
        return action

    def __call__(self, frame):
        if not isinstance(frame, dict):
            # play() catches a policy exception, logs the traceback and hands
            # the turn to tactics (battle.py:1314-1321), so raising here is
            # loud without being fatal -- and far better than a .get()
            # AttributeError from three frames down.
            raise TypeError(
                f"a battle policy is called with the frame dict from "
                f"BattleSession.frame(), not a {type(frame).__name__}"
            )
        turn = frame.get("turn", 0)
        if not frame.get("active"):
            return self._decide(
                None, "no battle is active; nothing to decide", turn
            )

        rows = frame.get("party")
        members = self.team.members(rows) if rows else self.party
        if not members:
            return self._decide(
                None,
                "the frame carried no party and none was supplied at build "
                "time, so laggards cannot be identified",
                turn,
            )

        me = frame.get("me") or {}
        active = me.get("party_index")
        enemy = frame.get("enemy") or {}
        enemy_level = enemy.get("level") or 0

        laggards = self.team.needs_training(members, tolerance=self.tolerance)
        if not laggards:
            parity = self.team.parity(members, tolerance=self.tolerance)
            return self._decide(
                None,
                f"no laggard: spread is {parity['spread']} level(s) within the "
                f"{self.tolerance}-level band, so tactics picks the turn",
                turn,
            )

        by_index = {m.index: m for m in members}
        switchable = frame.get("can_switch")
        if switchable is None:
            switchable = [m.index for m in members if m.alive and m.index != active]

        # Already training the right mon? Then the only question is whether it
        # is about to faint, because a fainted laggard earns nothing.
        if active is not None and any(l["index"] == active for l in laggards):
            mine = by_index.get(active)
            frac = (me.get("hp", 0) / me["max_hp"]) if me.get("max_hp") else (
                mine.hp_frac if mine else 0.0
            )
            if frac >= self.safe_hp_frac:
                lag = next(l for l in laggards if l["index"] == active)
                return self._decide(
                    None,
                    f"{lag['nickname']} is the laggard AND the sole "
                    f"participant at {frac:.0%} HP, so it keeps the KO and "
                    f"the full exp; tactics picks the move",
                    turn,
                )
            anchor = self._anchor(members, laggards, switchable)
            if anchor is None:
                return self._decide(
                    None,
                    f"laggard at slot {active} is down to {frac:.0%} HP but "
                    f"there is no healthy non-laggard to anchor on "
                    f"(switchable={switchable}); tactics decides",
                    turn,
                )
            return self._decide(
                ("switch", anchor.index),
                f"laggard at slot {active} is at {frac:.0%} HP, below the "
                f"{self.safe_hp_frac:.0%} floor; anchoring on "
                f"{anchor.label} L{anchor.level} -- a fainted laggard earns no "
                f"exp and costs a Centre trip",
                turn,
            )

        # Not training anyone yet. Lead with the furthest-behind laggard that
        # is both healthy enough and not outclassed by the enemy.
        refusals = []
        for lag in laggards:
            mon = by_index.get(lag["index"])
            if mon is None:
                refusals.append(f"slot {lag['index']} is not in the live party")
                continue
            if mon.index not in switchable:
                refusals.append(
                    f"{mon.label} is not switchable (fainted, an egg, or "
                    f"already on the field)"
                )
                continue
            if mon.hp_frac < self.safe_hp_frac:
                refusals.append(
                    f"{mon.label} is at {mon.hp_frac:.0%} HP, below the "
                    f"{self.safe_hp_frac:.0%} floor"
                )
                continue
            if enemy_level - mon.level > self.enemy_gap:
                refusals.append(
                    f"{mon.label} L{mon.level} is {enemy_level - mon.level} "
                    f"levels under the L{enemy_level} "
                    f"{enemy.get('species', 'enemy')}, past the "
                    f"{self.enemy_gap}-level gap"
                )
                continue
            split = (
                "exp will be split with the lead, which has already taken a "
                "turn" if turn else "the lead has not acted yet, so the "
                "laggard will be the sole participant and take all the exp"
            )
            return self._decide(
                ("switch", mon.index),
                f"{mon.label} L{mon.level} is {lag['gap']} levels under the "
                f"party median L{lag['party_median']:g} and safe to lead with "
                f"({mon.hp_frac:.0%} HP vs a L{enemy_level} "
                f"{enemy.get('species', 'enemy')}); {split}",
                turn,
            )

        return self._decide(
            None,
            "no laggard is safe to lead with -- "
            + "; ".join(refusals)
            + "; deferring to tactics",
            turn,
        )

    def _anchor(self, members, laggards, switchable):
        """The strongest healthy mon that is NOT a laggard: somewhere to hide a
        laggard that got hurt, without giving the exp to another laggard."""
        lag = {l["index"] for l in laggards}
        pool = [
            m for m in members
            if m.alive and m.index not in lag and m.index in switchable
        ]
        if not pool:
            return None
        return max(pool, key=lambda m: (m.level, m.hp_frac))


class Team:
    """Composition and training policy for the live party.

    Constructed from :class:`pokeagent.names.Names` and
    :class:`pokeagent.cconst.Constants`; ``state`` is optional and only needed
    by the convenience wrappers that read the party themselves.
    """

    def __init__(self, names, consts, state=None):
        self.names = names
        self.consts = consts
        self.state = state

        types = consts.ns("pokemon.h")
        mystery = types["TYPE_MYSTERY"]
        # Every TYPE_* except the table separator. Derived so that a Gen-1/2
        # adapter with a different type count works without editing this file.
        self._type_ids = tuple(sorted(
            value for name, value in types.items()
            if name.startswith("TYPE_") and isinstance(value, int)
            and value != mystery
        ))
        if not self._type_ids:
            raise ValueError(
                "constants/pokemon.h yielded no TYPE_* ids -- the header "
                "parse is wrong and every coverage number would be a lie"
            )

        # The super-effective threshold comes from the engine's own name, not
        # from "2.0" written down here. TYPE_MUL_* live in
        # include/battle.h:64-67, NOT in include/constants/, so the namespace
        # is addressed by full path -- `consts.battle` is constants/battle.h
        # and does not contain them. "Resists" is deliberately `< 1.0` rather
        # than TYPE_MUL_NOT_EFFECTIVE: a dual type can resist at x0.25 and an
        # immunity resists at x0, and both must count.
        mul = consts.ns(str(paths.INCLUDE / "battle.h"))
        self.super_effective = mul["TYPE_MUL_SUPER_EFFECTIVE"] / TYPE_MUL_NORMAL

        self._species_by_name = None
        self._type_names = {}

        #: The project's contract: a falsy or empty return always comes with
        #: the sentence explaining it.
        self.last_catch_reason = None
        self.last_parity_reason = None

    # ---- type vocabulary -------------------------------------------------

    @property
    def type_ids(self) -> tuple:
        """The real attacking/defending types, ``TYPE_MYSTERY`` excluded."""
        return self._type_ids

    def type_name(self, type_id) -> str:
        name = self._type_names.get(type_id)
        if name is None:
            name = self._type_names[type_id] = self.names.type(type_id)
        return name

    def species_types(self, species_id) -> tuple:
        """``(type1,)`` or ``(type1, type2)`` from ``gBaseStats``."""
        stats = self.names.base_stats(species_id)
        return tuple(dict.fromkeys((stats.type1, stats.type2)))

    def multiplier(self, attacking, defending_types) -> float:
        """Product of the chart entries for one attacking type against a
        member's type(s)."""
        chart = self.names.type_chart
        mul = 1.0
        for t in defending_types:
            mul *= chart.get((attacking, t), 1.0)
        return mul

    # ---- party normalisation ---------------------------------------------

    def _species_id(self, value, egg):
        if isinstance(value, int):
            return value
        if egg or not value or value in ("EGG", "-"):
            return 0
        if self._species_by_name is None:
            self._species_by_name = {
                self.names.species(i): i
                for i in range(self.names.species_count)
            }
        try:
            return self._species_by_name[value]
        except KeyError:
            raise ValueError(
                f"{value!r} is not a species name in gSpeciesNames -- refusing "
                f"to guess an id, because a wrong id reads the wrong row of "
                f"gBaseStats and every type fact downstream is silently wrong"
            ) from None

    def _move_ids(self, raw) -> tuple:
        out = []
        for entry in raw or ():
            if isinstance(entry, int):
                mid = entry
            elif isinstance(entry, dict):
                mid = entry.get("id")
                if mid is None:
                    # A frame row carries move NAMES, not ids. Resolving them
                    # by scanning gMoveNames is possible but the caller almost
                    # certainly has the Mon object that holds the ids, so say
                    # so rather than doing 350 ROM reads per member.
                    raise ValueError(
                        f"move entry {entry!r} has no 'id'; pass the party "
                        f"from state.party() (Mon.moves holds engine move "
                        f"ids) rather than a battle frame's party rows"
                    )
            else:
                raise ValueError(f"cannot read a move id out of {entry!r}")
            if mid:
                out.append(mid)
        return tuple(out)

    def _member(self, raw, index) -> Member:
        if isinstance(raw, Member):
            # Idempotent: a caller that already normalised its party (or that
            # is passing rows this module handed it) must not have them
            # re-derived. Re-reading gBaseStats per call is pure cost, and a
            # Member has no raw form to go back to.
            return raw
        if isinstance(raw, dict):
            egg = bool(raw.get("egg"))
            species = self._species_id(raw.get("species", 0), egg)
            moves = self._move_ids(raw.get("moves")) if raw.get("moves") else ()
            member = Member(
                index=raw.get("index", index),
                nickname=raw.get("nickname") or "",
                species=species,
                level=raw.get("level") or 0,
                hp=raw.get("hp") or 0,
                max_hp=raw.get("max_hp") or 0,
                egg=egg,
                moves=moves,
            )
        else:
            egg = bool(getattr(raw, "is_egg", False))
            member = Member(
                index=index,
                nickname=getattr(raw, "nickname", "") or "",
                species=getattr(raw, "species", 0) or 0,
                level=getattr(raw, "level", 0) or 0,
                hp=getattr(raw, "hp", 0) or 0,
                max_hp=getattr(raw, "max_hp", 0) or 0,
                egg=egg,
                moves=tuple(m for m in (getattr(raw, "moves", ()) or ()) if m),
            )
        if member.fights:
            member.types = self.species_types(member.species)
            member.attacks = self._attacks(member.moves)
        if not member.nickname and member.species:
            member.nickname = self.names.species(member.species)
        elif not member.nickname and member.egg:
            member.nickname = "EGG"
        return member

    def _attacks(self, move_ids) -> tuple:
        """``[(id, name, type)]`` for the damaging moves only.

        A zero-power move contributes nothing to offensive coverage: GROWL is
        not "Normal coverage". Fixed-damage moves (SEISMIC TOSS, DRAGON RAGE)
        also read as power 0 in ``gBattleMoves`` and are excluded on purpose --
        they ignore the effectiveness multiplier, so counting them as coverage
        would claim a super-effective hit the engine will never deal.
        """
        out = []
        for mid in move_ids:
            data = self.names.move_data(mid)
            if data.power:
                out.append((mid, data.name, data.type))
        return tuple(out)

    def members(self, party) -> tuple:
        """Normalise a party into :class:`Member` rows.

        Accepts :class:`pokeagent.pokemon.Mon` objects (from
        ``state.party()``), battle-frame party dicts, or anything with the same
        attributes.
        """
        return tuple(self._member(raw, i) for i, raw in enumerate(party or ()))

    def party(self) -> tuple:
        """The live party, normalised. Requires a ``state``."""
        if self.state is None:
            raise ValueError(
                "Team was built without a GameState, so it cannot read the "
                "live party; pass the party explicitly"
            )
        return self.members(self.state.party())

    # ---- well-roundedness ------------------------------------------------

    def coverage(self, party) -> Coverage:
        """Offensive coverage, defensive holes and the roundness score.

        Fainted members still count: this is a question about the team's
        composition, not about the current turn. Eggs and empty slots do not,
        because they can never take a turn.
        """
        members = tuple(m for m in self.members(party) if m.fights)
        if not members:
            return Coverage(
                types=(), covered=(), offense_gaps=(), no_effect=(),
                defense_holes=(), roundness=0.0, offense_fraction=0.0,
                defense_fraction=0.0, members=(),
                why="no battle-capable members: an empty party has no coverage "
                    "to analyse (eggs and empty slots are excluded because "
                    "they can never take a turn)",
            )

        has_attack = any(m.attacks for m in members)
        n = len(self._type_ids)
        facts = []
        for tid in self._type_ids:
            name = self.type_name(tid)

            best = None
            hitters = []
            for m in members:
                for _mid, move_name, move_type in m.attacks:
                    mul = self.names.effectiveness(move_type, tid)
                    if best is None or mul > best:
                        best = mul
                    if mul >= self.super_effective:
                        hitters.append((m.label, move_name, mul))

            incoming = {m.label: self.multiplier(tid, m.types) for m in members}
            worst = max(incoming.values())
            resisted_by = tuple(l for l, mul in incoming.items() if mul < 1.0)
            hits = tuple(
                l for l, mul in incoming.items() if mul >= self.super_effective
            )

            offense_gap = not hitters
            no_effect = has_attack and best == 0.0
            defense_hole = bool(hits) and not resisted_by

            if not has_attack:
                why = (
                    f"the party knows no damaging move at all, so its offense "
                    f"against {name} is undefined rather than zero"
                )
            elif hitters:
                who, move_name, mul = hitters[0]
                why = f"{who}'s {move_name} hits {name} for x{mul:g}"
            elif no_effect:
                why = (
                    f"nothing the party knows can damage {name} at all "
                    f"(best multiplier x0)"
                )
            else:
                why = (
                    f"no known move is super-effective against {name} "
                    f"(best x{best:g})"
                )
            if defense_hole:
                why += (
                    f"; and {name} hits {', '.join(hits)} for "
                    f"x{worst:g} with nothing on the team resisting it"
                )
            elif resisted_by:
                why += f"; {', '.join(resisted_by)} resists incoming {name}"

            facts.append(TypeFact(
                type_id=tid, type_name=name, best_offense=best,
                hitters=tuple(hitters), worst_incoming=worst,
                resisted_by=resisted_by, hits=hits, offense_gap=offense_gap,
                no_effect=no_effect, defense_hole=defense_hole, why=why,
            ))

        covered = tuple(f.type_name for f in facts if not f.offense_gap)
        gaps = tuple(f.type_name for f in facts if f.offense_gap)
        holes = tuple(f.type_name for f in facts if f.defense_hole)
        dead = tuple(f.type_name for f in facts if f.no_effect)

        offense_fraction = len(covered) / n
        defense_fraction = 1 - len(holes) / n
        roundness = round(100 * (offense_fraction + defense_fraction) / 2, 1)

        return Coverage(
            types=tuple(facts), covered=covered, offense_gaps=gaps,
            no_effect=dead, defense_holes=holes, roundness=roundness,
            offense_fraction=offense_fraction,
            defense_fraction=defense_fraction, members=members,
            why=(
                f"roundness {roundness}/100 = mean of offense "
                f"{len(covered)}/{n} types hit super-effectively and defense "
                f"1 - {len(holes)}/{n} unresisted super-effective types, over "
                f"{len(members)} battle-capable member(s)"
            ),
        )

    def gaps(self, party) -> tuple:
        """The species-agnostic type facts a catch planner should act on:
        every type the party cannot hit super-effectively, or that hits the
        party super-effectively unresisted."""
        cov = self.coverage(party)
        return tuple(
            f for f in cov.types if f.offense_gap or f.defense_hole
        )

    def roundness(self, party) -> float:
        """0-100. See :class:`Coverage` for the definition."""
        return self.coverage(party).roundness

    # ---- catch planning --------------------------------------------------

    def wild_moveset(self, species_id, level) -> tuple:
        """The four moves a wild Pokemon of this species and level actually
        has.

        Exactly ``GiveBoxMonInitialMoveset`` (src/pokemon_1.c:1915-1935): walk
        the level-up learnset in order, stop at the first entry above the
        mon's level, skip a move already in the four slots
        (``GiveMoveToBoxMon`` returns -2, ibid.:1875-1876) and shift out slot 0
        when full (``DeleteFirstMoveAndGiveMoveToBoxMon``, ibid.:1934). Any
        cheaper approximation -- "the last four entries", say -- gets a
        different moveset for every species with a repeated move.
        """
        known: list[int] = []
        for lvl, mid in self.names.level_up_moves(species_id):
            if lvl > level:
                break
            if mid in known:
                continue
            known.append(mid)
            if len(known) > MOVE_SLOTS:
                known.pop(0)
        return tuple(known)

    def _candidate(self, raw):
        """``(species_id, level)`` out of an id/level pair, or a dict."""
        if isinstance(raw, dict):
            species = raw.get("species")
            if species is None:
                raise ValueError(f"candidate {raw!r} has no 'species'")
            species = self._species_id(species, egg=False)
            level = raw.get("level")
            if level is None:
                levels = raw.get("levels")
                if not levels:
                    raise ValueError(
                        f"candidate {raw!r} has neither 'level' nor 'levels'; "
                        f"a candidate's moveset depends on its level, so "
                        f"there is no safe default to invent"
                    )
                # The dex dataset gives a range; the top of it is the strongest
                # thing actually catchable there, which is what a level floor
                # wants.
                level = max(levels)
            return species, int(level)
        if isinstance(raw, (tuple, list)) and len(raw) == 2:
            return self._species_id(raw[0], egg=False), int(raw[1])
        raise ValueError(
            f"candidate {raw!r} must be a (species, level) pair or a dict with "
            f"'species' and 'level'/'levels' -- a bare species id has no "
            f"level, and its moveset depends on one"
        )

    def recommend_catch(self, candidates, party, *, limit=None) -> list:
        """Rank catch candidates by how much of the party's gap they fill.

        ``candidates`` are ``(species_id, level)`` pairs (or dicts with
        ``species`` and ``level``/``levels``). The score, weights at the top of
        this module::

            score = 3 * |gap types it can hit SE with the moves it will have|
                  + 1 * |gap types its own types can hit SE|
                  + 2 * |defensive holes it resists|
                  - 2 * |its types the party already fields|
                  - 0.25 * (party max level - its level, floored at 0)

        The "moves it will have" come from :meth:`wild_moveset`, so the
        offensive credit is what the engine will actually give you on capture,
        not what the species can learn eventually -- that is the separate,
        cheaper ``offense_potential`` term, which is the credit for a type the
        candidate could exploit once it levels up or eats a TM.

        The parity term exists because this project's objective is a level
        FLOOR: a level-3 catch for a level-20 party is a real cost, not a free
        addition.
        """
        self.last_catch_reason = None
        cov = self.coverage(party)
        gap_ids = set(cov.gap_type_ids())
        hole_ids = set(cov.hole_type_ids())
        party_types = {t for m in cov.members for t in m.types}
        party_max = max((m.level for m in cov.members), default=0)

        pairs = [self._candidate(c) for c in candidates]
        if not pairs:
            self.last_catch_reason = "no candidates were supplied"
            return []

        out = []
        for species, level in pairs:
            if not species:
                raise ValueError(
                    "candidate species 0 is SPECIES_NONE; a caller passing it "
                    "has a bad id and would get a gBaseStats row of zeroes"
                )
            types = self.species_types(species)
            moveset = self.wild_moveset(species, level)
            attacks = self._attacks(moveset)

            now = []
            now_ids = set()
            for tid in sorted(gap_ids):
                for _mid, move_name, move_type in attacks:
                    if self.names.effectiveness(move_type, tid) >= self.super_effective:
                        now.append((self.type_name(tid), move_name))
                        now_ids.add(tid)
                        break

            potential = tuple(
                self.type_name(tid) for tid in sorted(gap_ids)
                if tid not in now_ids
                and any(
                    self.names.effectiveness(own, tid) >= self.super_effective
                    for own in types
                )
            )
            defense = tuple(
                self.type_name(tid) for tid in sorted(hole_ids)
                if self.multiplier(tid, types) < 1.0
            )
            redundant = tuple(
                self.type_name(t) for t in types if t in party_types
            )
            parity_cost = max(0, party_max - level)

            score = round(
                W_OFFENSE_NOW * len(now)
                + W_OFFENSE_POTENTIAL * len(potential)
                + W_DEFENSE * len(defense)
                + W_REDUNDANT * len(redundant)
                + W_PARITY * parity_cost,
                3,
            )

            name = self.names.species(species)
            bits = []
            if now:
                bits.append(
                    "fills " + ", ".join(f"{t} via {mv}" for t, mv in now)
                )
            if potential:
                bits.append("could fill " + ", ".join(potential) + " later")
            if defense:
                bits.append("resists the team's open " + ", ".join(defense))
            if redundant:
                bits.append(
                    "but duplicates " + ", ".join(redundant)
                    + " the team already fields"
                )
            if parity_cost:
                bits.append(
                    f"and owes {parity_cost} level(s) of training to reach the "
                    f"party's L{party_max} floor"
                )
            if not bits:
                bits.append(
                    "fills nothing: no gap type it can hit, no open hole it "
                    "resists"
                )

            out.append(CatchRec(
                species=species, species_name=name, level=level,
                types=tuple(self.type_name(t) for t in types),
                score=score,
                offense_now=tuple(t for t, _ in now),
                offense_potential=potential, defense_fill=defense,
                redundant_types=redundant, parity_cost=parity_cost,
                moveset=tuple(self.names.move(m) for m in moveset),
                why=f"{name} L{level} scores {score}: " + "; ".join(bits),
            ))

        out.sort(key=lambda r: (-r.score, -r.level, r.species_name))
        if limit is not None:
            out = out[:limit]
        return out

    # ---- level parity ----------------------------------------------------

    def _fighters(self, party) -> tuple:
        return tuple(m for m in self.members(party) if m.fights)

    def parity(self, party, tolerance=DEFAULT_TOLERANCE) -> dict:
        """``{min, max, mean, spread, laggards}`` over the non-egg members.

        A plain dict rather than a dataclass: this is a bag of numbers that
        goes straight into the live feed and the widget, unlike
        :class:`Coverage`, which carries per-type facts and accessors.

        **Eggs are excluded, and this is the important part.** An egg reads 0
        HP and 0 level (pokemon.py:96-98, 138-142), so counting one makes the
        party minimum 0, the spread the party maximum, and every real member a
        laggard forever. The predecessor project's heal rail looped infinitely
        on exactly this confusion.
        """
        self.last_parity_reason = None
        rows = self.members(party)
        members = tuple(m for m in rows if m.fights)
        eggs = sum(1 for m in rows if m.egg)
        if not members:
            self.last_parity_reason = (
                f"no non-egg party members with a species (of {len(rows)} "
                f"slot(s), {eggs} are eggs): there are no levels to compare"
            )
            return {
                "min": None, "max": None, "mean": None, "spread": None,
                "laggards": [], "count": 0, "eggs": eggs,
                "why": self.last_parity_reason,
            }

        levels = [m.level for m in members]
        lo, hi = min(levels), max(levels)
        mean = round(sum(levels) / len(levels), 2)
        return {
            "min": lo,
            "max": hi,
            "mean": mean,
            "spread": hi - lo,
            "laggards": self.needs_training(members, tolerance=tolerance),
            "count": len(members),
            "eggs": eggs,
            "why": (
                f"{len(members)} battle-capable member(s) at L{lo}-L{hi} "
                f"(mean {mean}), spread {hi - lo}"
                + (f"; {eggs} egg(s) excluded from every figure" if eggs else "")
            ),
        }

    def furthest_behind(self, party):
        """The healthy fighter with the lowest level, or None.

        Separate from :meth:`needs_training` on purpose. Training asks "is
        anyone so far back that we should go and grind for them", and the
        answer is usually no. ROTATION asks "who should walk in front", and the
        answer is almost always "whoever is furthest behind" -- there is no
        threshold, because the exp has to go somewhere and giving it to the
        leader is how a party of six becomes a party of one.

        Measured: with rotation gated on the training list, one mon went from
        L29 to L42 in fifty minutes while the other five sat at 27.
        """
        alive = [m for m in self._fighters(party) if m.alive and m.hp_frac > 0]
        return min(alive, key=lambda m: (m.level, m.index)) if alive else None

    def needs_training(self, party, tolerance=DEFAULT_TOLERANCE) -> list:
        """Members more than ``tolerance`` levels below the party MEDIAN.

        Not the maximum, and the difference is the whole behaviour. The
        starter is used more than anything else and drifts ahead: a party of
        26/26/26/27/27/35 has five members nine levels under the max, so every
        one of them is a laggard, forever. A run spent forty minutes grinding
        Route 102 in that state -- level-3 wilds, a level-26 party, and a gym
        it could already beat waiting two towns away.

        The median is what "all around the same level" actually means. It is
        robust to exactly one runaway, and training the bottom toward it
        converges; training everyone toward the top never terminates, because
        the top is also being trained.

        Furthest behind first, so the caller can just take the head. Each row
        carries its own ``why``, because "why is this mon being trained" is a
        question the operator will ask.
        """
        members = self._fighters(party)
        if not members:
            return []
        levels = sorted(m.level for m in members)
        mid = len(levels) // 2
        median = (levels[mid] if len(levels) % 2
                  else (levels[mid - 1] + levels[mid]) / 2)
        out = [
            {
                "index": m.index,
                "nickname": m.label,
                "level": m.level,
                "party_median": median,
                "party_max": max(levels),
                "gap": median - m.level,
                "hp_frac": round(m.hp_frac, 2),
                "alive": m.alive,
                "why": (
                    f"{m.label} is L{m.level}, {median - m.level:g} levels "
                    f"under the party's L{median:g} median and past the "
                    f"{tolerance}-level band"
                ),
            }
            for m in members
            if median - m.level > tolerance
        ]
        out.sort(key=lambda r: (-r["gap"], r["index"]))
        return out

    def training_policy(self, party=None, *, tolerance=DEFAULT_TOLERANCE,
                        safe_hp_frac=DEFAULT_SAFE_HP_FRAC,
                        enemy_gap=DEFAULT_ENEMY_GAP) -> TrainingPolicy:
        """A :meth:`pokeagent.battle.BattleSession.play` policy that feeds the
        KO to the party's laggards. See :class:`TrainingPolicy`."""
        return TrainingPolicy(
            self, party, tolerance=tolerance, safe_hp_frac=safe_hp_frac,
            enemy_gap=enemy_gap,
        )

    # ---- party order -----------------------------------------------------

    def rotation(self, party) -> dict:
        """Two NAMED party orders, never one silent choice.

        ``training`` puts the furthest-behind laggard in slot 0 so it is the
        sole participant from turn one and takes the whole exp share
        (src/battle_script_commands.c:3379-3396). ``gym`` puts the strongest
        healthy member first, because a gym leader's lead is the one fight
        where the exp does not matter and surviving does.

        Both orders are party INDEXES and both are advice: actually reordering
        the party means driving the party menu's SWITCH option, which is a
        separate act the caller performs deliberately.
        """
        members = self.members(party)
        fighters = [m for m in members if m.fights]
        eggs = [m.index for m in members if m.egg]
        empty = [m.index for m in members if not m.fights and not m.egg]

        lag = {r["index"] for r in self.needs_training(fighters)}

        def order(key):
            alive = sorted((m for m in fighters if m.alive), key=key)
            fainted = sorted((m for m in fighters if not m.alive), key=key)
            return [m.index for m in alive] + [m.index for m in fainted] \
                + eggs + empty

        training = order(lambda m: (m.index not in lag, m.level, m.index))
        gym = order(lambda m: (-m.level, -m.max_hp, m.index))

        lag_names = ", ".join(
            m.label for m in fighters if m.index in lag
        ) or "nobody"
        strongest = max(fighters, key=lambda m: m.level).label if fighters else "-"
        return {
            "training": {
                "order": training,
                "lead": training[0] if training else None,
                "why": (
                    f"laggards first ({lag_names}) so the one that needs exp is "
                    f"the sole participant from turn one; exp is divided by the "
                    f"number of participants, so a laggard that starts the "
                    f"battle keeps all of it"
                ),
            },
            "gym": {
                "order": gym,
                "lead": gym[0] if gym else None,
                "why": (
                    f"strongest healthy member first ({strongest}); a gym lead "
                    f"is the one fight where surviving beats farming exp"
                ),
            },
            "eggs_last": eggs,
        }

    # ---- reporting -------------------------------------------------------

    def report(self, party) -> str:
        """Plain text, because this is what gets pasted into a log when a run
        goes sideways."""
        cov = self.coverage(party)
        par = self.parity(party)
        rot = self.rotation(party)
        lines = [
            f"roundness {cov.roundness}/100 "
            f"(offense {cov.offense_fraction:.0%}, "
            f"defense {cov.defense_fraction:.0%})",
            f"  covers SE: {', '.join(cov.covered) or 'nothing'}",
            f"  offensive gaps ({len(cov.offense_gaps)}): "
            f"{', '.join(cov.offense_gaps) or 'none'}",
            f"  cannot damage at all: {', '.join(cov.no_effect) or 'nothing'}",
            f"  defensive holes ({len(cov.defense_holes)}): "
            f"{', '.join(cov.defense_holes) or 'none'}",
            f"parity: {par['why']}",
        ]
        for row in par["laggards"]:
            lines.append(f"  laggard {row['why']}")
        if not par["laggards"]:
            lines.append("  no laggards: everyone is inside the level band")
        lines.append(f"rotation training={rot['training']['order']} "
                     f"gym={rot['gym']['order']}")
        return "\n".join(lines)


def report(driver, tolerance=DEFAULT_TOLERANCE) -> dict:
    """The feed/widget view of the team, in one call.

    A convenience over `Team` for the publisher, which wants a flat bag of
    numbers rather than the analysis objects. Kept here so the shape the
    widget renders is defined next to the code that computes it.
    """
    t = Team(driver.names, driver.consts, driver.state)
    party = t.party()
    cov = t.coverage(party)
    return {
        "parity": t.parity(party, tolerance=tolerance),
        "coverage": {
            "roundness": round(cov.roundness, 1),
            "gaps": [t.type_name(i) for i in cov.gap_type_ids()],
            "holes": [t.type_name(i) for i in cov.hole_type_ids()],
            "offense_fraction": round(cov.offense_fraction, 3),
            "defense_fraction": round(cov.defense_fraction, 3),
        },
        "needs_training": t.needs_training(party, tolerance=tolerance),
    }
