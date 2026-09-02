"""Session helpers for the RUSTY run (persona_RUSTY.md). Import into the
warm kernel:  from rusty_helpers import *; boot('rusty')"""
import os, re, shutil, sys, types, logging, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.INFO, format="%(message)s")
import trek
from crystalagent.battle import norm_item

d = None
PC = 'ECRUTEAK_POKECENTER_1F'
ACC_FLOOR = 85
BANNED = {'SELFDESTRUCT', 'EXPLOSION'}
TRAINEE = 'SPROCKET'
NICKS = {'GEODUDE': 'SPROCKET', 'MAGNEMITE': 'SPARK', 'GASTLY': 'RIVET',
         'GYARADOS': 'CHAIN', 'TOGEPI': 'LUG', 'EEVEE': 'AXLE'}
FALLBACK = ['PLIERS', 'WRENCH', 'BOLT', 'GASKET', 'PISTON', 'CLAMP', 'FLINT', 'SOLDER']
KO_SPECIES = {'SUDOWOODO'}
CATCH_EXTRA = set()          # species a session decided to catch off-plan


def nick_for(species):
    return NICKS.get(species)


# -- battle policy -----------------------------------------------------------

def _best_damage(a):
    live = [m for m in a['moves'] if m.get('pp') != 0 and m['max'] > 0]
    good = [m for m in live if m.get('never_misses')
            or m.get('effective_accuracy', m['accuracy']) >= ACC_FLOOR]
    pool = good or live
    if not pool:
        return None
    pick = max(pool, key=lambda m: (m['min'] + m['max']) / 2 *
               (100 if m.get('never_misses') else m.get('effective_accuracy', m['accuracy'])))
    return ('attack', pick['slot'])


def rusty_policy(frame):
    a = d.outlook()
    if not a:
        return None
    me, their = a['me'], a.get('their_best')
    live = [m for m in a['moves'] if m.get('pp') != 0 and m['max'] > 0]
    rec, why = d.tactics.recommend(a, frame)
    kos = [m for m in live if m['ko_certain']]
    lethal = bool(their and their['max'] >= me['hp'])
    bag = {norm_item(k): v for k, v in (frame.get('bag') or {}).items()}
    potion = next((n for n in ('FULL RESTORE', 'MAX POTION', 'HYPER POTION',
                               'SUPER POTION', 'POTION') if bag.get(norm_item(n))), None)
    if kos and a.get('faster'):
        return rec
    if lethal and potion:
        heal_amt = {'POTION': 20, 'SUPER POTION': 50, 'HYPER POTION': 200,
                    'MAX POTION': 999, 'FULL RESTORE': 999}[potion]
        if min(me['hp'] + heal_amt, me['max_hp']) > their['max']:
            print(f"  heal: {their['move']} {their['min']}-{their['max']} vs {me['hp']} HP -> {potion}")
            return ('item', potion)
    if rec[0] == 'item':
        rec = None
    if isinstance(rec, tuple) and rec[0] == 'switch':
        party = frame.get('party') or []
        tgt = party[rec[1]] if isinstance(rec[1], int) and rec[1] < len(party) else None
        if (not tgt or tgt.get('egg') or tgt.get('hp', 0) <= 0
                or tgt.get('level', 0) + 5 < frame['enemy']['level']):
            print('  [policy] refusing switch to', rec[1], tgt and tgt.get('nick'))
            rec = None
    if rec is None:
        return _best_damage(a)
    return rec


def trainee_policy(frame):
    a = d.outlook()
    me = frame.get('me') or {}
    if a and TRAINEE and me.get('nick') == TRAINEE and not frame.get('wild'):
        kos = [m for m in a['moves'] if m['ko_certain'] and m.get('pp') != 0]
        their = a.get('their_best') or {}
        safe = their and their.get('max', 0) * 3 < a['me']['hp'] and \
            frame['enemy']['level'] <= me.get('level', 0) + 4
        if not (kos and a.get('faster')) and not safe:
            best = None
            for c in frame.get('can_switch') or []:
                p = frame['party'][c] if isinstance(c, int) and c < len(frame['party']) else (c if isinstance(c, dict) else None)
                if p and p.get('hp', 0) > 0 and not p.get('egg') and p.get('nick') != TRAINEE and p.get('level', 0) > 10:
                    idx = c if isinstance(c, int) else p.get('party_slot', p.get('slot'))
                    if best is None or p['level'] > best[1]:
                        best = (idx, p['level'], p['nick'])
            if best is not None:
                print(f"  [trainee] {TRAINEE} L{me.get('level')} vs {frame['enemy']['species']} "
                      f"L{frame['enemy']['level']} ({their.get('move')} {their.get('max')} vs {a['me']['hp']}): switch -> {best[2]}")
                return ('switch', best[0])
        elif safe:
            print(f"  [trainee] {TRAINEE} L{me.get('level')} solo vs {frame['enemy']['species']} L{frame['enemy']['level']}")
    return rusty_policy(frame)


