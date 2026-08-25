"""One registry of driver actions: the single source of truth shared by
trek's CLI, serve.py's `run`, and autopilot decisions.

Each entry binds a verb to a Driver method, its keyword contract, and an
optional precondition evaluated against live game state -- so a bad decision
is rejected with a sentence instead of corrupting play.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Action:
    name: str                        # verb used by serve/autopilot decisions
    method: str = ""                 # Driver attribute (default: same as name)
    required: tuple = ()             # kwargs the caller must pass
    optional: tuple = ()             # extra kwargs accepted; others rejected
    need_battle: bool | None = None  # True: only in battle; False: only outside
    fn: object | None = None         # non-Driver callable taking (d)


def _heal(d):
    # lazy import: trek (repo root) imports siblings of this package
    import trek
    return trek.heal_pokecenter(d)


ACTIONS = {
    a.name: a for a in (
        Action("goto", required=("x", "y"), optional=("label", "map_name"),
               need_battle=False),
        Action("walk", required=("path",), optional=("label",),
               need_battle=False),
        Action("fight", optional=("max_frames", "policy"), need_battle=True),
        Action("catch", optional=("ball", "max_balls", "nickname"),
               need_battle=True),
        Action("heal", fn=_heal),
        Action("talk_to", required=("x", "y"), optional=("label", "facing"),
               need_battle=False),
        Action("mart_buy", required=("x", "y", "item_name"),
               optional=("qty", "label"), need_battle=False),
        Action("use_item", required=("item_name",),
               optional=("target_slot", "field"), need_battle=False),
        Action("settle", optional=("quiet", "spacing", "max_frames")),
        Action("drain_scene", optional=("max_frames",)),
        Action("catch_up", optional=("nickname", "ball", "max_balls",
                                     "max_encounters", "label"),
               need_battle=False),
        Action("resolve_choice", optional=("choice",)),
        Action("who_fights", need_battle=True),
        Action("gym_scout", required=("map",)),
        Action("route", required=("dest_map",), optional=("max_cost",),
               need_battle=False),
        Action("travel", required=("dest_map",), optional=("label",),
               need_battle=False),
        Action("step_dir", required=("mv",), optional=("max_frames",),
               need_battle=False),
        Action("press", required=("seq",)),
        Action("use_cut", required=("tree_x", "tree_y"),
               optional=("label", "forget_move"), need_battle=False),
    )
}


def check(d, name, kwargs):
    """Validate a decision without executing it. Raises ValueError with a
    human sentence on unknown action, bad kwargs, or failed precondition."""
    act = ACTIONS.get(name)
    if act is None:
        raise ValueError(f"unknown action {name!r}; "
                         f"allowed: {', '.join(sorted(ACTIONS))}")
    if kwargs is None:
        kwargs = {}
    if not isinstance(kwargs, dict):
        raise ValueError(f"{name}: 'kwargs' must be an object")
    unknown = sorted(set(kwargs) - set(act.required) - set(act.optional))
    if unknown:
        raise ValueError(f"{name}: unknown argument(s) {unknown}; accepts "
                         f"required {list(act.required)}, optional "
                         f"{list(act.optional)}")
    missing = [k for k in act.required if k not in kwargs]
    if missing:
        raise ValueError(f"{name}: missing required argument(s) {missing}")
    if act.need_battle is not None:
        in_battle = bool((d.observe().get("ui") or {}).get("battle"))
        if in_battle != act.need_battle:
            want = "an active battle" if act.need_battle else "no active battle"
            raise ValueError(f"{name}: needs {want} (ui.battle={in_battle})")


def callable_for(d, name):
    """The bound callable for a validated action name."""
    act = ACTIONS[name]
    if act.fn is not None:
        return lambda **kw: act.fn(d)
    return getattr(d, act.method or name)


def resolve(d, name, kwargs):
    """check() + execute; returns whatever the driver method returns."""
    check(d, name, kwargs)
    return callable_for(d, name)(**(kwargs or {}))
