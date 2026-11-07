import QtQuick

// Segmented pill control (One UI / iOS style) with sliding indicator.
Rectangle {
    id: root
    property var options: []          // [{text, value}]
    property var value
    property bool onMedia: false
    signal picked(var value)
    height: 40
    radius: height / 2
    width: row.width + 8
    color: onMedia ? Qt.rgba(1, 1, 1, 0.08) : theme.surface2
    readonly property int idx: {
        for (var i = 0; i < options.length; i++) if (options[i].value === value) return i
        return -1
    }
    Rectangle {
        id: ind
        visible: root.idx >= 0
        y: 4; height: root.height - 8; radius: height / 2
        color: root.onMedia ? "white" : theme.inverse
        // rep.count dependency makes these re-evaluate once delegates exist
        readonly property Item target: rep.count > 0 && root.idx >= 0 ? rep.itemAt(root.idx) : null
        x: target ? target.x + 4 : 4
        width: target ? target.width : 0
        Behavior on x { NumberAnimation { duration: 240; easing.type: Easing.OutCubic } }
        Behavior on width { NumberAnimation { duration: 240; easing.type: Easing.OutCubic } }
    }
    Row {
        id: row
        x: 4
        anchors.verticalCenter: parent.verticalCenter
        Repeater {
            id: rep
            model: root.options
            Item {
                objectName: "seg_" + modelData.value
                width: t.implicitWidth + 32
                height: root.height - 8
                Text {
                    id: t
                    anchors.centerIn: parent
                    text: modelData.text
                    font.pixelSize: 13; font.weight: Font.DemiBold
                    color: index === root.idx ? (root.onMedia ? "#111" : theme.inverseText) : (root.onMedia ? "white" : theme.textDim)
                    Behavior on color { ColorAnimation { duration: 160; easing.type: Easing.OutCubic } }
                }
                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.picked(modelData.value) }
            }
        }
    }
}
