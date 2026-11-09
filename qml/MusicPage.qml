import QtQuick
import QtQuick.Controls

ListView {
    id: page
    signal playPaths(var paths, int index)
    signal menu(string path, real x, real y)
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    ScrollBar.vertical: ScrollBar {}
    model: library.musicModel
    cacheBuffer: 2000
    spacing: 2

    header: Column {
        width: page.width - 56
        x: 8
        topPadding: 36
        bottomPadding: 28
        spacing: 28
        PageHeader {
            width: parent.width
            eyebrow: qsTr("Biblioteca")
            title: qsTr("Músicas")
            subtitle: library.musicModel.count ? qsTr("%n faixa(s)", "", library.musicModel.count) : qsTr("Nenhuma faixa")
            SearchField { width: 260; placeholder: qsTr("Filtrar músicas"); onTextChanged: library.musicModel.search = text }
            PillButton { objectName: "musicShuffle"; text: qsTr("Aleatório"); icon: "shuffle"; variant: "outline"; h: 44; enabled: library.musicModel.count > 0
                onClicked: { if (!player.shuffle) player.toggleShuffle(); var p = library.musicModel.paths(); page.playPaths(p, Math.floor(Math.random() * p.length)) } }
            PillButton { objectName: "musicPlayAll"; text: qsTr("Tocar tudo"); icon: "play"; variant: "accent"; h: 44; enabled: library.musicModel.count > 0; onClicked: page.playPaths(library.musicModel.paths(), 0) }
        }
        Segmented {
            options: [{ text: qsTr("Recentes"), value: "recent" }, { text: qsTr("Título"), value: "name" }, { text: qsTr("Duração"), value: "duration" }]
            value: library.musicModel.sortMode
            onPicked: (v) => library.musicModel.sortMode = v
        }
        Item {
            width: parent.width; height: 28
            Text { x: 32; text: "#"; color: theme.textFaint; font.pixelSize: 12 }
            Text { x: 58; text: qsTr("TÍTULO"); color: theme.textFaint; font.pixelSize: 11; font.weight: Font.DemiBold; font.letterSpacing: 1.4 }
            Text { anchors.right: parent.right; anchors.rightMargin: 20; text: qsTr("DURAÇÃO"); color: theme.textFaint; font.pixelSize: 11; font.weight: Font.DemiBold; font.letterSpacing: 1.4 }
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.stroke }
        }
    }

    delegate: MusicRow {
        objectName: "music_" + index
        x: 8
        width: page.width - 56
        number: index + 1
        title: model.title; artist: model.artist; album: model.album; thumb: model.thumb; duration: model.duration
        current: player.currentPath === model.path
        playing: player.playing
        onPlay: page.playPaths(library.musicModel.paths(), index)
        onToggle: player.togglePlay()
        onMenu: (x, y) => page.menu(model.path, x, y)
    }

    Column {
        visible: library.musicModel.count === 0 && !library.scanning
        anchors.centerIn: parent
        spacing: 16
        Icon { anchors.horizontalCenter: parent.horizontalCenter; name: "music"; size: 56; color: theme.textFaint }
        Text { anchors.horizontalCenter: parent.horizontalCenter; text: qsTr("Nenhuma música por aqui"); color: theme.text; font.pixelSize: 22; font.weight: Font.DemiBold }
        Text { anchors.horizontalCenter: parent.horizontalCenter; text: qsTr("Adicione sua pasta de músicas em Ajustes."); color: theme.textDim; font.pixelSize: 14 }
    }
}
