import QtQuick
import QtQuick.Shapes

// Crisp vector play / pause glyph that morphs between states.
Item {
    id: root
    property bool playing: false
    property color color: "white"
    property real size: 28
    width: size; height: size
    property real t: playing ? 1 : 0
    Behavior on t { NumberAnimation { duration: 220; easing.type: Easing.InOutCubic } }

    Shape {
        anchors.fill: parent
        // Qt 6.8's CurveRenderer leaks memory every time the shape changes (~0.3 MB per track
        // change in music mode): the geometry renderer, multisampled for smooth edges
        preferredRendererType: Shape.GeometryRenderer
        layer.enabled: true
        layer.samples: 4
        // left half: triangle-left-half  ->  left bar
        ShapePath {
            fillColor: root.color; strokeColor: "transparent"
            startX: root.size * (0.22 + 0.0 * root.t); startY: root.size * (0.10 + 0.04 * root.t)
            PathLine { x: root.size * (0.55 - 0.13 * root.t); y: root.size * (0.30 - 0.16 * root.t) }
            PathLine { x: root.size * (0.55 - 0.13 * root.t); y: root.size * (0.70 + 0.16 * root.t) }
            PathLine { x: root.size * 0.22; y: root.size * (0.90 - 0.04 * root.t) }
        }
        // right half: triangle-right-half -> right bar
        ShapePath {
            fillColor: root.color; strokeColor: "transparent"
            startX: root.size * (0.55 + 0.03 * root.t); startY: root.size * (0.30 - 0.16 * root.t)
            PathLine { x: root.size * (0.90 - 0.12 * root.t); y: root.size * (0.50 - 0.36 * root.t) }
            PathLine { x: root.size * (0.90 - 0.12 * root.t); y: root.size * (0.50 + 0.36 * root.t) }
            PathLine { x: root.size * (0.55 + 0.03 * root.t); y: root.size * (0.70 + 0.16 * root.t) }
        }
    }
}
