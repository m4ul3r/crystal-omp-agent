

def test_live_alternative_reads_mon_objects_not_dicts():
    """The crash that killed a five-badge run mid-battle on Route 118.

    `analysis["party"]` carries Mon OBJECTS and `analysis["moves"]` carries
    dicts, and the retirement fallback assumed dicts for both. The first time
    a retired action needed a replacement, the whole play loop died with
    `'Mon' object has no attribute 'get'` -- a crash reachable only after
    something else had already gone wrong, which is the worst place to put an
    assumption.
    """
    from pokeagent.battle import BattleSession

    class Mon:
        def __init__(self, hp): self.hp = hp

    b = BattleSession.__new__(BattleSession)
    b._dead_actions = {repr(("attack", 0))}

    # Moves as dicts, party as objects: the shapes the engine actually hands us.
    analysis = {
        "moves": [
            {"slot": 0, "pp": 10, "kind": "damage", "power": 90},
            {"slot": 1, "pp": 5, "kind": "damage", "power": 40},
        ],
        "party": [Mon(100), Mon(80), Mon(0)],
    }
    # Slot 0 is retired, so the next usable move wins -- no exception.
    assert b._live_alternative(analysis) == ("attack", 1)

    # With every move retired it must switch to a LIVING benched mon.
    b._dead_actions |= {repr(("attack", 1))}
    assert b._live_alternative(analysis) == ("switch", 1)

    # Nothing left at all is flight, not a crash.
    b._dead_actions |= {repr(("switch", 1)), repr(("switch", 2))}
    assert b._live_alternative(analysis) == "flee"

    # And an empty analysis must not raise either.
    assert b._live_alternative({}) == "flee"


def test_the_active_battler_is_never_its_own_replacement():
    """The Route 121 deadlock: twelve minutes against a foe on 13 HP.

    A fainted LOTTAD sat in slot 0 and MIGHTYENA was ACTIVE in slot 1, out of
    PP on every move. The old rule skipped only slot 0, so the fallback kept
    proposing ("switch", 1) -- the mon already standing there. The engine
    answered "party slot 1 is already the active battler", the action was
    retired, flight was refused because the Kecleon ambush counts as a trainer
    battle, and nothing was left to try. Four healthy mons were on the bench.
    """
    from pokeagent.battle import BattleSession

    class Mon:
        def __init__(self, hp): self.hp = hp

    b = BattleSession.__new__(BattleSession)
    b._dead_actions = {repr(("attack", 0)), repr(("attack", 1))}
    analysis = {
        "moves": [{"slot": 0, "pp": 0, "kind": "damage", "power": 40}],
        "party": [Mon(0), Mon(66), Mon(132), Mon(122)],
        "active_party_index": 1,
    }
    # Not slot 0 (fainted) and NOT slot 1 (already fighting).
    assert b._live_alternative(analysis) == ("switch", 2)

    # Retire that one too and it moves along the bench rather than looping.
    b._dead_actions |= {repr(("switch", 2))}
    assert b._live_alternative(analysis) == ("switch", 3)


def test_the_stall_escape_calls_a_method_that_exists():
    """`self.wild()` never existed, and it sat on the only escape path.

    Four stalled turns crashed the process with `'BattleSession' object has no
    attribute 'wild'` instead of fleeing -- which killed a collection run
    outright. The reachable-attribute check is the point: a crash on the
    recovery path is worse than the thing it recovers from.
    """
    from pokeagent.battle import BattleSession

    assert not hasattr(BattleSession, "wild")
    assert callable(BattleSession.can_flee)
    src = BattleSession.play.__code__.co_consts
    names = BattleSession.play.__code__.co_names
    assert "wild" not in names, "play() still calls the method that never was"
    assert "can_flee" in names
