import QtQuick
import "Utils.js" as U

// Video seek bar: grows on hover, shows a time bubble, drag to scrub.
Item {
    id: root
    property real value: 0      // ms
    property real to: 1         // ms
    property color accent: theme.accent
    property real loopA: -1     // ms; the A-B stretch is shown on the track
    property real loopB: -1
    property var chapters: []   // [{time, name}]: ticks on the track, the name in the bubble
    property bool dragging: ma.pressed
    property bool previewEnabled: false
    property string previewSource: ""
    readonly property real hoverMs: ma.pressed ? root.dragValue : Math.max(0, Math.min(1, ma.mouseX / Math.max(1, root.width))) * root.to
    function chapterAt(ms) {
        var name = ""
        for (var i = 0; i < chapters.length; i++) if (ms >= chapters[i].time) name = chapters[i].name
        return name
    }
    signal seekRequested(real ms)
    signal scrub(real ms)
    signal previewRequested(real ms)

    height: 24
    readonly property real frac: to > 0 ? Math.max(0, Math.min(1, (dragging ? dragValue : value) / to)) : 0
    property real dragValue: 0
    readonly property bool active: ma.containsMouse || ma.pressed

    Rectangle {
        id: track
        anchors.verticalCenter: parent.verticalCenter
        width: parent.width
        height: root.active ? 6 : 4
        radius: height / 2
        color: Qt.rgba(1, 1, 1, 0.22)
        Behavior on height { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
        Rectangle {
            visible: ma.containsMouse && !ma.pressed
            width: Math.max(0, ma.mouseX - 0)
            height: parent.height; radius: parent.radius
            color: Qt.rgba(1, 1, 1, 0.18)
        }
        Rectangle {
            width: parent.width * root.frac
            height: parent.height; radius: parent.radius
            color: root.accent
        }
        Repeater {
            model: root.to > 0 ? root.chapters : []
            Rectangle {
                objectName: "chapterMark_" + index
                visible: modelData.time > 0
                x: parent.width * Math.min(1, modelData.time / root.to) - 1
                width: 2; height: parent.height
                color: Qt.rgba(0, 0, 0, 0.55)
            }
        }
        Rectangle {
            objectName: "loopRange"
            visible: root.loopA >= 0 && root.to > 0
            x: parent.width * Math.min(1, root.loopA / root.to) - 1
            width: root.loopB >= 0 ? Math.max(3, parent.width * (root.loopB - root.loopA) / root.to + 2) : 3
            y: -3; height: parent.height + 6; radius: height / 2
            color: Qt.rgba(1, 1, 1, 0.18)
            border.width: 1; border.color: Qt.rgba(1, 1, 1, 0.55)
            opacity: visible ? 1 : 0
            Behavior on width { NumberAnimation { duration: 260; easing.type: Easing.OutCubic } }
        }
    }
    Rectangle {
        id: knob
        width: 16; height: 16; radius: 8
        color: "white"
        x: track.width * root.frac - width / 2
        anchors.verticalCenter: parent.verticalCenter
        scale: root.active ? 1 : 0
        opacity: root.active ? 1 : 0
        Behavior on scale { NumberAnimation { duration: theme.reduceMotion ? 0 : 120; easing.type: Easing.OutCubic } }
        Behavior on opacity { NumberAnimation { duration: theme.reduceMotion ? 0 : 100; easing.type: Easing.OutCubic } }
    }
    Rectangle {
        id: previewCard
        readonly property bool ready: root.previewEnabled && root.active && root.previewSource !== "" && previewImg.status === Image.Ready
        visible: opacity > 0.01
        opacity: ready ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: theme.reduceMotion ? 0 : 90; easing.type: Easing.OutCubic } }
        width: 176; height: 100; radius: 10
        color: "#0D0D10"
        border.width: 1; border.color: Qt.rgba(1, 1, 1, 0.22)
        y: bubble.y - height - 6
        x: Math.max(0, Math.min(root.width - width, (ma.pressed ? track.width * root.frac : ma.mouseX) - width / 2))
        Image {
            id: previewImg
            anchors.fill: parent; anchors.margins: 2
            source: root.previewEnabled && root.active ? root.previewSource : ""
            fillMode: Image.PreserveAspectCrop
            asynchronous: false
            cache: false
            smooth: true
        }
    }
    Rectangle {
        id: bubble
        visible: ma.containsMouse || ma.pressed
        color: "#F2F2F4"; radius: 8
        width: bt.implicitWidth + 16; height: 26
        y: -height - 6
        x: Math.max(0, Math.min(root.width - width, (ma.pressed ? track.width * root.frac : ma.mouseX) - width / 2))
        Text {
            id: bt
            anchors.centerIn: parent
            readonly property real at: root.hoverMs
            readonly property string chapter: root.chapters.length ? root.chapterAt(at) : ""
            text: U.time(at) + (chapter ? "  ·  " + chapter : "")
            color: "#141416"; font.pixelSize: 12; font.weight: Font.DemiBold
            font.features: { "tnum": 1 }
        }
    }
    MouseArea {
        id: ma
        anchors.fill: parent
        anchors.topMargin: -6; anchors.bottomMargin: -6
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        function msAt(x) { return Math.max(0, Math.min(1, x / Math.max(1, root.width))) * root.to }
        onEntered: if (root.previewEnabled) root.previewRequested(msAt(mouseX))
        onPressed: (m) => {
            var ms = msAt(m.x)
            root.dragValue = ms
            root.scrub(ms)
            scrubPace.restart()
            if (root.previewEnabled) root.previewRequested(ms)
        }
        onPositionChanged: (m) => {
            var ms = msAt(m.x)
            if (pressed) { root.dragValue = ms; scrubPace.pending = true }
            if (root.previewEnabled) root.previewRequested(ms)
        }
        onReleased: (m) => { scrubPace.stop(); scrubPace.pending = false; root.seekRequested(msAt(m.x)) }
        onCanceled: { scrubPace.stop(); scrubPace.pending = false }
    }
    // a mouse reports far more moves than a player can seek: the newest point every 40 ms
    Timer {
        id: scrubPace
        property bool pending: false
        interval: 40; repeat: true
        onTriggered: {
            if (!ma.pressed) { stop(); return }
            if (pending) { pending = false; root.scrub(root.dragValue) }
        }
    }
}
