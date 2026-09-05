import QtQuick

// A layout slot that never gives its space back.
//
// Sections that only exist during a battle -- OPPONENT, STAGES -- used to be
// plain Columns with `visible: false` when idle, and an invisible child is
// skipped by Column's layout entirely. So every battle start and end resized
// the panel and shoved PARTY, DEX, TEAM, RUN and the notes up or down. That is
// the "everything underneath the screen image flickers" report: not a dropped
// frame, a reflow several times a minute.
//
// The slot measures its content and keeps a high-water mark. Once a section
// has been seen at a given height, the space stays reserved for the rest of
// the session, so appearing and disappearing costs no movement anywhere else.
// It starts at zero, so a panel that has never been in a battle shows no
// blank gap either.
Item {
    id: slot

    // Anything declared inside this element becomes content of the holder.
    default property alias slotData: holder.data

    // Tallest this slot's content has ever been. Only ever grows.
    property real reserved: 0

    // Set false to let the slot shrink again (used by tests).
    property bool sticky: true

    implicitHeight: Math.max(holder.childrenRect.height, reserved)
    height: implicitHeight

    Item {
        id: holder
        width: slot.width
        height: childrenRect.height

        onHeightChanged: {
            if (slot.sticky && height > slot.reserved)
                slot.reserved = height;
        }
    }
}
