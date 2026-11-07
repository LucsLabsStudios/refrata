import QtQuick
import QtQuick.Controls
import "Utils.js" as U

Flickable {
    id: page
    signal playPaths(var paths, int index)
    signal openFiles()
    signal openFolder()
    signal menu(string path, real x, real y)
    signal jump(int index)

    contentHeight: content.height + 140
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

    readonly property var history: settings.history
    readonly property bool searching: search.text.trim() !== ""
    readonly property var hero: {
        // most recent video with saved progress, otherwise latest played video, otherwise newest video in library
        var h = history
        for (var i = 0; i < h.length; i++)
            if (h[i].pos > 0 && h[i].path.indexOf("://") < 0 && !U.baseName(h[i].path).match(/\.(mp3|flac|wav|m4a|aac|ogg|opus|wma)$/i))
                return { path: h[i].path, title: h[i].title, pos: h[i].pos, len: h[i].len, label: "Continue assistindo" }
        for (var j = 0; j < h.length; j++)
            if (h[j].path.indexOf("://") < 0 && !U.baseName(h[j].path).match(/\.(mp3|flac|wav|m4a|aac|ogg|opus|wma)$/i))
                return { path: h[j].path, title: h[j].title, pos: 0, len: h[j].len, label: "Assistido recentemente" }
        if (library.videoModel.count > 0) {
            var v = library.videoModel.get(0)
            return { path: v.path, title: v.title, pos: 0, len: v.duration, label: "Novo na sua biblioteca" }
        }
        return null
    }
    readonly property var heroInfo: { library.revision; return hero ? library.info(hero.path) : ({}) }

    Column {
        id: content
        x: 8
        width: page.width - 56
        spacing: 40
        topPadding: 36

        // ---------- header ----------
        Item {
            width: parent.width
            height: Math.max(headCol.height, 44)
            Column {
                id: headCol
                spacing: 6
                width: parent.width - actions.width - 24
                Text {
                    text: U.today().toUpperCase()
                    color: theme.textDim
                    font.pixelSize: 12; font.weight: Font.DemiBold; font.letterSpacing: 2.2
                }
                Text {
                    width: parent.width
                    textFormat: Text.StyledText
                    text: U.greeting() + ".<br><font color='" + theme.textFaint + "'>O que vamos </font><font color='" + theme.accent + "'>assistir</font><font color='" + theme.textFaint + "'>?</font>"
                    color: theme.text
                    font.family: theme.displayFont
                    font.pixelSize: Math.min(76, Math.max(48, page.width / 16))
                    font.weight: Font.Light
                    lineHeight: 0.98
                    font.letterSpacing: -1.6
                    wrapMode: Text.WordWrap
                }
            }
            Row {
                id: actions
                anchors.right: parent.right
                spacing: 10
                SearchField { id: search; objectName: "homeSearch"; width: Math.min(320, page.width * 0.26); placeholder: "Buscar na biblioteca" }
                PillButton { objectName: "homeOpenFolder"; text: "Abrir pasta"; icon: "folder"; variant: "outline"; h: 44; onClicked: page.openFolder() }
            }
            Binding { target: library.searchModel; property: "search"; value: search.text.trim() }
        }

        // ---------- search results ----------
        Column {
            visible: page.searching
            width: parent.width
            spacing: 20
            Text {
                text: library.searchModel.count + " resultado" + (library.searchModel.count === 1 ? "" : "s") + " para “" + search.text.trim() + "”"
                color: theme.textDim; font.pixelSize: 16
            }
            Flow {
                width: parent.width
                spacing: 24
                Repeater {
                    model: page.searching ? library.searchModel : null
                    MediaCard {
                        width: Math.floor((content.width - 24 * 3) / 4)
                        path: model.path; title: model.title; thumb: model.thumb; duration: model.duration
                        audio: model.kind === "audio"
                        subtitle: model.kind === "audio" ? (model.artist || "Música") : model.folder
                        onPlay: page.playPaths([model.path], 0)
                        onMenu: (x, y) => page.menu(model.path, x, y)
                    }
                }
            }
        }

        // ---------- hero ----------
        Item {
            id: heroBox
            objectName: "hero"
            visible: !page.searching
            width: parent.width
            height: Math.round(Math.min(520, Math.max(340, width * 0.40)))

            Rectangle {
                anchors.fill: parent
                radius: 36
                color: theme.surface
                border.width: 1; border.color: theme.stroke
            }
            RoundedImage {
                anchors.fill: parent
                radius: 36
                source: page.heroInfo && page.heroInfo.thumb ? page.heroInfo.thumb : ""
                sourceWidth: 1600
            }
            Rectangle {
                anchors.fill: parent
                radius: 36
                visible: !!(page.heroInfo && page.heroInfo.thumb)
                gradient: Gradient {
                    orientation: Gradient.Horizontal
                    GradientStop { position: 0.0; color: Qt.rgba(0, 0, 0, 0.88) }
                    GradientStop { position: 0.5; color: Qt.rgba(0, 0, 0, 0.45) }
                    GradientStop { position: 1.0; color: Qt.rgba(0, 0, 0, 0.05) }
                }
            }
            Rectangle {
                anchors.fill: parent
                radius: 36
                visible: heroBox.overImage
                gradient: Gradient {
                    GradientStop { position: 0.4; color: "transparent" }
                    GradientStop { position: 1.0; color: Qt.rgba(0, 0, 0, 0.6) }
                }
            }
            readonly property bool overImage: !!(page.heroInfo && page.heroInfo.thumb)
            readonly property color fg: overImage ? "white" : theme.text
            readonly property color fgDim: overImage ? Qt.rgba(1, 1, 1, 0.72) : theme.textDim

            // empty state
            Column {
                visible: !page.hero
                anchors.left: parent.left; anchors.leftMargin: 56
                anchors.verticalCenter: parent.verticalCenter
                width: parent.width * 0.6
                spacing: 18
                Text { text: "COMECE AQUI"; color: theme.accent; font.pixelSize: 12; font.weight: Font.Bold; font.letterSpacing: 2.4 }
                Text {
                    width: parent.width; wrapMode: Text.WordWrap
                    text: "Solte qualquer vídeo, música ou pasta nesta janela."
                    color: theme.text; font.family: theme.displayFont; font.pixelSize: 46; font.weight: Font.Light; font.letterSpacing: -1
                }
                Text {
                    width: parent.width; wrapMode: Text.WordWrap
                    text: "O motor do VLC toca praticamente tudo: MKV, MP4, AVI, FLAC, streams de rede e muito mais."
                    color: theme.textDim; font.pixelSize: 16
                }
                Row {
                    spacing: 12
                    PillButton { objectName: "emptyOpenFiles"; text: "Abrir arquivos"; icon: "file"; variant: "filled"; h: 48; fontSize: 15; onClicked: page.openFiles() }
                    PillButton { objectName: "emptyAddFolder"; text: "Adicionar pasta"; icon: "folder"; variant: "outline"; h: 48; fontSize: 15; onClicked: page.openFolder() }
                }
            }

            Column {
                visible: !!page.hero
                anchors.left: parent.left; anchors.leftMargin: 52
                anchors.bottom: parent.bottom; anchors.bottomMargin: 48
                width: parent.width * 0.55
                spacing: 14
                Row {
                    spacing: 10
                    Rectangle {
                        width: lab.implicitWidth + 24; height: 30; radius: 15
                        color: heroBox.overImage ? Qt.rgba(1, 1, 1, 0.16) : theme.accentSoft
                        border.width: 1; border.color: heroBox.overImage ? Qt.rgba(1, 1, 1, 0.2) : "transparent"
                        Text {
                            id: lab; anchors.centerIn: parent
                            text: page.hero ? page.hero.label.toUpperCase() : ""
                            color: heroBox.overImage ? "white" : theme.accent
                            font.pixelSize: 11; font.weight: Font.Bold; font.letterSpacing: 1.6
                        }
                    }
                }
                Text {
                    width: parent.width
                    text: page.hero ? page.hero.title : ""
                    color: heroBox.fg
                    font.family: theme.displayFont
                    font.pixelSize: Math.min(64, Math.max(40, heroBox.width / 18))
                    font.weight: Font.Bold
                    font.letterSpacing: -1.2
                    wrapMode: Text.WordWrap
                    maximumLineCount: 2
                    elide: Text.ElideRight
                    lineHeight: 0.95
                }
                Text {
                    text: {
                        if (!page.hero) return ""
                        var parts = []
                        var len = page.hero.len || (page.heroInfo ? page.heroInfo.duration : 0)
                        if (page.hero.pos > 0 && len > 0) parts.push("Faltam " + U.duration(len - page.hero.pos))
                        else if (len > 0) parts.push(U.duration(len))
                        if (page.heroInfo && page.heroInfo.ext) parts.push(page.heroInfo.ext)
                        if (page.heroInfo && page.heroInfo.folder) parts.push(page.heroInfo.folder)
                        return parts.join("   ·   ")
                    }
                    color: heroBox.fgDim; font.pixelSize: 15
                }
                Item {
                    visible: page.hero && page.hero.pos > 0 && page.hero.len > 0
                    width: 320; height: 4
                    Rectangle { anchors.fill: parent; radius: 2; color: heroBox.overImage ? Qt.rgba(1, 1, 1, 0.25) : theme.surface3 }
                    Rectangle { height: 4; radius: 2; color: theme.accent; width: page.hero && page.hero.len ? parent.width * page.hero.pos / page.hero.len : 0 }
                }
                Item { width: 1; height: 6 }
                Row {
                    spacing: 12
                    PillButton {
                        objectName: "heroPlay"
                        text: page.hero && page.hero.pos > 0 ? "Continuar" : "Assistir"
                        icon: "play"; variant: "accent"; h: 52; fontSize: 15
                        onClicked: page.playPaths([page.hero.path], 0)
                    }
                    PillButton {
                        visible: heroBox.overImage
                        text: "Mostrar no Explorer"; icon: "folder"; variant: "glass"; h: 52; fontSize: 15
                        onClicked: appctl.reveal(page.hero.path)
                    }
                }
            }
        }

        // ---------- stats ----------
        Row {
            visible: !page.searching
            width: parent.width
            spacing: 20
            Repeater {
                model: [
                    { icon: "video", value: library.videoCount, label: "Vídeos na biblioteca", page: 1 },
                    { icon: "music", value: library.audioCount, label: "Músicas e áudios", page: 2 },
                    { icon: "clock", value: Math.round(library.totalHours), label: "Horas de conteúdo", page: 1 },
                    { icon: "history", value: page.history.length, label: "Reproduzidos recentemente", page: 3 },
                ]
                Rectangle {
                    objectName: "stat_" + index
                    width: (content.width - 60) / 4
                    height: 128
                    radius: 28
                    color: sma.containsMouse ? theme.surface2 : theme.surface
                    border.width: 1; border.color: theme.stroke
                    Behavior on color { ColorAnimation { duration: 140 } }
                    Rectangle {
                        x: 22; y: 22; width: 40; height: 40; radius: 20
                        color: theme.accentSoft
                        Icon { anchors.centerIn: parent; name: modelData.icon; size: 16; color: theme.accent }
                    }
                    Icon { anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 24; name: "right"; size: 12; color: theme.textFaint }
                    Column {
                        x: 22; anchors.bottom: parent.bottom; anchors.bottomMargin: 18
                        Text {
                            text: modelData.value + (library.scanning && modelData.page !== 3 ? "…" : "")
                            color: theme.text; font.family: theme.displayFont
                            font.pixelSize: 30; font.weight: Font.DemiBold
                            font.features: { "tnum": 1 }
                        }
                        Text { text: modelData.label; color: theme.textDim; font.pixelSize: 13 }
                    }
                    MouseArea { id: sma; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: page.jump(modelData.page) }
                }
            }
        }

        // ---------- recent ----------
        Shelf {
            visible: !page.searching && page.history.length > 0
            width: parent.width
            title: "Reproduzidos recentemente"
            actionText: "Limpar"
            onAction: settings.clearHistory()
            model: page.history
            delegate: MediaCard {
                width: 300
                readonly property var info: { library.revision; return library.info(modelData.path) }
                path: modelData.path
                title: modelData.title
                thumb: info.thumb || ""
                audio: info.kind === "audio"
                duration: modelData.len || info.duration || 0
                progress: modelData.len > 0 ? modelData.pos / modelData.len : 0
                subtitle: U.ago(modelData.time) + (info.folder ? "  ·  " + info.folder : "")
                onPlay: page.playPaths([modelData.path], 0)
                onMenu: (x, y) => page.menu(modelData.path, x, y)
            }
        }

        // ---------- newest videos ----------
        Shelf {
            visible: !page.searching && library.videoModel.count > 0
            width: parent.width
            title: "Adicionados recentemente"
            actionText: "Ver todos"
            onAction: page.jump(1)
            model: library.videoModel
            delegate: MediaCard {
                width: 300
                path: model.path; title: model.title; thumb: model.thumb; duration: model.duration
                subtitle: model.folder + "  ·  " + model.ext
                onPlay: page.playPaths([model.path], 0)
                onMenu: (x, y) => page.menu(model.path, x, y)
            }
        }

        // ---------- music ----------
        Shelf {
            visible: !page.searching && library.musicModel.count > 0
            width: parent.width
            title: "Para ouvir"
            actionText: "Ver todas"
            onAction: page.jump(2)
            model: library.musicModel
            itemHeight: 250
            delegate: Item {
                width: 190; height: 250
                Rectangle {
                    id: sq
                    width: 190; height: 190; radius: 24
                    color: theme.surface2
                    border.width: 1; border.color: theme.stroke
                    scale: am.containsMouse ? 1.03 : 1
                    Behavior on scale { NumberAnimation { duration: 200 } }
                    Icon { anchors.centerIn: parent; name: "music"; size: 40; color: theme.textFaint; visible: ai.status !== Image.Ready }
                    RoundedImage { id: ai; anchors.fill: parent; radius: 24; source: model.thumb; sourceWidth: 400 }
                    Rectangle {
                        anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: 12
                        width: 44; height: 44; radius: 22; color: theme.accent
                        opacity: am.containsMouse ? 1 : 0
                        Behavior on opacity { NumberAnimation { duration: 140 } }
                        PlayGlyph { anchors.centerIn: parent; anchors.horizontalCenterOffset: 1; size: 16; color: theme.onAccent }
                    }
                }
                Text { anchors.top: sq.bottom; anchors.topMargin: 10; width: parent.width; elide: Text.ElideRight; text: model.title; color: theme.text; font.pixelSize: 14; font.weight: Font.DemiBold }
                Text { anchors.top: sq.bottom; anchors.topMargin: 30; width: parent.width; elide: Text.ElideRight; text: model.artist || model.folder; color: theme.textDim; font.pixelSize: 12 }
                MouseArea {
                    id: am; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                    acceptedButtons: Qt.LeftButton | Qt.RightButton
                    onClicked: (m) => {
                        if (m.button === Qt.RightButton) { var p = mapToItem(null, m.x, m.y); page.menu(model.path, p.x, p.y) }
                        else page.playPaths(library.musicModel.paths(), index)
                    }
                }
            }
        }
    }
}
