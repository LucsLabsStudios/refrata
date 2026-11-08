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
        // grows with the text: some languages need two or three lines where Portuguese needs one
        height: Math.max(68, rowText.implicitHeight + 24, ctl.height + 16)
        Column {
            id: rowText
            anchors.verticalCenter: parent.verticalCenter
            width: parent.width - ctl.width - 24
            spacing: 3
            Text { text: title; color: theme.text; font.pixelSize: 15; font.weight: Font.DemiBold; width: parent.width; wrapMode: Text.WordWrap }
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

        PageHeader { width: parent.width; eyebrow: qsTr("Preferências"); title: qsTr("Ajustes"); subtitle: qsTr("Deixe o player do seu jeito.") }

        Card {
            title: qsTr("Aparência")
            SettingRow {
                title: qsTr("Tema"); desc: qsTr("Escuro é ideal para filmes; claro tem tons quentes e suaves.")
                Segmented {
                    objectName: "themeSeg"
                    options: [{ text: qsTr("Escuro"), value: "dark" }, { text: qsTr("Claro"), value: "light" }]
                    value: settings.theme
                    onPicked: (v) => settings.theme = v
                }
            }
            SettingRow {
                title: qsTr("Cor de destaque"); desc: qsTr("Usada em botões, progresso e seleção.")
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
                            Tip { text: qsTr(modelData.name); shown: sw.containsMouse }
                        }
                    }
                }
            }
        }

        Card {
            objectName: "languageCard"
            title: qsTr("Idioma")
            SettingRow {
                title: qsTr("Idioma do app")
                desc: qsTr("“Automático” segue o idioma do Windows. A troca vale na hora.")
            }
            Flow {
                width: parent.width
                spacing: 8
                bottomPadding: 6
                Repeater {
                    model: [{ code: "auto", name: qsTr("Automático (%1)").arg(i18n.nameOf(i18n.system)) }].concat(i18n.languages)
                    PillButton {
                        objectName: "lang_" + modelData.code
                        text: modelData.name
                        variant: "outline"; h: 36; fontSize: 13
                        checked: i18n.language === modelData.code
                        onClicked: i18n.language = modelData.code
                    }
                }
            }
        }

        Card {
            title: qsTr("Reprodução")
            SettingRow {
                title: qsTr("Continuar de onde parou"); desc: qsTr("Lembra a posição de cada arquivo.")
                Toggle { objectName: "toggleResume"; checked: settings.resume; onToggled: (on) => settings.resume = on }
            }
            SettingRow {
                title: qsTr("Reproduzir o próximo automaticamente"); desc: qsTr("Avança na fila ao terminar um item.")
                Toggle { objectName: "toggleAutoplay"; checked: settings.autoplay; onToggled: (on) => settings.autoplay = on }
            }
            SettingRow {
                title: qsTr("Decodificação por hardware"); desc: qsTr("Usa a GPU para decodificar vídeo. Vale ao reiniciar o app.")
                Toggle { objectName: "toggleHwdec"; checked: settings.hwdec; onToggled: (on) => settings.hwdec = on }
            }
        }

        Card {
            title: qsTr("Pastas da biblioteca")
            Repeater {
                model: settings.folders
                SettingRow {
                    title: modelData.split(/[\\/]/).pop()
                    desc: modelData
                    IconButton { icon: "delete"; size: 38; iconSize: 14; tip: qsTr("Remover"); onClicked: settings.removeFolder(modelData) }
                }
            }
            Item { width: 1; height: 10 }
            Row {
                spacing: 10
                PillButton { objectName: "settingsAddFolder"; text: qsTr("Adicionar pasta"); icon: "add"; variant: "filled"; onClicked: page.addFolder() }
                PillButton { objectName: "rescanBtn"; text: library.scanning ? qsTr("Atualizando…") : qsTr("Atualizar agora"); icon: "refresh"; variant: "outline"; onClicked: library.rescan() }
            }
        }

        Card {
            title: qsTr("Abertura rápida")
            SettingRow {
                title: qsTr("Prévia com Espaço no Explorer")
                desc: qsTr("Selecione um vídeo ou música no Explorer ou na área de trabalho e aperte Espaço. Espaço ou Esc fecha; ← → passa pelos arquivos da pasta.")
                Toggle { objectName: "toggleQuickLook"; checked: quicklook.enabled; onToggled: (on) => quicklook.enabled = on }
            }
            SettingRow {
                title: qsTr("Manter em segundo plano")
                desc: qsTr("Ao fechar a janela, o Refrata fica na bandeja do Windows, e a prévia e os arquivos abrem na hora.")
                Toggle { objectName: "toggleBackground"; checked: tray.background; onToggled: (on) => tray.background = on }
            }
            SettingRow {
                visible: tray.canAutostart
                title: qsTr("Iniciar com o Windows")
                desc: qsTr("Começa discretamente na bandeja ao ligar o computador.")
                Toggle { objectName: "toggleAutostart"; checked: tray.autostart; onToggled: (on) => tray.autostart = on }
            }
        }

        Card {
            title: qsTr("Atualizações")
            SettingRow {
                title: qsTr("Procurar atualizações automaticamente")
                desc: updater.kind === "store" ? qsTr("Esta cópia veio da Microsoft Store, que cuida das atualizações.")
                    : qsTr("Ao abrir o app, no máximo duas vezes por dia. Nada é instalado sem você pedir.")
                Toggle { objectName: "toggleAutoUpdate"; enabled: updater.kind !== "store"; checked: updater.auto; onToggled: (on) => updater.auto = on }
            }
            SettingRow {
                objectName: "updateStatusRow"
                title: qsTr("Versão %1").arg(updater.current)
                desc: {
                    switch (updater.state) {
                    case "checking": return qsTr("Procurando…")
                    case "uptodate": return qsTr("Você está com a versão mais recente.")
                    case "available": return qsTr("A versão %1 está disponível.").arg(updater.latest)
                    case "downloading": return qsTr("Baixando a versão %1… %2%").arg(updater.latest).arg(Math.round(updater.progress * 100))
                    case "ready": return qsTr("A versão %1 está pronta para instalar.").arg(updater.latest)
                    case "installing": return qsTr("Instalando…")
                    case "error": return updater.error
                    default: return updater.lastCheck > 0 ? qsTr("Última verificação: %1").arg(new Date(updater.lastCheck * 1000).toLocaleString(Qt.locale(i18n.locale), Locale.ShortFormat)) : qsTr("Ainda não verificado.")
                    }
                }
                Row {
                    spacing: 10
                    PillButton {
                        objectName: "updateCheck"
                        visible: updater.state !== "available" && updater.state !== "ready"
                        enabled: updater.kind !== "store" && updater.state !== "checking" && updater.state !== "downloading" && updater.state !== "installing"
                        text: updater.state === "checking" ? qsTr("Procurando…") : qsTr("Procurar agora"); icon: "sync"; variant: "outline"
                        onClicked: updater.checkNow()
                    }
                    PillButton {
                        objectName: "updateNow"
                        visible: updater.state === "available" || updater.state === "ready"
                        text: updater.state === "ready" ? qsTr("Reiniciar e atualizar") : (updater.canSelfUpdate ? qsTr("Atualizar") : qsTr("Baixar"))
                        icon: "download"; variant: "accent"
                        onClicked: updater.update()
                    }
                }
            }
        }

        Card {
            title: qsTr("Atalhos de teclado")
            Grid {
                columns: 2
                columnSpacing: 40
                rowSpacing: 12
                Repeater {
                    model: [
                        [qsTr("Espaço"), qsTr("Reproduzir / pausar")], [qsTr("F  ·  duplo clique"), qsTr("Tela cheia")],
                        ["← / →", qsTr("Voltar / avançar 10 s")], ["↑ / ↓", qsTr("Volume")],
                        ["M", qsTr("Silenciar")], ["N / P", qsTr("Próximo / anterior")],
                        ["[  /  ]", qsTr("Velocidade − / +")], ["S", qsTr("Capturar quadro")],
                        ["E", qsTr("Avançar um quadro")], ["L", qsTr("Repetir um trecho (A-B)")],
                        ["T", qsTr("Sempre no topo")], ["Ctrl + O", qsTr("Abrir arquivos")],
                        ["Ctrl + L", qsTr("Abrir stream de rede")], ["Esc", qsTr("Sair da tela cheia / fechar")],
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
                        Text { text: qsTr("apresenta"); color: Qt.rgba(1, 1, 1, 0.55); font.pixelSize: 12 }
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
                    text: qsTr("Todo o motor do VLC, <font color='#FF9A5C'>refratado</font> numa experiência nova.")
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
                            qsTr("Versão %1").arg(appctl.about.version || "?"),
                            "libVLC " + (appctl.about.vlcVersion || "?") + " · " + (appctl.about.vlcBundled ? qsTr("embutido") : qsTr("do sistema")),
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
                        text: qsTr("Falar com a LucsLabs"); icon: "mail"; variant: "glass"
                        onClicked: appctl.openExternal("mailto:" + (appctl.about.contact || ""))
                    }
                    PillButton {
                        objectName: "aboutSource"
                        text: qsTr("Código-fonte"); icon: "link"; variant: "glass"
                        onClicked: appctl.openExternal("https://github.com/LucsLabsStudios/refrata")
                    }
                    PillButton {
                        objectName: "aboutDefaults"
                        visible: appctl.about.installed === true
                        text: qsTr("Definir como player padrão"); icon: "apps"; variant: "glass"
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
                text: qsTr("Software livre (GPL 3.0) construído sobre o libVLC da VideoLAN (LGPL 2.1+). VLC é marca da VideoLAN; este é um projeto independente.")
            }
        }
    }

    function showAbout() {
        // scroll so the brand card is fully in view
        contentY = Math.max(0, Math.min(contentHeight - height, aboutCard.y + col.y - 24))
    }
}
