"""The Brain's failure modes, which are the whole point of the Brain.

No network: every test drives a fake transport. The happy path is the least
interesting case here -- what matters is that a local model being absent,
slow, or confidently wrong degrades to the caller's fallback with a reason
attached, and that a dead server costs one timeout rather than one per
decision.

The nickname tests use the real :class:`pokeagent.charmap.Charmap` on
purpose. The question "can the game type this" has exactly one authority,
pret's ``charmap.txt``, and a fake would let a name through that the naming
keyboard cannot produce -- which is how you end up with a party member called
``AAAAAAAAAA`` (AGENTS.md gotcha 5).
"""

import json
import logging

import pytest

from pokeagent import brain as B
from pokeagent.brain import Brain

pytestmark = pytest.mark.unit


class FakeTransport:
    """Scripted ``(url, payload, timeout) -> dict``.

    Each script entry is either a dict (returned as an Ollama reply body), a
    string (wrapped as ``message.content``), or an exception instance (raised).
    The last entry repeats once the script runs dry, so a "server is dead"
    test does not have to know how many calls the breaker will make.
    """

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []

    def __call__(self, url, payload, timeout):
        self.calls.append((url, payload, timeout))
        item = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, str):
            return {"message": {"content": item}}
        return item


def reply(reason="because", **fields) -> str:
    """A well-formed structured answer."""
    return json.dumps({**fields, "reason": reason})


