"""Rolling memory: compaction tree, frontier, and failure tolerance."""
import pytest

from crystalagent.rolling import (
    LEAF_SIZE, RAW_SOFT_LIMIT, RollingMemory, _default_summarize,
)

pytestmark = pytest.mark.unit


@pytest.fixture()
def mem(tmp_path):
    return RollingMemory(tmp_path / "mem.db")


def test_add_and_tail_order(mem):
    for i in range(5):
        mem.add(f"e{i}")
    assert mem.tail(3) == [(3, "e2"), (4, "e3"), (5, "e4")]
    assert mem.frontier() == []


def test_no_compaction_below_soft_limit(mem):
    for i in range(RAW_SOFT_LIMIT):
        mem.add(f"e{i}")
    assert mem.finalize_iteration() == 0
    assert mem.tail(1)[0][0] == RAW_SOFT_LIMIT


def test_leaf_folds_oldest_with_fake_summarizer(mem):
    calls = []

    def summarize(blocks):
        calls.append([b[0] for b in blocks])
        return f"summary of {blocks[0][0]}..{blocks[-1][0]}"

    m = RollingMemory(mem.db_path.parent / "m2.db", summarize_fn=summarize)
    n = RAW_SOFT_LIMIT + LEAF_SIZE          # exactly one fold
    for i in range(n):
        m.add(f"e{i}")
    assert m.finalize_iteration() == 1
    # the oldest LEAF_SIZE raws are gone from raw and live in a summary
    iters = [i for i, _ in m.tail(1000)]
    assert min(iters) == LEAF_SIZE + 1
    frontier = m.frontier()
    assert len(frontier) == 1
    start, end, level, content = frontier[0]
    assert (start, end, level) == (1, LEAF_SIZE, 1)
    assert content == f"summary of 1..{LEAF_SIZE}"


def test_pairwise_merge_builds_level_two(mem):
    def summarize(blocks):
        return "+"

    m = RollingMemory(mem.db_path.parent / "m3.db", summarize_fn=summarize)
    for i in range(RAW_SOFT_LIMIT + 2 * LEAF_SIZE):   # two leaves
        m.add("x")
    m.finalize_iteration()
    # both level-1 blocks are contiguous -> merged into one level-2 block
    frontier = m.frontier()
    assert len(frontier) == 1
    start, end, level, _ = frontier[0]
    assert level == 2
    assert (start, end) == (1, 2 * LEAF_SIZE)


def test_summarizer_failure_keeps_play_going(mem):
    def boom(blocks):
        raise RuntimeError("no model available")

    m = RollingMemory(mem.db_path.parent / "m4.db", summarize_fn=boom)
    for i in range(RAW_SOFT_LIMIT + LEAF_SIZE + 5):
        m.add(f"e{i}")
    assert m.finalize_iteration() == 0       # folded nothing, raised nothing
    assert len(m.tail(1)) == 1               # still readable


def test_render_sections(mem):
    mem.add("recent")
    text = mem.render(tail=5)
    assert "[1]:" in text or "[recent]" not in text
    assert "recent" in text


def test_default_summarizer_truncates_head():
    blocks = [(i, i, "x" * 50) for i in range(1, 21)]
    out = _default_summarize(blocks)
    assert len(out) <= 3000
