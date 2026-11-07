import QtQuick
import QtQuick.Effects

Item {
    id: root
    property alias source: img.source
    property alias status: img.status
    property real radius: 16
    property int fillMode: Image.PreserveAspectCrop
    property int sourceWidth: 640

    Image {
        id: img
        anchors.fill: parent
        fillMode: root.fillMode
        asynchronous: true
        sourceSize.width: root.sourceWidth
        smooth: true
        mipmap: true
        visible: false
    }
    MultiEffect {
        anchors.fill: parent
        source: img
        maskEnabled: true
        maskSource: m
        maskThresholdMin: 0.5
        maskSpreadAtMin: 1.0
        opacity: img.status === Image.Ready ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 260 } }
    }
    Item {
        id: m
        anchors.fill: parent
        visible: false
        layer.enabled: true
        Rectangle { anchors.fill: parent; radius: root.radius; color: "black" }
    }
}
