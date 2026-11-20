import QtQuick
import QtQuick.Controls
import "Utils.js" as U

Flickable {
    id: page
    signal addFolder()
    signal notify(string text)
    // one subject at a time: a tab bar on top, the cards of the chosen tab below
    property string tab: "general"
    readonly property var tabs: [
        { key: "general", text: qsTr("Geral") }, { key: "playback", text: qsTr("Reprodução") },
        { key: "sound", text: qsTr("Som") }, { key: "subtitles", text: qsTr("Legendas") },
        { key: "library", text: qsTr("Biblioteca") }, { key: "system", text: qsTr("Sistema") },
        { key: "shortcuts", text: qsTr("Atalhos") }, { key: "about", text: qsTr("Sobre") }]
    onTabChanged: contentY = 0
    // the player's picture right now (PlayerView.pictureStatus): each Imagem option says whether it
    // is working on the video playing, or why not
    property var pictureStatus: ({})
    readonly property bool videoOn: !!pictureStatus.video
    function rtxLine(wanted) { return U.rtxLine(wanted, videoOn, pictureStatus.rtx) }
    // the tab holding `item` (tests and showAbout open the right one before using a control)
    function reveal(item) {
        for (var p = item; p; p = p.parent)
            if (p.sectionTab !== undefined && p.sectionTab !== "") { page.tab = p.sectionTab; return true }
        return false
    }
    contentHeight: col.height + 120
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    ScrollBar.vertical: ScrollBar {}

    component Card: Rectangle {
        default property alias body: inner.data
        property string title: ""
        property string sectionTab: ""
        visible: page.tab === sectionTab
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
        // what the option is doing right now (shown under the description): "on" green, "paused"
        // amber with the reason, "idle" grey; "" = nothing to say
        property string status: ""
        property string statusKind: "idle"
        property string statusKey: ""      // tests find the line by it (the title is translated)
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
            Row {
                visible: status !== ""
                spacing: 8
                topPadding: 4
                width: parent.width
                Rectangle {
                    width: 8; height: 8; radius: 4
                    anchors.verticalCenter: statusText.verticalCenter
                    color: statusKind === "on" ? "#3ecf7a" : statusKind === "paused" ? "#f0b43c" : theme.textFaint
                }
                Text {
                    id: statusText
                    objectName: "status_" + statusKey
                    width: parent.width - 16
                    text: status
                    color: statusKind === "on" ? theme.text : theme.textDim
                    font.pixelSize: 13; font.weight: Font.DemiBold
                    wrapMode: Text.WordWrap
                }
            }
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

        Flow {
            objectName: "settingsTabs"
            width: parent.width
            spacing: 8
            Repeater {
                model: page.tabs
                PillButton {
                    objectName: "settingsTab_" + modelData.key
                    text: modelData.text
                    variant: "outline"; h: 38; fontSize: 13
                    checked: page.tab === modelData.key
                    onClicked: page.tab = modelData.key
                }
            }
        }

        Card {
            sectionTab: "general"
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
            sectionTab: "general"
            objectName: "languageCard"
            title: qsTr("Idioma")
            SettingRow {
                title: qsTr("Idioma do app")
                desc: (appctl.platform === "windows" ? qsTr("“Automático” segue o idioma do Windows. A troca vale na hora.") : qsTr("“Automático” segue o idioma do sistema. A troca vale na hora."))
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
                        // after the click finishes: switching rebuilds these very buttons (their
                        // labels are translated), and doing it inside the click event froze the app
                        onClicked: { var c = modelData.code; Qt.callLater(function () { i18n.language = c }) }
                    }
                }
            }
        }

        Card {
            sectionTab: "playback"
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
                title: qsTr("Decodificação por hardware"); desc: qsTr("Usa a GPU para decodificar vídeo. Desligue se um vídeo aparecer com falhas.")
                Toggle { objectName: "toggleHwdec"; checked: settings.hwdec; onToggled: (on) => settings.hwdec = on }
            }
        }

        Card {
            sectionTab: "playback"
            objectName: "pictureCard"
            title: qsTr("Imagem")
            SettingRow {
                title: qsTr("Movimento suave")
                desc: qsTr("Cria quadros intermediários na GPU: filmes de 24 e 30 fps passam a rodar a 60, 120 ou na taxa do seu monitor (%1 Hz).").arg(Math.round(appctl.refreshRate(Screen.name) || 60))
                Segmented {
                    objectName: "smoothMotionSeg"
                    options: [{ text: qsTr("Desligado"), value: "off" }, { text: "60 fps", value: "60" },
                              { text: "120 fps", value: "120" }, { text: qsTr("Máximo"), value: "max" }]
                    value: settings.smoothMotion
                    onPicked: (v) => settings.smoothMotion = v
                }
                status: settings.smoothMotion === "off" ? "" : !page.videoOn ? qsTr("Entra em ação quando um vídeo estiver tocando.")
                        : page.pictureStatus.motion === "on"
                          ? qsTr("Ativo: %1 → %2 quadros por segundo.").arg(Math.round(page.pictureStatus.motionFrom)).arg(Math.round(page.pictureStatus.motionTo))
                          : page.pictureStatus.motion === "idle" ? qsTr("Sem efeito neste vídeo: ele já tem quadros por segundo suficientes para a tela.")
                          : qsTr("Pronto: começa quando o vídeo tocar.")
                statusKind: page.pictureStatus.motion === "on" ? "on" : "idle"; statusKey: "motion"
            }
            SettingRow {
                title: qsTr("Realce de luz"); desc: qsTr("Clareia as áreas escuras e realça o contraste local: um visual de HDR em vídeos comuns.")
                MiniSlider {
                    objectName: "lightBoostSlider"
                    width: 200; from: 0; to: 100; stepSize: 5
                    value: settings.lightBoost
                    onMoved: (v) => settings.lightBoost = Math.round(v)
                }
            }
            SettingRow {
                title: qsTr("Realce de detalhes"); desc: qsTr("Nitidez adaptativa: realça texturas sem criar halos nem aumentar o ruído.")
                MiniSlider {
                    objectName: "detailBoostSlider"
                    width: 200; from: 0; to: 100; stepSize: 5
                    value: settings.detailBoost
                    onMoved: (v) => settings.detailBoost = Math.round(v)
                }
            }
            SettingRow {
                title: qsTr("HDR real em telas HDR")
                desc: {
                    var s = typeof hdrout !== "undefined" ? hdrout.screenInfo : ({})
                    var nits = Math.round(s.max_nits || 0)
                    if (typeof hdrout !== "undefined" && hdrout.screenHdr)
                        return qsTr("Ativo: vídeos HDR10, HDR10+ e HLG aparecem com o brilho real da sua tela (até %1 nits). A interface fica como no resto do Windows.").arg(nits)
                    if (s.hdr)
                        return settings.hdrOutput === "off"
                            ? qsTr("O HDR do Windows está ligado nesta tela (até %1 nits). Ligue esta opção para ver vídeos HDR com o brilho real.").arg(nits)
                            : hdrout.needsReopen
                            ? qsTr("O HDR do Windows foi ligado depois que o Refrata abriu (até %1 nits): reabra o Refrata para usar o HDR real.").arg(nits)
                            : qsTr("O HDR do Windows está ligado nesta tela (até %1 nits), mas o Windows não deixou a janela usar HDR. Atualize o driver de vídeo.").arg(nits)
                    if (nits > 0)
                        return qsTr("Sua tela aceita HDR (até %1 nits): ligue \"Usar HDR\" nas configurações de vídeo do Windows, sem fechar o Refrata. Sem isso, o HDR é convertido para a tela comum com as cores certas.").arg(nits)
                    return qsTr("Em telas comuns, vídeos HDR são convertidos com as cores e o brilho certos. Resoluções até 8K e acima.")
                }
                Toggle { objectName: "hdrOutputToggle"; checked: settings.hdrOutput !== "off"; onToggled: (on) => settings.hdrOutput = on ? "auto" : "off" }
                status: settings.hdrOutput === "off" || !page.videoOn ? ""
                        : page.pictureStatus.hdr > 0 && page.pictureStatus.hdrOut
                          ? qsTr("Ativo: este vídeo HDR está saindo em HDR real (branco em %1 nits, realces até %2).").arg(Math.round(page.pictureStatus.outWhite)).arg(Math.round(page.pictureStatus.outPeak))
                          : page.pictureStatus.hdr > 0 ? qsTr("Este vídeo é HDR, convertido para a tela comum.")
                          : qsTr("O vídeo que está tocando é comum (SDR).")
                statusKind: page.pictureStatus.hdr > 0 && page.pictureStatus.hdrOut ? "on" : "idle"; statusKey: "hdr"
            }
            SettingRow {
                visible: appctl.nvidiaRtx
                title: qsTr("RTX Video Super Resolution")
                desc: qsTr("A placa NVIDIA RTX amplia vídeos de resolução menor para o tamanho da tela com IA: mais nitidez, menos serrilhado e blocos de compressão. Vale com o movimento suave desligado e com \"Super Resolution\" ligado no NVIDIA App (Aprimoramento de vídeo RTX).")
                Toggle { objectName: "rtxVsrToggle"; checked: settings.rtxVsr; onToggled: (on) => settings.rtxVsr = on }
                status: page.rtxLine(settings.rtxVsr)[0]
                statusKind: page.rtxLine(settings.rtxVsr)[1]; statusKey: "rtxVsr"
            }
            SettingRow {
                visible: appctl.nvidiaRtx
                title: qsTr("RTX Video HDR")
                desc: qsTr("Numa tela HDR, a placa NVIDIA RTX converte vídeos comuns (SDR) em HDR com IA: mais brilho e cor nos realces. Precisa de \"RTX Video HDR\" ligado no NVIDIA App.")
                Toggle { objectName: "rtxHdrToggle"; checked: settings.rtxHdr; onToggled: (on) => settings.rtxHdr = on }
                status: page.rtxLine(settings.rtxHdr)[0]
                statusKind: page.rtxLine(settings.rtxHdr)[1]; statusKey: "rtxHdr"
            }
            SettingRow {
                title: qsTr("Brilho por cena")
                desc: qsTr("Em HDR10, mede o brilho de cada cena e ajusta o tone mapping a ela: cenas escuras não ficam apagadas. Filmes HDR10+ usam os dados do próprio filme.")
                Toggle { objectName: "hdrDynamicToggle"; checked: settings.hdrDynamic; onToggled: (on) => settings.hdrDynamic = on }
            }
        }

        Card {
            sectionTab: "sound"
            objectName: "soundSubsCard"
            title: qsTr("Som")
            SettingRow {
                title: qsTr("Normalizar volume"); desc: qsTr("Equilibra diálogos baixos e explosões altas.")
                Toggle { objectName: "settingsNormalize"; checked: player.normalize; onToggled: (on) => player.setNormalize(on) }
            }
            SettingRow {
                title: qsTr("Áudio digital direto (passthrough)")
                desc: qsTr("Envia o Dolby e o DTS do filme como estão para o receiver ou a soundbar (HDMI ou cabo óptico), que decodifica o som surround. HD inclui Dolby TrueHD, DTS-HD e Atmos, só por HDMI.")
                Segmented {
                    objectName: "passthroughSeg"
                    options: [{ text: qsTr("Desligado"), value: "off" }, { text: "Dolby / DTS", value: "on" }, { text: "HD", value: "hd" }]
                    value: player.passthrough
                    onPicked: (v) => player.setPassthrough(v)
                }
            }
            SettingRow {
                title: qsTr("Saída de áudio"); desc: qsTr("Onde o som toca: alto-falantes, fone de ouvido, TV pelo HDMI.")
                Dropdown {
                    objectName: "settingsDevices"
                    itemPrefix: "settingsDev_"
                    maxWidth: 340
                    model: player.audioDevices.map(function (d) { return { value: d.id, text: d.name } })
                    value: player.audioDevice
                    footerText: qsTr("Atualizar lista")
                    onPicked: (v) => player.setAudioDevice(v)
                    onFooterClicked: player.refreshAudioDevices()
                }
            }
        }

        Card {
            sectionTab: "subtitles"
            title: qsTr("Legendas")
            SettingRow { title: qsTr("Aparência da legenda"); desc: qsTr("Tamanho, cor e fundo do texto das legendas.") }
            Flow {
                width: parent.width; spacing: 8; bottomPadding: 6
                Repeater {
                    model: [{ k: "subSize", v: "small", t: qsTr("Pequena") }, { k: "subSize", v: "normal", t: qsTr("Normal") },
                            { k: "subSize", v: "large", t: qsTr("Grande") }, { k: "subSize", v: "huge", t: qsTr("Enorme") }]
                    PillButton { objectName: "settings_" + modelData.k + "_" + modelData.v; text: modelData.t; variant: "outline"; h: 36; fontSize: 13; checked: player.subtitleStyle[modelData.k] === modelData.v; onClicked: player.setSubtitleStyle(modelData.k, modelData.v) }
                }
                Item { width: 12; height: 1 }
                Repeater {
                    model: [{ k: "subColor", v: "white", t: qsTr("Branca") }, { k: "subColor", v: "yellow", t: qsTr("Amarela") },
                            { k: "subBackground", v: "outline", t: qsTr("Contorno") }, { k: "subBackground", v: "box", t: qsTr("Fundo escuro") },
                            { k: "subBackground", v: "none", t: qsTr("Sem contorno") }]
                    PillButton { objectName: "settings_" + modelData.k + "_" + modelData.v; text: modelData.t; variant: "outline"; h: 36; fontSize: 13; checked: player.subtitleStyle[modelData.k] === modelData.v; onClicked: player.setSubtitleStyle(modelData.k, modelData.v) }
                }
            }
            SettingRow {
                title: qsTr("Codificação das legendas"); desc: qsTr("Se os acentos aparecerem trocados por símbolos, escolha a do idioma da legenda.")
                Dropdown {
                    objectName: "subEncoding"
                    itemPrefix: "subEnc_"
                    maxWidth: 300
                    model: [{ value: "", text: qsTr("Automática") }, { value: "UTF-8", text: "UTF-8" },
                            { value: "Windows-1252", text: qsTr("Ocidental (Windows-1252)") }, { value: "Windows-1250", text: qsTr("Europa Central (Windows-1250)") },
                            { value: "Windows-1251", text: qsTr("Cirílico (Windows-1251)") }, { value: "Windows-1253", text: qsTr("Grego (Windows-1253)") },
                            { value: "Windows-1254", text: qsTr("Turco (Windows-1254)") }, { value: "GB18030", text: qsTr("Chinês simplificado (GB18030)") },
                            { value: "Shift_JIS", text: qsTr("Japonês (Shift_JIS)") }, { value: "EUC-KR", text: qsTr("Coreano (EUC-KR)") }]
                    value: player.subtitleStyle.subEncoding
                    onPicked: (v) => player.setSubtitleStyle("subEncoding", v)
                }
            }
        }

        Card {
            sectionTab: "library"
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
            sectionTab: "system"
            title: qsTr("Abertura rápida")
            SettingRow {
                objectName: "defaultAppRow"
                visible: winassoc.available && updater.kind !== "store"
                title: appctl.platform === "windows" ? qsTr("Player padrão do Windows") : qsTr("Player padrão do sistema")
                // how many types open in Refrata, and which of the common ones still do not: Windows
                // lets no app make itself the default, so the person picks each type in Settings
                readonly property string counted: winassoc.registered && winassoc.typeCount > 0
                    ? qsTr("Padrão para %1 de %2 tipos de arquivo.").replace("%1", winassoc.defaultCount).replace("%2", winassoc.typeCount) : ""
                readonly property string missing: winassoc.missingMain.length
                    ? qsTr("Ainda abrem em outro app: %1.").replace("%1", winassoc.missingMain.join(", ")) : ""
                desc: appctl.platform === "windows" ? (winassoc.isDefault
                        ? qsTr("O duplo clique num vídeo no Explorer já abre o Refrata.") + (winassoc.defaultCount < winassoc.typeCount ? " " + counted : "")
                    : winassoc.registered ? [counted, missing, qsTr("Nas configurações do Windows, clique em cada um e escolha o Refrata.")].filter(function (s) { return s }).join(" ")
                    : qsTr("Esta cópia portátil ainda não aparece no “Abrir com” do Windows. O botão a registra para o seu usuário e abre as configurações para você escolher o Refrata como padrão."))
                    : winassoc.isDefault ? qsTr("O Refrata já abre seus vídeos e músicas com dois cliques.")
                    : [qsTr("Faz o Refrata aparecer em “Abrir com” e abrir vídeos e músicas com dois cliques no gerenciador de arquivos."), counted, missing].filter(function (s) { return s }).join(" ")
                PillButton {
                    objectName: "defaultAppBtn"
                    visible: appctl.platform === "windows" || !winassoc.isDefault
                    text: winassoc.isDefault ? qsTr("Configurações") : qsTr("Tornar padrão")
                    variant: winassoc.isDefault ? "outline" : "filled"
                    onClicked: winassoc.openDefaults()
                }
            }
            SettingRow {
                visible: appctl.platform === "windows"   // an Explorer feature
                title: qsTr("Prévia com Espaço no Explorer")
                desc: qsTr("Selecione um vídeo ou música no Explorer ou na área de trabalho e aperte Espaço. Espaço ou Esc fecha; ← → passa pelos arquivos da pasta.")
                Toggle { objectName: "toggleQuickLook"; checked: quicklook.enabled; onToggled: (on) => quicklook.enabled = on }
            }
            SettingRow {
                title: qsTr("Abrir arquivos no player rápido")
                desc: (appctl.platform === "windows" ? qsTr("Um vídeo ou música aberto pelo Explorer começa numa janela compacta e flutuante; ⤢ leva para o app completo.")
                              : qsTr("Um vídeo ou música aberto pelo gerenciador de arquivos começa numa janela compacta e flutuante; ⤢ leva para o app completo."))
                      + (settings.quickOpen && winassoc.available && !winassoc.isDefault ? " " + (appctl.platform === "windows"
                         ? qsTr("Só vale para arquivos que o Windows abre com o Refrata (veja Player padrão do Windows, acima).")
                         : qsTr("Só vale para arquivos que o sistema abre com o Refrata (veja Player padrão do sistema, acima).")) : "")
                Toggle { objectName: "toggleQuickOpen"; checked: settings.quickOpen; onToggled: (on) => settings.quickOpen = on }
            }
            SettingRow {
                title: qsTr("Manter em segundo plano")
                desc: appctl.platform === "windows" ? qsTr("Ao fechar a janela, o Refrata fica na bandeja do Windows, e a prévia e os arquivos abrem na hora.")
                    : qsTr("Ao fechar a janela, o Refrata fica na bandeja do sistema, e os arquivos abrem na hora.")
                Toggle { objectName: "toggleBackground"; checked: tray.background; onToggled: (on) => tray.background = on }
            }
            SettingRow {
                visible: tray.canAutostart
                title: appctl.platform === "windows" ? qsTr("Iniciar com o Windows") : qsTr("Iniciar com o sistema")
                desc: qsTr("Começa discretamente na bandeja ao ligar o computador.")
                Toggle { objectName: "toggleAutostart"; checked: tray.autostart; onToggled: (on) => tray.autostart = on }
            }
        }

        Card {
            sectionTab: "system"
            title: qsTr("Atualizações")
            SettingRow {
                title: qsTr("Procurar atualizações automaticamente")
                desc: updater.kind === "store" ? qsTr("Esta cópia veio da Microsoft Store, que cuida das atualizações.")
                    : qsTr("Ao abrir o app, no máximo duas vezes por dia; o app só é atualizado quando você pedir. O yt-dlp, que abre links de sites, se atualiza sozinho quando você usa links.")
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
            id: keysCard
            sectionTab: "shortcuts"
            objectName: "keysCard"
            title: qsTr("Atalhos de teclado")
            property string capturing: ""   // the action waiting for its new key
            readonly property var names: ({
                playPause: qsTr("Reproduzir / pausar"), fullscreen: qsTr("Tela cheia"),
                back10: qsTr("Voltar 10 s"), fwd10: qsTr("Avançar 10 s"),
                back3: qsTr("Voltar 3 s"), fwd3: qsTr("Avançar 3 s"),
                back60: qsTr("Voltar 1 min"), fwd60: qsTr("Avançar 1 min"),
                volUp: qsTr("Aumentar o volume"), volDown: qsTr("Diminuir o volume"),
                mute: qsTr("Silenciar"), next: qsTr("Próximo"), prev: qsTr("Anterior"),
                nextChapter: qsTr("Próximo capítulo"), prevChapter: qsTr("Capítulo anterior"),
                faster: qsTr("Mais rápido"), slower: qsTr("Mais devagar"), normalSpeed: qsTr("Velocidade normal"),
                snapshot: qsTr("Capturar quadro"), frame: qsTr("Avançar um quadro"), frameBack: qsTr("Voltar um quadro"), bookmark: qsTr("Marcar favorito"),
                loop: qsTr("Repetir um trecho (A-B)"), onTop: qsTr("Sempre no topo"),
                info: qsTr("Informações da mídia"), rotate: qsTr("Girar o vídeo 90°"),
                pip: qsTr("Picture-in-picture"), open: qsTr("Abrir arquivos"),
                stream: qsTr("Abrir stream de rede"), settings: qsTr("Ajustes")
            })
            Text {
                width: parent.width; wrapMode: Text.WordWrap; bottomPadding: 12
                text: qsTr("Clique num atalho e aperte a tecla nova; Esc cancela. Esc, F11 e o duplo clique no vídeo são fixos.")
                color: theme.textDim; font.pixelSize: 13
            }
            Grid {
                columns: parent.width > 760 ? 2 : 1
                columnSpacing: 40
                rowSpacing: 10
                Repeater {
                    model: keys.actions
                    Row {
                        spacing: 14
                        Rectangle {
                            id: chip
                            objectName: "key_" + modelData
                            readonly property bool listening: keysCard.capturing === modelData
                            width: 150; height: 32; radius: 10
                            color: listening ? theme.accent : km.containsMouse ? theme.surface3 : theme.surface2
                            Behavior on color { ColorAnimation { duration: 140 } }
                            Text {
                                anchors.centerIn: parent; width: parent.width - 12; horizontalAlignment: Text.AlignHCenter; elide: Text.ElideRight
                                text: chip.listening ? qsTr("Aperte uma tecla…") : keys.label(keys.map[modelData])
                                color: chip.listening ? "white" : keys.map[modelData] ? theme.text : theme.textDim
                                font.pixelSize: 13; font.weight: Font.DemiBold
                            }
                            MouseArea {
                                id: km; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                onClicked: { keysCard.capturing = chip.listening ? "" : modelData; if (chip.listening) chip.forceActiveFocus() }
                            }
                            // while listening, every key (Space, arrows...) is the new shortcut, not a command
                            Keys.onShortcutOverride: (e) => e.accepted = chip.listening
                            Keys.onPressed: (e) => {
                                if (!chip.listening) return
                                e.accepted = true
                                if (e.key === Qt.Key_Escape) { keysCard.capturing = ""; return }
                                var seq = keys.fromKey(e.key, e.modifiers)
                                if (!seq) return   // a lone Ctrl/Shift/Alt: wait for the key
                                var took = keys.assign(modelData, seq)
                                keysCard.capturing = ""
                                if (took === "reserved") page.notify(qsTr("Essa tecla é fixa no Refrata"))
                                else if (took) page.notify(qsTr("A tecla saiu de “%1”").arg(keysCard.names[took]))
                            }
                            onActiveFocusChanged: if (!activeFocus && listening) keysCard.capturing = ""
                        }
                        Text { anchors.verticalCenter: parent.verticalCenter; text: keysCard.names[modelData] || modelData; color: theme.textDim; font.pixelSize: 14; width: 240; wrapMode: Text.WordWrap }
                    }
                }
            }
            Item { width: 1; height: 12; visible: keys.customized }
            PillButton { objectName: "keysReset"; visible: keys.customized; text: qsTr("Restaurar os atalhos padrão"); icon: "refresh"; variant: "outline"; onClicked: { keysCard.capturing = ""; keys.resetAll() } }
        }

        // ------------------------------------------------ about (brand card)
        Rectangle {
            id: aboutCard
            objectName: "aboutCard"
            readonly property string sectionTab: "about"
            visible: page.tab === "about"
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
        page.tab = "about"
        contentY = 0
    }
}
