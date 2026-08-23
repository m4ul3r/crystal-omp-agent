"""Probe-driven navigation for maps whose static collision grid is wrong.
Trusts only observed movement; fights anything that intercepts us."""
import sys, time
sys.path.insert(0, '/media/ssd/pokecrystal/crystal-agent')
from collections import deque
from trek import Driver

DIRS = {'U': (0, -1), 'D': (0, 1), 'L': (-1, 0), 'R': (1, 0)}
INV = {v: k for k, v in DIRS.items()}


class Probe:
    def __init__(self, d):
        self.d = d
        self.edges = {}   # cell -> {dir: neighbor_or_None_or_'WARP'}

    def pos(self):
        return self.d.pos()[2:]

    def try_move(self, mv):
        """One probe attempt, fighting through interceptions."""
        for _ in range(8):
            r = self.d.step_dir(mv)
            if r == 'battle':
                self.d.fight()
                continue
            if r == 'blocked' and self.d.textbox():
                self.d.flush_dialog()
                continue
            return r
        return 'blocked'

    def nav_to(self, target):
        """BFS over known edges from current position, then re-walk it."""
        here = self.pos()
        prev = {here: None}
        q = deque([here])
        while q:
            c = q.popleft()
            if c == target:
                seq = []
                while prev[c]:
                    p, mv = prev[c]
                    seq.append((p, mv))
                    c = p
                for pcell, mv in reversed(seq):
                    if self.pos() != pcell:
                        self.nav_to(pcell)
                    r = self.try_move(mv)
                    if r != 'moved' and self.pos() != target:
                        return False
                return True
            for mv, nxt in self.edges.get(c, {}).items():
                if isinstance(nxt, tuple) and nxt not in prev:
                    prev[nxt] = (c, mv)
                    q.append(nxt)
        raise RuntimeError(f"no known route {here} -> {target}")

    def explore(self, goal, max_probes=500):
        """Flood-probe until an edge into `goal` warps or reaches it."""
        start = self.pos()
        seen = {start}
        q = deque([start])
        probes = 0
        skipped = 0
        while q and probes < max_probes:
            cur = q.popleft()
            if cur != self.pos():
                try:
                    self.nav_to(cur)
                except RuntimeError:
                    skipped += 1
                    continue
            x, y = cur
            for mv, (dx, dy) in DIRS.items():
                nxt = (x + dx, y + dy)
                if mv in self.edges.get(cur, {}) or \
                        not (1 <= nxt[0] <= 18 and 1 <= nxt[1] <= 14):
                    continue
                if self.d.textbox():
                    self.d.flush_dialog()
                probes += 1
                r = self.try_move(mv)
                inv = INV[(dx, dy)]
                if r == 'moved':
                    e = self.edges.setdefault(cur, {})
                    e[mv] = nxt
                    self.edges.setdefault(nxt, {}).setdefault(inv, cur)
                    if nxt == goal:
                        return 'goal', self.d.map_name(), self.pos()
                    if nxt not in seen:
                        seen.add(nxt)
                        q.append(nxt)
                else:
                    self.edges.setdefault(cur, {})[mv] = \
                        'WARP' if r == 'warp' else None
                    if r == 'warp':
                        return 'warp', self.d.map_name(), self.pos()
                    if self.d.textbox():
                        self.d.flush_dialog()
        print(f'  [probes={probes} skipped={skipped}]', flush=True)
        return 'exhausted', self.d.map_name(), self.pos()


def main():
    d = Driver(sys.argv[1] if len(sys.argv) > 1 else None)
    t0 = time.time()
    print('[start]', d.status(), flush=True)
    p = Probe(d)

    stage = sys.argv[2] if len(sys.argv) > 2 else '2f'
    if stage == '2f':
        for attempt in range(3):
            out = p.explore((10, 14))
            print(f'[{time.time()-t0:.1f}s] 2F explore#{attempt} ->', out,
                  flush=True)
            if out[0] in ('warp', 'goal'):
                break
            for cell, e in p.edges.items():        # forget blocked guesses
                p.edges[cell] = {mv: n for mv, n in e.items()
                                 if n is not None}
    elif stage == '3f':
        out = p.explore((10, 3))
        print(f'[{time.time()-t0:.1f}s] 3F explore ->', out, flush=True)
        # Elder Li stands at (10,2) facing down: talk to him
        d.step_dir('U')
        d.press('.:20')
        d.press('A:6 .:30')
        d.flush_dialog(6000)
        f0 = d.emu.frame
        while d.emu.frame - f0 < 2500 and not d.battle():
            d.press('.:10')
        if d.battle():
            lead = d.fight(max_frames=120000)
            print(f'[{time.time()-t0:.1f}s] ELDER FOUGHT', flush=True)
        d.flush_dialog(8000)
    d.save()
    print('[end]', d.status())


if __name__ == '__main__':
    main()
