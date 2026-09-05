import QtQuick
import Quickshell.Io
import "Model.js" as Model

// Read-only view of one pokeagent live feed. The driving emulator is the
// only writer; this never touches the live directory, so a viewer crash, a
// second bar on another monitor, or ten of these at once cannot perturb a run.
//
// Two reads, deliberately unequal:
//
// * The snapshot JSON is small (a few hundred bytes) and drives the bar, so it
//   is polled whenever the widget is alive.
// * The narration log and the framebuffer PNG are only read while the panel is
//   open. The log grows to thousands of lines and the PNG is ~8 KB per frame;
//   neither belongs in a poll that runs all day behind a closed popup.
//
// FileView cannot watch a file that does not exist yet (the same limitation the
// bar works around for its own toggle flags), and "no feed yet" is the normal
// startup state here -- the widget is installed long before any run. So the
// timer is the primary clock and `watchChanges` is the low-latency bonus.
Item {
  id: root
  visible: false

  property string dir: ""
  property string feedName: "default"
  property int staleAfterSec: 6
  property int pollMs: 2000
  // Raised by the panel while it is open: narration and frames are only worth
  // reading when something is on screen to show them.
  property bool detailed: false
  property int detailPollMs: 400
  property int noteLines: 5

  readonly property bool configured: String(dir) !== ""
  readonly property string statePath: configured ? dir + "/" + feedName + ".json" : ""
  readonly property string notesPath: configured ? dir + "/" + feedName + ".jsonl" : ""
  readonly property string screenPath: configured ? dir + "/" + feedName + ".png" : ""

  property var state: null
  //: Consecutive reads that came back missing or unparseable. Exposed so the
  //: panel can say something if it ever becomes persistent rather than the
  //: one-frame blip a file swap produces.
  property int missedReads: 0
  property var notes: []

  // Recomputed on every tick, not on a binding to Date.now(): staleness is a
  // function of elapsed real time, and QML has nothing that changes when time
  // merely passes.
  property real age: Infinity
  property bool running: false

  readonly property bool present: state !== null
  readonly property string error: state && state.error ? String(state.error) : ""
  readonly property bool inBattle: !!(state && state.in_battle)
  readonly property int frame: state && state.frame ? state.frame : 0

  // Cache-busting suffix for the frame image. QUrl drops the query when it
  // resolves a file:// URL to a local path, so this reloads the same file
  // without an Image cache hit; the frame counter changes exactly when the
  // publisher has something new to show.
  readonly property string screenUrl: detailed && screenPath !== "" && present
    ? "file://" + screenPath + "?f=" + frame
    : ""

  signal noteAppended(var note)

  function refresh() {
    stateFile.reload()
    if (detailed) notesFile.reload()
    retime()
  }

  function retime() {
    root.age = Model.ageSeconds(root.state, Date.now())
    root.running = Model.isRunning(root.state, Date.now(), root.staleAfterSec,
                                   root.running)
  }

  function applyState(text) {
    var next = Model.parseState(text)
    // KEEP the last good snapshot when a read comes back unparseable. The
    // publisher rewrites this file four times a second, and a reader that
    // lands mid-replace gets nothing -- setting `state` to null on that made
    // EVERY binding in the widget collapse and re-lay-out at once, which is
    // the flash. A stale snapshot for one frame is invisible; an empty one is
    // the whole panel jumping.
    //
    // Genuine absence is still representable: `state` starts null and stays
    // null until a first successful read, and staleness is reported from the
    // snapshot's own timestamp rather than by blanking it.
    if (next === null && root.state !== null) {
      root.missedReads++
      retime()
      return
    }
    root.missedReads = 0
    // Reassign rather than mutate: bindings on `state` only re-evaluate when
    // the property itself changes.
    root.state = next
    retime()
  }

  function applyNotes(text) {
    var next = Model.tailNotes(text, root.noteLines)
    var previous = Model.latestNote(root.notes)
    root.notes = next
    var latest = Model.latestNote(next)
    if (latest && (!previous || previous.i !== latest.i)) root.noteAppended(latest)
  }

  onDirChanged: refresh()
  onFeedNameChanged: refresh()
  onDetailedChanged: refresh()

  FileView {
    id: stateFile
    path: root.statePath
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: root.applyState(text())
    // A missing feed is the idle state, not an error: the widget is installed
    // before the first run and stays installed after the last one. But a
    // FAILED read of a file that was there a moment ago is almost always the
    // publisher swapping it, so the last good snapshot is kept and the run is
    // reported stale by its own age instead.
    onLoadFailed: {
      if (root.state !== null) root.missedReads++
      root.retime()
    }
  }

  FileView {
    id: notesFile
    path: root.detailed ? root.notesPath : ""
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: root.applyNotes(text())
    onLoadFailed: root.notes = []
  }

  Timer {
    interval: root.detailed ? root.detailPollMs : root.pollMs
    running: root.configured
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }
}
