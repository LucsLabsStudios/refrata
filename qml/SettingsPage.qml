import QtQuick
import QtQuick.Controls

Flickable {
    id: page
    signal addFolder()
    contentHeight: col.height + 120
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    ScrollBar.vertical: ScrollBar {}

    component Card: Rectangle {
        default property alias body: inner.data
        property string title: ""
        width: col.width
        height: inner.height + 80
        radius: 32
        color: theme.surface
        border.width: 1; border.color: theme.stroke
        Text { x: 32; y: 28; text: parent.title; color: theme.text; font.family: theme.displayFont; font.pixelSize: 24; font.weight: Font.DemiBold }
        Column { id: inner; x: 32; y: 76; width: parent.width - 64; spacing: 4 }
    }
    component SettingRow: Item {
        property string title: ""
        property string desc: ""
        default property alias control: ctl.data
        width: parent ? parent.width : 0
        height: 68
        Column {
            anchors.verticalCenter: parent.verticalCenter
            width: parent.width - ctl.width - 24
            spacing: 3
            Text { text: title; color: theme.text; font.pixelSize: 15; font.weight: Font.DemiBold }
            Text { text: desc; visible: desc !== ""; color: theme.textDim; font.pixelSize: 13; width: parent.width; wrapMode: Text.WordWrap }
        }
        Item { id: ctl; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; width: childrenRect.width; height: childrenRect.height }
    }

    Column {
        id: col
        x: 8
        width: Math.min(page.width - 56, 980)
        topPadding: 36
        spacing: 24

        PageHeader { width: parent.width; eyebrow: "Preferências"; title: "Ajustes"; subtitle: "Deixe o player do seu jeito." }

        Card {
            title: "Aparência"
            SettingRow {
                title: "Tema"; desc: "Escuro é ideal para filmes; claro tem tons quentes e suaves."
                Segmented {
                    objectName: "themeSeg"
                    options: [{ text: "Escuro", value: "dark" }, { text: "Claro", value: "light" }]
                    value: settings.theme
                    onPicked: (v) => settings.theme = v
                }
            }
            SettingRow {
                title: "Cor de destaque"; desc: "Usada em botões, progresso e seleção."
                Row {
                    spacing: 10
                    Repeater {
                        model: theme.accents
                        Item {
                            objectName: "accent_" + index
                            width: 34; height: 34
                            Rectangle {
                                anchors.fill: parent; radius: 17
                                color: "transparent"
                                border.width: 2
                                border.color: settings.accent.toLowerCase() === modelData.color.toLowerCase() ? modelData.color : "transparent"
                            }
                            Rectangle { anchors.centerIn: parent; width: 24; height: 24; radius: 12; color: modelData.color }
                            MouseArea { id: sw; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: settings.accent = modelData.color }
                            Tip { text: modelData.name; shown: sw.containsMouse }
                        }
                    }
                }
            }
        }

        Card {
            title: "Reprodução"
            SettingRow {
                title: "Continuar de onde parou"; desc: "Lembra a posição de cada arquivo."
                Toggle { objectName: "toggleResume"; checked: settings.resume; onToggled: (on) => settings.resume = on }
            }
            SettingRow {
                title: "Reproduzir o próximo automaticamente"; desc: "Avança na fila ao terminar um item."
                Toggle { objectName: "toggleAutoplay"; checked: settings.autoplay; onToggled: (on) => settings.autoplay = on }
            }
            SettingRow {
                title: "Decodificação por hardware"; desc: "Usa a GPU para decodificar vídeo. Vale ao reiniciar o app."
                Toggle { objectName: "toggleHwdec"; checked: settings.hwdec; onToggled: (on) => settings.hwdec = on }
            }
        }

        Card {
            title: "Pastas da biblioteca"
            Repeater {
                model: settings.folders
                SettingRow {
                    title: modelData.split(/[\\/]/).pop()
                    desc: modelData
                    IconButton { icon: "delete"; size: 38; iconSize: 14; tip: "Remover"; onClicked: settings.removeFolder(modelData) }
                }
            }
            Item { width: 1; height: 10 }
            Row {
                spacing: 10
                PillButton { objectName: "settingsAddFolder"; text: "Adicionar pasta"; icon: "add"; variant: "filled"; onClicked: page.addFolder() }
                PillButton { objectName: "rescanBtn"; text: library.scanning ? "Atualizando…" : "Atualizar agora"; icon: "refresh"; variant: "outline"; onClicked: library.rescan() }
            }
        }

        Card {
            title: "Atualizações"
            SettingRow {
                title: "Procurar atualizações automaticamente"
                desc: updater.kind === "store" ? "Esta cópia veio da Microsoft Store, que cuida das atualizações."
                    : "Ao abrir o app, no máximo duas vezes por dia. Nada é instalado sem você pedir."
                Toggle { objectName: "toggleAutoUpdate"; enabled: updater.kind !== "store"; checked: updater.auto; onToggled: (on) => updater.auto = on }
            }
            SettingRow {
                objectName: "updateStatusRow"
                title: "Versão " + updater.current
                desc: {
                    switch (updater.state) {
                    case "checking": return "Procurando…"
                    case "uptodate": return "Você está com a versão mais recente."
                    case "available": return "A versão " + updater.latest + " está disponível."
                    case "downloading": return "Baixando a versão " + updater.latest + "… " + Math.round(updater.progress * 100) + "%"
                    case "ready": return "A versão " + updater.latest + " está pronta para instalar."
                    case "installing": return "Instalando…"
                    case "error": return updater.error
                    default: return updater.lastCheck > 0 ? "Última verificação: " + new Date(updater.lastCheck * 1000).toLocaleString(Qt.locale("pt_BR"), "dd/MM HH:mm") : "Ainda não verificado."
                    }
                }
                Row {
                    spacing: 10
                    PillButton {
                        objectName: "updateCheck"
                        visible: updater.state !== "available" && updater.state !== "ready"
                        enabled: updater.kind !== "store" && updater.state !== "checking" && updater.state !== "downloading" && updater.state !== "installing"
                        text: updater.state === "checking" ? "Procurando…" : "Procurar agora"; icon: "sync"; variant: "outline"
                        onClicked: updater.checkNow()
                    }
                    PillButton {
                        objectName: "updateNow"
                        visible: updater.state === "available" || updater.state === "ready"
                        text: updater.state === "ready" ? "Reiniciar e atualizar" : (updater.canSelfUpdate ? "Atualizar" : "Baixar")
                        icon: "download"; variant: "accent"
                        onClicked: updater.update()
                    }
                }
            }
        }

        Card {
            title: "Atalhos de teclado"
            Grid {
                columns: 2
                columnSpacing: 40
                rowSpacing: 12
                Repeater {
                    model: [
                        ["Espaço", "Reproduzir / pausar"], ["F  ·  duplo clique", "Tela cheia"],
                        ["← / →", "Voltar / avançar 10 s"], ["↑ / ↓", "Volume"],
                        ["M", "Silenciar"], ["N / P", "Próximo / anterior"],
                        ["[  /  ]", "Velocidade − / +"], ["S", "Capturar quadro"],
                        ["E", "Avançar um quadro"], ["Ctrl + O", "Abrir arquivos"],
                        ["Ctrl + L", "Abrir stream de rede"], ["Esc", "Sair da tela cheia / fechar"],
                    ]
                    Row {
                        spacing: 14
                        Rectangle {
                            width: 150; height: 32; radius: 10; color: theme.surface2
                            Text { anchors.centerIn: parent; text: modelData[0]; color: theme.text; font.pixelSize: 13; font.weight: Font.DemiBold }
                        }
                        Text { anchors.verticalCenter: parent.verticalCenter; text: modelData[1]; color: theme.textDim; font.pixelSize: 14; width: 220 }
                    }
                }
            }
        }

        // ------------------------------------------------ about (brand card)
        Rectangle {
            id: aboutCard
            objectName: "aboutCard"
            width: col.width
            height: 470
            radius: 32
            color: "#0B0A09"
            clip: true

            RoundedImage {
                anchors.fill: parent
                radius: 32
                source: assetsUrl + "brand/about.jpg"
                sourceWidth: 1536
            }
            // keep the left side dark for the type
            Rectangle {
                anchors.fill: parent; radius: 32
                gradient: Gradient {
                    orientation: Gradient.Horizontal
                    GradientStop { position: 0.0; color: Qt.rgba(0.04, 0.035, 0.03, 0.92) }
                    GradientStop { position: 0.45; color: Qt.rgba(0.04, 0.035, 0.03, 0.55) }
                    GradientStop { position: 0.8; color: "transparent" }
                }
            }
            Column {
                x: 44; y: 44
                width: Math.min(parent.width * 0.6, 560)
                spacing: 14
                Row {
                    spacing: 12
                    Image {
                        source: assetsUrl + "brand/icon-96.png"
                        width: 44; height: 44; sourceSize: Qt.size(88, 88); mipmap: true
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    Column {
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 2
                        Text { text: "LUCSLABS STUDIOS"; color: "#FFB36B"; font.pixelSize: 12; font.weight: Font.Bold; font.letterSpacing: 2.4 }
                        Text { text: "apresenta"; color: Qt.rgba(1, 1, 1, 0.55); font.pixelSize: 12 }
                    }
                }
                Text {
                    text: "Refrata"
                    color: "white"
                    font.family: theme.displayFont; font.pixelSize: 64; font.weight: Font.Light
                    font.letterSpacing: -1.5
                }
                Text {
                    width: parent.width
                    textFormat: Text.StyledText
                    text: "Todo o motor do VLC, <font color='#FF9A5C'>refratado</font> numa experiência nova."
                    color: Qt.rgba(1, 1, 1, 0.86)
                    font.family: theme.displayFont; font.pixelSize: 22; font.weight: Font.Normal
                    wrapMode: Text.WordWrap
                    lineHeight: 1.15
                }
                Flow {
                    width: parent.width
                    spacing: 8
                    topPadding: 6
                    Repeater {
                        model: [
                            "Versão " + (appctl.about.version || "?"),
                            "libVLC " + (appctl.about.vlcVersion || "?") + (appctl.about.vlcBundled ? " · embutido" : " · do sistema"),
                            "Qt 6 · QML"
                        ]
                        Rectangle {
                            objectName: "aboutChip_" + index
                            height: 30; radius: 15
                            width: chipText.implicitWidth + 26
                            color: Qt.rgba(1, 1, 1, 0.10)
                            border.width: 1; border.color: Qt.rgba(1, 1, 1, 0.14)
                            Text { id: chipText; anchors.centerIn: parent; text: modelData; color: "white"; font.pixelSize: 12; font.weight: Font.DemiBold }
                        }
                    }
                }
                Row {
                    spacing: 10
                    topPadding: 10
                    PillButton {
                        objectName: "aboutContact"
                        text: "Falar com a LucsLabs"; icon: "mail"; variant: "glass"
                        onClicked: appctl.openExternal("mailto:" + (appctl.about.contact || ""))
                    }
                    PillButton {
                        objectName: "aboutSource"
                        text: "Código-fonte"; icon: "link"; variant: "glass"
                        onClicked: appctl.openExternal("https://github.com/LucsLabsStudios/refrata")
                    }
                    PillButton {
                        objectName: "aboutDefaults"
                        visible: appctl.about.installed === true
                        text: "Definir como player padrão"; icon: "apps"; variant: "glass"
                        onClicked: appctl.openExternal("ms-settings:defaultapps")
                    }
                }
            }
            Text {
                anchors.left: parent.left; anchors.leftMargin: 44
                anchors.right: parent.right; anchors.rightMargin: 44
                anchors.bottom: parent.bottom; anchors.bottomMargin: 26
                wrapMode: Text.WordWrap
                color: Qt.rgba(1, 1, 1, 0.5); font.pixelSize: 12
                text: "Software livre (GPL 3.0) construído sobre o libVLC da VideoLAN (LGPL 2.1+). VLC é marca da VideoLAN; este é um projeto independente."
            }
        }
    }

    function showAbout() {
        // scroll so the brand card is fully in view
        contentY = Math.max(0, Math.min(contentHeight - height, aboutCard.y + col.y - 24))
    }
}
