"""Gen-3 damage math and battle tactics, computed from the game's own tables.

This is a line-for-line port of ``CalculateBaseDamage``
(src/calculate_base_damage.c:93-296) plus the three things the engine does to
its result afterwards, in order:

1. ``atk05_damagecalc`` multiplies by the crit multiplier
   (src/battle_script_commands.c:1410-1424);
2. ``atk06_typecalc`` applies STAB and then the type chart, one defender type
   at a time, truncating to an integer at every step
   (``TypeCalc``/``ModulateDmgByType2``, ibid.:1615-1705);
3. ``atk07_adjustnormaldamage`` applies the 85-100% roll
   (``ApplyRandomDmgMultiplier``, ibid.:1750-1763).

Why the port rather than an approximation: the order of the truncations is
load-bearing. ``damage / defense / 50`` floors twice before STAB, and the type
chart floors again per type, so a "close enough" float formula drifts by
several HP on the small numbers that decide early-game fights.

Three Gen-3 specifics that a Gen-2 port gets wrong:

* **Physical vs special is chosen by move TYPE, not per move.**
  ``IS_TYPE_PHYSICAL(t)`` is ``t < TYPE_MYSTERY`` (include/battle.h:500-501),
  so every NORMAL/FIGHTING/.../GHOST move is physical and every FIRE/WATER/...
  move is special, whatever the move looks like. ``TYPE_MYSTERY`` itself deals
  literally zero (calculate_base_damage.c:254-255).
* **Stat stages are 0..12 with 6 neutral**, not signed. The engine indexes
  ``gStatStageRatios[stage]`` directly, and that table lives in ROM -- we read
  it rather than retype it, because its accuracy twin has a four-byte stride
  where the source shows a two-byte struct.
* **Badge boosts are +10% and trainer-only.** ``BADGE_BOOST``
  (calculate_base_damage.c:81-91) is gated on ``BATTLE_TYPE_TRAINER``, so a
  wild fight gets none of it.

Nothing in this module is transcribed game data. Ids, base stats, move data,
the type chart, the stage tables, the hold-effect type table and item effects
all come out of the ROM or out of the decomp's headers. The one exception is
the ``STAT_STAGE_*`` block below, which is a C *enum* rather than a set of
``#define``s and so cannot go through :mod:`pokeagent.cconst`; it is cited
to the line it comes from, the same way :mod:`pokeagent.state` cites
``NUM_TASKS``.
"""

import logging
import re
from dataclasses import dataclass

from . import cstruct
from .names import TYPE_MUL_NORMAL

log = logging.getLogger("pokeagent.tactics")

#: ``enum``, include/pokemon.h:254-263. Not a #define, so cconst cannot see it.
STAT_STAGE_HP = 0
STAT_STAGE_ATK = 1
STAT_STAGE_DEF = 2
STAT_STAGE_SPEED = 3
STAT_STAGE_SPATK = 4
STAT_STAGE_SPDEF = 5
STAT_STAGE_ACC = 6
STAT_STAGE_EVASION = 7
#: include/pokemon.h:211 -- statStages[] length.
BATTLE_STATS_NO = 8

#: ApplyRandomDmgMultiplier: ``100 - Random() % 16`` (battle_script_commands.c:1754).
ROLL_MIN = 85
ROLL_MAX = 100

#: STAB, TypeCalc (battle_script_commands.c:1663-1664).
STAB_NUMERATOR = 15
STAB_DENOMINATOR = 10

#: A crit doubles the base damage (atk05_damagecalc, ibid.:1416).
CRIT_MULTIPLIER = 2

#: Turn-loss model, all from the attack canceller in src/battle_util.c:
#: sleep 1381-1424 and freeze 1425-1451 both return before the move runs (the
#: thaw branch sets ``effect = 2`` as well, so even unfreezing costs the turn);
#: confusion 1522-1551 is ``Random() & 1``; paralysis 1552-1562 is
#: ``Random() % 4 == 0``.
PARALYSIS_LOSS = 0.25
CONFUSION_LOSS = 0.50

#: ITEM6_HEAL_HP_FULL/HALF/LVL_UP, include/constants/item_effects.h:56-58.
#: Written there as ``((u8) -1)``, and :mod:`pokeagent.cconst` skips casts
#: on purpose (a cast is not an expression it can evaluate), so the three
#: sentinels are spelled out here rather than silently missing.
HEAL_HP_SENTINELS = {0xFF: "full", 0xFE: "half", 0xFD: "level"}

#: Fraction of max HP below which `recommend` starts looking for a potion.
DEFAULT_HEAL_AT = 0.35

#: Moves whose LISTED power the engine will not deliver without setup this run
#: never performs. They must be treated as STATUS moves by the damage model,
#: not as their headline number.
#:
#: SPIT UP reads 100 power and deals nothing without Stockpile. Measured in
#: Drake's room: nine consecutive turns of SPIT UP against an ALTARIA whose HP
#: never moved (63->63->63...), while it healed itself back to full twice --
#: the level-100 lead lost the fight without landing a hit, because a 100 in
#: the table outranked SURF's 95. CUT (power 2, an HM) came out for the same
#: reason once SPIT UP was spent.
CONDITIONAL_POWER = frozenset({"SPIT UP", "SWALLOW", "DREAM EATER",
                               "FOCUS PUNCH", "SNORE", "FAKE OUT"})


_NON_ALNUM = re.compile(r"[^A-Z0-9]")


def norm_item(name) -> str:
    """Canonical item key: uppercase alphanumerics only, ``é`` folded to E.

    Lets ``"POKE BALL"``, ``"Poke Ball"`` and the ROM's own ``"POKé BALL"``
    all name the same item.
    """
    return _NON_ALNUM.sub("", name.upper().replace("É", "E").replace("é", "E"))


def _u16(raw, off):
    return int.from_bytes(raw[off : off + 2], "little")


def _u32(raw, off):
    return int.from_bytes(raw[off : off + 4], "little")


@dataclass(slots=True)
class Combatant:
    """One side of a matchup, exactly as ``CalculateBaseDamage`` sees it.

    Constructed either from a live ``gBattleMons`` entry (:meth:`from_raw`)
    or by hand, which is what makes the damage math testable without booting
    a battle.
    """

    level: int
    hp: int
    max_hp: int
    types: tuple
    attack: int
    defense: int
    speed: int
    sp_attack: int
    sp_defense: int
    stat_stages: tuple = ()
    status1: int = 0
    status2: int = 0
    ability: int = 0
    item: int = 0
    species: int = 0
    name: str = "?"
    nickname: str = ""
    moves: tuple = ()
    pp: tuple = ()
    player_side: bool = False
    battler: int = 0
    side_status: int = 0

    def __post_init__(self):
        if not self.stat_stages:
            # DEFAULT_STAT_STAGE, include/constants/pokemon.h:145.
            self.stat_stages = (6,) * BATTLE_STATS_NO

    @property
    def fainted(self) -> bool:
        return self.hp <= 0

    def stage(self, index) -> int:
        return self.stat_stages[index]


