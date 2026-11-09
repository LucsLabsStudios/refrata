import QtQuick
import QtQuick.Window
import QtMultimedia

// Picture-in-picture: a small always-on-top window showing what the main player is playing
// (same player, a second video sink, so nothing restarts). Drag to move, drag an edge to
// resize, double-click or ⤡ to go back to the app.
Window {
    id: pip
    objectName: "pipWindow"
    flags: Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
    color: "black"
    title: player.title ? player.title + "  —  Refrata" : "Refrata"
    minimumWidth: 240; minimumHeight: 135

    signal backRequested()      // ⤡ / double-click / Esc: show the app again
    signal closeRequested()     // ✕: leave PiP and pause

    readonly property real aspect: player.videoWidth > 0 && player.videoHeight > 0
                                   ? player.videoWidth / player.videoHeight : 16 / 9
    property bool attached: false
    readonly property bool awake: hover.hovered || !player.playing

    function place() {
        var w = Math.round(Math.max(320, Math.min(480, Screen.desktopAvailableWidth * 0.26)))
        var h = Math.round(w / Math.max(0.5, Math.min(aspect, 3)))
        width = w; height = h
        x = Screen.virtualX + Screen.desktopAvailableWidth - w - 24
        y = Screen.virtualY + Screen.desktopAvailableHeight - h - 24
    }

    onVisibleChanged: {
        if (visible && !attached) { player.addSink(out.videoSink); attached = true }
        else if (!visible && attached) { player.removeSink(out.videoSink); attached = false }
    }
    Component.onDestruction: if (attached) player.removeSink(out.videoSink)
    // Alt+F4 on the small window: same as ✕
    onClosing: (close) => { close.accepted = false; pip.closeRequested() }

    VideoOutput {
        id: out
        objectName: "pipVideo"
        anchors.fill: parent
        fillMode: VideoOutput.PreserveAspectFit
        visible: !player.isAudio
    }

    // music, or a link still opening: the cover / the title instead of a black box
    Image {
        anchors.fill: parent
        visible: player.isAudio
        fillMode: Image.PreserveAspectCrop
        source: { library.revision; return player.artwork || library.thumbFor(player.currentPath) }
        asynchronous: true
    }
    Text {
        anchors.centerIn: parent
        width: parent.width - 32
        visible: player.state === "loading" || (player.isAudio && !player.artwork)
        horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight
        text: player.resolving ? qsTr("Abrindo o link…") : player.title
        color: "white"; font.pixelSize: 15; font.weight: Font.DemiBold
    }

    HoverHandler { id: hover }

    // move: drag anywhere; resize: drag within 8 px of an edge
    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        readonly property int grip: 8
        function edges(m) {
            var e = 0
            if (m.x < grip) e |= Qt.LeftEdge
            if (m.x > width - grip) e |= Qt.RightEdge
            if (m.y < grip) e |= Qt.TopEdge
            if (m.y > height - grip) e |= Qt.BottomEdge
            return e
        }
        cursorShape: {
            var e = edges({ x: mouseX, y: mouseY })
            if (e === (Qt.LeftEdge | Qt.TopEdge) || e === (Qt.RightEdge | Qt.BottomEdge)) return Qt.SizeFDiagCursor
            if (e === (Qt.RightEdge | Qt.TopEdge) || e === (Qt.LeftEdge | Qt.BottomEdge)) return Qt.SizeBDiagCursor
            if (e & (Qt.LeftEdge | Qt.RightEdge)) return Qt.SizeHorCursor
            if (e & (Qt.TopEdge | Qt.BottomEdge)) return Qt.SizeVerCursor
            return Qt.ArrowCursor
        }
        onPressed: (m) => {
            var e = edges(m)
            if (e) pip.startSystemResize(e)
            else pip.startSystemMove()
        }
        onDoubleClicked: pip.backRequested()
    }

    // ---------------------------------------------------------------- controls
    Rectangle {
        anchors.fill: parent
        opacity: pip.awake ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
        gradient: Gradient {
            GradientStop { position: 0; color: Qt.rgba(0, 0, 0, 0.55) }
            GradientStop { position: 0.35; color: "transparent" }
            GradientStop { position: 0.65; color: "transparent" }
            GradientStop { position: 1; color: Qt.rgba(0, 0, 0, 0.6) }
        }

        Row {
            anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 6
            spacing: 2
            IconButton { objectName: "pipBack"; icon: "pipexit"; onMedia: true; size: 36; iconSize: 14; tip: qsTr("Voltar ao app"); onClicked: pip.backRequested() }
            IconButton { objectName: "pipClose"; icon: "close"; onMedia: true; size: 36; iconSize: 14; tip: qsTr("Fechar"); onClicked: pip.closeRequested() }
        }

        Row {
            anchors.centerIn: parent
            spacing: 14
            IconButton { objectName: "pipBack10"; icon: "left"; onMedia: true; size: 40; iconSize: 15; visible: pip.width >= 300; tip: qsTr("Voltar 10 s"); onClicked: player.skip(-10000) }
            IconButton { objectName: "pipPlay"; icon: player.playing ? "pause" : "play"; onMedia: true; size: 52; iconSize: 22; tip: player.playing ? qsTr("Pausar") : qsTr("Reproduzir"); onClicked: player.togglePlay() }
            IconButton { objectName: "pipFwd10"; icon: "right"; onMedia: true; size: 40; iconSize: 15; visible: pip.width >= 300; tip: qsTr("Avançar 10 s"); onClicked: player.skip(10000) }
        }

        // thin progress line; click to seek
        Item {
            anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
            height: 14
            visible: player.length > 0
            Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: 3; color: Qt.rgba(1, 1, 1, 0.25) }
            Rectangle {
                anchors.left: parent.left; anchors.bottom: parent.bottom; height: 3; color: theme.accent
                width: parent.width * Math.max(0, Math.min(1, player.time / Math.max(1, player.length)))
            }
            MouseArea { anchors.fill: parent; onClicked: (m) => player.seek(Math.round(player.length * m.x / width)) }
        }
    }

    Shortcut { sequence: keys.map.playPause; onActivated: player.togglePlay() }
    Shortcut { sequence: keys.map.back10; onActivated: player.skip(-10000) }
    Shortcut { sequence: keys.map.fwd10; onActivated: player.skip(10000) }
    Shortcut { sequence: "Esc"; onActivated: pip.backRequested() }
}
