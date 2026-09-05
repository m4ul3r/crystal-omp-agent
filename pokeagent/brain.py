"""A small, local, *constrained* brain for the decisions not worth a frontier model.

A run of this harness is thousands of tiny choices -- which of four moves, is
this NPC worth talking to, what do I call the Mudkip, how many Potions to buy.
Each one is trivially decidable and none of them justifies a paid round trip.
This module hands those to an Ollama server (``gemma4:e4b`` by default), and
does it under three rules learned the hard way in this repo:

1. **The harness must never break because a local LLM is having a bad day.**
   Every entry point takes a ``fallback`` and returns it when the server is
   down, slow, or answers nonsense. Nothing here raises for a model problem;
   it raises only for a *caller* problem (an empty option list, a fallback
   that is not a legal answer), because that is a bug worth surfacing.
   A dead server costs one timeout, not one per decision: after
   :data:`DEFAULT_FAILURES` consecutive failures a circuit breaker opens and
   every call short-circuits to its fallback for :data:`DEFAULT_COOLDOWN`
   seconds.

2. **No free-form answers.** Every helper hands the model an explicit menu and
   validates that what came back is on it. The transport asks Ollama for
   structured output (``format`` = a JSON schema, supported since 0.5.0 and
   confirmed against the 0.32.14 server this was written for), which makes the
   *shape* reliable; the validation here makes the *content* reliable, because
   a JSON schema's ``enum`` is a decoding hint and not a guarantee. An 8B
   model at Q4 will cheerfully invent a fifth option out of a list of four.

3. **No silent decision.** Every call logs at INFO through the ``pokeagent``
   logger -- the answer, where it came from, and why -- so
   :class:`pokeagent.live.LiveFeed` narrates it and a human watching the bar
   widget can see the machine's reasoning. Unexplained falsy returns are this
   project's worst defect class; the twin of that rule is that an invisible
   decision is just as bad. :attr:`Brain.last_reason` always explains the most
   recent answer, fallback included.

Nothing game-specific lives here: the caller supplies the options, so this
works for Sapphire, for any other generation behind
:mod:`pokeagent.gamespec`, or for a decision that has nothing to do with a
game. The one game-shaped helper, :meth:`Brain.nickname`, validates through
:class:`pokeagent.charmap.Charmap` rather than a transcribed alphabet, so
"can the game type this" is answered by pret's own encoding table.

Stdlib only -- ``urllib``. Adding an SDK to reach a JSON endpoint that takes
one POST would be a dependency for nothing.
"""

import json
import logging
import os
import re
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

log = logging.getLogger("pokeagent.brain")

#: Where the Ollama server lives. Ollama's own default bind, so a machine
#: running ``ollama serve`` needs no edit and a machine running nothing fails
#: fast with a refused connection instead of a 20s timeout. The lane was
#: written against a box on a public address; that is a per-machine fact and
#: belongs in the environment, never in the source.
DEFAULT_HOST = os.environ.get("POKEAGENT_OLLAMA_HOST", "http://127.0.0.1:11434")
DEFAULT_MODEL = os.environ.get("POKEAGENT_OLLAMA_MODEL", "gemma4:e4b")

#: Seconds to wait for one decision. Measured against the real server, a
#: warm ``gemma4:e4b`` answers a short constrained question in 3-18s
#: depending on queue depth, so a tight timeout would fall back constantly
#: and a generous one is the difference between "slow" and "wrong". The
#: circuit breaker is what keeps a *dead* server from costing this per call.
DEFAULT_TIMEOUT = 20.0

#: Consecutive failures that open the breaker, and how long it stays open.
DEFAULT_FAILURES = 3
DEFAULT_COOLDOWN = 60.0

#: :meth:`Brain.available` caches its answer this long. The probe is a GET of
#: ``/api/tags``; cheap, but not free, and it gets asked in loops.
DEFAULT_PROBE_TTL = 30.0

#: Hard ceiling on generated tokens. A structured answer is ~30 tokens; this
#: stops a model that has lost the plot from eating the whole timeout.
NUM_PREDICT = 160
#: Cap on the model's free-text justification, enforced in the schema. Long
#: enough to be informative, short enough that the reason is not the latency.
REASON_MAX = 160

