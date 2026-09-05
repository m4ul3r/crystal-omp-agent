"""Rolling memory, the boundary schemas, and the autopilot rails.

Pure logic only: no ROM, no savestate, no emulator. Everything here is a
regression guard for a defect the Crystal harness actually shipped -- the
stuck digest that ignored money and bag, the whiteout recovery that could
roll back into the wiped party, the summarizer failure that lost entries.
"""

import json

import pytest

from autopilot import (
    classify_milestones,
    compact_obs,
    digest,
    healthy_checkpoints,
    party_alive,
    party_wiped,
    stuck,
)
from pokeagent.rolling import RollingMemory
from pokeagent.schemas import (
    SchemaError,
    validate_cycle_record,
    validate_decision,
    validate_observe,
    validate_request,
)

pytestmark = pytest.mark.unit


# -- fixtures ---------------------------------------------------------------

def make_obs(**over):
    """A shape-accurate observe(): GameState.snapshot (state.py:403) plus the
    keys Driver.observe adds (trek.py:144)."""
    obs = {
        "frame": 1000,
        "location": {"map": "LittlerootTown", "group": 0, "num": 9,
                     "x": 6, "y": 8, "facing": "down", "elevation": 3},
        "player": {"name": "TROY", "gender": "male", "trainer_id": 31337,
                   "money": 3000, "coins": 0, "play_time": "0:12:41",
                   "badges": []},
        "ui": {"battle": False, "message": "", "scene": False,
               "dialog": False, "callback": "CB2_Overworld", "tasks": []},
        "party": [{
            "nickname": "TREECKO", "species": "TREECKO", "level": 5,
            "hp": 19, "max_hp": 19, "status": None, "egg": False,
            "shiny": False, "nature": "HARDY",
            "moves": [{"name": "POUND", "pp": 35}],
        }],
        "bag": {"Items": {"POTION": 1}, "PokeBalls": {}},
        "tiles": {"U": "floor", "D": "floor", "L": "wall", "R": "floor",
                  "here": "floor"},
        "npcs": [{"x": 7, "y": 9, "gfx": 5}],
    }
    obs.update(over)
    return obs


def memory(tmp_path, **kw):
    """Tiny thresholds so a fold is four entries away, not a hundred."""
    kw.setdefault("leaf_size", 2)
    kw.setdefault("soft_limit", 2)
    return RollingMemory(tmp_path / "mem.db", **kw)


# -- RollingMemory ----------------------------------------------------------

def test_entries_stay_raw_below_the_soft_limit(tmp_path):
    """Nothing folds until there are soft_limit + leaf_size entries: recent
    history must stay verbatim."""
    m = memory(tmp_path)
    for i in range(3):
        m.add(f"line {i}")
    assert m.finalize_iteration() == 0
    assert m.frontier() == []
    assert [c for _, c in m.tail(10)] == ["line 0", "line 1", "line 2"]


def test_oldest_entries_fold_into_a_leaf_summary(tmp_path):
    """The oldest leaf_size raws become one level-1 block and leave the raw
    table; the newer ones are untouched."""
    m = memory(tmp_path)
    for i in range(4):
        m.add(f"line {i}")
    assert m.finalize_iteration() == 1
    (start, end, level, content) = m.frontier()[0]
    assert (start, end, level) == (1, 2, 1)
    assert "line 0" in content and "line 1" in content
    assert [c for _, c in m.tail(10)] == ["line 2", "line 3"]


def test_contiguous_leaves_merge_pairwise_upward(tmp_path):
    """Two adjacent level-1 blocks become one level-2 block covering both, so
    old context costs O(log n) blocks instead of O(n)."""
    m = memory(tmp_path)
    for i in range(6):
        m.add(f"line {i}")
    m.finalize_iteration()
    front = m.frontier()
    assert len(front) == 1, front
    start, end, level, content = front[0]
    assert (start, end, level) == (1, 4, 2)
    assert "line 3" in content
    # The frontier is what render() leads with, ahead of the raw tail.
    rendered = m.render()
    assert rendered.startswith("[1-4]L2:")
    assert rendered.endswith("[6]: line 5")


def test_nothing_is_lost_when_the_summarizer_fails(tmp_path):
    """A model call that raises must leave the data raw and say why -- an
    unexplained no-op is the defect class this project refuses to ship."""
    def broken(_blocks):
        raise TimeoutError("model call timed out")

    m = memory(tmp_path, summarize_fn=broken)
    for i in range(6):
        m.add(f"line {i}")
    assert m.finalize_iteration() == 0
    assert m.frontier() == []
    assert [c for _, c in m.tail(10)] == [f"line {i}" for i in range(6)]
    assert "model call timed out" in m.last_fold_reason
    assert "left raw" in m.last_fold_reason


