import QtQuick

// Lightweight tooltip shown above its parent.
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
    anchors.horizontalCenter: parent.horizontalCenter
    y: below ? parent.height + 8 : -height - 8
    Text {
        id: lbl
        anchors.centerIn: parent
        text: tip.text
        color: theme.dark ? "#151517" : "#F7F5F2"
        font.pixelSize: 12
        font.weight: Font.Medium
    }
}
