import QtQuick
import QtQuick.Window
import QtQuick.Dialogs
import "Utils.js" as U

Window {
    id: win
    width: 1440
    height: 900
    minimumWidth: 1040
    minimumHeight: 660
    // hidden when started for the quick player (file from Explorer) or in the tray (--background)
    visible: !startHidden
    color: theme.bg
    title: player.title ? player.title + "  —  Refrata" : "Refrata"

    property int page: 0
    property bool alwaysOnTop: false
    flags: Qt.Window | (alwaysOnTop ? Qt.WindowStaysOnTopHint : 0)
    onAlwaysOnTopChanged: { toast.show(alwaysOnTop ? qsTr("Sempre no topo") : qsTr("Sempre no topo desligado")); Qt.callLater(restyle) }
    property bool playerOpen: false
    // text fields must not keep keyboard focus (and swallow Space/arrows) once the player shows
    onPlayerOpenChanged: if (playerOpen) focusSink.forceActiveFocus()
    onPageChanged: focusSink.forceActiveFocus()
    Item { id: focusSink; focus: true }
    property int restoreVisibility: Window.Windowed
    readonly property bool fullscreen: visibility === Window.FullScreen

    function toggleFullscreen() {
        if (fullscreen) {
            visibility = restoreVisibility === Window.Maximized ? Window.Maximized : Window.Windowed
        } else {
            restoreVisibility = visibility
            if (!playerOpen && player.hasMedia) playerOpen = true
            visibility = Window.FullScreen
        }
    }
    function isAudioPath(p) { return /\.(mp3|flac|wav|m4a|aac|ogg|opus|wma|alac|aiff|ape|mka|mid|midi|amr|ac3|dts)$/i.test(p) }
    // the other pages are built in the background once the window has settled (see PageSlot)
    property bool pagesWarm: false
    // then one page per step, built whole: an incubation spread over frames (Loader.asynchronous)
    // was left half-evaluated on a busy machine (Settings' About card lost its version text)
    property int warmStep: -1
    Timer { interval: 1500; running: true; onTriggered: { win.pagesWarm = true; win.warmStep = 0 } }
    Timer { interval: 150; repeat: true; running: win.warmStep >= 0 && win.warmStep < 8; onTriggered: win.warmStep++ }
    function showAbout() {
        win.page = 5   // builds the settings page at once if it was not yet
        if (settingsSlot.item) settingsSlot.item.showAbout()
    }

    function playPaths(paths, index) {
        if (!paths || paths.length === 0) return
        player.openFiles(paths, index || 0)
        if (!isAudioPath(paths[index || 0])) playerOpen = true
    }
    function showMain() {
        if (win.visibility === Window.Hidden) win.show()
        appctl.activateWindow(win)
    }
    // A single media file from Explorer goes to the quick player (when that option is on) — but
    // only while the app itself is not on screen. With the full app open, the file plays there,
    // replacing what was playing, instead of a second player floating over the first.
    function openFromExplorer(paths) {
        if (win.visible) return false
        if (paths.length === 1 && settings.value("quickOpen") && appctl.isMedia(paths[0]) && !appctl.isPlaylist(paths[0])) {
            quick.open(paths[0], quicklook.siblings(paths[0]), "compact")
            return true
        }
        return false
    }
    // nothing visible and not living in the tray: leave
    function quitIfIdle() {
        if (!win.visible && !quick.visible && !tray.background) Qt.quit()
    }
    function handleMenu(key, path) {
        if (key === "play") playPaths([path], 0)
        else if (key === "next") player.playNext(path)
        else if (key === "queue") player.enqueue([path])
        else if (key === "reveal") appctl.reveal(path)
    }
    function handleDrop(urls) {
        if (urls.length === 1 && appctl.isSubtitle(urls[0]) && player.hasMedia) {
            player.addSubtitle(urls[0])
            return "subtitle"
        }
        var paths = appctl.expand(urls)
        if (paths.length) { win.playPaths(paths, 0); return "play" }
        toast.show(qsTr("Nenhuma mídia reconhecida"))
        return "none"
    }
    function restyle() { appctl.styleWindow(win, theme.dark, theme.bg) }

    Component.onCompleted: {
        restyle()
        var pages = { home: 0, videos: 1, music: 2, queue: 3, network: 4, settings: 5 }
        if (startPanel === "queue") pv.queueOpen = true
        else if (startPanel === "about") Qt.callLater(win.showAbout)
        else if (startPanel) pv.panel = startPanel
        if (startPage === "player") playerOpen = true
        else if (startPage in pages) page = pages[startPage]
    }
    Connections { target: theme; function onChanged() { win.restyle() } }
    Connections { target: player; function onMessage(m) { toast.show(m) } }
    // files from the command line / a second launch (Explorer "Abrir com", double-click)
    Connections {
        target: appctl
        function onOpenRequested(paths) {
            if (!paths.length) return
            if (paths.length === 1 && appctl.isSubtitle(paths[0]) && player.hasMedia) player.addSubtitle(paths[0])
            else if (!win.openFromExplorer(paths)) { win.playPaths(paths, 0); win.showMain() }
        }
    }

    // ------------------------------------------------------------ dialogs
    FileDialog {
        id: openDialog
        title: qsTr("Abrir mídia")
        fileMode: FileDialog.OpenFiles
        nameFilters: [qsTr("Mídia") + " (*.mp4 *.mkv *.avi *.mov *.wmv *.webm *.m4v *.mpg *.mpeg *.ts *.m2ts *.flv *.3gp *.ogv *.vob *.mp3 *.flac *.wav *.m4a *.aac *.ogg *.opus *.wma *.m3u *.m3u8 *.pls *.xspf)", qsTr("Todos os arquivos") + " (*)"]
        onAccepted: win.playPaths(appctl.expand(selectedFiles.map(function (u) { return u.toString() })), 0)
    }
    FolderDialog {
        id: playFolderDialog
        title: qsTr("Reproduzir pasta")
        onAccepted: win.playPaths(appctl.expand([selectedFolder.toString()]), 0)
    }
    FolderDialog {
        id: addFolderDialog
        title: qsTr("Adicionar pasta à biblioteca")
        onAccepted: { settings.addFolder(selectedFolder.toString()); toast.show(qsTr("Pasta adicionada à biblioteca")) }
    }
    FileDialog {
        id: subDialog
        title: qsTr("Carregar legenda")
        nameFilters: [qsTr("Legendas") + " (*.srt *.ass *.ssa *.vtt *.sub *.idx *.smi)", qsTr("Todos os arquivos") + " (*)"]
        onAccepted: player.addSubtitle(selectedFile.toString())
    }

    // ------------------------------------------------------------ shortcuts
    // keys come from Ajustes › Atalhos de teclado (refrata/shortcuts.py); an action without a
    // key has an empty sequence, which Qt leaves disabled
    Shortcut { sequence: keys.map.playPause; onActivated: player.togglePlay() }
    Shortcut { sequences: keys.map.fullscreen ? [keys.map.fullscreen, "F11"] : ["F11"]; onActivated: win.toggleFullscreen() }
    Shortcut { sequence: keys.map.back10; onActivated: { player.skip(-10000); pv.poke() } }
    Shortcut { sequence: keys.map.fwd10; onActivated: { player.skip(10000); pv.poke() } }
    Shortcut { sequence: keys.map.back3; onActivated: player.skip(-3000) }
    Shortcut { sequence: keys.map.fwd3; onActivated: player.skip(3000) }
    Shortcut { sequence: keys.map.back60; onActivated: player.skip(-60000) }
    Shortcut { sequence: keys.map.fwd60; onActivated: player.skip(60000) }
    Shortcut { sequence: keys.map.volUp; onActivated: { player.volume = player.volume + 5; toast.show(qsTr("Volume %1%").arg(player.volume)) } }
    Shortcut { sequence: keys.map.volDown; onActivated: { player.volume = player.volume - 5; toast.show(qsTr("Volume %1%").arg(player.volume)) } }
    Shortcut { sequence: keys.map.mute; onActivated: { player.toggleMute(); toast.show(player.muted ? qsTr("Sem som") : qsTr("Som ativado")) } }
    Shortcut { sequence: keys.map.next; onActivated: player.next() }
    Shortcut { sequence: keys.map.prev; onActivated: player.previous() }
    Shortcut { sequence: keys.map.snapshot; onActivated: player.snapshot() }
    Shortcut { sequence: keys.map.loop; onActivated: { player.cycleLoop(); pv.poke() } }
    Shortcut { sequence: keys.map.onTop; onActivated: win.alwaysOnTop = !win.alwaysOnTop }
    Shortcut { sequence: keys.map.frame; onActivated: player.frameStep() }
    Shortcut { sequence: keys.map.info; onActivated: if (win.playerOpen) pv.togglePanel("info") }
    Shortcut { sequence: keys.map.rotate; onActivated: if (win.playerOpen && !player.isAudio) { pv.rotate(90); toast.show(qsTr("Girado %1°").arg(pv.videoRotation)) } }
    Shortcut { sequence: keys.map.nextChapter; onActivated: if (player.chapters.length) { player.nextChapter(); pv.poke() } }
    Shortcut { sequence: keys.map.prevChapter; onActivated: if (player.chapters.length) { player.previousChapter(); pv.poke() } }
    Shortcut { sequence: keys.map.faster; onActivated: { player.rate = player.rate + 0.25; toast.show(qsTr("Velocidade %1×").arg(player.rate)) } }
    Shortcut { sequence: keys.map.slower; onActivated: { player.rate = player.rate - 0.25; toast.show(qsTr("Velocidade %1×").arg(player.rate)) } }
    Shortcut { sequence: keys.map.normalSpeed; onActivated: { player.rate = 1; toast.show(qsTr("Velocidade normal")) } }
    Shortcut { sequence: keys.map.open; onActivated: openDialog.open() }
    Shortcut { sequence: keys.map.stream; onActivated: { win.playerOpen = false; win.page = 4 } }
    Shortcut { sequence: keys.map.settings; onActivated: { win.playerOpen = false; win.page = 5 } }
    Shortcut {
        sequence: "Esc"
        onActivated: {
            if (menu.visible) menu.close()
            else if (win.fullscreen) win.toggleFullscreen()
            else if (pv.panel !== "") pv.panel = ""
            else if (win.playerOpen) win.playerOpen = false
        }
    }

    // ------------------------------------------------------------ library shell
    // 0 = library, 1 = player fully up; follows the player's animated y, so everything tied to
    // it moves in lock-step with the sheet, both ways
    readonly property real sheet: Math.max(0, Math.min(1, 1 - pv.y / Math.max(1, height)))

    Item {
        id: shell
        anchors.fill: parent
        // the library recedes a little under the rising player (depth, not a flat wipe)
        transform: Scale {
            origin.x: shell.width / 2; origin.y: shell.height / 2
            xScale: 1 - 0.035 * win.sheet; yScale: xScale
        }
        visible: win.sheet < 0.999

        Sidebar {
            id: sidebar
            objectName: "sidebar"
            z: 10
            anchors.top: parent.top; anchors.bottom: parent.bottom
            current: win.page
            onNavigate: (i) => win.page = i
            onOpenFiles: openDialog.open()
            onAboutRequested: win.showAbout()
        }

        Item {
            id: pages
            anchors.left: sidebar.right; anchors.right: parent.right
            anchors.top: parent.top; anchors.bottom: parent.bottom

            // A page is built the first time it is shown, or shortly after the window is up, one
            // at a time (win.warmStep): opening the app builds only the page on screen.
            component PageSlot: Item {
                id: slot
                property int index: 0
                property Component page: null      // pages given this way are built on demand
                readonly property Item item: lazy.item
                readonly property bool wanted: win.page === index || (win.pagesWarm && win.warmStep > index)
                property bool everWanted: false
                onWantedChanged: if (wanted) everWanted = true
                Component.onCompleted: if (wanted) everWanted = true
                Loader {
                    id: lazy
                    anchors.fill: parent
                    active: slot.page !== null && slot.everWanted
                    sourceComponent: slot.page
                }
                anchors.fill: parent
                opacity: win.page === index ? 1 : 0
                visible: opacity > 0.01
                // outgoing page vanishes quickly so two pages never visibly overlap
                Behavior on opacity { NumberAnimation { duration: theme.reduceMotion ? 120 : (win.page === index ? 260 : 110); easing.type: Easing.OutCubic } }
                transform: Translate {
                    y: win.page === index || theme.reduceMotion ? 0 : 18
                    Behavior on y { NumberAnimation { duration: 320; easing.type: Easing.OutQuart } }
                }
            }

            PageSlot {
                index: 0
                HomePage {
                    objectName: "homePage"
                    anchors.fill: parent
                    onPlayPaths: (p, i) => win.playPaths(p, i)
                    onOpenFiles: openDialog.open()
                    onOpenFolder: playFolderDialog.open()
                    onMenu: (path, x, y) => menu.openAt(path, x, y)
                    onJump: (i) => win.page = i
                }
            }
            PageSlot {
                index: 1
                page: Component {
                    VideosPage {
                        objectName: "videosPage"
                        onPlayPaths: (p, i) => win.playPaths(p, i)
                        onMenu: (path, x, y) => menu.openAt(path, x, y)
                        onOpenFolder: addFolderDialog.open()
                    }
                }
            }
            PageSlot {
                index: 2
                page: Component {
                    MusicPage {
                        objectName: "musicPage"
                        onPlayPaths: (p, i) => win.playPaths(p, i)
                        onMenu: (path, x, y) => menu.openAt(path, x, y)
                    }
                }
            }
            PageSlot {
                index: 3
                page: Component { QueuePage { objectName: "queuePage"; onOpenPlayer: win.playerOpen = !player.isAudio || win.playerOpen } }
            }
            PageSlot {
                index: 4
                page: Component { NetworkPage { objectName: "networkPage"; onOpenUrl: (u) => { player.openUrl(u); win.playerOpen = true } } }
            }
            PageSlot {
                id: settingsSlot
                index: 5
                page: Component { SettingsPage { objectName: "settingsPage"; onAddFolder: addFolderDialog.open(); onNotify: (text) => toast.show(text) } }
            }

            UpdateBanner {
                anchors.right: parent.right; anchors.rightMargin: 40
                y: 18
                z: 20
            }

            MiniPlayer {
                id: mini
                objectName: "miniPlayer"
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.horizontalCenterOffset: -24
                width: Math.min(parent.width - 96, 980)
                y: player.hasMedia && !win.playerOpen ? parent.height - height - 24 : parent.height + 40
                Behavior on y { NumberAnimation { duration: theme.reduceMotion ? 0 : 380; easing.type: Easing.OutQuart } }
                visible: y < parent.height
                onExpand: win.playerOpen = true
            }
        }
    }

    // ------------------------------------------------------------ player
    // dims the library as the player covers it
    Rectangle {
        anchors.fill: parent
        color: "black"
        opacity: 0.5 * win.sheet
        visible: win.sheet > 0.001 && win.sheet < 0.999
    }
    // soft shadow along the player's top edge while it travels
    Rectangle {
        x: 0; width: parent.width
        height: 72
        y: pv.y - height
        visible: win.sheet > 0.001 && win.sheet < 0.999
        gradient: Gradient {
            GradientStop { position: 0; color: "transparent" }
            GradientStop { position: 1; color: Qt.rgba(0, 0, 0, 0.45) }
        }
    }

    // Pre-warm: render the (off-screen) player once while the app is idle, so the GPU
    // pipelines of its blur/shadow effects exist before the first real open (no hitch then).
    property bool pvWarm: false
    Timer { interval: 1200; running: true; onTriggered: { win.pvWarm = true; warmEnd.start() } }
    Timer { id: warmEnd; interval: 400; onTriggered: win.pvWarm = false }
    // a new queue brings new covers: upload them while the player is still closed, after the
    // mini player has finished sliding in, so the first open has nothing left to load
    Timer { id: warmQueue; interval: 700; onTriggered: if (!win.playerOpen && player.isAudio) { win.pvWarm = true; warmEnd.restart() } }
    Connections { target: player; function onQueueChanged() { if (!win.playerOpen) warmQueue.restart() } }

    PlayerView {
        id: pv
        objectName: "playerView"
        width: parent.width
        height: parent.height
        y: win.playerOpen ? 0 : parent.height
        visible: y < parent.height || win.pvWarm
        warm: win.pvWarm
        Behavior on y { NumberAnimation { duration: theme.reduceMotion ? 0 : (win.playerOpen ? 440 : 300); easing.type: win.playerOpen ? Easing.OutQuart : Easing.InOutCubic } }
        fullscreen: win.fullscreen
        onMinimize: { if (win.fullscreen) win.toggleFullscreen(); win.playerOpen = false }
        onToggleFullscreen: win.toggleFullscreen()
        onPipRequested: win.togglePip()
        onLoadSubtitle: subDialog.open()
        alwaysOnTop: win.alwaysOnTop
        onToggleOnTop: win.alwaysOnTop = !win.alwaysOnTop
    }

    // ------------------------------------------------------------ drop
    DropArea {
        id: drop
        objectName: "dropArea"
        anchors.fill: parent
        keys: ["text/uri-list"]
        onDropped: (d) => win.handleDrop(d.urls.map(function (u) { return u.toString() }))
    }
    Rectangle {
        anchors.fill: parent
        anchors.margins: 16
        radius: 36
        visible: opacity > 0
        opacity: drop.containsDrag ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
        color: Qt.rgba(theme.accent.r, theme.accent.g, theme.accent.b, 0.12)
        border.width: 3
        border.color: theme.accent
        Column {
            anchors.centerIn: parent
            spacing: 12
            Icon { anchors.horizontalCenter: parent.horizontalCenter; name: "add"; size: 48; color: theme.accent }
            Text { anchors.horizontalCenter: parent.horizontalCenter; text: qsTr("Solte para reproduzir"); color: theme.text; font.family: theme.displayFont; font.pixelSize: 40; font.weight: Font.DemiBold }
            Text { anchors.horizontalCenter: parent.horizontalCenter; text: qsTr("Vídeos, músicas, pastas ou legendas"); color: theme.textDim; font.pixelSize: 16 }
        }
    }

    MediaMenu { id: menu; objectName: "mediaMenu"; onTriggered: (k, p) => win.handleMenu(k, p) }

    // closing the window in background mode keeps Refrata in the tray (music keeps playing)
    onClosing: (close) => {
        if (tray.background) {
            close.accepted = false
            if (player.playing && !player.isAudio) player.pause()
            win.hide()
            return
        }
        // closing the main window closes Refrata, quick player included (no window left behind
        // holding the app open)
        if (quick.visible) quick.close()
        if (pip.visible) pip.hide()
        Qt.callLater(Qt.quit)
    }

    // ------------------------------------------------------------ quick player / Space preview
    QuickPlayer {
        id: quick
        onExpandRequested: (files, index, ms) => {
            player.openFilesAt(files, index, ms)
            if (!win.isAudioPath(files[index])) win.playerOpen = true
            win.showMain()
        }
        onClosed: Qt.callLater(win.quitIfIdle)
    }
    Connections {
        target: quicklook
        function onPreviewRequested(path, list) { quick.open(path, list, "preview") }
    }
    // ------------------------------------------------------------ picture-in-picture
    property bool pipOn: false
    property double pipSince: 0
    function enterPip() {
        if (pipOn || !player.hasMedia || player.castingTo !== "") return
        pip.place()
        pip.show()
        pipOn = true
        pipSince = Date.now()
        // leaving full screen finishes asynchronously: minimise once it has, or Windows' late
        // "windowed" notice would read as "the app is back" and close the PiP right away
        if (fullscreen) { toggleFullscreen(); pipMinimize.restart() }
        else win.showMinimized()
    }
    Timer { id: pipMinimize; interval: 350; onTriggered: if (win.pipOn) win.showMinimized() }
    function leavePip(back) {
        if (!pipOn) return
        pipOn = false
        pip.hide()
        if (back) {
            if (player.hasMedia && !player.isAudio) win.playerOpen = true
            if (win.visibility === Window.Minimized || win.visibility === Window.Hidden) win.showNormal()
            appctl.activateWindow(win)
        }
    }
    function togglePip() { if (pipOn) leavePip(true); else enterPip() }
    PipWindow {
        id: pip
        onBackRequested: win.leavePip(true)
        onCloseRequested: { player.pause(); win.leavePip(false) }
    }
    // the app came back (taskbar, Alt+Tab): the small window is not needed any more
    // (ignored while the window is still settling after entering PiP)
    onVisibilityChanged: if (pipOn && Date.now() - pipSince > 1200 && !pipMinimize.running
                             && (win.visibility === Window.Windowed || win.visibility === Window.Maximized)) leavePip(true)
    // nothing left to show (queue cleared, cast started)
    Connections {
        target: player
        function onRenderersChanged() { if (player.castingTo !== "") win.leavePip(true) }
    }
    Shortcut { sequence: keys.map.pip; onActivated: win.togglePip() }
    // back from Windows' Apps padrão: show whether Refrata is now the default player
    Connections {
        target: Qt.application
        function onStateChanged() { if (Qt.application.state === Qt.ApplicationActive) winassoc.refresh() }
    }

    Toast { id: toast; objectName: "toast" }
}