class Tactics:
    """Damage/type analysis for the live battle, and a move recommendation.

    ``recommend`` never returns a bare choice: every branch carries the
    sentence that justifies it, because a harness decision the operator cannot
    audit is the failure mode the Crystal retrospective spends the most words
    on.
    """

    def __init__(self, emu, names, consts, state):
        self.emu = emu
        self.names = names
        self.consts = consts
        self.state = state

        self.battle_mon = cstruct.layout("BattlePokemon", "pokemon.h")
        self.battler_stride = self._battler_stride()
        self.max_battlers = consts.battle["MAX_BATTLERS_COUNT"]

        self.T = consts.ns("pokemon.h")
        p = self.T
        self.type_mystery = p["TYPE_MYSTERY"]
        self.min_stage = p["MIN_STAT_STAGE"]
        self.default_stage = p["DEFAULT_STAT_STAGE"]
        self.max_stage = p["MAX_STAT_STAGE"]
        self.stage_count = self.max_stage - self.min_stage + 1

        self.abilities = consts.ns("abilities.h")
        self.holds = consts.ns("hold_effects.h")
        self.item_fx = consts.ns("item_effects.h")
        self.move_effects = consts.ns("battle_move_effects.h")
        self.items = consts.items
        self.b = consts.battle

        #: ``last_*_reason`` is the project's contract: a falsy return always
        #: comes with the sentence explaining it.
        self.last_outlook_reason = None
        self._item_ids = None

        self._type_names = {}

        # Both stage tables live in ROM. gStatStageRatios is a packed
        # u8[13][2]; gAccuracyStageRatios is declared as the same two-u8
        # struct but the linker gives it a FOUR byte stride (verified against
        # the ROM: 33,100,0,0, 36,100,0,0, ...). Deriving both strides from
        # the symbol sizes is the whole reason `names.py` refuses to
        # transcribe strides, so we do the same here.
        self.stat_stage_ratios = self._read_ratio_table("gStatStageRatios")
        self.accuracy_stage_ratios = self._read_ratio_table("gAccuracyStageRatios")

        # gHoldEffectToType (calculate_base_damage.c:52-71): {hold effect: type}
        # for the seventeen type-boosting held items.
        self.hold_effect_to_type = self._read_hold_effect_types()

    # ---- table plumbing -------------------------------------------------

    def _battler_stride(self) -> int:
        """Bytes per ``gBattleMons`` entry.

        The symbol table gives EWRAM variables a zero size, so fall back to
        the struct's own last annotated field (``otId``, a u32) rather than
        writing 0x58 down.
        """
        size = self.emu.sym.size("gBattleMons")
        if size:
            return size // self.consts.battle["MAX_BATTLERS_COUNT"]
        return cstruct.size_of("BattlePokemon", "pokemon.h", last_field_size=4)

    def _read_ratio_table(self, symbol) -> tuple:
        """``[(dividend, divisor), ...]`` for the 13 stat stages."""
        size = self.emu.sym.size(symbol)
        if not size or size % self.stage_count:
            raise ValueError(
                f"{symbol} is {size:#x} bytes, not a whole number of "
                f"{self.stage_count} stage entries -- refusing to guess a stride"
            )
        stride = size // self.stage_count
        raw = self.emu.read(symbol, size)
        table = tuple(
            (raw[i * stride], raw[i * stride + 1]) for i in range(self.stage_count)
        )
        if any(d == 0 for _, d in table):
            raise ValueError(f"{symbol} has a zero divisor -- the read is wrong")
        return table

    def _read_hold_effect_types(self) -> dict:
        size = self.emu.sym.size("gHoldEffectToType")
        raw = self.emu.read("gHoldEffectToType", size)
        return {raw[i]: raw[i + 1] for i in range(0, size, 2)}

    def type_name(self, type_id) -> str:
        name = self._type_names.get(type_id)
        if name is None:
            name = self._type_names[type_id] = self.names.type(type_id)
        return name

    def types_text(self, types) -> str:
        return "/".join(dict.fromkeys(self.type_name(t) for t in types))

    # ---- the physical/special split -------------------------------------

    def is_physical(self, type_id) -> bool:
        """``IS_TYPE_PHYSICAL``, include/battle.h:500. By TYPE, not by move."""
        return type_id < self.type_mystery

    def is_special(self, type_id) -> bool:
        """``IS_TYPE_SPECIAL``, include/battle.h:501."""
        return type_id > self.type_mystery

    def category(self, type_id) -> str:
        if type_id == self.type_mystery:
            return "none"
        return "physical" if self.is_physical(type_id) else "special"

    # ---- reading the live battle ----------------------------------------

    def read_battler(self, index) -> Combatant:
        """One ``gBattleMons`` entry. Plaintext -- Gen 3's substructure
        encryption only covers the boxed ``struct Pokemon``, never the battle
        copy (src/battle_main.c, ``BattleStructToPokemon``)."""
        b = self.battle_mon
        raw = self.emu.read(
            ("gBattleMons", index * self.battler_stride), self.battler_stride
        )
        species = _u16(raw, b["species"])
        # B_SIDE_PLAYER is 0 and sides alternate by the low bit of the battler
        # index (GetBattlerSide, src/battle_util.c).
        player_side = (index % 2) == self.b["B_SIDE_PLAYER"]
        return Combatant(
            level=raw[b["level"]],
            hp=_u16(raw, b["hp"]),
            max_hp=_u16(raw, b["maxHP"]),
            types=(raw[b["type1"]], raw[b["type2"]]),
            attack=_u16(raw, b["attack"]),
            defense=_u16(raw, b["defense"]),
            speed=_u16(raw, b["speed"]),
            sp_attack=_u16(raw, b["spAttack"]),
            sp_defense=_u16(raw, b["spDefense"]),
            stat_stages=tuple(
                raw[b["statStages"] : b["statStages"] + BATTLE_STATS_NO]
            ),
            status1=_u32(raw, b["status1"]),
            status2=_u32(raw, b["status2"]),
            ability=raw[b["ability"]],
            item=_u16(raw, b["item"]),
            species=species,
            name=self.names.species(species) if species else "-",
            nickname=self.emu.charmap.decode(
                raw[b["nickname"] : b["nickname"] + 11]
            ),
            moves=tuple(_u16(raw, b["moves"] + i * 2) for i in range(4)),
            pp=tuple(raw[b["pp"] : b["pp"] + 4]),
            player_side=player_side,
            battler=index,
            side_status=self.emu.u16(("gSideStatuses", (index % 2) * 2)),
        )

    def badges(self) -> set:
        """Badge numbers the player has earned, as ints 1..8."""
        return {
            n for n in range(1, 9) if self.state.flag(f"FLAG_BADGE{n:02}_GET")
        }

    def badge_boost_applies(self) -> bool:
        """``BADGE_BOOST``'s gate, calculate_base_damage.c:81-91.

        Trainer battles only, no link/tower/e-reader, and not a secret base.
        """
        flags = self.emu.u16("gBattleTypeFlags")
        blocked = (
            self.b["BATTLE_TYPE_LINK"]
            | self.b["BATTLE_TYPE_BATTLE_TOWER"]
            | self.b["BATTLE_TYPE_EREADER_TRAINER"]
        )
        if flags & blocked:
            return False
        if not flags & self.b["BATTLE_TYPE_TRAINER"]:
            return False
        return self.emu.u16("gTrainerBattleOpponent") != self.b["SECRET_BASE_OPPONENT"]

    # ---- the formula ------------------------------------------------------

    def apply_stage(self, stat, stage) -> int:
        """``APPLY_STAT_MOD``, calculate_base_damage.c:77-81."""
        num, den = self.stat_stage_ratios[stage]
        return stat * num // den

    @staticmethod
    def _badge(stat, badge, *, player_side, boosting, badges) -> int:
        if not boosting or not player_side or badge not in badges:
            return stat
        return 110 * stat // 100

    def base_damage(
        self,
        attacker: Combatant,
        defender: Combatant,
        *,
        power: int,
        move_type: int,
        move_effect: int = 0,
        crit: bool = False,
        badges=None,
        badge_boost: bool = False,
        weather: int = 0,
    ) -> int:
        """``CalculateBaseDamage``, src/calculate_base_damage.c:93-296.

        The return value already carries the engine's ``+ 2`` tail. It does
        NOT carry the crit multiplier, STAB, the type chart or the damage
        roll -- those are applied by the callers the engine applies them in
        (:meth:`damage_span`).

        Double-battle spread halving is omitted deliberately: this harness
        only fights singles, and a multiplier that never fires is a lie
        waiting to be believed.
        """
        badges = self.badges() if badges is None else set(badges)
        boosting = badge_boost

        A = self.abilities
        H = self.holds

        attack, defense = attacker.attack, defender.defense
        sp_attack, sp_defense = attacker.sp_attack, defender.sp_defense

        atk_hold = self._hold_effect(attacker.item)
        atk_hold_param = self._hold_param(attacker.item)
        def_hold = self._hold_effect(defender.item)

        if attacker.ability in (A["ABILITY_HUGE_POWER"], A["ABILITY_PURE_POWER"]):
            attack *= 2

        # BADGE_BOOST(1, attack), (5, defense), (7, spAttack), (7, spDefense)
        attack = self._badge(attack, 1, player_side=attacker.player_side, boosting=boosting, badges=badges)
        defense = self._badge(defense, 5, player_side=defender.player_side, boosting=boosting, badges=badges)
        sp_attack = self._badge(sp_attack, 7, player_side=attacker.player_side, boosting=boosting, badges=badges)
        sp_defense = self._badge(sp_defense, 7, player_side=defender.player_side, boosting=boosting, badges=badges)

        # gHoldEffectToType: a type-matching held item boosts the relevant
        # offence by its param percent (calculate_base_damage.c:146-159).
        if self.hold_effect_to_type.get(atk_hold) == move_type:
            if self.is_physical(move_type):
                attack = attack * (atk_hold_param + 100) // 100
            else:
                sp_attack = sp_attack * (atk_hold_param + 100) // 100

        if atk_hold == H["HOLD_EFFECT_CHOICE_BAND"]:
            attack = 150 * attack // 100
        if atk_hold == H["HOLD_EFFECT_DEEP_SEA_TOOTH"] and attacker.species == self.consts.species["SPECIES_CLAMPERL"]:
            sp_attack *= 2
        if def_hold == H["HOLD_EFFECT_DEEP_SEA_SCALE"] and defender.species == self.consts.species["SPECIES_CLAMPERL"]:
            sp_defense *= 2
        if atk_hold == H["HOLD_EFFECT_LIGHT_BALL"] and attacker.species == self.consts.species["SPECIES_PIKACHU"]:
            sp_attack *= 2
        if def_hold == H["HOLD_EFFECT_METAL_POWDER"] and defender.species == self.consts.species["SPECIES_DITTO"]:
            defense *= 2
        if atk_hold == H["HOLD_EFFECT_THICK_CLUB"] and attacker.species in (
            self.consts.species["SPECIES_CUBONE"],
            self.consts.species["SPECIES_MAROWAK"],
        ):
            attack *= 2

        if defender.ability == A["ABILITY_THICK_FAT"] and move_type in (
            self.T["TYPE_FIRE"],
            self.T["TYPE_ICE"],
        ):
            sp_attack //= 2
        if attacker.ability == A["ABILITY_HUSTLE"]:
            attack = 150 * attack // 100
        if attacker.ability == A["ABILITY_GUTS"] and attacker.status1:
            attack = 150 * attack // 100
        if defender.ability == A["ABILITY_MARVEL_SCALE"] and defender.status1:
            defense = 150 * defense // 100

        power = self._pinch_ability_power(power, move_type, attacker)

        # EFFECT_EXPLOSION halves the defender's Defense (ibid.:204-205).
        if move_effect and move_effect == self.move_effects.get("EFFECT_EXPLOSION"):
            defense //= 2

        if move_type == self.type_mystery:
            # "is ??? type. does 0 damage." (calculate_base_damage.c:254-255)
            return 2

        if self.is_physical(move_type):
            damage = self._offence(attacker, attack, STAT_STAGE_ATK, crit)
            damage = damage * power
            damage *= 2 * attacker.level // 5 + 2
            helper = self._defence(defender, defense, STAT_STAGE_DEF, crit)
            damage = damage // helper
            damage //= 50
            if (attacker.status1 & self.b["STATUS1_BURN"]) and attacker.ability != A["ABILITY_GUTS"]:
                damage //= 2
            if (defender.side_status & self.b["SIDE_STATUS_REFLECT"]) and not crit:
                damage //= 2
            if damage == 0:
                damage = 1  # "moves always do at least 1 damage."
            return damage + 2

        damage = self._offence(attacker, sp_attack, STAT_STAGE_SPATK, crit)
        damage = damage * power
        damage *= 2 * attacker.level // 5 + 2
        helper = self._defence(defender, sp_defense, STAT_STAGE_SPDEF, crit)
        damage = damage // helper
        damage //= 50
        if (defender.side_status & self.b["SIDE_STATUS_LIGHTSCREEN"]) and not crit:
            damage //= 2
        damage = self._weather_damage(damage, move_type, weather)
        return damage + 2

    def _offence(self, mon, stat, stage_index, crit):
        """A crit ignores the attacker's own NEGATIVE offence stages
        (calculate_base_damage.c:209-217)."""
        if crit and mon.stage(stage_index) <= self.default_stage:
            return stat
        return self.apply_stage(stat, mon.stage(stage_index))

    def _defence(self, mon, stat, stage_index, crit):
        """A crit ignores the defender's POSITIVE defence stages (ibid.:221-229)."""
        if crit and mon.stage(stage_index) >= self.default_stage:
            return max(1, stat)
        return max(1, self.apply_stage(stat, mon.stage(stage_index)))

    def _pinch_ability_power(self, power, move_type, attacker):
        """Overgrow/Blaze/Torrent/Swarm: +50% power below a third of max HP
        (calculate_base_damage.c:196-203). Mudkip has TORRENT, so this fires
        in the very first gym."""
        A = self.abilities
        T = self.T
        pairs = (
            (T["TYPE_GRASS"], A["ABILITY_OVERGROW"]),
            (T["TYPE_FIRE"], A["ABILITY_BLAZE"]),
            (T["TYPE_WATER"], A["ABILITY_TORRENT"]),
            (T["TYPE_BUG"], A["ABILITY_SWARM"]),
        )
        for type_id, ability in pairs:
            if move_type == type_id and attacker.ability == ability and attacker.hp <= attacker.max_hp // 3:
                return 150 * power // 100
        return power

    def _weather_damage(self, damage, move_type, weather):
        """Rain/sun scaling on the special branch (calculate_base_damage.c:275-296)."""
        if not weather:
            return damage
        T = self.T
        if weather & self.b["B_WEATHER_RAIN"]:
            if move_type == T["TYPE_FIRE"]:
                return damage // 2
            if move_type == T["TYPE_WATER"]:
                return 15 * damage // 10
        if weather & self.b["B_WEATHER_SUN"]:
            if move_type == T["TYPE_FIRE"]:
                return 15 * damage // 10
            if move_type == T["TYPE_WATER"]:
                return damage // 2
        return damage

    def _hold_effect(self, item_id) -> int:
        if not item_id:
            return 0
        return self.names.item_data(item_id).hold_effect

    def _hold_param(self, item_id) -> int:
        """``ItemId_GetHoldEffectParam``: byte 0x13 of ``struct Item``.

        ``names.ItemData`` stops at ``hold_effect`` (0x12), so read the next
        byte straight out of the same row rather than duplicating the struct.
        """
        if not item_id:
            return 0
        stride = self.names.item_stride
        return self.emu.u8(("gItems", item_id * stride + 0x13))

    # ---- STAB, the type chart, and the roll -------------------------------

    def type_multiplier(self, move_type, defender: Combatant) -> float:
        """The chart product, as a float, for reporting."""
        chart = self.names.type_chart
        mul = chart.get((move_type, defender.types[0]), 1.0)
        if defender.types[1] != defender.types[0]:
            mul *= chart.get((move_type, defender.types[1]), 1.0)
        if defender.ability == self.abilities["ABILITY_LEVITATE"] and \
                move_type == self.T["TYPE_GROUND"]:
            return 0.0
        return mul

    def _apply_type_chart(self, damage, move_type, defender) -> int:
        """``TypeCalc``'s loop, integer-exact.

        Each matching row does ``damage = damage * multiplier / 10`` and then
        floors, with a floor of 1 for any non-zero multiplier
        (ModulateDmgByType2, battle_script_commands.c:1615-1619). Applying the
        product in one go gives a different answer on odd numbers, which is
        why this walks the types the way the engine does.
        """
        if defender.ability == self.abilities["ABILITY_LEVITATE"] and \
                move_type == self.T["TYPE_GROUND"]:
            return 0
        chart = self.names.type_chart
        seen = []
        for def_type in (defender.types[0], defender.types[1]):
            if def_type in seen:
                continue
            seen.append(def_type)
            mult = chart.get((move_type, def_type))
            if mult is None:
                continue
            raw = round(mult * TYPE_MUL_NORMAL)
            damage = damage * raw // TYPE_MUL_NORMAL
            if damage == 0 and raw != 0:
                damage = 1
        return damage

    def damage_span(
        self,
        attacker: Combatant,
        defender: Combatant,
        move_id: int,
        *,
        crit: bool = False,
        badges=None,
        badge_boost: bool = False,
        weather: int = 0,
        power: int | None = None,
        move_type: int | None = None,
    ) -> tuple:
        """``(min, max)`` damage for one hit of ``move_id``.

        Runs the full engine pipeline: base damage -> crit -> STAB -> type
        chart -> the 85-100% roll.
        """
        md = self.names.move_data(move_id)
        power = md.power if power is None else power
        move_type = md.type if move_type is None else move_type
        if power == 0:
            return (0, 0)

        damage = self.base_damage(
            attacker,
            defender,
            power=power,
            move_type=move_type,
            move_effect=md.effect,
            crit=crit,
            badges=badges,
            badge_boost=badge_boost,
            weather=weather,
        )
        if crit:
            damage *= CRIT_MULTIPLIER
        if move_type in attacker.types:
            damage = damage * STAB_NUMERATOR // STAB_DENOMINATOR
        damage = self._apply_type_chart(damage, move_type, defender)
        if damage == 0:
            return (0, 0)
        return (self._roll(damage, ROLL_MIN), self._roll(damage, ROLL_MAX))

    @staticmethod
    def _roll(damage, percent) -> int:
        out = damage * percent // 100
        return out or 1

    # ---- accuracy ----------------------------------------------------------

    def effective_accuracy(self, attacker: Combatant, defender: Combatant, move_id) -> int:
        """Listed accuracy after the live accuracy/evasion STAGES.

        ``atk01_accuracycheck``, battle_script_commands.c:1251-1300:
        ``buff = attackerACC + 6 - defenderEVASION``, clamped to 0..12, then
        ``accuracy * gAccuracyStageRatios[buff]``. Compound Eyes and Sand Veil
        are in the same block and are cheap to honour.

        A move with a listed accuracy of 0 never rolls at all
        (``AccuracyCalcHelper``), which is how SWIFT and FAINT ATTACK ignore a
        DOUBLE TEAM stack.
        """
        md = self.names.move_data(move_id)
        if md.accuracy == 0:
            return 100
        buff = (
            attacker.stage(STAT_STAGE_ACC)
            + self.default_stage
            - defender.stage(STAT_STAGE_EVASION)
        )
        buff = max(self.min_stage, min(self.max_stage, buff))
        num, den = self.accuracy_stage_ratios[buff]
        calc = num * md.accuracy // den
        if attacker.ability == self.abilities["ABILITY_COMPOUND_EYES"]:
            calc = calc * 130 // 100
        if attacker.ability == self.abilities["ABILITY_HUSTLE"] and self.is_physical(md.type):
            calc = calc * 80 // 100
        return max(0, min(100, calc))

    # ---- speed --------------------------------------------------------------

    def effective_speed(self, mon: Combatant, *, badges=()) -> int:
        """``GetWhoStrikesFirst``'s adjusted speed, src/battle_main.c:4615-4641.

        Stage ratio, then BADGE03's +10% for the player, then paralysis' /4.
        Two differences from Crystal worth naming: paralysis is NOT already
        baked into the stat word here (the engine divides at comparison time),
        and the badge speed boost is gated only on ``BATTLE_TYPE_LINK`` -- so
        unlike the damage badge boosts it applies in WILD battles too.
        """
        speed = self.apply_stage(mon.speed, mon.stage(STAT_STAGE_SPEED))
        if mon.player_side and 3 in badges:
            speed = speed * 110 // 100
        if mon.status1 & self.b["STATUS1_PARALYSIS"]:
            speed //= 4
        return speed

    # ---- status -------------------------------------------------------------

    def status_names(self, mon: Combatant) -> list:
        out = []
        s1, s2 = mon.status1, mon.status2
        if s1 & self.b["STATUS1_SLEEP"]:
            out.append(f"SLP({s1 & self.b['STATUS1_SLEEP']})")
        if s1 & self.b["STATUS1_TOXIC_POISON"]:
            out.append("TOX")
        elif s1 & self.b["STATUS1_POISON"]:
            out.append("PSN")
        if s1 & self.b["STATUS1_BURN"]:
            out.append("BRN")
        if s1 & self.b["STATUS1_FREEZE"]:
            out.append("FRZ")
        if s1 & self.b["STATUS1_PARALYSIS"]:
            out.append("PAR")
        if s2 & self.b["STATUS2_CONFUSION"]:
            out.append("CNF")
        if s2 & self.b["STATUS2_INFATUATION"]:
            out.append("ATR")
        return out

    def turn_loss(self, mon: Combatant) -> float:
        """Share of this side's turns its status is expected to eat.

        Sleep and freeze both return from the attack canceller before the move
        runs, and the thaw branch sets ``effect = 2`` too -- so the turn is
        gone either way (src/battle_util.c:1381-1451). Paralysis is a flat 25%
        and confusion a 50% self-hit, compounding.

        Paralysis' speed penalty is deliberately NOT here: unlike Crystal, the
        Gen-3 engine applies it at comparison time, so :meth:`effective_speed`
        owns it.
        """
        s1, s2 = mon.status1, mon.status2
        if s1 & (self.b["STATUS1_SLEEP"] | self.b["STATUS1_FREEZE"]):
            return 1.0
        act = 1.0
        if s1 & self.b["STATUS1_PARALYSIS"]:
            act *= 1.0 - PARALYSIS_LOSS
        if s2 & self.b["STATUS2_CONFUSION"]:
            act *= 1.0 - CONFUSION_LOSS
        return round(1.0 - act, 3)

    def turn_eating_status(self, mon: Combatant) -> int:
        """The ITEM3_* mask an item must carry to give this mon its turns back.

        PSN/BRN cost HP, not turns, so they are the potion branch's problem
        and are deliberately absent.
        """
        mask = 0
        if mon.status1 & self.b["STATUS1_SLEEP"]:
            mask |= self.item_fx["ITEM3_SLEEP"]
        if mon.status1 & self.b["STATUS1_FREEZE"]:
            mask |= self.item_fx["ITEM3_FREEZE"]
        if mon.status1 & self.b["STATUS1_PARALYSIS"]:
            mask |= self.item_fx["ITEM3_PARALYSIS"]
        if mon.status2 & self.b["STATUS2_CONFUSION"]:
            mask |= self.item_fx["ITEM3_CONFUSION"]
        return mask

    # ---- item effects --------------------------------------------------------

    def item_effect(self, item_id) -> bytes:
        """The ``gItemEffectTable`` row for an item, or ``b""``.

        The table is indexed by ``itemId - ITEM_POTION`` (src/pokemon_3.c:94)
        and holds pointers; a null pointer means "no table-based effect".
        Eight bytes is six flag fields plus the first two arguments, which is
        everything a battle decision reads.
        """
        first = self.items["ITEM_POTION"]
        index = item_id - first
        count = self.emu.sym.size("gItemEffectTable") // 4
        if not 0 <= index < count:
            return b""
        ptr = self.emu.u32(("gItemEffectTable", index * 4))
        if not ptr:
            return b""
        return self.emu.read(ptr, 8)

    def item_cures(self, item_id) -> int:
        """ITEM3 status mask this item clears."""
        fx = self.item_effect(item_id)
        if len(fx) < 4:
            return 0
        return fx[3] & self.item_fx["ITEM3_STATUS_ALL"]

    def item_heal_hp(self, item_id):
        """``(heals, amount)``: does it restore HP, and how much?

        ``amount`` is ``"full"``/``"half"``/``"level"`` for the three magic
        values (include/constants/item_effects.h:55-58), an int for a plain
        number, or ``None`` when the argument does not sit at
        ``ITEM_EFFECT_ARG_START``. It only moves when the item ALSO carries an
        EV-boost flag earlier in byte 4, which no HP restorer does -- but
        reporting ``None`` beats reporting a number scraped from the wrong
        offset.
        """
        fx = self.item_effect(item_id)
        if len(fx) < 7 or not fx[4] & self.item_fx["ITEM4_HEAL_HP"]:
            return (False, None)
        if fx[4] & self.item_fx["ITEM4_REVIVE"]:
            return (False, None)   # a revive is not a mid-battle heal
        earlier = fx[4] & (self.item_fx["ITEM4_EV_HP"] | self.item_fx["ITEM4_EV_ATK"])
        if earlier:
            return (True, None)
        amount = fx[self.item_fx["ITEM_EFFECT_ARG_START"]]
        return (True, HEAL_HP_SENTINELS.get(amount, amount))

    def _bag_item_ids(self, bag) -> list:
        """``[(item_id, name, quantity)]`` for the pockets a battle can use."""
        out = []
        wanted = ("items", "poke_balls", "berries")
        for pocket in wanted:
            for name, qty in (bag.get(pocket) or {}).items():
                item_id = self.item_id(name)
                if item_id:
                    out.append((item_id, name, qty))
        return out

    def item_id(self, name):
        """Item id for a name, matched loosely. ``None`` when unknown.

        The ROM spells it ``POKé BALL``; every human, policy and doc writes
        ``POKE BALL``. Matching the decoded string exactly turns that into
        "not an item this ROM knows about", which is the lookup-miss class
        that cost the predecessor real calls (its journal #23 and #30). So
        both sides are folded through :func:`norm_item` first.
        """
        if self._item_ids is None:
            count = self.emu.sym.size("gItems") // self.names.item_stride
            self._item_ids = {}
            for i in range(count):
                try:
                    self._item_ids.setdefault(norm_item(self.names.item(i)), i)
                except Exception:  # pragma: no cover - a torn ROM read
                    continue
        return self._item_ids.get(norm_item(name))

    def pick_ball(self, balls):
        """Cheapest stocked ball by ROM price, reserving MASTER BALL for callers."""
        best, best_price = None, None
        for name, quantity in balls.items():
            if not isinstance(quantity, int) or quantity <= 0:
                continue
            item_id = self.item_id(name)
            if not item_id or item_id == self.items["ITEM_MASTER_BALL"]:
                continue
            price = self.names.item_data(item_id).price
            if best_price is None or price < best_price:
                best, best_price = name, price
        return best

    # ---- one move ------------------------------------------------------------

    def move_view(
        self,
        attacker: Combatant,
        defender: Combatant,
        move_id: int,
        slot: int,
        pp: int | None,
        *,
        badge_boost: bool,
        badges=(),
        weather: int = 0,
    ) -> dict:
        """Everything a decision needs about one move, carrying its SLOT.

        The slot is the engine's own move index, and it travels with the row
        forever after. The Crystal harness returned sorted rows without it and
        the caller used the list position -- which picked LEER over a KO twice
        and cost a whiteout (its journal #22). Sorting must never be able to
        change which move gets pressed.
        """
        if not move_id:
            return {
                "slot": slot, "id": 0, "name": "-", "type": None,
                "type_name": "-", "power": 0, "pp": pp, "accuracy": 0,
                "effective_accuracy": 0, "effectiveness": 1.0, "stab": False,
                "category": "none", "kind": "empty",
                "damage_min": 0, "damage_max": 0, "pct_of_their_hp": 0.0,
                "hits_to_ko": None, "ko_certain": False, "ko_possible": False,
                "note": "empty slot",
            }
        md = self.names.move_data(move_id)
        mult = self.type_multiplier(md.type, defender)
        lo, hi = self.damage_span(
            attacker, defender, move_id,
            badge_boost=badge_boost, badges=badges, weather=weather,
        )
        acc = self.effective_accuracy(attacker, defender, move_id)
        hp = max(1, defender.hp)
        conditional = (md.name or "").upper() in CONDITIONAL_POWER
        if md.power == 0 or conditional:
            kind = "status"
            note = ("no damage: needs setup this run never does"
                    if conditional else "no damage: status move")
        elif mult == 0:
            kind, note = "immune", (
                f"{self.type_name(md.type)} does not affect "
                f"{self.types_text(defender.types)}"
            )
        else:
            kind, note = "attack", ""
        if pp == 0:
            note = (note + "; " if note else "") + "no PP left"
        return {
            "slot": slot,
            "id": move_id,
            "name": md.name,
            "type": md.type,
            "type_name": self.type_name(md.type),
            "power": 0 if conditional else md.power,
            "pp": pp,
            "accuracy": md.accuracy if md.accuracy else 100,
            "effective_accuracy": acc,
            "effectiveness": mult,
            "stab": md.type in attacker.types,
            "category": self.category(md.type),
            "kind": kind,
            "damage_min": 0 if conditional else lo,
            "damage_max": 0 if conditional else hi,
            "pct_of_their_hp": round(100 * hi / hp, 1),
            "hits_to_ko": None if lo <= 0 else -(-defender.hp // lo),
            "ko_certain": lo >= defender.hp > 0,
            "ko_possible": hi >= defender.hp > 0,
            "priority": md.priority,
            "note": note,
        }

    @staticmethod
    def score(view) -> float:
        """Expected damage, with a certain KO worth more than any overkill.

        Among certain KOs the RELIABLE one wins: a whiff hands the turn back,
        and the effective accuracy is what a listed 100% is actually worth
        against a DOUBLE TEAM stack.
        """
        if view.get("pp") == 0 or view["kind"] in ("empty", "immune", "status"):
            return -1.0 if view.get("pp") == 0 else 0.0
        hit = view["effective_accuracy"] / 100
        if view["ko_certain"]:
            return 2000 + 100 * hit
        return view["damage_min"] * hit

    # ---- the whole battle -------------------------------------------------------

    def outlook(self) -> dict | None:
        """Full analysis of the CURRENT battle, or ``None`` with a reason.

        ``None`` happens for real: ``gBattleMons`` is filled a few frames after
        ``gBattleTypeFlags`` goes non-zero, and reporting the blank L0 0/0 mon
        that sits there in the meantime invents a matchup that is not on
        screen. :attr:`last_outlook_reason` always says which it was.
        """
        self.last_outlook_reason = None
        if not self.state.in_battle():
            self.last_outlook_reason = "no battle is active"
            return None

        me = self.read_battler(0)
        enemy = self.read_battler(1)
        if not me.max_hp or not enemy.max_hp or not me.level:
            self.last_outlook_reason = (
                "gBattleMons is not populated yet (me "
                f"L{me.level} {me.hp}/{me.max_hp}, enemy "
                f"L{enemy.level} {enemy.hp}/{enemy.max_hp}) -- the battle "
                "type flags beat the mon blocks by a few frames"
            )
            return None

        badges = self.badges()
        badge_boost = self.badge_boost_applies()
        weather = self.emu.u16("gBattleWeather")
        flags = self.emu.u16("gBattleTypeFlags")
        # The BADGE03 speed boost is gated only on BATTLE_TYPE_LINK, unlike
        # the damage boosts (GetWhoStrikesFirst, src/battle_main.c:4631).
        speed_badges = () if flags & self.b["BATTLE_TYPE_LINK"] else badges

        by_slot = [
            self.move_view(me, enemy, mid, slot, pp, badge_boost=badge_boost,
                           badges=badges, weather=weather)
            for slot, (mid, pp) in enumerate(zip(me.moves, me.pp))
        ]
        moves = sorted(by_slot, key=self.score, reverse=True)
        threats = sorted(
            (
                self.move_view(enemy, me, mid, slot, None, badge_boost=badge_boost,
                               badges=badges, weather=weather)
                for slot, mid in enumerate(enemy.moves)
            ),
            key=lambda v: v["damage_max"],
            reverse=True,
        )

        my_speed = self.effective_speed(me, badges=speed_badges)
        their_speed = self.effective_speed(enemy, badges=speed_badges)
        best = moves[0] if moves else None
        worst = threats[0] if threats else None

        party = self.state.party()
        active_index = self.emu.u16(("gBattlerPartyIndexes", 0))

        return {
            "me": me,
            "enemy": enemy,
            "moves": moves,
            "moves_by_slot": by_slot,
            "threats": threats,
            "my_speed": my_speed,
            "their_speed": their_speed,
            "faster": my_speed > their_speed,
            "speed_tie": my_speed == their_speed,
            "my_best": best,
            "their_best": worst,
            "my_status": self.status_names(me),
            "their_status": self.status_names(enemy),
            "turn_loss": self.turn_loss(me),
            "their_turn_loss": self.turn_loss(enemy),
            "i_can_ko": bool(best and best["ko_certain"]),
            "i_die_next_turn": bool(worst and worst["damage_min"] >= me.hp),
            "turns_i_need": best["hits_to_ko"] if best else None,
            "turns_they_need": (
                None if not worst or worst["damage_max"] <= 0
                else -(-me.hp // max(1, worst["damage_min"] or worst["damage_max"]))
            ),
            "wild": not bool(flags & self.b["BATTLE_TYPE_TRAINER"]),
            "badge_boost": badge_boost,
            "weather": weather,
            "bag": self.state.bag(),
            "party": party,
            "active_party_index": active_index,
        }

    # ---- switching -------------------------------------------------------------

    def _already_on_the_field(self, mon, index, analysis) -> bool:
        """Is this candidate the mon already standing there?

        The obvious test is `index == active_party_index`, and it is not
        enough. `state.party()` and the engine's `gBattlerPartyIndexes` can
        disagree about WHERE a mon sits -- promoting a trainee reorders
        `gPlayerParty`, and a battle that began before the swap keeps the old
        index. Live, that produced a recommendation to "switch to ROCKY" while
        ROCKY was the mon taking the damage: the driver confirmed the SHIFT,
        the engine ignored it because there was nothing to change, and the
        battle burned six turns before the stall guard dropped the policy.
        Every battle where a switch looked good paid that toll.

        So the index is checked AND who the mon actually is. Nickname, species
        and level together are enough: two party members can share any one of
        them, but a mon is never a different mon from itself.

        The index exclusion stays conditional on the active mon still
        standing, because when it faints the engine reorders the party and
        that index starts pointing at a healthy benched mon -- excluding it
        blindly emptied this list at the one moment a replacement was needed.
        """
        me = analysis["me"]
        if me is None:
            return False
        if index == analysis["active_party_index"] and me.hp > 0:
            return True
        if me.hp <= 0:
            return False
        return (
            mon.species == me.species
            and mon.level == me.level
            and (mon.nickname or "") == (me.nickname or "")
        )

    def _enemy_types(self, analysis):
        """The active foe's types, or () when the battle block is not ready.

        Deliberately tolerant: an unreadable foe must make `can_damage`
        optimistic rather than pessimistic, because refusing to switch is the
        expensive mistake here.
        """
        enemy = analysis.get("enemy")
        if enemy is None:
            return ()
        try:
            stats = self.names.base_stats(enemy.species)
        except Exception:  # noqa: BLE001
            return ()
        return tuple(dict.fromkeys((stats.type1, stats.type2)))

    def switch_options(self, analysis) -> list:
        """Bench mons ranked as answers to the enemy's best move.

        A candidate is alive, not an egg, and not the mon already standing on
        the field. Types come from ``gBaseStats``, so the incoming move is
        scored against the real matchup rather than a guess.

        Who counts as "already on the field" is `_already_on_the_field`,
        which checks identity as well as index -- the two can disagree.
        """
        their = analysis.get("their_best")
        out = []
        for index, mon in enumerate(analysis.get("party") or []):
            if mon.is_egg or not mon.hp:
                continue
            if self._already_on_the_field(mon, index, analysis):
                continue
            stats = self.names.base_stats(mon.species)
            types = (stats.type1, stats.type2)
            incoming = 1.0
            if their and their.get("type") is not None and their["power"]:
                chart = self.names.type_chart
                incoming = chart.get((their["type"], types[0]), 1.0)
                if types[1] != types[0]:
                    incoming *= chart.get((their["type"], types[1]), 1.0)
            hp_frac = mon.hp / max(1, mon.max_hp)
            # Can this mon actually hurt what is standing there? A bench full
            # of mons whose damaging moves are out of PP is the situation this
            # exists to detect, so PP is part of the answer, not just the
            # moveset.
            can_damage = False
            for move_id, pp in zip(mon.moves, mon.pp):
                if not move_id or not pp:
                    continue
                md = self.names.move_data(move_id)
                if not md.power:
                    continue
                mult = 1.0
                if their_types := self._enemy_types(analysis):
                    chart = self.names.type_chart
                    mult = chart.get((md.type, their_types[0]), 1.0)
                    if len(their_types) > 1 and their_types[1] != their_types[0]:
                        mult *= chart.get((md.type, their_types[1]), 1.0)
                if mult > 0:
                    can_damage = True
                    break
            out.append({
                "index": index,
                "nickname": mon.nickname or self.names.species(mon.species),
                "types": tuple(self.type_name(t) for t in dict.fromkeys(types)),
                "incoming_mult": incoming,
                "hp_frac": round(hp_frac, 2),
                "can_damage": can_damage,
                "score": round((2.0 - incoming) * hp_frac
                               + (1.0 if can_damage else 0.0), 3),
            })
        return sorted(out, key=lambda m: m["score"], reverse=True)

    # ---- the decision ------------------------------------------------------------

    def recommend(self, analysis, *, heal_at=DEFAULT_HEAL_AT) -> tuple:
        """``(action, why)`` for this turn.

        Order, and the reason for each rung:

        1. **A certain KO**, because a dead enemy deals no damage. Ties break
           on EFFECTIVE accuracy -- the number after the evasion stack, not the
           one printed in the move list.
        2. **Heal**, when I am low and nothing is about to kill me anyway.
        3. **Cure a turn-eating status** (SLP/FRZ/PAR/CNF) with the cheapest
           bag item that covers all of it. PSN/BRN cost HP, not turns, so rung
           2 already answers them.
        4. **Switch** to a mon that resists the incoming move, when that move
           would otherwise kill me and I cannot KO first.
        5. **Best expected damage.**

        ``action`` is one of ``("attack", slot)``, ``("item", name)``,
        ``("switch", party_index)`` or ``"flee"``.
        """
        me = analysis["me"]
        their = analysis.get("their_best")
        usable = [m for m in analysis["moves"] if m["pp"] != 0 and m["kind"] != "empty"]
        live = [m for m in usable if m["damage_max"] > 0]

        kos = [m for m in live if m["ko_certain"]]
        if kos:
            pick = max(
                kos,
                key=lambda m: (m["effective_accuracy"], m["damage_min"]),
            )
            acc = self._accuracy_text(pick)
            return ("attack", pick["slot"]), (
                f"{pick['name']} (slot {pick['slot']}) KOs now: "
                f"{pick['damage_min']}-{pick['damage_max']} vs "
                f"{analysis['enemy'].hp} HP, x{pick['effectiveness']:g}, {acc}"
            )

        lethal = bool(their and their["damage_min"] >= me.hp)
        hurt = me.hp <= heal_at * me.max_hp
        if hurt and not lethal:
            heal = self._cheapest_heal(analysis)
            if heal:
                item, price, amount, sufficient = heal
                span = "" if amount is None else f" (restores {amount})"
                how = (
                    "the cheapest heal that covers the shortfall"
                    if sufficient
                    else "the biggest heal in the bag -- nothing here covers "
                         "the whole shortfall"
                )
                return ("item", item), (
                    f"{me.hp}/{me.max_hp} HP and nothing lethal is incoming; "
                    f"{item}{span} is {how} ({price} money)"
                )

        cure_mask = self.turn_eating_status(me)
        if cure_mask and not lethal:
            cure = self._cheapest_cure(analysis, cure_mask)
            if cure:
                item, price = cure
                names = "/".join(analysis["my_status"]) or "status"
                loss = analysis["turn_loss"]
                return ("item", item), (
                    f"{names} costs me ~{loss:.0%} of my turns and nothing "
                    f"lethal is incoming; {item} clears it for {price} money "
                    f"(cheapest item in the bag that covers all of it)"
                )

        if lethal and not analysis["i_can_ko"]:
            options = [
                o for o in self.switch_options(analysis)
                if o["incoming_mult"] < 1.0
            ]
            if options:
                best = options[0]
                return ("switch", best["index"]), (
                    f"{their['name']} does {their['damage_min']}-"
                    f"{their['damage_max']} to my {me.hp} HP and nothing KOs "
                    f"first; {best['nickname']} "
                    f"({'/'.join(best['types'])}) takes only "
                    f"x{best['incoming_mult']:g} of it"
                )

        if not live:
            # A mon with no damaging move LEFT cannot win this battle, and a
            # status move does not change that -- it only spends turns. The
            # observed cost: a lead whose every damaging move had run out of
            # PP used HARDEN three times per encounter, got retired for
            # changing nothing, and was switched out anyway. Switching FIRST
            # skips all of it, and a bench mon that can actually damage the
            # thing in front is strictly better than a turn that cannot.
            bench = [
                m for m in self.switch_options(analysis)
                if m.get("can_damage")
            ]
            if bench:
                pick = bench[0]
                return ("switch", pick["index"]), (
                    f"nothing left in this moveset damages "
                    f"{analysis['enemy'].name} (out of PP on every damaging "
                    f"move); {pick['nickname']} still can"
                )
            status = [m for m in usable if m["kind"] == "status"]
            if status and not analysis["wild"]:
                # Trainer battles cannot be fled, so a status move is the
                # least-bad remaining option there. In a WILD battle fleeing
                # is strictly better than stalling.
                pick = status[0]
                return ("attack", pick["slot"]), (
                    f"nothing in this moveset damages "
                    f"{analysis['enemy'].name} and this is a trainer battle; "
                    f"{pick['name']} (slot {pick['slot']}) at least does "
                    f"something"
                )
            if analysis["wild"]:
                return "flee", (
                    f"no usable move can touch {analysis['enemy'].name} "
                    f"(moveset: {', '.join(m['name'] for m in usable) or 'none'})"
                )
            return ("attack", 0), (
                "no usable move can touch it and a trainer battle cannot be "
                "fled; falling through to slot 0 to keep the turn moving"
            )

        pick = max(live, key=self.score)
        why = (
            f"{pick['name']} (slot {pick['slot']}) x{pick['effectiveness']:g} "
            f"{pick['damage_min']}-{pick['damage_max']} "
            f"({pick['pct_of_their_hp']}% of its HP), "
            f"{pick['hits_to_ko']} hit(s) to KO, {self._accuracy_text(pick)}"
        )
        if lethal:
            why += f" -- but {their['name']} can kill me first"
        return ("attack", pick["slot"]), why

    @staticmethod
    def _accuracy_text(view) -> str:
        eff, listed = view["effective_accuracy"], view["accuracy"]
        if eff == listed:
            return f"{eff}% acc"
        return f"{eff}% acc (listed {listed}%)"

    def _cheapest_heal(self, analysis):
        """``(name, price, amount, sufficient)``: the cheapest bag item that
        actually puts the missing HP back.

        Cheapest-that-suffices beats plain cheapest, because a 10 HP berry
        used at 4/20 spends a whole turn and leaves you still in range -- the
        predecessor logged three consecutive "heals" that restored nothing
        while Rollout ramped. When nothing in the bag suffices, fall back to
        the biggest heal available and say so.
        """
        me = analysis["me"]
        missing = max(0, me.max_hp - me.hp)
        picks = []
        for item_id, name, _qty in self._bag_item_ids(analysis["bag"]):
            data = self.names.item_data(item_id)
            if not data.battle_usage:
                continue
            heals, amount = self.item_heal_hp(item_id)
            if not heals:
                continue
            # "full"/"half"/"level" and an unknown offset all count as enough:
            # only a plain number can be measured against the shortfall.
            restored = amount if isinstance(amount, int) else me.max_hp
            picks.append((data.price, name, amount, restored))
        if not picks:
            return None
        enough = [p for p in picks if p[3] >= missing]
        if enough:
            price, name, amount, _ = min(enough, key=lambda p: (p[0], p[1]))
            return (name, price, amount, True)
        price, name, amount, _ = max(picks, key=lambda p: (p[3], -p[0]))
        return (name, price, amount, False)

    def _cheapest_cure(self, analysis, mask):
        """``(name, price)`` for the cheapest bag item covering the whole mask."""
        picks = []
        for item_id, name, _qty in self._bag_item_ids(analysis["bag"]):
            data = self.names.item_data(item_id)
            if not data.battle_usage:
                continue
            if self.item_cures(item_id) & mask != mask:
                continue
            picks.append((data.price, name))
        if not picks:
            return None
        price, name = min(picks, key=lambda p: (p[0], p[1]))
        return (name, price)

    # ---- the audit table ------------------------------------------------------------

    def explain(self, analysis) -> str:
        """One line per move, plus the enemy's answers. Plain text on purpose:
        this is what gets pasted into a log when a battle goes wrong."""
        me, en = analysis["me"], analysis["enemy"]
        order = (
            "I move first" if analysis["faster"]
            else "speed tie" if analysis["speed_tie"]
            else "they move first"
        )
        lines = [
            f"me {me.nickname or me.name} L{me.level} {me.hp}/{me.max_hp} "
            f"{self.types_text(me.types)} spd {analysis['my_speed']}"
            f"{self._state_text(me, analysis['my_status'])}"
            f"   vs   {en.name} L{en.level} {en.hp}/{en.max_hp} "
            f"{self.types_text(en.types)} spd {analysis['their_speed']}"
            f"{self._state_text(en, analysis['their_status'])}"
            f"   [{order}]"
        ]
        for v in analysis["moves"]:
            if v["kind"] == "empty":
                continue
            flag = "KO " if v["ko_certain"] else "ko?" if v["ko_possible"] else "   "
            listed = (
                "" if v["effective_accuracy"] == v["accuracy"]
                else f" (listed {v['accuracy']})"
            )
            lines.append(
                f"  [{v['slot']}] {flag} {v['name']:<13s} {v['type_name']:<9s}"
                f" {v['category'][:4]:<4s} x{v['effectiveness']:<4g}"
                f" {v['damage_min']:>3d}-{v['damage_max']:<3d}"
                f" ({v['pct_of_their_hp']:>5.1f}%)"
                f" acc {v['effective_accuracy']:>3d}{listed}"
                f"{'  STAB' if v['stab'] else '      '}"
                f" pp {v['pp'] if v['pp'] is not None else '?'}"
                + (f"  {v['note']}" if v["note"] else "")
            )
        for v in analysis["threats"]:
            if v["kind"] == "empty":
                continue
            lines.append(
                f"   <- {v['name']:<13s} {v['type_name']:<9s}"
                f" x{v['effectiveness']:<4g}"
                f" {v['damage_min']:>3d}-{v['damage_max']:<3d} on me"
                + ("   LETHAL" if v["damage_min"] >= me.hp else "")
            )
        return "\n".join(lines)

    def _state_text(self, mon, names) -> str:
        if not names:
            return ""
        loss = self.turn_loss(mon)
        return f" {'/'.join(names)}" + (f" -{loss:.0%} turns" if loss else "")
