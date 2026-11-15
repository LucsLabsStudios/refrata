import QtQuick

// Animated "now playing" bars. Four plain children, not a Repeater: these bars sit in list rows
// that may be built in the background (a page still loading) and dropped half-built when the
// list changes; a nested Repeater then warns "destroyed during incubation".
Row {
    id: root
    property bool running: true
    property color color: theme.accent
    spacing: 2.5
    height: 16

    component Bar: Rectangle {
        id: bar
        property int index: 0
        width: 3; radius: 1.5
        color: root.color
        anchors.bottom: parent.bottom
        height: 4
        SequentialAnimation on height {
            running: root.running
            loops: Animation.Infinite
            NumberAnimation { to: 6 + (bar.index * 7) % 11; duration: 260 + bar.index * 70; easing.type: Easing.InOutSine }
            NumberAnimation { to: 16 - bar.index * 2; duration: 300 + bar.index * 50; easing.type: Easing.InOutSine }
            NumberAnimation { to: 4; duration: 280; easing.type: Easing.InOutSine }
        }
    }
    Bar { index: 0 }
    Bar { index: 1 }
    Bar { index: 2 }
    Bar { index: 3 }
}