def rusty_encounter(frame):
    sp = frame['enemy']['species'] if isinstance(frame, dict) and 'enemy' in frame else None
    if sp in KO_SPECIES:
        return 'ko'
    party = d.observe()['party']
    owned = {p['species'] for p in party}
    real = sum(1 for p in party if not p['egg'])
    if (sp in NICKS or sp in CATCH_EXTRA) and sp not in owned and real < 6 and sp != 'GYARADOS':
        return 'catch'
    return 'flee'


# -- driver patches ------------------------------------------------------------

def _fight_wrapped(self, *a, **k):
    if self._pending_nickname is None:
        self._pending_nickname = nick_for
    try:
        return trek.Driver.fight(self, *a, **k)
    finally:
        if self._pending_nickname is None:
            self._pending_nickname = nick_for


def _take_wrapped(self):
    name = self._pending_nickname
    if callable(name) or isinstance(name, dict):
        name = None
        for m in self.observe()['party']:
            if m['nick'] == m['species'] and m['species'] in NICKS:
                name = NICKS[m['species']]
                break
        print('  gift/hatch nickname ->', name)
    self._pending_nickname = None
    self.dismiss_keyboard(name)
    self._pending_nickname = nick_for
    return name


def _outlook_filtered(self):
    a = trek.Driver.outlook(self)
    if a and a.get('moves'):
        a['moves'] = [m for m in a['moves'] if m['move'] not in BANNED] or a['moves']
    return a


def arm(policy=None):
    d.default_policy = policy or trainee_policy
    d.encounter_policy = rusty_encounter
    d.fight = types.MethodType(_fight_wrapped, d)
    d._take_pending_nickname = types.MethodType(_take_wrapped, d)
    d.outlook = types.MethodType(_outlook_filtered, d)
    d._pending_nickname = nick_for


def boot(name='rusty', live=True):
    """Boot the warm driver on saves/<name>.state (the working state)."""
    global d
    d = trek.Driver(f'saves/{name}.state',
                    live={'name': 'rusty', 'fps': 12, 'speed': 0} if live else None)
    arm()
    try:
        if any(m['name'] == 'SURF' for p in d.observe()['party'] for m in p['moves']):
            d.enable_surf()
    except Exception as ex:
        print('surf', ex)
    print(d.status())
    return d


def reload(name):
    """Discard the running emulator and restart from saves/<name>.state."""
    global d
    try:
        d.live_detach()
    except Exception as ex:
        print('detach', ex)
    try:
        d.emu.py.stop(save=False)
    except Exception as ex:
        print('stop', ex)
    shutil.copy(f'saves/{name}.state', 'saves/rusty.state')
    shutil.copy(f'saves/{name}.state.meta', 'saves/rusty.state.meta')
    return boot('rusty')


def fork(name):
    d.settle()
    d.save()
    shutil.copy('saves/rusty.state', f'saves/{name}.state')
    shutil.copy('saves/rusty.state.meta', f'saves/{name}.state.meta')
    print('forked', name)


# -- scene / travel helpers ----------------------------------------------------

def screen_tail():
    return [l for l in d.emu.screen_text()[12:] if l.strip('│ ') and '─' not in l]


def finish_scene(rounds=12, frames=6000):
    for i in range(rounds):
        r = d.flush_dialog(max_frames=frames)
        sm = d.emu.read_u8('wScriptMode')
        ui = d.observe()['ui']
        print(i, r, sm, ui, screen_tail()[:3])
        if r == 'menu' and d.last_choice_options:
            print(d.resolve_choice('YES'))
        elif r == 'menu':
            d.press('A:2 .:20')
        if sm == 0 and not ui['textbox']:
            return True
        d.press('.:30')
    return False


def go(dest, answer='YES', tries=4):
    for i in range(tries):
        try:
            d.travel(dest)
            r = 'ok'
        except Exception as ex:
            r = repr(ex)
        print(r[:300])
        print(d.status(), '|', d.last_goto_reason)
        if r == 'ok':
            return r
        if 'choice menu' in (d.last_goto_reason or ''):
            print('  choice:', screen_tail(), '->', answer)
            d.resolve_choice(answer)
            finish_scene()
            continue
        return r


