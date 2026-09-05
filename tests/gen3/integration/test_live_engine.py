"""Scenarios that only a running emulator can answer.

Each one guards a bug this port actually hit. The predecessor's own brief for
its integration lane makes the case: 593 unit tests booted no emulator, and
every significant defect was found by a model playing, never by a test.
"""

import random

import pytest

pytestmark = pytest.mark.integration

MOVES = {"U": "UP", "D": "DOWN", "L": "LEFT", "R": "RIGHT"}


def test_decoded_grid_agrees_with_the_engine(fork):
    """The whole nav layer rests on this: the model of a step and the
    engine's own answer must not diverge.

    Guards the elevation bug -- gPlayerAvatar.currentElevation is a 4-bit
    field sharing a byte with previousElevation, and reading the whole byte
    made every prediction wrong at once.
    """
    d = fork("lab")
    random.seed(11)
    agree = disagree = 0
    mismatches = []
    for _ in range(60):
        m, (x, y) = d.map_name(), d.pos()
        direction = random.choice("UDLR")
        # The driver's own predictor: static grid PLUS live NPC bodies. The
        # bare nav.step is the static half and legitimately disagrees where
        # an NPC is standing on walkable floor.
        predicted = d.predict_step(direction)
        d.emu.run_sequence(f"{MOVES[direction]}:16")
        d.settle(240)
        if d.map_name() != m:  # crossed a seam; a different code path
            break
        moved = d.pos() != (x, y)
        ok = (predicted is None and not moved) or (
            predicted is not None and d.pos() == predicted[:2]
        )
        if not ok and predicted is not None and not moved:
            # The first press only turns when not already facing that way.
            d.emu.run_sequence(f"{MOVES[direction]}:16")
            d.settle(240)
            ok = d.pos() == predicted[:2]
        if not ok and predicted is not None and not moved:
            # A BODY ARRIVED WHILE WE WERE PRESSING. `predict_step` reads the
            # live object list, then this test holds a key for 16 frames -- a
            # wandering NPC can step into the target cell in between, and the
            # engine then refuses a move that was legal when it was predicted.
            # That is the test racing the game, not the grid disagreeing with
            # it: in the lab, Birch's aide walks across (8,8). Re-read the
            # bodies and drop the sample if one is standing there now.
            occupied = {(o["x"], o["y"]) for o in d.live_npcs()
                        if not o.get("player")}
            if tuple(predicted[:2]) in occupied:
                continue
        if ok:
            agree += 1
        else:
            disagree += 1
            mismatches.append((m, (x, y), direction, predicted, d.pos()))
    assert disagree == 0, f"{disagree}/{agree + disagree} disagreed: {mismatches[:5]}"


def test_elevation_is_masked(fork):
    """A 4-bit field read as a whole byte yields 0x33; the mask is what makes
    every goto work."""
    d = fork("lab")
    assert 0 <= d.elevation() <= 15


def test_scene_lock_is_the_engine_flag(fork):
    """sLockFieldControls, not a guess from 'position stopped changing'."""
    d = fork("littleroot")
    assert d.scene_active(), "the arrival cutscene should own input"
    assert not d.step_dir("R"), "a step during a scene must be refused"
    assert "scene-owns-input" in (d.last_step_reason or "")
    assert d.advance_scene(80000), "the scene should finish"
    assert not d.scene_active()


def test_warp_needs_entering_not_standing(fork):
    """A warp fires on the step that ENTERS its tile, with the key still
    held. Standing on one does nothing."""
    d = fork("lab")
    door = next(
        e for e in d.nav.exits(d.map_name())
        if e["kind"] == "warp" and e["dest"] == "LittlerootTown"
    )
    before = d.map_name()
    # Standing on the warp is not entering it.
    assert d.goto(door["x"], door["y"]), d.last_goto_reason
    d.settle(300)
    assert d.map_name() == before, "standing on a warp must not fire it"
    assert d.take_warp(door["x"], door["y"]), d.last_warp_reason
    assert d.map_name() == "LittlerootTown" != before


def test_party_decrypts(fork, names, consts):
    """Species and moves come out of the XOR-and-unshuffle path, and the
    checksum must validate -- a bad decrypt reads as a bad egg."""
    d = fork("lab")
    party = d.state.party()
    assert len(party) == 1
    mon = party[0]
    assert mon.checksum_ok, "substructure checksum failed"
    assert mon.species == consts.species["SPECIES_TORCHIC"]
    assert mon.level == 5
    assert mon.hp == mon.max_hp
    assert [d.names.move(m) for m in mon.moves if m] == ["SCRATCH", "GROWL"]
    assert mon.nickname == "EMBER", "the nickname was typed deliberately"
    assert not mon.is_egg


def test_battle_is_detected_and_typed(fork):
    """gMain.inBattle plus gBattleTypeFlags; wild is 'not trainer'."""
    d = fork("first-battle")
    assert d.in_battle()
    assert d.state.battle_ready(), "gBattleMons must be populated at this checkpoint"
    b = d.state.battle()
    assert b.active and b.wild and not b.trainer
    assert b.battler_count == 2
    assert len(b.mons) == 2
    mine, theirs = b.mons[0], b.mons[1]
    assert mine["level"] == 5 and mine["max_hp"] > 0
    assert theirs["species"] and theirs["max_hp"] > 0


def test_registry_rejects_on_live_state(fork):
    """Preconditions are checked against a fresh read, with a sentence."""
    from pokeagent.registry import resolve

    d = fork("first-battle")
    with pytest.raises(ValueError, match="cannot run during a battle"):
        resolve(d, "goto", {"x": 1, "y": 1})
    d2 = fork("lab")
    with pytest.raises(ValueError, match="needs an active battle"):
        resolve(d2, "attack", {"slot": 0})
    with pytest.raises(ValueError, match="unknown argument"):
        resolve(d2, "goto", {"x": 1, "y": 1, "nope": 2})


def test_savestate_determinism(fork):
    """Same state plus same inputs is byte-identical, RNG included. That is
    what makes forking a timeline a real search primitive."""
    d = fork("lab")
    blob = bytes(d.emu.core.save_raw_state())
    d.emu.run_sequence("UP:16 .:30 DOWN:16 .:30")
    first = (d.pos(), d.emu.frame, bytes(d.emu.read("gSaveBlock1", 64)))
    d.emu.core.load_raw_state(blob)
    d.emu.run_sequence("UP:16 .:30 DOWN:16 .:30")
    assert (d.pos(), d.emu.frame, bytes(d.emu.read("gSaveBlock1", 64))) == first


def test_state_provenance_refuses_a_foreign_rom(fork, tmp_path):
    """A savestate from another ROM would read plausible garbage through
    every symbol, so the load must refuse rather than fork a bad timeline."""
    import json

    d = fork("lab")
    path = tmp_path / "tampered.state"
    d.emu.save_state(path)
    meta = path.with_suffix(path.suffix + ".meta")
    payload = json.loads(meta.read_text())
    payload["rom_sha256"] = "0" * 64
    meta.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="refusing to load"):
        d.emu.load_state(path)
