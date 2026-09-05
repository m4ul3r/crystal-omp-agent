"""Who is driving the game is part of every snapshot.

The widget answers "what agent is running it" from the feed's ``agent``
block. The process identity comes from the feed itself, so even a bare
kernel that says nothing about itself is named; the play loop's card
(session, model, risk) is MERGED over it, so adding a card must not cost
the pid and host that identify the process.
"""

import os
import sys

import pytest

from pokeagent import live
from pokeagent.live import LiveFeed

pytestmark = pytest.mark.unit


class FakeEmu:
    frame = 0
    observer = None


class FakeDriver:
    """No game: every state read fails, which is the intro-screen case."""

    def __init__(self):
        self.state = object()
        self.emu = FakeEmu()


def test_the_process_names_itself_even_with_no_game_to_read():
    feed = LiveFeed("test-agent")
    out = feed.snapshot(FakeDriver())
    assert "error" in out, "the fake driver has no game; the read must be reported"
    agent = out["agent"]
    assert agent["pid"] == os.getpid()
    assert agent["host"] != ""
    assert agent["name"] != ""


def test_the_play_loops_card_merges_over_the_process_identity():
    feed = LiveFeed("test-agent")
    feed.extra["agent"] = {"session": "live", "model": "gemma4:e4b", "model_state": "ready"}
    agent = feed.snapshot(FakeDriver())["agent"]
    assert agent["session"] == "live" and agent["model"] == "gemma4:e4b"
    assert agent["pid"] == os.getpid(), "the card must add to the identity, not replace it"


@pytest.mark.parametrize(
    ("argv0", "expected"),
    [
        ("/home/x/repo/scripts/play.py", "play.py"),
        ("-c", "python"),
        ("", "python"),
        ("/usr/lib/python3/site-packages/ipykernel_launcher.py", "python"),
    ],
)
def test_the_driving_process_is_named_from_its_own_argv(monkeypatch, argv0, expected):
    monkeypatch.setattr(sys, "argv", [argv0])
    assert live._process_name() == expected
