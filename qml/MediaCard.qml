import QtQuick
import "Utils.js" as U

// Video card: 16:9 thumbnail, duration badge, resume progress, title.
Item {
    id: root
    property string path: ""
    property string title: ""
    property string thumb: ""
    property string subtitle: ""
    property real duration: 0
    property real progress: 0      // 0..1 resume progress
    property bool large: false
    property bool audio: false
    signal play()
    signal menu(real x, real y)

    width: 280
    height: thumbBox.height + (large ? 0 : 64)

    Item {
        id: thumbBox
        width: parent.width
        height: Math.round(width * 9 / 16)
        scale: ma.containsMouse ? 1.025 : 1
        Behavior on scale { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }

        Rectangle {
            anchors.fill: parent
            radius: root.large ? 28 : 18
            gradient: Gradient {
                orientation: Gradient.Vertical
                GradientStop { position: 0; color: theme.surface2 }
                GradientStop { position: 1; color: theme.surface }
            }
            border.width: 1
            border.color: theme.stroke
            Icon {
                anchors.centerIn: parent
                name: root.audio ? "music" : "film"
                size: root.large ? 48 : 30
                color: theme.textFaint
                visible: img.status !== Image.Ready
            }
        }
        RoundedImage {
            id: img
            anchors.fill: parent
            radius: root.large ? 28 : 18
            source: root.thumb
            sourceWidth: root.large ? 1280 : 560
        }
        // bottom scrim + progress
        Rectangle {
            anchors.fill: parent
            radius: root.large ? 28 : 18
            gradient: Gradient {
                GradientStop { position: 0.55; color: "transparent" }
                GradientStop { position: 1.0; color: Qt.rgba(0, 0, 0, root.large ? 0.75 : 0.45) }
            }
        }
        Rectangle {
            visible: root.duration > 0 && !root.large
            anchors.right: parent.right; anchors.bottom: parent.bottom
            anchors.margins: 10
            anchors.bottomMargin: root.progress > 0 ? 18 : 10
            height: 24; radius: 12
            width: dl.implicitWidth + 16
            color: Qt.rgba(0, 0, 0, 0.55)
            Text {
                id: dl
                anchors.centerIn: parent
                text: U.time(root.duration)
                color: "white"; font.pixelSize: 12; font.weight: Font.DemiBold
                font.features: { "tnum": 1 }
            }
        }
        Rectangle {
            visible: root.progress > 0
            anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
            anchors.margins: root.large ? 28 : 10
            height: 4; radius: 2
            color: Qt.rgba(1, 1, 1, 0.3)
            Rectangle { width: parent.width * root.progress; height: parent.height; radius: 2; color: theme.accent }
        }
        // hover play button
        Rectangle {
            anchors.centerIn: parent
            width: root.large ? 0 : 56; height: width; radius: width / 2
            color: Qt.rgba(1, 1, 1, 0.92)
            opacity: ma.containsMouse && !root.large ? 1 : 0
            scale: ma.containsMouse ? 1 : 0.7
            Behavior on opacity { NumberAnimation { duration: 160 } }
            Behavior on scale { NumberAnimation { duration: 220; easing.type: Easing.OutBack } }
            PlayGlyph { anchors.centerIn: parent; anchors.horizontalCenterOffset: 2; size: 22; color: "#111" }
        }
    }

    Column {
        visible: !root.large
        anchors.top: thumbBox.bottom
        anchors.topMargin: 12
        width: parent.width
        spacing: 3
        Text {
            width: parent.width
            text: root.title
            color: theme.text
            font.pixelSize: 15
            font.weight: Font.DemiBold
            elide: Text.ElideRight
        }
        Text {
            width: parent.width
            text: root.subtitle
            color: theme.textDim
            font.pixelSize: 13
            elide: Text.ElideRight
        }
    }

    MouseArea {
        id: ma
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        cursorShape: Qt.PointingHandCursor
        onClicked: (m) => {
            if (m.button === Qt.RightButton) {
                var p = mapToItem(null, m.x, m.y)
                root.menu(p.x, p.y)
            } else root.play()
        }
    }
}
