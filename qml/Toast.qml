import QtQuick

Rectangle {
    id: root
    property string text: ""
    function show(t) { text = t; shown = true; hide.restart() }
    property bool shown: false
    z: 2000
    height: 44; radius: 22
    width: lbl.implicitWidth + 48
    color: theme.dark ? "#F2F2F4" : "#232220"
    anchors.horizontalCenter: parent.horizontalCenter
    y: shown ? parent.height - height - 112 : parent.height + 10
    opacity: shown ? 1 : 0
    Behavior on y { NumberAnimation { duration: 320; easing.type: Easing.OutCubic } }
    Behavior on opacity { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
    Timer { id: hide; interval: 2600; onTriggered: root.shown = false }
    Text {
        id: lbl
        anchors.centerIn: parent
        text: root.text
        color: theme.dark ? "#141416" : "#F7F5F2"
        font.pixelSize: 14; font.weight: Font.DemiBold
    }
}
