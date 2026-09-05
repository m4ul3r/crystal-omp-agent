"""The action table: every verb a decider may invoke, validated once.

Port of ``crystalagent/registry.py``, and it exists for the same reason its
docstring gives: three drifting whitelists is how that project shipped a
``NameError`` in its NDJSON server and a silently-no-op shop command. One
table, one validator, no side doors.

Validation happens BEFORE execution and against LIVE state, not against what
the decider believed two cycles ago. Rejection is information -- a human
sentence naming what was wrong -- not an obstacle.
"""

import logging
from dataclasses import dataclass, field

log = logging.getLogger("pokeagent.registry")


@dataclass(frozen=True, slots=True)
class Action:
    name: str
    required: tuple = ()
    optional: tuple = ()
    #: None = don't care, True = must be in battle, False = must not be.
    need_battle: bool | None = None
    #: Driver attribute to call; defaults to `name`.
    method: str | None = None
    doc: str = ""


ACTIONS = {
    a.name: a
    for a in (
        # -- observation -------------------------------------------------
        Action("observe", doc="full state snapshot incl. tiles and NPCs"),
        Action("status", doc="one-line summary"),
        Action("map_view", optional=("map_name",), method="render_map",
               doc="ASCII map; decide from find_tiles/exits instead"),
        Action("find_tiles", required=("kind",), optional=("map_name",),
               doc="absolute coordinates of every cell of a terrain kind"),
        Action("exits", optional=("map_name",), doc="warps and edge connections"),
        Action("live_npcs", doc="NPC positions as the engine has them now"),
        # -- movement ----------------------------------------------------
        Action("goto", ("x", "y"), ("map_name", "label"), need_battle=False,
               doc="pathfind and walk to a cell on this map"),
        Action("walk", ("path",), (), need_battle=False,
               doc="walk a literal direction string, verifying each step"),
        Action("step_dir", ("d",), ("verify",), need_battle=False,
               doc="one step"),
        Action("take_warp", ("x", "y"), (), need_battle=False,
               doc="enter a warp tile from an adjacent cell, holding the key"),
        Action("travel", ("dest_map",), ("max_legs",), need_battle=False,
               doc="cross maps over the warp/connection graph"),
        Action("talk_to", ("x", "y"), ("facing",), need_battle=False,
               doc="face an NPC and press A"),
        # -- scenes and menus --------------------------------------------
        Action("settle", (), ("max_frames", "quiet")),
        Action("advance_scene", (), ("max_frames", "stall_rounds"),
               doc="run a cutscene out, pressing A only when it stalls"),
        Action("drain_scene", (), ("max_frames",),
               doc="wait out a cutscene without pressing anything"),
        Action("flush_dialog", (), ("max_frames",)),
        Action("press", ("seq",), doc="raw input DSL; prefer a real verb"),
        Action("resolve_choice", (), ("choice",), doc="answer an open YES/NO box"),
        # -- battle ------------------------------------------------------
        Action("fight", (), ("policy", "max_frames"), need_battle=True,
               doc="play the current battle out"),
        Action("battle_frame", (), (), need_battle=True,
               doc="everything about the current turn in one read"),
        Action("outlook", (), (), need_battle=True,
               doc="every move scored with the game's own damage formula"),
        Action("recommend", (), (), need_battle=True,
               doc="a suggested action plus the reason for it"),
        Action("attack", ("slot",), (), need_battle=True),
        Action("switch_to", ("party_index",), (), need_battle=True),
        Action("use_battle_item", ("item_name",), (), need_battle=True),
        Action("throw_ball", (), ("ball",), need_battle=True),
        Action("flee", (), (), need_battle=True),
        # -- collection ------------------------------------------------
        Action("missables", (), ("kind",), doc="un-collected key items and HMs, live"),
        Action("field_moves", (), (), doc="per HM, who in the party knows it"),
        Action("needs_flash", (), ("map_name",), doc="is this map dark"),
        # -- healing ---------------------------------------------------
        Action("heal", (), ("tries",), need_battle=False,
               doc="talk to the nurse; verified by HP, not by the jingle"),
        Action("heal_at_nearest_center", (), ("max_hops",), need_battle=False,
               doc="route to the closest Pokemon Centre and heal"),
        # -- checkpoints ---------------------------------------------------
        Action("save", (), ("path",)),
    )
}


def check(driver, name, kwargs):
    """Validate a decision. Raises ValueError with a human sentence."""
    if name not in ACTIONS:
        raise ValueError(
            f"unknown action {name!r}; expected one of {' '.join(sorted(ACTIONS))}"
        )
    if kwargs is None:
        kwargs = {}
    if not isinstance(kwargs, dict):
        raise ValueError(f"{name}: kwargs must be an object, got {type(kwargs).__name__}")

    act = ACTIONS[name]
    allowed = set(act.required) | set(act.optional)
    unknown = set(kwargs) - allowed
    if unknown:
        raise ValueError(
            f"{name}: unknown argument(s) {', '.join(sorted(unknown))}; "
            f"accepts {', '.join(sorted(allowed)) or 'nothing'}"
        )
    missing = set(act.required) - set(kwargs)
    if missing:
        raise ValueError(f"{name}: missing required argument(s) {', '.join(sorted(missing))}")

    if act.need_battle is not None:
        # Checked against a FRESH read, never against a cached snapshot.
        live = driver.in_battle()
        if act.need_battle and not live:
            raise ValueError(f"{name}: needs an active battle (ui.battle=False)")
        if not act.need_battle and live:
            raise ValueError(f"{name}: cannot run during a battle (ui.battle=True)")
    return act


def callable_for(driver, name):
    act = ACTIONS[name]
    target = getattr(driver, act.method or name, None)
    if target is None:
        raise ValueError(
            f"{name} is in the action table but Driver has no {act.method or name}()"
        )
    return target


def resolve(driver, name, kwargs=None):
    """Validate then execute. The single entry point for every surface."""
    act = check(driver, name, kwargs or {})
    fn = callable_for(driver, name)
    result = fn(**(kwargs or {}))
    reason = getattr(driver, f"last_{name}_reason", None)
    if result is False and reason:
        log.info("%s failed: %s", name, reason)
    return result


def describe() -> list[dict]:
    """The table, for a decider that wants to know what it may ask for."""
    return [
        {
            "name": a.name,
            "required": list(a.required),
            "optional": list(a.optional),
            "precondition": (
                None if a.need_battle is None
                else ("in battle" if a.need_battle else "not in battle")
            ),
            "doc": a.doc,
        }
        for a in sorted(ACTIONS.values(), key=lambda a: a.name)
    ]
