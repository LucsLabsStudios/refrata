import QtQuick
import QtQuick.Effects
import "Utils.js" as U

// Music mode of the player: cover carousel of the queue + glass dock with the controls.
Item {
    id: stage
    objectName: "musicStage"
    property Item pv: null              // the PlayerView (panels, queue panel, poke)
    property Item backdrop: null        // what the dock's glass blurs
    property real sideInset: 0          // room taken on the right by the queue panel
    readonly property real dockH: 84
    readonly property real dockBottom: 40

    function hue(s) {
        var h = 7
        for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 104729
        return (h % 360) / 360
    }
    function infoFor(path) { library.revision; return library.info(path) }
    // the library already knows the tags; the player only learns them once the file is parsed,
    // so prefer the library and avoid flashing the file name on every track change
    readonly property var curInfo: infoFor(player.currentPath)
    readonly property string curTitle: curInfo.title || player.title
    readonly property string curArtist: curInfo.artist || player.artist
    readonly property string curAlbum: curInfo.album || player.album
    SmoothTime { id: clock; active: stage.visible }
    function artFor(i, path) {
        if (i === player.currentIndex && player.artwork) return player.artwork
        var inf = infoFor(path)
        return inf.thumb || ""
    }

    // ------------------------------------------------------------------ eyebrow
    Column {
        anchors.horizontalCenter: carousel.horizontalCenter
        y: 36
        spacing: 4
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "OUVINDO AGORA"
            color: theme.accent
            font.pixelSize: 12; font.weight: Font.Bold; font.letterSpacing: 2.4
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: {
                var parts = []
                if (stage.curAlbum) parts.push(stage.curAlbum)
                if (player.queue.length > 1) parts.push((player.currentIndex + 1) + " de " + player.queue.length)
                return parts.join("  ·  ")
            }
            visible: text !== ""
            color: Qt.rgba(1, 1, 1, 0.7)
            font.pixelSize: 13
        }
    }

    // ----------------------------------------------------------------- carousel
    Item {
        id: carousel
        objectName: "musicCarousel"
        x: 0; y: 96
        width: stage.width - stage.sideInset
        height: stage.height - y - stage.dockH - stage.dockBottom - 24
        Behavior on width { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }

        // position along the queue; glides to the current track. Long jumps start 2.5 cards
        // away instead of streaming every card in between past the viewer.
        property real pos: 0          // not bound: glide() owns it
        Component.onCompleted: pos = Math.max(0, target)
        readonly property int target: player.currentIndex
        onTargetChanged: glide()
        NumberAnimation { id: posAnim; target: carousel; property: "pos"; duration: 560; easing.type: Easing.OutCubic }
        function glide() {
            posAnim.stop()
            var t = target
            if (!stage.visible || t < 0) { pos = Math.max(0, t); return }
            if (Math.abs(t - pos) > 2.5) pos = t + (t > pos ? -2.5 : 2.5)
            posAnim.from = pos; posAnim.to = t
            posAnim.start()
        }
        readonly property real cardW: Math.max(180, Math.min(380, (height - 96) * 0.92, width * 0.30))

        // one hover tracker for the whole carousel instead of one per card: overlapping
        // cards would all report hovered; the topmost card under the pointer wins.
        property int hoverIndex: -1
        function pickHover() {
            if (!carouselHover.hovered) { hoverIndex = -1; return }
            var pt = carouselHover.point.position, best = -1, bestZ = -1e9
            for (var i = 0; i < cards.count; i++) {
                var c = cards.itemAt(i)
                if (!c || !c.visible || c.z <= bestZ) continue
                var q = c.mapFromItem(carousel, pt.x, pt.y)
                if (q.x >= 0 && q.y >= 0 && q.x <= c.width && q.y <= c.height) { best = i; bestZ = c.z }
            }
            hoverIndex = best
        }
        HoverHandler {
            id: carouselHover
            cursorShape: carousel.hoverIndex >= 0 ? Qt.PointingHandCursor : Qt.ArrowCursor
            onPointChanged: carousel.pickHover()
            onHoveredChanged: carousel.pickHover()
        }
        onPosChanged: if (hoverIndex >= 0 || carouselHover.hovered) pickHover()

        Repeater {
            id: cards
            model: player.queue
            delegate: Item {
                id: card
                objectName: "mcard_" + index
                readonly property real d: index - carousel.pos
                readonly property real ad: Math.abs(d)
                readonly property bool near: ad < 3.4
                readonly property bool cur: index === player.currentIndex
                readonly property var info: near ? stage.infoFor(modelData.path) : ({})
                readonly property string art: near ? stage.artFor(index, modelData.path) : ""
                readonly property bool hovered: carousel.hoverIndex === index
                // library items carry the tag title in `title`; files outside the library keep the queue's
                readonly property string title: cur ? stage.curTitle : (info.title || modelData.title || "")
                readonly property string artist: cur ? stage.curArtist : (info.artist || "")
                visible: near && opacity > 0
                width: carousel.cardW
                height: carousel.cardW + 96
                readonly property real off: carousel.cardW * (0.64 * Math.min(ad, 1) + 0.38 * Math.max(0, Math.min(ad, 3) - 1))
                x: carousel.width / 2 + (d < 0 ? -off : off) - width / 2
                y: (carousel.height - height) / 2
                z: 100 - ad * 10
                // depth from the carousel position (no Behavior: pos is already animated, a second
                // animation would lag behind it); hover/press feedback is a separate, own-animated factor
                property real feel: tap.pressed ? 0.97 : (card.hovered && ad > 0.5 ? 1.035 : 1)
                Behavior on feel { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
                scale: (1 - 0.16 * Math.min(ad, 1) - 0.09 * Math.max(0, Math.min(ad, 3) - 1)) * feel
                opacity: ad < 2.3 ? 1 : Math.max(0, 1 - (ad - 2.3) / 0.8)
                transform: Rotation {
                    origin.x: card.width / 2; origin.y: card.height / 2
                    axis { x: 0; y: 1; z: 0 }
                    angle: Math.max(-1, Math.min(1, card.d)) * 24   // side cards turn to face the centre
                }
                layer.enabled: visible
                layer.effect: MultiEffect {
                    shadowEnabled: true; shadowBlur: 1.0; shadowOpacity: 0.55
                    shadowVerticalOffset: 22; shadowColor: "black"; autoPaddingEnabled: true
                }

                // frosted card: a dark base keeps the cards behind from showing through,
                // the light layer on top gives the glass look against the coloured backdrop
                Rectangle {
                    anchors.fill: parent
                    radius: 30
                    color: Qt.rgba(0.05, 0.04, 0.06, 0.50)
                }
                Rectangle {
                    anchors.fill: parent
                    radius: 30
                    color: Qt.rgba(1, 1, 1, card.cur ? 0.16 : 0.11)
                    border.width: 1
                    border.color: Qt.rgba(1, 1, 1, card.cur ? 0.38 : 0.24)
                    Rectangle {   // top sheen
                        anchors.fill: parent; anchors.margins: 1
                        radius: 29
                        gradient: Gradient {
                            GradientStop { position: 0; color: Qt.rgba(1, 1, 1, 0.12) }
                            GradientStop { position: 0.45; color: Qt.rgba(1, 1, 1, 0.0) }
                        }
                    }
                }
                // cover
                Item {
                    id: coverBox
                    x: 12; y: 12
                    width: parent.width - 24; height: width
                    Rectangle {
                        anchors.fill: parent; radius: 22
                        readonly property real h: stage.hue(card.title)
                        gradient: Gradient {
                            orientation: Gradient.Vertical
                            GradientStop { position: 0; color: Qt.hsla(parent.h, 0.7, 0.52, 1) }
                            GradientStop { position: 1; color: Qt.hsla((parent.h + 0.08) % 1, 0.75, 0.28, 1) }
                        }
                        visible: coverImg.status !== Image.Ready
                        Icon { anchors.centerIn: parent; name: modelData.audio === false ? "film" : "music"; size: parent.width * 0.26; color: Qt.rgba(1, 1, 1, 0.55) }
                    }
                    RoundedImage { id: coverImg; anchors.fill: parent; radius: 22; source: card.art; sourceWidth: 760 }
                    // hover affordance: what a click will do (play this one / pause / resume)
                    Rectangle {
                        objectName: "mcardPlay_" + index
                        anchors.centerIn: parent
                        width: Math.min(76, parent.width * 0.24); height: width; radius: width / 2
                        color: Qt.rgba(1, 1, 1, 0.94)
                        readonly property bool show: card.hovered && card.ad < 1.5 || (card.cur && !player.playing && player.state !== "loading")
                        opacity: show ? 1 : 0
                        scale: show ? (tap.pressed ? 0.9 : 1) : 0.75
                        visible: opacity > 0.01
                        Behavior on opacity { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
                        Behavior on scale { NumberAnimation { duration: 240; easing.type: Easing.OutBack } }
                        PlayGlyph {
                            anchors.centerIn: parent
                            readonly property bool pauseIcon: card.cur && (player.playing || player.state === "loading")
                            anchors.horizontalCenterOffset: pauseIcon ? 0 : parent.width * 0.04
                            size: parent.width * 0.36
                            playing: pauseIcon
                            color: "#111"
                        }
                    }
                }
                // caption
                Column {
                    anchors.top: coverBox.bottom; anchors.topMargin: 16
                    anchors.left: parent.left; anchors.right: parent.right
                    anchors.leftMargin: 20; anchors.rightMargin: 20
                    spacing: 3
                    Text {
                        width: parent.width
                        horizontalAlignment: Text.AlignHCenter
                        elide: Text.ElideRight
                        text: card.title || "—"
                        color: "white"
                        font.family: theme.displayFont
                        font.pixelSize: Math.max(15, carousel.cardW * 0.062)
                        font.weight: Font.DemiBold
                    }
                    Text {
                        width: parent.width
                        horizontalAlignment: Text.AlignHCenter
                        elide: Text.ElideRight
                        text: card.artist || "Artista desconhecido"
                        color: Qt.rgba(1, 1, 1, 0.62)
                        font.pixelSize: Math.max(12, carousel.cardW * 0.042)
                    }
                }
                // depth dimming for the side cards
                Rectangle {
                    anchors.fill: parent; radius: 30
                    color: "black"
                    opacity: 0.30 * Math.min(card.ad, 1.6)
                }
                TapHandler {
                    id: tap
                    gesturePolicy: TapHandler.ReleaseWithinBounds
                    onTapped: {
                        if (card.cur) player.togglePlay()
                        else player.playAt(index)
                        if (stage.pv) stage.pv.poke()
                    }
                }
            }
        }

        // empty queue (should not happen in music mode, but never show a blank stage)
        Text {
            anchors.centerIn: parent
            visible: player.queue.length === 0
            text: "Nada tocando"
            color: Qt.rgba(1, 1, 1, 0.6)
            font.pixelSize: 18
        }
    }

    // --------------------------------------------------------------------- dock
    GlassPanel {
        id: dock
        objectName: "musicDock"
        anchors.horizontalCenter: carousel.horizontalCenter
        anchors.bottom: parent.bottom; anchors.bottomMargin: stage.dockBottom
        width: Math.min(1100, carousel.width - 64)
        height: stage.dockH
        radius: height / 2
        backdrop: stage.backdrop
        geometryTick: (stage.pv ? stage.pv.glassTick : 0) + width
        tint: Qt.rgba(1, 1, 1, 0.10)
        border: Qt.rgba(1, 1, 1, 0.26)
        blurMax: 64
        readonly property bool compact: width < 900
        HoverHandler { onHoveredChanged: if (stage.pv) stage.pv.pinned = hovered }

        Row {
            id: transport
            anchors.left: parent.left; anchors.leftMargin: 18
            anchors.verticalCenter: parent.verticalCenter
            spacing: 6
            IconButton { objectName: "mpPrev"; anchors.verticalCenter: parent.verticalCenter; icon: "prev"; onMedia: true; size: 48; iconSize: 17; tip: "Anterior (P)"; onClicked: player.previous() }
            // glowing accent orb inside a progress ring (the track's position runs around it)
            Item {
                objectName: "mpPlay"
                width: 70; height: 70
                anchors.verticalCenter: parent.verticalCenter
                ProgressRing {
                    objectName: "mpRing"
                    anchors.fill: parent
                    thickness: 3
                    value: player.length > 0 ? clock.value / player.length : 0
                    color: Qt.lighter(theme.accent, 1.15)
                    trackColor: Qt.rgba(1, 1, 1, 0.12)
                }
                Item {
                    anchors.centerIn: parent
                    width: 54; height: 54
                    scale: playMa.pressed ? 0.92 : (playMa.containsMouse ? 1.06 : 1)
                    Behavior on scale { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
                    // soft glow that breathes while playing
                    Rectangle {
                        anchors.centerIn: parent
                        width: parent.width; height: width; radius: width / 2
                        color: theme.accent
                        property real pulse: 0.55
                        opacity: player.playing ? pulse : 0.25
                        layer.enabled: true
                        layer.effect: MultiEffect { blurEnabled: true; blur: 1.0; blurMax: 24; autoPaddingEnabled: true }
                        SequentialAnimation on pulse {
                            running: player.playing && stage.visible
                            loops: Animation.Infinite
                            NumberAnimation { to: 0.75; duration: 1400; easing.type: Easing.InOutSine }
                            NumberAnimation { to: 0.4; duration: 1400; easing.type: Easing.InOutSine }
                        }
                    }
                    Rectangle {
                        anchors.fill: parent; radius: width / 2
                        gradient: Gradient {
                            GradientStop { position: 0; color: Qt.lighter(theme.accent, 1.3) }
                            GradientStop { position: 1; color: Qt.darker(theme.accent, 1.2) }
                        }
                        border.width: 1; border.color: Qt.rgba(1, 1, 1, 0.35)
                        // top highlight (relief)
                        Rectangle {
                            x: parent.width * 0.18; y: 3
                            width: parent.width * 0.64; height: parent.height * 0.42; radius: height / 2
                            gradient: Gradient {
                                GradientStop { position: 0; color: Qt.rgba(1, 1, 1, 0.38) }
                                GradientStop { position: 1; color: Qt.rgba(1, 1, 1, 0) }
                            }
                        }
                    }
                    PlayGlyph { anchors.centerIn: parent; anchors.horizontalCenterOffset: player.playing ? 0 : 2; size: 20; playing: player.playing; color: "white" }
                }
                MouseArea { id: playMa; anchors.fill: parent; anchors.margins: 6; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: player.togglePlay() }
            }
            IconButton { objectName: "mpNext"; anchors.verticalCenter: parent.verticalCenter; icon: "next"; onMedia: true; size: 48; iconSize: 17; tip: "Próximo (N)"; onClicked: player.next() }
        }

        Row {
            id: tools
            anchors.right: parent.right; anchors.rightMargin: 18
            anchors.verticalCenter: parent.verticalCenter
            spacing: 2
            IconButton { objectName: "mpShuffle"; anchors.verticalCenter: parent.verticalCenter; icon: "shuffle"; onMedia: true; size: 44; iconSize: 15; checked: player.shuffle; tip: "Aleatório"; onClicked: player.toggleShuffle() }
            IconButton { objectName: "mpRepeat"; anchors.verticalCenter: parent.verticalCenter; icon: player.repeat === 2 ? "repeat1" : "repeat"; onMedia: true; size: 44; iconSize: 15; checked: player.repeat > 0; tip: ["Repetir: desligado", "Repetir: tudo", "Repetir: um"][player.repeat]; onClicked: player.cycleRepeat() }
            IconButton { objectName: "mpEq"; anchors.verticalCenter: parent.verticalCenter; icon: "eq"; onMedia: true; size: 44; iconSize: 15; checked: stage.pv && stage.pv.panel === "eq"; tip: "Equalizador"; onClicked: stage.pv.togglePanel("eq") }
            IconButton { objectName: "mpSpeed"; anchors.verticalCenter: parent.verticalCenter; icon: "speed"; onMedia: true; size: 44; iconSize: 15; checked: stage.pv && stage.pv.panel === "speed"; tip: "Velocidade " + player.rate.toFixed(2).replace(/\.?0+$/, "") + "×"; onClicked: stage.pv.togglePanel("speed") }
            IconButton { objectName: "mpSleep"; anchors.verticalCenter: parent.verticalCenter; icon: "timer"; onMedia: true; size: 44; iconSize: 15; checked: (stage.pv && stage.pv.panel === "sleep") || player.sleepMinutes > 0; tip: player.sleepMinutes > 0 ? "Timer: " + player.sleepMinutes + " min" : "Timer de sono"; onClicked: stage.pv.togglePanel("sleep") }
            IconButton { objectName: "mpQueue"; anchors.verticalCenter: parent.verticalCenter; icon: "queue"; onMedia: true; size: 44; iconSize: 15; checked: stage.pv && stage.pv.queueOpen; tip: "A seguir"; onClicked: { stage.pv.queueOpen = !stage.pv.queueOpen; stage.pv.poke() } }
            Item { width: 6; height: 1 }
            IconButton {
                objectName: "mpMute"
                anchors.verticalCenter: parent.verticalCenter
                icon: player.muted || player.volume === 0 ? "mute" : player.volume < 34 ? "vol1" : player.volume < 67 ? "vol2" : "volume"
                onMedia: true; size: 40; iconSize: 15
                tip: player.muted ? "Sem som" : "Volume " + player.volume + "%"
                onClicked: player.toggleMute()
            }
            MiniSlider {
                objectName: "mpVolume"
                visible: !dock.compact
                width: 96; anchors.verticalCenter: parent.verticalCenter
                from: 0; to: 150; value: player.muted ? 0 : player.volume
                onMedia: true; accent: "white"
                mark: 100; boostColor: theme.accent
                onMoved: (v) => player.volume = v
            }
        }

        // now playing capsule
        Rectangle {
            id: now
            objectName: "mpNow"
            anchors.left: transport.right; anchors.leftMargin: 16
            anchors.right: tools.left; anchors.rightMargin: 16
            anchors.verticalCenter: parent.verticalCenter
            height: 60; radius: 18
            color: Qt.rgba(0, 0, 0, 0.42)
            border.width: 1; border.color: Qt.rgba(1, 1, 1, 0.08)
            clip: true

            Item {
                id: nthumb
                x: 8; anchors.verticalCenter: parent.verticalCenter; anchors.verticalCenterOffset: -2
                width: 42; height: 42
                Rectangle {
                    anchors.fill: parent; radius: 10
                    readonly property real h: stage.hue(player.title)
                    color: Qt.hsla(h, 0.65, 0.42, 1)
                    visible: nimg.status !== Image.Ready
                    Icon { anchors.centerIn: parent; name: "music"; size: 15; color: Qt.rgba(1, 1, 1, 0.7) }
                }
                RoundedImage { id: nimg; anchors.fill: parent; radius: 10; source: stage.artFor(player.currentIndex, player.currentPath); sourceWidth: 120 }
            }
            Column {
                anchors.left: nthumb.right; anchors.leftMargin: 12
                anchors.right: nmeta.left; anchors.rightMargin: 12
                anchors.verticalCenter: parent.verticalCenter; anchors.verticalCenterOffset: -2
                spacing: 1
                Text { objectName: "mpTitle"; width: parent.width; elide: Text.ElideRight; text: stage.curTitle || "—"; color: "white"; font.pixelSize: 14; font.weight: Font.DemiBold }
                Text { objectName: "mpArtist"; width: parent.width; elide: Text.ElideRight; text: stage.curArtist || "Artista desconhecido"; color: Qt.rgba(1, 1, 1, 0.6); font.pixelSize: 12 }
            }
            Row {
                id: nmeta
                anchors.right: parent.right; anchors.rightMargin: 14
                anchors.verticalCenter: parent.verticalCenter; anchors.verticalCenterOffset: -2
                spacing: 12
                EqualizerBars { anchors.verticalCenter: parent.verticalCenter; running: player.playing; color: "white"; opacity: 0.85 }
                Text {
                    objectName: "mpTime"
                    anchors.verticalCenter: parent.verticalCenter
                    visible: !dock.compact
                    text: U.time(seekMa.dragging ? seekMa.dragMs : player.time) + "  /  " + (player.length > 0 ? U.time(player.length) : "ao vivo")
                    color: Qt.rgba(1, 1, 1, 0.7); font.pixelSize: 12; font.weight: Font.DemiBold
                    font.features: { "tnum": 1 }
                }
            }
            // progress line along the bottom edge; the whole lower strip seeks
            Rectangle {
                id: track
                x: 12; width: parent.width - 24
                anchors.bottom: parent.bottom; anchors.bottomMargin: 5
                height: seekMa.containsMouse || seekMa.dragging ? 4 : 3
                radius: 2
                color: Qt.rgba(1, 1, 1, 0.18)
                Rectangle {
                    width: parent.width * (player.length > 0 ? Math.min(1, (seekMa.dragging ? seekMa.dragMs : clock.value) / player.length) : 0)
                    height: parent.height; radius: 2
                    color: "white"
                }
            }
            MouseArea {
                id: seekMa
                objectName: "mpSeek"
                anchors.left: parent.left; anchors.right: parent.right
                anchors.bottom: parent.bottom
                height: 16
                hoverEnabled: true
                enabled: player.seekable && player.length > 0
                cursorShape: Qt.PointingHandCursor
                property bool dragging: false
                property real dragMs: 0
                function msAt(mx) { return Math.max(0, Math.min(1, (mx - track.x) / track.width)) * player.length }
                onPressed: (m) => { dragging = true; dragMs = msAt(m.x) }
                onPositionChanged: (m) => { if (dragging) dragMs = msAt(m.x) }
                onReleased: (m) => { player.seek(msAt(m.x)); dragging = false }
                onCanceled: dragging = false
            }
        }
    }
}
