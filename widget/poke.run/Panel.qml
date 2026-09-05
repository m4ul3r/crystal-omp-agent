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

  // ONE caption column for every bar in the panel.
  //
  // A RunBar's track used to stop at whatever caption that particular row
  // happened to carry, so a ladder of stages drew a ladder of DIFFERENT
  // lengths -- "CHAMPION  done" left a long track, "VICTORY ROAD  62%" a
  // short one -- and no two bars in the group lined up with each other.
  //
  // Measured from the widest caption each bar can EVER show, not the one it
  // is showing right now, so a rung ticking 9% -> 10% cannot re-length every
  // bar on screen. Same doctrine as reserveCaption on the bar itself:
  // geometry must not be a function of content that moves.
  FontMetrics {
    id: captionColumnMetrics
    font.family: root.fontFamily
    font.pixelSize: Style.font.caption
  }

  readonly property real captionReserve: {
    // "100%" is the widest a percentage gets and "done" is what a finished
    // rung shows instead, so both are measured for every label -- the column
    // has to fit whichever ending a given bar reaches.
    var widest = Math.max(captionColumnMetrics.advanceWidth("100%"),
                          captionColumnMetrics.advanceWidth("done"))

    var dex = Model.dexLabel(Model.dex(feed.state))
    if (dex !== "")
      widest = Math.max(widest,
                        captionColumnMetrics.advanceWidth(dex + "  100%"))

    var rows = Model.stages(feed.state)
    for (var i = 0; i < rows.length; i++) {
      if (!rows[i] || !rows[i].name) continue
      widest = Math.max(
        widest,
        captionColumnMetrics.advanceWidth(rows[i].name + "  100%"),
        captionColumnMetrics.advanceWidth(rows[i].name + "  done"))
    }
    return Math.ceil(widest)
  }

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
    contentWidth: panel.fittedContentWidth(Style.space(360))
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
              meta: Model.headerMeta(feed.state, feed.running, feed.present)
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

          // FADED, NEVER UNMOUNTED. `feed.running` is recomputed from elapsed
          // time on every tick, so near the staleness threshold this line
          // appeared and vanished repeatedly -- and it sits ABOVE the
          // framebuffer, so each toggle shifted the screen down and back. Same
          // disease the narration line below the screen already had.
          Text {
            id: staleLine
            opacity: (feed.present && !feed.running) ? 1 : 0
            width: parent.width
            height: Math.ceil(smallMetrics.height)
            text: feed.state && feed.state.live === false
              ? "Run ended; last frame " + Math.round(feed.age) + "s ago."
              : "Feed stale: last frame " + Math.round(feed.age) + "s ago."
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            maximumLineCount: 1
            elide: Text.ElideRight
          }

          //: One shared metric for every space-reserving line above the
          //: screen. Ids resolve component-wide, so the earlier siblings can
          //: use it too.
          FontMetrics {
            id: smallMetrics
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
          }
          FontMetrics {
            id: bodyMetrics
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
          }

          // ---- what the agent is trying to do -----------------------------
          //
          // Top of the panel, above even the screen: the one thing a watcher
          // cannot work out by looking at the game.

          PanelSeparator {
            visible: objectiveColumn.visible
            foreground: root.foreground
          }

          Column {
            id: objectiveColumn
            // STICKY. A single state write without an objective would
            // otherwise unmount this whole column -- header, two lines and the
            // bar -- and slam the framebuffer upward for one frame. Keep the
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

            // FIXED HEIGHT, both of them. The objective text is rewritten
            // whenever the loop retargets -- "go to MossdeepCity_Gym for badge
            // 7 [at AquaHideout_B2F...]" -- and wrapping between one, two and
            // three lines moved every pixel below it, the framebuffer
            // included. Reserve the tallest case and let short text sit in it.
            Text {
              opacity: text !== "" ? 1 : 0
              width: parent.width
              height: Math.ceil(bodyMetrics.height * 2)
              text: objectiveColumn.objective ? objectiveColumn.objective.name : ""
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              font.bold: true
              wrapMode: Text.WordWrap
              maximumLineCount: 2
              elide: Text.ElideRight
              verticalAlignment: Text.AlignTop
            }

            Text {
              opacity: text !== "" ? 1 : 0
              width: parent.width
              height: Math.ceil(smallMetrics.height * 3)
              text: objectiveColumn.objective ? objectiveColumn.objective.detail : ""
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.WordWrap
              maximumLineCount: 3
              elide: Text.ElideRight
              verticalAlignment: Text.AlignTop
            }

            RunBar {
              // Faded rather than unmounted: percent goes null between
              // objectives, and dropping the bar out of the column moved the
              // screen every time it did.
              opacity: (objectiveColumn.objective
                && objectiveColumn.objective.percent !== null) ? 1 : 0
              width: parent.width
              fraction: objectiveColumn.objective
                ? Model.fraction(objectiveColumn.objective.percent) : 0
              // The percentage goes null between objectives, and the caption is
              // what gives this bar its height -- so the bar has to reserve a
              // caption line whether or not it has one to show, or the whole
              // objective column (and the framebuffer under it) resizes on
              // every retarget.
              reserveCaption: true
              caption: objectiveColumn.objective
                ? Model.percentLabel(objectiveColumn.objective.percent) : ""
              captionReserve: root.captionReserve
            }
          }

          // ---- the framebuffer the agent is actually running --------------

          Rectangle {
            id: screenBox
            // NOT gated on the image's status. The published URL carries a
            // cache-busting frame number, so it changes on every poll and the
            // Image drops to Loading each time. Hiding the box while that
            // happened unmounted it from the Column, so the whole panel
            // collapsed and snapped back several times a second.
            visible: root.showFrame
            width: parent.width

            // The published PNG is the authority on the screen's shape: a GBA
            // frame is 240x160 and a Game Boy / Color frame is 160x144, so
            // reading it off the image renders Gen 1-3 without stretching and
            // without a table of consoles. REMEMBERED rather than read live,
            // because sourceSize is zero while a reload is in flight and
            // feeding that into the height is the other half of the jump.
            property real aspect: 240 / 160
            height: Math.round(width / aspect)
            color: Qt.rgba(0, 0, 0, 0.35)
            radius: Style.cornerRadius
            clip: true

            // Which buffer is on screen. The other one loads the incoming
            // frame invisibly and they swap only once it is READY, so a fully
            // drawn frame is always showing. One Image cannot do this: Qt
            // clears it the moment its source changes.
            property bool frontIsA: true
            property bool everLoaded: false

            function present(img) {
              if (img.sourceSize.height > 0)
                aspect = img.sourceSize.width / img.sourceSize.height
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

            // SELF-HEAL. The URL only changes when the frame counter does, and
            // the emulator does not tick while the driver spends a minute
            // planning a route -- so a panel that opens during one of those
            // pauses gets a source assignment it has already seen, no load
            // fires, and the box sits blank until the game moves again. It
            // came back on its own, which is exactly the tell.
            //
            // Also covers a failed decode: a load error leaves the buffer
            // empty and nothing else would ever retry it.
            Timer {
              interval: 1500
              repeat: true
              running: screenBox.visible && feed.screenUrl !== ""
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
              // Only before the first frame ever arrives. After that a buffer
              // always holds something, and "Loading..." over a live screen
              // would be a lie.
              visible: !screenBox.everLoaded
              text: "Loading\u2026"
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }

            // SWAPPED BY OPACITY, NOT BY `visible`.
            //
            // `visible: false` lets the scene graph drop the render node, so
            // the buffer coming to the front has to re-upload its texture --
            // and with `asynchronous: true` that lands a frame late. For one
            // compositor frame NEITHER image painted and the box's own
            // `Qt.rgba(0,0,0,0.35)` backing showed through: a dark flash on
            // the game screen, at the publisher's frame rate, which is
            // exactly what kept getting reported after the feed itself
            // measured clean (0 black frames, 0 backward counters, 10.2 Hz,
            // max gap 0.48s).
            //
            // An opacity-0 Image keeps its node and its texture, so the swap
            // is a pure compositor operation with nothing to re-upload. Both
            // stay `visible`, and `z` keeps the live one on top so a
            // mid-load back buffer can never be seen even for a frame.
            Image {
              id: imageA
              anchors.fill: parent
              cache: false
              asynchronous: true
              fillMode: Image.PreserveAspectFit
              // A console framebuffer blown up: nearest-neighbour keeps it
              // looking like the hardware rather than a smeared photograph.
              smooth: false
              mipmap: false
              visible: screenBox.everLoaded
              opacity: screenBox.frontIsA ? 1 : 0
              z: screenBox.frontIsA ? 1 : 0
              onStatusChanged: if (status === Image.Ready) screenBox.present(imageA)
            }

            Image {
              id: imageB
              anchors.fill: parent
              cache: false
              asynchronous: true
              fillMode: Image.PreserveAspectFit
              smooth: false
              mipmap: false
              visible: screenBox.everLoaded
              opacity: screenBox.frontIsA ? 0 : 1
              z: screenBox.frontIsA ? 0 : 1
              onStatusChanged: if (status === Image.Ready) screenBox.present(imageB)
            }

            Component.onCompleted: load()
          }

          // The narration line, and the other half of the "everything below
          // the screen jumps" report. It sits directly under the framebuffer
          // and its content is the game's own message buffer, so it appeared,
          // vanished and changed between one and two lines AT GAME SPEED --
          // relaying out the whole column under the screen several times a
          // second, in lockstep with the frame updating just above it. That
          // is what read as the image flashing.
          //
          // Reserve the space instead: always exactly two lines tall, faded
          // rather than unmounted. Nothing below it can move.
          Text {
            id: messageLine
            opacity: (feed.running && Model.messageLine(feed.state) !== "")
              ? 1 : 0
            width: parent.width
            height: Math.ceil(fontMetrics.height * 2)
            text: Model.messageLine(feed.state)
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
            maximumLineCount: 2
            elide: Text.ElideRight
            verticalAlignment: Text.AlignTop
            FontMetrics {
              id: fontMetrics
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }
          }

          // ---- what is on the other side of the battle --------------------
          // Wrapped in a ReservedSlot: this section only exists during a
          // battle, and letting it collapse moved every section below it
          // twice per encounter.

          ReservedSlot {
          width: parent.width
          Column {
          width: parent.width
          spacing: Style.space(4)

          PanelSeparator {
            visible: opponentColumn.visible
            foreground: root.foreground
          }

          Column {
            id: opponentColumn
            readonly property var foe: Model.enemy(feed.state)

            visible: foe !== null
            width: parent.width
            spacing: Style.space(4)

            PanelSectionHeader {
              text: "OPPONENT"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Row {
              id: foeRow
              width: parent.width
              spacing: Style.space(8)

              Text {
                width: Math.max(0, foeRow.width - foeHp.implicitWidth - foeRow.spacing)
                text: Model.enemyLine(opponentColumn.foe)
                color: root.urgent
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                font.bold: true
                elide: Text.ElideRight
              }

              Text {
                id: foeHp
                text: Model.enemyHp(opponentColumn.foe)
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
              }
            }

            RunBar {
              visible: opponentColumn.foe && opponentColumn.foe.maxHp > 0
              width: parent.width
              fraction: Model.enemyFraction(opponentColumn.foe)
              fill: Model.hpColor(Model.enemyFraction(opponentColumn.foe))
            }
          }

          // ---- party ------------------------------------------------------

          }
          }

          PanelSeparator {
            visible: partyColumn.visible
            foreground: root.foreground
          }

          Column {
            id: partyColumn
            //: One evaluation of the party per snapshot, and a length the
            //: Repeater below can bind to.
            readonly property var rows: Model.party(feed.state)
            visible: rows.length > 0
            width: parent.width
            spacing: Style.space(8)

            PanelSectionHeader {
              text: "PARTY"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            // COUNT MODEL, not the array. A Repeater bound to a JS array
            // destroys and recreates EVERY delegate whenever that array's
            // CONTENT changes -- and `feed.state` is a fresh object on each of
            // the publisher's four writes a second, so the lead's HP ticking
            // rebuilt all six rows, their sprites and their HP bars four times
            // a second. Every rebuilt row is momentarily unsized, which is the
            // pop-in.
            //
            // Measured with qml6: twenty content changes over an array model
            // created 63 delegates; the same twenty over a length model created
            // 3. Binding on the length keeps the delegates alive and lets the
            // bindings inside them update in place.
            Repeater {
              model: partyColumn.rows.length

              MonRow {
                required property int index

                width: partyColumn.width
                mon: partyColumn.rows[index]
                isLead: index === 0
              }
            }
          }

          // ---- dex progress -----------------------------------------------

          PanelSeparator {
            visible: dexColumn.visible
            foreground: root.foreground
          }

          Column {
            id: dexColumn
            readonly property var dex: Model.dex(feed.state)

            visible: dex !== null
            width: parent.width
            spacing: Style.space(4)

            PanelSectionHeader {
              text: "POKEDEX"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            RunBar {
              visible: dexColumn.dex && dexColumn.dex.percent !== null
              width: parent.width
              fraction: dexColumn.dex ? Model.fraction(dexColumn.dex.percent) : 0
              caption: Model.dexCaption(dexColumn.dex)
              captionReserve: root.captionReserve
            }

            // A count with no denominator has no bar to draw: an empty track
            // beside "42" would claim the run is at 0%.
            Text {
              visible: dexColumn.dex && dexColumn.dex.percent === null
              width: parent.width
              text: Model.dexCaption(dexColumn.dex) + " caught"
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }
          }

          // ---- the goal ladder ---------------------------------------------
          // Also battle-scoped, also reserved, for the same reason.

          ReservedSlot {
          width: parent.width
          Column {
          width: parent.width
          spacing: Style.space(4)

          PanelSeparator {
            visible: stagesColumn.visible
            foreground: root.foreground
          }

          Column {
            id: stagesColumn
            readonly property var rows: Model.stages(feed.state)

            visible: rows.length > 0
            width: parent.width
            spacing: Style.space(4)

            PanelSectionHeader {
              text: "STAGES"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            // Length model, for the reason spelled out over the party Repeater:
            // stage percentages move as the run progresses, and an array model
            // rebuilds the whole ladder every time one does.
            Repeater {
              model: stagesColumn.rows.length

              // Stages the run has not reached still draw their track, so the
              // ladder reads as a plan instead of appearing one rung at a time.
              // The stage being worked on is the only one at full strength.
              RunBar {
                required property int index
                readonly property var row: stagesColumn.rows[index]

                width: stagesColumn.width
                fraction: row ? Model.fraction(row.percent) : 0
                caption: row ? Model.stageCaption(row) : ""
                captionReserve: root.captionReserve
                fill: row && row.current ? root.foreground : root.dim
              }
            }
          }

          }
          }

          // ---- team shape -------------------------------------------------

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
            spacing: Style.spacing.labelGap

            PanelSectionHeader {
              text: "TEAM"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            StatGrid {
              width: parent.width
              cells: Model.teamCells(feed.state)
            }

            // Full width, not a grid cell: the list is as long as the number of
            // types the team cannot answer, and eliding it would throw away the
            // only part of it that matters.
            WideRow {
              width: parent.width
              label: "GAPS"
              value: teamColumn.coverage
            }
          }

          // ---- where and how far ------------------------------------------

          PanelSeparator {
            visible: runColumn.visible
            foreground: root.foreground
          }

          Column {
            id: runColumn
            readonly property string where: Model.mapLabel(feed.state)

            visible: runGrid.cells.length > 0 || where !== ""
            width: parent.width
            spacing: Style.spacing.labelGap

            PanelSectionHeader {
              text: "RUN"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            WideRow {
              width: parent.width
              label: "MAP"
              value: runColumn.where
            }

            StatGrid {
              id: runGrid
              width: parent.width
              cells: Model.runCells(feed.state)
            }
          }

          // ---- session counters -------------------------------------------

          PanelSeparator {
            visible: counterGrid.cells.length > 0
            foreground: root.foreground
          }

          Column {
            visible: counterGrid.cells.length > 0
            width: parent.width
            spacing: Style.spacing.labelGap

            PanelSectionHeader {
              text: "COUNTERS"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            StatGrid {
              id: counterGrid
              width: parent.width
              cells: Model.counterCells(feed.state)
            }
          }

          // ---- narration ---------------------------------------------------

          PanelSeparator {
            visible: feed.notes.length > 0
            foreground: root.foreground
          }

          Column {
            visible: feed.notes.length > 0
            width: parent.width
            spacing: Style.space(4)

            PanelSectionHeader {
              text: "PACE"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            // Length model: the estimates are re-derived on every snapshot.
            Repeater {
              id: paceRepeater
              readonly property var rows: Model.paceLines(feed.state)
              model: rows.length

              Row {
                id: paceRow
                required property int index
                readonly property var row: paceRepeater.rows[index]

                width: column.width
                spacing: Style.space(8)

                Text {
                  text: paceRow.row ? paceRow.row.label : ""
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                }
                Text {
                  text: paceRow.row ? paceRow.row.value : ""
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  font.bold: true
                }
              }
            }

            // The basis travels with the numbers. Publishing a fourfold
            // extrapolation without saying so is how a demo becomes a lie.
            Text {
              visible: text !== ""
              width: column.width
              text: Model.paceBasis(feed.state)
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }

            PanelSeparator {}

            PanelSectionHeader {
              text: "NARRATION"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            // Length model: Feed.qml re-parses the tail of the narration log on
            // every read, so the array is new each time even when the last five
            // lines are the same five lines.
            Repeater {
              model: feed.notes.length

              Text {
                required property int index
                readonly property var note: feed.notes[index]

                width: column.width
                // The newest line is the one being read; the rest are context.
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

  // A progress track with an optional caption on its right. Deliberately the
  // same shape and weight as the HP bar, so a glance reads them as the same
  // kind of fact. The caller owns the width.
  // Named RunBar, not ProgressBar: QtQuick.Controls (imported above for its
  // own widgets) exports a ProgressBar too, and an inline component that
  // shadows an imported type is a coin-flip for any reader and for tooling --
  // qmllint resolved the imported one and reported every `fraction:` and
  // `caption:` here as a missing property. The bars were fine at runtime; the
  // name was not.
  component RunBar: Item {
    id: progress

    property real fraction: 0
    property string caption: ""
    property color fill: root.foreground
    // Reserve a caption line's height even with no caption to put in it. For
    // the bars whose caption comes and goes -- the objective's percentage is
    // null between objectives -- so the bar's height, and the position of
    // everything the column puts after it, cannot change.
    property bool reserveCaption: false
    // Width set aside for the caption, shared by every bar that wants to line
    // up with its neighbours. Zero means "size to my own caption", which is
    // right for a lone bar and was wrong for every group of them.
    property real captionReserve: 0

    readonly property real clamped: Math.max(0, Math.min(1, fraction))

    // An invisible Text still reports a full line of implicit height, so the
    // caption only gets to set the height when it is actually shown -- else
    // every bare bar would be as tall as a line of text. The reserved case
    // measures the FONT rather than the Text, so a caption that changes its
    // wording cannot change the bar's height either.
    implicitHeight: Math.max(track.height,
      (reserveCaption || captionText.visible) ? captionMetrics.height : 0)

    FontMetrics {
      id: captionMetrics
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
    }

    Text {
      id: captionText
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      // Right-aligned inside the reserved column, so the captions line up
      // with each other as well as the tracks do.
      width: progress.captionReserve > 0 ? progress.captionReserve
                                         : implicitWidth
      horizontalAlignment: Text.AlignRight
      visible: progress.caption !== ""
      text: progress.caption
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      elide: Text.ElideRight
    }

    Rectangle {
      id: track
      anchors.left: parent.left
      // Always the parent's edge less the caption column. Anchoring to
      // captionText.left made every track's length a function of its own
      // row's text. A reserved column also keeps a caption-LESS bar in a
      // group the same length as its captioned neighbours.
      anchors.right: parent.right
      anchors.rightMargin: progress.captionReserve > 0
        ? progress.captionReserve + Style.space(8)
        : (captionText.visible ? captionText.implicitWidth + Style.space(8) : 0)
      anchors.verticalCenter: parent.verticalCenter
      height: Style.space(4)
      radius: height / 2
      color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.12)

      Rectangle {
        width: Math.round(parent.width * progress.clamped)
        height: parent.height
        radius: parent.radius
        color: progress.fill
      }
    }
  }

  // Label/value cells, two to a row. The model is a list rather than a fixed
  // set of properties precisely so an unreported key produces no cell at all
  // instead of a label with a blank beside it. The caller owns the width.
  component StatGrid: Grid {
    id: statGrid

    property var cells: []

    columns: 2
    columnSpacing: Style.space(12)
    rowSpacing: Style.spacing.labelGap

    // Length model. RUN and COUNTERS both carry a frame counter, so their
    // cells' content changes on every single snapshot -- an array model
    // rebuilt every cell in the grid four times a second.
    Repeater {
      model: statGrid.cells.length

      Pair {
        required property int index
        readonly property var cell: statGrid.cells[index]

        cellWidth: (statGrid.width - statGrid.columnSpacing) / 2
        label: cell ? cell.label : ""
        value: cell ? cell.value : ""
      }
    }
  }

  // One party member. Only the lead carries an HP bar: six bars is a status
  // readout, one bar is something you can glance at.
  component MonRow: Item {
    id: monRow

    property var mon: null
    property bool isLead: false

    readonly property real fraction: Model.hpFraction(mon)
    readonly property bool barVisible: isLead && mon && mon.max_hp > 0

    implicitHeight: nameRow.implicitHeight
      + (barVisible ? hpBar.implicitHeight + Style.space(4) : 0)

    Row {
      id: nameRow
      width: parent.width
      spacing: Style.space(8)
      // The sprite is the tallest thing in the row, so the text sits centred
      // against it rather than the row growing a ragged baseline.
      Image {
        id: monSprite
        // Derived art from the decomp, keyed on palette index 0 and emitted
        // by widget/make_sprites.py. An egg or an unknown species simply has
        // no file, and the row falls back to text alone.
        source: Model.spriteFor(monRow.mon)
        // Geometry from the SOURCE, painting from the status. Sizing the slot
        // on `status === Image.Ready` meant the row's text width changed when
        // the sprite finished decoding, so every row shuffled its own contents
        // as it appeared. A species with no sprite file still collapses the
        // slot to nothing and falls back to text alone.
        visible: status === Image.Ready
        sourceSize.width: spriteSize
        sourceSize.height: spriteSize
        width: source !== "" ? spriteSize : 0
        height: source !== "" ? spriteSize : 0
        fillMode: Image.PreserveAspectFit
        smooth: false          // pixel art: never interpolate
        anchors.verticalCenter: parent.verticalCenter
        readonly property int spriteSize: monRow.isLead ? 28 : 20
      }

      Text {
        width: Math.max(0, nameRow.width - hpText.implicitWidth
                           - monSprite.width - nameRow.spacing * 2)
        text: Model.monLine(monRow.mon)
        color: monRow.mon && monRow.mon.fainted ? root.urgent : root.foreground
        font.family: root.fontFamily
        font.pixelSize: monRow.isLead ? Style.font.body : Style.font.bodySmall
        font.bold: monRow.isLead
        elide: Text.ElideRight
        anchors.verticalCenter: parent.verticalCenter
      }

      Text {
        id: hpText
        text: Model.monHp(monRow.mon)
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: monRow.isLead ? Style.font.body : Style.font.bodySmall
      }
    }

    RunBar {
      id: hpBar
      visible: monRow.barVisible
      anchors.top: nameRow.bottom
      anchors.topMargin: Style.space(4)
      width: parent.width
      fraction: monRow.fraction
      fill: Model.hpColor(monRow.fraction)
    }
  }

  // Label/value cell for the stat grids.
  component Pair: Row {
    id: pair

    property real cellWidth: 0
    property string label: ""
    property string value: ""

    width: cellWidth
    spacing: Style.space(6)

    Text {
      width: Style.space(52)
      text: pair.label
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      font.bold: true
    }

    Text {
      width: Math.max(0, pair.width - Style.space(52) - pair.spacing)
      text: pair.value
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      elide: Text.ElideRight
    }
  }

  // A label/value row that gets the whole panel width and wraps instead of
  // eliding. For values that are a phrase rather than a number -- an interior
  // map name, a list of type gaps -- where half a grid row would cut off
  // exactly the part worth reading. The caller owns the width.
  component WideRow: Row {
    id: wide

    property string label: ""
    property string value: ""

    visible: value !== ""
    spacing: Style.space(6)

    Text {
      width: Style.space(52)
      text: wide.label
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      font.bold: true
    }

    Text {
      width: Math.max(0, wide.width - Style.space(52) - wide.spacing)
      text: wide.value
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      wrapMode: Text.WordWrap
    }
  }
}
