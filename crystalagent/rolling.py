"""Hierarchical rolling memory: the log-compaction tree from
ai-plays-pokemon, rebuilt on stdlib sqlite3.

Raw entries accumulate; every RAW_SOFT_LIMIT + LEAF_SIZE entries the oldest
LEAF_SIZE are summarized into a level-1 block, and contiguous same-level
blocks merge pairwise into higher levels -- so recent history reads at O(1)
per entry and old context stays reachable in O(log n) blocks. Nothing is
ever lost: compacted content lives inside its summary.

A failing summarize_fn must never interrupt play -- the block simply stays
raw and compaction retries on a later finalize_iteration()."""

import sqlite3
from pathlib import Path

LEAF_SIZE = 20                 # raws folded into one summary
RAW_SOFT_LIMIT = 100           # compaction threshold before folding
SUMMARY_MAX_CHARS = 3000       # cap per summary block


def _default_summarize(blocks):
    """Fallback when no model summarizes: keep the most recent text."""
    return "\n".join(c for _, _, c in blocks)[-SUMMARY_MAX_CHARS:]


class RollingMemory:
    def __init__(self, db_path, summarize_fn=None):
        self.db_path = Path(db_path)
        self.summarize = summarize_fn or _default_summarize
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.db_path))
        self.db.execute("CREATE TABLE IF NOT EXISTS raw ("
                        "iter INTEGER PRIMARY KEY, content TEXT NOT NULL)")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS summ ("
            "start INTEGER NOT NULL, end INTEGER NOT NULL, "
            "level INTEGER NOT NULL, content TEXT NOT NULL, "
            "PRIMARY KEY (start, end, level))")
        self.db.commit()
        self.next_iter = self.db.execute(
            "SELECT COALESCE(MAX(iter), 0) + 1 FROM raw").fetchone()[0]

    def add(self, text):
        """Persist one entry immediately (a crash must not lose it)."""
        self.db.execute("INSERT INTO raw (iter, content) VALUES (?, ?)",
                        (self.next_iter, text))
        self.next_iter += 1
        self.db.commit()

    def tail(self, n=10):
        """The n most recent raw entries as [(iter, content), ...]."""
        return list(self.db.execute(
            "SELECT iter, content FROM raw ORDER BY iter DESC LIMIT ?",
            (n,)))[::-1]

    def frontier(self):
        """Summaries not covered by any wider summary, oldest first.

        With pairwise merging surviving blocks are already disjoint; the
        anti-join keeps that invariant explicit instead of trusted."""
        return list(self.db.execute(
            "SELECT start, end, level, content FROM summ s "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM summ t WHERE t.level > s.level "
            "  AND t.start <= s.start AND t.end >= s.end) "
            "ORDER BY s.start"))

    def finalize_iteration(self):
        """Fold the oldest LEAF_SIZE raws into summaries while over the
        soft limit. Returns how many folds ran."""
        folds = 0
        while True:
            n = self.db.execute("SELECT COUNT(*) FROM raw").fetchone()[0]
            if n < RAW_SOFT_LIMIT + LEAF_SIZE:
                return folds
            rows = self.db.execute(
                "SELECT iter, content FROM raw ORDER BY iter LIMIT ?",
                (LEAF_SIZE,)).fetchall()
            iters = [i for i, _ in rows]
            try:
                content = self.summarize(
                    [(i, i, c) for i, c in rows])[:SUMMARY_MAX_CHARS]
                with self.db:
                    self.db.execute(
                        "INSERT OR REPLACE INTO summ VALUES (?, ?, 1, ?)",
                        (min(iters), max(iters), content))
                    self.db.execute(
                        "DELETE FROM raw WHERE iter IN (%s)" %
                        ",".join("?" * len(iters)), iters)
            except Exception:
                return folds          # stay uncompacted; play continues
            folds += 1
            self._merge_level(1)

    def _merge_level(self, level):
        """Merge contiguous same-level pairs upward until none remain."""
        while True:
            rows = self.db.execute(
                "SELECT start, end, content FROM summ WHERE level = ? "
                "ORDER BY start", (level,)).fetchall()
            pair = next(((a, b) for a, b in zip(rows, rows[1:])
                         if a[1] + 1 == b[0]), None)
            if pair is None:
                return
            a, b = pair
            merged = (a[2] + "\n" + b[2])[:SUMMARY_MAX_CHARS]
            with self.db:
                self.db.execute(
                    "DELETE FROM summ WHERE level=? AND start=? AND end=?",
                    (level, a[0], a[1]))
                self.db.execute(
                    "DELETE FROM summ WHERE level=? AND start=? AND end=?",
                    (level, b[0], b[1]))
                self.db.execute(
                    "INSERT INTO summ VALUES (?, ?, ?, ?)",
                    (a[0], b[1], level + 1, merged))

    def render(self, tail=10):
        """Human/decider-readable view: summary blocks, then recent raws."""
        lines = [f"[{s}-{e}]L{l}: {c}" for s, e, l, c in self.frontier()]
        lines += [f"[{i}]: {c}" for i, c in self.tail(tail)]
        return "\n".join(lines)
