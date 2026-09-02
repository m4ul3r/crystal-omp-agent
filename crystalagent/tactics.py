"""Type- and damage-aware battle tactics: real Gen-2 damage math over the
harness's live battle state.

Nothing here is hardcoded game knowledge; every rule is read out of the
disassembly:

- ``constants/type_constants.asm:26``  ``DEF SPECIAL EQU const_value`` is the
  physical/special boundary. In Gen 2 the *move's TYPE* decides which attack
  and defense stats are used, not the move -- so IRON TAIL (STEEL, id < 20)
  is physical while DRAGONBREATH (DRAGON, id > 20) is special.
- ``engine/battle/effect_commands.asm:2900`` ``BattleCommand_DamageCalc``:
  ``((2*level/5 + 2) * power * attack / defense) / 50``, item boost, crit,
  cap at 999, then ``+MIN_DAMAGE``.
- ``engine/battle/effect_commands.asm:1214`` ``BattleCommand_Stab``: badge
  boosts, then STAB as ``d + d/2``, then the type-matchup rows.
- ``engine/battle/misc.asm:147`` ``DoBadgeTypeBoosts``: on the PLAYER's turn
  only, damage of a type covered by an earned badge gains ``d/8`` (minimum 1).
- ``engine/battle/effect_commands.asm:1496`` ``BattleCommand_DamageVariation``:
  the 85%-100% spread (``BattleRandom`` in 217..255, over 255).
- ``constants/move_effect_constants.asm``: effect ids, so fixed-damage moves
  (DRAGON RAGE is ``EFFECT_STATIC_DAMAGE`` with power 40) are not run through
  the formula and are not thrown away for "having no power".
- ``data/trainers/attributes.asm``: which items each TRAINER CLASS carries
  (``db ITEM, ITEM ; items``), keyed by the class ids that
  ``constants/trainer_constants.asm`` assigns with ``trainerclass`` -- so
  heal-aware burst is read out of the ROM, not hardcoded. The engine gates
  every enemy item behind ``.IsHighestLevel``
  (``engine/battle/ai/items.asm:167``), and heal items fire once the ace
  drops to half HP (``.HealItem``, ibid.:346).

Live stat reads use the in-battle structs, which the engine keeps
STAGE-MODIFIED (``ApplyStatLevelMultiplierOnAllStats`` writes straight into
``wBattleMonAttack``, ``engine/battle/core.asm:6671``), so a read already
reflects Screech, Swords Dance and friends.
"""

import re
from pathlib import Path

from .asmconst import parse_const_defs, parse_defs, parse_ratio_table
from .state import _status

MIN_DAMAGE = 2        # DamageCalc's floor
MAX_DAMAGE = 999      # DamageCalc's cap
VARIATION_LO = 217    # `85 percent` in DamageVariation
VARIATION_HI = 255

# Two-byte big-endian in-battle stats, per side. wBattleMon* is bank 0,
# wEnemyMon* is bank 1 (see pokecrystal.sym).
_U16 = ("hp", "max_hp", "attack", "defense", "speed", "spatk", "spdef")
# ``acc_level``/``eva_level`` are the accuracy and evasion STAGES (1..13,
# 7 neutral) that CheckHit reads live, and ``sub3`` carries
# SUBSTATUS_CONFUSED. Unlike the stat words, these are NOT baked into
# anything else: nothing else in the harness could see a MINIMIZE stack.
_SYMS = {
    "me": {"level": "wBattleMonLevel", "status": "wBattleMonStatus",
           "hp": "wBattleMonHP", "max_hp": "wBattleMonMaxHP",
           "attack": "wBattleMonAttack", "defense": "wBattleMonDefense",
           "speed": "wBattleMonSpeed", "spatk": "wBattleMonSpclAtk",
           "spdef": "wBattleMonSpclDef", "type1": "wBattleMonType1",
           "type2": "wBattleMonType2", "moves": "wBattleMonMoves",
           "pp": "wBattleMonPP", "acc_level": "wPlayerAccLevel",
           "eva_level": "wPlayerEvaLevel", "sub3": "wPlayerSubStatus3",
           "fury_cutter": "wPlayerFuryCutterCount",
           "rollout": "wPlayerRolloutCount"},
    "enemy": {"level": "wEnemyMonLevel", "status": "wEnemyMonStatus",
              "hp": "wEnemyMonHP", "max_hp": "wEnemyMonMaxHP",
              "attack": "wEnemyMonAttack", "defense": "wEnemyMonDefense",
              "speed": "wEnemyMonSpeed", "spatk": "wEnemyMonSpclAtk",
              "spdef": "wEnemyMonSpclDef", "type1": "wEnemyMonType1",
              "type2": "wEnemyMonType2", "moves": "wEnemyMonMoves",
              "pp": "wEnemyMonPP", "acc_level": "wEnemyAccLevel",
              "eva_level": "wEnemyEvaLevel", "sub3": "wEnemySubStatus3",
              "fury_cutter": "wEnemyFuryCutterCount",
              "rollout": "wEnemyRolloutCount"},
}

# Effects whose damage does NOT come from the formula.
FIXED = {
    "EFFECT_STATIC_DAMAGE": "power",   # DRAGON RAGE 40, SONICBOOM 20
    "EFFECT_LEVEL_DAMAGE": "level",    # SEISMIC TOSS / NIGHT SHADE
    "EFFECT_PSYWAVE": "psywave",
    "EFFECT_SUPER_FANG": "half",
    "EFFECT_OHKO": "ohko",
}
# Moves whose power DOUBLES per consecutive hit, and the side counter the
# engine keeps for them. FURY CUTTER doubles `count-1` times with the
# count capped at 5 (x16 max, engine/battle/move_effects/fury_cutter.asm),
# ROLLOUT the same over MAX_ROLLOUT_COUNT=5. A miss (or any other move)
# RESETS the counter -- which is why spending a turn on an item throws the
# whole ramp away, and why a damage model that ignores it recommends
# exactly that (live: Whitney's MILTANK survived three attempts because a
# scheduled potion turned an 80-damage hit into a 5-damage one).
CHAIN = {"EFFECT_FURY_CUTTER": "fury_cutter", "EFFECT_ROLLOUT": "rollout"}
CHAIN_CAP = 5


