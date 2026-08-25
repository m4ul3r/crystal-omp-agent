"""The model-facing decision surface for battles.

Everything here exists because the harness kept deciding battles the model
never saw (session claude-wren):

- ~80 wild encounters in one run, ~78 auto-KO'd by the default policy: the
  model was never asked "KO / catch / flee".
- A ping-pong switch policy fed Koga ~10 free switch-in hits and wiped five
  of six mons -- and there was NO per-turn record to diagnose it from.
- Model-written policies kept returning None ("harness, you pick") because
  assembling the state a decision needs -- my mon, the enemy, the party, the
  bag, what a move actually does to THIS enemy -- was ~30 lines of
  game_state()/observe() plumbing per policy, every turn.

So: `battle_frame()` assembles that state in ONE call, `TurnLog` keeps the
append-only per-turn record (with `free_hits()`, the number nobody was
counting during the Koga wipe), `explain()` renders a frame as one line, and
`DecisionRequired` is how the plumbing says "the MODEL has to answer this"
instead of quietly picking.

Vocabulary (shared with trek.py):

    battle DECISION   ('attack', slot) | ('switch', party_index)
                      | ('item', ITEM_NAME) | ('ball', BALL_NAME) | 'flee'
    encounter DISPOSITION (wilds, decided once when the wild appears)
                      'ko' | 'catch' | 'flee' | ('ball', BALL_NAME)
"""

import logging

from .battle import Battle, norm_item
from .state import EGG, MON_NAME_LENGTH, _status

log = logging.getLogger("trek")

# actor values that mean "our side took this turn"
_ME_ACTORS = frozenset(("me", "player", "us", "self"))

# decision kinds that hand the turn to the enemy without damaging it: the
# enemy's move lands and our side deals nothing (switch-ins, item uses,
# ball throws). Exactly the shape of the Koga wipe.
_CEDING_KINDS = frozenset(("switch", "item", "ball"))


class DecisionRequired(RuntimeError):
    """Raised when the harness reaches a point it must NOT decide alone --
    a wild encounter with no disposition, a battle with no policy. Carries
    the frame (or encounter dict) that the model needs to answer, so the
    caller can print it instead of guessing.

    `kind` names the question ('encounter', 'battle', 'learn', ...) and
    `options` lists the legal answers."""

    def __init__(self, message, frame=None, kind=None, options=()):
        super().__init__(message)
        self.frame = frame
        self.kind = kind
        self.options = tuple(options)


class Bag(dict):
    """{item name: quantity} whose lookups ignore spelling. The pack's own
    names come out of the ROM ('POKé BALL' with the game glyph, 'SUPER
    POTION'), while policies say 'POKE BALL' / 'Super Potion' / 'GREATBALL'
    -- every one of those finds the entry (norm_item, the same
    normalisation the pack cursor uses)."""

    def _resolve(self, name):
        if not isinstance(name, str):
            return None
        want = norm_item(name)
        for k in self:
            if norm_item(k) == want:
                return k
        return None

    def __contains__(self, name):
        if dict.__contains__(self, name):
            return True
        return self._resolve(name) is not None

    def __getitem__(self, name):
        try:
            return dict.__getitem__(self, name)
        except KeyError:
            key = self._resolve(name)
            if key is None:
                raise
            return dict.__getitem__(self, key)

    def get(self, name, default=None):
        try:
            return self[name]
        except KeyError:
            return default

    def quantity(self, name):
        """How many are held (0 when absent) -- no None handling at the
        call site, so `if frame['bag'].quantity('SUPER POTION') >= 2` works."""
        return self.get(name, 0)


# -- WRAM readers -------------------------------------------------------------

_POCKETS = {"items": ("wNumItems", "wItems"),
            "balls": ("wNumBalls", "wBalls")}