def test_a_recovered_summarizer_folds_on_the_next_pass(tmp_path):
    """Compaction is retried, not abandoned: the failure path is a delay."""
    calls = {"n": 0}

    def flaky(blocks):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return "\n".join(c for _, _, c in blocks)

    m = memory(tmp_path, summarize_fn=flaky)
    for i in range(4):
        m.add(f"line {i}")
    assert m.finalize_iteration() == 0
    assert m.finalize_iteration() == 1
    assert m.last_fold_reason is None
    assert m.frontier()[0][:3] == (1, 2, 1)


def test_a_non_string_summary_is_a_failure_not_a_corrupt_block(tmp_path):
    m = memory(tmp_path, summarize_fn=lambda blocks: {"summary": "oops"})
    for i in range(4):
        m.add(f"line {i}")
    assert m.finalize_iteration() == 0
    assert "expected str" in m.last_fold_reason


def test_numbering_continues_past_summarized_entries_after_reopen(tmp_path):
    """Folding deletes raw rows. A reopened database that numbered from the
    raw table alone would restart inside an existing summary's range."""
    m = memory(tmp_path)
    for i in range(4):
        m.add(f"line {i}")
    m.finalize_iteration()
    m.close()

    again = memory(tmp_path)
    n = again.add("after reopen")
    assert n == 5
    assert [i for i, _ in again.tail(10)] == [3, 4, 5]
    assert again.frontier()[0][:3] == (1, 2, 1)


# -- the stuck digest (the Crystal regression) ------------------------------

def test_identical_observations_digest_as_stuck():
    assert stuck(digest(make_obs()), digest(make_obs()))


def test_spending_money_is_a_world_delta():
    """A successful purchase moves money and nothing else the old digest
    looked at, so Crystal reported the working action as stuck."""
    poorer = make_obs()
    poorer["player"] = dict(poorer["player"], money=2700)
    assert digest(make_obs()) != digest(poorer)
    assert not stuck(digest(make_obs()), digest(poorer))


def test_gaining_an_item_is_a_world_delta():
    richer = make_obs()
    richer["bag"] = {"Items": {"POTION": 2}, "PokeBalls": {}}
    assert not stuck(digest(make_obs()), digest(richer))


def test_bag_pocket_ordering_is_not_a_world_delta():
    """The digest is compared for equality, so read order must not fabricate
    a difference the deciding agent would read as progress."""
    reordered = make_obs()
    reordered["bag"] = {"PokeBalls": {}, "Items": {"POTION": 1}}
    assert stuck(digest(make_obs()), digest(reordered))


@pytest.mark.parametrize("mutate", [
    lambda o: o["location"].update(x=7),
    lambda o: o["location"].update(map="Route101"),
    lambda o: o["ui"].update(battle=True),
    lambda o: o["ui"].update(message="TREECKO fainted!"),
    lambda o: o["player"].update(badges=["BADGE01"]),
    lambda o: o["party"][0].update(hp=4),
    lambda o: o["party"][0].update(status="PSN"),
])
def test_every_tracked_dimension_breaks_the_digest(mutate):
    changed = make_obs()
    mutate(changed)
    assert not stuck(digest(make_obs()), digest(changed))


def test_digest_is_json_serialisable():
    """It is written to the journal verbatim; a non-serialisable value would
    only surface at the moment of a crash."""
    json.dumps(digest(make_obs()))


# -- milestones and liveness ------------------------------------------------

def test_milestones_name_each_transition():
    before = make_obs()
    before["ui"]["battle"] = True
    after = make_obs()
    after["location"]["map"] = "Route101"
    after["party"][0]["level"] = 6
    after["player"]["badges"] = ["BADGE01"]
    assert classify_milestones(before, after) == [
        "map-entry", "battle-end", "level-up", "badge",
    ]


def test_a_reordered_party_is_not_a_level_up():
    """Switching reorders the party; matching by index would invent one."""
    before = make_obs()
    before["party"] = [
        dict(before["party"][0], nickname="A", level=5),
        dict(before["party"][0], nickname="B", level=9),
    ]
    after = make_obs()
    after["party"] = [
        dict(before["party"][0], nickname="B", level=9),
        dict(before["party"][0], nickname="A", level=5),
    ]
    assert classify_milestones(before, after) == []


