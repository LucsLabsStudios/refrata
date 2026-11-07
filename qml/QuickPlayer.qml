import QtQuick
import QtQuick.Window
import QtQuick.Effects
import QtMultimedia
import "Utils.js" as U

// Floating quick player.
//  mode "preview": Space on a file in Explorer (QuickLook-style). Space / Esc closes, ← → browse
//                  the folder, Enter opens it in Refrata at the same point.
//  mode "compact": a file opened from Explorer plays here first; Space pauses, ⤢ expands to the app.
// It plays with its own QtMultimedia player, so whatever Refrata is playing is left untouched.
Window {
    id: qw
    objectName: "quickPlayer"
    flags: Qt.Window | Qt.FramelessWindowHint | (pinned ? Qt.WindowStaysOnTopHint : 0)
    color: "transparent"
    title: name ? name + "  —  Refrata" : "Refrata"

    signal expandRequested(var files, int index, real ms)
    signal closed()

    property string mode: "preview"
    property var files: []
    property int index: 0
    readonly property string path: files.length ? files[Math.max(0, Math.min(index, files.length - 1))] : ""
    readonly property string fileName: path ? U.baseName(path).replace(/\.[^.]+$/, "") : ""
    // the library knows the real tags (title, artist); files outside it show their name
    readonly property var info: { library.revision; return path ? library.info(path) : ({}) }
    readonly property string name: info.title || fileName
    readonly property string artist: info.artist || ""
    readonly property bool isAudio: /\.(mp3|flac|wav|m4a|aac|ogg|opus|wma|alac|aiff|ape|mka|mid|midi|amr|ac3|dts)$/i.test(path)
    property bool pinned: mode === "preview"
    property bool shown: false
    property real openedAt: 0
    property int dir: 0                          // last browse direction, for the slide

    readonly property int pad: 28                // room for the soft shadow around the card
    readonly property size natural: {
        var r = mp.metaData.value(MediaMetaData.Resolution)
        return r && r.width > 0 ? Qt.size(r.width, r.height) : Qt.size(16, 9)
    }
    function fitSize() {
        if (isAudio) return Qt.size(440, 520)
        var maxW = Screen.desktopAvailableWidth * 0.6, maxH = Screen.desktopAvailableHeight * 0.62
        var ar = natural.width / Math.max(1, natural.height)
        var w = Math.min(maxW, Math.max(560, Math.min(natural.width, 1280)))
        var h = w / ar
        if (h > maxH) { h = maxH; w = h * ar }
        return Qt.size(Math.round(Math.max(420, w)), Math.round(Math.max(236, h)))
    }
    function placeCentered(sz) {
        width = sz.width + pad * 2
        height = sz.height + pad * 2
        x = Screen.virtualX + (Screen.desktopAvailableWidth - width) / 2
        y = Screen.virtualY + (Screen.desktopAvailableHeight - height) / 2 - 20
    }

    // ------------------------------------------------------------------ API
    function open(path, list, how) {
        if (visible && shown && mode === "preview" && how === "preview" && path === qw.path) { close(); return }
        mode = how
        files = list && list.length ? list : [path]
        var i = files.indexOf(path)
        index = i < 0 ? 0 : i
        dir = 0
        // two soundtracks at once is never wanted: hold Refrata's own playback while previewing
        if (!shown && player.playing) { player.pause(); pausedMain = true }
        load()
        placeCentered(fitSize())
        openedAt = Date.now()
        show()
        shown = true
        closeAnim.stop()
        openAnim.restart()
        appctl.activateWindow(qw)
        keys.forceActiveFocus()
        focusRetry.tries = 0
        focusRetry.restart()
    }
    // the window is still being mapped when it asks for the focus: ask again until Windows gives
    // it (otherwise Space / Esc would not reach the preview)
    Timer {
        id: focusRetry
        property int tries: 0
        interval: 80; repeat: true
        onTriggered: {
            if (qw.active || !qw.shown || ++tries > 18) { stop(); keys.forceActiveFocus(); return }
            qw.raise()
            qw.requestActivate()
            appctl.activateWindow(qw)
        }
    }
    function load() {
        failed = false
        mp.source = quicklook.fileUrl(path)
        mp.play()
        chrome.poke()
    }
    function browse(step) {
        if (files.length < 2) return
        dir = step
        index = (index + step + files.length) % files.length
        swap.restart()
        load()
    }
    function close() {
        if (!shown) return
        shown = false
        restoreFocus = mode === "preview"
        openAnim.stop()
        closeAnim.restart()
    }
    property bool expanded: false
    function expand() {
        expanded = true
        var ms = mp.position
        mp.pause()
        shown = false
        restoreFocus = false
        openAnim.stop()
        closeAnim.restart()
        expandRequested(files, index, ms)
    }
    property bool restoreFocus: false
    property bool pausedMain: false
    // in: grows from 92 % and fades up; out: quicker, settles to 97 % (exits ≈ half the entry)
    ParallelAnimation {
        id: openAnim
        NumberAnimation { target: card; property: "opacity"; from: 0; to: 1; duration: 220; easing.type: Easing.OutCubic }
        NumberAnimation { target: card; property: "scale"; from: theme.reduceMotion ? 1 : 0.92; to: 1; duration: 340; easing.type: Easing.OutQuint }
    }
    SequentialAnimation {
        id: closeAnim
        ParallelAnimation {
            NumberAnimation { target: card; property: "opacity"; to: 0; duration: 150; easing.type: Easing.OutCubic }
            NumberAnimation { target: card; property: "scale"; to: theme.reduceMotion ? 1 : 0.97; duration: 160; easing.type: Easing.InCubic }
        }
        ScriptAction {
            script: {
                mp.stop(); mp.source = ""
                if (qw.pausedMain && !qw.expanded) player.play()   // pick up where it was
                qw.pausedMain = false
                qw.expanded = false
                qw.hide()
                if (qw.restoreFocus) quicklook.restoreForeground()
                qw.closed()
            }
        }
    }
    // keep the window snug around new media (resolution arrives a moment after loading)
    onNaturalChanged: if (visible && !isAudio) { var c = Qt.point(x + width / 2, y + height / 2); var s = fitSize(); width = s.width + pad * 2; height = s.height + pad * 2; x = c.x - width / 2; y = c.y - height / 2 }

    // ------------------------------------------------------------------ player
    property bool failed: false
    MediaPlayer {
        id: mp
        audioOutput: AudioOutput { volume: Math.min(1, (player.volume || 80) / 100) }
        videoOutput: vo
        onErrorOccurred: qw.failed = true
    }

    // ------------------------------------------------------------------ card
    Item {
        id: card
        objectName: "quickCard"
        x: qw.pad; y: qw.pad
        width: qw.width - qw.pad * 2
        height: qw.height - qw.pad * 2
        opacity: 0
        scale: 0.92

        // soft shadow under the card
        Rectangle {
            anchors.fill: parent
            radius: 22
            color: "black"
            layer.enabled: true
            layer.effect: MultiEffect { shadowEnabled: true; shadowBlur: 1.0; shadowOpacity: 0.6; shadowVerticalOffset: 10; autoPaddingEnabled: true }
        }
        Item {
            id: body
            property bool tipBounds: true   // tooltips stay inside the rounded card
            anchors.fill: parent
            layer.enabled: true
            layer.effect: MultiEffect { maskEnabled: true; maskSource: cardMask; maskThresholdMin: 0.5; maskSpreadAtMin: 1 }

            Rectangle { anchors.fill: parent; color: "#0B0A09" }

            Item {
                id: content
                anchors.fill: parent
                transform: Translate { id: slide; x: 0 }
                // a click on the picture plays / pauses; a double click expands to Refrata
                TapHandler {
                    onTapped: if (!qw.failed) qw.togglePlay()
                    onDoubleTapped: qw.expand()
                }
                VideoOutput {
                    id: vo
                    anchors.fill: parent
                    visible: !qw.isAudio
                    fillMode: VideoOutput.PreserveAspectFit
                }
                // music: cover over its own blurred glow
                Item {
                    id: audioView
                    anchors.fill: parent
                    visible: qw.isAudio
                    readonly property string art: { library.revision; return library.thumbFor(qw.path) }
                    readonly property real hue: { var h = 7; for (var i = 0; i < qw.name.length; i++) h = (h * 31 + qw.name.charCodeAt(i)) % 104729; return (h % 360) / 360 }
                    Rectangle {
                        anchors.fill: parent
                        gradient: Gradient {
                            GradientStop { position: 0; color: Qt.hsla(audioView.hue, 0.6, 0.30, 1) }
                            GradientStop { position: 1; color: "#0B0A09" }
                        }
                    }
                    RoundedImage {
                        anchors.fill: parent; radius: 0; source: audioView.art; sourceWidth: 120
                        opacity: 0.55
                        layer.enabled: true
                        layer.effect: MultiEffect { blurEnabled: true; blur: 1; blurMax: 64; saturation: 0.3 }
                    }
                    Rectangle {
                        id: cover
                        anchors.horizontalCenter: parent.horizontalCenter
                        y: 70
                        width: parent.width - 120; height: width; radius: 18
                        gradient: Gradient {
                            GradientStop { position: 0; color: Qt.hsla(audioView.hue, 0.7, 0.52, 1) }
                            GradientStop { position: 1; color: Qt.hsla((audioView.hue + 0.08) % 1, 0.75, 0.28, 1) }
                        }
                        Icon { anchors.centerIn: parent; name: "music"; size: 56; color: Qt.rgba(1, 1, 1, 0.6); visible: coverImg.status !== Image.Ready }
                        RoundedImage { id: coverImg; anchors.fill: parent; radius: 18; source: audioView.art; sourceWidth: 640 }
                    }
                }
                // unsupported by the light player: offer the full app
                Column {
                    anchors.centerIn: parent
                    visible: qw.failed
                    spacing: 14
                    Icon { anchors.horizontalCenter: parent.horizontalCenter; name: "film"; size: 40; color: Qt.rgba(1, 1, 1, 0.5) }
                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: qsTr("A prévia não abre este formato"); color: "white"; font.pixelSize: 16; font.weight: Font.DemiBold }
                    PillButton { anchors.horizontalCenter: parent.horizontalCenter; text: qsTr("Abrir no Refrata"); icon: "fullscreen"; variant: "accent"; onClicked: qw.expand() }
                }
            }

            // ---------------------------------------------------- chrome (auto-hides over video)
            Item {
                id: chrome
                anchors.fill: parent
                property bool awake: true
                function poke() { awake = true; idle.restart() }
                opacity: awake || !mp.playing || qw.isAudio || qw.failed ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: 260; easing.type: Easing.OutCubic } }
                Timer { id: idle; interval: 2200; onTriggered: chrome.awake = false }

                Rectangle {
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                    height: 96
                    gradient: Gradient {
                        GradientStop { position: 0; color: Qt.rgba(0, 0, 0, 0.62) }
                        GradientStop { position: 1; color: "transparent" }
                    }
                }
                Rectangle {
                    anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                    height: 120
                    gradient: Gradient {
                        GradientStop { position: 0; color: "transparent" }
                        GradientStop { position: 1; color: Qt.rgba(0, 0, 0, 0.72) }
                    }
                }
                // title row (also the drag handle)
                Item {
                    id: topBar
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                    height: 60
                    DragHandler { target: null; onActiveChanged: if (active) qw.startSystemMove() }
                    Column {
                        anchors.left: parent.left; anchors.leftMargin: 20
                        anchors.right: topButtons.left; anchors.rightMargin: 12
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 1
                        SwapText {
                            objectName: "quickTitle"
                            width: parent.width; elide: Text.ElideRight
                            value: qw.name
                            color: "white"; font.pixelSize: 15; font.weight: Font.DemiBold
                        }
                        Text {
                            text: (qw.artist ? qw.artist + "  ·  " : "")
                                  + (qw.files.length > 1 ? qsTr("%1 de %2").arg(qw.index + 1).arg(qw.files.length) + "  ·  " : "")
                                  + (qw.mode === "preview" ? qsTr("Espaço fecha  ·  ← → navega") : qsTr("Espaço pausa  ·  ← → navega"))
                            color: Qt.rgba(1, 1, 1, 0.6); font.pixelSize: 11
                        }
                    }
                    Row {
                        id: topButtons
                        anchors.right: parent.right; anchors.rightMargin: 12
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 2
                        IconButton { objectName: "quickPin"; visible: qw.mode === "compact"; icon: "up"; motion: "pop"; onMedia: true; size: 36; iconSize: 13; checked: qw.pinned; tip: qw.pinned ? qsTr("Sempre no topo: ligado") : qsTr("Sempre no topo"); onClicked: qw.pinned = !qw.pinned }
                        IconButton { objectName: "quickExpand"; icon: "fullscreen"; onMedia: true; size: 36; iconSize: 13; tip: qsTr("Abrir no Refrata (Enter)"); onClicked: qw.expand() }
                        IconButton { objectName: "quickClose"; icon: "close"; onMedia: true; size: 36; iconSize: 12; tip: qsTr("Fechar (Esc)"); onClicked: qw.close() }
                    }
                }
                // transport
                Row {
                    anchors.left: parent.left; anchors.leftMargin: 16
                    anchors.right: parent.right; anchors.rightMargin: 20
                    anchors.bottom: parent.bottom; anchors.bottomMargin: 14
                    spacing: 12
                    Item {
                        objectName: "quickPlay"
                        width: 44; height: 44
                        anchors.verticalCenter: parent.verticalCenter
                        Rectangle {
                            anchors.fill: parent; radius: 22
                            gradient: Gradient {
                                GradientStop { position: 0; color: Qt.lighter(theme.accent, 1.3) }
                                GradientStop { position: 1; color: Qt.darker(theme.accent, 1.15) }
                            }
                            scale: pma.pressed ? 0.9 : (pma.containsMouse ? 1.06 : 1)
                            Behavior on scale { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
                        }
                        PlayGlyph { anchors.centerIn: parent; anchors.horizontalCenterOffset: mp.playing ? 0 : 1.5; size: 16; playing: mp.playing; color: "white" }
                        Ripple { id: qRipple; width: 44; color: theme.accent }
                        MouseArea { id: pma; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: { qRipple.burst(); qw.togglePlay() } }
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: U.time(seek.dragging ? seek.dragValue : mp.position)
                        color: "white"; font.pixelSize: 12; font.weight: Font.DemiBold; font.features: { "tnum": 1 }
                    }
                    SeekBar {
                        id: seek
                        objectName: "quickSeek"
                        anchors.verticalCenter: parent.verticalCenter
                        width: parent.width - 44 - 12 * 3 - 90
                        value: mp.position; to: Math.max(1, mp.duration)
                        onSeekRequested: (ms) => mp.position = ms
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: mp.duration > 0 ? U.time(mp.duration) : ""
                        color: Qt.rgba(1, 1, 1, 0.7); font.pixelSize: 12; font.features: { "tnum": 1 }
                    }
                }
            }
            HoverHandler { onPointChanged: chrome.poke() }
        }
        Item {
            id: cardMask
            anchors.fill: parent
            visible: false
            layer.enabled: true
            Rectangle { anchors.fill: parent; radius: 22; color: "black" }
        }
        Rectangle {   // hairline edge
            anchors.fill: parent; radius: 22; color: "transparent"
            border.width: 1; border.color: Qt.rgba(1, 1, 1, 0.10)
        }
    }

    function togglePlay() { mp.playing ? mp.pause() : mp.play(); chrome.poke() }

    // browsing slide: the new file glides in from the side it came from
    SequentialAnimation {
        id: swap
        PropertyAction { target: slide; property: "x"; value: qw.dir * 36 }
        NumberAnimation { target: slide; property: "x"; to: 0; duration: 320; easing.type: Easing.OutQuint }
    }

    // ------------------------------------------------------------------ keys
    Item {
        id: keys
        objectName: "quickKeys"
        focus: true
        Keys.onPressed: (e) => {
            if (e.isAutoRepeat && e.key === Qt.Key_Space) { e.accepted = true; return }
            // the Space that opened the preview may still be arriving: ignore it for a moment
            if (e.key === Qt.Key_Space && Date.now() - qw.openedAt < 280) { e.accepted = true; return }
            switch (e.key) {
            case Qt.Key_Space: qw.mode === "preview" ? qw.close() : qw.togglePlay(); break
            case Qt.Key_Escape: qw.close(); break
            case Qt.Key_Left: qw.browse(-1); break
            case Qt.Key_Right: qw.browse(1); break
            case Qt.Key_Return: case Qt.Key_Enter: qw.expand(); break
            case Qt.Key_Up: player.volume = player.volume + 5; break
            case Qt.Key_Down: player.volume = player.volume - 5; break
            default: return
            }
            e.accepted = true
        }
    }
}
