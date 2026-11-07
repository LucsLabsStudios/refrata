import QtQuick

// Floating pill navigation rail.
Item {
    id: root
    property int current: 0
    signal navigate(int index)
    signal openFiles()
    signal aboutRequested()
    width: 96

    readonly property var items: [
        { icon: "home", tip: "Início" },
        { icon: "video", tip: "Vídeos" },
        { icon: "music", tip: "Músicas" },
        { icon: "queue", tip: "A seguir" },
        { icon: "globe", tip: "Streams de rede" },
    ]

    // logo (LucsLabs prism) — click opens the About card
    Image {
        id: logo
        objectName: "brandLogo"
        width: 52; height: 52
        anchors.horizontalCenter: parent.horizontalCenter
        y: 20
        source: assetsUrl + "brand/icon-256.png"
        sourceSize: Qt.size(104, 104)
        smooth: true; mipmap: true
        scale: lma.pressed ? 0.92 : (lma.containsMouse ? 1.06 : 1)
        Behavior on scale { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
        MouseArea { id: lma; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.aboutRequested() }
        Tip { text: "Refrata · LucsLabs Studios"; shown: lma.containsMouse; anchors.horizontalCenter: undefined; x: parent.width + 14; y: (parent.height - height) / 2 }
    }

    Rectangle {
        id: pill
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.verticalCenter: parent.verticalCenter
        width: 64
        height: col.height + 20
        radius: 32
        color: theme.surface
        border.width: 1
        border.color: theme.stroke

        Rectangle {
            id: sel
            width: 48; height: 48; radius: 24
            x: 8
            y: 10 + root.current * (48 + 8)
            visible: root.current >= 0 && root.current < root.items.length
            color: theme.inverse
            Behavior on y { NumberAnimation { duration: 280; easing.type: Easing.OutCubic } }
        }
        Column {
            id: col
            x: 8; y: 10
            spacing: 8
            Repeater {
                model: root.items
                Item {
                    objectName: "nav" + index
                    width: 48; height: 48
                    Rectangle {
                        anchors.fill: parent; radius: 24
                        color: ma.containsMouse && root.current !== index ? (theme.dark ? Qt.rgba(1, 1, 1, 0.06) : Qt.rgba(0, 0, 0, 0.05)) : "transparent"
                    }
                    Icon {
                        anchors.centerIn: parent
                        name: modelData.icon
                        size: 18
                        color: root.current === index ? theme.inverseText : theme.textDim
                        Behavior on color { ColorAnimation { duration: 200 } }
                    }
                    MouseArea { id: ma; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.navigate(index) }
                    Tip { text: modelData.tip; shown: ma.containsMouse; anchors.horizontalCenter: undefined; x: parent.width + 14; y: (parent.height - height) / 2 }
                }
            }
        }
    }

    Column {
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 22
        spacing: 10
        Item {
            objectName: "navOpen"
            width: 48; height: 48
            Rectangle { anchors.fill: parent; radius: 24; color: theme.accent; scale: oma.pressed ? 0.92 : 1; Behavior on scale { NumberAnimation { duration: 100 } } }
            Icon { anchors.centerIn: parent; name: "add"; size: 18; color: theme.onAccent }
            MouseArea { id: oma; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.openFiles() }
            Tip { text: "Abrir arquivos"; shown: oma.containsMouse; anchors.horizontalCenter: undefined; x: parent.width + 14; y: (parent.height - height) / 2 }
        }
        Item {
            objectName: "nav5"
            width: 48; height: 48
            Rectangle { anchors.fill: parent; radius: 24; color: root.current === 5 ? theme.inverse : (sma.containsMouse ? theme.surface2 : theme.surface); border.width: 1; border.color: theme.stroke }
            Icon { anchors.centerIn: parent; name: "settings"; size: 18; color: root.current === 5 ? theme.inverseText : theme.textDim }
            MouseArea { id: sma; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.navigate(5) }
            Tip { text: "Ajustes"; shown: sma.containsMouse; anchors.horizontalCenter: undefined; x: parent.width + 14; y: (parent.height - height) / 2 }
        }
    }
}
