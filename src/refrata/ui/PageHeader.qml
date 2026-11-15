import QtQuick

// Big-text page header (One UI style).
Item {
    id: root
    property string eyebrow: ""
    property string title: ""
    property string subtitle: ""
    default property alias trailing: trail.data
    height: col.height
    Column {
        id: col
        width: parent.width - trail.width - 24
        spacing: 6
        Text {
            visible: root.eyebrow !== ""
            text: root.eyebrow.toUpperCase()
            color: theme.accent
            font.pixelSize: 12; font.weight: Font.Bold; font.letterSpacing: 2.2
        }
        Text {
            text: root.title
            color: theme.text
            font.family: theme.displayFont
            font.pixelSize: 68
            font.weight: Font.DemiBold
            font.letterSpacing: -2
        }
        Text {
            visible: root.subtitle !== ""
            text: root.subtitle
            color: theme.textDim
            font.pixelSize: 16
        }
    }
    Row {
        id: trail
        anchors.right: parent.right
        anchors.bottom: col.bottom
        anchors.bottomMargin: 4
        spacing: 10
    }
}
