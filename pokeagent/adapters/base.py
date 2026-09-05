"""What a generation has to provide.

An adapter's job is narrow: build the game-specific stack (emulator, symbol
table, text codec, state reader, map data, battle session) and hand it back
behind one shape, so `Driver` and everything above it never branch on
generation.

The interface is deliberately a *constructor* rather than a hundred abstract
read methods. Gen 2 and Gen 3 disagree about almost every detail of how state
is stored, but they agree completely about what a caller wants to know -- so
the seam belongs at "give me a state reader", not at "read the party".

Anything a generation genuinely cannot do reports that rather than faking it:
`Backend.capabilities` is a set of strings, and callers check it. A Gen-1 game
has no held items and no Special split; pretending otherwise would be the
"harness lied" failure this project keeps guarding against.
"""

from dataclasses import dataclass, field


@dataclass(slots=True)
class Backend:
    """The assembled, game-specific machine."""

    spec: object                 # GameSpec
    emu: object                  # the emulator wrapper
    sym: object                  # symbol table
    charmap: object              # text codec
    consts: object               # parsed constants
    names: object                # ROM data tables
    state: object                # structured state reader
    nav: object                  # map data + pathfinding
    capabilities: set = field(default_factory=set)
    #: Adapter-specific handles that only that generation has (Gen 3's
    #: metatile behaviours, Gen 2's decomp root). Kept in a dict rather than
    #: as attributes because Backend is slotted, and kept OUT of the common
    #: surface because nothing generic should reach for them.
    extra: dict = field(default_factory=dict)

    @property
    def generation(self):
        return self.spec.generation

    def battle(self):
        raise NotImplementedError


class MissingArtifacts(RuntimeError):
    """The game is declared but its files are not here.

    Carries the exact list, because "it does not work" is not actionable and
    a declared game that pretends to work is worse than one that refuses.
    """

    def __init__(self, spec, missing):
        self.spec = spec
        self.missing = list(missing)
        joined = "\n  ".join(str(m) for m in self.missing)
        super().__init__(
            f"{spec.name} is registered but its artifacts are missing:\n  {joined}\n"
            f"Its decompilation is {spec.decomp}; build it and supply a ROM whose "
            f"sha1 is one of {spec.rom_sha1 or '(any matching build)'}."
        )


class GameAdapter:
    """Base class. One subclass per generation, not per game."""

    #: Capabilities every game in this generation has.
    CAPABILITIES: set = set()

    def __init__(self, spec):
        self.spec = spec

    # ---- what the harness needs -------------------------------------------

    def artifacts(self) -> dict:
        """``{label: Path}`` of every file this game needs at runtime."""
        raise NotImplementedError

    def missing(self) -> list:
        return [f"{label}: {path}" for label, path in self.artifacts().items()
                if not path.exists()]

    def require(self):
        missing = self.missing()
        if missing:
            raise MissingArtifacts(self.spec, missing)

    def open(self, state_path=None, fresh=False) -> Backend:
        """Build the whole stack for this game."""
        raise NotImplementedError

    def __repr__(self):
        return f"<{type(self).__name__} {self.spec.id} ({self.spec.status})>"


def resolve(spec) -> GameAdapter:
    """Instantiate the adapter named by a GameSpec."""
    module_path, _, class_name = spec.adapter.partition(":")
    import importlib

    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise MissingArtifacts(
            spec, [f"adapter module {module_path} is not importable: {exc}"]
        ) from exc
    return getattr(module, class_name)(spec)
