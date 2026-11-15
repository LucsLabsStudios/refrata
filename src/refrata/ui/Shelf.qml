import QtQuick

// Titled horizontal row of items with scroll arrows.
Column {
    id: root
    property string title: ""
    property string actionText: ""
    property alias model: list.model
    property alias delegate: list.delegate
    property real itemHeight: 300 * 9 / 16 + 64
    signal action()
    spacing: 18

    Item {
        width: parent.width; height: 40
        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: root.title
            color: theme.text
            font.family: theme.displayFont
            font.pixelSize: 28; font.weight: Font.DemiBold; font.letterSpacing: -0.5
        }
        Row {
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            spacing: 8
            PillButton { visible: root.actionText !== ""; text: root.actionText; h: 36; fontSize: 13; onClicked: root.action() }
            IconButton { icon: "left"; size: 36; iconSize: 12; bg: theme.surface2; enabled: !list.atXBeginning; onClicked: list.flickBy(-list.width * 0.8) }
            IconButton { icon: "right"; size: 36; iconSize: 12; bg: theme.surface2; enabled: !list.atXEnd; onClicked: list.flickBy(list.width * 0.8) }
        }
    }
    ListView {
        id: list
        width: parent.width
        height: root.itemHeight + 8
        orientation: ListView.Horizontal
        spacing: 22
        clip: false
        interactive: false   // let vertical wheel scroll the page; arrows/touchpad scroll the row
        boundsBehavior: Flickable.StopAtBounds
        cacheBuffer: 1200
        function flickBy(dx) {
            anim.to = Math.max(0, Math.min(contentWidth - width, contentX + dx))
            anim.restart()
        }
        NumberAnimation { id: anim; target: list; property: "contentX"; duration: 420; easing.type: Easing.OutCubic }
        WheelHandler {
            acceptedDevices: PointerDevice.TouchPad
            onWheel: (e) => { list.contentX = Math.max(0, Math.min(list.contentWidth - list.width, list.contentX - e.angleDelta.x)) }
        }
    }
}
