"""Meta-test: every test function carries a marker matching its parent
directory (unit/ or integration/), and those markers are declared in
pyproject.toml. Pattern adopted from ai-plays-pokemon's
tests/unit/test_tests.py."""
import ast
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent

pytestmark = pytest.mark.unit


def _marker_names(node):
    """pytest.mark.X decorators directly on the function -> {'X', ...}."""
    got = set()
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if (isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Attribute)
                and target.value.attr == "mark"
                and isinstance(target.value.value, ast.Name)
                and target.value.value.id == "pytest"):
            got.add(target.attr)
    return got


def _module_markers(tree):
    """Module-level `pytestmark = pytest.mark.unit` -> {'unit'}."""
    for node in tree.body:
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "pytestmark"
                        for t in node.targets)):
            v = node.value
            elts = [v] if isinstance(v, ast.Attribute) else (
                v.elts if isinstance(v, (ast.Tuple, ast.List)) else [])
            out = set()
            for e in elts:
                parts = []
                cur = e
                while isinstance(cur, ast.Attribute):
                    parts.append(cur.attr)
                    cur = cur.value
                if isinstance(cur, ast.Name) and cur.id == "pytest" \
                        and len(parts) >= 2 and parts[-1] == "mark":
                    out.add(parts[-2])
            return out
    return set()


def test_registered_markers_include_ours():
    import tomllib
    cfg = tomllib.loads((TESTS.parent / "pyproject.toml")
                        .read_text(encoding="utf-8"))
    declared = {m.split(":", 1)[0].strip()
                for m in cfg["tool"]["pytest"]["ini_options"]["markers"]}
    assert {"unit", "integration"} <= declared


def test_every_test_has_directory_marker():
    problems = []
    for sub, want in (("unit", "unit"), ("integration", "integration")):
        d = TESTS / sub
        if not d.is_dir():
            continue
        for f in sorted(d.rglob("test_*.py")):
            tree = ast.parse(f.read_text(encoding="utf-8"))
            module_marks = _module_markers(tree)
            for node in ast.walk(tree):
                if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and node.name.startswith("test")):
                    marks = _marker_names(node) | module_marks
                    if want not in marks:
                        problems.append(
                            f"{f.relative_to(TESTS)}:{node.lineno} "
                            f"{node.name}: markers={sorted(marks)} "
                            f"missing '{want}'")
    assert not problems, "\n".join(problems)


@pytest.mark.unit
def test_meta_test_itself_is_marked():
    assert True
