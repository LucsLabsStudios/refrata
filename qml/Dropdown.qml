import QtQuick
import QtQuick.Controls

// A compact picker: the current choice in a pill; a click opens the list of options under it.
// model: [{ value, text }]. Rows are named itemPrefix + index (tests), the optional last row
// (footerText) is an action such as "Atualizar lista".
Item {
    id: root
    property var model: []
    property var value
    property string itemPrefix: ""
    property string footerText: ""
    property string footerIcon: "refresh"
    property real maxWidth: 380
    readonly property bool opened: pop.opened
    signal picked(var value)
    signal footerClicked()

    readonly property string currentText: {
        for (var i = 0; i < model.length; ++i)
            if (model[i].value === value) return model[i].text
        return model.length ? model[0].text : ""
    }
    implicitWidth: Math.min(maxWidth, Math.max(220, label.implicitWidth + 64))
    implicitHeight: 40
    width: implicitWidth
    height: implicitHeight

    function open() { pop.open() }
    // the list never outlives its box (switching tabs or pages hides the box)
    onVisibleChanged: if (!visible) pop.close()

    Rectangle {
        anchors.fill: parent
        radius: height / 2
        color: ma.containsMouse || pop.opened ? theme.surface3 : theme.surface2
        border.width: 1
        border.color: pop.opened ? theme.accent : theme.stroke
        Behavior on color { ColorAnimation { duration: 120 } }
    }
    Text {
        id: label
        x: 18
        anchors.verticalCenter: parent.verticalCenter
        width: parent.width - 54
        elide: Text.ElideRight
        text: root.currentText
        color: theme.text
        font.pixelSize: 13
        font.weight: Font.DemiBold
    }
    Icon {
        anchors.right: parent.right; anchors.rightMargin: 16
        anchors.verticalCenter: parent.verticalCenter
        name: "down"; size: 12; color: theme.textDim
        rotation: pop.opened ? 180 : 0
        Behavior on rotation { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
    }
    MouseArea {
        id: ma
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: pop.opened ? pop.close() : pop.open()
    }

    Popup {
        id: pop
        y: root.height + 6
        width: Math.max(root.width, 400)
        padding: 6
        height: Math.min(list.contentHeight + 12, 380)
        enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 120 } }
        exit: Transition { NumberAnimation { property: "opacity"; from: 1; to: 0; duration: 90 } }
        background: Rectangle { radius: 18; color: theme.surface; border.width: 1; border.color: theme.stroke }
        contentItem: ListView {
            id: list
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            model: root.model.length + (root.footerText ? 1 : 0)
            ScrollBar.vertical: ScrollBar { policy: list.contentHeight > list.height ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff }
            delegate: Rectangle {
                id: row
                readonly property bool isFooter: index >= root.model.length
                readonly property var entry: isFooter ? null : root.model[index]
                readonly property bool selected: !isFooter && entry.value === root.value
                objectName: root.itemPrefix + (isFooter ? "footer" : index)
                width: ListView.view.width
                height: 40
                radius: 12
                color: rowMa.containsMouse ? theme.surface2 : "transparent"
                Icon {
                    x: 12; anchors.verticalCenter: parent.verticalCenter
                    name: row.isFooter ? root.footerIcon : "check"; size: 13
                    color: row.isFooter ? theme.textDim : theme.accent
                    visible: row.isFooter || row.selected
                }
                Text {
                    x: 36; anchors.verticalCenter: parent.verticalCenter
                    width: parent.width - 48; elide: Text.ElideRight
                    text: row.isFooter ? root.footerText : row.entry.text
                    color: row.isFooter ? theme.textDim : row.selected ? theme.accent : theme.text
                    font.pixelSize: 13; font.weight: row.selected ? Font.DemiBold : Font.Normal
                }
                MouseArea {
                    id: rowMa
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        if (row.isFooter) root.footerClicked()
                        else root.picked(row.entry.value)
                        if (!row.isFooter) pop.close()
                    }
                }
            }
        }
    }
}
