"""Shared fixtures.

The unit lane needs the decompilation on disk (every parser reads it) and,
for the ROM tables, an emulator. Both are cheap and session-scoped: booting
mGBA and parsing 50k symbols takes well under a second, so there is no reason
to fake them and every reason not to -- the predecessor's unit lane faked the
emulator so thoroughly that it structurally could not find the bugs that
actually cost it sessions.

What is NOT here is any savestate dependency: the unit lane must run on a
fresh clone with only the ROM built. Savestate-driven scenarios live in
tests/integration and are marked.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pokeagent import paths  # noqa: E402


def _missing():
    absent = [
        str(p)
        for p in (paths.ROM, paths.SYM, paths.CHARMAP, paths.MAPS / "map_groups.json")
        if not p.exists()
    ]
    return absent


@pytest.fixture(scope="session", autouse=True)
def _require_artifacts():
    absent = _missing()
    if absent:
        pytest.skip(
            "missing build artifacts: "
            + ", ".join(absent)
            + " -- run scripts/build_rom.sh",
            allow_module_level=True,
        )


@pytest.fixture(scope="session")
def symbols():
    from pokeagent.symbols import Symbols

    return Symbols()


@pytest.fixture(scope="session")
def charmap():
    from pokeagent.charmap import Charmap

    return Charmap()


@pytest.fixture(scope="session")
def consts():
    from pokeagent.cconst import Constants

    return Constants()


@pytest.fixture(scope="session")
def cstruct():
    from pokeagent import cstruct as mod

    return mod


@pytest.fixture(scope="session")
def emu(symbols, charmap):
    from pokeagent.emu import Sapphire

    return Sapphire(sym=symbols, charmap=charmap)


@pytest.fixture(scope="session")
def names(emu, charmap, consts):
    from pokeagent.names import Names

    return Names(emu, charmap, consts)


@pytest.fixture(scope="session")
def behaviors(consts):
    from pokeagent.behaviors import Behaviors

    return Behaviors(consts)


@pytest.fixture(scope="session")
def mapdata(behaviors):
    from pokeagent.nav import MapData

    return MapData(behaviors)
