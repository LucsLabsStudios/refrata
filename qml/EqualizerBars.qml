import QtQuick

// Animated "now playing" bars.
Row {
    id: root
    property bool running: true
    property color color: theme.accent
    spacing: 2.5
    height: 16
    Repeater {
        model: 4
        Rectangle {
            id: bar
            width: 3; radius: 1.5
            color: root.color
            anchors.bottom: parent.bottom
            height: 4
            SequentialAnimation on height {
                running: root.running
                loops: Animation.Infinite
                NumberAnimation { to: 6 + (index * 7) % 11; duration: 260 + index * 70; easing.type: Easing.InOutSine }
                NumberAnimation { to: 16 - index * 2; duration: 300 + index * 50; easing.type: Easing.InOutSine }
                NumberAnimation { to: 4; duration: 280; easing.type: Easing.InOutSine }
            }
        }
    }
}
