"""How long does this actually take? Answered from two honest clocks.

The point of these numbers is a public claim -- "~100 hours of idle time to
beat the game" -- so the arithmetic behind them has to survive scrutiny. Two
clocks, because the sentence is ambiguous and both readings are interesting:

* `play_time` is the cartridge's own HH:MM:SS. It is what a human player would
  quote and what "beat the game in N hours" conventionally means.
* `at` is a unix timestamp, so its deltas are REAL elapsed time -- how long the
  machine actually ran, which is the "idle time" reading.

`wall` is neither, and using it was the bug these were written against: it
counts from the start of the CURRENT session, so it resets to zero on every
restart. The live store already holds badge 1 from one session and badge 2 from
the next, which made their "gap" negative.
"""

import json

import pytest

from pokeagent.metrics import Metrics

pytestmark = pytest.mark.unit


def store(tmp_path, rows):
    path = tmp_path / "run.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    metrics = object.__new__(Metrics)
    metrics.path, metrics.session = path, "test"
    return metrics


def badge(label, play, at, wall=0.0):
    return {"kind": "badge", "label": label, "wall": wall,
            "play_time": play, "at": at, "detail": {}}






def test_a_single_badge_refuses_to_extrapolate(tmp_path):
    m = store(tmp_path, [badge("BADGE01", "10:00:00", 1000)])
    out = m.projection()
    assert out["play_hours_to_eight_badges"] is None
    assert "no gap" in out["badge_basis"]


def test_two_badges_give_a_range_on_both_clocks(tmp_path):
    """One gap: mean and last are the same, so the range collapses to a point
    -- but it is still returned as a range so the caller cannot quote it as
    more certain than it is."""
    m = store(tmp_path, [
        badge("BADGE01", "10:00:00", 1_000_000),
        badge("BADGE02", "12:00:00", 1_007_200),   # +2h play, +2h real
    ])
    out = m.projection()
    # 2h spent, 6 badges left at 2h each -> 14h
    assert out["play_hours_to_eight_badges"] == [14.0, 14.0]
    assert out["real_hours_to_eight_badges"] == [14.0, 14.0]
    assert "2 badges" in out["badge_basis"]


def test_a_widening_gap_produces_a_widening_range(tmp_path):
    """Later badges cost more, so the last gap and the mean disagree -- and
    that disagreement IS the uncertainty worth publishing."""
    m = store(tmp_path, [
        badge("BADGE01", "00:00:00", 0),
        badge("BADGE02", "02:00:00", 7_200),
        badge("BADGE03", "06:00:00", 21_600),      # gaps: 2h, 4h
    ])
    low, high = m.projection()["play_hours_to_eight_badges"]
    assert low < high, "a mean of 3h and a last gap of 4h must not agree"
    assert low == 6 + 5 * 3      # 21h on the mean
    assert high == 6 + 5 * 4     # 26h on the last gap


def test_the_session_clock_reset_cannot_poison_the_estimate(tmp_path):
    """The exact live shape: badge 1 recorded near the start of one session,
    badge 2 near the start of the next, so `wall` goes DOWN between them. The
    play clock and the real clock both still rise."""
    m = store(tmp_path, [
        badge("BADGE01", "50:07:30", 1_788_016_609, wall=830.0),
        badge("BADGE02", "54:32:25", 1_788_017_511, wall=412.2),
    ])
    out = m.projection()
    assert out["play_hours_to_eight_badges"] is not None, \
        "a session restart between badges must not erase the estimate"
    low, _ = out["play_hours_to_eight_badges"]
    assert low > 0


def test_a_reloaded_fork_does_not_halve_the_estimate(tmp_path):
    """Two events with the same play clock -- a fork replayed, a state reloaded
    -- are dropped rather than counted as a zero-cost badge. Averaging that
    zero in is how an estimate silently halves."""
    m = store(tmp_path, [
        badge("BADGE01", "10:00:00", 1000),
        badge("BADGE02", "12:00:00", 8200),
        badge("BADGE02", "12:00:00", 8200),      # replayed
    ])
    out = m.projection()
    assert out["play_hours_to_eight_badges"] == [14.0, 14.0]


def test_species_needs_real_evidence_before_guessing_a_dex(tmp_path):
    rows = [badge("BADGE01", "10:00:00", 1000)]
    rows += [{"kind": "species", "label": f"MON{i}", "wall": 0.0,
              "play_time": f"{10 + i}:00:00", "at": 1000 + i * 3600, "detail": {}}
             for i in range(3)]
    out = store(tmp_path, rows).projection()
    assert out["play_hours_to_full_dex"] is None
    assert "only 3 species" in out["dex_basis"]


def test_a_dex_estimate_says_it_is_optimistic(tmp_path):
    """The rate of catching early route species does not hold for the ones
    behind stones, trades and the sea. The number is published WITH that
    caveat or not at all."""
    rows = [{"kind": "species", "label": f"MON{i}", "wall": 0.0,
             "play_time": f"{i:02d}:00:00", "at": i * 3600, "detail": {}}
            for i in range(8)]
    out = store(tmp_path, rows).projection(dex_target=100)
    assert out["play_hours_to_full_dex"] == 92.0     # 1h each, 92 to go
    assert "optimistic" in out["dex_basis"]
