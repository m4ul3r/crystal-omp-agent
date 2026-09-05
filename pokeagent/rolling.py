"""Hierarchical rolling memory: the log-compaction tree from
ai-plays-pokemon, rebuilt on stdlib sqlite3.

A run is thousands of decisions long and a context window is not.  Raw
entries accumulate; once there are more than ``soft_limit + leaf_size`` of
them the oldest ``leaf_size`` fold into one level-1 summary, and contiguous
same-level summaries merge pairwise upward.  Recent history therefore reads
at full fidelity while old context stays reachable in O(log n) blocks.

Two things are said honestly here that the Crystal original did not say:

* Merging is lossy.  A merged block is re-summarized (or, if no summarizer
  is available, joined and capped at ``SUMMARY_MAX_CHARS``), so deep history
  is a lossy compression of itself.  The original's docstring claimed
  "nothing is ever lost" while ``_merge_level`` truncated blindly.
* A failing summarizer is not silent.  It leaves the data raw -- play must
  never stop because a model call timed out -- but it records why in
  ``last_fold_reason`` and logs it, in line with the project rule that an
  unexplained falsy return is the worst defect class we ship.

Persistence is per-entry: ``add`` commits immediately, because the process
this lives inside is expected to be killed.
"""

import logging
import sqlite3
from pathlib import Path

log = logging.getLogger("pokeagent.rolling")

#: Raw entries folded into one level-1 summary.
LEAF_SIZE = 20
#: Raw entries kept verbatim before folding starts.
RAW_SOFT_LIMIT = 100
#: Cap on a single summary block.
SUMMARY_MAX_CHARS = 3000


def _default_summarize(blocks) -> str:
    """Fallback when no model summarizes: keep the most recent text.

    ``blocks`` is ``[(start, end, content), ...]`` oldest first, so slicing
    from the end keeps the newest characters -- the ones a decider is most
    likely to need.
    """
    return "\n".join(c for _, _, c in blocks)[-SUMMARY_MAX_CHARS:]