#: Characters a Pokemon nickname may use. The naming screen can reach more
#: (three pages of ``sKeyboardCharacters``, src/naming_screen.c:1772), but a
#: name restricted to these needs no page switching and reads unambiguously
#: in a log line. :class:`pokeagent.charmap.Charmap` is the second gate and
#: the authority on what the ROM can represent at all.
NICKNAME_CHARS = re.compile(r"^[A-Z0-9 ]+$")

_SYSTEM = (
    "You help a program play a Pokemon game. Answer with JSON matching the "
    "schema, nothing else. Pick only from the options you are given. Keep "
    "'reason' to one short clause."
)


class _Invalid(ValueError):
    """The model answered, but not with something we can use."""


@dataclass(slots=True)
class Decision:
    """One answer and its provenance, for logs and for after-the-fact audit."""

    kind: str
    question: str
    answer: object
    reason: str
    source: str  #: "model", "cache" or "fallback"
    latency_s: float = 0.0


@dataclass(slots=True)
class _Breaker:
    """Consecutive-failure breaker. Half-open: one probe call after cooldown,
    which either closes it or restarts the cooldown."""

    threshold: int = DEFAULT_FAILURES
    cooldown: float = DEFAULT_COOLDOWN
    failures: int = 0
    open_until: float = 0.0
    trips: int = 0

    def blocked(self, now: float) -> float:
        """Seconds still to wait, or 0.0 when a call may proceed."""
        return max(0.0, self.open_until - now)

    def record(self, ok: bool, now: float) -> None:
        if ok:
            self.failures = 0
            self.open_until = 0.0
            return
        self.failures += 1
        if self.failures >= self.threshold:
            self.open_until = now + self.cooldown
            self.trips += 1


def http_json(url: str, payload: dict | None, timeout: float) -> dict:
    """POST ``payload`` as JSON (or GET when it is None) and parse the reply.

    Raises :class:`TimeoutError` for a timeout so the caller can count it
    separately -- urllib buries ``socket.timeout`` inside ``URLError``, and a
    slow server and an absent one deserve different reasons.
    """
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.URLError as err:
        if isinstance(err.reason, (socket.timeout, TimeoutError)):
            raise TimeoutError(f"no reply in {timeout:g}s") from err
        raise
    except socket.timeout as err:  # pragma: no cover - urllib normally wraps it
        raise TimeoutError(f"no reply in {timeout:g}s") from err


def _schema(value: dict) -> dict:
    """A two-field object schema: the answer, plus the model's reason for it."""
    return {
        "type": "object",
        "properties": {
            **value,
            "reason": {"type": "string", "maxLength": REASON_MAX},
        },
        "required": [*value, "reason"],
    }


def _loads(text: str) -> dict:
    """Parse the model's content. Structured output returns bare JSON, but a
    fenced block is the one deviation worth surviving rather than logging."""
    body = text.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        obj = json.loads(body)
    except (ValueError, IndexError) as err:
        raise _Invalid(f"reply is not JSON ({err})") from err
    if not isinstance(obj, dict):
        raise _Invalid(f"reply is {type(obj).__name__}, want object")
    return obj


