"""Validation contracts at the machine boundaries.

Three places in this harness hand data to something that cannot inspect it:
``trek.Driver.observe()`` feeds a language model, the NDJSON servers
(``serve.py``, ``autopilot.py``) take decisions off a pipe, and
``journal/<session>.jsonl`` is read back by a later session that was not
running when it was written.  A malformed value at any of those points does
not fail there -- it becomes a plausible-looking wrong answer much later.
That is the failure class this project keeps paying for, so the boundary is
where it gets caught.

The shapes are not invented.  They mirror what the code actually produces:
``GameState.snapshot`` (pokeagent/state.py:403) plus the keys
``Driver.observe`` adds on top of it (trek.py:144), and the action table in
``pokeagent/registry.py``.  Action names are checked against
``registry.ACTIONS`` rather than a second list, because a duplicated
whitelist drifting into a ``NameError`` is precisely what the registry
docstring exists to prevent.  Argument-level and precondition checking is
NOT re-implemented here either: ``registry.check`` owns it, because it needs
the live driver to answer "are we in a battle right now".

Pydantic is deliberately not a dependency (see ``pyproject.toml``): the
whole contract is a few dozen typed fields, so it is spelled with
dataclasses and explicit checks.  Every validator returns its argument
unchanged on success and raises :class:`SchemaError` -- one sentence naming
the offending key, what was expected and what arrived -- on failure.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from pokeagent import registry


class SchemaError(ValueError):
    """A boundary rejection, phrased as a sentence a human can act on."""


@dataclass(frozen=True, slots=True)
class Field:
    """One key of an object contract.

    ``check`` runs only after the type matched, so it may assume the type.
    """

    name: str
    types: type | tuple[type, ...]
    required: bool = True
    check: Callable[[str, object], None] | None = None


def _tuple(types) -> tuple:
    return types if isinstance(types, tuple) else (types,)


def _typename(value) -> str:
    return "null" if value is None else type(value).__name__


def _expected(types) -> str:
    names = ["null" if t is type(None) else t.__name__ for t in _tuple(types)]
    return " or ".join(names)


def _is(value, types) -> bool:
    allowed = _tuple(types)
    # bool is a subclass of int, so an unguarded int field would accept True.
    # A flag arriving where a count belongs is exactly the kind of quiet
    # nonsense this module exists to stop.
    if isinstance(value, bool) and bool not in allowed:
        return False
    return isinstance(value, allowed)


def _object(where: str, value, fields: Sequence[Field], *, forbid_extra=True):
    """Validate a mapping against a field table. Returns it unchanged."""
    if not isinstance(value, Mapping):
        raise SchemaError(f"{where} must be an object, got {_typename(value)}")
    allowed = {f.name for f in fields}
    if forbid_extra:
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise SchemaError(
                f"{where} has unexpected key(s) {', '.join(unknown)}; "
                f"accepts {', '.join(sorted(allowed))}"
            )
    for f in fields:
        if f.name not in value:
            if f.required:
                raise SchemaError(f"{where} is missing required key {f.name!r}")
            continue
        v = value[f.name]
        if not _is(v, f.types):
            raise SchemaError(
                f"{where}.{f.name} must be {_expected(f.types)}, got {_typename(v)}"
            )
        if f.check is not None:
            f.check(f"{where}.{f.name}", v)
    return value


def _each(item_check: Callable[[str, object], None]) -> Callable[[str, object], None]:
    def run(where, seq):
        for i, item in enumerate(seq):
            item_check(f"{where}[{i}]", item)

    return run


def _strings(where, seq):
    for i, item in enumerate(seq):
        if not isinstance(item, str):
            raise SchemaError(f"{where}[{i}] must be a str, got {_typename(item)}")


def _sub(fields: Sequence[Field], *, forbid_extra=True):
    def run(where, value):
        _object(where, value, fields, forbid_extra=forbid_extra)

    return run


# -- observe() --------------------------------------------------------------
# trek.py:144 == GameState.snapshot (state.py:403) + tiles/npcs/ui extras.

_MOVE = (
    Field("name", str),
    Field("pp", int),
)

_MON = (
    Field("nickname", str),
    Field("species", str),
    Field("level", int),
    Field("hp", int),
    Field("max_hp", int),
    # status_name() returns None for a healthy mon (pokemon.py:78).
    Field("status", (str, type(None))),
    Field("egg", bool),
    Field("shiny", bool),
    Field("nature", str),
    Field("moves", list, check=_each(_sub(_MOVE))),
)

_LOCATION = (
    Field("map", str),
    Field("group", int),
    Field("num", int),
    Field("x", int),
    Field("y", int),
    Field("facing", str),
    # Added by observe(); masked to a nibble (gotcha 7).
    Field("elevation", int),
)

_PLAYER = (
    Field("name", str),
    Field("gender", str),
    Field("trainer_id", int),
    Field("money", int),
    Field("coins", int),
    Field("play_time", str),
    Field("badges", list, check=_strings),
)

_UI = (
    Field("battle", bool),
    Field("message", str),
    Field("scene", bool),
    Field("dialog", bool),
    Field("callback", str),
    Field("tasks", list, check=_strings),
)

_NPC = (
    Field("x", int),
    Field("y", int),
    Field("gfx", (int, str)),
)

_OBSERVE = (
    Field("frame", int),
    Field("location", dict, check=_sub(_LOCATION)),
    Field("player", dict, check=_sub(_PLAYER)),
    Field("ui", dict, check=_sub(_UI)),
    Field("party", list, check=_each(_sub(_MON))),
    Field("bag", dict),
    Field("tiles", dict),
    Field("npcs", list, check=_each(_sub(_NPC))),
    # Present only while a battle is live (state.py:430).
    Field("battle", dict, required=False),
)


def validate_observe(obs: dict) -> dict:
    """The snapshot a decider reasons from. Returns it unchanged."""
    _object("observe", obs, _OBSERVE)
    _validate_bag("observe.bag", obs["bag"])
    return obs


def _validate_bag(where: str, bag) -> None:
    """``{pocket: {item_name: quantity}}`` (state.py:285). Pocket and item
    names come from the decomp's item table, so they are not enumerated
    here -- only the shape is."""
    for pocket, items in bag.items():
        if not isinstance(pocket, str):
            raise SchemaError(f"{where} pocket key must be a str, got {_typename(pocket)}")
        if not isinstance(items, Mapping):
            raise SchemaError(
                f"{where}.{pocket} must be an object of item->count, "
                f"got {_typename(items)}"
            )
        for item, qty in items.items():
            if not isinstance(item, str):
                raise SchemaError(
                    f"{where}.{pocket} item key must be a str, got {_typename(item)}"
                )
            if not _is(qty, int):
                raise SchemaError(
                    f"{where}.{pocket}.{item} must be an int, got {_typename(qty)}"
                )


# -- NDJSON envelopes -------------------------------------------------------

_REQUEST = (
    Field("id", (int, str, type(None)), required=False),
    Field("cmd", str),
    Field("args", dict, required=False),
)


def validate_request(req: dict, commands: Sequence[str]) -> dict:
    """One line off the pipe. ``commands`` is the surface's own verb list, so
    an unknown ``cmd`` is rejected with the list a caller can actually use."""
    _object("request", req, _REQUEST)
    if req["cmd"] not in commands:
        raise SchemaError(
            f"unknown cmd {req['cmd']!r}; expected one of {'|'.join(commands)}"
        )
    return req


# -- autopilot decisions ----------------------------------------------------

_ACTION = (
    Field("name", str),
    Field("kwargs", dict, required=False),
)

_SUCCESS = (
    Field("map", str, required=False),
    Field("min_badges", int, required=False),
    Field("flag", str, required=False),
)

_DECISION = (
    Field("action", dict),
    Field("goal", (str, type(None)), required=False),
    Field("risky", bool, required=False),
    Field("success", dict, required=False),
)


def validate_action(action: dict) -> dict:
    """``{name, kwargs}`` -- shape plus membership in ``registry.ACTIONS``.

    Argument names and battle preconditions are deliberately NOT checked
    here: ``registry.check`` does that against the live driver, and a second
    copy of those rules would drift away from the table it is copying.
    """
    _object("action", action, _ACTION)
    name = action["name"]
    if name not in registry.ACTIONS:
        raise SchemaError(
            f"action.name {name!r} is not an action; expected one of "
            f"{' '.join(sorted(registry.ACTIONS))}"
        )
    return action


def validate_decision(args: dict) -> dict:
    """One autopilot ``decision`` payload, checked BEFORE anything runs."""
    _object("decision", args, _DECISION)
    validate_action(args["action"])
    if "success" in args:
        _object("decision.success", args["success"], _SUCCESS)
    return args


# -- journal ----------------------------------------------------------------

_CYCLE = (
    Field("t", str),
    Field("wall_s", (int, float)),
    Field("frame", int),
    Field("used", int),
    Field("action", dict, check=_sub(_ACTION)),
    Field("goal", (str, type(None))),
    Field("ok", bool),
    Field("digest", dict),
    Field("lead_level", (int, type(None))),
    Field("error", (str, type(None)), required=False),
    Field("why", list, required=False, check=_strings),
    # Set only when this very record failed validation; see autopilot's
    # _journal_cycle, which must never let schema drift skip the rails.
    Field("record_schema_error", str, required=False),
)


def validate_cycle_record(record: dict) -> dict:
    """One journal line per decision cycle: wall-clock, frame spend, the
    action as executed, and the digest the stuck detector compared."""
    return _object("cycle", record, _CYCLE)