def read_bag(emu, names, pockets=("items", "balls")):
    """The two pockets the battle pack can open, as one Bag. Entries are
    (id, quantity) pairs in WRAM (same layout battle.bag_item_index walks);
    the key pocket is deliberately skipped -- nothing in it is usable in a
    battle."""
    bag = Bag()
    for pocket in pockets:
        count_sym, list_sym = _POCKETS[pocket]
        try:
            count = min(emu.read_u8(count_sym), 20)
            bank, addr = emu.sym[list_sym]
            raw = emu.read((bank, addr), count * 2)
        except Exception:
            continue
        for i in range(count):
            item_id, qty = raw[i * 2], raw[i * 2 + 1]
            if not item_id or item_id == 0xFF:
                continue
            name = names.items.get(item_id, f"ITEM_{item_id}")
            bag[name] = bag.get(name, 0) + qty
    return bag


def read_party(emu, names):
    """[{index, nickname, species, species_id, level, hp, max_hp, status,
    fainted, egg}] straight from wPartyMon1..6 + wPartyMonNicknames.

    Only the fields a battle decision needs (state.game_state() is the full
    version -- DVs, shininess, held items, schema validation -- and is far
    too much machinery to run every turn)."""
    sym = emu.sym
    stride = sym.offset("wPartyMon2", "wPartyMon1")
    off = lambda f: sym.offset("wPartyMon1" + f, "wPartyMon1")
    bank, base = sym["wPartyMon1"]
    nick_bank, nick_base = sym["wPartyMonNicknames"]
    count = min(emu.read_u8("wPartyCount"), 6)
    slots = emu.read("wPartySpecies", count) if count else b""
    party = []
    for i in range(count):
        rd = lambda f, n=1: emu.read((bank, base + i * stride + off(f)), n)
        species = rd("Species")[0]
        hp = int.from_bytes(rd("HP", 2), "big")
        party.append({
            "index": i,
            "nickname": emu.charmap.decode(
                emu.read((nick_bank, nick_base + i * MON_NAME_LENGTH),
                         MON_NAME_LENGTH)),
            "species": names.species.get(species, "?"),
            "species_id": species,
            "level": rd("Level")[0],
            "hp": hp,
            "max_hp": int.from_bytes(rd("MaxHP", 2), "big"),
            "status": _status(rd("Status")[0]),
            "fainted": hp <= 0,
            "egg": bool(slots[i:i + 1]) and slots[i] == EGG,
        })
    return party


def _party_entry(mon, index):
    """Normalise a state.game_state()-shaped party mon into the frame's
    shape, so callers can hand us the party they already read. game_state
    keys the id as 'species' and the name as 'name'; read_party() above
    keys them 'species_id'/'species' -- both land here."""
    hp = mon.get("hp", 0)
    sp, name = mon.get("species"), mon.get("name")
    if isinstance(sp, int):
        species, species_id = name or "?", sp
    else:
        species, species_id = sp or name or "?", mon.get("species_id")
    return {
        "index": mon.get("index", index),
        "nickname": mon.get("nickname", ""),
        "species": species,
        "species_id": species_id,
        "level": mon.get("level", 0),
        "hp": hp,
        "max_hp": mon.get("max_hp", 0),
        "status": list(mon.get("status") or []),
        "fainted": bool(mon.get("fainted", hp <= 0)),
        "egg": bool(mon.get("egg", False)),
    }


def _type_names(bdata, type_ids):
    """Type ids -> names ('WATER'), duplicates collapsed. Gen 2 stores a
    mono-type mon's single type twice; the engine counts it once
    (CheckTypeMatchup), so the frame shows it once."""
    lookup = {}
    if bdata is not None:
        lookup = {v: k for k, v in getattr(bdata, "types", {}).items()}
    return [lookup.get(t, f"TYPE_{t}") for t in dict.fromkeys(type_ids)]


def _turn_counter(emu):
    """Turns the ACTIVE mon has taken (wPlayerTurnsTaken; the engine zeroes
    it on every send-out, NewBattleMonStatus). 0 on a fresh switch-in, which
    is exactly what a decision wants to know."""
    try:
        return emu.read_u8("wPlayerTurnsTaken")
    except Exception:
        return 0


# -- the frame ----------------------------------------------------------------

