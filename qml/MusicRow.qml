import QtQuick
import "Utils.js" as U

Item {
    id: root
    property int number: 0
    property string title: ""
    property string artist: ""
    property string album: ""
    property string thumb: ""
    property real duration: 0
    property bool current: false
    property bool playing: false
    signal play()
    signal toggle()
    signal menu(real x, real y)

    height: 64

    Rectangle {
        anchors.fill: parent
        radius: 16
        color: root.current ? theme.accentSoft : (ma.containsMouse ? (theme.dark ? Qt.rgba(1, 1, 1, 0.05) : Qt.rgba(0, 0, 0, 0.04)) : "transparent")
        Behavior on color { ColorAnimation { duration: 120 } }
    }
    // leading slot: number → (hover) play; current track: equalizer → (hover) pause/resume
    readonly property bool hot: ma.containsMouse
    Text {
        id: num
        x: 16; width: 28
        anchors.verticalCenter: parent.verticalCenter
        text: root.number
        opacity: !root.hot && !root.current ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 120 } }
        color: theme.textFaint; font.pixelSize: 14
        horizontalAlignment: Text.AlignRight
        font.features: { "tnum": 1 }
    }
    EqualizerBars {
        x: 22
        anchors.verticalCenter: parent.verticalCenter
        opacity: root.current && !root.hot ? 1 : 0
        visible: opacity > 0.01
        Behavior on opacity { NumberAnimation { duration: 120 } }
        running: root.playing && root.current
    }
    Rectangle {
        id: playBtn
        objectName: "rowPlay"
        x: 14; width: 34; height: 34; radius: 17
        anchors.verticalCenter: parent.verticalCenter
        color: root.current ? theme.accent : (theme.dark ? Qt.rgba(1, 1, 1, 0.12) : Qt.rgba(0, 0, 0, 0.07))
        opacity: root.hot ? 1 : 0
        scale: root.hot ? (ma.pressed ? 0.88 : 1) : 0.7
        visible: opacity > 0.01
        Behavior on opacity { NumberAnimation { duration: 140 } }
        Behavior on scale { NumberAnimation { duration: 200; easing.type: Easing.OutBack } }
        PlayGlyph {
            anchors.centerIn: parent
            readonly property bool pauseIcon: root.current && root.playing
            anchors.horizontalCenterOffset: pauseIcon ? 0 : 1.5
            size: 13
            playing: pauseIcon
            color: root.current ? theme.onAccent : theme.text
        }
    }
    Rectangle {
        id: art
        x: 58; width: 44; height: 44; radius: 10
        anchors.verticalCenter: parent.verticalCenter
        color: theme.surface2
        Icon { anchors.centerIn: parent; name: "music"; size: 18; color: theme.textFaint; visible: ri.status !== Image.Ready }
        RoundedImage { id: ri; anchors.fill: parent; radius: 10; source: root.thumb; sourceWidth: 120 }
    }
    Column {
        anchors.left: art.right; anchors.leftMargin: 14
        anchors.right: albumTxt.left; anchors.rightMargin: 16
        anchors.verticalCenter: parent.verticalCenter
        spacing: 2
        Text {
            width: parent.width; elide: Text.ElideRight
            text: root.title
            color: root.current ? theme.accent : theme.text
            font.pixelSize: 15; font.weight: Font.DemiBold
        }
        Text {
            width: parent.width; elide: Text.ElideRight
            text: root.artist || "Artista desconhecido"
            color: theme.textDim; font.pixelSize: 13
        }
    }
    Text {
        id: albumTxt
        anchors.right: durTxt.left; anchors.rightMargin: 24
        anchors.verticalCenter: parent.verticalCenter
        width: Math.min(260, root.width * 0.28)
        elide: Text.ElideRight
        text: root.album
        color: theme.textDim; font.pixelSize: 13
    }
    Text {
        id: durTxt
        anchors.right: parent.right; anchors.rightMargin: 20
        anchors.verticalCenter: parent.verticalCenter
        text: root.duration > 0 ? U.time(root.duration) : ""
        color: theme.textDim; font.pixelSize: 13
        font.features: { "tnum": 1 }
    }
    MouseArea {
        id: ma
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        cursorShape: Qt.PointingHandCursor
        // one click plays; on the current track it pauses/resumes. A double click is the
        // first click plus a no-op second one (it only makes sure the track is playing).
        onClicked: (m) => {
            if (m.button === Qt.RightButton) {
                var p = mapToItem(null, m.x, m.y); root.menu(p.x, p.y)
            } else if (root.current) root.toggle()
            else root.play()
        }
        onDoubleClicked: (m) => { if (m.button === Qt.LeftButton && root.current && player.state === "paused") root.toggle() }
    }
}
