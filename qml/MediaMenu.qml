import QtQuick

// Floating menu for a media path; actions handled here.
Item {
    id: root
    anchors.fill: parent
    visible: false
    z: 900
    property string path: ""
    property var actions: [
        { key: "play", text: "Reproduzir", icon: "play" },
        { key: "next", text: "Tocar em seguida", icon: "next" },
        { key: "queue", text: "Adicionar à fila", icon: "add" },
        { key: "reveal", text: "Mostrar no Explorer", icon: "folder" },
    ]
    signal triggered(string key, string path)

    function openAt(path, x, y) {
        root.path = path
        visible = true
        card.x = Math.min(x, root.width - card.width - 12)
        card.y = Math.min(y, root.height - card.height - 12)
        card.opacity = 0; card.scale = 0.94
        appear.restart()
    }
    function close() { visible = false }

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.AllButtons
        onPressed: root.close()
        onWheel: root.close()
    }
    Rectangle {
        id: card
        width: 240
        height: col.implicitHeight + 12
        radius: 18
        color: theme.dark ? "#22232A" : "#FFFFFF"
        border.width: 1
        border.color: theme.strokeStrong
        transformOrigin: Item.TopLeft
        ParallelAnimation {
            id: appear
            NumberAnimation { target: card; property: "opacity"; to: 1; duration: 140; easing.type: Easing.OutCubic }
            NumberAnimation { target: card; property: "scale"; to: 1; duration: 200; easing.type: Easing.OutCubic }
        }
        Column {
            id: col
            x: 6; y: 6
            width: parent.width - 12
            Repeater {
                model: root.actions
                Rectangle {
                    width: col.width; height: 40; radius: 12
                    color: ia.containsMouse ? (theme.dark ? Qt.rgba(1, 1, 1, 0.07) : Qt.rgba(0, 0, 0, 0.05)) : "transparent"
                    Row {
                        x: 12; spacing: 12
                        anchors.verticalCenter: parent.verticalCenter
                        Icon { name: modelData.icon; size: 15; color: theme.textDim; anchors.verticalCenter: parent.verticalCenter }
                        Text { text: modelData.text; color: theme.text; font.pixelSize: 14; anchors.verticalCenter: parent.verticalCenter }
                    }
                    MouseArea {
                        id: ia; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                        onClicked: { root.close(); root.triggered(modelData.key, root.path) }
                    }
                }
            }
        }
    }
}