def battle_frame(emu_or_battle, names=None, bdata=None, party=None, *,
                 battle=None, turn=None):
    """Everything one battle decision needs, in one call.

        {'me': {...}, 'enemy': {...}, 'party': [...], 'bag': {...},
         'turn': int, 'wild': bool, 'can_switch': [party_index, ...],
         'moves': [{'slot', 'name', 'type', 'power', 'pp', 'effect_mult'}]}

    'me'/'enemy' carry nickname, species (NAME; 'species_id' is the number),
    level, hp, max_hp, types (names) and status. 'effect_mult' is the type
    effectiveness of that move against the CURRENT enemy -- the number
    policies were computing by hand, or more often not at all.
    'can_switch' lists party indexes that are alive, not an EGG and not
    already out; ('switch', i) for anything else is rejected by
    Battle._invalid_action_reason.

    First argument is polymorphic: pass a live Battle (`battle_frame(b)`) or
    the long form `battle_frame(emu, names, bdata)`. `party` accepts an
    already-read party list (e.g. state.game_state()['party']) to skip the
    WRAM re-read; `turn` overrides the turn counter for callers keeping
    their own."""
    b = battle
    emu = emu_or_battle
    if callable(getattr(emu_or_battle, "me", None)):
        b = emu_or_battle
        emu = b.emu
        names = names if names is not None else b.names
        bdata = bdata if bdata is not None else b.data
    if b is None:
        b = Battle(emu, names, bdata)
    if bdata is None:
        bdata = b.data

    me, enemy = b.me(), b.enemy()
    enemy_type_ids = list(enemy["types"])

    moves = []
    for slot, (mid, pp) in enumerate(me["moves"]):
        mv = (getattr(bdata, "moves", {}) or {}).get(mid) if bdata else None
        mtype = mv["type"] if mv else None
        moves.append({
            "slot": slot,
            "name": names.moves.get(mid, f"MOVE_{mid}"),
            "type": _type_names(bdata, [mtype])[0] if mtype is not None
            else None,
            "power": mv["power"] if mv else None,
            "pp": pp,
            "effect_mult": bdata.effectiveness(mtype, enemy_type_ids)
            if (mv and bdata is not None) else None,
        })

    if party is None:
        roster = read_party(emu, names)
    else:
        roster = [_party_entry(mon, i) for i, mon in enumerate(party)]

    active = me["party_slot"]

    # The encounter hook fires BEFORE the battle mon block is populated, so
    # b.me() can read back as a blank L0 0/0 mon. A disposition policy that
    # compares its own level against the enemy then flees a winnable fight
    # (observed live: fled a L34 Graveler because 'me' read as L0). The
    # roster from game_state is always real -- stand in with its active mon.
    if not (me.get("level") or 0) and not (me.get("max_hp") or 0):
        stand_in = next((m for m in roster
                         if m["index"] == active and not m["fainted"]), None) \
            or next((m for m in roster
                     if not m["fainted"] and not m["egg"]), None)
        if stand_in:
            me = dict(me)
            me.update({
                "nickname": stand_in.get("nickname") or me.get("nickname"),
                "name": stand_in.get("species") or me.get("name"),
                "species": stand_in.get("species_id") or me.get("species"),
                "level": stand_in.get("level") or 0,
                "hp": stand_in.get("hp") or 0,
                "max_hp": stand_in.get("max_hp") or 0,
                "party_slot": stand_in["index"],
            })
            active = stand_in["index"]

    can_switch = [mon["index"] for mon in roster
                  if not mon["fainted"] and not mon["egg"]
                  and mon["index"] != active]

    try:
        wild = emu.read_u8("wBattleMode") == 1
    except Exception:
        wild = False

    return {
        "me": {
            "nickname": me["nickname"],
            "species": me["name"],
            "species_id": me["species"],
            "party_slot": active,
            "level": me["level"],
            "hp": me["hp"],
            "max_hp": me["max_hp"],
            "types": _type_names(bdata, me["types"]),
            "status": list(me.get("status") or []),
        },
        "enemy": {
            "nickname": enemy["nickname"],
            "species": enemy["name"],
            "species_id": enemy["species"],
            "level": enemy["level"],
            "hp": enemy["hp"],
            "max_hp": enemy["max_hp"],
            "types": _type_names(bdata, enemy_type_ids),
            "status": list(enemy.get("status") or []),
        },
        "party": roster,
        "bag": read_bag(emu, names),
        "turn": _turn_counter(emu) if turn is None else turn,
        "wild": wild,
        "can_switch": can_switch,
        "moves": moves,
    }