class FakeClock:
    """Monotonic seconds under test control, so a 60s cooldown costs no time."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make(*script, **kw):
    transport = FakeTransport(*script)
    return Brain(transport=transport, **kw), transport


# ---- the happy path --------------------------------------------------


def test_choose_returns_the_models_pick_and_its_reason():
    b, t = make(reply(choice="VINE WHIP", reason="grass beats water"))
    assert b.choose("best move?", ["TACKLE", "VINE WHIP"], fallback="TACKLE") == (
        "VINE WHIP"
    )
    assert b.last_reason == "grass beats water"
    assert b.stats()["hits"] == 1
    assert b.stats()["fallbacks"] == 0


def test_the_request_constrains_the_model_to_the_options():
    """The enum in the schema is the model's decoding constraint; without it
    an 8B model at Q4 free-forms and every answer needs a rescue."""
    b, t = make(reply(choice="B"))
    b.choose("q", ["A", "B"], fallback="A")
    url, payload, timeout = t.calls[0]
    assert url.endswith("/api/chat")
    assert payload["stream"] is False
    assert payload["format"]["properties"]["choice"]["enum"] == ["A", "B"]
    assert payload["format"]["required"] == ["choice", "reason"]
    assert payload["options"]["temperature"] == 0.0
    assert "A, B" in payload["messages"][-1]["content"]


def test_context_reaches_the_prompt_and_is_not_silently_dropped():
    b, t = make(reply(choice="A"))
    b.choose("q", ["A", "B"], fallback="A", context="opponent is asleep")
    assert "opponent is asleep" in t.calls[0][1]["messages"][-1]["content"]


def test_yes_no_maps_the_enum_to_a_bool():
    b, _ = make(reply(answer="NO", reason="too weak"))
    assert b.yes_no("fight it?", fallback=True) is False
    assert b.last_reason == "too weak"


def test_number_accepts_an_in_range_integer():
    b, t = make(reply(value=4, reason="four is plenty"))
    assert b.number("how many potions?", 0, 10, fallback=1) == 4
    schema = t.calls[0][1]["format"]["properties"]["value"]
    assert (schema["minimum"], schema["maximum"]) == (0, 10)


def test_rank_returns_a_permutation():
    b, _ = make(reply(order=["C", "A", "B"], reason="speed first"))
    assert b.rank("order these", ["A", "B", "C"], fallback=["A", "B", "C"]) == [
        "C",
        "A",
        "B",
    ]


def test_every_decision_is_logged_at_info_with_answer_and_reason(caplog):
    """An invisible harness decision is as bad as an unexplained one."""
    caplog.set_level(logging.INFO, logger="pokeagent.brain")
    b, _ = make(reply(choice="B", reason="stronger"))
    b.choose("q", ["A", "B"], fallback="A")
    line = caplog.text
    assert "choose" in line and "-> B" in line and "stronger" in line
    assert "model" in line


# ---- failure modes ---------------------------------------------------


def test_timeout_falls_back_and_says_so():
    b, _ = make(TimeoutError("no reply in 20s"))
    assert b.choose("q", ["A", "B"], fallback="A") == "A"
    assert "timeout" in b.last_reason and "20s" in b.last_reason
    s = b.stats()
    assert (s["timeouts"], s["fallbacks"], s["hits"]) == (1, 1, 0)


def test_unreachable_server_falls_back_and_names_the_error():
    b, _ = make(ConnectionRefusedError("connection refused"))
    assert b.yes_no("q", fallback=True) is True
    assert "unreachable" in b.last_reason
    assert "ConnectionRefusedError" in b.last_reason
    assert b.stats()["errors"] == 1


def test_malformed_reply_falls_back():
    b, _ = make("not json at all")
    assert b.choose("q", ["A", "B"], fallback="B") == "B"
    assert "not JSON" in b.last_reason
    assert b.stats()["invalid"] == 1


def test_a_reply_with_no_content_falls_back():
    """A 200 with an unexpected body shape is not a usable answer."""
    b, _ = make({"error": "model not found"})
    assert b.choose("q", ["A", "B"], fallback="A") == "A"
    assert "no message.content" in b.last_reason


def test_answer_outside_the_options_falls_back():
    """The enum is a hint, not a guarantee -- this is the common real failure."""
    b, _ = make(reply(choice="Z", reason="Z is best"))
    assert b.choose("q", ["A", "B"], fallback="A") == "A"
    assert "'Z'" in b.last_reason and "not one of" in b.last_reason
    assert b.stats()["hits"] == 0


def test_number_out_of_range_falls_back():
    b, _ = make(reply(value=99))
    assert b.number("how many?", 0, 10, fallback=3) == 3
    assert "outside [0, 10]" in b.last_reason


def test_number_rejects_a_non_integer():
    b, _ = make(reply(value="four"))
    assert b.number("how many?", 0, 10, fallback=3) == 3
    assert "want an integer" in b.last_reason


def test_yes_no_rejects_anything_but_yes_or_no():
    b, _ = make(reply(answer="MAYBE"))
    assert b.yes_no("q", fallback=False) is False
    assert "want YES or NO" in b.last_reason


def test_rank_rejects_a_dropped_option():
    """A ranking that loses an option is not a ranking, and silently using it
    would delete a candidate move from consideration."""
    b, _ = make(reply(order=["B", "A"]))
    assert b.rank("q", ["A", "B", "C"], fallback=["A", "B", "C"]) == ["A", "B", "C"]
    assert "not a permutation" in b.last_reason


def test_rank_rejects_a_duplicated_option():
    b, _ = make(reply(order=["A", "A", "B"]))
    assert b.rank("q", ["A", "B", "C"], fallback=["C", "B", "A"]) == ["C", "B", "A"]
    assert "not a permutation" in b.last_reason


def test_a_fenced_json_block_still_parses():
    """Structured output returns bare JSON, but a model that wraps it anyway
    has still answered correctly; that is not worth a fallback."""
    b, _ = make("```json\n" + reply(choice="B", reason="fenced") + "\n```")
    assert b.choose("q", ["A", "B"], fallback="A") == "B"
    assert b.last_reason == "fenced"


def test_a_fallback_is_logged_too(caplog):
    caplog.set_level(logging.INFO, logger="pokeagent.brain")
    b, _ = make(TimeoutError("gone"))
    b.choose("q", ["A", "B"], fallback="A")
    assert "fallback" in caplog.text and "timeout" in caplog.text


# ---- nicknames, gated by the ROM's own codec -------------------------


def test_nickname_accepts_a_typable_name(charmap):
    b, _ = make(reply(name="MUDDY", reason="short and rude-free"), charmap=charmap)
    assert b.nickname("MUDKIP", fallback="MUDKIP") == "MUDDY"


def test_nickname_uppercases_and_trims(charmap):
    b, _ = make(reply(name="  muddy  "), charmap=charmap)
    assert b.nickname("MUDKIP", fallback="MUDKIP") == "MUDDY"


def test_nickname_rejects_a_character_the_charmap_has_never_heard_of(charmap):
    """The gate is pret's charmap.txt, not a transcribed alphabet."""
    b, _ = make(reply(name="MUD\u2603KIP"), charmap=charmap)
    assert b.nickname("MUDKIP", fallback="MUDKIP") == "MUDKIP"
    assert "untypable" in b.last_reason
    assert "charmap.txt" in b.last_reason