def chain_power(power, count):
    """Power of the NEXT hit of a ramping move after `count` landed ones."""
    return power * (2 ** (min(int(count or 0) + 1, CHAIN_CAP) - 1))

# Effects that make a damage estimate a lie in one direction or another.
RISKY = {
    "EFFECT_SELFDESTRUCT": "user faints",
    "EFFECT_RECOIL_HIT": "recoil damage",
    "EFFECT_MULTI_HIT": "2-5 hits",
    "EFFECT_DOUBLE_HIT": "hits twice",
    "EFFECT_RAMPAGE": "locks in 2-3 turns, then confusion",
    "EFFECT_HYPER_BEAM": "recharge turn",
}
# Moves that ignore accuracy AND the target's evasion entirely.
# EFFECT_ALWAYS_HIT (FAINT ATTACK, SWIFT) skips the accuracy check, which is
# the only reliable answer to a MINIMIZE / DOUBLE TEAM stack -- live, Koga's
# Muk and Crobat blanked two "100%" attacks in a row while a 15-18 damage
# FAINT ATTACK finished each of them on demand.
NEVER_MISS = ("EFFECT_ALWAYS_HIT",)

# The AI's HP-restoring items (engine/battle/ai/items.asm:274-279, the
# AI_Items table): these are the ones that ERASE chip damage mid-fight.
# FULL_HEAL cures status only -- it does not undo damage, so it is
# deliberately not here.
HEALING_ITEMS = ("FULL_RESTORE", "MAX_POTION", "HYPER_POTION",
                 "SUPER_POTION", "POTION")


def parse_trainer_items(repo):
    """``{class_id: {"class", "items", "line"}}`` from
    data/trainers/attributes.asm.

    Entries correspond in order to the ``trainerclass`` constants of
    constants/trainer_constants.asm (attributes.asm:2 says so); TRAINER_NONE
    (id 0) has no entry, so class ids run from 1. Provenance is the line of
    the entry's ``db ..., ... ; items`` row. Class names come from the ROM's
    own constant list; the display comments in attributes.asm ("Lt Surge",
    "Blackbelt T") do not match them and are not used for lookup."""
    consts = re.findall(r"^\ttrainerclass (\w+)",
                        (Path(repo) / "constants/trainer_constants.asm")
                        .read_text(), re.M)
    classes = [c for c in consts if c != "TRAINER_NONE"]
    out, idx = {}, 0
    path = Path(repo) / "data/trainers/attributes.asm"
    for n, line in enumerate(path.read_text().splitlines(), 1):
        m = re.match(r"\tdb (\w+), (\w+) ; items$", line)
        if not m or idx >= len(classes):
            continue
        out[idx + 1] = {
            "class": classes[idx],
            "items": [x for x in m.groups() if x != "NO_ITEM"],
            "line": n,
        }
        idx += 1
    return out


def parse_effects(repo):
    """``{EFFECT_NAME: id}`` from constants/move_effect_constants.asm."""
    path = Path(repo) / "constants/move_effect_constants.asm"
    out, eid = {}, 0
    for line in path.read_text().splitlines():
        m = re.match(r"\s+const (EFFECT_\w+)", line)
        if m:
            out[m.group(1)] = eid
            eid += 1
    return out


def parse_badge_boosts(repo):
    """Attacking types boosted by each badge, in badge-bit order: the eight
    Johto badges then the eight Kanto ones (data/types/badge_type_boosts.asm).
    ``DoBadgeTypeBoosts`` walks this table while rotating wKantoBadges:wJohtoBadges
    right through carry (misc.asm:170), so entry i is badge bit i."""
    path = Path(repo) / "data/types/badge_type_boosts.asm"
    out = []
    for line in path.read_text().splitlines():
        m = re.match(r"\s+db (\w+)", line)
        if m and m.group(1) != "-1":
            out.append(m.group(1).replace("PSYCHIC_TYPE", "PSYCHIC"))
    return out


def boosted_types(emu, bdata, repo):
    """Live set of type ids that earn the badge damage boost."""
    order = parse_badge_boosts(repo)
    johto = emu.read_u8("wJohtoBadges")
    kanto = emu.read_u8("wKantoBadges")
    bits = [(johto >> i) & 1 for i in range(8)] + \
           [(kanto >> i) & 1 for i in range(8)]
    return {bdata.types[name]
            for name, on in zip(order, bits)
            if on and name in bdata.types}


def parse_species_data(repo):
    """``(types, tmhm)`` parsed from data/pokemon/base_stats/*.asm in ONE
    walk of the ~250 files.

    ``types``: ``{SPECIES_NAME: [type1, type2], dex_no: [...]}`` -- the
    frame's party entries carry a species name and id but no types, and a
    switch cannot be judged without them, so it is keyed both ways.
    ``tmhm``: ``{SPECIES_NAME: [MOVE_CONST, ...]}`` from the ``tmhm`` line
    -- the learnset the game itself checks, so "can this mon learn this
    TM?" is answered before a single button is pressed.
    """
    types, tmhm = {}, {}
    for path in sorted((Path(repo) / "data/pokemon/base_stats").glob("*.asm")):
        name = dex = mytypes = None
        learns = []
        for line in path.read_text().splitlines():
            m = re.match(r"\s+db (\w+) ; (\d+)\s*$", line)
            if m and name is None:
                name, dex = m.group(1), int(m.group(2))
                continue
            m = re.match(r"\s+db (\w+), (\w+) ; type", line)
            if m:
                mytypes = [m.group(1), m.group(2)]
                continue
            m = re.match(r"\s+tmhm (.+?)\s*$", line)
            if m:
                learns = [t.strip() for t in m.group(1).split(",")
                          if t.strip()]
        if name and mytypes:
            types[name] = mytypes
            if dex is not None:
                types[dex] = mytypes
        if name:
            tmhm[name] = learns
            if dex is not None:
                tmhm[dex] = learns
    return types, tmhm