def _vitals(mon):
    st = ("+" + ",".join(mon["status"])) if mon.get("status") else ""
    types = "/".join(mon.get("types") or []) or "?"
    return (f"{mon['species']} L{mon['level']} "
            f"{mon['hp']}/{mon['max_hp']} {types}{st}")


def _move_text(m):
    """'0:SURF/95p/pp15/x4' -- slot, name, power, PP left, effectiveness
    against the mon actually standing there."""
    power = f"/{m['power']}p" if m.get("power") else ""
    mult = m.get("effect_mult")
    eff = "" if mult is None else "/x{:g}".format(mult)
    return f"{m['slot']}:{m['name']}{power}/pp{m['pp']}{eff}"


def explain(frame, max_bag=6):
    """One compact line for a frame -- the log/transcript rendering of
    everything a decision is being made on."""
    me, enemy = frame["me"], frame["enemy"]
    nick = me["nickname"]
    mine = _vitals(me)
    if nick and nick != me["species"]:
        mine = f"{nick}({mine})"
    moves = " ".join(_move_text(m) for m in frame["moves"]) or "none"
    items = list(frame["bag"].items())
    bag = ", ".join(f"{n} x{q}" for n, q in items[:max_bag]) or "empty"
    if len(items) > max_bag:
        bag += f" (+{len(items) - max_bag} more)"
    return ("T{} {} | me {} | enemy {} | moves {} | switch {} | bag {}"
            .format(frame["turn"], "wild" if frame["wild"] else "trainer",
                    mine, _vitals(enemy), moves,
                    frame["can_switch"] or "none", bag))


# -- the per-turn record ------------------------------------------------------

def snapshot(battle):
    """{'my_hp', 'enemy_hp', 'enemy_species'} for one side of a turn. Never
    raises: a battle that has already ended still gets a row (Nones)."""
    try:
        return battle.hp_snapshot()
    except Exception:
        return {"my_hp": None, "enemy_hp": None, "enemy_species": None}


class _TurnRecorder:
    """Context manager from TurnLog.turn(): snapshots vitals on entry, runs
    the turn, snapshots again on exit and files the row. Set `.action` /
    `.note` inside the block."""

    def __init__(self, tlog, battle, actor, turn, action, note):
        self.log, self.battle, self.actor = tlog, battle, actor
        self.turn, self.action, self.note = turn, action, note
        self.row = None
        self._before = None

    def __enter__(self):
        self._before = snapshot(self.battle)
        return self

    def __exit__(self, exc_type, exc, tb):
        after = snapshot(self.battle)
        note = self.note
        if exc is not None:
            note = f"{note} raised {exc_type.__name__}: {exc}".strip()
        self.row = self.log.record(
            actor=self.actor, action=self.action, turn=self.turn,
            enemy_species=self._before.get("enemy_species"),
            enemy_hp_before=self._before.get("enemy_hp"),
            enemy_hp_after=after.get("enemy_hp"),
            my_hp_before=self._before.get("my_hp"),
            my_hp_after=after.get("my_hp"),
            note=note)
        return False       # never swallow the caller's exception


def _kind(action):
    """Decision kind of an action: 'attack' | 'switch' | 'item' | 'ball' |
    'flee' | None."""
    if isinstance(action, (tuple, list)) and action:
        return str(action[0]).lower()
    if isinstance(action, str):
        return action.lower()
    return None


def _arg(action):
    if isinstance(action, (tuple, list)) and len(action) > 1:
        return action[1]
    return None