def trainer(x, y, label=''):
    r = d.talk_to(x, y, label=label)
    print('talk_to ->', r, '|', d.last_goto_reason)
    if d.last_battle is not None:
        try:
            print(d.last_battle.summary())
        except Exception as ex:
            print('summary err', ex)
    print(d.status())
    return r


def sweep(items=True, skip=(), only=None, order='y'):
    objs = d.map_objects()
    objs = sorted(objs, key=lambda o: (o['y'], o['x'])) if order == 'y' else objs
    for o in objs:
        s = o['script']
        if o.get('masked'):
            continue
        x, y = o['x'], o['y']
        if (x, y) in skip:
            continue
        if only and (x, y) not in only:
            continue
        is_tr = s.startswith('Trainer')
        is_item = o['sprite'] == 'SPRITE_POKE_BALL' and items
        if not (is_tr or is_item):
            continue
        if any(m['x'] == x and m['y'] == y and m.get('masked') for m in d.map_objects()):
            print(f"-- {s} already masked")
            continue
        lx, ly = d._live_target(x, y)
        cells = d._approach_cells(lx, ly)
        adj = [c for c in cells if abs(c[0] - lx) + abs(c[1] - ly) == 1]
        if not adj:
            print(f"-- {s} at ({x},{y}) unreachable now ({cells})")
            continue
        print(f"== {s} at ({x},{y})")
        r = d.talk_to(x, y, s)
        print('   ->', r, d.last_goto_reason)
        if r is False and 'choice menu' in (d.last_goto_reason or ''):
            print('   choice', screen_tail())
            d.resolve_choice('YES')
            finish_scene()
            r = d.talk_to(x, y, s)
            print('   retry ->', r)
        if d.last_choice_options and d.observe()['ui']['textbox']:
            print('   post-choice', screen_tail())
            d.resolve_choice('YES')
            finish_scene()
        if d.battle():
            d.fight()
            finish_scene()
        if d._whiteout_stop('sweep'):
            print('   WHITEOUT')
            return False
        p = d.observe()['party']
        print('   party', [(m['nick'], m['level'], m['hp'], m['max_hp']) for m in p], d.observe()['bag'])
    return True


def needs_heal():
    p = [m for m in d.observe()['party'] if not m['egg']]
    lead = p[0]
    return (any(m['hp'] == 0 for m in p) or lead['hp'] < 0.5 * lead['max_hp']
            or (len(p) > 1 and p[1]['hp'] < 0.5 * p[1]['max_hp']))


def ensure_healthy(back_to=None):
    if not needs_heal():
        return False
    print(f'  [heal] party low -> {PC}')
    go(PC)
    d.heal()
    if back_to:
        go(back_to)
    return True


def sweep_sorted(skip=(), items=True, desc=True, back_to=None):
    objs = [o for o in d.map_objects()
            if (o['script'].startswith('Trainer') or (items and o['sprite'] == 'SPRITE_POKE_BALL'))
            and not o.get('masked') and (o['x'], o['y']) not in skip]
    objs.sort(key=lambda o: -o['y'] if desc else o['y'])
    mp = d.map_name()
    for o in objs:
        if d.map_name() != mp:
            go(mp)
        if any(m['x'] == o['x'] and m['y'] == o['y'] and m.get('masked') for m in d.map_objects()):
            continue
        sweep(items=items, only=[(o['x'], o['y'])], order=None)
        ensure_healthy(back_to=mp)
    return True


def show(x0, y0, x1, y1):
    t = d.tiles_in(x0, y0, x1, y1)
    g = {'floor': '.', 'grass': '%', 'blocked': '#', 'wall': '#', 'ledge': '^',
         'npc': 'N', 'warp': 'O', 'water': '~', 'cut-tree': 'T'}
    for y in range(y0, y1 + 1):
        print(f"{y:2d}", ''.join(g.get(t.get((x, y)), '?') for x in range(x0, x1 + 1)))


def buy(x, y, item, qty):
    """mart_buy with the quantity-picker presses lengthened (U:4 is swallowed)."""
    real = d.press

    def patched(seq, *a, **k):
        return real(re.sub(r'\b([UDLR]):4\b', r'\1:8', seq), *a, **k)
    d.press = patched
    try:
        return d.mart_buy(x, y, item, qty)
    finally:
        d.press = real


def party():
    return [(m['nick'], m['level'], m['hp'], m['max_hp']) for m in d.observe()['party']]


def journal(text):
    p = open('PROGRESS.md').read().split('\n', 3)
    open('PROGRESS.md', 'w').write('\n'.join(p[:3]) + text + '\n' + p[3])
