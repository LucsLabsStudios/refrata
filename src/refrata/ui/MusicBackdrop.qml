import QtQuick
import QtQuick.Effects

// Music mode background: the current cover, hugely blurred and slowly breathing, cross-fading
// between tracks. Without artwork, soft colour fields seeded from the title stand in.
Item {
    id: root
    property string art: ""
    property string seed: ""

    function hue(s) {
        var h = 7
        for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 104729
        return (h % 360) / 360
    }
    readonly property real h1: hue(seed || "refrata")
    readonly property color c1: Qt.hsla(h1, 0.78, 0.46, 1)
    readonly property color c2: Qt.hsla((h1 + 0.09) % 1, 0.85, 0.40, 1)
    readonly property color c3: Qt.hsla((h1 + 0.55) % 1, 0.55, 0.30, 1)

    Rectangle { anchors.fill: parent; color: "#08080A" }

    // ---- colour fields: drawn small, upscaled smoothly, then blurred → very soft light
    Item {
        id: fields
        anchors.fill: parent
        opacity: front.status === Image.Ready ? 0 : 1
        Behavior on opacity { NumberAnimation { duration: 700; easing.type: Easing.OutCubic } }
        layer.enabled: true
        layer.textureSize: Qt.size(160, 100)
        layer.smooth: true
        visible: false
        Rectangle { anchors.fill: parent; color: Qt.darker(root.c3, 2.2) }
        Rectangle {
            width: parent.width * 0.7; height: width; radius: width / 2
            color: root.c1
            x: parent.width * (0.08 + 0.05 * Math.sin(drift.t)); y: parent.height * (0.05 + 0.06 * Math.cos(drift.t * 0.8))
            Behavior on color { ColorAnimation { duration: 900; easing.type: Easing.OutCubic } }
        }
        Rectangle {
            width: parent.width * 0.55; height: width; radius: width / 2
            color: root.c2
            x: parent.width * (0.48 + 0.05 * Math.cos(drift.t * 1.1)); y: parent.height * (0.30 + 0.05 * Math.sin(drift.t))
            Behavior on color { ColorAnimation { duration: 900; easing.type: Easing.OutCubic } }
        }
        Rectangle {
            width: parent.width * 0.35; height: width; radius: width / 2
            color: theme.accent
            opacity: 0.55
            x: parent.width * (0.70 + 0.04 * Math.sin(drift.t * 0.7)); y: parent.height * (-0.05 + 0.05 * Math.cos(drift.t * 1.3))
        }
    }
    MultiEffect {
        anchors.fill: parent
        source: fields
        opacity: fields.opacity
        blurEnabled: true; blur: 1.0; blurMax: 64
    }

    // ---- artwork, two layers for the cross-fade
    Item {
        id: artLayer
        anchors.fill: parent
        visible: false
        layer.enabled: true
        layer.textureSize: Qt.size(192, 120)
        layer.smooth: true
        Image {
            id: imgA
            anchors.fill: parent; anchors.margins: -parent.width * 0.12
            fillMode: Image.PreserveAspectCrop; sourceSize.width: 96; asynchronous: true; smooth: true
            opacity: root.frontIsA ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: 900; easing.type: Easing.InOutQuad } }
        }
        Image {
            id: imgB
            anchors.fill: parent; anchors.margins: -parent.width * 0.12
            fillMode: Image.PreserveAspectCrop; sourceSize.width: 96; asynchronous: true; smooth: true
            opacity: root.frontIsA ? 0 : 1
            Behavior on opacity { NumberAnimation { duration: 900; easing.type: Easing.InOutQuad } }
        }
    }
    // Image.source normalises the URL (%20 → space), so track the requested strings ourselves
    property bool frontIsA: true
    property string shown: ""
    property string pending: ""
    readonly property Image front: frontIsA ? imgA : imgB
    readonly property Image back: frontIsA ? imgB : imgA
    function load() {
        if (!art) { imgA.source = ""; imgB.source = ""; shown = ""; pending = ""; return }
        if (art === shown || art === pending) return
        pending = art
        back.source = art
        if (back.status === Image.Ready) promote()
    }
    function promote() {
        shown = pending
        pending = ""
        frontIsA = !frontIsA
    }
    onArtChanged: load()
    Component.onCompleted: load()
    Connections {
        target: imgA
        function onStatusChanged() { if (!root.frontIsA && imgA.status === Image.Ready && root.pending) root.promote() }
    }
    Connections {
        target: imgB
        function onStatusChanged() { if (root.frontIsA && imgB.status === Image.Ready && root.pending) root.promote() }
    }
    MultiEffect {
        anchors.fill: parent
        source: artLayer
        visible: root.front.status === Image.Ready
        blurEnabled: true; blur: 1.0; blurMax: 48
        saturation: 0.35; brightness: -0.08
        scale: 1.06 + 0.04 * Math.sin(drift.t * 0.6)
    }

    // slow drift shared by both layers
    Item {
        id: drift
        property real t: 0
        NumberAnimation on t { from: 0; to: Math.PI * 2; duration: 36000; loops: Animation.Infinite; running: root.visible && root.opacity > 0 && !theme.reduceMotion }
    }

    // vignette: keeps white text readable at the top and around the dock
    Rectangle {
        anchors.fill: parent
        gradient: Gradient {
            GradientStop { position: 0.0; color: Qt.rgba(0, 0, 0, 0.42) }
            GradientStop { position: 0.35; color: Qt.rgba(0, 0, 0, 0.08) }
            GradientStop { position: 0.7; color: Qt.rgba(0, 0, 0, 0.12) }
            GradientStop { position: 1.0; color: Qt.rgba(0, 0, 0, 0.55) }
        }
    }
}
