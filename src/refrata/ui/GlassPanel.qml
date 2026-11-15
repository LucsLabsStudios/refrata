import QtQuick
import QtQuick.Effects

// Frosted-glass surface. When `backdrop` is set, the area of the backdrop
// behind the panel is blurred live (works over playing video).
Item {
    id: root
    property Item backdrop: null
    property real radius: Math.min(width, height) / 2
    property color tint: Qt.rgba(0.07, 0.07, 0.09, 0.42)
    property color border: Qt.rgba(1, 1, 1, 0.12)
    property real blurMax: 48
    property int geometryTick: 0   // bump to force re-mapping after layout moves
    default property alias content: holder.data

    function remap() { geometryTick++ }

    Item {
        id: blurred
        anchors.fill: parent
        visible: root.backdrop !== null && root.visible && root.opacity > 0
        // keep the layer (and its GPU textures) alive while hidden: re-creating ~8 of them in
        // the frame the player slides up was the hitch at the start of the animation
        layer.enabled: root.backdrop !== null
        layer.effect: MultiEffect {
            maskEnabled: true
            maskSource: mask
            maskThresholdMin: 0.5
            maskSpreadAtMin: 1.0
        }
        ShaderEffectSource {
            id: src
            anchors.fill: parent
            sourceItem: root.backdrop
            visible: false
            live: blurred.visible
            sourceRect: {
                root.geometryTick; root.x; root.y; root.width; root.height
                if (!root.backdrop) return Qt.rect(0, 0, 0, 0)
                var p = root.mapToItem(root.backdrop, 0, 0)
                return Qt.rect(p.x, p.y, root.width, root.height)
            }
        }
        MultiEffect {
            anchors.fill: parent
            source: src
            blurEnabled: true
            blur: 1.0
            blurMax: root.blurMax
            saturation: 0.25
            brightness: -0.04
        }
    }
    Item {
        id: mask
        anchors.fill: parent
        visible: false
        layer.enabled: true
        Rectangle { anchors.fill: parent; radius: root.radius; color: "black" }
    }
    Rectangle {
        anchors.fill: parent
        radius: root.radius
        color: root.tint
        border.width: 1
        border.color: root.border
    }
    Item { id: holder; anchors.fill: parent }
}
