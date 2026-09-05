"""script_advances_scene against the real disassembly.

Ground truth: a coord_event script is a ONE-SHOT cutscene only when it
provably sets the map's scene to a token DIFFERENT from the one it was
triggered with, following scall/sjump/jump/iftrue/iffalse targets within
the same maps/<Camel>.asm."""
import pytest

from crystalagent.nav import script_advances_scene
from crystalagent.paths import REPO_ROOT

pytestmark = pytest.mark.unit


def test_elmslab_aide_potion_scall_reaches_setscene():
    """AideScript_WalkPotion1 scalls AideScript_GivePotion, whose
    `setscene SCENE_ELMSLAB_NOOP` differs from the triggering token --
    a real one-shot. Missing this made the lab's only corridor
    unroutable."""
    assert script_advances_scene(
        REPO_ROOT, "ElmsLab", "AideScript_WalkPotion1",
        "SCENE_ELMSLAB_AIDE_GIVES_POTION") is True


def test_elmslab_leave_script_is_not_a_cutscene():
    assert script_advances_scene(
        REPO_ROOT, "ElmsLab", "LabTryToLeaveScript",
        "SCENE_ELMSLAB_CANT_LEAVE") is False


def test_route32_cooltrainer_pushback_never_sets_a_scene():
    """Fires forever -- no setscene anywhere in its reachable body."""
    assert script_advances_scene(
        REPO_ROOT, "Route32", "Route32CooltrainerMStopsYouScene",
        "SCENE_ROUTE32_COOLTRAINER_M_BLOCKS") is False


def test_indigo_plateau_rival_sets_scene_back_to_its_own_id():
    """setscene targets the SAME token it was triggered with, so this
    must not count as advancing the scene."""
    assert script_advances_scene(
        REPO_ROOT, "IndigoPlateauPokecenter1F", "PlateauRivalBattle1",
        "SCENE_INDIGOPLATEAUPOKECENTER1F_RIVAL_BATTLE") is False


def test_missing_label_or_file_returns_false_not_raises():
    assert script_advances_scene(
        REPO_ROOT, "NoSuchCamelMap", "NoSuchScriptLabel",
        "SCENE_DOES_NOT_EXIST") is False
    assert script_advances_scene(
        REPO_ROOT, "ElmsLab", "NoSuchScriptLabel",
        "SCENE_DOES_NOT_EXIST") is False
