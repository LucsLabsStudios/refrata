import QtQuick

Rectangle {
    id: root
    property alias text: input.text
    property string placeholder: qsTr("Buscar")
    signal accepted()
    width: 280; height: 44; radius: 22
    color: theme.surface2
    border.width: input.activeFocus ? 2 : 1
    border.color: input.activeFocus ? theme.accent : theme.stroke
    Behavior on border.color { ColorAnimation { duration: 140; easing.type: Easing.OutCubic } }

    function focusField() { input.forceActiveFocus(); input.selectAll() }

    Icon { id: ic; x: 16; anchors.verticalCenter: parent.verticalCenter; name: "search"; size: 15; color: theme.textDim }
    TextInput {
        id: input
        anchors.left: ic.right; anchors.leftMargin: 10
        anchors.right: clr.left; anchors.rightMargin: 6
        anchors.verticalCenter: parent.verticalCenter
        color: theme.text
        font.pixelSize: 14
        selectByMouse: true
        selectionColor: theme.accent
        clip: true
        onAccepted: root.accepted()
        Keys.onEscapePressed: { text = ""; focus = false }
        Text {
            anchors.verticalCenter: parent.verticalCenter
            visible: !input.text
            text: root.placeholder
            color: theme.textFaint
            font.pixelSize: 14
        }
    }
    IconButton {
        id: clr
        anchors.right: parent.right; anchors.rightMargin: 6
        anchors.verticalCenter: parent.verticalCenter
        size: 32; iconSize: 11; icon: "close"
        visible: input.text !== ""
        onClicked: input.text = ""
    }
}
