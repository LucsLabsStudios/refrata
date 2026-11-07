import QtQuick
import QtQuick.Controls
import "Utils.js" as U

GridView {
    id: page
    signal playPaths(var paths, int index)
    signal menu(string path, real x, real y)
    signal openFolder()

    readonly property int cols: Math.max(2, Math.floor((width - 56) / 300))
    cellWidth: Math.floor((width - 56) / cols)
    cellHeight: Math.round((cellWidth - 24) * 9 / 16) + 96
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    ScrollBar.vertical: ScrollBar {}
    model: library.videoModel
    cacheBuffer: 1600
    leftMargin: 8

    header: Column {
        width: page.width - 56
        topPadding: 36
        bottomPadding: 32
        spacing: 28
        PageHeader {
            width: parent.width
            eyebrow: "Biblioteca"
            title: "Vídeos"
            subtitle: library.videoModel.count + " vídeos" + (library.scanning ? " · atualizando…" : "")
            SearchField {
                width: 280
                placeholder: "Filtrar vídeos"
                onTextChanged: library.videoModel.search = text
            }
            PillButton { objectName: "videosPlayAll"; text: "Reproduzir tudo"; icon: "play"; variant: "accent"; h: 44; enabled: library.videoModel.count > 0; onClicked: page.playPaths(library.videoModel.paths(), 0) }
        }
        Row {
            spacing: 12
            Segmented {
                objectName: "videoSort"
                options: [
                    { text: "Recentes", value: "recent" }, { text: "Nome", value: "name" },
                    { text: "Duração", value: "duration" }, { text: "Tamanho", value: "size" },
                ]
                value: library.videoModel.sortMode
                onPicked: (v) => library.videoModel.sortMode = v
            }
            PillButton { objectName: "videosAddFolder"; text: "Adicionar pasta"; icon: "folder"; variant: "outline"; h: 40; onClicked: page.openFolder() }
            IconButton { icon: "refresh"; tip: "Atualizar biblioteca"; size: 40; iconSize: 14; bg: theme.surface2; onClicked: library.rescan() }
        }
    }

    delegate: Item {
        width: page.cellWidth; height: page.cellHeight
        MediaCard {
            objectName: "video_" + index
            width: parent.width - 24
            path: model.path; title: model.title; thumb: model.thumb; duration: model.duration
            subtitle: model.folder + "  ·  " + model.ext + "  ·  " + U.size(model.size)
            onPlay: page.playPaths(library.videoModel.paths(), index)
            onMenu: (x, y) => page.menu(model.path, x, y)
        }
    }

    Column {
        visible: library.videoModel.count === 0 && !library.scanning
        anchors.centerIn: parent
        spacing: 16
        Icon { anchors.horizontalCenter: parent.horizontalCenter; name: "film"; size: 56; color: theme.textFaint }
        Text { anchors.horizontalCenter: parent.horizontalCenter; text: "Nenhum vídeo encontrado"; color: theme.text; font.pixelSize: 22; font.weight: Font.DemiBold }
        Text { anchors.horizontalCenter: parent.horizontalCenter; text: "Adicione uma pasta em Ajustes ou arraste arquivos para cá."; color: theme.textDim; font.pixelSize: 14 }
    }
}
