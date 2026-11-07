import QtQuick

Item {
    id: root
    property bool checked: false
    signal toggled(bool on)
    width: 52; height: 30
    Rectangle {
        anchors.fill: parent; radius: height / 2
        color: root.checked ? theme.accent : theme.surface3
        Behavior on color { ColorAnimation { duration: 160 } }
    }
    Rectangle {
        width: 24; height: 24; radius: 12
        y: 3
        x: root.checked ? root.width - width - 3 : 3
        color: "white"
        Behavior on x { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
    }
    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.toggled(!root.checked) }
}
