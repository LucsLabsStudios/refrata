import QtQuick
import QtQuick.Controls
import "Utils.js" as U

Flickable {
    id: page
    signal openUrl(string url)
    contentHeight: col.height + 120
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    ScrollBar.vertical: ScrollBar {}

    function submit() {
        var u = field.text.trim()
        if (u) page.openUrl(u)
    }

    Column {
        id: col
        x: 8
        width: page.width - 56
        topPadding: 36
        spacing: 36

        PageHeader {
            width: parent.width
            eyebrow: qsTr("Rede")
            title: qsTr("Streams")
            subtitle: qsTr("Links do YouTube, Vimeo e outros sites, além de HTTP, HLS, RTSP, RTMP, SMB… tudo o que o VLC abre.")
        }

        Rectangle {
            width: parent.width
            height: 200
            radius: 36
            color: theme.surface
            border.width: 1; border.color: theme.stroke
            Column {
                anchors.fill: parent
                anchors.margins: 36
                spacing: 22
                Text { text: qsTr("Cole um link ou endereço de rede"); color: theme.text; font.family: theme.displayFont; font.pixelSize: 28; font.weight: Font.DemiBold }
                Row {
                    width: parent.width
                    spacing: 12
                    Rectangle {
                        width: parent.width - goBtn.width - pasteBtn.width - 24
                        height: 60; radius: 30
                        color: theme.surface2
                        border.width: field.activeFocus ? 2 : 1
                        border.color: field.activeFocus ? theme.accent : theme.stroke
                        Icon { id: li; x: 22; anchors.verticalCenter: parent.verticalCenter; name: "link"; size: 18; color: theme.textDim }
                        TextInput {
                            id: field
                            objectName: "urlField"
                            anchors.left: li.right; anchors.leftMargin: 14
                            anchors.right: parent.right; anchors.rightMargin: 22
                            anchors.verticalCenter: parent.verticalCenter
                            color: theme.text; font.pixelSize: 18
                            selectByMouse: true; clip: true
                            selectionColor: theme.accent
                            onAccepted: page.submit()
                            Text { visible: !field.text; text: qsTr("https://www.youtube.com/watch?v=…  ou  https://exemplo.com/video.m3u8"); color: theme.textFaint; font.pixelSize: 18; anchors.verticalCenter: parent.verticalCenter }
                        }
                    }
                    PillButton { id: pasteBtn; objectName: "pasteBtn"; text: qsTr("Colar"); variant: "outline"; h: 60; fontSize: 15; onClicked: field.text = appctl.clipboardText() }
                    PillButton { id: goBtn; objectName: "goBtn"; text: qsTr("Reproduzir"); icon: "play"; variant: "accent"; h: 60; fontSize: 15; onClicked: page.submit() }
                }
            }
        }

        Column {
            width: parent.width
            spacing: 14
            readonly property var urls: settings.history.filter(function (h) { return h.path.indexOf("://") > 0 })
            visible: urls.length > 0
            Text { text: qsTr("Streams recentes"); color: theme.text; font.family: theme.displayFont; font.pixelSize: 26; font.weight: Font.DemiBold }
            Repeater {
                model: parent.urls
                Rectangle {
                    width: parent.width; height: 64; radius: 20
                    color: ua.containsMouse ? theme.surface2 : theme.surface
                    border.width: 1; border.color: theme.stroke
                    Rectangle { x: 14; anchors.verticalCenter: parent.verticalCenter; width: 40; height: 40; radius: 20; color: theme.accentSoft
                        Icon { anchors.centerIn: parent; name: "globe"; size: 16; color: theme.accent } }
                    Text { x: 70; anchors.verticalCenter: parent.verticalCenter; width: parent.width - 200; elide: Text.ElideMiddle; text: modelData.path; color: theme.text; font.pixelSize: 15 }
                    Text { anchors.right: parent.right; anchors.rightMargin: 20; anchors.verticalCenter: parent.verticalCenter; text: U.ago(modelData.time); color: theme.textDim; font.pixelSize: 13 }
                    MouseArea { id: ua; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: page.openUrl(modelData.path) }
                }
            }
        }
    }
}
