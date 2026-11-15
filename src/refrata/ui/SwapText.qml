import QtQuick

// Text that cross-fades when its value changes (track titles, artists): the old line slips
// out, the new one slides in, so nothing pops on a track change.
Text {
    id: root
    property string value: ""
    property real shift: 6
    text: value
    property real dx: 0
    transform: Translate { x: root.dx }

    property bool _ready: false
    Component.onCompleted: _ready = true
    onValueChanged: {
        if (!_ready || theme.reduceMotion || !visible) { swap.stop(); text = value; opacity = 1; dx = 0; return }
        swap.restart()
    }
    SequentialAnimation {
        id: swap
        ParallelAnimation {
            NumberAnimation { target: root; property: "opacity"; to: 0; duration: 95; easing.type: Easing.OutCubic }
            NumberAnimation { target: root; property: "dx"; to: -root.shift; duration: 95; easing.type: Easing.OutCubic }
        }
        // read the value when the swap happens (a PropertyAction could keep a stale one when the
        // value changes twice in a row, e.g. fast track skips or a list set before its index)
        ScriptAction { script: root.text = root.value }
        PropertyAction { target: root; property: "dx"; value: root.shift }
        ParallelAnimation {
            NumberAnimation { target: root; property: "opacity"; to: 1; duration: 260; easing.type: Easing.OutCubic }
            NumberAnimation { target: root; property: "dx"; to: 0; duration: 320; easing.type: Easing.OutCubic }
        }
    }
}