def test_a_party_of_eggs_is_not_a_living_party():
    obs = make_obs()
    obs["party"] = [dict(obs["party"][0], egg=True, hp=0)]
    assert not party_alive(obs)
    assert not party_wiped(obs)     # nothing to wipe: no fighting mon at all


def test_all_fainted_is_a_wipe():
    obs = make_obs()
    obs["party"] = [dict(obs["party"][0], hp=0)]
    assert party_wiped(obs)
    assert not party_alive(obs)


# -- rollback candidates (the whiteout regression) --------------------------

def write_journal(path, entries):
    path.write_text("".join(json.dumps(e) + "\n" for e in entries))
    return path


def test_rollback_skips_checkpoints_taken_at_or_after_the_wipe(tmp_path):
    """Crystal checkpointed the battle-end that wiped the party and then
    'recovered' into it. A candidate must predate the wipe AND have been
    taken with a living party."""
    j = write_journal(tmp_path / "s.jsonl", [
        {"event": "checkpoint", "file": "s-map-entry-1.state", "frame": 100,
         "party_alive": True},
        {"event": "checkpoint", "file": "s-battle-end-1.state", "frame": 400,
         "party_alive": False},
        {"event": "checkpoint", "file": "s-map-entry-2.state", "frame": 900,
         "party_alive": True},
    ])
    assert healthy_checkpoints(j, before_frame=500) == ["s-map-entry-1.state"]


def test_rollback_prefers_the_newest_living_checkpoint(tmp_path):
    j = write_journal(tmp_path / "s.jsonl", [
        {"event": "checkpoint", "file": "a.state", "frame": 100, "party_alive": True},
        {"event": "checkpoint", "file": "b.state", "frame": 200, "party_alive": True},
        {"event": "fork", "file": "s-pre-1.state", "frame": 250},
    ])
    assert healthy_checkpoints(j, before_frame=500) == ["b.state", "a.state"]


def test_a_torn_final_journal_line_is_survivable(tmp_path):
    j = tmp_path / "s.jsonl"
    j.write_text(
        json.dumps({"event": "checkpoint", "file": "a.state", "frame": 1,
                    "party_alive": True}) + "\n{\"event\": \"check"
    )
    assert healthy_checkpoints(j) == ["a.state"]


def test_no_journal_means_no_candidates(tmp_path):
    assert healthy_checkpoints(tmp_path / "missing.jsonl") == []


# -- schemas: observe -------------------------------------------------------

def test_validate_observe_returns_its_argument_unchanged():
    obs = make_obs()
    assert validate_observe(obs) is obs


def test_observe_rejects_a_missing_key_by_name():
    obs = make_obs()
    del obs["tiles"]
    with pytest.raises(SchemaError) as e:
        validate_observe(obs)
    assert "observe is missing required key 'tiles'" in str(e.value)


def test_observe_rejects_a_wrong_type_with_both_types_named():
    obs = make_obs()
    obs["location"]["x"] = "6"
    with pytest.raises(SchemaError) as e:
        validate_observe(obs)
    assert str(e.value) == "observe.location.x must be int, got str"


def test_observe_rejects_a_bool_where_a_number_belongs():
    """bool subclasses int, so an unguarded check would accept True as money
    and the number would be silently wrong forever after."""
    obs = make_obs()
    obs["player"]["money"] = True
    with pytest.raises(SchemaError) as e:
        validate_observe(obs)
    assert "observe.player.money must be int, got bool" in str(e.value)


def test_observe_rejects_an_unexpected_key():
    obs = make_obs()
    obs["hp"] = 19
    with pytest.raises(SchemaError) as e:
        validate_observe(obs)
    assert "unexpected key(s) hp" in str(e.value)


def test_observe_accepts_a_healthy_mon_with_no_status():
    obs = make_obs()
    obs["party"][0]["status"] = None
    assert validate_observe(obs)


def test_observe_reports_the_offending_party_slot():
    obs = make_obs()
    obs["party"].append(dict(obs["party"][0], level="five"))
    with pytest.raises(SchemaError) as e:
        validate_observe(obs)
    assert "observe.party[1].level must be int, got str" in str(e.value)


def test_observe_rejects_a_bag_count_that_is_not_a_number():
    obs = make_obs()
    obs["bag"]["Items"]["POTION"] = "one"
    with pytest.raises(SchemaError) as e:
        validate_observe(obs)
    assert "observe.bag.Items.POTION must be an int, got str" in str(e.value)


def test_observe_accepts_the_battle_block_only_when_present():
    obs = make_obs()
    obs["battle"] = {"kinds": ["WILD"], "battlers": 2, "mons": []}
    assert validate_observe(obs)


