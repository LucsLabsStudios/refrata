import QtQuick
import QtQuick.Effects
import QtMultimedia
import "Utils.js" as U

Item {
    id: pv
    property bool fullscreen: false
    property string panel: ""          // tracks | speed | eq | adjust | sleep
    property bool queueOpen: false
    property int fillMode: VideoOutput.PreserveAspectFit
    property bool controlsVisible: true
    property int glassTick: 0
    signal minimize()
    signal toggleFullscreen()
    signal loadSubtitle()

    // music mode keeps its dock on screen; video hides the chrome while playing
    readonly property bool idleHide: player.playing && !player.isAudio && panel === "" && !queueOpen && !pinned
    property bool pinned: false        // mouse over controls
    function poke() { controlsVisible = true; hideTimer.restart() }
    function togglePanel(name) { panel = (panel === name ? "" : name); poke() }
    onWidthChanged: Qt.callLater(function () { pv.glassTick++ })
    onHeightChanged: Qt.callLater(function () { pv.glassTick++ })
    onVisibleChanged: if (visible) { poke(); Qt.callLater(function () { pv.glassTick++ }) }

    SmoothTime { id: pvClock; active: pv.visible && !player.isAudio }
    Timer {
        id: hideTimer
        interval: 2800
        onTriggered: if (pv.idleHide) pv.controlsVisible = false
    }
    Connections {
        target: player
        function onStateChanged() { if (!player.playing) pv.controlsVisible = true; else hideTimer.restart() }
    }

    Rectangle { anchors.fill: parent; color: "black" }

    // Passive hover detection at the root: sees every mouse move, even while the
    // controls are fading out and their children own the hover.
    // Qt Quick re-sends hover on every rendered frame, so only react to real movement.
    HoverHandler {
        id: rootHover
        property point last: Qt.point(-1, -1)
        onPointChanged: {
            var p = point.scenePosition
            if (Math.abs(p.x - last.x) + Math.abs(p.y - last.y) >= 2) {
                last = Qt.point(p.x, p.y)
                if (pv.visible) pv.poke()
            }
        }
    }

    // ---------------------------------------------------------------- backdrop
    Item {
        id: backdrop
        anchors.fill: parent

        VideoOutput {
            id: video
            anchors.fill: parent
            fillMode: pv.fillMode
            visible: !player.isAudio
            Component.onCompleted: player.addSink(video.videoSink)
            Component.onDestruction: player.removeSink(video.videoSink)
        }

        // music mode: the cover as a blurred, breathing colour field
        MusicBackdrop {
            objectName: "musicBackdrop"
            anchors.fill: parent
            visible: player.isAudio
            art: { library.revision; return player.artwork || library.thumbFor(player.currentPath) }
            seed: player.title
        }
    }

    // loading spinner: only for loads that take a moment (no flash on every track change);
    // music mode shows its cards instead
    Timer { id: spinDelay; interval: 350; running: player.state === "loading" }
    Item {
        anchors.centerIn: parent
        width: 64; height: 64
        opacity: player.state === "loading" && !spinDelay.running && !player.isAudio ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 200 } }
        visible: opacity > 0.01
        Rectangle {
            anchors.fill: parent; radius: 32; color: "transparent"
            border.width: 4; border.color: Qt.rgba(1, 1, 1, 0.15)
        }
        Item {
            anchors.fill: parent
            RotationAnimation on rotation { from: 0; to: 360; duration: 900; loops: Animation.Infinite; running: player.state === "loading" }
            Rectangle { x: 26; y: -2; width: 12; height: 12; radius: 6; color: theme.accent }
        }
    }

    // ---------------------------------------------------------------- scrims
    Rectangle {
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
        height: 180
        opacity: chrome.opacity
        gradient: Gradient {
            GradientStop { position: 0; color: Qt.rgba(0, 0, 0, 0.55) }
            GradientStop { position: 1; color: "transparent" }
        }
    }
    Rectangle {
        anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
        height: Math.min(pv.height * 0.7, 520)
        opacity: chrome.opacity
        visible: !player.isAudio   // the music backdrop carries its own vignette
        gradient: Gradient {
            GradientStop { position: 0; color: "transparent" }
            GradientStop { position: 0.4; color: Qt.rgba(0, 0, 0, 0.55) }
            GradientStop { position: 1; color: Qt.rgba(0, 0, 0, 0.92) }
        }
    }

    // ---------------------------------------------------- activity mouse area
    MouseArea {
        id: activity
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton
        cursorShape: pv.controlsVisible ? Qt.ArrowCursor : Qt.BlankCursor
        onClicked: {
            if (pv.panel !== "") { pv.panel = ""; return }
            // music mode: the cards and the dock are the controls; a stray click on the
            // backdrop must not pause the song
            if (!player.isAudio) clickTimer.restart()
        }
        onDoubleClicked: { clickTimer.stop(); pv.toggleFullscreen() }
        onWheel: (w) => { player.volume = player.volume + (w.angleDelta.y > 0 ? 5 : -5); pv.poke() }
        Timer { id: clickTimer; interval: 230; onTriggered: { player.togglePlay(); pv.poke() } }
    }

    // ------------------------------------------------------- music mode stage
    MusicStage {
        id: musicStage
        anchors.fill: parent
        visible: player.isAudio
        pv: pv
        backdrop: backdrop
        sideInset: pv.queueOpen ? queuePanel.width + 32 : 0
    }

    // ---------------------------------------------------------------- chrome
    Item {
        id: chrome
        objectName: "chrome"
        anchors.fill: parent
        opacity: pv.controlsVisible ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: 260 } }

        // ---- top-left
        Row {
            x: 32; y: 28
            spacing: 12
            GlassPanel {
                width: 52; height: 52; backdrop: backdrop; geometryTick: pv.glassTick
                IconButton { objectName: "pvMinimize"; anchors.centerIn: parent; icon: "down"; onMedia: true; size: 52; iconSize: 16; tip: "Minimizar"; onClicked: pv.minimize() }
            }
            GlassPanel {
                width: tl.width + 12; height: 52; backdrop: backdrop; geometryTick: pv.glassTick
                Row {
                    id: tl
                    anchors.centerIn: parent
                    IconButton { objectName: "pvSnapshot"; visible: !player.isAudio; icon: "camera"; onMedia: true; size: 44; iconSize: 16; tip: "Capturar quadro (S)"; onClicked: player.snapshot() }
                    IconButton {
                        objectName: "pvAspect"
                        visible: !player.isAudio
                        icon: "aspect"; onMedia: true; size: 44; iconSize: 16
                        tip: ["Ajustar à tela", "Preencher tela", "Esticar"][[VideoOutput.PreserveAspectFit, VideoOutput.PreserveAspectCrop, VideoOutput.Stretch].indexOf(pv.fillMode)]
                        onClicked: {
                            var modes = [VideoOutput.PreserveAspectFit, VideoOutput.PreserveAspectCrop, VideoOutput.Stretch]
                            pv.fillMode = modes[(modes.indexOf(pv.fillMode) + 1) % 3]
                        }
                    }
                    IconButton { objectName: "pvReveal"; icon: "explorer"; onMedia: true; size: 44; iconSize: 16; tip: "Mostrar no Explorer"; enabled: player.currentPath.indexOf("://") < 0; onClicked: appctl.reveal(player.currentPath) }
                }
            }
        }

        // ---- top-right: volume
        GlassPanel {
            visible: !player.isAudio   // music mode has the volume in its dock
            anchors.right: parent.right; anchors.rightMargin: 32; y: 28
            width: vrow.width + 28; height: 52
            backdrop: backdrop; geometryTick: pv.glassTick
            Row {
                id: vrow
                anchors.centerIn: parent
                spacing: 6
                MiniSlider {
                    objectName: "pvVolume"
                    width: 150; anchors.verticalCenter: parent.verticalCenter
                    from: 0; to: 150; value: player.muted ? 0 : player.volume
                    onMedia: true; accent: "white"
                    mark: 100; boostColor: theme.accent   // above 100 % libVLC amplifies in software (applies with a lag)
                    onMoved: (v) => player.volume = v
                }
                Text {
                    width: 40; anchors.verticalCenter: parent.verticalCenter
                    text: player.muted ? "0%" : player.volume + "%"
                    color: player.volume > 100 ? theme.accent : "white"
                    font.pixelSize: 12; font.weight: Font.DemiBold; horizontalAlignment: Text.AlignRight
                    font.features: { "tnum": 1 }
                }
                IconButton {
                    objectName: "pvMute"
                    anchors.verticalCenter: parent.verticalCenter
                    icon: player.muted || player.volume === 0 ? "mute" : player.volume < 34 ? "vol1" : player.volume < 67 ? "vol2" : "volume"
                    onMedia: true; size: 40; iconSize: 16
                    onClicked: player.toggleMute()
                }
            }
        }

        // ---- center transport
        Row {
            anchors.centerIn: parent
            anchors.verticalCenterOffset: player.isAudio ? 9999 : 0
            visible: !player.isAudio
            spacing: 36
            SkipButton { objectName: "pvSkipBack"; anchors.verticalCenter: parent.verticalCenter; size: 64; backdrop: backdrop; onClicked: { player.skip(-10000); pv.poke() } }
            GlassPanel {
                objectName: "bigPlay"
                width: 96; height: 96
                backdrop: backdrop; geometryTick: pv.glassTick
                tint: pma.containsMouse ? Qt.rgba(1, 1, 1, 0.22) : Qt.rgba(0.1, 0.1, 0.12, 0.35)
                scale: pma.pressed ? 0.92 : 1
                Behavior on scale { NumberAnimation { duration: 120 } }
                PlayGlyph { anchors.centerIn: parent; anchors.horizontalCenterOffset: player.playing ? 0 : 3; size: 36; playing: player.playing }
                MouseArea { id: pma; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: { player.togglePlay(); pv.poke() } }
            }
            SkipButton { objectName: "pvSkipFwd"; anchors.verticalCenter: parent.verticalCenter; forward: true; size: 64; backdrop: backdrop; onClicked: { player.skip(10000); pv.poke() } }
        }

        // ---- bottom block
        Column {
            id: bottom
            visible: !player.isAudio   // music mode: MusicStage's dock
            anchors.left: parent.left; anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.leftMargin: 48; anchors.rightMargin: pv.queueOpen ? 48 + queuePanel.width + 24 : 48
            anchors.bottomMargin: 36
            spacing: 18
            Behavior on anchors.rightMargin { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }
            HoverHandler { onHoveredChanged: pv.pinned = hovered }
            readonly property bool compact: width < 1180

            // title block fades out while a popover panel covers it
            Text {
                opacity: pv.panel === "" ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: 160 } }
                text: player.isAudio ? "OUVINDO AGORA" : "REPRODUZINDO AGORA"
                color: theme.accent
                font.pixelSize: 12; font.weight: Font.Bold; font.letterSpacing: 2.4
            }
            Text {
                opacity: pv.panel === "" ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: 160 } }
                width: parent.width * 0.75
                text: player.title || "—"
                color: "white"
                font.family: theme.displayFont
                font.pixelSize: Math.min(64, Math.max(34, pv.width / 26))
                font.weight: Font.Bold
                font.letterSpacing: -1.2
                elide: Text.ElideRight
                maximumLineCount: 2
                wrapMode: Text.WordWrap
                lineHeight: 0.95
            }
            Text {
                text: {
                    var parts = []
                    if (player.artist) parts.push(player.artist)
                    if (player.album) parts.push(player.album)
                    var p = player.currentPath
                    if (p && p.indexOf("://") < 0) parts.push(p.split(".").pop().toUpperCase())
                    if (player.videoWidth > 0 && !player.isAudio) parts.push(player.videoWidth + " × " + player.videoHeight)
                    if (player.queue.length > 1) parts.push((player.currentIndex + 1) + " de " + player.queue.length)
                    return parts.join("   ·   ")
                }
                color: Qt.rgba(1, 1, 1, 0.7)
                font.pixelSize: 15
                opacity: pv.panel === "" ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: 160 } }
            }
            Item { width: 1; height: 2 }

            // seek
            Item {
                width: parent.width; height: 24
                Text {
                    id: tcur
                    anchors.verticalCenter: parent.verticalCenter
                    text: U.time(seek.dragging ? seek.dragValue : player.time)
                    color: "white"; font.pixelSize: 13; font.weight: Font.DemiBold
                    font.features: { "tnum": 1 }
                    width: 64
                }
                SeekBar {
                    id: seek
                    objectName: "seekBar"
                    anchors.left: tcur.right; anchors.right: tlen.left
                    anchors.leftMargin: 8; anchors.rightMargin: 8
                    anchors.verticalCenter: parent.verticalCenter
                    value: pvClock.value; to: Math.max(1, player.length)
                    enabled: player.seekable
                    onSeekRequested: (ms) => player.seek(ms)
                }
                Text {
                    id: tlen
                    objectName: "pvTimeLabel"
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    width: 72; horizontalAlignment: Text.AlignRight
                    property bool remaining: false
                    text: player.length > 0 ? (remaining ? "−" + U.time(player.length - player.time) : U.time(player.length)) : "AO VIVO"
                    color: Qt.rgba(1, 1, 1, 0.75); font.pixelSize: 13; font.weight: Font.DemiBold
                    font.features: { "tnum": 1 }
                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: parent.remaining = !parent.remaining }
                }
            }

            // buttons row
            Item {
                width: parent.width; height: 48
                Row {
                    spacing: 8
                    anchors.verticalCenter: parent.verticalCenter
                    IconButton { objectName: "pvPrev"; icon: "prev"; onMedia: true; size: 44; iconSize: 15; tip: "Anterior (P)"; onClicked: player.previous() }
                    IconButton { objectName: "pvNext"; icon: "next"; onMedia: true; size: 44; iconSize: 15; tip: "Próximo (N)"; onClicked: player.next() }
                    Item { width: 8; height: 1 }
                    PillButton { objectName: "pvTracks"; variant: "glass"; icon: "cc"; text: bottom.compact ? "" : "Faixas"; h: 44; checked: pv.panel === "tracks"; onClicked: pv.togglePanel("tracks") }
                    PillButton { objectName: "pvSpeed"; variant: "glass"; icon: "speed"; text: player.rate.toFixed(2).replace(/\.?0+$/, "") + "×"; h: 44; checked: pv.panel === "speed"; onClicked: pv.togglePanel("speed") }
                    PillButton { objectName: "pvEq"; variant: "glass"; icon: "eq"; text: bottom.compact ? "" : "Equalizador"; h: 44; checked: pv.panel === "eq"; onClicked: pv.togglePanel("eq") }
                    PillButton { objectName: "pvAdjust"; variant: "glass"; icon: "brightness"; text: bottom.compact ? "" : "Imagem"; h: 44; visible: !player.isAudio; checked: pv.panel === "adjust"; onClicked: pv.togglePanel("adjust") }
                    PillButton { objectName: "pvSleep"; variant: "glass"; icon: "timer"; text: player.sleepMinutes > 0 ? player.sleepMinutes + " min" : (bottom.compact ? "" : "Timer"); h: 44; checked: pv.panel === "sleep"; onClicked: pv.togglePanel("sleep") }
                }
                Row {
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 6
                    IconButton { objectName: "pvShuffle"; icon: "shuffle"; onMedia: true; size: 44; iconSize: 15; checked: player.shuffle; tip: "Aleatório"; onClicked: player.toggleShuffle() }
                    IconButton { objectName: "pvRepeat"; icon: player.repeat === 2 ? "repeat1" : "repeat"; onMedia: true; size: 44; iconSize: 15; checked: player.repeat > 0; tip: ["Repetir: desligado", "Repetir: tudo", "Repetir: um"][player.repeat]; onClicked: player.cycleRepeat() }
                    PillButton { objectName: "pvQueue"; variant: "glass"; icon: "queue"; text: bottom.compact ? "" : "A seguir"; h: 44; checked: pv.queueOpen; onClicked: { pv.queueOpen = !pv.queueOpen; pv.poke() } }
                    IconButton { objectName: "pvFullscreen"; icon: pv.fullscreen ? "exitfull" : "fullscreen"; onMedia: true; size: 44; iconSize: 15; tip: "Tela cheia (F)"; onClicked: pv.toggleFullscreen() }
                }
            }
        }
    }

    // ---------------------------------------------------------- popover panels
    component PanelTitle: Text { color: "white"; font.family: theme.displayFont; font.pixelSize: 22; font.weight: Font.DemiBold }
    component Caption: Text { color: Qt.rgba(1, 1, 1, 0.55); font.pixelSize: 11; font.weight: Font.Bold; font.letterSpacing: 1.6 }
    component Chip: Rectangle {
        id: chip
        property string text: ""
        property bool selected: false
        signal clicked()
        width: Math.max(56, ct.implicitWidth + 28); height: 38; radius: 19
        color: selected ? "white" : (cm.containsMouse ? Qt.rgba(1, 1, 1, 0.16) : Qt.rgba(1, 1, 1, 0.08))
        Text { id: ct; anchors.centerIn: parent; text: chip.text; color: chip.selected ? "#111" : "white"; font.pixelSize: 13; font.weight: Font.DemiBold }
        MouseArea { id: cm; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: chip.clicked() }
    }
    component TrackItem: Rectangle {
        id: ti
        property string text: ""
        property bool selected: false
        signal clicked()
        width: parent ? parent.width : 200; height: 42; radius: 14
        color: tm.containsMouse ? Qt.rgba(1, 1, 1, 0.08) : "transparent"
        Icon { x: 12; anchors.verticalCenter: parent.verticalCenter; name: "check"; size: 13; color: theme.accent; visible: ti.selected }
        Text { x: 36; anchors.verticalCenter: parent.verticalCenter; width: parent.width - 48; elide: Text.ElideRight; text: ti.text; color: ti.selected ? "white" : Qt.rgba(1, 1, 1, 0.8); font.pixelSize: 14; font.weight: ti.selected ? Font.DemiBold : Font.Normal }
        MouseArea { id: tm; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: ti.clicked() }
    }
    component Stepper: Row {
        id: st
        property int value: 0
        property string unit: "ms"
        signal changed(int v)
        spacing: 8
        Chip { text: "−"; onClicked: st.changed(st.value - 100) }
        Text { width: 84; anchors.verticalCenter: parent.verticalCenter; horizontalAlignment: Text.AlignHCenter; text: (st.value > 0 ? "+" : "") + st.value + " " + st.unit; color: "white"; font.pixelSize: 14; font.weight: Font.DemiBold; font.features: { "tnum": 1 } }
        Chip { text: "+"; onClicked: st.changed(st.value + 100) }
        Chip { text: "Zerar"; onClicked: st.changed(0) }
    }

    GlassPanel {
        id: popover
        objectName: "popover"
        visible: opacity > 0
        opacity: pv.panel !== "" && pv.controlsVisible ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 180 } }
        x: player.isAudio ? Math.max(32, (pv.width - (pv.queueOpen ? queuePanel.width + 32 : 0) - width) / 2) : 48 + 120
        anchors.bottom: parent.bottom
        anchors.bottomMargin: player.isAudio ? musicStage.dockBottom + musicStage.dockH + 16 : 36 + 48 + 16
        width: pv.panel === "eq" ? 620 : pv.panel === "tracks" ? 640 : pv.panel === "adjust" ? 460 : 420
        height: panelLoader.item ? panelLoader.item.implicitHeight + 56 : 100
        radius: 28
        backdrop: backdrop
        geometryTick: pv.glassTick + width + height
        tint: Qt.rgba(0.07, 0.07, 0.09, 0.66)
        blurMax: 64
        MouseArea { anchors.fill: parent; hoverEnabled: true; onPositionChanged: pv.poke() }
        Loader {
            id: panelLoader
            x: 28; y: 28
            width: parent.width - 56
            sourceComponent: pv.panel === "tracks" ? tracksPanel : pv.panel === "speed" ? speedPanel
                             : pv.panel === "eq" ? eqPanel : pv.panel === "adjust" ? adjustPanel
                             : pv.panel === "sleep" ? sleepPanel : null
        }
    }

    Component {
        id: tracksPanel
        Row {
            spacing: 24
            Column {
                id: ac
                width: 270
                spacing: 6
                PanelTitle { text: "Áudio" }
                Item { width: 1; height: 6 }
                Repeater {
                    model: player.audioTracks
                    TrackItem { objectName: "atrack_" + index; text: modelData.name; selected: modelData.id === player.audioTrack; onClicked: player.setAudioTrack(modelData.id) }
                }
                Text { visible: player.audioTracks.length === 0; text: "Nenhuma faixa"; color: Qt.rgba(1, 1, 1, 0.5); font.pixelSize: 13 }
                Item { width: 1; height: 8 }
                Caption { text: "ATRASO DO ÁUDIO" }
                Stepper { value: player.audioDelay; onChanged: (v) => player.setAudioDelay(v) }
            }
            Column {
                id: sc
                width: 290
                spacing: 6
                Row {
                    width: parent.width
                    PanelTitle { text: "Legendas"; width: parent.width - addSub.width }
                    Chip { id: addSub; objectName: "addSubBtn"; text: "+ Arquivo"; onClicked: pv.loadSubtitle() }
                }
                Item { width: 1; height: 6 }
                Repeater {
                    model: player.subtitleTracks
                    TrackItem { objectName: "strack_" + index; text: modelData.name; selected: modelData.id === player.subtitleTrack; onClicked: player.setSubtitleTrack(modelData.id) }
                }
                Text { visible: player.subtitleTracks.length === 0; text: "Nenhuma legenda — arraste um .srt para a janela"; color: Qt.rgba(1, 1, 1, 0.5); font.pixelSize: 13; width: parent.width; wrapMode: Text.WordWrap }
                Item { width: 1; height: 8 }
                Caption { text: "SINCRONIA DA LEGENDA" }
                Stepper { value: player.subtitleDelay; onChanged: (v) => player.setSubtitleDelay(v) }
            }
        }
    }

    Component {
        id: speedPanel
        Column {
            spacing: 18
            Row {
                width: parent.width
                PanelTitle { text: "Velocidade"; width: parent.width - big.width }
                Text { id: big; text: player.rate.toFixed(2) + "×"; color: theme.accent; font.family: theme.displayFont; font.pixelSize: 28; font.weight: Font.Bold }
            }
            Flow {
                width: parent.width
                spacing: 8
                Repeater {
                    model: [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 3.0]
                    Chip { objectName: "speed_" + modelData; text: modelData + "×"; selected: Math.abs(player.rate - modelData) < 0.001; onClicked: player.rate = modelData }
                }
            }
            MiniSlider { width: parent.width; from: 0.25; to: 4; stepSize: 0.05; value: player.rate; onMedia: true; onMoved: (v) => player.rate = v }
        }
    }

    Component {
        id: eqPanel
        Column {
            spacing: 18
            Row {
                width: parent.width
                PanelTitle { text: bottom.compact ? "" : "Equalizador"; width: parent.width - eqSw.width }
                Toggle { id: eqSw; objectName: "eqToggle"; checked: player.eqEnabled; onToggled: (on) => player.setEqEnabled(on) }
            }
            Flow {
                width: parent.width
                spacing: 6
                Repeater {
                    model: player.eqPresets
                    Chip { objectName: "eqp_" + index; text: modelData; selected: index === player.eqPreset; height: 32; onClicked: player.setEqPreset(index) }
                }
            }
            Row {
                spacing: 0
                opacity: player.eqEnabled ? 1 : 0.4
                Column {
                    width: 56; spacing: 8
                    MiniSlider { vertical: true; height: 150; anchors.horizontalCenter: parent.horizontalCenter; from: -20; to: 20; center: 0; value: player.eqPreamp; onMedia: true; accent: "white"; onMoved: (v) => player.setEqPreamp(v) }
                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: "Pré"; color: Qt.rgba(1, 1, 1, 0.6); font.pixelSize: 11 }
                }
                Repeater {
                    model: player.eqFrequencies
                    Column {
                        width: 52; spacing: 8
                        MiniSlider { objectName: "eqband_" + index; vertical: true; height: 150; anchors.horizontalCenter: parent.horizontalCenter; from: -20; to: 20; center: 0; value: player.eqBands[index]; onMedia: true; onMoved: (v) => player.setEqBand(index, v) }
                        Text { anchors.horizontalCenter: parent.horizontalCenter; text: modelData; color: Qt.rgba(1, 1, 1, 0.6); font.pixelSize: 11 }
                    }
                }
            }
        }
    }

    Component {
        id: adjustPanel
        Column {
            spacing: 14
            Row {
                width: parent.width
                PanelTitle { text: bottom.compact ? "" : "Imagem"; width: parent.width - rs.width }
                Chip { id: rs; objectName: "adjReset"; text: "Restaurar"; onClicked: player.resetAdjust() }
            }
            Repeater {
                model: [
                    { key: "brightness", label: "Brilho", from: 0, to: 2, center: 1 },
                    { key: "contrast", label: "Contraste", from: 0, to: 2, center: 1 },
                    { key: "saturation", label: "Saturação", from: 0, to: 3, center: 1 },
                    { key: "gamma", label: "Gama", from: 0.1, to: 3, center: 1 },
                    { key: "hue", label: "Matiz", from: -180, to: 180, center: 0 },
                ]
                Row {
                    spacing: 12
                    Text { width: 90; anchors.verticalCenter: parent.verticalCenter; text: modelData.label; color: "white"; font.pixelSize: 14 }
                    MiniSlider {
                        objectName: "adj_" + modelData.key
                        width: 250; anchors.verticalCenter: parent.verticalCenter
                        from: modelData.from; to: modelData.to; center: modelData.center
                        value: player.adjust[modelData.key]; onMedia: true
                        onMoved: (v) => player.setAdjust(modelData.key, v)
                    }
                    Text { width: 40; anchors.verticalCenter: parent.verticalCenter; horizontalAlignment: Text.AlignRight; text: player.adjust[modelData.key].toFixed(modelData.key === "hue" ? 0 : 2); color: Qt.rgba(1, 1, 1, 0.6); font.pixelSize: 12; font.features: { "tnum": 1 } }
                }
            }
            Caption { text: "ENQUADRAMENTO" }
            Segmented {
                onMedia: true
                options: [{ text: "Ajustar", value: VideoOutput.PreserveAspectFit }, { text: "Preencher", value: VideoOutput.PreserveAspectCrop }, { text: "Esticar", value: VideoOutput.Stretch }]
                value: pv.fillMode
                onPicked: (v) => pv.fillMode = v
            }
        }
    }

    Component {
        id: sleepPanel
        Column {
            spacing: 16
            PanelTitle { text: "Timer de sono" }
            Text { width: parent.width; wrapMode: Text.WordWrap; text: player.sleepMinutes > 0 ? "Pausando em " + player.sleepMinutes + " min." : "Pausa a reprodução automaticamente."; color: Qt.rgba(1, 1, 1, 0.7); font.pixelSize: 14 }
            Flow {
                width: parent.width; spacing: 8
                Repeater {
                    model: [15, 30, 45, 60, 90, 120]
                    Chip { objectName: "sleep_" + modelData; text: modelData + " min"; onClicked: player.setSleepTimer(modelData) }
                }
                Chip { objectName: "sleepOff"; text: "Desligar"; selected: player.sleepMinutes === 0; onClicked: player.setSleepTimer(0) }
            }
        }
    }

    // ------------------------------------------------------ up next side panel
    GlassPanel {
        id: queuePanel
        objectName: "queuePanel"
        width: Math.min(400, pv.width * 0.3)
        anchors.top: parent.top; anchors.topMargin: 100
        anchors.bottom: parent.bottom
        anchors.bottomMargin: player.isAudio ? musicStage.dockBottom + musicStage.dockH + 16 : 36
        x: pv.queueOpen && pv.controlsVisible ? pv.width - width - 32 : pv.width + 20
        Behavior on x { NumberAnimation { duration: 320; easing.type: Easing.OutCubic } }
        visible: x < pv.width
        onXChanged: remap()
        radius: 32
        backdrop: backdrop
        geometryTick: pv.glassTick
        tint: Qt.rgba(0.07, 0.07, 0.09, 0.6)
        MouseArea { anchors.fill: parent; hoverEnabled: true; onPositionChanged: pv.poke() }

        Text { x: 24; y: 24; text: "A seguir"; color: "white"; font.family: theme.displayFont; font.pixelSize: 26; font.weight: Font.DemiBold }
        Text { anchors.right: parent.right; anchors.rightMargin: 24; y: 34; text: player.queue.length + " itens"; color: Qt.rgba(1, 1, 1, 0.55); font.pixelSize: 13 }
        ListView {
            id: ql
            anchors.fill: parent
            anchors.topMargin: 72; anchors.margins: 12
            clip: true
            spacing: 4
            model: player.queue
            currentIndex: player.currentIndex
            delegate: Rectangle {
                objectName: "qitem_" + index
                width: ql.width; height: 72; radius: 18
                readonly property bool cur: index === player.currentIndex
                readonly property var info: { library.revision; return library.info(modelData.path) }
                color: cur ? Qt.rgba(1, 1, 1, 0.14) : (qm.containsMouse ? Qt.rgba(1, 1, 1, 0.07) : "transparent")
                Rectangle {
                    id: qth
                    x: 10; anchors.verticalCenter: parent.verticalCenter
                    width: modelData.audio ? 52 : 92; height: 52; radius: 12
                    color: Qt.rgba(1, 1, 1, 0.08)
                    Icon { anchors.centerIn: parent; name: modelData.audio ? "music" : "film"; size: 16; color: Qt.rgba(1, 1, 1, 0.4); visible: qi.status !== Image.Ready }
                    RoundedImage { id: qi; anchors.fill: parent; radius: 12; source: parent.parent.info.thumb || ""; sourceWidth: 200 }
                }
                Column {
                    anchors.left: qth.right; anchors.leftMargin: 14
                    anchors.right: parent.right; anchors.rightMargin: 14
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 3
                    Text { width: parent.width; elide: Text.ElideRight; text: parent.parent.info.title || modelData.title; color: parent.parent.cur ? theme.accent : "white"; font.pixelSize: 14; font.weight: Font.DemiBold }
                    Text { width: parent.width; elide: Text.ElideRight; text: parent.parent.cur ? (player.playing ? "Reproduzindo" : "Pausado") : (parent.parent.info.duration ? U.time(parent.parent.info.duration) : ""); color: Qt.rgba(1, 1, 1, 0.55); font.pixelSize: 12 }
                }
                MouseArea { id: qm; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: player.playAt(index) }
            }
        }
    }
}
