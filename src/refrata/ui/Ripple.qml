import QtQuick

// Expanding ring that fades out from the centre of its parent: call burst() on a click.
Item {
    id: root
    anchors.centerIn: parent
    width: parent ? parent.width : 0
    height: width
    property color color: "white"
    property real reach: 1.7          // final size, relative to the parent

    function burst() {
        if (theme.reduceMotion) return
        anim.restart()
    }

    Rectangle {
        id: ring
        anchors.centerIn: parent
        width: root.width; height: width; radius: width / 2
        color: "transparent"
        border.width: 2
        border.color: root.color
        opacity: 0
        scale: 1
    }
    ParallelAnimation {
        id: anim
        NumberAnimation { target: ring; property: "scale"; from: 0.9; to: root.reach; duration: 620; easing.type: Easing.OutCubic }
        SequentialAnimation {
            NumberAnimation { target: ring; property: "opacity"; from: 0; to: 0.55; duration: 60 }
            NumberAnimation { target: ring; property: "opacity"; to: 0; duration: 560; easing.type: Easing.OutCubic }
        }
    }
}