def parse_species_types(repo):
    """``{SPECIES_NAME: [type1, type2], dex_no: [...]}``."""
    return parse_species_data(repo)[0]


def parse_species_tmhm(repo):
    """``{SPECIES_NAME: [MOVE_CONST, ...]}`` TM/HM learnsets."""
    return parse_species_data(repo)[1]


def parse_tmhm_moves(repo):
    """``{'TM01': 'DYNAMICPUNCH', ..., 'HM07': 'WATERFALL'}`` in TM/HM
    number order, from the ``add_tm``/``add_hm`` lines of
    constants/item_constants.asm.

    That file is the source: data/moves/tmhm_moves.asm builds its table
    with an rgbds ``for`` loop over the ``TM##_MOVE`` constants defined
    here, so there is no literal list to read there. Item ids interleave
    non-TM entries (``const ITEM_C3`` sits between TM04 and TM05), which
    is exactly why the numbering has to come from counting add_tm lines
    rather than from item ids.
    """
    path = Path(repo) / "constants/item_constants.asm"
    tms, hms = [], []
    for line in path.read_text().splitlines():
        m = re.match(r"\s+add_tm\s+(\w+)", line)
        if m:
            tms.append(m.group(1))
            continue
        m = re.match(r"\s+add_hm\s+(\w+)", line)
        if m:
            hms.append(m.group(1))
    out = {f"TM{i:02d}": mv for i, mv in enumerate(tms, 1)}
    out.update({f"HM{i:02d}": mv for i, mv in enumerate(hms, 1)})
    return out


def read_side(emu, side):
    """Live in-battle stats for ``'me'`` or ``'enemy'`` (stage-modified).

    ``moves`` drops empty slots but ``slots`` keeps the raw 4-slot layout, so
    an ``('attack', slot)`` action always names the slot the engine expects.
    ``pp`` is aligned to ``moves``."""
    out = {}
    for key, sym in _SYMS[side].items():
        bank, addr = emu.sym[sym]
        if key in ("moves", "pp"):
            out[key] = list(emu.read((bank, addr), 4))
        elif key in _U16:
            hi, lo = emu.read((bank, addr), 2)
            out[key] = (hi << 8) | lo
        else:
            out[key] = emu.read((bank, addr), 1)[0]
    raw, pp = out["moves"], out.get("pp") or [0, 0, 0, 0]
    out["slots"] = [(i, mid, pp[i] if i < len(pp) else 0)
                    for i, mid in enumerate(raw) if mid]
    out["moves"] = [mid for mid in raw if mid]
    out["pp"] = [p for i, p in enumerate(pp) if i < len(raw) and raw[i]]
    out["types"] = [out["type1"], out["type2"]]
    return out


def read_battle(emu):
    """Both sides' live stats: ``{'me': {...}, 'enemy': {...}}``."""
    return {"me": read_side(emu, "me"), "enemy": read_side(emu, "enemy")}