def test_nickname_rejects_a_name_that_overruns_the_name_buffer(charmap):
    """11 letters plus the 0xFF terminator do not fit a 10-character name, and
    the byte count -- not a len() -- is what says so."""
    b, _ = make(reply(name="MUDKIPPPPPP"), charmap=charmap)
    assert b.nickname("MUDKIP", fallback="MUDKIP") == "MUDKIP"
    assert "12 > 11 bytes" in b.last_reason


def test_nickname_rejects_punctuation_the_keyboard_hides_behind_a_page(charmap):
    b, _ = make(reply(name="MR. MUD"), charmap=charmap)
    assert b.nickname("MUDKIP", fallback="MUDKIP") == "MUDKIP"
    assert "A-Z, 0-9 or space" in b.last_reason


def test_nickname_honours_a_shorter_max_len(charmap):
    b, _ = make(reply(name="MUDDY"), charmap=charmap)
    assert b.nickname("MUDKIP", fallback="MUD", max_len=3) == "MUD"
    assert "bytes" in b.last_reason


def test_an_untypable_fallback_is_a_caller_bug_and_raises(charmap):
    """Fail loudly: a fallback nobody can type is broken at every call site."""
    b, _ = make(reply(name="MUDDY"), charmap=charmap)
    with pytest.raises(ValueError, match="not a typable nickname"):
        b.nickname("MUDKIP", fallback="mudkip!")


# ---- the circuit breaker ---------------------------------------------


def test_breaker_opens_after_n_consecutive_failures_and_stops_calling():
    """A dead server must cost one timeout, not one per decision."""
    clock = FakeClock()
    t = FakeTransport(TimeoutError("dead"))
    b = Brain(transport=t, clock=clock, failures=3, cooldown=60.0)

    for _ in range(3):
        assert b.choose("q", ["A", "B"], fallback="A") == "A"
    assert len(t.calls) == 3, "the first three attempts really go out"
    assert b.stats()["circuit_trips"] == 1

    for _ in range(10):
        assert b.choose("another q", ["A", "B"], fallback="B") == "B"
    assert len(t.calls) == 3, "the breaker is open: nothing more is sent"
    assert "circuit open" in b.last_reason
    assert b.stats()["short_circuits"] == 10


def test_breaker_closes_again_after_the_cooldown():
    clock = FakeClock()
    t = FakeTransport(TimeoutError("dead"), TimeoutError("dead"), reply(choice="B"))
    b = Brain(transport=t, clock=clock, failures=2, cooldown=60.0)

    b.choose("q1", ["A", "B"], fallback="A")
    b.choose("q2", ["A", "B"], fallback="A")
    assert b.stats()["circuit_open_for"] == 60.0

    clock.advance(59.0)
    assert b.choose("q3", ["A", "B"], fallback="A") == "A"
    assert len(t.calls) == 2, "still cooling down"

    clock.advance(2.0)
    assert b.choose("q3", ["A", "B"], fallback="A") == "B", "half-open retry succeeds"
    assert len(t.calls) == 3
    assert b.stats()["consecutive_failures"] == 0
    assert b.stats()["circuit_open_for"] == 0.0


