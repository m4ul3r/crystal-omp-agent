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

Live stat reads use the in-battle structs, which the engine keeps
STAGE-MODIFIED (``ApplyStatLevelMultiplierOnAllStats`` writes straight into
``wBattleMonAttack``, ``engine/battle/core.asm:6671``), so a read already
reflects Screech, Swords Dance and friends.
"""

import re
from pathlib import Path

MIN_DAMAGE = 2        # DamageCalc's floor
MAX_DAMAGE = 999      # DamageCalc's cap
VARIATION_LO = 217    # `85 percent` in DamageVariation
VARIATION_HI = 255

# Two-byte big-endian in-battle stats, per side. wBattleMon* is bank 0,
# wEnemyMon* is bank 1 (see pokecrystal.sym).
_U16 = ("hp", "max_hp", "attack", "defense", "speed", "spatk", "spdef")
_SYMS = {
    "me": {"level": "wBattleMonLevel", "status": "wBattleMonStatus",
           "hp": "wBattleMonHP", "max_hp": "wBattleMonMaxHP",
           "attack": "wBattleMonAttack", "defense": "wBattleMonDefense",
           "speed": "wBattleMonSpeed", "spatk": "wBattleMonSpclAtk",
           "spdef": "wBattleMonSpclDef", "type1": "wBattleMonType1",
           "type2": "wBattleMonType2", "moves": "wBattleMonMoves",
           "pp": "wBattleMonPP"},
    "enemy": {"level": "wEnemyMonLevel", "status": "wEnemyMonStatus",
              "hp": "wEnemyMonHP", "max_hp": "wEnemyMonMaxHP",
              "attack": "wEnemyMonAttack", "defense": "wEnemyMonDefense",
              "speed": "wEnemyMonSpeed", "spatk": "wEnemyMonSpclAtk",
              "spdef": "wEnemyMonSpclDef", "type1": "wEnemyMonType1",
              "type2": "wEnemyMonType2", "moves": "wEnemyMonMoves",
              "pp": "wEnemyMonPP"},
}

# Effects whose damage does NOT come from the formula.
FIXED = {
    "EFFECT_STATIC_DAMAGE": "power",   # DRAGON RAGE 40, SONICBOOM 20
    "EFFECT_LEVEL_DAMAGE": "level",    # SEISMIC TOSS / NIGHT SHADE
    "EFFECT_PSYWAVE": "psywave",
    "EFFECT_SUPER_FANG": "half",
    "EFFECT_OHKO": "ohko",
}
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


def parse_species_types(repo):
    """``{SPECIES_NAME: [type1, type2], dex_no: [...]}`` from
    data/pokemon/base_stats/*.asm. The frame's party entries carry a species
    name and id but no types, and a switch cannot be judged without them.
    Keyed both ways because game_state names the species while read_party
    keeps the id."""
    out = {}
    for path in sorted((Path(repo) / "data/pokemon/base_stats").glob("*.asm")):
        name = dex = types = None
        for line in path.read_text().splitlines():
            m = re.match(r"\s+db (\w+) ; (\d+)\s*$", line)
            if m and name is None:
                name, dex = m.group(1), int(m.group(2))
                continue
            m = re.match(r"\s+db (\w+), (\w+) ; type", line)
            if m:
                types = [m.group(1), m.group(2)]
                break
        if name and types:
            out[name] = types
            if dex is not None:
                out[dex] = types
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


class Tactics:
    """Damage/type analysis for one live battle.

    ``badge_types`` are the attacking types boosted by earned badges
    (DoBadgeTypeBoosts); pass an empty set to model an enemy's turn, which
    never gets the boost (``hBattleTurn`` check, misc.asm:157).
    """

    def __init__(self, bdata, names, repo, badge_types=()):
        self.bdata = bdata
        self.names = names
        self.effects = parse_effects(repo)
        self.by_effect_id = {v: k for k, v in self.effects.items()}
        self.special_from = bdata.types["FIRE"]   # DEF SPECIAL EQU (line 26)
        self.badge_types = set(badge_types)
        self.species_types = parse_species_types(repo)

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
                    "type": None, "type_name": "?", "power": 0,
                    "effect": "?", "stab": False, "category": "physical",
                    "note": "no move data"}
            return self._summarise(view, defender)
        mtype, power, acc = rec["type"], rec["power"], rec["accuracy"]
        eff = self.by_effect_id.get(rec["effect"], "?")
        mult = self.bdata.effectiveness(mtype, defender["types"])
        stab = mtype in attacker["types"]
        view = {
            "move": name, "id": move_id, "type": mtype,
            "type_name": self._type_name(mtype), "power": power,
            "accuracy": acc, "effect": eff, "mult": mult, "stab": stab,
            "category": self.category(mtype), "note": "",
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
        accuracy, because listed accuracy still loses to MINIMIZE."""
        if v.get("pp") == 0:          # None means "PP unknown", 0 means empty
            return -1
        if v["kind"] in ("immune", "status", "unknown"):
            return 0
        hit = 1.0 if v.get("never_misses") else v["accuracy"] / 100
        if v["ko_certain"]:
            return 2_000 + 100 * hit
        return v["min"] * hit

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
        mine = self.my_moves(me, enemy, pp)
        threats = self.enemy_threats(me, enemy)
        faster = me["speed"] > enemy["speed"]
        best = mine[0] if mine else None
        worst = threats[0] if threats else None
        return {
            "me": me, "enemy": enemy, "moves": mine, "threats": threats,
            "faster": faster,
            "my_best": best, "their_best": worst,
            "i_die_next_turn": bool(worst and worst["min"] >= me["hp"]),
            "i_can_ko": bool(best and best["ko_certain"]),
            "turns_i_need": best["hits_to_ko"] if best else None,
            "turns_they_need": (
                None if not worst or worst["max"] <= 0
                else -(-me["hp"] // max(1, worst["min"] or worst["max"]))),
        }

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
            })
        return sorted(out, key=lambda m: m["score"], reverse=True)

    def recommend(self, analysis, frame=None, *, heal_at=0.3):
        """``(action, reason)`` for this turn -- the whole point of the
        module. Order of preference:

        1. a certain KO (highest accuracy wins), because a dead enemy
           deals no damage;
        2. healing, if I am about to be out-damaged and carry a potion;
        3. switching, if their best move kills me, I cannot kill them, and
           a party member resists what is coming;
        4. otherwise the best expected damage, ignoring immunities and
           empty slots.
        """
        moves = [m for m in analysis["moves"] if m.get("pp") != 0]
        live = [m for m in moves if m["max"] > 0]
        me = analysis["me"]
        their = analysis["their_best"]
        kos = [m for m in live if m["ko_certain"]]
        if kos:
            # Reliability first: an unmissable kill beats a listed-100% kill
            # (evasion still applies to the latter), which beats a bigger
            # but chancier one.
            pick = max(kos, key=lambda m: (bool(m.get("never_misses")),
                                           m["accuracy"], m["min"]))
            hits = ("unmissable" if pick.get("never_misses")
                    else f"{pick['accuracy']}% acc")
            return ("attack", pick["slot"]), (
                f"{pick['move']} KOs now ({pick['min']}-{pick['max']} vs "
                f"{analysis['enemy']['hp']} HP, x{pick['mult']:g}, {hits})")
        lethal = bool(their and their["min"] >= me["hp"])
        hurt = me["hp"] <= heal_at * me["max_hp"]
        # The frame's bag is keyed by normalised names ('FULLRESTORE'), so
        # match through norm_item rather than hoping about spacing.
        from .battle import norm_item
        bag = {norm_item(k): v for k, v in
               ((frame or {}).get("bag") or {}).items()}
        potion = next((n for n in ("FULL RESTORE", "MAX POTION",
                                   "HYPER POTION", "SUPER POTION", "POTION")
                       if bag.get(norm_item(n))), None)
        if hurt and potion and not lethal:
            return ("item", potion), (
                f"{me['hp']}/{me['max_hp']} HP with {potion} in the bag and "
                f"nothing lethal incoming")
        if lethal and not analysis["i_can_ko"]:
            best_switch = next(
                (s for s in self.switch_options(analysis, frame)
                 if s["incoming_mult"] < 1.0 and s["hp_frac"] > 0.5), None)
            if best_switch:
                return ("switch", best_switch["index"]), (
                    f"{their['move']} does {their['min']}-{their['max']} to "
                    f"my {me['hp']} HP; {best_switch['nickname']} resists it "
                    f"(x{best_switch['incoming_mult']:g})")
        if not live:
            status = [m for m in moves if m["kind"] == "status"]
            if status:
                return ("attack", status[0]["slot"]), (
                    f"no damaging move connects; {status[0]['move']} instead")
            return "flee", "nothing in this moveset can touch it"
        pick = live[0]
        why = (f"{pick['move']} x{pick['mult']:g} "
               f"{pick['min']}-{pick['max']} ({pick['pct_max']}% of its HP)"
               f", {pick['hits_to_ko']} hit(s) to KO")
        if lethal:
            why += f" -- but {their['move']} can kill me first"
        return ("attack", pick["slot"]), why

    def explain(self, analysis):
        """One-line-per-move audit of a `read()`."""
        me, en = analysis["me"], analysis["enemy"]
        lines = [
            f"me L{me['level']} {me['hp']}/{me['max_hp']} "
            f"{self._types_text(me['types'])} spd {me['speed']}"
            f"  vs  enemy L{en['level']} {en['hp']}/{en['max_hp']} "
            f"{self._types_text(en['types'])} spd {en['speed']}"
            f"   [{'I move first' if analysis['faster'] else 'they move first'}]"
        ]
        for v in analysis["moves"]:
            flag = ("KO" if v["ko_certain"] else
                    "ko?" if v["ko_possible"] else "  ")
            lines.append(
                f"  {flag} {v['move']:<13s} {v['type_name']:<8s}"
                f" {v['category'][:4]:<4s} x{v['mult']:<4g}"
                f" {v['min']:>3d}-{v['max']:<3d} ({v['pct_max']:>5.1f}%)"
                f" acc {v['accuracy']:>3d}"
                f"{' STAB' if v['stab'] else '     '}"
                f" {v['note']}")
        for v in analysis["threats"][:4]:
            lines.append(
                f"  <- {v['move']:<13s} {v['type_name']:<8s} x{v['mult']:<4g}"
                f" {v['min']:>3d}-{v['max']:<3d} on me"
                f"{'  LETHAL' if v['min'] >= analysis['me']['hp'] else ''}")
        return "\n".join(lines)
