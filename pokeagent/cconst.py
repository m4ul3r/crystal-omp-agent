"""Constants parsed out of the decomp's C headers.

The Crystal harness had :mod:`crystalagent.asmconst` for rgbasm's
``const``/``const_def``/``shift_const`` vocabulary. Gen 3 is plain C, so the
grammar is just ``#define NAME value`` -- but values are frequently
*expressions over other constants*::

    #define SYSTEM_FLAGS      0x800
    #define FLAG_BADGE01_GET  (SYSTEM_FLAGS + 0x07)
    #define TRAINER_FLAG_START 0x500

so a parser that only accepts integer literals silently drops the badge
flags, every trainer flag, and half of ``vars.h``. We evaluate expressions
against the names already defined in the same header (and any headers passed
as context), and simply skip anything that still will not resolve --
function-like macros, casts, struct references.

Same doctrine as Crystal: the game's numbers are read from the game's own
source, never retyped here.
"""

import ast
import operator
import re

from . import paths

_DEFINE = re.compile(r"^\s*#define\s+([A-Za-z_]\w*)\s+(.+?)\s*(?://.*)?$")
_TRAILING_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_SUFFIX = re.compile(r"\b(0[xX][0-9a-fA-F]+|\d+)[uUlL]+\b")

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.FloorDiv: operator.floordiv,
    ast.Div: operator.floordiv,
    ast.Mod: operator.mod,
    ast.LShift: operator.lshift,
    ast.RShift: operator.rshift,
    ast.BitOr: operator.or_,
    ast.BitAnd: operator.and_,
    ast.BitXor: operator.xor,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
    ast.Invert: operator.invert,
}

_cache: dict[tuple, dict] = {}


def _evaluate(node, env):
    if isinstance(node, ast.Expression):
        return _evaluate(node.body, env)
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in env:
            raise KeyError(node.id)
        return env[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_evaluate(node.left, env), _evaluate(node.right, env))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_evaluate(node.operand, env))
    raise ValueError(ast.dump(node))


def parse_defines(*names, extra_env=None):
    """``parse_defines("flags.h")`` -> ``{"FLAG_BADGE01_GET": 0x807, ...}``.

    Several headers may be given; later ones see earlier ones' names, which
    is how ``flags.h`` resolves ``SYSTEM_FLAGS`` and ``vars.h`` resolves
    ``VARS_START``.
    """
    key = (names, tuple(sorted((extra_env or {}).items())))
    if key in _cache:
        return _cache[key]

    env = dict(extra_env or {})
    for name in names:
        path = paths.CONSTANTS / name if not str(name).startswith("/") else name
        text = _TRAILING_COMMENT.sub(" ", paths.require(
            path, f"constants header {name}", "is the pret/ submodule checked out?"
        ).read_text(encoding="utf-8", errors="replace"))
        # A #define may continue across escaped newlines.
        text = text.replace("\\\n", " ")
        pending = []
        for line in text.splitlines():
            m = _DEFINE.match(line)
            if not m:
                continue
            key_, raw = m.groups()
            if "(" in key_:  # function-like macro
                continue
            pending.append((key_, raw))

        # Two passes: a header may use a name defined further down.
        for _ in range(2):
            leftover = []
            for key_, raw in pending:
                expr = _SUFFIX.sub(r"\1", raw).strip()
                if not expr or expr.startswith("("):
                    # Keep parenthesised arithmetic, drop casts like (u8)
                    if re.match(r"^\(\s*(u8|u16|u32|s8|s16|s32|void|const)\b", expr):
                        continue
                try:
                    env[key_] = _evaluate(ast.parse(expr, mode="eval"), env)
                except Exception:
                    leftover.append((key_, raw))
            if not leftover or len(leftover) == len(pending):
                break
            pending = leftover
    _cache[key] = env
    return env


class Constants:
    """Lazily-parsed constant namespaces, each keyed by header."""

    #: Headers that need names from another header to evaluate.
    _CONTEXT = {"flags.h": ("global.h",), "vars.h": ("global.h",)}

    def __init__(self):
        self._ns: dict[str, dict] = {}

    def ns(self, header) -> dict:
        if header not in self._ns:
            self._ns[header] = parse_defines(*self._CONTEXT.get(header, ()), header)
        return self._ns[header]

    def get(self, header, name):
        try:
            return self.ns(header)[name]
        except KeyError:
            raise KeyError(f"{name} is not defined in constants/{header}") from None

    def inverse(self, header, prefix) -> dict[int, str]:
        """value -> shortest NAME with `prefix`. For turning a species id back
        into ``SPECIES_MUDKIP`` in logs."""
        out: dict[int, str] = {}
        for name, value in self.ns(header).items():
            if not name.startswith(prefix) or not isinstance(value, int):
                continue
            if value not in out or len(name) < len(out[value]):
                out[value] = name
        return out

    # Convenience namespaces used all over the harness.
    @property
    def species(self):
        return self.ns("species.h")

    @property
    def moves(self):
        return self.ns("moves.h")

    @property
    def items(self):
        return self.ns("items.h")

    @property
    def flags(self):
        return self.ns("flags.h")

    @property
    def vars(self):
        return self.ns("vars.h")

    @property
    def battle(self):
        return self.ns("battle.h")

    @property
    def behaviors(self):
        return self.ns("metatile_behaviors.h")
