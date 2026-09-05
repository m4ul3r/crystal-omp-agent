"""Abandoning a step must be temporary, because gates open.

A run sat in Granite Cave for half an hour, 233 battles deep, training. The
only step that led anywhere -- sail to Slateport -- had been abandoned six
attempts earlier, and abandonment was a permanent set. Every objective after it
was therefore unreachable, and the loop looked busy the entire time.

Giving up is still right in the moment: hammering a gate that will not open
wastes the same time. The bug was that it never reconsidered.
"""

import time

import pytest

from scripts.play import Session

pytestmark = pytest.mark.unit


def make_session():
    session = object.__new__(Session)
    session.notes = []
    session.note = session.notes.append
    session._story_tries = {}
    session._travel_fails = {}
    session._story_given_up = {}
    session._last_skipped_story = None
    return session


def test_something_never_abandoned_is_available():
    assert make_session()._still_given_up({}, "DewfordTown") is False


def test_a_fresh_abandonment_holds():
    """The point of giving up is to stop retrying immediately."""
    s = make_session()
    book = {"sail": time.time()}
    assert s._still_given_up(book, "sail") is True
    assert "sail" in book


def test_it_expires_and_is_reconsidered():
    """The failure this was written for: the sail was abandoned while the
    letter that unlocks it was still undelivered, and never revisited once it
    was."""
    s = make_session()
    book = {"sail": time.time() - Session.GIVE_UP_FOR - 1}
    assert s._still_given_up(book, "sail") is False
    assert "sail" not in book, "an expired entry must be dropped, not re-checked"
    assert any("reconsidering sail" in n for n in s.notes)


def test_expiry_clears_the_failure_counts_too():
    """Reconsidering with the old count intact would give up again on the very
    next attempt, which is the same bug with extra steps."""
    s = make_session()
    s._story_tries = {"sail": 6}
    s._travel_fails = {"sail": 8}
    s._still_given_up({"sail": time.time() - Session.GIVE_UP_FOR - 1}, "sail")
    assert s._story_tries == {}
    assert s._travel_fails == {}


def test_expiry_is_per_key():
    """One reconsidered step must not silently revive every other."""
    s = make_session()
    book = {"old": time.time() - Session.GIVE_UP_FOR - 1, "new": time.time()}
    assert s._still_given_up(book, "old") is False
    assert s._still_given_up(book, "new") is True
    assert set(book) == {"new"}


def test_reconsidering_lets_the_next_skip_be_heard():
    """The skip note is latched so it prints once per step rather than once
    per iteration. Reconsidering has to clear that latch, or a step abandoned
    a second time would do it silently -- which is the observability hole this
    whole failure hid in."""
    s = make_session()
    s._last_skipped_story = "sail"
    s._still_given_up({"sail": time.time() - Session.GIVE_UP_FOR - 1}, "sail")
    assert s._last_skipped_story is None
