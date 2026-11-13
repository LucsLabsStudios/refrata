import QtQuick
import QtQuick.Effects
import QtMultimedia
import "Utils.js" as U

Item {
    id: pv
    property bool fullscreen: false
    property string panel: ""          // tracks | speed | eq | adjust | sleep | chapters | info
    // picture geometry, per file (a new file starts straight): rotation in 90° steps, mirror,
    // zoom and a forced aspect ratio ("" = the video's own)
    property int videoRotation: 0
    property bool videoMirror: false
    property real videoZoom: 1
    property string videoAspect: ""
    property string geometryPath: ""
    // languages for the online subtitle search: the app's language first, then English
    property string subLanguages: {
        var m = { pt: "pt-br,pt-pt", en: "en", es: "es", fr: "fr", de: "de", it: "it", ru: "ru", pl: "pl", tr: "tr",
                  id: "id", hi: "hi", zh: "zh-cn", ja: "ja", ko: "ko" }
        var first = m[i18n.current] || "en"
        return first === "en" ? "en" : first + ",en"
    }
    function resetGeometry() { videoRotation = 0; videoMirror = false; videoZoom = 1; videoAspect = "" }
    function rotate(deg) { videoRotation = ((videoRotation + deg) % 360 + 360) % 360; poke() }
    property bool queueOpen: false
    property int fillMode: VideoOutput.PreserveAspectFit
    property bool controlsVisible: true
    property int glassTick: 0
    property bool alwaysOnTop: false
    signal toggleOnTop()
    property bool warm: false          // rendered off-screen once at startup (see Main.qml)
    signal minimize()
    signal toggleFullscreen()
    signal pipRequested()
    signal loadSubtitle()

    // music mode keeps its dock on screen; video hides the chrome while playing
    readonly property bool idleHide: player.playing && !player.isAudio && panel === "" && !queueOpen && !pinned
    property bool pinned: false        // mouse over controls
    // the timer only looks once: hiding allowed again (a panel closed, the mouse left the bar) counts anew
    onIdleHideChanged: if (idleHide) hideTimer.restart()
    function poke() { controlsVisible = true; hideTimer.restart() }
    function togglePanel(name) { panel = (panel === name ? "" : name); poke() }
    onWidthChanged: Qt.callLater(function () { pv.glassTick++ })
    onHeightChanged: Qt.callLater(function () { pv.glassTick++ })
    onVisibleChanged: if (visible && !warm) { poke(); Qt.callLater(function () { pv.glassTick++ }) }

    SmoothTime { id: pvClock; active: pv.visible && !player.isAudio }
    Timer {
        id: hideTimer
        interval: 2800
        onTriggered: if (pv.idleHide) pv.controlsVisible = false
    }
    Connections {
        target: player
        function onStateChanged() { if (!player.playing) pv.controlsVisible = true; else hideTimer.restart() }
        function onMediaChanged() {
            if (player.currentPath !== pv.geometryPath) { pv.geometryPath = player.currentPath; pv.resetGeometry() }
        }
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
        // the blurred cover is drawn larger than the view (no dark blur edges): keep it inside,
        // or it leaks above the player's top edge while the sheet slides
        clip: true

        Item {
            id: videoBox
            objectName: "videoBox"
            anchors.fill: parent
            visible: !player.isAudio
            // a forced aspect ratio: the picture is stretched into a box of that shape, fitted
            // (or cropped, with "Preencher") to the window
            readonly property real forced: {
                var a = pv.videoAspect.split(":")
                return a.length === 2 && +a[1] > 0 ? (+a[0]) / (+a[1]) : 0
            }
            readonly property bool crop: pv.fillMode === VideoOutput.PreserveAspectCrop
            VideoOutput {
                id: video
                objectName: "videoOutput"
                anchors.centerIn: parent
                width: videoBox.forced > 0 ? (videoBox.crop ? Math.max(parent.width, parent.height * videoBox.forced)
                                                          : Math.min(parent.width, parent.height * videoBox.forced))
                                           : parent.width
                height: videoBox.forced > 0 ? width / videoBox.forced : parent.height
                fillMode: videoBox.forced > 0 ? VideoOutput.Stretch : pv.fillMode
                orientation: pv.videoRotation
                transform: Scale {
                    origin.x: video.width / 2; origin.y: video.height / 2
                    xScale: pv.videoZoom * (pv.videoMirror ? -1 : 1); yScale: pv.videoZoom
                }
                Component.onCompleted: player.addSink(video.videoSink)
                Component.onDestruction: player.removeSink(video.videoSink)
            }
        }

        // music mode: the cover as a blurred, breathing colour field
        MusicBackdrop {
            objectName: "musicBackdrop"
            anchors.fill: parent
            visible: player.isAudio || pv.warm
            art: { library.revision; return player.artwork || library.thumbFor(player.currentPath) }
            seed: player.title
        }
    }

    Column {
        objectName: "castingBanner"
        anchors.centerIn: parent
        anchors.verticalCenterOffset: -60
        visible: player.castingTo !== ""
        spacing: 14
        Icon { anchors.horizontalCenter: parent.horizontalCenter; name: "cast"; size: 56; color: "white" }
        Text { anchors.horizontalCenter: parent.horizontalCenter; text: qsTr("Transmitindo para %1").arg(player.castingTo); color: "white"; font.family: theme.displayFont; font.pixelSize: 28; font.weight: Font.DemiBold }
        Text { anchors.horizontalCenter: parent.horizontalCenter; text: qsTr("Os controles daqui comandam a TV."); color: Qt.rgba(1, 1, 1, 0.65); font.pixelSize: 15 }
    }

    // loading spinner: only for loads that take a moment (no flash on every track change);
    // music mode shows its cards instead
    Timer { id: spinDelay; interval: 350; running: player.state === "loading" }
    Item {
        anchors.centerIn: parent
        width: 64; height: 64
        opacity: player.state === "loading" && (!spinDelay.running || player.resolving) && !player.isAudio ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
        visible: opacity > 0.01
        anchors.verticalCenterOffset: player.resolving ? -40 : 0
        z: 1   // above the scrims
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

    // a YouTube/Vimeo… link: yt-dlp needs a few seconds to find the stream; say so right away
    Column {
        objectName: "resolvingBanner"
        z: 1   // above the scrims
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.verticalCenter: parent.verticalCenter
        anchors.verticalCenterOffset: 70
        width: Math.min(pv.width - 64, 560)
        spacing: 10
        opacity: player.resolving ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
        visible: opacity > 0.01
        Text {
            width: parent.width; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
            text: player.resolvingSite ? qsTr("Abrindo link do %1…").arg(player.resolvingSite) : qsTr("Abrindo o link…")
            color: "white"; font.family: theme.displayFont; font.pixelSize: 24; font.weight: Font.DemiBold
        }
        Text {
            width: parent.width; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
            text: qsTr("Procurando a melhor qualidade. Isso leva alguns segundos.")
            color: Qt.rgba(1, 1, 1, 0.65); font.pixelSize: 15
        }
        Item { width: 1; height: 6 }
        PillButton {
            objectName: "resolvingCancel"
            anchors.horizontalCenter: parent.horizontalCenter
            variant: "glass"; h: 40; text: qsTr("Cancelar")
            onClicked: player.stop()
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
            if (!player.isAudio) {
                // what the click means is decided now, from what is on screen: applied 230 ms later
                // (a double click is full screen), a plain toggle went over whatever happened in
                // between (the Space preview pausing the player: the click then set it playing)
                clickTimer.wantPlay = !(player.state === "playing" || player.state === "loading")
                clickTimer.restart()
            }
        }
        onDoubleClicked: { clickTimer.stop(); pv.toggleFullscreen() }
        onWheel: (w) => { player.volume = player.volume + (w.angleDelta.y > 0 ? 5 : -5); pv.poke() }
        Timer {
            id: clickTimer
            property bool wantPlay: false
            interval: 230
            onTriggered: {
                var on = player.state === "playing" || player.state === "loading"
                if (wantPlay && !on) player.play()
                else if (!wantPlay && on) player.pause()
                pv.poke()
            }
        }
    }

    // ------------------------------------------------------- music mode stage
    MusicStage {
        id: musicStage
        anchors.fill: parent
        visible: player.isAudio || pv.warm
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
        Behavior on opacity { NumberAnimation { duration: 260; easing.type: Easing.OutCubic } }

        // ---- top-left
        Row {
            x: 32; y: 28
            spacing: 12
            GlassPanel {
                width: 52; height: 52; backdrop: backdrop; geometryTick: pv.glassTick
                IconButton { objectName: "pvMinimize"; anchors.centerIn: parent; icon: "down"; onMedia: true; size: 52; iconSize: 16; tip: qsTr("Minimizar"); onClicked: pv.minimize() }
            }
            GlassPanel {
                width: tl.width + 12; height: 52; backdrop: backdrop; geometryTick: pv.glassTick
                Row {
                    id: tl
                    anchors.centerIn: parent
                    IconButton { objectName: "pvSnapshot"; visible: !player.isAudio; icon: "camera"; onMedia: true; size: 44; iconSize: 16; tip: qsTr("Capturar quadro (S)"); onClicked: player.snapshot() }
                    IconButton {
                        objectName: "pvAspect"
                        visible: !player.isAudio
                        icon: "aspect"; onMedia: true; size: 44; iconSize: 16
                        tip: [qsTr("Ajustar à tela"), qsTr("Preencher tela"), qsTr("Esticar")][[VideoOutput.PreserveAspectFit, VideoOutput.PreserveAspectCrop, VideoOutput.Stretch].indexOf(pv.fillMode)]
                        onClicked: {
                            var modes = [VideoOutput.PreserveAspectFit, VideoOutput.PreserveAspectCrop, VideoOutput.Stretch]
                            pv.fillMode = modes[(modes.indexOf(pv.fillMode) + 1) % 3]
                        }
                    }
                    IconButton { objectName: "pvPip"; visible: !player.isAudio; icon: "pip"; motion: "pop"; onMedia: true; size: 44; iconSize: 16; enabled: player.castingTo === ""; tip: qsTr("Picture-in-picture (Ctrl+P)"); onClicked: pv.pipRequested() }
                    IconButton { objectName: "pvOnTop"; icon: "pin"; motion: "drop"; onMedia: true; size: 44; iconSize: 16; checked: pv.alwaysOnTop; tip: pv.alwaysOnTop ? qsTr("Sempre no topo: ligado (T)") : qsTr("Sempre no topo (T)"); onClicked: pv.toggleOnTop() }
                    IconButton { objectName: "pvCast"; icon: "cast"; onMedia: true; size: 44; iconSize: 16; checked: pv.panel === "cast" || player.castingTo !== ""; tip: player.castingTo ? qsTr("Transmitindo para %1").arg(player.castingTo) : qsTr("Transmitir para a TV"); onClicked: { player.startCastDiscovery(); pv.togglePanel("cast") } }
                    IconButton { objectName: "pvInfo"; icon: "info"; onMedia: true; size: 44; iconSize: 16; checked: pv.panel === "info"; tip: qsTr("Informações da mídia (I)"); onClicked: pv.togglePanel("info") }
                    IconButton { objectName: "pvReveal"; icon: "explorer"; onMedia: true; size: 44; iconSize: 16; tip: (appctl.platform === "windows" ? qsTr("Mostrar no Explorer") : qsTr("Mostrar na pasta")); enabled: player.currentPath.indexOf("://") < 0; onClicked: appctl.reveal(player.currentPath) }
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
            visible: !player.isAudio && !player.resolving   // a link opening has its own message there
            spacing: 36
            SkipButton { objectName: "pvSkipBack"; anchors.verticalCenter: parent.verticalCenter; size: 64; backdrop: backdrop; onClicked: { player.skip(-10000); pv.poke() } }
            GlassPanel {
                objectName: "bigPlay"
                width: 96; height: 96
                backdrop: backdrop; geometryTick: pv.glassTick
                tint: pma.containsMouse ? Qt.rgba(1, 1, 1, 0.22) : Qt.rgba(0.1, 0.1, 0.12, 0.35)
                scale: pma.pressed ? 0.92 : 1
                Behavior on scale { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
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
                Behavior on opacity { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
                text: player.isAudio ? qsTr("OUVINDO AGORA") : qsTr("REPRODUZINDO AGORA")
                color: theme.accent
                font.pixelSize: 12; font.weight: Font.Bold; font.letterSpacing: 2.4
            }
            Text {
                opacity: pv.panel === "" ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
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
                    if (player.queueCount > 1) parts.push(qsTr("%1 de %2").arg(player.currentIndex + 1).arg(player.queueCount))
                    return parts.join("   ·   ")
                }
                color: Qt.rgba(1, 1, 1, 0.7)
                font.pixelSize: 15
                opacity: pv.panel === "" ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
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
                    loopA: player.loopA; loopB: player.loopB
                    chapters: player.chapters
                    enabled: player.seekable
                    previewEnabled: player.hasVideo && player.seekable && player.length > 0 && player.currentPath !== "" && !/^https?:/i.test(player.currentPath)
                    onPreviewRequested: (ms) => {
                        var src = scrubPreview.request(player.currentPath, player.length, ms)
                        if (src) previewSource = src
                    }
                    onScrub: (ms) => player.scrub(ms)      // the picture follows the drag
                    onSeekRequested: (ms) => player.seek(ms)
                }
                Connections {
                    target: scrubPreview
                    function onPreviewReady(path, bucketMs, url) {
                        if (path === player.currentPath && seek.active) seek.previewSource = url
                    }
                }
                Connections {
                    target: player
                    function onCurrentPathChanged() { seek.previewSource = ""; scrubPreview.clear() }
                    function onHasMediaChanged() { if (!player.hasMedia) { seek.previewSource = ""; scrubPreview.clear() } }
                }
                Text {
                    id: tlen
                    objectName: "pvTimeLabel"
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    width: 72; horizontalAlignment: Text.AlignRight
                    property bool remaining: false
                    text: player.length > 0 ? (remaining ? "−" + U.time(player.length - player.time) : U.time(player.length))
                                            : player.state === "loading" ? "" : qsTr("AO VIVO")
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
                    IconButton { objectName: "pvPrev"; icon: "prev"; onMedia: true; size: 44; iconSize: 15; tip: qsTr("Anterior (P)"); onClicked: player.previous() }
                    IconButton { objectName: "pvNext"; icon: "next"; onMedia: true; size: 44; iconSize: 15; tip: qsTr("Próximo (N)"); onClicked: player.next() }
                    Item { width: 8; height: 1 }
                    PillButton { objectName: "pvTracks"; variant: "glass"; icon: "cc"; text: bottom.compact ? "" : qsTr("Faixas"); h: 44; checked: pv.panel === "tracks"; onClicked: pv.togglePanel("tracks") }
                    PillButton { objectName: "pvSpeed"; variant: "glass"; icon: "speed"; text: player.rate.toFixed(2).replace(/\.?0+$/, "") + "×"; h: 44; checked: pv.panel === "speed"; onClicked: pv.togglePanel("speed") }
                    PillButton { objectName: "pvEq"; variant: "glass"; icon: "eq"; text: bottom.compact ? "" : qsTr("Equalizador"); h: 44; checked: pv.panel === "eq"; onClicked: pv.togglePanel("eq") }
                    PillButton { objectName: "pvAdjust"; variant: "glass"; icon: "brightness"; text: bottom.compact ? "" : qsTr("Imagem"); h: 44; visible: !player.isAudio; checked: pv.panel === "adjust"; onClicked: pv.togglePanel("adjust") }
                    PillButton { objectName: "pvChapters"; variant: "glass"; icon: "chapters"; visible: player.chapters.length > 0; text: bottom.compact ? "" : qsTr("Capítulos"); h: 44; checked: pv.panel === "chapters"; onClicked: pv.togglePanel("chapters") }
                    PillButton { objectName: "pvSleep"; variant: "glass"; icon: "timer"; text: player.sleepMinutes > 0 ? qsTr("%1 min").arg(player.sleepMinutes) : (bottom.compact ? "" : qsTr("Timer")); h: 44; checked: pv.panel === "sleep"; onClicked: pv.togglePanel("sleep") }
                }
                Row {
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 6
                    IconButton { objectName: "pvShuffle"; icon: "shuffle"; onMedia: true; size: 44; iconSize: 15; checked: player.shuffle; tip: qsTr("Aleatório"); onClicked: player.toggleShuffle() }
                    IconButton { objectName: "pvLoop"; label: "A-B"; motion: "pop"; onMedia: true; size: 44; iconSize: 15; checked: player.loopA >= 0
                                 tip: player.loopB >= 0 ? qsTr("Repetindo o trecho: L desliga") : player.loopA >= 0 ? qsTr("Marque o fim do trecho (L)") : qsTr("Repetir um trecho, A-B (L)"); onClicked: player.cycleLoop() }
                    IconButton { objectName: "pvRepeat"; icon: player.repeat === 2 ? "repeat1" : "repeat"; onMedia: true; size: 44; iconSize: 15; checked: player.repeat > 0; tip: [qsTr("Repetir: desligado"), qsTr("Repetir: tudo"), qsTr("Repetir: um")][player.repeat]; onClicked: player.cycleRepeat() }
                    PillButton { objectName: "pvQueue"; variant: "glass"; icon: "queue"; text: bottom.compact ? "" : qsTr("A seguir"); h: 44; checked: pv.queueOpen; onClicked: { pv.queueOpen = !pv.queueOpen; pv.poke() } }
                    IconButton { objectName: "pvFullscreen"; icon: pv.fullscreen ? "exitfull" : "fullscreen"; onMedia: true; size: 44; iconSize: 15; tip: qsTr("Tela cheia (F)"); onClicked: pv.toggleFullscreen() }
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
        Chip { text: qsTr("Zerar"); onClicked: st.changed(0) }
    }

    GlassPanel {
        id: popover
        objectName: "popover"
        visible: opacity > 0
        opacity: pv.panel !== "" && pv.controlsVisible ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
        x: player.isAudio ? Math.max(32, (pv.width - (pv.queueOpen ? queuePanel.width + 32 : 0) - width) / 2) : 48 + 120
        anchors.bottom: parent.bottom
        anchors.bottomMargin: player.isAudio ? musicStage.dockBottom + musicStage.dockH + 16 : 36 + 48 + 16
        width: Math.min(pv.width - 96, pv.panel === "eq" ? 620 : pv.panel === "tracks" ? 680 : pv.panel === "adjust" ? 780
               : pv.panel === "info" ? 560 : pv.panel === "chapters" ? 460 : pv.panel === "subs" ? 640 : 420)
        height: panelLoader.item ? panelLoader.item.implicitHeight + 56 : 100
        radius: 28
        backdrop: backdrop
        geometryTick: pv.glassTick + width + height
        tint: Qt.rgba(0.07, 0.07, 0.09, 0.66)
        blurMax: 64
        MouseArea { anchors.fill: parent; hoverEnabled: true; onPositionChanged: pv.poke(); onWheel: (w) => { w.accepted = true; pv.poke() } }
        Loader {
            id: panelLoader
            x: 28; y: 28
            width: parent.width - 56
            sourceComponent: pv.panel === "tracks" ? tracksPanel : pv.panel === "speed" ? speedPanel
                             : pv.panel === "eq" ? eqPanel : pv.panel === "adjust" ? adjustPanel
                             : pv.panel === "sleep" ? sleepPanel : pv.panel === "chapters" ? chaptersPanel
                             : pv.panel === "info" ? infoPanel : pv.panel === "cast" ? castPanel
                             : pv.panel === "subs" ? subsPanel : null
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
                PanelTitle { text: qsTr("Áudio") }
                Item { width: 1; height: 6 }
                Repeater {
                    model: player.audioTracks
                    TrackItem { objectName: "atrack_" + index; text: modelData.name; selected: modelData.id === player.audioTrack; onClicked: player.setAudioTrack(modelData.id) }
                }
                Text { visible: player.audioTracks.length === 0; text: qsTr("Nenhuma faixa"); color: Qt.rgba(1, 1, 1, 0.5); font.pixelSize: 13 }
                Item { width: 1; height: 8 }
                Caption { text: qsTr("ATRASO DO ÁUDIO") }
                Stepper { value: player.audioDelay; onChanged: (v) => player.setAudioDelay(v) }
                Item { width: 1; height: 8 }
                Caption { text: qsTr("SAÍDA DE ÁUDIO") }
                ListView {
                    objectName: "audioDevices"
                    width: parent.width
                    height: Math.min(contentHeight, 42 * 4)
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    model: player.audioDevices
                    delegate: TrackItem { objectName: "adev_" + index; width: ListView.view.width; text: modelData.name; selected: modelData.id === player.audioDevice; onClicked: player.setAudioDevice(modelData.id) }
                }
            }
            Column {
                id: sc
                width: 290
                spacing: 6
                Row {
                    width: parent.width
                    spacing: 6
                    PanelTitle { text: qsTr("Legendas"); width: parent.width - addSub.width - (findSub.visible ? findSub.width + 6 : 0) - 6; elide: Text.ElideRight }
                    Chip { id: findSub; objectName: "findSubBtn"; visible: subsearch.available && player.currentPath !== ""; text: qsTr("Buscar online"); onClicked: { pv.panel = "subs"; subsearch.find(pv.subLanguages) } }
                    Chip { id: addSub; objectName: "addSubBtn"; text: qsTr("+ Arquivo"); onClicked: pv.loadSubtitle() }
                }
                Item { width: 1; height: 6 }
                Repeater {
                    model: player.subtitleTracks
                    TrackItem { objectName: "strack_" + index; text: modelData.name; selected: modelData.id === player.subtitleTrack; onClicked: player.setSubtitleTrack(modelData.id) }
                }
                Text { visible: player.subtitleTracks.length === 0; text: qsTr("Nenhuma legenda — arraste um .srt para a janela"); color: Qt.rgba(1, 1, 1, 0.5); font.pixelSize: 13; width: parent.width; wrapMode: Text.WordWrap }
                Item { width: 1; height: 8 }
                Caption { text: qsTr("SINCRONIA DA LEGENDA") }
                Stepper { value: player.subtitleDelay; onChanged: (v) => player.setSubtitleDelay(v) }
                Item { width: 1; height: 8 }
                Caption { text: qsTr("APARÊNCIA DA LEGENDA") }
                Flow {
                    width: parent.width; spacing: 6
                    Repeater {
                        model: [{ v: "small", t: qsTr("Pequena") }, { v: "normal", t: qsTr("Normal") }, { v: "large", t: qsTr("Grande") }, { v: "huge", t: qsTr("Enorme") }]
                        Chip { objectName: "subSize_" + modelData.v; height: 32; text: modelData.t; selected: player.subtitleStyle.subSize === modelData.v; onClicked: player.setSubtitleStyle("subSize", modelData.v) }
                    }
                }
                Flow {
                    width: parent.width; spacing: 6
                    Repeater {
                        model: [{ k: "subColor", v: "white", t: qsTr("Branca") }, { k: "subColor", v: "yellow", t: qsTr("Amarela") },
                                { k: "subBackground", v: "outline", t: qsTr("Contorno") }, { k: "subBackground", v: "box", t: qsTr("Fundo escuro") }]
                        Chip { objectName: modelData.k + "_" + modelData.v; height: 32; text: modelData.t; selected: player.subtitleStyle[modelData.k] === modelData.v; onClicked: player.setSubtitleStyle(modelData.k, modelData.v) }
                    }
                }
            }
        }
    }

    Component {
        id: speedPanel
        Column {
            spacing: 18
            Row {
                width: parent.width
                PanelTitle { text: qsTr("Velocidade"); width: parent.width - big.width }
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
                PanelTitle { text: bottom.compact ? "" : qsTr("Equalizador"); width: parent.width - eqSw.width }
                Toggle { id: eqSw; objectName: "eqToggle"; checked: player.eqEnabled; onToggled: (on) => player.setEqEnabled(on) }
            }
            Row {
                width: parent.width
                spacing: 12
                Toggle { id: normSw; objectName: "normalizeToggle"; anchors.verticalCenter: parent.verticalCenter; checked: player.normalize; onToggled: (on) => player.setNormalize(on) }
                Column {
                    anchors.verticalCenter: parent.verticalCenter
                    width: parent.width - normSw.width - 12
                    Text { text: qsTr("Normalizar volume"); color: "white"; font.pixelSize: 14; font.weight: Font.DemiBold }
                    Text { width: parent.width; wrapMode: Text.WordWrap; text: qsTr("Equilibra diálogos baixos e explosões altas."); color: Qt.rgba(1, 1, 1, 0.6); font.pixelSize: 12 }
                }
            }
            Flow {
                width: parent.width
                spacing: 6
                Repeater {
                    model: player.eqPresets
                    Chip { objectName: "eqp_" + index; text: qsTr(modelData); selected: index === player.eqPreset; height: 32; onClicked: player.setEqPreset(index) }
                }
            }
            Row {
                spacing: 0
                opacity: player.eqEnabled ? 1 : 0.4
                Column {
                    width: 56; spacing: 8
                    MiniSlider { vertical: true; height: 150; anchors.horizontalCenter: parent.horizontalCenter; from: -20; to: 20; center: 0; value: player.eqPreamp; onMedia: true; accent: "white"; onMoved: (v) => player.setEqPreamp(v) }
                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: qsTr("Pré"); color: Qt.rgba(1, 1, 1, 0.6); font.pixelSize: 11 }
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
        Row {
            spacing: 28
            Column {
                width: 400
                spacing: 14
                Row {
                    width: parent.width
                    PanelTitle { text: bottom.compact ? "" : qsTr("Imagem"); width: parent.width - rs.width }
                    Chip { id: rs; objectName: "adjReset"; text: qsTr("Restaurar"); onClicked: { player.resetAdjust(); pv.resetGeometry() } }
                }
                Repeater {
                    model: [
                        { key: "brightness", label: qsTr("Brilho"), from: 0, to: 2, center: 1 },
                        { key: "contrast", label: qsTr("Contraste"), from: 0, to: 2, center: 1 },
                        { key: "saturation", label: qsTr("Saturação"), from: 0, to: 3, center: 1 },
                        { key: "gamma", label: qsTr("Gama"), from: 0.1, to: 3, center: 1 },
                        { key: "hue", label: qsTr("Matiz"), from: -180, to: 180, center: 0 },
                    ]
                    Row {
                        spacing: 12
                        Text { width: 90; anchors.verticalCenter: parent.verticalCenter; text: modelData.label; color: "white"; font.pixelSize: 14; elide: Text.ElideRight }
                        MiniSlider {
                            objectName: "adj_" + modelData.key
                            width: 240; anchors.verticalCenter: parent.verticalCenter
                            from: modelData.from; to: modelData.to; center: modelData.center
                            value: player.adjust[modelData.key]; onMedia: true
                            onMoved: (v) => player.setAdjust(modelData.key, v)
                        }
                        Text { width: 40; anchors.verticalCenter: parent.verticalCenter; horizontalAlignment: Text.AlignRight; text: player.adjust[modelData.key].toFixed(modelData.key === "hue" ? 0 : 2); color: Qt.rgba(1, 1, 1, 0.6); font.pixelSize: 12; font.features: { "tnum": 1 } }
                    }
                }
                Caption { text: qsTr("ENQUADRAMENTO") }
                Segmented {
                    onMedia: true
                    options: [{ text: qsTr("Ajustar"), value: VideoOutput.PreserveAspectFit }, { text: qsTr("Preencher"), value: VideoOutput.PreserveAspectCrop }, { text: qsTr("Esticar"), value: VideoOutput.Stretch }]
                    value: pv.fillMode
                    onPicked: (v) => pv.fillMode = v
                }
            }
            Column {
                width: 296
                spacing: 10
                Item { width: 1; height: 40 }
                Caption { text: qsTr("GIRAR E ESPELHAR") }
                Flow {
                    width: parent.width; spacing: 6
                    Chip { objectName: "rotateLeft"; height: 32; text: "↺ 90°"; onClicked: pv.rotate(-90) }
                    Chip { objectName: "rotateRight"; height: 32; text: "↻ 90°"; onClicked: pv.rotate(90) }
                    Chip { objectName: "mirror"; height: 32; text: qsTr("Espelhar"); selected: pv.videoMirror; onClicked: pv.videoMirror = !pv.videoMirror }
                }
                Caption { text: qsTr("PROPORÇÃO") }
                Flow {
                    width: parent.width; spacing: 6
                    Repeater {
                        model: [{ v: "", t: qsTr("Original") }, { v: "16:9", t: "16:9" }, { v: "4:3", t: "4:3" }, { v: "21:9", t: "21:9" }, { v: "1:1", t: "1:1" }]
                        Chip { objectName: "aspect_" + (modelData.v || "auto"); height: 32; text: modelData.t; selected: pv.videoAspect === modelData.v; onClicked: pv.videoAspect = modelData.v }
                    }
                }
                Caption { text: qsTr("ZOOM") }
                Flow {
                    width: parent.width; spacing: 6
                    Repeater {
                        model: [1, 1.25, 1.5, 2]
                        Chip { objectName: "zoom_" + modelData; height: 32; text: Math.round(modelData * 100) + "%"; selected: Math.abs(pv.videoZoom - modelData) < 0.01; onClicked: pv.videoZoom = modelData }
                    }
                }
                Caption { text: qsTr("DESENTRELAÇAR") }
                Segmented {
                    onMedia: true
                    options: [{ text: qsTr("Automático"), value: "auto" }, { text: qsTr("Ligado"), value: "on" }, { text: qsTr("Desligado"), value: "off" }]
                    value: player.deinterlace
                    onPicked: (v) => player.setDeinterlace(v)
                }
            }
        }
    }

    Component {
        id: chaptersPanel
        Column {
            spacing: 6
            PanelTitle { text: qsTr("Capítulos") }
            Item { width: 1; height: 6 }
            ListView {
                width: parent.width
                height: Math.min(contentHeight, 42 * 8)
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                model: player.chapters
                delegate: TrackItem {
                    objectName: "chapter_" + index
                    width: ListView.view.width
                    text: U.time(modelData.time) + "    " + modelData.name
                    selected: index === player.chapter
                    onClicked: player.setChapter(index)
                }
            }
        }
    }

    Component {
        id: infoPanel
        Column {
            id: ip
            spacing: 10
            property var info: player.mediaInfo()
            Timer { interval: 1000; running: true; repeat: true; onTriggered: ip.info = player.mediaInfo() }
            function size(b) { return b >= 1073741824 ? (b / 1073741824).toFixed(2) + " GB" : (b / 1048576).toFixed(1) + " MB" }
            readonly property var lines: {
                var i = ip.info, out = []
                out.push([qsTr("Arquivo"), i.path ? i.path.split(/[\\/]/).pop() : "—"])
                if (i.container) out.push([qsTr("Formato"), i.container + (i.size ? "  ·  " + ip.size(i.size) : "")])
                if (i.length > 0) out.push([qsTr("Duração"), U.time(i.length) + (i.bitrate ? "  ·  " + i.bitrate + " kbit/s" : "")])
                for (var k = 0; k < i.tracks.length; k++) {
                    var t = i.tracks[k]
                    if (t.type === "video")
                        out.push([qsTr("Vídeo"), t.codec.toUpperCase() + "  ·  " + t.width + " × " + t.height + (t.fps ? "  ·  " + t.fps.toFixed(t.fps % 1 ? 2 : 0) + " fps" : "")])
                    else if (t.type === "audio")
                        out.push([qsTr("Áudio"), t.codec.toUpperCase() + "  ·  " + qsTr("%n canal(is)", "", t.channels) + "  ·  " + (t.rate / 1000) + " kHz" + (t.language ? "  ·  " + t.language : "")])
                    else if (t.type === "text")
                        out.push([qsTr("Legenda"), t.codec.toUpperCase() + (t.language ? "  ·  " + t.language : "")])
                }
                if (i.videoSize) out.push([qsTr("Na tela"), i.videoSize + "  ·  " + qsTr("%1 quadros, %2 descartados").arg(i.displayed).arg(i.dropped)])
                return out
            }
            Row {
                width: parent.width
                PanelTitle { text: qsTr("Informações da mídia"); width: parent.width - cp.width }
                Chip {
                    id: cp; objectName: "infoCopy"; text: qsTr("Copiar")
                    onClicked: {
                        var s = []
                        for (var j = 0; j < ip.lines.length; j++) s.push(ip.lines[j][0] + ": " + ip.lines[j][1])
                        appctl.copyText(s.join("\n"))
                    }
                }
            }
            Item { width: 1; height: 4 }
            Repeater {
                model: ip.lines
                Row {
                    width: ip.width
                    spacing: 12
                    Text { width: 110; text: modelData[0]; color: Qt.rgba(1, 1, 1, 0.55); font.pixelSize: 13; elide: Text.ElideRight }
                    Text { width: parent.width - 122; text: modelData[1]; color: "white"; font.pixelSize: 13; wrapMode: Text.Wrap; font.features: { "tnum": 1 } }
                }
            }
        }
    }

    Component {
        id: castPanel
        Column {
            spacing: 6
            PanelTitle { text: qsTr("Transmitir") }
            Text { width: parent.width; wrapMode: Text.WordWrap; text: qsTr("Aparelhos Chromecast na mesma rede Wi-Fi aparecem aqui."); color: Qt.rgba(1, 1, 1, 0.6); font.pixelSize: 13 }
            Item { width: 1; height: 6 }
            TrackItem { objectName: "castLocal"; text: qsTr("Este computador"); selected: player.castingTo === ""; onClicked: player.setRenderer(-1) }
            Repeater {
                model: player.renderers
                TrackItem { objectName: "cast_" + index; text: modelData.name; selected: player.castingTo === modelData.name; onClicked: player.setRenderer(index) }
            }
            Row {
                visible: player.renderers.length === 0
                spacing: 10
                leftPadding: 12; topPadding: 6
                Item {
                    width: 14; height: 14
                    anchors.verticalCenter: parent.verticalCenter
                    RotationAnimation on rotation { from: 0; to: 360; duration: 900; loops: Animation.Infinite; running: pv.panel === "cast" }
                    Rectangle { width: 5; height: 5; radius: 2.5; color: theme.accent; x: 4.5 }
                }
                Text { text: qsTr("Procurando aparelhos na rede…"); color: Qt.rgba(1, 1, 1, 0.7); font.pixelSize: 13 }
            }
        }
    }

    Component {
        id: subsPanel
        Column {
            spacing: 8
            Row {
                width: parent.width
                PanelTitle { text: qsTr("Legendas online"); width: parent.width - back.width }
                Chip { id: back; text: qsTr("Voltar"); onClicked: pv.panel = "tracks" }
            }
            Text { width: parent.width; wrapMode: Text.WordWrap; text: qsTr("Do OpenSubtitles.com. As marcadas com ✓ foram feitas para este arquivo."); color: Qt.rgba(1, 1, 1, 0.6); font.pixelSize: 13 }
            Text {
                visible: subsearch.status !== ""
                width: parent.width; wrapMode: Text.WordWrap
                text: subsearch.status; color: "white"; font.pixelSize: 14; topPadding: 6
            }
            ListView {
                objectName: "subResults"
                width: parent.width
                height: Math.min(contentHeight, 42 * 7)
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                model: subsearch.results
                delegate: TrackItem {
                    objectName: "subResult_" + index
                    width: ListView.view.width
                    text: (modelData.exact ? "✓  " : "") + modelData.language.toUpperCase() + "   " + modelData.name
                    onClicked: subsearch.fetch(index)
                }
            }
        }
    }

    Component {
        id: sleepPanel
        Column {
            spacing: 16
            PanelTitle { text: qsTr("Timer de sono") }
            Text { width: parent.width; wrapMode: Text.WordWrap; text: player.sleepMinutes > 0 ? qsTr("Pausando em %1 min.").arg(player.sleepMinutes) : qsTr("Pausa a reprodução automaticamente."); color: Qt.rgba(1, 1, 1, 0.7); font.pixelSize: 14 }
            Flow {
                width: parent.width; spacing: 8
                Repeater {
                    model: [15, 30, 45, 60, 90, 120]
                    Chip { objectName: "sleep_" + modelData; text: qsTr("%1 min").arg(modelData); onClicked: player.setSleepTimer(modelData) }
                }
                Chip { objectName: "sleepOff"; text: qsTr("Desligar"); selected: player.sleepMinutes === 0; onClicked: player.setSleepTimer(0) }
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
        MouseArea { anchors.fill: parent; hoverEnabled: true; onPositionChanged: pv.poke(); onWheel: (w) => { w.accepted = true; pv.poke() } }

        Text { x: 24; y: 24; text: qsTr("A seguir"); color: "white"; font.family: theme.displayFont; font.pixelSize: 26; font.weight: Font.DemiBold }
        Text { anchors.right: parent.right; anchors.rightMargin: 24; y: 34; text: qsTr("%n item(ns)", "", player.queueCount); color: Qt.rgba(1, 1, 1, 0.55); font.pixelSize: 13 }
        ListView {
            id: ql
            anchors.fill: parent
            anchors.topMargin: 72; anchors.margins: 12
            clip: true
            spacing: 4
            model: player.queueModel
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
                    Text { width: parent.width; elide: Text.ElideRight; text: parent.parent.cur ? (player.playing ? qsTr("Reproduzindo") : qsTr("Pausado")) : (parent.parent.info.duration ? U.time(parent.parent.info.duration) : ""); color: Qt.rgba(1, 1, 1, 0.55); font.pixelSize: 12 }
                }
                MouseArea { id: qm; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: player.playAt(index) }
            }
        }
    }
}