def test_a_success_resets_the_failure_count_before_the_breaker_trips():
    clock = FakeClock()
    t = FakeTransport(TimeoutError("blip"), reply(choice="A"), TimeoutError("blip"))
    b = Brain(transport=t, clock=clock, failures=3, cooldown=60.0)
    b.choose("q1", ["A", "B"], fallback="B")
    b.choose("q2", ["A", "B"], fallback="B")
    b.choose("q3", ["A", "B"], fallback="B")
    assert b.stats()["consecutive_failures"] == 1
    assert b.stats()["circuit_trips"] == 0


def test_a_bad_answer_counts_toward_the_breaker_too():
    """A server that is up but answering rubbish is just as useless."""
    clock = FakeClock()
    t = FakeTransport(reply(choice="Z"))
    b = Brain(transport=t, clock=clock, failures=2, cooldown=30.0)
    b.choose("q1", ["A", "B"], fallback="A")
    b.choose("q2", ["A", "B"], fallback="A")
    assert b.stats()["circuit_trips"] == 1
    b.choose("q3", ["A", "B"], fallback="A")
    assert len(t.calls) == 2


# ---- the cache -------------------------------------------------------


def test_an_identical_question_is_answered_from_cache():
    b, t = make(reply(choice="B", reason="stronger"))
    first = b.choose("best move?", ["A", "B"], fallback="A")
    second = b.choose("best move?", ["A", "B"], fallback="A")
    assert first == second == "B"
    assert len(t.calls) == 1
    assert b.stats()["cache_hits"] == 1
    assert b.last_reason == "stronger", "the cached reason survives"


def test_cache_is_keyed_on_context_and_options_too():
    b, t = make(reply(choice="B"), reply(choice="A"), reply(choice="B"))
    b.choose("q", ["A", "B"], fallback="A")
    b.choose("q", ["A", "B"], fallback="A", context="it is poisoned")
    b.choose("q", ["A", "B", "C"], fallback="A")
    assert len(t.calls) == 3
    assert b.stats()["cache_hits"] == 0


def test_kinds_do_not_share_a_cache_slot():
    b, t = make(reply(choice="A"), reply(answer="YES"))
    b.choose("go?", ["A", "B"], fallback="A")
    assert b.yes_no("go?", fallback=False) is True
    assert len(t.calls) == 2


def test_a_fallback_is_never_cached():
    """Caching a transient failure would freeze the wrong answer for the run."""
    b, t = make(TimeoutError("blip"), reply(choice="B", reason="recovered"))
    assert b.choose("q", ["A", "B"], fallback="A") == "A"
    assert b.choose("q", ["A", "B"], fallback="A") == "B"
    assert len(t.calls) == 2


def test_a_cache_hit_is_still_logged(caplog):
    caplog.set_level(logging.INFO, logger="pokeagent.brain")
    b, _ = make(reply(choice="B", reason="stronger"))
    b.choose("q", ["A", "B"], fallback="A")
    caplog.clear()
    b.choose("q", ["A", "B"], fallback="A")
    assert "cache" in caplog.text and "stronger" in caplog.text


# ---- availability and the off switch ---------------------------------


def test_available_probes_the_tag_list_once_then_caches():
    tags = {"models": [{"name": "gemma4:e4b"}, {"name": "gemma4:12b"}]}
    b, t = make(tags, clock=FakeClock(), probe_ttl=30.0)
    assert b.available() is True
    assert b.available() is True
    assert len(t.calls) == 1
    assert t.calls[0][0].endswith("/api/tags")
    assert t.calls[0][1] is None, "a probe is a GET"


