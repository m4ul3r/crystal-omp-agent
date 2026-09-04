"""Public Driver facade assembled from non-overlapping ownership mixins."""

from .battle import BattleMixin, DecisionRequired
from .core import CoreMixin
from .inventory import HealError, InventoryMixin, heal_pokecenter
from .navigation import NavigationMixin, TravelError
from .ui import UIMixin
from .world import WorldMixin


class Driver(CoreMixin, WorldMixin, UIMixin, BattleMixin, InventoryMixin, NavigationMixin):
    """Stable public facade for all emulator-driving behavior."""


__all__ = ["Driver", "DecisionRequired", "TravelError", "HealError"]
