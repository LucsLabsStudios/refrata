import QtQuick

// Compact horizontal slider (volume, equalizer, adjustments).
Item {
    id: root
    property real from: 0
    property real to: 100
    property real value: 0
    property real stepSize: 0
    property color accent: theme.accent
    property color trackColor: onMedia ? Qt.rgba(1, 1, 1, 0.22) : theme.surface3
    property bool onMedia: false
    property bool vertical: false
    property real center: from        // fill starts here (e.g. 0 for -20..20)
    // optional tick (volume: 100 %): the fill past it takes boostColor, and dragging near it sticks
    property real mark: -1
    property color boostColor: theme.accent
    readonly property bool hasMark: mark > from && mark < to
    readonly property real mfrac: (mark - from) / (to - from)
    signal moved(real value)

    implicitWidth: vertical ? 28 : 120
    implicitHeight: vertical ? 120 : 28

    readonly property real frac: (value - from) / (to - from)
    readonly property real cfrac: (center - from) / (to - from)
    readonly property real len: vertical ? height : width

    Rectangle {
        id: track
        x: root.vertical ? (root.width - width) / 2 : 0
        y: root.vertical ? 0 : (root.height - height) / 2
        width: root.vertical ? 5 : root.width
        height: root.vertical ? root.height : 5
        radius: 2.5
        color: root.trackColor
        Rectangle {
            readonly property real a: Math.min(root.frac, root.cfrac)
            readonly property real b: root.hasMark ? Math.min(Math.max(root.frac, root.cfrac), root.mfrac)
                                                   : Math.max(root.frac, root.cfrac)
            color: root.accent
            radius: 2.5
            x: root.vertical ? 0 : a * root.len
            width: root.vertical ? parent.width : Math.max(0, b - a) * root.len
            y: root.vertical ? (1 - b) * root.len : 0
            height: root.vertical ? Math.max(0, b - a) * root.len : parent.height
        }
        Rectangle {
            objectName: "boostFill"
            visible: root.hasMark && root.frac > root.mfrac
            readonly property real a: root.mfrac
            readonly property real b: root.frac
            color: root.boostColor
            radius: 2.5
            x: root.vertical ? 0 : a * root.len
            width: root.vertical ? parent.width : Math.max(0, b - a) * root.len
            y: root.vertical ? (1 - b) * root.len : 0
            height: root.vertical ? Math.max(0, b - a) * root.len : parent.height
        }
    }
    Rectangle {
        objectName: "sliderMark"
        visible: root.hasMark
        width: root.vertical ? 11 : 2
        height: root.vertical ? 2 : 11
        radius: 1
        color: root.onMedia ? Qt.rgba(1, 1, 1, 0.7) : theme.strokeStrong
        x: root.vertical ? (root.width - width) / 2 : root.mfrac * root.len - width / 2
        y: root.vertical ? (1 - root.mfrac) * root.len - height / 2 : (root.height - height) / 2
    }
    Rectangle {
        width: ma.containsMouse || ma.pressed ? 16 : 12; height: width; radius: width / 2
        color: "white"
        border.width: theme.dark || root.onMedia ? 0 : 1
        border.color: theme.strokeStrong
        x: root.vertical ? (root.width - width) / 2 : root.frac * root.len - width / 2
        y: root.vertical ? (1 - root.frac) * root.len - height / 2 : (root.height - height) / 2
        Behavior on width { NumberAnimation { duration: 100 } }
    }
    MouseArea {
        id: ma
        anchors.fill: parent
        anchors.margins: -4
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        preventStealing: true
        function upd(m) {
            var f = root.vertical ? 1 - (m.y - 4) / root.len : (m.x - 4) / root.len
            f = Math.max(0, Math.min(1, f))
            var v = root.from + f * (root.to - root.from)
            if (root.stepSize > 0) v = Math.round(v / root.stepSize) * root.stepSize
            if (root.hasMark && Math.abs(v - root.mark) <= (root.to - root.from) * 0.025) v = root.mark
            root.moved(v)
        }
        onPressed: (m) => upd(m)
        onPositionChanged: (m) => { if (pressed) upd(m) }
        onWheel: (w) => {
            var step = root.stepSize > 0 ? root.stepSize : (root.to - root.from) / 20
            var v = root.value + (w.angleDelta.y > 0 ? step : -step)
            root.moved(Math.max(root.from, Math.min(root.to, v)))
        }
    }
}
