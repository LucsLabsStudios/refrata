import QtQuick

// Pill-shaped button. variant: "filled" | "accent" | "ghost" | "glass" | "outline"
Item {
    id: root
    property string text: ""
    property string icon: ""
    property string trailingIcon: ""
    property string variant: "ghost"
    property bool checked: false
    property real h: 40
    property real fontSize: 14
    property bool onMedia: false
    signal clicked()

    implicitWidth: row.implicitWidth + (text ? 36 : 24)
    implicitHeight: h
    width: implicitWidth; height: implicitHeight
    opacity: enabled ? 1 : 0.4

    readonly property color fg: {
        if (checked) return theme.onAccent
        switch (variant) {
        case "accent": return theme.onAccent
        case "filled": return theme.inverseText
        case "glass": return "white"
        default: return onMedia ? "white" : theme.text
        }
    }

    Rectangle {
        id: bgRect
        anchors.fill: parent
        radius: height / 2
        color: {
            if (root.checked) return theme.accent
            switch (root.variant) {
            case "accent": return ma.containsMouse ? Qt.lighter(theme.accent, 1.08) : theme.accent
            case "filled": return ma.containsMouse ? Qt.lighter(theme.inverse, 1.1) : theme.inverse
            case "glass": return ma.containsMouse ? Qt.rgba(1, 1, 1, 0.22) : Qt.rgba(1, 1, 1, 0.12)
            case "outline": return ma.containsMouse ? (theme.dark ? Qt.rgba(1,1,1,0.06) : Qt.rgba(0,0,0,0.04)) : "transparent"
            default: return ma.containsMouse ? (root.onMedia ? Qt.rgba(1,1,1,0.14) : (theme.dark ? Qt.rgba(1,1,1,0.08) : Qt.rgba(0,0,0,0.06)))
                                             : (root.onMedia ? Qt.rgba(1,1,1,0.08) : theme.surface2)
            }
        }
        border.width: root.variant === "outline" || root.variant === "glass" ? 1 : 0
        border.color: root.variant === "glass" ? Qt.rgba(1, 1, 1, 0.16) : theme.strokeStrong
        Behavior on color { ColorAnimation { duration: 140 } }
        scale: ma.pressed ? 0.96 : 1
        Behavior on scale { NumberAnimation { duration: 120 } }
    }
    Row {
        id: row
        anchors.centerIn: parent
        spacing: 8
        Icon { visible: root.icon !== ""; name: root.icon; size: root.fontSize + 2; color: root.fg; anchors.verticalCenter: parent.verticalCenter }
        Text {
            visible: root.text !== ""
            text: root.text
            color: root.fg
            font.pixelSize: root.fontSize
            font.weight: Font.DemiBold
            anchors.verticalCenter: parent.verticalCenter
        }
        Icon { visible: root.trailingIcon !== ""; name: root.trailingIcon; size: root.fontSize; color: root.fg; anchors.verticalCenter: parent.verticalCenter }
    }
    MouseArea {
        id: ma
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: root.clicked()
    }
}