def test_available_is_false_when_the_model_is_absent():
    b, _ = make({"models": [{"name": "llama3:8b"}]})
    assert b.available() is False
    assert "gemma4:e4b not on" in b.last_reason


def test_available_is_false_and_free_when_the_server_is_down():
    b, _ = make(ConnectionRefusedError("nope"))
    assert b.available() is False
    assert "probe failed" in b.last_reason


def test_available_short_circuits_while_the_breaker_is_open():
    clock = FakeClock()
    t = FakeTransport(TimeoutError("dead"))
    b = Brain(transport=t, clock=clock, failures=1, cooldown=60.0)
    b.choose("q", ["A", "B"], fallback="A")
    assert b.available() is False
    assert len(t.calls) == 1, "no probe while the breaker is open"
    assert "circuit open" in b.last_reason


def test_probe_re_runs_once_the_ttl_expires():
    clock = FakeClock()
    b, t = make({"models": [{"name": "gemma4:e4b"}]}, clock=clock, probe_ttl=30.0)
    assert b.available() is True
    clock.advance(31.0)
    assert b.available() is True
    assert len(t.calls) == 2


def test_a_disabled_brain_never_calls_out():
    b, t = make(reply(choice="B"), enabled=False)
    assert b.choose("q", ["A", "B"], fallback="A") == "A"
    assert b.yes_no("q", fallback=True) is True
    assert b.available() is False
    assert t.calls == []
    assert b.last_reason == "brain disabled"


# ---- caller bugs raise rather than degrade ---------------------------


def test_an_empty_option_list_raises():
    b, _ = make(reply(choice="A"))
    with pytest.raises(ValueError, match="no options"):
        b.choose("q", [], fallback="A")


def test_duplicate_options_raise():
    b, _ = make(reply(choice="A"))
    with pytest.raises(ValueError, match="duplicate options"):
        b.choose("q", ["A", "A"], fallback="A")


def test_a_fallback_outside_the_options_raises():
    """Silently returning an illegal fallback would poison the caller."""
    b, _ = make(reply(choice="A"))
    with pytest.raises(ValueError, match="not one of"):
        b.choose("q", ["A", "B"], fallback="Z")


def test_a_fallback_outside_the_number_range_raises():
    b, _ = make(reply(value=1))
    with pytest.raises(ValueError, match=r"outside \[0, 10\]"):
        b.number("q", 0, 10, fallback=99)


def test_an_inverted_number_range_raises():
    b, _ = make(reply(value=1))
    with pytest.raises(ValueError, match="empty range"):
        b.number("q", 10, 0, fallback=5)


def test_a_rank_fallback_must_be_a_permutation():
    b, _ = make(reply(order=["A", "B"]))
    with pytest.raises(ValueError, match="not a permutation"):
        b.rank("q", ["A", "B"], fallback=["A"])


# ---- the transport itself --------------------------------------------


def test_http_json_turns_a_socket_timeout_into_a_timeout_error(monkeypatch):
    """urllib buries socket.timeout inside URLError; a slow server and an
    absent one must be distinguishable, because only one of them is worth
    retrying later."""
    import socket
    import urllib.error

    def boom(req, timeout=None):
        raise urllib.error.URLError(socket.timeout("timed out"))

    monkeypatch.setattr(B.urllib.request, "urlopen", boom)
    with pytest.raises(TimeoutError, match="no reply in 5s"):
        B.http_json("http://x/api/chat", {}, 5.0)


def test_http_json_lets_a_real_connection_error_through(monkeypatch):
    import urllib.error

    def boom(req, timeout=None):
        raise urllib.error.URLError(ConnectionRefusedError("refused"))

    monkeypatch.setattr(B.urllib.request, "urlopen", boom)
    with pytest.raises(urllib.error.URLError):
        B.http_json("http://x/api/chat", {}, 5.0)
