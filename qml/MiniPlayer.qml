import QtQuick
import QtQuick.Effects
import QtMultimedia
import "Utils.js" as U

// Floating "now playing" bar with a live video preview.
Item {
    id: root
    signal expand()
    height: 80
    SmoothTime { id: clock; active: root.visible }
    // the library already has the tags; player.title is the file name until libVLC parses it
    readonly property var info: { library.revision; return library.info(player.currentPath) }
    readonly property string title: info.title || player.title
    readonly property string artist: info.artist || player.artist

    Rectangle {
        id: bg
        anchors.fill: parent
        radius: 28
        color: theme.dark ? "#1C1D23" : "#FFFFFF"
        border.width: 1
        border.color: theme.stroke
        layer.enabled: true
        layer.effect: MultiEffect {
            shadowEnabled: true
            shadowBlur: 1.0
            shadowVerticalOffset: 12
            shadowColor: theme.shadow
            autoPaddingEnabled: true
        }
    }
    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.expand() }

    // preview
    Item {
        id: preview
        x: 10; y: 10
        height: parent.height - 20
        width: player.isAudio ? height : Math.round(height * 16 / 9)
        Behavior on width { NumberAnimation { duration: 200 } }
        Rectangle { anchors.fill: parent; radius: 18; color: "black" }
        Item {
            anchors.fill: parent
            layer.enabled: true
            layer.effect: MultiEffect { maskEnabled: true; maskSource: pmask; maskThresholdMin: 0.5; maskSpreadAtMin: 1 }
            VideoOutput {
                id: vo
                anchors.fill: parent
                fillMode: VideoOutput.PreserveAspectCrop
                visible: !player.isAudio
                Component.onCompleted: player.addSink(vo.videoSink)
                Component.onDestruction: player.removeSink(vo.videoSink)
            }
            Rectangle {
                anchors.fill: parent
                visible: player.isAudio
                color: theme.surface2
                Icon { anchors.centerIn: parent; name: "music"; size: 20; color: theme.textDim; visible: art.status !== Image.Ready }
                Image { id: art; anchors.fill: parent; source: player.artwork || root.info.thumb || ""; fillMode: Image.PreserveAspectCrop; asynchronous: true; sourceSize.width: 160 }
            }
        }
        Item { id: pmask; anchors.fill: parent; visible: false; layer.enabled: true; Rectangle { anchors.fill: parent; radius: 18 } }
    }

    Column {
        anchors.left: preview.right; anchors.leftMargin: 16
        anchors.right: controls.left; anchors.rightMargin: 16
        anchors.verticalCenter: parent.verticalCenter
        spacing: 3
        Text {
            width: parent.width; elide: Text.ElideRight
            text: root.title || "Nada tocando"
            color: theme.text; font.pixelSize: 15; font.weight: Font.DemiBold
        }
        Text {
            width: parent.width; elide: Text.ElideRight
            text: (root.artist ? root.artist + "  ·  " : "") + U.time(player.time) + " / " + U.time(player.length)
            color: theme.textDim; font.pixelSize: 12
            font.features: { "tnum": 1 }
        }
    }

    Row {
        id: controls
        anchors.right: right.left; anchors.rightMargin: 12
        anchors.verticalCenter: parent.verticalCenter
        spacing: 6
        IconButton { objectName: "miniPrev"; icon: "prev"; size: 40; iconSize: 15; onClicked: player.previous(); anchors.verticalCenter: parent.verticalCenter }
        Item {
            width: 48; height: 48
            anchors.verticalCenter: parent.verticalCenter
            Rectangle { anchors.fill: parent; radius: 24; color: theme.inverse; scale: pma.pressed ? 0.92 : 1; Behavior on scale { NumberAnimation { duration: 100 } } }
            PlayGlyph { anchors.centerIn: parent; anchors.horizontalCenterOffset: player.playing ? 0 : 2; size: 18; playing: player.playing; color: theme.inverseText }
            MouseArea { id: pma; objectName: "miniPlay"; anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: player.togglePlay() }
        }
        IconButton { objectName: "miniNext"; icon: "next"; size: 40; iconSize: 15; onClicked: player.next(); anchors.verticalCenter: parent.verticalCenter }
    }
    Row {
        id: right
        anchors.right: parent.right; anchors.rightMargin: 14
        anchors.verticalCenter: parent.verticalCenter
        spacing: 4
        IconButton {
            objectName: "miniMute"
            icon: player.muted || player.volume === 0 ? "mute" : "volume"; size: 40; iconSize: 15
            anchors.verticalCenter: parent.verticalCenter
            onClicked: player.toggleMute()
        }
        MiniSlider {
            objectName: "miniVolume"
            width: 96; anchors.verticalCenter: parent.verticalCenter
            from: 0; to: 150; value: player.volume
            mark: 100; boostColor: "#E0398C"
            onMoved: (v) => player.volume = v
        }
        IconButton { objectName: "miniExpand"; icon: "fullscreen"; tip: "Abrir player"; size: 40; iconSize: 14; onClicked: root.expand(); anchors.verticalCenter: parent.verticalCenter }
    }
    // progress
    Rectangle {
        anchors.left: preview.right; anchors.leftMargin: 16
        anchors.right: parent.right; anchors.rightMargin: 28
        anchors.bottom: parent.bottom; anchors.bottomMargin: 8
        height: 3; radius: 1.5
        color: theme.surface3
        Rectangle {
            height: parent.height; radius: 1.5
            width: player.length > 0 ? parent.width * Math.min(1, clock.value / player.length) : 0
            color: theme.accent
        }
    }
}