class Brain:
    """Constrained small decisions, delegated to a local model.

    ``transport`` is the seam for tests: any ``(url, payload, timeout) ->
    dict`` callable. ``clock`` likewise, so the breaker's cooldown can be
    stepped without sleeping.
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        model: str = DEFAULT_MODEL,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        enabled: bool = True,
        failures: int = DEFAULT_FAILURES,
        cooldown: float = DEFAULT_COOLDOWN,
        probe_ttl: float = DEFAULT_PROBE_TTL,
        think: bool | None = False,
        seed: int = 0,
        temperature: float = 0.0,
        charmap=None,
        transport=None,
        clock=time.monotonic,
    ):
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.enabled = enabled
        self.think = think
        self.seed = seed
        self.temperature = temperature
        self._transport = transport or http_json
        self._clock = clock
        self._charmap = charmap
        self._breaker = _Breaker(threshold=failures, cooldown=cooldown)
        self._probe_ttl = probe_ttl
        self._probe: tuple[float, bool] | None = None
        self._cache: dict[tuple, tuple[object, str]] = {}
        #: Why the last answer is what it is -- model reason or failure cause.
        self.last_reason: str = "no decision yet"
        #: The full record of the last answer.
        self.last_decision: Decision | None = None
        self._counts = {
            "hits": 0,
            "fallbacks": 0,
            "timeouts": 0,
            "cache_hits": 0,
            "errors": 0,
            "invalid": 0,
            "short_circuits": 0,
        }

    # ---- introspection ------------------------------------------------

    def available(self) -> bool:
        """Is the server up and carrying our model? Cached for
        :data:`DEFAULT_PROBE_TTL`, and False without a call while the breaker
        is open -- an open breaker is exactly the "not available" answer."""
        now = self._clock()
        if not self.enabled:
            self.last_reason = "brain disabled"
            return False
        wait = self._breaker.blocked(now)
        if wait:
            self.last_reason = f"circuit open, {wait:.0f}s to go"
            return False
        if self._probe and now - self._probe[0] < self._probe_ttl:
            return self._probe[1]
        try:
            tags = self._transport(f"{self.host}/api/tags", None, self.timeout)
            models = {m.get("name", "") for m in tags.get("models", [])}
            ok = self.model in models
            self.last_reason = (
                f"{self.model} present" if ok else f"{self.model} not on {self.host}"
            )
        except Exception as err:
            ok = False
            self.last_reason = f"probe failed: {type(err).__name__}: {err}"
        self._probe = (now, ok)
        return ok

    def stats(self) -> dict:
        """Counters, plus the breaker's live position. Cheap to poll."""
        now = self._clock()
        return {
            **self._counts,
            "cached": len(self._cache),
            "consecutive_failures": self._breaker.failures,
            "circuit_trips": self._breaker.trips,
            "circuit_open_for": round(self._breaker.blocked(now), 1),
        }

    def state(self) -> str:
        """One word for the widget's card, with NO I/O: ``off``, ``ready``,
        ``unreachable`` or ``unknown``.

        Most recent evidence wins: an open breaker, then the last decision
        (a model answer or a transport failure), then the last probe. A
        card refreshed once a minute must never cost a 20s probe -- the loop
        stalled for exactly that when it asked ``available()`` instead.
        """
        if not self.enabled:
            return "off"
        if self._breaker.blocked(self._clock()):
            return "unreachable"
        last = self.last_decision
        if last is not None:
            if last.source == "model":
                return "ready"
            if last.source == "fallback" and self._breaker.failures > 0:
                return "unreachable"
        if self._probe is not None:
            return "ready" if self._probe[1] else "unreachable"
        return "unknown"

    # ---- decisions ----------------------------------------------------

    def choose(self, question, options, *, fallback, context=None, timeout=None):
        """Pick one of ``options``. Returns ``fallback`` if the model cannot.

        ``timeout`` overrides the instance default for this call. A decision
        that blocks a BATTLE TURN cannot afford the 20s a nickname can: the
        default is generous because gemma4:e4b's first token can take seconds
        under queue, but mid-battle that is a visible stall.
        """
        opts = self._options(options)
        if fallback not in opts:
            raise ValueError(f"fallback {fallback!r} is not one of {opts}")

        def extract(obj):
            pick = obj.get("choice")
            if pick not in opts:
                raise _Invalid(f"answered {pick!r}, not one of {opts}")
            return pick

        return self._ask(
            kind="choose",
            question=question,
            context=context,
            options=opts,
            prompt=f"Choose exactly one.\nOptions: {', '.join(opts)}",
            schema=_schema({"choice": {"type": "string", "enum": list(opts)}}),
            extract=extract,
            fallback=fallback,
            timeout=timeout,
        )

    def rank(self, question, options, *, fallback, context=None) -> list:
        """Reorder ``options``, best first. The answer must be a permutation:
        a list that drops or duplicates an option is not a ranking."""
        opts = self._options(options)
        if sorted(fallback) != sorted(opts):
            raise ValueError(f"fallback {list(fallback)} is not a permutation of {opts}")

        def extract(obj):
            order = obj.get("order")
            if not isinstance(order, list):
                raise _Invalid(f"order is {type(order).__name__}, want list")
            if sorted(order) != sorted(opts):
                raise _Invalid(f"ranked {order}, not a permutation of {opts}")
            return order

        return self._ask(
            kind="rank",
            question=question,
            context=context,
            options=opts,
            prompt=(
                "Rank every option, best first. Use each exactly once.\n"
                f"Options: {', '.join(opts)}"
            ),
            schema=_schema(
                {
                    "order": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(opts)},
                        "minItems": len(opts),
                        "maxItems": len(opts),
                    }
                }
            ),
            extract=extract,
            fallback=list(fallback),
        )

    def yes_no(self, question, *, fallback, context=None) -> bool:
        """A boolean, asked as YES/NO because a model is far better at an
        enum of two words than at JSON ``true``/``false``."""
        if not isinstance(fallback, bool):
            raise ValueError(f"fallback {fallback!r} is not a bool")

        def extract(obj):
            answer = obj.get("answer")
            if answer not in ("YES", "NO"):
                raise _Invalid(f"answered {answer!r}, want YES or NO")
            return answer == "YES"

        return self._ask(
            kind="yes_no",
            question=question,
            context=context,
            options=("YES", "NO"),
            prompt="Answer YES or NO.",
            schema=_schema({"answer": {"type": "string", "enum": ["YES", "NO"]}}),
            extract=extract,
            fallback=fallback,
        )

    def number(self, question, low, high, *, fallback, context=None) -> int:
        """An integer in ``[low, high]``. Bounds are inclusive."""
        low, high = int(low), int(high)
        if low > high:
            raise ValueError(f"empty range [{low}, {high}]")
        if not isinstance(fallback, int) or isinstance(fallback, bool):
            raise ValueError(f"fallback {fallback!r} is not an int")
        if not low <= fallback <= high:
            raise ValueError(f"fallback {fallback} is outside [{low}, {high}]")

        def extract(obj):
            value = obj.get("value")
            if isinstance(value, bool) or not isinstance(value, int):
                raise _Invalid(f"answered {value!r}, want an integer")
            if not low <= value <= high:
                raise _Invalid(f"answered {value}, outside [{low}, {high}]")
            return value

        return self._ask(
            kind="number",
            question=question,
            context=context,
            options=(str(low), str(high)),
            prompt=f"Answer with one whole number from {low} to {high} inclusive.",
            schema=_schema(
                {"value": {"type": "integer", "minimum": low, "maximum": high}}
            ),
            extract=extract,
            fallback=fallback,
        )

    def nickname(self, species, *, fallback, max_len: int = 10) -> str:
        """Name a Pokemon something the game can actually type.

        Two gates, both real. :meth:`pokeagent.charmap.Charmap.encode` decides
        whether the ROM's text codec can represent the string at all and, via
        ``pad_to``, whether it fits the name buffer *including* its 0xFF
        terminator -- so the length limit is the game's, not a guess.
        :data:`NICKNAME_CHARS` then narrows it to the keys that need no
        keyboard page switch. Anything else falls back, loudly.
        """
        if not self._typable(fallback, max_len)[0]:
            raise ValueError(f"fallback {fallback!r} is not a typable nickname")

        def extract(obj):
            raw = obj.get("name")
            if not isinstance(raw, str):
                raise _Invalid(f"name is {type(raw).__name__}, want string")
            name = raw.strip().upper()
            ok, why = self._typable(name, max_len)
            if not ok:
                raise _Invalid(f"{raw!r} is untypable: {why}")
            return name

        return self._ask(
            kind="nickname",
            question=f"Nickname this {species}.",
            context=f"max {max_len} characters",
            options=(),
            prompt=(
                f"Invent a nickname for a {species}. Use only capital letters "
                f"A-Z, digits and spaces, at most {max_len} characters."
            ),
            schema=_schema({"name": {"type": "string", "maxLength": max_len}}),
            extract=extract,
            fallback=fallback,
        )

    # ---- internals ----------------------------------------------------

    @staticmethod
    def _options(options) -> tuple:
        opts = tuple(options)
        if not opts:
            raise ValueError("no options to choose from")
        if len(set(opts)) != len(opts):
            raise ValueError(f"duplicate options: {opts}")
        return opts

    def _typable(self, name, max_len) -> tuple[bool, str]:
        """``(ok, why_not)`` for a candidate nickname."""
        if not isinstance(name, str) or not name.strip():
            return False, "empty"
        name = name.strip()
        try:
            self.charmap.encode(name, pad_to=max_len + 1)
        except ValueError as err:
            return False, str(err)
        except Exception as err:  # charmap.txt absent: cannot validate, so refuse
            return False, f"charmap unavailable: {type(err).__name__}: {err}"
        if not NICKNAME_CHARS.match(name):
            return False, "not all A-Z, 0-9 or space"
        return True, ""

    @property
    def charmap(self):
        """Lazy, because most Brain users never name anything and building it
        parses pret's charmap.txt off disk."""
        if self._charmap is None:
            from .charmap import Charmap

            self._charmap = Charmap()
        return self._charmap

    def _ask(self, *, kind, question, context, options, prompt, schema, extract,
             fallback, timeout=None):
        key = (kind, question, options, context)
        cached = self._cache.get(key)
        if cached is not None:
            answer, reason = cached
            self._counts["cache_hits"] += 1
            return self._finish(
                Decision(kind, question, answer, reason, "cache"),
            )

        if not self.enabled:
            return self._fail(kind, question, fallback, "brain disabled")

        now = self._clock()
        wait = self._breaker.blocked(now)
        if wait:
            self._counts["short_circuits"] += 1
            return self._fail(
                kind, question, fallback, f"circuit open, {wait:.0f}s to go"
            )

        body = question if not context else f"{question}\nContext: {context}"
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": f"{body}\n{prompt}"},
            ],
            "format": schema,
            "options": {
                "temperature": self.temperature,
                "seed": self.seed,
                "num_predict": NUM_PREDICT,
            },
        }
        if self.think is not None:
            payload["think"] = self.think

        started = self._clock()
        try:
            reply = self._transport(
                f"{self.host}/api/chat", payload, timeout or self.timeout
            )
        except TimeoutError as err:
            self._counts["timeouts"] += 1
            self._breaker.record(False, self._clock())
            return self._fail(kind, question, fallback, f"timeout: {err}")
        except Exception as err:
            self._counts["errors"] += 1
            self._breaker.record(False, self._clock())
            return self._fail(
                kind, question, fallback, f"unreachable: {type(err).__name__}: {err}"
            )

        elapsed = self._clock() - started
        try:
            content = reply.get("message", {}).get("content")
            if not isinstance(content, str):
                raise _Invalid(f"no message.content in reply keys {sorted(reply)}")
            obj = _loads(content)
            answer = extract(obj)
        except _Invalid as err:
            self._counts["invalid"] += 1
            self._breaker.record(False, self._clock())
            return self._fail(kind, question, fallback, str(err), latency=elapsed)

        reason = str(obj.get("reason") or "").strip() or "no reason given"
        self._counts["hits"] += 1
        self._breaker.record(True, self._clock())
        self._cache[key] = (answer, reason)
        return self._finish(Decision(kind, question, answer, reason, "model", elapsed))

    def _fail(self, kind, question, fallback, reason, *, latency=0.0):
        self._counts["fallbacks"] += 1
        return self._finish(
            Decision(kind, question, fallback, reason, "fallback", latency)
        )

    def _finish(self, decision: Decision):
        """Single exit point, so no answer can escape unlogged."""
        self.last_decision = decision
        self.last_reason = decision.reason
        log.info(
            "%s -> %s [%s%s] %s",
            decision.kind,
            decision.answer,
            decision.source,
            f" {decision.latency_s:.1f}s" if decision.latency_s else "",
            decision.reason,
        )
        return decision.answer
