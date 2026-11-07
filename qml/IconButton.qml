import QtQuick

// Round icon button with hover/press feedback and an optional signature micro-animation
// played on click (`motion`): nudgeLeft | nudgeRight | spin | pop | wiggle | drop | lift | pulse.
Item {
    id: root
    property string icon: ""
    property real iconSize: 18
    property real size: 40
    property bool checked: false
    property bool onMedia: false          // white-on-video styling
    property color fg: onMedia ? "white" : theme.text
    property color hoverBg: onMedia ? Qt.rgba(1, 1, 1, 0.14) : (theme.dark ? Qt.rgba(1, 1, 1, 0.08) : Qt.rgba(0, 0, 0, 0.06))
    property color bg: "transparent"
    property string tip: ""
    // each icon has its own signature gesture; set `motion` to override (or "none")
    property string motion: ({
        "prev": "nudgeLeft", "next": "nudgeRight", "left": "nudgeLeft", "right": "nudgeRight",
        "repeat": "spin", "repeat1": "spin", "refresh": "spin", "sync": "spin", "speed": "spin",
        "shuffle": "pop", "eq": "pop", "camera": "pop", "fullscreen": "pop", "exitfull": "pop",
        "aspect": "pop", "cc": "pop", "subtitle": "pop", "add": "pop", "apps": "pop",
        "timer": "wiggle", "delete": "wiggle", "close": "wiggle",
        "mute": "pulse", "volume": "pulse", "vol1": "pulse", "vol2": "pulse", "vol3": "pulse",
        "down": "drop", "folder": "drop", "explorer": "drop", "up": "lift",
        "queue": "nudgeRight", "list": "nudgeRight", "playlist": "nudgeRight"
    })[icon] || ""
    property alias hovered: ma.containsMouse
    signal clicked()

    width: size; height: size
    opacity: enabled ? 1 : 0.35
    Behavior on opacity { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }

    // micro-animation channels, all reset to rest values
    property real kx: 0
    property real ky: 0
    property real krot: 0
    property real kscale: 1

    function play() {
        if (theme.reduceMotion) return
        switch (motion) {
        case "nudgeLeft": nudge.dir = -1; nudge.restart(); break
        case "nudgeRight": nudge.dir = 1; nudge.restart(); break
        case "spin": spin.restart(); break
        case "pop": pop.restart(); break
        case "wiggle": wiggle.restart(); break
        case "drop": drop.dir = 1; drop.restart(); break
        case "lift": drop.dir = -1; drop.restart(); break
        case "pulse": pulse.restart(); break
        }
    }
    // toggles (shuffle, repeat, panels) acknowledge the new state with a soft pop
    onCheckedChanged: if (!theme.reduceMotion && root.visible) pop.restart()

    Rectangle {
        anchors.fill: parent
        radius: width / 2
        color: root.checked ? theme.accentSoft : (ma.containsMouse ? root.hoverBg : root.bg)
        scale: ma.pressed ? 0.94 : 1
        Behavior on color { ColorAnimation { duration: 160; easing.type: Easing.OutCubic } }
        Behavior on scale { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }
    }
    Icon {
        id: glyph
        anchors.centerIn: parent
        anchors.horizontalCenterOffset: root.kx
        anchors.verticalCenterOffset: root.ky
        name: root.icon
        size: root.iconSize
        color: root.checked ? theme.accent : root.fg
        Behavior on color { ColorAnimation { duration: 180; easing.type: Easing.OutCubic } }
        rotation: root.krot
        property real press: ma.pressed ? 0.84 : (ma.containsMouse ? 1.08 : 1)
        Behavior on press { NumberAnimation { duration: 180; easing.type: Easing.OutBack; easing.overshoot: 2.2 } }
        scale: press * root.kscale
    }

    SequentialAnimation {
        id: nudge
        property int dir: 1
        NumberAnimation { target: root; property: "kx"; to: 6 * nudge.dir; duration: 90; easing.type: Easing.OutQuad }
        NumberAnimation { target: root; property: "kx"; to: 0; duration: 320; easing.type: Easing.OutBack; easing.overshoot: 2.4 }
    }
    NumberAnimation { id: spin; target: root; property: "krot"; from: 0; to: 360; duration: 560; easing.type: Easing.OutCubic }
    SequentialAnimation {
        id: pop
        NumberAnimation { target: root; property: "kscale"; to: 1.22; duration: 110; easing.type: Easing.OutQuad }
        NumberAnimation { target: root; property: "kscale"; to: 1; duration: 340; easing.type: Easing.OutBack; easing.overshoot: 2.6 }
    }
    SequentialAnimation {
        id: wiggle
        NumberAnimation { target: root; property: "krot"; to: -16; duration: 80; easing.type: Easing.OutQuad }
        NumberAnimation { target: root; property: "krot"; to: 12; duration: 120; easing.type: Easing.InOutQuad }
        NumberAnimation { target: root; property: "krot"; to: -6; duration: 110; easing.type: Easing.InOutQuad }
        NumberAnimation { target: root; property: "krot"; to: 0; duration: 160; easing.type: Easing.OutCubic }
    }
    SequentialAnimation {
        id: drop
        property int dir: 1
        NumberAnimation { target: root; property: "ky"; to: 5 * drop.dir; duration: 110; easing.type: Easing.OutQuad }
        NumberAnimation { target: root; property: "ky"; to: 0; duration: 300; easing.type: Easing.OutBack; easing.overshoot: 2 }
    }
    SequentialAnimation {
        id: pulse
        NumberAnimation { target: root; property: "kscale"; to: 0.82; duration: 90; easing.type: Easing.OutQuad }
        NumberAnimation { target: root; property: "kscale"; to: 1; duration: 380; easing.type: Easing.OutElastic; easing.amplitude: 1.1; easing.period: 0.45 }
    }

    MouseArea {
        id: ma
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: { root.play(); root.clicked() }
    }
    Tip { text: root.tip; shown: ma.containsMouse && root.tip !== "" }
}
