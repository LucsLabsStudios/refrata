import QtQuick
import QtQuick.Controls
import "Utils.js" as U

ListView {
    id: page
    signal openPlayer()
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    ScrollBar.vertical: ScrollBar {}
    model: player.queue
    spacing: 4

    header: Column {
        width: page.width - 56
        x: 8
        topPadding: 36
        bottomPadding: 24
        spacing: 24
        PageHeader {
            width: parent.width
            eyebrow: qsTr("Reprodução")
            title: qsTr("A seguir")
            subtitle: player.queue.length ? qsTr("%n item(ns) na fila", "", player.queue.length) : qsTr("A fila está vazia")
            IconButton { icon: "shuffle"; tip: qsTr("Aleatório"); checked: player.shuffle; size: 44; iconSize: 15; bg: theme.surface2; onClicked: player.toggleShuffle() }
            IconButton { icon: player.repeat === 2 ? "repeat1" : "repeat"; tip: [qsTr("Repetir: desligado"), qsTr("Repetir: tudo"), qsTr("Repetir: um")][player.repeat]; checked: player.repeat > 0; size: 44; iconSize: 15; bg: theme.surface2; onClicked: player.cycleRepeat() }
            PillButton { objectName: "queueClear"; text: qsTr("Limpar fila"); icon: "delete"; variant: "outline"; h: 44; enabled: player.queue.length > 0; onClicked: player.clearQueue() }
        }
    }

    delegate: Item {
        id: row
        objectName: "queue_" + index
        x: 8
        width: page.width - 56
        height: 72
        readonly property bool current: index === player.currentIndex
        readonly property var info: { library.revision; return library.info(modelData.path) }
        Rectangle {
            anchors.fill: parent; radius: 20
            color: row.current ? theme.accentSoft : (rma.containsMouse ? theme.surface : "transparent")
            Behavior on color { ColorAnimation { duration: 120; easing.type: Easing.OutCubic } }
        }
        readonly property bool hot: rma.containsMouse
        Text {
            x: 18; width: 26; anchors.verticalCenter: parent.verticalCenter
            text: index + 1
            opacity: !row.current && !row.hot ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
            color: theme.textFaint; font.pixelSize: 14; horizontalAlignment: Text.AlignRight
        }
        EqualizerBars {
            x: 26; anchors.verticalCenter: parent.verticalCenter
            opacity: row.current && !row.hot ? 1 : 0
            visible: opacity > 0.01
            Behavior on opacity { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
            running: player.playing && row.current
        }
        Rectangle {
            x: 14; width: 34; height: 34; radius: 17
            anchors.verticalCenter: parent.verticalCenter
            color: row.current ? theme.accent : (theme.dark ? Qt.rgba(1, 1, 1, 0.12) : Qt.rgba(0, 0, 0, 0.07))
            opacity: row.hot ? 1 : 0
            scale: row.hot ? (rma.pressed ? 0.88 : 1) : 0.7
            visible: opacity > 0.01
            Behavior on opacity { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }
            Behavior on scale { NumberAnimation { duration: 200; easing.type: Easing.OutBack } }
            PlayGlyph {
                anchors.centerIn: parent
                readonly property bool pauseIcon: row.current && player.playing
                anchors.horizontalCenterOffset: pauseIcon ? 0 : 1.5
                size: 13; playing: pauseIcon
                color: row.current ? theme.onAccent : theme.text
            }
        }
        Rectangle {
            id: th
            x: 58; anchors.verticalCenter: parent.verticalCenter
            height: 52; width: modelData.audio ? 52 : 92; radius: 12
            color: theme.surface2
            Icon { anchors.centerIn: parent; name: modelData.isUrl ? "globe" : modelData.audio ? "music" : "film"; size: 16; color: theme.textFaint; visible: ti.status !== Image.Ready }
            RoundedImage { id: ti; anchors.fill: parent; radius: 12; source: row.info.thumb || ""; sourceWidth: 200 }
        }
        Column {
            anchors.left: th.right; anchors.leftMargin: 16
            anchors.right: acts.left; anchors.rightMargin: 16
            anchors.verticalCenter: parent.verticalCenter
            spacing: 3
            Text { width: parent.width; elide: Text.ElideRight; text: modelData.title; color: row.current ? theme.accent : theme.text; font.pixelSize: 15; font.weight: Font.DemiBold }
            Text {
                width: parent.width; elide: Text.ElideMiddle
                text: (row.info.duration ? U.time(row.info.duration) + "  ·  " : "") + modelData.path
                color: theme.textDim; font.pixelSize: 12
            }
        }
        Row {
            id: acts
            anchors.right: parent.right; anchors.rightMargin: 12
            anchors.verticalCenter: parent.verticalCenter
            spacing: 4
            opacity: hh.hovered ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
            IconButton { icon: "up"; size: 36; iconSize: 12; enabled: index > 0; onClicked: player.moveItem(index, index - 1) }
            IconButton { icon: "down"; size: 36; iconSize: 12; enabled: index < player.queue.length - 1; onClicked: player.moveItem(index, index + 1) }
            IconButton { objectName: "queueRemove_" + index; icon: "close"; size: 36; iconSize: 12; onClicked: player.removeAt(index) }
        }
        MouseArea {
            id: rma
            anchors.fill: parent
            anchors.rightMargin: 130
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            // one click plays (or pauses/resumes the current item); a double click also opens the player
            onClicked: {
                if (row.current) { player.togglePlay(); return }
                player.playAt(index)
                if (!modelData.audio) page.openPlayer()   // same as the library: videos open the player
            }
            onDoubleClicked: { if (row.current && player.state === "paused") player.togglePlay(); page.openPlayer() }
        }
        HoverHandler { id: hh }
    }

    Column {
        visible: player.queue.length === 0
        anchors.centerIn: parent
        spacing: 16
        Icon { anchors.horizontalCenter: parent.horizontalCenter; name: "playlist"; size: 56; color: theme.textFaint }
        Text { anchors.horizontalCenter: parent.horizontalCenter; text: qsTr("Nada na fila"); color: theme.text; font.pixelSize: 22; font.weight: Font.DemiBold }
        Text { anchors.horizontalCenter: parent.horizontalCenter; text: qsTr("Clique com o botão direito em um item e escolha “Adicionar à fila”."); color: theme.textDim; font.pixelSize: 14 }
    }
}
