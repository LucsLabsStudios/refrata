import QtQuick
import QtQuick.Effects

// Windows HDR switched on after Refrata opened (hdroutput.needsReopen): HDR video can show in
// HDR only in a new process. One click reopens in place: same video, same point, same window.
Item {
    id: root
    objectName: "hdrBanner"
    property bool dismissed: false
    readonly property bool wanted: typeof hdrout !== "undefined" && hdrout.needsReopen
    readonly property bool shown: wanted && !dismissed
    onWantedChanged: if (!wanted) dismissed = false     // asked again the next time it happens
    width: row.implicitWidth + 24
    height: 60
    opacity: shown ? 1 : 0
    visible: opacity > 0.01
    transform: Translate { y: root.shown ? 0 : -16; Behavior on y { enabled: !theme.reduceMotion; NumberAnimation { duration: 320; easing.type: Easing.OutCubic } } }
    Behavior on opacity { enabled: !theme.reduceMotion; NumberAnimation { duration: 240; easing.type: Easing.OutCubic } }

    Rectangle {
        anchors.fill: parent
        radius: height / 2
        color: theme.dark ? "#1E1A17" : "#FFFFFF"
        border.width: 1
        border.color: Qt.rgba(theme.accent.r, theme.accent.g, theme.accent.b, 0.45)
        layer.enabled: root.visible
        layer.effect: MultiEffect {
            shadowEnabled: true; shadowBlur: 1.0; shadowVerticalOffset: 10
            shadowColor: Qt.rgba(0, 0, 0, 0.35)
            autoPaddingEnabled: true
        }
    }
    Row {
        id: row
        anchors.left: parent.left; anchors.leftMargin: 10
        anchors.verticalCenter: parent.verticalCenter
        spacing: 12
        Rectangle {
            width: 40; height: 40; radius: 20
            anchors.verticalCenter: parent.verticalCenter
            gradient: Gradient {
                GradientStop { position: 0; color: Qt.lighter(theme.accent, 1.35) }
                GradientStop { position: 1; color: Qt.darker(theme.accent, 1.25) }
            }
            Icon { anchors.centerIn: parent; name: "brightness"; size: 17; color: "white" }
        }
        Column {
            anchors.verticalCenter: parent.verticalCenter
            spacing: 1
            Text {
                text: qsTr("O HDR foi ligado no Windows")
                color: theme.text; font.pixelSize: 14; font.weight: Font.DemiBold
            }
            Text {
                text: qsTr("Reabra para ver vídeos HDR com o brilho real. Volta no mesmo ponto.")
                width: Math.min(implicitWidth, 360); elide: Text.ElideRight
                color: theme.textDim; font.pixelSize: 12
            }
        }
        Item { width: 4; height: 1 }
        PillButton {
            objectName: "hdrReopen"
            anchors.verticalCenter: parent.verticalCenter
            variant: "accent"; h: 38; fontSize: 13
            text: qsTr("Reabrir em HDR")
            onClicked: if (!hdrout.reopen()) toast.show(qsTr("Não deu para reabrir. Feche e abra o Refrata."))
        }
        IconButton {
            objectName: "hdrReopenDismiss"
            anchors.verticalCenter: parent.verticalCenter
            icon: "close"; size: 34; iconSize: 11; tip: qsTr("Agora não")
            onClicked: root.dismissed = true
        }
    }
}
