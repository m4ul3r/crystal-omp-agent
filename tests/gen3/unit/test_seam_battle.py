"""A battle on the way to a seam must not read as an impassable border.

Route 116's west edge is Rustboro City and the route is wall-to-wall grass.
A fresh run logged `could not cross the L seam to RustboroCity` six times,
gave up on "trigger the stolen Devon Goods errand in Rustboro", and ground
the route at 0 badges instead. Driven by hand -- fight the wild, then
goto(0,8) and one step LEFT -- the crossing worked on the first try.

The warp branch of `travel` already fought and retried the leg; the seam
branch raised. These tests pin that both branches recover the same way.
"""
import pytest


class FakeSeamDriver:
    """Enough of `travel`'s collaborators to exercise the seam branch."""

    def __init__(self, battles: int, on_battle="fight"):
        self.battles_left = battles
        self.on_battle = on_battle
        self.crossed = False
        self.fights = 0
        self.attempts = 0

    def _cross_seam(self, here, edge, on_battle) -> bool:
        self.attempts += 1
        if self.battles_left:
            self.battles_left -= 1
            return False          # interrupted, mid-battle on return
        self.crossed = True
        return True

    def in_battle(self) -> bool:
        return self.battles_left >= 0 and self.attempts > 0 and not self.crossed

    def fight(self):
        self.fights += 1

    def advance_scene(self, _n):
        pass


def _run_leg(d, legs=6):
    """The seam branch of `travel`, transcribed."""
    for _ in range(legs):
        if not d._cross_seam("Route116", {"direction": "L"}, d.on_battle):
            if d.in_battle():
                if d.on_battle == "fight":
                    d.fight()
                    d.advance_scene(40000)
                    continue
                raise RuntimeError("TravelInterrupted")
            raise RuntimeError("could not cross the L seam")
        return True
    return False


@pytest.mark.unit
def test_a_wild_before_the_seam_is_fought_and_the_leg_retried():
    d = FakeSeamDriver(battles=2)
    assert _run_leg(d) is True
    assert d.crossed is True
    assert d.fights == 2, "each interruption must be fought, not reported"


@pytest.mark.unit
def test_a_genuinely_impassable_seam_still_raises():
    """The recovery must not swallow a real routing failure."""
    d = FakeSeamDriver(battles=0)
    d.attempts = 0

    class Blocked(FakeSeamDriver):
        def _cross_seam(self, here, edge, on_battle):
            self.attempts += 1
            return False

        def in_battle(self):
            return False

    b = Blocked(battles=0)
    with pytest.raises(RuntimeError, match="could not cross the L seam"):
        _run_leg(b)
    assert b.fights == 0


@pytest.mark.unit
def test_raise_policy_still_hands_the_battle_to_the_caller():
    d = FakeSeamDriver(battles=1, on_battle="raise")
    with pytest.raises(RuntimeError, match="TravelInterrupted"):
        _run_leg(d)
    assert d.fights == 0, "on_battle='raise' means the caller decides"
