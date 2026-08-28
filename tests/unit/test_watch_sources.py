"""Live-feed consumer surface in watch.py -- LiveSource and Sources read
live/<name>.json/.jsonl off disk with no emulator/ROM involved. Only
Sources.get() on a *save* key ever needs a Viewer, and that is lazy.
"""
import json
import time

import pytest

import watch
from crystalagent import paths

pytestmark = pytest.mark.unit


class FakeViewer:
    """Stands in for watch.Viewer so a bare/save key never loads the ROM."""
    def __init__(self):
        self.built = True


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


@pytest.fixture(autouse=True)
def live_dir(tmp_path, monkeypatch):
    live = tmp_path / "live"
    saves = tmp_path / "saves"
    live.mkdir()
    saves.mkdir()
    monkeypatch.setattr(paths, "LIVE_DIR", live)
    monkeypatch.setattr(paths, "SAVES_DIR", saves)
    return live


# -- LiveSource.snapshot() ---------------------------------------------------

def test_snapshot_injects_sprite_urls_and_live_metadata(live_dir):
    _write_json(live_dir / "x.json", {
        "name": "x", "fps": 12.0, "speed": 2.0,
        "party": [
            {"species": "PIKACHU", "shiny": False, "form": None,
             "egg": False},
            {"species": "TOGEPI", "shiny": True, "form": None, "egg": True},
        ],
        "battle": {"enemy": {"species": "GEODUDE", "shiny": True,
                              "form": None}},
    })

    src = watch.LiveSource("x")
    snap = src.snapshot()

    assert snap["save"] == "live:x"
    assert snap["live"] == {"fps": 12.0, "speed": 2.0}
    assert isinstance(snap["state_age_ms"], float)
    assert snap["state_age_ms"] >= 0.0

    pikachu, togepi = snap["party"]
    assert pikachu["sprite"] == "/sprite/PIKACHU.png"
    # egg overrides species with "egg", and shiny is carried through
    assert togepi["sprite"] == "/sprite/egg.png?shiny=1"

    assert snap["battle"]["enemy"]["sprite"] == "/sprite/GEODUDE.png?shiny=1"


def test_snapshot_reports_error_without_exception_when_feed_absent(live_dir):
    src = watch.LiveSource("missing")
    snap = src.snapshot()

    assert "error" in snap
    assert snap["save"] == "live:missing"


def test_png_raises_value_error_without_a_frame(live_dir):
    src = watch.LiveSource("missing")
    with pytest.raises(ValueError):
        src.png()


def test_pump_is_empty_without_a_feed(live_dir):
    src = watch.LiveSource("missing")
    assert src.pump() == []


# -- LiveSource.pump() narration ---------------------------------------------

def test_pump_accumulates_new_rows_monotonically(live_dir):
    log = live_dir / "x.jsonl"
    _write_jsonl(log, [
        {"i": 0, "msg": "first"},
        {"i": 1, "msg": "second"},
    ])

    src = watch.LiveSource("x")
    first = src.pump()
    assert [r["msg"] for r in first] == ["first", "second"]

    _write_jsonl(log, [{"i": 2, "msg": "third"}])
    second = src.pump()

    # monotonic: everything seen before is still there, plus the new row,
    # with nothing duplicated.
    assert [r["msg"] for r in second] == ["first", "second", "third"]

    # a third call with nothing new appended must not repeat/duplicate rows
    third = src.pump()
    assert [r["msg"] for r in third] == ["first", "second", "third"]


# -- Sources ------------------------------------------------------------------

def test_get_live_key_returns_cached_live_source_without_building_viewer(
        live_dir):
    sources = watch.Sources("save:default.state")
    a = sources.get("live:x")
    b = sources.get("live:x")

    assert isinstance(a, watch.LiveSource)
    assert a is b                     # cached
    assert sources._viewer is None    # no ROM ever loaded for a live source


def test_get_bare_name_resolves_to_save_key(live_dir, monkeypatch):
    monkeypatch.setattr(watch, "Viewer", FakeViewer)
    sources = watch.Sources("save:default.state")

    src = sources.get("foo.state")

    assert isinstance(src, watch.SaveSource)
    assert src.key == "save:foo.state"
    assert sources.get("foo.state") is src   # cached


def test_listing_lists_live_feeds_before_saves(live_dir, monkeypatch):
    now = time.time()
    _write_json(live_dir / "x.json", {"name": "x"})
    (paths.SAVES_DIR / "a.state").write_text("state", encoding="utf-8")

    sources = watch.Sources("save:default.state")
    rows = sources.listing()

    assert rows[0] == {"key": "live:x", "label": "x", "kind": "live",
                        "age_s": pytest.approx(0, abs=2)}
    assert rows[1]["key"] == "save:a.state"
    assert rows[1]["kind"] == "save"
    assert set(rows[1]) == {"key", "label", "kind", "age_s"}