def damage_span(level, power, atk, dfn, *, stab, mult,
                badge=False, crit=False):
    """(min, max) damage for one hit, following DamageCalc -> Stab ->
    DamageVariation in that order. ``mult`` is the type multiplier."""
    if power <= 0 or mult == 0:
        return (0, 0)
    d = ((2 * level) // 5 + 2) * power * atk // max(1, dfn) // 50
    if crit:
        d *= 2
    d = min(d, MAX_DAMAGE - MIN_DAMAGE) + MIN_DAMAGE
    if badge:
        d += max(1, d // 8)
    if stab:
        d += d // 2
    d = int(d * mult)
    if d <= 0:
        return (0, 0)
    return (min(d * VARIATION_LO // VARIATION_HI, MAX_DAMAGE),
            min(d, MAX_DAMAGE))


def effective_accuracy(raw, acc_stage, eva_stage, table, *, base=7, top=13):
    """Accuracy byte after accuracy/evasion stages, exactly as the engine
    computes it (``BattleCommand_CheckHit.StatModifiers``,
    engine/battle/effect_commands.asm:1758).

    Two passes over ``AccuracyLevelMultipliers``: one with the attacker's
    accuracy stage, one with ``MAX_STAT_LEVEL + 1 - the target's evasion
    stage``. Each pass multiplies then integer-divides and floors at 1;
    the result is capped at $ff. Stages run 1..13 with 7 neutral
    (BASE_STAT_LEVEL / MAX_STAT_LEVEL, constants/battle_constants.asm:10).

    This is the number a policy actually needs: a listed-100% move against
    two MINIMIZEs really lands 60% of the time, and Koga's Muk blanking
    two "100%" attacks in a row is what that looks like live.
    """
    val = int(raw)
    for stage in (acc_stage or base, top + 1 - (eva_stage or base)):
        num, den = table[min(max(int(stage), 1), top) - 1]
        val = max(1, val * num // den)
    return min(val, 0xff)


def acc_percent(byte):
    """0-255 accuracy byte -> percentage, scaled like BattleData's."""
    return max(1, min(100, round(int(byte) * 100 / 255)))


class Tactics:
    """Damage/type analysis for one live battle.

    ``badge_types`` are the attacking types boosted by earned badges
    (DoBadgeTypeBoosts); pass an empty set to model an enemy's turn, which
    never gets the boost (``hBattleTurn`` check, misc.asm:157).
    """

    def __init__(self, bdata, names, repo, badge_types=(), heal_table=None):
        self.bdata = bdata
        self.names = names
        self.effects = parse_effects(repo)
        self.by_effect_id = {v: k for k, v in self.effects.items()}
        self.special_from = bdata.types["FIRE"]   # DEF SPECIAL EQU (line 26)
        self.badge_types = set(badge_types)
        self.species_types = parse_species_types(repo)
        # Accuracy/evasion stage table and the stage bounds, straight out
        # of the files the engine itself is built from.
        repo = Path(repo)
        self.acc_mults = parse_ratio_table(
            repo / "data/battle/accuracy_multipliers.asm",
            "AccuracyLevelMultipliers")
        defs = parse_defs(repo / "constants/battle_constants.asm")
        self.base_stage = defs["BASE_STAT_LEVEL"]
        self.max_stage = defs["MAX_STAT_LEVEL"]
        self.slp_mask = defs["SLP_MASK"]
        consts = parse_const_defs(repo / "constants/battle_constants.asm")
        self.status_bits = {n: 1 << consts[n]
                            for n in ("PSN", "BRN", "FRZ", "PAR")}
        self.confused_bit = 1 << consts["SUBSTATUS_CONFUSED"]
        # PAR/SLP/FRZ are the ones that cost TURNS, which is what a
        # mid-battle cure is worth spending an item on. PSN/BRN cost HP,
        # which the potion branch and heal_party already answer.
        self.turn_status = (self.status_bits["PAR"] | self.status_bits["FRZ"]
                            | self.slp_mask)
        # {normalised item: curative properties} from the ROM's own tables,
        # so `recommend` can name a real cure instead of guessing one.
        self.heal_table = heal_table or {}
        # {class_id: {"class", "items", "line"}} -- who heals, from the ROM.
        self.trainer_items = parse_trainer_items(repo)

    # -- categories ------------------------------------------------------

    def is_special(self, type_id):
        return type_id >= self.special_from

    def category(self, type_id):
        return "special" if self.is_special(type_id) else "physical"

    def effect_name(self, move_id):
        rec = self.bdata.moves.get(move_id) or {}
        return self.by_effect_id.get(rec.get("effect"), "?")

    # -- one move --------------------------------------------------------

    def outlook(self, move_id, attacker, defender, *, boosted=True):
        """What one move does: type multiplier, damage span, hits-to-KO.

        ``boosted`` applies the player's badge boost; pass False for the
        enemy's moves.
        """
        rec = self.bdata.moves.get(move_id)
        name = self.names.moves.get(move_id, f"?id{move_id}")
        if not rec:
            view = {"move": name, "id": move_id, "kind": "unknown",
                    "min": 0, "max": 0, "mult": 1.0, "accuracy": 0,
                    "effective_accuracy": 0,
                    "type": None, "type_name": "?", "power": 0,
                    "effect": "?", "stab": False, "category": "physical",
                    "note": "no move data"}
            return self._summarise(view, defender)
        mtype, power, acc = rec["type"], rec["power"], rec["accuracy"]
        # The stage math runs on the ROM's 0-255 byte. A record without it
        # gets the percentage scaled back up rather than a free 255: an
        # assumed "always hits" is exactly the bug this whole path exists
        # to kill.
        raw = rec.get("accuracy_raw")
        if raw is None:
            raw = min(0xff, round(acc * 255 / 100))
        eff = self.by_effect_id.get(rec["effect"], "?")
        mult = self.bdata.effectiveness(mtype, defender["types"])
        stab = mtype in attacker["types"]
        chain_key = CHAIN.get(eff)
        chain_count = int(attacker.get(chain_key) or 0) if chain_key else 0
        if chain_key:
            # the NEXT hit's power, ramp included -- an un-ramped 10 is
            # what made a live FURY CUTTER chain look worthless
            power = chain_power(power, chain_count)
        view = {
            "move": name, "id": move_id, "type": mtype,
            "type_name": self._type_name(mtype), "power": power,
            "accuracy": acc, "effect": eff, "mult": mult, "stab": stab,
            "category": self.category(mtype), "note": "",
            "chain": chain_key, "chain_count": chain_count,
            # What the move ACTUALLY lands at, after the attacker's
            # accuracy stage and the target's evasion stage.
            "effective_accuracy": acc_percent(effective_accuracy(
                raw,
                attacker.get("acc_level"), defender.get("eva_level"),
                self.acc_mults, base=self.base_stage, top=self.max_stage)),
        }
        # Immunity beats everything, including fixed damage.
        if mult == 0:
            view.update(kind="immune", min=0, max=0,
                        note=f"{view['type_name']} does not affect "
                             f"{self._types_text(defender['types'])}")
            return self._summarise(view, defender)
        fixed = FIXED.get(eff)
        if fixed:
            lo, hi = self._fixed_damage(fixed, power, attacker, defender)
            view.update(kind="fixed", min=lo, max=hi,
                        note=f"{eff.removeprefix('EFFECT_').lower()}: "
                             f"ignores stats")
        elif power == 0:
            view.update(kind="status", min=0, max=0,
                        note=f"{eff.removeprefix('EFFECT_').lower()}")
        else:
            special = self.is_special(mtype)
            atk = attacker["spatk" if special else "attack"]
            dfn = defender["spdef" if special else "defense"]
            lo, hi = damage_span(
                attacker["level"], power, atk, dfn, stab=stab, mult=mult,
                badge=boosted and mtype in self.badge_types)
            view.update(kind="attack", min=lo, max=hi)
        if eff in RISKY:
            view["note"] = (view["note"] + "; " if view["note"] else "") \
                           + RISKY[eff]
        if eff in NEVER_MISS:
            view["never_misses"] = True
            view["effective_accuracy"] = 100
            view["note"] = (view["note"] + "; " if view["note"] else "") \
                           + "never misses (ignores evasion)"
        else:
            view["never_misses"] = False
        return self._summarise(view, defender)

    @staticmethod
    def _summarise(view, defender):
        """KO arithmetic every view carries -- including the ones that deal
        no damage, so a caller can read `ko_certain` off any move without
        first checking `kind`."""
        hp = max(1, defender["hp"])
        view["pct_max"] = round(100 * view["max"] / hp, 1)
        view["ko_certain"] = view["min"] >= defender["hp"] > 0
        view["ko_possible"] = view["max"] >= defender["hp"] > 0
        view["hits_to_ko"] = (
            None if view["max"] <= 0
            else -(-defender["hp"] // max(1, view["min"] or view["max"])))
        return view

    def _fixed_damage(self, kind, power, attacker, defender):
        if kind == "power":
            return (power, power)                     # DRAGON RAGE = 40
        if kind == "level":
            return (attacker["level"], attacker["level"])
        if kind == "psywave":
            return (1, max(1, attacker["level"] * 3 // 2))
        if kind == "half":
            return (max(1, defender["hp"] // 2),) * 2
        if kind == "ohko":
            return (defender["hp"], defender["hp"])
        return (0, 0)

    def _type_name(self, tid):
        return next((n for n, v in self.bdata.types.items() if v == tid),
                    f"?{tid}")

    def _types_text(self, types):
        return "/".join(dict.fromkeys(self._type_name(t) for t in types))

    # -- both sides ------------------------------------------------------

    def my_moves(self, me, enemy, pp=None):
        """Every one of my moves, best first, each carrying the engine slot
        it lives in. ``pp`` optionally overrides the live PP by move id."""
        views = []
        for slot, mid, live_pp in me.get(
                "slots", [(i, m, None) for i, m in enumerate(me["moves"])]):
            v = self.outlook(mid, me, enemy)
            v["slot"] = slot
            v["pp"] = pp.get(mid, live_pp) if pp else live_pp
            views.append(v)
        return sorted(views, key=self._score, reverse=True)

    def enemy_threats(self, me, enemy):
        """The enemy's moves against my active mon, worst first. The enemy
        gets no badge boost."""
        views = [self.outlook(mid, enemy, me, boosted=False)
                 for mid in enemy["moves"]]
        return sorted(views, key=lambda v: v["max"], reverse=True)

    @staticmethod
    def _score(v):
        """Expected damage, with a certain KO worth more than any amount of
        overkill -- and among certain KOs, the RELIABLE one wins.

        Live lesson (Will's Jynx, Bruno's Onix, Karen's Gengar): when two
        moves both kill, the bigger number is worth nothing and the miss
        chance is worth everything -- a whiff on Gengar hands it the turn it
        needs to DESTINY BOND. `never_misses` beats even 100% listed
        accuracy, because listed accuracy still loses to MINIMIZE -- and
        against a MINIMIZE stack the EFFECTIVE accuracy is what a listed
        100% is really worth."""
        if v.get("pp") == 0:          # None means "PP unknown", 0 means empty
            return -1
        if v["kind"] in ("immune", "status", "unknown"):
            return 0
        hit = (1.0 if v.get("never_misses")
               else v.get("effective_accuracy", v["accuracy"]) / 100)
        if v["ko_certain"]:
            return 2_000 + 100 * hit
        return v["min"] * hit

    def turn_loss(self, side):
        """Fraction of this side's turns its status is expected to eat.

        1.0 while asleep or frozen (the engine returns before the move
        runs at all), otherwise 25% for full paralysis
        (effect_commands.asm:323 `cp 25 percent`) compounded with 50% for
        a confusion self-hit (ibid.:494). Paralysis' half-SPEED is NOT
        here: the engine writes it straight into wBattleMonSpeed
        (ApplyPrzEffectOnSpeed, core.asm:6585), so a speed read already
        has it."""
        status = side.get("status") or 0
        if status & (self.slp_mask | self.status_bits["FRZ"]):
            return 1.0
        act = 1.0
        if status & self.status_bits["PAR"]:
            act *= 0.75
        if side.get("confused"):
            act *= 0.5
        return round(1.0 - act, 3)

    def _decode_state(self, side):
        """Status names and confusion, added to a side in place."""
        side["status_names"] = _status(side.get("status") or 0)
        side["confused"] = bool((side.get("sub3") or 0) & self.confused_bit)
        return side

    def read(self, emu, pp=None):
        """Live analysis of the current battle, or None before the battle
        mon blocks are populated.

        The encounter hook fires BEFORE the engine fills wBattleMon*, where
        a read comes back as a blank L0 0/0 mon standing next to the
        PREVIOUS battle's enemy. Reporting that is worse than reporting
        nothing: it invents a matchup that is not on screen."""
        sides = read_battle(emu)
        me, enemy = sides["me"], sides["enemy"]
        if not me["level"] or not me["max_hp"] or not enemy["max_hp"]:
            return None
        self._decode_state(me)
        self._decode_state(enemy)
        mine = self.my_moves(me, enemy, pp)
        threats = self.enemy_threats(me, enemy)
        # Raw speed compare on purpose: paralysis' halving is already in
        # the WRAM word (ApplyPrzEffectOnSpeed, core.asm:6585).
        faster = me["speed"] > enemy["speed"]
        best = mine[0] if mine else None
        worst = threats[0] if threats else None
        return {
            "me": me, "enemy": enemy, "moves": mine, "threats": threats,
            "faster": faster,
            "my_best": best, "their_best": worst,
            "my_status": me["status_names"], "my_confused": me["confused"],
            "their_status": enemy["status_names"],
            "their_confused": enemy["confused"],
            "turn_loss": self.turn_loss(me),
            "their_turn_loss": self.turn_loss(enemy),
            "i_die_next_turn": bool(worst and worst["min"] >= me["hp"]),
            "i_can_ko": bool(best and best["ko_certain"]),
            "turns_i_need": best["hits_to_ko"] if best else None,
            "turns_they_need": (
                None if not worst or worst["max"] <= 0
                else -(-me["hp"] // max(1, worst["min"] or worst["max"]))),
            "trainer": self.trainer_context(emu),
        }

    def trainer_context(self, emu):
        """Who am I fighting, and what does their class carry? ``None`` for
        a wild battle (wTrainerClass 0) or any read failure -- the heal
        model must degrade to today's behaviour, never raise: this runs
        inside a live battle loop.

        wTrainerClass / wOTPartyMon1Level are bank-1 WRAM (pokecrystal.sym);
        the OT party levels are what .IsHighestLevel
        (engine/battle/ai/items.asm:242) itself compares."""
        try:
            bank, addr = emu.sym["wTrainerClass"]
            cls = emu.read((bank, addr), 1)[0]
            if not cls:
                return None
            obank, obase = emu.sym["wOTPartyMon1Level"]
            stride = emu.sym.offset("wOTPartyMon2", "wOTPartyMon1")
            count = min(emu.read_u8("wOTPartyCount"), 6)
            levels = [emu.read((obank, obase + i * stride), 1)[0]
                      for i in range(count)]
        except Exception:
            return None
        rec = self.trainer_items.get(cls) or {}
        return {"class": cls,
                "class_name": rec.get("class"),
                "items": list(rec.get("items") or []),
                "source": (f"data/trainers/attributes.asm:{rec['line']}"
                           if rec else None),
                "enemy_levels": levels}

    def expects_heal(self, analysis):
        """Will the mon facing me be healed out from under my chip damage?

        True exactly when BATTLE.md §10's rule holds: a trainer battle
        whose class carries an HP-restoring item (data/trainers/
        attributes.asm) AND the mon in front is its highest-level one --
        AI_TryItem gates EVERY enemy item behind .IsHighestLevel
        (engine/battle/ai/items.asm:167), and heal items fire once that mon
        drops to half HP (.HealItem, ibid.:346). Live: Koga healed his
        Crobat 10 -> 26 HP mid-fight, exactly here.

        Returns ``{"heal_items", "source", "enemy_level", "party_max"}`` or
        False. Unknown class, unknown levels or no HP healer all degrade to
        False -- no bias, no exception."""
        tr = analysis.get("trainer") or {}
        if not tr.get("class"):
            return False
        heals = [h for h in HEALING_ITEMS if h in (tr.get("items") or [])]
        if not heals:
            return False
        levels = tr.get("enemy_levels") or []
        level = (analysis.get("enemy") or {}).get("level")
        if not levels or level is None:
            return False          # cannot tell; refuse to guess
        if max(levels) > level:
            return False          # a bigger mon waits: not the ace
        return {"heal_items": [h.replace("_", " ") for h in heals],
                "source": tr.get("source"),
                "enemy_level": level, "party_max": max(levels)}

    def sacrifice_line(self, analysis, frame=None):
        """The doomed-mon assessment behind the RIPTIDE line, or None.

        Doomed means the enemy's best move KILLS me on its minimum roll
        AND I cannot certainly KO first -- respecting `faster`: an
        outspeeding certain KO removes the threat before it resolves
        (BATTLE.md §8), so there is nothing to sacrifice against. When
        doomed, my remaining value is the damage I deal before fainting,
        because a faint lets the replacement enter FREE while a voluntary
        switch concedes a hit (§9). The returned dict names the max-
        expected-damage move (fixed-damage moves compete on their flat
        number -- DRAGON RAGE's 40 beat a resisted STAB Surf live) and the
        successor with whether IT can finish what is left after the chip.
        """
        me = analysis["me"]
        their = analysis.get("their_best")
        if not their or me["hp"] <= 0 or their["min"] < me["hp"]:
            return None
        kos = [m for m in analysis["moves"]
               if m.get("pp") != 0 and m["ko_certain"]]
        if kos and analysis.get("faster"):
            return None           # §8: kill it before the threat resolves
        live = [m for m in analysis["moves"]
                if m.get("pp") != 0 and m["max"] > 0]
        if not live:
            return None
        pick = max(live, key=self._score)
        after_chip = dict(analysis["enemy"])
        after_chip["hp"] = max(1, after_chip["hp"] - pick["min"])
        succ = next(iter(self.switch_options(analysis, frame)), None)
        finish = self._successor_finish(succ, after_chip) if succ else {}
        return {"pick": pick, "chip_min": pick["min"],
                "enemy_hp_after_chip": after_chip["hp"],
                "successor": succ, "successor_finishes": finish}

    def _successor_finish(self, succ, defender):
        """Can the incoming mon KO the chipped enemy in one move? Needs the
        frame's party entry to carry moves and stats (read_party provides
        both); anything missing degrades to hits_to_ko None."""
        entry = succ.get("_entry") if succ else None
        if not entry or not entry.get("moves"):
            return {"move": None, "hits_to_ko": None}
        names = self.species_types.get(entry.get("species")) \
            or self.species_types.get(entry.get("species_id")) or []
        tids = [self.bdata.types[n] for n in names if n in self.bdata.types]
        attacker = {"level": entry.get("level", 0), "hp": entry.get("hp", 1),
                    "types": tids or [0], "status": 0, "sub3": 0,
                    "acc_level": self.base_stage, "eva_level": self.base_stage,
                    "attack": entry.get("attack", 0),
                    "defense": entry.get("defense", 0),
                    "speed": entry.get("speed", 0),
                    "spatk": entry.get("spatk", 0),
                    "spdef": entry.get("spdef", 0)}
        views = [self.outlook(mid, attacker, defender)
                 for mid in entry["moves"] if mid in self.bdata.moves]
        best = min((v for v in views if v["hits_to_ko"] is not None),
                   key=lambda v: v["hits_to_ko"], default=None)
        if not best:
            return {"move": None, "hits_to_ko": None}
        return {"move": best["move"], "hits_to_ko": best["hits_to_ko"]}

    # -- the actual decision ---------------------------------------------

    def switch_options(self, analysis, frame):
        """Party members ranked as answers to the CURRENT enemy.

        The frame gives `can_switch` as LEGAL party indexes and `party` as
        the roster, but no types -- those come from the base-stats data, so
        the enemy's best move can be scored against each candidate. A
        resisted hit and a healthy bench mon score high.
        """
        their = analysis["their_best"]
        legal = set((frame or {}).get("can_switch") or [])
        out = []
        for mon in (frame or {}).get("party") or []:
            if mon.get("index") not in legal:
                continue
            names = self.species_types.get(mon.get("species")) \
                or self.species_types.get(mon.get("species_id")) or []
            types = [self.bdata.types[n] for n in names
                     if n in self.bdata.types]
            incoming = (self.bdata.effectiveness(their["type"], types)
                        if their and types and their.get("type") is not None
                        else 1.0)
            hp_frac = mon.get("hp", 0) / max(1, mon.get("max_hp", 1))
            out.append({
                "index": mon["index"],
                "nickname": mon.get("nickname") or mon.get("species"),
                "types": names,
                "incoming_mult": incoming,
                "hp_frac": round(hp_frac, 2),
                "score": round((2.0 - incoming) * hp_frac, 3),
                # the raw roster entry, so the sacrifice line can read the
                # successor's moves/stats without a second lookup
                "_entry": mon,
            })
        return sorted(out, key=lambda m: m["score"], reverse=True)

    def recommend(self, analysis, frame=None, *, heal_at=0.3):
        """``(action, reason)`` for this turn -- the whole point of the
        module. Order of preference:

        1. a certain KO (the most RELIABLE one wins -- BATTLE.md §7),
           because a dead enemy deals no damage;
        2. the SACRIFICE LINE, when their best move kills me on its
           minimum roll and I cannot certainly KO first: spend the last
           turns on maximum damage and let the replacement enter free
           (§9) -- a voluntary switch would concede a hit, so switching is
           deliberately NOT on this list once doomed;
        3. healing, if I am about to be out-damaged and carry a potion;
        4. curing PAR/SLP/FRZ, which cost whole TURNS, when nothing is
           about to kill me and the bag holds the cure;
        5. otherwise the best expected damage -- or, against a healer's
           ace (expects_heal), the move that removes it in the fewest
           hits, because chip gets erased by a FULL RESTORE (§10).
        """
        moves = [m for m in analysis["moves"] if m.get("pp") != 0]
        live = [m for m in moves if m["max"] > 0]
        me = analysis["me"]
        their = analysis["their_best"]
        kos = [m for m in live if m["ko_certain"]]
        if kos:
            # Reliability first: an unmissable kill beats a 100%-listed kill
            # (which a MINIMIZE stack has already devalued -- rank on the
            # EFFECTIVE number), which beats a bigger but chancier one.
            pick = max(kos, key=lambda m: (
                bool(m.get("never_misses")),
                m.get("effective_accuracy", m["accuracy"]), m["min"]))
            eff = pick.get("effective_accuracy", pick["accuracy"])
            hits = ("unmissable" if pick.get("never_misses")
                    else f"{eff}% acc" if eff == pick["accuracy"]
                    else f"{eff}% acc (listed {pick['accuracy']}%)")
            return ("attack", pick["slot"]), (
                f"{pick['move']} KOs now ({pick['min']}-{pick['max']} vs "
                f"{analysis['enemy']['hp']} HP, x{pick['mult']:g}, {hits})")
        doom = self.sacrifice_line(analysis, frame)
        if doom:
            pick = doom["pick"]
            succ = doom["successor"]
            tail = (f"{pick['move']} {pick['min']}-{pick['max']} is my "
                    f"remaining value")
            if succ:
                fin = doom.get("successor_finishes") or {}
                tail += (f"; {succ['nickname']} enters FREE on the faint "
                         f"(takes x{succ['incoming_mult']:g} of "
                         f"{their['move']})")
                if fin.get("hits_to_ko"):
                    tail += (f" and needs {fin['hits_to_ko']} hit(s) to "
                             f"finish the {doom['enemy_hp_after_chip']} HP "
                             f"left" + (f" with {fin['move']}"
                                        if fin.get("move") else ""))
            else:
                tail += "; no replacement waits, but fainting still beats " \
                        "conceding a switch-in hit"
            return ("attack", pick["slot"]), (
                f"doomed: {their['move']} does {their['min']}-{their['max']} "
                f"vs my {me['hp']} HP and nothing KOs first -- {tail}")
        lethal = bool(their and their["min"] >= me["hp"])
        # Heal on the FRACTION or on the THREAT, whichever bites first: a
        # flat 30% is far too late against a 2HKO. Live (rival Croconaw,
        # WATER GUN 15-18 into a 62 HP QUILAVA): by the time 30% was
        # reached the sacrifice line owned the turn and healing stopped
        # forever, so the run whited out three times with TEN potions in
        # the bag. Healing while two of their best hits still fit in my
        # HP bar wins that fight -- and `lethal` keeps a doomed turn from
        # being wasted on a potion.
        # ...unless the window can never be escaped: when a full bar minus
        # one hit is STILL inside two hits (max_hp <= 3 x their max), the
        # rule heals, eats a hit, heals again until the bag is empty and
        # never attacks (live: SIX Hyper Potions into Bruno's Machamp, all
        # at 105+/170 HP, zero attacks). Against such a hitter a potion is
        # only worth the turn when the NEXT hit can kill (their max roll
        # reaches me); otherwise attack and take the hit.
        if their and their["max"]:
            escapable = me["max_hp"] > 3 * their["max"]
            threatened = (me["hp"] < me["max_hp"] and
                          me["hp"] <= 2 * their["max"] and
                          (escapable or me["hp"] <= their["max"]))
        else:
            threatened = False
        hurt = me["hp"] <= heal_at * me["max_hp"] or threatened
        # The frame's bag is keyed by normalised names ('FULLRESTORE'), so
        # match through norm_item rather than hoping about spacing.
        from .battle import norm_item
        bag = {norm_item(k): v for k, v in
               ((frame or {}).get("bag") or {}).items()}
        potion = next((n for n in ("FULL RESTORE", "MAX POTION",
                                   "HYPER POTION", "SUPER POTION", "POTION")
                       if bag.get(norm_item(n))), None)
        # A LIVE ramp is worth more than the potion: an item turn costs the
        # turn AND resets the chain to zero (fury_cutter.asm resets on
        # anything but a landed hit). Live: two scheduled SUPER POTIONs
        # turned a chained FURY CUTTER that had just done 41 into one that
        # did 5, and Whitney's MILTANK won three times over it.
        chained = next((m for m in live
                        if m.get("chain") and m.get("chain_count")), None)
        if hurt and potion and not lethal and not chained:
            why = (f"{me['hp']}/{me['max_hp']} HP with {potion} in the bag "
                   f"and nothing lethal incoming")
            if threatened and me["hp"] > heal_at * me["max_hp"]:
                why += (f" -- two {their['move']} hits ({their['max']} each) "
                        f"would finish me")
            return ("item", potion), why
        if hurt and potion and not lethal and chained:
            return ("attack", chained["slot"]), (
                f"{chained['move']} is {chained['chain_count'] + 1} hits "
                f"into its ramp ({chained['min']}-{chained['max']} now) -- "
                f"a {potion} would reset it, and nothing lethal is incoming")
        # A turn-eating status is worth an item when nothing is about to
        # kill me and no kill is available: PAR alone throws away a quarter
        # of my turns, SLP/FRZ all of them. PSN/BRN cost HP, not turns --
        # the potion branch above and heal_party already answer those.
        cure_mask = (me.get("status") or 0) & self.turn_status
        if cure_mask and not lethal:
            from .battle import cheapest_heal
            cure = cheapest_heal(self.heal_table, bag, None, 0,
                                 cure_mask, False)
            if cure:
                names = "/".join(analysis.get("my_status")
                                 or _status(cure_mask)) or "status"
                loss = analysis.get("turn_loss", self.turn_loss(me))
                return ("item", cure), (
                    f"{names} costs me ~{loss:.0%} of my turns and nothing "
                    f"lethal is incoming; {cure} clears it")
        if not live:
            status = [m for m in moves if m["kind"] == "status"]
            if status:
                return ("attack", status[0]["slot"]), (
                    f"no damaging move connects; {status[0]['move']} instead")
            return "flee", "nothing in this moveset can touch it"
        heal = self.expects_heal(analysis)
        if heal:
            # Burst over chip: their FULL RESTORE undoes every turn of
            # chipping once the ace hits half HP (.HealItem,
            # engine/battle/ai/items.asm:346), so minimise hits_to_ko.
            pick = min(live, key=lambda m: (
                m["hits_to_ko"] if m["hits_to_ko"] else 999,
                -self._score(m)))
            why = (f"{pick['move']} burst over chip: this ace will be "
                   f"healed ({'/'.join(heal['heal_items'])}, "
                   f"{heal['source']}), so {pick['hits_to_ko']} hit(s) to "
                   f"KO beats accumulating damage that gets erased")
        else:
            pick = live[0]
            why = (f"{pick['move']} x{pick['mult']:g} "
                   f"{pick['min']}-{pick['max']} ({pick['pct_max']}% of "
                   f"its HP), {pick['hits_to_ko']} hit(s) to KO")
        if lethal:
            why += f" -- but {their['move']} can kill me first"
        return ("attack", pick["slot"]), why

    def explain(self, analysis):
        """One-line-per-move audit of a `read()`."""
        me, en = analysis["me"], analysis["enemy"]
        lines = [
            f"me L{me['level']} {me['hp']}/{me['max_hp']} "
            f"{self._types_text(me['types'])} spd {me['speed']}"
            f"{self._state_text(me)}"
            f"  vs  enemy L{en['level']} {en['hp']}/{en['max_hp']} "
            f"{self._types_text(en['types'])} spd {en['speed']}"
            f"{self._state_text(en)}"
            f"   [{'I move first' if analysis['faster'] else 'they move first'}]"
        ]
        for v in analysis["moves"]:
            flag = ("KO" if v["ko_certain"] else
                    "ko?" if v["ko_possible"] else "  ")
            eff = v.get("effective_accuracy", v["accuracy"])
            listed = ("" if eff == v["accuracy"]
                      else f" (listed {v['accuracy']})")
            lines.append(
                f"  {flag} {v['move']:<13s} {v['type_name']:<8s}"
                f" {v['category'][:4]:<4s} x{v['mult']:<4g}"
                f" {v['min']:>3d}-{v['max']:<3d} ({v['pct_max']:>5.1f}%)"
                f" acc {eff:>3d}{listed}"
                f"{' STAB' if v['stab'] else '     '}"
                f" {v['note']}")
        for v in analysis["threats"][:4]:
            lines.append(
                f"  <- {v['move']:<13s} {v['type_name']:<8s} x{v['mult']:<4g}"
                f" {v['min']:>3d}-{v['max']:<3d} on me"
                f"{'  LETHAL' if v['min'] >= analysis['me']['hp'] else ''}")
        return "\n".join(lines)

    def _state_text(self, side):
        """`` PAR -25% turns`` for a side that is losing turns, else ''."""
        names = list(side.get("status_names") or [])
        if side.get("confused"):
            names.append("CNF")
        if not names:
            return ""
        loss = self.turn_loss(side)
        return f" {'/'.join(names)}" + (f" -{loss:.0%} turns" if loss else "")
