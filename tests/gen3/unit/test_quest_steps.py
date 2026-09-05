

def test_require_gates_offer_steps_at_the_right_time():
    """The chain is flat but the game is not.

    The Go-Goggles exist only after badge 4 and the harbor scene only after
    Mt. Pyre; ungated, the first unmet step is offered forever even when
    nothing the player can do will meet it. And the cable-car step must stop
    being offered once the leader falls, because its own condition
    (VAR_JAGGED_PASS_ASH_WEATHER) resets on every Route 112 transition and
    would otherwise send the run back up the mountain for eternity.
    """
    from pokeagent.quest import Quest, StoryStep

    class FakeState:
        def __init__(self, flags=(), vars_=None):
            self._flags = set(flags)
            self._vars = vars_ or {}
        def flag(self, name): return name in self._flags
        def var(self, name): return self._vars.get(name, 0)

    class FakeDriver:
        def __init__(self, state): self.state = state

    q = Quest.__new__(Quest)   # only _step_offered is exercised

    def offered(require, state):
        q.d = FakeDriver(state)
        return q._step_offered(StoryStep(
            "t", "flag_unset", "FLAG_X", None, "Map", None, require=require,
        ))

    assert offered(None, FakeState())
    assert offered("FLAG_A", FakeState(flags=["FLAG_A"]))
    assert not offered("FLAG_A", FakeState())
    assert offered("!FLAG_A", FakeState())
    assert not offered("!FLAG_A", FakeState(flags=["FLAG_A"]))
    assert offered("VAR_N>=2", FakeState(vars_={"VAR_N": 2}))
    assert not offered("VAR_N>=2", FakeState(vars_={"VAR_N": 1}))

    class Exploding:
        def flag(self, name): raise RuntimeError("save not loaded")
        def var(self, name): raise RuntimeError("save not loaded")
    assert not offered("FLAG_A", Exploding()), (
        "a gate that cannot be read is a step that cannot be acted on"
    )