class RollingMemory:
    """Append-only log with a summary tree over it, on one sqlite file."""

    def __init__(self, db_path, summarize_fn=None, leaf_size=LEAF_SIZE,
                 soft_limit=RAW_SOFT_LIMIT):
        if leaf_size < 1:
            raise ValueError(f"leaf_size must be >= 1, got {leaf_size}")
        if soft_limit < 0:
            raise ValueError(f"soft_limit must be >= 0, got {soft_limit}")
        self.db_path = Path(db_path)
        self.summarize = summarize_fn or _default_summarize
        self.leaf_size = leaf_size
        self.soft_limit = soft_limit
        #: None when the last fold succeeded or none was due.
        self.last_fold_reason: str | None = None
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.db_path))
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS raw ("
            "iter INTEGER PRIMARY KEY, content TEXT NOT NULL)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS summ ("
            "start INTEGER NOT NULL, end INTEGER NOT NULL, "
            "level INTEGER NOT NULL, content TEXT NOT NULL, "
            "PRIMARY KEY (start, end, level))"
        )
        self.db.commit()
        # Numbering must continue past everything already SUMMARIZED, not
        # just past the surviving raws: folding deletes raw rows, so a
        # reopened database that only consulted `raw` would restart inside
        # an existing summary's range and corrupt the ordering.
        self.next_iter = self.db.execute(
            "SELECT MAX(hi) + 1 FROM ("
            "  SELECT COALESCE(MAX(iter), 0) AS hi FROM raw"
            "  UNION ALL SELECT COALESCE(MAX(end), 0) FROM summ)"
        ).fetchone()[0]

    # -- writing ------------------------------------------------------------

    def add(self, text: str) -> int:
        """Persist one entry immediately; a crash must not lose it."""
        n = self.next_iter
        self.db.execute("INSERT INTO raw (iter, content) VALUES (?, ?)", (n, str(text)))
        self.db.commit()
        self.next_iter = n + 1
        return n

    # -- reading ------------------------------------------------------------

    def tail(self, n: int = 10) -> list[tuple[int, str]]:
        """The ``n`` most recent raw entries, oldest first."""
        return list(
            self.db.execute(
                "SELECT iter, content FROM raw ORDER BY iter DESC LIMIT ?", (n,)
            )
        )[::-1]

    def frontier(self) -> list[tuple[int, int, int, str]]:
        """Summaries not covered by a wider summary, oldest first.

        Pairwise merging already leaves the survivors disjoint; the anti-join
        makes that an asserted invariant rather than a trusted one.
        """
        return list(
            self.db.execute(
                "SELECT start, end, level, content FROM summ s "
                "WHERE NOT EXISTS ("
                "  SELECT 1 FROM summ t WHERE t.level > s.level "
                "  AND t.start <= s.start AND t.end >= s.end) "
                "ORDER BY s.start"
            )
        )

    def render(self, tail: int = 10) -> str:
        """Decider-readable view: summary blocks, then the recent raws."""
        lines = [f"[{s}-{e}]L{lv}: {c}" for s, e, lv, c in self.frontier()]
        lines += [f"[{i}]: {c}" for i, c in self.tail(tail)]
        return "\n".join(lines)

    # -- compaction ---------------------------------------------------------

    def finalize_iteration(self) -> int:
        """Fold while over the soft limit. Returns how many folds ran.

        A summarizer failure stops compaction and leaves every raw entry in
        place: the next call retries.  Nothing is dropped on the failure
        path, which is the whole point of folding lazily.
        """
        self.last_fold_reason = None
        folds = 0
        while True:
            n = self.db.execute("SELECT COUNT(*) FROM raw").fetchone()[0]
            if n < self.soft_limit + self.leaf_size:
                return folds
            rows = self.db.execute(
                "SELECT iter, content FROM raw ORDER BY iter LIMIT ?", (self.leaf_size,)
            ).fetchall()
            iters = [i for i, _ in rows]
            try:
                content = self._summarize([(i, i, c) for i, c in rows])
            except Exception as exc:
                self.last_fold_reason = (
                    f"summarizer failed on raws {min(iters)}-{max(iters)}: "
                    f"{type(exc).__name__}: {exc}; {n} entries left raw"
                )
                log.warning("%s", self.last_fold_reason)
                return folds
            with self.db:  # fold and delete are one transaction or neither
                self.db.execute(
                    "INSERT OR REPLACE INTO summ VALUES (?, ?, 1, ?)",
                    (min(iters), max(iters), content),
                )
                self.db.execute(
                    "DELETE FROM raw WHERE iter IN (%s)" % ",".join("?" * len(iters)),
                    iters,
                )
            folds += 1
            self._merge_level(1)

    def _summarize(self, blocks) -> str:
        """Run the summarizer and hold it to the block-size contract."""
        out = self.summarize(blocks)
        if not isinstance(out, str):
            raise TypeError(f"summarize_fn returned {type(out).__name__}, expected str")
        return out[:SUMMARY_MAX_CHARS]

    def _merge_level(self, level: int) -> None:
        """Merge contiguous same-level pairs upward until none remain.

        The merged text is re-summarized rather than concatenated-and-cut, so
        a level-3 block is a summary of summaries instead of the first 3000
        characters of two blocks glued together.  A failure here leaves both
        halves in place at their own level: still readable, just not merged.
        """
        while True:
            rows = self.db.execute(
                "SELECT start, end, content FROM summ WHERE level = ? ORDER BY start",
                (level,),
            ).fetchall()
            pair = next(
                ((a, b) for a, b in zip(rows, rows[1:]) if a[1] + 1 == b[0]), None
            )
            if pair is None:
                return
            a, b = pair
            try:
                merged = self._summarize([(a[0], a[1], a[2]), (b[0], b[1], b[2])])
            except Exception as exc:
                self.last_fold_reason = (
                    f"merge of L{level} blocks {a[0]}-{a[1]} and {b[0]}-{b[1]} "
                    f"failed: {type(exc).__name__}: {exc}; both left unmerged"
                )
                log.warning("%s", self.last_fold_reason)
                return
            with self.db:
                self.db.execute(
                    "DELETE FROM summ WHERE level=? AND start=? AND end=?",
                    (level, a[0], a[1]),
                )
                self.db.execute(
                    "DELETE FROM summ WHERE level=? AND start=? AND end=?",
                    (level, b[0], b[1]),
                )
                self.db.execute(
                    "INSERT OR REPLACE INTO summ VALUES (?, ?, ?, ?)",
                    (a[0], b[1], level + 1, merged),
                )

    def close(self) -> None:
        self.db.close()
