import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

// Bar widget + popup for a live pokeagent run.
//
// The bar slot is deliberately one line of text beside the mark: the lead's HP,
// which is the number a watcher reacts to. Everything expensive -- the
// framebuffer, the narration log, the rest of the party, every derived
// statistic -- lives in the popup and is only read while the popup is open (see
// Feed.detailed).
//
// Nothing in here names a cartridge. The harness drives any Gen 1-3 game, so
// which one is booted is the publisher's to say (`game`), the screen's shape is
// read off the published framebuffer, and every derived section is optional:
// what a Gen 1 adapter cannot compute simply does not appear.
//
// The widget stays visible when nothing is running, because "the agent is not
// playing" is information too, and a widget that vanishes is indistinguishable
// from a widget that is broken.
Panel {
  id: root
  moduleName: "poke.run"
  ipcTarget: "poke.run"
  manageIpc: false

  // Where the driving process publishes. Resolution order, most specific
  // first: the shell.json entry (what install.sh pins), then the same env var
  // pokeagent/paths.py honours. Neither set is reported as "not
  // configured" rather than silently polling a guessed path forever.
  readonly property string feedDir: {
    var pinned = String(setting("feedDir", "") || "")
    if (pinned !== "") return pinned.replace(/\/+$/, "")
    return String(Quickshell.env("SAPPHIRE_LIVE_DIR") || "").replace(/\/+$/, "")
  }
  readonly property string feedName: String(setting("feed", "default"))
  readonly property int staleAfterSec: Number(setting("staleAfterSec", 6))
  readonly property bool showFrame: setting("showFrame", true) !== false

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property bool vertical: bar ? bar.vertical : false

  readonly property string label: vertical ? "" : Model.barLabel(feed.state, feed.running)
  readonly property string statusText: Model.tooltip(feed.state, feed.running, feed.configured, feedName)

  // The mark, regenerated from the decomp's own Torchic sprite by
  // widget/make_logo.py -- see docs/logo/. Shipped inside the plugin directory
  // so an installed copy keeps working when this checkout moves or goes away.
  readonly property string markGif: "torchic.gif"
  readonly property string markStill: "torchic-32.png"

  // The sprite is a tall, narrow silhouette, so squeezing it into the 16px
  // canvas a nerd-font glyph gets would leave a ten-pixel-wide bird. It takes a
  // little more of the slot than a glyph does in order to read at all.
  readonly property int markBarSize: Math.round(Style.bar.iconCanvas * 1.25)

  // The framebuffer is drawn at a WHOLE number of device pixels per source
  // pixel (see screenBox), and the popup is sized around it: the glass is
  // the frame's exact size (at 2x a 240px GBA frame wants 720 device pixels,
  // a 360-logical glass), the bezel wraps it, and the two instrument columns
  // sit either side. The next multiple UP is taken whenever the glass it
  // needs is at most a quarter wider than the design glass (a 3x frame beats
  // a 2x frame swimming in margins); past that the frame is centred at the
  // multiple below.
  readonly property real dpr: Screen.devicePixelRatio > 0 ? Screen.devicePixelRatio : 1
  readonly property int designGlass: Style.space(360)
  //: The screen's bezel, each side.
  readonly property int bezelInset: Style.space(8)
  //: Gap between the cockpit's columns, and between the footer's.
  readonly property int columnGap: Style.space(14)
  readonly property int partyWidth: Style.space(128)
  readonly property int instrumentWidth: Style.space(200)

  readonly property int glassWidth: {
    var exact = designGlass * dpr / screenBox.srcW
    var k = Math.ceil(exact)
    if (Math.ceil(k * screenBox.srcW / dpr) > designGlass * 1.25)
      k = Math.max(1, Math.floor(exact))
    return Math.max(designGlass, Math.ceil(k * screenBox.srcW / dpr))
  }
  readonly property int bezelWidth: glassWidth + 2 * bezelInset

  // What the card keeps for itself: its padding and its border
  // (Ui/KeyboardPanel.qml sizes the card to `contentWidth` and lays the
  // content out inside both).
  readonly property int cardInset: 2 * panel.padding
    + Border.left(panel.borderSpec) + Border.right(panel.borderSpec)
  readonly property int cockpitWidth:
    partyWidth + bezelWidth + instrumentWidth + 2 * columnGap + cardInset

  function refresh() {
    feed.refresh()
  }

  function announce() {
    if (bar) bar.run("omarchy-notification-send " + bar.shellQuote(statusText))
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onOpenedChanged: if (opened) {
    if (panelFlick) panelFlick.contentY = 0
    feed.refresh()
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  Feed {
    id: feed
    dir: root.feedDir
    feedName: root.feedName
    staleAfterSec: root.staleAfterSec
    // Frames and narration are only read while the panel is showing them.
    detailed: root.opened
  }

  IpcHandler {
    target: root.ipcTarget
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): string { root.refresh(); return "ok" }
    function status(): string { return root.statusText }
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    // WidgetButton centres its own label, which leaves nowhere to put a sprite
    // beside it. So its label is switched off and the mark and the text are laid
    // out here as one Row; everything else about the button -- the dim/lit
    // treatment, the urgent colour, the tooltip, the click registration -- is
    // still the shared component's.
    labelVisible: false
    hasVisualContent: true
    text: root.label
    // Urgent colour while a battle is on: the affordance the bar already uses
    // for "look at this now", rather than a second glyph nobody can read.
    active: feed.inBattle
    dimmed: !feed.running
    tooltipText: root.statusText
    fontSize: Style.font.bodySmall
    horizontalMargin: 7
    // A vertical bar sizes its slots off the bar's own thickness, so the fixed
    // width only applies when the bar runs horizontally.
    fixedWidth: root.vertical ? -1 : Math.round(slot.implicitWidth + 2 * Style.spaceReal(7))

    onPressed: function(b) {
      if (b === Qt.RightButton) root.announce()
      else if (b === Qt.MiddleButton) root.refresh()
      else root.toggle()
    }

    Row {
      id: slot
      anchors.centerIn: parent
      spacing: Style.space(6)

      Mark {
        anchors.verticalCenter: parent.verticalCenter
        size: root.markBarSize
        animate: feed.running
        tint: feed.inBattle ? button.activeColor : button.foreground
      }

      // FIXED WIDTH, and this is the flicker.
      //
      // `button.fixedWidth` is derived from `slot.implicitWidth`, so for as
      // long as this Text sized itself to its own content the whole bar module
      // resized every time the label changed -- and the label carries the
      // lead's HP, which changes several times a second in a battle. Measured
      // by diffing screenshots 180ms apart: the module's right edge walking
      // 452, 224, 380, 297, 212, 357, 188 px while everything to its right
      // was shoved back and forth. That is the "pop-in / flash / flickering"
      // and it was never the framebuffer at all.
      //
      // So the label gets the width of its WORST CASE and elides into it. The
      // module now has exactly two sizes -- no run, and a run -- and changes
      // between them only when a run starts or stops.
      TextMetrics {
        id: labelMetrics
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        // Ten characters is the Gen-3 nickname limit
        // (include/constants/pokemon.h, POKEMON_NAME_LENGTH), and HP is at
        // most three digits a side. Cap-M is the widest glyph, so this
        // over-reserves slightly rather than clipping a real name.
        text: "MMMMMMMMMM 000/000"
      }

      // STICKY SLOT, never a toggled one. An invisible child is dropped from
      // a Row along with the spacing before it, and `button.fixedWidth` is
      // derived from `slot.implicitWidth` -- so hiding this Text resized the
      // whole bar module. `root.label` is empty whenever `feed.running` is
      // false, and `running` is recomputed from elapsed time on EVERY tick:
      // a driver that stops publishing for six seconds while it plans a route
      // made the module narrow, then wide again the moment it resumed. Every
      // module to its right jumped -- and so did the popup, whose x is
      // computed from this anchor's position (Ui/KeyboardPanel.qml
      // `cardOrigin`), which slides the framebuffer sideways.
      //
      // So the slot is claimed once, when a feed is first seen, and never
      // given back. `feed.present` only ever goes false -> true (Feed.qml
      // keeps the last good snapshot), so this is one widening per session; an
      // install that has never seen a run still shows a bare mark.
      Text {
        anchors.verticalCenter: parent.verticalCenter
        visible: feed.present
        opacity: root.label !== "" ? 1 : 0
        width: labelMetrics.width
        elide: Text.ElideRight
        horizontalAlignment: Text.AlignLeft
        text: root.label
        color: feed.inBattle ? button.activeColor : button.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        renderType: Text.NativeRendering
      }
    }
  }

  // ---- popup ------------------------------------------------------------

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(root.cockpitWidth)
    // The popup is a dashboard now -- objective, screen, opponent, party, dex,
    // team, totals, counters, narration -- so it is allowed to be tall enough
    // for the narration to stay above the fold on a full-height screen. The cap
    // is only the design limit; KeyboardPanel already clamps to what actually
    // fits, and the Flickable scrolls whatever is left over.
    // HIGH-WATER HEIGHT. The popup used to size straight to its content, so
    // every section that comes and goes -- OPPONENT and STAGES on each battle,
    // DEX, TEAM, the notes -- resized the whole window and dragged everything
    // below the screen image with it, several times a minute. That is the
    // flicker; it was never the frame. The window now grows to fit and never
    // shrinks back within a session, so a battle starting costs no movement.
    readonly property real wantedHeight:
      panel.fittedContentHeight(column.implicitHeight, Style.space(1200))
    property real heldHeight: 0
    onWantedHeightChanged: if (wantedHeight > heldHeight) heldHeight = wantedHeight
    contentHeight: Math.max(heldHeight, wantedHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) { if (t === "r" || t === "R") root.refresh() }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: column
          width: panelFlick.width
          spacing: Style.space(12)

          // The header answers "which game is this?" first, because with any of
          // Gen 1-3 potentially booted, every number below it means something
          // different depending on the answer.
          //
          // RESERVED. PanelHero sizes itself to its own labels
          // (Ui/PanelHero.qml, `implicitHeight`), and two of them come and go:
          // the BATTLE pill -- a bordered body-sized Text plus Style.space(4),
          // which is taller than the title line it sits beside -- and the meta
          // line, which hides itself when it has no text. The hero sits
          // directly above the framebuffer, so either one moved the picture,
          // twice per battle. The slot keeps the tallest height it has seen, so
          // the frame stops travelling.
          ReservedSlot {
            width: parent.width

            PanelHero {
              width: parent.width
              title: Model.gameTitle(feed.state)
              meta: Model.headerMeta(feed.state, feed.running, feed.present, feed.age)
              detail: feed.running && feed.inBattle ? "BATTLE" : ""
              foreground: root.foreground
              fontFamily: root.fontFamily
              // The mark carries the same idle-vs-running treatment the bar slot
              // does: lit and breathing while a run is live, dimmed and still
              // when it is not.
              iconOpacity: feed.running ? 1.0 : 0.45
              iconComponent: Component {
                Mark {
                  size: Style.font.display
                  animate: feed.running
                  tint: root.foreground
                }
              }
            }
          }

          // Idle / misconfigured / broken states, in decreasing severity. Each
          // says what to do about it: an unexplained blank panel is the one
          // outcome worse than having no widget at all.
          Text {
            visible: !feed.configured
            width: parent.width
            text: "No feed directory configured.\n\nRun widget/install.sh from the "
              + "checkout, or point it at one by hand:\n\n"
              + "  omarchy bar set poke.run feedDir <repo>/live"
            color: root.urgent
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
          }

          Text {
            visible: feed.configured && !feed.present
            width: parent.width
            text: "Waiting for a run.\n\nAttach a feed in the driver:\n"
              + "  LiveFeed(\"" + root.feedName + "\").attach(d)\n\n"
              + "Watching " + feed.statePath
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
          }

          // RESERVED, and faded rather than unmounted. `state.error` is the
          // publisher's last failed write (pokeagent/live.py `_fail`), so it
          // appears and disappears with transient failures -- and it is a
          // WRAPPING Text above the framebuffer, so each appearance shoved the
          // picture down by one to three lines and each disappearance pulled it
          // back. It costs no space until an error has actually happened once.
          ReservedSlot {
            // A Column inserts its spacing before every VISIBLE child, so an
            // always-visible zero-height slot would cost a permanent gap above
            // the frame. The slot appears the first time it has something to
            // hold and never leaves again.
            visible: reserved > 0
            width: parent.width

            Text {
              opacity: feed.error !== "" ? 1 : 0
              width: parent.width
              height: text !== "" ? implicitHeight : 0
              text: feed.error !== "" ? "Feed reports: " + feed.error : ""
              color: root.urgent
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.WordWrap
            }
          }

          // ---- the cockpit -------------------------------------------------
          //
          // Screen dead centre, instruments either side, nothing to scroll
          // for. Everything above this row is reserved space, so the frame
          // cannot move within a session; the columns beside and below it
          // may reflow at the rate their content changes.

          Row {
            id: cockpit
            width: parent.width
            spacing: root.columnGap

            // ---- left instrument: the party, lead on top ------------------

            Column {
              id: partyColumn
              //: Six entries, empty ones null, so the Repeater's model never
              //: changes length.
              readonly property var slots: Model.partySlots(feed.state)

              width: root.partyWidth
              spacing: Style.space(4)

              PanelSectionHeader {
                text: "PARTY"
                foreground: root.foreground
                fontFamily: root.fontFamily
              }

              // COUNT MODEL, not the array. A Repeater bound to a JS array
              // destroys and recreates EVERY delegate whenever that array's
              // CONTENT changes -- and `feed.state` is a fresh object on each
              // of the publisher's four writes a second, so the lead's HP
              // ticking rebuilt every slot, sprite and bar four times a
              // second. Binding on the length keeps the delegates alive and
              // lets the bindings inside them update in place.
              Repeater {
                model: partyColumn.slots.length

                PartySlot {
                  required property int index
                  width: partyColumn.width
                  mon: partyColumn.slots[index]
                  isLead: index === 0
                }
              }

              // The team's shape belongs with the party it describes, and
              // it fills the column under six slots instead of a footer cell.
              PanelSeparator {
                visible: teamColumn.visible
                foreground: root.foreground
              }

              Column {
                id: teamColumn
                readonly property var team: Model.team(feed.state)
                readonly property string coverage: Model.coverageLabel(team)

                visible: team !== null
                width: parent.width
                spacing: Style.space(6)

                PanelSectionHeader {
                  text: "TEAM"
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                }

                StatGrid {
                  width: parent.width
                  columns: 2
                  cells: Model.teamCells(feed.state)
                }

                // Full width: the list is as long as the number of types the
                // team cannot answer, and eliding it would throw away the
                // only part of it that matters.
                Stat {
                  visible: teamColumn.coverage !== ""
                  width: parent.width
                  label: "GAPS"
                  value: teamColumn.coverage
                  wrap: true
                }
              }
            }

            // ---- centre: the screen in its bezel, the matchup under it ------

            Column {
              id: centre
              width: root.bezelWidth
              spacing: Style.space(8)

              Rectangle {
                id: bezel
                visible: root.showFrame
                width: parent.width
                height: 2 * inset + screenBox.height
                color: Qt.rgba(0, 0, 0, 0.35)
                radius: Style.cornerRadius

                //: The dark margin between the glass and the panel.
                readonly property int inset: root.bezelInset

                Item {
                  id: screenBox
                  x: bezel.inset
                  y: bezel.inset
                  width: parent.width - 2 * bezel.inset

                  // INTEGER SCALE, counted in DEVICE pixels. A 240px frame
                  // stretched across a 332-logical column on a 2x display is
                  // 2.77 source pixels per pixel, and nearest-neighbour then
                  // draws alternating two- and three-pixel columns: every
                  // sprite looked chewed. The frame is drawn at the largest
                  // whole multiple that fits and centred; `root.frameFitWidth`
                  // picks the popup width so that multiple spans the glass.
                  //
                  // The published PNG is the authority on the frame's shape
                  // (a GBA frame is 240x160, a Game Boy / Color frame
                  // 160x144), so Gen 1-3 render without stretching and
                  // without a table of consoles. REMEMBERED from the last
                  // decoded frame rather than read live, because sourceSize
                  // is zero while a reload is in flight and feeding that into
                  // the height is the other half of the jump.
                  property int srcW: 240
                  property int srcH: 160
                  readonly property int pixelScale: Math.max(1, Math.floor(width * root.dpr / srcW))
                  readonly property real frameW: pixelScale * srcW / root.dpr
                  readonly property real frameH: pixelScale * srcH / root.dpr
                  // Centred on a whole device pixel: a half-pixel offset
                  // would put the nearest-neighbour sampling off the grid.
                  readonly property real frameX: Math.round((width - frameW) / 2 * root.dpr) / root.dpr

                  height: Math.ceil(frameH)

                  // Which buffer is on screen. The other one loads the
                  // incoming frame invisibly and they swap only once it is
                  // READY, so a fully drawn frame is always showing. One
                  // Image cannot do this: Qt clears it the moment its source
                  // changes.
                  property bool frontIsA: true
                  property bool everLoaded: false

                  function present(img) {
                    if (img.sourceSize.width > 0 && img.sourceSize.height > 0) {
                      srcW = img.sourceSize.width
                      srcH = img.sourceSize.height
                    }
                    everLoaded = true
                    frontIsA = (img === imageA)
                  }

                  // Hand each new URL to whichever buffer is currently hidden.
                  readonly property string incoming: feed.screenUrl
                  onIncomingChanged: load()

                  //: Bumped to force a reload of a URL that has not changed.
                  property int nonce: 0

                  function load() {
                    if (incoming === "") return
                    var back = frontIsA ? imageB : imageA
                    back.source = incoming + "&n=" + nonce
                  }

                  // SELF-HEAL. The URL only changes when the frame counter
                  // does, and the emulator does not tick while the driver
                  // spends a minute planning a route -- so a panel that opens
                  // during one of those pauses gets a source assignment it
                  // has already seen, no load fires, and the box sits blank
                  // until the game moves again. Also covers a failed decode.
                  Timer {
                    interval: 1500
                    repeat: true
                    running: bezel.visible && feed.screenUrl !== ""
                    onTriggered: {
                      var front = screenBox.frontIsA ? imageA : imageB
                      var back = screenBox.frontIsA ? imageB : imageA
                      if (screenBox.everLoaded
                          && front.status === Image.Ready
                          && back.status !== Image.Error) return
                      screenBox.nonce++
                      screenBox.load()
                    }
                  }

                  Text {
                    anchors.centerIn: parent
                    // Only before the first frame ever arrives. After that a
                    // buffer always holds something, and "Loading..." over a
                    // live screen would be a lie.
                    visible: !screenBox.everLoaded
                    text: "Loading\u2026"
                    color: root.dim
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                  }

                  // SWAPPED BY OPACITY, NOT BY `visible`. `visible: false`
                  // lets the scene graph drop the render node, so the buffer
                  // coming to the front has to re-upload its texture -- and
                  // with `asynchronous: true` that lands a frame late: for
                  // one compositor frame neither image painted and the bezel
                  // showed through as a dark flash at the publisher's frame
                  // rate. An opacity-0 Image keeps its node and its texture,
                  // so the swap is a pure compositor operation; `z` keeps the
                  // live one on top so a mid-load back buffer can never be
                  // seen even for a frame.
                  Image {
                    id: imageA
                    x: screenBox.frameX
                    y: 0
                    width: screenBox.frameW
                    height: screenBox.frameH
                    cache: false
                    asynchronous: true
                    fillMode: Image.Stretch
                    // A console framebuffer blown up: nearest-neighbour keeps
                    // it looking like the hardware, not a smeared photograph.
                    smooth: false
                    mipmap: false
                    visible: screenBox.everLoaded
                    opacity: screenBox.frontIsA ? 1 : 0
                    z: screenBox.frontIsA ? 1 : 0
                    onStatusChanged: if (status === Image.Ready) screenBox.present(imageA)
                  }

                  Image {
                    id: imageB
                    x: screenBox.frameX
                    y: 0
                    width: screenBox.frameW
                    height: screenBox.frameH
                    cache: false
                    asynchronous: true
                    fillMode: Image.Stretch
                    smooth: false
                    mipmap: false
                    visible: screenBox.everLoaded
                    opacity: screenBox.frontIsA ? 0 : 1
                    z: screenBox.frontIsA ? 0 : 1
                    onStatusChanged: if (status === Image.Ready) screenBox.present(imageB)
                  }

                  Component.onCompleted: load()
                }
              }

              // The matchup: the lead against the foe, the way the game's own
              // battle HUD faces them off -- two HP bars pointing at each
              // other. Wrapped in a ReservedSlot: it only exists during a
              // battle, and letting it collapse moved the footer twice per
              // encounter.
              ReservedSlot {
                width: parent.width

                Row {
                  id: matchup
                  readonly property var foe: Model.enemy(feed.state)
                  readonly property var mine: Model.lead(feed.state)

                  visible: foe !== null
                  width: parent.width
                  spacing: Style.space(10)

                  Combatant {
                    width: (matchup.width - vsMark.width - 2 * matchup.spacing) / 2
                    mon: matchup.mine
                    name: Model.monName(matchup.mine)
                    level: Model.monLevel(matchup.mine)
                    hp: Model.monHp(matchup.mine)
                    fraction: Model.hpFraction(matchup.mine)
                    nameColor: root.foreground
                  }

                  Text {
                    id: vsMark
                    anchors.verticalCenter: parent.verticalCenter
                    text: "vs"
                    color: root.dim
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    font.bold: true
                  }

                  Combatant {
                    width: (matchup.width - vsMark.width - 2 * matchup.spacing) / 2
                    mirrored: true
                    mon: matchup.foe ? { species: matchup.foe.species } : null
                    name: matchup.foe ? matchup.foe.species : ""
                    level: matchup.foe && matchup.foe.level !== null ? "L" + matchup.foe.level : ""
                    hp: Model.enemyHp(matchup.foe)
                    fraction: Model.enemyFraction(matchup.foe)
                    nameColor: root.urgent
                  }
                }
              }

              // What the agent is trying to do: the one thing a watcher
              // cannot work out by looking at the game, so it captions the
              // screen. Glass width, so a retarget's longer wording still
              // fits on two lines.
              PanelSeparator {
                visible: objectiveColumn.visible
                foreground: root.foreground
              }

              Column {
                id: objectiveColumn
                // STICKY. A single state write without an objective would
                // otherwise unmount this whole column for one frame. Keep the
                // last one that existed; it is stale for a tick at worst.
                readonly property var incoming: Model.objective(feed.state)
                property var objective: null
                onIncomingChanged: if (incoming !== null) objective = incoming

                visible: objective !== null
                width: parent.width
                spacing: Style.space(4)

                PanelSectionHeader {
                  text: "OBJECTIVE"
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                }

                ProgressRow {
                  width: parent.width
                  title: objectiveColumn.objective ? objectiveColumn.objective.name : ""
                  detail: objectiveColumn.objective ? objectiveColumn.objective.detail : ""
                  value: objectiveColumn.objective
                    ? Model.percentLabel(objectiveColumn.objective.percent) : ""
                  titleSize: Style.font.body
                  bold: true
                  // Faded rather than unmounted: percent goes null between
                  // objectives, and a track that comes and goes moves the
                  // rows under it on every retarget.
                  trackOpacity: (objectiveColumn.objective
                    && objectiveColumn.objective.percent !== null) ? 1 : 0
                  fraction: objectiveColumn.objective
                    ? Model.fraction(objectiveColumn.objective.percent) : 0
                }
              }
            }

            // ---- right instrument: where, the trainer card, the goal --------

            Column {
              id: instruments
              readonly property string where: Model.mapLabel(feed.state)
              readonly property var hudCells: Model.hudCells(feed.state)
              readonly property var badges: Model.badgeCount(feed.state)

              width: root.instrumentWidth
              spacing: Style.space(10)

              Column {
                width: parent.width
                spacing: Style.space(2)
                visible: instruments.where !== ""

                Text {
                  width: parent.width
                  text: instruments.where
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  font.bold: true
                  wrapMode: Text.WordWrap
                  maximumLineCount: 2
                  elide: Text.ElideRight
                }

                Text {
                  visible: text !== ""
                  width: parent.width
                  text: Model.whereLabel(feed.state)
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  elide: Text.ElideRight
                }
              }

              StatGrid {
                width: parent.width
                columns: 2
                cells: instruments.hudCells
              }

              Stat {
                visible: instruments.badges !== null
                width: parent.width
                label: "BADGES " + (instruments.badges !== null ? instruments.badges : 0) + "/" + Model.BADGE_SLOTS
                valueComponent: Component {
                  BadgePips { count: instruments.badges !== null ? instruments.badges : 0 }
                }
              }

              PanelSeparator {
                visible: progressColumn.visible
                foreground: root.foreground
              }

              Column {
                id: progressColumn
                readonly property var dex: Model.dex(feed.state)
                readonly property var stages: Model.stages(feed.state)

                visible: dex !== null || stages.length > 0
                width: parent.width
                spacing: Style.space(3)

                PanelSectionHeader {
                  text: "PROGRESS"
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                }

                ProgressRow {
                  visible: progressColumn.dex !== null
                  width: parent.width
                  title: "Pok\u00e9dex " + Model.dexLabel(progressColumn.dex)
                  value: Model.percentLabel(progressColumn.dex ? progressColumn.dex.percent : null)
                  // A count with no denominator has no bar to draw: an empty
                  // track beside "42" would claim the run is at 0%.
                  showTrack: progressColumn.dex !== null && progressColumn.dex.percent !== null
                  fraction: progressColumn.dex ? Model.fraction(progressColumn.dex.percent) : 0
                }

                // The ladder reads as a plan: every rung listed, in rank
                // order, with how far along it is. Only the rung being worked
                // on carries a track -- five empty tracks stacked under each
                // other were a wall of nothing for the first day of every
                // run. Length model, for the reason over the party Repeater.
                Repeater {
                  model: progressColumn.stages.length

                  ProgressRow {
                    required property int index
                    readonly property var row: progressColumn.stages[index]

                    width: progressColumn.width
                    title: row ? row.name : ""
                    value: Model.stageValue(row)
                    titleColor: row && row.current ? root.foreground : root.dim
                    bold: row ? row.current : false
                    showTrack: row ? row.current : false
                    fraction: row ? Model.fraction(row.percent) : 0
                  }
                }
              }

              PanelSeparator {
                visible: counterColumn.visible
                foreground: root.foreground
              }

              Column {
                id: counterColumn
                readonly property var cells: Model.counterCells(feed.state)

                visible: cells.length > 0
                width: parent.width
                spacing: Style.space(6)

                PanelSectionHeader {
                  text: "COUNTERS"
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                }

                StatGrid {
                  width: parent.width
                  columns: 2
                  cells: counterColumn.cells
                }
              }
            }
          }

          // ---- footer strip: what the agent says -----------------------------
          // The pace estimate and the narration log, the two things that are
          // prose. Narration gets the width: a log line that fits on one
          // line is read; one that wraps is skimmed.

          PanelSeparator {
            visible: paceColumn.visible || notesColumn.visible
            foreground: root.foreground
          }

          Row {
            id: footer
            width: parent.width
            spacing: root.columnGap

            // The pace column is the party column's width, so the narration
            // starts on the same line the screen does: one grid, top to
            // bottom.
            readonly property real paceWidth: root.partyWidth
            readonly property real notesWidth:
              paceColumn.visible ? width - paceWidth - spacing : width

            Column {
              id: paceColumn
              readonly property var rows: Model.paceLines(feed.state)

              visible: rows.length > 0
              width: footer.paceWidth
              spacing: Style.space(4)

              PanelSectionHeader {
                text: "PACE"
                foreground: root.foreground
                fontFamily: root.fontFamily
              }

              // Length model: the estimates are re-derived on every snapshot.
              Repeater {
                model: paceColumn.rows.length

                Stat {
                  required property int index
                  readonly property var row: paceColumn.rows[index]
                  width: paceColumn.width
                  label: row ? String(row.label).toUpperCase() : ""
                  value: row ? row.value : ""
                  wrap: true
                }
              }

              // The basis travels with the numbers. Publishing a fourfold
              // extrapolation without saying so is how a demo becomes a lie.
              Text {
                visible: text !== ""
                width: parent.width
                text: Model.paceBasis(feed.state)
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                wrapMode: Text.WordWrap
              }
            }

            Column {
              id: notesColumn
              visible: feed.notes.length > 0
              width: footer.notesWidth
              spacing: Style.space(4)

              PanelSectionHeader {
                text: "NARRATION"
                foreground: root.foreground
                fontFamily: root.fontFamily
              }

              // Length model: Feed.qml re-parses the tail of the narration
              // log on every read, so the array is new each time even when
              // the last five lines are the same five lines.
              Repeater {
                model: feed.notes.length

                Text {
                  required property int index
                  readonly property var note: feed.notes[index]

                  width: notesColumn.width
                  // The newest line is the one being read; the rest are
                  // context.
                  text: note ? note.t + "  " + note.msg : ""
                  color: index === feed.notes.length - 1 ? root.foreground : root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  wrapMode: Text.WordWrap
                  maximumLineCount: 2
                  elide: Text.ElideRight
                }
              }
            }
          }
        }
      }
    }
  }

  // ---- row components ----------------------------------------------------

  // The Torchic mark. Three layers deep on purpose: an installed plugin whose
  // assets did not get copied, or a Qt build without the GIF image plugin,
  // should degrade to something rather than leave a hole in the bar.
  component Mark: Item {
    id: mark

    property real size: Style.font.display
    // The hop is the run. Torchic breathes while the agent is playing and
    // stands still when it is not -- the same fact the dim/lit treatment
    // carries, in a channel the eye catches without being pointed at it.
    property bool animate: false
    property color tint: root.foreground

    implicitWidth: size
    implicitHeight: size
    width: size
    height: size

    AnimatedImage {
      id: animated
      anchors.fill: parent
      source: root.markGif
      playing: mark.animate
      fillMode: Image.PreserveAspectFit
      // A 98px sprite drawn at 20: mipmapping is what keeps the downscale from
      // aliasing into a different bird every hop.
      smooth: true
      mipmap: true
      // Parked mid-hop reads as a stalled render; parked on the first frame
      // reads as a sprite standing still.
      onPlayingChanged: if (!playing) currentFrame = 0
    }

    Image {
      id: still
      anchors.fill: parent
      // Only once the animation has actually failed -- not while it is still
      // loading, or the bar would flash a different bird on every shell reload.
      visible: animated.status === Image.Error
      source: root.markStill
      fillMode: Image.PreserveAspectFit
      smooth: true
      mipmap: true
    }

    Text {
      anchors.centerIn: parent
      // nf-fa-gamepad: the one glyph that says "a game is being played here"
      // without borrowing another widget's vocabulary. Only ever seen if both
      // sprite assets are missing from the installed plugin.
      text: "\uf11b"
      visible: animated.status === Image.Error && still.status === Image.Error
      color: mark.tint
      font.family: root.fontFamily
      font.pixelSize: mark.size
    }
  }

  // The bare progress track. One shape and weight for every bar in the panel
  // -- HP, objective, dex, the current stage -- so a glance reads them as the
  // same kind of fact. The track colour is the shell's own slider track
  // (Ui/PanelSlider.qml), so it sits in the panel like a native control.
  // Named Track, not ProgressBar: QtQuick.Controls (imported above for its own
  // widgets) exports a ProgressBar too, and an inline component that shadows
  // an imported type is a coin-flip for any reader and for tooling.
  component Track: Rectangle {
    id: track

    property real fraction: 0
    property color fill: root.foreground
    //: Anchor the fill to the right edge instead, so a draining bar recedes
    //: towards the left (the foe's side of the matchup).
    property bool mirrored: false

    readonly property real clamped: Math.max(0, Math.min(1, fraction))

    implicitHeight: Style.space(4)
    height: implicitHeight
    radius: height / 2
    color: Style.selectedFillFor(root.foreground, Color.accent)

    Rectangle {
      width: Math.round(parent.width * track.clamped)
      x: track.mirrored ? parent.width - width : 0
      height: parent.height
      radius: parent.radius
      color: track.fill

      // Progress moves in steps (a badge, a catch, a tick of HP); the fill
      // easing turns each step into a motion the eye can follow instead of a
      // jump it has to notice. Same duration as the shell's sliders.
      Behavior on width {
        NumberAnimation { duration: 140; easing.type: Easing.OutCubic }
      }
    }
  }

  // A labelled progress line: the title at the left, its number at the right,
  // and the track under both, full width. Every bar in the panel used to keep
  // its caption in a column BESIDE the track, sized to the widest caption any
  // bar could ever show -- so every track stopped at half the panel with a
  // lone "0%" floating far to its right. Stacking the label over the track
  // gives the row a stable height without measuring anything, lets each
  // track run the full column, and lines every row's number up on one edge.
  component ProgressRow: Column {
    id: progressRow

    property string title: ""
    property string detail: ""
    property string value: ""
    property real fraction: 0
    property color fill: root.foreground
    property color titleColor: root.foreground
    property real titleSize: Style.font.bodySmall
    property real valueSize: Style.font.caption
    property bool bold: false
    property bool showTrack: true
    property real trackOpacity: 1

    spacing: Style.space(4)

    Row {
      id: labelRow
      width: parent.width
      spacing: Style.space(8)

      Text {
        id: titleText
        width: Math.max(0, labelRow.width - valueText.width
                           - (valueText.visible ? labelRow.spacing : 0))
        text: progressRow.title
        color: progressRow.titleColor
        font.family: root.fontFamily
        font.pixelSize: progressRow.titleSize
        font.bold: progressRow.bold
        elide: Text.ElideRight
      }

      // The number is the smaller face, so it is centred against the title
      // rather than hung from the row's top edge.
      Text {
        id: valueText
        visible: text !== ""
        text: progressRow.value
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: progressRow.valueSize
        anchors.verticalCenter: titleText.verticalCenter
      }
    }

    Text {
      visible: text !== ""
      width: parent.width
      text: progressRow.detail
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      wrapMode: Text.WordWrap
      maximumLineCount: 3
      elide: Text.ElideRight
    }

    Track {
      visible: progressRow.showTrack
      opacity: progressRow.trackOpacity
      width: parent.width
      fraction: progressRow.fraction
      fill: progressRow.fill
    }
  }

  // A small fact: micro-label over value, the way the shell's own weather
  // panel lays out FEELS / WIND / HUMID. One shape for the HUD, the team
  // strip and the counters. `valueComponent` swaps the value text for
  // something drawn (the badge pips). The caller owns the width.
  component Stat: Column {
    id: stat

    property string label: ""
    property string value: ""
    property bool wrap: false
    property Component valueComponent: null

    spacing: Style.space(2)

    Text {
      width: parent.width
      text: stat.label
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      font.bold: true
      elide: Text.ElideRight
    }

    Text {
      visible: stat.valueComponent === null
      width: parent.width
      text: stat.value
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      wrapMode: stat.wrap ? Text.WordWrap : Text.NoWrap
      elide: stat.wrap ? Text.ElideNone : Text.ElideRight
    }

    Loader {
      active: stat.valueComponent !== null
      visible: active
      sourceComponent: stat.valueComponent
    }
  }

  // Small facts in equal columns. The model is a list rather than a fixed
  // set of properties precisely so an unreported key produces no cell at all
  // instead of a label with a blank beside it. The caller owns the width.
  component StatGrid: Grid {
    id: statGrid

    property var cells: []

    columns: 4
    columnSpacing: Style.space(12)
    rowSpacing: Style.space(6)

    // Length model. The counters carry a frame count, so their content
    // changes on every single snapshot -- an array model rebuilt every cell
    // in the grid four times a second.
    Repeater {
      model: statGrid.cells.length

      Stat {
        required property int index
        readonly property var cell: statGrid.cells[index]

        width: (statGrid.width - (statGrid.columns - 1) * statGrid.columnSpacing)
          / statGrid.columns
        label: cell ? cell.label : ""
        value: cell ? cell.value : ""
      }
    }
  }

  // The badge case: eight slots, the earned ones lit. Read at a glance the
  // way the trainer card reads it, instead of as "3/8" in a grid cell.
  component BadgePips: Row {
    id: pips

    property int count: 0

    spacing: Style.space(3)
    // Rides the value line's height so the Stat above it keeps the same
    // rhythm as its text-valued neighbours.
    height: Math.ceil(pipMetrics.height)

    FontMetrics {
      id: pipMetrics
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    Repeater {
      model: Model.BADGE_SLOTS

      Rectangle {
        required property int index
        anchors.verticalCenter: parent.verticalCenter
        width: Style.space(8)
        height: width
        radius: Math.min(2, Style.cornerRadius)
        color: index < pips.count
          ? root.foreground
          : Style.selectedFillFor(root.foreground, Color.accent)
      }
    }
  }

  // One of the six party slots, as a row: the sprite beside the name, level
  // and a thin HP bar, the way the game's own party screen lists them. An
  // empty slot is drawn faintly rather than left out, so a party of two
  // reads as two of six. The lead is the only one outlined: it is the one
  // the bar and the matchup are about.
  component PartySlot: Rectangle {
    id: partySlot

    property var mon: null
    property bool isLead: false

    readonly property bool filled: mon !== null && mon !== undefined
    readonly property bool fainted: filled && mon.fainted === true
    readonly property real fraction: Model.hpFraction(mon)
    readonly property int pad: Style.space(5)

    implicitHeight: slotRow.implicitHeight + 2 * pad
    height: implicitHeight
    radius: Style.cornerRadius
    color: filled ? Style.normalFill : Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.03)
    border.width: isLead && filled ? Style.selectedBorderWidth || 1 : 0
    border.color: Style.selectedBorderFor(root.foreground, Color.accent)

    Row {
      id: slotRow
      x: partySlot.pad
      y: partySlot.pad
      width: parent.width - 2 * partySlot.pad
      spacing: Style.space(6)

      // Geometry from the SLOT, painting from the status: the sprite's box is
      // the same size whether the sprite has decoded, is missing (an egg, an
      // unknown species) or the slot is empty, so a row never reshuffles.
      Item {
        width: Style.space(28)
        height: Style.space(28)
        anchors.verticalCenter: parent.verticalCenter

        Image {
          anchors.fill: parent
          source: Model.spriteFor(partySlot.mon)
          visible: status === Image.Ready
          sourceSize.width: width
          sourceSize.height: height
          fillMode: Image.PreserveAspectFit
          smooth: false          // pixel art: never interpolate
          opacity: partySlot.fainted ? 0.4 : 1
        }

        // An egg has no sprite the player is allowed to see.
        Text {
          anchors.centerIn: parent
          visible: partySlot.filled && partySlot.mon.egg === true
          text: "EGG"
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          font.bold: true
        }
      }

      Column {
        width: parent.width - Style.space(28) - parent.spacing
        anchors.verticalCenter: parent.verticalCenter
        spacing: Style.space(3)

        Row {
          width: parent.width
          spacing: Style.space(4)

          Text {
            id: slotName
            width: Math.max(0, parent.width - slotLevel.width
                               - (slotLevel.visible ? parent.spacing : 0))
            text: partySlot.filled ? Model.monName(partySlot.mon) : ""
            color: partySlot.fainted ? root.urgent : root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            font.bold: partySlot.isLead
            elide: Text.ElideRight
          }

          Text {
            id: slotLevel
            visible: text !== ""
            text: Model.monLevel(partySlot.mon)
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            anchors.verticalCenter: slotName.verticalCenter
          }
        }

        Track {
          width: parent.width
          implicitHeight: Style.space(3)
          // Faded, not unmounted: an egg or an empty slot keeps the row's
          // height so the column never changes shape.
          opacity: partySlot.filled && partySlot.mon.max_hp > 0 ? 1 : 0
          fraction: partySlot.fraction
          fill: Model.hpColor(partySlot.fraction)
        }
      }
    }
  }

  // One side of the matchup: sprite, name and level, HP and its bar. The
  // foe's side is mirrored so the two bars point at each other across the
  // "vs", the way the game's own battle HUD faces them off.
  component Combatant: Column {
    id: side

    property var mon: null
    property string name: ""
    property string level: ""
    property string hp: ""
    property real fraction: 0
    property color nameColor: root.foreground
    property bool mirrored: false

    spacing: Style.space(4)

    Row {
      width: parent.width
      spacing: Style.space(6)
      layoutDirection: side.mirrored ? Qt.RightToLeft : Qt.LeftToRight

      Item {
        width: Style.space(28)
        height: Style.space(28)
        anchors.verticalCenter: parent.verticalCenter

        Image {
          anchors.fill: parent
          source: Model.spriteFor(side.mon)
          visible: status === Image.Ready
          sourceSize.width: width
          sourceSize.height: height
          fillMode: Image.PreserveAspectFit
          smooth: false
          // The player's mon is seen from behind in the game; here both
          // sprites are the front view, so the near side is flipped to face
          // the foe.
          mirror: !side.mirrored
        }
      }

      Column {
        width: parent.width - Style.space(28) - parent.spacing
        anchors.verticalCenter: parent.verticalCenter
        spacing: Style.space(1)

        Text {
          width: parent.width
          text: side.level !== "" ? side.name + "  " + side.level : side.name
          color: side.nameColor
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
          font.bold: true
          elide: Text.ElideRight
          horizontalAlignment: side.mirrored ? Text.AlignRight : Text.AlignLeft
        }

        Text {
          width: parent.width
          text: side.hp
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          horizontalAlignment: side.mirrored ? Text.AlignRight : Text.AlignLeft
        }
      }
    }

    Track {
      width: parent.width
      fraction: side.fraction
      fill: Model.hpColor(side.fraction)
      // The foe's bar drains from the left, towards the "vs".
      mirrored: side.mirrored
    }
  }
}