# -- schemas: decisions -----------------------------------------------------

def test_validate_decision_accepts_a_full_payload():
    args = {"action": {"name": "goto", "kwargs": {"x": 7, "y": 15}},
            "goal": "reach the door", "risky": True,
            "success": {"map": "LittlerootTown"}}
    assert validate_decision(args) is args


def test_validate_decision_accepts_a_bare_action():
    assert validate_decision({"action": {"name": "settle"}})


def test_decision_rejects_an_action_the_registry_does_not_define():
    with pytest.raises(SchemaError) as e:
        validate_decision({"action": {"name": "mart_buy"}})
    msg = str(e.value)
    assert "'mart_buy' is not an action" in msg
    assert "travel" in msg          # the sentence lists what IS allowed


def test_decision_rejects_a_string_where_the_action_object_belongs():
    with pytest.raises(SchemaError) as e:
        validate_decision({"action": "goto"})
    assert "decision.action must be dict, got str" in str(e.value)


def test_decision_rejects_kwargs_that_are_not_an_object():
    with pytest.raises(SchemaError) as e:
        validate_decision({"action": {"name": "goto", "kwargs": [7, 15]}})
    assert "action.kwargs must be dict, got list" in str(e.value)


def test_decision_rejects_a_misspelled_success_criterion():
    with pytest.raises(SchemaError) as e:
        validate_decision({"action": {"name": "settle"},
                           "success": {"badges": 1}})
    assert "decision.success has unexpected key(s) badges" in str(e.value)
    assert "min_badges" in str(e.value)


def test_decision_rejects_an_unknown_top_level_key():
    with pytest.raises(SchemaError) as e:
        validate_decision({"action": {"name": "settle"}, "retries": 3})
    assert "decision has unexpected key(s) retries" in str(e.value)


# -- schemas: requests and journal lines ------------------------------------

def test_request_rejects_an_unknown_command_and_lists_the_real_ones():
    with pytest.raises(SchemaError) as e:
        validate_request({"id": 1, "cmd": "run"}, ("decision", "observe", "quit"))
    assert "unknown cmd 'run'; expected one of decision|observe|quit" in str(e.value)


def test_request_accepts_a_string_id_and_no_args():
    req = {"id": "abc", "cmd": "observe"}
    assert validate_request(req, ("observe",)) is req


def test_request_requires_a_cmd():
    with pytest.raises(SchemaError) as e:
        validate_request({"id": 1}, ("observe",))
    assert "request is missing required key 'cmd'" in str(e.value)


def cycle_record(**over):
    rec = {"t": "2026-08-28T10:00:00+00:00", "wall_s": 1.25, "frame": 1234,
           "used": 96, "action": {"name": "settle", "kwargs": {}},
           "goal": None, "ok": True, "digest": digest(make_obs()),
           "lead_level": 5}
    rec.update(over)
    return rec


def test_cycle_record_round_trips():
    rec = cycle_record()
    assert validate_cycle_record(rec) is rec
    validate_cycle_record(json.loads(json.dumps(rec)))


def test_cycle_record_requires_the_frame_spend():
    rec = cycle_record()
    del rec["used"]
    with pytest.raises(SchemaError) as e:
        validate_cycle_record(rec)
    assert "cycle is missing required key 'used'" in str(e.value)


def test_cycle_record_requires_wall_clock_time():
    rec = cycle_record()
    del rec["t"]
    with pytest.raises(SchemaError) as e:
        validate_cycle_record(rec)
    assert "cycle is missing required key 't'" in str(e.value)


def test_cycle_record_accepts_a_failure_with_reasons():
    assert validate_cycle_record(cycle_record(
        ok=False, error="ValueError: nope", why=["map is Route101, wanted Oldale"]))


def test_cycle_record_rejects_reasons_that_are_not_sentences():
    with pytest.raises(SchemaError) as e:
        validate_cycle_record(cycle_record(why=[{"reason": "stuck"}]))
    assert "cycle.why[0] must be a str, got dict" in str(e.value)


# -- compact_obs ------------------------------------------------------------

def test_compact_obs_keeps_what_a_decision_needs():
    out = compact_obs(make_obs())
    assert out["map"] == "LittlerootTown"
    assert out["party"][0]["hp"] == 19
    assert out["bag"] == {"Items": {"POTION": 1}, "PokeBalls": {}}
    assert out["tiles"]["U"] == "floor"
    assert "npcs" not in out        # the token budget is the point
    json.dumps(out)
