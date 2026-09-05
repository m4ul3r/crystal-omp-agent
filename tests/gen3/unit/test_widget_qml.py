"""The widget's QML has to at least PARSE.

There is no JS runtime on this machine, so `Model.js` has no unit lane and the
STAGES section was written blind against the feed's JSON -- the compositor is
its only real test, which is what issue #20 is for.

`qmllint` closes part of that gap without a display: it will not tell us the
layout looks right, but it will catch a syntax error, a stray brace or a
property assigned outside an object, which is exactly the class of mistake
"written blind" invites. Skipped when qmllint is absent rather than failing,
because it is a Qt tool and not a dependency of the harness.

Unresolvable imports are EXPECTED and ignored: `qs.Commons` and `qs.Ui` come
from omarchy-shell, and the panel's own inline components cannot be resolved
once those imports fail. Only genuine parse errors are fatal here.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

WIDGET = Path(__file__).resolve().parents[3] / "widget" / "poke.run"

QMLLINT = shutil.which("qmllint") or "/usr/lib/qt6/bin/qmllint"

#: Diagnostics that only mean "omarchy-shell is not on the import path".
IGNORABLE = (
    "Failed to import",
    "Warnings occurred while importing",
    "was not found. Did you add all imports",
    "Did you mean",
)


def _lint(path: Path) -> list[str]:
    if not Path(QMLLINT).exists():
        pytest.skip("qmllint is not installed")
    proc = subprocess.run(
        [QMLLINT, str(path)], capture_output=True, text=True, timeout=120
    )
    fatal = []
    for line in (proc.stdout + proc.stderr).splitlines():
        if not line.strip() or line.startswith(("Info:", "---", " ", "^")):
            continue
        if any(token in line for token in IGNORABLE):
            continue
        if line.startswith("Error:"):
            fatal.append(line)
    return fatal


@pytest.mark.parametrize("name", ["Panel.qml", "Feed.qml"])
def test_the_widget_qml_parses(name):
    path = WIDGET / name
    if not path.exists():
        pytest.skip(f"{name} is not in this checkout")
    assert _lint(path) == [], f"{name} has parse errors"


def test_the_panel_still_renders_every_section_the_feed_publishes():
    """A cheap structural check on the file itself.

    Not a substitute for looking at it -- see issue #20 -- but it does catch
    someone deleting a section while the publisher keeps sending the data,
    which would silently drop status the user asked for.
    """
    text = (WIDGET / "Panel.qml").read_text()
    for section in ("OBJECTIVE", "PROGRESS", "TEAM", "PARTY", "COUNTERS",
                    "NARRATION"):
        assert f'"{section}"' in text, f"the {section} section is gone"
    # The battle has no header: it is the matchup strip under the screen.
    assert "id: matchup" in text and "Model.enemy(feed.state)" in text, \
        "the opponent is no longer drawn"
    assert "BadgePips" in text and "Model.badgeCount(feed.state)" in text, \
        "the badge case is gone"
    # Who is driving lives on the hero's trailing edge, not in a section.
    assert "AgentCard" in text and "Model.agent(feed.state)" in text, \
        "the agent card is gone"


def test_the_stage_helpers_the_panel_calls_exist_in_the_model():
    """Panel.qml calls these by name; a rename in one file and not the other
    fails silently at runtime, in a widget nobody can unit-test."""
    panel = (WIDGET / "Panel.qml").read_text()
    model = (WIDGET / "Model.js").read_text()
    for fn in ("stages", "stageValue", "fraction"):
        assert f"Model.{fn}(" in panel, f"Panel.qml no longer calls {fn}"
        assert f"function {fn}(" in model, f"Model.js no longer defines {fn}"


def test_the_pace_section_publishes_its_basis_with_its_numbers():
    """A range from two data points is worth publishing. The same range quoted
    as though it were measured is not, so the panel must render the basis
    string beside the figures rather than instead of them."""
    model = (WIDGET / "Model.js").read_text()
    panel = (WIDGET / "Panel.qml").read_text()
    assert "function paceLines(state)" in model
    assert "function paceBasis(state)" in model
    assert "Model.paceLines(feed.state)" in panel
    assert "Model.paceBasis(feed.state)" in panel


def test_the_pace_section_says_so_when_there_is_no_evidence():
    """Two badges in, there is nothing to extrapolate from. An empty section
    reads as a bug; saying so reads as honesty."""
    model = (WIDGET / "Model.js").read_text()
    assert "not enough evidence yet" in model


def test_the_screen_box_is_not_hidden_while_a_frame_loads():
    """The panel flashed and everything below it snapped up, several times a
    second.

    The published screen URL carries a cache-busting frame number, so it
    changes on every poll and the Image drops to `Loading`. The container's
    `visible` was bound to `status === Image.Ready`, so it UNMOUNTED from the
    Column each time -- taking its height with it and letting the rest of the
    panel jump. Measured with both shapes side by side: mid-load the old box
    reports visible=false and shows nothing, the new one stays visible and
    still shows the previous frame.
    """
    panel = (WIDGET / "Panel.qml").read_text()
    assert "visible: root.showFrame" in panel, \
        "the screen box must not be gated on image status"
    assert "frameImage.status === Image.Ready" not in panel


def test_the_screen_keeps_its_shape_while_reloading():
    """`sourceSize` is zero while a reload is in flight, so reading the frame's
    shape live was the other half of the jump. It is remembered and only
    updated on a successful load."""
    panel = (WIDGET / "Panel.qml").read_text()
    assert "property int srcW: 240" in panel and "property int srcH: 160" in panel, \
        "the frame's shape must be a remembered property, not a live binding"
    assert "srcW = img.sourceSize.width" in panel, \
        "the shape is taken from a frame that has decoded"


def test_two_buffers_so_a_drawn_frame_is_always_on_screen():
    """One Image cannot do this: Qt clears it the moment its source changes.
    The hidden buffer loads the incoming frame and they swap only on Ready."""
    panel = (WIDGET / "Panel.qml").read_text()
    assert "id: imageA" in panel and "id: imageB" in panel
    assert "property bool frontIsA" in panel
    assert "function present(img)" in panel


def test_loading_is_only_claimed_before_the_first_frame():
    """"Loading..." painted over a live screen would be a lie; after the first
    frame a buffer always holds something."""
    panel = (WIDGET / "Panel.qml").read_text()
    assert "visible: !screenBox.everLoaded" in panel


def test_the_hover_text_is_sentences_not_a_debug_line():
    """The tooltip is the only thing most people will ever read.

    It used to show the status line verbatim --
    `frame=41083574 map=Route111 pos=(12,68) lead=LOTTAD L30 70/93
    money=14488 badges=3/8` -- which is a developer's string. Every one of
    those facts is in the popup; none of them is a sentence.
    """
    model = (WIDGET / "Model.js").read_text()
    assert "function prettyMap(state)" in model, \
        "map CONSTANTS like Route111 have to be split into words"
    assert '"In battle with "' in model, "a battle outranks the objective"
    assert '"Working on "' in model


def test_the_tooltip_reads_player_as_a_string():
    """`player` is a bare string in the feed, not an object. Reading `.name`
    off it silently showed the game title for every run."""
    model = (WIDGET / "Model.js").read_text()
    assert "str(state.player)" in model
    assert "state.player && state.player.name" not in model


def test_the_tooltip_does_not_say_the_badge_count_twice():
    """`objective.detail` is "3/8 badges, then the Elite Four" and the
    progress line already says "3/8 badges". `next_step` is the specific thing
    being worked on and is preferred."""
    model = (WIDGET / "Model.js").read_text()
    assert "state.objective.next_step" in model


def test_the_dex_is_counted_against_what_is_achievable():
    """Seven Ruby exclusives and seven trade evolutions are not a shortfall,
    so 6/188 is the honest fraction and 6/386 is not."""
    model = (WIDGET / "Model.js").read_text()
    assert "state.dex.achievable" in model


def test_the_feed_keeps_the_last_snapshot_when_a_read_misses():
    """The whole-widget flash, and it was never the image.

    The publisher rewrites the feed JSON four times a second with
    write-then-`os.replace`, which is atomic for a reader that opens the path
    cleanly. It is NOT free for an inotify watcher: `os.replace` swaps the
    inode, the watch sees a delete/create pair, and a read landing in that
    window fails. Setting `state = null` on that made every binding in the
    widget collapse and re-lay-out at once.

    A stale snapshot for one frame is invisible. An empty one is the whole
    panel jumping.
    """
    feed = (WIDGET / "Feed.qml").read_text()
    assert "if (next === null && root.state !== null)" in feed, \
        "an unparseable read must not blank a good snapshot"
    assert "root.state = null\n      root.retime()" not in feed, \
        "onLoadFailed must not blank the state"


def test_genuine_absence_is_still_representable():
    """`state` starts null and stays null until a first successful read, so
    "no run yet" and "one bad read" remain different things."""
    feed = (WIDGET / "Feed.qml").read_text()
    assert "property var state: null" in feed
    assert "property int missedReads" in feed


def test_no_repeater_binds_to_a_javascript_array():
    """The pop-in, measured.

    A Repeater bound to a JS array destroys and recreates EVERY delegate
    whenever the array's CONTENT changes -- and `feed.state` is a fresh object
    on each of the publisher's four writes a second, so the lead's HP ticking
    rebuilt every party row, sprite, HP bar and stat cell four times a second.
    Each rebuilt delegate is momentarily unsized, which is what pops.

    Measured with qml6 on twenty content changes of a three-element model:
    63 delegate creations over an array model, 3 over a length model.
    """
    panel = (WIDGET / "Panel.qml").read_text()
    models = re.findall(r"^\s*model: (.+)$", panel, re.MULTILINE)
    assert models, "the popup has Repeaters; the pattern must have changed"
    for m in models:
        m = m.strip()
        # A fixed slot count (the eight badge pips) is a number, not an array.
        assert m.endswith(".length") or m.endswith("_SLOTS"), (
            f"Repeater model `{m}` is a JS array: bind it to a length "
            "and index into the array, or every delegate is rebuilt per poll"
        )


def test_the_bar_label_slot_is_never_unmounted():
    """`button.fixedWidth` is derived from `slot.implicitWidth`, and an
    invisible child leaves a Row along with the spacing before it. The label is
    empty whenever `feed.running` is false, and `running` is recomputed from
    elapsed time on every tick -- so a driver that pauses six seconds to plan a
    route narrowed the module and widened it again on resume. Every module to
    its right jumped, and so did the popup, whose x comes from this anchor
    (Ui/KeyboardPanel.qml `cardOrigin`).
    """
    panel = (WIDGET / "Panel.qml").read_text()
    assert "visible: root.label !== \"\"" not in panel, (
        "toggling the bar label's `visible` resizes the whole bar module"
    )
    assert "visible: feed.present" in panel, \
        "the label's space is claimed once, when a feed is first seen"


def test_everything_above_the_framebuffer_reserves_its_space():
    """The frame's y is the sum of every height above it, so anything up there
    that resizes moves the picture.

    PanelHero grows when the BATTLE pill appears (Ui/PanelHero.qml sizes to its
    own labels) and the publisher's error line is a WRAPPING Text, one to three
    lines. Both now sit in a ReservedSlot, whose height only ever grows.
    Measured on the running shell over a synthetic feed cycling
    overworld/battle/no-percent/error three times: the frame moved twice, by
    1px and 15px, both monotone -- against 76px peak-to-peak reversing on every
    cycle before.
    """
    panel = (WIDGET / "Panel.qml").read_text()
    above = panel[:panel.index("id: screenBox")]
    assert "ReservedSlot {" in above[:above.index("PanelHero {")], \
        "the hero must be wrapped in a slot that cannot shrink"
    assert above.count("ReservedSlot {") >= 2, \
        "the hero and the error line both need reserved slots"
    assert 'opacity: feed.error !== "" ? 1 : 0' in above, \
        "the error line must fade, not unmount"
    assert 'visible: feed.error !== ""' not in panel, (
        "binding the error line's `visible` reflows a wrapping Text directly "
        "above the framebuffer every time a publish fails and recovers"
    )


def test_the_party_sprite_slot_does_not_wait_for_a_decode():
    """Sizing the sprite on `status === Image.Ready` meant the tile changed
    shape when the sprite finished decoding. The slot is a fixed-size Item
    and only the painting is gated on the status."""
    panel = (WIDGET / "Panel.qml").read_text()
    tile = panel[panel.index("component PartySlot"):panel.index("component Combatant")]
    assert "visible: status === Image.Ready" in tile
    assert "width: visible ? " not in tile and "height: visible ? " not in tile
    assert "height: Style.space(28)" in tile, "the sprite slot must have a fixed height"
