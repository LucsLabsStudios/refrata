import QtQuick

// Round icon button with hover/press feedback.
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
    property alias hovered: ma.containsMouse
    signal clicked()

    width: size; height: size
    opacity: enabled ? 1 : 0.35

    Rectangle {
        anchors.fill: parent
        radius: width / 2
        color: root.checked ? theme.accentSoft : (ma.containsMouse ? root.hoverBg : root.bg)
        Behavior on color { ColorAnimation { duration: 140 } }
    }
    Icon {
        anchors.centerIn: parent
        name: root.icon
        size: root.iconSize
        color: root.checked ? theme.accent : root.fg
        scale: ma.pressed ? 0.86 : 1
        Behavior on scale { NumberAnimation { duration: 120; easing.type: Easing.OutBack } }
    }
    MouseArea {
        id: ma
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: root.clicked()
    }
    Tip { text: root.tip; shown: ma.containsMouse && root.tip !== "" }
}
