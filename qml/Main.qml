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
    function playPaths(paths, index) {
        if (!paths || paths.length === 0) return
        player.openFiles(paths, index || 0)
        if (!isAudioPath(paths[index || 0])) playerOpen = true
    }
    function showMain() {
        if (win.visibility === Window.Hidden) win.show()
        appctl.activateWindow(win)
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
        else if (startPanel === "about") Qt.callLater(settingsPage.showAbout)
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
            // a double-click in Explorer means "play it": straight to the full app, never the
            // quick player (that one is only for Space on a selected file)
            else { win.playPaths(paths, 0); win.showMain() }
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
    Shortcut { sequence: "Space"; onActivated: player.togglePlay() }
    Shortcut { sequences: ["F", "F11"]; onActivated: win.toggleFullscreen() }
    Shortcut { sequence: "Left"; onActivated: { player.skip(-10000); pv.poke() } }
    Shortcut { sequence: "Right"; onActivated: { player.skip(10000); pv.poke() } }
    Shortcut { sequence: "Shift+Left"; onActivated: player.skip(-3000) }
    Shortcut { sequence: "Shift+Right"; onActivated: player.skip(3000) }
    Shortcut { sequence: "Ctrl+Left"; onActivated: player.skip(-60000) }
    Shortcut { sequence: "Ctrl+Right"; onActivated: player.skip(60000) }
    Shortcut { sequence: "Up"; onActivated: { player.volume = player.volume + 5; toast.show(qsTr("Volume %1%").arg(player.volume)) } }
    Shortcut { sequence: "Down"; onActivated: { player.volume = player.volume - 5; toast.show(qsTr("Volume %1%").arg(player.volume)) } }
    Shortcut { sequence: "M"; onActivated: { player.toggleMute(); toast.show(player.muted ? "Sem som" : "Som ativado") } }
    Shortcut { sequence: "N"; onActivated: player.next() }
    Shortcut { sequence: "P"; onActivated: player.previous() }
    Shortcut { sequence: "S"; onActivated: player.snapshot() }
    Shortcut { sequence: "L"; onActivated: { player.cycleLoop(); pv.poke() } }
    Shortcut { sequence: "T"; onActivated: win.alwaysOnTop = !win.alwaysOnTop }
    Shortcut { sequence: "E"; onActivated: player.frameStep() }
    Shortcut { sequence: "]"; onActivated: { player.rate = player.rate + 0.25; toast.show(qsTr("Velocidade %1×").arg(player.rate)) } }
    Shortcut { sequence: "["; onActivated: { player.rate = player.rate - 0.25; toast.show(qsTr("Velocidade %1×").arg(player.rate)) } }
    Shortcut { sequence: "="; onActivated: { player.rate = 1; toast.show("Velocidade normal") } }
    Shortcut { sequence: "Ctrl+O"; onActivated: openDialog.open() }
    Shortcut { sequence: "Ctrl+L"; onActivated: { win.playerOpen = false; win.page = 4 } }
    Shortcut { sequence: "Ctrl+,"; onActivated: { win.playerOpen = false; win.page = 5 } }
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
            onAboutRequested: { win.page = 5; settingsPage.showAbout() }
        }

        Item {
            id: pages
            anchors.left: sidebar.right; anchors.right: parent.right
            anchors.top: parent.top; anchors.bottom: parent.bottom

            component PageSlot: Item {
                property int index: 0
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
                VideosPage {
                    objectName: "videosPage"
                    anchors.fill: parent
                    onPlayPaths: (p, i) => win.playPaths(p, i)
                    onMenu: (path, x, y) => menu.openAt(path, x, y)
                    onOpenFolder: addFolderDialog.open()
                }
            }
            PageSlot {
                index: 2
                MusicPage {
                    objectName: "musicPage"
                    anchors.fill: parent
                    onPlayPaths: (p, i) => win.playPaths(p, i)
                    onMenu: (path, x, y) => menu.openAt(path, x, y)
                }
            }
            PageSlot {
                index: 3
                QueuePage { objectName: "queuePage"; anchors.fill: parent; onOpenPlayer: win.playerOpen = !player.isAudio || win.playerOpen }
            }
            PageSlot {
                index: 4
                NetworkPage { objectName: "networkPage"; anchors.fill: parent; onOpenUrl: (u) => { player.openUrl(u); win.playerOpen = true } }
            }
            PageSlot {
                index: 5
                SettingsPage { id: settingsPage; objectName: "settingsPage"; anchors.fill: parent; onAddFolder: addFolderDialog.open() }
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
    Toast { id: toast; objectName: "toast" }
}
