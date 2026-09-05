"""tallow: level-floor training in a route's grass, heal rail included.

    .venv/bin/python scripts/tallow_train.py saves/tallow.state ROUTE_31 9 '{"FLOUR": 9}' [max_battles]
"""
import json, logging, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("tallow")
from scripts.tallow_lib import boot, travel, save_clean

state, route, target = sys.argv[1], sys.argv[2], int(sys.argv[3])
targets = json.loads(sys.argv[4]) if len(sys.argv) > 4 else None
max_battles = int(sys.argv[5]) if len(sys.argv) > 5 else 80

d = boot(state)
assert travel(d, route), d.map_name()
lo = d.train(target, max_battles=max_battles, targets=targets)
log.info("train -> min L%d party=%s moves=%s", lo,
         [(m["nick"], m["level"], m["hp"], m["max_hp"]) for m in d.observe()["party"]],
         d.move_changes)
save_clean(d)
