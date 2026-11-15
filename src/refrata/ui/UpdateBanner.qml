import QtQuick
import QtQuick.Effects

// Floating "new version" pill: available → downloading (progress) → ready → installing.
Item {
    id: root
    objectName: "updateBanner"
    readonly property bool shown: updater.bannerVisible
    width: row.implicitWidth + 24
    height: 60
    opacity: shown ? 1 : 0
    visible: opacity > 0.01
    transform: Translate { y: root.shown ? 0 : -16; Behavior on y { NumberAnimation { duration: 320; easing.type: Easing.OutCubic } } }
    Behavior on opacity { NumberAnimation { duration: 240; easing.type: Easing.OutCubic } }

    readonly property string st: updater.state
    readonly property bool selfUpdate: updater.canSelfUpdate

    Rectangle {
        id: bg
        anchors.fill: parent
        radius: height / 2
        color: theme.dark ? "#1E1A17" : "#FFFFFF"
        border.width: 1
        border.color: Qt.rgba(theme.accent.r, theme.accent.g, theme.accent.b, 0.45)
        layer.enabled: true
        layer.effect: MultiEffect {
            shadowEnabled: true; shadowBlur: 1.0; shadowVerticalOffset: 10
            shadowColor: Qt.rgba(theme.accent.r, theme.accent.g, theme.accent.b, 0.35)
            autoPaddingEnabled: true
        }
    }
    // download progress fills the pill from the left
    Rectangle {
        visible: root.st === "downloading" || root.st === "ready"
        x: 1; y: 1; height: parent.height - 2
        width: Math.max(height, (parent.width - 2) * updater.progress)
        radius: height / 2
        color: Qt.rgba(theme.accent.r, theme.accent.g, theme.accent.b, 0.16)
        Behavior on width { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
    }

    Row {
        id: row
        anchors.left: parent.left; anchors.leftMargin: 10
        anchors.verticalCenter: parent.verticalCenter
        spacing: 12
        // glowing orb
        Item {
            width: 40; height: 40
            anchors.verticalCenter: parent.verticalCenter
            Rectangle {
                anchors.fill: parent; radius: 20
                gradient: Gradient {
                    GradientStop { position: 0; color: Qt.lighter(theme.accent, 1.35) }
                    GradientStop { position: 1; color: Qt.darker(theme.accent, 1.25) }
                }
            }
            Icon {
                anchors.centerIn: parent
                name: root.st === "downloading" || root.st === "installing" ? "sync" : "download"
                size: 16; color: "white"
                RotationAnimation on rotation {
                    running: root.visible && (root.st === "downloading" || root.st === "installing")
                    from: 0; to: 360; duration: 1400; loops: Animation.Infinite
                    onRunningChanged: if (!running) parent.rotation = 0
                }
            }
        }
        Column {
            anchors.verticalCenter: parent.verticalCenter
            spacing: 1
            Text {
                objectName: "updateTitle"
                text: {
                    switch (root.st) {
                    case "downloading": return qsTr("Baixando a versão %1…  %2%").arg(updater.latest).arg(Math.round(updater.progress * 100))
                    case "ready": return qsTr("Versão %1 pronta").arg(updater.latest)
                    case "installing": return qsTr("Instalando a versão %1…").arg(updater.latest)
                    case "error": return qsTr("A atualização não deu certo")
                    default: return qsTr("Nova versão %1 disponível").arg(updater.latest)
                    }
                }
                width: Math.min(implicitWidth, 300); elide: Text.ElideRight
                color: theme.text; font.pixelSize: 14; font.weight: Font.DemiBold
            }
            Text {
                text: {
                    if (root.st === "error") return updater.error
                    if (root.st === "installing") return qsTr("O Refrata vai fechar e abrir sozinho")
                    if (root.st === "ready") return qsTr("Leva poucos segundos, e o app reabre sozinho")
                    if (!root.selfUpdate) return qsTr("Baixe o instalador no site")
                    return qsTr("Você está na %1").arg(updater.current)
                }
                width: Math.min(implicitWidth, 300); elide: Text.ElideRight
                color: theme.textDim; font.pixelSize: 12
            }
        }
        Item { width: 4; height: 1 }
        PillButton {
            objectName: "updateAction"
            anchors.verticalCenter: parent.verticalCenter
            visible: root.st !== "installing"
            variant: root.st === "downloading" ? "outline" : "accent"
            h: 38; fontSize: 13
            text: root.st === "downloading" ? qsTr("Cancelar")
                : root.st === "ready" ? qsTr("Reiniciar e atualizar")
                : root.st === "error" ? qsTr("Tentar de novo")
                : root.selfUpdate ? qsTr("Atualizar") : qsTr("Baixar")
            onClicked: root.st === "downloading" ? updater.cancel() : updater.update()
        }
        IconButton {
            objectName: "updateDismiss"
            anchors.verticalCenter: parent.verticalCenter
            visible: root.st !== "installing" && root.st !== "downloading"
            icon: "close"; size: 34; iconSize: 11; tip: qsTr("Agora não")
            onClicked: updater.dismiss()
        }
    }
}
