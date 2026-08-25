"""gym_scout: parse repo ground truth for gym rosters (parties.asm)."""
import pytest

import trek


pytestmark = pytest.mark.unit


def _scout_driver():
    d = trek.Driver.__new__(trek.Driver)

    class Nav:
        consts = {"VIOLET_GYM": "VIOLET_GYM",
                  "NOT_A_REAL_GYM_XYZ": "NOT_A_REAL_GYM_XYZ"}
        camel = {}

    d.nav = Nav()
    return d


def test_violet_gym_scout_from_repo_source():
    """Falkner + keepers, straight from data/trainers/parties.asm."""
    out = _scout_driver().gym_scout("VIOLET_GYM")
    by = {t["trainer"]: t for t in out}
    falkner = by["FALKNER"]
    assert [(m["species"], m["level"]) for m in falkner["mons"]] == [
        ("PIDGEY", 7), ("PIDGEOTTO", 9)]
    assert falkner["mons"][0]["moves"][:2] == ["TACKLE", "MUD_SLAP"]
    rod = by.get("ROD")
    assert rod and {"species": "PIDGEY", "level": 7} in [
        {"species": m["species"], "level": m["level"]} for m in rod["mons"]]


def test_unknown_map_raises_value_error():
    with pytest.raises(ValueError):
        _scout_driver().gym_scout("NOT_A_REAL_GYM_XYZ")
