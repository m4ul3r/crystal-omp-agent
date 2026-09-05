"""One place the widget and the harness agree on what the user wants.

The widget already has a settings UI: omarchy renders it from the `schema`
block in `manifest.json` and persists the answers into
`~/.config/omarchy/shell.json`, inside the bar-layout entry whose `id` is
`poke.run`. So rather than invent a second settings system with its own file
and its own drift, the harness READS THAT ENTRY. Click the widget, change a
value, and the next run picks it up.

Anything not set falls back to `DEFAULTS`, and a repo-local
`settings.json` overrides both -- that path exists so the harness is usable
without omarchy installed at all, which matters for CI and for anyone running
this upstream.

Precedence, lowest to highest:

    DEFAULTS  <  shell.json (the widget's UI)  <  ./settings.json  <  env

The risk setting deserves a note. It is one slider from 0 (cautious) to 1
(reckless) and it moves several thresholds together, because "how brave is
this run" is one decision a human makes, not four. At 0 the party heals at
70% and never leads with a mon below full; at 1 it heals at 20% and will send
a half-dead laggard in for the experience.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

SHELL_JSON = Path(
    os.environ.get("OMARCHY_SHELL_JSON", Path.home() / ".config/omarchy/shell.json")
)
LOCAL_JSON = Path(__file__).resolve().parents[1] / "settings.json"
PLUGIN_ID = "poke.run"

DEFAULTS = {
    # Which game, and where its ROM is. Empty means "use the built one".
    "game": "sapphire",
    "romPath": "",
    # Run identity.
    "trainerName": "RUBI",
    "starter": "TORCHIC",
    "nicknames": True,
    # 0 = cautious, 1 = reckless. See the module docstring.
    "risk": 0.35,
}

#: Environment overrides, for scripted runs and tests.
ENV = {
    "game": "POKEAGENT_GAME",
    "romPath": "POKEAGENT_ROM",
    "trainerName": "POKEAGENT_TRAINER",
    "starter": "POKEAGENT_STARTER",
    "risk": "POKEAGENT_RISK",
}


def _from_shell() -> dict:
    """The widget's own settings, as omarchy persisted them."""
    try:
        data = json.loads(SHELL_JSON.read_text())
    except Exception as err:  # noqa: BLE001 - omarchy may not be installed
        log.debug("no shell.json (%s)", err)
        return {}
    found = {}

    def walk(node):
        if isinstance(node, dict):
            if node.get("id") == PLUGIN_ID:
                found.update({k: v for k, v in node.items() if k != "id"})
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return found


def _from_local() -> dict:
    try:
        return json.loads(LOCAL_JSON.read_text())
    except Exception:  # noqa: BLE001
        return {}


def _from_env() -> dict:
    out = {}
    for key, var in ENV.items():
        raw = os.environ.get(var)
        if raw is None or raw == "":
            continue
        out[key] = float(raw) if key == "risk" else raw
    if os.environ.get("POKEAGENT_NICKNAMES") is not None:
        out["nicknames"] = os.environ["POKEAGENT_NICKNAMES"] not in ("0", "false", "no")
    return out


def load() -> dict:
    """Everything, merged by precedence. Read fresh: the user may have just
    changed a value in the widget and a long run should not need restarting to
    see it."""
    merged = dict(DEFAULTS)
    for layer in (_from_shell(), _from_local(), _from_env()):
        merged.update({k: v for k, v in layer.items() if v not in (None, "")})
    # Keep the slider honest whatever the source said.
    try:
        merged["risk"] = min(1.0, max(0.0, float(merged.get("risk", 0.35))))
    except (TypeError, ValueError):
        merged["risk"] = DEFAULTS["risk"]
    merged["nicknames"] = bool(merged.get("nicknames", True))
    return merged


def get(key, default=None):
    return load().get(key, DEFAULTS.get(key, default))


# ---- what the slider actually means ------------------------------------


def heal_below(risk=None) -> float:
    """Fraction of max HP at which the run goes to a Pokemon Centre.

    Cautious 0.85 down to reckless 0.50. A Centre trip is cheap and a dead
    team member is not, and the old curve (0.70 down to 0.20) let a reckless
    run walk around at a fifth of its HP -- which is not risk appetite, it is
    a faint waiting for a critical hit. Even the reckless end now heals at
    half, and healing also restores PP, which is the other way a party dies.
    """
    r = load()["risk"] if risk is None else risk
    return round(0.85 - 0.35 * r, 3)


def lead_min_hp(risk=None) -> float:
    """How healthy a laggard must be before it is sent in to be trained."""
    r = load()["risk"] if risk is None else risk
    return round(1.0 - 0.60 * r, 3)


def gym_margin(risk=None) -> int:
    """Levels above the leader's ace before the run walks into a gym."""
    r = load()["risk"] if risk is None else risk
    return int(round(4 - 4 * r))


def mood(risk=None) -> str:
    """One word for the risk setting, the way the widget and the log name it."""
    r = load()["risk"] if risk is None else risk
    return "cautious" if r < 0.34 else ("balanced" if r < 0.67 else "reckless")


def describe(risk=None) -> str:
    r = load()["risk"] if risk is None else risk
    return (
        f"risk {r:.2f} ({mood(r)}): heal below {heal_below(r):.0%}, "
        f"train a laggard above {lead_min_hp(r):.0%}, "
        f"enter a gym {gym_margin(r)} levels up"
    )