class TurnLog:
    """Append-only per-turn record of a battle.

    Rows are exactly:
        {'turn', 'actor', 'action', 'enemy_species', 'enemy_hp_before',
         'enemy_hp_after', 'my_hp_before', 'my_hp_after', 'note'}

    The Koga wipe (five of six mons lost to a ping-pong switch policy) was
    invisible because nothing recorded this and nobody counted free_hits()."""

    FIELDS = ("turn", "actor", "action", "enemy_species",
              "enemy_hp_before", "enemy_hp_after",
              "my_hp_before", "my_hp_after", "note")

    def __init__(self):
        self._rows = []

    def __len__(self):
        return len(self._rows)

    def record(self, actor="me", action=None, turn=None, enemy_species=None,
               enemy_hp_before=None, enemy_hp_after=None,
               my_hp_before=None, my_hp_after=None, note=""):
        """File one turn. `turn` defaults to the next 1-based index.
        Returns the stored row (append-only: it is never rewritten)."""
        row = {
            "turn": len(self._rows) + 1 if turn is None else turn,
            "actor": actor,
            "action": action,
            "enemy_species": enemy_species,
            "enemy_hp_before": enemy_hp_before,
            "enemy_hp_after": enemy_hp_after,
            "my_hp_before": my_hp_before,
            "my_hp_after": my_hp_after,
            "note": note or "",
        }
        self._rows.append(row)
        return row

    def turn(self, battle, actor="me", turn=None, action=None, note=""):
        """`with tlog.turn(b, actor='me') as t:` -- vitals before/after are
        snapshotted for you; assign t.action / t.note inside the block."""
        return _TurnRecorder(self, battle, actor, turn, action, note)

    def rows(self):
        """Copies, so a reader can never mutate the record."""
        return [dict(r) for r in self._rows]

    # -- accounting ---------------------------------------------------------

    @staticmethod
    def _dealt_damage(row):
        before, after = row["enemy_hp_before"], row["enemy_hp_after"]
        if before is None or after is None:
            return False
        return after < before

    @staticmethod
    def _took_damage(row):
        before, after = row["my_hp_before"], row["my_hp_after"]
        if before is None or after is None:
            return False
        return after < before

    @classmethod
    def is_free_hit(cls, row):
        """A turn our side spent WITHOUT damaging the enemy while the enemy
        got its move: a switch-in, an item/ball turn, or an attack that
        landed nothing while we took damage. Ten of these in a row is what
        Koga's gym actually was."""
        if row["actor"] not in _ME_ACTORS:
            return False              # the enemy's own row, not a free hit
        if cls._dealt_damage(row):
            return False
        return _kind(row["action"]) in _CEDING_KINDS or cls._took_damage(row)

    def free_hit_rows(self):
        return [dict(r) for r in self._rows if self.is_free_hit(r)]

    def free_hits(self):
        """How many turns the enemy got for free. Print it after every
        battle: it is the one number that makes a ping-pong policy obvious."""
        return sum(1 for r in self._rows if self.is_free_hit(r))

    # -- rendering ----------------------------------------------------------

    @staticmethod
    def _action_text(action):
        kind = _kind(action)
        if kind is None:
            return "-"
        arg = _arg(action)
        return kind if arg is None else f"{kind}:{arg}"

    @classmethod
    def _line(cls, row):
        def hp(a, b):
            a = "?" if a is None else a
            b = "?" if b is None else b
            return f"{a}->{b}"
        line = ("T{} {} {} | enemy#{} {} | me {}".format(
            row["turn"], row["actor"], cls._action_text(row["action"]),
            "?" if row["enemy_species"] is None else row["enemy_species"],
            hp(row["enemy_hp_before"], row["enemy_hp_after"]),
            hp(row["my_hp_before"], row["my_hp_after"])))
        if cls.is_free_hit(row):
            line += " | FREE HIT"
        if row["note"]:
            line += " | " + " ".join(str(row["note"]).split())
        return line

    def summary(self):
        """The whole record, one line per turn (no trailing newline)."""
        return "\n".join(self._line(r) for r in self._rows)
