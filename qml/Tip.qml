import QtQuick
import QtQuick.Window

// Lightweight tooltip next to its parent. Prefers above (or below with `below`), flips when there
// is no room and never leaves the window sideways (small windows like the quick player).
Rectangle {
    id: tip
    property string text: ""
    property bool shown: false
    property bool below: false
    visible: opacity > 0
    opacity: shown ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
    z: 1000
    color: theme.dark ? "#F2F2F4" : "#232220"
    radius: 8
    width: lbl.implicitWidth + 18
    height: 28
    x: (parent ? parent.width : 0) / 2 - width / 2
    y: below ? (parent ? parent.height : 0) + 8 : -height - 8

    function place() {
        if (!parent || !Window.window) return
        var p = parent.mapToItem(null, 0, 0)
        var W = Window.window.width, H = Window.window.height, m = 6
        var up = !below
        if (up && p.y - height - 8 < m) up = false
        else if (!up && p.y + parent.height + 8 + height > H - m) up = true
        y = up ? -height - 8 : parent.height + 8
        var sx = p.x + parent.width / 2 - width / 2
        sx = Math.max(m, Math.min(W - width - m, sx))
        x = sx - p.x
    }
    onShownChanged: if (shown) place()

    Text {
        id: lbl
        anchors.centerIn: parent
        text: tip.text
        color: theme.dark ? "#151517" : "#F7F5F2"
        font.pixelSize: 12
        font.weight: Font.Medium
    }
}
